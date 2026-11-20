import pytest

from wl_sync.rp1.pio_capture import DesyncError, pair_fifo

DATA = 16


def stream(*events, data_count=DATA):
    """The FIFO as the PIO leaves it: per event, a data word then the raw counter.

    The counter runs DOWN, because `jmp x--` is PIO's only decrement, so the raw
    value falls as time advances and the host inverts it.
    """
    entries = []
    for tick, word in events:
        entries.append(word)
        entries.append((~tick) & 0xFFFFFFFF)
    return entries


def test_pairs_a_single_event():
    assert pair_fifo(stream((1_000, 0x8001)), DATA) == [(1_000, 0x8001)]


def test_pairs_several_events_in_order():
    got = pair_fifo(stream((10, 0x1), (2_000_000, 0x2), (5_000_000, 0x3)), DATA)
    assert got == [(10, 0x1), (2_000_000, 0x2), (5_000_000, 0x3)]


def test_spans_a_gap_far_wider_than_a_16_bit_field():
    """THE DEFECT THIS FORMAT EXISTS TO FIX. A 16-bit delta spans 65.535 ms; trials
    are ~3 s, so inter-trial gaps overflowed it and every later timestamp was wrong by
    a cumulative multiple of 65.536 ms, silently. A full 32-bit tick spans 71.6 min --
    the same wrap period as now_us(), so Recorder's shared clock covers
    it with no new concept."""
    got = pair_fifo(stream((1_000, 0xAAAA), (3_001_000, 0xBBBB)), DATA)
    assert got[1][0] - got[0][0] == 3_000_000  # a 3 s gap, carried exactly


def test_a_dropped_word_is_raised_not_silently_swapped():
    """The reason two words is safe. Lose one and data/tick swap roles for the rest of
    the session -- so the pairing is self-checking: only `data_count` data lines exist,
    so a data word ALWAYS has its upper bits clear. A counter word almost never does."""
    entries = stream((1_000, 0x8001), (2_000, 0x8002))
    del entries[1]  # the first counter word never made it out of the FIFO
    with pytest.raises(DesyncError):
        pair_fifo(entries, DATA)


def test_an_odd_length_stream_is_raised():
    """A drain that caught a pair mid-flight. Better to refuse the batch than to
    attribute the last word to the wrong half."""
    with pytest.raises(DesyncError):
        pair_fifo(stream((1_000, 0x8001))[:-1], DATA)


def test_an_empty_drain_is_not_an_error():
    assert pair_fifo([], DATA) == []


def test_rejects_impossible_data_width():
    with pytest.raises(ValueError):
        pair_fifo([], 33)


def test_a_word_pump_delivers_each_event_in_order():
    from wl_sync.rp1.pio_capture import WordPump

    seen = []
    pump = WordPump(on_word=lambda t, w: seen.append((t, w)), data_count=DATA)
    assert pump.handle(stream((10, 0x0001), (20, 0x0002))) is True
    assert seen == [(10, 0x0001), (20, 0x0002)]
    assert pump.error is None


def test_a_full_fifo_is_recorded_but_capture_CONTINUES():
    """Nothing called drain(), so the RX FIFO -- 8 words, 4 events -- filled on the
    first burst of codes and `push noblock` discarded the rest without a sound. A full
    FIFO is the only witness that words MAY have gone. If whole pairs went the stream
    is still intact, so this is a data-quality event like a dropped edge: held for
    check_health(), and capture carries on."""
    from wl_sync.rp1.pio_capture import WordPump, WordsDroppedError

    seen = []
    pump = WordPump(on_word=lambda t, w: seen.append(w), data_count=DATA)
    pump.handle(stream((10, 1)), fifo_was_full=True)
    pump.handle(stream((20, 2)))

    assert seen == [1, 2], "capture stopped after a full FIFO"
    assert isinstance(pump.error, WordsDroppedError)


def test_a_desync_no_longer_stops_the_word_pump_and_is_held_and_counted():
    """THIS TEST PINNED THAT A DESYNC STOPPED THE PUMP, and it no longer does (Task 1.20;
    the user's ruling, 2026-11-20). Until then the batch was refused and no code word
    was captured for the rest of the process. Now the word that cannot be paired is
    dropped and counted, the pairs proven whole are delivered, and the pump goes on.

    10's counter where a data word belongs, first in the stream, has no word before it
    to prove it a counter, so it is dropped: one strobe, in an L record. 20's pair is
    proven (its counter has bits above the window, and no push between it and its data
    word can have been dropped), and is delivered. The last word waits for its counter."""
    pump, got = _pumped()
    slipped = stream((10, 1), (20, 2))[1:] + [0]
    assert pump.handle(slipped) is True
    assert got == [("L", 1), ("W", 20, 2)]
    error = pump.take_error()
    assert isinstance(error, DesyncError)
    assert "1 desync(s) this run" in str(error) and "capture goes on" in str(error)
    assert pump.desyncs == 1


def test_a_failing_word_consumer_stops_the_pump_and_is_held():
    from wl_sync.rp1.pio_capture import WordPump

    def explode(tick, word):
        raise OSError("the sink is gone")

    pump = WordPump(on_word=explode, data_count=DATA)
    assert pump.handle(stream((10, 1))) is False
    assert isinstance(pump.error, OSError)


def test_a_word_pumps_held_error_is_taken_once_and_a_failure_is_held_over_it():
    """`take_error` is check_health's, as the edge and stamp pumps' are: the error held,
    cleared, so each is reported once. `fail` holds a failure over what was held, as
    `handle` does for each of its own, and is how the backend holds a drain that raised."""
    from wl_sync.rp1.pio_capture import WordPump, WordsDroppedError

    pump = WordPump(on_word=lambda t, w: None, data_count=DATA)
    assert pump.take_error() is None
    assert pump.handle(stream((10, 1)), fifo_was_full=True) is True
    assert isinstance(pump.take_error(), WordsDroppedError)
    assert pump.take_error() is None
    assert pump.handle(stream((10, 1)), fifo_was_full=True) is True
    pump.fail(OSError("EIO"))
    assert isinstance(pump.take_error(), OSError)
    assert pump.error is None


# --- Task 1.20: the word pump pairs the stream again after a desync ---------------------
#
# Each scenario is one the FIFO can make: 8 words deep, a push that finds it full is
# dropped, and the drain reads the level, pops that many words one at a time, then reads
# the level again (`level_after`). A word can be lost only where the drain's own counts
# say the FIFO could have been full.


def _counter(tick):
    return (~tick) & 0xFFFF_FFFF


def _pumped():
    """A WordPump whose code words and loss reports land in one list, in stream order."""
    from wl_sync.rp1.pio_capture import WordPump

    got = []
    pump = WordPump(
        on_word=lambda tick, word: got.append(("W", tick, word)),
        data_count=DATA,
        on_loss=lambda loss: got.append(("L", loss.count)),
    )
    return pump, got


FOUR = stream((10, 1), (20, 2), (30, 3), (40, 4))  # a full FIFO: 8 words
DELIVERED_FOUR = [("W", 10, 1), ("W", 20, 2), ("W", 30, 3), ("W", 40, 4)]


def test_a_lost_counter_word_costs_its_strobe_and_capture_goes_on():
    """The drain finds the FIFO full and pops slowly. After its first pop, strobe 5's data
    word finds room and its counter, pushed 2 us later, does not: a lone data word. Strobe
    6 comes after the last pop. The data word is dropped and counted, one strobe in an L
    record just before the next code word, and 6 and 7 are written."""
    pump, got = _pumped()
    assert pump.handle(FOUR, fifo_was_full=True, level_after=1) is True  # 5's data in
    assert pump.handle([5] + stream((60, 6), (70, 7)), level_after=0) is True
    assert got == DELIVERED_FOUR + [("L", 1), ("W", 60, 6), ("W", 70, 7)]
    assert pump.desyncs == 1
    assert "1 desync(s) this run" in str(pump.take_error())


def test_a_lost_data_word_costs_its_strobe_and_capture_goes_on():
    """The other lone word: strobe 5's data word finds the FIFO full, and the drain pops
    before its counter comes. The counter lands where a data word belongs, is dropped and
    counted, and 6 and 7 are written."""
    pump, got = _pumped()
    assert pump.handle(FOUR, fifo_was_full=True, level_after=1) is True  # 5's counter in
    assert pump.handle([_counter(50)] + stream((60, 6), (70, 7)), level_after=0) is True
    assert got == DELIVERED_FOUR + [("L", 1), ("W", 60, 6), ("W", 70, 7)]
    assert pump.desyncs == 1


def test_two_lost_words_the_bits_cannot_show_write_no_wrong_code_word():
    """THE CASE A RESYNC ON THE BITS ALONE GETS WRONG. While the drain pops a full FIFO,
    strobe 5's data word finds room, its counter and strobe 6's data word do not, and
    strobe 6's counter does. The stream reads data, counter, data, counter: no slip
    shows, and pairing by position, or on the bits as the stamp pump does, writes 5's
    code at 6's tick. Both words came while the FIFO could have been full, so neither
    pairing can be proven, and both are dropped: at least one strobe, in an L record."""
    pump, got = _pumped()
    assert pump.handle(FOUR, fifo_was_full=True, level_after=2) is True  # 5's data, 6's counter
    assert pump.handle([5, _counter(60)] + stream((70, 7)), level_after=0) is True
    assert ("W", 60, 5) not in got, "5's code written at 6's tick"
    assert got == DELIVERED_FOUR + [("L", 1), ("W", 70, 7)]


def test_the_valid_pairs_before_a_slip_in_the_same_batch_are_delivered():
    """Until Task 1.20 a desync refused its whole batch, the pairs before the slip
    included (docs/next-session.md §3, the fourth minor finding). The drain finds 6 words
    and pops slowly: strobe 4 comes whole, then 5's data word finds the FIFO full and its
    counter finds room. 4's data word came with room behind it for one more word, so its
    counter is the next push, and the pair is proven and delivered. 5's counter is
    dropped and counted, and 6 is written."""
    pump, got = _pumped()
    assert pump.handle(stream((10, 1), (20, 2), (30, 3)), level_after=3) is True
    batch = stream((40, 4)) + [_counter(50)] + stream((60, 6))
    assert pump.handle(batch, level_after=0) is True
    assert got == DELIVERED_FOUR + [("L", 1), ("W", 60, 6)]


def test_the_mark_waits_for_a_data_word_carried_to_the_next_batch():
    """The drain reads every word the FIFO holds, so a batch can end on a data word whose
    counter is not in yet. It is carried, and the mark does not move while it is: as
    before, a drain that left half a pair behind says nothing. The counter comes with the
    next batch, and the mark moves."""
    from wl_sync.rp1.pio_capture import WORDS_LAG_US

    pump, got = _pumped()
    assert pump.handle(stream((10, 1)) + [2], polled_at=5_000) is True
    assert got == [("W", 10, 1)] and pump.delivered_through is None
    assert pump.handle([_counter(20)], polled_at=6_000) is True
    assert got == [("W", 10, 1), ("W", 20, 2)]
    assert pump.delivered_through == 6_000 - WORDS_LAG_US - 1


WRAP = 1 << 32


def test_a_clean_stream_through_the_window_before_the_wrap_is_written_whole():
    """For 65.5 ms before P's wrap a counter has no bits above the window, so the bits
    cannot say it is one (`pair_stamps`). A stream that has lost nothing is paired through
    it all the same: where no push can have been dropped, a counter follows its data word
    and a data word follows a counter, so the last counter before the window carries the
    phase through it, and the first after the wrap has its bits again."""
    ticks = [WRAP - (1 << 16) - 10_000, WRAP - 40_000, WRAP - 30_000, WRAP - 100, 500, 1_000]
    pump, got = _pumped()
    for k, tick in enumerate(ticks):
        assert pump.handle(stream((tick % WRAP, k)), level_after=0) is True
    assert got == [("W", tick % WRAP, k) for k, tick in enumerate(ticks)]
    assert pump.desyncs == 0


def test_a_desync_in_the_window_before_the_wrap_drops_until_the_first_counter_after_it():
    """In the window a desync leaves nothing to find the phase by, since no counter there
    has bits above the window. Everything from it to the first counter after the wrap is
    dropped and counted, and nothing is written wrong: a pairing by position would read
    5's data word with 6's as its counter, a tick in the window that no strobe had.

    Strobe 0 is before the window. 1-4 fill the FIFO, in the window, all after the drain
    popped 0, so no word between them can have been lost and 0's counter carries the
    phase to them. While the drain pops them, 5's data word finds room and its counter
    does not. 6 and 7, in the window, cannot be paired with nothing to fix the phase;
    8, after the wrap, is."""
    base = WRAP - 60_000
    window = [(base + 1_000 * k) % WRAP for k in range(1, 8)]
    pump, got = _pumped()
    assert pump.handle(stream((WRAP - (1 << 16) - 5_000, 0)), level_after=0) is True
    assert pump.handle(stream(*[(window[k], k + 1) for k in range(4)]), level_after=1)
    assert pump.handle(
        [5] + stream((window[5], 6), (window[6], 7), (2_000, 8)), level_after=0
    ) is True
    written = [item for item in got if item[0] == "W"]
    assert written == [
        ("W", WRAP - (1 << 16) - 5_000, 0),
        ("W", window[0], 1), ("W", window[1], 2), ("W", window[2], 3),
        ("W", window[3], 4), ("W", 2_000, 8),
    ]
    assert sum(item[1] for item in got if item[0] == "L") == 3  # strobes 5, 6 and 7
    assert ("W", _counter(6), 5) not in got, "5's code at a tick no strobe had"


def test_where_the_counts_and_the_bits_disagree_nothing_is_paired_by_alternation():
    """A word with bits above the window right behind a counter, where the drain's counts
    say no word between them can have been lost. The program cannot push a counter after
    a counter (wl_sync/rp1/program.py:88-102), and a data word pushed after a taken push
    has no such bits (`CodeWordPairer`, fact 4): a fact the proof rests on did not hold.
    The pairer then trusts neither word's half, and carries no phase on from them.
    Outside the window the next counter's bits find it again at once; inside it, nothing
    is paired until they can.

    Here the stray word is 7's data word with a bit above the window, and 7's counter and
    8's words are in the window. Carried on from the stray word as a counter, 7's counter
    would be read as a data word and 8's data word as its counter: 7's counter written
    as a code, at a tick no strobe had."""
    pump, got = _pumped()
    before = WRAP - (1 << 16) - 5_000
    batch = stream((before, 0)) + [0x0001_0007] + [_counter(WRAP - 1_000)] + stream(
        (WRAP - 500, 8)
    )
    assert pump.handle(batch) is True
    assert ("W", _counter(8), _counter(WRAP - 1_000)) not in got, got
    assert [item for item in got if item[0] == "W"] == [("W", before, 0)], got
    assert pump.desyncs == 1


# --- Fix round 1 (Ruling R40): a level read that fails, and the run's count ---------------


def test_a_level_read_that_fails_at_the_look_pairs_nothing_and_is_held():
    """A look whose level read failed popped nothing, so the stream is as if it had not
    happened: the next batch's words came after the last look that read, and are counted
    from it. piolib's own read returned 0 on a failure (pio_rp1.c:591-598 at utils
    b57c48e), and a look counted as an empty one would call the next batch's first words
    closed when they are not. Here the drain popped a full FIFO and could not say what
    came while it popped; then a look failed; then two words lost in a row. Counted from
    an empty look, 5's code would be written at 6's tick."""
    from wl_sync.rp1.pio_capture import WORDS_LAG_US, LevelReadError

    pump, got = _pumped()
    assert pump.handle(FOUR, fifo_was_full=True, polled_at=5_000) is True
    assert pump.handle([], polled_at=9_000, level_error=5) is True
    error = pump.take_error()
    assert isinstance(error, LevelReadError) and "errno 5" in str(error), error
    assert pump.delivered_through == 5_000 - WORDS_LAG_US - 1, "a failed look moved the mark"
    assert pump.handle([5, _counter(60)] + stream((70, 7)), level_after=0) is True
    assert ("W", 60, 5) not in got, got
    assert [item for item in got if item[0] == "W"] == DELIVERED_FOUR, got


def test_a_level_read_that_fails_after_popping_drops_the_batch_and_counts_it():
    """The look read the level and the words were popped, but the level after them could
    not be read: nothing is paired from that drain (Ruling R40), its words are dropped and
    counted, at least half of them in strobes, and the drain goes on. With nothing known
    of what came while they were popped, the next batch is counted as if all of it came
    then, and errs safe."""
    from wl_sync.rp1.pio_capture import LevelReadError

    pump, got = _pumped()
    assert pump.handle(FOUR, polled_at=5_000, level_error=5) is True
    assert got == [("L", 4)] and pump.delivered_through is None
    assert isinstance(pump.take_error(), LevelReadError)
    assert pump.handle(stream((50, 5)), level_after=0) is True  # in while 4 words popped
    assert pump.handle(stream((60, 6)), level_after=0) is True
    assert got == [("L", 4), ("L", 1), ("W", 60, 6)]
    assert pump.desyncs == 1, "one run of dropped words, reported twice, is one desync"


def test_a_desync_held_is_not_displaced_by_a_later_full_fifo():
    """The desync's ERROR carries the run's count and says the FIFO may have dropped a word,
    so a full FIFO found before check_health takes it adds nothing, and is not held over it
    (review M4). Held alone, a full FIFO still is."""
    from wl_sync.rp1.pio_capture import WordsDroppedError

    pump, got = _pumped()
    assert pump.handle(FOUR, fifo_was_full=True, level_after=1)
    assert pump.handle([_counter(50)] + stream((60, 6)), level_after=0)
    assert pump.handle(stream((70, 7)), fifo_was_full=True, level_after=0)
    assert isinstance(pump.take_error(), DesyncError)
    assert pump.handle(stream((80, 8)), fifo_was_full=True, level_after=0)
    assert isinstance(pump.take_error(), WordsDroppedError)


def test_a_word_still_waiting_when_capture_stops_is_counted():
    """At the last drain a data word can wait for a counter that will not be read: its
    strobe is lost. `finish` drops it and counts it (review M2), and says nothing when
    nothing waits."""
    pump, got = _pumped()
    assert pump.handle(stream((10, 1)) + [2]) is True
    assert got == [("W", 10, 1)]
    assert pump.finish() is True
    assert got == [("W", 10, 1), ("L", 1)] and pump.desyncs == 1
    assert pump.finish() is True and got[-1] == ("L", 1) and len(got) == 2


# The fuzz: the capture machine and the word drain on one timeline.

def _through_the_fifo(rng, events, slow, starved, depth=8, fails=0.0, answer=None):
    """Run `events`, (time_us, tick, data) strobes, through the FIFO as the box has it,
    and return the batches the drain hands the pump: each a list of (push, word), `push`
    being the word's place among every word the program pushed, with its `level_after`
    and the errno of a level read that failed, or None.

    The program pushes a strobe's data word, then 2 us later its counter
    (wl_sync/rp1/program.py:88-102: 4 cycles at 2 MHz). The FIFO is 8 words, and a push
    that finds it full is dropped (`push noblock`). The drain reads the level, pops that
    many words one call at a time, each call taking its own time, then reads the level
    again when it popped any (Rp1Backend.drain). A share `slow` of its calls are slower
    than a strobe, which is how a word is left alone, and a share `starved` of its polls
    come milliseconds late, which is how the FIFO fills. The FIFO holds `depth` words, and
    a share `fails` of level reads fail (Ruling R40): a look that fails pops nothing, and a
    read after popping that fails leaves the batch with no level after it.

    `answer`, the shim's compiled rule (tests/fifo_state_c.py), reads every level as the box
    does (fix round 3): the kernel answers from a status register, `empty` and `full`, and
    a level field that saturates at 15 (bench probe, wl-rig-sync0, 2026-11-20), and the two
    registers are read at instants of their own, in either order, with pushes landing
    between them in a third of the reads. The level the rule returns is what the drain
    counts on, and a refusal is a failed read."""
    pushes = []
    for at, tick, data in events:
        pushes += [(at + 1.0, data), (at + 3.0, _counter(tick))]
    fifo, batches, pushed = [], [], 0

    def run_until(now):
        nonlocal pushed
        while pushed < len(pushes) and pushes[pushed][0] < now:
            if len(fifo) < depth:
                fifo.append((pushed, pushes[pushed][1]))
            pushed += 1

    def level_read():
        """(level, errno): the FIFO's level as the drain reads it."""
        nonlocal now
        if answer is None:
            return len(fifo), None
        status_first = rng.random() < 0.5
        first = len(fifo)
        if rng.random() < 1 / 3:
            now += rng.uniform(0.5, 6)
            run_until(now)
        second = len(fifo)
        status, field = (first, second) if status_first else (second, first)
        level = answer(8, 0, min(field, 15), int(status == 0), int(status >= depth), depth)
        return (level, None) if level >= 0 else (None, -level)

    now = 0.0
    # No read fails once the last strobe, 200 ms after the rest, is due, so the fuzz can
    # ask that it is written: a failure on its own drain would drop it, by the rule.
    calm = events[-1][0] - 1_000
    while pushed < len(pushes) or fifo:
        late = rng.random() < starved
        now += rng.uniform(2_000, 20_000) if late else rng.uniform(0, 1_500)
        run_until(now)
        failing = fails if now < calm else 0.0
        if rng.random() < failing:
            batches.append(([], None, 5))  # the look's read failed: nothing popped
            continue
        level, refused = level_read()
        if refused is not None:
            batches.append(([], None, refused))  # the rule refused the answer
            continue
        batch, level_after, error = [], None, None
        for _ in range(level):
            now += rng.uniform(4, 40) if rng.random() < slow else rng.uniform(0.3, 3)
            run_until(now)
            batch.append(fifo.pop(0))
        if level:
            now += rng.uniform(0.3, 3)
            run_until(now)
            if rng.random() < failing:
                error = 5
            else:
                level_after, error = level_read()
        batches.append((batch, level_after, error))
    return batches


def _fuzzed_strobes(rng, near_wrap):
    """40-80 strobes, in bursts 6-15 us apart and gaps up to 3 ms, each with its own code,
    then one more 200 ms after the rest. Near the wrap, P starts up to 30 ms before the
    65.5 ms window, so the run crosses the window and the wrap."""
    count = rng.randrange(40, 81)
    base = (WRAP - (1 << 16) - rng.randrange(30_000) if near_wrap
            else rng.randrange(1_000_000, WRAP - 1_000_000_000))
    codes = rng.sample(range(1 << DATA), count + 1)
    bursty = rng.uniform(0.2, 0.8)
    at, events = 10.0, []
    for k in range(count):
        at += rng.uniform(6, 15) if rng.random() < bursty else rng.uniform(20, 3_000)
        events.append((at, (base + int(at)) % WRAP, codes[k]))
    at += 200_000
    events.append((at, (base + int(at)) % WRAP, codes[count]))
    return events


def _bits_only(words):
    """The stamp pump's rule applied to code words (`pair_stamps`): a word with bits above
    the window is a counter, and anything else pairs with the word after it. The
    reference the fuzz measures itself against."""
    paired, index = [], 0
    above = ~((1 << DATA) - 1)
    while index + 1 < len(words):
        if words[index] & above:
            index += 1
            continue
        paired.append((_counter(words[index + 1]), words[index]))
        index += 2
    return paired


def _closed(batches, fifo_words=8):
    """Each word's closed flag by the pairer's rule (`CodeWordPairer`), from the batch
    sizes and levels alone, for the fuzz to check against the pushes themselves. A look
    whose read failed is passed over; a batch whose level after failed leaves none."""
    flags, previous, after = [], 0, None
    for batch, level_after, error in batches:
        if error is not None and not batch:
            continue
        for position in range(len(batch)):
            popping = after is None or position < after
            flags.append(position + 1 + (previous if popping else 0) < fifo_words)
        previous, after = len(batch), None if error is not None else level_after
    return flags


FUZZ_RUNS = 2_000


FUZZ_CASES = {
    # name: (near the wrap, the FIFO's depth, the depth the pump is told, read failures,
    #        levels read as the box's shim reads them)
    "far": (False, 8, 8, 0.0, False),
    "near-wrap": (True, 8, 8, 0.0, False),
    "joined-16": (False, 16, 16, 0.0, False),
    "told-less-than-real": (False, 16, 8, 0.0, False),
    "level-reads-fail": (False, 8, 8, 0.03, False),
    "near-wrap-level-reads-fail": (True, 8, 8, 0.03, False),
    "box-saturating-16": (False, 16, 16, 0.0, True),
    "box-saturating-16-fails": (False, 16, 16, 0.03, True),
}


@pytest.mark.parametrize("case_name", list(FUZZ_CASES))
def test_after_any_loss_the_fifo_can_make_no_code_word_is_written_wrong(case_name):
    """Fuzzed over the strobes' spacing, the drain's pace and its starvation, so over every
    way the FIFO loses words: lone data words, lone counters, whole pairs, and runs the
    bits cannot show. FUZZ_RUNS runs each, far from P's wrap and across it, each run with
    its own share of slow calls (0-40%) and late polls (0-8%).

    THE LEMMA, against the pushes themselves: after every word the counts call closed, the
    next word read is the next one pushed. THE RULE: every code word written is a strobe's
    own code at its own tick, each once and in order, so no wrong W. Every word read and
    not written is counted, at least a strobe for each two in a row, and never more
    strobes than were lost. The pump never stops, and the strobe 200 ms after each run's
    last is written. And the fuzz bites: the stamp pump's rule writes wrong code words on
    these same streams.

    Fix round 1 (Ruling R40) adds RP1's joined depth, 16 (pio_rp1.c:985 at utils b57c48e,
    doubled); a pump told less than the real depth, which errs safe; and level reads that
    fail, 3% of them, at the look and after popping, each handed to the pump as the drain
    hands it (`level_error`). Fix round 3 adds levels read as the box's shim reads them,
    from a saturating field and a status register read at instants of their own
    (`_through_the_fifo`'s `answer`): the lemma is checked against the pushes there too."""
    import random

    from wl_sync.rp1.pio_capture import WordPump

    near_wrap, depth, told, fails, box = FUZZ_CASES[case_name]
    answer = None
    if box:
        from tests import fifo_state_c

        answer = fifo_state_c.answer()
        if answer is None:
            pytest.skip("no C compiler on this host")
    rng = random.Random(f"120-{case_name}")
    totals = dict.fromkeys(
        ("strobes", "written", "lost_reported", "words_dropped", "whole_dropped",
         "runs_with_a_loss", "bits_only_wrong"), 0)
    for case in range(FUZZ_RUNS):
        events = _fuzzed_strobes(rng, near_wrap)
        batches = _through_the_fifo(
            rng, events, rng.uniform(0, 0.4), rng.uniform(0, 0.08), depth, fails, answer
        )
        pushes = [push for batch, _, _ in batches for push, _ in batch]
        words = [word for batch, _, _ in batches for _, word in batch]
        for k, closed in enumerate(_closed(batches, told)):
            if closed and k + 1 < len(pushes):
                assert pushes[k + 1] == pushes[k] + 1, (case, k, "a closed word lost one")

        code_at = {tick: data for _, tick, data in events}
        order = {tick: k for k, (_, tick, _) in enumerate(events)}
        written, lost = [], []
        pump = WordPump(
            on_word=lambda tick, word: written.append((tick, word)),
            data_count=DATA,
            on_loss=lambda loss: lost.append(loss.count),
            fifo_words=told,
        )
        for batch, level_after, error in batches:
            assert pump.handle(
                [w for _, w in batch], level_after=level_after, level_error=error
            ) is True
        assert pump.finish() is True
        wrong = [(t, w) for t, w in written if code_at.get(t) != w]
        assert wrong == [], (case, wrong)
        positions = [order[t] for t, _ in written]
        assert positions == sorted(set(positions)), (case, positions)
        assert written[-1] == (events[-1][1], events[-1][2]), (case, "capture did not go on")
        dropped = len(words) - 2 * len(written)
        assert sum(lost) <= len(events) - len(written), (case, lost)
        assert 2 * sum(lost) >= dropped, (case, lost, dropped)
        assert all(count >= 1 for count in lost)
        assert (pump.desyncs >= 1) == bool(lost) and pump.desyncs <= len(lost)

        taken = set(pushes)
        whole = {k for k in range(len(events)) if {2 * k, 2 * k + 1} <= taken}
        totals["whole_dropped"] += len(whole) - len(written)
        totals["bits_only_wrong"] += sum(code_at.get(t) != w for t, w in _bits_only(words))
        totals["runs_with_a_loss"] += len(taken) < 2 * len(events)
        totals["strobes"] += len(events)
        totals["written"] += len(written)
        totals["lost_reported"] += sum(lost)
        totals["words_dropped"] += dropped
    print(f"\n{case_name}: {totals}")
    assert totals["words_dropped"] > 500 and totals["bits_only_wrong"] > 20, totals


def test_a_pump_told_a_deeper_fifo_than_the_real_one_writes_wrong_code_words():
    """WHY THE DEPTH IS MEASURED AT START-UP (Ruling R40). The proof's first fact is the
    FIFO's depth, and only one error of it breaks the proof: a pump told more than the FIFO
    holds calls words closed after which a push was dropped. Here the FIFO is 8 deep, as
    RP1's is unjoined (pio_rp1.c:985 at utils b57c48e), and the pump is told 16, as it would
    be were the join assumed and not taken. It writes wrong code words: the backend's
    start-up check (Rp1Backend._measure_fifo_depth) is what keeps that off the box."""
    import random

    from wl_sync.rp1.pio_capture import WordPump

    rng = random.Random("told-more")
    wrong = 0
    for _ in range(400):
        events = _fuzzed_strobes(rng, False)
        code_at = {tick: data for _, tick, data in events}
        written = []
        pump = WordPump(on_word=lambda t, w: written.append((t, w)), data_count=DATA,
                        fifo_words=16)
        for batch, level_after, error in _through_the_fifo(
            rng, events, rng.uniform(0, 0.4), rng.uniform(0, 0.08), 8
        ):
            pump.handle([w for _, w in batch], level_after=level_after, level_error=error)
        wrong += sum(code_at.get(t) != w for t, w in written)
    assert wrong > 0


# --- The stamp machine's pump (Task 1.10) -------------------------------------------------
#
# The stamp machine runs the capture program with GPIO0-15 as its data window, so its
# FIFO carries the same (data, counter) pairs and `pair_fifo`'s upper-half check holds.
# What it hands over is the counter alone: the P count of each rising edge on its pin.


def test_a_stamp_pump_hands_over_its_stamps_in_order():
    from wl_sync.rp1.pio_capture import StampPump

    pump = StampPump(data_count=DATA)
    assert pump.handle(stream((10, 0), (20, 0))) is True
    assert pump.take() == [10, 20]
    assert pump.take() == []
    assert pump.error is None


def test_a_full_stamp_fifo_is_held_and_the_pump_continues():
    """As for the word pump: whole pairs may have gone, and the stream is still intact."""
    from wl_sync.rp1.pio_capture import StampPump, WordsDroppedError

    pump = StampPump(data_count=DATA)
    assert pump.handle(stream((10, 0)), fifo_was_full=True) is True
    assert isinstance(pump.error, WordsDroppedError)
    assert pump.take() == [10]


def test_a_desynced_stamp_stream_is_resynchronised_on_its_counter_and_the_pump_goes_on():
    """A slipped pairing would read counters as data and data as counters: stamps for
    edges that never happened. Until Task 1.15 the batch was refused and the pump
    stopped, and every later B was dropped for the rest of the process (final review
    I1). Now (the user's ruling, 2026-11-20) the word with bits above the data window,
    which only a counter can have, is taken as the counter it is, the stream is paired
    again from the word after it, and the pump goes on. The desync is held for
    check_health once, and counted."""
    from wl_sync.rp1.pio_capture import StampPump

    pump = StampPump(data_count=DATA)
    slipped = stream((10, 0), (20, 0))[1:] + [0]  # 10's data word lost; 30's data last
    assert pump.handle(slipped) is True
    assert pump.take() == [10, 20]
    error = pump.take_error()
    assert isinstance(error, DesyncError)
    assert "resynchronised" in str(error) and "1 desync(s) this run" in str(error)
    assert pump.desyncs == 1 and not pump.stopped
    # 30's data word was the batch's last, so it waits for its counter in the next.
    assert pump.handle([(~30) & 0xFFFF_FFFF] + stream((40, 0))[:1]) is True
    assert pump.take() == [30]
    assert pump.handle([(~40) & 0xFFFF_FFFF, 0]) is True
    assert pump.take() == [40]
    assert pump.error is None and pump.desyncs == 1, "the stream was in phase again"


def test_a_counter_left_alone_at_a_batchs_end_is_taken_as_a_counter():
    """A batch's last word, with no partner, is carried as a data word only when it could
    be one: bits above the window make it a counter, whose data word was lost, and it is
    queued now. Carried, it would wait for the next drain while the mark this drain sets
    said every stamp before it was queued."""
    from wl_sync.rp1.pio_capture import StampPump

    pump = StampPump(data_count=DATA)
    assert pump.handle([(~5) & 0xFFFF_FFFF, 0]) is True  # 5's data lost; 10's data last
    assert pump.take() == [5] and pump.desyncs == 1
    assert pump.handle([(~10) & 0xFFFF_FFFF, (~20) & 0xFFFF_FFFF]) is True  # 20's lost
    assert pump.take() == [10, 20] and pump.desyncs == 2
    assert pump.handle(stream((30, 0))) is True
    assert pump.take() == [30]


def test_a_lone_data_word_costs_its_stamp_and_one_stamp_just_short_of_the_wrap():
    """The other way a word is lost: a data word whose counter the full FIFO dropped. It
    pairs with the next data word, read as a counter, and nothing in the layout can tell:
    that is the blind spot pair_fifo has always had. The stamp read is 0xFFFFFFFF less a
    16-bit data word, within 65.5 ms before P's wrap, where no frame lies unless P is
    there (the backend's match places it, `tests/test_rp1_backend.py`). The counter after
    it is where the slip shows, and the stream is paired again from there."""
    from wl_sync.rp1.pio_capture import StampPump

    pump = StampPump(data_count=DATA)
    lone = stream((10, 0x0001)) + [0x0002] + stream((30, 0x0003), (40, 0x0004)) + [5]
    assert pump.handle(lone[:4]) is True  # 20's counter lost
    assert pump.handle(lone[4:]) is True
    assert pump.take() == [10, 0xFFFF_FFFF - 0x0003, 30, 40]
    assert pump.desyncs == 1


def _fuzzed_streams(cases):
    """`cases` streams of 6-12 stamps a few ms to a few s apart, P well clear of its wrap,
    each with its own data words, one word lost at random, and cut into batches of any
    size, as the stamp drain reads them."""
    import random

    rng = random.Random(115)
    for _ in range(cases):
        tick = rng.randrange(1_000_000, 4_000_000_000)
        events = []
        for _ in range(rng.randrange(6, 13)):
            tick += rng.randrange(10_000, 3_000_000)
            events.append((tick, rng.randrange(1 << DATA)))
        words = stream(*events)
        lost = rng.randrange(len(words))
        del words[lost]
        # The stamp drain reads every word the FIFO holds, so a batch can end anywhere,
        # a data word whose counter is not in it yet included.
        batches, at = [], 0
        while at < len(words):
            batches.append(words[at:at + rng.randrange(1, 8)])
            at += len(batches[-1])
        yield events, lost, batches


def test_after_a_lost_word_every_stamp_is_a_counter_the_machine_pushed_or_its_blind_spot():
    """Fuzzed over the position of the lost word, and so over its phase. Every stamp is
    either a counter the machine pushed, in order, or the lone-data-word stamp of the
    test above, past 0xFFFF0000. At most the lost word's own stamp is missing, one
    desync is counted when anything follows the slip, and the pump never stops."""
    from wl_sync.rp1.pio_capture import StampPump

    for events, lost, batches in _fuzzed_streams(400):
        pump = StampPump(data_count=DATA)
        for batch in batches:
            assert pump.handle(batch) is True
        taken = pump.take()
        real = [tick for tick, _ in events]
        genuine = [tick for tick in taken if tick < 0xFFFF_0000]
        assert all(tick in real for tick in genuine), (lost, taken, real)
        assert genuine == sorted(genuine), (lost, taken)
        delivered = sum(len(batch) for batch in batches)
        # Every pair wholly delivered, but the one the lost word belonged to.
        whole = min(len(real), (delivered + 1) // 2)
        assert len(genuine) >= whole - 1, (lost, taken, real, delivered)
        assert len(taken) - len(genuine) <= 1, (lost, taken)
        # The slip shows at the lost data word's counter, or at the counter after a lost
        # counter: either way within two words of the loss.
        assert pump.desyncs == (1 if lost + 2 <= delivered else pump.desyncs), lost
        assert pump.desyncs <= 1 and not pump.stopped


def test_stamps_through_follows_the_word_pumps_rule():
    from wl_sync.rp1.pio_capture import WORDS_LAG_US, StampPump

    pump = StampPump(data_count=DATA)
    polled_at = 3 * WORDS_LAG_US  # past the lag, so the mark is not wrapped below zero
    assert pump.stamps_through is None
    assert pump.handle([], polled_at=polled_at) is True
    assert pump.stamps_through == polled_at - WORDS_LAG_US - 1
    assert pump.handle([], polled_at=None) is True
    assert pump.stamps_through == polled_at - WORDS_LAG_US - 1
