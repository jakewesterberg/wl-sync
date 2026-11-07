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
    MappedTimebase,
    MonoRawTable,
    RawToP,
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


def test_past_the_last_reading_mono_is_extrapolated_at_the_last_rate():
    assert table_of((1_000, 5_000), (21_000, 25_010)).raw_of(31_000) == 35_015


def test_before_the_first_reading_mono_is_extrapolated_at_the_first_rate():
    table = table_of((1_000, 5_000), (21_000, 25_010), (41_000, 45_000))
    # the first two readings run at 20_010 / 20_000, not the later 19_990 / 20_000
    assert table.raw_of(-9_000) == 5_000 - 10_005


def test_with_one_reading_mono_and_raw_differ_by_its_offset():
    table = table_of((1_000, 5_000))
    assert table.raw_of(1_000) == 5_000
    assert table.raw_of(9_000) == 13_000
    assert table.raw_of(-500) == 3_500


def test_a_table_with_no_reading_refuses():
    with pytest.raises(LookupError):
        MonoRawTable().raw_of(1)


def test_the_table_keeps_only_its_newest_readings():
    # rates: 1.0 up to the third reading, then 2.0, 3.0, 4.0, 5.0
    readings = [(0, 0), (10, 10), (20, 20), (30, 40), (40, 70), (50, 110)]
    table = table_of(*readings, keep=4)
    # the oldest two are gone, so the first rate is the third-to-fourth one, 2.0
    assert table.raw_of(10) == 20 - 20
    assert table_of(*readings).raw_of(10) == 10


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
        )
        if found.p is None:
            unmatched.append(frame)
            continue
        fit.add((raw_before + raw_after) // 2, found.p)
    assert unmatched == []


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


def test_the_lead_stamp_tolerance_is_a_millisecond():
    assert LEAD_STAMP_TOLERANCE == 1_000


def test_the_first_stamp_in_the_bracket_is_the_lead_edge():
    got = match_lead_stamp([B0 + 4, B0 + 20_004, B0 + 60_004], BRACKET, END)
    assert got == LeadStamp(p=B0 + 4, between=0, consumed=2, leftover=(), nearest=None)


def test_a_stamp_within_the_tolerance_of_the_bracket_counts():
    assert match_lead_stamp([B0 - 900], BRACKET, END).p == B0 - 900


def test_the_window_edges_are_inclusive():
    assert match_lead_stamp([B0 - 1_000], BRACKET, END).p == B0 - 1_000
    assert match_lead_stamp([B0 + 20 + 1_000], BRACKET, END).p == B0 + 1_020
    assert match_lead_stamp([B0 + 1_021], BRACKET, END).p is None


def test_stamps_before_the_window_are_between_frames():
    got = match_lead_stamp([B0 - 5_000, B0 - 1_001, B0 + 4], BRACKET, END)
    assert (got.between, got.p) == (2, B0 + 4)


def test_with_no_stamp_nothing_is_nearest():
    got = match_lead_stamp([], BRACKET, END)
    assert (got.p, got.nearest, got.between, got.consumed, got.leftover) == (None, None, 0, 0, ())


def test_a_missed_lead_edge_names_the_nearest_stamp_after_it():
    got = match_lead_stamp([B0 + 20_020, B0 + 60_020], BRACKET, END)
    assert (got.p, got.nearest, got.consumed) == (None, 20_000, 2)


def test_nearest_is_measured_from_the_nearer_end_of_the_bracket():
    # Before the bracket the nearer end is bracket[0]; the stamp 5_000 before it.
    assert match_lead_stamp([B0 - 5_000], BRACKET, END).nearest == 5_000
    # Of two stamps, the closer one by either end.
    got = match_lead_stamp([B0 - 5_000, B0 + 20 + 3_000], BRACKET, END)
    assert got.nearest == 3_000


def test_the_frames_end_is_inclusive_and_later_stamps_wait():
    got = match_lead_stamp([B0 + 4, END, END + 1], BRACKET, END)
    assert (got.consumed, got.leftover) == (1, (END + 1,))


def test_stamps_after_the_frame_end_wait_for_the_next_frame():
    got = match_lead_stamp([B0 + 4, END + 1], BRACKET, END)
    assert (got.leftover, got.consumed) == ((END + 1,), 0)


def test_a_stamp_after_the_end_is_not_nearest():
    got = match_lead_stamp([END + 1], BRACKET, END)
    assert (got.p, got.nearest, got.leftover) == (None, None, (END + 1,))


def test_a_raw_count_is_unwrapped_to_the_one_nearest():
    assert unwrap_near(5, 2**32 - 3) == 2**32 + 5
    assert unwrap_near(2**32 - 3, 2**32 + 5) == 2**32 - 3
