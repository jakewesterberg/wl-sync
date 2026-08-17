"""The day's manifest: which segments exist, and where the box was NOT watching.

`gaps` is the point. The other recording devices keep running while this box is down,
so trials happen during an outage; they really occurred and must be recognisable as
UNWITNESSED rather than silently absent. Enumerating gaps lets wl-preproc say so
positively instead of discovering a hole.

The manifest is DERIVED STATE, rebuilt by scanning segments at every start. It is never
authoritative and never merged into, so a crash cannot leave it stale.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from wl_sync.segment import SEGMENT_PATTERN, segment_index_of

_TRAILER_RE = re.compile(r"^T,")
_BARCODE_RE = re.compile(r"^B,(\d+),(\d+)$")


@dataclass(frozen=True, slots=True)
class SegmentSummary:
    file: str
    barcode_first: int | None
    barcode_last: int | None
    clock_trusted: bool
    closed: str


def summarise_segment(path: Path) -> SegmentSummary:
    """Read a segment's header and barcode span without holding its records.

    Parsed line by line rather than via read_log() because a crashed segment's last line
    may be torn, and because a day's segment can hold tens of millions of records.
    """
    barcode_first: int | None = None
    barcode_last: int | None = None
    closed = "crash"
    clock_trusted = True
    with path.open("r", encoding="utf-8") as handle:
        header_line = handle.readline()
        try:
            clock_trusted = bool(json.loads(header_line).get("clock_trusted", True))
        except (json.JSONDecodeError, AttributeError):
            clock_trusted = False
        for line in handle:
            if not line.endswith("\n"):
                break  # torn final line: a crash caught mid-write
            if _TRAILER_RE.match(line):
                closed = "clean"
                continue
            match = _BARCODE_RE.match(line.rstrip("\n"))
            if match:
                value = int(match.group(2))
                if barcode_first is None:
                    barcode_first = value
                barcode_last = value
    return SegmentSummary(
        file=path.name,
        barcode_first=barcode_first,
        barcode_last=barcode_last,
        clock_trusted=clock_trusted,
        closed=closed,
    )


def build_manifest(day_dir: Path) -> dict:
    paths = sorted(
        (path for path in day_dir.glob(SEGMENT_PATTERN) if segment_index_of(path) is not None),
        key=segment_index_of,
    )
    summaries = [summarise_segment(path) for path in paths]

    gaps = []
    for previous, following in zip(summaries, summaries[1:]):
        if previous.barcode_last is None or following.barcode_first is None:
            continue
        seconds = following.barcode_first - previous.barcode_last
        if seconds > 1:
            gaps.append(
                {
                    "after": previous.barcode_last,
                    "before": following.barcode_first,
                    "seconds": seconds,
                }
            )

    return {
        "day": day_dir.name,
        "segments": [asdict(summary) for summary in summaries],
        "gaps": gaps,
    }


def write_manifest(day_dir: Path) -> dict:
    manifest = build_manifest(day_dir)
    path = day_dir / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return manifest
