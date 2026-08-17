import datetime

import pytest
from pydantic import ValidationError

from wl_sync.log import (
    TICK_WRAP_US,
    BarcodeEmitted,
    CodeWord,
    Edge,
    SyncBoxLogHeader,
    read_log,
    unwrap_ticks,
    write_log,
)

HEADER = SyncBoxLogHeader(
    schema_version=1,
    session_id="2027-03-14_01",
    rig="rig-a",
    boot_id="b7f1c2",
    written_at=datetime.datetime(2027, 3, 14, 11, 0, tzinfo=datetime.timezone.utc),
    gpio_map={"barcode_out": 17, "code_data_base": 2, "code_strobe": 18},
)


def test_monotonic_ticks_pass_through():
    assert unwrap_ticks([10, 20, 30]) == [10, 20, 30]


def test_ticks_unwrap_across_the_32_bit_boundary():
    raw = [TICK_WRAP_US - 100, TICK_WRAP_US - 50, 25, 75]
    assert unwrap_ticks(raw) == [
        TICK_WRAP_US - 100,
        TICK_WRAP_US - 50,
        TICK_WRAP_US + 25,
        TICK_WRAP_US + 75,
    ]


def test_ticks_unwrap_more_than_once():
    unwrapped = unwrap_ticks([TICK_WRAP_US - 10, 5, TICK_WRAP_US - 10, 5])
    assert unwrapped == sorted(unwrapped)
    assert unwrapped[-1] > 2 * TICK_WRAP_US


def test_empty_tick_list():
    assert unwrap_ticks([]) == []


def test_round_trip_mixed_records(tmp_path):
    records = [
        Edge(tick_us=100, gpio=17, level=1),
        CodeWord(tick_us=5_000, word=0x8001),
        Edge(tick_us=10_100, gpio=17, level=0),
        CodeWord(tick_us=11_000, word=0x0001),
    ]
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, records)
    header, read_back = read_log(path)
    assert header == HEADER
    assert read_back == records


def test_round_trip_with_no_records(tmp_path):
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [])
    header, records = read_log(path)
    assert header == HEADER
    assert records == []


def test_header_rejects_unknown_key():
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate({**HEADER.model_dump(mode="json"), "rgi": "rig-a"})


def test_header_rejects_naive_datetime():
    payload = HEADER.model_dump(mode="json")
    payload["written_at"] = "2027-03-14T11:00:00"
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(payload)


def test_header_rejects_malformed_session_id():
    payload = HEADER.model_dump(mode="json")
    payload["session_id"] = "March the 14th"
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(payload)


def test_barcode_records_round_trip(tmp_path):
    path = tmp_path / "syncbox.log"
    records = [
        BarcodeEmitted(tick_us=1_000, value=212_000_000),
        Edge(tick_us=1_500, gpio=26, level=0),
        BarcodeEmitted(tick_us=1_001_000, value=212_000_001),
    ]
    write_log(path, HEADER, records)
    _, read_back = read_log(path)
    assert read_back == records


def test_barcode_record_line_format(tmp_path):
    """The on-disk shape is a downstream contract, so pin the literal line."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [BarcodeEmitted(tick_us=1_000, value=212_000_000)])
    assert path.read_text().splitlines()[1] == "B,1000,212000000"


def test_unknown_record_type_raises(tmp_path):
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("X,1,2\n")
    with pytest.raises(ValueError):
        read_log(path)


def test_a_torn_final_line_is_dropped_not_parsed(tmp_path):
    """The shape a crash actually produces. A truncation that stops mid-field raised
    `not enough values to unpack` straight out of read_log, so the canonical reader
    could not open a crash-closed segment at all -- while summarise_segment, reading
    the same format, handled it."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [Edge(tick_us=100, gpio=17, level=1)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("E,123,26")  # no newline: torn
    _, records = read_log(path)
    assert records == [Edge(tick_us=100, gpio=17, level=1)]


def test_a_torn_line_that_parses_is_still_dropped(tmp_path):
    """The worse half. When the truncation lands on a comma boundary the remnant is a
    well-formed record, so read_log SUCCEEDED and returned a barcode that never
    existed, carrying a garbage alignment tick. A fabricated record is more dangerous
    than a raised error, and this is the reader wl-preproc uses."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [BarcodeEmitted(tick_us=1_000, value=500)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("B,999999,10")  # parses cleanly, and is not a record
    _, records = read_log(path)
    assert records == [BarcodeEmitted(tick_us=1_000, value=500)]


def test_read_log_and_summarise_segment_agree_on_a_torn_segment(tmp_path):
    """Two readers of one format must not disagree about what is in the file."""
    from wl_sync.manifest import summarise_segment

    path = tmp_path / "seg-000.log"
    write_log(path, HEADER, [BarcodeEmitted(tick_us=1_000, value=500)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("B,999999,10")
    _, records = read_log(path)
    summary = summarise_segment(path)
    assert [r.value for r in records if isinstance(r, BarcodeEmitted)] == [500]
    assert (summary.barcode_first, summary.barcode_last) == (500, 500)


def test_header_defaults_describe_a_single_trusted_segment():
    assert (HEADER.segment_index, HEADER.ordering) == (0, "per-path")
    assert (HEADER.clock_trusted, HEADER.clock_reason) == (True, "")


def test_header_carries_clock_distrust(tmp_path):
    header = HEADER.model_copy(
        update={"clock_trusted": False, "clock_reason": "ntp_unsynchronized"}
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [])
    read_back, _ = read_log(path)
    assert read_back.clock_trusted is False
    assert read_back.clock_reason == "ntp_unsynchronized"


def test_every_non_default_header_field_round_trips(tmp_path):
    """segment_index and ordering have defaults, so a round-trip built on HEADER alone
    passes whether or not they are written at all. Both are read by wl-preproc:
    segment_index orders the day, ordering tells it the file is per-path and needs
    merging."""
    header = HEADER.model_copy(
        update={
            "segment_index": 7,
            "ordering": "global",
            "clock_trusted": False,
            "clock_reason": "clock_before_epoch",
        }
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [])
    read_back, _ = read_log(path)
    assert read_back == header
    assert (read_back.segment_index, read_back.ordering) == (7, "global")
