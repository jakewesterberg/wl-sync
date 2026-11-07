"""A cycle-accurate interpreter for PIO instruction words, for tests only.

It replaces a hand-written table (`wl_sync/rp1/program.py:113-128` at a452599) whose test
(`tests/test_rp1_program.py:69-83` at a452599) checked the table and not the program: a
wrong listing and a matching table passed together. This runs the listing's real
words, so what a test proves is about the program the box loads.

It models only what it names below, and raises `Unmodelled` for the rest. That is
the semantics this repository already assumes (`pio/strobe_capture.pio:101-108` and
`wl_sync/rp1/program.py:42-56`, both at a452599). Whether silicon agrees is the
bench's P0, not this module's.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

M32 = 0xFFFF_FFFF

_PUSH_NOBLOCK = 0x8000
_MOV_ISR_X = 0xA0C1
_MOV_X_NOT_NULL = 0xA02B


class LeftProgram(AssertionError):
    """The PC was outside the program before a cycle ran."""

    def __init__(self, cycle: int, address: int) -> None:
        super().__init__(f"PC {address} is outside the program at cycle {cycle}")
        self.cycle = cycle
        self.address = address


class Unmodelled(ValueError):
    """An instruction word this interpreter does not model."""


@dataclass
class Run:
    cycles: int
    x: int
    decrement_cycles: list[int] = field(default_factory=list)
    zero_at: list[int] = field(default_factory=list)
    pushes: list[tuple[int, int]] = field(default_factory=list)
    visited: set[int] = field(default_factory=set)

    @property
    def decrements(self) -> int:
        return len(self.decrement_cycles)


def run(
    words: Sequence[int],
    *,
    entry: int,
    wrap_target: int,
    wrap: int,
    cycles: int,
    strobe: Callable[[int], bool] = lambda c: False,
    data: Callable[[int], int] = lambda c: 0,
    x: int = M32,
) -> Run:
    """Run `cycles` cycles from `entry`, one instruction per cycle."""
    result = Run(cycles=cycles, x=x)
    isr = 0
    pc = entry
    for cycle in range(cycles):
        if pc not in range(len(words)):
            raise LeftProgram(cycle, pc)
        word = words[pc]
        result.visited.add(pc)
        # A delay would change the cycle count, so ignoring it would make every
        # result here wrong.
        if word & 0x1F00:
            raise Unmodelled(f"{word:#06x} has a delay/side-set field")
        opcode, operand = word >> 13, (word >> 5) & 0x7
        target = pc + 1 if pc != wrap else wrap_target
        if opcode == 0b000:
            condition = operand
            if condition == 0b000:
                jumps = True
            elif condition == 0b010:
                result.decrement_cycles.append(cycle)
                jumps = result.x != 0
                if not jumps:
                    result.zero_at.append(pc)
                result.x = (result.x - 1) & M32
            elif condition == 0b110:
                jumps = strobe(cycle)
            else:
                raise Unmodelled(f"{word:#06x}: jmp condition {condition:03b}")
            if jumps:
                target = word & 0x1F
        elif opcode == 0b010:
            if operand != 0:
                raise Unmodelled(f"{word:#06x}: in source {operand:03b}")
            count = (word & 0x1F) or 32
            isr = ((isr << count) | (data(cycle) & ((1 << count) - 1))) & M32
        elif word == _PUSH_NOBLOCK:
            result.pushes.append((cycle, isr))
            isr = 0
        elif word == _MOV_ISR_X:
            isr = result.x
        elif word == _MOV_X_NOT_NULL:
            result.x = M32
        else:
            raise Unmodelled(f"{word:#06x}")
        pc = target
    return result


def high_during(intervals: Sequence[tuple[int, int]]) -> Callable[[int], bool]:
    """A pin that is high on each [rise, fall), in cycles."""
    return lambda cycle: any(rise <= cycle < fall for rise, fall in intervals)


def strobe_train(
    seed: int, count: int, *, widths=(2, 300), lows=(2, 400), spacing=12, start=10
) -> list[tuple[int, int]]:
    """`count` seeded strobes as (rise, fall) cycles, each rising `spacing` or more after the last."""
    rng = random.Random(seed)
    rise, train = start, []
    for _ in range(count):
        width = rng.randint(*widths)
        train.append((rise, rise + width))
        rise = max(rise + width + rng.randint(*lows), rise + spacing)
    return train
