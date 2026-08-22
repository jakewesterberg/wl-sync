"""One log segment: one process run's worth of records, appended as they arrive.

WHY SEGMENTS AND NOT ONE FILE PER DAY. RP1's tick counter wraps every ~71.6 minutes and
the unwrap offset is in memory, so ticks after a restart are not comparable BY NUMBER to
ticks before it. A single file under a single header would be lying about its own
timebase. One header per segment states one clock epoch, which is true.

WHY THE TRAILER IS THE CRASH TEST. Its presence means the segment was closed cleanly;
its absence means it was not. Nothing else is consulted, so the distinction never depends
on a dying process managing to record its own death. A torn final line is likewise
trailer-less, and therefore crash-closed, which is correct.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

from wl_sync.log import BarcodeEmitted, Edge, Record, SyncBoxLogHeader

SEGMENT_PATTERN = "seg-*.log"
_SEGMENT_RE = re.compile(r"^seg-(\d+)\.log$")


def segment_name(index: int) -> str:
    """Format a segment file name from its index, zero-padded to sort lexically."""
    return f"seg-{index:03d}.log"


def segment_index_of(path: Path) -> int | None:
    """The segment index encoded in a file's name, or None if it is not a segment file.

    Public because the manifest needs it too, both to filter a directory listing and to
    order it. Keeping the regex private and sharing a named function instead means the
    naming scheme has exactly one reader.
    """
    match = _SEGMENT_RE.match(path.name)
    return int(match.group(1)) if match else None


def next_segment_index(day_dir: Path) -> int:
    """One past the highest segment present. Derived from the filesystem, so a restart
    needs no memory of how many runs preceded it."""
    indices = [
        index
        for path in day_dir.glob(SEGMENT_PATTERN)
        if (index := segment_index_of(path)) is not None
    ]
    return max(indices) + 1 if indices else 0


class SegmentWriter:
    """Append-only sink. Satisfies the `write(record)` protocol `Recorder` expects.

    NOT THREAD-SAFE, AND DELIBERATELY UNGUARDED. `write()` is one file write plus an
    unsynchronised read-modify-write of `record_count` and the barcode span; concurrent
    callers would interleave partial lines and lose counts. `FakeBackend` dispatches
    synchronously so no test can see this, but the RP1 backend will deliver from capture
    threads -- so the constraint is stated here rather than discovered there. The caller
    owns the serialisation: either a single thread drives every `Recorder` path and the
    emit loop, or the sink is wrapped in a lock before it is handed over.

    OPENED WITH "x", NOT "w". This is the only destructive operation in a module whose
    entire purpose is bounding data loss. A human running `wl-sync record` while the
    systemd unit is up races it for the same segment index, and under "w" the loser's
    whole segment vanished silently with no error anywhere. Failing loudly on a
    collision is the only acceptable outcome.
    """

    def __init__(self, path: Path, header: SyncBoxLogHeader) -> None:
        self._handle = path.open("x", encoding="utf-8")
        # The file EXISTS from the line above, so a failure writing the header leaves a
        # headerless segment behind and an open handle with it. That is permanent
        # damage rather than a transient: next_segment_index counts the file, so the
        # index is burned, and every manifest rebuilt for the rest of the day reports it
        # as unreadable -- an outage that never happened, in the file whose whole job is
        # to say where the real outages were. Unwinding leaves the day as it was.
        try:
            self._handle.write(json.dumps(header.model_dump(mode="json")) + "\n")
            self.barcode_first: int | None = None
            self.barcode_last: int | None = None
            self.record_count = 0
            self.flush()
        except BaseException:
            self._handle.close()
            path.unlink(missing_ok=True)
            raise

    def write(self, record: Record) -> None:
        """Write a record to the segment."""
        if isinstance(record, Edge):
            line = f"E,{record.tick_us},{record.gpio},{record.level}"
        elif isinstance(record, BarcodeEmitted):
            line = f"B,{record.tick_us},{record.value}"
            if self.barcode_first is None:
                self.barcode_first = record.value
            self.barcode_last = record.value
        else:
            line = f"W,{record.tick_us},{record.word}"
        self._handle.write(line + "\n")
        self.record_count += 1

    def flush(self) -> None:
        """Push to the platter, not merely to the OS. At ~40 KB/s onto the NVMe spec
        Sec.4.1 chose, once per second is free and bounds crash loss to one second."""
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self, closed_at: datetime.datetime) -> None:
        """Close the segment cleanly, writing a trailer that proves it was not a crash.

        The trailer's format is: T,<isoformat>,<first_barcode>,<last_barcode>,<count>
        The barcode span fields are empty strings when the segment carried no barcodes.
        """
        first = "" if self.barcode_first is None else self.barcode_first
        last = "" if self.barcode_last is None else self.barcode_last
        self._handle.write(
            f"T,{closed_at.isoformat()},{first},{last},{self.record_count}\n"
        )
        self.flush()
        self._handle.close()
