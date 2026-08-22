"""The strobe-capture program as instruction words.

Encodings are from the RP2040 PIO instruction set, which piolib's own
`pio_instr_bits` and `pio_src_dest` enums confirm (jmp=0x0000, in=0x4000,
push=0x8000, mov=0xa000; pins=0, x=1, y=2, null=3, isr=6, osr=7). They are
cross-checked against the C `pio_encode_*` functions on the bench -- see
`assert_agrees_with_piolib` -- so this file is the laptop half of that pair.
"""

import pytest

from wl_sync.rp1.program import (
    STROBE_CAPTURE,
    encode_in_pins,
    encode_jmp,
    encode_jmp_pin,
    encode_jmp_x_dec,
    encode_mov_isr_x,
    encode_mov_x_not_null,
    encode_push_noblock,
)


def test_jmp_always():
    assert encode_jmp(5) == 0x0005


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


def test_every_path_costs_two_cycles_per_decrement():
    """THE +/-100 us ARGUMENT, asserted rather than trusted to a comment.

    A version that decrements only while idle loses the whole capture path and the
    whole strobe-high period from the counter -- about 2 us per event, which is 1.2 ms
    over 600 events and fails Task 5b Step 3 on arithmetic alone. So every path through
    the program must advance the counter once per two instructions.
    """
    from wl_sync.rp1.program import cycles_per_decrement_by_path

    for path, (cycles, decrements) in cycles_per_decrement_by_path().items():
        assert decrements > 0, f"path {path!r} never advances the counter"
        assert cycles == 2 * decrements, (
            f"path {path!r} costs {cycles} cycles for {decrements} decrements; "
            f"time is lost at {cycles - 2 * decrements} cycles per pass"
        )
