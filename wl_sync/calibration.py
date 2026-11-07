"""The SI rate of the one clock: the kernel's NTP state, the gate, and the estimator.

Every record sits on RP1's PIO count P. One rate r per process scales P to SI
microseconds; r is the slope of P against NTP-disciplined CLOCK_MONOTONIC (MONO),
measured over earlier processes and only over pairs this gate lets through. Nothing
here reads a clock or touches a file, except `probe_kernel_ntp`'s one adjtimex read.
"""

import ctypes
from dataclasses import dataclass
from fractions import Fraction
from math import sqrt

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


def si_gate(ntp_known: bool | None, kernel: KernelNtp | None, pair: ClockPair) -> bool:
    """True only when MONO is known to be NTP-disciplined at this pair: NTP synced, a
    kernel read that is synchronized with its PLL on and a small offset, and a narrow
    bracket. Unknown is not good: None for either input closes the gate."""
    return (ntp_known is True
            and kernel is not None
            and not kernel.unsynchronized
            and kernel.pll
            and abs(kernel.offset_ns) <= KERNEL_OFFSET_MAX_NS
            and pair.narrow)


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
