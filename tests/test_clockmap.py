import random
from fractions import Fraction

import pytest

from wl_sync.clockmap import (
    BRACKET_WIDTH_MAX_NS,
    FIT_FREE_SLOPE_PAIRS,
    FIT_WINDOW_PAIRS,
    LEAD_STAMP_TOLERANCE,
    ClockPair,
    LeadStamp,
    MONO_RAW_SPAN_MIN_NS,
    MappedTimebase,
    MonoRawTable,
    IDENTITY,
    RawToP,
    TickScale,
    match_lead_stamp,
    unwrap_near,
)

S = 10**9
_PPB_50 = 10**9 + 50_000


def table_of(*readings, keep=256):
    table = MonoRawTable(keep)
    for mono, raw in readings:
        table.add(mono, raw)
    return table


def test_the_constants_are_the_briefs():
    assert (BRACKET_WIDTH_MAX_NS, FIT_WINDOW_PAIRS, FIT_FREE_SLOPE_PAIRS) == (100_000, 120, 30)


def test_a_pair_is_narrow_up_to_the_bracket_limit():
    def pair(width):
        return ClockPair(p=1, mono_ns=2, raw_ns=3, width_ns=width, realtime_minus_mono_ns=4)

    assert pair(BRACKET_WIDTH_MAX_NS).narrow
    assert not pair(BRACKET_WIDTH_MAX_NS + 1).narrow


def test_mono_is_carried_to_raw_by_interpolation():
    assert table_of((1_000, 5_000), (21_000, 25_010)).raw_of(11_000) == 15_005


# Three readings a second apart: 30 us of RAW gained in the first second, 10 us lost in the
# next, so the whole table runs 10 ppm fast and its last two readings 10 ppm slow.
_TWO_SECONDS = ((0, 0), (S, S + 30_000), (2 * S, 2 * S + 20_000))


def test_past_the_last_reading_mono_is_extrapolated_at_the_whole_tables_rate():
    """Ruling R31: at the slope from the first reading to the last, not the last two's."""
    assert table_of(*_TWO_SECONDS).raw_of(3 * S) == 2 * S + 20_000 + S + 10_000


def test_before_the_first_reading_mono_is_extrapolated_at_the_whole_tables_rate():
    """Ruling R31: at the slope from the first reading to the last, not the first two's
    (30 ppm fast)."""
    assert table_of(*_TWO_SECONDS).raw_of(-S) == -S - 10_000


def test_with_one_reading_mono_and_raw_differ_by_its_offset():
    table = table_of((1_000, 5_000))
    assert table.raw_of(1_000) == 5_000
    assert table.raw_of(9_000) == 13_000
    assert table.raw_of(-500) == 3_500


def test_a_table_with_no_reading_refuses():
    with pytest.raises(LookupError):
        MonoRawTable().raw_of(1)


def test_the_table_keeps_only_its_newest_readings():
    """Ruling R31: what falls before the kept readings is extrapolated at their own
    slope, first to last, where it was the first two's."""
    # RAW keeps pace with MONO to the third reading, then gains 3 us a second.
    readings = [
        (0, 0), (S, S), (2 * S, 2 * S), (3 * S, 3 * S + 3_000),
        (4 * S, 4 * S + 6_000), (5 * S, 5 * S + 9_000),
    ]
    table = table_of(*readings, keep=4)
    # The oldest two are gone, so a second before the third reading is extrapolated at
    # the kept readings' 3 ppm.
    assert table.raw_of(S) == S - 3_000
    assert table_of(*readings).raw_of(S) == S


def test_noise_on_two_close_readings_moves_an_extrapolated_edge_by_no_more_than_the_noise():
    """Ruling R31. Two seconds of readings 20 ms apart on RAW = MONO, then one 100 us after
    the last, read 1 us off. An edge 20 ms later is that 1 us off, and 10 ns for the
    slope it bent over two seconds. At the slope of the last two readings it was 201 us
    off: 1 us over 100 us, carried 20 ms."""
    readings = [(k * 20_000_000, k * 20_000_000) for k in range(101)]
    close = 2 * S + 100_000
    table = table_of(*readings, (close, close + 1_000))
    edge = close + 20_000_000
    assert table.raw_of(edge) - edge == 1_000 + 10


def test_a_table_spanning_under_a_second_extrapolates_at_slope_one():
    """Ruling R31. Under MONO_RAW_SPAN_MIN_NS the nearer reading's offset alone, as with one
    reading: NTP's slew keeps MONO within 500 ppm of RAW. At the span, the table's own
    slope, here 5 ppm."""
    assert MONO_RAW_SPAN_MIN_NS == S
    short = table_of((0, 0), (S - 1, S - 1 + 5_000))
    assert short.raw_of(2 * S) == 2 * S + 5_000
    assert short.raw_of(-S) == -S
    assert table_of((0, 0), (S, S + 5_000)).raw_of(2 * S) == 2 * S + 10_000


def test_a_reading_that_goes_back_in_mono_is_ignored():
    table = table_of((10, 10), (20, 25))
    before = [table.raw_of(m) for m in (0, 5, 15, 30)]
    table.add(5, 500)  # no exception: the edge pump's thread must survive it
    assert [table.raw_of(m) for m in (0, 5, 15, 30)] == before


def test_a_repeated_mono_reading_replaces_the_last():
    table = table_of((0, 0), (10, 10), (10, 12))
    assert table.raw_of(5) == 6


def test_before_any_pair_raw_maps_through_the_origin_at_the_seeded_ratio():
    fit = RawToP(5_000_000, 50_000)
    assert fit.p_of(5_000_000) == 0
    assert fit.p_of(1_005_000_000) == 1_000_050


def test_under_thirty_pairs_the_fit_moves_only_its_offset():
    """Through the newest pair, at the seeded slope (Ruling R17; the centroid gave
    300 + 5_500_110)."""
    fit = RawToP(0)
    for k in range(1, 11):
        fit.add(k * S, 300 + k * 1_000_020)
    assert fit.p_of(10 * S) == 300 + 10_000_200
    assert fit.p_of(5_500_000_000) == 300 + 5_500_200
    assert fit.counts_per_ns() == Fraction(1, 1000)


@pytest.mark.parametrize("ppm", [100, 300, -300])
def test_an_uncalibrated_fit_matches_every_lead_edge_before_its_slope_is_freed(ppm):
    """Seed 0 on a box whose crystals are `ppm` apart, one frame a second, as the run
    loop and Task 1.10's emit_pulses drive it: bracket the lead edge, map it, match the
    stamp, add the pair. Through the centroid at the seed slope, the map's error at
    frame N+1 was the crystals' difference over (N+1)/2 s: past LEAD_STAMP_TOLERANCE
    before FIT_FREE_SLOPE_PAIRS (from frame 20 at 100 ppm), and then no lead matched
    again, so no pair came to correct it. Through the newest pair it is the difference
    over one period."""
    fit = RawToP(0)

    def p_at(raw_ns):  # the PIO count, `ppm` fast of RAW, in whole counts
        return raw_ns * (1_000_000 + ppm) // 1_000_000_000

    unmatched = []
    for frame in range(1, 61):
        raw_before = frame * S
        raw_after = raw_before + 20_000
        lead = p_at(raw_before + 10_000)
        found = match_lead_stamp(
            [lead],
            (fit.p_of(raw_before), fit.p_of(raw_after)),
            fit.p_of(raw_before + 200_000_000),
            rises=(0,),  # the lead alone: the fit is under test here, not the match
        )
        if found.p is None:
            unmatched.append(frame)
            continue
        fit.add((raw_before + raw_after) // 2, found.p)
    assert unmatched == []


def test_restart_drops_the_pairs_and_runs_its_line_through_one_pair_at_the_seed_slope():
    """Ruling R19: a fit that has lost the lead edges restarts at one, as if it held that
    pair alone (Task 1.2's rule), whatever it held before."""
    fit = RawToP(0)
    for k in range(1, 41):  # +40 ppm, and past FIT_FREE_SLOPE_PAIRS: the slope is free
        fit.add(k * S, 300 + k * 1_000_040)
    assert fit.counts_per_ns() == Fraction(1_000_040, 10**9)
    r, p = 50 * S + 7, 123_456_789
    fit.restart(r, p)
    assert fit.p_of(r + 1_000_000_000) == p + 1_000_000
    assert fit.counts_per_ns() == Fraction(1, 1000)
    fit.add(51 * S, 51_000_123)  # the newest pair, at the seed slope
    assert fit.p_of(52 * S) == 51_000_123 + 1_000_000
    assert fit.p_of(50 * S) == 51_000_123 - 1_000_000


def test_thirty_pairs_or_more_fit_their_own_slope():
    fit = RawToP(0)
    for k in range(1, 41):
        fit.add(k * S, 300 + k * 1_000_020)
    assert fit.p_of(41 * S) == 300 + 41 * 1_000_020
    assert fit.counts_per_ns() == Fraction(1_000_020, 10**9)


def test_only_the_newest_hundred_and_twenty_pairs_are_fitted():
    fit = RawToP(0)
    p = 0
    for k in range(1, 121):
        p += 1_000_020
        fit.add(k * S, p)
    for k in range(121, 241):
        p += 999_970
        fit.add(k * S, p)
    assert fit.counts_per_ns() == Fraction(999_970, 10**9)


def test_noisy_pairs_extrapolated_one_second_stay_within_four_times_the_design_figure():
    # spec §3: 0.18 sigma, about 1.6 us at sigma = 9 counts, is the design figure
    rng = random.Random(5)
    fit = RawToP(0)
    for k in range(1, 121):
        fit.add(k * S, round(k * 1_000_000 + rng.gauss(0, 9)))
    assert abs(fit.p_of(121 * S) - 121_000_000) <= 7


def test_an_edge_is_mapped_mono_to_raw_to_p():
    table = table_of((10 * S, 9 * S), (11 * S, 10 * S + 100))
    timebase = MappedTimebase(table, RawToP(8 * S), 8 * S)
    assert timebase.tick(10_500_000_000) == 1_500_000


def test_a_tick_wraps_to_thirty_two_bits():
    table = table_of((0, 0))
    timebase = MappedTimebase(table, RawToP(0), 0)
    assert timebase.tick(5_000_000_000_000) == 5_000_000_000 & 0xFFFF_FFFF


def test_an_edge_stamped_before_the_origin_is_refused():
    table = table_of((10 * S, 9 * S), (11 * S, 10 * S))
    timebase = MappedTimebase(table, RawToP(9 * S), 9 * S)
    with pytest.raises(ValueError):
        timebase.tick(10 * S - 1)


def test_an_hours_span_is_converted_at_the_current_ratio():
    # spec §2.3: "At 50 ppm an hour is 180 ms otherwise"
    timebase = MappedTimebase(MonoRawTable(), RawToP(0, 50_000), 0)
    assert timebase.span(3_600 * S + 5, 5) == 3_600_180_000


def test_a_span_that_runs_backwards_is_zero():
    timebase = MappedTimebase(MonoRawTable(), RawToP(0), 0)
    assert timebase.span(5, 10_000) == 0


def test_an_edge_with_no_mono_raw_reading_is_refused_as_a_value_error():
    timebase = MappedTimebase(MonoRawTable(), RawToP(0), 0)
    with pytest.raises(ValueError):
        timebase.tick(1)


def test_an_edge_before_p_zero_is_refused_not_wrapped():
    # one pair at RAW 20 s, P 5 000 000: at the seed slope the line is negative from
    # RAW 15 s back, though RAW 10 s is past the origin (0)
    fit = RawToP(0)
    fit.add(20 * S, 5_000_000)
    table = table_of((0, 0))
    timebase = MappedTimebase(table, fit, 0)
    assert fit.p_of(10 * S) < 0
    with pytest.raises(ValueError):
        timebase.tick(10 * S)


def test_thirty_pairs_at_one_raw_keep_the_seed_slope():
    """Coverage: the sxx == 0 guard keeps the seed slope; it passed before Task 1.2's fix round."""
    fit = RawToP(0, 50_000)
    for k in range(30):
        fit.add(S, 1_000 + k)
    assert fit.counts_per_ns() == Fraction(_PPB_50, 10**12)


def test_the_slope_is_free_at_exactly_thirty_pairs_and_not_at_twenty_nine():
    """Coverage: pins the 29/30 boundary of FIT_FREE_SLOPE_PAIRS; it passed before the fix round."""
    def fitted(n):
        fit = RawToP(0)
        for k in range(1, n + 1):
            fit.add(k * S, 300 + k * 1_000_020)
        return fit.counts_per_ns()

    assert fitted(29) == Fraction(1, 1000)
    assert fitted(30) == Fraction(1_000_020, 10**9)


B0 = 10_000_000
BRACKET = (B0, B0 + 20)
END = B0 + 220_000
# A frame of one rise: its stamp alone is its alignment, so the window decides, as it did
# alone before the match took the frame's rises (Ruling R26).
LONE = (0,)


def test_the_lead_stamp_tolerance_is_a_millisecond():
    assert LEAD_STAMP_TOLERANCE == 1_000


def test_the_first_stamp_in_the_bracket_is_the_lead_edge():
    got = match_lead_stamp(
        [B0 + 4, B0 + 20_004, B0 + 60_004], BRACKET, END, rises=(0, 20_000, 60_000)
    )
    assert got == LeadStamp(
        p=B0 + 4, between=0, consumed=2, leftover=(), nearest=None, lead=B0 + 4
    )


def test_a_stamp_within_the_tolerance_of_the_bracket_counts():
    assert match_lead_stamp([B0 - 900], BRACKET, END, rises=LONE).p == B0 - 900


def test_the_window_edges_are_inclusive():
    assert match_lead_stamp([B0 - 1_000], BRACKET, END, rises=LONE).p == B0 - 1_000
    assert match_lead_stamp([B0 + 20 + 1_000], BRACKET, END, rises=LONE).p == B0 + 1_020
    assert match_lead_stamp([B0 + 1_021], BRACKET, END, rises=LONE).p is None


def test_stamps_before_the_window_are_between_frames():
    got = match_lead_stamp([B0 - 5_000, B0 - 1_001, B0 + 4], BRACKET, END, rises=LONE)
    assert (got.between, got.p) == (2, B0 + 4)


def test_with_no_stamp_nothing_is_nearest():
    got = match_lead_stamp([], BRACKET, END, rises=LONE)
    assert (got.p, got.nearest, got.between, got.consumed, got.leftover) == (None, None, 0, 0, ())


def test_a_missed_lead_edge_aligns_no_lead_and_names_no_distance():
    """Its later edges alone: the rises align on neither as the lead, so nothing is the
    lead, and no distance is named (Ruling R26). The nearest stamp was named before, 20 ms
    off: a later edge, taken for a lead that was too far."""
    got = match_lead_stamp(
        [B0 + 20_020, B0 + 60_020], BRACKET, END, rises=(0, 20_000, 60_000)
    )
    assert (got.p, got.lead, got.nearest, got.consumed) == (None, None, None, 2)


def test_nearest_is_measured_from_the_nearer_end_of_the_bracket():
    # Before the bracket the nearer end is bracket[0]; the stamp 5_000 before it.
    assert match_lead_stamp([B0 - 5_000], BRACKET, END, rises=LONE).nearest == 5_000
    # Of two stamps, the closer one by either end.
    got = match_lead_stamp([B0 - 5_000, B0 + 20 + 3_000], BRACKET, END, rises=LONE)
    assert got.nearest == 3_000


def test_the_frames_end_is_inclusive_and_later_stamps_wait():
    got = match_lead_stamp([B0 + 4, END, END + 1], BRACKET, END, rises=LONE)
    assert (got.consumed, got.leftover) == (1, (END + 1,))


def test_stamps_after_the_frame_end_wait_for_the_next_frame():
    got = match_lead_stamp([B0 + 4, END + 1], BRACKET, END, rises=LONE)
    assert (got.leftover, got.consumed) == ((END + 1,), 0)


def test_a_stamp_after_the_end_is_not_nearest():
    got = match_lead_stamp([END + 1], BRACKET, END, rises=LONE)
    assert (got.p, got.nearest, got.leftover) == (None, None, (END + 1,))


# Ruling R26: the stamps aligned to the frame's own rises.
RISES = (0, 20_000, 60_000, 190_000)  # a frame's rises, us from its lead


def test_the_lead_is_the_stamp_the_frames_rises_align_on():
    """The line 20 ms ahead: the frame's second rise lands in the lead's window. The rises
    align on the stamp 20 ms before it, which is the lead, outside the window."""
    lead = B0 - 20_000
    got = match_lead_stamp([lead + r for r in RISES], BRACKET, END, rises=RISES)
    assert (got.p, got.lead, got.between, got.consumed) == (None, lead, 0, 3)
    assert got.nearest == 20_000


def test_all_but_one_rise_is_enough_and_two_short_is_not():
    stamps = [B0 + 4 + r for r in RISES]
    assert match_lead_stamp(stamps[:2] + stamps[3:], BRACKET, END, rises=RISES).p == B0 + 4
    got = match_lead_stamp(stamps[:1] + stamps[3:], BRACKET, END, rises=RISES)
    assert (got.p, got.lead, got.nearest) == (None, None, None)
    assert (got.between, got.consumed) == (0, 2)  # by the window, as with no lead


def test_a_lone_stamp_is_no_lead_for_a_frame_of_two_rises():
    """All the rises but one, of two, is the stamp alone, which aligns on itself: a frame
    that lost its lead would have its TRAIL taken for it. A lead needs a second stamp."""
    rises = (0, 190_000)
    trail = B0 + 4 + 190_000
    assert match_lead_stamp([trail], BRACKET, END, rises=rises).lead is None
    assert match_lead_stamp([B0 + 4, trail], BRACKET, END, rises=rises).p == B0 + 4


def test_of_two_alignments_the_closer_is_the_lead():
    """A stray 600 us before the lead, in the window: the rises align on both, within the
    tolerance, and on the lead more closely. The stray is a stamp between."""
    stamps = [B0 - 596] + [B0 + 4 + r for r in RISES]
    got = match_lead_stamp(stamps, BRACKET, END, rises=RISES)
    assert (got.p, got.between, got.consumed) == (B0 + 4, 1, 3)


def test_the_closer_alignment_wins_over_a_stray_nearer_the_bracket():
    """The lead stamped 0.5 ms after its bracket, and a stray at the bracket itself: the
    rises align on both, and on the lead more closely, though the stray lies nearer."""
    stamps = [B0 + 4] + [B0 + 504 + r for r in RISES]
    got = match_lead_stamp(stamps, BRACKET, END, rises=RISES)
    assert (got.p, got.between) == (B0 + 504, 1)


def test_more_rises_aligned_outweighs_the_window():
    """Value 0x0001_0000's rises lie at 0, 95 and 190 ms, so with the line 95 ms ahead its
    second rise lands in the window, and the rises align on it too, all but TRAIL: two of
    three, the minimum. They align on the frame's own lead with all three, outside the
    window: that is the lead, and the frame is shifted, not matched 95 ms off."""
    rises = (0, 95_000, 190_000)
    lead = B0 + 4 - 95_000
    got = match_lead_stamp([lead + r for r in rises], BRACKET, END, rises=rises)
    assert (got.p, got.lead) == (None, lead)


# Ruling R28: an alignment more than IDLE_MIN_US from its bracket is refused.


def test_an_alignment_beyond_the_idle_is_refused_and_its_distance_named():
    """Nearer another frame's slot than its own: refused as the lead, so the frame is not
    shifted, and its distance is named for the journal. At the idle itself it stands."""
    from wl_sync.barcode import IDLE_MIN_US

    beyond = B0 - IDLE_MIN_US - 1
    got = match_lead_stamp([beyond + r for r in RISES], BRACKET, END, rises=RISES)
    assert (got.p, got.lead, got.nearest) == (None, None, IDLE_MIN_US + 1)
    at = B0 - IDLE_MIN_US
    assert match_lead_stamp([at + r for r in RISES], BRACKET, END, rises=RISES).lead == at


def test_a_frames_own_alignment_in_reach_outranks_a_fuller_one_beyond_it():
    """A frame before's whole pattern, a second off, and this frame's own, one rise short:
    the one beyond reach never competes, so this frame's lead is its own."""
    stale = [B0 - 1_000_000 + r for r in RISES]
    own = [B0 + 4 + r for r in RISES]
    got = match_lead_stamp(stale + own[:2] + own[3:], BRACKET, END, rises=RISES)
    assert (got.p, got.between) == (B0 + 4, len(RISES))


def test_a_raw_count_is_unwrapped_to_the_one_nearest():
    assert unwrap_near(5, 2**32 - 3) == 2**32 + 5
    assert unwrap_near(2**32 - 3, 2**32 + 5) == 2**32 - 3


def test_a_scale_of_zero_ppb_leaves_counts_as_they_are():
    assert IDENTITY == TickScale(0)
    for n in (0, 1, 2**32 + 5, 10**12):
        assert TickScale(0).tick(n) == n


def test_a_fast_crystal_is_scaled_down_to_si_microseconds():
    assert TickScale(100_000).tick(1_000_100) == 1_000_000
    assert TickScale(-50_000).tick(999_950) == 1_000_000


def test_scaling_rounds_to_the_nearest_microsecond_and_never_reorders():
    for ppb in (-200_000, 37, 200_000):
        scale = TickScale(ppb)
        previous = -1
        for p in range(0, 5_000_000, 997):
            tick = scale.tick(p)
            assert tick >= previous
            previous = tick
            assert abs(tick - p / (1 + ppb / 1e9)) <= 0.5


def test_a_divisor_that_is_zero_or_negative_is_refused():
    for ppb in (-10**9, -10**9 - 1):
        with pytest.raises(ValueError):
            TickScale(ppb)


def test_scaling_is_exact_and_ordered_around_two_to_the_32_and_40():
    """Coverage: pins the integer arithmetic beyond 2**32, where P is unwrapped; it passed
    when written, because `tick` was already exact."""
    for ppb in (-200_000, 37, 200_000):
        scale = TickScale(ppb)
        r = 1 + Fraction(ppb, 10**9)
        for centre in (2**32, 2**40):
            previous = None
            for p in range(centre - 2_000, centre + 2_000, 7):
                tick = scale.tick(p)
                assert abs(tick - Fraction(p) / r) <= Fraction(1, 2)
                assert previous is None or tick >= previous
                previous = tick
