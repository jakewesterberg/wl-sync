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

    `pair_fifo` raises it: it pairs one batch alone, and cannot repair a slip. Both
    pumps pair the stream again instead and HOLD this to report it, each by its own
    rule. Stamps are paired again on the bits (`pair_stamps`, `StampPump`), because a
    stamp is not a record, and B is written only from one a frame's rises vouch for.
    Code words are records with no such check, so they are paired again only where the
    drain's own counts prove the pairing, and every word that cannot be proven is
    dropped and counted (`CodeWordPairer`, `WordPump`; Task 1.20).
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
    (wl_sync/rp1/program.py:55-61, :88-102). A push to a full FIFO is dropped and still
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


# The depth a WordPump counts on when not told one: 8, RP1's per-direction depth
# (pio_rp1.c:985 at utils b57c48e), the least its RX FIFO holds whether or not the join
# (pio/wlsync_pio.c:179) took. It once said "both 4-deep FIFOs joined", RP2040's numbers
# (review I1). The backend passes the depth it measured at start-up, 16 joined
# (Rp1Backend._measure_fifo_depth). Told less than the FIFO holds, the pairing errs safe;
# told more, it does not (tests/test_pio_capture.py, the told-deeper test).
RX_FIFO_WORDS = 8

_DATA, _COUNTER = "data", "counter"


class _Word:
    """One word read from the FIFO, as `CodeWordPairer` knows it."""

    __slots__ = ("value", "closed", "half", "decided")

    def __init__(self, value: int, closed: bool) -> None:
        self.value = value
        # No push between this word and the next one read was dropped: proven.
        self.closed = closed
        # _DATA or _COUNTER where proven, None where not.
        self.half: str | None = None
        # Written in a pair, or dropped; a word not yet decided is the batch's last.
        self.decided = False


class CodeWordPairer:
    """The word machine's FIFO stream as code words, PAIRED AGAIN AFTER A LOST WORD, AND
    NEVER WRONGLY (Task 1.20; the user's ruling, 2026-11-20). `feed` takes each batch the
    drain read and returns, in stream order, ("word", tick, data) for each pair it proves
    is one strobe's two words, and ("lost", strobes) for the words it drops.

    WHY THE STAMP RULE IS NOT ENOUGH. `pair_stamps` pairs again on the bits: a word with
    bits above the window is a counter. A stamp is checked again by its frame's rises; a
    code word has no check of its own, and the bits miss two words lost in a row. When a
    strobe's counter and the next strobe's data word are both dropped, the stream still
    reads data, counter, data, counter, and a pairing by position, or by the bits, writes
    one strobe's code at the next one's tick: a wrong W, which nothing downstream can see.

    WHAT THE DRAIN'S COUNTS PROVE, from four facts, none of them about timing:
      1. The FIFO holds at least `fifo_words` words, and `push noblock` drops a word only
         when it is full (RP2040 datasheet §3.4.6, PUSH; RP1's PIO is documented
         compatible). The backend measures the depth at start-up, and a FIFO not as deep
         as it counts on is a capture fault that records no W (Rp1Backend.
         _measure_fifo_depth; Ruling R40). Told less than the FIFO holds, this errs safe.
      2. Only the drain pops it, on one thread. It reads the level, pops every word the
         level showed, and reads the level again when it popped any
         (Rp1Backend.drain). Every level is read exactly, or the read says it failed
         (pio/wlsync_pio.c, `wlsync_sm_rx_level`; Ruling R40), and nothing is paired
         from a drain whose read failed. So the stream is every word the FIFO took, in
         order. Only one wl-sync process may hold the PIO block: the kernel does not
         stop another from popping it (bench condition B-5).
      3. Each strobe pushes its data word, then its counter, and nothing is pushed between
         them (wl_sync/rp1/program.py:88-102).
      4. A push that is taken clears the ISR, and `in pins, 16` fills bits 0-15 of it
         (wl_sync/rp1/program.py:55-61): a data word pushed straight after a taken push
         has no bits above the window. The box's window is those 16 lines
         (wl_sync/pins.py:11), and capture refuses any other
         (wl_sync/rp1/backend.py:996-1004).

    When the n-th word the FIFO ever took went in, it held n words less those popped. That
    word was not in the FIFO when the drain before the one that read it looked, so every
    pop before that look came first. Counting only those pops, the FIFO held at most

        its position in its batch + 1 + the previous batch's size

    words with it in, or only position + 1 when it came after the previous drain's last
    pop: the level that drain read after popping (`level_after`) is how many came before.
    When that is less than `fifo_words`, the FIFO had room for the next push, so by 1 the
    next push was taken, and by 2 it is the next word read: none was lost between them.
    Such a word is CLOSED.

    A word is a counter when it has bits above the window and the word before it is
    closed: by 4, a data word there has none. Across a closed word the halves alternate,
    by 3, so a word behind a closed data word is a counter, and one behind a closed
    counter a data word. A pair is written only when its data word is closed and its
    counter is a counter by these rules. Then the two are consecutive pushes, the second a
    counter, so by 3 they are one strobe's data word and counter. Nothing else is written,
    so no pair is written across a lost word, and no counter is out of order with its
    neighbours. The datasheet's word on a DROPPED push, that it clears the ISR too
    (unverified on RP1, `pair_stamps`), is not needed: the word after a lost one is never
    taken for a counter by its bits.

    WHAT IS DROPPED. Every word not in a written pair: a lone data word or counter, pairs
    the counts cannot prove, as when the drain popped a full FIFO while strobes came, and
    every word of a drain whose level read after popping failed (`feed`'s `discard`). A
    look whose level read failed popped nothing, so it is not fed at all: the words the
    next look finds came after the last look that read, and are counted from it.
    Each unbroken run of dropped words is at least half its length in strobes, rounded up,
    since a strobe's two words are consecutive. That is reported as each batch ends, and
    before the next pair is written, so the loss sits after every code word written before
    it; a run that goes on into the next batch reports only the strobes it adds.

    THE WINDOW BEFORE P'S WRAP. For 65.5 ms before each 71.6-min wrap a counter, ~P, has no
    bits above the window, so none there is found by its bits. In a stream that has lost
    nothing, the last counter before the window carries the phase through it, and every
    pair is written. After a word lost in the window there is nothing to find the phase by
    until the first counter after the wrap: every pair until then is dropped and counted,
    and none is written wrong.
    """

    def __init__(self, data_count: int, fifo_words: int = RX_FIFO_WORDS) -> None:
        if not 1 <= data_count < FIFO_WORD_BITS:
            raise ValueError(f"data_count must be 1..{FIFO_WORD_BITS - 1}, got {data_count}")
        self._above = ~((1 << data_count) - 1)
        self._fifo_words = fifo_words
        # The previous batch's size, which its drain read as the level, and the level it
        # read after popping: None when it popped nothing, or did not say.
        self._previous = 0
        self._after: int | None = None
        self._last: _Word | None = None
        # The run of dropped words now open, and the strobes already reported for it.
        self._run = 0
        self._reported = 0

    @property
    def pending(self) -> bool:
        """The last word read waits for the next batch: a data word, maybe, whose counter
        the drain has not read yet."""
        last = self._last
        return last is not None and not last.decided

    def feed(
        self,
        batch: Sequence[int],
        level_after: int | None = None,
        discard: bool = False,
    ) -> list[tuple]:
        """Pair one batch: every word the FIFO held at the drain's look, and the level it
        read after popping them. `discard` drops the whole batch and pairs nothing from it,
        for a drain whose read after popping failed (`WordPump.handle`): its words are
        counted as every dropped word is, and the batch still counts in the proof, its
        level after it unknown."""
        out: list[tuple] = []
        for position, value in enumerate(batch):
            # A word in after the previous drain's last pop has every pop of it behind it.
            popping = self._after is None or position < self._after
            held = position + 1 + (self._previous if popping else 0)
            word = _Word(value, closed=held < self._fifo_words)
            last = self._last
            if last is not None and last.closed:
                if value & self._above:
                    # Two counters in a row cannot be pushed: something is not as proven.
                    word.half = None if last.half == _COUNTER else _COUNTER
                elif last.half is not None:
                    word.half = _DATA if last.half == _COUNTER else _COUNTER
            if (
                not discard
                and word.half == _COUNTER
                and not last.decided
                and last.half != _COUNTER
                and not last.value & self._above
            ):
                self._end_run(out)
                out.append(("word", (~value) & 0xFFFF_FFFF, last.value))
                last.decided = word.decided = True
            elif last is not None and not last.decided:
                self._drop(last)
            self._last = word
        last = self._last
        if last is not None and not last.decided and (discard or not (
            last.closed and last.half != _COUNTER and not last.value & self._above
        )):
            self._drop(last)  # it cannot be a data word: nothing will pair it
        self._report(out)
        self._previous, self._after = len(batch), None if discard else level_after
        return out

    def finish(self) -> list[tuple]:
        """Capture has stopped: a word still waiting for the next batch will not be paired,
        so it is dropped and counted (review M2)."""
        out: list[tuple] = []
        last = self._last
        if last is not None and not last.decided:
            self._drop(last)
            self._report(out)
        return out

    def _drop(self, word: _Word) -> None:
        word.decided = True
        self._run += 1

    def _report(self, out: list[tuple]) -> None:
        """("lost", strobes, first): `first` when the run had reported nothing before."""
        due = (self._run + 1) // 2 - self._reported
        if due > 0:
            out.append(("lost", due, self._reported == 0))
            self._reported += due

    def _end_run(self, out: list[tuple]) -> None:
        self._report(out)
        self._run = self._reported = 0


class LevelReadError(RuntimeError):
    """A drain could not read its FIFO's level (Ruling R40). piolib's own read reported a
    failure as a level of 0 (pio/wlsync_pio.c, `wlsync_sm_rx_level`), and a level read low
    is what the word pump's proof cannot survive, so the shim's read says it failed, and
    nothing is paired from that drain."""


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

      * A FULL FIFO means words MAY have been dropped. It is held, and capture CONTINUES,
        as it does after a dropped edge. What it cost is found by the pairing, below.
      * A DESYNC means words the pairing cannot prove whole (`CodeWordPairer`). They are
        DROPPED AND COUNTED, and capture CONTINUES (Task 1.20; the user's ruling,
        2026-11-20). Until then a desync stopped the pump, refused its whole batch, and
        no code word was captured for the rest of the process, though the fault, a full
        FIFO and a drain between a pair's two pushes, had passed. A pair read across a
        lost word would be a plausible event that never happened, so the pairer writes
        only pairs it proves, and none can be wrong: the proof is its docstring.
      * A FAILING CONSUMER means records are not being kept. The pump STOPS.

    `delivered_through` is how far it has delivered, as a raw `now_us()` reading (see
    `handle`), for the run loop's roll barrier. None until the first drain that can say.

    A DESYNC GOES TO `on_loss`, as a CaptureLoss, for its L record in the segment
    (wl_sync.log.Loss): at least the strobes dropped, before the next code word, so the
    record sits after the last code word written before the loss. It is held as a
    DesyncError with the run's count (`desyncs`), so check_health reports it, an ERROR an
    episode (wl_sync/cli.py:1247-1266). A FULL FIFO GOES TO NO L RECORD: it MAY have
    dropped words, and if no strobe came while it was full nothing was lost, so its count
    cannot be bounded below by one. `on_loss` raises only when the sink behind it fails:
    then the failure is held, the desync its context, and the pump stops. A report the
    Recorder cannot make into a record it drops itself (wl_sync.service.Recorder._on_loss),
    and the desync stays the held error.
    """

    def __init__(
        self,
        on_word: Callable[[int, int], None],
        data_count: int,
        on_loss=None,
        fifo_words: int = RX_FIFO_WORDS,
    ) -> None:
        self._on_word = on_word
        self._on_loss = on_loss
        self._pairer = CodeWordPairer(data_count, fifo_words)
        # The held error, written on the pump's thread and taken on the run loop's
        # (`take_error`): both under `_held`, so a take cannot clear one held between its
        # read and its clear, as for EdgePump's (wl_sync/rp1/gpio.py:994-1004).
        self._held = threading.Lock()
        self.error: BaseException | None = None
        self.delivered_through: int | None = None
        self._desyncs = 0

    @property
    def desyncs(self) -> int:
        """How many times the pairing has lost the stream and found it again, this
        process: one for each unbroken run of dropped words, whose L records may be
        several, one a drain it spans (review M3)."""
        with self._held:
            return self._desyncs

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
        level_after: int | None = None,
        level_error: int | None = None,
    ) -> bool:
        """Deliver one drained batch. Returns False when the pump should stop: only for a
        consumer that fails, since Task 1.20.

        `level_error` is the errno of a level read that failed (Ruling R40). It is held
        for check_health, and nothing is paired from that drain, which moves no mark. A
        drain whose look failed popped nothing, so `entries` is empty, and the pairing
        goes on from the last look that read. One whose read after popping failed hands
        over the words it popped: they are dropped, counted as at least half their number
        in strobes in an L record, and the next batch is counted as if it all came while
        they were popped, which errs safe.

        `entries` is every word the FIFO held when the drain looked, and `level_after` the
        level it read after popping them (wl_sync/rp1/backend.py:1007-1012): the counts
        the pairing's proof is made of (`CodeWordPairer`).

        `polled_at` is `now_us()` read just before the drain that produced `entries`. Every
        strobe more than WORDS_LAG_US before that reading had both its words in the FIFO
        by then, so once this batch is delivered, so is every strobe at or before
        `polled_at - WORDS_LAG_US - 1`, and `delivered_through` moves there; one dropped
        is reported by then. The allowance means the rule does not depend on how long the
        shim takes to read the level. It moves only when no word waits for the next
        batch (`CodeWordPairer.pending`): a data word whose counter is not in yet, as
        before, when the drain left an odd word behind. In the window before P's wrap, a
        counter after a loss waits there too, and holds the mark until the next strobe: a
        roll in between waits out its barrier and names the path (wl_sync/cli.py:368-392).

        "Before" is on the box clock, where the reading is from, not by the words' own
        ticks, which count P. In a split process the box clock is MONO, which P drifts
        from over a run; on one count it is the fit's estimate of P, which differs from P
        by the estimate's error (WORDS_LAG_US says how much of that the lag covers).
        A batch that fails moves nothing.
        """
        if fifo_was_full:
            with self._held:
                # A desync held already says the FIFO may have dropped a word, with the
                # run's count, so a full FIFO found before it is taken does not displace it
                # (review M4).
                if not isinstance(self.error, DesyncError):
                    self.error = WordsDroppedError(
                        "the PIO RX FIFO was full when drained: event codes may have been "
                        "lost"
                    )  # recorded, and we keep going
        failed = None if level_error is None else LevelReadError(
            f"the code-word FIFO's level could not be read (errno {level_error}): "
            + ("the words that drain popped are left out, with an L record in the "
               "segment, " if entries else "that drain read nothing, ")
            + "and capture goes on"
        )
        if failed is not None and not entries:
            self.fail(failed)
            return True  # nothing popped: the pairing goes on from the last look
        items = self._pairer.feed(entries, level_after, discard=failed is not None)
        if not self._deliver(items):
            return False
        if failed is not None:
            self.fail(failed)  # held over the desync its words made: it is the cause
        if level_error is None and polled_at is not None and not self._pairer.pending:
            self.delivered_through = (polled_at - WORDS_LAG_US - 1) & 0xFFFF_FFFF
        return True

    def finish(self) -> bool:
        """Capture has stopped, after the last drain: count a word still waiting for a
        counter that will not be read (review M2). Returns False when its report fails."""
        return self._deliver(self._pairer.finish())

    def _deliver(self, items: list[tuple]) -> bool:
        for item in items:
            if item[0] == "lost":
                _, strobes, first = item
                with self._held:
                    self._desyncs += first
                    desyncs = self._desyncs
                self.fail(DesyncError(
                    f"the code-word FIFO may have dropped a word, so at least {strobes} "
                    f"strobe(s) whose data/counter pairing cannot be proven are left out, "
                    f"with an L record in the segment: paired again from the next pair "
                    f"proven whole, and capture goes on; {desyncs} desync(s) this run"
                ))
                if self._on_loss is not None:
                    try:
                        self._on_loss(CaptureLoss("desync", strobes, "at_least"))
                    except BaseException as failure:  # noqa: BLE001 - check_health's
                        self.fail(failure)  # a failing consumer, the desync its context
                        return False
                continue
            _, tick, word = item
            try:
                self._on_word(tick, word)
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.fail(error)
                return False
        return True


class StampPump:
    """Queues the stamp machine's stamps for `emit_pulses`, and survives what goes wrong.

    The stamp machine runs the capture program with GPIO0-15 as its data window, so its
    FIFO carries the same (data, counter) pairs as the word machine's, and each batch is
    paired by `pair_stamps`, which makes `pair_fifo`'s upper-half check (spec §2.1,
    docs/superpowers/specs/2026-10-30-one-clock-design.md:185). Only the counter is
    kept: the P count of a rising edge on the stamp pin, raw, 32 bits.

    A FULL FIFO is held, as WordPump holds it, and the pump CONTINUES.

    A DESYNC IS HELD AND THE PUMP CONTINUES (Task 1.15; the user's ruling on final review
    I1, 2026-11-20). The stream is paired again on the counter where the slip shows
    (`pair_stamps`), and the desync counted (`desyncs`). Until then a desync stopped this
    pump, and every later B was dropped for the rest of the process, though the fault, a
    full FIFO and a drain between a pair's two pushes, had passed. The word pump goes on
    too since Task 1.20, by a stricter rule (`CodeWordPairer`): a code word is a record,
    and a word read as the wrong half would be a plausible event that never happened, so
    it writes only pairs the drain's counts prove. A stamp is not a record: B is written
    only from a stamp a frame's own rises align on (wl_sync/clockmap.py:329-341), so the
    one stamp a slip can misread is refused there unless its frame lies within 65.5 ms
    before P's wrap (`pair_stamps`), and a stamp lost in it is a missed stamp, whose
    frame is dropped as an unstamped lead is.
    It is held as WordPump holds its desync, so check_health reports it, an ERROR once per
    desync (wl_sync/cli.py:1247-1266). No L record: stamps are not written, and the L
    format's paths are W and E (wl_sync/log.py:419-420).

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
        (wl_sync/rp1/backend.py:1126-1166), where WordPump's is passed only when its
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

    def hold(self, error: BaseException) -> None:
        """Hold `error` and go on: a drain that read nothing (Ruling R40)."""
        with self._lock:
            self._error = error

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
