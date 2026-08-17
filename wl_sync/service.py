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
from wl_sync.log import TICK_WRAP_US, CodeWord, Edge, Record
from wl_sync.pins import FRAME_TIME_GPIO

_FALLING = 0


class BarcodeGenerator:
    """Emits one barcode frame per call, incrementing a monotonic counter.

    The counter is never reset — across sessions or reboots — so every barcode
    the lab ever emits is globally unique and cross-session mis-alignment is
    structurally impossible rather than merely unlikely.
    """

    def __init__(self, backend: SyncBackend, pin: int, start_value: int) -> None:
        self._backend = backend
        self._pin = pin
        self.next_value = start_value

    def emit_frame(self) -> int:
        value = self.next_value
        self._backend.emit_pulses(self._pin, encode(value))
        self.next_value = value + 1
        return value


class _TickUnwrapper:
    """Undoes 32-bit tick wraparound for one delivery path.

    One per path, never shared. The two capture paths — the PIO FIFO and edge
    capture — deliver independently, so a word can arrive after an edge that
    precedes it in time. A shared counter would read that ordinary interleaving
    as a wraparound and jump every subsequent record forward by 4295 seconds.
    Each path is individually ordered, which is what makes per-path state
    correct.
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
    """Collects strobed code words and individual edges onto one unwrapped clock.

    `sink` takes each record as it arrives. The default accumulates in memory and
    `records()` returns them tick-sorted. A streaming sink (`SegmentWriter`) receives
    them in ARRIVAL order instead, because the two capture paths deliver independently
    and a stream cannot be globally sorted -- the segment header declares
    `ordering="per-path"` and the reader merges.
    """

    def __init__(self, backend: SyncBackend, sink=None) -> None:
        self._backend = backend
        self._memory = _MemorySink() if sink is None else None
        self._sink = self._memory if sink is None else sink
        self._unwrap_words = _TickUnwrapper()
        self._unwrap_edges = _TickUnwrapper()

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
