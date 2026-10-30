"""Sync box on-disk log.

One JSON header line, then one CSV line per record, type-tagged:

    E,tick_us,gpio,level     a transition on a single line
    W,tick_us,word           a 16-bit event code word latched on a strobe edge
    B,tick_us,value          a barcode frame this box emitted
    L,tick_us,path,gpio,count,bound,cause,before_us,after_us
                             events a capture path saw and could not record (`Loss`)

Three of them record what happened, because three things ARRIVE DIFFERENTLY, and
only two of the three are captures at all:

  E and W differ in how RP1 delivers them. A strobed parallel word is one FIFO
  entry; reward, lick and loopback lines are individual transitions. Flattening
  those into one shape would throw away the distinction and force the reader to
  reconstruct it.

  B is not captured by anything. This box EMITS it, once per second, and every
  other device records it for clock alignment -- so the copy kept here is the
  emitter's own account of what it put on the wire, not a measurement of it.
  Reading the RP1 rationale onto B is the mistake to avoid: it explains why E and
  W are separate from each other, and says nothing about why B exists.

The fourth, L, records no event at all (added 2026-10-30). It marks events that E or
W lost -- a kernel buffer that overflowed, a FIFO pairing that slipped -- in the
segment being written when the box finds the loss, so a reader meets the hole in the
stream instead of decoding across it. It belongs to the path it reports on, and sits
in that path's stream.

Plain text so it is readable with standard tools on a rig PC at 8am, which is
when it matters.

RP1's timebase counter wraps, so raw ticks must be unwrapped before use.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wl_sync.session import SessionId

TICK_WRAP_US = 1 << 32
SCHEMA_VERSION = 1


class SyncBoxLogHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    session_id: str
    rig: str
    boot_id: str
    written_at: datetime.datetime
    gpio_map: dict[str, int]
    # `ge=0` because this ORDERS THE DAY for wl-preproc. A negative index is not a
    # smaller number; it is a file claiming to precede the day's first segment, and
    # nothing downstream re-derives it, so nothing downstream can catch it.
    segment_index: int = Field(default=0, ge=0)
    # Records reach disk in ARRIVAL order, and the THREE capture paths -- edges (E),
    # strobed code words (W) and this box's own barcodes (B) -- deliver independently,
    # so the file is ordered WITHIN each path and not across them. A reader does a
    # three-way merge. An L record is in the stream of the path it reports on, E or W
    # (`Loss`, ORDER). Declared rather than faked: a reorder buffer would invent a
    # tuning parameter and still need a late-arrival escape hatch, for a property the
    # reader recovers for free.
    #
    # CORRECTED 2026-10-30, AND OPEN. "Ordered within each path" holds for W and B, and
    # for E only on each line. Across lines E is in the kernel buffer's order: two
    # lines' IRQ threads are not ordered (wl_sync.rp1.gpio.SeqnoTracker), so an edge can
    # be written up to EDGE_STAMP_LAG_US behind one written before it -- 20 ms, the
    # allowance the roll barrier assumes, which bench day has not measured. GPIO26 and
    # GPIO27 fire together on every frame, so inversions of microseconds are routine.
    # Whether to keep the promise for E, with a reorder buffer before delivery, or to
    # declare E ordered per line, is the user's question, open in
    # docs/pending-wl-preproc-amendments.md, note 6.
    #
    # A CLOSED SET, because this field tells a reader HOW to read the rest of the file
    # and no code in this package reads it -- wl-preproc does. A typo is therefore
    # invisible on this side of the boundary and changes how the day is reconstructed on
    # the other, so the schema is the only place it can be caught.
    ordering: Literal["per-path", "global"] = "per-path"
    clock_trusted: bool = True
    clock_reason: str = ""
    process_uuid: str | None = None
    """Which recorder process wrote this segment. `wl-sync record` makes one UUID4 when
    the process starts and writes it, in canonical form, into every segment that process
    writes, before and after every roll. Every new process makes a new one. None means
    the segment was written before this field existed (2026-10-30). Every segment
    `wl-sync record` has written since carries one; a log built with `write_log`, such
    as a synthetic one, need not.

    THE CONTRACT A READER MAY RELY ON:

    - Segments with the same id were written by one process, so their ticks are on one
      clock: the box clock in `wl_sync.service` (`_BoxClock`), on which every path's
      ticks are placed. They compare by number, wherever the segments are.
    - "One clock" means one origin and one wrap count, not one rate. `B` and `E` ticks
      count CLOCK_MONOTONIC. `W` ticks count RP1's own crystal, joined to it once, when
      capture starts, so they drift from `B` and `E` by that crystal's rate error times
      the time since the process started: about 0.18 s an hour at 50 ppm. The real rate
      is unmeasured, and the fix is open (wl-sync
      docs/superpowers/specs/2026-10-30-one-clock-design.md).
    - Two segments in one folder, with consecutive `segment_index` and the same id, are
      not a restart. The index goes up by one between them, and the ticks run on, on
      that one clock, which a roll does not reset.
    - A different id, or None, means a new process began the later segment. That is a
      restart, and a restart begins a new clock, whose ticks are not comparable by
      number with the earlier segment's (spec Sec.3).

    THE ID PROMISES A SHARED CLOCK, NOT ADJACENCY. Something can lie between two such
    segments, recorded in another folder on the same clock:
    - In `unassigned/`, a visit normally does, recorded in its session's own folder.
    - In a session's folder, normally nothing does, because the box does not go back
      into a session folder it has left. It can, though. If reading the state file fails
      once during a request (`read_open` then says "none open"), the recording rolls out
      to `unassigned/`, and the next request rolls it back in.

    Nothing lies between the two segments of a roll in place, which the box makes once,
    when NTP first syncs (`wl_sync.cli.run`), in whichever folder it is recording.

    A reader that needs adjacency checks the `B` values across the boundary. Where
    nothing lies between, the second segment's first value normally follows the
    first's last by one, as consecutive frames do within a segment. Across a roll in
    place it can follow by two: NTP's first sync can step the clock by a fraction of a
    second, the frame grid is then placed mid-second again (`wl_sync.cli.run`), and that
    frame may skip one value. The gap is elapsed time, not a loss. Treat +1 or +2 across
    a roll in place as adjacent.

    Records cross a roll as they cross any point in a file, ordered within each path
    (`ordering`). A roll waits up to 0.2 s for each capture path to deliver what it
    captured before the boundary; a record delivered later lands in the next segment
    (session-identity spec Sec.3.4, amended 2026-10-30).

    A UUID, NOT A PID: pids repeat across boots, and `boot_id` cannot tell two processes
    in one boot apart. The canonical spelling is enforced (`_process_uuid_canonical`),
    so a reader can compare ids as text: one UUID spelled two ways would read as two
    processes, and so as a restart.

    NOT `run_id`, although this package calls one recorder process a run (`cli.run()`).
    "Run" means something else to the people who read this header. wl-preproc's runs are
    SpikeGLX runs, and deploy/README.md uses "the run" for a MonkeyLogic task. A "run id"
    beside those reads as one of them."""

    barcode_delay_us: int | None = Field(default=None, ge=0)
    """How long after a `B` record's tick the barcode frame's lead rising edge reaches
    the wire, in µs, as the bench loopback measures it: the median "lead edge after its
    logged tick" that `python -m wl_sync.bench loopback` prints. None means unmeasured,
    and stays the default until bench day measures it. The box is given the value with
    `wl-sync record --barcode-delay-us N` (deploy/README.md), and writes it into every
    segment of the run.

    ADD IT TO A `B` TICK to get the wire time: `tick_us + barcode_delay_us`. It is never
    negative, because the tick is read before the frame goes out (`wl_sync.cli.run`,
    spec Sec.5): after the checkpoint is written, immediately before emission. The
    lead edge therefore rises after the tick, and this is how long after. The loopback
    sees that edge as an `E` record on an input wired to the barcode output, so the sum
    is where this box's own edge capture places the lead edge.

    EXCEPT THE FIRST `B` OF EACH `process_uuid`. That record carries an extra delay
    that nothing measures. On the box, `Rp1Backend.emit_pulses` requests the output line
    during the first frame, after that frame's tick is read, so its lead edge rises later
    by however long the request takes. The loopback reports a segment's first frame as
    not checkable, so its median never includes this one.

    None is not zero. Zero is a measurement. A reader with None uses each tick as it is,
    and records the delay as unmeasured."""

    @field_validator("session_id")
    @classmethod
    def _session_id_well_formed(cls, value: str) -> str:
        SessionId.parse(value)
        return value

    @field_validator("process_uuid")
    @classmethod
    def _process_uuid_canonical(cls, value: str | None) -> str | None:
        # uuid.UUID also accepts upper case, no hyphens, braces and a urn: prefix. The
        # round trip through str() rejects all of them.
        if value is not None and str(uuid.UUID(value)) != value:
            raise ValueError(f"process_uuid must be a UUID in canonical form, got {value!r}")
        return value

    @field_validator("written_at")
    @classmethod
    def _must_be_aware(cls, value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("written_at must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class Edge:
    tick_us: int
    gpio: int
    level: int


@dataclass(frozen=True, slots=True)
class CodeWord:
    tick_us: int
    word: int


@dataclass(frozen=True, slots=True)
class BarcodeEmitted:
    """A barcode frame this box emitted, with the tick of its LEAD rising edge.

    The box hands every other device this value as the alignment key; until 2026-08-16
    it kept no copy of its own, so its log could not be aligned against the devices it
    was synchronising. One record per second, ~86 KB/day against a ~1 GB day.

    `tick_us` is UNWRAPPED, exactly like `Edge` and `CodeWord` -- these records go
    through `Recorder`, whose one shared clock places every path's ticks against
    `now_us()`, so all three share one timebase across RP1's 71.6-minute counter wrap.
    It is sampled BEFORE the frame is emitted, because a frame takes 200 ms (`barcode.FRAME_US`) and every receiving device
    reports `Barcode.start_us`, the lead rising edge. Reading the counter after
    emission would put this box's own alignment record 200 ms behind every device it
    is aligning.
    """

    tick_us: int
    value: int


@dataclass(frozen=True)
class _Shape:
    """What one cause's L record holds, beyond the rules every L record keeps."""

    path: str
    gpio: bool  # names its line; otherwise the field is empty
    bound: str  # the one bound it carries
    before: str  # "required" or "optional"
    after: str  # "required", "empty" or "optional"
    tick: str  # where tick_us sits against the window: a rule below, or "" for none


_AT_AFTER = "equal to after_us"
_FROM_AFTER = "at or after after_us"
_AT_BEFORE = "equal to before_us when before_us is given"

# Each cause's shape (`Loss`, THE SHAPE OF EACH CAUSE), and with it the closed set of
# causes per path. Closed, for the reason `ordering` is: no code in this package reads an
# L record -- wl-preproc does -- so a typo would be invisible here and change a session's
# timing check there. Provisional, as the whole format is until the user approves it,
# and strict: a reader is easier to loosen later than to tighten, and the box writes
# exactly this.
LOSS_SHAPES: dict[str, _Shape] = {
    "buffer_overflow": _Shape("E", True, "exact", "required", "required", _AT_AFTER),
    "coalesced": _Shape("E", True, "at_least", "required", "required", _AT_AFTER),
    "unaccounted": _Shape("E", False, "at_least", "optional", "required", _FROM_AFTER),
    "desync": _Shape("W", False, "at_least", "optional", "empty", _AT_BEFORE),
    "fifo_full": _Shape("W", False, "at_least", "optional", "optional", ""),  # reserved
}
LOSS_CAUSES: dict[str, tuple[str, ...]] = {
    path: tuple(cause for cause, shape in LOSS_SHAPES.items() if shape.path == path)
    for path in ("W", "E")
}


@dataclass(frozen=True, slots=True)
class Loss:
    """Capture events a path saw and could not record: the L record (2026-10-30).

        L,tick_us,path,gpio,count,bound,cause,before_us,after_us
        L,40400,E,20,2,exact,buffer_overflow,40100,40400
        L,8500,W,,3,at_least,desync,8500,

    WHY. The box has always found its own losses and said so in the journal, and the
    segment said nothing. A reader decoding W records into a session's events met a
    lost code word as silence: a decode error at best, a plausible and wrong event
    stream at worst. An L record marks each loss in the segment, beside the journal
    line, which is unchanged. A reader pinned before it raises "unknown log record
    type" on the first one.

    THE FIELDS. An empty field is None, never 0, which is a real tick and a real GPIO.
    Every number is ASCII digits only: no sign, no separator.
      * `tick_us`: where the record sits in its path's stream, on the box clock, as
        every tick here is (wl_sync.service.Recorder). An ordering key, not the time of
        the loss: the window gives that. See ORDER.
      * `path`: "W" or "E", the capture path that lost the events. B is never lost:
        this box emits it.
      * `gpio`: the line, for an E loss whose line is known. See the shapes below.
      * `count`: how many were lost, at least 1: code words (strobes) on W, edges on E.
        A buffer_overflow's exact count is of edge events, the line's own numbers; an
        event the kernel threw away may itself have carried a coalesced edge.
      * `bound`: "exact" when `count` is the number lost, "at_least" when it can be
        more.
      * `cause`: why, from a closed set per path, `LOSS_CAUSES`.
      * `before_us`: the tick of the last record of the path known to precede the loss;
        empty when none is known.
      * `after_us`: the tick of the first record of the path known to follow it; empty
        when none does.

    THE SHAPE OF EACH CAUSE (`LOSS_SHAPES`). Provisional, with the rest of the format.

        cause            path  gpio      bound     before_us  after_us  tick_us
        buffer_overflow  E     the line  exact     given      given     = after_us
        coalesced        E     the line  at_least  given      given     = after_us
        unaccounted      E     empty     at_least  optional   given     >= after_us
        desync           W     empty     at_least  optional   empty     = before_us if any
        fifo_full        W     empty     at_least  optional   optional  reserved

    And for every cause: count >= 1; every tick and gpio >= 0; before_us <= after_us when
    both are given. The reader refuses anything else, and the box never writes it: a
    report that would make one is dropped with an ERROR and reported by the health
    check, and capture goes on (wl_sync.service.Recorder._on_loss).

    THE CAUSES, one per place the box finds a loss (wl_sync.rp1):
      * E, buffer_overflow: a gap in one line's own event numbers. The kernel's buffer
        was full and threw that line's oldest events away. Found at the line's next
        event, X. `count` is the numbers missing, exact: the line numbered every event
        it lost. The window runs from the line's event before the gap to X.
      * E, coalesced: two events in a row on one line at the same level. An edge
        between them never became an event of its own. Found at the second, X. `count`
        is 1, at_least: the least a pair can mean. The window is the pair.
      * E, unaccounted: request-wide numbers that never arrived, which nothing on any
        line explains. Final only when its window closes, 20 to 40 ms after the read
        that showed it on a healthy pump (SeqnoTracker, THE WINDOW). `count` is the
        numbers missing that nothing explains, at_least: each was a hard IRQ, so at
        least one edge, on a line nobody knows. The window runs from the event numbered
        just below the gap to the one just above it, which showed it.
      * W, desync: the FIFO's (data, counter) pairing slipped, so a word was dropped.
        The batch that showed it is refused and the word pump stops. `count` is the
        strobes the refused batch held, half its entries rounded up, at_least: a strobe
        pushes two, and the stream is short of at least one. The window opens at the
        last code word recorded and has no end: no code word follows in this process,
        so every later strobe is lost too, uncounted. Before any code word, the window
        is empty at both ends.
      * W, fifo_full: RESERVED. This version does not write it: a FIFO found full MAY
        have dropped words, and if no strobe came while it was full nothing was lost,
        so its count cannot be bounded below by 1. It is in the closed set so that a
        reader of this format already accepts it once a box can write it, from the
        state machine's overflow flag (FDEBUG RXSTALL, set when `push noblock` drops);
        its tick and window will be defined then.

    THE WINDOW. The loss lies in [before_us, after_us], and an empty end leaves that
    side open. Two exceptions, each by microseconds, both from the IRQ thread's race
    (rp1.gpio.SeqnoTracker, THE IRQ THREAD RACES):
      * coalesced, at a line's first event or its first after an overflow: the lost edge
        can lie before before_us, by the time the race took.
      * unaccounted: the window rests on numbers following stamps, as one chained
        handler stamps and numbers the bank's edges in turn. An edge stamped from the
        IRQ thread can break that. Where it puts the event below the gap after the one
        above, the window is [after_us, after_us], and the loss can lie just outside it.

    ORDER. W records are in tick order. E records are in tick order on each line; across
    lines they are in the kernel buffer's order, so an edge can be written up to
    EDGE_STAMP_LAG_US behind one written before it -- 20 ms, an allowance bench day has
    not measured (SyncBoxLogHeader.ordering: corrected 2026-10-30, and open). An L
    record keeps the order its path has:
      * with a GPIO (buffer_overflow, coalesced): written just before X at X's tick. It
        keeps its line's order, and across lines it sits exactly where X does.
      * with no GPIO (unaccounted): at the highest E tick written before it. Nothing
        written before it is ahead of it, and it puts no E record written after it any
        further behind than that record already is: it adds no lateness to any E
        record. At its window's ticks it would sit behind the edges found after the gap,
        which are written by the time the window closes.
      * desync: at the last code word's tick, or, before any code word, at the box clock
        when the record is made. No code word follows it.

    WHICH SEGMENT. An L record goes into the segment open when the box finds the loss,
    as any record of its path does (session-identity spec Sec.3.4):
      * one written just before X lands with X. They are two writes to the queue, so a
        roll queued between them -- after the barrier has passed both -- leaves the
        record last in the closing segment and X first in the next, with its after_us in
        that next file.
      * an unaccounted one whose window ends at or before a roll's boundary lands in
        the closing segment: the edge pump holds its mark below the event that showed
        the gap until the record is written (rp1.gpio.EdgePump.pump_once). The roll
        waits for it as for any record, up to ROLL_BARRIER_TIMEOUT_S (0.2 s); a
        pending loss adds about 40 ms to that wait (34-48 ms in simulation; not a bound).
        Past the timeout, the record lands in the next segment.
      * a desync's lands in the closing segment of a roll that was waiting on the word
        path.
    A loss is found only when something shows it, so its record can be in a later
    segment than the loss: an overflow on a line that stays quiet across a roll, whose
    `before_us` is then in an earlier segment, on the same clock (same `process_uuid`).
    A desync's `before_us` and `tick_us` are the last code word's, which can be hours
    old and in another folder's segment.

    COUNTS DO NOT ADD ACROSS RECORDS. One lost edge can be named twice: an overflow on a
    line that does not fire again within the window is first an unaccounted record,
    then a buffer_overflow one when its line next fires. Each record is true alone.

    NO RECORD, because the count cannot be bounded below by 1: a FIFO found full; a gap
    still inside its window when capture stops and the edge pump's last look ends on
    its bound; the second of two pairs in a row on a line, because in the IRQ thread's
    race one damaged edge makes both and the second's window need not hold it. The
    journal reports each, as before. Nor has a capture thread that stops: all after it
    is lost, uncounted, and the journal says the thread stopped.
    """

    tick_us: int
    path: str
    gpio: int | None
    count: int
    bound: str
    cause: str
    before_us: int | None
    after_us: int | None

    def __post_init__(self) -> None:
        # The reader builds every L record through here, and so does the box, so
        # neither can hold one the format does not define.
        if self.path not in LOSS_CAUSES:
            raise ValueError(f"an L record's path is W or E, got {self.path!r}")
        if self.cause not in LOSS_CAUSES[self.path]:
            raise ValueError(
                f"an L record on {self.path} has a cause in {LOSS_CAUSES[self.path]}, "
                f"got {self.cause!r}"
            )
        if self.count < 1:
            raise ValueError(f"an L record's count is at least 1, got {self.count}")
        for name in ("tick_us", "gpio", "before_us", "after_us"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"an L record's {name} is at least 0, got {value}")
        shape = LOSS_SHAPES[self.cause]
        if (self.gpio is not None) != shape.gpio:
            raise ValueError(
                f"an L record of cause {self.cause} "
                f"{'names its line' if shape.gpio else 'has no gpio'}, got {self.gpio}"
            )
        if self.bound != shape.bound:
            raise ValueError(
                f"an L record of cause {self.cause} has bound {shape.bound}, "
                f"got {self.bound!r}"
            )
        for name, rule in (("before_us", shape.before), ("after_us", shape.after)):
            given = getattr(self, name) is not None
            if (rule == "required" and not given) or (rule == "empty" and given):
                raise ValueError(
                    f"an L record of cause {self.cause} has {name} {rule}, "
                    f"got {getattr(self, name)}"
                )
        if (
            self.before_us is not None
            and self.after_us is not None
            and self.before_us > self.after_us
        ):
            raise ValueError(
                f"an L record's window ends before it begins: {self.before_us} > "
                f"{self.after_us}"
            )
        if not _tick_keeps(shape.tick, self.tick_us, self.before_us, self.after_us):
            raise ValueError(
                f"an L record of cause {self.cause} has tick_us {shape.tick}, got "
                f"{self.tick_us} against window [{self.before_us}, {self.after_us}]"
            )


def _tick_keeps(rule: str, tick: int, before: int | None, after: int | None) -> bool:
    if rule == _AT_AFTER:
        return tick == after
    if rule == _FROM_AFTER:
        return after is not None and tick >= after
    if rule == _AT_BEFORE:
        return before is None or tick == before
    return True


Record = Edge | CodeWord | BarcodeEmitted | Loss


def unwrap_ticks(raw: Sequence[int]) -> list[int]:
    """Undo 32-bit tick wraparound, assuming records arrive in order."""
    unwrapped: list[int] = []
    offset = 0
    previous: int | None = None
    for tick in raw:
        if previous is not None and tick < previous:
            offset += TICK_WRAP_US
        unwrapped.append(tick + offset)
        previous = tick
    return unwrapped


def _blank(value: int | None) -> str:
    return "" if value is None else str(value)


_DIGITS = re.compile(r"[0-9]+")


def _loss_number(name: str, field: str, optional: bool = False) -> int | None:
    """One numeric field of an L record: ASCII digits only, so it reads back as written.
    `int()` would also take a sign, an underscore, spaces and other scripts' digits."""
    if optional and field == "":
        return None
    if _DIGITS.fullmatch(field) is None:
        allowed = "empty or digits" if optional else "digits"
        raise ValueError(f"an L record's {name} must be {allowed}, got {field!r}")
    return int(field)


def record_line(record: Record) -> str:
    """One record's line, without its newline. The one place each type's shape is
    written, so write_log and the streaming SegmentWriter cannot disagree."""
    if isinstance(record, Edge):
        return f"E,{record.tick_us},{record.gpio},{record.level}"
    if isinstance(record, BarcodeEmitted):
        return f"B,{record.tick_us},{record.value}"
    if isinstance(record, Loss):
        return (
            f"L,{record.tick_us},{record.path},{_blank(record.gpio)},{record.count},"
            f"{record.bound},{record.cause},{_blank(record.before_us)},"
            f"{_blank(record.after_us)}"
        )
    return f"W,{record.tick_us},{record.word}"


def _read_loss(fields: list[str]) -> Loss:
    if len(fields) != 8:
        raise ValueError(f"an L record has 8 fields after its type, got {len(fields)}")
    tick, path, gpio, count, bound, cause, before, after = fields
    return Loss(
        tick_us=_loss_number("tick_us", tick),
        path=path,
        gpio=_loss_number("gpio", gpio, optional=True),
        count=_loss_number("count", count),
        bound=bound,
        cause=cause,
        before_us=_loss_number("before_us", before, optional=True),
        after_us=_loss_number("after_us", after, optional=True),
    )


def write_log(path: Path, header: SyncBoxLogHeader, records: Iterable[Record]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header.model_dump(mode="json")) + "\n")
        for record in records:
            handle.write(record_line(record) + "\n")


def read_log(path: Path) -> tuple[SyncBoxLogHeader, list[Record]]:
    with path.open("r", encoding="utf-8") as handle:
        header = SyncBoxLogHeader.model_validate(json.loads(handle.readline()))
        records: list[Record] = []
        for line in handle:
            if not line.endswith("\n"):
                # Torn final line: a crash caught mid-write, which is the ONLY shape a
                # crash-closed segment ever has. Dropped rather than parsed, mirroring
                # manifest.summarise_segment, because a truncation landing on a comma
                # boundary parses perfectly -- "B,999999,10" is a well-formed record
                # that never existed, carrying a garbage alignment tick. Silently
                # fabricating one is worse than the ValueError the other truncations
                # raise, and it is the unsafe reader wl-preproc would have used.
                break
            stripped = line.strip()
            if not stripped:
                continue
            kind, *fields = stripped.split(",")
            if kind == "E":
                tick, gpio, level = (int(field) for field in fields)
                records.append(Edge(tick_us=tick, gpio=gpio, level=level))
            elif kind == "B":
                tick, value = (int(field) for field in fields)
                records.append(BarcodeEmitted(tick_us=tick, value=value))
            elif kind == "W":
                tick, word = (int(field) for field in fields)
                records.append(CodeWord(tick_us=tick, word=word))
            elif kind == "L":
                records.append(_read_loss(fields))
            elif kind == "T":
                # Segment trailer (wl_sync.segment): metadata about how the segment
                # closed, not a record. Skipped so the canonical reader accepts a
                # cleanly-closed segment; wl_sync.manifest reads the trailer itself.
                continue
            else:
                raise ValueError(f"unknown log record type: {kind!r}")
    return header, records
