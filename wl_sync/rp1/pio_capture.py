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

This module is pure arithmetic plus `WordPump`'s decisions about failure, and is
tested on a laptop. Only the piolib calls that produce `entries` are hardware-gated.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

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


class WordsDroppedError(RuntimeError):
    """The RX FIFO was full when drained, so `push noblock` may have discarded words."""


class WordPump:
    """Turns RX FIFO batches into `on_word` calls, and survives what goes wrong.

    The PIO counterpart of `wl_sync.rp1.gpio.EdgePump`, SPLIT OUT FOR THE SAME REASON:
    the thread and the piolib calls around it are hardware, and this is the part that
    decides what each failure means. Errors are HELD for `check_health()` rather than
    raised out of the thread, because a pump thread that dies takes the event codes
    with it while the segment keeps growing from the other paths.

    Three failures, and they are not treated alike:

      * A FULL FIFO means words MAY have been dropped. If whole pairs went, the stream
        is still intact and every later event is good, so capture CONTINUES, as it does
        after a dropped edge. If a single word went, the next batch desyncs, below.
      * A DESYNC means the pairing has slipped, and every later word would be read as
        the wrong half: plausible timestamps for events that never happened. The pump
        STOPS. The whole batch is refused, because it cannot be trusted either.
      * A FAILING CONSUMER means records are not being kept. The pump STOPS.
    """

    def __init__(self, on_word: Callable[[int, int], None], data_count: int) -> None:
        self._on_word = on_word
        self._data_count = data_count
        self.error: BaseException | None = None

    def handle(self, entries: Sequence[int], fifo_was_full: bool = False) -> bool:
        """Deliver one drained batch. Returns False when the pump should stop."""
        if fifo_was_full:
            self.error = WordsDroppedError(
                "the PIO RX FIFO was full when drained: event codes may have been lost"
            )  # recorded, and we keep going
        try:
            events = pair_fifo(entries, self._data_count)
        except DesyncError as error:
            self.error = error
            return False
        for tick, word in events:
            try:
                self._on_word(tick, word)
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.error = error
                return False
        return True
