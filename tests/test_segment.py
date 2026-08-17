import datetime
from pathlib import Path

from wl_sync.log import BarcodeEmitted, Edge, read_log
from wl_sync.segment import (
    SegmentWriter,
    next_segment_index,
    segment_index_of,
    segment_name,
)
from tests.test_log import HEADER

UTC = datetime.timezone.utc
CLOSED_AT = datetime.datetime(2026, 8, 16, 17, 0, tzinfo=UTC)


def test_segment_names_are_zero_padded_and_sort_lexically():
    assert segment_name(0) == "seg-000.log"
    assert segment_name(12) == "seg-012.log"
    assert sorted([segment_name(2), segment_name(10)]) == [segment_name(2), segment_name(10)]


def test_first_segment_in_a_new_directory_is_zero(tmp_path):
    assert next_segment_index(tmp_path) == 0


def test_next_index_follows_the_highest_present(tmp_path):
    (tmp_path / segment_name(0)).touch()
    (tmp_path / segment_name(1)).touch()
    assert next_segment_index(tmp_path) == 2


def test_next_index_ignores_unrelated_files(tmp_path):
    (tmp_path / segment_name(0)).touch()
    (tmp_path / "manifest.json").touch()
    assert next_segment_index(tmp_path) == 1


def test_records_are_readable_before_close(tmp_path):
    """The whole point of streaming: a crash keeps everything already written."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.write(Edge(tick_us=1_500, gpio=26, level=0))
    writer.flush()
    _, records = read_log(path)
    assert records == [
        BarcodeEmitted(tick_us=1_000, value=212_000_000),
        Edge(tick_us=1_500, gpio=26, level=0),
    ]


def test_a_clean_close_writes_a_trailer(tmp_path):
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.write(BarcodeEmitted(tick_us=2_000, value=212_000_001))
    writer.close(CLOSED_AT)
    assert path.read_text().splitlines()[-1] == (
        f"T,{CLOSED_AT.isoformat()},212000000,212000001,2"
    )


def test_an_unclosed_segment_has_no_trailer(tmp_path):
    """Simulates a crash: the writer is abandoned without close()."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=212_000_000))
    writer.flush()
    assert not path.read_text().splitlines()[-1].startswith("T,")


def test_barcode_span_tracks_what_was_written(tmp_path):
    writer = SegmentWriter(tmp_path / segment_name(0), HEADER)
    assert writer.barcode_first is None and writer.barcode_last is None
    writer.write(BarcodeEmitted(tick_us=1_000, value=500))
    writer.write(Edge(tick_us=1_200, gpio=26, level=0))
    writer.write(BarcodeEmitted(tick_us=2_000, value=501))
    assert (writer.barcode_first, writer.barcode_last, writer.record_count) == (500, 501, 3)


def test_segment_index_of_real_segment_name():
    """segment_index_of should extract the index from a valid segment name."""
    assert segment_index_of(Path("seg-000.log")) == 0
    assert segment_index_of(Path("seg-012.log")) == 12
    assert segment_index_of(Path("seg-999.log")) == 999


def test_segment_index_of_non_segment_name():
    """segment_index_of should return None for non-segment names."""
    assert segment_index_of(Path("manifest.json")) is None
    assert segment_index_of(Path("seg-001")) is None
    assert segment_index_of(Path("seg-abc.log")) is None
    assert segment_index_of(Path("foo.log")) is None


def test_trailer_with_no_barcodes(tmp_path):
    """A segment closed having received no BarcodeEmitted records encodes absent span as empty fields."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(Edge(tick_us=1_000, gpio=26, level=1))
    writer.write(Edge(tick_us=2_000, gpio=26, level=0))
    writer.close(CLOSED_AT)
    assert path.read_text().splitlines()[-1] == (
        f"T,{CLOSED_AT.isoformat()},,,2"
    )


def test_read_log_skips_trailer_on_closed_segment(tmp_path):
    """read_log round-trips a cleanly-closed segment, returning only records (not trailer)."""
    path = tmp_path / segment_name(0)
    writer = SegmentWriter(path, HEADER)
    writer.write(BarcodeEmitted(tick_us=1_000, value=500))
    writer.write(Edge(tick_us=1_500, gpio=26, level=0))
    writer.write(BarcodeEmitted(tick_us=2_000, value=501))
    writer.close(CLOSED_AT)
    _, records = read_log(path)
    assert records == [
        BarcodeEmitted(tick_us=1_000, value=500),
        Edge(tick_us=1_500, gpio=26, level=0),
        BarcodeEmitted(tick_us=2_000, value=501),
    ]
