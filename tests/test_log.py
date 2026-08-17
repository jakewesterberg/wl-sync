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
