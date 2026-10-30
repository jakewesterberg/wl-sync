import datetime
import json

import pytest
from pydantic import ValidationError

from wl_sync.clock import MAX_BARCODE
from wl_sync.log import (
    TICK_WRAP_US,
    BarcodeEmitted,
    CodeWord,
    Edge,
    Loss,
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

# A canonical UUID4, as `wl-sync record` mints one per process.
PROCESS_UUID = "0b4f1c7e-3d52-4a8e-9f61-2c7d8e9a0b13"


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


@pytest.mark.parametrize("lenient", ["2027-03-14_01\n", "٢٠٢٧-03-14_01"])
def test_header_rejects_a_session_id_that_is_not_canonical(lenient):
    """A header's id must print back as itself, because wl-preproc matches it against
    the folder name as text."""
    payload = HEADER.model_dump(mode="json")
    payload["session_id"] = lenient
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
            "process_uuid": PROCESS_UUID,
            "barcode_delay_us": 850,
        }
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [])
    read_back, _ = read_log(path)
    assert read_back == header
    assert (read_back.segment_index, read_back.ordering) == (7, "global")


# --- the per-process id and the barcode delay (2026-10-30) ------------------------------

# The header exactly as a box wrote it from 2026-08-17 until these two fields existed:
# no process_uuid, no barcode_delay_us.
BEFORE_THE_FIELDS = {
    "schema_version": 1,
    "session_id": "2026-10-19_00",
    "rig": "rig0",
    "boot_id": "5c0f6a52-7b43-4f0e-8d0c-3f1e2a9b8c71",
    "written_at": "2026-10-19T11:42:00.123456Z",
    "gpio_map": {"barcode_out": 17, "code_strobe": 18},
    "segment_index": 2,
    "ordering": "per-path",
    "clock_trusted": False,
    "clock_reason": "ntp_unsynchronized",
}


def test_a_header_written_before_the_process_uuid_and_delay_still_reads(tmp_path):
    """New readers must still read old logs: wl-preproc reads every segment the box has
    ever written with this model. Both fields are None there, meaning "written before
    this field existed" for the one and "unmeasured" for the other."""
    path = tmp_path / "seg-002.log"
    path.write_text(json.dumps(BEFORE_THE_FIELDS) + "\nB,1000,214573322\n", encoding="utf-8")

    header, records = read_log(path)

    assert header.process_uuid is None
    assert header.barcode_delay_us is None
    assert header.segment_index == 2
    assert records == [BarcodeEmitted(tick_us=1000, value=214573322)]


@pytest.mark.parametrize("delay_us", [0, 850])
def test_the_process_uuid_and_barcode_delay_round_trip(tmp_path, delay_us):
    """Zero is a measurement, not "unmeasured": a writer or reader that tested the delay
    for truth would turn a measured 0 into None."""
    header = SyncBoxLogHeader.model_validate(
        {
            **HEADER.model_dump(mode="json"),
            "process_uuid": PROCESS_UUID,
            "barcode_delay_us": delay_us,
        }
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [BarcodeEmitted(tick_us=1_000, value=212_000_000)])

    read_back, _ = read_log(path)

    assert read_back == header
    assert (read_back.process_uuid, read_back.barcode_delay_us) == (PROCESS_UUID, delay_us)


def test_a_header_written_before_the_timebase_still_reads(tmp_path):
    """None means "written before this field existed": the segment's clocks are whatever
    the box had then, which for every segment written so far is the split one."""
    path = tmp_path / "seg-002.log"
    path.write_text(json.dumps(BEFORE_THE_FIELDS) + "\nB,1000,214573322\n", encoding="utf-8")

    header, _ = read_log(path)

    assert header.timebase is None


@pytest.mark.parametrize("timebase", ["split", "fake", "rp1-pio"])
def test_the_timebase_round_trips_whatever_it_names(tmp_path, timebase):
    """FREE TEXT, NOT A CLOSED SET. A value this version does not define -- "rp1-pio",
    the one-clock build -- must still read, so that a reader pinned today reads the
    segments of a box built later. A closed set would cost wl-preproc a second pin move
    for each new arrangement of clocks."""
    header = SyncBoxLogHeader.model_validate(
        {**HEADER.model_dump(mode="json"), "timebase": timebase}
    )
    path = tmp_path / "syncbox.log"
    write_log(path, header, [])

    read_back, _ = read_log(path)

    assert read_back.timebase == timebase


def test_header_rejects_an_empty_timebase():
    """Empty is not "unknown": None says that. An empty string would be a writer that
    forgot to say, and a reader could not tell it from a deliberate value."""
    with pytest.raises(ValueError):
        SyncBoxLogHeader.model_validate({**HEADER.model_dump(mode="json"), "timebase": ""})


def test_header_rejects_a_negative_barcode_delay():
    """The B tick is read BEFORE the frame is emitted, so the lead edge rises after it,
    never before. A negative delay is a sign error, and a reader adding it would move
    every barcode the wrong way."""
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(
            {**HEADER.model_dump(mode="json"), "barcode_delay_us": -1}
        )


@pytest.mark.parametrize(
    "text",
    [
        "not-a-uuid",
        PROCESS_UUID.upper(),
        PROCESS_UUID.replace("-", ""),
        "{" + PROCESS_UUID + "}",
        "urn:uuid:" + PROCESS_UUID,
    ],
)
def test_header_rejects_a_process_uuid_not_in_canonical_form(text):
    """wl-preproc compares these ids to tell a roll from a restart, and compares them as
    text. The same UUID spelled two ways would read as two processes, and so as a
    restart, so only the canonical spelling is accepted."""
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(
            {**HEADER.model_dump(mode="json"), "process_uuid": text}
        )


def test_header_rejects_a_negative_segment_index():
    """`segment_index` orders the day for wl-preproc, so a negative one is not a
    smaller number -- it is a file that sorts before everything and claims to be the
    day's first. Nothing downstream re-derives it, so nothing downstream can catch it."""
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(
            {**HEADER.model_dump(mode="json"), "segment_index": -1}
        )


def test_header_rejects_an_ordering_it_does_not_define():
    """`ordering` is the field that tells a reader HOW to read the rest of the file:
    `per-path` means the records need a k-way merge, `global` means they are already
    sorted. No code in this package reads it -- wl-preproc does -- so a typo here is
    invisible on this side of the boundary and changes how the day is reconstructed on
    the other. A closed set is the only place that can be caught."""
    with pytest.raises(ValidationError):
        SyncBoxLogHeader.model_validate(
            {**HEADER.model_dump(mode="json"), "ordering": "per_path"}
        )


def test_all_three_record_types_round_trip_interleaved(tmp_path):
    """B alongside BOTH other types, which no existing round-trip covered.

    `test_round_trip_mixed_records` predates barcodes and carries only E and W;
    `test_barcode_records_round_trip` carries B and E. Nothing exercised all three
    together, which is the shape a real segment actually has -- and the reader
    dispatches on the type tag, so a B line landing between a W and an E is exactly
    where a mis-ordered branch would show.
    """
    records = [
        BarcodeEmitted(tick_us=1_000, value=209_034_000),
        CodeWord(tick_us=1_200, word=0x8001),
        Edge(tick_us=1_500, gpio=26, level=0),
        CodeWord(tick_us=2_000, word=0x0001),
        BarcodeEmitted(tick_us=1_001_000, value=209_034_001),
        Edge(tick_us=1_001_500, gpio=27, level=1),
    ]
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, records)

    _, read_back = read_log(path)

    assert read_back == records


def test_a_barcode_at_the_32_bit_ceiling_round_trips(tmp_path):
    """The largest encodable value, which is where a reader that narrowed the type
    would fail and nowhere else.

    Untested until now at either end. The value is unremarkable to Python -- ints are
    unbounded -- but it is the boundary `barcode.encode` enforces and the one a corrupt
    checkpoint lands on, so a log carrying it must survive the round trip rather than
    being the second failure in that story.
    """
    records = [
        BarcodeEmitted(tick_us=0, value=0),
        BarcodeEmitted(tick_us=1_000_000, value=MAX_BARCODE),
    ]
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, records)

    _, read_back = read_log(path)

    assert read_back == records
    assert read_back[1].value == (1 << 32) - 1


# --- L: capture events a path lost (2026-10-30) -----------------------------------------

# An edge loss as the edge pump finds it at the event that shows it, and a word loss with
# nothing after it, which leaves gpio and after_us empty.
OVERFLOW = Loss(
    tick_us=1_500, path="E", gpio=20, count=2, bound="exact",
    cause="buffer_overflow", before_us=1_000, after_us=1_500,
)
DESYNC = Loss(
    tick_us=8_500, path="W", gpio=None, count=3, bound="at_least",
    cause="desync", before_us=8_500, after_us=None,
)


def test_a_loss_record_round_trips_with_every_field(tmp_path):
    """Every field, the empty ones included: a W loss has no gpio, a loss with nothing of
    its path after it has no after_us, and one before anything of its path has no
    before_us. Empty must come back as None, never as 0, which is a real tick and a real
    GPIO."""
    records = [
        Edge(tick_us=1_000, gpio=20, level=1),
        OVERFLOW,
        Edge(tick_us=1_500, gpio=20, level=0),
        CodeWord(tick_us=8_500, word=0x8001),
        DESYNC,
        Loss(
            tick_us=12_000, path="E", gpio=None, count=1, bound="at_least",
            cause="unaccounted", before_us=None, after_us=11_000,
        ),
        Loss(
            tick_us=0, path="E", gpio=0, count=1, bound="at_least",
            cause="coalesced", before_us=0, after_us=0,
        ),
    ]
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, records)

    _, read_back = read_log(path)

    assert read_back == records
    assert (read_back[4].gpio, read_back[4].after_us) == (None, None)
    assert read_back[5].before_us is None
    assert (read_back[6].gpio, read_back[6].before_us) == (0, 0)


def test_loss_record_line_format(tmp_path):
    """The on-disk shape is the contract wl-preproc reads, so the literal lines are
    pinned, empty fields included: tick second, as on every record, then the path it
    belongs to, then what was lost, then the window, whose empty ends trail."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [OVERFLOW, DESYNC])
    assert path.read_text().splitlines()[1:] == [
        "L,1500,E,20,2,exact,buffer_overflow,1000,1500",
        "L,8500,W,,3,at_least,desync,8500,",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "L,1500,E,20,0,exact,buffer_overflow,1000,1500",  # a count that names no loss
        "L,1500,E,20,-2,exact,buffer_overflow,1000,1500",
        "L,1500,E,20,2,approximately,buffer_overflow,1000,1500",  # bound outside the set
        "L,1500,E,20,2,exact,overflow,1000,1500",  # a cause misspelt
        "L,1500,W,,1,at_least,coalesced,1000,1500",  # an edge cause on the word path
        "L,1500,E,20,1,at_least,desync,1000,1500",  # a word cause on the edge path
        "L,1500,B,,1,at_least,desync,1000,",  # B loses nothing: it is not captured
        "L,9000,W,20,3,at_least,desync,8500,",  # a code word has no line of its own
        "L,1500,E,-1,1,at_least,coalesced,1000,1500",
        "L,1500,E,20,2,exact,buffer_overflow,2000,1500",  # a window that ends first
        "L,1500,E,20,2,exact,buffer_overflow,1000",  # a field short
        "L,1500,E,20,2,exact,buffer_overflow,1000,1500,7",  # a field over
        "L,,E,20,2,exact,buffer_overflow,1000,1500",  # the ordering key is never empty
        "L,1500,E,20,,exact,buffer_overflow,1000,1500",  # nor is the count
        # Each cause's shape (`LOSS_SHAPES`): which fields it has, its bound, its tick.
        "L,1500,E,,2,exact,buffer_overflow,1000,1500",  # an overflow names its line
        "L,1500,E,,1,at_least,coalesced,1000,1500",  # so does a pair
        "L,1500,E,20,1,at_least,unaccounted,1000,1500",  # nothing says whose this was
        "L,1500,E,20,2,at_least,buffer_overflow,1000,1500",  # an overflow's count is exact
        "L,1500,E,20,1,exact,coalesced,1000,1500",  # a pair's is the least it can mean
        "L,1500,E,,1,exact,unaccounted,1000,1500",
        "L,8500,W,,3,exact,desync,8500,",
        "L,1500,W,,1,exact,fifo_full,,",
        "L,1500,E,20,2,exact,buffer_overflow,,1500",  # the line's event before the gap
        "L,1500,E,20,1,at_least,coalesced,1000,",  # the event that showed the pair
        "L,1500,E,,1,at_least,unaccounted,1000,",  # the event that showed the gap
        "L,8500,W,,3,at_least,desync,8500,9000",  # nothing of W follows a desync
        "L,1400,E,20,2,exact,buffer_overflow,1000,1500",  # written just before X, at X's tick
        "L,1600,E,20,1,at_least,coalesced,1000,1500",
        "L,1400,E,,1,at_least,unaccounted,1000,1500",  # never behind its window's end
        "L,9000,W,,3,at_least,desync,8500,",  # at the last code word's tick
        # Digits only, in every numeric field: no sign, no separator, no other script.
        "L,1_500,E,20,2,exact,buffer_overflow,1000,1500",
        "L,+1500,E,20,2,exact,buffer_overflow,1000,1500",
        "L,1500,E,20,2,exact,buffer_overflow, 1000,1500",
        "L,1500,E,20,\u0662,exact,buffer_overflow,1000,1500",
        "L,1500,E,20,2,exact,buffer_overflow,-1000,1500",
        "L,1500,E,0x14,2,exact,buffer_overflow,1000,1500",
    ],
)
def test_a_loss_record_the_format_does_not_define_is_refused(tmp_path, line):
    """No code in this package reads an L record -- wl-preproc does -- so a malformed one
    would be invisible on this side of the boundary and silently change what a session's
    timing check says on the other. The reader refuses it, as it refuses an unknown
    record type; the same checks stop the box from writing one."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    with pytest.raises(ValueError):
        read_log(path)


def test_the_box_cannot_make_a_loss_record_the_format_refuses():
    with pytest.raises(ValueError, match="count"):
        Loss(
            tick_us=1, path="E", gpio=20, count=0, bound="exact",
            cause="buffer_overflow", before_us=0, after_us=1,
        )
    with pytest.raises(ValueError, match="cause"):
        Loss(
            tick_us=1, path="W", gpio=None, count=1, bound="at_least",
            cause="coalesced", before_us=None, after_us=None,
        )
    with pytest.raises(ValueError, match="before_us"):
        Loss(
            tick_us=1, path="E", gpio=20, count=1, bound="at_least",
            cause="coalesced", before_us=-1, after_us=1,
        )


@pytest.mark.parametrize(
    "line",
    [
        "L,1500,E,20,2,exact,buffer_overflow,2000,1500",
        "L,1500,E,,1,at_least,unaccounted,1600,1500",
    ],
)
def test_a_window_that_ends_before_it_begins_is_refused_by_that_rule(tmp_path, line):
    """Each line keeps every other rule, the tick rule included, so only the window's
    own check can refuse it."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    with pytest.raises(ValueError, match="window ends before it begins"):
        read_log(path)


@pytest.mark.parametrize(
    ("line", "field"),
    [
        ("L,,E,20,2,exact,buffer_overflow,1000,1500", "tick_us"),
        ("L,1500,E,20,,exact,buffer_overflow,1000,1500", "count"),
        ("L,1500,E,+20,2,exact,buffer_overflow,1000,1500", "gpio"),
        ("L,1500,E,20,2,exact,buffer_overflow,1_000,1500", "before_us"),
        ("L,1500,E,20,2,exact,buffer_overflow,1000,15OO", "after_us"),
    ],
)
def test_a_malformed_loss_field_is_named_in_the_error(tmp_path, line, field):
    """`invalid literal for int() with base 10: ''` names no record and no field. A rig
    PC at 8am needs to know which."""
    path = tmp_path / "syncbox.log"
    write_log(path, HEADER, [])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    with pytest.raises(ValueError, match=f"an L record's {field} "):
        read_log(path)


def test_a_log_written_before_loss_records_existed_still_reads_and_holds_none(tmp_path):
    """Every segment the box wrote before 2026-10-30 has no L record, and the reader that
    knows L must still read them all."""
    path = tmp_path / "seg-002.log"
    path.write_text(
        json.dumps(BEFORE_THE_FIELDS)
        + "\nE,900,20,1\nW,1000,32769\nB,1000,214573322\n"
        + "T,2026-10-19T12:00:00+00:00,214573322,214573322,3\n",
        encoding="utf-8",
    )
    _, records = read_log(path)
    assert records == [
        Edge(tick_us=900, gpio=20, level=1),
        CodeWord(tick_us=1000, word=0x8001),
        BarcodeEmitted(tick_us=1000, value=214573322),
    ]
    assert not any(isinstance(record, Loss) for record in records)
