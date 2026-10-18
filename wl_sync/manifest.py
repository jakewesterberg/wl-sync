"""The day's manifest: which segments exist, and where the box was NOT watching.

`gaps` is the point. The other recording devices keep running while this box is down,
so trials happen during an outage; they really occurred and must be recognisable as
UNWITNESSED rather than silently absent. Enumerating gaps lets wl-preproc say so
positively instead of discovering a hole.

The manifest is DERIVED STATE, rebuilt by scanning segments at every start. It is never
authoritative and never merged into, so a crash cannot leave it stale.

The scan is memoised per segment (see `_summarise_cached`), because the run loop also
rebuilds a folder's manifest at every roll, and a crashed segment's scan is slow. The
memo is checked against the file on every lookup, so it never stands in for a segment
that has changed; the manifest is still rebuilt whole every time.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path

from wl_sync.segment import SEGMENT_PATTERN, segment_index_of

_TRAILER_RE = re.compile(r"^T,")
_BARCODE_RE = re.compile(r"^B,(\d+),(\d+)$")

# T,<isoformat>,<first>,<last>,<count>. The span fields are EMPTY when the segment
# carried no barcodes, so `\d*` rather than `\d+`: empty must come back as None, never
# as 0, which is a legitimate barcode value (the epoch itself, which a dead-RTC start
# really does emit). `[^,]*` for the timestamp is safe because SegmentWriter writes
# datetime.isoformat(), which uses a period as its decimal separator; anything with an
# extra comma fails to match and falls back to the scan, which is the safe direction.
_TRAILER_SPAN_RE = re.compile(r"^T,[^,]*,(\d*),(\d*),\d+$")

# Enough to hold a trailer many times over. A last line longer than this fails to match
# and falls back to the full scan rather than being read wrongly.
_TAIL_BYTES = 4096


def _trailer_span(path: Path) -> tuple[int | None, int | None] | None:
    """A clean segment's barcode span, read from its trailer instead of its body.

    THIS IS WHY THE MANIFEST DOES NOT COST A DAY-LONG READ. `run()` rebuilds the
    manifest BEFORE it records anything, so every restart used to pay for a full scan of
    every segment already on disk -- and a day-long segment holds tens of millions of
    records. A restart late in the day spent its first seconds re-parsing, lengthening
    the very outage the manifest exists to measure.

    `SegmentWriter.close()` writes `first`, `last` and `count` from the same counters a
    scan would rebuild, so on a well-formed segment the two agree by construction. Where
    they disagree the file is corrupt, and the trailer is the half written last and
    fsynced.

    Returns None -- meaning "scan it properly" -- whenever the tail is not a complete,
    parseable trailer: a torn final line, a crashed segment with no trailer at all, an
    over-long last line, or undecodable bytes. Every one of those degrades to the old
    behaviour rather than to a wrong answer.
    """
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        window = min(size, _TAIL_BYTES)
        handle.seek(size - window)
        tail = handle.read(window)

    if not tail.endswith(b"\n"):
        return None  # torn final line: a crash caught mid-write
    lines = tail.split(b"\n")
    if len(lines) < 2:
        return None
    try:
        last_line = lines[-2].decode("utf-8")
    except UnicodeDecodeError:
        # Let the scan meet the same bytes and raise, so the segment is reported
        # `unreadable` rather than quietly summarised from a guess.
        return None

    match = _TRAILER_SPAN_RE.match(last_line)
    if match is None:
        return None
    first, last = match.group(1), match.group(2)
    return (int(first) if first else None, int(last) if last else None)


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

    A cleanly-closed segment is read from its TRAILER -- header line plus tail, no body
    -- because the trailer already carries the span a scan would rebuild. See
    `_trailer_span` for why that matters and when it declines. Everything else is parsed
    line by line, rather than via read_log(), because a crashed segment's last line may
    be torn and because a day's segment can hold tens of millions of records.

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

        # Independent of the header, so a segment with a junk header still gets the fast
        # path -- the two answer different questions and neither needs the other.
        span = _trailer_span(path)
        if span is not None:
            barcode_first, barcode_last = span
            return SegmentSummary(
                file=path.name,
                barcode_first=barcode_first,
                barcode_last=barcode_last,
                clock_trusted=clock_trusted,
                clock_reason=clock_reason,
                closed="clean",
            )

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


# Enough for months of one box's segments -- a day makes tens of them -- and small: an
# entry is a path, a stat key and one SegmentSummary, measured at about 1.4 KB, so a full
# memo is under 6 MB. See _summarise_cached.
_SUMMARY_CACHE_LIMIT = 4096

# path -> ((st_ino, st_size, st_mtime_ns), summary), least recently used first.
_summaries: OrderedDict[Path, tuple[tuple[int, int, int], SegmentSummary]] = OrderedDict()
_summaries_lock = threading.Lock()


def _summarise_cached(path: Path) -> SegmentSummary:
    """summarise_segment, reusing an earlier summary of the same, unchanged file.

    WHY THE MANIFEST REMEMBERS. The run loop rebuilds a folder's manifest when the
    recording rolls out of it, on its own thread, between two frames. A segment left
    without a trailer by a crash declines the fast path and is parsed line by line:
    210 MB took 1.87 s on a laptop, and a CM5 is slower. For that long no barcode frame
    went out, the new segment went unflushed past the one-second crash-loss bound, and a
    pure-Python loop held the GIL against the 1 ms word pump -- at every open and close
    after a crash, which spec Sec.2 forbids ("recording that stalls ... at an open or a
    close"). The start of the run has already parsed every segment in the folder it
    starts in, before capture begins, and nothing writes to a crashed segment again. So
    a roll out of that folder now costs a stat per segment, not a parse.

    WHY IT CANNOT GO STALE. An entry is used only while the file's inode, size and
    modification time all still match. Segments are append-only, so any write changes
    the size; the time and the inode catch a file rewritten or replaced in place. The
    stat is taken BEFORE the parse: a file that grows between the two is summarised
    with more than the stat saw, and the next lookup sees a new size and parses again.
    The other order would file a summary of the old contents under the new size.

    WHAT IS NOT REMEMBERED. A failure. Whatever made a read fail -- permissions, say --
    can be put right without touching the size or the time, so a remembered failure
    could outlive its cause.

    BOUNDED, least recently used out first, because the process can run for months. The
    limit is far above what a day's rolls can add (99 sessions at most, two rolls
    each), so it does not evict what a roll is about to reuse. A lock, because nothing
    confines callers to one thread; it is never held across a parse.
    """
    stat = path.stat()
    key = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _summaries_lock:
        cached = _summaries.get(path)
        if cached is not None and cached[0] == key:
            _summaries.move_to_end(path)
            return cached[1]
    summary = summarise_segment(path)
    with _summaries_lock:
        _summaries[path] = (key, summary)
        _summaries.move_to_end(path)
        while len(_summaries) > _SUMMARY_CACHE_LIMIT:
            _summaries.popitem(last=False)
    return summary


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
        return _summarise_cached(path)
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        return SegmentSummary(
            file=path.name,
            barcode_first=None,
            barcode_last=None,
            clock_trusted=False,
            clock_reason="unreadable_segment",
            closed="unreadable",
        )


def build_manifest(day_dir: Path, *, excluding: Path | None = None) -> dict:
    """The manifest of the segments in `day_dir`, leaving out `excluding` if given.

    `excluding` is for the segment still being written. Listed, it could only read as
    `closed: "crash"`, since it has no trailer until it closes; the start of a run
    rebuilds its folder's manifest before its own segment exists, so it lists the
    segments that were there when the recording arrived. A roll into a folder passes its
    new segment here to say the same thing.
    """
    paths = sorted(
        (
            path
            for path in day_dir.glob(SEGMENT_PATTERN)
            if segment_index_of(path) is not None and path != excluding
        ),
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
    overlaps = []
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
            elif seconds <= 0:
                # THE OPPOSITE OF A GAP, and it must not be recorded as one. A positive
                # difference means the box was down while the other devices kept
                # running, which `gaps` exists to let wl-preproc mark UNWITNESSED. A
                # difference of zero or less means values were RE-ISSUED: one identity
                # naming two moments, so a trial can be attributed to the wrong one.
                #
                # Both used to fail the `> 1` test and vanish, which made a day carrying
                # a correctness failure look cleaner than a day with an ordinary
                # restart. Kept in its own list rather than admitted to `gaps` as a
                # negative number, because it is not a duration on a timeline and no
                # consumer should be doing arithmetic with it.
                overlaps.append(
                    {
                        "after": last_seen,
                        "before": summary.barcode_first,
                        "seconds": seconds,
                    }
                )
        if summary.barcode_last is not None:
            last_seen = summary.barcode_last

    return {
        # Segments now live in <session>/syncbox/, so the session is the folder above it.
        "day": day_dir.parent.name if day_dir.name == "syncbox" else day_dir.name,
        "segments": [asdict(summary) for summary in summaries],
        "gaps": gaps,
        "overlaps": overlaps,
    }


def write_manifest(day_dir: Path, *, excluding: Path | None = None) -> dict:
    manifest = build_manifest(day_dir, excluding=excluding)
    path = day_dir / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return manifest
