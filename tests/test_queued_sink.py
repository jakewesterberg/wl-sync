"""Fan-in from capture threads onto one owner thread.

`Recorder`'s three paths plus the emit loop all call `sink.write()`, and
`SegmentWriter` is documented as not thread-safe: `write()` is a file write plus
an unguarded read-modify-write of `record_count` and the barcode span. Nothing
enforced that while `FakeBackend` dispatched synchronously. The RP1 backend
delivers from capture threads, so this is where that stated constraint becomes a
real one.
"""

import threading
import time

import pytest

from wl_sync.log import BarcodeEmitted, CodeWord, Edge
from wl_sync.service import QueuedSink

WRITERS = 8
PER_WRITER = 250


class _RacySink:
    """Records what a genuinely unguarded sink would suffer.

    `record_count += 1` is LOAD_ATTR / ADD / STORE_ATTR, so it loses increments
    under concurrency; the sleep widens the window that CPython's bytecode leaves
    open anyway, so the test fails on purpose rather than on timing luck.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.record_count = 0
        self.flushes = 0
        self.concurrent = False
        self._inside = 0

    def write(self, record) -> None:
        self._inside += 1
        if self._inside > 1:
            self.concurrent = True
        count = self.record_count
        time.sleep(0)  # yield inside the read-modify-write
        self.lines.append(f"{record.tick_us}")
        self.record_count = count + 1
        self._inside -= 1

    def flush(self) -> None:
        self._inside += 1
        if self._inside > 1:
            self.concurrent = True
        self.flushes += 1
        self._inside -= 1


def _hammer(sink, records_per_thread=PER_WRITER, threads=WRITERS):
    def run(base):
        for index in range(records_per_thread):
            sink.write(Edge(tick_us=base * 10_000 + index, gpio=20, level=1))

    workers = [threading.Thread(target=run, args=(n,)) for n in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


def test_the_owner_thread_is_the_only_one_that_touches_the_sink():
    """The whole guarantee. No lock lives in SegmentWriter; instead nothing but the
    drain thread ever calls into it, which is why the sink stays as simple as it is."""
    inner = _RacySink()
    sink = QueuedSink(inner)
    try:
        _hammer(sink)
        sink.flush()
    finally:
        sink.close()

    assert not inner.concurrent, "two threads were inside the sink at once"


def test_no_record_is_lost_under_concurrent_writers():
    inner = _RacySink()
    sink = QueuedSink(inner)
    try:
        _hammer(sink)
    finally:
        sink.close()

    expected = WRITERS * PER_WRITER
    assert inner.record_count == expected, f"lost {expected - inner.record_count}"
    assert len(inner.lines) == expected


def test_flush_does_not_return_until_the_sink_has_flushed():
    """THE ONE-SECOND BOUND, which is the reason for a barrier rather than a marker.

    spec Sec.4 promises a crash loses at most one second, and the emit loop delivers
    that by calling flush() every second. If flush() merely enqueued, it would return
    while records were still in the queue and the promise would quietly become 'one
    second plus however far behind the drain is'.
    """
    inner = _RacySink()
    sink = QueuedSink(inner)
    try:
        for index in range(500):
            sink.write(CodeWord(tick_us=index, word=index & 0xFFFF))
        sink.flush()
        # Read immediately: everything queued before the flush must already be down.
        assert inner.record_count == 500
        assert inner.flushes == 1
    finally:
        sink.close()


def test_a_capture_thread_never_waits_for_the_sink():
    """Why a queue and not a lock. A lock covering write() and flush() lets a capture
    thread block on os.fsync(); the PIO RX FIFO is 8 deep and the program uses
    `push noblock`, so a stalled capture thread becomes DROPPED WORDS -- which fails
    Task 5b Step 3 outright. Writing must stay independent of how slow the sink is."""

    class _SlowSink(_RacySink):
        def flush(self) -> None:
            time.sleep(0.25)  # stands in for a slow fsync
            super().flush()

    inner = _SlowSink()
    sink = QueuedSink(inner)
    try:
        blocker = threading.Thread(target=sink.flush)
        blocker.start()
        time.sleep(0.02)  # let the drain thread get into the slow flush

        started = time.perf_counter()
        for index in range(100):
            sink.write(Edge(tick_us=index, gpio=21, level=0))
        elapsed = time.perf_counter() - started

        blocker.join()
        assert elapsed < 0.1, (
            f"writing took {elapsed:.3f}s while the sink was busy; a capture thread "
            "would have been blocked on disk and the FIFO would overflow"
        )
    finally:
        sink.close()


def test_close_drains_what_is_still_queued():
    """A close that dropped the tail would lose the end of every session, which is
    where the day's last trials live."""
    inner = _RacySink()
    sink = QueuedSink(inner)
    for index in range(300):
        sink.write(BarcodeEmitted(tick_us=index * 1_000_000, value=index))
    sink.close()

    assert inner.record_count == 300


def test_writing_after_close_is_refused():
    """Silently accepting a record the owner thread will never take is worse than
    raising: it looks recorded and is not."""
    sink = QueuedSink(_RacySink())
    sink.close()
    with pytest.raises(RuntimeError):
        sink.write(Edge(tick_us=1, gpio=20, level=1))


def test_records_keep_their_per_path_order():
    """Arrival order within one path is what the segment's `ordering: per-path` header
    promises a reader, so the queue must not reorder a single producer's records."""
    inner = _RacySink()
    sink = QueuedSink(inner)
    try:
        for index in range(400):
            sink.write(Edge(tick_us=index, gpio=20, level=1))
        sink.flush()
        assert inner.lines == [str(index) for index in range(400)]
    finally:
        sink.close()


def test_a_sink_that_raises_does_not_die_quietly():
    """The failure mode a background thread invites, and the one that matters most.

    If the drain thread exits on an exception, every later record is accepted and
    discarded: the segment simply stops mid-day, the caller sees no error, and the
    manifest later reports a crash that never happened. The exception is held and
    re-raised on the next call instead.
    """

    class _Breaks(_RacySink):
        def write(self, record) -> None:
            raise OSError("no space left on device")

    sink = QueuedSink(_Breaks())
    sink.write(Edge(tick_us=1, gpio=20, level=1))

    for _ in range(200):  # let the owner thread pick it up
        if sink._error is not None:
            break
        time.sleep(0.005)

    with pytest.raises(RuntimeError, match="owner thread died") as raised:
        sink.write(Edge(tick_us=2, gpio=20, level=1))
    assert isinstance(raised.value.__cause__, OSError)


def test_a_failed_close_surfaces_rather_than_being_swallowed():
    """close() writes the trailer, and the trailer is what distinguishes a clean
    segment from a crashed one. Losing that error would mark a good day as a crash."""

    class _BreaksOnClose(_RacySink):
        def close(self, *args) -> None:
            raise OSError("disk went away")

    sink = QueuedSink(_BreaksOnClose())
    sink.write(Edge(tick_us=1, gpio=20, level=1))
    with pytest.raises(RuntimeError, match="owner thread died"):
        sink.close()


def _close_in_a_thread(sink, timeout=3.0):
    """close() must not be able to hang. Run it where a hang is a failure rather
    than a stuck test run."""
    outcome = {}

    def run():
        try:
            sink.close()
            outcome["raised"] = None
        except BaseException as error:  # noqa: BLE001 - recorded, then asserted on
            outcome["raised"] = error

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    return worker.is_alive(), outcome


def test_close_does_not_hang_when_the_owner_thread_already_died():
    """The deadlock the error path introduced.

    A write that raises makes the drain thread return, so nothing is left to answer a
    close request -- and close() waited on a barrier that could never be set. On the
    rig that is the daemon hanging at session end, holding the segment open with no
    trailer, so the day it just recorded reports as a crash.
    """

    class _Breaks(_RacySink):
        def write(self, record) -> None:
            raise OSError("no space left on device")

    sink = QueuedSink(_Breaks())
    sink.write(Edge(tick_us=1, gpio=20, level=1))
    for _ in range(200):
        if not sink._thread.is_alive():
            break
        time.sleep(0.005)

    hung, outcome = _close_in_a_thread(sink)

    assert not hung, "close() hung after the owner thread died"
    assert isinstance(outcome["raised"], RuntimeError)


def test_close_is_idempotent_and_still_does_not_hang():
    """systemd sends SIGTERM and the process may also close on its own way out, so a
    second close must be a no-op rather than a second wait on a stopped thread."""
    sink = QueuedSink(_RacySink())
    sink.close()
    hung, outcome = _close_in_a_thread(sink)
    assert not hung and outcome["raised"] is None


def test_a_write_accepted_before_close_is_never_silently_dropped():
    """`_closed` was set AFTER the close request was queued, so a writer could pass the
    check, enqueue behind the close, and have its record discarded by a drain thread
    that had already returned -- a write that succeeded and vanished.

    Run repeatedly because the window is small; the assertion is the invariant, not the
    timing: every write that RETURNS must be delivered, and every write that cannot be
    must raise.
    """
    for _ in range(40):
        inner = _RacySink()
        sink = QueuedSink(inner)
        accepted = []
        start = threading.Event()

        def writer():
            start.wait()
            for index in range(50):
                try:
                    sink.write(Edge(tick_us=index, gpio=20, level=1))
                    accepted.append(index)
                except RuntimeError:
                    return  # refused, which is the honest outcome

        worker = threading.Thread(target=writer)
        worker.start()
        start.set()
        sink.close()
        worker.join()

        assert inner.record_count == len(accepted), (
            f"{len(accepted) - inner.record_count} record(s) were accepted by write() "
            "and then discarded"
        )
