"""Sync box on-disk log.

One JSON header line, then one CSV line per record, type-tagged:

    E,tick_us,gpio,level     a transition on a single line
    W,tick_us,word           a 16-bit event code word latched on a strobe edge
    B,tick_us,value          a barcode frame this box emitted

Three record types because the RP1 PIO captures them differently: a strobed
parallel word arrives as one FIFO entry, while reward, lick and loopback lines
arrive as individual transitions. Flattening them into one shape would throw
away that distinction and force the reader to reconstruct it. Barcode records
are emitted once per second by this box and recorded by every other device for
clock alignment.

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

from pydantic import BaseModel, ConfigDict, field_validator

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
