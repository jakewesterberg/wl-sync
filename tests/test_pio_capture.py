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
    the same wrap period as now_us(), so Recorder's existing per-path unwrapper covers
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
