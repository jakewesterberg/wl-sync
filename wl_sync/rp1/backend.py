"""RP1 PIO backend. Hardware-gated: constructing it needs libwlsync_pio.so.

BOUND THROUGH A SHIM, pio/wlsync_pio.c, NOT TO PIOLIB DIRECTLY. piolib's API is a clone
of the Pico SDK's and nearly all of it is `static inline` in piolib.h: libpio.so exports
three of the functions this backend needs, and ctypes can only call what a library
exports. That was found on the CM5 on 2026-10-18, the first time this ran on silicon;
the binding it replaces would have failed on its first missing symbol. The shim exports
scalar-argument functions and does the state machine's configuration in C. What the
design keeps:

  * `pio_encode_*` exists, so THE PROGRAM NEEDS NO ASSEMBLER. It is built as
    instruction words in wl_sync.rp1.program and handed over as a plain
    `uint16_t *`. No `pioasm`, and the program is a value laptop tests can check.
  * Those same encoders let this module VERIFY its own Python encodings at startup
    (`_assert_encoders_agree`). If RP1's PIO ever diverges from RP2040 in any
    instruction this program uses, the box refuses to start instead of recording
    wrong timestamps -- the failure that would otherwise be found months later in a
    session nobody can align. All seven agreed on the CM5, 2026-10-18.

EDGE CAPTURE AND BARCODE OUTPUT ARE NOT PIO, and are bound to libgpiod v2
instead. Individual-pin edge events are not a PIO facility -- piolib's `gpio_*`
calls configure pins rather than deliver transitions -- and libgpiod is the
better tool anyway: the kernel stamps every edge from CLOCK_MONOTONIC at
interrupt time, on the same clock `now_us()` counts from, so an edge carries no
trace of when this process happened to be scheduled.

EVERYTHING HERE FAILS BY GOING QUIET. A pump thread that dies, a kernel event
buffer that overflows, a PIO FIFO that drops words: none of them interrupt the
emit loop, so a day can be recorded with half the rig's signals missing and a
clean trailer on the end saying nothing was wrong. `check_health()` is what makes
that loud, and THE RUN LOOP MUST CALL IT EVERY SECOND.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
import time
from collections.abc import Callable, Sequence

from wl_sync.backend import CaptureDegraded
from wl_sync.pins import GPIO_MAP
from wl_sync.rp1 import program
from wl_sync.rp1.gpio import EdgePump, pulse_deadlines
from wl_sync.rp1.pio_capture import WordPump

_TICK_HZ = 1_000_000  # the program's idle loop is 2 cycles, clocked at 2 MHz
_CYCLES_PER_TICK = 2

# How long before a deadline to stop sleeping and spin. time.sleep overshoots by
# roughly a millisecond, which is a fifth of a barcode bit slot.
_SPIN_NS = 2_000_000

# How often the word pump drains the RX FIFO. The joined FIFO is 8 words, i.e. FOUR
# EVENTS, so this is set against bursts of strobed codes rather than their average
# rate. A BENCH-DAY NUMBER, not a derived one, and there is a known way to miss it:
# emit_pulses spins holding the GIL for up to 2 ms per pulse, and Python's default
# switch interval is 5 ms, so a barcode frame can delay this thread past the FIFO's
# headroom. A WordsDroppedError that coincides with frames is that.
_WORD_POLL_S = 0.001


log = logging.getLogger(__name__)

# The bank must reach every pin this box uses: GPIO 0-27, from wl_sync.pins.
_LINES_NEEDED = max(GPIO_MAP.values()) + 1


def _check_chip(chip_path: str) -> None:
    """Refuse a GPIO chip that cannot be RP1's bank, BEFORE anything is recorded.

    The chip used to be opened first inside run(), after the segment file existed, so
    a wrong --gpiochip crash-looped under Restart=always and left a header-only segment
    behind on every restart. Checked here, it fails before run() creates anything.

    A missing chip, or one too small for GPIO 0-27, is refused. The LABEL is only
    warned about: RP1's is believed to read "pinctrl-rp1", which is UNVERIFIED until
    bench day, and refusing on an unverified string could stop a correctly wired box.
    """
    import gpiod

    hint = "confirm RP1's bank with gpioinfo and pass it with --gpiochip"
    try:
        chip = gpiod.Chip(chip_path)
    except OSError as error:
        raise RuntimeError(f"cannot open {chip_path} ({error}); {hint}") from error
    with chip:
        info = chip.get_info()
    if info.num_lines < _LINES_NEEDED:
        raise RuntimeError(
            f"{chip_path} ({info.label}) has {info.num_lines} lines, and this box "
            f"captures GPIO 0-{_LINES_NEEDED - 1}, so it is not RP1's bank; {hint}"
        )
    if "rp1" not in info.label.lower():
        log.warning("%s is labelled %r, which does not look like RP1's bank; %s",
                    chip_path, info.label, hint)


class PiolibError(RuntimeError):
    """A piolib call failed, or the library disagreed with our encodings."""


_RX_FIFO_WORDS = 8  # joined RX: both 4-deep FIFOs as one
_CLK_SYS_SANE_HZ = (10_000_000, 1_000_000_000)


def _load_shim() -> ctypes.CDLL:
    """libwlsync_pio.so: pio/wlsync_pio.c, with piolib compiled into it.

    NOT libpiolib, which does not exist under that name -- piolib's CMake target is
    `pio`, so it builds libpio.so -- and not libpio either, which exports three of the
    functions this backend needs, because the rest are `static inline`. The shim
    compiles them into real symbols. pio/build-shim.sh builds it.
    """
    name = ctypes.util.find_library("wlsync_pio") or "libwlsync_pio.so"
    try:
        lib = ctypes.CDLL(name)
    except OSError as error:
        raise PiolibError(
            f"libwlsync_pio not found ({error}). This backend is hardware-gated: build "
            "it on the box with pio/build-shim.sh and install it (deploy/README.md)."
        ) from error
    _declare(lib)
    return lib


def _declare(lib: ctypes.CDLL) -> None:
    """Argument and return types. Scalars only: no struct crosses this boundary."""
    lib.wlsync_pio_open.argtypes = [ctypes.c_uint]
    lib.wlsync_pio_open.restype = ctypes.c_int
    lib.wlsync_pio_close.restype = None
    for encoder in ("wlsync_encode_jmp", "wlsync_encode_jmp_pin", "wlsync_encode_jmp_x_dec",
                    "wlsync_encode_in_pins"):
        getattr(lib, encoder).argtypes = [ctypes.c_uint]
        getattr(lib, encoder).restype = ctypes.c_uint
    for encoder in ("wlsync_encode_push_noblock", "wlsync_encode_mov_isr_x",
                    "wlsync_encode_mov_x_not_null"):
        getattr(lib, encoder).argtypes = []
        getattr(lib, encoder).restype = ctypes.c_uint
    lib.wlsync_clk_sys_hz.restype = ctypes.c_uint32
    lib.wlsync_start_capture.argtypes = [
        ctypes.POINTER(ctypes.c_uint16), ctypes.c_uint,   # instructions, length
        ctypes.c_uint, ctypes.c_uint,                     # wrap_target, wrap
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,      # data_base, data_count, strobe
        ctypes.c_float, ctypes.c_uint,                    # clkdiv, seed instruction
    ]
    lib.wlsync_start_capture.restype = ctypes.c_int
    lib.wlsync_sm_set_enabled.argtypes = [ctypes.c_uint, ctypes.c_int]
    lib.wlsync_sm_set_enabled.restype = ctypes.c_int
    lib.wlsync_sm_rx_level.argtypes = [ctypes.c_uint]
    lib.wlsync_sm_rx_level.restype = ctypes.c_uint
    lib.wlsync_sm_rx_full.argtypes = [ctypes.c_uint]
    lib.wlsync_sm_rx_full.restype = ctypes.c_int
    lib.wlsync_sm_get.argtypes = [ctypes.c_uint]
    lib.wlsync_sm_get.restype = ctypes.c_uint32


def _check(result: int, what: str) -> int:
    """The shim returns a negative errno on failure, where piolib would have exited."""
    if result < 0:
        raise PiolibError(f"{what} failed: errno {-result}")
    return result


def _assert_encoders_agree(lib: ctypes.CDLL) -> None:
    """Every instruction this program uses, checked against piolib's own encoder.

    THE POINT IS TO FAIL AT STARTUP RATHER THAN IN THE DATA. wl_sync.rp1.program
    encodes from the RP2040 instruction set so the program can be tested on a laptop;
    that is only sound while RP1 agrees. A divergence here means wrong timestamps in
    every session until someone notices, so it must stop the box instead.

    MUST RUN AFTER THE BLOCK IS OPEN: piolib's encoders read the current block.
    Confirmed on silicon 2026-10-18: all seven agree.
    """
    checks = (
        ("jmp", program.encode_jmp(5), lib.wlsync_encode_jmp(5)),
        ("jmp pin", program.encode_jmp_pin(5), lib.wlsync_encode_jmp_pin(5)),
        ("jmp x--", program.encode_jmp_x_dec(1), lib.wlsync_encode_jmp_x_dec(1)),
        ("in pins", program.encode_in_pins(16), lib.wlsync_encode_in_pins(16)),
        ("push noblock", program.encode_push_noblock(), lib.wlsync_encode_push_noblock()),
        ("mov isr,x", program.encode_mov_isr_x(), lib.wlsync_encode_mov_isr_x()),
        ("mov x,~null", program.encode_mov_x_not_null(), lib.wlsync_encode_mov_x_not_null()),
    )
    wrong = [
        f"{name}: ours {ours:#06x} != piolib {theirs:#06x}"
        for name, ours, theirs in checks
        if ours != theirs
    ]
    if wrong:
        raise PiolibError(
            "PIO instruction encodings disagree with piolib, so this program would "
            "not do what its tests say:\n  " + "\n  ".join(wrong)
        )


class Rp1Backend:
    """`SyncBackend` on RP1 PIO. One state machine for strobed capture."""

    def __init__(self, pio_index: int = 0, chip_path: str = "/dev/gpiochip0") -> None:
        # Which chardev carries RP1's bank has moved between releases (gpiochip4 on a
        # Pi 4), so it is a parameter. Confirmed by label on the CM5 2026-10-18:
        # gpiochip0 [pinctrl-rp1], 54 lines.
        _check_chip(chip_path)
        self._lib = _load_shim()
        _check(self._lib.wlsync_pio_open(pio_index), f"opening PIO block {pio_index}")
        # After the open, never before: piolib's encoders read the current block.
        _assert_encoders_agree(self._lib)
        self._sm: int | None = None
        self._chip_path = chip_path
        self._edges = None
        self._out = None
        self._edge_stop: threading.Event | None = None
        self._edge_thread: threading.Thread | None = None
        self._pump: EdgePump | None = None
        self._words: WordPump | None = None
        self._word_stop: threading.Event | None = None
        self._word_thread: threading.Thread | None = None
        # THE SHARED ORIGIN. The PIO counter starts at 0xFFFFFFFF when the state
        # machine is enabled, so its ticks are microseconds since that instant. E and
        # B ticks must share it or the three paths do not align. Captured as close to
        # pio_sm_set_enabled as this can be; the residual offset is one syscall, and
        # BENCH DAY MUST MEASURE IT rather than assume it is negligible -- it is spent
        # directly out of the +/-100 us budget.
        self._origin_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)

    def now_us(self) -> int:
        elapsed = time.clock_gettime_ns(time.CLOCK_MONOTONIC) - self._origin_ns
        return (elapsed // 1000) & 0xFFFF_FFFF

    def start_strobed_capture(
        self,
        data_base: int,
        data_count: int,
        strobe_pin: int,
        on_word: Callable[[int, int], None],
    ) -> None:
        lib = self._lib
        if strobe_pin != data_base + data_count:
            raise ValueError(
                f"the strobe must sit immediately above the capture window: "
                f"data {data_base}..{data_base + data_count - 1}, strobe {strobe_pin}"
            )

        # clk_sys BY NAME, inside the shim. This read clock_get_hz(0), which is
        # clk_gpout0, and RP1 answers every index but clk_sys with PIO_ORIGIN_ANY --
        # 4294967295 -- so the divider would have been ~2,147 rather than 100.
        system_hz = lib.wlsync_clk_sys_hz()
        low, high = _CLK_SYS_SANE_HZ
        if not low <= system_hz <= high:
            raise PiolibError(
                f"RP1 reported clk_sys as {system_hz} Hz, which cannot be right; "
                "every PIO timestamp is scaled by it"
            )

        # Load, configure and seed in C, so no struct crosses ctypes. Left DISABLED.
        words = (ctypes.c_uint16 * len(program.STROBE_CAPTURE))(*program.STROBE_CAPTURE)
        self._sm = _check(
            lib.wlsync_start_capture(
                words, len(program.STROBE_CAPTURE),
                program.WRAP_TARGET, program.WRAP,
                data_base, data_count, strobe_pin,
                system_hz / (_TICK_HZ * _CYCLES_PER_TICK),
                program.encode_mov_x_not_null(),
            ),
            "starting strobed capture",
        )
        self._origin_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        _check(lib.wlsync_sm_set_enabled(self._sm, 1), "enabling the state machine")

        # NOTHING DRAINED THE FIFO until 2026-10-18. drain() existed and nothing called
        # it, so the first burst of more than four codes filled the FIFO and `push
        # noblock` discarded every later word without a sound. The pump thread is what
        # makes the strobed path a capture path at all.
        self._words = WordPump(on_word, data_count)
        self._word_stop = threading.Event()
        self._word_thread = threading.Thread(
            target=self._pump_words, name="wl-sync-words", daemon=True
        )
        self._word_thread.start()

    def drain(self) -> bool:
        """Move whatever the FIFO holds through the word pump. Returns False when the
        pump should stop. Called from ONE thread, the pump's own.

        Reads an even number of words only. The FIFO can be caught mid-pair, and a
        batch cut between a data word and its counter would make `pair_fifo` raise on
        a stream that is in fact intact -- so the odd tail is left for the next drain.

        Fullness is checked BEFORE draining. `push noblock` drops silently, `pair_fifo`
        catches a single lost word but not a lost pair, and Task 5b Step 3 requires zero
        dropped words -- so a full FIFO is the only honest witness. It is HELD by the
        pump, not raised: see `WordPump` for why capture continues past it.
        """
        lib = self._lib
        if self._sm is None or self._words is None:
            raise PiolibError("start_strobed_capture has not run")
        full = bool(lib.wlsync_sm_rx_full(self._sm))
        level = lib.wlsync_sm_rx_level(self._sm)
        # The level is a witness too: a FIFO that filled between the flag's sample and
        # this read holds every word it can, and any push into it was dropped.
        full = full or level >= _RX_FIFO_WORDS
        entries = [lib.wlsync_sm_get(self._sm) for _ in range(level - level % 2)]
        return self._words.handle(entries, fifo_was_full=full)

    def _pump_words(self) -> None:
        """Drain the RX FIFO until told to stop. Errors are HELD, never raised.

        One last drain after the stop, because close() stops this thread before it
        disables the state machine: whatever the FIFO held at that moment is the end of
        the session's last trial.
        """
        try:
            while not self._word_stop.is_set():
                if not self.drain():
                    return
                time.sleep(_WORD_POLL_S)
            self.drain()
        except BaseException as error:  # noqa: BLE001 - surfaced by check_health
            self._words.error = error

    def start_edge_capture(
        self, pins: Sequence[int], on_edge: Callable[[int, int, int], None]
    ) -> None:
        """Capture individual-pin transitions via libgpiod line events.

        NOT PIO. Edge capture is not a PIO facility -- piolib's `gpio_*` calls
        configure pins rather than deliver transitions -- and it does not need to be:
        the kernel stamps each event from CLOCK_MONOTONIC AT INTERRUPT TIME, which is
        better than anything this process could do for itself. `now_us()` counts from
        the same clock, so the edges land on the shared timebase with no correction
        for thread wake-up latency.

        `on_edge` is called from this thread, so the sink behind it must be a
        `QueuedSink` -- see wl_sync.service. That is what makes a capture thread's
        write independent of how slow the disk is.
        """
        import gpiod
        from gpiod.line import Clock, Direction, Edge

        settings = gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.BOTH,
            event_clock=Clock.MONOTONIC,
        )
        self._edges = gpiod.request_lines(
            self._chip_path,
            config={tuple(pins): settings},
            consumer="wl-sync",
            # Generous, because the kernel's buffer overflows SILENTLY and the only
            # witness is the sequence-number gap EdgePump records and check_health
            # reports.
            event_buffer_size=1024,
        )
        self._pump = EdgePump(on_edge, self._origin_ns)
        self._edge_stop = threading.Event()
        self._edge_thread = threading.Thread(
            target=self._pump_edges, name="wl-sync-edges", daemon=True
        )
        self._edge_thread.start()

    def _pump_edges(self) -> None:
        """Drain libgpiod into the pump. Errors are HELD, never raised out of here.

        A background thread that dies on an exception takes the rig's edge signals with
        it: the segment keeps growing from the other two paths and nothing says that
        rewards and licks stopped being recorded. `check_health()` is what makes that
        loud, and the run loop is expected to call it every second.
        """
        while not self._edge_stop.is_set():
            try:
                if not self._edges.wait_edge_events(timeout=0.2):
                    continue
                if not self._pump.handle(self._edges.read_edge_events()):
                    return
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self._pump.error = error
                return

    def check_health(self) -> None:
        """Raise if anything this backend runs in the background has failed.

        THE RUN LOOP MUST CALL THIS, once per iteration. Every capture path here is a
        thread or a hardware FIFO, and each one's failure mode is to go quiet rather
        than to complain: a dead pump thread, a kernel buffer that overflowed, a PIO
        FIFO that dropped words. None of them interrupt the emit loop on their own, so
        a day can otherwise be recorded with half its signals missing and a clean
        trailer on the end saying nothing was wrong.
        """
        # EVERY PATH, EVERY CALL. Raising at the first problem meant a dead edge thread
        # answered every later call, and the event codes behind it were never looked at
        # again. A held error is reported once and cleared; a dead thread stays dead and
        # is reported on every call, and the run loop collapses that into one episode.
        problems: list[tuple[str, str]] = []
        held = []
        for path, pump in (("edge capture", self._pump), ("event-code capture", self._words)):
            if pump is not None and pump.error is not None:
                error, pump.error = pump.error, None
                held.append(error)
                problems.append((f"{path}: {type(error).__name__}", f"{path}: {error}"))
        for path, thread, stop in (
            ("edge capture", self._edge_thread, self._edge_stop),
            ("event-code capture", self._word_thread, self._word_stop),
        ):
            if thread is not None and not thread.is_alive() and not stop.is_set():
                problems.append(
                    (f"{path}: stopped", f"the {path} thread stopped unexpectedly")
                )
        if problems:
            raise CaptureDegraded(problems) from (held[0] if held else None)

    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> None:
        """Drive the barcode frame, software-timed against ABSOLUTE deadlines.

        Software timing is fine here and precision is deliberately spent elsewhere: 5 ms
        slots sampled at 30 kHz give 150 samples per bit, and every receiver times its
        own edges. What is NOT fine is letting error accumulate. The decoder samples
        each bit at its slot centre measured from the lead edge, so it assumes uniform
        slots; sleeping for each duration in turn makes every overshoot permanent and
        the tail of a 36-pulse frame walks out of its slots. Deadlines are computed once
        from a single start, so a late pulse is corrected by the next.

        Sleeps to just short of each deadline and spins the remainder: `time.sleep`
        alone overshoots by around a millisecond, which is a fifth of a bit slot.
        """
        import gpiod
        from gpiod.line import Direction, Value

        if self._out is None:
            self._out = gpiod.request_lines(
                self._chip_path,
                config={pin: gpiod.LineSettings(direction=Direction.OUTPUT)},
                consumer="wl-sync",
            )
        start = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        # BACK TO LOW WHEN THE LAST PULSE ENDS. Every level is set at its pulse's START,
        # and a frame ends on TRAIL HIGH, so without this the pin stayed high between
        # frames. A frame is found by a rising edge after IDLE_MIN_US of LOW, so only
        # the first barcode of a run decoded.
        end_ns = start + sum(duration for _, duration in pulses) * 1_000
        for level, deadline_ns in [*pulse_deadlines(pulses, start), (0, end_ns)]:
            remaining = deadline_ns - time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            if remaining > _SPIN_NS:
                time.sleep((remaining - _SPIN_NS) / 1e9)
            while time.clock_gettime_ns(time.CLOCK_MONOTONIC) < deadline_ns:
                pass
            self._out.set_value(pin, Value.ACTIVE if level else Value.INACTIVE)

    def close(self) -> None:
        """Stop everything this backend started, in the order that loses least.

        The capture threads first, because they call into the sink and the sink is
        about to be closed by the caller; then the state machine; then the descriptors.
        The word pump drains once more on its way out, so it stops before the state
        machine does.
        """
        for stop in (self._edge_stop, self._word_stop):
            if stop is not None:
                stop.set()
        for thread in (self._edge_thread, self._word_thread):
            if thread is not None:
                thread.join(timeout=1.0)
        for request in (self._edges, self._out):
            if request is not None:
                request.release()
        if self._sm is not None:
            self._lib.wlsync_sm_set_enabled(self._sm, 0)
        self._lib.wlsync_pio_close()
