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

from collections.abc import Iterable, Sequence

from wl_sync.backend import SyncBackend
from wl_sync.barcode import encode
from wl_sync.log import TICK_WRAP_US, BarcodeEmitted, CodeWord, Edge, Record
from wl_sync.pins import FRAME_TIME_GPIO

_FALLING = 0


class BarcodeGenerator:
    """Emits one barcode frame per call.

    `value_source` is called for each frame and returns the value to emit -- normally
    the wall clock, since a barcode value IS seconds since 2020 (see wl_sync.clock).
    The result is clamped to strictly exceed the previous one, so monotonicity holds at
    the point of emission whatever the source does. A repeated barcode would be two
    different moments carrying one identity, which is the one thing it must never be.
    """

    def __init__(self, backend: SyncBackend, pin: int, value_source) -> None:
        self._backend = backend
        self._pin = pin
        self._value_source = value_source
        self._last: int | None = None

    def emit_frame(self) -> int:
        value = self._value_source()
        if self._last is not None and value <= self._last:
            value = self._last + 1
        self._backend.emit_pulses(self._pin, encode(value))
        self._last = value
        return value


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
    no test can see this, but the RP1 backend will deliver from capture threads, where
    concurrent writes interleave partial lines and `SegmentWriter`'s `record_count` and
    barcode span become unguarded read-modify-writes. The constraint is stated rather
    than enforced so the backend inherits it as a design decision instead of finding it
    as a bug; whoever introduces real threads owns adding the lock.
    """

    def __init__(self, backend: SyncBackend, sink=None) -> None:
        self._backend = backend
        self._memory = _MemorySink() if sink is None else None
        self._sink = self._memory if sink is None else sink
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
