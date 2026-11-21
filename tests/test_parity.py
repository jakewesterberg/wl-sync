"""Task 5b Step 4, the structural parity check (wl_sync/parity.py).

The plan's sentence (docs/superpowers/plans/2026-08-13-sync-box.md:1134): the bench capture
replayed through `Recorder` gives a log "byte-identical in structure to one produced by
`FakeBackend` given the same injected words". These tests pin what that means.
"""

import datetime
import json
from pathlib import Path

import pytest

from wl_sync import parity
from wl_sync.backend import FakeBackend
from wl_sync.log import BarcodeEmitted, CodeWord, Edge, Loss, SyncBoxLogHeader, read_log
from wl_sync.pins import GPIO_MAP
from wl_sync.segment import SegmentWriter
from wl_sync.service import Recorder

CLOSED = datetime.datetime(2026, 11, 21, 18, 0, tzinfo=datetime.timezone.utc)
FRAME_GPIO = 26


def _header(**fields):
    base = dict(
        schema_version=1,
        session_id="2026-11-21_01",
        rig="rig0",
        boot_id="abc",
        written_at=CLOSED,
        gpio_map=GPIO_MAP,
        segment_index=0,
        process_uuid="11111111-1111-4111-8111-111111111111",
        barcode_delay_us=0,
        timebase="rp1-pio-si",
    )
    base.update(fields)
    return SyncBoxLogHeader(**base)


def _segment(path: Path, records, header=None, close=True):
    writer = SegmentWriter(path, header or _header())
    for record in records:
        writer.write(record)
    if close:
        writer.close(CLOSED)
    return path


RECORDS = [
    CodeWord(tick_us=1_000, word=0x8001),
    Edge(tick_us=1_500, gpio=FRAME_GPIO, level=1),
    BarcodeEmitted(tick_us=2_000, value=77),
    CodeWord(tick_us=3_000, word=0x0001),
    Edge(tick_us=3_500, gpio=FRAME_GPIO, level=0),
]


def test_identical_shapes_pass(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    other = _segment(tmp_path / "other.log", RECORDS)
    assert parity.first_difference(parity.shape_of(real), parity.shape_of(other)) is None


def test_values_that_may_differ_do_not_fail(tmp_path):
    """Ticks, the id, the clock and the timebase are the listed exclusions."""
    real = _segment(tmp_path / "real.log", RECORDS)
    other = _segment(
        tmp_path / "other.log",
        [CodeWord(r.tick_us + 17, r.word) if isinstance(r, CodeWord)
         else Edge(r.tick_us + 17, r.gpio, r.level) if isinstance(r, Edge)
         else BarcodeEmitted(r.tick_us + 17, r.value) for r in RECORDS],
        header=_header(session_id="2026-11-22_09", boot_id="zzz", timebase="fake",
                       barcode_delay_us=None, process_uuid="22222222-2222-4222-8222-222222222222"),
    )
    assert parity.first_difference(parity.shape_of(real), parity.shape_of(other)) is None


def test_a_missing_record_type_fails_and_names_it(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    no_edges = _segment(tmp_path / "fake.log", [r for r in RECORDS if not isinstance(r, Edge)])
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(no_edges))
    assert message is not None
    assert "record 2" in message and "E" in message


def test_a_record_the_fake_lacks_at_the_end_fails(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    short = _segment(tmp_path / "fake.log", RECORDS[:-1])
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(short))
    assert message is not None and "record 5" in message


def test_an_extra_header_key_fails_and_names_it(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    fake = _segment(tmp_path / "fake.log", RECORDS)
    lines = real.read_text().split("\n")
    head = json.loads(lines[0])
    head["extra_field"] = 1
    lines[0] = json.dumps(head)
    real.write_text("\n".join(lines))
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "extra_field" in message and "header" in message


def test_a_header_key_of_another_type_fails(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    fake = _segment(tmp_path / "fake.log", RECORDS)
    lines = real.read_text().split("\n")
    head = json.loads(lines[0])
    head["segment_index"] = "0"
    lines[0] = json.dumps(head)
    real.write_text("\n".join(lines))
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "segment_index" in message and "str" in message


def test_a_reordered_record_fails_and_names_it(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    swapped = list(RECORDS)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    fake = _segment(tmp_path / "fake.log", swapped)
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "record 1" in message


def test_a_changed_word_fails(tmp_path):
    """The same injected words must come out as the same words."""
    real = _segment(tmp_path / "real.log", RECORDS)
    other = list(RECORDS)
    other[0] = CodeWord(tick_us=1_000, word=0x8002)
    fake = _segment(tmp_path / "fake.log", other)
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "record 1" in message


def test_a_missing_trailer_fails(tmp_path):
    real = _segment(tmp_path / "real.log", RECORDS)
    fake = _segment(tmp_path / "fake.log", RECORDS, close=False)
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "a trailer T" in message


def _loss(**fields):
    base = dict(tick_us=1_500, path="W", gpio=None, count=1, bound="at_least",
                cause="desync", before_us=1_500, after_us=None)
    base.update(fields)
    return Loss(**base)


def test_l_records_are_in_the_compared_sequence(tmp_path):
    """An L record the fake lacks, or has where the box has none, is a difference."""
    real = _segment(tmp_path / "real.log", [RECORDS[0], _loss(), *RECORDS[1:]])
    fake = _segment(tmp_path / "fake.log", RECORDS)
    message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
    assert message is not None and "record 2" in message and "L" in message
    message = parity.first_difference(parity.shape_of(fake), parity.shape_of(real))
    assert message is not None and "record 2" in message


def test_an_l_records_blank_pattern_is_compared(tmp_path):
    a = _segment(tmp_path / "a.log", [RECORDS[0], _loss(before_us=1_500), *RECORDS[1:]])
    b = _segment(tmp_path / "b.log", [RECORDS[0], _loss(before_us=None), *RECORDS[1:]])
    message = parity.first_difference(parity.shape_of(a), parity.shape_of(b))
    assert message is not None and "record 2" in message


def test_replay_writes_every_cause_of_l_record_through_the_fake(tmp_path):
    records = [
        CodeWord(tick_us=1_000, word=1),
        _loss(tick_us=1_000, before_us=1_000),
        Edge(tick_us=2_000, gpio=FRAME_GPIO, level=1),
        _loss(tick_us=2_100, path="E", gpio=FRAME_GPIO, count=2, bound="exact",
              cause="buffer_overflow", before_us=2_000, after_us=2_100),
        Edge(tick_us=2_100, gpio=FRAME_GPIO, level=0),
        _loss(tick_us=2_200, path="E", gpio=FRAME_GPIO, count=1, bound="at_least",
              cause="coalesced", before_us=2_100, after_us=2_200),
        Edge(tick_us=2_200, gpio=FRAME_GPIO, level=0),
        _loss(tick_us=2_300, path="E", gpio=None, count=1, bound="at_least",
              cause="unaccounted", before_us=2_200, after_us=2_300),
        Edge(tick_us=2_300, gpio=FRAME_GPIO, level=1),
    ]
    path = _segment(tmp_path / "seg-000.log", records)
    result = parity.check_segment(path, tmp_path / "work")
    assert result.passed, result.difference


def test_schema_version_and_ordering_are_compared(tmp_path):
    for key, value in (("ordering", "global"), ("schema_version", 2)):
        real = _segment(tmp_path / f"{key}.log", RECORDS)
        lines = real.read_text().split("\n")
        head = json.loads(lines[0])
        head[key] = value
        lines[0] = json.dumps(head)
        real.write_text("\n".join(lines))
        fake = _segment(tmp_path / f"{key}-fake.log", RECORDS)
        message = parity.first_difference(parity.shape_of(real), parity.shape_of(fake))
        assert message is not None and key in message, key


def test_a_segment_from_the_fake_round_trips(tmp_path):
    """A log written the box's way, from the fake, replays through the fake to the same
    shape: the real-format round trip."""
    backend = FakeBackend()
    header = _header(timebase=backend.timebase, barcode_delay_us=backend.barcode_delay_us)
    writer = SegmentWriter(tmp_path / "seg-000.log", header)
    recorder = Recorder(backend, sink=writer)
    recorder.capture_codes(2, 16, 18)
    recorder.capture_edges([FRAME_GPIO])
    backend.set_now_us(1_000)
    backend.inject_word(1_000, 0x8001)
    backend.inject_edge(FRAME_GPIO, 1, 1_200)
    recorder.record_barcode(1_300, 5)
    backend.inject_word(2_000, 0x0001)
    writer.close(CLOSED)
    result = parity.check_segment(tmp_path / "seg-000.log", tmp_path / "work")
    assert result.passed, result.difference
    _header_read, records = read_log(tmp_path / "seg-000.log")
    assert [type(r).__name__ for r in records] == ["CodeWord", "Edge", "BarcodeEmitted", "CodeWord"]


def test_a_real_looking_segment_past_a_counter_wrap_passes(tmp_path):
    """Ticks past 2^32 (the box's clock unwraps them), a scaled-timebase header."""
    wrap = 1 << 32
    records = [
        CodeWord(tick_us=wrap - 500, word=1),
        Edge(tick_us=wrap - 400, gpio=FRAME_GPIO, level=1),
        BarcodeEmitted(tick_us=wrap + 100, value=9),
        CodeWord(tick_us=wrap + 600, word=2),
        Edge(tick_us=wrap + 3 * 3_600_000_000, gpio=FRAME_GPIO, level=0),
    ]
    path = _segment(tmp_path / "seg-000.log", records)
    result = parity.check_segment(path, tmp_path / "work")
    assert result.passed, result.difference


def test_check_fails_on_a_segment_whose_shape_the_replay_cannot_reproduce(tmp_path):
    """An unknown record kind in the real file is a difference, not a crash."""
    path = _segment(tmp_path / "seg-000.log", RECORDS)
    text = path.read_text().split("\n")
    text.insert(2, "Q,1,2")
    path.write_text("\n".join(text))
    result = parity.check_segment(path, tmp_path / "work")
    assert not result.passed and "Q" in result.difference


def test_a_torn_final_line_is_dropped_as_iter_records_drops_it_and_said_so(tmp_path, capsys):
    from wl_sync import bench
    path = _segment(tmp_path / "seg-000.log", RECORDS, close=False)
    with path.open("a") as handle:
        handle.write("E,4294971296,26")  # no newline: a crash caught mid-write
    assert bench.main(["parity", str(path)]) == 0
    out = capsys.readouterr().out
    assert "torn final line" in out and "dropped" in out
    assert out.splitlines()[-1].startswith("PASS")


@pytest.mark.parametrize("header", ["", "\n", "not json\n", "[1, 2]\n"])
def test_a_bad_header_is_a_fail_with_a_reason_not_a_traceback(tmp_path, capsys, header):
    from wl_sync import bench
    path = tmp_path / "seg-000.log"
    path.write_text(header + "W,1,2\n")
    assert bench.main(["parity", str(path)]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("FAIL") and "header" in out


def test_a_record_that_does_not_parse_is_a_fail(tmp_path, capsys):
    from wl_sync import bench
    path = _segment(tmp_path / "seg-000.log", RECORDS)
    lines = path.read_text().split("\n")
    lines.insert(2, "E,1,2")
    path.write_text("\n".join(lines))
    assert bench.main(["parity", str(path)]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith("FAIL")


def test_a_directory_with_two_same_named_segments_checks_each_with_its_own_fake(tmp_path, capsys):
    from wl_sync import bench
    day = tmp_path / "day"
    for folder, records in (("unassigned/syncbox", RECORDS), ("2026-11-21_01/syncbox", RECORDS[:3])):
        (day / folder).mkdir(parents=True)
        _segment(day / folder / "seg-000.log", records)
    assert bench.main(["parity", str(day)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("PASS: 2 segment(s)")


def test_the_check_streams_a_long_segment_in_bounded_memory(tmp_path):
    """The old comparison held ~700 bytes per record (2 GB for 3,000,000). A segment of tens of
    millions of records must cost no more than a short one, so the peak is bounded here, well
    under what holding 60,000 records' shapes would take."""
    import tracemalloc

    def records():
        for k in range(30_000):
            yield CodeWord(tick_us=1_000 + 2 * k, word=k & 0xFFFF)
            yield Edge(tick_us=1_001 + 2 * k, gpio=FRAME_GPIO, level=k & 1)

    path = _segment(tmp_path / "seg-000.log", records())
    tracemalloc.start()
    try:
        result = parity.check_segment(path, tmp_path / "work")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.passed, result.difference
    assert peak < 8_000_000, peak


def test_the_command_prints_pass_and_the_exclusions(tmp_path, capsys):
    from wl_sync import bench
    path = _segment(tmp_path / "seg-000.log", RECORDS)
    assert bench.main(["parity", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("PASS")
    assert "not compared" in out
    for name in ("tick_us", "timebase", "process_uuid", "written_at", "gpio_map"):
        assert name in out


def test_the_command_prints_fail_with_the_first_difference(tmp_path, capsys):
    from wl_sync import bench
    path = _segment(tmp_path / "seg-000.log", RECORDS)
    lines = path.read_text().split("\n")
    head = json.loads(lines[0])
    del head["rig"]
    lines[0] = json.dumps(head)
    path.write_text("\n".join(lines))
    assert bench.main(["parity", str(path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "rig" in out
