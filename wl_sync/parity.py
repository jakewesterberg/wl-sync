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

  1. The header JSON has the same keys, in the same order, each of the same JSON type, and
     the same `schema_version` and `ordering`, which tell a reader how to read the rest.
  2. The records are the same sequence of types. A `W` is a `W`, in the same place.
  3. Each record has the same fields, each of the same kind (an integer, or not: an L
     record's empty fields are its None).
  4. A field that is not a tick holds the same value: a `W`'s word, an `E`'s gpio and level,
     a `B`'s value, an `L`'s path, gpio, count, bound and cause. These are the injected
     inputs, and `Recorder` must not change them.
  5. The trailer is present in both, with the same number of fields.

THE FAKE'S LOG IS MADE the way the box makes one: the header by `wl_sync.cli._segment_factory`,
the one place a header is defined (its docstring says this module depends on it); the
records by `Recorder` over a `FakeBackend` writing into a `SegmentWriter`; the trailer by
`SegmentWriter.close`. Its inputs are the box segment's own, in file order: each `W` is
injected as a word, each `E` as an edge, each `B` through `Recorder.record_barcode`, and each
`L` as the loss report a backend makes (`FakeBackend.inject_word_loss` / `inject_edge_loss`,
which `Recorder._on_loss` writes as an L record). The box segment's header is NOT used to
build the fake's, so a key the box adds or drops is a difference. The fake's header comes
from THIS checkout, so run it at the commit the box runs, or a header field added since will
read as a fault of the box.

IT STREAMS. Both segments are read a line at a time and compared in lockstep, stopping at the
first difference, so a soak's segment of tens of millions of records needs no more memory than
a short one (tests/test_parity.py bounds it).

A TORN FINAL LINE, the only shape a crash-closed segment ever has, is dropped, as
`wl_sync.log.iter_records` drops it, and the output says so. A segment with no trailer is
crash-closed: its fake is left unclosed too, and the output says that the trailer was not
compared. Any other malformed input (an
empty or garbage header, a record that does not parse) is a FAIL with its reason, not a crash.

WHAT IS NOT COMPARED, and why (`EXCLUDED`, which the command prints). Nothing else is excluded:
  * every record's `tick_us`, and an L record's `before_us` and `after_us`: the box's ticks are
    PIO counts scaled to SI us; the fake's pass through the identity scale from the injected
    tick, and the replay sets the fake's clock itself. (Whether those two fields are present
    or empty IS compared, as a field kind);
  * the header's values other than `schema_version` and `ordering`: `session_id`, `boot_id`,
    `written_at`, `process_uuid`, `segment_index`, `rig`, `clock_trusted`, `clock_reason`,
    `timebase` and `gpio_map` are facts about the box and its run, and the fake's are its own;
  * the JSON type of `barcode_delay_us`: the box has measured it (an integer) and the fake
    has no wire between its stamp and its edge, so it is null (`FakeBackend.barcode_delay_us`);
  * the trailer's values: its time and the box's counts and span are the box's.

A FIELD THAT CANNOT BE COMPARED IS LISTED, NOT SKIPPED QUIETLY.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from wl_sync.backend import CaptureLoss, FakeBackend
from wl_sync.clock import ClockTrust
from wl_sync.cli import _segment_factory
from wl_sync.log import BarcodeEmitted, CodeWord, Edge, Loss, iter_records
from wl_sync.pins import CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN, EDGE_PINS
from wl_sync.segment import SEGMENT_PATTERN, segment_index_of
from wl_sync.service import Recorder
from wl_sync.session import SessionId

# (what is not compared, why). Printed by the command, so a reader sees what PASS did not say.
EXCLUDED: tuple[tuple[str, str], ...] = (
    ("tick_us of every record, and an L record's before_us and after_us (whether they are "
     "present is compared)",
     "the box's ticks are PIO counts scaled to SI us; the fake's pass through the identity"),
    ("header values other than schema_version and ordering: session_id, boot_id, written_at, "
     "process_uuid, segment_index, rig, clock_trusted, clock_reason, timebase, gpio_map",
     "facts about the box and its run; the fake's are its own"),
    ("the JSON type of barcode_delay_us",
     "the box measured it (an integer); the fake has no wire, so it is null"),
    ("the trailer's values",
     "its time, count and barcode span are the box's own"),
)

# Header keys whose JSON type is not compared (the reason is in EXCLUDED).
_TYPE_EXCLUDED = frozenset({"barcode_delay_us"})
# Header keys whose VALUE is compared: they tell a reader how to read the rest of the file.
_VALUE_COMPARED = frozenset({"schema_version", "ordering"})

# The fields after the tick, by position in the line's fields, that are compared by value.
# W: word. E: gpio, level. B: value. L: path, gpio, count, bound, cause.
_COMPARED = {"W": (1,), "E": (1, 2), "B": (1,), "L": (1, 2, 3, 4, 5)}


class ShapeError(ValueError):
    """A segment too malformed to take a shape from; the message is the FAIL's reason."""


@dataclass(frozen=True)
class SegmentShape:
    """A segment reduced to what Step 4 compares. The records are not held: `records()`
    reads the file again, a line at a time, so a segment of any length costs no memory."""

    path: Path
    header: tuple[tuple[str, str, object], ...]  # (key, JSON type, value if compared)

    def records(self) -> Iterator[tuple]:
        """(kind, field count, kinds of fields, compared values), in file order. A torn final
        line is dropped, as iter_records drops it."""
        with self.path.open("r", encoding="utf-8") as handle:
            handle.readline()
            for line in handle:
                if not line.endswith("\n"):
                    break
                text = line.strip()
                if not text:
                    continue
                kind, *fields = text.split(",")
                if kind == "T":
                    yield ("T", len(fields), (), ())
                    continue
                compared = tuple(
                    fields[i] for i in _COMPARED.get(kind, ()) if i < len(fields)
                )
                yield (kind, len(fields), tuple(_field_kind(f) for f in fields), compared)


def _json_type(value) -> str:
    return "null" if value is None else type(value).__name__


def _field_kind(text: str) -> str:
    try:
        int(text)
    except ValueError:
        return "text"
    return "int"


def torn_final_line(path: Path) -> bool:
    """True when the file does not end in a newline: a crash caught mid-write."""
    size = path.stat().st_size
    if size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(size - 1)
        return handle.read(1) != b"\n"


def has_trailer(path: Path) -> bool:
    """True when the segment's last complete line is a trailer. Its absence is how a
    crash-closed segment looks (wl_sync/segment.py), so the fake is left unclosed to match."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - 4096))
        lines = handle.read().split(b"\n")
    complete = [line for line in lines[:-1] if line.strip()]  # the last piece has no newline
    return bool(complete) and complete[-1].startswith(b"T,")


def shape_of(path: Path) -> SegmentShape:
    """The shape of a segment file, read as text so it holds whatever the file holds, not
    what the reader would accept. Raises ShapeError for a header that is not a JSON object."""
    with path.open("r", encoding="utf-8") as handle:
        line = handle.readline()
    if not line.strip():
        raise ShapeError("header: the first line is empty or missing")
    try:
        header_json = json.loads(line)
    except ValueError as error:
        raise ShapeError(f"header: the first line is not JSON ({error})") from error
    if not isinstance(header_json, dict):
        raise ShapeError("header: the first line is JSON but not an object")
    header = tuple(
        (key, _json_type(value), value if key in _VALUE_COMPARED else None)
        for key, value in header_json.items()
    )
    return SegmentShape(path, header)


def first_difference(real: SegmentShape, fake: SegmentShape) -> str | None:
    """The first way `real` differs from `fake` in structure, or None. The records of both
    are read in lockstep and the comparison stops at the first difference."""
    real_keys = [key for key, _, _ in real.header]
    fake_keys = [key for key, _, _ in fake.header]
    for key in real_keys:
        if key not in fake_keys:
            return f"header: key {key!r} is in the box's segment and not in the fake's"
    for key in fake_keys:
        if key not in real_keys:
            return f"header: key {key!r} is in the fake's segment and not in the box's"
    if real_keys != fake_keys:
        return f"header: the keys are in another order: box {real_keys}, fake {fake_keys}"
    for (key, real_type, real_value), (_, fake_type, fake_value) in zip(real.header, fake.header):
        if key not in _TYPE_EXCLUDED and real_type != fake_type:
            return f"header: key {key!r} is {real_type} in the box's segment, {fake_type} in the fake's"
        if key in _VALUE_COMPARED and real_value != fake_value:
            return f"header: {key!r} is {real_value!r} in the box's segment, {fake_value!r} in the fake's"
    pairs = zip_longest(real.records(), fake.records())
    for number, (real_record, fake_record) in enumerate(pairs, start=1):
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


def _capture_loss(loss: Loss) -> CaptureLoss:
    """The report a backend would have made for this L record (wl_sync.backend.CaptureLoss),
    so Recorder._on_loss writes it back as the same record, up to its ticks."""
    mask = (1 << 32) - 1
    span = (
        loss.after_us - loss.before_us
        if loss.after_us is not None and loss.before_us is not None
        else None
    )
    return CaptureLoss(
        cause=loss.cause,
        count=loss.count,
        bound=loss.bound,
        gpio=loss.gpio,
        after=None if loss.after_us is None else loss.after_us & mask,
        span_us=span,
        # Reported just before the record at `after`: the L record takes that record's tick.
        ahead=loss.after_us is not None and loss.tick_us == loss.after_us,
    )


def replay(path: Path, folder: Path, close: bool = True) -> Path:
    """Write the fake's segment for the inputs in `path`, into the empty `folder`, and return it.

    Each record is injected in file order, a line at a time. The fake's clock is set to the
    record's tick before it is injected, so the tick is in the clock's contract
    (FakeBackend._require_in_contract) and `Recorder` unwraps it as it unwraps the box's.
    """
    backend = FakeBackend()
    target, make = _segment_factory(
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
    recorder.capture_edges(EDGE_PINS)  # what the box registers (wl_sync/cli.py:585)
    mask = (1 << 32) - 1
    for record in iter_records(path):
        backend.set_now_us(record.tick_us)
        if isinstance(record, CodeWord):
            backend.inject_word(record.tick_us & mask, record.word)
        elif isinstance(record, Edge):
            backend.inject_edge(record.gpio, record.level, record.tick_us & mask)
        elif isinstance(record, BarcodeEmitted):
            recorder.record_barcode(record.tick_us & mask, record.value)
        elif isinstance(record, Loss):
            report = _capture_loss(record)
            if record.path == "W":
                backend.inject_word_loss(report)
            else:
                backend.inject_edge_loss(report)
    if close:  # a crash-closed box segment has no trailer, so neither has its fake
        writer.close(datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc))
    else:
        writer.flush()
    return target


@dataclass
class ParityResult:
    path: Path
    difference: str | None
    torn: bool = False
    trailer: bool = True

    @property
    def passed(self) -> bool:
        return self.difference is None


def check_segment(path: Path, workdir: Path) -> ParityResult:
    """Replay `path` through the fake, into `workdir` (a folder of its own), and compare the
    two segments' shapes."""
    try:
        torn = torn_final_line(path)
        trailer = has_trailer(path)
        real = shape_of(path)
        fake_path = replay(path, workdir, close=trailer)
        return ParityResult(path, first_difference(real, shape_of(fake_path)), torn, trailer)
    except (ShapeError, ValueError, OSError) as error:
        # A header that is not JSON, a record that does not parse (iter_records' ValueError),
        # a record the log's own rules refuse, an unreadable file: a reason, not a traceback.
        return ParityResult(path, f"the segment cannot be checked: {error}")


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
    """The command's verdict and its text. The last line is PASS or FAIL. Each segment is
    replayed into a folder of its own under `workdir`, numbered, so two segments of the same
    name in different folders never share a fake."""
    segments = segments_of(paths)
    if not segments:
        return False, "FAIL: no segments to check"
    lines = [exclusions_text()]
    results = []
    for number, path in enumerate(segments):
        result = check_segment(path, workdir / f"{number:05d}")
        results.append(result)
        if not result.trailer:
            lines.append(
                f"  {path}: no trailer, so the segment was crash-closed: the fake is left "
                f"unclosed too, and the trailer is not compared"
            )
        if result.torn:
            lines.append(
                f"  {path}: torn final line dropped, as wl_sync.log.iter_records drops it"
            )
    failed = [result for result in results if not result.passed]
    if failed:
        first = failed[0]
        lines.append(f"FAIL {first.path}: {first.difference}")
        return False, "\n".join(lines)
    lines.append(
        f"PASS: {len(results)} segment(s) match the fake's in header keys and types, "
        f"schema_version and ordering, record types and order, fields, and the injected values"
    )
    return True, "\n".join(lines)
