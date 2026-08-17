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
    # Spec Sec.7 requires the machine-readable reason in BOTH the segment header and the
    # manifest. It was in the header only, and deploy/README.md calls the manifest "the
    # thing to read" -- so the one field saying WHY the clock is distrusted was missing
    # from exactly where someone looks first.
    clock_reason: str
    closed: str


def summarise_segment(path: Path) -> SegmentSummary:
    """Read a segment's header and barcode span without holding its records.

    Parsed line by line rather than via read_log() because a crashed segment's last line
    may be torn, and because a day's segment can hold tens of millions of records.

    Raises on an unreadable segment. build_manifest degrades instead of propagating --
    see _summarise_or_degrade -- but a direct caller gets the honest error.
    """
    barcode_first: int | None = None
    barcode_last: int | None = None
    closed = "crash"
    clock_trusted = True
    clock_reason = ""
    with path.open("r", encoding="utf-8") as handle:
        header_line = handle.readline()
        try:
            header = json.loads(header_line)
            clock_trusted = bool(header.get("clock_trusted", True))
            clock_reason = str(header.get("clock_reason", ""))
        except (json.JSONDecodeError, AttributeError):
            clock_trusted = False
            clock_reason = "unreadable_header"
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
        clock_reason=clock_reason,
        closed=closed,
    )


def _summarise_or_degrade(path: Path) -> SegmentSummary:
    """One unreadable segment must not cost the box the rest of the day.

    Bad permissions, or media corruption surfacing as a UnicodeDecodeError out of
    `for line in handle`, used to propagate out of summarise_segment, out of
    build_manifest, out of run() -- and since run() rebuilds the manifest before it
    records anything, the box then could not record for the rest of that day, on every
    restart. A file we cannot read is an outage like any other: it reports no barcodes,
    so the day's gap arithmetic still carries the last witnessed value across it.

    `closed: "unreadable"` is deliberately a third value rather than "crash", because
    "we could not read this" and "this process died" are different facts and only one
    of them says anything about the run.
    """
    try:
        return summarise_segment(path)
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        return SegmentSummary(
            file=path.name,
            barcode_first=None,
            barcode_last=None,
            clock_trusted=False,
            clock_reason="unreadable_segment",
            closed="unreadable",
        )


def build_manifest(day_dir: Path) -> dict:
    paths = sorted(
        (path for path in day_dir.glob(SEGMENT_PATTERN) if segment_index_of(path) is not None),
        key=segment_index_of,
    )
    summaries = [_summarise_or_degrade(path) for path in paths]

    # Carries the last WITNESSED barcode forward across any number of segments that
    # saw none at all — e.g. one opened and then crashed before its first barcode tick,
    # which repeats on every restart under systemd's Restart=always. Zipping ADJACENT
    # summaries instead would let such an all-None segment's `continue` swallow both the
    # gap before it and the gap after it, hiding the outage completely. Do not
    # "simplify" this back into a pairwise zip.
    gaps = []
    last_seen: int | None = None
    for summary in summaries:
        if summary.barcode_first is not None and last_seen is not None:
            seconds = summary.barcode_first - last_seen
            if seconds > 1:
                gaps.append(
                    {
                        "after": last_seen,
                        "before": summary.barcode_first,
                        "seconds": seconds,
                    }
                )
        if summary.barcode_last is not None:
            last_seen = summary.barcode_last

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
