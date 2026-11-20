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


def test_a_desync_stops_the_word_pump_and_is_held():
    """A single lost word slips the pairing, and from then on every data word is read
    as a counter and every counter as a data word. Those are plausible timestamps for
    events that never happened, so the pump stops rather than recording them."""
    from wl_sync.rp1.pio_capture import WordPump

    seen = []
    pump = WordPump(on_word=lambda t, w: seen.append(w), data_count=DATA)
    slipped = stream((10, 1), (20, 2))[1:] + [0]
    assert pump.handle(slipped) is False
    assert seen == []
    assert isinstance(pump.error, DesyncError)


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
