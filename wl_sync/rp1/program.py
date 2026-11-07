"""The strobe-capture PIO program, as instruction words.

BUILT HERE RATHER THAN ASSEMBLED. piolib exposes `pio_encode_*` and takes a
`pio_program_t` holding a plain `uint16_t *`, so nothing requires running
`pioasm` or shipping a `.pio` source file through a build step. Encoding in
Python instead means the program is a value this package can TEST on a laptop,
which is otherwise the one part of Task 5b with no verification at all.

The encodings are the RP2040 PIO instruction set. piolib's own headers confirm
the pieces this file depends on -- `pio_instr_bits` gives the opcode bases
(jmp=0x0000, in=0x4000, push=0x8000, mov=0xa000) and `pio_src_dest` gives the
register numbers (pins=0, x=1, y=2, null=3, isr=6, osr=7). `Rp1Backend` asserts
these agree with the C encoders at load time, so a divergence between RP1 and
RP2040 surfaces as a refusal to start rather than as wrong timestamps.

WHAT THE PROGRAM DOES: on each strobe it pushes TWO words -- the 16 data lines
(upper bits clear), then the raw down-counter. See wl_sync.rp1.pio_capture for
why two words rather than one, and pio/strobe_capture.pio for the annotated
source form of what is encoded below.
"""

from __future__ import annotations

_JMP = 0x0000
_IN = 0x4000
_PUSH = 0x8000
_MOV = 0xA000

# pio_src_dest, from piolib's header.
_PINS, _X, _NULL, _ISR = 0, 1, 3, 6

_COND_PIN = 6  # jmp condition 110: the pin named by sm_config_set_jmp_pin
_COND_X_DEC = 2  # jmp condition 010: decrement X, jump if it was non-zero
_MOV_OP_INVERT = 1


def encode_jmp_pin(address: int) -> int:
    """`jmp pin, addr` -- test the strobe WITHOUT stalling.

    `wait` would be the obvious instruction and is never used in this program: it
    stalls the state machine, and the state machine is where the clock lives.
    """
    return _JMP | (_COND_PIN << 5) | (address & 0x1F)


def encode_jmp_x_dec(address: int) -> int:
    """`jmp x--, addr` -- PIO's only decrement, hence a counter that runs DOWN.

    Also the only way to spend a tick outside the idle loop, which is what lets the
    capture path stay on the same cycles-per-tick ratio as the idle loop.
    """
    return _JMP | (_COND_X_DEC << 5) | (address & 0x1F)


def encode_in_pins(bit_count: int) -> int:
    """`in pins, n`. Requires in_shiftdir LEFT so the lines land in ISR bits 0..n-1
    and leave the upper half clear -- which is what makes the two-word pairing
    self-checking. Shifting right puts them at the top and destroys that."""
    if not 1 <= bit_count <= 32:
        raise ValueError(f"in pins width must be 1..32, got {bit_count}")
    return _IN | (_PINS << 5) | (bit_count & 0x1F)  # 32 encodes as 0


def encode_push_noblock() -> int:
    """`push noblock`. Blocking would stall the machine and stop the counter, so a
    full FIFO drops instead. `pair_fifo` raises on a single lost word; a lost PAIR is
    invisible, so the bench run checks the RX overflow flag directly."""
    return _PUSH


def encode_mov_isr_x() -> int:
    return _MOV | (_ISR << 5) | _X


def encode_mov_x_not_null() -> int:
    """X = 0xFFFFFFFF. `set` carries a 5-bit immediate only, so the counter cannot be
    seeded with a literal. Executed via pio_sm_exec before the machine is enabled."""
    return _MOV | (_X << 5) | (_MOV_OP_INVERT << 3) | _NULL


# WHY THIS SHAPE. Execution starts at idle (offset + ENTRY), and every `jmp x--` names
# the address it would fall through to. A `jmp x--` costs one cycle whether or not it
# jumps, so X == 0 (the counter's wrap) changes nothing: the instruction falls through to
# the address it names anyway, and no path can leave the program there. Every path costs
# two cycles per decrement, entry and exit included; tests/test_rp1_program.py:80 runs the
# words in tests/piosim.py to show it.
STROBE_CAPTURE: tuple[int, ...] = (
    encode_jmp_x_dec(1),  # 0   entry: pairs with the taken jmp pin at 11
    encode_in_pins(16),  # 1
    encode_jmp_x_dec(3),  # 2
    encode_push_noblock(),  # 3   word 1: data
    encode_jmp_x_dec(5),  # 4
    encode_mov_isr_x(),  # 5   the counter, 3 decrements after the jmp pin that saw the strobe
    encode_jmp_x_dec(7),  # 6
    encode_push_noblock(),  # 7   word 2: counter
    encode_jmp_x_dec(9),  # 8   held: the decrement, falling into the test either way
    encode_jmp_pin(8),  # 9   held while high
    encode_jmp_x_dec(11),  # 10  exit: pairs with the untaken test, falling into idle either way
    encode_jmp_pin(0),  # 11  idle (ENTRY, WRAP_TARGET)
    encode_jmp_x_dec(11),  # 12  idle (WRAP)
)

# Where the state machine starts, relative to where pio_add_program put the program: idle.
# Starting at offset + 0 would run one capture at once and push a pair no strobe made.
ENTRY = 11
WRAP_TARGET = 11
WRAP = 12

# `in pins` runs this many cycles after the `jmp pin` that saw the strobe (the taken jump
# at 11, then the jmp x-- at 0, then `in pins` at 1), so the data lines are read this
# long after the strobe was seen. What the task PC must hold, and for how long after
# raising the strobe, is start_strobed_capture's contract (wl_sync/rp1/backend.py:329-336).
DATA_SAMPLE_CYCLES = 2
