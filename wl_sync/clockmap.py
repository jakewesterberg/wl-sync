"""The edge map and the lead-stamp match (spec §2.3, §2.5). Pure -- no hardware.

The box's one clock is RP1's PIO count, "P". An edge arrives stamped by the kernel on
CLOCK_MONOTONIC ("MONO"), which is not P, so it is carried in two steps:

    MONO --MonoRawTable--> CLOCK_MONOTONIC_RAW ("RAW") --RawToP--> P

The first step is a table of paired readings, because MONO is slewed by NTP and RAW is
not: the two differ by a slowly changing rate, and a table interpolates it rather than
assuming it. The second is a least-squares fit of (RAW, P) pairs taken at the barcode's
lead edges, because RAW and P are two free-running oscillators whose ratio is a
property of this box and is only known by measuring it. Until it has pairs enough to
trust its own slope, it keeps the seeded one through the NEWEST pair, so its error at
the next frame is the seed's slope error over one frame period (see RawToP).

All the arithmetic is exact `Fraction`/int. A fit over nanosecond RAW values and
counts near 2**32 loses bits in a float. The fit is centred on the window's first pair
so the sums are of small ints and stay cheap; with exact arithmetic that does not
affect exactness, only cost.

P becomes SI microseconds once, where a tick becomes a record (spec §2.2): `TickScale`
divides the unwrapped count by one rate r, fixed for the process. One affine map adds no
curvature to the box's clock, so every interval keeps its P ratios and only the unit
changes; a second conversion, or a rate that moved mid-run, would bend the clock between
the records it separates. The identity until a calibration supplies r.
"""

from __future__ import annotations

import threading
from bisect import bisect_left
from collections.abc import Sequence
from collections import deque
from dataclasses import dataclass
from fractions import Fraction

# Spec §2.2 and §2.3 reject brackets wider than this; the value itself is not in the
# spec. Provisional until bench B5 measures what a bracket really costs.
BRACKET_WIDTH_MAX_NS = 100_000

# The newest pairs the fit uses, and how many it needs before it trusts its own slope.
# Below FIT_FREE_SLOPE_PAIRS the slope would be set by a few seconds of quantisation
# noise (spec §3), so the fit moves only its offset and keeps the seeded slope.
FIT_WINDOW_PAIRS = 120
FIT_FREE_SLOPE_PAIRS = 30

# Spec §2.5 step 2: the lead edge's stamp may lie this far outside its mapped CPU bracket
# ("±1 ms"), in P counts (1 count = 1 us at 1 MHz). The map is good to far less once warm;
# the frame's own later edges are >= 20 ms after the lead (spec §2.5 step 2), so a window
# this narrow cannot take one for it.
LEAD_STAMP_TOLERANCE = 1_000

_NS_PER_US = 1_000
_PPB = 1_000_000_000
_TICK_MASK = 0xFFFF_FFFF


@dataclass(frozen=True)
class ClockPair:
    """One simultaneous reading of the clocks: the P count of a lead edge against the
    MONO and RAW readings that bracket it. `mono_ns` and `raw_ns` are the bracket's
    midpoints, `width_ns` its width, and `realtime_minus_mono_ns` carries CLOCK_REALTIME
    along for the header."""

    p: int
    mono_ns: int
    raw_ns: int
    width_ns: int
    realtime_minus_mono_ns: int

    @property
    def narrow(self) -> bool:
        return self.width_ns <= BRACKET_WIDTH_MAX_NS


def _round(value: Fraction) -> int:
    return round(value)


class MonoRawTable:
    """MONO readings paired with RAW readings, newest `keep` of them.

    Called from one thread, the edge pump's, so it takes no lock. That is load-bearing:
    `add`'s pop-then-append and `raw_of`'s bisect over a deque that `add` mutates are not
    safe across threads. `add` never raises, and ignores a reading whose MONO is below
    the last (the caller may log it).
    """

    def __init__(self, keep: int = 256) -> None:
        self._readings: deque[tuple[int, int]] = deque(maxlen=keep)

    def add(self, mono_ns: int, raw_ns: int) -> None:
        # A MONO that goes back would break `raw_of`'s search, but this runs on the edge
        # pump's thread and an exception there fails edge capture (wl_sync/rp1/gpio.py:803-805);
        # the box records anyway, so the reading is dropped. The caller may log it.
        if self._readings:
            last_mono, _ = self._readings[-1]
            if mono_ns < last_mono:
                return
            if mono_ns == last_mono:
                # Two readings at one MONO give no slope (a zero divisor below); the
                # later one is the better reading of the pair.
                self._readings.pop()
        self._readings.append((mono_ns, raw_ns))

    def raw_of(self, mono_ns: int) -> int:
        """Interpolates between the readings either side. Outside the table it
        extrapolates from the two nearest readings; with one it applies that reading's
        offset only; with none it raises `LookupError`."""
        readings = self._readings
        if not readings:
            raise LookupError("no MONO/RAW reading yet")
        if len(readings) == 1:
            mono0, raw0 = readings[0]
            return raw0 + (mono_ns - mono0)
        # The pair whose line is used: the bracketing one inside the table, the nearest
        # two beyond either end.
        i = bisect_left(readings, mono_ns, key=lambda reading: reading[0])
        i = min(max(i, 1), len(readings) - 1)
        (mono0, raw0), (mono1, raw1) = readings[i - 1], readings[i]
        return raw0 + _round(Fraction((mono_ns - mono0) * (raw1 - raw0), mono1 - mono0))


class RawToP:
    """The line from RAW to P: seeded from the origin, then fitted to lead-edge pairs.

    `seed_ppb` is the P counts per RAW microsecond, as parts per billion above 1. The rule:

    - with no pair, the line runs through `(origin_raw_ns, 0)` at the seed slope;
    - with fewer than `FIT_FREE_SLOPE_PAIRS` pairs in the window, it runs through the
      newest pair at the seed slope;
    - with that many or more, it is the least-squares line over the newest
      `FIT_WINDOW_PAIRS`.

    THE NEWEST PAIR, NOT THE CENTROID (2026-11-07). The error at the next frame is
    then the seed's slope error over one frame period. Through the centroid it was
    that error over half the pairs' span, which grows with each pair until the slope is
    freed: with seed 0 and crystals some 67 ppm apart, at one frame a second, it passed
    LEAD_STAMP_TOLERANCE before the 30th pair. Then no lead edge matched again, so no
    pair came to correct it. What the centroid bought was the offset's noise averaged:
    a pair's own is 3-9 counts (spec §3,
    docs/superpowers/specs/2026-10-30-one-clock-design.md:507), against a window of a
    thousand either side.

    A lock, because `add` runs on the run loop's thread and `p_of` on all three.
    """

    def __init__(self, origin_raw_ns: int, seed_ppb: int = 0) -> None:
        self._origin_raw_ns = origin_raw_ns
        # counts per ns: 1 count per RAW us at seed 0, so 1/1000 per ns.
        self._seed_slope = Fraction(_PPB + seed_ppb, _PPB * _NS_PER_US)
        self._pairs: deque[tuple[int, int]] = deque(maxlen=FIT_WINDOW_PAIRS)
        self._lock = threading.Lock()
        # (slope, anchor_raw, anchor_p), rebuilt only when a pair arrives: `p_of` runs
        # for every edge and the fit sums over up to 120 pairs.
        self._line: tuple[Fraction, int, Fraction] = (self._seed_slope, origin_raw_ns, Fraction(0))

    def add(self, raw_ns: int, p: int) -> None:
        with self._lock:
            self._pairs.append((raw_ns, p))
            self._line = self._fit()

    def p_of(self, raw_ns: int) -> int:
        with self._lock:
            slope, anchor_raw, anchor_p = self._line
        return _round(anchor_p + slope * (raw_ns - anchor_raw))

    def counts_per_ns(self) -> Fraction:
        with self._lock:
            return self._line[0]

    def _fit(self) -> tuple[Fraction, int, Fraction]:
        pairs = self._pairs
        n = len(pairs)
        if n < FIT_FREE_SLOPE_PAIRS:
            # Through the newest pair, not the centroid: see the class docstring.
            newest_raw, newest_p = pairs[-1]
            return self._seed_slope, newest_raw, Fraction(newest_p)
        # Centred on the first pair: the sums below are of small differences, so they
        # stay exact and cheap however large the absolute RAW and P are.
        raw0, p0 = pairs[0]
        sum_x = sum(raw - raw0 for raw, _ in pairs)
        sum_y = sum(p - p0 for _, p in pairs)
        centroid_raw = raw0 + Fraction(sum_x, n)
        centroid_p = p0 + Fraction(sum_y, n)
        # Sxx and Sxy about the centroid, from the centred sums: Sxx = Σx² − (Σx)²/n.
        sxx = sum((raw - raw0) ** 2 for raw, _ in pairs) - Fraction(sum_x**2, n)
        sxy = sum((raw - raw0) * (p - p0) for raw, p in pairs) - Fraction(sum_x * sum_y, n)
        if sxx == 0:
            # Every pair at one RAW: no slope to read, so keep the seeded one.
            return self._seed_slope, centroid_raw, centroid_p
        return sxy / sxx, centroid_raw, centroid_p


class MappedTimebase:
    """Edges on P, from kernel MONO stamps through the table and the fit."""

    def __init__(self, table: MonoRawTable, fit: RawToP, origin_raw_ns: int) -> None:
        self._table = table
        self._fit = fit
        self._origin_raw_ns = origin_raw_ns

    def tick(self, timestamp_ns: int) -> int:
        """An edge's P count, wrapped to 32 bits like every tick in this package.

        Refuses an edge whose RAW precedes the origin, as `edge_tick_us` does
        (wl_sync/rp1/gpio.py:223-227): it would sort before every record in the segment.
        """
        try:
            raw_ns = self._table.raw_of(timestamp_ns)
        except LookupError as error:
            # One exception type for "this edge cannot be mapped": the edge witness
            # skips on ValueError, and an empty table is the same kind of case.
            raise ValueError(f"edge timestamp {timestamp_ns} cannot be mapped: {error}") from error
        if raw_ns < self._origin_raw_ns:
            raise ValueError(
                f"edge timestamp {timestamp_ns} maps to RAW {raw_ns}, which precedes "
                f"the timebase origin {self._origin_raw_ns}; "
                "it would sort before every record in the segment"
            )
        p = self._fit.p_of(raw_ns)
        if p < 0:
            # An edge before P's zero, like a pre-origin one; wrapped it would be about
            # 4.29e9 and sort after every record in the segment.
            raise ValueError(f"edge timestamp {timestamp_ns} maps to P {p}, before P's zero")
        return p & _TICK_MASK

    def span(self, after_ns: int, before_ns: int) -> int:
        """P counts between two MONO stamps, at the fit's current ratio (spec §2.3:
        "converted at the current P/RAW ratio"). Not a difference of two mapped ticks,
        which would mix two interpolations: at 50 ppm an hour is 180 ms otherwise."""
        return max(0, round((after_ns - before_ns) * self._fit.counts_per_ns()))


def unwrap_near(raw: int, near: int) -> int:
    """The integer congruent to `raw` mod 2**32 that lies nearest `near`. A stamp is a
    32-bit count; the bracket it is matched against is not wrapped. A tie (exactly half
    a wrap away) goes to the lower."""
    modulus = _TICK_MASK + 1
    return raw + modulus * _round_half_down(Fraction(near - raw, modulus))


def _round_half_down(value: Fraction) -> int:
    # `round` goes to even on a tie, which would make `unwrap_near`'s tie depend on the
    # parity of the wrap count; the lower is the same answer everywhere.
    floor = value.__floor__()
    return floor + 1 if value - floor > Fraction(1, 2) else floor


@dataclass(frozen=True)
class LeadStamp:
    """What one frame made of the stamps queued since the last. `p` is the lead edge's
    stamp, or None if none lay in the window. `between` counts stamps before the window
    (spec §2.5 step 4), `consumed` the frame's own later edges (step 3), `leftover` the
    stamps after the frame's end, which belong to the next. `nearest` is set only when
    `p` is None: the distance from the bracket's nearer end to the closest stamp at or
    before the frame's end, for the journal's "the stamp lies %d us from" cause."""

    p: int | None
    between: int
    consumed: int
    leftover: tuple[int, ...]
    nearest: int | None


def match_lead_stamp(
    stamps: Sequence[int],
    bracket: tuple[int, int],
    end: int,
    tolerance: int = LEAD_STAMP_TOLERANCE,
) -> LeadStamp:
    """Spec §2.5 steps 2-5. `stamps` are unwrapped and ascending, as the stamp drain
    queues them; nothing here sorts them, and `p` is the first in-window stamp in list
    order. `bracket` is the lead edge's CPU bracket mapped to P and `end` the frame's
    end mapped to P."""
    low, high = bracket[0] - tolerance, bracket[1] + tolerance
    # The window is tested before `end`, so a window stamp past `end` would be taken as
    # `p`. No real frame allows it: its later edges come >= 20 ms after the lead
    # (wl_sync/barcode.py:5-7), so `end` lies far beyond `high`.
    p: int | None = None
    between = consumed = 0
    leftover: list[int] = []
    nearest: int | None = None
    for stamp in stamps:
        if p is None and low <= stamp <= high:
            p = stamp
        elif stamp > end:
            leftover.append(stamp)
        else:
            # Past the end is the next frame's, so it is never the nearest (§2.5 step 2
            # takes stamps "queued since the previous frame ended"). Distance is to the
            # bracket's nearer end; inside the bracket it is zero, though a stamp there
            # would have been `p`.
            gap = max(bracket[0] - stamp, stamp - bracket[1], 0)
            nearest = gap if nearest is None else min(nearest, gap)
            if stamp < low:
                between += 1
            else:
                # The frame's own later edges are consumed whether or not the lead was
                # found (spec §2.5 step 3, "every other stamp ... to the frame's end").
                consumed += 1
    return LeadStamp(
        p=p,
        between=between,
        consumed=consumed,
        leftover=tuple(leftover),
        nearest=None if p is not None else nearest,
    )


@dataclass(frozen=True)
class TickScale:
    """P counts -> SI microseconds, as integer arithmetic.

    `rate_ppb` is r - 1 in parts per billion, where r is the PIO counts per SI us. Written
    ticks are integers (plan Global Constraints), so `tick` rounds `counts / r` half up
    with no float: `d = 10**9 + rate_ppb`, and (2 * counts * 10**9 + d) // (2 * d). It is
    monotone, so it cannot reorder records."""

    rate_ppb: int = 0

    def tick(self, counts: int) -> int:
        d = 10**9 + self.rate_ppb
        return (2 * counts * 10**9 + d) // (2 * d)


IDENTITY = TickScale(0)
