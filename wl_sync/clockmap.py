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

from wl_sync.barcode import IDLE_MIN_US

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
# the frame's own later edges are >= 20 ms after the lead (spec §2.5 step 2), so while the
# map holds, a window this narrow cannot take one for it. Off by a rise's distance it
# could, which is why the lead is the stamp the frame's rises align on, each within this
# too (`match_lead_stamp`, Ruling R26).
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


# The span the table must cover before it extrapolates at its own slope (Ruling R31).
# MONO and RAW come from one counter and differ only by NTP's slew, at most 500 ppm, so
# slope 1 is never far off: 10 us over a 20 ms extrapolation at the very limit. A slope
# read off readings a second or more apart is better still. A slope read off two close
# readings is not: it turns each reading's error into an edge's error times distance over
# spacing, and one reading 1 us off, 100 us after the one before, put an edge 20 ms on
# 200 us out.
MONO_RAW_SPAN_MIN_NS = 1_000_000_000


class MonoRawTable:
    """MONO readings paired with RAW readings, newest `keep` of them.

    Called from one thread, the edge pump's, so it takes no lock. That is load-bearing:
    `add`'s pop-then-append and `raw_of`'s bisect over a deque that `add` mutates are not
    safe across threads. `add` never raises, and ignores a reading whose MONO is below
    the last (the caller may log it). A `provisional` reading stands in only until the
    next reading is added, which replaces it (Ruling R33).
    """

    def __init__(self, keep: int = 256) -> None:
        self._readings: deque[tuple[int, int]] = deque(maxlen=keep)
        self._provisional = False

    def add(self, mono_ns: int, raw_ns: int, *, provisional: bool = False) -> None:
        # A MONO that goes back would break `raw_of`'s search, but this runs on the edge
        # thread, where an exception ends edge capture (wl_sync/rp1/backend.py:1076-1079):
        # the box records anyway, so the reading is dropped. The caller may log it.
        if self._readings:
            last_mono, _ = self._readings[-1]
            if mono_ns < last_mono:
                return
            if mono_ns == last_mono or self._provisional:
                # Two readings at one MONO give no slope (a zero divisor below); the
                # later one is the better reading of the pair. A provisional reading is
                # the newest until this one replaces it.
                self._readings.pop()
        self._readings.append((mono_ns, raw_ns))
        self._provisional = provisional

    def raw_of(self, mono_ns: int) -> int:
        """Interpolates between the readings either side. Outside the table, before the
        first reading or after the last, it extrapolates from the nearer end at the slope
        across the whole table, first reading to last, or at slope 1 while the table
        spans less than MONO_RAW_SPAN_MIN_NS (Ruling R31; one reading spans none). With
        none it raises `LookupError`.

        NOT THE TWO NEAREST READINGS, as it was. An edge is nearly always stamped after
        the newest reading, which the edge thread takes before it waits, so nearly every
        edge is extrapolated, and the two newest readings can be a pass apart: under 1 ms
        when the edges come fast (MONO_RAW_SPAN_MIN_NS says what that cost)."""
        readings = self._readings
        if not readings:
            raise LookupError("no MONO/RAW reading yet")
        (first_mono, first_raw), (last_mono, last_raw) = readings[0], readings[-1]
        if first_mono <= mono_ns <= last_mono and len(readings) > 1:
            i = bisect_left(readings, mono_ns, key=lambda reading: reading[0])
            i = min(max(i, 1), len(readings) - 1)
            (mono0, raw0), (mono1, raw1) = readings[i - 1], readings[i]
            return raw0 + _round(
                Fraction((mono_ns - mono0) * (raw1 - raw0), mono1 - mono0)
            )
        nearer = (first_mono, first_raw) if mono_ns < first_mono else (last_mono, last_raw)
        mono0, raw0 = nearer
        span = last_mono - first_mono
        if span < MONO_RAW_SPAN_MIN_NS:
            return raw0 + (mono_ns - mono0)
        return raw0 + _round(Fraction((mono_ns - mono0) * (last_raw - first_raw), span))


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
    docs/superpowers/specs/2026-10-30-one-clock-design.md:511), against a window of a
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

    def restart(self, raw_ns: int, p: int) -> None:
        """Drop every pair and hold this one alone, so the line runs through it at the
        seed slope, as with one pair (the rule above). For a fit that has lost the lead
        edges, at any pair count (Ruling R19; wl_sync/rp1/backend.py `_reacquire`): no
        later pair could correct it, and past FIT_FREE_SLOPE_PAIRS one far pair barely
        moves a line fitted over FIT_WINDOW_PAIRS."""
        with self._lock:
            self._pairs.clear()
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
        (wl_sync/rp1/gpio.py:258-262): it would sort before every record in the segment.
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
    """What one frame made of the stamps queued since the last (spec §2.5; Ruling R26).

    `lead` is the stamp the frame's own rises align on (`match_lead_stamp`), or None when
    no alignment reached the minimum. `p` is that lead when it lies in the window: the lead
    edge's stamp, and the frame's B. None otherwise. `between` counts the stamps up to the
    frame's end that came before the lead, or, with no lead, before the window (spec §2.5
    step 4). `consumed` counts the rest up to the end: the frame's own later edges, and
    any stray among them (step 3). The RP1 backend adds to `between` the stamps carried
    from the frame before, which are never this frame's (Ruling R28). `leftover` holds the
    stamps after the frame's end. `nearest` is set for a lead outside the window, and for
    an alignment refused beyond `reach`: its distance from the bracket's nearer end, for
    the journal's "the stamp lies %d us from" cause."""

    p: int | None
    between: int
    consumed: int
    leftover: tuple[int, ...]
    nearest: int | None
    lead: int | None = None


def match_lead_stamp(
    stamps: Sequence[int],
    bracket: tuple[int, int],
    end: int,
    *,
    rises: Sequence[int],
    tolerance: int = LEAD_STAMP_TOLERANCE,
    reach: int = IDLE_MIN_US,
) -> LeadStamp:
    """Spec §2.5 steps 2-5, by the frame's own rises (Ruling R26). `stamps` are unwrapped
    and ascending, as the RP1 backend sorts them (wl_sync/rp1/backend.py:1344); nothing
    here sorts them. `bracket` is the lead edge's CPU bracket mapped to P, `end`
    the frame's end mapped to P, and `rises` where the frame's rising edges begin, in us
    from its start (`wl_sync.barcode.rises_us`).

    THE STAMPS ARE ALIGNED TO THE RISES AS A WHOLE. Each stamp up to the end is tried as
    the lead: the rises are laid out from it, and a rise is aligned when a stamp lies
    within `tolerance` of its place. The lead is the stamp of the alignment with the most
    rises aligned. Ties go to the one whose stamps lie closest to their places, then to a
    lead in the window, then to the lead nearer the bracket. An alignment counts only
    with every rise aligned but one, and never fewer than two: one stamp alone aligns on
    itself, so a frame that lost its lead would have a later edge taken for it.

    AN ALIGNMENT MORE THAN `reach` FROM THE BRACKET IS REFUSED (Ruling R28): IDLE_MIN_US,
    0.4 s: a lead that far before its bracket lies nearer the frame before's slot than
    its own, one a second (wl_sync/barcode.py:35-37). It never competes, so a frame
    before's whole pattern, drained late, cannot outrank this frame's own one rise
    short. Refused, it leaves no lead, and only its distance in `nearest`.

    WHY NOT THE WINDOW ALONE. Every rise after the lead comes at least 20 ms after it
    (wl_sync/barcode.py:5-7), so while the map is within the window, no later edge can
    pass for the lead. With the map off by a rise's distance, one did: a line 20 ms ahead
    of P put the second rise's stamp in the window, its B was written 20 ms off, and the
    fit anchored there. Aligned, the lead is the stamp the whole frame vouches for: with
    no stray stamp among them, and none carried from the frame before (which the backend
    keeps out, Ruling R28), no other stamp can become B, or anchor the fit through R19's
    restart. One fresh source remains: a frame before that a drain which gave up at
    STAMP_DRAIN_WAIT_S delivers late. With the line 0.6-1 s behind, its pattern lies in
    reach, and three such stalls in a row could restart the fit on it; on silicon the
    4-stamp FIFO and its held full flag narrow that further. With strays, two faults
    together can still pass: a stray within 1 ms of a lead that went unstamped, as any
    +-1 ms window allows, or one within the jitter of a lead that was stamped.

    A LEAD OUTSIDE THE WINDOW leaves `p` None and its distance in `nearest`: the frame is
    shifted, and its B is dropped (wl_sync/rp1/backend.py, `_reacquire`). With no
    alignment, `between` and `consumed` count by the window, as they did before it.
    """
    low, high = bracket[0] - tolerance, bracket[1] + tolerance
    frame = [stamp for stamp in stamps if stamp <= end]  # ascending, so a prefix
    leftover = tuple(stamp for stamp in stamps if stamp > end)
    need = len(rises) if len(rises) < 2 else max(2, len(rises) - 1)
    best: tuple[int, int, bool, int, int] | None = None
    beyond: tuple[int, int, bool, int, int] | None = None  # the best refused
    for index, candidate in enumerate(frame if rises else ()):
        aligned, off = 1, 0
        for rise in rises[1:]:
            place = candidate + rise - rises[0]
            at = bisect_left(frame, place)
            gap = min(
                (abs(frame[k] - place) for k in (at - 1, at) if 0 <= k < len(frame)),
                default=tolerance + 1,
            )
            if gap <= tolerance:
                aligned += 1
                off += gap
        if aligned < need:
            continue
        distance = max(bracket[0] - candidate, candidate - bracket[1], 0)
        key = (-aligned, off, not low <= candidate <= high, distance, index)
        if distance > reach:
            beyond = key if beyond is None or key < beyond else beyond
        else:
            best = key if best is None or key < best else best
    if best is None:
        between = sum(1 for stamp in frame if stamp < low)
        return LeadStamp(
            p=None, between=between, consumed=len(frame) - between, leftover=leftover,
            nearest=None if beyond is None else beyond[3],
        )
    index = best[-1]
    lead = frame[index]
    inside = low <= lead <= high
    return LeadStamp(
        p=lead if inside else None,
        between=index,
        consumed=len(frame) - index - 1,
        leftover=leftover,
        nearest=None if inside else max(bracket[0] - lead, lead - bracket[1], 0),
        lead=lead,
    )


@dataclass(frozen=True)
class TickScale:
    """P counts -> SI microseconds, as integer arithmetic.

    `rate_ppb` is r - 1 in parts per billion, where r is the PIO counts per SI us. Written
    ticks are integers (plan Global Constraints), so `tick` rounds `counts / r` half up
    with no float: `d = 10**9 + rate_ppb`, and (2 * counts * 10**9 + d) // (2 * d). It is
    monotone, so it cannot reorder records."""

    rate_ppb: int = 0

    def __post_init__(self) -> None:
        # d = 10**9 + rate_ppb is the divisor: at or below zero, `tick` divides by zero
        # or reverses the order of the records.
        if self.rate_ppb <= -(10**9):
            raise ValueError(f"rate_ppb {self.rate_ppb} leaves no positive divisor")

    def tick(self, counts: int) -> int:
        d = 10**9 + self.rate_ppb
        return (2 * counts * 10**9 + d) // (2 * d)


IDENTITY = TickScale(0)
