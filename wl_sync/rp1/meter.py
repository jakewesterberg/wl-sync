"""The bench's PIO width meter. NOT ON THE RECORDING PATH: only wl_sync.rp1.harness loads it.

THE USER'S RULING, 2026-11-20 (Task 1.21 fix round 6): "A PIO width meter". The capture
program pushes both of a stamp's words before it decides between its held loop and its
short exit, and costs the same on either, so nothing it records says which path a high took
(fix round 5; .superpowers/sdd/2026-11-07-one-clock/fr5/paths.py). The CPU cannot bound a
high finely enough either: one RP1 read costs 5.1 us (box step 2). So a spare state machine
on the dense pin, unclaimed as the stamp machines are, measures every high itself, and the
harness reads the path from the width (wl_sync.bench, `path_of_width`).

THE PROGRAM, pio/width_meter.pio's listing as words, in the capture program's style
(wl_sync/rp1/program.py:82-102) and from the same six instructions, so piolib's encoders
are already checked against these at every open (backend.py, `_assert_encoders_agree`):
idle tests the pin every 2 cycles; a rise seeds X with all ones; the loop decrements X once
and tests the pin, 2 cycles a count, while the pin is high; the fall copies X to the ISR and
pushes it, and the machine goes back to idle. The count is ~word.

RESOLUTION AND RANGE, at CLKDIV 1 and RP1's clk_sys of 200 MHz (5 ns a cycle): a count is 2
cycles, 10 ns, and the width is (2 count + 1) cycles to within one cycle, 5 ns
(tests/test_rp1_meter.py runs it: 1 us to 1 ms at both phases). A high must last 2 cycles,
10 ns, to be seen at all; the capture program needs 1 us. The count wraps modulo 2^32, after
2^33 cycles, 42.9 s of high: every `jmp x--` names its own fall-through, so X reaching 0
changes nothing but the count. The harness's longest high is 1 ms.
"""

from __future__ import annotations

from wl_sync.rp1.program import (
    encode_jmp_pin, encode_jmp_x_dec, encode_mov_isr_x, encode_mov_x_not_null,
    encode_push_noblock,
)

WIDTH_METER: tuple[int, ...] = (
    encode_mov_x_not_null(),  # 0   the rise: X = 0xFFFFFFFF
    encode_jmp_x_dec(2),  # 1   count: one decrement, falling into the test either way
    encode_jmp_pin(1),  # 2   still high: count again
    encode_mov_isr_x(),  # 3   the fall: the count, as ~X
    encode_push_noblock(),  # 4   one word a high
    encode_jmp_pin(0),  # 5   idle (ENTRY, WRAP_TARGET): a rise starts a measurement
    encode_jmp_x_dec(5),  # 6   idle (WRAP): back to 5 either way
)

# Where the machine starts, relative to where pio_add_program put the program: idle, so a
# pin found high at the enable is not measured from part way through.
ENTRY = 5
WRAP_TARGET = 5
WRAP = 6

# The meter runs at clk_sys itself: the finest resolution the PIO has.
CLKDIV = 1.0
_CYCLES_PER_COUNT = 2


def _cycle_ns(clk_sys_hz: int) -> float:
    return CLKDIV * 1e9 / clk_sys_hz


def width_ns(count: int, clk_sys_hz: int) -> float:
    """The high's width from its count: (2 count + 1) cycles, within `uncertainty_ns`."""
    return (_CYCLES_PER_COUNT * count + 1) * _cycle_ns(clk_sys_hz)


def uncertainty_ns(clk_sys_hz: int) -> float:
    """How far a high's true width can lie from `width_ns`: one cycle either way."""
    return _cycle_ns(clk_sys_hz)
