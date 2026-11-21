"""Rp1Backend, against stand-ins for libwlsync_pio.so and the libgpiod bindings.

The backend loads its library in its constructor rather than at import, so fakes can
take its place on a laptop. The library fake accepts every configuration call and turns
the RX FIFO into a list the test fills. What that exercises is the backend's own glue:
that something drains the FIFO, that what goes wrong reaches check_health(), the order
things start and stop in. What it cannot exercise is silicon -- the PIO program, real
FIFO timing, the chardev -- which is bench day's.
"""

import ctypes
import inspect
import pathlib
import re
import threading
import time
import types
import unittest.mock

import pytest

from wl_sync.barcode import rises_us
from wl_sync.rp1 import backend as rp1
from wl_sync.rp1 import program
from wl_sync.rp1.backend import Rp1Backend

DATA = 16

# The LOW after the start-up test's pulse, as the box sleeps it, read before the `lib`
# fixture sets it to 0.
_QUIET_ON_THE_BOX_S = rp1.STAMP_TEST_QUIET_S


def stream(*events):
    entries = []
    for tick, word in events:
        entries.append(word)
        entries.append((~tick) & 0xFFFFFFFF)
    return entries


class _FakeShim:
    """libwlsync_pio.so: every call succeeds, and `log` is the order of events.

    SM 0 is the word machine, SM 1 the stamp machine and SM 2 the start-up test's machine
    (Task 1.10a), each with its own RX FIFO: `fifo`, `full` and `push()` are SM 0's, as
    they were before the stamp machine existed, `stamp_fifo`, `stamp_full` and
    `push_stamps()` are SM 1's, and `test_fifo` is SM 2's. `drains` counts SM 0's drains
    alone. `fifo_calls` records each FIFO call's state machine and thread, and `calls` the
    thread of every call into the library, which is how a test sees that piolib is called
    from one thread while capture runs.

    `enabled` is the set of machines running, and `enables` every (sm, on) that
    `wlsync_sm_set_enabled` and `wlsync_enable_in_sync` set, in order; `on_sync` runs at
    each sync enable, where the PIO's count starts (`crystals`), the last one winning.
    `refuse_sync` makes every sync enable a refused one (C-7): it returns 0, as the shim
    does when piolib discards the refusal (pio/wlsync_pio.c:252-262), and runs nothing.
    `sync_errno` and `open_result` are what the shim itself can return.

    EVERY FIFO IS RP1'S: `fifo_depth`, 8, a way (pio_rp1.c:985 at utils b57c48e), joined, so
    `stamp_depth`, `word_depth` and `test_depth` are 16, and `wlsync_rx_fifo_depth` gives 16,
    as the shim does (Ruling R40; until then the fake used the backend's own constant, 8,
    and could not show a depth the backend did not assume, review M5). A test sets a depth
    of its own to model a join that did not take; `level_wrap` makes every level read
    modulo it, as a level field too narrow for the joined depth would; `box_answers`
    answers as the box's shim does, from a level field that saturates at 15 and a `full`
    flag (bench probe, wl-rig-sync0, 2026-11-20), through the shim's own compiled rule; and
    `level_failures[sm]` holds the errnos the next level reads of `sm` return, negated, as
    the shim's checked read returns a failed one, None for a read that goes through
    (pio/wlsync_pio.c, `wlsync_sm_rx_level`).

    THE STAMP MACHINE'S FIFO: `stamp_depth` words, joined as
    the shim configures it (pio/wlsync_pio.c:237). A word pushed to it
    full is dropped, as `push noblock` drops it, and counted in `stamp_dropped`, and
    `wlsync_sm_rx_full` reads it full, as `pio_sm_is_rx_fifo_full` reads FSTAT
    (pio/wlsync_pio.c:316). Until Task 1.15 it was unbounded (final review M11), so the
    fault that desynced the stream could not be built here. `between_words`, when set, is
    called between the two words of every pair pushed to it, which is where a drain must
    pop for a word to be left alone; `stamp_gate`, an Event, holds the stamp drain at its
    first call until it is set, as a pump the GIL keeps from running.

    SM 0'S FIFO TOO since Task 1.20: `word_depth` words, joined as the shim
    configures it (pio/wlsync_pio.c:183), a word pushed to it full dropped and counted in
    `word_dropped`, and `wlsync_sm_rx_full` reading it full as it reads SM 1's, or when
    `full` is set. `word_gate`, an Event, holds the word drain at its first call until it
    is set, and `word_held` is set once a drain waits there. Together they make the fault
    a desync comes from: a full FIFO, and a drain that pops between a pair's two pushes.

    Like piolib, its encoders answer correctly only once a PIO block is open -- piolib's
    read the CURRENT block -- so a backend that checks its encodings too early is refused
    here exactly as it would be on the box.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fifo_calls: list[tuple[str, int, str]] = []
        self.fifo: list[int] = []
        self.full = False
        self.stamp_fifo: list[int] = []
        self.stamp_full = False
        self.fifo_depth = 8
        self.stamp_depth = 2 * self.fifo_depth
        self.test_depth = 2 * self.fifo_depth
        self.level_wrap: int | None = None
        # Answer level reads as the box's shim does (bench probe, wl-rig-sync0, 2026-11-20):
        # the kernel's raw answer, its level field saturating at 15 and `full` set at the
        # FIFO's depth, read through the shim's own rule (tests/fifo_state_c.py).
        self.box_answers = False
        self.level_failures: dict[int, list[int]] = {}
        self.stamp_dropped = 0
        self.between_words = None
        self.stamp_gate: threading.Event | None = None
        self.word_depth = 2 * self.fifo_depth
        self.word_dropped = 0
        self.word_gate: threading.Event | None = None
        self.word_held = threading.Event()
        self.test_fifo: list[int] = []
        self.drains = 0
        self.log: list[str] = []
        self.opened = False
        self.clk_sys_hz = 200_000_000
        self.start_result = 0
        self.started_with: dict | None = None
        # The first `wlsync_start_stamp` starts the stamp machine, and returns
        # `stamp_result`; every later one starts the test machine, and returns
        # `test_result`. Each is an SM number, or a negative errno.
        self.stamp_result = 1
        self.test_result = 2
        # Every call's arguments, in order; `stamp_started_with` is the first's.
        self.stamp_starts: list[dict] = []
        self.stamp_started_with: dict | None = None
        # The test machine, once one has started, and each started machine's jmp pin.
        self.test_sm: int | None = None
        self.jmp_pins: dict[int, int] = {}
        self.enabled: set[int] = set()
        self.enables: list[tuple[int, bool]] = []
        self.on_sync = None
        self.refuse_sync = False
        # `wlsync_enable_in_sync(mask)` returns this errno, negated, for mask.
        self.sync_errno: dict[int, int] = {}
        # What `wlsync_pio_open` returns: 0, or a negative errno.
        self.open_result = 0
        # `wlsync_sm_set_enabled(sm, on)` returns this errno, negated, for (sm, on).
        self.set_enabled_errno: dict[tuple[int, bool], int] = {}
        self.disabled: list[int] = []
        # The next `wlsync_sm_get` on this SM raises this, once: a drain that fails.
        self.get_failures: dict[int, BaseException] = {}
        self._lock = threading.Lock()

    def __getattribute__(self, name):
        if name.startswith("wlsync_"):
            calls = object.__getattribute__(self, "calls")
            calls.append((name, threading.current_thread().name))
        return object.__getattribute__(self, name)

    def wlsync_pio_open(self, index):
        self.opened = self.open_result >= 0
        self.log.append("open")
        return self.open_result

    def wlsync_pio_close(self):
        self.log.append("close")

    def _encoded(self, value):
        self.log.append("encode")
        return value if self.opened else 0xFFFF

    def wlsync_encode_jmp_pin(self, addr):
        return self._encoded(program.encode_jmp_pin(addr))

    def wlsync_encode_jmp_x_dec(self, addr):
        return self._encoded(program.encode_jmp_x_dec(addr))

    def wlsync_encode_in_pins(self, count):
        return self._encoded(program.encode_in_pins(count))

    def wlsync_encode_push_noblock(self):
        return self._encoded(program.encode_push_noblock())

    def wlsync_encode_mov_isr_x(self):
        return self._encoded(program.encode_mov_isr_x())

    def wlsync_encode_mov_x_not_null(self):
        return self._encoded(program.encode_mov_x_not_null())

    def wlsync_clk_sys_hz(self):
        return self.clk_sys_hz

    def wlsync_start_capture(self, instructions, length, wrap_target, wrap,
                             entry, data_base, data_count, strobe_pin, clkdiv, seed):
        self.log.append("start_capture")
        self.started_with = {
            "length": length, "wrap_target": wrap_target, "wrap": wrap,
            "entry": entry,
            "data_base": data_base, "data_count": data_count, "strobe_pin": strobe_pin,
            "clkdiv": clkdiv, "seed": seed,
        }
        return self.start_result

    def wlsync_start_stamp(self, wrap_target, wrap, entry, data_base, jmp_pin,
                           claim_jmp_pin, clkdiv, seed):
        self.log.append("start_stamp")
        started_with = {
            "wrap_target": wrap_target, "wrap": wrap, "entry": entry,
            "data_base": data_base, "jmp_pin": jmp_pin,
            "claim_jmp_pin": claim_jmp_pin, "clkdiv": clkdiv, "seed": seed,
        }
        self.stamp_starts.append(started_with)
        if len(self.stamp_starts) == 1:
            self.stamp_started_with = started_with
            result = self.stamp_result
        else:
            result = self.test_result
            if result >= 0:
                self.test_sm = result
        if result >= 0:
            self.jmp_pins[result] = jmp_pin
        return result

    def wlsync_enable_in_sync(self, mask):
        self.log.append(f"sync={mask:#x}")
        errno = self.sync_errno.get(mask)
        if errno is not None:
            return -errno
        if self.refuse_sync:
            return 0
        for sm in range(4):
            if mask >> sm & 1:
                self.enabled.add(sm)
                self.enables.append((sm, True))
        if self.on_sync is not None:
            self.on_sync()
        return 0

    def wlsync_sm_set_enabled(self, sm, enabled):
        self.log.append(f"enabled={bool(enabled)}")
        self.enables.append((sm, bool(enabled)))
        errno = self.set_enabled_errno.get((sm, bool(enabled)))
        if errno is not None:
            return -errno
        if enabled:
            self.enabled.add(sm)
        else:
            self.enabled.discard(sm)
            self.disabled.append(sm)
        return 0

    def sm_enabled_never(self, sm) -> bool:
        """`sm` was never enabled, alone or in sync."""
        return (sm, True) not in self.enables

    @property
    def testing(self) -> bool:
        """The test machine is running: what GPIO17 does now is the start-up test's pulse."""
        return self.test_sm is not None and self.test_sm in self.enabled

    def watching(self, pin) -> list[int]:
        """The running machines whose jmp pin is `pin`: those a rise on it is stamped by."""
        return sorted(sm for sm in self.enabled if self.jmp_pins.get(sm) == pin)

    def push(self, entries) -> None:
        self.push_to(0, entries)

    def push_stamps(self, entries) -> None:
        self.push_to(1, entries)

    def push_to(self, sm, entries) -> None:
        if sm == 0:
            with self._lock:
                for word in entries:
                    if len(self.fifo) < self.word_depth:
                        self.fifo.append(word)
                    else:
                        self.word_dropped += 1
            return
        if sm == 2:
            with self._lock:
                for word in entries:
                    if len(self.test_fifo) < self.test_depth:
                        self.test_fifo.append(word)
            return
        if sm != 1:
            with self._lock:
                self._fifo_of(sm).extend(entries)
            return
        for index, word in enumerate(entries):
            if index % 2 and self.between_words is not None:
                self.between_words()
            with self._lock:
                if len(self.stamp_fifo) < self.stamp_depth:
                    self.stamp_fifo.append(word)
                else:
                    self.stamp_dropped += 1

    def _fifo_of(self, sm):
        return {1: self.stamp_fifo, 2: self.test_fifo}.get(sm, self.fifo)

    def _witness(self, call, sm):
        self.fifo_calls.append((call, sm, threading.current_thread().name))

    def wlsync_sm_rx_full(self, sm):
        self._witness("rx_full", sm)
        if sm == 1:
            gate = self.stamp_gate
            if gate is not None:
                gate.wait(timeout=5)
            with self._lock:
                full = len(self.stamp_fifo) >= self.stamp_depth
            return 1 if self.stamp_full or full else 0
        if sm == 2:
            return 0
        gate = self.word_gate
        if gate is not None:
            self.word_held.set()
            gate.wait(timeout=5)
        self.drains += 1  # the first call of every drain
        with self._lock:
            full = len(self.fifo) >= self.word_depth
        return 1 if self.full or full else 0

    def wlsync_sm_rx_level(self, sm):
        self._witness("rx_level", sm)
        failures = self.level_failures.get(sm)
        errno = failures.pop(0) if failures else None  # None: this read goes through
        if errno is not None:
            return -errno
        with self._lock:
            level = len(self._fifo_of(sm))
        if self.box_answers:
            from tests import fifo_state_c

            depth = {0: self.word_depth, 1: self.stamp_depth, 2: self.test_depth}[sm]
            return fifo_state_c.answer()(
                8, 0, min(level, 15), int(level == 0), int(level >= depth),
                self.wlsync_rx_fifo_depth(),
            )
        return level % self.level_wrap if self.level_wrap else level

    def wlsync_rx_fifo_depth(self):
        return 2 * self.fifo_depth

    def wlsync_sm_get(self, sm):
        self._witness("get", sm)
        failure = self.get_failures.pop(sm, None)
        if failure is not None:
            raise failure
        with self._lock:
            return self._fifo_of(sm).pop(0)


@pytest.fixture
def lib(monkeypatch, gpiod):
    fake = _FakeShim()
    monkeypatch.setattr(rp1, "_load_shim", lambda: fake)
    # The LOW after the start-up test's pulse, 0.5 s on the box, which every backend built
    # on the real clock would sleep. The quiet's own tests set it back.
    monkeypatch.setattr(rp1, "STAMP_TEST_QUIET_S", 0)
    gpiod.shim = fake  # where the stamp machine's stamps land
    return fake


def _until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.001)
    return True


def _next_complaint(backend, timeout=1.0):
    """What check_health() next raises, or None if it stays healthy until the timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            backend.check_health()
        except RuntimeError as error:
            return str(error)
        time.sleep(0.001)
    return None


def _started(lib, on_word):
    backend = Rp1Backend()
    backend.start_strobed_capture(0, DATA, DATA, on_word)
    return backend


# The strobes that fill the fake's word FIFO, 16 words joined (`_FakeShim`), in
# `_lose_a_counters_data_word`: what is delivered before the lone counter.
FILLING = [(10 * k, k) for k in range(1, 9)]


def _lose_a_counters_data_word(lib):
    """The fault a code-word desync comes from, as the FIFO makes it (Task 1.20). The word
    drain is held while the strobes of FILLING fill the FIFO, so strobe 90's data word finds
    it full and is dropped. One drain then pops all of it, and only then does 90's counter
    come, alone, where a data word belongs. Returns once it is in the FIFO."""
    gate = threading.Event()
    lib.word_held.clear()
    lib.word_gate = gate
    assert lib.word_held.wait(1), "the word drain never came to the gate"
    lib.push(stream(*FILLING) + [9])
    assert lib.word_dropped == 1, "90's data word found room"
    drains = lib.drains
    lib.word_gate = None
    gate.set()
    assert _until(lambda: lib.drains >= drains + 2)  # the held drain has popped them all
    lib.push([(~90) & 0xFFFFFFFF])


def test_capture_starts_at_the_programs_entry(lib):
    backend = _started(lib, lambda tick, word: None)
    try:
        assert lib.started_with["entry"] == program.ENTRY
    finally:
        backend.close()


def test_both_machines_are_configured_at_construction_and_enabled_together_at_capture(
    lib, monkeypatch
):
    """Configured before the first segment, so the start-up test's verdict reaches its
    header, and enabled in sync once capture starts, so W and B count one P. The test
    machine runs alone at construction, enabled in a sync mask of its own since Task 1.16
    (this test asserted no sync enable before capture until then), and is not in the
    production mask."""
    real = rp1.time.clock_gettime_ns
    names = {time.CLOCK_MONOTONIC: "origin", time.CLOCK_MONOTONIC_RAW: "raw"}
    capturing = threading.Event()

    def clock(which):
        # Only the origin's reads happen between capture's start and the enable: MONO for
        # now_us(), then RAW for the fit. The pump's reads come after the enable.
        if capturing.is_set() and "sync=0x3" not in lib.log:
            lib.log.append(names.get(which, f"clock {which}"))
        return real(which)

    monkeypatch.setattr(rp1.time, "clock_gettime_ns", clock)
    backend = Rp1Backend()
    try:
        built = len(lib.log)
        assert [e for e in lib.log if e.startswith("start_")] == [
            "start_capture", "start_stamp", "start_stamp",
        ]
        assert [s["jmp_pin"] for s in lib.stamp_starts] == [17, 17]
        assert not {0, 1} & {sm for sm, on in lib.enables if on}
        assert [e for e in lib.log if e.startswith("sync=")] == ["sync=0x4"]

        capturing.set()
        backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
        started = [e for e in lib.log[built:]
                   if e.startswith(("start_", "sync=", "enabled=", "clock "))
                   or e in names.values()]
        assert started == ["origin", "raw", "sync=0x3"]  # SM 0 and SM 1; not SM 2
    finally:
        backend.close()


def test_the_stamp_machine_reads_gpio17_itself_by_default_and_starts_at_the_entry(lib):
    backend = _started(lib, lambda tick, word: None)
    try:
        assert lib.stamp_started_with["jmp_pin"] == 17
        assert lib.stamp_started_with["claim_jmp_pin"] == 0
        assert lib.stamp_started_with["entry"] == program.ENTRY
        assert lib.stamp_started_with["data_base"] == 0
    finally:
        backend.close()


def test_a_spare_pin_is_claimed_for_the_bench_controls(lib):
    backend = Rp1Backend(stamp_pin=19)
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    try:
        assert lib.stamp_started_with["jmp_pin"] == 19
        assert lib.stamp_started_with["claim_jmp_pin"] == 1
    finally:
        backend.close()


def test_a_stamp_pin_that_is_not_an_int_is_refused(lib):
    """17.0 == 17 would pass a membership test and fail later inside ctypes."""
    with pytest.raises(ValueError):
        Rp1Backend(stamp_pin=17.0)


@pytest.mark.parametrize("pin", [16, 20])
def test_a_pin_the_board_allocates_is_refused_as_a_stamp_pin(lib, pin):
    """Claiming it would take it from its owner: the strobe, or an edge input."""
    with pytest.raises(ValueError, match=(
        rf"the stamp machine reads GPIO17 itself, or a spare pin \(GPIO18, GPIO19\) "
        rf"for the bench controls, not GPIO{pin}"
    )):
        Rp1Backend(stamp_pin=pin)


def test_close_disables_both_machines(lib):
    """And the test machine was disabled once, at construction, where its test ended."""
    backend = _started(lib, lambda tick, word: None)
    assert lib.disabled == [2]
    backend.close()
    assert lib.disabled[0] == 2 and sorted(lib.disabled[1:]) == [0, 1]


# --- The start-up test (Task 1.10a) ------------------------------------------------------
#
# Before the first segment, a third state machine on GPIO17 (the test machine) is enabled
# alone, GPIO17 is pulsed once, and the test machine's FIFO is read. A stamp keeps the
# process on one count; anything else puts it on the split clock, with one ERROR.

_SPLIT_ERROR = (
    "the stamp machine's start-up test failed ({}): this process records timebase split, "
    "with B and E on CLOCK_MONOTONIC and W on RP1's crystal, until a restart passes it"
)


# Both pulses unstamped: the PIO does not see GPIO17 rise (Task 1.16). The text is the
# journal's, so it is spelled out here.
_BLIND = (
    "no stamp when GPIO17 rose, enabled in sync or alone: the PIO does not see GPIO17 rise"
)


def _errors(caplog):
    import logging

    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def _is_split(backend):
    return (backend.timebase, backend.barcode_delay_us) == ("split", None)


def _capture_enables(lib, backend):
    """What starting capture enabled: (sm, on) in order, and whether it enabled in sync."""
    enables, log = len(lib.enables), len(lib.log)
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    synced = any(e.startswith("sync=") for e in lib.log[log:])
    return lib.enables[enables:], synced


def test_a_stamp_machine_that_sees_gpio17_leaves_the_process_on_one_count(lib, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger=rp1.log.name):
        backend = Rp1Backend()
    try:
        built = threading.current_thread().name
        assert backend.stamp_test_failure == ""
        assert backend.timebase == "rp1-pio" and backend.barcode_delay_us == 0
        assert (
            "the stamp machine saw GPIO17 rise at start-up: B is the stamp machine's count "
            "of each lead edge"
        ) in [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert _errors(caplog) == []
        _, test = lib.stamp_starts
        assert (test["jmp_pin"], test["claim_jmp_pin"]) == (17, 0)
        assert lib.enables == [(2, True), (2, False)]
        # Read on the constructing thread, before any pump thread existed.
        reads = {(call, thread) for call, sm, thread in lib.fifo_calls if sm == 2}
        assert {call for call, _ in reads} >= {"rx_level", "get"}
        assert {thread for _, thread in reads} == {built}
        assert "wl-sync-words" not in {thread for _, thread in lib.calls}
        assert backend._word_thread is None
    finally:
        backend.close()


def test_a_stamp_machine_blind_to_gpio17_puts_the_process_on_the_split_clock_with_an_error(
    lib, gpiod, caplog
):
    import logging

    gpiod.pio_sees_barcode = False
    with caplog.at_level(logging.INFO, logger=rp1.log.name):
        backend = Rp1Backend()
    try:
        assert backend.stamp_test_failure == _BLIND
        assert _is_split(backend)
        assert _errors(caplog) == [_SPLIT_ERROR.format(_BLIND)]
        enables, synced = _capture_enables(lib, backend)
        assert enables == [(0, True)] and not synced
    finally:
        backend.close()


def test_a_stamp_machine_that_cannot_start_puts_the_process_on_the_split_clock(
    lib, caplog
):
    """It used to record on one count with every B dropped (Ruling R12). No test machine
    is started: there is no stamp machine for it to vouch for."""
    import logging

    lib.stamp_result = -16  # -EBUSY: another client holds the block's machines
    with caplog.at_level(logging.INFO, logger=rp1.log.name):
        backend = Rp1Backend()
    try:
        reason = "the stamp machine did not start (errno 16)"
        assert backend.stamp_test_failure == reason
        assert _is_split(backend)
        assert lib.log.count("start_stamp") == 1
        assert lib.enables == []
        assert _errors(caplog) == [_SPLIT_ERROR.format(reason)]
        enables, synced = _capture_enables(lib, backend)
        assert enables == [(0, True)] and not synced
    finally:
        backend.close()
    assert lib.disabled == [0]


def test_a_test_machine_that_cannot_start_falls_back_and_never_raises(lib, gpiod, caplog):
    lib.test_result = -16
    backend = Rp1Backend()
    try:
        reason = "the test machine did not start (errno 16)"
        assert backend.stamp_test_failure == reason
        assert _is_split(backend)
        assert _errors(caplog) == [_SPLIT_ERROR.format(reason)]
        assert lib.enables == [] and gpiod.sets == []  # nothing to watch a pulse
    finally:
        backend.close()


@pytest.mark.parametrize("where", ["request", "pulse"])
def test_a_test_that_cannot_run_falls_back_and_never_raises(lib, gpiod, caplog, where):
    """GPIO17 cannot be taken, or a level cannot be set mid-pulse. The test machine is
    started only once the line is held, and is disabled on every path after."""
    if where == "request":
        def busy(path, config, consumer, **kwargs):
            raise OSError("busy")

        gpiod.request_lines = busy
        error = "busy"
    else:
        gpiod.set_failures = [None, OSError("EIO")]  # the rise is set; the fall raises
        error = "EIO"
    backend = Rp1Backend()
    try:
        reason = f"the test could not run ({error})"
        assert backend.stamp_test_failure == reason
        assert _is_split(backend)
        assert _errors(caplog) == [_SPLIT_ERROR.format(reason)]
        if where == "request":
            assert lib.log.count("start_stamp") == 1 and lib.enables == []
        else:
            assert lib.enables == [(2, True), (2, False)] and lib.enabled == set()
    finally:
        backend.close()


@pytest.mark.parametrize("on", [True, False], ids=["enable", "disable"])
def test_a_test_machine_that_cannot_be_switched_falls_back_and_never_raises(
    lib, caplog, on
):
    """A disable that fails after a pass fails the test too: the block refused a call the
    production machines need, and the test machine may still be running. Since Task 1.16
    the test machine is enabled through the sync enable, whose one errno is the shim's
    own (pio/wlsync_pio.c:256-262); this test enabled it through wlsync_sm_set_enabled
    until then."""
    if on:
        lib.sync_errno[0x4] = 19
    else:
        lib.set_enabled_errno[(2, False)] = 5
    backend = Rp1Backend()
    try:
        what = "enabling the test machine in sync failed: errno 19" if on else (
            "disabling the test machine failed: errno 5"
        )
        reason = f"the test could not run ({what})"
        assert backend.stamp_test_failure == reason
        assert _is_split(backend)
        assert lib.enables[-1] == (2, False), "the test machine was not disabled"
        assert _errors(caplog) == [_SPLIT_ERROR.format(reason)]
    finally:
        backend.close()


def test_the_test_pulse_is_one_the_decoders_reject(lib, gpiod, monkeypatch):
    """One rise and one fall, STAMP_TEST_HIGH_US apart: the line is low again a quarter
    of LEAD in, where a lead must still be high (wl_sync/barcode.py:40, :135-136). Since
    Ruling R40 the depth check's burst follows it (`_measure_fifo_depth`): depth/2 + 1
    pulses of STAMP_TEST_SETTLE_US, and the decoder rejects the whole of it too."""
    from wl_sync.barcode import FRAME_US, IDLE_MIN_US, decode_edges

    monkeypatch.setattr(rp1, "time", gpiod.clock)
    Rp1Backend().close()
    (rise_ns, high), (fall_ns, low), *burst = gpiod.sets
    assert (high, low) == (1, 0)
    assert rp1.STAMP_TEST_HIGH_US == 1_000
    assert (fall_ns - rise_ns) / 1_000 == pytest.approx(rp1.STAMP_TEST_HIGH_US, abs=20)
    assert (fall_ns - rise_ns) / 1_000 < 2_500
    assert [level for _, level in burst] == [1, 0] * (lib.test_depth // 2 + 1)
    assert (burst[-1][0] - rise_ns) / 1_000 < 10_000, "the burst outlasts a quarter LEAD"
    # The idle before it is known, and the record runs a frame past it, so the decoder
    # does test it as a lead.
    edges = [(ns // 1_000, level) for ns, level in gpiod.sets]
    edges.append((rise_ns // 1_000 + 2 * FRAME_US, 0))
    assert decode_edges(edges, start_us=rise_ns // 1_000 - IDLE_MIN_US) == []


def test_the_first_frame_decodes_after_the_test_pulse(lib, gpiod, monkeypatch):
    """STAMP_TEST_QUIET_S of LOW after the pulse, more than IDLE_MIN_US, so a decoder that
    takes the pulse's fall for the end of the idle still finds the first frame."""
    from wl_sync.barcode import IDLE_MIN_US, decode_edges, encode

    assert _QUIET_ON_THE_BOX_S == 0.5
    monkeypatch.setattr(rp1, "STAMP_TEST_QUIET_S", _QUIET_ON_THE_BOX_S)
    monkeypatch.setattr(rp1, "time", gpiod.clock)  # where the quiet costs nothing
    backend = Rp1Backend()
    try:
        backend.emit_pulses(17, encode(1000))
        gpiod.clock.sleep(0.8)
        backend.emit_pulses(17, encode(1001))
    finally:
        backend.close()
    edges = []
    for ns, level in gpiod.sets:
        if not edges or edges[-1][1] != level:
            edges.append((ns // 1000, level))
    # The test's pulse, then the depth check's burst (`_measure_fifo_depth`), then the frame.
    startup = 2 * (1 + lib.test_depth // 2 + 1)
    (_, high), (_, low) = edges[:2]
    fall_us, lead_us = edges[startup - 1][0], edges[startup][0]
    assert (high, low) == (1, 0)
    barcodes = decode_edges(edges, start_us=edges[0][0] - IDLE_MIN_US)
    assert barcodes[0].value == 1000 and barcodes[0].start_us == lead_us
    assert lead_us - fall_us > IDLE_MIN_US


def test_the_test_machines_stamp_never_reaches_a_frame(lib, gpiod, crystals):
    """Its stamp went to SM 2, which no pump drains: had the test used the stamp machine,
    the first frame would carry it as a stamp between."""
    from wl_sync.barcode import encode

    crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        first = backend.emit_pulses(17, encode(1000))
    finally:
        backend.close()
    assert first.between == 0
    assert first.tick == gpiod.leads[1]
    # The test's rise, then the depth check's (`_measure_fifo_depth`): all to SM 2.
    test_stamps = [p for sm, p in gpiod.pushes if sm == 2]
    rises = 1 + lib.test_depth // 2 + 1
    assert len(test_stamps) == rises and gpiod.pushes[:rises] == [(2, p) for p in test_stamps]
    assert {sm for sm, _ in gpiod.pushes[rises:]} == {1}
    drained = {sm for _, sm, thread in lib.fifo_calls if thread == "wl-sync-words"}
    assert 2 not in drained and 1 in drained


def test_start_strobed_capture_refuses_pins_other_than_those_configured(lib):
    """The machines are configured at construction, on wl_sync.pins' capture window."""
    backend = Rp1Backend()
    try:
        with pytest.raises(ValueError, match="configured"):
            backend.start_strobed_capture(0, 8, 8, lambda tick, word: None)
        # The strobe's placement is checked first, as before.
        with pytest.raises(ValueError, match="immediately above"):
            backend.start_strobed_capture(0, 8, 9, lambda tick, word: None)
        assert backend._word_thread is None and not backend._started
    finally:
        backend.close()


def test_with_a_spare_stamp_pin_the_test_still_reads_gpio17(lib):
    """Bench S.3 and S.3b's controls put the stamp machine on an unwired spare pin. The
    test is of the PIO's sight of GPIO17, whatever the stamp machine reads."""
    backend = Rp1Backend(stamp_pin=19)
    try:
        stamp, test = lib.stamp_starts
        assert (stamp["jmp_pin"], stamp["claim_jmp_pin"]) == (19, 1)
        assert (test["jmp_pin"], test["claim_jmp_pin"]) == (17, 0)
        assert backend.stamp_test_failure == ""
        assert backend.timebase == "rp1-pio"
    finally:
        backend.close()


@pytest.mark.parametrize("view", ["stuck-high", "inverted"])
def test_a_pio_view_of_gpio17_that_does_not_follow_its_rise_fails_the_test(
    lib, gpiod, view, monkeypatch
):
    """Ruling R29: the test witnesses the rise. The idle `jmp pin` tests the level
    (wl_sync/rp1/program.py:100), so a PIO that sees GPIO17 high while it is low captures
    as soon as the test machine is enabled, and one that sees it inverted stamps the fall
    as well. Neither stamps the rise, and on one count each would lose every B, yet by
    the time the pulse ends each has put a stamp in the FIFO.

    Both views stamp at EVERY enable of the test machine: the sync enable, which the first
    pulse runs on and whose verdict decides between one count and split, and the plain
    one, which the diagnostic second pulse runs on (Task 1.16). Until Task 1.16's fix round
    only the plain enable stamped, so a build that skipped the first pulse's discard
    passed here (Task 1.16's review, Important 1). Each pulse's verdict is asserted:
    both find no stamp of the rise."""
    stamp_of_the_enable = [0, (~3) & 0xFFFF_FFFF]
    enable, enable_in_sync = lib.wlsync_sm_set_enabled, lib.wlsync_enable_in_sync

    def enable_seeing_high(sm, on):  # GPIO17 is low, and this view sees it high
        result = enable(sm, on)
        if on and sm == lib.test_sm:
            lib.push_to(sm, stamp_of_the_enable)
        return result

    def enable_in_sync_seeing_high(mask):
        result = enable_in_sync(mask)
        if lib.test_sm is not None and mask >> lib.test_sm & 1:
            lib.push_to(lib.test_sm, stamp_of_the_enable)
        return result

    verdicts = []
    rise_stamped = Rp1Backend._rise_stamped

    def witnessed(self, test):
        verdicts.append(rise_stamped(self, test))
        return verdicts[-1]

    monkeypatch.setattr(Rp1Backend, "_rise_stamped", witnessed)
    lib.wlsync_sm_set_enabled = enable_seeing_high
    lib.wlsync_enable_in_sync = enable_in_sync_seeing_high
    gpiod.pio_sees_barcode = False  # nothing stamped at the rise
    if view == "inverted":
        request = gpiod.request_lines

        def request_inverted(*args, **kwargs):
            line = request(*args, **kwargs)
            set_value = line.set_value

            def set_value_stamping_the_fall(pin, value):
                set_value(pin, value)
                if pin == 17 and value == gpiod.line.Value.INACTIVE and lib.testing:
                    lib.push_to(lib.test_sm, [0, (~1_003) & 0xFFFF_FFFF])

            line.set_value = set_value_stamping_the_fall
            return line

        gpiod.request_lines = request_inverted
    backend = Rp1Backend()
    try:
        assert verdicts == [False, False]  # the first pulse alone fails the test
        assert backend.stamp_test_failure == _BLIND
        assert _is_split(backend)
    finally:
        backend.close()
    # The settle that lets a stamp at the enable land before it is discarded, and the
    # read after the rise: more than the 5.5 us a pair takes
    # (wl_sync/rp1/pio_capture.py:352-357), and well inside the pulse.
    assert 5.5 < rp1.STAMP_TEST_SETTLE_US < rp1.STAMP_TEST_HIGH_US / 2


def test_each_read_of_the_test_fifo_waits_out_the_settle(lib, gpiod, monkeypatch):
    """Coverage: both settles of the start-up test (Task 1.10a's re-review). The discard
    comes a settle after the enable and before the rise, and the read a settle after the
    rise; without this, removing either settle passed every other test. The enable is
    the sync enable's since Task 1.16 (wlsync_sm_set_enabled's until then)."""
    monkeypatch.setattr(rp1, "time", gpiod.clock)
    at = {}
    enable, level = lib.wlsync_enable_in_sync, lib.wlsync_sm_rx_level

    def enable_in_sync(mask):
        if mask == 1 << 2:
            at["enabled"] = gpiod.clock.ns
        return enable(mask)

    def rx_level(sm):
        if sm == 2:
            at.setdefault("reads", []).append(gpiod.clock.ns)
        return level(sm)

    lib.wlsync_enable_in_sync, lib.wlsync_sm_rx_level = enable_in_sync, rx_level
    Rp1Backend().close()
    (rise_ns, _), *_ = gpiod.sets
    discard, read = at["reads"][:2]  # then the depth's reads (`_measure_fifo_depth`)
    settle_ns = rp1.STAMP_TEST_SETTLE_US * 1_000
    assert discard - at["enabled"] >= settle_ns
    assert discard < rise_ns and read - rise_ns >= settle_ns


@pytest.mark.parametrize("fault", ["encoders", "clk_sys"])
def test_a_block_that_cannot_be_trusted_closes_before_raising(lib, fault, monkeypatch):
    """The shim's open overwrites the block it holds without closing it
    (pio/wlsync_pio.c:77-94), so a caller that retried would leak a block a try. These
    two still raise (Task 1.16, the user's ruling on its Q3): an encoding that disagrees
    would put wrong ticks in every W, and an impossible clk_sys would scale every tick by
    it. A capture machine that cannot be configured no longer raises: see below."""
    if fault == "encoders":
        def disagree(_lib):
            raise rp1.PiolibError("an encoder disagrees")

        monkeypatch.setattr(rp1, "_assert_encoders_agree", disagree)
    else:
        lib.clk_sys_hz = 0xFFFF_FFFF
    with pytest.raises(rp1.PiolibError):
        Rp1Backend()
    assert lib.log.count("open") == 1 and lib.log[-1] == "close"


# --- A capture machine that cannot run records anyway (Task 1.16) ------------------------
#
# The user's rulings of 2026-11-20: a process whose capture machine cannot be configured,
# whose shim cannot be loaded, or whose PIO block cannot be opened records split with no W
# records, says so in one ERROR, and the health check holds a "capture degraded" episode.
# A capture machine that cannot be enabled at capture's start records on without W.

_NO_CAPTURE_ERROR = (
    "event-code capture cannot run ({}): this process records timebase split with no W "
    "records, and B and E on CLOCK_MONOTONIC, until a restart starts the capture machine"
)
_NO_CAPTURE_HEALTH = (
    "event-code capture did not start ({}): no code word is recorded in this process"
)


def _records_without_w(backend, gpiod, reason):
    """Capture starts with no word thread, the health check reports W on every call, and
    B (in software, split's) and E still record.

    Coverage: E's delivery, from the Task 1.16 review (Minor 2). Split's ordinary edge
    path, so it passed when added; edge capture that started and delivered nothing in
    a process with no W fails every test that calls this."""
    from wl_sync.backend import CaptureDegraded, FrameStamp
    from wl_sync.barcode import encode

    words = []
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: words.append(word))
    assert backend._word_thread is None
    assert backend.delivered_through() == {}
    for _ in range(2):  # every call: a path that never started never comes back
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
        assert raised.value.kinds == {"event-code capture: did not start"}
        assert str(raised.value) == _NO_CAPTURE_HEALTH.format(reason)
    edges = []
    backend.start_edge_capture([20], lambda gpio, level, tick: edges.append(tick))
    gpiod.edges.append(_edge_stamped(1, backend._origin_ns + 1_500_000))
    assert _until(lambda: edges), "no E delivered"
    assert edges == [1_500]  # MONO us since the origin, as split counts it
    stamp = backend.emit_pulses(17, encode(1000))
    assert isinstance(stamp, FrameStamp) and stamp.tick is not None
    assert any(level == 1 for _, level in gpiod.sets)
    assert words == []


def test_a_capture_machine_that_cannot_be_configured_records_split_without_w(
    lib, gpiod, caplog
):
    """It raised from the constructor until Task 1.16 (the test that pinned that,
    test_a_capture_that_cannot_start_raises_rather_than_recording_nothing, is gone), and
    under Restart=always the unit crash-looped. Now nothing PIO runs: the block is closed
    at once, no stamp or test machine is started, and the process records split."""
    lib.start_result = -16  # -EBUSY: no free state machine
    backend = Rp1Backend()
    try:
        reason = "the capture machine did not start (errno 16)"
        assert _is_split(backend)
        assert backend.stamp_test_failure == reason  # so bench stamps refuses it
        assert _errors(caplog) == [_NO_CAPTURE_ERROR.format(reason)]
        assert "start_stamp" not in lib.log
        assert lib.log[lib.log.index("start_capture") + 1] == "close"
        assert lib.enables == [] and gpiod.sets == []  # no test pulse: nothing to test
        _records_without_w(backend, gpiod, reason)
        assert lib.enables == []
    finally:
        backend.close()
    assert _errors(caplog) == [_NO_CAPTURE_ERROR.format(reason)]


@pytest.mark.parametrize("fault", ["missing", "version"])
def test_a_shim_that_cannot_be_loaded_records_split_without_w(
    lib, gpiod, caplog, monkeypatch, fault
):
    """The user's ruling on Task 1.16's Q3: no PIO at all is a capture machine that cannot
    run. The reason is the load's own message, which names the rebuild."""
    message = {
        "missing": "libwlsync_pio not found (no such file)",
        "version": "libwlsync_pio is version 2, and this wl-sync needs 3",
    }[fault]

    def unloadable():
        raise rp1.PiolibError(message)

    monkeypatch.setattr(rp1, "_load_shim", unloadable)
    backend = Rp1Backend()
    try:
        assert _is_split(backend)
        assert _errors(caplog) == [_NO_CAPTURE_ERROR.format(message)]
        _records_without_w(backend, gpiod, message)
    finally:
        backend.close()  # with no library, close calls none
    assert lib.log == []


def test_a_pio_block_that_cannot_be_opened_records_split_without_w(lib, gpiod, caplog):
    lib.open_result = -2  # -ENOENT: no /dev/pio0
    backend = Rp1Backend()
    try:
        reason = "opening PIO block 0 failed: errno 2"
        assert _is_split(backend)
        assert _errors(caplog) == [_NO_CAPTURE_ERROR.format(reason)]
        assert lib.log == ["open"]
        _records_without_w(backend, gpiod, reason)
    finally:
        backend.close()


def test_a_capture_machine_that_cannot_be_enabled_records_on_without_w(
    lib, gpiod, caplog
):
    """In a split process, at capture's start, after the first header: "split" stays true
    of a segment with no W records, so the process records on (Task 1.16)."""
    lib.stamp_result = -16  # split, as a failed start-up test leaves it
    lib.set_enabled_errno[(0, True)] = 5
    backend = Rp1Backend()
    try:
        reason = "enabling the state machine failed: errno 5"
        before = _errors(caplog)
        _records_without_w(backend, gpiod, reason)
        assert _errors(caplog)[len(before):] == [(
            f"event-code capture could not start ({reason}): this process records no W "
            "records, and B and E go on, on CLOCK_MONOTONIC, until a restart starts the "
            "capture machine"
        )]
    finally:
        backend.close()


def test_machines_that_cannot_be_enabled_in_sync_record_on_without_w_or_b(
    lib, gpiod, caplog
):
    """On one count, at capture's start. The shim returns an errno here only with no block
    open (pio/wlsync_pio.c:256-262), which a backend that opened one does not reach; if
    it did, the process records on: E through the fit, extrapolated, and every B dropped
    as not stamped, with W reported by the health check."""
    from wl_sync.backend import STAMPS_NOT_STARTED, CaptureDegraded, FrameStamp
    from wl_sync.barcode import encode

    lib.sync_errno[0x3] = 19
    backend = Rp1Backend()
    try:
        assert backend.timebase == "rp1-pio"
        backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
        reason = "enabling the state machines failed: errno 19"
        assert _errors(caplog) == [(
            f"the capture and stamp machines could not start ({reason}): this process "
            "records no W and no B records, and E goes on, extrapolated from the sync "
            "enable, until a restart starts them"
        )]
        assert backend._word_thread is None
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
        assert str(raised.value) == _NO_CAPTURE_HEALTH.format(reason)
        backend.start_edge_capture([20], lambda gpio, level, tick: None)
        assert backend.emit_pulses(17, encode(1000)) == FrameStamp(None, STAMPS_NOT_STARTED)
    finally:
        backend.close()


# --- A refused sync enable fails the start-up test (Task 1.16, C-7) ----------------------

def test_the_test_machine_is_enabled_through_the_sync_enable(lib):
    """The call the production machines are enabled by, alone in its mask, so a box that
    refuses it fails the test before the first segment."""
    backend = Rp1Backend()
    try:
        assert backend.stamp_test_failure == ""
        assert [e for e in lib.log if e.startswith(("sync=", "enabled="))] == [
            "sync=0x4", "enabled=False",
        ]
    finally:
        backend.close()


def test_a_refused_sync_enable_puts_the_process_on_the_split_clock(lib, gpiod, caplog):
    """The second pulse, with the plain enable, is stamped, so the ERROR names the sync
    enable rather than the PIO's sight of GPIO17."""
    lib.refuse_sync = True
    backend = Rp1Backend()
    try:
        reason = (
            "the sync enable was refused: the test machine stamped GPIO17's rise only "
            "when enabled alone"
        )
        assert backend.stamp_test_failure == reason
        assert _is_split(backend)
        assert _errors(caplog) == [_SPLIT_ERROR.format(reason)]
        assert [level for _, level in gpiod.sets] == [1, 0, 1, 0]
        enables, synced = _capture_enables(lib, backend)
        assert enables == [(0, True)] and not synced
    finally:
        backend.close()


def test_the_two_test_pulses_are_rejected_and_the_first_frame_follows(
    lib, gpiod, monkeypatch
):
    """On the failure path the second pulse comes STAMP_TEST_QUIET_S after the first: each
    is a 1 ms pulse after more than IDLE_MIN_US of LOW, which no decoder takes for a
    frame, and the first frame still decodes after the second."""
    from wl_sync.barcode import IDLE_MIN_US, decode_edges, encode

    monkeypatch.setattr(rp1, "STAMP_TEST_QUIET_S", _QUIET_ON_THE_BOX_S)
    monkeypatch.setattr(rp1, "time", gpiod.clock)
    lib.refuse_sync = True
    backend = Rp1Backend()
    try:
        backend.emit_pulses(17, encode(1000))
        gpiod.clock.sleep(0.8)
        backend.emit_pulses(17, encode(1001))  # so the decoder sees the first one end
    finally:
        backend.close()
    edges = []
    for ns, level in gpiod.sets:
        if not edges or edges[-1][1] != level:
            edges.append((ns // 1000, level))
    (rise1, _), (fall1, _), (rise2, _), (fall2, _), (lead, _) = edges[:5]
    assert rise2 - fall1 > IDLE_MIN_US and lead - fall2 > IDLE_MIN_US
    assert fall1 - rise1 < 2_500 and fall2 - rise2 < 2_500
    barcodes = decode_edges(edges, start_us=rise1 - IDLE_MIN_US)
    assert [(b.value, b.start_us) for b in barcodes][0] == (1000, lead)


def test_a_frame_before_capture_starts_is_dropped_as_not_started(lib, gpiod, monkeypatch):
    """Coverage: the STAMPS_NOT_STARTED guard in `_lead_stamp`. A one-count backend has no
    stamp pump until capture starts, so a frame driven before then is dropped at once, and
    waits for no drain."""
    from wl_sync.backend import STAMPS_NOT_STARTED, FrameStamp
    from wl_sync.barcode import FRAME_US, encode

    monkeypatch.setattr(rp1, "time", gpiod.clock)  # where the frame costs nothing
    backend = Rp1Backend()
    try:
        began = gpiod.clock.ns
        stamp = backend.emit_pulses(17, encode(1000))
        took_ns = gpiod.clock.ns - began
    finally:
        backend.close()
    assert backend.timebase == "rp1-pio"
    assert stamp == FrameStamp(None, STAMPS_NOT_STARTED)
    assert took_ns < FRAME_US * 1_000 + rp1.STAMP_DRAIN_WAIT_S * 1e9


def test_a_shim_without_a_version_is_refused_with_how_to_rebuild_it(monkeypatch):
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: types.SimpleNamespace())
    with pytest.raises(rp1.PiolibError, match=r"pio/build-shim\.sh"):
        rp1._load_shim()


def test_a_shim_of_another_version_is_refused(monkeypatch):
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 1)
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: shim)
    with pytest.raises(rp1.PiolibError, match=r"version 1.*needs 6"):
        rp1._load_shim()


def test_a_version_2_shim_is_refused(monkeypatch):
    """Version 2 has no wlsync_start_stamp; calling through it would fail on a missing
    symbol, not with the rebuild instruction."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 2)
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: shim)
    with pytest.raises(rp1.PiolibError, match=r"version 2.*needs 6"):
        rp1._load_shim()


def test_a_version_3_shim_is_refused(monkeypatch):
    """Version 3's level read could not fail visibly: piolib's returned 0 on a failed
    ioctl (pio_rp1.c:591-598 at utils b57c48e), the low read the word pump's proof
    cannot survive. Version 4's returns the errno (Ruling R40), and a version 3 shim
    is refused with the rebuild."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 3)
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: shim)
    with pytest.raises(rp1.PiolibError, match=r"version 3.*needs 6.*build-shim"):
        rp1._load_shim()


def test_a_version_4_shim_is_refused(monkeypatch):
    """Version 4 read a full FIFO's level as RP1's saturated 15 (bench probe, wl-rig-sync0,
    2026-11-20), so its box failed the depth check with a levels mismatch. Version 5 reads
    it as 16 (Task 1.20 fix round 3), and a version 4 shim is refused, naming the rebuild
    (Ruling R42)."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 4)
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: shim)
    with pytest.raises(rp1.PiolibError, match=r"version 4.*needs 6.*build-shim"):
        rp1._load_shim()


def test_a_version_6_shim_missing_a_symbol_is_refused_with_how_to_rebuild_it(
    gpiod, monkeypatch, caplog
):
    """A .so that reports version 6 but lacks an export, as one hand-built from another
    tree might (Task 1.16's review, Minor 4; Ruling R37). ctypes raised AttributeError
    from `_declare`, which `_configure` did not catch, so the unit crash-looped. Now it is
    a shim that cannot be loaded: the process records split with no W, and the ERROR
    names the symbol and the rebuild."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 6)
    monkeypatch.setattr(rp1.ctypes, "CDLL", lambda name: shim)
    with pytest.raises(rp1.PiolibError, match=r"wlsync_pio_open.*pio/build-shim\.sh"):
        rp1._load_shim()
    backend = Rp1Backend()
    try:
        assert _is_split(backend) and backend.capture_failure
        [error] = _errors(caplog)
        assert error.startswith("event-code capture cannot run (libwlsync_pio reports ")
    finally:
        backend.close()


def test_a_shim_of_the_current_version_is_accepted():
    """Coverage: pins the accepting side of the version check, which passed against the
    sources when it was added. Without it, a check that refused every shim, the current
    one included, would fail only on the box."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 6)
    assert rp1._check_shim_version(shim) is None


_C_SOURCE = pathlib.Path(__file__).resolve().parent.parent / "pio" / "wlsync_pio.c"


def _c_prototype(text, name):
    """The parameter names of C function `name`, in order, from its definition in `text`."""
    match = re.search(rf"\b{name}\s*\(([^)]*)\)\s*\{{", text)
    assert match, f"no definition of {name}(...) found in {_C_SOURCE}"
    names = []
    for parameter in match.group(1).split(","):
        found = re.search(r"(\w+)\s*$", parameter.strip())
        assert found, f"cannot read parameter {parameter.strip()!r} of {name}"
        names.append(found.group(1))
    return names


# (ioctl result, errno, level, empty, full, joined depth) as the kernel answers
# PIO_IOC_SM_FIFO_STATE, and what the shim makes of it: the level, or a negative errno.
_EIO = -5
_BOX = [
    # The bench probe, wl-rig-sync0, 2026-11-20: the ioctl returns 8 on success, and the
    # joined RX FIFO's level field saturates at 15, `full` set at 16 words.
    ((8, 0, 0, 1, 0, 16), 0),
    ((8, 0, 2, 0, 0, 16), 2),
    ((8, 0, 14, 0, 0, 16), 14),
    ((8, 0, 15, 0, 1, 16), 16),   # 16 held: the field saturated, `full` makes it exact
    ((8, 0, 15, 0, 0, 16), 15),   # one popped: 15 held, exactly
    ((8, 0, 1, 0, 0, 16), 1),
]
_FIFO_STATE_CASES = _BOX + [
    ((0, 0, 5, 0, 0, 16), 5),                # a result of 0 is success too
    ((-1, 5, 5, 0, 0, 16), -5),              # the ioctl failed: its errno
    ((-1, 0, 5, 0, 0, 16), _EIO),            # ... with no errno: EIO
    ((8, 0, 16, 0, 1, 16), 16),              # a field wide enough to read 16
    ((8, 0, 0, 1 << 2, 0, 16), 0),           # empty as a mask, 1 << sm (fix round 2)
    ((8, 0, 3, 1 << 1, 0, 16), _EIO),        # ... contradicting its level
    ((8, 0, 0, 0, 0, 16), _EIO),             # a level of 0 that is not empty
    ((8, 0, 7, 1, 0, 16), _EIO),             # empty, with words in it
    ((8, 0, 14, 0, 1, 16), _EIO),            # full, with fewer words than the depth
    ((8, 0, 15, 1, 1, 16), _EIO),            # full and empty
    ((8, 0, 16, 0, 0, 16), _EIO),            # the depth, and not full
    ((8, 0, 15, 0, 0, 32), _EIO),            # saturated below a deeper FIFO's depth: 15 or more
    ((8, 0, 15, 0, 1, 32), 32),              # ... and full: exactly its depth
    ((8, 0, 5, 0, 0, 0), _EIO),              # no depth to read against
    ((8, 0, 0xFFFF, 1, 0, 16), _EIO),        # the level never written
    ((8, 0, 0, 0xFF, 0, 16), _EIO),          # empty never written
    ((8, 0, 4, 0, 0xFF, 16), _EIO),          # full never written
]


def test_the_shim_reads_the_boxs_answers_and_refuses_what_cannot_be_exact():
    """The shim's reading of the kernel's FIFO-state answer (pio/wlsync_fifo_state.h), run
    as compiled C (tests/fifo_state_c.py), since the shim cannot run off the box.

    THE BOX'S ANSWERS FIRST (bench probe, wl-rig-sync0, 2026-11-20). The ioctl returns 8 on
    success, so any result >= 0 is success. The joined RX FIFO's level field reads 0-14
    exactly, then 15 for 15 AND for 16 words, with `full` set only at 16: the field
    saturates, and with `full` read it is lossless. Until fix round 3 the shim returned 15
    for a full FIFO, the depth check read [0, 2, ..., 14, 15, 15], and the box recorded
    split with no W.

    Then what stays refused: a seed left in place (fix round 2), an empty or full flag
    that contradicts the level, and a saturated reading that cannot be exact, as 15 would
    be below a FIFO deeper than 16. Any other value is an answer, a mask-style `empty`
    included (fix round 2, N-M1)."""
    from tests import fifo_state_c

    answer = fifo_state_c.answer()
    if answer is None:
        pytest.skip("no C compiler on this host")
    got = [answer(*case) for case, _ in _FIFO_STATE_CASES]
    assert got == [want for _, want in _FIFO_STATE_CASES], [
        (case, want, have) for (case, want), have in zip(_FIFO_STATE_CASES, got) if want != have
    ]


def test_the_shim_uses_the_checked_answer():
    """The shim's level read goes through the header the test above runs, not a copy, and
    hands it the ioctl's own result, which is 8 on the box, not 0, and the errno taken
    straight after the call into a local of its own, so the order the arguments are
    evaluated in cannot change it (the re-review of fix round 3, Minor 4)."""
    text = _C_SOURCE.read_text()
    assert '#include "wlsync_fifo_state.h"' in text
    call = text.index("int result = ioctl(g_fd, PIO_IOC_SM_FIFO_STATE, &args);")
    taken = text.index("int error = errno;", call)
    used = text.index(
        "wlsync_fifo_state_answer(result, error, args.level, args.empty, args.full, depth)",
        taken,
    )
    assert call < taken < used


def test_the_c_shim_and_the_python_that_binds_it_agree_on_version_and_signature():
    """Coverage: pins agreement that holds today. Part 1 bumps the shim version, and a
    bump on one side only would refuse the box with a "rebuild" message that rebuilding
    cannot fix. The same goes for a parameter added to the C and not to `_declare`."""
    text = _C_SOURCE.read_text()
    version = re.search(r"#define\s+WLSYNC_SHIM_VERSION\s+(\d+)", text)
    assert version, f"no WLSYNC_SHIM_VERSION in {_C_SOURCE}"
    assert int(version.group(1)) == rp1._SHIM_VERSION, (
        f"{_C_SOURCE.name} says shim version {version.group(1)} but "
        f"wl_sync/rp1/backend.py says _SHIM_VERSION = {rp1._SHIM_VERSION}: bump both together"
    )

    lib = unittest.mock.MagicMock()
    rp1._declare(lib)
    for function in ("wlsync_start_capture", "wlsync_start_stamp"):
        c_names = _c_prototype(text, function)
        argtypes = getattr(lib, function).argtypes
        assert len(c_names) == len(argtypes), (
            f"{function} takes {len(c_names)} parameters in C ({', '.join(c_names)}) "
            f"but _declare sets {len(argtypes)} argtypes"
        )
        fake_names = list(inspect.signature(getattr(_FakeShim, function)).parameters)[1:]
        assert len(fake_names) == len(c_names), (
            f"_FakeShim.{function} takes {len(fake_names)} parameters, the C "
            f"{len(c_names)}"
        )
        for position, (c_name, fake_name) in enumerate(zip(c_names, fake_names)):
            assert c_name.startswith(fake_name), (
                f"{function} parameter {position} is `{c_name}` in C and "
                f"`{fake_name}` in _FakeShim"
            )


# The ctypes type `_declare` must give each C type the shim's exports use.
_CTYPE_OF = {
    "int": ctypes.c_int,
    "unsigned": ctypes.c_uint,
    "uint32_t": ctypes.c_uint32,
    "float": ctypes.c_float,
    "const uint16_t *": ctypes.POINTER(ctypes.c_uint16),
    "void": None,
}


def test_the_python_binds_each_shim_export_at_its_c_types():
    """Coverage: the test above counts `_declare`'s argtypes against the C parameters, not
    their types (Part 0's re-review, out of its scope). ctypes converts each argument by
    the type it is told, so a c_uint where the C takes an int, or a restype narrower
    than the C's, would pass that count and read wrong on the box without a sound. This
    pins every type of every export `_declare` binds, as they agree today; an export it
    gives no argtypes must take none."""
    text = _C_SOURCE.read_text()
    definitions = re.findall(
        r"^(int|unsigned|uint32_t|void)\s+(wlsync_\w+)\s*\(([^)]*)\)", text, re.MULTILINE
    )
    assert len(definitions) >= 15, f"read only {len(definitions)} exports from {_C_SOURCE}"
    lib = unittest.mock.MagicMock()
    _declare_all(lib)  # the backend's exports, and the bench harness's meter (version 6)
    for returns, function, parameters in definitions:
        if function == "wlsync_shim_version":  # typed where it is read (_check_shim_version)
            continue
        c_types = [" ".join(re.match(r"(.*?)\w+$", parameter.strip()).group(1).split())
                   for parameter in parameters.split(",") if parameter.strip() != "void"]
        bound = getattr(lib, function)
        assert bound.restype is _CTYPE_OF[returns], f"{function} returns {returns}"
        argtypes = bound.argtypes if isinstance(bound.argtypes, list) else []
        assert argtypes == [_CTYPE_OF[c_type] for c_type in c_types], (
            f"{function} takes ({', '.join(c_types)}) in C, but _declare binds {argtypes}"
        )


def _shape(word):
    """A jmp's address field masked: relocation and target do not change the encoding."""
    return word & ~0x1F if word >> 13 == 0 else word


def test_every_instruction_the_program_uses_is_checked_against_piolib(lib):
    """Coverage: closes a gap rather than discriminating today. An instruction added to
    the program without a piolib cross-check would run unverified on RP1."""
    lib.opened = True
    checked = {_shape(ours) for _, ours, _ in rp1._encoder_checks(lib)}
    used = {_shape(w) for w in (*program.STROBE_CAPTURE, program.encode_mov_x_not_null())}
    assert used <= checked


def test_strobed_words_are_delivered_without_anyone_calling_drain(lib):
    """drain() existed and nothing called it, so on the box the FIFO -- 8 words, four
    events -- filled on the first burst of codes and `push noblock` discarded every
    word after, silently. Capture has to drain itself."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append((tick, word)))
    try:
        lib.push(stream((10, 1), (20, 2)))
        assert _until(lambda: len(seen) == 2), f"delivered {seen}"
        assert seen == [(10, 1), (20, 2)]
    finally:
        backend.close()


def test_a_full_fifo_reaches_check_health_and_capture_continues(lib):
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        lib.full = True
        lib.push(stream((10, 1)))
        assert _until(lambda: seen == [1])
        lib.full = False
        # Two more drains, so the one that may already have read "full" has finished.
        settled = lib.drains + 2
        assert _until(lambda: lib.drains >= settled)
        with pytest.raises(RuntimeError, match="event-code capture: .*FIFO was full"):
            backend.check_health()

        lib.push(stream((20, 2)))
        assert _until(lambda: seen == [1, 2]), "capture stopped after a full FIFO"
        backend.check_health()  # reported once, and nothing new since
    finally:
        backend.close()


def test_a_desynced_stream_is_reported_once_and_capture_goes_on(lib, caplog):
    """THIS TEST PINNED THAT THE WORD DRAIN STOPPED ON A DESYNC, and was reported stopped
    from then on. Since Task 1.20 (the user's ruling, 2026-11-20) it goes on: the counter
    left alone is dropped and counted, check_health says so once, nothing is reported
    stopped, and the strobes after it are captured. The run's end gives the count."""
    import logging

    seen = []
    backend = _started(lib, lambda tick, word: seen.append((tick, word)))
    try:
        _lose_a_counters_data_word(lib)
        lib.push(stream((100, 10)))
        assert _until(lambda: seen[-1:] == [(100, 10)]), seen
        assert seen == FILLING + [(100, 10)]
        first = _next_complaint(backend)
        assert first is not None and "1 desync(s) this run" in first, first
        assert "capture goes on" in first and "event-code capture" in first, first
        assert _next_complaint(backend, timeout=0.2) is None, "reported twice, or stopped"
        lib.push(stream((110, 11)))
        assert _until(lambda: seen[-1:] == [(110, 11)]), seen
    finally:
        with caplog.at_level(logging.INFO, logger=rp1.log.name):
            backend.close()
    assert "event-code capture: 1 desync(s) this run, each resynchronised" in [
        r.getMessage() for r in caplog.records
    ]


def test_a_clean_run_ends_with_a_count_of_no_word_desyncs(lib, caplog):
    """The run's end says how many desyncs the word stream had, as the stamp stream's
    does (`_summarise_stamps`), in either mode: a clean run says none."""
    import logging

    backend = _started(lib, lambda tick, word: None)
    with caplog.at_level(logging.INFO, logger=rp1.log.name):
        backend.close()
        backend.close()  # once: a second close says nothing again
    lines = [r.getMessage() for r in caplog.records]
    assert lines.count("event-code capture: 0 desync(s) this run") == 1, lines


def test_the_word_drain_reads_every_word_and_then_the_level_again(lib):
    """Until Task 1.20 the drain read an even number of words and left the odd one for
    the next drain. The pump now pairs across batches, so the drain reads every word, and
    reads the level again after popping: how many came while it popped is half of what
    the pairing's proof is made of (`CodeWordPairer`). An empty poll reads it once."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        calls = len(lib.fifo_calls)
        lib.push(stream((10, 1)) + [2])  # 20's counter not in yet
        assert _until(lambda: lib.fifo == [])
        assert _until(lambda: seen == [1])
        lib.push([(~20) & 0xFFFFFFFF])
        assert _until(lambda: seen == [1, 2])
    finally:
        backend.close()
    ours = [call for call, sm, _ in lib.fifo_calls[calls:] if sm == 0]
    first = ours.index("get")
    assert ours[first - 1:first + 4] == ["rx_level", "get", "get", "get", "rx_level"], ours
    assert ours[-2:] == ["rx_full", "rx_level"], ours  # close's last poll, which was empty


# --- Fix round 1 (Ruling R40): a checked level read, and the FIFO's depth measured ------


def test_a_word_drain_whose_level_read_fails_is_held_and_capture_goes_on(lib):
    """The shim's level read says when it fails (version 4), where piolib's read 0
    (pio_rp1.c:591-598 at utils b57c48e). A drain whose look fails pops nothing and pairs
    nothing: check_health says so, nothing stops, and the next look reads the words."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append((tick, word)))
    try:
        lib.level_failures[0] = [5]
        assert _until(lambda: not lib.level_failures[0])
        complaint = _next_complaint(backend)
        assert complaint is not None and "level could not be read (errno 5)" in complaint
        assert "that drain read nothing" in complaint, complaint
        lib.push(stream((10, 1)))
        assert _until(lambda: seen == [(10, 1)]), seen
        assert _next_complaint(backend, timeout=0.2) is None, "reported twice, or stopped"
    finally:
        backend.close()


def test_a_word_drain_whose_level_after_popping_fails_drops_what_it_popped(lib, tmp_path):
    """The look read the level and the words were popped, but the read after them failed:
    nothing is paired from that drain (Ruling R40). Its strobe is dropped, an L record
    counts it, and the next strobe is written."""
    from wl_sync.log import CodeWord, Loss, read_log

    backend, recorder, close_sink, path = _recording_on(tmp_path)
    recorder.capture_codes(0, DATA, DATA)
    try:
        gate = threading.Event()
        lib.word_held.clear()
        lib.word_gate = gate
        assert lib.word_held.wait(1)
        lib.push(stream((10, 1)))
        lib.level_failures[0] = [None, 5]  # the look reads; the read after popping fails
        lib.word_gate = None
        gate.set()
        assert _until(lambda: backend._words.desyncs == 1)
        complaint = _next_complaint(backend)
        assert complaint is not None and "level could not be read (errno 5)" in complaint
        lib.push(stream((20, 2)))
        assert _until(lambda: lib.fifo == [] and backend.delivered_through().get(
            "event-code capture", 0) > 0)
        assert _until(lambda: lib.drains > 5)
    finally:
        backend.close()
        close_sink()
    _, records = read_log(path)
    assert records == [
        Loss(tick_us=records[0].tick_us, path="W", gpio=None, count=1, bound="at_least",
             cause="desync", before_us=None, after_us=None),
        CodeWord(tick_us=20, word=2),
    ]


def test_a_stamp_drain_whose_level_read_fails_is_held_and_stamping_goes_on(
    lib, gpiod, crystals
):
    """The stamp drain read a failed level as 0 and handed its mark over, past stamps still
    in the FIFO. Now it holds the failure, reads nothing, moves no mark, and goes on."""
    from wl_sync.barcode import encode

    crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        lib.level_failures[1] = [5]
        assert _until(lambda: not lib.level_failures[1])
        complaint = _next_complaint(backend)
        assert complaint is not None and "stamp FIFO's level could not be read" in complaint
        stamp = backend.emit_pulses(17, encode(1000))
        assert stamp.tick == gpiod.leads[1]
        assert not backend._stamp_pump.stopped
    finally:
        backend.close()


def test_the_rx_fifo_depth_is_measured_at_start_up_and_counted_on(lib):
    """piolib gives RP1 8 words a way (pio_rp1.c:985 at utils b57c48e), the shim joins
    them, so 16; the start-up check fills the test machine's FIFO and reads every level
    to 16 (`_measure_fifo_depth`), and the pumps count on 16. Until Ruling R40 the backend
    assumed 8 from RP2040's numbers."""
    backend = _started(lib, lambda tick, word: None)
    try:
        assert backend._rx_depth == backend._rx_words == 16
        assert backend._words._pairer._fifo_words == 16
        assert not _is_split(backend) and not backend.capture_failure
        levels = [call for call, sm, _ in lib.fifo_calls if sm == 2 and call == "rx_level"]
        assert len(levels) >= 2 + 1 + 16 // 2 + 1, levels
    finally:
        backend.close()


@pytest.mark.parametrize("fault", ["join", "wrap", "read"])
def test_a_fifo_not_as_deep_as_counted_on_is_a_capture_fault_with_no_w(lib, caplog, fault):
    """A FIFO whose join did not take holds 8; a level field that wraps at 16 reads a full
    FIFO as empty; a level read that fails while the FIFO is measured leaves its depth
    unknown. Each would let the pump call a word closed after a dropped push, and write a
    wrong W. So each is a capture fault, as Task 1.16 handles one (Ruling R40): the box
    records split with no W, with the ERROR, and no capture machine is enabled."""
    if fault == "join":
        lib.test_depth = 8
    elif fault == "wrap":
        lib.level_wrap = 16
    else:
        lib.level_failures[2] = [None, None, None, 5]  # the rise's reads, then the depth's
    seen = []
    backend = Rp1Backend()
    try:
        assert _is_split(backend) and backend.capture_failure
        [error] = _errors(caplog)
        assert error.startswith("event-code capture cannot run (the RX FIFO"), error
        assert "no W records" in error
        backend.start_strobed_capture(0, DATA, DATA, lambda t, w: seen.append((t, w)))
        lib.push(stream((10, 1)))
        time.sleep(0.05)
        assert seen == [] and lib.sm_enabled_never(0)
    finally:
        backend.close()


def _needs_a_c_compiler():
    from tests import fifo_state_c

    if fifo_state_c.answer() is None:
        pytest.skip("no C compiler on this host")


def test_the_depth_check_passes_on_a_level_field_that_saturates_as_the_boxs_does(lib):
    """THE BOX'S FINDING (bench probe, wl-rig-sync0, 2026-11-20). Its joined RX FIFO's level
    field reads 15 for 15 and for 16 words, with `full` set at 16. Until fix round 3 the
    shim passed 15 on, the depth check read [0, 2, ..., 14, 15, 15], and the box recorded
    split with no W. The shim now reads 15 with `full` as 16, exactly, and the check passes
    with the levels the hardware really gives."""
    _needs_a_c_compiler()
    lib.box_answers = True
    backend = _started(lib, lambda tick, word: None)
    try:
        assert not _is_split(backend) and not backend.capture_failure
        assert backend._rx_words == 16
    finally:
        backend.close()


def test_a_saturated_level_below_a_deeper_fifos_depth_is_a_capture_fault(lib, caplog):
    """A field that saturates at 15 below a FIFO deeper than 16 cannot say how many words
    15 is. The shim refuses that answer, so the depth check cannot measure the FIFO, and the
    process records split with no W: the pairing never counts on a level it cannot trust."""
    _needs_a_c_compiler()
    lib.box_answers = True
    lib.fifo_depth = 16
    lib.word_depth = lib.stamp_depth = lib.test_depth = 32
    backend = Rp1Backend()
    try:
        assert _is_split(backend) and backend.capture_failure
        assert "could not be measured" in backend.capture_failure, backend.capture_failure
    finally:
        backend.close()


def test_a_block_whose_level_cannot_be_read_runs_no_capture_machine(lib, caplog):
    """The first read of the capture machine's level, at configuration, fails: W would be
    dropped at every drain, or counted on a read that cannot be trusted. A capture fault,
    found before the first segment (Task 1.16's form)."""
    lib.level_failures[0] = [5]
    backend = Rp1Backend()
    try:
        assert _is_split(backend) and "cannot be measured" in backend.capture_failure
        [error] = _errors(caplog)
        assert error.startswith("event-code capture cannot run (the capture machine's RX FIFO")
    finally:
        backend.close()


def test_a_process_whose_stamp_test_fails_counts_on_the_per_direction_depth(lib, gpiod):
    """A blind start-up test leaves the depth unmeasured: the process records split, with W,
    and the word pump counts on piolib's per-direction depth, 8, the least the FIFO holds
    whether or not the join took. Told less than the FIFO holds, the pump errs safe
    (tests/test_pio_capture.py, the told-less fuzz case)."""
    gpiod.pio_sees_barcode = False
    backend = _started(lib, lambda tick, word: None)
    try:
        assert _is_split(backend) and not backend.capture_failure
        assert backend._rx_words == 8 and backend._words._pairer._fifo_words == 8
    finally:
        backend.close()


def test_a_data_word_left_waiting_at_close_is_counted(lib, tmp_path):
    """The last drain finds a data word whose counter it will not read: its strobe is lost,
    and since fix round 1 counted in an L record (review M2)."""
    from wl_sync.log import CodeWord, Loss, read_log

    backend, recorder, close_sink, path = _recording_on(tmp_path)
    recorder.capture_codes(0, DATA, DATA)
    try:
        lib.push(stream((10, 1)) + [2])
        assert _until(lambda: lib.fifo == [] and lib.drains > 3)
    finally:
        backend.close()
        close_sink()
    _, records = read_log(path)
    assert records == [
        CodeWord(tick_us=10, word=1),
        Loss(tick_us=10, path="W", gpio=None, count=1, bound="at_least", cause="desync",
             before_us=10, after_us=None),
    ]


def test_close_delivers_what_the_fifo_held_before_the_state_machine_stops(lib):
    """The last words in the FIFO are the end of the session's last trial. close() stops
    the pump with one final drain and only then disables the state machine, so nothing
    the PIO captured is left behind in it."""
    def on_word(tick, word):
        lib.log.append(f"word={word}")

    backend = _started(lib, on_word)
    lib.push(stream((10, 7)))
    backend.close()

    # From the sync enable: the test machine was enabled and disabled at construction.
    captured = lib.log[lib.log.index("sync=0x3"):]
    assert "word=7" in captured
    assert captured.index("word=7") < captured.index("enabled=False")


class _FakeTime:
    """A monotonic clock that advances 20 us per read and by exactly what is slept.

    One crystal behind every clock: MONO, RAW and REALTIME read alike, and the PIO
    count is the same time in microseconds."""

    CLOCK_REALTIME = 0
    CLOCK_MONOTONIC = 1
    CLOCK_MONOTONIC_RAW = 4

    def __init__(self) -> None:
        self.ns = 10**12

    def clock_gettime_ns(self, clock):
        self.ns += 20_000
        return self.ns

    def sleep(self, seconds):
        self.ns += int(seconds * 1e9)

    def p_now(self):
        return (self.ns // 1_000) & 0xFFFF_FFFF


class _TwoCrystalClock:
    """The CPU's crystal behind MONO, RAW and REALTIME, and RP1's behind the PIO count.

    TRUE TIME IS THE RUN LOOP'S. It advances 20 us per clock read and by each sleep, on
    the thread that made the clock, which calls `emit_pulses` as the run loop does. A
    read from any other thread, a pump's, samples true time without moving it, and a
    pump's sleep sleeps for real only, at most 1 ms, so the pumps run as `_manual_clock`'s
    do. Until the final review's M6 a pump's sleep moved true time 1 ms a poll too, so a
    poll that landed in `_drive`'s spin made a `set_value` up to 1 ms late, at random, and
    a test with a smaller margin flaked. THE PRICE IS A ROLL: the run loop's wait for the
    paths to deliver sleeps for real (wl_sync/cli.py:374-383), and true time moves only
    by its reads, about 20 us a poll, short of the edge pump's 20 ms lag
    (wl_sync/rp1/gpio.py:26, :45). A test that rolls on this clock with edge capture
    running would wait the full 0.2 s and log a false "rolling before" WARNING; none
    does yet (final fix wave's re-review, Minor 2). RAW reads true x (1 + cpu_ppm 1e-6), and
    MONO reads the same unless a test slews it: RAW x (1 + mono_slew_ppm 1e-6) plus
    mono_offset_ns, as NTP moves MONO and not RAW. REALTIME reads MONO plus an offset,
    and `p_now()`, the PIO count, true time since P's zero x (1 + rp1_ppm 1e-6) in
    microseconds.

    A read samples at its middle, 10 us in. So an edge set between two reads lies at
    their midpoint, as the fit assumes of a bracket; sampled at either end, every pair
    would carry half a read, 10 counts, into the fit.

    P'S ZERO IS THE SYNC ENABLE, as on silicon: `crystals` has the shim fake call
    `start_p` there. True time starts at 0, and P counted from it until Task 1.10a, when
    P's zero lay four reads, about 70 counts, before the RAW origin. Construction now
    holds GPIO17 high for STAMP_TEST_HIGH_US, so counted from 0, P would run more than
    LEAD_STAMP_TOLERANCE ahead of the fit before its first pair. From the enable, it lies
    half a read, 10 counts, after the RAW origin, where on silicon it is one syscall
    after. The first pair takes up that offset, as it takes up the real one.
    """

    CLOCK_REALTIME = 0
    CLOCK_MONOTONIC = 1
    CLOCK_MONOTONIC_RAW = 4
    REALTIME_OFFSET_NS = 1_762_500_000 * 10**9

    def __init__(self, cpu_ppm=0, rp1_ppm=0, mono_slew_ppm=0, mono_offset_ns=0) -> None:
        self._true_ns = 0
        self._cpu = 1 + cpu_ppm * 1e-6
        self._rp1 = 1 + rp1_ppm * 1e-6
        self._slew = 1 + mono_slew_ppm * 1e-6
        self._mono_offset_ns = mono_offset_ns
        self.p_zero_ns = 0  # true time at P's zero
        self._lock = threading.Lock()
        self._run_loop = threading.get_ident()

    def _mono(self, raw_ns):
        return round(raw_ns * self._slew) + self._mono_offset_ns

    def _moves_time(self):
        return threading.get_ident() == self._run_loop

    def clock_gettime_ns(self, clock):
        moves = self._moves_time()
        with self._lock:
            if moves:
                self._true_ns += 10_000
            raw = round(self._true_ns * self._cpu)
            if moves:
                self._true_ns += 10_000
        if clock == self.CLOCK_MONOTONIC_RAW:
            return raw
        mono = self._mono(raw)
        return mono + self.REALTIME_OFFSET_NS if clock == self.CLOCK_REALTIME else mono

    def sleep(self, seconds):
        if self._moves_time():
            self.advance(round(seconds * 1e9))
        time.sleep(min(seconds, 0.001))

    def advance(self, ns):
        """True time moved without a read or a sleep: a slow call."""
        with self._lock:
            self._true_ns += ns

    def monotonic(self):
        return time.monotonic()

    @property
    def ns(self):
        """MONO now, without the read moving it: what the gpiod fake records."""
        with self._lock:
            return self._mono(round(self._true_ns * self._cpu))

    def start_p(self):
        """The sync enable: P counts from here."""
        with self._lock:
            self.p_zero_ns = self._true_ns

    def p_now(self):
        with self._lock:
            elapsed_ns = self._true_ns - self.p_zero_ns
            return int(elapsed_ns * self._rp1 / 1_000) & 0xFFFF_FFFF

    def p_at_mono(self, mono_ns):
        """The PIO count when MONO read `mono_ns`: where an edge the kernel stamped then
        belongs on P."""
        with self._lock:
            true_ns = (mono_ns - self._mono_offset_ns) / self._slew / self._cpu
            return int((true_ns - self.p_zero_ns) * self._rp1 / 1_000) & 0xFFFF_FFFF


@pytest.fixture
def crystals(monkeypatch, gpiod):
    """Puts the backend and the gpiod fake on a `_TwoCrystalClock`, whose P starts at the
    shim fake's sync enable."""

    def make(cpu_ppm=0, rp1_ppm=0, **mono):
        clock = _TwoCrystalClock(cpu_ppm, rp1_ppm, **mono)
        monkeypatch.setattr(rp1, "time", clock)
        gpiod.clock = clock
        if gpiod.shim is not None:
            gpiod.shim.on_sync = clock.start_p
        return clock

    return make


def test_only_the_run_loops_thread_moves_the_two_crystal_clocks_true_time():
    """Final review M6, on the fixture itself. A pump's reads and sleeps leave true time
    where it was, so a run-loop `set_value` lands at its deadline however the pumps poll;
    the run loop's own read and sleep still move it, 20 us and the sleep's length."""
    clock = _TwoCrystalClock()

    def pump():
        for _ in range(5):
            clock.clock_gettime_ns(clock.CLOCK_MONOTONIC_RAW)
            clock.sleep(0.001)

    thread = threading.Thread(target=pump)
    thread.start()
    thread.join()
    assert clock.ns == 0
    clock.clock_gettime_ns(clock.CLOCK_MONOTONIC_RAW)
    clock.sleep(0.001)
    assert clock.ns == 1_020_000


@pytest.fixture
def gpiod(monkeypatch):
    """A stand-in for the libgpiod v2 bindings that records every level set.

    THE PIO'S VIEW. A rise on GPIO17 is stamped at the PIO count, `clock.p_now()`, into the
    FIFO of every enabled machine whose jmp pin is 17 (`lib.watching(17)`), while
    `pio_sees_barcode` is True: a pair of a data word 0 and the inverted count, as the
    program pushes it. The PIO reads the pin libgpiod drives, with no wire (the box's
    c-direct, 300/300 lead edges stamped). `stamp_late_us` moves a frame's stamps later
    than the edge, `stamped` lists each count pushed for a frame, and `pushes` every
    (sm, count) pushed. A test that blinds the machine sets `pio_sees_barcode` False AFTER
    building the backend, because from Task 1.10a on, construction tests that the PIO sees
    GPIO17. `set_cost_ns` makes each `set_value` take that long, with the edge at its
    middle, and `set_failures` holds what each next `set_value` raises, None for none.

    FRAMES ARE COUNTED AS THE DECODER FINDS THEM: a rise on GPIO17 after IDLE_MIN_US of LOW,
    or its first rise, begins one, numbered from 1 in `frame` (wl_sync/barcode.py:11-13).
    THE START-UP TEST'S PULSE IS NO FRAME: the decoder rejects it (`Rp1Backend`'s
    STAMP_TEST_HIGH_US), so what GPIO17 does while the test machine runs (`lib.testing`)
    is counted in no frame, and its stamp is pushed to the test machine, the one machine
    enabled then. `frame_stamp_offset_us` moves every stamp of the frames a test names by
    that many us. `unstamped_rises` maps a frame to the rises, numbered from 0 for its
    lead, that go unstamped. `stray_before_lead_us` maps a frame to a stray stamp pushed
    that many us before its lead's. `leads` gives each frame's lead-edge stamp as
    pushed. `data_word(frame, rise)` is the data word a stamp's pair carries, GPIO0-15 as
    the stamp machine reads them (wl_sync/rp1/program.py:90), 0 unless a test says, and
    `lost_words` maps a (frame, rise) to the word of its pair the FIFO loses, 0 the data
    and 1 the counter: a stream left with one word alone (Task 1.15)."""
    import sys
    import types

    from wl_sync.barcode import IDLE_MIN_US

    line = types.ModuleType("gpiod.line")
    line.Direction = types.SimpleNamespace(INPUT="in", OUTPUT="out")
    line.Value = types.SimpleNamespace(ACTIVE=1, INACTIVE=0)
    line.Edge = types.SimpleNamespace(BOTH="both")
    line.Clock = types.SimpleNamespace(MONOTONIC="mono")

    module = types.ModuleType("gpiod")
    module.line = line
    module.sets = []
    module.chips = {"/dev/gpiochip0": ("pinctrl-rp1", 54)}
    module.LineSettings = lambda **kwargs: kwargs

    def push(machines, p, data=0, lose=None):
        for sm in machines:
            module.pushes.append((sm, p))
            words = [data, (~p) & 0xFFFF_FFFF]
            if lose is not None:
                del words[lose]
            module.shim.push_to(sm, words)

    class _Request:
        def set_value(self, pin, value):
            failure = module.set_failures.pop(0) if module.set_failures else None
            if failure is not None:
                raise failure
            half = module.set_cost_ns // 2
            if half:
                module.clock.advance(half)
            was = module.levels.get(pin)
            rising = value == line.Value.ACTIVE and was != value
            falling = value != line.Value.ACTIVE and was == line.Value.ACTIVE
            module.levels[pin] = value
            shim = module.shim
            testing = shim is not None and shim.testing
            lead = False
            if pin == 17 and rising and not testing:
                fell = module.fell_ns
                lead = fell is None or module.clock.ns - fell >= IDLE_MIN_US * 1_000
                module.frame += lead
                module.rise = 0 if lead else module.rise + 1
            if pin == 17 and falling and not testing:
                module.fell_ns = module.clock.ns
            watching = shim.watching(17) if shim is not None else []
            if rising and pin == 17 and module.pio_sees_barcode and testing:
                push(watching, module.clock.p_now() & 0xFFFF_FFFF)
            elif (rising and pin == 17 and module.pio_sees_barcode and watching
                    and module.rise not in module.unstamped_rises.get(module.frame, ())):
                p = (module.clock.p_now() + module.stamp_late_us
                     + module.frame_stamp_offset_us.get(module.frame, 0)) & 0xFFFF_FFFF
                stray = module.stray_before_lead_us.get(module.frame) if lead else None
                if stray is not None:  # pushed first: the FIFO is in time order
                    q = (p - stray) & 0xFFFF_FFFF
                    module.stamped.append(q)
                    push(watching, q)
                module.stamped.append(p)
                if lead:
                    module.leads[module.frame] = p
                push(watching, p, module.data_word(module.frame, module.rise),
                     module.lost_words.get((module.frame, module.rise)))
            module.sets.append((module.clock.ns, value))
            if module.set_cost_ns - half:
                module.clock.advance(module.set_cost_ns - half)

        def wait_edge_events(self, timeout):
            # The real one is select() on the request's fd: with no edges it returns
            # False once the whole timeout has passed, never sooner. `module.edges` is
            # the kernel's buffer, which a test fills.
            if module.edge_failure is not None:
                raise module.edge_failure
            deadline = time.monotonic() + timeout
            while not module.edges:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.0005)
            return True

        def read_edge_events(self, max_events=None):
            taken = module.edges[:max_events]
            del module.edges[:max_events]
            return taken

        def release(self):
            pass

    def request_lines(path, config, consumer, **kwargs):
        if path not in module.chips:
            raise FileNotFoundError(path)
        module.requested.append(kwargs)
        return _Request()

    class Chip:
        def __init__(self, path):
            if path not in module.chips:
                raise FileNotFoundError(path)
            self._label, self._lines = module.chips[path]

        def get_info(self):
            return types.SimpleNamespace(label=self._label, num_lines=self._lines)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    module.request_lines = request_lines
    module.Chip = Chip
    module.clock = _FakeTime()
    module.shim = None
    module.pio_sees_barcode = True
    module.stamp_late_us = 0
    module.set_cost_ns = 0
    module.set_failures = []
    module.stamped = []
    module.pushes = []
    module.frame = 0
    module.fell_ns = None
    module.frame_stamp_offset_us = {}
    module.rise = 0
    module.unstamped_rises = {}
    module.stray_before_lead_us = {}
    module.leads = {}
    module.data_word = lambda frame, rise: 0
    module.lost_words = {}
    module.levels = {}
    module.edge_failure = None
    module.edges = []
    module.requested = []
    monkeypatch.setitem(sys.modules, "gpiod", module)
    monkeypatch.setitem(sys.modules, "gpiod.line", line)
    return module


def test_every_barcode_frame_decodes_not_just_the_first(lib, gpiod, monkeypatch):
    """A frame ends on TRAIL HIGH, and emit_pulses set each level at its pulse's START
    and returned, so the pin stayed HIGH between frames. The decoder finds a frame by
    a rising edge after 400 ms of LOW, which the stuck-high pin never produced again:
    on the box, only the first barcode of a run would have decoded. The codec's own
    tests could not see it, because their pulse model ends every frame LOW.

    Every level the fake saw is decoded, the start-up test's pulse included, so the
    quiet after it is the box's: on the fake clock, where it costs nothing."""
    from wl_sync.barcode import decode_edges, encode

    import wl_sync.rp1.backend as rp1_module

    monkeypatch.setattr(rp1, "STAMP_TEST_QUIET_S", _QUIET_ON_THE_BOX_S)
    rp1_module.time = gpiod.clock
    try:
        backend = Rp1Backend()
        first = gpiod.clock.ns
        for value in (1000, 1001, 1002):
            backend.emit_pulses(17, encode(value))
            gpiod.clock.sleep(0.8)  # the run loop's idle between frames
    finally:
        rp1_module.time = time

    edges = []
    for ns, level in gpiod.sets:
        if not edges or edges[-1][1] != level:
            edges.append((ns // 1000, level))
    values = [b.value for b in decode_edges(edges, start_us=first // 1000 - 1_000_000)]
    # The last frame is not asserted: a decoder only accepts a frame once the record
    # runs past its end, and this record stops at the third frame's final edge.
    assert values[:2] == [1000, 1001]


def test_a_dead_edge_path_does_not_hide_a_failing_event_code_path(lib, gpiod):
    """check_health() raised at the FIRST problem. Once the edge thread had died, every
    call stopped at "edge capture thread stopped" and never looked at the event codes
    again, so a desync after it went unreported for the rest of the day. Since Task 1.20
    the desync stops nothing, and it is still reported."""
    backend = _started(lib, lambda tick, word: None)
    try:
        backend.start_edge_capture([20, 21], lambda gpio, level, tick: None)
        gpiod.edge_failure = OSError("EIO")
        assert _until(lambda: not backend._edge_thread.is_alive())
        _lose_a_counters_data_word(lib)  # and then the pairing slips
        assert _until(lambda: backend._words.desyncs == 1)

        with pytest.raises(RuntimeError) as raised:
            backend.check_health()
        assert "edge capture" in str(raised.value)
        assert "event-code capture" in str(raised.value)
    finally:
        backend.close()


def test_a_chip_that_does_not_exist_is_refused_before_anything_is_recorded(lib, gpiod, tmp_path):
    """The chip was first opened inside run(), after the segment file existed, so a wrong
    --gpiochip crash-looped and left a header-only segment behind on every restart --
    up to 86,400 a day under RestartSec=1, each one re-read by every manifest rebuild.
    It is checked when the backend is built, before run() creates anything."""
    from wl_sync.cli import main

    with pytest.raises(RuntimeError, match="/dev/gpiochip9"):
        main(["record", "--out", str(tmp_path), "--duration", "1", "--gpiochip", "/dev/gpiochip9"])
    assert list(tmp_path.rglob("seg-*.log")) == []


def test_a_chip_too_small_for_the_capture_pins_is_refused(lib, gpiod):
    gpiod.chips["/dev/gpiochip4"] = ("gpio-brcmstb@107d517c00", 17)
    with pytest.raises(RuntimeError, match="17 lines"):
        Rp1Backend(chip_path="/dev/gpiochip4")


def test_a_fifo_found_holding_its_depth_counts_as_full(lib):
    """Fullness was sampled once, before reading, so a FIFO that filled between that
    sample and the read went unwitnessed. A FIFO holding its depth IS full, and any push
    into it was dropped, so the level is a witness as well. The depth is the one measured
    at start-up, 16 joined on RP1, not the 8 this test once held (Ruling R40)."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        assert backend._rx_words == 16
        lib.push(stream(*FILLING))
        assert _until(lambda: len(seen) == 8)
        complaint = _next_complaint(backend)
        assert complaint is not None and "FIFO was full" in complaint, complaint
    finally:
        backend.close()


def test_the_hardware_backend_is_built_on_the_chip_it_is_given(monkeypatch):
    """Closes a coverage gap rather than discriminating against the shipped code: a
    mutant passing the chip positionally survived the suite. Rp1Backend's first
    positional parameter is the PIO index, so that mutant opens the default chip
    whatever --gpiochip says."""
    from wl_sync import cli

    built = []
    monkeypatch.setattr(rp1, "Rp1Backend", lambda *args, **kwargs: built.append((args, kwargs)))
    cli._hardware_backend("/dev/gpiochip4")
    cli._hardware_backend("/dev/gpiochip4", split=True)  # Task 1.24's --split
    assert built == [((), {"chip_path": "/dev/gpiochip4", "force_split": False}),
                     ((), {"chip_path": "/dev/gpiochip4", "force_split": True})]


def test_the_shim_is_the_library_looked_for(monkeypatch):
    """The loader asked for "piolib", falling back to libpiolib.so. piolib's CMake target
    is `pio`, so what it builds is libpio.so -- and that exports only three of the
    functions this backend needs anyway, because the rest are static inline. What the
    backend loads is this repository's shim, pio/wlsync_pio.c."""
    asked = []
    monkeypatch.setattr(rp1.ctypes.util, "find_library", lambda name: asked.append(name))

    def refuse(name):
        asked.append(f"CDLL {name}")
        raise OSError("not here")

    monkeypatch.setattr(rp1.ctypes, "CDLL", refuse)
    with pytest.raises(rp1.PiolibError, match="wlsync_pio"):
        rp1._load_shim()
    assert asked == ["wlsync_pio", "CDLL libwlsync_pio.so"]


def test_the_encodings_are_checked_after_the_block_is_open(lib, gpiod):
    """piolib's encoders read the CURRENT PIO block, so before pio_open they encode
    nothing in particular. The check ran before the open, so on the box it would have
    compared this program's encodings with garbage and refused to start."""
    Rp1Backend().close()
    assert lib.log.index("open") < lib.log.index("encode")


def test_the_clock_divider_comes_from_the_system_clock(lib):
    """The divider was computed from clock_get_hz(0). In piolib's enum 0 is clk_gpout0,
    not clk_sys, and RP1 answers every index but clk_sys with PIO_ORIGIN_ANY --
    4294967295 as a uint32_t -- so the PIO would have run about 2,000 times slow, and
    every timestamp with it. 200 MHz over 1 MHz x 2 cycles per tick is exactly 100."""
    backend = _started(lib, lambda tick, word: None)
    try:
        assert lib.started_with["clkdiv"] == pytest.approx(100.0)
        assert lib.started_with["seed"] == program.encode_mov_x_not_null()
        assert (lib.started_with["wrap_target"], lib.started_with["wrap"]) == (
            program.WRAP_TARGET, program.WRAP)
    finally:
        backend.close()


def test_a_system_clock_that_cannot_be_real_is_refused(lib):
    """At construction, where the machines are configured (Task 1.10a)."""
    lib.clk_sys_hz = 0xFFFF_FFFF  # what RP1 answers for any clock but clk_sys
    with pytest.raises(rp1.PiolibError, match="clk_sys"):
        Rp1Backend()
    assert "start_capture" not in lib.log


def test_the_recorders_clock_is_not_read_before_the_origin_moves(lib, gpiod, monkeypatch):
    """start_strobed_capture re-takes the timebase origin, AFTER Recorder exists. A clock
    primed at construction holds a reading from the dead origin (here 1 s), so the first
    real reading (~1 ms) looks like a wrap and every record of the run lands 2^32 us late.
    The first unwrap must seed the clock instead."""
    import types

    from wl_sync.log import TICK_WRAP_US, BarcodeEmitted, CodeWord
    from wl_sync.service import Recorder

    now = types.SimpleNamespace(ns=10**12)  # a manual clock: only the test moves it
    monkeypatch.setattr(
        rp1,
        "time",
        types.SimpleNamespace(
            CLOCK_REALTIME=0,
            CLOCK_MONOTONIC=1,
            CLOCK_MONOTONIC_RAW=4,
            clock_gettime_ns=lambda _clock: now.ns,
            sleep=time.sleep,
            monotonic=time.monotonic,
        ),
    )

    class _Sink:
        def __init__(self):
            self.written = []

        def write(self, record):
            self.written.append(record)

    backend = Rp1Backend()  # origin 1
    now.ns += 1_000_000_000  # run(): checkpoint, NTP probe, manifest, segment open
    sink = _Sink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_codes(0, DATA, 16)  # origin 2: now_us() restarts at 0
    try:
        now.ns += 1_000_000  # 1 ms into capture
        lib.push(stream((300, 0x0001)))
        assert _until(lambda: len(sink.written) == 1)
        recorder.record_barcode(backend.now_us(), 7)
    finally:
        backend.close()

    word, barcode = sink.written
    assert isinstance(word, CodeWord) and word.tick_us == 300
    assert isinstance(barcode, BarcodeEmitted) and barcode.tick_us == 1_000
    assert barcode.tick_us < TICK_WRAP_US


def _manual_clock(monkeypatch):
    """The box clock, moved only by the test; sleeps are real, so the pump threads run."""
    import types

    now = types.SimpleNamespace(ns=10**12)
    monkeypatch.setattr(
        rp1,
        "time",
        types.SimpleNamespace(
            CLOCK_REALTIME=0,
            CLOCK_MONOTONIC=1,
            CLOCK_MONOTONIC_RAW=4,
            clock_gettime_ns=lambda _clock: now.ns,
            sleep=time.sleep,
            monotonic=time.monotonic,
        ),
    )
    return now


def test_the_word_pump_never_claims_a_word_it_has_not_delivered(lib, gpiod, monkeypatch):
    """How far the word pump has delivered, which the run loop's roll barrier waits on.

    The pump reads the clock just before each poll, and moves its mark there, less
    WORDS_LAG_US (the time a strobe's two words take to reach the FIFO, and the clock's own
    error), only once that poll has delivered every word it found. A word still being
    delivered holds the mark back, and so does half a pair: the data word of a strobe
    whose counter is not in the FIFO yet."""
    from wl_sync.rp1.pio_capture import WORDS_LAG_US

    now = _manual_clock(monkeypatch)
    delivering, release = threading.Event(), threading.Event()
    seen = []

    def on_word(tick, word):
        delivering.set()
        release.wait(5)
        seen.append(word)

    backend = _started(lib, on_word)  # the origin is now: now_us() reads 0

    def mark():
        return backend.delivered_through()["event-code capture"]

    try:
        now.ns += 1_000_000_000  # 1 s in: a poll there moves the mark to just before it
        assert _until(lambda: mark() == 1_000_000 - WORDS_LAG_US - 1)

        now.ns += 1_000_000  # a strobe at 1 001 000 us
        lib.push(stream((1_001_000, 1)))
        assert delivering.wait(1)
        now.ns += 4_000_000  # the clock runs on while the word is being delivered
        time.sleep(0.02)
        assert mark() < 1_001_000
        release.set()
        assert _until(lambda: seen == [1])
        assert _until(lambda: mark() >= 1_001_000)

        now.ns += 1_000_000  # 1 006 000 us: a strobe whose counter word is not in yet
        lib.push([2])
        drains = lib.drains + 3
        assert _until(lambda: lib.drains >= drains)
        held = mark()
        assert held < 1_006_000
        now.ns += 1_000_000
        drains = lib.drains + 3
        assert _until(lambda: lib.drains >= drains)
        assert mark() == held, "a poll that left half a pair behind moved the mark"
        now.ns += WORDS_LAG_US * 1_000  # so a poll now lies past the strobe by the lag
        lib.push([(~1_006_000) & 0xFFFFFFFF])
        assert _until(lambda: seen == [1, 2])
        assert _until(lambda: mark() >= 1_006_000)
    finally:
        release.set()
        backend.close()


def test_the_backend_says_how_far_each_capture_path_has_delivered(lib, gpiod):
    """Raw now_us() readings, one per path started, named as check_health() names them.
    Unwrapping them is the Recorder's: the shared clock holds the wrap count."""
    backend = Rp1Backend()
    assert backend.delivered_through() == {}
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    backend.start_edge_capture([20, 21], lambda gpio, level, tick: None)
    try:
        assert _until(
            lambda: None not in backend.delivered_through().values(), timeout=2.0
        )
        assert set(backend.delivered_through()) == {"event-code capture", "edge capture"}
    finally:
        backend.close()


def test_with_prompt_delivery_both_paths_pass_a_boundary_well_inside_the_bound(lib, gpiod):
    """The roll barrier's normal cost, on real threads. The word pump polls every
    millisecond; an idle edge line moves its mark once per timed-out wait. So both paths
    pass a boundary within a few edge waits, far inside ROLL_BARRIER_TIMEOUT_S: a roll with
    prompt delivery never runs the barrier to its bound."""
    from wl_sync.cli import ROLL_BARRIER_TIMEOUT_S
    from wl_sync.service import Recorder

    class _Sink:
        def write(self, record):
            pass

    backend = Rp1Backend()
    recorder = Recorder(backend, sink=_Sink())
    recorder.capture_codes(0, DATA, DATA)
    recorder.capture_edges([20, 21])
    try:
        assert _until(lambda: None not in recorder.delivered_through().values(), 2.0)
        took = []
        for _ in range(5):
            boundary = recorder.now()
            began = time.monotonic()
            assert _until(
                lambda: min(recorder.delivered_through().values()) >= boundary, 1.0
            )
            took.append(time.monotonic() - began)
        assert max(took) < ROLL_BARRIER_TIMEOUT_S, took
    finally:
        backend.close()


def test_a_capture_path_that_has_stopped_is_not_waited_for(lib, gpiod, caplog):
    """A path that has stopped, its thread exited or its word drain stopped alone (Ruling
    R16), has delivered all it ever will, so a roll has nothing to wait for. It used to
    keep its last mark, and every later open and close
    then waited out ROLL_BARRIER_TIMEOUT_S and logged a WARNING about records it would
    "deliver late". check_health() is what reports the stop.

    A barrier that waits for a dead path takes the whole timeout and logs that WARNING, so
    each barrier here must end before the timeout, and no WARNING may appear. The first
    barrier still waits for the live word pump, which a loaded runner can delay past a
    quarter of the timeout (0.056 s seen against a bound of 0.05 s)."""
    import logging

    from wl_sync import cli
    from wl_sync.service import Recorder

    class _Sink:
        def write(self, record):
            pass

    backend = Rp1Backend()
    recorder = Recorder(backend, sink=_Sink())
    recorder.capture_codes(0, DATA, DATA)
    recorder.capture_edges([20, 21])

    def barrier():
        began = time.monotonic()
        cli._await_delivery(recorder, recorder.now())
        return time.monotonic() - began

    try:
        assert _until(lambda: None not in recorder.delivered_through().values(), 2.0)
        with caplog.at_level(logging.WARNING, logger=cli.log.name):
            gpiod.edge_failure = OSError("EIO")
            assert _until(lambda: not backend._edge_thread.is_alive())
            assert set(backend.delivered_through()) == {"event-code capture"}
            assert barrier() < cli.ROLL_BARRIER_TIMEOUT_S

            # A drain that fails stops the word drain; since Task 1.20 a desync does not.
            lib.get_failures[0] = OSError("EIO")
            lib.push(stream((10, 1)))
            assert _until(backend._words_stopped)
            assert backend.delivered_through() == {}
            assert barrier() < cli.ROLL_BARRIER_TIMEOUT_S
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    finally:
        backend.close()


def test_the_edge_buffer_holds_a_whole_read(lib, gpiod, monkeypatch):
    """The edge pump takes a read shorter than EDGE_READ_MAX for an empty kernel buffer,
    and libgpiod caps each read at the buffer it allocates from event_buffer_size. A
    smaller buffer would make every full read look short, and move the mark past edges
    still waiting in the kernel."""
    from wl_sync.rp1.gpio import EDGE_READ_MAX

    backend = _started(lib, lambda tick, word: None)  # one count: edges map through its fit
    try:
        backend.start_edge_capture([20], lambda gpio, level, tick: None)
    finally:
        backend.close()
    [edges] = [kwargs for kwargs in gpiod.requested if "event_buffer_size" in kwargs]
    assert edges["event_buffer_size"] >= EDGE_READ_MAX

    monkeypatch.setattr(rp1, "_EDGE_EVENT_BUFFER", EDGE_READ_MAX - 1)
    backend = Rp1Backend()
    try:
        with pytest.raises(ValueError, match="EDGE_READ_MAX"):
            backend.start_edge_capture([20], lambda gpio, level, tick: None)
    finally:
        backend.close()


def _edge_stamped(seqno, timestamp_ns):
    """An edge the kernel stamped at `timestamp_ns` on MONO. All on GPIO20, so its line's
    number is the request's, rising and falling in turn."""
    kinds = types.SimpleNamespace(RISING_EDGE="rising", FALLING_EDGE="falling")
    return types.SimpleNamespace(
        global_seqno=seqno,
        line_seqno=seqno,
        line_offset=20,
        event_type=kinds.RISING_EDGE if seqno % 2 else kinds.FALLING_EDGE,
        Type=kinds,
        timestamp_ns=timestamp_ns,
    )


def _edge_now_less(backend, seqno, us):
    """An edge the kernel stamped `us` before now (`_edge_stamped`)."""
    return _edge_stamped(seqno, time.clock_gettime_ns(time.CLOCK_MONOTONIC) - us * 1_000)


def test_the_edge_pump_witnesses_how_late_edges_reach_it(lib, gpiod):
    """The roll barrier assumes the kernel buffers an edge within EDGE_STAMP_LAG_US of
    stamping it. Each read bounds that from above -- the clock when the read returned,
    less the edge's stamp -- and the backend reports the largest bound, and how many
    edges went over, for the health report to log."""
    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US

    seen = []
    backend = Rp1Backend()
    assert backend.stamp_lag() is None
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)  # the fit
    time.sleep(0.1)  # so an edge stamped 50 ms ago is after the origin
    backend.start_edge_capture([20], lambda gpio, level, tick: seen.append(tick))
    try:
        gpiod.edges.append(_edge_now_less(backend, 1, 1_000))
        assert _until(lambda: len(seen) == 1)
        lag = backend.stamp_lag()
        assert lag.allowance_us == EDGE_STAMP_LAG_US
        assert lag.max_us >= 1_000  # an upper bound: it can never be less than the truth

        gpiod.edges.append(_edge_now_less(backend, 2, 50_000))
        assert _until(lambda: len(seen) == 2)
        lag = backend.stamp_lag()
        assert lag.max_us >= 50_000 and lag.over >= 1
    finally:
        backend.close()


def test_the_backend_reports_the_order_guards_clamps_for_the_summary(lib, gpiod):
    """Final review M2: the edge pump's count of edges the order guard clamped, and the
    furthest, reach the run loop as the witness does (`stamp_lag`), for the run's summary.
    None while no edge capture runs; the pump's counts, zero at first, once it does."""
    from wl_sync.backend import OrderClamps

    backend = Rp1Backend()
    assert backend.order_clamps() is None
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    backend.start_edge_capture([20], lambda gpio, level, tick: None)
    try:
        assert backend.order_clamps() == OrderClamps(clamped=0, max_us=0)
        backend._pump.order_clamped, backend._pump.order_clamp_max_us = 7, 412
        assert backend.order_clamps() == OrderClamps(clamped=7, max_us=412)
    finally:
        backend.close()


def test_lost_edges_reach_check_health_by_cause_and_a_swap_does_not(lib, gpiod):
    """The camera lines' events out of order are not a problem at all. A gap in one
    line's own numbers reaches check_health() at once, its cause in the problem's kind,
    so a change of cause is a new episode for the run loop, and in its message."""
    import types

    from wl_sync.backend import CaptureDegraded

    kinds = types.SimpleNamespace(RISING_EDGE="rising", FALLING_EDGE="falling")

    def edge(seqno, gpio, line_seqno, rising):
        return types.SimpleNamespace(
            global_seqno=seqno,
            line_seqno=line_seqno,
            line_offset=gpio,
            event_type=kinds.RISING_EDGE if rising else kinds.FALLING_EDGE,
            Type=kinds,
            timestamp_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC) - 1_000_000,
        )

    seen = []
    backend = Rp1Backend()
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)  # the fit
    time.sleep(0.01)  # so an edge stamped 1 ms ago is after the origin
    backend.start_edge_capture([20, 26, 27], lambda gpio, level, tick: seen.append(gpio))
    try:
        gpiod.edges.extend([
            edge(1, 26, 1, True), edge(3, 26, 2, False),  # GPIO26's thread pushed first
            edge(2, 27, 1, True), edge(4, 27, 2, False),
        ])
        assert _until(lambda: len(seen) == 4)
        assert _next_complaint(backend, timeout=0.2) is None  # the window has closed

        gpiod.edges.extend([edge(5, 20, 1, True), edge(8, 20, 4, False)])  # 2 and 3 lost
        assert _until(lambda: len(seen) == 6)
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
        assert raised.value.kinds == {"edge capture: EdgeBufferOverflowError"}
        assert "2 edge event(s) lost (kernel buffer overflow on GPIO20)" in str(
            raised.value
        )
        assert _next_complaint(backend, timeout=0.2) is None, "the overflow reported twice"
    finally:
        backend.close()


def test_the_edge_thread_takes_a_last_look_when_capture_stops(lib, gpiod, monkeypatch):
    """close() stops the edge thread before the caller closes the sink, and run() asks
    check_health() once more after close(), for whatever capture held at the end. A gap
    held then would never be reported unless the thread takes its last look on the way
    out (EdgePump.finish), on the same request, before close() releases it."""
    from wl_sync.rp1 import gpio

    looks = []
    monkeypatch.setattr(
        gpio.EdgePump, "finish", lambda pump, request, now_us: looks.append(request)
    )
    backend = _started(lib, lambda tick, word: None)  # one count: edges map through its fit
    backend.start_edge_capture([20], lambda gpio, level, tick: None)
    request = backend._edges
    backend.close()
    assert looks == [request]


def test_check_health_takes_the_edge_loss_under_the_pumps_lock(lib, gpiod, monkeypatch):
    """The pump adds a loss to the one it holds while check_health() takes it; unlocked,
    a take between the pump's read and its store reported the first loss twice."""
    from wl_sync.backend import CaptureDegraded
    from wl_sync.rp1 import gpio

    taken = []
    take = gpio.EdgePump.take_error
    monkeypatch.setattr(
        gpio.EdgePump, "take_error", lambda pump: taken.append(pump) or take(pump)
    )
    backend = _started(lib, lambda tick, word: None)  # one count: edges map through its fit
    backend.start_edge_capture([20], lambda gpio, level, tick: None)
    try:
        backend._pump._hold(gpio.UnaccountedEdgeError(unaccounted=1))
        with pytest.raises(CaptureDegraded, match="1 edge event"):
            backend.check_health()
        assert taken == [backend._pump]
        backend.check_health()  # taken once, and cleared
    finally:
        backend.close()


def test_the_last_look_fits_inside_closes_join():
    """On a clock that stands still no window ever closes, so the last look runs to its
    pass cap, each pass a wait of up to EDGE_WAIT_S. The cap, and the pass in progress
    when the stop is set, must fit inside the join close() gives the edge thread, or
    close() releases the request under a look still running."""
    from wl_sync.rp1.gpio import EDGE_WAIT_S, STOP_PASSES

    assert (STOP_PASSES + 1) * EDGE_WAIT_S < rp1._STOP_JOIN_S


def _held_gap_on_a_still_clock(backend, gpiod):
    """Start edge capture on a clock that stands still, and leave a gap held: number 2,
    on GPIO20, never comes, and no window can close on it."""
    import types

    kinds = types.SimpleNamespace(RISING_EDGE="rising", FALLING_EDGE="falling")

    def edge(seqno, gpio, line_seqno, rising):
        return types.SimpleNamespace(
            global_seqno=seqno, line_seqno=line_seqno, line_offset=gpio,
            event_type=kinds.RISING_EDGE if rising else kinds.FALLING_EDGE, Type=kinds,
            timestamp_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC) - 1_000_000,
        )

    seen = []
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)  # the fit
    time.sleep(0.01)  # so an edge stamped 1 ms ago is after the origin
    frozen = backend.now_us()
    backend.now_us = lambda: frozen
    backend.start_edge_capture([20, 26], lambda gpio, level, tick: seen.append(gpio))
    gpiod.edges.extend([edge(1, 20, 1, True), edge(3, 20, 2, False)])
    assert _until(lambda: len(seen) == 2)
    return edge, seen


def test_a_gap_held_when_the_edge_thread_dies_is_reported(lib, gpiod, caplog):
    """A gap is held for the window before it is called a loss. If the edge thread dies
    on a read error inside that window, the stop's own error is what check_health()
    holds, and the gap went nowhere: no journal line, no count. The same input before
    the window existed reported "1 edge event(s) lost" at once. The dying thread now
    settles what it held, and the journal names it."""
    import logging

    backend = Rp1Backend()
    _held_gap_on_a_still_clock(backend, gpiod)
    with caplog.at_level(logging.ERROR, logger="wl_sync.rp1.backend"):
        gpiod.edge_failure = OSError("EIO")
        assert _until(lambda: not backend._edge_thread.is_alive())
    try:
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("1 edge event" in message for message in errors), errors
        with pytest.raises(RuntimeError, match="EIO"):  # the stop still matters more
            backend.check_health()
    finally:
        backend.close()


def test_close_returns_with_the_edge_thread_finished_on_a_still_clock(lib, gpiod):
    """A wait that times out ends the last look: the buffer stayed empty for EDGE_WAIT_S,
    and nothing held can still be coming. Without that, on a clock that stands still the
    look ran every pass, 64 waits, past close()'s join; close() returned with the thread
    alive and released the request under it."""
    from wl_sync.backend import CaptureDegraded

    backend = Rp1Backend()
    _held_gap_on_a_still_clock(backend, gpiod)
    began = time.monotonic()
    backend.close()
    assert time.monotonic() - began < 0.5
    assert not backend._edge_thread.is_alive()
    with pytest.raises(CaptureDegraded, match="1 edge event"):
        backend.check_health()


def test_close_returns_on_a_still_clock_with_traffic_through_the_stop(lib, gpiod, monkeypatch):
    """A line firing every 2 ms through the stop, on a clock that stands still, with a
    wait that never times out: no window closes and no wait comes back empty, so the
    last look can end only on its pass cap. It runs exactly STOP_PASSES passes, the edge
    thread has finished before close() releases the request, and the gap is reported.

    The look takes about 0.1 s here, far inside the join, so its duration proves
    nothing about the join. Any pass could have been a whole wait instead, so the waits
    the look made, and the pass in progress at the stop, must fit inside the join: what
    test_the_last_look_fits_inside_closes_join asserts of the constants, asserted of the
    passes the loop really ran. The quiet ending, on a wait that times out, is
    test_close_returns_with_the_edge_thread_finished_on_a_still_clock's."""
    from wl_sync.backend import CaptureDegraded
    from wl_sync.rp1 import gpio
    from wl_sync.rp1.gpio import EDGE_WAIT_S, STOP_PASSES

    looking = threading.Event()
    finish = gpio.EdgePump.finish

    def last_look(pump, request, now_us):
        looking.set()
        finish(pump, request, now_us)

    monkeypatch.setattr(gpio.EdgePump, "finish", last_look)

    backend = Rp1Backend()
    edge, _ = _held_gap_on_a_still_clock(backend, gpiod)
    request = backend._edges
    release = request.release
    released = threading.Event()
    alive_at_release = []
    look = []  # the timeout each of the last look's waits asked for

    class RequestReleasedError(Exception):
        """libgpiod's, raised by any call on a request once it is released."""

    def wait_for_an_edge(timeout):
        # The fixture's wait without its timeout. A call on a released request raises,
        # as libgpiod's does; so does a call blocked here when the request is released,
        # where the real select() would return at the timeout this one ignores. Either
        # way a look still running at the release ends, and the test cannot hang.
        if looking.is_set():
            look.append(timeout)
        while not released.is_set():
            if gpiod.edges:
                return True
            time.sleep(0.0005)
        raise RequestReleasedError("GPIO lines have been released")

    def release_and_witness():
        alive_at_release.append(backend._edge_thread.is_alive())
        released.set()
        release()

    request.wait_edge_events = wait_for_an_edge
    request.release = release_and_witness
    stop = threading.Event()

    def chatter():
        k = 1
        while not stop.wait(0.002):
            gpiod.edges.append(edge(3 + k, 26, k, k % 2 == 1))
            k += 1

    feeder = threading.Thread(target=chatter, daemon=True)
    feeder.start()
    try:
        backend.close()
    finally:
        stop.set()
        feeder.join(1.0)
    assert alive_at_release == [False], "close() released the request under the look"
    assert len(look) == STOP_PASSES, f"the look ended after {len(look)} passes, not on its cap"
    assert sum(look) + EDGE_WAIT_S < rp1._STOP_JOIN_S, (
        f"{len(look)} waits of up to {EDGE_WAIT_S} s, and the pass in progress at the "
        f"stop, do not fit inside close()'s join of {rp1._STOP_JOIN_S} s"
    )
    with pytest.raises(CaptureDegraded, match="1 edge event"):
        backend.check_health()


# --- E on the box clock (Task 1.11) ------------------------------------------------------
#
# The kernel stamps each edge on MONO. On one count the edge pump maps it onto P, MONO ->
# RAW through a table the edge thread fills, then RAW -> P through the lead-edge fit (spec
# §2.3, docs/superpowers/specs/2026-10-30-one-clock-design.md:354-364). A split process,
# which has no fit, counts it in MONO us since the origin, as before the one-clock build.


def _clock_reads(monkeypatch):
    """The real clocks, with every read the backend makes recorded as (thread, clock,
    value). Each pass of the edge pump is recorded as (thread, "pass", None), and its last
    look as (thread, "finish", None)."""
    from wl_sync.rp1 import gpio

    reads = []

    def clock(which):
        value = time.clock_gettime_ns(which)
        reads.append((threading.current_thread().name, which, value))
        return value

    monkeypatch.setattr(rp1, "time", types.SimpleNamespace(
        CLOCK_REALTIME=time.CLOCK_REALTIME,
        CLOCK_MONOTONIC=time.CLOCK_MONOTONIC,
        CLOCK_MONOTONIC_RAW=time.CLOCK_MONOTONIC_RAW,
        clock_gettime_ns=clock,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ))
    for name, mark in (("pump_once", "pass"), ("finish", "finish")):
        def marked(pump, request, now_us, _mark=mark, _real=getattr(gpio.EdgePump, name)):
            reads.append((threading.current_thread().name, _mark, None))
            return _real(pump, request, now_us)

        monkeypatch.setattr(gpio.EdgePump, name, marked)
    return reads


def test_edges_land_on_the_pio_count_not_on_cpu_time(lib, gpiod, crystals):
    """The CPU's crystal 50 ppm fast and RP1's true. After forty frames an edge the kernel
    stamped on MONO is delivered on P, to 2 counts, where MONO us since the origin, which a
    split process counts, are more than a millisecond off."""
    from wl_sync.barcode import encode

    clock = crystals(cpu_ppm=50, rp1_ppm=0)
    seen = []
    backend = _started(lib, lambda tick, word: None)
    backend.start_edge_capture([20], lambda gpio, level, tick: seen.append(tick))
    try:
        for k in range(40):
            assert backend.emit_pulses(17, encode(1000 + k)).tick is not None, k
            clock.sleep(0.5)
        stamp_ns = clock.ns
        gpiod.edges.append(_edge_stamped(1, stamp_ns))
        assert _until(lambda: seen)
    finally:
        backend.close()
    p = clock.p_at_mono(stamp_ns)
    assert abs(seen[0] - p) <= 2, (seen[0], p)
    assert abs((stamp_ns - backend._origin_ns) // 1_000 - p) > 1_000


def test_the_edge_thread_reads_mono_and_raw_together_on_every_pass(lib, gpiod, monkeypatch):
    """Spec §2.3 step 1 (the same file's :355-358): before each pass, and before the last
    look, the edge thread reads (MONO, RAW) for the edge map's table. RAW is read either
    side of MONO and paired by its midpoint, so the pair is one instant whatever a clock
    read costs, and a wide bracket is dropped (Ruling R31). start_edge_capture adds the
    first, on its caller's thread, before the edge thread exists, as provisional: the edge
    thread's first narrow reading replaces it (Ruling R33, Important 1)."""
    from wl_sync import clockmap

    added = []

    class _Table(clockmap.MonoRawTable):
        def add(self, mono_ns, raw_ns, *, provisional=False):
            added.append((threading.current_thread().name, mono_ns, raw_ns, provisional))
            super().add(mono_ns, raw_ns, provisional=provisional)

    monkeypatch.setattr(rp1, "MonoRawTable", _Table)
    reads = _clock_reads(monkeypatch)
    backend = _started(lib, lambda tick, word: None)
    backend.start_edge_capture([20], lambda gpio, level, tick: None)
    try:
        assert _until(lambda: sum(entry[1] == "pass" for entry in reads) >= 5)
    finally:
        backend.close()

    names = {time.CLOCK_MONOTONIC: "MONO", time.CLOCK_MONOTONIC_RAW: "RAW"}
    edge = [(names.get(which, which), value)
            for thread, which, value in reads if thread == "wl-sync-edges"]
    looked = edge.index(("finish", None))
    passes = [
        i for i, (what, _) in enumerate(edge[:looked + 1]) if what in ("pass", "finish")
    ]
    assert len(passes) >= 6
    taken = []
    for i in passes:
        (first, raw_before), (middle, mono), (last, raw_after) = edge[i - 3:i]
        assert (first, middle, last) == ("RAW", "MONO", "RAW"), edge[i - 3:i + 1]
        if raw_after - raw_before <= rp1.MONO_RAW_BRACKET_MAX_NS:
            taken.append((mono, (raw_before + raw_after) // 2))
    assert [(mono, raw) for thread, mono, raw, _ in added[1:]] == taken
    assert {thread for thread, _, _, _ in added[1:]} == {"wl-sync-edges"}
    assert [provisional for _, _, _, provisional in added] == [True] + [False] * len(taken)
    assert added[0][0] == threading.current_thread().name
    assert taken, "no narrow reading: nothing replaced the start-up one"
    assert list(backend._edge_table._readings)[0] == taken[0]


def test_a_mono_raw_reading_with_a_wide_bracket_is_dropped(monkeypatch):
    """Ruling R31. A RAW bracket wider than MONO_RAW_BRACKET_MAX_NS was interrupted, and its
    midpoint can be half of it off, so the reading is dropped: here 60 us wide, which would
    have put the pair 30 us off. Start-up's reading is kept however wide, since an empty
    table places no edge, and provisional: a wide reading after it is dropped and leaves
    it standing, and the first narrow one replaces it (Ruling R33, Important 1). Kept, it
    stayed the table's first reading for about 5 s and set its whole slope."""
    from wl_sync.clockmap import MonoRawTable

    reads = []
    monkeypatch.setattr(rp1, "time", types.SimpleNamespace(
        CLOCK_MONOTONIC=time.CLOCK_MONOTONIC,
        CLOCK_MONOTONIC_RAW=time.CLOCK_MONOTONIC_RAW,
        clock_gettime_ns=lambda _which: reads.pop(0),
    ))
    assert rp1.MONO_RAW_BRACKET_MAX_NS == 50_000
    table = MonoRawTable()
    # RAW, MONO, RAW, in microseconds: MONO runs 1 000 000 us behind RAW.
    for raw_before, mono, raw_after, start_up, kept in (
        (2_000_000, 1_000_020, 2_000_060, True, [(1_000_020, 2_000_030)]),  # 60 us, start-up
        (3_000_000, 2_000_020, 3_000_060, False, [(1_000_020, 2_000_030)]),  # 60 us: dropped
        (4_000_000, 3_000_020, 4_000_040, False, [(3_000_020, 4_000_020)]),  # 40 us: replaces
        (5_000_000, 4_000_020, 5_000_060, False, [(3_000_020, 4_000_020)]),  # 60 us: dropped
        (6_000_000, 5_000_020, 6_000_040, False,  # 40 us: kept beside it
         [(3_000_020, 4_000_020), (5_000_020, 6_000_020)]),
    ):
        reads[:] = [raw_before * 1_000, mono * 1_000, raw_after * 1_000]
        rp1.Rp1Backend._add_mono_raw(table, start_up=start_up)
        assert list(table._readings) == [
            (mono_us * 1_000, raw_us * 1_000) for mono_us, raw_us in kept
        ], (raw_before, start_up)


def test_a_split_process_counts_edges_from_the_monotonic_origin_as_today(
    lib, gpiod, monkeypatch
):
    """A process whose stamp machine failed its start-up test (Task 1.10a) has no fit to map
    through: an edge stamped 1 500 us after the origin is delivered at 1 500, and the edge
    thread reads MONO alone, for the now_us() its marks are on.

    Coverage: split is today's arithmetic, so this passed before Task 1.11 and was never
    red. It pins that one count left split alone. Two mutants fail it (Ruling R33,
    Minor 6): a split process that takes (MONO, RAW) readings, as one count does, and one
    that counts from an origin 1 ms late."""
    reads = _clock_reads(monkeypatch)
    seen = []
    backend = Rp1Backend()
    backend._fall_back_to_split("x")
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    backend.start_edge_capture([20], lambda gpio, level, tick: seen.append(tick))
    try:
        gpiod.edges.append(_edge_stamped(1, backend._origin_ns + 1_500_000))
        assert _until(lambda: seen and sum(entry[1] == "pass" for entry in reads) >= 5)
    finally:
        backend.close()
    assert seen == [1_500]
    on_the_edge_thread = {which for thread, which, _ in reads
                          if thread == "wl-sync-edges" and which not in ("pass", "finish")}
    assert on_the_edge_thread == {time.CLOCK_MONOTONIC}


def test_edges_cannot_be_mapped_before_the_count_has_started(lib, gpiod):
    """On one count the edge map runs through the RAW->P fit, which the sync enable makes
    (`start_strobed_capture`), and run() starts codes before edges (wl_sync/cli.py:584-585).
    Refused, the start opens no line request and fixes nothing: a calibration can still be
    applied."""
    backend = Rp1Backend()
    try:
        with pytest.raises(RuntimeError, match="start_strobed_capture"):
            backend.start_edge_capture([20], lambda gpio, level, tick: None)
        assert not any("event_buffer_size" in kwargs for kwargs in gpiod.requested)
        assert backend._edge_thread is None
        assert backend.apply_calibration(None) is not None  # nothing has started
    finally:
        backend.close()


# --- L records through the backend's own threads (2026-10-30) ----------------------------
#
# The pumps report each loss where they find it, from their own threads; the Recorder
# places it on the box clock and the QueuedSink's owner thread writes it into the
# segment. These run the path the box runs, threads and all.


def _recording_on(tmp_path):
    import datetime

    from tests.test_log import HEADER
    from wl_sync.segment import SegmentWriter
    from wl_sync.service import QueuedSink, Recorder

    path = tmp_path / "seg-000.log"
    sink = QueuedSink(SegmentWriter(path, HEADER))
    backend = Rp1Backend()
    recorder = Recorder(backend, sink=sink)
    closed = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.timezone.utc)
    return backend, recorder, lambda: sink.close(closed), path


def test_a_desync_reaches_the_segment_as_a_loss_record(lib, gpiod, tmp_path):
    """The lone counter is dropped, and its strobe is an L record after the last code word
    before it. Until Task 1.20 the word drain stopped there, and nothing followed; now the
    code words after it do."""
    from wl_sync.log import CodeWord, Loss, read_log

    backend, recorder, close_sink, path = _recording_on(tmp_path)
    recorder.capture_codes(0, DATA, DATA)
    try:
        _lose_a_counters_data_word(lib)
        lib.push(stream((100, 10)))
        assert _until(lambda: backend._words.desyncs == 1 and lib.fifo == [])
        assert _until(lambda: backend.delivered_through().get("event-code capture"))
    finally:
        backend.close()
        close_sink()

    _, records = read_log(path)
    assert records == [CodeWord(tick_us=tick, word=word) for tick, word in FILLING] + [
        Loss(
            tick_us=80, path="W", gpio=None, count=1, bound="at_least",
            cause="desync", before_us=80, after_us=None,
        ),
        CodeWord(tick_us=100, word=10),
    ]


def test_a_full_fifo_reaches_the_journal_but_not_the_segment(lib, gpiod, tmp_path):
    from wl_sync.log import CodeWord, read_log

    backend, recorder, close_sink, path = _recording_on(tmp_path)
    recorder.capture_codes(0, DATA, DATA)
    try:
        lib.full = True
        lib.push(stream((10, 1)))
        assert _until(lambda: lib.fifo == [] and lib.drains > 2)
        lib.full = False
        complaint = _next_complaint(backend)
        assert complaint is not None and "FIFO was full" in complaint, complaint
    finally:
        backend.close()
        close_sink()

    assert read_log(path)[1] == [CodeWord(tick_us=10, word=1)]


@pytest.mark.parametrize("split", [False, True], ids=["one-count", "split"])
def test_an_edge_overflow_reaches_the_segment_as_a_loss_record(lib, gpiod, tmp_path, split):
    """In a split process the window's start is the edge before's tick, exactly. On one
    count it is `after` less a span at the fit's ratio, rounded once where the two edges'
    mapped ticks round twice (wl_sync/clockmap.py:269-273), so it is within a count."""
    import types

    from wl_sync.log import Edge, Loss, read_log

    kinds = types.SimpleNamespace(RISING_EDGE="rising", FALLING_EDGE="falling")

    def edge(seqno, rising, ago_us):
        return types.SimpleNamespace(
            global_seqno=seqno, line_seqno=seqno, line_offset=20,
            event_type=kinds.RISING_EDGE if rising else kinds.FALLING_EDGE, Type=kinds,
            timestamp_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC) - ago_us * 1_000,
        )

    backend, recorder, close_sink, path = _recording_on(tmp_path)
    if split:
        backend._fall_back_to_split("x")
    recorder.capture_codes(0, DATA, DATA)  # as run() does: on one count, the fit
    time.sleep(0.01)  # so an edge stamped 2 ms ago is after the origin
    recorder.capture_edges([20])
    try:
        gpiod.edges.extend([edge(1, True, 2_000), edge(4, False, 1_000)])  # 2, 3 lost
        assert _until(lambda: gpiod.edges == [])
        complaint = _next_complaint(backend)
        assert complaint is not None and "kernel buffer overflow on GPIO20" in complaint
    finally:
        backend.close()
        close_sink()

    _, records = read_log(path)
    first, loss, second = records
    assert isinstance(first, Edge) and isinstance(second, Edge)
    assert loss == Loss(
        tick_us=second.tick_us, path="E", gpio=20, count=2, bound="exact",
        cause="buffer_overflow", before_us=loss.before_us, after_us=second.tick_us,
    )
    assert abs(loss.before_us - first.tick_us) <= (0 if split else 1)


def _calibration(rate_ppb=0, p_per_raw_ppb=0):
    import datetime

    from wl_sync.calibration import Calibration

    return Calibration(
        serial="0123456789abcdef", rate_ppb=rate_ppb, p_per_raw_ppb=p_per_raw_ppb,
        baseline_s=3_600.0, pairs=3_600, residual_rms_us=3.0, boot_id="boot",
        written_at=datetime.datetime(2026, 11, 7, 12, 0, tzinfo=datetime.timezone.utc),
    )


def test_the_timebase_says_which_clock_this_process_records_on(lib):
    """One count by default: W and B count P, as RP1's crystal runs, or in SI us once a
    calibration gives the rate (spec §2.6). A process put in split mode says "split", and
    a calibration changes nothing there: its B and E count MONO, which NTP already holds
    near SI. Per instance: one process's mode is not another's."""
    from wl_sync.clockmap import IDENTITY, TickScale

    calibration = _calibration(rate_ppb=12_345, p_per_raw_ppb=40_000)
    one_count, split = Rp1Backend(), Rp1Backend()
    try:
        assert one_count.timebase == "rp1-pio"
        assert one_count.apply_calibration(None) == IDENTITY
        assert one_count.timebase == "rp1-pio"
        assert one_count.apply_calibration(calibration) == TickScale(12_345)
        assert one_count.timebase == "rp1-pio-si"

        split._fall_back_to_split("x")
        assert split.timebase == "split"
        assert split.apply_calibration(calibration) is None
        assert split.timebase == "split"
        assert Rp1Backend.timebase == "rp1-pio"
    finally:
        one_count.close()
        split.close()


def test_the_data_lines_are_read_one_microsecond_after_the_strobe_is_seen():
    """Coverage: the program's DATA_SAMPLE_CYCLES at the 2 MHz the divider sets."""
    assert rp1.DATA_SAMPLE_DELAY_US == 1.0


# --- The stamp drain and the lead-edge match (Task 1.10, first half) ---------------------
#
# The pump thread drains the stamp machine after the word machine, on every pass, so
# piolib keeps one caller while capture runs. emit_pulses brackets each frame's lead edge
# with clock reads, waits for the drain to pass the frame's end, and takes the stamp in
# the bracket. These run on `_TwoCrystalClock`, with the gpiod fake's PIO stamping
# every rise on GPIO17 into SM 1's FIFO.


def _holding_the_pump():
    """An `on_word` that holds the pump thread inside it from the first word until
    released. A held pump drains nothing and moves no clock, so only the test's own
    thread moves true time."""
    inside, release = threading.Event(), threading.Event()

    def on_word(tick, word):
        inside.set()
        release.wait(5)

    return on_word, inside, release


def test_the_b_of_a_frame_is_its_lead_edges_stamp(lib, gpiod, crystals):
    from wl_sync.backend import FrameStamp
    from wl_sync.barcode import encode

    crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        stamp = backend.emit_pulses(17, encode(214_507_817))
    finally:
        backend.close()
    assert gpiod.stamped, "the stamp machine stamped nothing"
    assert stamp == FrameStamp(gpiod.stamped[0])


def test_a_frame_with_no_stamp_is_missed_as_not_stamped(lib, gpiod, crystals):
    from wl_sync.backend import STAMP_NOT_SEEN
    from wl_sync.barcode import FRAME_US, encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.pio_sees_barcode = False  # after construction, which tests that the PIO sees GPIO17
    try:
        began = clock.ns
        stamp = backend.emit_pulses(17, encode(1000))
        took_ns = clock.ns - began
    finally:
        backend.close()
    assert stamp.tick is None
    assert stamp.missed == STAMP_NOT_SEEN
    # The frame, then the drain wait, which a live pump ends at once. The slack is the
    # pump's own sleeps, which move true time while this thread waits.
    assert took_ns <= FRAME_US * 1_000 + rp1.STAMP_DRAIN_WAIT_S * 1e9 + 20_000_000, took_ns


def test_a_drain_that_falls_behind_is_waited_for_until_50_ms_past_the_frames_end(
    lib, gpiod, crystals
):
    """The wait's bound (spec §2.5 step 1), with the pump held so nothing drains: the
    frame, then STAMP_DRAIN_WAIT_S past its end, and not a poll longer."""
    from wl_sync.backend import STAMP_NOT_SEEN
    from wl_sync.barcode import FRAME_US, encode

    assert rp1.STAMP_DRAIN_WAIT_S == 0.05
    clock = crystals()
    on_word, inside, release = _holding_the_pump()
    backend = _started(lib, on_word)
    try:
        lib.push(stream((10, 1)))
        assert inside.wait(1)
        began = clock.ns
        stamp = backend.emit_pulses(17, encode(1000))
        took_ns = clock.ns - began
    finally:
        release.set()
        backend.close()
    bound_ns = FRAME_US * 1_000 + 50_000_000
    assert bound_ns <= took_ns <= bound_ns + 1_100_000, took_ns
    assert stamp.tick is None and stamp.missed == STAMP_NOT_SEEN


def test_a_stamp_far_from_the_lead_edge_is_missed_with_its_distance(lib, gpiod, crystals):
    from wl_sync.barcode import encode
    from wl_sync.clockmap import LEAD_STAMP_TOLERANCE

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        # A first frame warms the map: until a pair moves it, the fake's P runs some
        # 10 counts behind it (_TwoCrystalClock).
        assert backend.emit_pulses(17, encode(999)).tick is not None
        clock.sleep(0.8)
        gpiod.stamp_late_us = 5_000
        stamp = backend.emit_pulses(17, encode(1000))
    finally:
        backend.close()
    assert stamp.tick is None
    found = re.fullmatch(
        r"the stamp lies (\d+) us from the frame's lead edge as the CPU bracketed it "
        rf"\({LEAD_STAMP_TOLERANCE} us allowed\)",
        stamp.missed,
    )
    assert found, stamp.missed
    # From the bracket's later end, which the pump's sleep can stretch by a millisecond.
    assert 3_900 <= int(found.group(1)) <= 5_000, stamp.missed


def test_stamps_between_frames_are_counted_and_discarded(lib, gpiod, crystals):
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        first = backend.emit_pulses(17, encode(1000))
        clock.sleep(0.3)
        lib.push_stamps(stream((clock.p_now(), 0), (clock.p_now() + 100_000, 0)))
        clock.sleep(0.5)
        lead = len(gpiod.stamped)
        second = backend.emit_pulses(17, encode(1001))
    finally:
        backend.close()
    assert first.tick is not None and first.between == 0
    assert second.between == 2
    assert second.tick == gpiod.stamped[lead]


def test_a_frames_later_rising_edges_are_consumed_silently(lib, gpiod, crystals):
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    value = 0x5555_5555  # alternate bits: every set bit is its own rise
    try:
        first = backend.emit_pulses(17, encode(value))
        consumed = backend.last_frame.lead.consumed
        clock.sleep(0.8)
        second = backend.emit_pulses(17, encode(value + 1))
    finally:
        backend.close()
    assert len(rises_us(encode(value))) == 18
    assert consumed == len(rises_us(encode(value))) - 1
    assert backend.last_frame.lead.consumed == len(rises_us(encode(value + 1))) - 1
    assert first.between == 0 and second.between == 0
    assert second.tick is not None


def test_a_stamp_past_a_frames_end_is_the_next_frames_between(lib, gpiod, crystals):
    """A stamp after the frame's end is the next frame's to judge (spec §2.5 step 2),
    so it is carried over: stamped 15 ms late, TRAIL's rise lands 5 ms past the end."""
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        assert backend.emit_pulses(17, encode(1)).tick is not None  # warms the map
        clock.sleep(0.8)
        gpiod.stamp_late_us = 15_000
        late = backend.emit_pulses(17, encode(2))
        carried = backend.last_frame.lead.leftover
        gpiod.stamp_late_us = 0
        clock.sleep(0.8)
        after = backend.emit_pulses(17, encode(3))
    finally:
        backend.close()
    assert late.tick is None and len(carried) == 1
    assert after.tick is not None and after.between == 1


def test_frames_across_the_wrap_are_matched_and_ticked_in_32_bits(lib, gpiod, crystals):
    """P wraps at 2**32 us, 71.6 min. The frame that spans the wrap is matched, the
    frame after it hands over a raw 32-bit tick, and the drain's raw mark is unwrapped
    before it is held to the frame's end: compared raw, it never passed an end beyond
    the wrap, and every frame from then on waited out STAMP_DRAIN_WAIT_S."""
    from wl_sync.barcode import FRAME_US, encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        assert backend.emit_pulses(17, encode(1)).tick is not None
        clock.advance((2**32 - 100_000 - clock.p_now()) * 1_000)  # 100 ms short of it
        lead = len(gpiod.stamped)
        across = backend.emit_pulses(17, encode(2))
        clock.sleep(0.8)
        began = clock.ns
        after = backend.emit_pulses(17, encode(3))
        took_ns = clock.ns - began
    finally:
        backend.close()
    assert across.tick == gpiod.stamped[lead]
    assert after.tick is not None and 0 <= after.tick <= 0xFFFF_FFFF
    assert after.tick == gpiod.stamped[-len(rises_us(encode(3)))]
    assert took_ns < FRAME_US * 1_000 + 10_000_000, took_ns


def test_the_pump_drains_the_stamp_machine_on_its_own_thread(lib, gpiod, crystals):
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    began = len(lib.calls)
    # While capture runs: construction read the test machine's FIFO on this thread.
    fifo_began = len(lib.fifo_calls)
    try:
        for value in (1000, 1001):
            assert backend.emit_pulses(17, encode(value)).tick is not None
            clock.sleep(0.8)
        during = lib.calls[began:]
    finally:
        backend.close()
    gets = [(sm, thread) for call, sm, thread in lib.fifo_calls[fifo_began:]
            if call == "get"]
    assert any(sm == 1 for sm, _ in gets), "nothing was taken from the stamp machine"
    assert {thread for _, thread in gets} == {"wl-sync-words"}
    # Every call into the library while capture ran, not only the gets.
    assert during and {thread for _, thread in during} == {"wl-sync-words"}, sorted(
        set(during)
    )


def test_a_desynced_stamp_stream_is_reported_once_and_later_frames_are_stamped(
    lib, gpiod, crystals
):
    """THE RULE CHANGED IN TASK 1.15 (the user's ruling on final review I1, 2026-11-20).
    This test pinned that a desync stopped the stamp drain, and every later frame was
    missed as STAMPS_STOPPED until a restart. Now the stream is paired again on the
    counter where the slip shows, and stamping goes on. The word drain goes on too, as
    before: the box records anyway. The desync is reported once and counted, and the
    stamp drain is never reported stopped."""
    from wl_sync.barcode import encode

    clock = crystals()
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        # 10's data word lost, so the pairing slipped; then 20's pair, whole. The drain
        # reads all three words: 10 is taken at the slip, and 20's pair after it.
        lib.push_stamps(stream((10, 0))[1:] + stream((20, 0)))
        first = _next_complaint(backend)
        assert first is not None and "barcode stamping" in first, first
        assert "pairing slipped" in first and "resynchronised" in first, first
        clock.sleep(0.8)
        later = []
        for value in (1000, 1001, 1002):
            later.append(backend.emit_pulses(17, encode(value)))
            clock.sleep(0.8)
        lib.push(stream((10, 1), (20, 2)))
        assert _until(lambda: seen == [1, 2]), "the word drain stopped with the stamps"
        assert _next_complaint(backend, timeout=0.1) is None, "reported twice, or stopped"
    finally:
        backend.close()
    assert backend._stamp_pump.desyncs == 1 and not backend._stamp_pump.stopped
    assert [stamp.tick for stamp in later] == [gpiod.leads[k] for k in (1, 2, 3)]


def _health_kinds(backend):
    """The kinds check_health raises now, or none."""
    from wl_sync.backend import CaptureDegraded

    try:
        backend.check_health()
    except CaptureDegraded as error:
        return set(error.kinds)
    return set()


def test_a_full_stamp_fifo_and_a_lone_counter_desync_the_stream_and_b_recovers(
    lib, gpiod, crystals, caplog
):
    """Final review I1's trigger, end to end on an 8-word FIFO (M11a), as RP1's would be
    were its join not to take: the scenario is built on that depth, and the fake's is 16
    since Ruling R40, so it is set here. The pump is
    starved through frame 3, so its stamps fill the FIFO, and rise 4's pair is dropped
    whole. At rise 5 the drain runs at last, between the pair's two pushes: the data word
    was dropped, and the counter lands alone. The next batch desyncs. Until Task 1.15 that
    stopped the stamp drain, and every later B was dropped for the rest of the process;
    now the stream is paired again, every later frame is stamped at its own lead, and the
    desync is reported once and counted, and the run's end gives the count."""
    import logging

    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    lib.stamp_depth = 8
    gate = threading.Event()
    full_pairs = []

    def between_words():
        # Called between a pair's two pushes: when the FIFO is full, its data word was
        # just dropped.
        with lib._lock:
            full = len(lib.stamp_fifo) >= lib.stamp_depth
        if not full or gate.is_set():
            return
        full_pairs.append(gpiod.rise)
        if len(full_pairs) < 2:
            return  # rise 4: its counter is dropped too
        gate.set()  # rise 5: the drain looks, and takes a word before the counter lands
        assert _until(lambda: len(lib.stamp_fifo) < lib.stamp_depth, timeout=2)
        lib.stamp_gate = None

    value_of = {3: 0x5555_5555}  # 18 rises, so more than the FIFO's 4 pairs
    stamps, kinds = [], []
    try:
        for k in range(1, 9):
            if k == 3:
                lib.stamp_gate, lib.between_words = gate, between_words
            stamps.append(backend.emit_pulses(17, encode(value_of.get(k, 1000 + k))))
            kinds.append(_health_kinds(backend))
            clock.sleep(0.8)
    finally:
        with caplog.at_level(logging.INFO, logger=rp1.log.name):
            backend.close()
    assert "barcode stamping: 1 desync(s) this run, each resynchronised" in [
        record.getMessage() for record in caplog.records
    ]
    assert full_pairs == [4, 5] and lib.stamp_dropped == 3
    assert _wrong(stamps, gpiod) == []
    assert [stamp.tick for stamp in stamps[3:]] == [gpiod.leads[k] for k in range(4, 9)]
    assert [stamp.between for stamp in stamps[4:]] == [0] * 4
    desynced = [k for k, found in enumerate(kinds, 1)
                if "barcode stamping: DesyncError" in found]
    assert len(desynced) == 1, kinds
    assert not any("barcode stamping: stopped" in found for found in kinds), kinds
    assert backend._stamp_pump.desyncs == 1 and not backend._stamp_pump.stopped


def test_after_a_lone_word_the_stamp_drains_mark_still_moves(lib):
    """A word left alone shifts the stream against the FIFO by one, so the drain's even
    read left a counter behind at every look from then on: its mark never moved again,
    every frame waited out STAMP_DRAIN_WAIT_S, and each frame's last stamp reached the
    next frame as a stamp between. The stamp drain now reads every word, the pump carries
    a data word whose counter has not come, and the mark moves at every look."""
    from wl_sync.rp1.pio_capture import WORDS_LAG_US

    backend = _started(lib, lambda tick, word: None)
    pump = backend._stamp_pump
    try:
        backend._p_now = lambda: 5_000_000
        assert _until(lambda: pump.stamps_through == 5_000_000 - WORDS_LAG_US - 1)
        backend._p_now = lambda: 6_000_000
        # 10's data word lost: its counter, then 20's pair.
        lib.push_stamps([(~10) & 0xFFFF_FFFF] + stream((20, 0)))
        assert _until(lambda: pump.stamps_through == 6_000_000 - WORDS_LAG_US - 1)
        assert lib.stamp_fifo == []
        assert pump.take() == [10, 20] and pump.desyncs == 1
        lib.push_stamps(stream((30, 0), (40, 0)))
        assert _until(lambda: lib.stamp_fifo == [])
        assert _until(lambda: pump.take() == [30, 40])
    finally:
        backend.close()


@pytest.mark.parametrize("case", range(8))
@pytest.mark.parametrize("far", [False, True], ids=["p-near-0", "p-past-2**31"])
def test_after_a_lost_stamp_word_no_frame_gets_a_wrong_b(
    lib, gpiod, crystals, far, case
):
    """Fuzzed over the phase of the lost word: the data or the counter word of one rise of
    frame 2, 3 or 4, chosen at random, with GPIO0-15 carrying a different code at every
    stamp, and with P near 0 or past 2**31. A misread stamp lies before its frame, or,
    with P past 2**31, far past its end, and either way is counted between frames, once.
    A lost one costs at most its own frame's B, and every frame after is stamped at its
    lead."""
    import random

    from wl_sync.barcode import encode

    rng = random.Random(1150 + case)
    frame = rng.choice((2, 3, 4))
    rise = rng.randrange(len(rises_us(encode(1000 + frame))))
    gpiod.lost_words[(frame, rise)] = rng.randrange(2)
    gpiod.data_word = lambda k, r: (k * 7919 + r * 104_729) & 0xFFFF
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    if far:  # P past 2**31, where unwrap_near puts the misread after the frame
        clock.advance((2**31 + 100_000_000) * 1_000)
    try:
        stamps, traces = _frames(backend, clock, gpiod, 7)
    finally:
        backend.close()
    lost = (frame, rise, gpiod.lost_words[(frame, rise)], far)
    if far:
        assert gpiod.leads[1] > 2**31
    assert _wrong(stamps, gpiod) == [], lost
    # The frame loses its B only when the word lost was its lead's counter, its stamp.
    kept = (rise, lost[2]) != (0, 1)
    assert (stamps[frame - 1].tick is not None) == kept, (lost, stamps[frame - 1])
    assert [stamp.tick for stamp in stamps[frame:]] == [
        gpiod.leads[k] for k in range(frame + 1, 8)
    ], lost
    # In phase again by then: no frame's stamps reach the next. Past 2**31 the misread is
    # carried, so the frame after the one that queued it may count it.
    settled = frame + 1 + far
    assert [stamp.between for stamp in stamps[settled:]] == [0] * (7 - settled), lost
    # Coverage: the misread is one stamp between frames, at one frame, so the run loop's
    # journal gives it an episode of one frame, a WARNING to open it and one to close it
    # (wl_sync/cli.py `_StampsBetweenFrames`; Task 1.17), never one a frame.
    assert sum(stamp.between for stamp in stamps) <= 1, (lost, stamps)
    if far and lost[2] == 1:  # a lone data word: its misread is carried, never matched
        assert any(trace.lead.leftover and max(trace.lead.leftover) >= 0xFFFF_0000
                   for trace in traces[frame - 1:frame + 1]), lost
    assert backend._stamp_pump.desyncs == 1 and not backend._stamp_pump.stopped, lost


def test_the_stamp_drain_is_marked_on_the_fits_count_not_on_now_us(lib):
    """The frame's end the mark is compared with is mapped through the fit, so the mark
    is too, whatever now_us() reads: here patched apart from it, as MONO, which a split
    process counts, drifts from P by the crystals' difference."""
    from wl_sync.rp1.pio_capture import WORDS_LAG_US

    backend = _started(lib, lambda tick, word: None)
    try:
        backend._p_now = lambda: 5_000_000
        backend.now_us = lambda: 7_000_000
        assert _until(
            lambda: backend._stamp_pump.stamps_through == 5_000_000 - WORDS_LAG_US - 1
        ), backend._stamp_pump.stamps_through
    finally:
        backend.close()


def test_the_fit_follows_the_pio_count_once_pairs_are_fitted(lib, gpiod, crystals):
    """RP1's crystal 40 ppm fast of the CPU's. Forty frames fit the slope, so the fit
    carries P across half a minute with no frame, where MONO falls behind by ms."""
    from wl_sync.barcode import encode

    clock = crystals(cpu_ppm=0, rp1_ppm=40)
    backend = _started(lib, lambda tick, word: None)
    try:
        for k in range(40):
            assert backend.emit_pulses(17, encode(1000 + k)).tick is not None, k
            clock.sleep(0.3)
    finally:
        backend.close()  # the pump's sleeps stop moving true time between the reads
    clock.sleep(30)
    # The truth either side of the read, and its midpoint: the read samples there.
    before = clock.p_now()
    mapped = backend._p_now()
    truth = (before + clock.p_now()) / 2
    assert abs(mapped - truth) <= 2, (mapped, truth)
    assert abs((clock.ns - backend._origin_ns) // 1_000 - clock.p_now()) > 1_000  # MONO


def test_the_bracket_the_fit_and_the_mark_follow_raw_not_mono(lib, gpiod, crystals):
    """NTP slews MONO and leaves RAW alone, which is why the fit is on RAW. Here MONO
    runs 500 ppm fast of RAW, the kernel's limit (spec §2.2,
    docs/superpowers/specs/2026-10-30-one-clock-design.md:222), and 10 s ahead. A MONO
    read in the fit's origin, in the bracket's RAW, in `_p_now` or at the frame's end
    puts those seconds, or milliseconds of slew, into P: the last as a frame that waits
    out STAMP_DRAIN_WAIT_S for a mark 10 s short of its end. Less than half a wrap, so
    the unwrap cannot hide the offset."""
    from wl_sync.barcode import FRAME_US, encode

    clock = crystals(mono_slew_ppm=500, mono_offset_ns=10 * 10**9)
    backend = _started(lib, lambda tick, word: None)
    try:
        # The origin: before any pair, RAW maps through it. The fixture's P runs some
        # 10 counts behind the origin (_TwoCrystalClock).
        before = clock.p_now()
        unpaired = backend._p_now() - (before + clock.p_now()) / 2
        stamps = []
        for value in range(1000, 1006):
            began = clock.ns
            stamps.append(backend.emit_pulses(17, encode(value)))
            took_ns = clock.ns - began
            clock.sleep(0.8)
        pairs = backend.take_pairs()
    finally:
        backend.close()
    assert abs(unpaired) <= 100, unpaired
    assert took_ns < FRAME_US * 1_000 * 1.0005 + 25_000_000, took_ns
    assert [stamp.tick is not None for stamp in stamps] == [True] * 6
    for pair in pairs:  # the bracket: RAW where RAW goes, MONO where MONO goes
        assert abs((pair.raw_ns - clock.p_zero_ns) // 1_000 - pair.p) <= 1, pair
        assert pair.mono_ns - pair.raw_ns > 10 * 10**9, pair
    before = clock.p_now()  # the fit and the mark, carried on RAW alone
    mapped = backend._p_now()
    assert abs(mapped - (before + clock.p_now()) / 2) <= 2


def test_a_wide_bracket_is_kept_out_of_the_fit_but_still_handed_over(lib, gpiod, crystals):
    from wl_sync.barcode import encode
    from wl_sync.clockmap import BRACKET_WIDTH_MAX_NS

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    added = []
    fit_add = backend._fit.add
    backend._fit.add = lambda raw_ns, p: (added.append((raw_ns, p)), fit_add(raw_ns, p))
    try:
        gpiod.set_cost_ns = 2 * BRACKET_WIDTH_MAX_NS
        wide = backend.emit_pulses(17, encode(1000))
        gpiod.set_cost_ns = 0
        for value in (1001, 1002, 1003):
            clock.sleep(0.8)
            assert backend.emit_pulses(17, encode(value)).tick is not None
        pairs = backend.take_pairs()
    finally:
        backend.close()
    assert wide.tick is not None, "a wide bracket still stamps its frame"
    assert len(pairs) == 4
    assert pairs[0].p & 0xFFFF_FFFF == wide.tick
    assert pairs[0].width_ns > BRACKET_WIDTH_MAX_NS and not pairs[0].narrow
    # The pump's sleep can land inside a bracket, so the narrow ones are found, not named.
    assert any(pair.narrow for pair in pairs[1:])
    assert added == [(pair.raw_ns, pair.p) for pair in pairs if pair.narrow]
    assert backend.take_pairs() == [], "pairs are handed over once"


def test_every_frame_leaves_its_bracket_width_stamped_or_not(lib, gpiod, crystals):
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        backend.emit_pulses(17, encode(1000))
        stamped = backend.last_frame
        gpiod.pio_sees_barcode = False
        clock.sleep(0.8)
        backend.emit_pulses(17, encode(1001))
        unstamped = backend.last_frame
    finally:
        backend.close()
    assert stamped.lead.p is not None and stamped.width_ns > 0
    assert unstamped.lead.p is None
    assert unstamped.width_ns > 0


# Ruling R15: a stamp machine that never started (R12) costs nothing per frame. From Task
# 1.10a such a process records split, which drains SM 0 alone and has no stamp path.


def test_with_no_stamp_machine_the_pump_drains_the_capture_machine_alone(lib):
    """Coverage: passes on the sources before Task 1.10, which drain no stamp machine at
    all. It pins R15 against the new drain: no machine, no FIFO calls on SM 1."""
    lib.stamp_result = -16
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        lib.push(stream((10, 1)))
        assert _until(lambda: seen == [1])
        drains = lib.drains + 3
        assert _until(lambda: lib.drains >= drains)
    finally:
        backend.close()
    assert lib.fifo_calls
    assert {sm for _, sm, _ in lib.fifo_calls} == {0}


def test_with_no_stamp_machine_check_health_does_not_report_it_again(lib, gpiod, crystals):
    """The construction logged its ERROR once; every check after says nothing.

    Coverage: passes on the sources before Task 1.10, which have no stamp path in
    check_health. It pins R15 against the new one."""
    from wl_sync.barcode import encode

    clock = crystals()
    lib.stamp_result = -16
    backend = _started(lib, lambda tick, word: None)
    try:
        assert _next_complaint(backend, timeout=0.05) is None
        backend.emit_pulses(17, encode(1000))
        clock.sleep(0.8)
        backend.emit_pulses(17, encode(1001))
        assert _next_complaint(backend, timeout=0.05) is None
    finally:
        backend.close()
    backend.check_health()


# --- One count, and the split path kept as a mode (Task 1.10, second half) --------------
#
# By default a process records on one count: both machines run, B is the stamp, and
# now_us() is the RAW->P fit's. `_fall_back_to_split`, which Task 1.10a calls when the
# stamp machine fails its start-up test, keeps today's split path for the whole process.


def _start(backend, path):
    """Start one capture path: the strobed words, or the edges. On one count the edges map
    through the fit the strobed start makes, so they cannot start alone
    (`start_edge_capture`), and the words start first, as run() starts them. So a one-count
    [edges] leg repeats its [strobed] leg, whose start has already fixed the mode, and adds
    only that starting edges after it changes nothing (Ruling R33, Minor 7). Only a split
    [edges] leg starts the edges alone."""
    if path == "strobed" or not backend._split:
        backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    if path == "edges":
        backend.start_edge_capture([20], lambda gpio, level, tick: None)


def _now_less_p(backend, clock):
    """now_us() less the PIO count at the read's middle, where the read samples. Taken with
    no pump running, so only this thread moves true time between the reads."""
    before = clock.p_now()
    mapped = backend.now_us()
    return mapped - (before + clock.p_now()) / 2


def test_now_us_runs_at_the_pio_rate_once_pairs_are_fitted(lib, gpiod, crystals):
    """RP1's crystal 40 ppm fast of the CPU's. now_us() is the fit's P (spec §2.4,
    docs/superpowers/specs/2026-10-30-one-clock-design.md:375-387), so half a minute with
    no frame leaves it on P, where MONO us since the origin, today's now_us(), fall ms
    behind."""
    from wl_sync.barcode import encode

    clock = crystals(cpu_ppm=0, rp1_ppm=40)
    backend = _started(lib, lambda tick, word: None)
    try:
        for k in range(40):
            assert backend.emit_pulses(17, encode(1000 + k)).tick is not None, k
            clock.sleep(0.3)
    finally:
        backend.close()  # the pump's sleeps stop moving true time between the reads
    clock.sleep(30)
    assert abs(_now_less_p(backend, clock)) <= 2
    mono_us = (clock.ns - backend._origin_ns) // 1_000
    assert abs(mono_us - clock.p_now()) > 1_000


@pytest.mark.parametrize("path", ["strobed", "edges"])
@pytest.mark.parametrize("split", [False, True], ids=["one-count", "split"])
def test_a_calibration_applied_after_capture_started_is_refused(lib, gpiod, split, path):
    """One rate for the whole process (spec §2.2): ticks already placed are at the rate
    they had, so a later one would bend the clock between them. Refused in either mode,
    and the timebase the headers already carry stands. The one-count [edges] leg repeats
    [strobed] (`_start`)."""
    backend = Rp1Backend()
    if split:
        backend._fall_back_to_split("x")
    _start(backend, path)
    try:
        with pytest.raises(RuntimeError, match="before capture starts"):
            backend.apply_calibration(_calibration(rate_ppb=40_000, p_per_raw_ppb=40_000))
        assert backend.timebase == ("split" if split else "rp1-pio")
        assert backend._seed_ppb == 0
    finally:
        backend.close()


def test_a_calibration_seeds_the_fit_before_the_first_pair(lib, gpiod, crystals):
    """RP1's crystal 40 ppm fast of the CPU's, and a calibration that says so. Before any
    pair the fit runs through the origin at the seeded rate (spec §2.4), so 10 s on,
    now_us() still keeps pace with P. Unseeded it falls 400 counts behind.

    P's zero in the fixture lies 10 counts after the RAW origin
    (_TwoCrystalClock), where on silicon it lies one syscall after it. The first pair
    takes that offset up; before any pair it stays, so it is read at the start and taken
    out. What the seed must keep is that offset, to 2 counts."""
    clock = crystals(cpu_ppm=0, rp1_ppm=40)
    backend = Rp1Backend()
    backend.apply_calibration(_calibration(rate_ppb=40_000, p_per_raw_ppb=40_000))
    on_word, inside, release = _holding_the_pump()
    backend.start_strobed_capture(0, DATA, DATA, on_word)
    try:
        lib.push(stream((10, 1)))
        assert inside.wait(1)  # the pump held: only this thread moves true time
        at_start = _now_less_p(backend, clock)
        clock.sleep(10)
        assert gpiod.stamped == [], "a frame was emitted"
        assert abs(_now_less_p(backend, clock) - at_start) <= 2
    finally:
        release.set()
        backend.close()


def test_the_rp1_backend_stamps_the_edge_itself_so_its_delay_is_zero(lib):
    """B is the stamp machine's count of GPIO17's own edge (spec §2.6), so nothing lies
    between B and the wire. A split process reads B from the clock before the frame, as
    before, and the run loop uses --barcode-delay-us."""
    backend = Rp1Backend()
    try:
        assert backend.barcode_delay_us == 0
        backend._fall_back_to_split("x")
        assert backend.barcode_delay_us is None
    finally:
        backend.close()


def test_a_split_process_runs_the_capture_machine_alone_as_today(lib, monkeypatch):
    """Today's start, kept: the origin, then the capture machine enabled by itself. The
    stamp machine, configured at construction, is never enabled, nothing is sync-enabled
    but the start-up test's machine, alone (Task 1.16, which until then was enabled the
    plain way, so this test saw no sync enable at all), and no stamp pump or fit is
    made."""
    real = rp1.time.clock_gettime_ns
    names = {time.CLOCK_MONOTONIC: "origin", time.CLOCK_MONOTONIC_RAW: "raw"}
    capturing = threading.Event()

    def clock(which):
        if capturing.is_set() and lib.enables[-1] != (0, True):
            lib.log.append(names.get(which, f"clock {which}"))
        return real(which)

    backend = Rp1Backend()
    backend._fall_back_to_split("x")
    monkeypatch.setattr(rp1.time, "clock_gettime_ns", clock)
    built, enables = len(lib.log), len(lib.enables)
    capturing.set()
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    try:
        started = [e for e in lib.log[built:]
                   if e.startswith(("start_", "sync=", "enabled=", "clock "))
                   or e in names.values()]
        assert started == ["origin", "enabled=True"]
        assert lib.enables[enables:] == [(0, True)]
        assert [e for e in lib.log if e.startswith("sync=")] == ["sync=0x4"]
        assert backend._stamp_pump is None and backend._fit is None
    finally:
        backend.close()
    assert (1, True) not in lib.enables


def test_a_split_process_stamps_b_from_the_clock_read_before_the_frame(
    lib, gpiod, crystals
):
    """B is now_us(), MONO us since the origin, read before the first level is set, as the
    run loop read it before Task 1.7. Nothing is stamped and no pair is made."""
    from wl_sync.backend import FrameStamp
    from wl_sync.barcode import encode

    crystals()
    backend = Rp1Backend()
    backend._fall_back_to_split("x")
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    real = backend.now_us
    reads = []

    def now_us():
        value = real()
        reads.append((threading.current_thread().name, len(gpiod.sets), value))
        return value

    backend.now_us = now_us
    calls = len(lib.calls)
    before = len(gpiod.sets)  # the start-up test's pulse, at construction
    try:
        stamp = backend.emit_pulses(17, encode(1000))
        pairs = backend.take_pairs()
        during = lib.calls[calls:]
    finally:
        backend.close()
    mine = [(sets, value) for thread, sets, value in reads if thread != "wl-sync-words"]
    assert len(mine) == 1 and mine[0][0] == before, mine
    assert stamp == FrameStamp(mine[0][1])
    assert pairs == [] and gpiod.stamped == []
    # piolib keeps one caller in this mode too, the pump's thread.
    assert during and {thread for _, thread in during} == {"wl-sync-words"}


@pytest.mark.parametrize("path", ["strobed", "edges"])
def test_falling_back_after_capture_started_is_refused(lib, gpiod, path):
    """The mode is the process's: now_us() counts P on one count and MONO on split, and a
    path already started reads one of them. Both legs run on one count, so [edges]
    repeats [strobed] (`_start`)."""
    backend = Rp1Backend()
    _start(backend, path)
    try:
        with pytest.raises(RuntimeError, match="before capture starts"):
            backend._fall_back_to_split("x")
        assert backend.timebase == "rp1-pio" and backend.barcode_delay_us == 0
    finally:
        backend.close()


# Ruling R16: the word drain stops alone, as the stamp drain does. From this half B is the
# stamp, so a word drain that took the stamp drain with it would drop every later B.


def test_a_desynced_word_stream_stops_neither_drain_and_frames_are_still_stamped(
    lib, gpiod, crystals
):
    """THIS TEST PINNED THAT A DESYNC STOPPED THE WORD DRAIN, leaving the stamp drain
    alone (Ruling R16). Since Task 1.20 it stops neither: after it both machines are
    drained, the frame is stamped, and piolib keeps its one caller."""
    from wl_sync.backend import FrameStamp
    from wl_sync.barcode import encode

    crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        _lose_a_counters_data_word(lib)
        assert _until(lambda: backend._words.desyncs == 1)
        complaint = _next_complaint(backend)
        assert complaint is not None and "1 desync(s) this run" in complaint, complaint
        held = len(lib.fifo_calls)  # the desync is held: its drain's calls are all made
        calls = len(lib.calls)
        stamp = backend.emit_pulses(17, encode(1000))
        assert stamp == FrameStamp(gpiod.stamped[0])
        assert backend._word_thread.is_alive()
        after, during = lib.fifo_calls[held:], lib.calls[calls:]
    finally:
        backend.close()
    assert {sm for _, sm, _ in after} == {0, 1}, sorted(set(after))
    # piolib keeps one caller, the pump's thread (spec §2.1).
    assert during and {thread for _, thread in during} == {"wl-sync-words"}


def test_a_word_drain_that_raises_stops_alone(lib, gpiod, crystals):
    from wl_sync.backend import CaptureDegraded, FrameStamp
    from wl_sync.barcode import encode

    crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        lib.get_failures[0] = OSError("EIO")
        lib.push(stream((10, 1)))
        complaint = _next_complaint(backend)
        assert complaint is not None and "event-code capture: EIO" in complaint, complaint
        held = len(lib.fifo_calls)
        stamp = backend.emit_pulses(17, encode(1000))
        assert stamp == FrameStamp(gpiod.stamped[0])
        assert backend._word_thread.is_alive()
        after = lib.fifo_calls[held:]
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
    finally:
        backend.close()
    assert after and {sm for _, sm, _ in after} == {1}, sorted(set(after))
    assert raised.value.kinds == {"event-code capture: stopped"}, raised.value.kinds


def test_a_failing_consumer_stops_the_word_drain_alone(lib, gpiod, crystals):
    """drain() returns False for a consumer that raises (`WordPump.handle`), and since
    Task 1.20 for nothing else: the records are not being kept. The word drain stops, and
    the stamp drain goes on."""
    from wl_sync.backend import FrameStamp
    from wl_sync.barcode import encode

    def on_word(tick, word):
        raise RuntimeError("the sink is closed")

    crystals()
    backend = _started(lib, on_word)
    try:
        lib.push(stream((10, 1)))
        complaint = _next_complaint(backend)
        assert complaint is not None, complaint
        assert "event-code capture: the sink is closed" in complaint, complaint
        held = len(lib.fifo_calls)
        stamp = backend.emit_pulses(17, encode(1000))
        assert stamp == FrameStamp(gpiod.stamped[0])
        assert backend._word_thread.is_alive()
        after = lib.fifo_calls[held:]
    finally:
        backend.close()
    assert after and {sm for _, sm, _ in after} == {1}, sorted(set(after))


def test_a_stopped_word_drain_is_reported_on_every_check_and_left_out_of_delivered_through(
    lib, gpiod
):
    """Every call, as a dead word thread was: the run loop keys an episode on its kinds
    (wl_sync/backend.py:32-42). And a roll must not wait for words the drain will never
    deliver, as it did not for a dead thread."""
    from wl_sync.backend import CaptureDegraded

    backend = _started(lib, lambda tick, word: None)
    try:
        lib.get_failures[0] = OSError("EIO")  # since Task 1.20 a desync stops nothing
        lib.push(stream((10, 1)))
        assert _until(lambda: "event-code capture" not in backend.delivered_through())
        kinds = []
        for _ in range(2):
            with pytest.raises(CaptureDegraded) as raised:
                backend.check_health()
            kinds.append(raised.value.kinds)
        assert backend._word_thread.is_alive()
        assert "event-code capture" not in backend.delivered_through()
    finally:
        backend.close()
    assert all("event-code capture: stopped" in k for k in kinds), kinds
    assert "event-code capture: OSError" in kinds[0], kinds
    assert "event-code capture: OSError" not in kinds[1], "the failure reported twice"


def test_the_pump_thread_ends_when_both_drains_have_stopped(lib, gpiod):
    """Both drains are stopped here by a read that fails. Until Task 1.15 a desync
    stopped the stamp drain, and until Task 1.20 the word drain; a desync now stops
    neither (`StampPump`, `WordPump`), and a drain that raises is the one way, beside the
    thread's own end."""
    from wl_sync.backend import CaptureDegraded

    backend = _started(lib, lambda tick, word: None)

    def stamp_drains():
        return sum(1 for call, sm, _ in lib.fifo_calls if call == "rx_full" and sm == 1)

    try:
        lib.get_failures[0] = OSError("EIO")  # the word drain stops; a desync would not
        lib.push(stream((10, 1)))
        assert _until(lambda: "event-code capture" not in backend.delivered_through())
        drains = stamp_drains() + 3
        assert _until(lambda: stamp_drains() >= drains)
        assert backend._word_thread.is_alive(), "it ended with the stamp drain running"
        lib.get_failures[1] = OSError("EIO")  # and then the stamp drain
        lib.push_stamps(stream((10, 0)))
        assert _until(lambda: not backend._word_thread.is_alive())
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
    finally:
        backend.close()
    stopped = {"event-code capture: stopped", "barcode stamping: stopped"}
    assert stopped <= raised.value.kinds, raised.value.kinds


# Ruling R19: the fit finds the lead edges again. Below FIT_FREE_SLOPE_PAIRS only a matched
# pair moves the fit (wl_sync/clockmap.py:154-163), so a fit that has lost the lead edges
# gets no pair to correct it. Three shifted frames in a row, their offsets agreeing,
# restart it.


def _frames(
    backend, clock, gpiod, count, *, blind=(), wide=(), pause=None,
    value_of=lambda k: 1000 + k,
):
    """`count` frames, one a second, numbered from 1 as the gpiod fake numbers them, with
    the PIO blind to GPIO17 through the frames in `blind`, and every `set_value` of the
    frames in `wide` slow enough to widen the lead's bracket past BRACKET_WIDTH_MAX_NS.
    `pause` maps a frame to the seconds of LOW after it, 0.8 when it does not name it.
    Each frame's stamp and trace."""
    from wl_sync.barcode import encode
    from wl_sync.clockmap import BRACKET_WIDTH_MAX_NS

    stamps, traces = [], []
    for k in range(1, count + 1):
        gpiod.pio_sees_barcode = k not in blind
        gpiod.set_cost_ns = 2 * BRACKET_WIDTH_MAX_NS if k in wide else 0
        stamps.append(backend.emit_pulses(17, encode(value_of(k))))
        traces.append(backend.last_frame)
        clock.sleep((pause or {}).get(k, 0.8))
    assert gpiod.frame == count
    return stamps, traces


def _restarts(caplog):
    messages = [record.getMessage() for record in caplog.records]
    return [message for message in messages if "lost the lead edges" in message]


def test_a_fit_anchored_at_a_wrong_stamp_finds_the_lead_edges_again(
    lib, gpiod, crystals, caplog
):
    """R19's first way, simulated on the first half: at 300 ppm with seed 0, frame 5's lead
    stamped 900 us early, inside its window, became the newest-pair anchor, and every
    later lead lay further than LEAD_STAMP_TOLERANCE from the line."""
    import logging

    clock = crystals(cpu_ppm=0, rp1_ppm=300)
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us[5] = -900
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, 60)
    finally:
        backend.close()
    assert [stamps[k - 1].tick for k in range(15, 61)] == [
        gpiod.leads[k] for k in range(15, 61)
    ]
    dropped = [k for k, stamp in enumerate(stamps, 1) if stamp.tick is None]
    assert len(dropped) <= rp1.REACQUIRE_FRAMES + 2 and max(dropped) < 15, dropped
    [restart] = _restarts(caplog)
    assert rp1.REACQUIRE_FRAMES == 3
    assert re.fullmatch(
        r"the RAW->P fit lost the lead edges \(3 frames in a row stamped \d+ us from their "
        r"brackets\) and restarts at the last of them",
        restart,
    ), restart


def test_a_long_run_of_misses_before_the_thirtieth_pair_does_not_lock_out(
    lib, gpiod, crystals, caplog
):
    """R19's second way: at 100 ppm with seed 0, 15 s without a stamp carries the fit
    1.6 ms from P, past LEAD_STAMP_TOLERANCE, so no lead edge matched again."""
    import logging

    clock = crystals(cpu_ppm=0, rp1_ppm=100)
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, 30, blind=range(6, 21))
    finally:
        backend.close()
    matched = [k for k, stamp in enumerate(stamps, 1) if stamp.tick is not None]
    assert matched[:5] == [1, 2, 3, 4, 5]
    assert [stamps[k - 1].tick for k in range(24, 31)] == [
        gpiod.leads[k] for k in range(24, 31)
    ]
    assert len(_restarts(caplog)) == 1, _restarts(caplog)


def test_a_frame_whose_lead_edge_alone_went_unstamped_is_not_taken_for_shifted(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """It has one stamp fewer than its rising edges, and its first is its second rise,
    20 ms after the lead (wl_sync/barcode.py:5-7): taken for the lead, B would be 20 ms
    off. Three such frames in a row, each with its top bit set, so their first stamps
    agree: a rule that took the first stamp for the lead would restart the fit here, and
    the next frame's own second rise would then match. The frame's rises align on none
    of its stamps (Ruling R26), so nothing is shifted.

    Coverage: passes on the sources before, which never restart the fit. It pins the
    rule that the lead be the stamp the rises align on against the new rule."""
    import logging

    from wl_sync.barcode import encode
    from wl_sync.clockmap import RawToP

    restarted = []
    monkeypatch.setattr(RawToP, "restart", lambda fit, *pair: restarted.append(pair),
                        raising=False)
    clock = crystals(cpu_ppm=0, rp1_ppm=100)
    backend = _started(lib, lambda tick, word: None)
    gpiod.unstamped_rises.update({k: {0} for k in (4, 5, 6)})

    def value_of(k):
        return 0x8000_0000 | k

    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, 8, value_of=value_of)
    finally:
        backend.close()
    for k in (4, 5, 6):
        lead = traces[k - 1].lead
        assert stamps[k - 1].tick is None, k
        rises = len(rises_us(encode(value_of(k))))
        assert (lead.between, lead.consumed) == (0, rises - 1), k
    assert [stamps[k - 1].tick for k in (7, 8)] == [gpiod.leads[7], gpiod.leads[8]]
    assert restarted == [] and _restarts(caplog) == []


def test_shifted_frames_whose_offsets_disagree_do_not_restart_the_fit(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """Three shifted frames, stamped 2, 5 and 8 ms from their brackets: each 3 ms from the
    one before, more than LEAD_STAMP_TOLERANCE. Restarted at the third, the line would lie
    8 ms from the next frame's lead.

    Coverage: passes on the sources before, which never restart the fit. It pins the
    agreement condition against the new rule."""
    import logging

    from wl_sync.barcode import encode
    from wl_sync.clockmap import RawToP

    restarted = []
    monkeypatch.setattr(RawToP, "restart", lambda fit, *pair: restarted.append(pair),
                        raising=False)
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us.update({4: 2_000, 5: 5_000, 6: 8_000})
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, 8)
    finally:
        backend.close()
    for k in (4, 5, 6):  # shifted: the rises align on the frame's own lead, outside
        lead = traces[k - 1].lead
        assert stamps[k - 1].tick is None, k
        assert lead.lead == gpiod.leads[k], k
        assert lead.between + lead.consumed == len(rises_us(encode(1000 + k))) - 1, k
        assert lead.leftover == (), k
    assert [stamps[k - 1].tick for k in (7, 8)] == [gpiod.leads[7], gpiod.leads[8]]
    assert restarted == [] and _restarts(caplog) == []


def test_shifted_frames_with_wide_brackets_do_not_restart_the_fit(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """Three frames stamped 2.0, 2.1 and 2.2 ms from their brackets, which agree, but each
    bracket wider than BRACKET_WIDTH_MAX_NS: a midpoint that uncertain is no place to
    restart a line, as a wide pair is no pair to fit (wl_sync/clockmap.py:39-41).
    Restarted at the third, the line would lie 2.2 ms from the next frame's lead.

    Coverage: passes on the sources before, which never restart the fit. It pins the
    narrow-bracket condition against the new rule."""
    import logging

    from wl_sync.clockmap import BRACKET_WIDTH_MAX_NS, RawToP

    restarted = []
    monkeypatch.setattr(RawToP, "restart", lambda fit, *pair: restarted.append(pair),
                        raising=False)
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us.update({4: 2_000, 5: 2_100, 6: 2_200})
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, 8, wide=(4, 5, 6))
    finally:
        backend.close()
    for k in (4, 5, 6):
        assert stamps[k - 1].tick is None and traces[k - 1].width_ns > BRACKET_WIDTH_MAX_NS
    assert [stamps[k - 1].tick for k in (7, 8)] == [gpiod.leads[7], gpiod.leads[8]]
    assert restarted == [] and _restarts(caplog) == []


# Ruling R24: a frame is shifted only if its stamps follow its own rises. With the line more
# than 10 ms behind P, each frame's TRAIL stamp lands past its mapped end and is carried to
# the next frame, whose count then matches: one stamp in, one out. Restarted at that TRAIL,
# about 0.8 s off, the fit matched every later B to the frame before's TRAIL.


def _wrong(stamps, gpiod):
    """(frame, us off) for every B written for an edge other than its frame's lead."""
    return [(k, stamp.tick - gpiod.leads[k]) for k, stamp in enumerate(stamps, 1)
            if stamp.tick is not None and stamp.tick != gpiod.leads[k]]


def _restarted_at(monkeypatch):
    """Every `RawToP.restart`, recorded, and made."""
    from wl_sync.clockmap import RawToP

    restarted, restart = [], RawToP.restart

    def spy(fit, raw_ns, p):
        restarted.append((raw_ns, p))
        restart(fit, raw_ns, p)

    monkeypatch.setattr(RawToP, "restart", spy)
    return restarted


def _restart_offsets(caplog):
    return [int(re.search(r"stamped (-?\d+) us", message).group(1))
            for message in _restarts(caplog)]


def test_a_line_more_than_10_ms_behind_p_is_not_restarted_at_the_trail_before(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """Every stamp from frame 4 on lands 15 ms after the line, as a line 15 ms behind P
    puts it: past TRAIL's 10 ms before the end (wl_sync/barcode.py:3-9), so each frame's
    TRAIL is carried into the next. By the count alone the fit restarted at that carried
    TRAIL, 0.8 s off (Ruling R24). The frame's own rises align on its own lead, all but
    its TRAIL (Ruling R26): three such frames restart the fit at the third's lead, and
    every B after it is its own."""
    import logging

    restarted = _restarted_at(monkeypatch)
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us.update({k: 15_000 for k in range(4, 13)})
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, 12)
    finally:
        backend.close()
    wrong = _wrong(stamps, gpiod)
    assert wrong == [], f"B written for another edge: (frame, us off) {wrong}"
    leads = [gpiod.leads[k] for k in range(1, 13)]
    assert [stamp.tick for stamp in stamps] == leads[:3] + [None] * 3 + leads[6:]
    [offset] = _restart_offsets(caplog)
    assert abs(offset - 15_000) <= 1_000, offset
    assert [p for _, p in restarted] == [gpiod.leads[6]]
    # The shape R24 is about: frames 5 and 6 began with the TRAIL carried to them, and
    # their own lead was the one aligned.
    assert [traces[k - 1].lead.between for k in (5, 6)] == [1, 1]
    assert [traces[k - 1].lead.lead for k in (4, 5, 6)] == leads[3:6]


def test_after_a_long_blind_run_a_line_10_ms_behind_is_not_restarted_at_a_trail(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """The plan's own residual, by drift: 100 ppm with seed 0, five frames stamped, then
    110 s with none, which leaves the line about 11 ms behind P
    (docs/superpowers/plans/2026-11-07-one-clock.md:1485). 110 s of LOW leaves the fit
    where 110 blind frames would: only a matched pair moves it. No B is written for an
    edge not its own, and no restart lands anywhere but a frame's own lead."""
    import logging

    _restarted_at(monkeypatch)
    clock = crystals(cpu_ppm=0, rp1_ppm=100)
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, 17, pause={5: 110.0})
    finally:
        backend.close()
    wrong = [(k, stamp.tick - gpiod.leads[k]) for k, stamp in enumerate(stamps, 1)
             if stamp.tick is not None and stamp.tick != gpiod.leads[k]]
    assert wrong == [], f"B written for another edge: (frame, us off) {wrong}"
    assert [stamp.tick for stamp in stamps[:5]] == [gpiod.leads[k] for k in range(1, 6)]
    assert [stamp.tick for stamp in stamps[5:8]] == [None] * 3
    offsets = _restart_offsets(caplog)
    assert all(0 < offset < 20_000 for offset in offsets), _restarts(caplog)


# Ruling R26: a frame's stamps are matched to its own rises as a whole. Its lead is the
# stamp its rises align on (wl_sync/clockmap.py, `match_lead_stamp`), so no other stamp can
# become B or anchor the fit.


def test_a_line_20_ms_ahead_of_p_does_not_take_the_second_rise_for_the_lead(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """Every stamp from frame 4 on lands 20 ms before the line, as a line 20 ms ahead of P
    puts it. Each frame's top bit is set, so its second rise comes 20 ms after its lead
    (wl_sync/barcode.py:5-7) and its stamp lands in the lead's window: taken for the lead,
    every B was 20 ms off, and the fit anchored there. The rises align on the frame's own
    lead, outside the window, so three such frames restart the fit at it, and every later
    B is its own lead."""
    import logging

    restarted = _restarted_at(monkeypatch)
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us.update({k: -20_000 for k in range(4, 13)})
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(
                backend, clock, gpiod, 12, value_of=lambda k: 0x8000_0000 | k
            )
    finally:
        backend.close()
    wrong = _wrong(stamps, gpiod)
    assert wrong == [], f"B written for another edge: (frame, us off) {wrong}"
    assert [stamp.tick for stamp in stamps[3:6]] == [None] * 3
    assert [stamp.tick for stamp in stamps[6:]] == [gpiod.leads[k] for k in range(7, 13)]
    [offset] = _restart_offsets(caplog)
    assert abs(offset + 20_000) <= 1_000, offset
    assert [p for _, p in restarted] == [gpiod.leads[6]]


def test_a_frame_missing_one_later_rise_is_still_matched_to_its_own_lead(
    lib, gpiod, crystals
):
    """A later edge the stamp machine missed costs the frame nothing: its lead and its
    other rises align, and the minimum is all the rises but one.

    Coverage: passes on the sources before, which looked at the lead's window alone. It
    pins the minimum against the alignment."""
    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.unstamped_rises[3] = {2}
    try:
        stamps, traces = _frames(backend, clock, gpiod, 4)
    finally:
        backend.close()
    assert [stamp.tick for stamp in stamps] == [gpiod.leads[k] for k in range(1, 5)]
    assert traces[2].lead.consumed == len(rises_us(encode(1003))) - 2


def test_a_frame_missing_two_later_rises_is_dropped_as_off_its_rises(
    lib, gpiod, crystals, caplog
):
    """Two later edges missing leave too few stamps on the frame's rises, though its lead's
    stamp lies in its window, which alone was enough before: its B is dropped, with a
    cause of its own, and nothing restarts."""
    import logging

    from wl_sync.barcode import encode

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.unstamped_rises[3] = {1, 2}
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, 4)
    finally:
        backend.close()
    assert len(rises_us(encode(1003))) == 5  # so two missing leave three, under four
    assert stamps[2].tick is None
    from wl_sync.backend import STAMPS_OFF_RISES

    assert stamps[2].missed == STAMPS_OFF_RISES
    assert (traces[2].lead.lead, traces[2].lead.nearest) == (None, None)
    assert [stamps[k - 1].tick for k in (1, 2, 4)] == [gpiod.leads[k] for k in (1, 2, 4)]
    assert _restarts(caplog) == []


@pytest.mark.parametrize(
    ("late", "stray"), [(0, 600), (500, 1_200)], ids=["on-the-rises", "off-the-rises"]
)
def test_a_stray_stamp_in_the_window_is_not_taken_for_the_lead(
    lib, gpiod, crystals, late, stray
):
    """A stray stamp before frame 3's lead, inside its window. 600 us before a lead the map
    puts at its bracket, the frame's rises align on the stray too, but less closely than on
    the lead; 1.2 ms before a lead stamped 0.5 ms late, they do not align on it at all.
    The first stamp in the window was the lead before."""
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us[3] = late
    gpiod.stray_before_lead_us[3] = stray
    try:
        stamps, traces = _frames(backend, clock, gpiod, 4)
    finally:
        backend.close()
    assert [stamp.tick for stamp in stamps] == [gpiod.leads[k] for k in range(1, 5)]
    assert traces[2].lead.between == 1  # the stray, a stamp between frames


# Ruling R28: stamps carried from the frame before are never this frame's own, and an
# alignment more than IDLE_MIN_US from its bracket is refused. With the line more than a
# frame behind P, each frame's whole stamp set was carried into the next, whose rises
# aligned on it: the fit restarted 0.8 s off and every later B was written a frame early.


def test_a_line_more_than_a_frame_behind_p_drops_every_frame_and_never_restarts(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """Every stamp from frame 4 on lands 210 ms after the line: past its frame's end, all
    of them, so each frame's stamps are carried whole into the next.

    Task 1.15: the B rule is unchanged, and nothing restarts. The journal now says, once,
    that E is extrapolated: the run is EXTRAPOLATION_WARN_FRAMES + 5 frames, so that K + 2
    go unpaired after the first three (15 frames before, when K did not exist)."""
    import logging

    from wl_sync.backend import STAMP_NOT_SEEN

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    count = k + 5
    restarted = _restarted_at(monkeypatch)
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us.update({n: 210_000 for n in range(4, count + 1)})
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, traces = _frames(backend, clock, gpiod, count)
    finally:
        backend.close()
    wrong = _wrong(stamps, gpiod)
    assert wrong == [], f"B written for another edge: (frame, us off) {wrong}"
    assert [stamp.tick for stamp in stamps[3:]] == [None] * (count - 3)
    assert restarted == [] and _restarts(caplog) == []
    # Frame 5 on: only the frame before's stamps, carried, lie up to the end.
    assert {stamps[n - 1].missed for n in range(5, count + 1)} == {STAMP_NOT_SEEN}
    assert all(traces[n - 1].lead.between > 1 for n in range(5, count + 1))
    [(age, frames)] = _extrapolation_warnings(caplog)
    assert frames == k and abs(age - frames) < 0.5, age
    assert len(_extrapolated_to_the_end(caplog)) == 1, "the episode stayed open"


def test_a_drift_of_2100_s_at_100_ppm_drops_every_frame_and_never_restarts(
    lib, gpiod, crystals, caplog, monkeypatch
):
    """The plan's drift by itself: 100 ppm with seed 0, five frames, then 2,100 s with
    none, which leaves the line 210 ms behind P."""
    import logging

    restarted = _restarted_at(monkeypatch)
    clock = crystals(cpu_ppm=0, rp1_ppm=100)
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, 18, pause={5: 2_100.0})
    finally:
        backend.close()
    wrong = _wrong(stamps, gpiod)
    assert wrong == [], f"B written for another edge: (frame, us off) {wrong}"
    assert [stamp.tick for stamp in stamps[:5]] == [gpiod.leads[k] for k in range(1, 6)]
    assert [stamp.tick for stamp in stamps[5:]] == [None] * 13
    assert restarted == [] and _restarts(caplog) == []
    # Task 1.15: the journal says so at frame 6, the first without a pair, because its
    # last pair is already 2,100 s old: far more than EXTRAPOLATION_WARN_FRAMES' time.
    [(age, frames)] = _extrapolation_warnings(caplog)
    assert frames == 1 and 2_100 < age < 2_102, (age, frames)


# Task 1.15 (the user's ruling on final review I1, 2026-11-20): the journal says when the
# RAW->P fit has gone EXTRAPOLATION_WARN_FRAMES frames without a pair, so that E ticks and
# roll boundaries are extrapolated, and says when that ends.
_EXTRAPOLATING = re.compile(
    r"the RAW->P fit's last lead-edge pair is (\d+\.\d) s old, (\d+) frame\(s\) ago: E "
    r"ticks and roll boundaries are extrapolated from it, drifting from P by the fit's "
    r"slope error, until a lead edge is matched again"
)
_PAIRED_AGAIN = re.compile(
    r"a lead-edge pair reached the RAW->P fit again after (\d+) frame\(s\) without one: "
    r"E ticks and roll boundaries had been extrapolated from a pair up to (\d+\.\d) s old"
)
_TO_THE_END = re.compile(
    r"capture stopped (\d+) frame\(s\) after the RAW->P fit's last lead-edge pair: E "
    r"ticks and roll boundaries were extrapolated from a pair (\d+\.\d) s old to the end "
    r"of the run"
)


_NEVER_PAIRED = re.compile(
    r"the RAW->P fit has had no lead-edge pair since capture started (\d+\.\d) s ago, "
    r"(\d+) frame\(s\): E ticks and roll boundaries are extrapolated from the sync "
    r"enable, drifting from P by the fit's slope error, until a lead edge is matched"
)
_FIRST_PAIR = re.compile(
    r"the RAW->P fit took its first lead-edge pair after (\d+) frame\(s\) without one: "
    r"E ticks and roll boundaries had been extrapolated from the sync enable, "
    r"(\d+\.\d) s before it"
)
_NEVER_PAIRED_TO_THE_END = re.compile(
    r"capture stopped (\d+) frame\(s\) after it started, with no lead-edge pair for the "
    r"RAW->P fit: E ticks and roll boundaries were extrapolated from the sync enable, "
    r"(\d+\.\d) s before, to the end of the run"
)


def _warnings_matching(caplog, pattern):
    import logging

    return [pattern.fullmatch(record.getMessage()) for record in caplog.records
            if record.levelno == logging.WARNING and pattern.fullmatch(record.getMessage())]


def _extrapolation_warnings(caplog):
    """(age in s, frames without a pair) of each WARNING that E is extrapolated."""
    return [(float(m.group(1)), int(m.group(2)))
            for m in _warnings_matching(caplog, _EXTRAPOLATING)]


def _paired_again(caplog):
    """(frames without a pair, the last pair's age in s) of each episode's end."""
    return [(int(m.group(1)), float(m.group(2)))
            for m in _warnings_matching(caplog, _PAIRED_AGAIN)]


def _extrapolated_to_the_end(caplog):
    return [(int(m.group(1)), float(m.group(2)))
            for m in _warnings_matching(caplog, _TO_THE_END)]


def test_the_journal_says_once_when_e_is_extrapolated_and_when_a_pair_ends_it(
    lib, gpiod, crystals, caplog
):
    """The PIO blind from frame 4 for K + 2 frames: the fit takes no pair, so E and the
    roll boundaries run on from frame 3's. One WARNING at the K-th frame, with the pair's
    age, however many frames follow, and one when frame K + 6's pair ends it, with the
    episode's length. Nothing at the run's end: no episode is open."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, k + 8, blind=range(4, k + 6))
    finally:
        backend.close()
    [(age, frames)] = _extrapolation_warnings(caplog)
    assert frames == k and abs(age - k) < 0.5, (age, frames)
    [(without, length)] = _paired_again(caplog)
    assert without == k + 2 and abs(length - (k + 3)) < 0.5, (without, length)
    assert _extrapolated_to_the_end(caplog) == []
    episode = [bool(_EXTRAPOLATING.fullmatch(record.getMessage()))
               for record in caplog.records
               if _EXTRAPOLATING.fullmatch(record.getMessage())
               or _PAIRED_AGAIN.fullmatch(record.getMessage())]
    assert episode == [True, False], "the end came before the start"
    assert [stamp.tick for stamp in stamps[k + 5:]] == [
        gpiod.leads[n] for n in range(k + 6, k + 9)
    ]


def test_a_run_that_ends_while_e_is_extrapolated_says_so_at_close(
    lib, gpiod, crystals, caplog
):
    """The episode is open when capture stops: the journal says how long E had been
    extrapolated, so a reader can tell that from an episode whose end was not logged."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            _frames(backend, clock, gpiod, k + 4, blind=range(4, k + 5))
    finally:
        backend.close()
    assert len(_extrapolation_warnings(caplog)) == 1
    [(frames, age)] = _extrapolated_to_the_end(caplog)
    assert frames == k + 1 and k + 1 < age < k + 2.5, (frames, age)


def test_before_the_first_pair_the_journal_names_the_sync_enable_not_a_pair(
    lib, gpiod, crystals, caplog
):
    """Final review of Task 1.15, Minor 4. With the PIO blind from the first frame the
    line runs from the origin, the sync enable, and no pair exists to be "last": the
    WARNING says capture started S s ago, and the first pair's line says it is the first.
    Neither line of an episode after a pair is given."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, k + 4, blind=range(1, k + 2))
    finally:
        backend.close()
    [started] = _warnings_matching(caplog, _NEVER_PAIRED)
    assert int(started.group(2)) == k and abs(float(started.group(1)) - k) < 1.5, started
    [first] = _warnings_matching(caplog, _FIRST_PAIR)
    assert int(first.group(1)) == k + 1, first.group(0)
    assert _extrapolation_warnings(caplog) == [] and _paired_again(caplog) == []
    assert stamps[-1].tick == gpiod.leads[k + 4]


def test_a_run_with_no_pair_at_all_says_so_at_close(lib, gpiod, crystals, caplog):
    """The same, still open when capture stops."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            _frames(backend, clock, gpiod, k + 1, blind=range(1, k + 2))
    finally:
        backend.close()
    assert len(_warnings_matching(caplog, _NEVER_PAIRED)) == 1
    [end] = _warnings_matching(caplog, _NEVER_PAIRED_TO_THE_END)
    assert int(end.group(1)) == k + 1, end.group(0)
    assert _extrapolated_to_the_end(caplog) == []


def test_a_clean_run_ends_with_a_count_of_no_desyncs(lib, gpiod, crystals, caplog):
    """Final review of Task 1.15, Minor 4's polish: with none, the run-end INFO gives the
    count alone, as nothing was resynchronised."""
    import logging

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        _frames(backend, clock, gpiod, 2)
    finally:
        with caplog.at_level(logging.INFO, logger=rp1.log.name):
            backend.close()
    summaries = [record.getMessage() for record in caplog.records
                 if record.getMessage().startswith("barcode stamping: ")]
    assert summaries == ["barcode stamping: 0 desync(s) this run"], summaries


def test_an_r19_restart_ends_the_extrapolation_episode(lib, gpiod, crystals, caplog):
    """At 100 ppm with seed 0, the PIO blind for K + 5 frames carries the line 2.5 ms from P,
    so the first frames it sees again are shifted, and the third restarts the fit (R19).
    The restart is the fit's new pair, so it ends the episode: the line runs from it."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals(cpu_ppm=0, rp1_ppm=100)
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, k + 14, blind=range(6, k + 11))
    finally:
        backend.close()
    assert len(_restarts(caplog)) == 1
    assert len(_extrapolation_warnings(caplog)) == 1
    [(without, _)] = _paired_again(caplog)
    # The blind frames and the shifted ones before the third, whose restart is its pair.
    assert without == k + 5 + rp1.REACQUIRE_FRAMES - 1, without
    assert _extrapolated_to_the_end(caplog) == []
    assert stamps[-1].tick == gpiod.leads[k + 14]


def test_frames_matched_without_moving_the_fit_still_count_as_unpaired(
    lib, gpiod, crystals, caplog
):
    """A wide bracket keeps its frame's B but gives the fit no pair (ClockPair.narrow), so
    the line runs on from the last narrow one: those frames count toward the WARNING."""
    import logging

    k = rp1.EXTRAPOLATION_WARN_FRAMES
    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    try:
        with caplog.at_level(logging.WARNING, logger=rp1.log.name):
            stamps, _ = _frames(backend, clock, gpiod, k + 4, wide=range(4, k + 4))
    finally:
        backend.close()
    assert all(stamp.tick is not None for stamp in stamps), "a wide bracket drops no B"
    [(age, frames)] = _extrapolation_warnings(caplog)
    assert frames == k, frames
    assert [without for without, _ in _paired_again(caplog)] == [k]


def test_a_frame_with_only_carried_stamps_is_not_stamped(lib, gpiod, crystals):
    """Frame 2's stamps land 15 ms late, so its TRAIL's is carried into frame 3, which the
    PIO does not see. That stamp was queued before frame 3 was driven, so it is not frame
    3's: a stamp between, and frame 3's lead edge was not stamped."""
    from wl_sync.backend import STAMP_NOT_SEEN

    clock = crystals()
    backend = _started(lib, lambda tick, word: None)
    gpiod.frame_stamp_offset_us[2] = 15_000
    try:
        stamps, traces = _frames(backend, clock, gpiod, 4, blind={3})
    finally:
        backend.close()
    assert stamps[2].tick is None and stamps[2].missed == STAMP_NOT_SEEN
    assert (stamps[2].between, traces[2].lead.lead) == (1, None)
    assert stamps[3].tick == gpiod.leads[4]


def test_a_frame_before_drained_late_is_not_taken_for_a_lead_a_second_away(
    lib, gpiod, crystals
):
    """The pump is held through frame 3, so frame 3's stamps reach the queue only after its
    match gave up on them, and are taken with frame 4's, which the PIO does not see. Frame
    4's rises (value 1001) align on frame 3's (value 1000) all but one, the minimum: a lead
    a second before frame 4's bracket. More than IDLE_MIN_US away, nearer another frame's
    slot than its own, it is refused, and frame 4 is not counted as shifted."""
    from wl_sync.barcode import IDLE_MIN_US, encode

    clock = crystals()
    on_word, inside, release = _holding_the_pump()
    backend = _started(lib, on_word)
    stamps, traces = [], []
    try:
        for k, value in enumerate((998, 999, 1000, 1001), 1):
            if k == 3:
                lib.push(stream((10, 1)))
                assert inside.wait(1)  # the pump held: nothing is drained
            gpiod.pio_sees_barcode = k != 4
            stamps.append(backend.emit_pulses(17, encode(value)))
            traces.append(backend.last_frame)
            if k == 3:
                release.set()
                assert _until(lambda: lib.stamp_fifo == [])
            clock.sleep(0.8)
        shifted = list(backend._shifted)
    finally:
        release.set()
        backend.close()
    assert stamps[2].tick is None and stamps[3].tick is None
    assert traces[3].lead.lead is None, "an alignment a second away was taken for a lead"
    assert traces[3].lead.nearest > IDLE_MIN_US
    assert shifted == []


# Ruling R25.


@pytest.mark.parametrize("given", [False, True], ids=["none", "calibration"])
def test_falling_back_after_a_calibration_was_applied_is_refused(lib, given):
    """The run loop already holds the scale apply_calibration returned, IDENTITY without
    a calibration: it keeps a rate keeper because there is one, and scales every record by
    it (wl_sync/cli.py:519-549, :583). The backend cannot take it back."""
    backend = Rp1Backend()
    try:
        backend.apply_calibration(_calibration(40_000, 40_000) if given else None)
        with pytest.raises(RuntimeError, match="before a calibration is applied"):
            backend._fall_back_to_split("x")
        assert backend.timebase == ("rp1-pio-si" if given else "rp1-pio")
        assert backend.barcode_delay_us == 0
    finally:
        backend.close()


def test_check_health_reads_a_stopped_word_drain_before_it_takes_its_cause(lib):
    """The pump holds the cause, then sets the stop (`_pump_codes`). check_health reads
    the stop first and takes the cause second, so whatever the pump does between the two,
    no call reports the stop before its cause. Here the pump does both just as the stop
    is read: taken the other way round, the call saw the stop and not the cause."""
    from wl_sync.backend import CaptureDegraded

    backend = _started(lib, lambda tick, word: None)
    read = backend._words_stopped

    def words_stopped():
        if not backend._word_drain_stopped.is_set():
            backend._words.error = OSError("EIO")  # the pump's hold
            backend._word_drain_stopped.set()  # and then its stop
        return read()

    backend._words_stopped = words_stopped
    try:
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
    finally:
        backend.close()
    # A drain that raised: since Task 1.20 a desync stops nothing.
    stopped = {"event-code capture: OSError", "event-code capture: stopped"}
    assert stopped <= raised.value.kinds, raised.value.kinds


def test_check_health_takes_the_word_error_under_the_pumps_lock(lib, monkeypatch):
    """As for the edge pump's (test_check_health_takes_the_edge_loss_under_the_pumps_lock):
    the pump holds an error while check_health takes it, and a take that read the error
    and then cleared it, unlocked, could clear one held in between."""
    from wl_sync.backend import CaptureDegraded
    from wl_sync.rp1.pio_capture import WordPump, WordsDroppedError

    taken = []
    take = WordPump.take_error
    monkeypatch.setattr(
        WordPump, "take_error", lambda pump: taken.append(pump) or take(pump)
    )
    backend = _started(lib, lambda tick, word: None)
    try:
        backend._words.fail(WordsDroppedError("the PIO RX FIFO was full when drained"))
        with pytest.raises(CaptureDegraded, match="FIFO was full"):
            backend.check_health()
        assert taken and set(taken) == {backend._words}
        backend.check_health()  # taken once, and cleared
    finally:
        backend.close()


# --- cli.run on the real backend (final review M11) -------------------------------------
#
# Every one-count behaviour of the run loop is tested through FakeBackend subclasses
# (tests/test_cli.py). These run `cli.run` itself on `Rp1Backend`, over the shim, gpiod
# and two-crystal fakes, so the header the loop writes and the ticks the backend hands it
# are checked together, as the box would produce them.


@pytest.mark.parametrize("rate_ppb", [None, 50_000], ids=["no-rate-file", "a-rate-file"])
def test_run_on_the_rp1_backend_writes_b_at_the_lead_stamp_scaled_once(
    tmp_path, lib, gpiod, crystals, monkeypatch, rate_ppb
):
    """Coverage: passes on 1d9f2bc. RP1's crystal runs 50 ppm fast of the CPU's. With no
    rate file the header says "rp1-pio" and each B is its lead edge's stamp, P, as
    counted; with one, "rp1-pio-si" and B = round(P / r), r = 1 + rate_ppb 1e-9, scaled
    once (spec §2.2). Either way the backend stamps the lead edge itself, so the header's
    barcode delay is 0, and a code word captured mid-run is on the same scale as B."""
    import datetime

    from wl_sync import cli
    from wl_sync.calibration import RATE_FILE_NAME, write_calibration
    from wl_sync.log import BarcodeEmitted, CodeWord, read_log

    utc = datetime.timezone.utc
    clock = crystals(cpu_ppm=0, rp1_ppm=50)
    serial = "0123456789abcdef"
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda timeout_s=5: True)
    monkeypatch.setattr(cli, "probe_board_serial", lambda: serial)
    if rate_ppb is not None:
        calibration = _calibration(rate_ppb=rate_ppb, p_per_raw_ppb=50_000)
        assert calibration.serial == serial
        write_calibration(tmp_path / RATE_FILE_NAME, calibration)
    backend = Rp1Backend()
    word_p = []

    def tick(seconds):
        if len(word_p) == 0 and gpiod.leads:
            word_p.append(clock.p_now())  # a code word on the capture machine, now
            lib.push(stream((word_p[0], 7)))
        clock.sleep(seconds)

    wall = {"n": 0}

    def now_fn():
        wall["n"] += 1
        return datetime.datetime(2026, 11, 19, 9, 0, tzinfo=utc) + datetime.timedelta(
            seconds=wall["n"] - 1
        )

    folder = cli.run(out=tmp_path, backend=backend, now_fn=now_fn, duration_s=5,
                     tick_fn=tick)

    header, records = read_log(sorted(folder.glob("seg-*.log"))[0])
    assert header.timebase == ("rp1-pio" if rate_ppb is None else "rp1-pio-si")
    assert header.barcode_delay_us == 0
    d = 10**9 + (rate_ppb or 0)

    def scaled(p):
        return (2 * p * 10**9 + d) // (2 * d)  # round(p / r), halves up

    barcodes = [r for r in records if isinstance(r, BarcodeEmitted)]
    assert len(barcodes) == 5
    assert [b.tick_us for b in barcodes] == [scaled(gpiod.leads[k]) for k in range(1, 6)]
    words = [r for r in records if isinstance(r, CodeWord)]
    assert [(w.tick_us, w.word) for w in words] == [(scaled(word_p[0]), 7)]


def _run_for_five_frames(tmp_path, backend, monkeypatch, tick=None):
    """cli.run on `backend` for 5 s of a wall clock that steps a second a read."""
    import datetime

    from wl_sync import cli

    utc = datetime.timezone.utc
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda timeout_s=5: True)
    monkeypatch.setattr(cli, "probe_board_serial", lambda: "0123456789abcdef")
    wall = {"n": 0}

    def now_fn():
        wall["n"] += 1
        return datetime.datetime(2026, 11, 20, 9, 0, tzinfo=utc) + datetime.timedelta(
            seconds=wall["n"] - 1
        )

    folder = cli.run(out=tmp_path, backend=backend, now_fn=now_fn, duration_s=5,
                     tick_fn=tick or (lambda seconds: None))
    return sorted(folder.glob("seg-*.log"))


def _journal_errors(caplog):
    import logging

    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("where", ["configure", "start"])
def test_run_records_anyway_when_the_capture_machine_cannot_run(
    tmp_path, lib, gpiod, caplog, monkeypatch, where
):
    """Task 1.16's "record anyway", through cli.run. A capture machine that cannot be
    configured (at construction, before the first header) or enabled (at capture's
    start, in a split process, after it) gives one ERROR at start and one "capture
    degraded" episode, and the run records B and E without W. Every segment's header
    says "split", with no barcode delay: B is read in software."""
    from wl_sync.log import BarcodeEmitted, CodeWord, read_log

    if where == "configure":
        lib.start_result = -16
        reason = "the capture machine did not start (errno 16)"
        start_error = _NO_CAPTURE_ERROR.format(reason)
    else:
        lib.stamp_result = -16
        lib.set_enabled_errno[(0, True)] = 5
        reason = "enabling the state machine failed: errno 5"
        start_error = (
            f"event-code capture could not start ({reason}): this process records no W "
            "records, and B and E go on, on CLOCK_MONOTONIC, until a restart starts the "
            "capture machine"
        )
    backend = Rp1Backend()
    lib.push(stream((10, 7)))  # a strobe no machine captures
    segments = _run_for_five_frames(tmp_path, backend, monkeypatch)
    header, records = read_log(segments[0])
    assert (header.timebase, header.barcode_delay_us) == ("split", None)
    assert len([r for r in records if isinstance(r, BarcodeEmitted)]) == 5
    assert not [r for r in records if isinstance(r, CodeWord)]
    errors = _journal_errors(caplog)
    if where == "start":
        errors = errors[1:]  # the start-up test's, which made the process split
    assert errors == [
        start_error,
        "capture degraded, still recording: " + _NO_CAPTURE_HEALTH.format(reason),
    ]


def test_run_on_a_box_that_refuses_the_sync_enable_records_split(
    tmp_path, lib, gpiod, caplog, monkeypatch
):
    """Task 1.16, C-7, through cli.run: one ERROR, a split process, and W, B and E all
    recorded, W by the capture machine enabled alone."""
    from wl_sync.log import BarcodeEmitted, CodeWord, read_log

    lib.refuse_sync = True
    backend = Rp1Backend()
    pushed = []

    def tick(seconds):
        if not pushed and gpiod.sets:
            pushed.append(True)
            lib.push(stream((10, 7)))

    segments = _run_for_five_frames(tmp_path, backend, monkeypatch, tick)
    header, records = read_log(segments[0])
    assert (header.timebase, header.barcode_delay_us) == ("split", None)
    assert len([r for r in records if isinstance(r, BarcodeEmitted)]) == 5
    assert [r.word for r in records if isinstance(r, CodeWord)] == [7]
    assert _journal_errors(caplog) == [_SPLIT_ERROR.format(
        "the sync enable was refused: the test machine stamped GPIO17's rise only when "
        "enabled alone"
    )]


# --- --split, the bench's forced fallback (Task 1.24; the user's ruling, 2026-11-20) -----
#
# B2 measures the split backend's drift on current code (the plan's B2), so the bench
# asks for the fallback a failed start-up test gives: the capture machine alone, B and E
# on MONO. One ERROR at start, the health check's episode and an ERROR in the run's
# summary say so, so a process recording split by force never passes silently.

_FORCED = "forced by --split, for the bench only"
_FORCED_ERROR = (
    "the stamp machine was not started (forced by --split, for the bench only): this "
    "process records timebase split, with B and E on CLOCK_MONOTONIC, until a restart "
    "without --split"
)
_FORCED_HEALTH = (
    "timebase split forced by --split, for the bench only: B and E are on "
    "CLOCK_MONOTONIC in this process, not on RP1's count"
)
_FORCED_SUMMARY = (
    "this run recorded timebase split, forced by --split, for the bench only: B and E "
    "were on CLOCK_MONOTONIC throughout"
)


def test_split_forced_puts_the_process_where_a_failed_start_up_test_does(
    lib, gpiod, caplog, monkeypatch
):
    """The fallback's own path (`_fall_back_to_split`), with no stamp machine and no test:
    the capture machine alone, enabled the plain way, its pump counting on half the FIFO
    as a failed test leaves it. The test's pulses never reach GPIO17."""
    import logging

    gpiod.pio_sees_barcode = False
    failed = Rp1Backend()
    failed_words = failed._rx_words
    failed.close()
    gpiod.sets.clear()
    caplog.clear()

    shim = _FakeShim()
    monkeypatch.setattr(rp1, "_load_shim", lambda: shim)
    gpiod.shim = shim
    with caplog.at_level(logging.INFO, logger=rp1.log.name):
        backend = Rp1Backend(force_split=True)
    try:
        assert _is_split(backend)
        assert backend.split_forced
        assert backend.stamp_test_failure == _FORCED  # so bench stamps refuses it
        assert backend.capture_failure == ""
        assert "start_stamp" not in shim.log
        assert shim.enables == [] and gpiod.sets == []
        assert backend._rx_words == failed_words == shim.word_depth // 2
        assert _errors(caplog) == [_FORCED_ERROR]
        enables, synced = _capture_enables(shim, backend)
        assert enables == [(0, True)] and not synced
    finally:
        backend.close()
    assert shim.disabled == [0]


def test_split_forced_still_refuses_a_block_that_cannot_be_trusted(lib):
    """The flag skips the stamp machine, not the capture machine's checks (`_configure`):
    an impossible clk_sys still refuses the start, as without it."""
    lib.clk_sys_hz = 0xFFFF_FFFF
    with pytest.raises(rp1.PiolibError):
        Rp1Backend(force_split=True)
    assert lib.log.count("open") == 1 and lib.log[-1] == "close"


def test_split_forced_reaches_the_health_check_on_every_call(lib, gpiod):
    """As a capture machine that did not start is (`_records_without_w`): on every call
    once capture has started, so the run loop holds one episode for the run."""
    from wl_sync.backend import CaptureDegraded

    backend = Rp1Backend(force_split=True)
    try:
        backend.check_health()  # before capture starts, nothing is running on the clock
        backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
        for _ in range(2):
            with pytest.raises(CaptureDegraded) as raised:
                backend.check_health()
            assert raised.value.kinds == {"timebase: split forced"}
            assert str(raised.value) == _FORCED_HEALTH
    finally:
        backend.close()


def test_split_forced_is_in_the_runs_summary_once(lib, gpiod, caplog):
    backend = Rp1Backend(force_split=True)
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    caplog.clear()
    backend.close()
    backend.close()
    assert _errors(caplog) == [_FORCED_SUMMARY]


def test_without_split_nothing_says_forced(lib, gpiod, caplog):
    gpiod.pio_sees_barcode = False
    backend = Rp1Backend()
    backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    backend.check_health()
    backend.close()
    assert not backend.split_forced
    assert not [e for e in _errors(caplog) if "--split" in e]


def test_split_forced_on_a_box_whose_capture_machine_cannot_run_records_anyway(
    lib, gpiod, caplog
):
    """The box records anyway (Task 1.16): the capture fault's ERROR, then the flag's,
    and the health check holds both."""
    from wl_sync.backend import CaptureDegraded

    lib.start_result = -16
    reason = "the capture machine did not start (errno 16)"
    backend = Rp1Backend(force_split=True)
    try:
        assert _is_split(backend) and backend.split_forced
        assert backend.stamp_test_failure == reason  # the fault's, which says more
        assert _errors(caplog) == [_NO_CAPTURE_ERROR.format(reason), _FORCED_ERROR]
        backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
        with pytest.raises(CaptureDegraded) as raised:
            backend.check_health()
        assert raised.value.kinds == {
            "event-code capture: did not start", "timebase: split forced",
        }
    finally:
        backend.close()


def _five_split_frames(tmp_path, monkeypatch, gpiod, **kwargs):
    """cli.run for five frames on a fresh shim fake, one strobe pushed at the first frame;
    the first segment's header and records."""
    from wl_sync.log import read_log

    shim = _FakeShim()
    monkeypatch.setattr(rp1, "_load_shim", lambda: shim)
    gpiod.shim = shim
    gpiod.pio_sees_barcode = kwargs.pop("pio_sees_barcode", True)
    backend = Rp1Backend(**kwargs)
    sets, pushed = len(gpiod.sets), []

    def tick(seconds):
        if not pushed and len(gpiod.sets) > sets:
            pushed.append(True)
            shim.push(stream((10, 7)))

    tmp_path.mkdir(parents=True)
    segments = _run_for_five_frames(tmp_path, backend, monkeypatch, tick)
    return read_log(segments[0])


def test_run_split_by_force_writes_the_header_and_records_a_failed_test_writes(
    tmp_path, lib, gpiod, caplog, monkeypatch
):
    """The point of the flag (the user's ruling, 2026-11-20): B2 measures the fallback the
    box would really record. So the header is the failed test's, field for field but the
    process's own, and so are the records, kind for kind and value for value; ticks stay
    whole numbers. The journal's ERRORs are the flag's three: start, episode, summary."""
    from wl_sync.log import BarcodeEmitted, CodeWord

    failed_header, failed = _five_split_frames(
        tmp_path / "failed" / "rig0", monkeypatch, gpiod, pio_sees_barcode=False)
    caplog.clear()
    forced_header, forced = _five_split_frames(
        tmp_path / "forced" / "rig0", monkeypatch, gpiod, force_split=True)

    volatile = {"process_uuid", "written_at", "boot_id"}
    assert forced_header.timebase == "split" and forced_header.barcode_delay_us is None
    assert (forced_header.model_dump(exclude=volatile)
            == failed_header.model_dump(exclude=volatile))

    def shape(records):
        return [(type(r).__name__, getattr(r, "value", None), getattr(r, "word", None))
                for r in records]

    assert shape(forced) == shape(failed)
    assert len([r for r in forced if isinstance(r, BarcodeEmitted)]) == 5
    assert [r.word for r in forced if isinstance(r, CodeWord)] == [7]
    assert all(type(r.tick_us) is int for r in forced if hasattr(r, "tick_us"))
    assert _journal_errors(caplog) == [
        _FORCED_ERROR,
        "capture degraded, still recording: " + _FORCED_HEALTH,
        _FORCED_SUMMARY,
    ]


def _declare_all(lib):
    """Every shim export bound: the backend's (`_declare`), and the one only the bench
    harness calls, `wlsync_start_meter` (wl_sync/rp1/harness.py, `_declare_meter`)."""
    from wl_sync.rp1 import harness

    rp1._declare(lib)
    harness._declare_meter(lib)


def test_a_version_5_shim_is_refused():
    """Version 5 has no `wlsync_start_meter`, the bench's width meter (Task 1.21 fix round
    6; the user's ruling, 2026-11-20). Version 6 adds it, and a version 5 shim is refused
    with the rebuild, as R42's rule has each bump refuse the one before."""
    shim = types.SimpleNamespace(wlsync_shim_version=lambda: 5)
    with pytest.raises(rp1.PiolibError, match=r"version 5.*needs 6.*build-shim"):
        rp1._check_shim_version(shim)


def test_the_meter_export_is_bound_with_one_argtype_per_c_parameter():
    """The bench harness binds `wlsync_start_meter` itself; its argtypes count its C
    parameters, as the tie test above counts the backend's."""
    from wl_sync.rp1 import harness

    lib = unittest.mock.MagicMock()
    harness._declare_meter(lib)
    c_names = _c_prototype(_C_SOURCE.read_text(), "wlsync_start_meter")
    assert c_names == ["instructions", "length", "wrap_target", "wrap", "entry", "jmp_pin",
                       "clkdiv"]
    assert len(lib.wlsync_start_meter.argtypes) == len(c_names)
    assert "wlsync_start_meter" not in inspect.getsource(rp1)  # never the backend's
