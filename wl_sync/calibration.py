"""The SI rate of the one clock: the kernel's NTP state, the gate, and the estimator.

Every record sits on RP1's PIO count P. One rate r per process scales P to SI
microseconds; r is the slope of P against NTP-disciplined CLOCK_MONOTONIC (MONO),
measured over earlier processes and only over pairs this gate lets through. Nothing
here reads a clock; the only I/O is `probe_kernel_ntp`'s one adjtimex read and the
calibration file that carries r into the next process.
"""

import ctypes
from dataclasses import dataclass
from fractions import Fraction
from math import sqrt
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, ValidationError

from wl_sync.clock import write_durably
from wl_sync.clockmap import ClockPair

STA_PLL = 0x0001
STA_UNSYNC = 0x0040
STA_NANO = 0x2000

# A kernel whose phase offset exceeds this is still converging, so MONO is not yet SI to
# the precision the rate is quoted in.
KERNEL_OFFSET_MAX_NS = 1_000_000
# REALTIME stepping against MONO means someone (or NTP) stepped the wall clock; the
# discipline changed under the baseline, so slopes across the step mix two regimes.
REALTIME_STEP_NS = 1_000_000


class Timex(ctypes.Structure):
    """Linux's `struct timex` (include/uapi/linux/timex.h). Plain ctypes fields keep C's
    alignment, which is what the kernel's layout is."""

    _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long),
                ("status", ctypes.c_int), ("constant", ctypes.c_long),
                ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
                ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long),
                ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
                ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
                ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long),
                ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
                ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
                ("_reserved", ctypes.c_int * 11)]


@dataclass(frozen=True)
class KernelNtp:
    unsynchronized: bool
    pll: bool
    offset_ns: int


def kernel_ntp_from(timex: Timex) -> KernelNtp:
    # adjtimex(2): `offset` is in ns when STA_NANO is set in `status`, otherwise in us.
    offset = timex.offset if timex.status & STA_NANO else timex.offset * 1000
    return KernelNtp(unsynchronized=bool(timex.status & STA_UNSYNC),
                     pll=bool(timex.status & STA_PLL),
                     offset_ns=int(offset))


try:
    _libc = ctypes.CDLL(None, use_errno=True)
except OSError:
    _libc = None


def probe_kernel_ntp() -> KernelNtp | None:
    """The kernel's NTP state, or None where it cannot be asked. Never raises: the box
    records anyway, and an unread kernel only keeps pairs out of the SI rate. modes = 0
    is a read and needs no privilege."""
    try:
        adjtimex = getattr(_libc, "adjtimex", None)  # absent from macOS's libc
        if adjtimex is None:
            return None
        timex = Timex()
        timex.modes = 0
        if adjtimex(ctypes.byref(timex)) == -1:
            return None
        return kernel_ntp_from(timex)
    except Exception:
        return None


# What holds the gate, by name, for the journal (`gate_holds`; final review M4), in the
# order si_gate tests them. A kernel that cannot be read has none of the last three.
HELD_NTP_UNKNOWN = "NTP's state unknown"
HELD_NTP_UNSYNCED = "NTP not synchronised"
HELD_KERNEL_UNREAD = "the kernel's NTP state unreadable"
HELD_STA_UNSYNC = "STA_UNSYNC set"
HELD_NO_PLL = "no STA_PLL"
HELD_OFFSET = f"the kernel's offset over {KERNEL_OFFSET_MAX_NS / 1e6:g} ms"


def gate_holds(ntp_known: bool | None, kernel: KernelNtp | None) -> tuple[str, ...]:
    """Every condition, of NTP's state and the kernel's, that closes the gate, in
    si_gate's order: empty when only the bracket is left to judge. Named so a gate that
    has not opened in an hour can say why (wl_sync/cli.py `_RateKeeper`)."""
    held = []
    if ntp_known is None:
        held.append(HELD_NTP_UNKNOWN)
    elif ntp_known is not True:
        held.append(HELD_NTP_UNSYNCED)
    if kernel is None:
        held.append(HELD_KERNEL_UNREAD)
    else:
        if kernel.unsynchronized:
            held.append(HELD_STA_UNSYNC)
        if not kernel.pll:
            held.append(HELD_NO_PLL)
        if abs(kernel.offset_ns) > KERNEL_OFFSET_MAX_NS:
            held.append(HELD_OFFSET)
    return tuple(held)


def si_gate(ntp_known: bool | None, kernel: KernelNtp | None, pair: ClockPair) -> bool:
    """True only when MONO is known to be NTP-disciplined at this pair: NTP synced, a
    kernel read that is synchronized with its PLL on and a small offset, and a narrow
    bracket. Unknown is not good: None for either input closes the gate. Its conditions
    are those of `gate_holds`, so the journal names exactly what closed it."""
    return pair.narrow and not gate_holds(ntp_known, kernel)


@dataclass(frozen=True)
class RateEstimate:
    rate_ppb: int
    p_per_raw_ppb: int
    baseline_s: float
    pairs: int
    residual_rms_us: float


class _Sums:
    """Exact least-squares sums of y on x, centred by the first pair so the products stay
    small; exact Python ints, so the slope carries no accumulated rounding."""

    def __init__(self) -> None:
        self.n = 0
        self.x0 = self.y0 = 0
        self.sx = self.sy = self.sxx = self.sxy = self.syy = 0
        self.x_last = 0

    def add(self, x: int, y: int) -> None:
        if self.n == 0:
            self.x0, self.y0 = x, y
        x -= self.x0
        y -= self.y0
        self.n += 1
        self.sx += x
        self.sy += y
        self.sxx += x * x
        self.sxy += x * y
        self.syy += y * y
        self.x_last = x

    def _spread(self) -> tuple[int, int, int]:
        # n times the centred sums: n*Sxx, n*Sxy, n*Syy.
        n = self.n
        return (n * self.sxx - self.sx**2, n * self.sxy - self.sx * self.sy,
                n * self.syy - self.sy**2)

    def slope(self) -> Fraction | None:
        if self.n < 2:
            return None
        nsxx, nsxy, _ = self._spread()
        return Fraction(nsxy, nsxx) if nsxx else None

    def ppb(self) -> int | None:
        slope = self.slope()
        return None if slope is None else round(slope * 10**12 - 10**9)

    def rms(self) -> float:
        nsxx, nsxy, nsyy = self._spread()
        ssr = Fraction(nsyy) - Fraction(nsxy * nsxy, nsxx)  # n * residual sum of squares
        return sqrt(max(ssr, 0) / (self.n * self.n))


class RateEstimator:
    """The slope of P on MONO and on RAW. `si()` uses the gated pairs of the current
    baseline; `raw_ratio()` every narrow pair, gated or not. Wide pairs reach neither."""

    def __init__(self) -> None:
        # Not reset on a REALTIME step: a step does not move RAW (spec:477), and the
        # P/RAW ratio is a live witness (spec:313-316), so its baseline stays valid.
        self._all_raw = _Sums()
        self._reset()

    def _reset(self) -> None:
        self._gated_mono = _Sums()
        self._gated_raw = _Sums()
        self._last_step: int | None = None

    def add(self, pair: ClockPair, gated: bool) -> None:
        if not pair.narrow:
            return
        step = pair.realtime_minus_mono_ns
        if self._last_step is not None and abs(step - self._last_step) > REALTIME_STEP_NS:
            self._reset()
        self._last_step = step
        self._all_raw.add(pair.raw_ns, pair.p)
        if gated:
            self._gated_mono.add(pair.mono_ns, pair.p)
            self._gated_raw.add(pair.raw_ns, pair.p)

    def si(self) -> RateEstimate | None:
        rate = self._gated_mono.ppb()
        p_per_raw = self._gated_raw.ppb()
        if rate is None or p_per_raw is None:
            return None
        return RateEstimate(rate_ppb=rate, p_per_raw_ppb=p_per_raw,
                            baseline_s=self._gated_mono.x_last / 1e9,
                            pairs=self._gated_mono.n,
                            residual_rms_us=self._gated_mono.rms())

    def raw_ratio(self) -> tuple[int, float] | None:
        ppb = self._all_raw.ppb()
        if ppb is None:
            return None
        return ppb, self._all_raw.x_last / 1e9


# Beside the barcode checkpoint in the rig root (cli.py:465; spec Sec.2.2, "Persisting it").
RATE_FILE_NAME = ".wl-sync-pio-rate"
# A sanity bound, not a tolerance: a clk_sys that is not really 200 MHz, or a broken
# reference, lands outside it (spec Sec.2.2).
RATE_BOUND_PPB = 200_000
# The same kind of bound on the P/RAW ratio, which seeds the RAW->P fit
# (wl_sync/rp1/backend.py:1726), so a wrong one maps every E wrongly (final review M3).
# P/RAW is P/MONO, the rate, times MONO/RAW, and MONO differs from RAW only by NTP's slew,
# at most 500 ppm (wl_sync/clockmap.py:85). A file whose rate is within RATE_BOUND_PPB
# therefore has a ratio within about 700 ppm; 1000 ppm leaves margin, and refuses a ratio
# that is not a crystal's, such as the -10^9 ppb that flattens the fit's slope to 0.
P_PER_RAW_BOUND_PPB = 1_000_000

DEVICE_TREE_SERIAL = Path("/proc/device-tree/serial-number")
CPUINFO = Path("/proc/cpuinfo")


class Calibration(BaseModel):
    """What one process measured and the next applies. Strict on read: an unknown field
    means a file from some other version, which is not trusted to mean what we think."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal[1] = 1
    serial: str
    # Strict ints and finite floats: lax pydantic reads a hand-edited `true` as 1 and
    # "NaN" as a float, and either would be applied as if it were measured.
    rate_ppb: StrictInt
    p_per_raw_ppb: StrictInt
    baseline_s: float = Field(allow_inf_nan=False)
    pairs: StrictInt
    residual_rms_us: float = Field(allow_inf_nan=False)
    boot_id: str
    written_at: AwareDatetime


def read_calibration(path: Path, serial: str | None) -> tuple[Calibration | None, str]:
    """`(calibration, "")` when usable, else `(None, reason)`. Never raises: the box
    records anyway, at r = 1, and the reason ends the WARNING in spec Sec.2.2."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None, "no calibration file"
    except (OSError, ValueError) as error:  # ValueError: undecodable bytes
        return None, f"the calibration file is unreadable ({error})"
    try:
        calibration = Calibration.model_validate_json(text)
    except ValidationError as error:
        return None, f"the calibration file is unreadable ({error})"
    if serial is None:
        return None, "this board's serial cannot be read"
    if calibration.serial != serial:
        return None, (f"it is another board's (serial {calibration.serial}, "
                      f"this board {serial})")
    if abs(calibration.rate_ppb) > RATE_BOUND_PPB:
        return None, f"its rate, {calibration.rate_ppb:+d} ppb, is outside +-200 ppm"
    if abs(calibration.p_per_raw_ppb) > P_PER_RAW_BOUND_PPB:
        return None, (f"its P/RAW ratio, {calibration.p_per_raw_ppb:+d} ppb, is outside "
                      f"+-1000 ppm")
    return calibration, ""


def write_calibration(path: Path, calibration: Calibration) -> None:
    """Atomic and durable, exactly as the barcode checkpoint is (`write_durably`): a
    power cut must not leave a rate that parses but belongs to no measurement."""
    write_durably(path, calibration.model_dump_json() + "\n")


def probe_board_serial() -> str | None:
    """This board's serial number, or None where it cannot be read. Never raises.

    Verified on the box: spike S.6, 2026-11-07, on wl-sync-rig0 (a CM5 Lite, revision
    d041a0). `/proc/device-tree/serial-number` holds the serial followed by a NUL byte,
    so it is stripped of NULs; `/proc/cpuinfo`'s `Serial` line gives the same value.
    The device tree is tried first, cpuinfo as the fallback."""
    try:
        serial = DEVICE_TREE_SERIAL.read_bytes().decode("ascii", "ignore").strip("\x00 \t\r\n")
        if serial:
            return serial
    except OSError:
        pass
    try:
        for line in CPUINFO.read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "Serial" and value.strip():
                return value.strip()
    except (OSError, ValueError):
        pass
    return None
