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


# ---- decode_edges is linear, and decodes exactly what the linear scan did ----------
#
# wl-preproc pins this codec and decodes whole sessions with it, so making it fast must not
# change a single decoded frame. The reference below is decode_edges as it was before the
# bisect (wl-sync c186111): a level lookup that scans the edges from their start. It is kept
# here, not in the package, so that the package has one decoder.


def _reference_level_at(edges, tick_us):
    level = 0
    for edge_tick, edge_level in edges:
        if edge_tick > tick_us:
            break
        level = edge_level
    return level


def _reference_decode(edges, start_us=None):
    from wl_sync.barcode import BIT_SLOT_US, N_BITS, WRAPPER_US

    quarter = WRAPPER_US // 4
    ordered = sorted(edges)
    if not ordered:
        return []
    barcodes = []
    last_falling = start_us
    for tick, level in ordered:
        if level == 0:
            last_falling = tick
            continue
        if last_falling is None or tick - last_falling < IDLE_MIN_US:
            continue
        start = tick
        if ordered[-1][0] < start + FRAME_US:
            continue
        if _reference_level_at(ordered, start + quarter) != 1:
            continue
        if _reference_level_at(ordered, start + 3 * quarter) != 1:
            continue
        if _reference_level_at(ordered, start + WRAPPER_US + quarter) != 0:
            continue
        if _reference_level_at(ordered, start + WRAPPER_US + 3 * quarter) != 0:
            continue
        data_start = start + 2 * WRAPPER_US
        value = 0
        for bit in range(N_BITS):
            centre = data_start + bit * BIT_SLOT_US + BIT_SLOT_US // 2
            value = (value << 1) | _reference_level_at(ordered, centre)
        gap2 = data_start + N_BITS * BIT_SLOT_US
        if _reference_level_at(ordered, gap2 + WRAPPER_US // 2) != 0:
            continue
        if _reference_level_at(ordered, gap2 + WRAPPER_US + WRAPPER_US // 2) != 1:
            continue
        barcodes.append(Barcode(value=value, start_us=start))
        last_falling = None
    return barcodes


def _messy_record(rng, n_frames):
    """Frames with jitter, spurious glitches, duplicated and coincident edges, a cut-off
    tail: everything that makes a lookup's tie-breaking and boundaries matter."""
    edges = []
    for index in range(n_frames):
        tick = INTERVAL_US * (index + 1) + rng.randint(-2_000, 2_000)
        for level, duration in encode(rng.getrandbits(32)):
            edges.append((tick + rng.choice((0, 0, 0, -1, 1, 2_500)), level))
            tick += duration
        edges.append((tick, 0))
    for _ in range(n_frames * 3):  # glitches, some landing exactly on sample centres
        t = rng.randint(0, INTERVAL_US * (n_frames + 1))
        edges.append((t, rng.randint(0, 1)))
        if rng.random() < 0.3:
            edges.append((t, 1 - edges[-1][1]))  # a coincident opposite edge
    edges.extend(rng.sample(edges, k=len(edges) // 20))  # exact duplicates
    cut = rng.randint(len(edges) * 9 // 10, len(edges))
    return sorted(edges)[:cut]


def test_decode_matches_the_linear_scan_reference():
    """The bisect must decode exactly what the linear scan did, glitches and ties included.

    This guards an invariant rather than driving a change: it passes on the old code by
    construction, and fails if the faster lookup ever breaks a tie differently.
    """
    import random

    rng = random.Random(20261028)
    for trial in range(300):
        edges = _messy_record(rng, rng.randint(1, 6))
        start = rng.choice((None, 0, rng.randint(0, INTERVAL_US)))
        assert decode_edges(edges, start_us=start) == _reference_decode(edges, start), trial


def test_a_four_hour_record_decodes_in_seconds():
    """14,400 frames, one a second for four hours, as the box writes in a long session.
    The linear scan took 687 s on this in wl-phantom's tests (docs/next-session.md), and a
    day's record took hours; the bound leaves a slow CI runner ample room."""
    import time

    values = [1_000 + index for index in range(14_400)]
    edges = emit(values)
    began = time.perf_counter()
    decoded = decode_edges(edges, start_us=0)
    elapsed = time.perf_counter() - began
    assert [b.value for b in decoded] == values
    assert elapsed < 30, f"decoding four hours took {elapsed:.1f} s"
