import random
from fractions import Fraction

import pytest

from wl_sync.clockmap import (
    BRACKET_WIDTH_MAX_NS,
    FIT_FREE_SLOPE_PAIRS,
    FIT_WINDOW_PAIRS,
    ClockPair,
    MappedTimebase,
    MonoRawTable,
    RawToP,
)

S = 10**9


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


def test_a_reading_that_goes_back_in_mono_is_refused():
    table = table_of((10, 10))
    with pytest.raises(ValueError):
        table.add(5, 5)


def test_a_repeated_mono_reading_replaces_the_last():
    table = table_of((0, 0), (10, 10), (10, 12))
    assert table.raw_of(5) == 6


def test_before_any_pair_raw_maps_through_the_origin_at_the_seeded_ratio():
    fit = RawToP(5_000_000, 50_000)
    assert fit.p_of(5_000_000) == 0
    assert fit.p_of(1_005_000_000) == 1_000_050


def test_under_thirty_pairs_the_fit_moves_only_its_offset():
    fit = RawToP(0)
    for k in range(1, 11):
        fit.add(k * S, 300 + k * 1_000_020)
    assert fit.p_of(5_500_000_000) == 300 + 5_500_110
    assert fit.counts_per_ns() == Fraction(1, 1000)


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
