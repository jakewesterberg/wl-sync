"""Hardware backend protocol and an in-memory fake.

Deliberately mechanism-neutral: the protocol says *what* the sync box needs, not
how a particular chip provides it. On Pi 5 the three functions land on three
different mechanisms — PIO for strobed capture, hardware PWM for camera
triggers, ordinary GPIO for barcode output — and none of that leaks into the
service layer or the tests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol


class SyncBackend(Protocol):
    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> None:
        """Drive pin through (level, duration_us). Timing need not be precise."""

    def start_pwm(self, pin: int, freq_hz: float, duty: float) -> None:
        """Start a hardware PWM channel — the camera trigger."""

    def start_strobed_capture(
        self,
        data_base: int,
        data_count: int,
        strobe_pin: int,
        on_word: Callable[[int, int], None],
    ) -> None:
        """Latch data_count contiguous pins on each strobe edge.

        Calls on_word(tick_us, word). The contiguity requirement is PIO's.
        """

    def start_edge_capture(
        self, pins: Sequence[int], on_edge: Callable[[int, int, int], None]
    ) -> None:
        """Capture transitions on individual pins. Calls on_edge(gpio, level, tick_us)."""

    def now_us(self) -> int:
        """Current value of the hardware tick counter."""


class FakeBackend:
    """Records outputs and dispatches injected inputs. Test double only."""

    def __init__(self) -> None:
        self.emitted: list[tuple[int, list[tuple[int, int]]]] = []
        self.pwm: dict[int, tuple[float, float]] = {}
        self._on_word: Callable[[int, int], None] | None = None
        self._edge_pins: list[int] = []
        self._on_edge: Callable[[int, int, int], None] | None = None
        self._tick = 0

    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> None:
        self.emitted.append((pin, list(pulses)))

    def start_pwm(self, pin: int, freq_hz: float, duty: float) -> None:
        self.pwm[pin] = (freq_hz, duty)

    def start_strobed_capture(
        self,
        data_base: int,
        data_count: int,
        strobe_pin: int,
        on_word: Callable[[int, int], None],
    ) -> None:
        self._on_word = on_word

    def start_edge_capture(
        self, pins: Sequence[int], on_edge: Callable[[int, int, int], None]
    ) -> None:
        self._edge_pins = list(pins)
        self._on_edge = on_edge

    def now_us(self) -> int:
        return self._tick

    def inject_word(self, tick: int, word: int) -> None:
        self._tick = tick
        if self._on_word is not None:
            self._on_word(tick, word)

    def inject_edge(self, gpio: int, level: int, tick: int) -> None:
        self._tick = tick
        if self._on_edge is not None and gpio in self._edge_pins:
            self._on_edge(gpio, level, tick)
