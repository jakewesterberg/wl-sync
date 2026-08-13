"""Decoding for the strobe-capture PIO program's FIFO entries.

The PIO program pushes one 32-bit word per strobe edge: the captured data lines
in the low `data_count` bits, and the tick delta since the previous capture in
the bits above. Deltas rather than absolute ticks because the state machine's
counter is narrow; the host accumulates them against a known start.

This module is pure arithmetic and is tested on a laptop. Only the piolib call
that produces `entries` is hardware-gated.
"""

from __future__ import annotations

from collections.abc import Sequence

FIFO_WORD_BITS = 32


def decode_fifo(entries: Sequence[int], data_count: int) -> list[tuple[int, int]]:
    """Split FIFO entries into (tick_delta, word) pairs."""
    if not 1 <= data_count < FIFO_WORD_BITS:
        raise ValueError(f"data_count must be 1..{FIFO_WORD_BITS - 1}, got {data_count}")
    mask = (1 << data_count) - 1
    return [(entry >> data_count, entry & mask) for entry in entries]


def accumulate_ticks(
    pairs: Sequence[tuple[int, int]], t0: int
) -> list[tuple[int, int]]:
    """Turn (tick_delta, word) pairs into (absolute_tick, word)."""
    absolute: list[tuple[int, int]] = []
    tick = t0
    for delta, word in pairs:
        tick += delta
        absolute.append((tick, word))
    return absolute
