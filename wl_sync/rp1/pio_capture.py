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

This module is pure arithmetic plus the pumps' decisions about failure, and is
tested on a laptop. Only the piolib calls that produce `entries` are hardware-gated.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from wl_sync.backend import CaptureLoss

FIFO_WORD_BITS = 32


class DesyncError(ValueError):
    """The FIFO stream stopped being a sequence of (data, counter) pairs.

    Raised rather than repaired for code words: once the pairing slips, every later
    word is attributed to the wrong half, so a batch that cannot be trusted must not be
    quietly turned into plausible timestamps. Stamps are paired again instead, and the
    stamp pump holds this to report it (`pair_stamps`, `StampPump`): a stamp is not a
    record, and B is written only from one a frame's rises vouch for.
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
    `Recorder`'s shared clock (nearest to `now_us()` unwrapped), rather than a second
    wrap concept of its own.

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


def pair_stamps(
    words: Sequence[int], data_count: int
) -> tuple[list[int], list[int], list[int]]:
    """The stamp machine's FIFO stream as stamps, PAIRED AGAIN WHERE A WORD WAS LOST
    (Task 1.15; the user's ruling on final review I1, 2026-11-20). Returns the stamps (raw
    32-bit P counts, in the order pushed), the words left for the next batch, and where
    the stream slipped, as indices into `words`.

    WHAT THE LAYOUT TELLS APART. A data word is `in pins, 16`, shifted in from the left
    into an ISR that every push clears, so its bits above the window are always clear
    (wl_sync/rp1/program.py:55-61, :87-101). A push to a full FIFO is dropped and still
    clears the ISR (RP2040 datasheet §3.4.6.2 [unverified on RP1]), so a lost push leaves
    no bits behind for the next data word. A counter is ~P, so its upper half is set
    unless P lies within 2**16 us, 65.5 ms, before its 71.6-min wrap. A word with bits above
    the window is therefore a counter, always. That is the check `pair_fifo` makes; here
    it also gives the phase: such a word where a data word belongs is a counter whose
    data word was lost, or which follows a lone data word, so it is taken as a stamp, and
    the stream is in phase again from the word after it. Nothing is dropped to find the
    phase.

    WHAT IT CANNOT TELL APART, which is pair_fifo's blind spot too:
      * A lone data word, its counter dropped, pairs with the next data word read as a
        counter: one stamp at 0xFFFFFFFF less that 16-bit word, within 65.5 ms before P's
        wrap. The slip shows at the next counter. Unwrapped near a frame anywhere else,
        it lies before the frame and is counted between frames, or, while P lies past
        2**31, far past the frame's end, so it is carried and the next frame counts it
        between frames (wl_sync/clockmap.py:276-281). Only a frame within those
        65.5 ms of the wrap could take it for its lead, and only if the frame's other rises
        align on it, each within LEAD_STAMP_TOLERANCE (wl_sync/clockmap.py:329-341), and
        the 16-bit word happens to put it there: that B would be up to 1 ms off.
      * Within those 65.5 ms a counter looks like data, so a slip there pairs words
        wrongly until the first counter after the wrap, as above.

    A batch's last word, unpaired, is left for the next batch when it can be a data word,
    whose counter the drain has not read yet. When it is a counter it is taken now: the
    drain's mark says every stamp before it is queued (`StampPump.stamps_through`).
    """
    if not 1 <= data_count < FIFO_WORD_BITS:
        raise ValueError(f"data_count must be 1..{FIFO_WORD_BITS - 1}, got {data_count}")
    above = ~((1 << data_count) - 1)
    stamps: list[int] = []
    slips: list[int] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word & above:  # a counter where a data word belongs
            slips.append(index)
            stamps.append((~word) & 0xFFFF_FFFF)
            index += 1
        elif index + 1 < len(words):
            stamps.append((~words[index + 1]) & 0xFFFF_FFFF)
            index += 2
        else:
            break  # a data word, its counter not drained yet
    return stamps, list(words[index:]), slips


class WordsDroppedError(RuntimeError):
    """The RX FIFO was full when drained, so `push noblock` may have discarded words."""


# How long after a strobe edge both of its words are in the RX FIFO, as the box clock
# that `polled_at` is read on sees it, with room to spare. The program
# (wl_sync.rp1.program) runs at 2 MHz, 0.5 us a cycle. The idle loop tests the strobe
# every 2 cycles, so a strobe is seen up to 2 cycles after it rises; the second word is
# pushed 8 cycles after the `jmp pin` that saw it, and the push completes 1 cycle later.
# 2 + 8 + 1 = 11 cycles after the edge, 5.5 us. That was the whole allowance, at 10 us.
#
# 1_000 ON ONE COUNT, BECAUSE THE CLOCK IS AN ESTIMATE THERE. A word's tick is P itself,
# and `polled_at` is now_us(), the RAW->P fit's estimate of P, so the lag must also cover
# how far ahead of P the estimate runs. The spec widens it for the warm-up error, tens of
# us before the first pairs, and finds 1 ms costs a roll nothing measurable (spec §2.4,
# docs/superpowers/specs/2026-10-30-one-clock-design.md:375-387). It covers an estimate
# up to a little under 1 ms ahead of P: the lag, less the 5.5 us above. Lead edges that
# still match keep it there, since a match needs the map within LEAD_STAMP_TOLERANCE of
# the stamp (wl_sync/clockmap.py:49-55).
#
# IT DOES NOT COVER LOST LEAD EDGES. While the fit has lost them, until R19's restart
# finds them again (wl_sync/rp1/backend.py, `_reacquire`) or for good where it cannot,
# the estimate can run further ahead of P than that. A mark can then claim words whose
# ticks lie at or before it while they are still in the FIFO, so a roll can close a
# segment ahead of them, and they land in the next.
#
# In a split process `polled_at` is MONO us since the origin, as it was at 10, and
# "before" is on that clock (`WordPump.handle`). 1_000 is a larger lag than it needs
# there, and still safe, because a larger lag only claims less. The stamp drain's mark
# follows the same rule (`StampPump.stamps_through`), so a frame waits about 1 ms longer
# for it.
WORDS_LAG_US = 1_000


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

    `delivered_through` is how far it has delivered, as a raw `now_us()` reading (see
    `handle`), for the run loop's roll barrier. None until the first drain that can say.

    A DESYNC ALSO GOES TO `on_loss`, as a CaptureLoss, for its L record in the segment
    (wl_sync.log.Loss): the strobes in the refused batch, at least half its entries
    rounded up, since a strobe pushes two. Nothing of this path follows it, because the
    pump stops, so its window has no end. A FULL FIFO DOES NOT: it MAY have dropped
    words, and if no strobe came while it was full nothing was lost, so its count cannot
    be bounded below by one. The held error, and so the journal, is unchanged for both.
    `on_loss` raises only when the sink behind it fails: then the failure is held, the
    desync its context. A report the Recorder cannot make into a record it drops itself
    (wl_sync.service.Recorder._on_loss), and the desync stays the held error.
    """

    def __init__(
        self, on_word: Callable[[int, int], None], data_count: int, on_loss=None
    ) -> None:
        self._on_word = on_word
        self._on_loss = on_loss
        self._data_count = data_count
        # The held error, written on the pump's thread and taken on the run loop's
        # (`take_error`): both under `_held`, so a take cannot clear one held between its
        # read and its clear, as for EdgePump's (wl_sync/rp1/gpio.py:984-994).
        self._held = threading.Lock()
        self.error: BaseException | None = None
        self.delivered_through: int | None = None

    def fail(self, error: BaseException) -> None:
        """Hold `error`, over anything held: the latest failure is the one to report."""
        with self._held:
            self.error = error

    def take_error(self) -> BaseException | None:
        """The error held, cleared: check_health reports each one once."""
        with self._held:
            error, self.error = self.error, None
        return error

    def handle(
        self,
        entries: Sequence[int],
        fifo_was_full: bool = False,
        polled_at: int | None = None,
    ) -> bool:
        """Deliver one drained batch. Returns False when the pump should stop.

        `polled_at` is `now_us()` read just before the drain that produced `entries`. The
        drain passes it only when it read every word the FIFO held when it looked: an even
        level, with no half pair left behind. Every strobe more than WORDS_LAG_US before
        that reading had both its words in the FIFO by then, so once this batch is
        delivered, so is every strobe at or before `polled_at - WORDS_LAG_US - 1`, and
        `delivered_through` moves there. The allowance means the rule does not depend on
        how long the shim takes to read the level.

        "Before" is on the box clock, where the reading is from, not by the words' own
        ticks, which count P. In a split process the box clock is MONO, which P drifts
        from over a run; on one count it is the fit's estimate of P, which differs from P
        by the estimate's error (WORDS_LAG_US says how much of that the lag covers).
        A batch that fails moves nothing.
        """
        if fifo_was_full:
            self.fail(WordsDroppedError(
                "the PIO RX FIFO was full when drained: event codes may have been lost"
            ))  # recorded, and we keep going
        try:
            events = pair_fifo(entries, self._data_count)
        except DesyncError as error:
            self.fail(error)
            if self._on_loss is not None:
                strobes = (len(entries) + 1) // 2
                try:
                    self._on_loss(CaptureLoss("desync", strobes, "at_least"))
                except BaseException as failure:  # noqa: BLE001 - check_health's
                    self.fail(failure)  # a failing consumer, the desync its context
            return False
        for tick, word in events:
            try:
                self._on_word(tick, word)
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.fail(error)
                return False
        if polled_at is not None:
            self.delivered_through = (polled_at - WORDS_LAG_US - 1) & 0xFFFF_FFFF
        return True


class StampPump:
    """Queues the stamp machine's stamps for `emit_pulses`, and survives what goes wrong.

    The stamp machine runs the capture program with GPIO0-15 as its data window, so its
    FIFO carries the same (data, counter) pairs as the word machine's, and each batch is
    paired by `pair_stamps`, which makes `pair_fifo`'s upper-half check (spec §2.1,
    docs/superpowers/specs/2026-10-30-one-clock-design.md:185). Only the counter is
    kept: the P count of a rising edge on the stamp pin, raw, 32 bits.

    A FULL FIFO is held, as WordPump holds it, and the pump CONTINUES.

    A DESYNC IS HELD AND THE PUMP CONTINUES, where WordPump stops (Task 1.15; the user's
    ruling on final review I1, 2026-11-20). The stream is paired again on the counter
    where the slip shows (`pair_stamps`), and the desync counted (`desyncs`). Until then a
    desync stopped this pump, and every later B was dropped for the rest of the process,
    though the fault, a full FIFO and a drain between a pair's two pushes, had passed. The
    word pump refuses its batch and stops because a code word is a record, and a word
    read as the wrong half would be a plausible event that never happened. A stamp is not
    a record: B is written only from a stamp a frame's own rises align on
    (wl_sync/clockmap.py:329-341), so the one stamp a slip can misread is refused there
    unless its frame lies within 65.5 ms before P's wrap (`pair_stamps`), and a stamp lost
    in it is a missed stamp, whose frame is dropped as an unstamped lead is.
    It is held as WordPump holds its desync, so check_health reports it, an ERROR once per
    desync (wl_sync/cli.py:1251-1270). No L record: stamps are not written, and the L
    format's paths are W and E (wl_sync/log.py:369-370).

    THE PUMP STOPS only when the drain itself fails (`stop`): `stopped` turns True.

    LOCKED, because it is shared across two threads: `handle` runs on the pump's, and
    `take`, `stamps_through`, `stopped` and `take_error` on the run loop's.
    """

    def __init__(self, data_count: int) -> None:
        self._data_count = data_count
        self._lock = threading.Lock()
        self._queued: list[int] = []
        self._through: int | None = None
        self._error: BaseException | None = None
        self._stopped = False
        # A data word whose counter the drain has not read yet (`pair_stamps`). The pump's
        # thread alone touches it, in `handle`, so it is not under the lock.
        self._carry: list[int] = []
        self._desyncs = 0

    @property
    def desyncs(self) -> int:
        """How many times the stream has slipped and been paired again, this process."""
        with self._lock:
            return self._desyncs

    @property
    def stamps_through(self) -> int | None:
        """Every stamp at or before this raw P count has been queued, by WordPump's rule
        (`WordPump.handle`): `polled_at` less WORDS_LAG_US less 1. None until the first
        drain that can say."""
        with self._lock:
            return self._through

    @property
    def error(self) -> BaseException | None:
        with self._lock:
            return self._error

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def handle(
        self,
        entries: Sequence[int],
        fifo_was_full: bool = False,
        polled_at: int | None = None,
    ) -> bool:
        """Queue one drained batch. Returns True: since Task 1.15 nothing in a batch
        stops the pump, a desync included (the class docstring).

        `polled_at` is the P count read just before the drain looked at the FIFO. The
        stamp drain reads every word the FIFO holds and passes it at every look
        (wl_sync/rp1/backend.py:992-1023), where WordPump's is passed only when its
        drain left no half pair behind. It is on P, not on `now_us()`: the frame's end it
        is compared with is a P count. Every stamp it marks is queued, however the stream
        was paired: a data word carried to the next batch had no counter in the FIFO at
        the look, so its edge came within a pair's 5.5 us of it, after the mark, or its
        counter was lost; and a counter alone at the end is taken now (`pair_stamps`).
        """
        if fifo_was_full:
            with self._lock:  # recorded, and we keep going
                self._error = WordsDroppedError(
                    "the stamp machine's RX FIFO was full when drained: "
                    "barcode stamps may have been lost"
                )
        words = self._carry + list(entries)
        stamps, self._carry, slips = pair_stamps(words, self._data_count)
        with self._lock:
            self._queued.extend(stamps)
            if slips:
                self._desyncs += len(slips)
                word = words[slips[-1]]
                self._error = DesyncError(
                    f"the stamp FIFO lost a word, so its data/counter pairing slipped "
                    f"(a counter, {word:#010x}, where a data word belongs): resynchronised "
                    f"on that counter, and stamping goes on; a frame whose lead edge's "
                    f"stamp was lost is dropped; {self._desyncs} desync(s) this run"
                )
            if polled_at is not None:
                self._through = (polled_at - WORDS_LAG_US - 1) & 0xFFFF_FFFF
        return True

    def stop(self, error: BaseException) -> None:
        """Hold `error` and stop: the drain failed itself. A desync does not stop the
        pump (`handle`)."""
        with self._lock:
            self._error = error
            self._stopped = True

    def take(self) -> list[int]:
        """The raw stamps queued since the last call, in the order they were pushed."""
        with self._lock:
            taken, self._queued = self._queued, []
        return taken

    def take_error(self) -> BaseException | None:
        """The held error, cleared: check_health reports each one once."""
        with self._lock:
            error, self._error = self._error, None
        return error
