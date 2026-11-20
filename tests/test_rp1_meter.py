"""The bench's PIO width meter (wl_sync.rp1.meter), run in tests/piosim.py.

The meter is the user's ruling of 2026-11-20, "A PIO width meter": a harness-only program on
a spare state machine that counts while the dense pin is high and pushes the count at the
fall, so the PIO itself measures each high, and B3b can say which path through the capture
program the high took. These tests run its real instruction words, as
tests/test_rp1_program.py runs the capture program's.
"""

import pytest

from tests.piosim import M32, high_during, run
from wl_sync.rp1 import meter, program

# At clk_sys 200 MHz and CLKDIV 1, one PIO cycle is 5 ns.
CYCLE_NS = 5


def _measure(train, cycles=None):
    end = cycles if cycles is not None else train[-1][1] + 40
    result = run(meter.WIDTH_METER, entry=meter.ENTRY, wrap_target=meter.WRAP_TARGET,
                 wrap=meter.WRAP, cycles=end, strobe=high_during(train))
    return result, [(~word) & M32 for _, word in result.pushes]


def meter_count(rise, fall):
    """The count the meter pushes for a high on [rise, fall) in cycles, or None, from the
    rule the simulator gives (`test_the_fakes_rule_is_the_simulators`): the idle `jmp pin`
    samples on even cycles, so a rise is seen at the first even cycle at or after it, and
    the count is max(1, ceil((fall - seen - 1) / 2)), none when the pin fell first."""
    seen = rise + (rise % 2)
    if seen >= fall:
        return None
    return max(1, -(-(fall - seen - 1) // 2))


def test_the_meter_and_the_capture_program_fit_in_one_block_together():
    """Both programs, at their own offsets, in the 32 instructions of a PIO block
    (`pio_can_add_program`); the capture program is loaded first, as on the recording path,
    and is not changed."""
    assert len(program.STROBE_CAPTURE) + len(meter.WIDTH_METER) <= 32
    assert len(meter.WIDTH_METER) == 7


def test_every_jmp_x_dec_names_its_own_fall_through_so_the_count_wraps_in_place():
    """X reaching 0 makes a `jmp x--` fall through instead of jumping; each names the address
    it would fall through to (or, at WRAP, the wrap target), so the count wraps modulo 2^32,
    after 42.9 s of high, without the program leaving its path."""
    for address, word in enumerate(meter.WIDTH_METER):
        if word >> 13 == 0 and (word >> 5) & 7 == 2:
            fall_through = meter.WRAP_TARGET if address == meter.WRAP else address + 1
            assert word & 0x1F == fall_through, address


@pytest.mark.parametrize("width_us", [1, 2, 4, 5, 5.5, 6, 10, 1000])
@pytest.mark.parametrize("phase", [0, 1])
def test_each_high_is_measured_within_one_cycle(width_us, phase):
    """Highs of 1 us to 1 ms at both phases of the idle sampling: one push each, at the
    fall, whose count gives the width within one cycle, 5 ns (`meter.width_ns`)."""
    width = round(width_us * 1_000 / CYCLE_NS)
    rise = 100 + phase
    result, counts = _measure([(rise, rise + width)])
    assert len(counts) == 1
    estimate = meter.width_ns(counts[0], 200_000_000)
    assert abs(estimate - width * CYCLE_NS) <= meter.uncertainty_ns(200_000_000)
    [(pushed_at, _)] = result.pushes
    assert 0 < pushed_at - (rise + width) <= 4  # pushed 2-4 cycles after the fall


def test_two_highs_give_two_counts_and_a_low_gives_none():
    result, counts = _measure([(100, 300), (400, 410)])
    assert counts == [100, 5]
    result, counts = _measure([(100, 100)], cycles=500)
    assert counts == []


def test_the_fakes_rule_is_the_simulators():
    """The board fake (tests/test_rp1_harness.py) pushes `meter_count`'s count rather than
    running the simulator for every high; this pins the two together."""
    for rise in (100, 101):
        for width in range(1, 80):
            _, counts = _measure([(rise, rise + width)])
            expected = meter_count(rise, rise + width)
            assert counts == ([] if expected is None else [expected]), (rise, width)


def test_the_stated_resolution_and_range():
    assert meter.CLKDIV == 1
    assert meter.width_ns(1, 200_000_000) == 15  # 3 cycles: a count is 2 cycles, 10 ns
    assert meter.uncertainty_ns(200_000_000) == 5
    assert meter.width_ns(M32, 200_000_000) > 40 * 10**9  # 42.9 s before the count wraps
