"""GPIO arithmetic: pulse scheduling, edge timebase, and drop detection.

Pure, and tested on a laptop. The libgpiod request is opened in wl_sync.rp1.backend,
which is hardware-gated, and handed to `EdgePump.pump_once`, so a stand-in can take its
place. Splitting them this way is the same move Task 5a made for the PIO FIFO, and for
the same reason: the parts that can be wrong quietly are the parts worth testing, and
none of them need a Pi.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

TICK_WRAP_US = 1 << 32

# How long the edge pump waits for an edge before it looks again. It bounds how stale
# the pump's mark gets on an idle line, and a roll waits on that mark (wl_sync.cli,
# _await_delivery, for the arithmetic). It was 0.2 s, which alone would have held a roll
# for up to 0.4 s. At 20 ms the thread wakes 50 times a second, for a select() and a
# clock read, beside the word pump's thousand polls a second.
EDGE_WAIT_S = 0.02

# The most edges one read asks for. It is the bindings' own default, named here because
# a read that returns FEWER than it asked for is how the pump knows the kernel's buffer
# is empty. THAT HOLDS ONLY IF THE REQUEST'S event_buffer_size IS AT LEAST THIS:
# libgpiod caps each read at the buffer it allocates from that size
# (gpiod_edge_event_buffer_read_fd), and a smaller one would make every full read look
# short. Rp1Backend.start_edge_capture refuses a request that breaks this.
EDGE_READ_MAX = 64

# How long the kernel may take from stamping an edge to putting it in the request's
# buffer. gpiolib-cdev.c stamps it in the hard IRQ handler (edge_irq_handler) and puts
# it in the buffer from the IRQ thread (edge_irq_thread -> linereq_put_event), so an
# edge can be stamped before the pump looks and arrive after. UNMEASURED ON THE BOX. An
# IRQ thread runs at real-time priority, so one wait is a generous allowance, not a
# measured one. It must not exceed EDGE_WAIT_S, because a wait that times out relies on
# the IRQ thread having caught up within it. The pump witnesses it on every read (see
# EdgePump.pump_once) and the health report warns when edges go over; bench day should
# measure it directly, under load.
EDGE_STAMP_LAG_US = round(EDGE_WAIT_S * 1_000_000)


class DroppedEdgeError(RuntimeError):
    """The kernel's edge-event sequence numbers skipped, so events were lost."""


def pulse_deadlines(
    pulses: Sequence[tuple[int, int]], start_ns: int
) -> list[tuple[int, int]]:
    """(level, absolute deadline in ns) for a pulse train.

    ABSOLUTE, NOT A LOOP OF SLEEPS, and that is the whole point. A barcode frame is
    200 ms of 5 ms slots, and the decoder samples each bit at its slot CENTRE measured
    from the lead edge -- it assumes the slots are uniform. Sleeping for each duration
    in turn makes every overshoot permanent: 100 us of oversleep on each of 36 pulses
    is 3.6 ms by the end, outside the +/-2.5 ms a slot centre allows, and the tail of
    the word decodes as garbage. Against absolute deadlines a late pulse is corrected
    by the next one instead of compounding, so only the WORST single delay matters
    rather than their sum.

    Nanoseconds because that is what `time.clock_gettime_ns(CLOCK_MONOTONIC)` gives,
    and rounding to microseconds 36 times would reintroduce a smaller version of the
    drift this exists to remove.
    """
    deadlines: list[tuple[int, int]] = []
    elapsed_us = 0
    for level, duration_us in pulses:
        deadlines.append((level, start_ns + elapsed_us * 1_000))
        elapsed_us += duration_us
    return deadlines


def edge_tick_us(timestamp_ns: int, origin_ns: int) -> int:
    """A kernel edge timestamp on this box's shared 32-bit timebase.

    libgpiod stamps edge events from CLOCK_MONOTONIC inside the kernel, at interrupt
    time -- `LineSettings.event_clock` defaults to `Clock.MONOTONIC`. That is the same
    clock `Rp1Backend.now_us()` counts from, so an edge needs no correction for when
    this process happened to be scheduled. Reading a clock in the callback instead
    would fold thread wake-up latency into every edge, which is precisely the error
    this box exists to keep out of the record.

    Wrapped to 32 bits because every tick in this package is, and `Recorder`'s shared clock
    expects exactly that shape from all three paths.
    """
    if timestamp_ns < origin_ns:
        raise ValueError(
            f"edge timestamp {timestamp_ns} precedes the timebase origin {origin_ns}; "
            "it would sort before every record in the segment"
        )
    return ((timestamp_ns - origin_ns) // 1_000) % TICK_WRAP_US


class SeqnoTracker:
    """Watches libgpiod's edge-event sequence numbers for gaps.

    THE KERNEL DROPS SILENTLY. Its per-request event buffer is finite, and a line that
    chatters faster than this process drains it overflows with no error anywhere -- the
    record simply comes up short. libgpiod numbers every event, so the loss is
    DETECTABLE rather than merely possible, and a missing edge is a reward or a lick
    that reads as never having happened.

    Deliberately raises rather than counting. A short edge record cannot be repaired
    afterwards, so the useful moment to hear about it is while the session is running.
    """

    def __init__(self) -> None:
        self._previous: int | None = None

    def check(self, seqno: int) -> None:
        # The baseline moves BEFORE any raise. Left behind, it measured every later and
        # perfectly consecutive edge against the stale number, so one overflow became
        # a new and larger "lost" report on each edge for the rest of the day.
        previous, self._previous = self._previous, seqno
        if previous is not None and seqno != previous + 1:
            missing = seqno - previous - 1
            raise DroppedEdgeError(
                f"{missing} edge event(s) lost between sequence {previous} and "
                f"{seqno}: the kernel's event buffer overflowed"
            )


class EdgePump:
    """Turns libgpiod edge events into `on_edge` calls, and survives what goes wrong.

    SPLIT OUT SO IT CAN BE TESTED. The thread and the libgpiod request around it are
    hardware; this is the part that decides what a dropped sequence number means and
    what happens when the consumer fails, and both of those are decisions that can be
    wrong quietly.

    A BACKGROUND THREAD THAT DIES ON AN EXCEPTION IS THE FAILURE TO AVOID. Edges would
    simply stop, the segment would keep being written by the other paths, and nothing
    would say that half the rig's signals had gone missing. So errors are HELD rather
    than raised out of the loop, for `check_health()` to surface.

    The two failures are not the same and are not treated the same:

      * A DROPPED EDGE is a data-quality event. The kernel's buffer overflowed and a
        few edges are gone; everything after them is still good. Stopping would trade a
        small detectable gap for the rest of the day's edges, so capture CONTINUES.
      * A FAILING CONSUMER means records are not being kept at all. Continuing would
        spin, delivering into something that cannot accept, so the pump stops.

    `delivered_through` is how far it has delivered, as a raw `now_us()` reading (see
    `pump_once`), for the run loop's roll barrier. None until the first wait or read
    that can say. `stamp_lag_max_us` and `stamp_lag_over` are the witness for the one
    assumption that rule rests on (also `pump_once`).
    """

    def __init__(self, on_edge, origin_ns: int) -> None:
        self._on_edge = on_edge
        self._origin_ns = origin_ns
        self._seqnos = SeqnoTracker()
        self.error: BaseException | None = None
        self.delivered_through: int | None = None
        self.stamp_lag_max_us: int | None = None
        self.stamp_lag_over = 0

    def pump_once(self, request, now_us: Callable[[], int]) -> bool:
        """One wait for edges, and one read if it found any. Returns False when the pump
        should stop. Moves `delivered_through` where the rule below allows.

        WHAT LIBGPIOD AND THE KERNEL GUARANTEE. `wait_edge_events` is select() on the
        request's fd (gpiod/_internal.py, poll_fd), which is readable while the kernel's
        buffer holds an edge (gpiolib-cdev.c, linereq_poll). `read_edge_events` is one
        read() (libgpiod, gpiod_edge_event_buffer_read_fd), and the kernel's read stops
        early only when that buffer is empty (linereq_read). The hard IRQ handler stamps
        an edge, and the IRQ thread puts it in the buffer up to EDGE_STAMP_LAG_US later.

        THE RULE. With T the clock read just before the wait:

          * A wait that times out saw the buffer empty for EDGE_WAIT_S. An edge stamped
            before T reaches the buffer before the wait ends -- that is the assumption,
            EDGE_STAMP_LAG_US <= EDGE_WAIT_S, not a fact about the kernel -- so, the
            buffer having stayed empty, it had been read and delivered already. The mark
            moves to T - 1.
          * A read that returns fewer than EDGE_READ_MAX emptied the buffer. Once its batch
            is delivered, so is every edge that had reached the buffer. An edge stamped
            just before T may not have, so the mark moves to T - EDGE_STAMP_LAG_US - 1.
          * A read that returns EDGE_READ_MAX says nothing about what is left, and a batch
            that fails says nothing at all. Neither moves the mark.

        NOT "the clock read before a wait whose events were all delivered". A wait
        returns as soon as one edge is in the buffer, at once if one already is, while an
        edge stamped a moment before can still be with the IRQ thread. And a read takes
        at most EDGE_READ_MAX, so one read need not empty the buffer.

        THE WITNESS. Every edge a read returns was in the buffer when the read returned,
        so the clock then, less the edge's stamp, bounds from above how long the kernel
        took to buffer it. The bound also counts the pump's own lateness. The largest is
        `stamp_lag_max_us`, and `stamp_lag_over` counts the edges whose bound exceeded
        EDGE_STAMP_LAG_US. A quiet maximum confirms the assumption on the box.
        """
        began = now_us()
        if not request.wait_edge_events(timeout=EDGE_WAIT_S):
            self.delivered_through = (began - 1) % TICK_WRAP_US
            return True
        events = request.read_edge_events(max_events=EDGE_READ_MAX)
        self._witness(events, now_us())
        if not self.handle(events):
            return False
        if len(events) < EDGE_READ_MAX:
            self.delivered_through = (began - EDGE_STAMP_LAG_US - 1) % TICK_WRAP_US
        return True

    def _witness(self, events, read_at: int) -> None:
        """Bound each edge's stamp-to-buffer lag by `read_at`, the clock when the read
        returned. An edge stamped before the origin is left to `handle` to refuse."""
        for event in events:
            if event.timestamp_ns < self._origin_ns:
                continue
            tick = edge_tick_us(event.timestamp_ns, self._origin_ns)
            bound = (read_at - tick) % TICK_WRAP_US
            if self.stamp_lag_max_us is None or bound > self.stamp_lag_max_us:
                self.stamp_lag_max_us = bound
            if bound > EDGE_STAMP_LAG_US:
                self.stamp_lag_over += 1

    def handle(self, events) -> bool:
        """Deliver a batch. Returns False when the pump should stop."""
        for event in events:
            try:
                self._seqnos.check(event.global_seqno)
            except DroppedEdgeError as dropped:
                self.error = dropped  # recorded, and we keep going
            try:
                rising = event.event_type is event.Type.RISING_EDGE
                self._on_edge(
                    event.line_offset,
                    1 if rising else 0,
                    edge_tick_us(event.timestamp_ns, self._origin_ns),
                )
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.error = error
                return False
        return True
