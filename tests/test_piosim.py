import pytest

from tests.piosim import M32, LeftProgram, Unmodelled, high_during, run, strobe_train

# wl_sync/rp1/program.py:90-107 at a452599, as words: entry 0, wrap 0..1. A literal,
# so this fixture outlives the encoders it was built from.
OLD = (0x00C2, 0x0040, 0x4010, 0x0044, 0x8000, 0x0046, 0xA0C1,
       0x0048, 0x8000, 0x004A, 0x00CC, 0x0000, 0x004A)


def test_the_old_listing_loses_a_tick_and_a_half_per_strobe():
    """The fault OC-R1 found. Entering costs 3 cycles for 1 decrement, and
    leaving costs 4 (spec §2.1). The design's sim lost 600 ticks over 400 strobes; this
    interpreter must see the same, or it cannot be trusted to clear the fix."""
    train = strobe_train(seed=1, count=400)
    end = train[-1][1] + 50
    result = run(OLD, entry=0, wrap_target=0, wrap=1, cycles=end, strobe=high_during(train))
    assert len(result.pushes) == 2 * 400
    assert abs((end // 2 - result.decrements) - 600) <= 1


def test_the_old_listing_leaves_its_program_when_x_reaches_zero_while_held():
    with pytest.raises(LeftProgram) as left:
        run(OLD, entry=0, wrap_target=0, wrap=1, cycles=200,
            strobe=high_during([(10, 150)]), x=20)
    assert left.value.address == 13


@pytest.mark.parametrize("word", [0x2000, 0x0140, 0x0020, 0x8020, 0xA022])
def test_an_instruction_the_interpreter_does_not_model_is_refused(word):
    """wait; jmp x-- with a delay; jmp !x; push block; mov x, y."""
    with pytest.raises(Unmodelled):
        run([word], entry=0, wrap_target=0, wrap=0, cycles=1)


def test_the_seed_makes_x_all_ones():
    assert run([0xA02B], entry=0, wrap_target=0, wrap=0, cycles=1, x=0).x == M32
