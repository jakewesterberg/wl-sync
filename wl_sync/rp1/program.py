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


def encode_jmp(address: int) -> int:
    return _JMP | (address & 0x1F)


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


# Instruction addresses, so the jump targets stay consistent with the listing.
_IDLE = 0
_CAPTURE = 2
_HELD = 10

STROBE_CAPTURE: tuple[int, ...] = (
    # idle: count, watching the strobe.                          2 cycles / tick
    encode_jmp_pin(_CAPTURE),  # 0
    encode_jmp_x_dec(_IDLE),  # 1
    # capture: latch and push, still advancing the counter.      2 cycles / tick each
    encode_in_pins(16),  # 2
    encode_jmp_x_dec(4),  # 3
    encode_push_noblock(),  # 4   word 1: data
    encode_jmp_x_dec(6),  # 5
    encode_mov_isr_x(),  # 6
    encode_jmp_x_dec(8),  # 7
    encode_push_noblock(),  # 8   word 2: raw counter
    encode_jmp_x_dec(_HELD),  # 9
    # held: count through a wide strobe rather than waiting on it.
    encode_jmp_pin(12),  # 10
    encode_jmp(_IDLE),  # 11  fallen -> re-arm
    encode_jmp_x_dec(_HELD),  # 12
)

WRAP_TARGET = _IDLE
WRAP = 1  # the idle loop auto-wraps; every other path jumps explicitly


def cycles_per_decrement_by_path() -> dict[str, tuple[int, int]]:
    """(cycles, decrements) for each path, so the timing claim is testable.

    THE WHOLE +/-100 us ARGUMENT IS THAT THESE STAY IN A 2:1 RATIO. A program that
    counts only while idle loses the capture path and the strobe-high period from the
    counter -- roughly 2 us per event, 1.2 ms over a 600-event run, which fails Step 3
    before any hardware effect is considered.

    Every instruction is one cycle here; none carries a delay slot, and none can stall,
    because `wait` and blocking `push` are both deliberately absent.
    """
    return {
        "idle": (2, 1),  # jmp pin + jmp x--
        "capture": (8, 4),  # four (instruction + jmp x--) pairs
        "held": (2, 1),  # jmp pin + jmp x--
    }
