"""Decoding for the strobe-capture PIO program's FIFO entries.

The PIO program pushes TWO 32-bit words per strobe edge: the captured data lines
(upper bits clear), then the raw down-counter. See pio/strobe_capture.pio.

`decode_fifo` and `accumulate_ticks` lived here until 2026-08-22 and are GONE
rather than deprecated. They implemented a one-word format packing data beside a
tick DELTA, which left 16 delta bits beside 16 data lines -- 65.535 ms of span
against ~3 s trials. `accumulate_ticks` summed overflowed deltas without
complaint, so the error was silent and cumulative for the rest of the session.
Keeping a working implementation of a format the hardware no longer emits would
be a trap, not a courtesy.

This module is pure arithmetic and is tested on a laptop. Only the piolib call
that produces `entries` is hardware-gated.
"""

from __future__ import annotations

from collections.abc import Sequence

FIFO_WORD_BITS = 32


class DesyncError(ValueError):
    """The FIFO stream stopped being a sequence of (data, counter) pairs.

    Raised rather than repaired: once the pairing slips, every later word is
    attributed to the wrong half, so a batch that cannot be trusted must not be
    quietly turned into plausible timestamps.
    """


def pair_fifo(entries: Sequence[int], data_count: int) -> list[tuple[int, int]]:
    """Turn the two-word FIFO stream into (absolute_tick_us, word) pairs.

    TWO WORDS PER EVENT, NOT ONE. Until 2026-08-22 the PIO packed a data word and a
    tick DELTA into one 32-bit entry, which left 16 bits of delta beside 16 data lines
    -- 65.535 ms of span. Trials are ~3 s, so ordinary inter-trial gaps overflowed the
    field, and `accumulate_ticks` summed the truncated remainder without complaint: a
    cumulative error of 65.536 ms per overflow, for the rest of the session. No clock
    divider fixes it, because covering 3 s in 16 bits needs >=45.8 us per tick, which
    spends half the +/-100 us acceptance budget on quantisation alone.

    A full 32-bit tick wraps at 2^32 us = 71.6 minutes, which is exactly `now_us()`'s
    wrap period -- so this path gets the SAME treatment as edges and barcodes from
    `Recorder`'s per-path unwrapper, rather than a second wrap concept of its own.

    THE COUNTER RUNS DOWN. `jmp x--` is PIO's only decrement, so the state machine
    counts down and the raw word falls as time advances; inverting it here keeps the
    arithmetic in one place rather than in the PIO program, where instructions are the
    scarce resource.

    Pairing is self-checked. Only `data_count` data lines exist, so a data word always
    has its upper bits clear -- which turns a dropped word from a silent permanent swap
    into a raised `DesyncError`. That check is the reason two words is safe, and it is
    not available in the one-word format at all.
    """
    if not 1 <= data_count < FIFO_WORD_BITS:
        raise ValueError(f"data_count must be 1..{FIFO_WORD_BITS - 1}, got {data_count}")
    if len(entries) % 2:
        raise DesyncError(
            f"odd number of FIFO entries ({len(entries)}): a pair was caught mid-flight"
        )

    mask = (1 << data_count) - 1
    paired: list[tuple[int, int]] = []
    for index in range(0, len(entries), 2):
        word, counter = entries[index], entries[index + 1]
        if word & ~mask:
            raise DesyncError(
                f"entry {index} has bits above the {data_count}-line data window "
                f"({word:#010x}); the data/counter pairing has slipped"
            )
        paired.append(((~counter) & 0xFFFF_FFFF, word))
    return paired
