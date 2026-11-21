"""Task 5b Step 4, "parity with the fake": does a real box's segment have the structure the
fake's log has, given the same injected inputs?

    python -m wl_sync.bench parity <segment, or a day directory> [<segment> ...]

THE PLAN'S SENTENCE (docs/superpowers/plans/2026-08-13-sync-box.md:1134): "Replay the bench
capture through `Recorder` and confirm the resulting log is byte-identical in structure to
one produced by `FakeBackend` given the same injected words. The Task 4 tests already pin
that shape, so this is the real payoff of the adapter."

WHAT THE TASK 4 TESTS PIN (tests/test_service.py, written in the plan at :695-800): a
`Recorder` over `FakeBackend` turns an injected word into a `CodeWord(tick_us, word)`, an
injected edge into an `Edge(tick_us, gpio, level)`, hands every record on in the order it
was delivered, and puts every tick on one unwrapped clock. wl_sync.log pins the rest: the
header's keys (SyncBoxLogHeader, extra="forbid") and each record's line (`record_line`).

"BYTE-IDENTICAL IN STRUCTURE" IS DEFINED HERE as all of these holding between the box's
segment and the segment the fake writes from the box's own inputs:

  1. The header JSON has the same keys, in the same order, each of the same JSON type.
  2. The records are the same sequence of types. A `W` is a `W`, in the same place.
  3. Each record has the same fields, each of the same kind (an integer or not).
  4. A field that is not a tick holds the same value: a `W`'s word, an `E`'s gpio and
     level, a `B`'s value. These are the injected words, and `Recorder` must not change them.
  5. The trailer is present in both, with the same number of fields.

THE FAKE'S LOG IS MADE the way the box makes one: the header by `wl_sync.cli._segment_factory`,
the one place a header is defined; the records by `Recorder` over a `FakeBackend` writing
into a `SegmentWriter`; the trailer by `SegmentWriter.close`. Its inputs are the box
segment's own, in file order: each `W` is injected as a word, each `E` as an edge, each `B`
through `Recorder.record_barcode`. The box segment's header is NOT used to build the fake's,
so a key the box adds or drops is a difference.

WHAT IS NOT COMPARED, and why (`EXCLUDED`, which the command prints):
  * every record's `tick_us`: the box's ticks count its PIO and are scaled to SI us
    (log.py `CodeWord`); the fake's pass through the identity scale from the injected tick,
    and the replay sets the fake's clock itself;
  * `L` records (counted and named in the output): a loss is reported by the backend that
    saw it, and `FakeBackend` has nothing to lose, so replaying one would mean inventing the
    report;
  * the header's values: `session_id`, `boot_id`, `written_at`, `process_uuid`,
    `segment_index`, `rig`, `clock_trusted`, `clock_reason`, `timebase`, `gpio_map` are
    facts about the box and its run, and the fake's are its own;
  * the JSON type of `barcode_delay_us`: the box has measured it (an integer) and the fake
    has no wire between its stamp and its edge, so it is null (`FakeBackend.barcode_delay_us`);
  * the trailer's values: its time and the box's counts and span are the box's.

A FIELD THAT CANNOT BE COMPARED IS LISTED, NOT SKIPPED QUIETLY. Nothing else is excluded.
"""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.clock import ClockTrust
from wl_sync.cli import _segment_factory
from wl_sync.log import BarcodeEmitted, CodeWord, Edge, iter_records
from wl_sync.pins import CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN, EDGE_PINS
from wl_sync.segment import SEGMENT_PATTERN, segment_index_of
from wl_sync.service import Recorder
from wl_sync.session import SessionId

# (what is not compared, why). Printed by the command, so a reader sees what PASS did not say.
EXCLUDED: tuple[tuple[str, str], ...] = (
    ("tick_us of every record",
     "the box's ticks are PIO counts scaled to SI us; the fake's pass through the identity"),
    ("L records",
     "the backend that saw a loss reports it, and FakeBackend has nothing to lose"),
    ("header values: session_id, boot_id, written_at, process_uuid, segment_index, rig, "
     "clock_trusted, clock_reason, timebase, gpio_map",
     "facts about the box and its run; the fake's are its own"),
    ("the JSON type of barcode_delay_us",
     "the box measured it (an integer); the fake has no wire, so it is null"),
    ("the trailer's values",
     "its time, count and barcode span are the box's own"),
)

# Header keys whose JSON type is not compared, with FakeBackend.barcode_delay_us's reason above.
_TYPE_EXCLUDED = frozenset({"barcode_delay_us"})

_COMPARED = {"W": (1,), "E": (1, 2), "B": (1,)}  # the fields after the tick that are compared


@dataclass(frozen=True)
class SegmentShape:
    """A segment reduced to what Step 4 compares."""

    header: tuple[tuple[str, str], ...]  # (key, JSON type), in file order
    records: tuple[tuple, ...]  # (kind, field count, kinds of fields, compared values)
    losses_excluded: int = 0


def _json_type(value) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _field_kind(text: str) -> str:
    try:
        int(text)
    except ValueError:
        return "text"
    return "int"


def shape_of(path: Path) -> SegmentShape:
    """The shape of a segment file, read as text so it holds whatever the file holds,
    not what the reader would accept."""
    with path.open("r", encoding="utf-8") as handle:
        header_line = handle.readline()
        body = [line for line in handle.read().split("\n") if line.strip()]
    header_json = json.loads(header_line)
    header = tuple((key, _json_type(value)) for key, value in header_json.items())
    records: list[tuple] = []
    losses = 0
    for line in body:
        kind, *fields = line.split(",")
        if kind == "L":
            losses += 1
            continue
        if kind == "T":
            records.append(("T", len(fields), (), ()))
            continue
        compared = tuple(fields[i] for i in _COMPARED.get(kind, ()) if i - 1 < len(fields))
        records.append((kind, len(fields), tuple(_field_kind(f) for f in fields), compared))
    return SegmentShape(header, tuple(records), losses)


def first_difference(real: SegmentShape, fake: SegmentShape) -> str | None:
    """The first way `real` differs from `fake` in structure, or None."""
    real_keys = [key for key, _ in real.header]
    fake_keys = [key for key, _ in fake.header]
    for key in real_keys:
        if key not in fake_keys:
            return f"header: key {key!r} is in the box's segment and not in the fake's"
    for key in fake_keys:
        if key not in real_keys:
            return f"header: key {key!r} is in the fake's segment and not in the box's"
    if real_keys != fake_keys:
        return f"header: the keys are in another order: box {real_keys}, fake {fake_keys}"
    for (key, real_type), (_, fake_type) in zip(real.header, fake.header):
        if key not in _TYPE_EXCLUDED and real_type != fake_type:
            return f"header: key {key!r} is {real_type} in the box's segment, {fake_type} in the fake's"
    longest = max(len(real.records), len(fake.records))
    for number in range(1, longest + 1):
        real_record = real.records[number - 1] if number <= len(real.records) else None
        fake_record = fake.records[number - 1] if number <= len(fake.records) else None
        if real_record == fake_record:
            continue
        if real_record is None:
            return f"record {number}: the box's segment has ended, the fake's has {_describe(fake_record)}"
        if fake_record is None:
            return f"record {number}: the box's segment has {_describe(real_record)}, the fake's has ended"
        return (f"record {number}: the box's segment has {_describe(real_record)}, "
                f"the fake's has {_describe(fake_record)}")
    return None


def _describe(record: tuple) -> str:
    kind, count, kinds, compared = record
    if kind == "T":
        return f"a trailer T with {count} fields"
    return f"{kind} with {count} fields {list(kinds)}, compared values {list(compared)}"


def replay(path: Path, folder: Path) -> Path:
    """Write the fake's segment for the inputs in `path`, into `folder`, and return it.

    Each record is injected in file order. The fake's clock is set to the record's tick
    before it is injected, so the tick is in the clock's contract (FakeBackend._require_in_contract)
    and `Recorder` unwraps it as it unwraps the box's.
    """
    backend = FakeBackend()
    _target, make = _segment_factory(
        folder,
        SessionId(datetime.date(2026, 11, 21), 1),
        rig="parity",
        written_at=datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc),
        trust=ClockTrust(True, ""),
        process_uuid=str(uuid.uuid4()),
        barcode_delay_us=backend.barcode_delay_us,
        timebase=backend.timebase,
    )
    writer = make()
    recorder = Recorder(backend, sink=writer)
    recorder.capture_codes(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN)
    pins = {record.gpio for record in iter_records(path) if isinstance(record, Edge)}
    recorder.capture_edges(sorted(pins | set(EDGE_PINS)))
    mask = (1 << 32) - 1
    for record in iter_records(path):
        if isinstance(record, CodeWord):
            backend.set_now_us(record.tick_us)
            backend.inject_word(record.tick_us & mask, record.word)
        elif isinstance(record, Edge):
            backend.set_now_us(record.tick_us)
            backend.inject_edge(record.gpio, record.level, record.tick_us & mask)
        elif isinstance(record, BarcodeEmitted):
            backend.set_now_us(record.tick_us)
            recorder.record_barcode(record.tick_us & mask, record.value)
        # An L record is not an input: EXCLUDED says why.
    writer.close(datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc))
    return folder / next(iter(sorted(p.name for p in folder.glob(SEGMENT_PATTERN))))


@dataclass
class ParityResult:
    path: Path
    difference: str | None
    losses_excluded: int = 0
    records: int = 0

    @property
    def passed(self) -> bool:
        return self.difference is None


def check_segment(path: Path, workdir: Path) -> ParityResult:
    """Replay `path` through the fake and compare the two segments' shapes."""
    real = shape_of(path)
    folder = workdir / path.stem
    try:
        fake_path = replay(path, folder)
    except ValueError as error:  # a record kind the reader does not know, a malformed field
        return ParityResult(path, f"the segment cannot be replayed: {error}", real.losses_excluded)
    fake = shape_of(fake_path)
    return ParityResult(path, first_difference(real, fake), real.losses_excluded, len(real.records))


def segments_of(paths: list[Path]) -> list[Path]:
    """Each segment file as given, and every segment of a directory, in index order."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(
                (p for p in path.rglob(SEGMENT_PATTERN) if segment_index_of(p) is not None),
                key=lambda p: (str(p.parent), segment_index_of(p)),
            ))
        else:
            found.append(path)
    return found


def exclusions_text() -> str:
    return "\n".join(f"  not compared: {what} ({why})" for what, why in EXCLUDED)


def run(paths: list[Path], workdir: Path) -> tuple[bool, str]:
    """The command's verdict and its text. The last line is PASS or FAIL."""
    segments = segments_of(paths)
    if not segments:
        return False, "FAIL: no segments to check"
    results = [check_segment(path, workdir) for path in segments]
    lines = [exclusions_text()]
    for result in results:
        if result.losses_excluded:
            lines.append(f"  {result.path}: {result.losses_excluded} L record(s) not compared")
    failed = [result for result in results if not result.passed]
    if failed:
        first = failed[0]
        lines.append(f"FAIL {first.path}: {first.difference}")
        return False, "\n".join(lines)
    lines.append(
        f"PASS: {len(results)} segment(s) match the fake's in header keys and types, record "
        f"types and order, fields, and the injected values"
    )
    return True, "\n".join(lines)
