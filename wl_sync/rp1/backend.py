"""RP1 PIO backend. Hardware-gated: importing this needs libpiolib present.

Bound against piolib's real published API (raspberrypi/utils, `piolib.h`) rather
than a guessed one. What that buys, beyond correctness:

  * `pio_encode_*` exists, so THE PROGRAM NEEDS NO ASSEMBLER. It is built as
    instruction words in wl_sync.rp1.program and handed over as a plain
    `uint16_t *` inside a `pio_program_t`. No `pioasm`, no build step, and the
    program becomes a value that laptop tests can check.
  * Those same encoders let this module VERIFY its own Python encodings at load
    time (`_assert_encoders_agree`). If RP1's PIO ever diverges from RP2040 in
    any instruction this program uses, the box refuses to start instead of
    recording wrong timestamps -- which is the failure that would otherwise be
    found months later in a session nobody can align.

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


class _PioSmConfig(ctypes.Structure):
    """`typedef struct { uint32_t content[4]; } pio_sm_config;`"""

    _fields_ = [("content", ctypes.c_uint32 * 4)]


class _PioProgram(ctypes.Structure):
    """`struct pio_program` -- instructions, length, origin, pio_version."""

    _fields_ = [
        ("instructions", ctypes.POINTER(ctypes.c_uint16)),
        ("length", ctypes.c_uint8),
        ("origin", ctypes.c_int8),
        ("pio_version", ctypes.c_uint8),
    ]


_PIO_ORIGIN_ANY = -1
_PIO_FIFO_JOIN_RX = 2
_RX_FIFO_WORDS = 8  # joined RX: both 4-deep FIFOs as one


def _load_piolib() -> ctypes.CDLL:
    name = ctypes.util.find_library("piolib") or "libpiolib.so"
    try:
        lib = ctypes.CDLL(name)
    except OSError as error:
        raise PiolibError(
            f"libpiolib not found ({error}). This backend is hardware-gated; "
            "install the `pi` extra on a Pi 5 running the vendor kernel."
        ) from error
    _declare(lib)
    return lib


def _declare(lib: ctypes.CDLL) -> None:
    """Argument and return types, so ctypes does not silently truncate pointers.

    `PIO` is an opaque pointer and errors come back INSIDE it -- piolib's
    `PIO_IS_ERR` is `(uintptr_t)x >= (uintptr_t)-200`, i.e. a small negative value
    cast to a pointer. Declaring the restype as c_void_p rather than the default
    int is what makes that check possible on a 64-bit host at all.
    """
    lib.pio_init.restype = ctypes.c_int
    lib.pio_open.argtypes = [ctypes.c_uint]
    lib.pio_open.restype = ctypes.c_void_p
    lib.pio_close.argtypes = [ctypes.c_void_p]

    lib.pio_add_program.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PioProgram)]
    lib.pio_add_program.restype = ctypes.c_uint
    lib.pio_claim_unused_sm.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    lib.pio_claim_unused_sm.restype = ctypes.c_int

    lib.pio_get_default_sm_config.restype = _PioSmConfig
    for setter, extra in (
        ("sm_config_set_in_pins", [ctypes.c_uint]),
        ("sm_config_set_jmp_pin", [ctypes.c_uint]),
        ("sm_config_set_fifo_join", [ctypes.c_int]),
    ):
        getattr(lib, setter).argtypes = [ctypes.POINTER(_PioSmConfig), *extra]
    lib.sm_config_set_in_shift.argtypes = [
        ctypes.POINTER(_PioSmConfig), ctypes.c_bool, ctypes.c_bool, ctypes.c_uint
    ]
    lib.sm_config_set_wrap.argtypes = [
        ctypes.POINTER(_PioSmConfig), ctypes.c_uint, ctypes.c_uint
    ]
    lib.sm_config_set_clkdiv.argtypes = [ctypes.POINTER(_PioSmConfig), ctypes.c_float]

    lib.pio_sm_init.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(_PioSmConfig)
    ]
    lib.pio_sm_exec.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    lib.pio_sm_set_enabled.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_bool]
    lib.pio_sm_set_consecutive_pindirs.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_bool
    ]
    lib.pio_gpio_init.argtypes = [ctypes.c_void_p, ctypes.c_uint]

    lib.pio_sm_get_rx_fifo_level.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.pio_sm_get_rx_fifo_level.restype = ctypes.c_uint
    lib.pio_sm_is_rx_fifo_full.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.pio_sm_is_rx_fifo_full.restype = ctypes.c_bool
    lib.pio_sm_get.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.pio_sm_get.restype = ctypes.c_uint32

    for encoder in ("pio_encode_jmp", "pio_encode_jmp_pin", "pio_encode_jmp_x_dec"):
        getattr(lib, encoder).argtypes = [ctypes.c_uint]
        getattr(lib, encoder).restype = ctypes.c_uint
    lib.pio_encode_in.argtypes = [ctypes.c_int, ctypes.c_uint]
    lib.pio_encode_in.restype = ctypes.c_uint
    lib.pio_encode_push.argtypes = [ctypes.c_bool, ctypes.c_bool]
    lib.pio_encode_push.restype = ctypes.c_uint
    lib.pio_encode_mov.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.pio_encode_mov.restype = ctypes.c_uint
    lib.pio_encode_mov_not.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.pio_encode_mov_not.restype = ctypes.c_uint

    lib.clock_get_hz.argtypes = [ctypes.c_int]
    lib.clock_get_hz.restype = ctypes.c_uint32


def _check_pio(handle: int | None) -> int:
    """`PIO_IS_ERR`: piolib returns small negative errno values cast to the pointer."""
    value = 0 if handle is None else int(handle)
    if value >= (1 << 64) - 200:
        raise PiolibError(f"pio_open failed: errno {value - (1 << 64)}")
    if value == 0:
        raise PiolibError("pio_open returned NULL")
    return value


_PIO_PINS, _PIO_X, _PIO_NULL, _PIO_ISR = 0, 1, 3, 6


def _assert_encoders_agree(lib: ctypes.CDLL) -> None:
    """Every instruction this program uses, checked against piolib's own encoder.

    THE POINT IS TO FAIL AT STARTUP RATHER THAN IN THE DATA. wl_sync.rp1.program
    encodes from the RP2040 instruction set so the program can be tested on a laptop;
    that is only sound while RP1 agrees. A divergence here means wrong timestamps in
    every session until someone notices, so it must stop the box instead.
    """
    checks = (
        ("jmp", program.encode_jmp(5), lib.pio_encode_jmp(5)),
        ("jmp pin", program.encode_jmp_pin(5), lib.pio_encode_jmp_pin(5)),
        ("jmp x--", program.encode_jmp_x_dec(1), lib.pio_encode_jmp_x_dec(1)),
        ("in pins", program.encode_in_pins(16), lib.pio_encode_in(_PIO_PINS, 16)),
        ("push noblock", program.encode_push_noblock(), lib.pio_encode_push(False, False)),
        ("mov isr,x", program.encode_mov_isr_x(), lib.pio_encode_mov(_PIO_ISR, _PIO_X)),
        ("mov x,~null", program.encode_mov_x_not_null(), lib.pio_encode_mov_not(_PIO_X, _PIO_NULL)),
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
        # UNVERIFIED: which chardev carries RP1's bank. gpiochip0 on current Pi 5
        # firmware, gpiochip4 on Pi 4, and it has moved between releases -- so it is
        # a parameter, and bench day confirms it by label rather than by hope.
        _check_chip(chip_path)
        self._lib = _load_piolib()
        if self._lib.pio_init() < 0:
            raise PiolibError("pio_init failed")
        _assert_encoders_agree(self._lib)
        self._pio = _check_pio(self._lib.pio_open(pio_index))
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

        words = (ctypes.c_uint16 * len(program.STROBE_CAPTURE))(*program.STROBE_CAPTURE)
        prog = _PioProgram(
            instructions=words,
            length=len(program.STROBE_CAPTURE),
            origin=_PIO_ORIGIN_ANY,
            pio_version=0,
        )
        offset = lib.pio_add_program(self._pio, ctypes.byref(prog))
        sm = lib.pio_claim_unused_sm(self._pio, True)
        if sm < 0:
            raise PiolibError("no free PIO state machine")
        self._sm = sm

        for pin in range(data_base, strobe_pin + 1):
            lib.pio_gpio_init(self._pio, pin)
        lib.pio_sm_set_consecutive_pindirs(
            self._pio, sm, data_base, data_count + 1, False
        )

        config = lib.pio_get_default_sm_config()
        lib.sm_config_set_in_pins(ctypes.byref(config), data_base)
        lib.sm_config_set_jmp_pin(ctypes.byref(config), strobe_pin)
        # shift_right=False so the lines land in ISR bits 0..n-1 and leave the upper
        # half clear. That clear upper half IS the pairing check in pair_fifo.
        lib.sm_config_set_in_shift(ctypes.byref(config), False, False, 32)
        lib.sm_config_set_fifo_join(ctypes.byref(config), _PIO_FIFO_JOIN_RX)
        lib.sm_config_set_wrap(
            ctypes.byref(config), offset + program.WRAP_TARGET, offset + program.WRAP
        )
        system_hz = lib.clock_get_hz(0)
        lib.sm_config_set_clkdiv(
            ctypes.byref(config), system_hz / (_TICK_HZ * _CYCLES_PER_TICK)
        )
        lib.pio_sm_init(self._pio, sm, offset, ctypes.byref(config))

        # Seed the counter before enabling: `set` carries a 5-bit immediate only.
        lib.pio_sm_exec(self._pio, sm, program.encode_mov_x_not_null())
        self._origin_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        lib.pio_sm_set_enabled(self._pio, sm, True)

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
        full = bool(lib.pio_sm_is_rx_fifo_full(self._pio, self._sm))
        level = lib.pio_sm_get_rx_fifo_level(self._pio, self._sm)
        # The level is a witness too: a FIFO that filled between the flag's sample and
        # this read holds every word it can, and any push into it was dropped.
        full = full or level >= _RX_FIFO_WORDS
        entries = [lib.pio_sm_get(self._pio, self._sm) for _ in range(level - level % 2)]
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
            self._lib.pio_sm_set_enabled(self._pio, self._sm, False)
        self._lib.pio_close(self._pio)
