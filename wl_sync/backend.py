"""Hardware backend protocol and an in-memory fake.

Deliberately mechanism-neutral: the protocol says *what* the sync box needs, not
how a particular chip provides it. On Pi 5 these land on different mechanisms —
PIO for strobed capture, ordinary GPIO for barcode output and for edge capture —
and none of that leaks into the service layer or the tests.

`start_pwm()` was here until 2026-08-16, described as "the camera trigger". It is
gone because the thing it drove is gone: the cameras free-run, so this box emits
no trigger at all. The five camera-facing panel BNCs now carry the barcode — a
timebase to record rather than a clock to obey — and GPIO18/19 are spare. What
replaced it is the opposite direction of travel: the cameras' own exposure
strobes come back IN on GPIO26/27, and `start_edge_capture()` already covers
that, so no new backend capability was needed. See
hardware/breakout/camera-sync-change.md and frame-time-inputs.md.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class CaptureDegraded(RuntimeError):
    """What `check_health()` raises: every problem found in one call, not the first.

    `kinds` names each problem without its details -- which path, and what went wrong
    -- so the run loop can tell a persisting failure from a new one even when its
    message carries a count that changes every second. The message joins them all.
    """

    def __init__(self, problems: Sequence[tuple[str, str]]) -> None:
        self.kinds = frozenset(kind for kind, _ in problems)
        super().__init__("; ".join(message for _, message in problems))


@dataclass(frozen=True)
class CaptureLoss:
    """Events a capture path saw and could not record, as a backend reports them. The
    Recorder makes each one an L record (wl_sync.log.Loss), on the box clock, in the
    stream of the path that reported it; that docstring defines every field.

    `cause`, `count`, `bound` and `gpio` are the record's own. The rest place it:

      * `after`: the RAW tick of the first record of the path known to follow the loss,
        as every tick a backend hands over is raw. None when nothing of the path follows
        it: the loss lies after everything the path has delivered, so the window opens
        at the highest tick the path has delivered and has no end.
      * `span_us`: how far before `after` the last record known to precede the loss lies,
        in us, exactly. None when no such record is known. A DISTANCE, NOT A TICK,
        because that record can be any age: a line quiet for hours, then an overflow.
        A raw tick more than half a wrap old would be placed a wrap off, while `after`
        is recent, being what showed the loss.
      * `ahead`: the loss is reported just before the record at `after`, which the
        backend delivers next, so the L record takes that record's tick. Otherwise it is
        reported once later records have been delivered, and sits at the highest tick
        its path has written.

    A backend reports only a loss whose count it can bound below by 1. A report the
    Recorder cannot make into a valid record is dropped there with an ERROR, never
    raised back into the capture path (Recorder._on_loss).
    """

    cause: str
    count: int
    bound: str
    gpio: int | None = None
    after: int | None = None
    span_us: int | None = None
    ahead: bool = False


@dataclass(frozen=True)
class StampLag:
    """The edge path's witness for the assumption a roll's boundary rests on: that the
    kernel puts an edge in its buffer within `allowance_us` of stamping it.

    `max_us` is the largest upper bound seen this run on that delay: the clock when the
    edge pump's read returned, less the edge's stamp. The bound also counts how late the
    pump itself was, so one over the allowance means the assumption could not be
    confirmed, not that it failed. `over` counts the edges whose bound exceeded the
    allowance, and only ever grows.
    """

    max_us: int
    over: int
    allowance_us: int


class SyncBackend(Protocol):
    timebase: str
    """Which clocks this backend's ticks count, written into every segment header
    (`wl_sync.log.SyncBoxLogHeader.timebase` defines the values)."""

    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> None:
        """Drive pin through (level, duration_us). Timing need not be precise."""

    def start_strobed_capture(
        self,
        data_base: int,
        data_count: int,
        strobe_pin: int,
        on_word: Callable[[int, int], None],
        on_loss: Callable[[CaptureLoss], None] | None = None,
    ) -> None:
        """Latch data_count contiguous pins on each strobe edge.

        Calls on_word(tick_us, word). The contiguity requirement is PIO's. Calls
        on_loss(CaptureLoss) for code words it lost, from the thread that calls on_word,
        so a loss keeps its place among the words.
        """

    def start_edge_capture(
        self,
        pins: Sequence[int],
        on_edge: Callable[[int, int, int], None],
        on_loss: Callable[[CaptureLoss], None] | None = None,
    ) -> None:
        """Capture transitions on individual pins. Calls on_edge(gpio, level, tick_us),
        and on_loss(CaptureLoss) for edges it lost, from the same thread."""

    def now_us(self) -> int:
        """Current value of the hardware tick counter."""

    def delivered_through(self) -> dict[str, int | None]:
        """How far each capture path has delivered, which a roll waits on.

        One entry per path started, named as check_health() names it: a raw `now_us()`
        reading W such that every record the path captured at a reading at or before W
        has been passed to its callback. None until the path can say. A gap the path has
        seen and cannot yet call a loss holds W back, so that if it is one, its L record
        is passed on first (rp1.gpio.EdgePump.pump_once).

        Raw, as every tick a backend hands over is. The wrap count belongs to the
        Recorder's one clock, which unwraps these and compares them with the roll's
        boundary (wl_sync.cli._await_delivery).

        A path that will deliver nothing more, because its thread has stopped, is left
        out: there is nothing to wait for.
        """

    def stamp_lag(self) -> StampLag | None:
        """How late edges have reached this process after the kernel stamped them, as an
        upper bound (`StampLag`), for the health report. None for a backend with no such
        delay, or before the first edge."""

    def check_health(self) -> None:
        """Raise if anything capturing in the background has failed.

        The run loop calls this once per iteration, because a capture path that dies
        goes quiet rather than complaining. A backend with nothing in the background
        simply returns.
        """

    def close(self) -> None:
        """Stop capturing, delivering whatever was still in flight.

        Called BEFORE the sink is closed, so the last records land in the segment
        rather than against a closed sink.
        """


class FakeBackend:
    """Records outputs and dispatches injected inputs. Test double only."""

    timebase = "fake"

    def __init__(self) -> None:
        self.emitted: list[tuple[int, list[tuple[int, int]]]] = []
        self._on_word: Callable[[int, int], None] | None = None
        self._word_loss: Callable[[CaptureLoss], None] | None = None
        self._edge_pins: list[int] = []
        self._on_edge: Callable[[int, int, int], None] | None = None
        self._edge_loss: Callable[[CaptureLoss], None] | None = None
        self._tick = 0

    def emit_pulses(self, pin: int, pulses: Sequence[tuple[int, int]]) -> None:
        self.emitted.append((pin, list(pulses)))

    def start_strobed_capture(
        self,
        data_base: int,
        data_count: int,
        strobe_pin: int,
        on_word: Callable[[int, int], None],
        on_loss: Callable[[CaptureLoss], None] | None = None,
    ) -> None:
        self._on_word = on_word
        self._word_loss = on_loss

    def start_edge_capture(
        self,
        pins: Sequence[int],
        on_edge: Callable[[int, int, int], None],
        on_loss: Callable[[CaptureLoss], None] | None = None,
    ) -> None:
        self._edge_pins = list(pins)
        self._on_edge = on_edge
        self._edge_loss = on_loss

    def now_us(self) -> int:
        return self._tick

    def set_now_us(self, true_us: int) -> None:
        """Move the counter to `true_us` of elapsed time, wrapped to 32 bits like the
        real one. The clock never moves on its own and `inject_*` leave it alone, so a
        test can hold it somewhere a delivered tick is NOT."""
        self._tick = true_us & 0xFFFFFFFF

    def _require_in_contract(self, tick: int) -> None:
        """Refuse a delivered tick far from the clock. `Recorder` places a tick at the value
        nearest `now_us()`, so one 2^30 us or more away would be mapped a wrap off -- or
        negative -- without a sound. Loud here, so a test fixes its clock."""
        distance = (tick - self._tick + (1 << 31)) % (1 << 32) - (1 << 31)
        if abs(distance) >= 1 << 30:
            raise AssertionError(
                f"injected tick {tick} is {distance} us from now_us() {self._tick}; "
                "call set_now_us() so the clock is near the tick"
            )

    def delivered_through(self) -> dict[str, int | None]:
        """Every path started has delivered through now, because dispatch is synchronous:
        a record is delivered before inject_* returns. Read through now_us(), so a
        subclass with a clock of its own says its own now.

        A SUBCLASS THAT DELIVERS ASYNCHRONOUSLY, from a thread or a queue of its own, MUST
        OVERRIDE THIS. Otherwise it claims records it has not delivered, and a roll's
        barrier passes without them.
        """
        now = self.now_us()
        marks: dict[str, int | None] = {}
        if self._on_word is not None:
            marks["event-code capture"] = now
        if self._on_edge is not None:
            marks["edge capture"] = now
        return marks

    def stamp_lag(self) -> StampLag | None:
        return None  # no kernel between an injected edge and its delivery

    def check_health(self) -> None:
        pass  # dispatch is synchronous, so nothing runs in the background to fail

    def close(self) -> None:
        pass  # nothing is ever in flight

    def inject_word(self, tick: int, word: int) -> None:
        # Delivers `tick` as given and does NOT move the clock: a delivered tick and
        # the box's clock are separate facts (a word can arrive late, or be stamped a
        # little after now_us()), and `set_now_us` is how a test says what time it is.
        self._require_in_contract(tick)
        if self._on_word is not None:
            self._on_word(tick, word)

    def inject_edge(self, gpio: int, level: int, tick: int) -> None:
        self._require_in_contract(tick)
        if self._on_edge is not None and gpio in self._edge_pins:
            self._on_edge(gpio, level, tick)

    def inject_word_loss(self, loss: CaptureLoss) -> None:
        """Report a loss on the code-word path, as Rp1Backend's word pump reports a
        desync. Any W cause, in the shape its cause has (wl_sync.log.LOSS_SHAPES): the
        Recorder places what it is given."""
        if loss.after is not None:
            self._require_in_contract(loss.after)
        if self._word_loss is not None:
            self._word_loss(loss)

    def inject_edge_loss(self, loss: CaptureLoss) -> None:
        """Report a loss on the edge path, as Rp1Backend's edge pump reports an overflow,
        a pair at one level or an unaccounted number."""
        if loss.after is not None:
            self._require_in_contract(loss.after)
        if self._edge_loss is not None:
            self._edge_loss(loss)
