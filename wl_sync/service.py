"""Barcode generation, recording, and camera frame times.

Barcode output is software-timed and that is deliberate: 5 ms bit slots sampled
at 30 kHz give 150 samples per bit, and each receiver times its own edges, so
generator jitter of a few hundred microseconds is harmless. Precision is spent
where it is needed — strobed capture, which on training days is the sole record
of event times.

Camera frame times arrive as ordinary edges on GPIO26/27 (`FRAME_TIME_GPIO`) and
need no new capture machinery. What they do need is `frame_times()`, which encodes
the one non-obvious thing about them: only the FALLING edge is trustworthy.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from wl_sync.backend import SyncBackend
from wl_sync.barcode import encode
from wl_sync.log import TICK_WRAP_US, BarcodeEmitted, CodeWord, Edge, Record
from wl_sync.pins import FRAME_TIME_GPIO

_FALLING = 0


class RecordSink(Protocol):
    """What `Recorder` needs of a destination: somewhere to put one record.

    Named so the `sink` parameter can say what it accepts. `SegmentWriter.write` and
    `_MemorySink.write` both satisfy it without inheriting from it, which is the point --
    the streaming sink lives in another module and should not have to import this one.
    """

    def write(self, record: Record) -> None: ...


class BarcodeGenerator:
    """Emits one barcode frame per call, for the value it is handed.

    IT CHOOSES NOTHING, and that is the point. Until 2026-08-22 it took a
    `value_source` and clamped the result to strictly exceed the previous one, which
    made it a second decision point sitting DOWNSTREAM of the checkpoint. The caller
    now marks a value as spent before handing it here (wl_sync.cli.run), so a clamp at
    this end would emit something other than what was marked -- putting the checkpoint
    back out of step with the wire, which is the defect that ordering exists to close,
    reappearing one line further down.

    A repeated barcode is two different moments carrying one identity, the one thing it
    must never be. That guarantee now lives entirely where the value is CHOSEN; see
    wl_sync.cli._ValueSource.
    """

    def __init__(self, backend: SyncBackend, pin: int) -> None:
        self._backend = backend
        self._pin = pin

    def emit_frame(self, value: int) -> None:
        self._backend.emit_pulses(self._pin, encode(value))


class _BoxClock:
    """The box's one clock: unwraps 32-bit ticks from every delivery path against
    `now_us()`, rather than against each path's own previous tick.

    All three paths -- strobed words, edges and this box's barcodes -- and `now_us()`
    share one origin and one 32-bit wrap (rp1/backend.py takes the PIO counter's start
    and `_origin_ns` together; `edge_tick_us` uses the same origin). So there is ONE
    wrap count, and `now()` is the only thing that has to observe it.

    `now()` is `now_us()` unwrapped: it adds 2^32 whenever the raw reading is below the
    previous one. `unwrap(raw)` returns the value congruent to `raw` mod 2^32 NEAREST to
    `now()`. Nearest, not "at or before": W ticks come from the RP1 crystal while
    `now_us()` follows NTP-slewed CLOCK_MONOTONIC, so a tick can lie slightly AFTER the
    reading, and "at or before" would send it a whole wrap back. The two diverge by ppm
    times run length; nearest tolerates up to +-2^31 us (35.8 min), roughly 500 days of
    continuous running at 50 ppm.

    WHY SHARING IS SAFE NOW. The per-path unwrappers this replaced each noticed a wrap
    only when their own tick fell below their own previous one. That misses a wrap if the
    path is silent for 71.6 minutes or its first record arrives that late -- and every
    later record is then 4294.967296 s early against the barcodes wl-preproc aligns by.
    A naive SHARED "falls below the previous tick" counter is wrong for the opposite
    reason: the paths deliver independently, so an edge stamped just before a wrap can
    arrive after a word stamped just after it, and that ordinary interleaving would read
    as a wrap. Here no per-record state is compared at all; each record is placed by its
    distance from `now()`, so interleaving cannot confuse it.

    PRECONDITIONS. (1) `now()` is read at least once per 71.6 minutes, or a wrap goes
    uncounted. Every record reads it, and the 1 Hz `record_barcode` guarantees it.
    (2) It must NOT read `now_us()` before capture starts, and it is deliberately not
    primed at construction: the RP1 backend fixes its timebase origin in
    `start_strobed_capture`, after the `Recorder` exists, so an earlier reading would be
    on a dead origin and the first real reading would count a spurious wrap, shifting the
    whole run by 2^32 us. The first `unwrap` seeds the clock instead.

    Ticks cannot go negative: every raw tick is at or after the origin (the PIO counter
    reads 0 at enable, edges before the origin are refused by `edge_tick_us`, and B ticks
    are `now_us()`), and lies within +-2^31 us of `now()`.

    A run that never misses a wrap gets exactly the values the per-path unwrappers
    gave, on both backends: the same origin, with the offset starting at 0.

    THE LOCK. Capture threads call `unwrap` concurrently on the RP1 backend. It covers
    only `now_us()` and integer arithmetic -- no I/O and nothing that blocks -- so it
    cannot stall a capture thread the way the sink lock `Recorder` warns against would,
    whose problem is `flush()` fsyncing while holding it.
    """

    def __init__(self, now_us: Callable[[], int]) -> None:
        self._now_us = now_us
        self._lock = threading.Lock()
        self._offset = 0
        self._previous_raw: int | None = None

    def _now_locked(self) -> int:
        raw = self._now_us()
        if self._previous_raw is not None and raw < self._previous_raw:
            self._offset += TICK_WRAP_US
        self._previous_raw = raw
        return raw + self._offset

    def now(self) -> int:
        with self._lock:
            return self._now_locked()

    def unwrap(self, raw: int) -> int:
        with self._lock:
            now = self._now_locked()
            delta = (raw - now) % TICK_WRAP_US
            if delta >= TICK_WRAP_US // 2:
                delta -= TICK_WRAP_US
            return now + delta

    def unwrap_reading(self, raw: int) -> int:
        """A raw `now_us()` READING taken earlier, unwrapped: the value congruent to `raw`
        mod 2^32 at or before `now()`.

        For readings only, such as how far a capture path has delivered, never for ticks.
        A reading cannot lie after now, since it came from this clock earlier, so "at or
        before" is exact for one up to 71.6 min old, where `unwrap`'s nearest is exact only
        to 35.8 min. That matters for a capture path that has stalled: nearest would carry
        a mark more than 35.8 min old a wrap FORWARD, past any boundary, and a roll would
        stop waiting for a path that has delivered nothing. A tick can lie after now (see
        above), which is why ticks get nearest.
        """
        with self._lock:
            now = self._now_locked()
            return now - (now - raw) % TICK_WRAP_US


class _MemorySink:
    """The historical behaviour: hold everything, sort on read. Correct for tests and
    short runs; fatal for a whole day, which is what SegmentWriter is for."""

    def __init__(self) -> None:
        self.records: list[Record] = []

    def write(self, record: Record) -> None:
        self.records.append(record)


class Recorder:
    """Collects strobed code words, individual edges and this box's own barcodes onto
    one unwrapped clock.

    THREE delivery paths -- code words, edges, and the barcodes this box emits -- share
    ONE `_BoxClock`, which places each raw tick at the value nearest `backend.now_us()`
    unwrapped (see its docstring). Barcodes go through here rather than straight to the
    sink for one reason -- an un-unwrapped `B` tick is on a DIFFERENT TIMEBASE from the
    `E` and `W` ticks it exists to align, and diverges from them by k*2^32 us after the
    first wrap, i.e. after 71.6 minutes of a day-long run. Every path reads that clock, and
    the 1 Hz barcode path guarantees it is read well inside one wrap period, so a word
    or edge path that goes silent for hours still lands on the barcodes' timebase.

    `sink` takes each record as it arrives. The default accumulates in memory and
    `records()` returns them tick-sorted. A streaming sink (`SegmentWriter`) receives
    them in ARRIVAL order instead, because the paths deliver independently and a stream
    cannot be globally sorted -- the segment header declares `ordering="per-path"` and
    the reader merges.

    `sink.write()` MUST BE CALLED FROM ONE THREAD. All three paths plus the emit loop
    call it, and nothing here serialises them. `FakeBackend` dispatches synchronously so
    no test can see this, but the RP1 backend delivers from capture threads, where
    concurrent writes interleave partial lines and `SegmentWriter`'s `record_count` and
    barcode span become unguarded read-modify-writes. Measured on 8 threads x 250
    records: 313 of 2000 increments lost, while every line still reached the file -- so
    the trailer's count silently disagrees with the records beneath it.

    SATISFY IT BY WRAPPING THE SINK IN `QueuedSink`, not by taking a lock. A lock has to
    cover `flush()` too, and `SegmentWriter.flush()` fsyncs, so a capture thread would
    block on disk (measured: 229 ms) and stop draining an 8-deep FIFO fed by
    `push noblock` -- turning a data race into dropped words. Recorder itself stays a
    direct call, so the fake path is unchanged.
    """

    def __init__(self, backend: SyncBackend, sink: RecordSink | None = None) -> None:
        self._backend = backend
        # One ternary, not two on the same condition: the pair could be edited apart,
        # and `_memory` set with `_sink` pointing elsewhere is a Recorder that silently
        # records into a sink `records()` will never return.
        self._memory = _MemorySink() if sink is None else None
        self._sink = sink if self._memory is None else self._memory
        # Not read here: see _BoxClock precondition (2). The first unwrap seeds it.
        self._clock = _BoxClock(backend.now_us)

    def capture_codes(self, data_base: int, data_count: int, strobe_pin: int) -> None:
        self._backend.start_strobed_capture(
            data_base, data_count, strobe_pin, self._on_word
        )

    def capture_edges(self, pins: Sequence[int]) -> None:
        self._backend.start_edge_capture(pins, self._on_edge)

    def _on_word(self, tick: int, word: int) -> None:
        self._sink.write(CodeWord(tick_us=self._clock.unwrap(tick), word=word))

    def _on_edge(self, gpio: int, level: int, tick: int) -> None:
        self._sink.write(
            Edge(tick_us=self._clock.unwrap(tick), gpio=gpio, level=level)
        )

    def record_barcode(self, tick: int, value: int) -> None:
        """Record a barcode this box emitted, unwrapping its tick on the shared clock.

        `tick` is the RAW backend counter sampled immediately BEFORE the frame is
        emitted, so the recorded tick marks the frame's lead rising edge rather than
        its end 200 ms later. The emit loop calls this instead of writing to the sink
        itself, so `B` ticks land on the same unwrapped timebase as `E` and `W`.
        """
        self._sink.write(
            BarcodeEmitted(tick_us=self._clock.unwrap(tick), value=value)
        )

    def now(self) -> int:
        """The box clock now, unwrapped: the timebase every record lands on. A roll takes
        its boundary from here (wl_sync.cli)."""
        return self._clock.now()

    def delivered_through(self) -> dict[str, int | None]:
        """`backend.delivered_through()` on the box clock: for each capture path started, an
        unwrapped reading W such that every record that path captured at or before W has
        reached the sink. None for a path that cannot say yet.

        THE BACKEND KNOWS WHEN, THIS CLOCK KNOWS WHICH WRAP. The pumps see their buffers
        empty and can read `now_us()`, but they hold no wrap count; this clock does, so the
        split is the one ticks already follow. PER PATH, NOT ONE MINIMUM: a minimum of raw
        readings picks the wrong path across a wrap, and the run loop names the path that
        is behind.
        """
        return {
            path: None if raw is None else self._clock.unwrap_reading(raw)
            for path, raw in self._backend.delivered_through().items()
        }

    def records(self) -> list[Record]:
        """Tick-sorted, and empty when a streaming sink was supplied -- nothing is held."""
        if self._memory is None:
            return []
        return sorted(self._memory.records, key=lambda record: record.tick_us)


def frame_times(records: Iterable[Record], gpio: int) -> list[int]:
    """Camera exposure times on one frame-time line, in tick order.

    FALLING EDGES ONLY, AND THAT IS A HARDWARE FACT RATHER THAN A CONVENTION. A FLIR
    camera's ExposureActive strobe asserts by pulling LOW, and that edge is actively driven
    by the opto transistor — fast, and independent of the cable. The RISING edge is RC
    through the 1 kΩ pull-up and varies with cable length, so a frame time taken from it,
    or a pulse WIDTH measured across the pair, inherits a systematic error that looks
    perfectly clean on a scope. See hardware/breakout/frame-time-inputs.md §6.

    Both edges are still recorded — the log is a record of what the pin did, and the level
    field already distinguishes them. This function is where the decision about which one
    means "the frame happened" lives, so it exists once rather than at every call site.
    """
    return [
        record.tick_us
        for record in records
        if isinstance(record, Edge)
        and record.gpio == gpio
        and record.level == _FALLING
    ]


class _FlushBarrier:
    """A flush the caller waits on, rather than a marker it drops and forgets.

    spec Sec.4 promises a crash loses at most one second, and the emit loop delivers
    that by flushing every second. A flush that merely queued would return while
    records were still above it, quietly turning the promise into "one second plus
    however far behind the drain thread is". Waiting keeps the bound exact, and costs
    only the emit loop -- which already blocks on fsync today.
    """

    __slots__ = ("done", "args")

    def __init__(self, args: tuple = ()) -> None:
        self.done = threading.Event()
        self.args = args


class _CloseRequest(_FlushBarrier):
    """Close, after everything already queued has gone down."""


class _RollRequest(_FlushBarrier):
    """Close the current sink, then continue into the one `make_sink` builds.

    Carries the factory, not a finished sink, so the new segment is created on the owner
    thread AFTER the old one's trailer is down. Building it earlier would open the next
    segment while the previous is still being written.
    """

    __slots__ = ("make_sink",)

    def __init__(self, make_sink, args: tuple = ()) -> None:
        super().__init__(args)
        self.make_sink = make_sink


class QueuedSink:
    """Serialises fan-in from several threads onto one owner thread.

    WHY A QUEUE AND NOT A LOCK, which is the obvious answer and the wrong one. The
    lock would have to cover `flush()` as well as `write()`, since flushing mid-write
    is the same hazard -- and `SegmentWriter.flush()` calls `os.fsync()`. A capture
    thread that blocks on disk stops draining the PIO RX FIFO, which is 8 words deep
    and fed by `push noblock`, so the stall becomes DROPPED WORDS. That fails Task 5b
    Step 3's "zero dropped words" outright. A lock would trade a data race for data
    loss, which is the worse of the two.

    Here nothing but the drain thread ever touches the wrapped sink, so the sink needs
    no lock and stays exactly as simple as it is -- `Recorder` and `SegmentWriter` both
    document that they must be called from one thread, and this is what makes that
    true again once the RP1 backend delivers from capture threads.

    ADDITIVE BY DESIGN. `Recorder`'s default path is still a direct call. `cli.run()`
    wraps its segment on every backend, FakeBackend included, so the tests exercise the
    path the box runs; for synchronous dispatch the wrapper changes nothing observable.

    A FAILING SINK MUST NOT DIE QUIETLY. If the wrapped sink raises, the drain thread
    would otherwise exit and every later record would be accepted and discarded, with
    the segment simply stopping mid-day and nothing saying so. The exception is held
    and re-raised on the next `write`, `flush` or `close` instead.
    """

    def __init__(self, sink: RecordSink) -> None:
        self.sink = sink
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._error: BaseException | None = None
        # Guards the closed-flag check and the enqueue TOGETHER, and nothing else. No
        # I/O ever happens under it -- it covers a boolean read and a deque append -- so
        # it does not reintroduce the fsync blocking that ruled a lock out for the sink
        # itself. Without it a writer can pass the check, enqueue BEHIND a close request
        # already in the queue, and have its record dropped by a drain thread that has
        # returned: a write that succeeded and then vanished.
        self._gate = threading.Lock()
        self._thread = threading.Thread(
            target=self._drain, name="wl-sync-sink", daemon=True
        )
        self._thread.start()

    def _check(self) -> None:
        if self._error is not None:
            raise RuntimeError("the sink's owner thread died") from self._error
        if self._closed:
            raise RuntimeError("sink is closed; the owner thread will never take this")

    def write(self, record: Record) -> None:
        """Enqueue. Never waits on the sink, which is the whole point."""
        with self._gate:
            self._check()
            self._queue.put(record)

    def flush(self) -> None:
        with self._gate:
            self._check()
            barrier = _FlushBarrier()
            self._queue.put(barrier)
        self._await(barrier)
        self._check()

    def close(self, *args) -> None:
        """Drain what is queued, close the wrapped sink, and stop the owner thread.

        A close that dropped the tail would lose the end of every session, which is
        where the day's last trials are.
        """
        with self._gate:
            if self._closed:
                return
            self._closed = True
            request = _CloseRequest(args)
            self._queue.put(request)
        self._await(request)
        self._thread.join()
        if self._error is not None:
            raise RuntimeError("the sink's owner thread died") from self._error

    def roll(self, make_sink: Callable[[], RecordSink], *close_args) -> None:
        """Close the current sink with `close(*close_args)` and carry on into `make_sink()`.

        THE OWNER THREAD DOES IT, NOT THE CALLER. Capture threads hold this QueuedSink,
        not the segment under it, and keep writing through a roll without ever seeing the
        segment change or waiting on it. Swapping `self.sink` from the caller's thread
        would race the drain thread's `write` into a half-closed segment. Queued in FIFO
        order, a roll also splits the stream exactly: records written before it land in
        the old segment, records written after it in the new one.

        Blocks until the owner thread is done, as `flush()` and `close()` do. A failure
        in either half is held and raised here and on every later `write`: if the close
        succeeded but `make_sink()` raised, the old segment is already closed, and the
        owner thread stops rather than write into it.
        """
        with self._gate:
            self._check()
            request = _RollRequest(make_sink, close_args)
            self._queue.put(request)
        self._await(request)
        self._check()

    def _await(self, barrier: _FlushBarrier) -> None:
        """Wait for the owner thread to reach a barrier -- OR TO HAVE DIED.

        A bare `wait()` deadlocks: a write that raises makes the drain thread return,
        so nothing is left to set the barrier and the caller waits forever. On the rig
        that is the daemon hanging at session end with the segment still open and no
        trailer -- so the day it just finished recording reports as a crash, caused by
        the very error handling meant to make failure visible.
        """
        while not barrier.done.wait(0.05):
            if not self._thread.is_alive():
                return

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if isinstance(item, _CloseRequest):
                try:
                    close = getattr(self.sink, "close", None)
                    if close is not None:
                        close(*item.args)
                except BaseException as error:  # noqa: BLE001 - surfaced on close()
                    self._error = error
                finally:
                    item.done.set()
                return
            if isinstance(item, _RollRequest):
                try:
                    close = getattr(self.sink, "close", None)
                    if close is not None:
                        close(*item.args)
                    self.sink = item.make_sink()
                except BaseException as error:  # noqa: BLE001 - surfaced on roll()
                    # Return, as a failed write does: the old sink is closed, so
                    # nothing may write into it again, and _check() now refuses.
                    self._error = error
                    return
                finally:
                    item.done.set()
                continue
            if isinstance(item, _FlushBarrier):
                try:
                    flush = getattr(self.sink, "flush", None)
                    if flush is not None:
                        flush()
                except BaseException as error:  # noqa: BLE001 - surfaced on flush()
                    self._error = error
                finally:
                    item.done.set()
                continue
            try:
                self.sink.write(item)
            except BaseException as error:  # noqa: BLE001 - surfaced on the next call
                self._error = error
                return
