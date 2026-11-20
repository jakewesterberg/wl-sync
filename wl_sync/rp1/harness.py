"""The B3a and B3b bench harness. Hardware-gated, and NEVER ON THE RECORDING PATH.

    python -m wl_sync.bench skew      B3a: start skew between stamp machines on one enable
    python -m wl_sync.bench streams   B3b: a dense pin's machine against GPIO17's, 1 h

WHY NOT Rp1Backend. Its sync mask is the capture and stamp machines alone
(wl_sync/rp1/backend.py:1076), its start-up test keeps a third machine claimed for the
life of the block (:784-786), and it claims a spare stamp pin for the PIO (:666), which
would take GPIO18 from libgpiod. So this module calls the shim itself, through the exports
it already has (pio/wlsync_pio.c, version 6), as the start-up test does (backend.py:
832-845). Nothing here is imported by the recording path, and nothing in it changes it.

ONE THREAD CALLS PIOLIB, AND IT IS THE ONLY THREAD (the plan's Global Constraints,
docs/superpowers/plans/2026-11-07-one-clock.md:51). It drives an edge, waits
SETTLE_NS, then drains every machine. A pair is in the FIFO 5.5 us after its edge
(wl_sync/rp1/pio_capture.py:352-357), so each drain finds at most one pair a machine and
every stamp belongs to a known edge. A pump thread, as production has, could hold the
GIL for a spin and let a FIFO fill for the harness's reasons rather than the PIO's
(backend.py:76-84). `Block` refuses a call from any other thread.

THE REGISTER WRITER (`PulseWriter`) is the one part that touches RP1 directly. libgpiod's
`set_value` costs about 20 us a call (S.5's bracket median, 21.9 us,
docs/next-session.md:164-166), so two in a row cannot make a high under the 5 us below
which the capture program leaves by its short exit rather than its held loop
(wl_sync.bench.SHORT_EXIT_MAX_NS). B3b must cover both paths (the user's ruling,
2026-11-20, decision 2), so the writer makes the short highs through /dev/gpiomem0. It is
confined to this module and to the one dense pin; see its docstring for the limits.
"""

from __future__ import annotations

import ctypes
import mmap
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from wl_sync.pins import (
    BARCODE_PIN, CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN, SPARE_PINS,
)
from wl_sync.rp1 import meter, program
from wl_sync.rp1.backend import (
    _CLK_SYS_SANE_HZ, _CYCLES_PER_TICK, _TICK_HZ, STAMP_TEST_HIGH_US, STAMP_TEST_SETTLE_US,
    PiolibError, _assert_encoders_agree, _check,
)
from wl_sync.rp1.pio_capture import pair_stamps

# How long before a RAW deadline to stop sleeping and spin, as the backend's `_drive` does
# (wl_sync/rp1/backend.py:74): time.sleep overshoots by about a millisecond.
_SPIN_NS = 2_000_000


def raw_ns() -> int:
    """CLOCK_MONOTONIC_RAW, the clock every bracket here is read on: the CPU's crystal,
    untouched by NTP, which B3b fits each machine's count against."""
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)


def wait_until(deadline_ns: int) -> None:
    """Sleep, then spin, to a RAW deadline, as `_drive` does on MONO (backend.py:1561-1566)."""
    remaining = deadline_ns - raw_ns()
    if remaining > _SPIN_NS:
        time.sleep((remaining - _SPIN_NS) / 1e9)
    while raw_ns() < deadline_ns:
        pass


# ---------------------------------------------------------------------------
# The register writer
# ---------------------------------------------------------------------------
#
# Every number below is from "Raspberry Pi RP1 Peripherals" (build 2023-11-07, b9b6f74),
# recorded with its pages in hardware/datasheet-params.toml, [rp1_peripherals], which
# tests/test_rp1_harness.py checks these against. Page numbers are the printed ones.

# /dev/gpiomem0 maps IO_BANK0 from its offset 0. NOT IN THE DATASHEET, which never names
# the device: it is the kernel's device tree. IO_BANK0 is RP1's 0x400d0000 (Table 2,
# p.9), BAR1 + 0xd0000 to the host (section 2.3.1, p.8). `PulseWriter.verify` checks the
# mapping on the box before the first write.
GPIOMEM_PATH = "/dev/gpiomem0"
# The four atomic aliases of a block take 16 kB (section 2.4, p.11), so this span holds
# IO_BANK0's every register through each. It is the only part of the map `Gpiomem` reaches.
IO_BANK0_SPAN = 0x4000
# How much of /dev/gpiomem0 is mapped: ALL of its window, 0x30000. A BENCH FACT, not the
# datasheet's (wl-rig-sync0, 2026-11-20, box step 1). The kernel logs "rpi-gpiomem
# 1f000d0000.gpiomem: window base 0x1f000d0000 size 0x00030000 ... as /dev/gpiomem0", and
# rpi-gpiomem refuses a shorter map: mmap of 0x1000 and 0x4000 failed with EINVAL, under
# PROT_READ and under PROT_READ | PROT_WRITE, and 0x30000 succeeded. The window runs from
# IO_BANK0, BAR1 + 0xd0000, through sys_rio and pads_bank0 (Table 2, p.9). Nothing beyond
# IO_BANK0_SPAN is ever read or written: `Gpiomem._index` refuses it, and the writer's
# stores are guarded again by `_store`.
GPIOMEM_MAP_LENGTH = 0x30000
# Section 2.4, p.11: "Addr + 0x2000 : atomic bitmask set on write", "Addr + 0x3000 :
# atomic bitmask clear on write". IO_BANK0 has atomic access (Table 5, p.18). So each
# write changes only the bits it names, with no read-modify-write that could race the
# kernel's driver.
ALIAS_SET = 0x2000
ALIAS_CLEAR = 0x3000

# GPIOn_CTRL (Table 8, p.21-22).
FUNCSEL_MASK = 0x1F  # bits 4:0, "Function select. 31 == NULL"
FUNCSEL_SYS_RIO = 5  # Table 4, p.15: column a5 is SYS_RIO[n] on every GPIO0-27
FUNCSEL_PIO = 7  # column a7, PIO[n]
FUNCSEL_NULL = 31
# OUTOVER, bits 13:12: 0x0 the output funcsel selects, 0x3 "drive output high". Both bits
# set through SET is high; both cleared through CLEAR is the funcsel's output again, which
# is the level libgpiod holds.
OUTOVER_MASK = 0x3 << 12
OEOVER_MASK = 0x3 << 14  # bits 15:14, the output enable's override
IRQRESET = 1 << 28  # type SC, self-clearing: not part of the state to restore

# GPIOn_STATUS (Table 7, p.20-21), read-only.
STATUS_OUTTOPAD = 1 << 9  # "output signal to pad after register overide is applied"
STATUS_OETOPAD = 1 << 13  # "output enable to pad after register overide is applied"
STATUS_INFROMPAD = 1 << 17  # "input signal from pad, before filtering and override"
# The funcsel's own output and output enable, BEFORE the override: these follow libgpiod
# even while OUTOVER forces the pad, which is how `clear_override` checks the map.
STATUS_OUTFROMPERI = 1 << 8  # "output signal from selected peripheral, before register overide"
STATUS_OEFROMPERI = 1 << 12  # "output enable from selected peripheral, before register overide"

# How many times verify() toggles the pin through libgpiod. One rise and fall could match a
# stale bit by chance; three cannot, on a map that is not IO_BANK0.
VERIFY_TOGGLES = 3


def status_offset(pin: int) -> int:
    """GPIOn_STATUS: 0x000, 0x008, ... 0x0d8 (Table 6, p.18-20; heading, p.20)."""
    return 8 * pin


def ctrl_offset(pin: int) -> int:
    """GPIOn_CTRL: 0x004, 0x00c, ... 0x0dc (Table 6; heading, p.21)."""
    return 8 * pin + 4


class WriterError(RuntimeError):
    """The register writer found the pin other than it left it."""


class NothingToClear(WriterError):
    """`clear_override` found no override on the pin it was asked to clear: nothing was
    written, and nothing was cleared, so a run's stuck pin may be the other one."""


class WriterRefused(WriterError):
    """The register writer would not write: the pin is not this harness's to drive, or
    the map is not the bank it was assumed to be. Nothing was written."""


class Gpiomem:
    """/dev/gpiomem0, mapped: 32-bit reads and writes at byte offsets into IO_BANK0.

    One word an access, through a memoryview of unsigned ints, so each is a single 32-bit
    load or store [inferred: that CPython's fixed-size copy compiles to one]. O_SYNC, as
    pinctrl opens it [inferred]. Writes cross PCIe as posted transactions (section 3.3.1,
    p.35). That a read which follows them returns only after they land is [inferred]: the
    datasheet promises it after a write barrier, which nothing here issues (`pulse`)."""

    def __init__(self, path: str) -> None:
        fd = os.open(path, os.O_RDWR | os.O_SYNC)
        try:
            self._map = mmap.mmap(fd, GPIOMEM_MAP_LENGTH, mmap.MAP_SHARED,
                                  mmap.PROT_READ | mmap.PROT_WRITE)
        finally:
            os.close(fd)
        self._words: memoryview | None = memoryview(self._map).cast("I")
        if self._words.itemsize != 4:
            self.close()
            raise WriterError("this platform's unsigned int is not 32 bits")

    def _index(self, offset: int) -> int:
        if offset % 4 or not 0 <= offset < IO_BANK0_SPAN:
            raise ValueError(f"{offset:#x} is not a word offset into IO_BANK0's aliases")
        if self._words is None:
            raise WriterError("the map is closed")
        return offset // 4

    def read32(self, offset: int) -> int:
        return self._words[self._index(offset)]

    def write32(self, offset: int, value: int) -> None:
        self._words[self._index(offset)] = value

    def close(self) -> None:
        if self._words is not None:
            self._words.release()
            self._words = None
            self._map.close()


def _store(regs, pin: int, offset: int, value: int, aliases: Sequence[int]) -> None:
    """THE ONLY WAY ANYTHING HERE WRITES RP1. The map spans the whole 0x30000 window
    (GPIOMEM_MAP_LENGTH), so this guard, not the map's bounds, keeps every store to
    OUTOVER_MASK on GPIO`pin`'s CTRL, through the aliases named: SET and CLEAR for the
    writer, CLEAR alone for `clear_override`. Anything else raises with nothing
    written."""
    allowed = {ctrl_offset(pin) + alias for alias in aliases}
    if offset not in allowed or value != OUTOVER_MASK:
        raise WriterError(
            f"the harness stores only {OUTOVER_MASK:#06x} to GPIO{pin}'s CTRL through "
            f"{', '.join(f'{a:#x}' for a in sorted(allowed))}, not {value:#x} to {offset:#x}"
        )
    regs.write32(offset, value)


def clear_command(pin: int) -> str:
    """The command that clears an override left set on `pin` (`clear_override`)."""
    return f"python -m wl_sync.bench override --pin {pin} --clear"


def _refusal(pin: int, ctrl: int, status: int) -> str:
    """Why GPIO`pin` is not this harness's libgpiod output, held low, or "" when it is."""
    funcsel = ctrl & FUNCSEL_MASK
    if funcsel == FUNCSEL_PIO:
        return (f"GPIO{pin} is the PIO's (FUNCSEL {FUNCSEL_PIO}, PIO[{pin}]): something has "
                "claimed it for a state machine")
    if funcsel != FUNCSEL_SYS_RIO:
        named = " (NULL)" if funcsel == FUNCSEL_NULL else ""
        return (f"GPIO{pin} runs function {funcsel}{named}, not SYS_RIO "
                f"({FUNCSEL_SYS_RIO}), the function of a line libgpiod drives")
    if ctrl & OEOVER_MASK:
        return (f"GPIO{pin}'s output enable is under a register override "
                f"(CTRL {ctrl:#010x}): something else is driving it")
    if ctrl & OUTOVER_MASK:
        return (f"GPIO{pin}'s output is under a register override already "
                f"(CTRL {ctrl:#010x}), as a run killed between a pulse's two stores leaves "
                f"it; clear it with: {clear_command(pin)}")
    if not status & STATUS_OETOPAD:
        return f"GPIO{pin} is not driven as an output (STATUS {status:#010x})"
    if status & STATUS_OUTTOPAD:
        return f"GPIO{pin} is high (STATUS {status:#010x}), where libgpiod holds it low"
    return ""


class PulseBounds(NamedTuple):
    """One writer pulse's RAW readings (`PulseWriter.pulse`): the SET lands inside
    [before, set_after] and the CLEAR inside [clear_before, after]; each read-back's cost."""

    before: int
    set_after: int
    clear_before: int
    after: int
    set_read_ns: int
    clear_read_ns: int


class PulseWriter:
    """Highs shorter than libgpiod makes, on ONE spare pin, through RP1's GPIOn_CTRL.

    WHAT IT WRITES, AND NOTHING ELSE: the two OUTOVER bits of the dense pin's CTRL,
    through that register's SET alias (drive high) and its CLEAR alias (back to the
    funcsel's output, the low libgpiod holds). The value is OUTOVER_MASK every time. RIO
    is never written: the datasheet gives no offset for its registers (section 3.3, p.34).

    WHAT IT REFUSES. A pin but GPIO18 or GPIO19 (`SPARE_PINS`). A pin that is not this
    process's libgpiod output, held low, with no override: one the PIO holds, one under
    another function, one not driven, one high, one already overridden (`_refusal`). A
    libgpiod request for a line another consumer holds is refused by the kernel first
    (`Lines`). And a map whose STATUS does not follow the level libgpiod sets
    (`verify`), which is how a /dev/gpiomem0 that is not IO_BANK0 would show.

    WHAT IT RESTORES, ON EVERY PATH. The precondition is OUTOVER 0, so clearing OUTOVER
    is the pin's prior state. Each pulse clears it in a `finally`, whatever stops the
    hold; close() clears it again and reads CTRL back against the word open() read, and
    raises if they differ. A writer that never armed a pulse writes nothing at close,
    so a refused map or pin is never written (review I1). SIGTERM and SIGHUP restore
    (the bench's handlers). SIGKILL between the two stores still leaves the pin forced
    high under the override: harmless, since GPIO18 and GPIO19 are no-connect on the
    bench and on the breakout, and the next open() refuses the pin, naming
    `clear_override`'s command, which clears it with no sudo.

    WHAT IS UNVERIFIED ON THE CM5: all of it. That the posted SET and CLEAR writes make a
    high of about the hold, that `verify` sees what the pad does, and that the PIO samples
    the overridden pad as it samples libgpiod's level. The probe measures the first and
    the last before B3b's hour (`bench streams`).
    """

    def __init__(
        self,
        pin: int,
        open_map: Callable[[str], Gpiomem] = Gpiomem,
        path: str = GPIOMEM_PATH,
    ) -> None:
        if type(pin) is not int or pin not in SPARE_PINS:
            raise ValueError(
                f"the register writer drives a spare pin "
                f"({', '.join(f'GPIO{p}' for p in SPARE_PINS)}) and nothing else, not "
                f"GPIO{pin}"
            )
        self.pin = pin
        self._ctrl = ctrl_offset(pin)
        self._status = status_offset(pin)
        self._open_map = open_map
        self._path = path
        self._regs: Gpiomem | None = None
        self._prior = 0
        self._verified = False
        # Set immediately before the first SET store: only then has the writer anything to
        # restore, and close() writes nothing through a map it never wrote (review I1).
        self._armed = False
        self._pulsed = False

    def __enter__(self) -> PulseWriter:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        """Map the bank and read the pin's CTRL and STATUS; refuse, closing the map with
        nothing written, unless the pin is libgpiod's output, low, with no override."""
        regs = self._open_map(self._path)
        try:
            ctrl = regs.read32(self._ctrl)
            refusal = _refusal(self.pin, ctrl, regs.read32(self._status))
            if refusal:
                raise WriterRefused(refusal)
        except BaseException:
            regs.close()
            raise
        self._regs, self._prior = regs, ctrl

    def _store(self, offset: int, value: int) -> None:
        """Every store the writer makes, through `_store`'s guard."""
        _store(self._opened(), self.pin, offset, value, (ALIAS_SET, ALIAS_CLEAR))

    def check(self) -> None:
        """Read the pin again, as open() read it, and refuse it on the same grounds: the
        rig calls it once the PIO block is open, after which a pin the block had taken
        would be the PIO's, and no longer the writer's to override."""
        regs = self._opened()
        refusal = _refusal(self.pin, regs.read32(self._ctrl), regs.read32(self._status))
        if refusal:
            raise WriterRefused(refusal)

    def _opened(self) -> Gpiomem:
        if self._regs is None:
            raise WriterRefused("the register writer is not open")
        return self._regs

    def verify(self, drive: Callable[[bool], None]) -> None:
        """Before the first write: toggle the pin through libgpiod (`drive`) and read
        STATUS through the map. Its output to the pad and its input from the pad must
        follow, with the output enabled, every time, or the map is not this pin's
        register and nothing is ever written. Reads only; leaves the pin low."""
        regs = self._opened()
        try:
            for _ in range(VERIFY_TOGGLES):
                for high in (True, False):
                    drive(high)
                    status = regs.read32(self._status)
                    levels = status & (STATUS_OUTTOPAD | STATUS_INFROMPAD)
                    want = (STATUS_OUTTOPAD | STATUS_INFROMPAD) if high else 0
                    if levels != want or not status & STATUS_OETOPAD:
                        raise WriterRefused(
                            f"GPIO{self.pin}'s STATUS through {self._path} does not follow "
                            f"the level libgpiod set ({'high' if high else 'low'}: STATUS "
                            f"{status:#010x}), so the map is not IO_BANK0 as assumed"
                        )
        finally:
            drive(False)
        self._verified = True

    def _checked(self) -> Gpiomem:
        regs = self._opened()
        if not self._verified:
            raise WriterRefused("the register writer writes only once verify() has passed")
        return regs

    def pulse(self, hold_ns: int) -> PulseBounds:
        """One high, EACH STORE READ BACK BEFORE ITS RAW (box step 2, fix round 4):
        RAW, the SET store, RAW, a STATUS read, RAW, a spin of `hold_ns`, the CLEAR store,
        RAW, a STATUS read, RAW.

        WHY THE READ-BACKS. RP1 is behind PCIe and its writes are posted. On the box
        (wl-rig-sync0, 2026-11-20) the probe's writer highs, a SET and a CLEAR with no read
        between them, were stamped 27 times in 600, and the libgpiod ones, each read back,
        every time: the two stores reached RP1 nearly together, whatever the CPU's hold.
        A read of the same block cannot complete before the posted writes ahead of it have
        landed: PCIe's ordering rules let no read request pass a posted write, and Arm keeps
        Device-memory accesses to one peripheral in program order. That is an ENGINEERING
        JUDGMENT, not a document's claim. RP1 Peripherals section 3.3.1.1 (p.35) gives the
        practice ("insert a write barrier after the last write that toggles a pin, then
        issue the read ... a read will sample the point after the state change"), with a
        barrier this does not issue.

        THE BOUNDS. The SET lands after `before` and before `set_after`; the CLEAR after
        `clear_before` and before `after`. So the high lasts at most `after - before` and
        at least `clear_before - set_after`, which is at least `hold_ns`: the hold is timed
        from the SET's read-back (review I2), so a high can no longer be cut under 1 us.
        `set_read_ns` and `clear_read_ns` are what each read-back cost in RAW, which sets
        how short an upper bound can be. Raises when the SET's read-back shows the pad low
        (the ordering failed) or the CLEAR's shows it high; the CLEAR is stored either way.

        THE FIRST PULSE READS CTRL BACK (review M3), against the word open() read: a store
        that was split or widened, and so touched other bits of CTRL, stops the writer after
        one pulse rather than at close."""
        regs = self._checked()
        before = raw_ns()
        # Armed before the first store, so close() clears the override whatever stops this
        # between here and the CLEAR; never armed, close() writes nothing (review I1).
        self._armed = True
        try:
            self._store(self._ctrl + ALIAS_SET, OUTOVER_MASK)
            set_issued = raw_ns()
            status = regs.read32(self._status)
            set_after = clear_before = raw_ns()
            if not status & STATUS_OUTTOPAD:
                raise WriterError(
                    f"GPIO{self.pin} did not rise by the SET's read-back (STATUS "
                    f"{status:#010x}): the read passed the posted write"
                )
            deadline = set_after + hold_ns
            while clear_before < deadline:
                clear_before = raw_ns()
        finally:
            self._store(self._ctrl + ALIAS_CLEAR, OUTOVER_MASK)
        clear_issued = raw_ns()
        status = regs.read32(self._status)
        after = raw_ns()
        if not self._pulsed:
            self._pulsed = True
            ctrl = regs.read32(self._ctrl)
            if (ctrl ^ self._prior) & ~IRQRESET:
                raise WriterError(
                    f"GPIO{self.pin}'s CTRL reads {ctrl:#010x} after the first pulse, where "
                    f"it read {self._prior:#010x} before it: a store touched more than "
                    "OUTOVER"
                )
        if status & STATUS_OUTTOPAD:
            raise WriterError(f"GPIO{self.pin} is still high after the override was cleared "
                              f"(STATUS {status:#010x})")
        return PulseBounds(before, set_after, clear_before, after,
                           set_after - set_issued, after - clear_issued)

    def rise_landed(self) -> int:
        """After libgpiod's rise on the pin: one STATUS read, then RAW. The read returns
        only once the rise's write has landed [inferred: that the kernel's posted write is
        ordered before this process's read of the same block, as `pulse` reasons], so that
        RAW starts a held high's lower bound. Raises when the pin reads low."""
        status = self._checked().read32(self._status)
        landed = raw_ns()
        if not status & STATUS_OUTTOPAD:
            raise WriterError(f"GPIO{self.pin} did not rise (STATUS {status:#010x})")
        return landed

    def fall_landed(self) -> int:
        """After libgpiod's fall on the pin: one STATUS read, then RAW, which ends the
        high's upper bound as `pulse`'s does (fix round 4) [inferred, as `rise_landed`].
        Raises when the pin reads high."""
        status = self._checked().read32(self._status)
        landed = raw_ns()
        if status & STATUS_OUTTOPAD:
            raise WriterError(f"GPIO{self.pin} did not fall (STATUS {status:#010x})")
        return landed

    def close(self) -> None:
        """Unmap; and if the writer ever armed a pulse, first clear the override and read
        CTRL back. Raises WriterError when CTRL is then not the word open() found, all but
        the self-clearing IRQRESET; the map is closed either way. A writer that never
        armed writes nothing: after a refusal by `verify` or `check` the map, or the pin,
        is not the writer's to write (review I1). Once only: a second call does
        nothing."""
        regs, self._regs = self._regs, None
        if regs is None:
            return
        self._verified = False
        if not self._armed:
            regs.close()
            return
        try:
            _store(regs, self.pin, self._ctrl + ALIAS_CLEAR, OUTOVER_MASK,
                   (ALIAS_SET, ALIAS_CLEAR))
            now = regs.read32(self._ctrl)
        finally:
            regs.close()
        if (now ^ self._prior) & ~IRQRESET:
            raise WriterError(
                f"GPIO{self.pin}'s CTRL was not restored: it reads {now:#010x} with the "
                f"override cleared, where it read {self._prior:#010x} when the writer "
                "opened"
            )


# ---------------------------------------------------------------------------
# The PIO block, the lines, and the drive-settle-drain loop
# ---------------------------------------------------------------------------

# How long after a rise every FIFO is read: the start-up test's settle (backend.py:104-108),
# far past the 5.5 us a pair takes to land (pio_capture.py:352-357).
SETTLE_NS = STAMP_TEST_SETTLE_US * 1_000

# The consumer libgpiod records for the harness's lines, so `gpioinfo` shows who holds them
# and it is not taken for the recording process's "wl-sync" (backend.py:830).
CONSUMER = "wl-sync-bench"

# The running check's pulse: the start-up test's (backend.py:97), which no decoder takes
# for a frame.
RUNNING_CHECK_HIGH_NS = STAMP_TEST_HIGH_US * 1_000

# The running check's two failures, as the start-up test words its own (backend.py:112-119).
SYNC_REFUSED = (
    "the sync enable was refused: the machines on GPIO{pin} stamped its rise only when "
    "enabled alone"
)
BLIND = (
    "no stamp when GPIO{pin} rose, enabled in sync or alone: the PIO does not see "
    "GPIO{pin}, unclaimed"
)


class HarnessError(RuntimeError):
    """The harness cannot run as asked: a line held by another consumer, or piolib
    called from a second thread."""


@dataclass(frozen=True)
class FifoRead:
    """One look at a machine's RX FIFO: every word it held, whether it was full, and the
    level read's errno when it failed (nothing is popped then)."""

    words: list[int]
    full: bool
    level_error: int | None = None


class Block:
    """The PIO block for the harness: the capture machine, a bystander on GPIO0-16, and
    stamp machines on chosen pins, unclaimed. Every call into the shim is made here, on
    the thread that built it."""

    def __init__(self, load: Callable[[], object] | None = None) -> None:
        self._thread = threading.get_ident()
        self._load = load
        self._lib = None
        self.capture_sm: int | None = None
        self.stamp_sms: list[int] = []
        self.pins: dict[int, int] = {}  # stamp machine -> its jmp pin
        self.depth = 0
        # The width meter's machine (fix round 6), and clk_sys, which its counts scale by.
        self.meter_sm: int | None = None
        self.clk_sys_hz = 0

    def _here(self):
        if threading.get_ident() != self._thread:
            raise HarnessError(
                "piolib called from a second thread: one thread calls it while capture "
                "runs (the plan's Global Constraints)"
            )
        if self._lib is None:
            raise HarnessError("the PIO block is not open")
        return self._lib

    def open(self, stamp_pins: Sequence[int], meter_pin: int | None = None) -> list[int]:
        """Load and check the shim, open block 0, start the capture machine as production
        configures it, then one stamp machine per pin with claim_jmp_pin 0, all left
        disabled. Each step is backend.py's `_configure` (:584-654) and the start-up test's
        start (:832-836). With `meter_pin`, then the width meter on it (`_start_meter`).
        On any failure the block is closed and the error raised."""
        load = self._load
        if load is None:
            from wl_sync.rp1.backend import _load_shim as load
        lib = load()
        opened = lib.wlsync_pio_open(0)
        if opened < 0:
            raise PiolibError(f"opening PIO block 0 failed: errno {-opened}")
        self._lib = lib
        try:
            # After the open: piolib's encoders read the current block (backend.py:608).
            _assert_encoders_agree(lib)
            system_hz = lib.wlsync_clk_sys_hz()
            low, high = _CLK_SYS_SANE_HZ
            if not low <= system_hz <= high:
                raise PiolibError(f"RP1 reported clk_sys as {system_hz} Hz")
            clkdiv = system_hz / (_TICK_HZ * _CYCLES_PER_TICK)
            depth = lib.wlsync_rx_fifo_depth()
            if depth < 2 or depth % 2:
                raise PiolibError(f"the RX FIFO's depth reads {depth}")
            self.depth = depth
            words = (ctypes.c_uint16 * len(program.STROBE_CAPTURE))(*program.STROBE_CAPTURE)
            self.capture_sm = _check(lib.wlsync_start_capture(
                words, len(program.STROBE_CAPTURE),
                program.WRAP_TARGET, program.WRAP, program.ENTRY,
                CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN,
                clkdiv, program.encode_mov_x_not_null(),
            ), "starting the capture machine")
            for pin in stamp_pins:
                # claim_jmp_pin 0: the pin's function is left alone, so libgpiod keeps it
                # (pio/wlsync_pio.c:204-207). The data window is the capture machine's,
                # so every data word's upper half stays clear (:232-234).
                sm = _check(lib.wlsync_start_stamp(
                    program.WRAP_TARGET, program.WRAP, program.ENTRY,
                    CODE_DATA_BASE, pin, 0, clkdiv, program.encode_mov_x_not_null(),
                ), f"starting a stamp machine on GPIO{pin}")
                self.stamp_sms.append(sm)
                self.pins[sm] = pin
            self.clk_sys_hz = system_hz
            if meter_pin is not None:
                self._start_meter(lib, meter_pin)
        except BaseException:
            self.close()
            raise
        return list(self.stamp_sms)

    def _start_meter(self, lib, pin: int) -> None:
        """The width meter (wl_sync/rp1/meter.py; the user's ruling, 2026-11-20, "A PIO
        width meter"), through shim version 6's `wlsync_start_meter`: loaded AFTER the
        capture program, at its own offset, so the capture program and its loading are as
        on the recording path; on the block's last free machine; its jmp pin `pin`,
        unclaimed; CLKDIV 1. Left disabled, for the one sync enable."""
        if isinstance(lib, ctypes.CDLL):  # the shim itself; a test's stand-in has its own
            try:
                _declare_meter(lib)
            except AttributeError as error:  # a .so that reports 6 and lacks the export
                raise PiolibError(f"libwlsync_pio lacks the width meter ({error}): "
                                  "rebuild it with pio/build-shim.sh") from error
        words = (ctypes.c_uint16 * len(meter.WIDTH_METER))(*meter.WIDTH_METER)
        self.meter_sm = _check(lib.wlsync_start_meter(
            words, len(meter.WIDTH_METER), meter.WRAP_TARGET, meter.WRAP, meter.ENTRY,
            pin, meter.CLKDIV,
        ), f"starting the width meter on GPIO{pin}")

    def machines(self) -> list[int]:
        return [sm for sm in (self.capture_sm, *self.stamp_sms, self.meter_sm)
                if sm is not None]

    def enable_in_sync(self) -> int:
        """Every machine, the capture machine included, in one sync enable, as production
        enables its two (backend.py:1076-1085). Returns RAW read immediately before it,
        as `_start_one_count` reads it (:1073): P's zero, near enough to unwrap by."""
        lib = self._here()
        mask = sum(1 << sm for sm in self.machines())
        before = raw_ns()
        _check(lib.wlsync_enable_in_sync(mask), "enabling the machines in sync")
        return before

    def enable_alone(self, sms: Sequence[int]) -> None:
        lib = self._here()
        for sm in sms:
            _check(lib.wlsync_sm_set_enabled(sm, 1), f"enabling SM {sm}")

    def disable_all(self) -> None:
        lib = self._here()
        for sm in self.machines():
            _check(lib.wlsync_sm_set_enabled(sm, 0), f"disabling SM {sm}")

    def read(self, sm: int) -> FifoRead:
        """`drain_stamps`' look (backend.py:1155-1172): the full flag, then the level, a
        level of the whole depth counted as full too, then every word. A level that cannot
        be read pops nothing."""
        lib = self._here()
        full = bool(lib.wlsync_sm_rx_full(sm))
        level = lib.wlsync_sm_rx_level(sm)
        if level < 0:
            return FifoRead([], full, -level)
        full = full or level >= self.depth
        return FifoRead([lib.wlsync_sm_get(sm) for _ in range(level)], full)

    def close(self) -> None:
        """Every machine disabled, then the block closed, as backend.py's close
        (:1896-1900). Once only. The block is closed even if a disable fails."""
        lib = self._lib
        if lib is None:
            return
        try:
            for sm in self.machines():
                lib.wlsync_sm_set_enabled(sm, 0)
        finally:
            self._lib = None
            lib.wlsync_pio_close()


class Lines:
    """One libgpiod request for every pin the harness drives, outputs starting low, under
    CONSUMER. A line another consumer holds is refused before anything is requested: the
    harness must never take a line from its owner."""

    def __init__(self, chip_path: str, pins: Sequence[int]) -> None:
        import gpiod
        from gpiod.line import Direction, Value

        with gpiod.Chip(chip_path) as chip:
            for pin in pins:
                info = chip.get_line_info(pin)
                if info.used:
                    raise HarnessError(
                        f"GPIO{pin} is held by {info.consumer!r}: the harness drives only "
                        "lines nothing else holds"
                    )
        self.pins = tuple(pins)
        self._active, self._inactive = Value.ACTIVE, Value.INACTIVE
        # The tuple key is the backend's own form for several lines (backend.py:1301),
        # and the low start is the start-up test's (:825-830).
        self._request = gpiod.request_lines(
            chip_path,
            config={self.pins: gpiod.LineSettings(direction=Direction.OUTPUT,
                                                  output_value=Value.INACTIVE)},
            consumer=CONSUMER,
        )

    def _value(self, high: bool):
        return self._active if high else self._inactive

    def set(self, pin: int, high: bool) -> None:
        self._request.set_value(pin, self._value(high))

    def set_bracketed(self, pin: int, high: bool) -> tuple[int, int]:
        """`set_value` with RAW read immediately either side, as `_set_bracketed` reads RAW
        innermost (backend.py:1574-1587). The write lies between the two."""
        before = raw_ns()
        self._request.set_value(pin, self._value(high))
        return before, raw_ns()

    def set_together(self, pins: Sequence[int], high: bool) -> tuple[int, int]:
        """Several lines in one `set_values` call, bracketed. That libgpiod changes them in
        one register write is [unverified]."""
        value = self._value(high)
        before = raw_ns()
        self._request.set_values({pin: value for pin in pins})
        return before, raw_ns()

    def release(self) -> None:
        """Every line low, then the request released, whatever the low raises."""
        try:
            self._request.set_values({pin: self._inactive for pin in self.pins})
        finally:
            self._request.release()


@dataclass
class Event:
    """One driven rise and what each stamp machine pushed for it.

    `rise_before` is RAW before the rise's write. `rise_after` is RAW after libgpiod's
    call returned, after the rise was read back through the register map
    (`rise_landed`), or, for a writer pulse (`writer`), after its SET's read-back: a
    read-back bracket holds the rise's landing, libgpiod's unread bracket only its issue
    (review N6; fix round 4).
    `fall_before` and `fall_after` bracket the fall's write; for a writer pulse
    `fall_after` is RAW after the read that follows the fall (`PulseWriter.pulse`).
    `drained_at` is RAW when the FIFOs were read. Ticks and RAW are integers."""

    index: int
    pins: tuple[int, ...]
    shape: str
    rise_before: int
    rise_after: int | None
    fall_before: int | None
    fall_after: int | None
    rise_landed: bool
    drained_at: int
    stamps: dict[int, list[int]]
    writer: bool = False
    # The fall read back through the register map (`fall_landed`), and a writer pulse's
    # two read-back costs in RAW ns (`PulseBounds`).
    fall_landed: bool = False
    reads: tuple[int, int] | None = None
    # The width meter's counts drained with this event (fix round 6): one for each high on
    # the dense pin, none for any other; `meter.width_ns` gives each width.
    widths: list[int] = field(default_factory=list)

    @property
    def bounded(self) -> bool:
        """Both ends of the high read back, so `upper_ns` and `lower_ns` are bounds: every
        writer pulse, and a libgpiod high on the dense pin (fix round 4)."""
        return self.writer or (self.rise_landed and self.fall_landed)

    @property
    def upper_ns(self) -> int | None:
        """The most the high can have lasted: a bound where the fall was read back (a
        writer pulse, or `fall_landed`; see `bounded`), and otherwise short of the fall's
        posting. None for a rise left high."""
        if self.fall_after is None:
            return None
        return self.fall_after - self.rise_before

    @property
    def lower_ns(self) -> int | None:
        """The least the high can have lasted: from the rise read back (a libgpiod rise's
        `rise_landed`, a writer SET's read-back) to before the fall was written, which
        lands after that. None for a libgpiod rise not read back."""
        if (not (self.rise_landed or self.writer) or self.fall_before is None
                or self.rise_after is None):
            return None
        return self.fall_before - self.rise_after

    @property
    def bracket_ns(self) -> int:
        """The width of the bracket the rise lies in: the fit's noise, and what
        BRACKET_WIDTH_MAX_NS bounds."""
        end = self.rise_after if self.rise_after is not None else self.fall_after
        assert end is not None  # a rise has one or the other
        return end - self.rise_before

    @property
    def rise_ns_x2(self) -> int:
        """Twice the rise's RAW estimate, an integer: its bracket's midpoint."""
        if self.rise_after is None:
            return 2 * self.rise_before
        return self.rise_before + self.rise_after


@dataclass
class Tally:
    """What the drains found that is not a stamp: code words (with nothing wired, any is a
    finding), full FIFOs, slips (`pair_stamps`), failed level reads, and how long each
    drain took (nobody has measured what a piolib FIFO call costs on RP1)."""

    words: int = 0
    full: int = 0
    slips: int = 0
    level_errors: int = 0
    drain_ns: list[int] = field(default_factory=list)


class Rig:
    """The block, the lines and, on B3b, the register writer, driven from one thread:
    each rise is followed by SETTLE_NS and a drain of every machine, so every stamp is
    the edge's own. `Rig.open` builds it in the order that refuses before touching RP1."""

    def __init__(self, block: Block, lines: Lines, writer: PulseWriter | None = None) -> None:
        self.block = block
        self.lines = lines
        self.writer = writer
        self.tally = Tally()
        self._carry: dict[int, list[int]] = {sm: [] for sm in block.stamp_sms}
        self._events = 0
        self._closed = False
        # Called with every event as it is made, the running check's included: the
        # bench's --record.
        self.on_event: Callable[[Event], None] | None = None
        self._widths: list[int] = []

    @classmethod
    def open(
        cls,
        chip_path: str,
        stamp_pins: Sequence[int],
        dense_pin: int | None = None,
        load: Callable[[], object] | None = None,
        open_map: Callable[[str], Gpiomem] | None = None,
    ) -> Rig:
        """The lines (refusing any another consumer holds), then on B3b the writer on
        `dense_pin`, opened and verified against libgpiod, then the block. A failure
        closes what is open, in reverse."""
        for pin in stamp_pins:
            if pin != BARCODE_PIN and pin not in SPARE_PINS:
                raise ValueError(
                    f"a stamp machine reads GPIO{BARCODE_PIN} or a spare pin "
                    f"({', '.join(f'GPIO{p}' for p in SPARE_PINS)}), not GPIO{pin}"
                )
        lines = Lines(chip_path, sorted(set(stamp_pins)))
        writer = block = None
        try:
            if dense_pin is not None:
                writer = PulseWriter(dense_pin, open_map=open_map or Gpiomem)
                writer.open()
                writer.verify(lambda high: lines.set(dense_pin, high))
            block = Block(load)
            block.open(stamp_pins, meter_pin=dense_pin)
            if writer is not None:
                writer.check()
        except BaseException as error:
            try:
                _close_all([writer.close if writer else None,
                            block.close if block else None, lines.release])
            except BaseException as closing:  # noqa: BLE001 - the first error stays first
                error.add_note(f"and closing what was open raised: {closing!r}")
            raise
        return cls(block, lines, writer)

    def drain(self) -> dict[int, list[int]]:
        """Every machine's FIFO, the capture machine's first: its words counted, each
        stamp machine's paired across batches (`pair_stamps`, pio_capture.py:87-141) with
        a carried data word kept for the next drain. Returns the stamps by machine."""
        started = raw_ns()
        block, tally = self.block, self.tally
        self._count(block.read(block.capture_sm), capture=True)
        stamps: dict[int, list[int]] = {}
        for sm in block.stamp_sms:
            read = self._count(block.read(sm))
            found, self._carry[sm], slips = pair_stamps(self._carry[sm] + read.words,
                                                        CODE_DATA_COUNT)
            tally.slips += len(slips)
            stamps[sm] = found
        # The width meter's words, one a high, each ~count (wl_sync/rp1/meter.py).
        self._widths = []
        if block.meter_sm is not None:
            read = self._count(block.read(block.meter_sm))
            self._widths = [(~word) & 0xFFFF_FFFF for word in read.words]
        tally.drain_ns.append(raw_ns() - started)
        return stamps

    def _count(self, read: FifoRead, capture: bool = False) -> FifoRead:
        self.tally.full += read.full
        self.tally.level_errors += read.level_error is not None
        if capture:
            self.tally.words += len(read.words)
        return read

    def meter_width_ns(self, count: int) -> float:
        """A width meter count as the high's width in ns (wl_sync/rp1/meter.py)."""
        return meter.width_ns(count, self.block.clk_sys_hz)

    @property
    def meter_uncertainty_ns(self) -> float:
        return meter.uncertainty_ns(self.block.clk_sys_hz)

    def carried(self) -> int:
        """Data words still waiting for their counter: none, after a settle."""
        return sum(len(words) for words in self._carry.values())

    def _event(self, pins, shape, rise_before, rise_after, fall_before, fall_after,
               landed, writer: bool = False, fall_landed: bool = False,
               reads: tuple[int, int] | None = None) -> Event:
        wait_until(rise_before + SETTLE_NS)
        drained_at = raw_ns()
        stamps = self.drain()
        self._events += 1
        event = Event(self._events, tuple(pins), shape, rise_before, rise_after,
                      fall_before, fall_after, landed, drained_at, stamps, writer,
                      fall_landed, reads, self._widths)
        if self.on_event is not None:
            self.on_event(event)
        return event

    def _landed(self, pin: int, after: int) -> tuple[int, bool]:
        if self.writer is not None and pin == self.writer.pin:
            return self.writer.rise_landed(), True
        return after, False

    def _fallen(self, pin: int, after: int) -> tuple[int, bool]:
        if self.writer is not None and pin == self.writer.pin:
            return self.writer.fall_landed(), True
        return after, False

    def rise(self, pin: int, shape: str = "") -> Event:
        """A rise through libgpiod, left high for the caller to end; then the settle and
        a drain. A barcode frame's rises are driven so."""
        before, after = self.lines.set_bracketed(pin, True)
        after, landed = self._landed(pin, after)
        return self._event((pin,), shape, before, after, None, None, landed)

    def edge(self, pin: int, hold_ns: int, shape: str = "") -> Event:
        """A rise through libgpiod, held `hold_ns` from when it is known to be in, a fall,
        then the settle from the rise and a drain."""
        before, after = self.lines.set_bracketed(pin, True)
        after, landed = self._landed(pin, after)
        wait_until(after + hold_ns)
        fall_before, fall_after = self.lines.set_bracketed(pin, False)
        fall_after, fallen = self._fallen(pin, fall_after)
        return self._event((pin,), shape, before, after, fall_before, fall_after, landed,
                           fall_landed=fallen)

    def short(self, hold_ns: int, shape: str = "") -> Event:
        """A high on the dense pin from the register writer (`PulseWriter.pulse`)."""
        if self.writer is None:
            raise HarnessError("no register writer: the rig was opened without a dense pin")
        bounds = self.writer.pulse(hold_ns)
        return self._event((self.writer.pin,), shape, bounds.before, bounds.set_after,
                           bounds.clear_before, bounds.after, False, writer=True,
                           reads=(bounds.set_read_ns, bounds.clear_read_ns))

    def marker(self, pins: Sequence[int], fall_pin: int, hold_ns: int) -> Event:
        """Every pin in `pins` rising in one `set_values`, then `fall_pin` alone falling
        `hold_ns` later; the others stay high for the caller to drive on."""
        before, after = self.lines.set_together(pins, True)
        after, landed = self._landed(fall_pin, after)
        wait_until(after + hold_ns)
        fall_before, fall_after = self.lines.set_bracketed(fall_pin, False)
        fall_after, fallen = self._fallen(fall_pin, fall_after)
        return self._event(pins, "marker", before, after, fall_before, fall_after, landed,
                           fall_landed=fallen)

    def running_check(self, pin: int) -> str:
        """Whether every stamp machine on `pin` stamps a RUNNING_CHECK_HIGH_NS pulse: ""
        when it does, and otherwise why not. The start-up test's two-pulse logic
        (backend.py:847-858), because the shim cannot report a refused enable
        (pio/wlsync_pio.c:252-255): on a miss every machine is disabled, those on `pin`
        are enabled alone, the plain way, and the pin pulsed again. A stamp then reads as
        a refused sync enable, none as a pin the PIO does not see. Either ends the run:
        the machines no longer share one enable."""
        watching = [sm for sm, jmp in self.block.pins.items() if jmp == pin]
        event = self.edge(pin, RUNNING_CHECK_HIGH_NS, "running check")
        if all(len(event.stamps[sm]) == 1 for sm in watching):
            return ""
        self.block.disable_all()
        self.block.enable_alone(watching)
        wait_until(raw_ns() + SETTLE_NS)
        self.drain()
        event = self.edge(pin, RUNNING_CHECK_HIGH_NS, "running check alone")
        stamped = [sm for sm in watching if event.stamps[sm]]
        if len(stamped) == len(watching):
            return SYNC_REFUSED.format(pin=pin)
        if not stamped:
            return BLIND.format(pin=pin)
        return (f"of the machines on GPIO{pin}, only SM {stamped} stamped its rise, "
                "enabled in sync or alone")

    def close(self) -> None:
        """The writer restored first, while libgpiod still holds the pin low; then every
        machine disabled and the block closed; then the lines low and released. Every
        step runs whatever the one before raised, and the first error is raised after.
        Once only."""
        if self._closed:
            return
        self._closed = True
        _close_all([self.writer.close if self.writer else None, self.block.close,
                    self.lines.release])


def _declare_meter(lib) -> None:
    """Version 6's `wlsync_start_meter` (pio/wlsync_pio.c, at its end), bound here, not in
    backend.py's `_declare`: the recording path never calls it. Scalars and the words
    only, as every export."""
    lib.wlsync_start_meter.argtypes = [
        ctypes.POINTER(ctypes.c_uint16), ctypes.c_uint,   # instructions, length
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,      # wrap_target, wrap, entry
        ctypes.c_uint, ctypes.c_float,                    # jmp_pin, clkdiv
    ]
    lib.wlsync_start_meter.restype = ctypes.c_int


def _close_all(steps: Sequence[Callable[[], None] | None]) -> None:
    first: BaseException | None = None
    for step in steps:
        if step is None:
            continue
        try:
            step()
        except BaseException as error:  # noqa: BLE001 - every step runs; the first is raised
            first = first or error
    if first is not None:
        raise first



# ---------------------------------------------------------------------------
# The override left set: seeing it, and clearing it with no sudo (review I3)
# ---------------------------------------------------------------------------


def _spare(pin: int) -> None:
    if type(pin) is not int or pin not in SPARE_PINS:
        raise ValueError(
            f"a spare pin ({', '.join(f'GPIO{p}' for p in SPARE_PINS)}), not GPIO{pin}"
        )


def read_overrides(
    pins: Sequence[int], open_map: Callable[[str], Gpiomem] | None = None,
) -> list[tuple[int, int, int, int, int]]:
    """(pin, CTRL, STATUS, OUTOVER, OEOVER) for each spare pin, READ ONLY: the post-check
    after a run, since pinctrl does not print the override fields [inferred]. It does not
    check the map against libgpiod, which would mean driving the line: the run that wrote
    checked it."""
    for pin in pins:
        _spare(pin)
    regs = (open_map or Gpiomem)(GPIOMEM_PATH)
    try:
        found = []
        for pin in pins:
            ctrl = regs.read32(ctrl_offset(pin))
            found.append((pin, ctrl, regs.read32(status_offset(pin)),
                          (ctrl & OUTOVER_MASK) >> 12, (ctrl & OEOVER_MASK) >> 14))
        return found
    finally:
        regs.close()


def clear_override(
    pin: int, chip_path: str, open_map: Callable[[str], Gpiomem] | None = None,
) -> str:
    """Clear an OUTOVER left set on one spare pin, with no sudo: the recovery for a run
    killed between a pulse's two stores. Returns what it did.

    THE SAME RULES AS THE WRITER. libgpiod requests the line first, low, under CONSUMER,
    so a line another consumer holds is refused before anything is mapped (`Lines`).
    Then CTRL must show SYS_RIO and no output-enable override. THE MAP IS CHECKED BEFORE
    THE WRITE: libgpiod toggles the line three times and STATUS.OUTFROMPERI, the funcsel's
    output before the override (Table 7, p.20-21), must follow it, with OEFROMPERI set.
    That works while OUTOVER still forces the pad, where `PulseWriter.verify`, which reads
    the pad, would not. Any refusal writes nothing. ONLY OUTOVER 3, the one value the
    writer sets, is cleared: 1 or 2 is someone else's override, refused with its value
    named (review N3), and 0 raises NothingToClear, so that clearing the wrong pin never
    reads as a success (N4). Then ONE write, OUTOVER's two bits through CTRL's CLEAR
    alias, and CTRL and STATUS are read back: OUTOVER 0, nothing else changed, the pad
    low."""
    _spare(pin)
    lines = Lines(chip_path, [pin])
    try:
        regs = (open_map or Gpiomem)(GPIOMEM_PATH)
        try:
            ctrl = regs.read32(ctrl_offset(pin))
            funcsel = ctrl & FUNCSEL_MASK
            if funcsel == FUNCSEL_PIO:
                raise WriterRefused(f"GPIO{pin} is the PIO's (FUNCSEL {FUNCSEL_PIO}): "
                                    "nothing written")
            if funcsel != FUNCSEL_SYS_RIO:
                raise WriterRefused(f"GPIO{pin} runs function {funcsel}, not SYS_RIO, with "
                                    "libgpiod holding it: nothing written")
            if ctrl & OEOVER_MASK:
                raise WriterRefused(f"GPIO{pin}'s output enable is overridden too (CTRL "
                                    f"{ctrl:#010x}), which this harness never sets: "
                                    "nothing written")
            outover = (ctrl & OUTOVER_MASK) >> 12
            if outover == 0:
                raise NothingToClear(f"GPIO{pin} has no override set (CTRL {ctrl:#010x}): "
                                     "nothing written. Is the run's dense pin the other one?")
            if outover != 3:
                raise WriterRefused(f"GPIO{pin} has OUTOVER {outover}, not the 3 this harness "
                                    f"writes (CTRL {ctrl:#010x}): someone else's override, "
                                    "nothing written")
            try:
                for _ in range(VERIFY_TOGGLES):
                    for high in (True, False):
                        lines.set(pin, high)
                        status = regs.read32(status_offset(pin))
                        if (bool(status & STATUS_OUTFROMPERI) != high
                                or not status & STATUS_OEFROMPERI):
                            raise WriterRefused(
                                f"GPIO{pin}'s STATUS through {GPIOMEM_PATH} does not follow "
                                f"the level libgpiod set (STATUS {status:#010x}), so the map "
                                "is not IO_BANK0 as assumed: nothing written"
                            )
            finally:
                lines.set(pin, False)
            _store(regs, pin, ctrl_offset(pin) + ALIAS_CLEAR, OUTOVER_MASK, (ALIAS_CLEAR,))
            now = regs.read32(ctrl_offset(pin))
            status = regs.read32(status_offset(pin))
        finally:
            regs.close()
    finally:
        lines.release()
    if now & OUTOVER_MASK or (now ^ ctrl) & ~(OUTOVER_MASK | IRQRESET):
        raise WriterError(f"GPIO{pin}'s CTRL reads {now:#010x} after the clear, from "
                          f"{ctrl:#010x}")
    if status & STATUS_OUTTOPAD:
        raise WriterError(f"GPIO{pin} is still high after the clear (STATUS {status:#010x})")
    return (f"GPIO{pin}: cleared OUTOVER {outover} (CTRL {ctrl:#010x} -> {now:#010x}); "
            "the pad reads low")
