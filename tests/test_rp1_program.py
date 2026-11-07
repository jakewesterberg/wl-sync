"""The strobe-capture program as instruction words.

Encodings are from the RP2040 PIO instruction set, which piolib's own
`pio_instr_bits` and `pio_src_dest` enums confirm (jmp=0x0000, in=0x4000,
push=0x8000, mov=0xa000; pins=0, x=1, y=2, null=3, isr=6, osr=7). They are
cross-checked against the C `pio_encode_*` functions on the bench -- see
`assert_agrees_with_piolib` -- so this file is the laptop half of that pair.
"""

import pytest

from tests.piosim import M32, high_during, run, strobe_train
from wl_sync.rp1.program import (
    DATA_SAMPLE_CYCLES,
    ENTRY,
    STROBE_CAPTURE,
    WRAP,
    WRAP_TARGET,
    encode_in_pins,
    encode_jmp_pin,
    encode_jmp_x_dec,
    encode_mov_isr_x,
    encode_mov_x_not_null,
    encode_push_noblock,
)


def test_jmp_on_the_jmp_pin():
    """Condition 110 in bits 7:5 -- how the strobe is tested without stalling."""
    assert encode_jmp_pin(5) == 0x00C5


def test_jmp_x_decrement():
    """Condition 010. PIO's ONLY decrement, which is why the counter runs down."""
    assert encode_jmp_x_dec(1) == 0x0041


def test_in_pins_16():
    assert encode_in_pins(16) == 0x4010


def test_in_pins_rejects_a_width_the_field_cannot_hold():
    for bad in (0, 33):
        with pytest.raises(ValueError):
            encode_in_pins(bad)


def test_push_noblock():
    """noblock, so a full FIFO drops rather than stalling. A stalled state machine
    is a stopped clock, and the counter lives in that machine."""
    assert encode_push_noblock() == 0x8000


def test_mov_isr_x():
    assert encode_mov_isr_x() == 0xA0C1


def test_mov_x_not_null():
    """X = 0xFFFFFFFF. `set` carries only a 5-bit immediate, so the counter cannot be
    seeded with a literal; inverting NULL is the standard way to get all ones."""
    assert encode_mov_x_not_null() == 0xA02B


def test_the_program_fits_well_inside_the_instruction_memory():
    """Budget from the plan: leave room for edge capture in the same 32-word memory."""
    assert len(STROBE_CAPTURE) <= 16, f"{len(STROBE_CAPTURE)} instructions is too many"


JMP_X_DEC = {a for a, w in enumerate(STROBE_CAPTURE) if w >> 13 == 0 and (w >> 5) & 7 == 2}


def _capture(train, *, x=M32, data=lambda c: 0, entry=ENTRY, cycles=None):
    end = cycles if cycles is not None else train[-1][1] + 50
    return run(STROBE_CAPTURE, entry=entry, wrap_target=WRAP_TARGET, wrap=WRAP, cycles=end,
               strobe=high_during(train), data=data, x=x), end


@pytest.mark.parametrize("widths", [(2, 2), (3, 3), (8, 9), (16, 17), (2, 300)])
@pytest.mark.parametrize("seed", range(1, 6))
def test_every_decrement_falls_on_an_odd_cycle_whatever_the_strobes_do(widths, seed):
    """2 cycles per decrement on EVERY path, entry and exit included, derived from the
    program itself (spec §2.1 Part 0). A path that does not decrement loses about 2 us
    per event, 1.2 ms over 600 events, which fails the +/-100 us gate on arithmetic
    alone. Odd cycles only means no path ever shifts the phase; every address visited
    means no path went unexercised."""
    train = strobe_train(seed, 100, widths=widths)
    result, end = _capture(train)
    assert all(cycle % 2 == 1 for cycle in result.decrement_cycles)
    assert result.decrements == end // 2
    assert result.visited == set(range(len(STROBE_CAPTURE)))
    assert len(result.pushes) == 2 * len(train)


def test_four_hundred_strobes_lose_no_tick():
    """The old listing lost 600 on this train (tests/test_piosim.py:11)."""
    result, end = _capture(strobe_train(seed=1, count=400))
    assert end // 2 - result.decrements == 0


def test_x_reaching_zero_at_every_jmp_x_dec_stays_in_the_program():
    zero_at = set()
    for seed in range(20):
        for x in range(64):
            train = strobe_train(seed, 6, widths=(2, 30), lows=(2, 30))
            result, end = _capture(train, x=x)  # LeftProgram fails the test
            assert result.decrements == end // 2
            zero_at.update(result.zero_at)
    assert zero_at == JMP_X_DEC  # X == 0 was met at every one of them


def test_the_count_runs_on_across_the_counters_wrap():
    train = strobe_train(seed=2, count=50)
    result, end = _capture(train, x=40)
    counters = [word for i, (_, word) in enumerate(result.pushes) if i % 2]
    for (rise, _), counter in zip(train, counters, strict=True):
        assert (40 - counter) & M32 == (rise + 1) // 2 + 3
    assert result.decrements == end // 2 and result.x > 40  # it did wrap


def test_each_strobe_pushes_its_data_then_its_count():
    """Edges on cycle boundaries: the data is read 1.0-1.5 us after the strobe rises and
    the count is latched 3.0-3.5 ticks after it. A real edge can fall between two
    samples and adds up to a further 1 us: the full interval is the next test's."""
    train = strobe_train(seed=3, count=100)
    result, _ = _capture(train, data=lambda cycle: cycle & 0xFFFF)
    data = [word for i, (_, word) in enumerate(result.pushes) if i % 2 == 0]
    counts = [~word & M32 for i, (_, word) in enumerate(result.pushes) if i % 2]
    for (rise, _), word, tick in zip(train, data, counts, strict=True):
        seen = 2 * ((rise + 1) // 2)  # the even cycle whose jmp pin saw it
        assert word == (seen + DATA_SAMPLE_CYCLES) & 0xFFFF
        assert tick == (rise + 1) // 2 + 3


@pytest.mark.parametrize("width", [2, 4])
@pytest.mark.parametrize("phase", [0, 1])
def test_a_burst_six_microseconds_apart_is_captured_whole(width, phase):
    train = [(10 + phase + 12 * i, 10 + phase + 12 * i + width) for i in range(30)]
    result, end = _capture(train)
    assert len(result.pushes) == 60 and result.decrements == end // 2


def test_strobes_closer_than_six_microseconds_are_merged():
    """Pins the program's limit (today's needed 5.5 us), so a task PC that strobes
    faster is a known failure rather than a silent one."""
    train = [(10 + 11 * i, 12 + 11 * i) for i in range(30)]
    result, _ = _capture(train)
    assert len(result.pushes) // 2 < 30


def test_a_one_cycle_strobe_is_seen_only_on_a_sampling_cycle():
    """Coverage: pins that the program samples the strobe every 2 cycles, on even
    cycles from the entry, which the timing bounds above and below rest on."""
    assert len(_capture([(100, 101)], cycles=200)[0].pushes) == 2
    assert len(_capture([(101, 102)], cycles=200)[0].pushes) == 0


@pytest.mark.parametrize("fraction", [0.1, 0.25, 0.5, 0.75, 0.99])
@pytest.mark.parametrize("whole", [20, 21, 40, 41, 100])
def test_an_edge_at_any_instant_is_timed_within_the_documented_bounds(whole, fraction):
    """A real strobe rises at any instant but is tested every 2 cycles, so it is seen up
    to 1 us late: the data is read 1.0-2.0 us after the rise, the count is latched
    3.0-4.0 ticks after it, and the pair is in the FIFO within 11 cycles (5.5 us)."""
    rise = whole + fraction
    result, _ = _capture([(rise, rise + 4)], data=lambda cycle: cycle & 0xFFFF, cycles=300)
    assert len(result.pushes) == 2
    (_, word), (second, counter) = result.pushes
    seen = (word - DATA_SAMPLE_CYCLES) & 0xFFFF  # the sampling cycle that saw it
    assert 0 <= seen - rise < 2
    assert 2 <= seen + DATA_SAMPLE_CYCLES - rise < 4  # cycles: 1.0-2.0 us
    ticks = (~counter & M32) - rise / 2
    assert 3 <= ticks < 4
    assert second + 1 - rise < 11


def test_a_low_between_samples_does_not_split_a_strobe():
    """The strobe is tested every 2 cycles, so a 1-cycle (0.5 us) low that no test falls
    in is invisible: one capture, not two."""
    result, _ = _capture([(10, 21), (22, 30)], cycles=100)
    assert len(result.pushes) == 2


def test_a_low_on_a_sampling_cycle_splits_a_strobe_in_two():
    result, _ = _capture([(10, 20), (21, 30)], cycles=100)
    assert len(result.pushes) == 4


@pytest.mark.parametrize("phase", [0, 1])
def test_a_two_cycle_low_splits_a_strobe_in_two_at_either_phase(phase):
    """Pins the limit "low at least 1 us": a 2-cycle low always holds a sampling cycle."""
    result, _ = _capture([(10, 20 + phase), (22 + phase, 32)], cycles=100)
    assert len(result.pushes) == 4


def test_entering_anywhere_but_the_entry_pushes_a_pair_no_strobe_made():
    """Why the shim passes ENTRY: pio_sm_init at offset + 0 runs one capture at once."""
    assert len(_capture([], entry=0, cycles=100)[0].pushes) == 2
    assert len(_capture([], cycles=100)[0].pushes) == 0
