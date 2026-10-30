"""GPIO arithmetic: pulse scheduling, edge timebase, and drop detection.

Pure, and tested on a laptop. The libgpiod request is opened in wl_sync.rp1.backend,
which is hardware-gated, and handed to `EdgePump.pump_once`, so a stand-in can take its
place. Splitting them this way is the same move Task 5a made for the PIO FIFO, and for
the same reason: the parts that can be wrong quietly are the parts worth testing, and
none of them need a Pi.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from wl_sync.backend import CaptureLoss

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

# The most events a line request's kernel buffer can hold, whatever size is asked for:
# linereq_create caps event_buffer_size at GPIO_V2_LINES_MAX * 16 (gpiolib-cdev.c), and
# kfifo_alloc rounds it up to a power of two, which 1024 already is. SeqnoTracker leans on
# it to stop holding a gap open while the pump works through a backlog.
KERNEL_EVENT_BUFFER_MAX = 1024

# How long the edge pump's last look may run when capture stops (EdgePump.finish): twice
# the longest a healthy pump holds a gap open, EDGE_STAMP_LAG_US for the IRQ thread and
# then a wait of EDGE_WAIT_S, so the window closes inside it on any gap held at the stop.
STOP_BUDGET_US = 4 * EDGE_STAMP_LAG_US

# The most passes the last look takes, whatever the clock says: the backstop for a clock
# that does not move, on which no window ever closes. Each pass waits up to EDGE_WAIT_S,
# so with the pass already running when the stop is set the look takes at most
# (STOP_PASSES + 1) x EDGE_WAIT_S = 0.82 s, inside the second close() gives a capture
# thread to stop (wl_sync.rp1.backend._STOP_JOIN_S). At 64 it was 1.3 s, and close()
# released the request under a look still running.
STOP_PASSES = 40

# Both edge-event numbers are __u32 in the uAPI (struct gpio_v2_line_event: seqno,
# line_seqno), so both wrap to 0.
_SEQNO_WRAP = 1 << 32
_HALF = 1 << 31


def _nearest(raw: int, near: int) -> int:
    """The integer congruent to `raw` modulo 2**32 that is nearest to `near`."""
    return near + (raw - near + _HALF) % _SEQNO_WRAP - _HALF


def _since(later: int, earlier: int) -> int:
    """`later - earlier` for two raw now_us() readings, across the clock's 32-bit wrap."""
    return (later - earlier + _HALF) % TICK_WRAP_US - _HALF


class DroppedEdgeError(RuntimeError):
    """Edges the kernel saw that never reached the record as events of their own.

    Three kinds, each counted in edge events:
      * `overflowed`, GPIO -> count: a gap in the line's own numbers. The kernel's
        buffer was full and threw its oldest events away.
      * `coalesced`, GPIO -> count: two events in a row on the line at the same level.
        An edge between them never became an event of its own.
      * `unaccounted`: request numbers that never arrived and that nothing on any line
        explains. The cause cannot be known, so none is named.
    Raised as the subclass for its one kind, and as this class when one report carries
    more than one.
    """

    def __init__(
        self,
        overflowed: dict[int, int] | None = None,
        coalesced: dict[int, int] | None = None,
        unaccounted: int = 0,
    ) -> None:
        self.overflowed = dict(overflowed or {})
        self.coalesced = dict(coalesced or {})
        self.unaccounted = unaccounted
        super().__init__(_describe(self.overflowed, self.coalesced, self.unaccounted))

    def __reduce__(self):
        # Rebuilt from its counts, not from its message, so copy and pickle work.
        return type(self), (self.overflowed, self.coalesced, self.unaccounted)

    @property
    def lost(self) -> int:
        return (
            sum(self.overflowed.values()) + sum(self.coalesced.values()) + self.unaccounted
        )

    def combine(self, other: DroppedEdgeError) -> DroppedEdgeError:
        """Both reports as one, so a health check that finds both says so."""
        overflowed, coalesced = dict(self.overflowed), dict(self.coalesced)
        for gpio, count in other.overflowed.items():
            overflowed[gpio] = overflowed.get(gpio, 0) + count
        for gpio, count in other.coalesced.items():
            coalesced[gpio] = coalesced.get(gpio, 0) + count
        return _dropped(overflowed, coalesced, self.unaccounted + other.unaccounted)


class EdgeBufferOverflowError(DroppedEdgeError):
    """The kernel's buffer was full, so it threw its oldest events away (kfifo_skip)."""


class CoalescedEdgeError(DroppedEdgeError):
    """Two events in a row on one line at the same level: an edge between them was
    merged or latched with another and never became an event of its own."""


class UnaccountedEdgeError(DroppedEdgeError):
    """Numbers the kernel gave edges that never arrived, with nothing to say why."""


def _dropped(overflowed, coalesced, unaccounted=0) -> DroppedEdgeError:
    kinds = [
        kind
        for kind, present in (
            (EdgeBufferOverflowError, overflowed),
            (CoalescedEdgeError, coalesced),
            (UnaccountedEdgeError, unaccounted),
        )
        if present
    ]
    kind = kinds[0] if len(kinds) == 1 else DroppedEdgeError
    return kind(overflowed, coalesced, unaccounted)


def _describe(
    overflowed: dict[int, int], coalesced: dict[int, int], unaccounted: int
) -> str:
    """"N edge event(s) lost (cause on where): why", one clause per kind."""

    def where(counts):
        if len(counts) == 1:
            return f" on GPIO{next(iter(counts))}"
        return ": " + ", ".join(f"{n} on GPIO{g}" for g, n in sorted(counts.items()))

    parts = []
    if overflowed:
        parts.append(
            f"{sum(overflowed.values())} edge event(s) lost (kernel buffer overflow"
            f"{where(overflowed)}): the edge pump fell a whole kernel buffer behind"
        )
    if coalesced:
        parts.append(
            f"{sum(coalesced.values())} edge event(s) lost (coalesced interrupts"
            f"{where(coalesced)}): two events in a row at the same level, so an edge "
            "between them never became an event of its own"
        )
    if unaccounted:
        parts.append(
            f"{unaccounted} edge event(s) lost on an unknown line: numbered interrupts "
            "that never arrived, from coalesced interrupts, a kernel buffer overflow on "
            "a line that has not fired since, or an event delivered later than the window"
        )
    return "; ".join(parts)


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


@dataclass(eq=False)
class _Sighting:
    """The read that first showed a run of missing numbers: `read_at` is the clock when
    it returned, and `read_after` counts the events read since the IRQ thread's allowance
    on it ran out."""

    read_at: int
    read_after: int = 0


@dataclass(eq=False)
class _Around:
    """The kernel stamps of the events numbered just below and just above a run of
    missing numbers, when the read showed it: its L record's window. `below_ns` is None
    when no event is numbered below it."""

    below_ns: int | None
    above_ns: int


@dataclass
class _Gap:
    first: int  # request numbers, unwrapped, inclusive
    last: int
    seen: _Sighting
    around: _Around  # shared by every piece the gap is later split into


@dataclass
class _Line:
    seqno: int  # the line's own number for its last event, unwrapped
    number: int  # the request's number for that event, unwrapped
    before: int  # the request's number for the event before it
    rising: bool
    stamp: int  # the kernel's stamp for that event, in ns
    compared: bool = False  # its level was compared with an event before it
    paired: bool = False  # and was the same: a pair at one level, with its excuse


@dataclass(frozen=True)
class _Site:
    """One loss, as an L record will name it: the cause, the line if known, how many and
    how sure, and the stamps of the events known to come just before and just after it
    (wl_sync.log.Loss)."""

    cause: str
    gpio: int | None
    count: int
    bound: str
    before_ns: int | None
    after_ns: int


class SeqnoTracker:
    """Watches libgpiod's edge-event sequence numbers, and each line's levels, for edges
    that never reached the record as events of their own.

    THE KERNEL DROPS SILENTLY. Its per-request event buffer is finite, and a line that
    chatters faster than this process drains it overflows with no error anywhere -- the
    record simply comes up short. libgpiod numbers every event, so the loss is
    DETECTABLE rather than merely possible, and a missing edge is a reward or a lick
    that reads as never having happened.

    TWO NUMBERS AND A LEVEL, and the kernel source says what each can show (drivers/
    gpio/gpiolib-cdev.c, rpi-6.12.y; libgpiod copies the numbers into EdgeEvent
    unchanged):

      * `global_seqno` counts the request's edges. For a request of several lines the
        hard IRQ handler takes it (edge_irq_handler: atomic_inc_return on lr->seqno),
        and the line's IRQ thread puts the event in the buffer later (edge_irq_thread ->
        linereq_put_event). Each line has its own thread, and nothing orders two of them,
        so the buffer can hold n+1 before n. GPIO26 and GPIO27, the cameras' frame-time
        lines, fire together on every frame, and RP1's one chained handler numbers them
        back to back (rp1_gpio_irq_handler, pinctrl-rp1.c, in bit order).
      * `line_seqno` counts one line's events. edge_irq_thread increments it
        (line->line_seqno++) just before linereq_put_event. Each line gets its own
        threaded IRQ (request_threaded_irq in edge_detector_setup), and an IRQ thread
        runs its handler once at a time (irq_thread, kernel/irq/manage.c). So a line's
        events reach the buffer in its order, and the buffer is a FIFO.
      * The level. With both edges detected, as this box requests them, the thread reads
        the line's level for each event's type (gpiod_get_value_cansleep), so a line's
        events alternate rising and falling.

    A GAP IN A LINE'S OWN NUMBERS IS FINAL AT ONCE, and has one cause: the buffer was
    full, and linereq_put_event threw its oldest event away (kfifo_skip). It is raised by
    `check`, by line, as an overflow.

    TWO EVENTS IN A ROW ON A LINE AT THE SAME LEVEL ARE FINAL AT ONCE TOO, whatever the
    numbers say: an edge between them never became an event of its own. It is raised by
    `check`, by line, as coalesced, one edge per pair, the least it can be. Three causes:
      * Coalescing. pinctrl-rp1 handles edge IRQs with handle_edge_irq, which leaves the
        line unmasked while its oneshot thread runs, and a hard IRQ that comes before
        the thread has started only re-sets IRQTF_RUNTHREAD (__irq_wake_thread,
        kernel/irq/handle.c). The thread runs once for both: one event, with the second
        IRQ's stamp and number. The first IRQ's number never appears.
      * Latching. rp1_gpio_irq_handler writes IRQRESET once per pin per pass, clearing
        every edge latched on it, so edges closer together than the hard IRQ's latency
        take one IRQ, one number, one event. There is no gap in any number.
      * THE IRQ THREAD RACES, below.

    A GAP IN THE REQUEST'S NUMBERS MAY BE AN EVENT STILL ON ITS WAY. Each read is put in
    number order first, so a swap within one read is nothing. A gap that remains is held
    open, and closes silently if its number arrives. Once final (THE WINDOW), a number
    still missing is EXCUSED if a line explains it: an overflow on that line, a pair at
    one level, or a repeated number, each between the line's two events either side of
    it. The rest are raised by `settle` as unaccounted. Nothing says whose they were, so
    no cause is named: a merge of three edges leaves the levels alternating, an overflow
    shows on its line only when that line next fires, and an event later than the
    window has not arrived yet.

    EXCUSES WAIT FOR THE WINDOW. Each explains so many numbers between two events of
    one line, and is taken only from numbers already final, the excuse whose range ends
    soonest first, lowest number first. So an excuse never takes a number another line
    still has on its way, and the matching leaves the fewest unexplained.

    THE IRQ THREAD RACES. A second hard IRQ on the line while its thread is building the
    first event (edge_irq_thread):
      * after the thread has started but before it reads the stamp: the first event
        carries the second IRQ's stamp and number, and the re-run stamps the second edge
        from the thread, late, with a fresh number;
      * after it reads the stamp but before it clears it: the second stamp is wiped, and
        the re-run likewise stamps from the thread and takes a fresh number;
      * after it clears the stamp but before it reads the number: the first event takes
        the second IRQ's number and the re-run sends that number again.
    In each, the first IRQ's number never appears, and the thread most likely reads the
    level for the first event after the second edge has landed, so the first event
    carries the second edge's level. With the event before it, that is a pair at one
    level, reported as coalesced, which excuses the missing number: two edges became two
    events, but one is at the wrong level, so the record has lost that edge all the same.
    In the first two, the re-run's event reads the same level again and is stamped late,
    a second pair, so those races count two. In the third, the re-run repeats the
    number. Its level is the true one, so it is not compared with the first copy, and
    the repeat excuses the missing number unless the first copy's pair already has:
    one missing number, one excuse. Where the first copy had no event before it to pair
    with (a line's first event, or the first after a gap in its own numbers), the repeat
    is compared with it instead, so that race is not silent.
    A fourth leaves every number in place: the second edge lands before the thread reads
    the level for the first event, but its hard IRQ only after the thread has taken the
    first event's number, inside RP1's hard-IRQ latency. Two events, the right numbers and
    stamps, the first at the wrong level: like the first two races, it counts two, with
    no late stamp.

    THE WINDOW. A missing number was taken before the one that showed the gap, so before
    the read that showed it returned, at `read_at`. If its event exists, the IRQ thread
    buffered it within EDGE_STAMP_LAG_US, the assumption the roll barrier rests on. So
    once the pump's mark (`EdgePump.delivered_through`) reaches `read_at`, it would have
    been delivered. Two consequences:
      * The gap is held across at least one more look at the buffer, because the read
        that showed it leaves the mark before `read_at`.
      * It closes on the next wait that times out (EDGE_WAIT_S, 20 ms), or on the first
        short read begun EDGE_STAMP_LAG_US after `read_at`: 20 to 40 ms on a healthy pump.
    While the pump works through a backlog no read is short and the mark stands still.
    So a gap is also final once KERNEL_EVENT_BUFFER_MAX events have been read without
    it, counting from the first read begun EDGE_STAMP_LAG_US after `read_at`: the event
    was in the buffer by then, and the buffer is a FIFO of at most that many. And when
    capture stops, the pump's last look (`EdgePump.finish`) runs until the window has
    closed on every gap it holds, within STOP_BUDGET_US; whatever is still held then is
    final, because no later look could fill it. No gap is held open forever. All of
    this rests on the roll barrier's assumption: an event the IRQ thread buffers later
    than EDGE_STAMP_LAG_US is reported lost, and is passed on without complaint when it
    does arrive. The stamp-lag witness reports that case too.

    WHAT IT CANNOT TELL.
      * Edges folded into one event in an even number beyond its own: a whole pulse
        latched together with the edge before or after it is three edges, one hard IRQ,
        one number, one event, and the levels still alternate. A whole pulse latched as
        one event shows as a pair at one level, except as a line's first event or the
        first after a gap in its own numbers, where there is no level to compare.
      * How many edges a pair at one level lost: it counts one, the least. And one event
        at the wrong level makes two pairs, so a race can count two for one damaged edge.
      * An overflow on a line that does not fire again until after the window: its
        request number is first reported as unaccounted, and the overflow is reported
        again when the line fires. An overflow needs the pump a whole buffer behind,
        which the stamp-lag witness and the roll barrier report as well.
      * A pair whose excuse explains no missing number -- a latched pair, or the re-run's
        pair in the first two races -- excuses another line's number in its range if
        that one is really lost, and that loss goes uncounted. The re-run's range spans
        microseconds.
      * A stop on a line that keeps some gap open until the last look ends on its
        bound: what is held then is reported, though its event may have been a moment
        from arriving. The bound is STOP_BUDGET_US, or STOP_PASSES passes if they come
        sooner -- reads faster than every 2 ms -- so it takes a new straddle on every
        read for up to 80 ms.

    Numbers are unwrapped to the nearest value, so their 32-bit wrap is not a gap. The
    first event sets the baseline: a request joined mid-stream starts wherever the
    kernel's counter already is.

    Deliberately raises rather than counting. A short edge record cannot be repaired
    afterwards, so the useful moment to hear about it is while the session is running.
    Every raise comes AFTER the whole batch is taken in. A baseline left behind measured
    every later, perfectly consecutive edge against the stale number, so one overflow
    became a new and larger "lost" report on each edge for the rest of the day.

    AND EACH LOSS IS KEPT WHERE IT WAS FOUND, for its L record in the segment
    (wl_sync.log.Loss), beside the raise, which the journal reports unchanged. An
    overflow or a pair is kept by the index, in its batch, of the event that showed it
    (`take_found`); numbers nothing explains, once their window closes (`take_settled`).
    Kept only where the count is at least one for certain, so two of the raised losses
    have none: the second of two pairs in a row on a line, which in the race above is
    the same damaged edge as the first, its window need not hold; and a gap that
    `settle` closes only because capture stopped, whose event may have been a moment
    from arriving.
    """

    def __init__(self) -> None:
        self._top: int | None = None  # the highest request number seen, unwrapped
        self._floor: int | None = None  # nothing at or below it is tracked
        self._gaps: list[_Gap] = []  # the numbers missing below the top, ascending
        self._lines: dict[int, _Line] = {}
        self._excuses: list[list[int]] = []  # [after, before, count], EXCUSES
        self._last_read_at: int | None = None
        self._top_ns: int | None = None  # the stamp of the event numbered _top
        self._found: dict[int, list[_Site]] = {}  # the last batch's, by event index
        self._settled: list[_Site] = []

    def check(self, events: Sequence, read_at: int) -> None:
        """Take in one read's batch, in the order the buffer gave it. `read_at` is the
        clock when the read returned, a raw now_us() reading. Raises
        EdgeBufferOverflowError or CoalescedEdgeError, or both as DroppedEdgeError, for
        what a line's own numbers or levels show, and keeps each loss for `take_found`
        by the index of the event that showed it."""
        self._found = {}
        if not events:
            return
        if self._top is None:
            first = events[0].global_seqno
            self._top = min(_nearest(e.global_seqno, first) for e in events) - 1
            self._floor = self._top
        numbers = [_nearest(event.global_seqno, self._top) for event in events]

        overflowed: dict[int, int] = {}
        coalesced: dict[int, int] = {}
        fresh = []
        stamps: dict[int, int] = {}  # number -> stamp, for the fresh ones
        for index, (event, number) in enumerate(zip(events, numbers)):
            gpio = event.line_offset
            rising = event.event_type is event.Type.RISING_EDGE
            stamp = event.timestamp_ns
            line = self._lines.get(gpio)
            if line is None:
                self._lines[gpio] = _Line(
                    event.line_seqno, number, self._floor, rising, stamp
                )
                fresh.append(number)
                stamps[number] = stamp
                continue
            seqno = _nearest(event.line_seqno, line.seqno)
            step = seqno - line.seqno
            if step <= 0:  # a line's events come in its order; nothing to learn
                fresh.append(number)
                stamps[number] = stamp
                continue
            compared = paired = False
            if step == 1 and number == line.number:  # THE IRQ THREAD RACES: the re-run
                if not (line.paired and rising == line.rising):
                    # Unless the first copy's pair is the race's own: the repeat at the
                    # same level says the first copy carried the second edge's level,
                    # and that pair already explains the missing number.
                    self._excuses.append([line.before, number, 1])
                if not line.compared and rising == line.rising:
                    coalesced[gpio] = coalesced.get(gpio, 0) + 1
                    self._find(index, "coalesced", gpio, 1, "at_least", line.stamp, stamp)
                compared = True
            else:
                fresh.append(number)
                stamps[number] = stamp
                if step > 1:  # levels across lost events say nothing
                    lost = step - 1
                    overflowed[gpio] = overflowed.get(gpio, 0) + lost
                    self._excuses.append([line.number, number, lost])
                    self._find(index, "buffer_overflow", gpio, lost, "exact", line.stamp, stamp)
                else:
                    compared, paired = True, rising == line.rising
                    if paired:
                        coalesced[gpio] = coalesced.get(gpio, 0) + 1
                        self._excuses.append([line.number, number, 1])
                        if not line.paired:  # the second of two in a row: no record
                            self._find(
                                index, "coalesced", gpio, 1, "at_least", line.stamp, stamp
                            )
            line.seqno, line.before, line.number = seqno, line.number, number
            line.rising, line.compared, line.paired = rising, compared, paired
            line.stamp = stamp

        seen = _Sighting(read_at)
        for number in sorted(fresh):
            if number > self._top:
                if number > self._top + 1:
                    around = _Around(self._top_ns, stamps[number])
                    self._gaps.append(_Gap(self._top + 1, number - 1, seen, around))
                self._top, self._top_ns = number, stamps[number]
            else:
                self._take(number)  # it arrived after a later one

        if self._last_read_at is not None:  # this read began after the last returned
            for sighting in {gap.seen for gap in self._gaps}:
                if _since(self._last_read_at, sighting.read_at) >= EDGE_STAMP_LAG_US:
                    sighting.read_after += len(events)
        self._last_read_at = read_at
        if overflowed or coalesced:
            raise _dropped(overflowed, coalesced)

    @property
    def holding(self) -> bool:
        """Whether any gap is held open, waiting on its window."""
        return bool(self._gaps)

    def first_open_shown_ns(self) -> int | None:
        """The stamp of the earliest event that showed a gap still held open, or None.
        The pump holds its mark below it until the gap settles (EdgePump.pump_once)."""
        return min((gap.around.above_ns for gap in self._gaps), default=None)

    def take_found(self) -> dict[int, list[_Site]]:
        """The losses the last `check` found, by the index in its batch of the event
        that showed each, and forgets them."""
        found, self._found = self._found, {}
        return found

    def take_settled(self) -> list[_Site]:
        """The unaccounted losses `settle` closed on their window, and forgets them."""
        settled, self._settled = self._settled, []
        return settled

    def settle(self, through: int | None, final: bool = False) -> None:
        """Close the gaps now final (THE WINDOW). `through` is the pump's mark if this
        step moved it, else None; `final`, when capture stops, closes every gap. Raises
        UnaccountedEdgeError for the numbers no excuse explains, and keeps those whose
        window closed for `take_settled`, one site per gap as it was first seen. A gap
        `final` alone closes is raised and not kept: its event may have been a moment
        from arriving."""
        done, still_open = [], []
        for gap in self._gaps:
            due = final or self._window_closed(gap, through)
            (done if due else still_open).append(gap)
        self._gaps = still_open
        done = self._excuse(done)
        self._excuses = [  # an excuse with no open number in its range has none to come
            excuse for excuse in self._excuses
            if excuse[2] and any(
                gap.first < excuse[1] and gap.last > excuse[0] for gap in self._gaps
            )
        ]
        sure: dict[_Around, int] = {}  # numbers lost, by the gap as first seen
        for gap in done:
            if self._window_closed(gap, through):
                sure[gap.around] = sure.get(gap.around, 0) + gap.last - gap.first + 1
        for around, lost in sure.items():
            below, above = around.below_ns, around.above_ns
            self._settled.append(_Site("unaccounted", None, lost, "at_least", below, above))
        unaccounted = sum(gap.last - gap.first + 1 for gap in done)
        if unaccounted:
            raise UnaccountedEdgeError(unaccounted=unaccounted)

    @staticmethod
    def _window_closed(gap: _Gap, through: int | None) -> bool:
        return gap.seen.read_after >= KERNEL_EVENT_BUFFER_MAX or (
            through is not None and _since(through, gap.seen.read_at) >= 0
        )

    def _find(self, index: int, *site) -> None:
        """Keep a loss found at the batch's event `index`: _Site's fields, in order."""
        self._found.setdefault(index, []).append(_Site(*site))

    def _excuse(self, gaps: list[_Gap]) -> list[_Gap]:
        """These final gaps less what the excuses explain (EXCUSES)."""
        for excuse in sorted(self._excuses, key=lambda excuse: excuse[1]):
            after, before, count = excuse
            kept = []
            for gap in gaps:
                first = max(gap.first, after + 1)
                last = min(gap.last, before - 1, first + count - 1)
                if first > last:
                    kept.append(gap)
                    continue
                count -= last - first + 1
                if gap.first < first:
                    kept.append(_Gap(gap.first, first - 1, gap.seen, gap.around))
                if last < gap.last:
                    kept.append(_Gap(last + 1, gap.last, gap.seen, gap.around))
            excuse[2], gaps = count, kept
        return gaps

    def _take(self, number: int) -> None:
        """No longer missing: it arrived."""
        kept = []
        for gap in self._gaps:
            if not gap.first <= number <= gap.last:
                kept.append(gap)
                continue
            if gap.first < number:
                kept.append(_Gap(gap.first, number - 1, gap.seen, gap.around))
            if number < gap.last:
                kept.append(_Gap(number + 1, gap.last, gap.seen, gap.around))
        self._gaps = kept


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

      * A DROPPED EDGE is a data-quality event. The kernel's buffer overflowed, or it
        merged or latched two edges into one event, and a few edges are gone; everything
        after them is still good. Stopping would trade a small detectable gap for the
        rest of the day's edges, so capture CONTINUES.
      * A FAILING CONSUMER means records are not being kept at all. Continuing would
        spin, delivering into something that cannot accept, so the pump stops.

    `delivered_through` is how far it has delivered, as a raw `now_us()` reading (see
    `pump_once`), for the run loop's roll barrier. None until the first wait or read
    that can say. `stamp_lag_max_us` and `stamp_lag_over` are the witness for the one
    assumption that rule rests on (also `pump_once`).

    EACH LOSS GOES TO `on_loss` TOO, as a CaptureLoss, for its L record in the segment
    (wl_sync.log.Loss), from this thread, so it keeps its place among the edges: an
    overflow or a pair just before the event that showed it, an unaccounted number once
    its window closes. The held error, and so the journal, is unchanged. A consumer
    that raises on a loss is a failing consumer, as on an edge, and stops the pump. The
    Recorder raises there only when its sink fails: a report it cannot make into a
    record it drops, and capture goes on (wl_sync.service.Recorder._on_loss).

    THE HELD ERROR CROSSES THREADS. The pump holds it and check_health() takes it, from
    the run loop's thread, through `take_error`. Adding a loss to one already held is a
    read and a store, and a take landing between them reported the first loss twice, so
    both sides take one lock. Only bookkeeping happens under it: no I/O, nothing that
    waits.
    """

    def __init__(self, on_edge, origin_ns: int, on_loss=None) -> None:
        self._on_edge = on_edge
        self._on_loss = on_loss
        self._origin_ns = origin_ns
        self._seqnos = SeqnoTracker()
        self._held = threading.Lock()
        self._waited_out = False
        self._drained = False  # the last pass left the kernel's buffer empty
        self._through: int | None = None  # the mark THE RULE gives, before any hold
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

        THE SAME MARK CLOSES A GAP in the edge numbers (SeqnoTracker, THE WINDOW): a
        missing number is lost once the mark reaches the moment its gap was seen. So the
        tracker cannot call an edge lost that the mark still allows to be on its way.

        AND AN OPEN GAP HOLDS THE MARK, because its L record is a record the roll waits
        for too. The record is written only once the gap's window closes, while edges
        stamped after the gap's own can already have been delivered, and the mark with
        them. So while a gap is open the mark published stays below the event that
        showed it, and moves on only after the gap is settled and its record written.
        An unaccounted record whose window ends at or before a roll's boundary therefore
        lands in the closing segment with the edges around it. A gap that fills, as a
        swap across two reads does at the next read, holds nothing for longer.
        """
        began = now_us()
        self._waited_out = not request.wait_edge_events(timeout=EDGE_WAIT_S)
        self._drained = self._waited_out
        if self._waited_out:
            self._through = (began - 1) % TICK_WRAP_US
            going = self._settle(self._through)
            self._publish()
            return going
        events = request.read_edge_events(max_events=EDGE_READ_MAX)
        read_at = now_us()
        self._witness(events, read_at)
        if not self.handle(events, read_at):
            return False
        self._drained = len(events) < EDGE_READ_MAX
        if len(events) < EDGE_READ_MAX:
            self._through = (began - EDGE_STAMP_LAG_US - 1) % TICK_WRAP_US
            going = self._settle(self._through)
        else:
            going = self._settle(None)
        self._publish()
        return going

    def _publish(self) -> None:
        """Publish the mark THE RULE gives, held below the event that showed the
        earliest gap still open, if that is earlier."""
        mark = self._through
        shown = self._seqnos.first_open_shown_ns()
        if mark is not None and shown is not None:
            held = (edge_tick_us(shown, self._origin_ns) - 1) % TICK_WRAP_US
            if _since(mark, held) > 0:
                mark = held
        self.delivered_through = mark

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

    def handle(self, events, read_at: int) -> bool:
        """Deliver a batch, `read_at` being the clock when its read returned. Returns
        False when the pump should stop.

        The whole batch's numbers are checked first (SeqnoTracker.check). Delivery keeps
        the buffer's order, which already holds each line's edges in order. The mark
        moves only after the whole batch, so which ticks have been delivered does not
        depend on their order within it. A loss the check found at an event goes to
        `on_loss` just before that event goes to `on_edge`."""
        try:
            self._seqnos.check(events, read_at)
        except DroppedEdgeError as dropped:
            self._hold(dropped)  # recorded, and we keep going
        found = self._seqnos.take_found()
        for index, event in enumerate(events):
            try:
                for site in found.get(index, ()):
                    self._report(site, ahead=True)
                rising = event.event_type is event.Type.RISING_EDGE
                self._on_edge(
                    event.line_offset,
                    1 if rising else 0,
                    edge_tick_us(event.timestamp_ns, self._origin_ns),
                )
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.fail(error)
                return False
        return True

    def finish(self, request, now_us: Callable[[], int]) -> None:
        """The last look, once capture is told to stop: deliver what the kernel still
        holds, and report every gap still held. Without it, a loss seen in the run's
        last 20 to 40 ms was held past the end and never reported, and edges still in
        the kernel's buffer were released unread.

        IT ENDS ON THE WINDOW, NOT ON A COUNT, and only once the buffer is empty. It
        pumps until a pass has emptied the buffer (a read shorter than EDGE_READ_MAX)
        with no gap held, that is with the mark past the moment every gap it held was
        seen: a gap that never fills is reported then, in the normal way, and one that
        was an event on its way is filled. A wait that times out ends it too: the buffer
        stayed empty for EDGE_WAIT_S, and nothing held can still be coming. Ending after
        a fixed number of passes forced the last read's gaps final, and with the cameras
        faster than EDGE_WAIT_S, when no wait times out, a frame that read caught out of
        order was reported lost at the end of a healthy run. Ending on a full read with
        nothing held released the rest of the buffer unread, with nothing to say so.

        BOUNDED, whatever the kernel does. It ends once STOP_BUDGET_US has passed on the
        clock, checked after each pass, or after STOP_PASSES passes. A pass is at most
        one wait of EDGE_WAIT_S and one read of EDGE_READ_MAX events. So the worst case:
          * on a clock that moves, STOP_BUDGET_US plus one pass, about 0.1 s;
          * on a clock that stands still, where no window closes and the budget never
            runs out, STOP_PASSES waits with traffic arriving through them, 0.8 s.
        Both fit inside the second close() gives the edge thread. Whatever is held then
        is final, because no later look will come: the journal reports it, and no L
        record is written for it, since its event may have been a moment from arriving."""
        began = now_us()
        for _ in range(STOP_PASSES):
            if not self.pump_once(request, now_us):
                return
            if (
                self._waited_out
                or (self._drained and not self._seqnos.holding)
                or _since(now_us(), began) >= STOP_BUDGET_US
            ):
                break
        self._settle(None, final=True)

    def take_error(self) -> BaseException | None:
        """The error held, cleared: what check_health() reports. A loss added while it
        is taken is reported once, neither twice nor never."""
        with self._held:
            error, self.error = self.error, None
        return error

    def fail(self, error: BaseException) -> None:
        """Hold what stopped the pump, over any loss already held: the stop matters more."""
        with self._held:
            self.error = error

    def abandon(self, error: BaseException) -> DroppedEdgeError | None:
        """The pump stopped on `error`, with no last look. Hold the error, as fail()
        does, and make every gap still held final. A gap held for the window would
        otherwise go nowhere, because fail() keeps the stop over any loss. Each loss
        goes to `on_loss` too, for its L record, as far as `on_loss` lets it. Returns
        the loss, for the caller to report."""
        self.fail(error)
        lost = None
        try:
            self._seqnos.settle(None, True)
        except DroppedEdgeError as dropped:
            lost = dropped
        for site in self._seqnos.take_settled():
            try:
                self._report(site, ahead=False)
            except BaseException:  # noqa: BLE001 - the stop is held already
                break
        return lost

    def _settle(self, through: int | None, final: bool = False) -> bool:
        """Settle the tracker's gaps, and report each loss now final. Returns False when
        the pump should stop: `on_loss` failed."""
        try:
            self._seqnos.settle(through, final)
        except DroppedEdgeError as dropped:
            self._hold(dropped)
        for site in self._seqnos.take_settled():
            try:
                self._report(site, ahead=False)
            except BaseException as error:  # noqa: BLE001 - surfaced by check_health
                self.fail(error)
                return False
        return True

    def _report(self, site: _Site, ahead: bool) -> None:
        """One loss to `on_loss`, its stamps made ticks on the shared 32-bit timebase.
        The event after it is recent and goes raw; the one before it can be any age, so
        it goes as an exact distance back (CaptureLoss)."""
        if self._on_loss is None:
            return
        span = None
        if site.before_ns is not None:
            span = max(
                0,
                (site.after_ns - self._origin_ns) // 1_000
                - (site.before_ns - self._origin_ns) // 1_000,
            )
        self._on_loss(
            CaptureLoss(
                cause=site.cause,
                count=site.count,
                bound=site.bound,
                gpio=site.gpio,
                after=edge_tick_us(site.after_ns, self._origin_ns),
                span_us=span,
                ahead=ahead,
            )
        )

    def _hold(self, dropped: DroppedEdgeError) -> None:
        """Hold a loss for check_health(), added to any loss already held, so a check
        that finds both kinds reports both. A held failure that stopped the pump is
        never overwritten. The slot is read once: a second read could find it taken."""
        with self._held:
            held = self.error
            if held is None:
                self.error = dropped
            elif isinstance(held, DroppedEdgeError):
                self.error = held.combine(dropped)
