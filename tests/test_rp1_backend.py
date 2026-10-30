"""Rp1Backend, against stand-ins for libwlsync_pio.so and the libgpiod bindings.

The backend loads its library in its constructor rather than at import, so fakes can
take its place on a laptop. The library fake accepts every configuration call and turns
the RX FIFO into a list the test fills. What that exercises is the backend's own glue:
that something drains the FIFO, that what goes wrong reaches check_health(), the order
things start and stop in. What it cannot exercise is silicon -- the PIO program, real
FIFO timing, the chardev -- which is bench day's.
"""

import threading
import time

import pytest

from wl_sync.rp1 import backend as rp1
from wl_sync.rp1 import program
from wl_sync.rp1.backend import Rp1Backend

DATA = 16


def stream(*events):
    entries = []
    for tick, word in events:
        entries.append(word)
        entries.append((~tick) & 0xFFFFFFFF)
    return entries


class _FakeShim:
    """libwlsync_pio.so: every call succeeds, the RX FIFO is `fifo`, and `log` is the
    order of events.

    Like piolib, its encoders answer correctly only once a PIO block is open -- piolib's
    read the CURRENT block -- so a backend that checks its encodings too early is refused
    here exactly as it would be on the box.
    """

    def __init__(self) -> None:
        self.fifo: list[int] = []
        self.full = False
        self.drains = 0
        self.log: list[str] = []
        self.opened = False
        self.clk_sys_hz = 200_000_000
        self.start_result = 0
        self.started_with: dict | None = None
        self._lock = threading.Lock()

    def wlsync_pio_open(self, index):
        self.opened = True
        self.log.append("open")
        return 0

    def wlsync_pio_close(self):
        self.log.append("close")

    def _encoded(self, value):
        self.log.append("encode")
        return value if self.opened else 0xFFFF

    def wlsync_encode_jmp(self, addr):
        return self._encoded(program.encode_jmp(addr))

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
                             data_base, data_count, strobe_pin, clkdiv, seed):
        self.started_with = {
            "length": length, "wrap_target": wrap_target, "wrap": wrap,
            "data_base": data_base, "data_count": data_count, "strobe_pin": strobe_pin,
            "clkdiv": clkdiv, "seed": seed,
        }
        return self.start_result

    def wlsync_sm_set_enabled(self, sm, enabled):
        self.log.append(f"enabled={bool(enabled)}")
        return 0

    def push(self, entries) -> None:
        with self._lock:
            self.fifo.extend(entries)

    def wlsync_sm_rx_full(self, sm):
        self.drains += 1  # the first call of every drain
        return 1 if self.full else 0

    def wlsync_sm_rx_level(self, sm):
        with self._lock:
            return len(self.fifo)

    def wlsync_sm_get(self, sm):
        with self._lock:
            return self.fifo.pop(0)


@pytest.fixture
def lib(monkeypatch, gpiod):
    fake = _FakeShim()
    monkeypatch.setattr(rp1, "_load_shim", lambda: fake)
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


def test_a_desynced_stream_is_reported_and_then_reported_as_stopped(lib):
    """The pump stops on a desync. check_health() says why once, and from then on says
    that the thread is not running, so the journal shows both what happened and that
    the event codes are not coming back on their own."""
    backend = _started(lib, lambda tick, word: None)
    try:
        lib.push(stream((10, 1))[1:] + [0])  # one word lost: the pairing has slipped
        first = _next_complaint(backend)
        assert first is not None and "pairing has slipped" in first, first
        second = _next_complaint(backend)
        assert second is not None and "event-code capture thread stopped" in second, second
    finally:
        backend.close()


def test_close_delivers_what_the_fifo_held_before_the_state_machine_stops(lib):
    """The last words in the FIFO are the end of the session's last trial. close() stops
    the pump with one final drain and only then disables the state machine, so nothing
    the PIO captured is left behind in it."""
    def on_word(tick, word):
        lib.log.append(f"word={word}")

    backend = _started(lib, on_word)
    lib.push(stream((10, 7)))
    backend.close()

    assert "word=7" in lib.log
    assert lib.log.index("word=7") < lib.log.index("enabled=False")


class _FakeTime:
    """A monotonic clock that advances 20 us per read and by exactly what is slept."""

    CLOCK_MONOTONIC = 1

    def __init__(self) -> None:
        self.ns = 10**12

    def clock_gettime_ns(self, clock):
        self.ns += 20_000
        return self.ns

    def sleep(self, seconds):
        self.ns += int(seconds * 1e9)


@pytest.fixture
def gpiod(monkeypatch):
    """A stand-in for the libgpiod v2 bindings that records every level set."""
    import sys
    import types

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

    class _Request:
        def set_value(self, pin, value):
            module.sets.append((module.clock.ns, value))

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
    module.edge_failure = None
    module.edges = []
    module.requested = []
    monkeypatch.setitem(sys.modules, "gpiod", module)
    monkeypatch.setitem(sys.modules, "gpiod.line", line)
    return module


def test_every_barcode_frame_decodes_not_just_the_first(lib, gpiod):
    """A frame ends on TRAIL HIGH, and emit_pulses set each level at its pulse's START
    and returned, so the pin stayed HIGH between frames. The decoder finds a frame by
    a rising edge after 400 ms of LOW, which the stuck-high pin never produced again:
    on the box, only the first barcode of a run would have decoded. The codec's own
    tests could not see it, because their pulse model ends every frame LOW."""
    from wl_sync.barcode import decode_edges, encode

    import wl_sync.rp1.backend as rp1_module

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
    again, so a desync after it went unreported for the rest of the day."""
    backend = _started(lib, lambda tick, word: None)
    try:
        backend.start_edge_capture([20, 21], lambda gpio, level, tick: None)
        gpiod.edge_failure = OSError("EIO")
        assert _until(lambda: not backend._edge_thread.is_alive())
        lib.push(stream((10, 1))[1:] + [0])  # and then the pairing slips
        assert _until(lambda: not backend._word_thread.is_alive())

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


def test_a_fifo_found_holding_eight_words_counts_as_full(lib):
    """Fullness was sampled once, before reading, so a FIFO that filled between that
    sample and the read went unwitnessed. Eight words waiting IS a full joined FIFO,
    and any push into it was dropped, so the level is a witness as well."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        lib.push(stream((10, 1), (20, 2), (30, 3), (40, 4)))
        assert _until(lambda: len(seen) == 4)
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
    assert built == [((), {"chip_path": "/dev/gpiochip4"})]



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
    lib.clk_sys_hz = 0xFFFF_FFFF  # what RP1 answers for any clock but clk_sys
    backend = Rp1Backend()
    try:
        with pytest.raises(rp1.PiolibError, match="clk_sys"):
            backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    finally:
        backend.close()


def test_a_capture_that_cannot_start_raises_rather_than_recording_nothing(lib):
    lib.start_result = -16  # -EBUSY: no free state machine
    backend = Rp1Backend()
    try:
        with pytest.raises(rp1.PiolibError, match="errno 16"):
            backend.start_strobed_capture(0, DATA, DATA, lambda tick, word: None)
    finally:
        backend.close()


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
            CLOCK_MONOTONIC=1,
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
            CLOCK_MONOTONIC=1,
            clock_gettime_ns=lambda _clock: now.ns,
            sleep=time.sleep,
            monotonic=time.monotonic,
        ),
    )
    return now


def test_the_word_pump_never_claims_a_word_it_has_not_delivered(lib, gpiod, monkeypatch):
    """How far the word pump has delivered, which the run loop's roll barrier waits on.

    The pump reads the clock just before each poll, and moves its mark there (less the few
    microseconds a strobe's two words take to reach the FIFO) only once that poll has
    delivered every word it found. A word still being delivered holds the mark back, and
    so does half a pair: the data word of a strobe whose counter is not in the FIFO yet."""
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
    """A pump whose thread has exited has delivered all it ever will, so a roll has
    nothing to wait for. It used to keep its last mark, and every later open and close
    then waited out ROLL_BARRIER_TIMEOUT_S and logged a WARNING about records it would
    "deliver late". check_health() is what reports the stop."""
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
            assert barrier() < cli.ROLL_BARRIER_TIMEOUT_S / 4

            lib.push(stream((10, 1))[1:] + [0])  # the pairing slips: the word pump stops
            assert _until(lambda: not backend._word_thread.is_alive())
            assert backend.delivered_through() == {}
            assert barrier() < cli.ROLL_BARRIER_TIMEOUT_S / 4
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    finally:
        backend.close()


def test_the_edge_buffer_holds_a_whole_read(lib, gpiod, monkeypatch):
    """The edge pump takes a read shorter than EDGE_READ_MAX for an empty kernel buffer,
    and libgpiod caps each read at the buffer it allocates from event_buffer_size. A
    smaller buffer would make every full read look short, and move the mark past edges
    still waiting in the kernel."""
    from wl_sync.rp1.gpio import EDGE_READ_MAX

    backend = Rp1Backend()
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


def _edge_now_less(backend, seqno, us):
    """An edge the kernel stamped `us` before now, on the backend's own timebase. All on
    GPIO20, so its line's number is the request's, rising and falling in turn."""
    import types

    kinds = types.SimpleNamespace(RISING_EDGE="rising", FALLING_EDGE="falling")
    return types.SimpleNamespace(
        global_seqno=seqno,
        line_seqno=seqno,
        line_offset=20,
        event_type=kinds.RISING_EDGE if seqno % 2 else kinds.FALLING_EDGE,
        Type=kinds,
        timestamp_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC) - us * 1_000,
    )


def test_the_edge_pump_witnesses_how_late_edges_reach_it(lib, gpiod):
    """The roll barrier assumes the kernel buffers an edge within EDGE_STAMP_LAG_US of
    stamping it. Each read bounds that from above -- the clock when the read returned,
    less the edge's stamp -- and the backend reports the largest bound, and how many
    edges went over, for the health report to log."""
    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US

    seen = []
    backend = Rp1Backend()
    assert backend.stamp_lag() is None
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
    backend = Rp1Backend()
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
    backend = Rp1Backend()
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
    time.sleep(0.01)  # so an edge stamped 1 ms ago is after the origin
    frozen = backend.now_us()
    backend.now_us = lambda: frozen
    backend.start_edge_capture([20, 26], lambda gpio, level, tick: seen.append(gpio))
    gpiod.edges.extend([edge(1, 20, 1, True), edge(3, 20, 2, False)])
    assert _until(lambda: len(seen) == 2)
    return edge, seen


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
