import pytest

from wl_sync.rp1.pio_capture import accumulate_ticks, decode_fifo


def pack(tick_delta: int, word: int, data_count: int = 16) -> int:
    """PIO pushes one 32-bit entry: data in the low bits, tick delta above."""
    return (tick_delta << data_count) | word


def test_decodes_a_single_entry():
    assert decode_fifo([pack(50, 0x8001)], data_count=16) == [(50, 0x8001)]


def test_decodes_several_entries():
    entries = [pack(10, 0x0001), pack(20, 0x0002), pack(30, 0x0003)]
    assert decode_fifo(entries, data_count=16) == [(10, 0x0001), (20, 0x0002), (30, 0x0003)]


def test_masks_data_to_the_declared_width():
    """A narrower capture must not leak tick bits into the code word."""
    assert decode_fifo([pack(7, 0x00FF, data_count=8)], data_count=8) == [(7, 0x00FF)]


def test_accumulates_deltas_into_absolute_ticks():
    pairs = [(10, 0x1), (20, 0x2), (30, 0x3)]
    assert accumulate_ticks(pairs, t0=1_000) == [
        (1_010, 0x1),
        (1_030, 0x2),
        (1_060, 0x3),
    ]


def test_accumulate_on_empty_input():
    assert accumulate_ticks([], t0=5) == []


def test_rejects_impossible_data_width():
    with pytest.raises(ValueError):
        decode_fifo([0], data_count=33)
