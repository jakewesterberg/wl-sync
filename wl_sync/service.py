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
from collections.abc import Iterable, Sequence
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


class _TickUnwrapper:
    """Undoes 32-bit tick wraparound for one delivery path.

    One per path, never shared. The three paths — the PIO FIFO, edge capture, and
    this box's own barcode emission — deliver independently, so a word can arrive
    after an edge that precedes it in time. A shared counter would read that
    ordinary interleaving as a wraparound and jump every subsequent record forward
    by 4295 seconds. Each path is individually ordered, which is what makes
    per-path state correct.
    """

    def __init__(self) -> None:
        self._offset = 0
        self._previous_raw: int | None = None

    def __call__(self, tick: int) -> int:
        if self._previous_raw is not None and tick < self._previous_raw:
            self._offset += TICK_WRAP_US
        self._previous_raw = tick
        return tick + self._offset


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

    THREE delivery paths, each with its own `_TickUnwrapper`: code words, edges, and
    the barcodes this box emits. Barcodes go through here rather than straight to the
    sink for one reason -- an un-unwrapped `B` tick is on a DIFFERENT TIMEBASE from the
    `E` and `W` ticks it exists to align, and diverges from them by k*2^32 us after the
    first wrap, i.e. after 71.6 minutes of a day-long run. A per-path unwrapper is
    correct here for the same reason it is correct for the other two, and safe at 1 Hz
    because one second is four orders of magnitude below the wrap period.

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
        self._unwrap_words = _TickUnwrapper()
        self._unwrap_edges = _TickUnwrapper()
        self._unwrap_barcodes = _TickUnwrapper()

    def capture_codes(self, data_base: int, data_count: int, strobe_pin: int) -> None:
        self._backend.start_strobed_capture(
            data_base, data_count, strobe_pin, self._on_word
        )

    def capture_edges(self, pins: Sequence[int]) -> None:
        self._backend.start_edge_capture(pins, self._on_edge)

    def _on_word(self, tick: int, word: int) -> None:
        self._sink.write(CodeWord(tick_us=self._unwrap_words(tick), word=word))

    def _on_edge(self, gpio: int, level: int, tick: int) -> None:
        self._sink.write(
            Edge(tick_us=self._unwrap_edges(tick), gpio=gpio, level=level)
        )

    def record_barcode(self, tick: int, value: int) -> None:
        """Record a barcode this box emitted, unwrapping its tick like any other path.

        `tick` is the RAW backend counter sampled immediately BEFORE the frame is
        emitted, so the recorded tick marks the frame's lead rising edge rather than
        its end 200 ms later. The emit loop calls this instead of writing to the sink
        itself, so `B` ticks land on the same unwrapped timebase as `E` and `W`.
        """
        self._sink.write(
            BarcodeEmitted(tick_us=self._unwrap_barcodes(tick), value=value)
        )

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

    ADDITIVE BY DESIGN. `Recorder`'s default path is still a direct call, so
    `FakeBackend`'s synchronous dispatch is untouched. Only the hardware path wraps.

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
        self._check()
        self._queue.put(record)

    def flush(self) -> None:
        self._check()
        barrier = _FlushBarrier()
        self._queue.put(barrier)
        barrier.done.wait()
        self._check()

    def close(self, *args) -> None:
        """Drain what is queued, close the wrapped sink, and stop the owner thread.

        A close that dropped the tail would lose the end of every session, which is
        where the day's last trials are.
        """
        if self._closed:
            return
        request = _CloseRequest(args)
        self._queue.put(request)
        self._closed = True
        request.done.wait()
        self._thread.join()
        if self._error is not None:
            raise RuntimeError("the sink's owner thread died") from self._error

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
