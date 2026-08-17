import datetime
import json

import pytest

from wl_sync.log import BarcodeEmitted
from wl_sync.manifest import build_manifest, summarise_segment, write_manifest
from wl_sync.segment import SegmentWriter, segment_name
from tests.test_log import HEADER

UTC = datetime.timezone.utc
CLOSED_AT = datetime.datetime(2026, 8, 16, 17, 0, tzinfo=UTC)


def _segment(day_dir, index, first, last, *, clean):
    writer = SegmentWriter(day_dir / segment_name(index), HEADER)
    for value in range(first, last + 1):
        writer.write(BarcodeEmitted(tick_us=value * 1_000_000, value=value))
    if clean:
        writer.close(CLOSED_AT)
    else:
        writer.flush()
    return writer


def test_a_closed_segment_summarises_as_clean(tmp_path):
    _segment(tmp_path, 0, 100, 103, clean=True)
    summary = summarise_segment(tmp_path / segment_name(0))
    assert (summary.closed, summary.barcode_first, summary.barcode_last) == ("clean", 100, 103)


def test_an_unclosed_segment_summarises_as_crash(tmp_path):
    _segment(tmp_path, 0, 100, 103, clean=False)
    assert summarise_segment(tmp_path / segment_name(0)).closed == "crash"


def test_a_torn_final_line_still_summarises(tmp_path):
    """A crash mid-write leaves a partial line. It must not raise, and the segment must
    still report the barcodes that DID land."""
    path = tmp_path / segment_name(0)
    _segment(tmp_path, 0, 100, 103, clean=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("B,999999,10")  # no newline: torn
    summary = summarise_segment(path)
    assert summary.closed == "crash"
    assert summary.barcode_last == 103


def test_a_gap_between_segments_is_reported(tmp_path):
    _segment(tmp_path, 0, 1_000, 4_200, clean=False)
    _segment(tmp_path, 1, 4_700, 9_000, clean=True)
    manifest = build_manifest(tmp_path)
    assert manifest["gaps"] == [{"after": 4_200, "before": 4_700, "seconds": 500}]


def test_contiguous_segments_report_no_gap(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    _segment(tmp_path, 1, 1_101, 1_200, clean=True)
    assert build_manifest(tmp_path)["gaps"] == []


def test_a_gap_survives_an_intervening_segment_with_no_barcodes(tmp_path):
    """A segment can be opened and then crash before its first barcode tick (Task 8's
    run() opens the SegmentWriter before entering the emit loop). Under systemd's
    Restart=always this shape repeats on every restart, so it is not rare. Zipping
    ADJACENT summaries would let that all-None segment absorb both the gap before it
    and the gap after it, hiding the outage entirely. The gap must still be reported by
    carrying the last witnessed barcode forward across it."""
    _segment(tmp_path, 0, 100, 200, clean=True)
    SegmentWriter(tmp_path / segment_name(1), HEADER).flush()  # opened, no barcodes, crash
    _segment(tmp_path, 2, 5_000, 5_010, clean=True)
    manifest = build_manifest(tmp_path)
    assert manifest["gaps"] == [{"after": 200, "before": 5_000, "seconds": 4_800}]


def test_segments_are_ordered_by_index_not_by_name_luck(tmp_path):
    """seg-9 vs seg-10: lexically "seg-10" sorts FIRST, numerically it sorts second.
    Written unpadded on purpose — segment_name pads to 3, so a padded pair would not
    diverge until 1000 and this test would pass even against lexical sorting."""
    for name, first, last in (("seg-9.log", 1_000, 1_100), ("seg-10.log", 2_000, 2_100)):
        writer = SegmentWriter(tmp_path / name, HEADER)
        for value in range(first, last + 1):
            writer.write(BarcodeEmitted(tick_us=value * 1_000_000, value=value))
        writer.close(CLOSED_AT)
    assert [entry["file"] for entry in build_manifest(tmp_path)["segments"]] == [
        "seg-9.log",
        "seg-10.log",
    ]


def test_write_manifest_lands_on_disk(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    write_manifest(tmp_path)
    written = json.loads((tmp_path / "manifest.json").read_text())
    assert written["segments"][0]["barcode_last"] == 1_100


def test_the_manifest_carries_the_clock_reason_not_just_the_flag(tmp_path):
    """Spec Sec.7 requires clock_trusted: false "plus a machine-readable clock_reason in
    both the segment header AND the manifest". It was in the header only, and
    deploy/README.md calls the manifest the thing to read -- so the field saying WHY was
    missing from exactly where someone looks."""
    header = HEADER.model_copy(
        update={"clock_trusted": False, "clock_reason": "ntp_unsynchronized"}
    )
    SegmentWriter(tmp_path / segment_name(0), header).close(CLOSED_AT)

    entry = write_manifest(tmp_path)["segments"][0]
    assert list(entry) == [
        "file",
        "barcode_first",
        "barcode_last",
        "clock_trusted",
        "clock_reason",
        "closed",
    ]
    assert (entry["clock_trusted"], entry["clock_reason"]) == (False, "ntp_unsynchronized")


def test_a_trusted_segment_reports_an_empty_reason(tmp_path):
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    entry = build_manifest(tmp_path)["segments"][0]
    assert (entry["clock_trusted"], entry["clock_reason"]) == (True, "")


def test_one_unreadable_segment_does_not_cost_the_rest_of_the_day(tmp_path):
    """Media corruption raises UnicodeDecodeError out of `for line in handle`. That used
    to propagate out of summarise_segment, out of build_manifest, out of run() -- and
    since run() rebuilds the manifest BEFORE recording anything, one bad file meant the
    box could not record for the rest of that day, on every restart."""
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    (tmp_path / segment_name(1)).write_bytes(
        b'{"schema_version": 1}\n' + b"B,1000,\xff\xfe\n"
    )
    _segment(tmp_path, 2, 5_000, 5_010, clean=True)

    manifest = build_manifest(tmp_path)
    assert [entry["closed"] for entry in manifest["segments"]] == [
        "clean",
        "unreadable",
        "clean",
    ]
    bad = manifest["segments"][1]
    assert (bad["barcode_first"], bad["barcode_last"]) == (None, None)
    assert (bad["clock_trusted"], bad["clock_reason"]) == (False, "unreadable_segment")
    # The outage is still visible: the last witnessed barcode carries across it.
    assert manifest["gaps"] == [{"after": 1_100, "before": 5_000, "seconds": 3_900}]


def test_an_unreadable_segment_still_raises_for_a_direct_caller(tmp_path):
    """The degradation belongs to build_manifest, which has a day to protect.
    summarise_segment stays honest."""
    path = tmp_path / segment_name(0)
    path.write_bytes(b'{"schema_version": 1}\n' + b"B,1000,\xff\xfe\n")
    with pytest.raises(UnicodeDecodeError):
        summarise_segment(path)


def test_manifest_is_rebuilt_not_appended(tmp_path):
    """It is derived state. A stale manifest from a crashed run must be replaced
    wholesale, never merged into."""
    (tmp_path / "manifest.json").write_text(json.dumps({"segments": ["nonsense"]}))
    _segment(tmp_path, 0, 1_000, 1_100, clean=True)
    assert write_manifest(tmp_path)["segments"][0]["file"] == segment_name(0)
