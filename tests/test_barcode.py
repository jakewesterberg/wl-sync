import pytest

from wl_sync.barcode import (
    FRAME_US,
    IDLE_MIN_US,
    INTERVAL_US,
    Barcode,
    decode_edges,
    edges_from_samples,
    encode,
)


def pulses_to_edges(pulses, start_us):
    edges = []
    tick = start_us
    for level, duration in pulses:
        edges.append((tick, level))
        tick += duration
    edges.append((tick, 0))
    return edges


def emit(values, first_start_us=INTERVAL_US):
    edges = []
    for index, value in enumerate(values):
        edges.extend(pulses_to_edges(encode(value), first_start_us + index * INTERVAL_US))
    return edges


def test_frame_is_exactly_200_ms():
    assert sum(duration for _, duration in encode(0xDEADBEEF)) == FRAME_US


def test_round_trip_single_barcode():
    assert decode_edges(emit([0xDEADBEEF]), start_us=0) == [
        Barcode(value=0xDEADBEEF, start_us=INTERVAL_US)
    ]


def test_first_frame_is_skipped_without_a_declared_start():
    """A line that starts LOW emits no edge, so the idle cannot be verified.
    Skipping is correct: a wrong barcode is far worse than a missing one."""
    assert decode_edges(emit([0xDEADBEEF])) == []


def test_round_trip_all_zero_and_all_one_payloads():
    values = [0x00000000, 0xFFFFFFFF]
    assert [b.value for b in decode_edges(emit(values), start_us=0)] == values


def test_round_trip_sequence_preserves_cadence():
    decoded = decode_edges(emit([1000, 1001, 1002, 1003]), start_us=0)
    assert [b.value for b in decoded] == [1000, 1001, 1002, 1003]
    assert decoded[1].start_us - decoded[0].start_us == INTERVAL_US


def test_partial_leading_frame_is_discarded():
    """A segment starting mid-frame must not yield a bogus barcode."""
    edges = emit([7, 8])
    truncated = [e for e in edges if e[0] > INTERVAL_US + 50_000]
    assert [b.value for b in decode_edges(truncated)] == [8]


def test_one_barcode_in_any_2_second_window():
    """A 3 s trial-length segment is always alignable. One barcode suffices:
    the timebase inherits clock rate from the device-level fit and needs only
    an offset (spec section 4.5)."""
    edges = emit(range(100, 120), 0)
    for offset in range(0, INTERVAL_US, 50_000):
        start = 5 * INTERVAL_US + offset
        inside = [(t, lvl) for t, lvl in edges if start <= t <= start + 2_000_000]
        assert len(decode_edges(inside)) >= 1, f"offset {offset} yielded none"


def test_two_barcodes_in_any_3_second_window():
    edges = emit(range(100, 120), 0)
    for offset in range(0, INTERVAL_US, 50_000):
        start = 5 * INTERVAL_US + offset
        inside = [(t, lvl) for t, lvl in edges if start <= t <= start + 3_000_000]
        assert len(decode_edges(inside)) >= 2, f"offset {offset} yielded too few"


def test_decode_from_sampled_trace_at_30khz():
    fs = 30_000.0
    trace = [0] * int(0.5 * fs)
    for level, duration in encode(0x0BADC0DE):
        trace.extend([level] * round(duration * 1e-6 * fs))
    trace.extend([0] * int(0.5 * fs))
    assert [b.value for b in decode_edges(edges_from_samples(trace, fs))] == [0x0BADC0DE]


def test_generator_jitter_does_not_break_decoding():
    """Barcode output is software-timed on Pi 5, so slot boundaries wander.
    5 ms slots make that harmless — this pins the tolerance."""
    edges = []
    tick = INTERVAL_US
    for index, (level, duration) in enumerate(encode(0xC0FFEE)):
        jitter = 400 if index % 2 else -400  # +/- 0.4 ms
        edges.append((tick + jitter, level))
        tick += duration
    edges.append((tick, 0))
    assert [b.value for b in decode_edges(edges, start_us=0)] == [0xC0FFEE]


def test_value_out_of_32_bit_range_rejected():
    with pytest.raises(ValueError):
        encode(1 << 32)
