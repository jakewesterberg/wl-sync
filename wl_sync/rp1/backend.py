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
    session nobody can align. All seven agreed on the CM5, 2026-10-18;
    the program has used six since 2026-11-07.

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
from dataclasses import dataclass
from typing import NamedTuple

from wl_sync.backend import (
    STAMP_NOT_SEEN, CaptureDegraded, CaptureLoss, FrameStamp, StampLag,
)
from wl_sync.clockmap import (
    LEAD_STAMP_TOLERANCE, ClockPair, LeadStamp, RawToP, match_lead_stamp, unwrap_near,
)
from wl_sync.pins import BARCODE_PIN, GPIO_MAP, SPARE_PINS
from wl_sync.rp1 import program
from wl_sync.rp1.gpio import EDGE_READ_MAX, EDGE_STAMP_LAG_US, EdgePump, pulse_deadlines
from wl_sync.rp1.pio_capture import StampPump, WordPump

# Every path is 2 cycles a decrement (tests/test_rp1_program.py:80), clocked at 2 MHz.
_TICK_HZ = 1_000_000
_CYCLES_PER_TICK = 2

# How long after the program sees a strobe it reads the data lines. What the task PC must
# hold, and for how long after raising the strobe, is start_strobed_capture's contract
# (start_strobed_capture's docstring).
DATA_SAMPLE_DELAY_US = program.DATA_SAMPLE_CYCLES / _CYCLES_PER_TICK * 1_000_000 / _TICK_HZ

# How long before a deadline to stop sleeping and spin. time.sleep overshoots by
# roughly a millisecond, which is a fifth of a barcode bit slot.
_SPIN_NS = 2_000_000

# How often the word pump drains the RX FIFO. The joined FIFO is 8 words, i.e. FOUR
# EVENTS, so this is set against bursts of strobed codes rather than their average
# rate. A BENCH-DAY NUMBER, not a derived one, and there is a known way to miss it:
# emit_pulses spins holding the GIL for up to 2 ms per pulse, and Python's default
# switch interval is 5 ms, so a barcode frame can delay this thread past the FIFO's
# headroom. A WordsDroppedError that coincides with frames is that. The same pass
# drains the stamp machine's FIFO, which a frame cannot fill at this rate: its rising
# edges are at least 10 ms apart (wl_sync/barcode.py:3-9).
_WORD_POLL_S = 0.001

# How long emit_pulses waits past a frame's end for the pump to drain the stamp FIFO
# through it, and how often it looks meanwhile (spec §2.5 step 1,
# docs/superpowers/specs/2026-10-30-one-clock-design.md:386). A live pump passes the
# end within a poll or two; the wait bounds what a stalled one costs, against the
# 800 ms of idle between frames (wl_sync/barcode.py:11).
STAMP_DRAIN_WAIT_S = 0.05
_STAMP_WAIT_POLL_S = 0.001

# Why a frame has no stamp when there is nothing to match it against. Each completes the
# journal's "barcode %d dropped: <cause>", as STAMP_NOT_SEEN does. Neither waits for the
# drain: a stamp machine that never started cannot be drained, and a stopped drain
# cannot pass the frame's end.
STAMPS_NOT_STARTED = "the stamp machine did not start"
STAMPS_STOPPED = "the stamp drain stopped"

# The edge request's event_buffer_size: the kernel's buffer, and the one libgpiod reads
# into. Generous, because the kernel's buffer overflows SILENTLY and the only witness is
# the sequence-number gap EdgePump records and check_health reports. It must hold a
# whole read (EDGE_READ_MAX), or the edge pump would take every full read for a short one.
_EDGE_EVENT_BUFFER = 1024

# How long close() waits for each capture thread to stop. The edge thread's last look
# (EdgePump.finish) is sized to end inside it: see STOP_PASSES.
_STOP_JOIN_S = 1.0


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
    _check_shim_version(lib)
    _declare(lib)
    return lib


# Bump with WLSYNC_SHIM_VERSION in pio/wlsync_pio.c whenever an exported signature
# changes: ctypes cannot see a signature, so a stale .so would be called with the wrong
# arguments without a sound. 1 is the unversioned original; 2: `entry` added,
# `wlsync_encode_jmp` removed; 3: `wlsync_start_stamp` and `wlsync_enable_in_sync` added.
_SHIM_VERSION = 3


def _check_shim_version(lib: ctypes.CDLL) -> None:
    """Refuse a libwlsync_pio built before this wl-sync, naming the rebuild.

    The box's shim is built once (deploy/README.md step 2), so updating Python alone
    leaves an old .so in place. `entry` sits mid-list, not at the end, so an old one
    reads every argument after `wrap` one place early (entry as data_base, data_base as
    data_count, and so on). On this box's pins (wl_sync/pins.py: 0, 16, 16) its own strobe
    check then fails with errno 22. That is cryptic; this check turns it into the rebuild
    instruction, and guards later changes whose shifted arguments might pass validation.
    """
    rebuild = (
        "rebuild and reinstall it with pio/build-shim.sh "
        "(deploy/README.md, step 2: piolib, compiled into this repository's shim)"
    )
    version_of = getattr(lib, "wlsync_shim_version", None)
    if version_of is None:
        raise PiolibError(
            f"libwlsync_pio predates shim versioning and this wl-sync needs {_SHIM_VERSION}: "
            + rebuild
        )
    version_of.restype = ctypes.c_int
    version = version_of()
    if version != _SHIM_VERSION:
        raise PiolibError(
            f"libwlsync_pio is version {version}, and this wl-sync needs {_SHIM_VERSION}: "
            + rebuild
        )


def _declare(lib: ctypes.CDLL) -> None:
    """Argument and return types. Scalars only: no struct crosses this boundary."""
    lib.wlsync_pio_open.argtypes = [ctypes.c_uint]
    lib.wlsync_pio_open.restype = ctypes.c_int
    lib.wlsync_pio_close.restype = None
    for encoder in ("wlsync_encode_jmp_pin", "wlsync_encode_jmp_x_dec",
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
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,      # wrap_target, wrap, entry
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,      # data_base, data_count, strobe
        ctypes.c_float, ctypes.c_uint,                    # clkdiv, seed instruction
    ]
    lib.wlsync_start_capture.restype = ctypes.c_int
    lib.wlsync_start_stamp.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,      # wrap_target, wrap, entry
        ctypes.c_uint, ctypes.c_uint, ctypes.c_int,       # data_base, jmp_pin, claim_jmp_pin
        ctypes.c_float, ctypes.c_uint,                    # clkdiv, seed instruction
    ]
    lib.wlsync_start_stamp.restype = ctypes.c_int
    lib.wlsync_enable_in_sync.argtypes = [ctypes.c_uint]
    lib.wlsync_enable_in_sync.restype = ctypes.c_int
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


def _encoder_checks(lib: ctypes.CDLL) -> tuple[tuple[str, int, int], ...]:
    """(name, ours, piolib's) for every instruction the program uses."""
    return (
        ("jmp pin", program.encode_jmp_pin(5), lib.wlsync_encode_jmp_pin(5)),
        ("jmp x--", program.encode_jmp_x_dec(1), lib.wlsync_encode_jmp_x_dec(1)),
        ("in pins", program.encode_in_pins(16), lib.wlsync_encode_in_pins(16)),
        ("push noblock", program.encode_push_noblock(), lib.wlsync_encode_push_noblock()),
        ("mov isr,x", program.encode_mov_isr_x(), lib.wlsync_encode_mov_isr_x()),
        ("mov x,~null", program.encode_mov_x_not_null(), lib.wlsync_encode_mov_x_not_null()),
    )


def _assert_encoders_agree(lib: ctypes.CDLL) -> None:
    """Every instruction this program uses, checked against piolib's own encoder.

    THE POINT IS TO FAIL AT STARTUP RATHER THAN IN THE DATA. wl_sync.rp1.program
    encodes from the RP2040 instruction set so the program can be tested on a laptop;
    that is only sound while RP1 agrees. A divergence here means wrong timestamps in
    every session until someone notices, so it must stop the box instead.

    MUST RUN AFTER THE BLOCK IS OPEN: piolib's encoders read the current block.
    Confirmed on silicon 2026-10-18: all seven agreed; the program has used six since
    2026-11-07.
    """
    wrong = [
        f"{name}: ours {ours:#06x} != piolib {theirs:#06x}"
        for name, ours, theirs in _encoder_checks(lib)
        if ours != theirs
    ]
    if wrong:
        raise PiolibError(
            "PIO instruction encodings disagree with piolib, so this program would "
            "not do what its tests say:\n  " + "\n  ".join(wrong)
        )


@dataclass(frozen=True)
class FrameTrace:
    """The last frame's match, and its lead edge's bracket width in RAW ns, for `bench
    stamps`. A bracket is measured whether or not a stamp is found: bench check B5 reads
    what a bracket costs from every frame."""

    lead: LeadStamp
    width_ns: int


class _Bracket(NamedTuple):
    """The clock readings around the lead edge's `set_value`: RAW innermost, then MONO,
    then REALTIME, read beside MONO for the header's offset."""

    realtime_minus_mono_ns: int
    mono_before_ns: int
    raw_before_ns: int
    raw_after_ns: int
    mono_after_ns: int

    @property
    def width_ns(self) -> int:
        return self.raw_after_ns - self.raw_before_ns


class Rp1Backend:
    """`SyncBackend` on RP1 PIO. A state machine for strobed capture, and a second on the
    same program that stamps the barcode output."""

    # W counts RP1's PIO counter, joined to CLOCK_MONOTONIC once in
    # start_strobed_capture; B and E count CLOCK_MONOTONIC (SyncBoxLogHeader.timebase).
    timebase = "split"

    def __init__(
        self,
        pio_index: int = 0,
        chip_path: str = "/dev/gpiochip0",
        stamp_pin: int = BARCODE_PIN,
    ) -> None:
        # The stamp machine reads GPIO17 itself and leaves it to libgpiod, which drives
        # it (c-direct). A spare pin, claimed, is for the bench controls: an unwired pin
        # stamps nothing, so a miss is a miss on silicon. Any other pin is allocated, and
        # claiming it would take it from its owner.
        if type(stamp_pin) is not int or (
            stamp_pin != BARCODE_PIN and stamp_pin not in SPARE_PINS
        ):
            raise ValueError(
                f"the stamp machine reads GPIO{BARCODE_PIN} itself, or a spare pin "
                f"({', '.join(f'GPIO{p}' for p in SPARE_PINS)}) for the bench controls, "
                f"not GPIO{stamp_pin}"
            )
        self._stamp_pin = stamp_pin
        # Which chardev carries RP1's bank has moved between releases (gpiochip4 on a
        # Pi 4), so it is a parameter. Confirmed by label on the CM5 2026-10-18:
        # gpiochip0 [pinctrl-rp1], 54 lines.
        _check_chip(chip_path)
        self._lib = _load_shim()
        _check(self._lib.wlsync_pio_open(pio_index), f"opening PIO block {pio_index}")
        # After the open, never before: piolib's encoders read the current block.
        _assert_encoders_agree(self._lib)
        self._sm: int | None = None
        # None until the stamp machine starts, and None for good if it could not.
        self._sm_stamps: int | None = None
        self._chip_path = chip_path
        self._edges = None
        self._out = None
        self._edge_stop: threading.Event | None = None
        self._edge_thread: threading.Thread | None = None
        self._pump: EdgePump | None = None
        self._words: WordPump | None = None
        self._word_stop: threading.Event | None = None
        self._word_thread: threading.Thread | None = None
        # The stamp machine's queue, filled on the pump's thread; None without the machine.
        self._stamp_pump: StampPump | None = None
        # RAW -> P (wl_sync.clockmap.RawToP), made at the sync enable. None before it, so
        # no provisional fit exists to be captured by mistake.
        self._fit: RawToP | None = None
        # The fit's seeded P per RAW rate, as ppb above 1. 0 until a calibration is given.
        self._seed_ppb = 0
        # Lead-edge pairs not yet taken, oldest first. Appended on the run loop's thread,
        # and locked because take_pairs may not be.
        self._pairs: list[ClockPair] = []
        self._pairs_lock = threading.Lock()
        # Stamps past the last frame's end, unwrapped: the next frame's to match. Run
        # loop's thread only, as is `last_frame`.
        self._leftover: tuple[int, ...] = ()
        self.last_frame: FrameTrace | None = None
        # THE SHARED ORIGIN. The PIO counter starts at 0xFFFFFFFF when the state
        # machine is enabled, so its ticks are microseconds since that instant. E and
        # B ticks must share it or the three paths do not align. Captured as close to
        # wlsync_enable_in_sync as this can be; the residual offset is one syscall, and
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
        on_loss: Callable[[CaptureLoss], None] | None = None,
    ) -> None:
        """Start the capture program; `on_word` gets each strobe's (tick, data).

        THE CONTRACT WITH THE TASK PC. The program reads the data lines
        DATA_SAMPLE_DELAY_US (1 us) after it sees the strobe. The strobe is tested every
        2 cycles, so it is seen up to 1 us after it rises, and the data is read 1.0-2.0 us
        after the rise. The task PC must hold the code at least 2.5 us after raising the
        strobe: the read ends before 2.0 us at the box's pins, and the extra 0.5 us covers
        skew between the strobe and data paths (the generator's channels, the wiring, the
        input synchroniser). A strobe must be high for at least 1 us and low for at least
        1 us, and must rise at least 6 us after the previous one; closer strobes may merge
        (tests/test_rp1_program.py:136, :142, :176, :183 and :191).
        """
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
                program.WRAP_TARGET, program.WRAP, program.ENTRY,
                data_base, data_count, strobe_pin,
                system_hz / (_TICK_HZ * _CYCLES_PER_TICK),
                program.encode_mov_x_not_null(),
            ),
            "starting strobed capture",
        )
        # THE BOX RECORDS ANYWAY. A stamp machine that cannot start must not stop the
        # capture machine, and raising here would: nothing above catches it
        # (wl_sync/service.py:252, wl_sync/cli.py:504 and :1333-1343), so run() would
        # exit and Restart=always (deploy/wl-sync.service:30) would loop on it.
        # Recording without stamps is today's split behaviour; the log line is the
        # record that this run has none.
        stamps = lib.wlsync_start_stamp(
            program.WRAP_TARGET, program.WRAP, program.ENTRY,
            data_base, self._stamp_pin, int(self._stamp_pin != BARCODE_PIN),
            system_hz / (_TICK_HZ * _CYCLES_PER_TICK),
            program.encode_mov_x_not_null(),
        )
        self._sm_stamps = stamps if stamps >= 0 else None
        if stamps < 0:
            log.error(
                "stamp machine did not start (errno %d): recording without barcode stamps",
                -stamps,
            )
        self._origin_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        # P's zero is the enable, so the fit starts on a line through RAW here and P 0, at
        # the seeded rate, until lead-edge pairs move it (wl_sync/clockmap.py:121-127).
        # Read after MONO, so RAW is the reading nearer the enable.
        self._fit = RawToP(time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW), self._seed_ppb)
        mask = 1 << self._sm
        if self._sm_stamps is not None:
            mask |= 1 << self._sm_stamps
        _check(lib.wlsync_enable_in_sync(mask), "enabling the state machines")

        # NOTHING DRAINED THE FIFO until 2026-10-18. drain() existed and nothing called
        # it, so the first burst of more than four codes filled the FIFO and `push
        # noblock` discarded every later word without a sound. The pump thread is what
        # makes the strobed path a capture path at all.
        self._words = WordPump(on_word, data_count, on_loss)
        if self._sm_stamps is not None:
            # The same program, so the same data window and the same pair check.
            self._stamp_pump = StampPump(data_count)
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

        The clock is read BEFORE the FIFO is looked at, and handed over only when no half
        pair is left behind: that is how far this drain can say the pump has delivered
        (`WordPump.handle`), which a roll waits on.
        """
        lib = self._lib
        if self._sm is None or self._words is None:
            raise PiolibError("start_strobed_capture has not run")
        polled_at = self.now_us()
        full = bool(lib.wlsync_sm_rx_full(self._sm))
        level = lib.wlsync_sm_rx_level(self._sm)
        # The level is a witness too: a FIFO that filled between the flag's sample and
        # this read holds every word it can, and any push into it was dropped.
        full = full or level >= _RX_FIFO_WORDS
        entries = [lib.wlsync_sm_get(self._sm) for _ in range(level - level % 2)]
        return self._words.handle(
            entries, fifo_was_full=full, polled_at=None if level % 2 else polled_at
        )

    def drain_stamps(self) -> bool:
        """Move whatever the stamp machine's FIFO holds into the stamp pump. Returns
        False when it should stop. Called from ONE thread, the pump's own, after `drain`:
        piolib has one caller while capture runs.

        As `drain` does: an even number of words, fullness checked before, and the mark
        read before the FIFO is looked at and handed over only with no half pair left.
        The mark is `_p_now()`, on the fit's count, NOT `now_us()`. In this half now_us()
        is MONO, which drifts from P by the two crystals' difference (spec §1,
        docs/superpowers/specs/2026-10-30-one-clock-design.md:81-88), and the frame's end
        that `emit_pulses` compares the mark with is mapped through the fit.
        """
        lib = self._lib
        sm, pump = self._sm_stamps, self._stamp_pump
        if sm is None or pump is None:
            raise PiolibError("the stamp machine is not running")
        polled_at = self._p_now()
        full = bool(lib.wlsync_sm_rx_full(sm))
        level = lib.wlsync_sm_rx_level(sm)
        full = full or level >= _RX_FIFO_WORDS
        entries = [lib.wlsync_sm_get(sm) for _ in range(level - level % 2)]
        return pump.handle(
            entries, fifo_was_full=full, polled_at=None if level % 2 else polled_at
        )

    def _p_now(self) -> int:
        """RAW now through the fit: the box's estimate of P, raw, 32 bits."""
        if self._fit is None:
            raise PiolibError("start_strobed_capture has not run")
        return self._fit.p_of(time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)) & 0xFFFF_FFFF

    def _pump_words(self) -> None:
        """Drain both RX FIFOs until told to stop: the capture machine's, then the stamp
        machine's. Errors are HELD, never raised.

        One last pass after the stop, because close() stops this thread before it
        disables the state machines: whatever the FIFO held at that moment is the end of
        the session's last trial.

        THE STAMP DRAIN STOPS ALONE. A desync or a failure of it stops it and leaves the
        word drain running: the event codes are records, and the box records anyway. A
        word drain that stops ends the thread, as it did before the stamp machine
        existed, and the stamp drain with it. check_health reports each.
        """
        try:
            while not self._word_stop.is_set():
                if not self.drain():
                    return
                self._pump_stamps()
                time.sleep(_WORD_POLL_S)
            self.drain()
            self._pump_stamps()
        except BaseException as error:  # noqa: BLE001 - surfaced by check_health
            self._words.error = error

    def _pump_stamps(self) -> None:
        """One stamp drain, its failure held by the stamp pump, which then stops."""
        pump = self._stamp_pump
        if pump is None or pump.stopped:
            return
        try:
            self.drain_stamps()  # a False return has already stopped the pump
        except BaseException as error:  # noqa: BLE001 - surfaced by check_health
            pump.stop(error)

    def start_edge_capture(
        self,
        pins: Sequence[int],
        on_edge: Callable[[int, int, int], None],
        on_loss: Callable[[CaptureLoss], None] | None = None,
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
        write independent of how slow the disk is. So is `on_loss`, for the edges the
        pump finds lost (EdgePump), and the word pump's for the code words it does.
        """
        import gpiod
        from gpiod.line import Clock, Direction, Edge

        if _EDGE_EVENT_BUFFER < EDGE_READ_MAX:
            raise ValueError(
                f"an edge buffer of {_EDGE_EVENT_BUFFER} cannot hold a whole read "
                f"(EDGE_READ_MAX, {EDGE_READ_MAX}): every full read would look short, "
                "and the edge pump's mark would pass edges still in the kernel"
            )
        settings = gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.BOTH,
            event_clock=Clock.MONOTONIC,
        )
        self._edges = gpiod.request_lines(
            self._chip_path,
            config={tuple(pins): settings},
            consumer="wl-sync",
            event_buffer_size=_EDGE_EVENT_BUFFER,
        )
        self._pump = EdgePump(on_edge, self._origin_ns, on_loss)
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

        Each pass is one wait and at most one read (`EdgePump.pump_once`), which also
        moves the mark a roll waits on. The wait is EDGE_WAIT_S, 20 ms; it was 0.2 s.

        One last look after the stop (`EdgePump.finish`), as the word pump drains once
        more: it delivers what the kernel still holds and reports every gap still held,
        which run() asks check_health() for after close(). It runs before close()
        releases the request. With the pass already running when the stop is set, it
        takes at most about 0.12 s on the real clock (STOP_BUDGET_US and two passes),
        and (STOP_PASSES + 1) x EDGE_WAIT_S = 0.82 s on a clock that stands still: both
        inside close()'s join, _STOP_JOIN_S.
        """
        try:
            while not self._edge_stop.is_set():
                if not self._pump.pump_once(self._edges, self.now_us):
                    return
            self._pump.finish(self._edges, self.now_us)
        except BaseException as error:  # noqa: BLE001 - surfaced by check_health
            # check_health() reports the stop. A loss still held for its window is
            # named here, or nowhere.
            lost = self._pump.abandon(error)
            if lost is not None:
                log.error("edge capture stopped with a loss still held: %s", lost)

    def delivered_through(self) -> dict[str, int | None]:
        """How far each capture path started has delivered (`SyncBackend`), as each pump
        last published it: `WordPump.handle` and `EdgePump.pump_once` give the rules.

        A PATH WHOSE THREAD HAS STOPPED IS LEFT OUT. It has delivered all it ever will,
        so a roll has nothing to wait for. Kept in, its last mark made every later open
        and close wait out the whole bound and log a WARNING about records it would
        never deliver. `check_health()` is what reports the stop.
        """
        marks: dict[str, int | None] = {}
        for path, pump, thread in (
            ("event-code capture", self._words, self._word_thread),
            ("edge capture", self._pump, self._edge_thread),
        ):
            if pump is not None and thread is not None and thread.is_alive():
                marks[path] = pump.delivered_through
        return marks

    def stamp_lag(self) -> StampLag | None:
        """The edge pump's witness (`EdgePump.pump_once`); None before its first read."""
        pump = self._pump
        if pump is None or pump.stamp_lag_max_us is None:
            return None
        return StampLag(pump.stamp_lag_max_us, pump.stamp_lag_over, EDGE_STAMP_LAG_US)

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
        taken = []
        if self._pump is not None:  # under the pump's lock: see EdgePump
            taken.append(("edge capture", self._pump.take_error()))
        if self._words is not None:
            error, self._words.error = self._words.error, None
            taken.append(("event-code capture", error))
        # No stamp pump, no stamp path: a stamp machine that never started was logged
        # once by start_strobed_capture, and each frame's FrameStamp says so again.
        if self._stamp_pump is not None:  # under the stamp pump's lock: see StampPump
            taken.append(("barcode stamping", self._stamp_pump.take_error()))
        for path, error in taken:
            if error is not None:
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
        stop = self._word_stop
        if self._stamps_stopped() and stop is not None and not stop.is_set():
            problems.append((
                "barcode stamping: stopped",
                "barcode stamping stopped: no later frame's lead edge can be stamped",
            ))
        if problems:
            raise CaptureDegraded(problems) from (held[0] if held else None)

    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> FrameStamp:
        """Drive the barcode frame, software-timed against ABSOLUTE deadlines, and find
        its lead edge's stamp.

        Software timing is fine here and precision is deliberately spent elsewhere: 5 ms
        slots sampled at 30 kHz give 150 samples per bit, and every receiver times its
        own edges. What is NOT fine is letting error accumulate. The decoder samples
        each bit at its slot centre measured from the lead edge, so it assumes uniform
        slots; sleeping for each duration in turn makes every overshoot permanent and
        the tail of a 36-pulse frame walks out of its slots. Deadlines are computed once
        from a single start, so a late pulse is corrected by the next.

        Sleeps to just short of each deadline and spins the remainder: `time.sleep`
        alone overshoots by around a millisecond, which is a fifth of a bit slot.

        THE LEAD EDGE IS BRACKETED by clock reads either side of its `set_value`, and
        after the frame `_lead_stamp` matches the stamp machine's stamps to it. Nothing
        here calls piolib: this runs on the run loop's thread, and while capture runs
        piolib has one caller, the pump's.

        In this half the stamp is only returned. The run loop still records `B` from the
        clock read before the frame (wl_sync/cli.py:787-790), and
        `BarcodeGenerator.emit_frame` drops what this returns (wl_sync/service.py:67-68).
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
        schedule = [*pulse_deadlines(pulses, start), (0, end_ns)]
        # The lead edge is the frame's first rise: the line is LOW before it, because
        # every frame ends LOW, above.
        lead = next((index for index, (level, _) in enumerate(schedule) if level), None)
        bracket = None
        for index, (level, deadline_ns) in enumerate(schedule):
            remaining = deadline_ns - time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            if remaining > _SPIN_NS:
                time.sleep((remaining - _SPIN_NS) / 1e9)
            while time.clock_gettime_ns(time.CLOCK_MONOTONIC) < deadline_ns:
                pass
            value = Value.ACTIVE if level else Value.INACTIVE
            if index == lead:
                bracket = self._set_bracketed(pin, value)
            else:
                self._out.set_value(pin, value)
        return self._lead_stamp(bracket, end_ns)

    def _set_bracketed(self, pin: int, value) -> _Bracket:
        """`set_value`, with RAW read immediately either side, MONO either side of
        those, and REALTIME beside MONO: one frame's pair (spec §2.2,
        docs/superpowers/specs/2026-10-30-one-clock-design.md:247-251). RAW is innermost
        because RAW is what the fit takes, and its width is what B5 measures."""
        realtime = time.clock_gettime_ns(time.CLOCK_REALTIME)
        mono_before = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        raw_before = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
        self._out.set_value(pin, value)
        raw_after = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
        mono_after = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        return _Bracket(
            realtime - mono_before, mono_before, raw_before, raw_after, mono_after
        )

    def _lead_stamp(self, bracket: _Bracket | None, end_ns: int) -> FrameStamp:
        """Spec §2.5 steps 1-5, for the frame just driven: `bracket` its lead edge's
        readings, `end_ns` its end on MONO
        (docs/superpowers/specs/2026-10-30-one-clock-design.md:386-393).

        The frame's end, read as RAW now, and the bracket are mapped to P through the
        fit. This waits, in 1 ms sleeps, until the stamp drain's mark has passed the end,
        or until STAMP_DRAIN_WAIT_S past it, and then matches the stamps queued since the
        last frame (`clockmap.match_lead_stamp`). A stamp makes a `ClockPair`; a narrow
        one moves the fit, and every one is kept for `take_pairs`.
        """
        if bracket is None:  # no rise at all, so nothing for a stamp to be of
            self.last_frame = None
            return FrameStamp(None, STAMP_NOT_SEEN)
        unmatched = LeadStamp(p=None, between=0, consumed=0, leftover=(), nearest=None)
        pump, fit = self._stamp_pump, self._fit
        if pump is None or fit is None:
            self.last_frame = FrameTrace(unmatched, bracket.width_ns)
            return FrameStamp(None, STAMPS_NOT_STARTED)
        if self._stamps_stopped():
            self.last_frame = FrameTrace(unmatched, bracket.width_ns)
            return FrameStamp(None, STAMPS_STOPPED)

        end = fit.p_of(time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW))
        low, high = fit.p_of(bracket.raw_before_ns), fit.p_of(bracket.raw_after_ns)
        give_up_ns = end_ns + round(STAMP_DRAIN_WAIT_S * 1e9)
        while True:
            through = pump.stamps_through  # raw, so unwrapped near the end it is held to
            if through is not None and unwrap_near(through, end) >= end:
                break
            if time.clock_gettime_ns(time.CLOCK_MONOTONIC) >= give_up_ns:
                break
            time.sleep(_STAMP_WAIT_POLL_S)
        # Ascending, as match_lead_stamp takes them: the leftovers were queued first.
        stamps = [*self._leftover, *(unwrap_near(stamp, low) for stamp in pump.take())]
        found = match_lead_stamp(stamps, (low, high), end)
        self._leftover = found.leftover
        self.last_frame = FrameTrace(found, bracket.width_ns)
        if found.p is None:
            if found.nearest is None:
                return FrameStamp(None, STAMP_NOT_SEEN, found.between)
            return FrameStamp(
                None,
                f"the stamp lies {found.nearest} us from the frame's lead edge as the CPU "
                f"bracketed it ({LEAD_STAMP_TOLERANCE} us allowed)",
                found.between,
            )
        pair = ClockPair(
            p=found.p,
            mono_ns=(bracket.mono_before_ns + bracket.mono_after_ns) // 2,
            raw_ns=(bracket.raw_before_ns + bracket.raw_after_ns) // 2,
            width_ns=bracket.width_ns,
            realtime_minus_mono_ns=bracket.realtime_minus_mono_ns,
        )
        if pair.narrow:
            fit.add(pair.raw_ns, pair.p)
        with self._pairs_lock:
            self._pairs.append(pair)
        # LeadStamp.p is unwrapped, to match against the unwrapped bracket; a tick a
        # backend hands over is raw.
        return FrameStamp(found.p & 0xFFFF_FFFF, between=found.between)

    def _stamps_stopped(self) -> bool:
        """The stamp drain has stopped: its own stop, or the pump thread's. False with no
        stamp machine, which has nothing to stop."""
        if self._stamp_pump is None:
            return False
        thread = self._word_thread
        return self._stamp_pump.stopped or thread is None or not thread.is_alive()

    def take_pairs(self) -> list[ClockPair]:
        """The lead-edge pairs since the last call, oldest first. Wide ones too: each
        carries its width, and only the narrow moved the fit."""
        with self._pairs_lock:
            pairs, self._pairs = self._pairs, []
        return pairs

    def close(self) -> None:
        """Stop everything this backend started, in the order that loses least.

        The capture threads first, because they call into the sink and the sink is
        about to be closed by the caller; then the state machines (the capture machine
        and, when it started, the stamp machine); then the descriptors. The word pump
        drains both machines once more on its way out, so it stops before they do.
        """
        for stop in (self._edge_stop, self._word_stop):
            if stop is not None:
                stop.set()
        for thread in (self._edge_thread, self._word_thread):
            if thread is not None:
                thread.join(timeout=_STOP_JOIN_S)
        for request in (self._edges, self._out):
            if request is not None:
                request.release()
        for sm in (self._sm, self._sm_stamps):
            if sm is not None:
                self._lib.wlsync_sm_set_enabled(sm, 0)
        self._lib.wlsync_pio_close()
