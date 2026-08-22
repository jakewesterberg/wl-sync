"""Sync box on-disk log.

One JSON header line, then one CSV line per record, type-tagged:

    E,tick_us,gpio,level     a transition on a single line
    W,tick_us,word           a 16-bit event code word latched on a strobe edge
    B,tick_us,value          a barcode frame this box emitted

Three record types because three things ARRIVE DIFFERENTLY, and only two of the
three are captures at all:

  E and W differ in how RP1 delivers them. A strobed parallel word is one FIFO
  entry; reward, lick and loopback lines are individual transitions. Flattening
  those into one shape would throw away the distinction and force the reader to
  reconstruct it.

  B is not captured by anything. This box EMITS it, once per second, and every
  other device records it for clock alignment -- so the copy kept here is the
  emitter's own account of what it put on the wire, not a measurement of it.
  Reading the RP1 rationale onto B is the mistake to avoid: it explains why E and
  W are separate from each other, and says nothing about why B exists.

Plain text so it is readable with standard tools on a rig PC at 8am, which is
when it matters.

RP1's timebase counter wraps, so raw ticks must be unwrapped before use.
"""

from __future__ import annotations

import datetime
import json
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
    # three-way merge. Declared rather than faked: a reorder buffer would invent a
    # tuning parameter and still need a late-arrival escape hatch, for a property the
    # reader recovers for free.
    #
    # A CLOSED SET, because this field tells a reader HOW to read the rest of the file
    # and no code in this package reads it -- wl-preproc does. A typo is therefore
    # invisible on this side of the boundary and changes how the day is reconstructed on
    # the other, so the schema is the only place it can be caught.
    ordering: Literal["per-path", "global"] = "per-path"
    clock_trusted: bool = True
    clock_reason: str = ""

    @field_validator("session_id")
    @classmethod
    def _session_id_well_formed(cls, value: str) -> str:
        SessionId.parse(value)
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
    through `Recorder` on their own capture path, so all three share one timebase
    across RP1's 71.6-minute counter wrap. It is sampled BEFORE the frame is emitted,
    because a frame takes 200 ms (`barcode.FRAME_US`) and every receiving device
    reports `Barcode.start_us`, the lead rising edge. Reading the counter after
    emission would put this box's own alignment record 200 ms behind every device it
    is aligning.
    """

    tick_us: int
    value: int


Record = Edge | CodeWord | BarcodeEmitted


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


def write_log(path: Path, header: SyncBoxLogHeader, records: Iterable[Record]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header.model_dump(mode="json")) + "\n")
        for record in records:
            if isinstance(record, Edge):
                handle.write(f"E,{record.tick_us},{record.gpio},{record.level}\n")
            elif isinstance(record, BarcodeEmitted):
                handle.write(f"B,{record.tick_us},{record.value}\n")
            else:
                handle.write(f"W,{record.tick_us},{record.word}\n")


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
            elif kind == "T":
                # Segment trailer (wl_sync.segment): metadata about how the segment
                # closed, not a record. Skipped so the canonical reader accepts a
                # cleanly-closed segment; wl_sync.manifest reads the trailer itself.
                continue
            else:
                raise ValueError(f"unknown log record type: {kind!r}")
    return header, records
