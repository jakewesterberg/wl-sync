"""The parts of GPIO handling that are arithmetic rather than hardware."""

import pytest

from wl_sync.rp1.gpio import (
    DroppedEdgeError,
    SeqnoTracker,
    edge_tick_us,
    pulse_deadlines,
)


def test_deadlines_are_absolute_so_error_cannot_accumulate():
    """THE REASON THIS IS NOT A LOOP OF SLEEPS.

    A barcode frame is 200 ms of 5 ms slots, and the decoder samples each bit at its
    slot CENTRE measured from the lead edge -- so it assumes uniform slots. Sleeping
    per pulse makes every overshoot permanent: 100 us of oversleep on each of 36 pulses
    is 3.6 ms by the end, past the +/-2.5 ms a slot centre allows, and the tail of the
    word decodes as garbage. Absolute deadlines let a late pulse be corrected by the
    next one instead of compounding.
    """
    pulses = [(1, 10_000), (0, 10_000)] + [(1, 5_000)] * 32
    deadlines = pulse_deadlines(pulses, start_ns=1_000_000_000)

    assert [level for level, _ in deadlines] == [level for level, _ in pulses]
    # Each deadline is the sum of every duration before it, not a running estimate.
    assert deadlines[0][1] == 1_000_000_000
    assert deadlines[1][1] == 1_000_000_000 + 10_000_000
    assert deadlines[-1][1] == 1_000_000_000 + (20_000 + 31 * 5_000) * 1_000


def test_deadlines_of_an_empty_frame():
    assert pulse_deadlines([], start_ns=0) == []


def test_an_edge_timestamp_lands_on_the_shared_timebase():
    """The kernel stamps edges on CLOCK_MONOTONIC, which is the clock now_us() counts
    from -- so an edge needs no correction for when this process happened to wake up.
    That is the whole reason to prefer libgpiod's timestamp over reading a clock in
    the callback."""
    assert edge_tick_us(timestamp_ns=1_500_000_000, origin_ns=1_000_000_000) == 500_000


def test_an_edge_tick_wraps_like_the_hardware_counter():
    """Ticks are 32-bit everywhere in this package, and Recorder's shared clock
    expects exactly that -- so this must wrap rather than grow without bound."""
    origin = 0
    assert edge_tick_us(timestamp_ns=(1 << 32) * 1_000, origin_ns=origin) == 0
    assert edge_tick_us(timestamp_ns=((1 << 32) + 7) * 1_000, origin_ns=origin) == 7


def test_an_edge_before_the_origin_is_refused():
    """A negative tick would sort before every record in the segment and read as the
    oldest thing in the day. Better to refuse it than to file it."""
    with pytest.raises(ValueError):
        edge_tick_us(timestamp_ns=999, origin_ns=1_000_000)


class _FakeEvent:
    """A libgpiod EdgeEvent. `line_seqno` counts this line's events and `seqno` (the
    event's `global_seqno`) the whole request's; for events all on one line, as most
    tests here use, the two are the same, which is the default."""

    class Type:
        RISING_EDGE = "rising"
        FALLING_EDGE = "falling"

    def __init__(self, seqno, offset, rising, timestamp_ns, line_seqno=None):
        self.global_seqno = seqno
        self.line_seqno = seqno if line_seqno is None else line_seqno
        self.line_offset = offset
        self.event_type = self.Type.RISING_EDGE if rising else self.Type.FALLING_EDGE
        self.timestamp_ns = timestamp_ns


def _on_one_line(*seqnos, gpio=20):
    """Events on one line, so each is numbered alike on the line and in the request."""
    return [_FakeEvent(s, gpio, s % 2 == 1, 1_000 * s) for s in seqnos]


def test_consecutive_sequence_numbers_pass():
    tracker = SeqnoTracker()
    tracker.check(_on_one_line(1, 2, 3, 4), read_at=1_000)
    tracker.settle(through=2_000)


def test_a_gap_in_the_sequence_numbers_is_raised():
    """libgpiod numbers every edge event, so a KERNEL-side drop is detectable rather
    than inferred. The kernel's event buffer overflows silently under a fast line, and
    a silently short edge record is a trial that looks like it never happened."""
    tracker = SeqnoTracker()
    tracker.check(_on_one_line(1, 2), read_at=1_000)
    with pytest.raises(DroppedEdgeError, match="3"):
        tracker.check(_on_one_line(6), read_at=2_000)


def test_the_first_event_sets_the_baseline_whatever_its_number():
    """A request joined mid-stream starts wherever the kernel's counter already is."""
    tracker = SeqnoTracker()
    tracker.check(_on_one_line(9_000), read_at=1_000)
    tracker.check(_on_one_line(9_001), read_at=2_000)
    tracker.settle(through=3_000)


def test_events_are_delivered_on_the_shared_timebase():
    from wl_sync.rp1.gpio import EdgePump

    seen = []
    pump = EdgePump(on_edge=lambda g, l, t: seen.append((g, l, t)), origin_ns=1_000)
    pump.handle([_FakeEvent(1, 26, True, 1_000 + 5_000_000)], read_at=6_000)
    assert seen == [(26, 1, 5_000)]


def test_a_dropped_edge_is_recorded_but_capture_CONTINUES():
    """A kernel buffer overflow is a data-quality event, not a reason to stop.

    Raising out of the pump thread would kill it, so a handful of lost edges would cost
    the REST OF THE DAY's edges as well -- trading a small, detectable gap for a large,
    silent one. The drop is held for `check_health()` to surface while the box keeps
    recording everything after it.
    """
    from wl_sync.rp1.gpio import DroppedEdgeError, EdgePump

    seen = []
    pump = EdgePump(on_edge=lambda g, l, t: seen.append(g), origin_ns=0)
    pump.handle([_FakeEvent(1, 20, True, 0)], read_at=1)
    pump.handle([_FakeEvent(9, 20, False, 1_000)], read_at=2)   # GPIO20's 2..8 lost
    pump.handle([_FakeEvent(10, 22, True, 2_000, line_seqno=1)], read_at=3)  # must arrive

    assert seen == [20, 20, 22], "capture stopped after a drop"
    assert isinstance(pump.error, DroppedEdgeError)


def test_a_failing_consumer_stops_the_pump_and_is_held():
    """Unlike a drop, a sink that raises means records are NOT being kept. Continuing
    would spin, delivering into something that cannot accept -- so it stops, and says
    so, instead of dying quietly and letting the segment simply end."""
    from wl_sync.rp1.gpio import EdgePump

    def explode(gpio, level, tick):
        raise OSError("the sink is gone")

    pump = EdgePump(on_edge=explode, origin_ns=0)
    assert pump.handle([_FakeEvent(1, 20, True, 0)], read_at=1) is False
    assert isinstance(pump.error, OSError)


def test_a_healthy_pump_reports_no_error():
    from wl_sync.rp1.gpio import EdgePump

    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert pump.handle([_FakeEvent(1, 20, True, 0)], read_at=1) is True
    assert pump.error is None


def test_one_overflow_is_one_report_not_one_per_edge_after_it():
    """The tracker raised BEFORE it moved its baseline, so after one gap every later,
    perfectly consecutive edge was measured against the stale number and reported a
    fresh, growing loss: one 7-event overflow became an ERROR line every second for
    the rest of the day, each claiming more edges lost than the last.

    Nor is the overflow reported twice. The request-wide numbers 2..8 are missing too,
    and once the line's own numbers have said why, they are not a second loss."""
    tracker = SeqnoTracker()
    tracker.check(_on_one_line(1), read_at=1_000)
    with pytest.raises(DroppedEdgeError):
        tracker.check(_on_one_line(9), read_at=2_000)
    tracker.check(_on_one_line(10), read_at=3_000)  # consecutive with 9: nothing lost
    tracker.check(_on_one_line(11), read_at=4_000)
    tracker.settle(through=5_000)


# How far the edge pump has delivered (`EdgePump.delivered_through`), which the run loop's
# roll barrier waits on (wl_sync.cli._await_delivery). The rule rests on two kernel facts,
# drivers/gpio/gpiolib-cdev.c: an edge is STAMPED in the hard IRQ handler and only PUT IN
# THE BUFFER by the IRQ thread afterwards; and a read returns early only when the buffer
# is empty. libgpiod's wait_edge_events is select() on the request's fd, readable while
# the buffer holds anything.


class _Request:
    """A libgpiod LineRequest, as EdgePump uses it, scripted step by step: None is a wait
    that times out, and a list is a wait that finds events followed by the read that
    returns that list. Once the script runs out, every wait times out: nothing more comes.
    `now_us` is the box clock, moved only by the test."""

    def __init__(self, script, now_us=50_000):
        self.script = list(script)
        self.now = now_us
        self.waited = []
        self.asked = []

    def now_us(self):
        return self.now

    def wait_edge_events(self, timeout):
        self.waited.append(timeout)
        if not self.script or self.script[0] is None:
            self.script[:1] = []
            return False
        return True

    def read_edge_events(self, max_events=None):
        self.asked.append(max_events)
        return self.script.pop(0)


def _edge(seqno, stamp_us):
    """An edge on GPIO20, rising and falling in turn, as a line's edges do."""
    return _FakeEvent(seqno, 20, seqno % 2 == 1, stamp_us * 1_000)


def test_a_wait_that_times_out_moves_the_mark_to_just_before_it_began():
    """The buffer stayed empty for the whole wait, so every edge stamped before the wait
    began was already read and delivered -- provided the IRQ thread had put it in the
    buffer within the wait, which is the one assumption (EDGE_STAMP_LAG_US <= the wait)."""
    from wl_sync.rp1.gpio import EDGE_WAIT_S, EdgePump

    request = _Request([None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert pump.delivered_through is None
    assert pump.pump_once(request, request.now_us) is True
    assert request.waited == [EDGE_WAIT_S]
    assert pump.delivered_through == 49_999


def test_a_read_that_empties_the_buffer_moves_the_mark_back_by_the_kernels_lag():
    from wl_sync.rp1.gpio import EDGE_READ_MAX, EDGE_STAMP_LAG_US, EdgePump

    seen = []
    request = _Request([[_edge(1, 40_000)]])
    pump = EdgePump(on_edge=lambda g, l, t: seen.append(t), origin_ns=0)
    assert pump.pump_once(request, request.now_us) is True
    assert seen == [40_000]
    assert request.asked == [EDGE_READ_MAX]
    assert pump.delivered_through == 50_000 - EDGE_STAMP_LAG_US - 1


def test_a_read_that_fills_its_maximum_does_not_move_the_mark():
    """A read stops at its maximum with the buffer possibly still holding edges stamped
    long before the wait began, so it says nothing about them."""
    from wl_sync.rp1.gpio import EDGE_READ_MAX, EdgePump

    full = [_edge(n + 1, 1_000 + n) for n in range(EDGE_READ_MAX)]
    request = _Request([full])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert pump.pump_once(request, request.now_us) is True
    assert pump.delivered_through is None


def test_a_failing_consumer_does_not_move_the_mark():
    from wl_sync.rp1.gpio import EdgePump

    def explode(gpio, level, tick):
        raise OSError("the sink is gone")

    request = _Request([[_edge(1, 40_000)]])
    pump = EdgePump(on_edge=explode, origin_ns=0)
    assert pump.pump_once(request, request.now_us) is False
    assert pump.delivered_through is None


def test_the_mark_never_passes_an_edge_still_on_its_way_to_the_buffer():
    """The hazard in "the clock read before a wait whose events were all delivered".

    The kernel stamps an edge in its hard IRQ handler and its IRQ thread puts it in the
    buffer afterwards. So when the wait begins at 50 000 us, an edge stamped at 49 990 can
    still be on its way, while an edge stamped earlier is already waiting. The wait returns
    at once, the read takes the one waiting and empties the buffer, and a mark of 49 999
    would claim the edge in flight. Moving the mark back by the IRQ thread's allowance
    keeps it behind that edge."""
    from wl_sync.rp1.gpio import EdgePump

    seen = []
    request = _Request([[_edge(1, 49_000)]])
    pump = EdgePump(on_edge=lambda g, l, t: seen.append(t), origin_ns=0)
    pump.pump_once(request, request.now_us)
    assert seen == [49_000]
    assert pump.delivered_through < 49_990


class _Kernel:
    """A line request's event buffer on a clock it advances itself.

    Each edge is (stamp_us, arrives_us): stamped by the hard IRQ handler at `stamp_us`, put
    in the buffer by the IRQ thread at `arrives_us`. A wait returns at the first arrival
    within its timeout, or at the timeout; a read takes up to `max_events` and costs 5 us."""

    def __init__(self, edges, now_us):
        self.now = now_us
        self.on_the_way = sorted(edges, key=lambda edge: edge[1])
        self.buffer = []
        self.seqno = 0

    def now_us(self):
        return self.now

    def _arrive(self):
        while self.on_the_way and self.on_the_way[0][1] <= self.now:
            stamp, _ = self.on_the_way.pop(0)
            self.seqno += 1
            self.buffer.append(_edge(self.seqno, stamp))

    def wait_edge_events(self, timeout):
        self._arrive()
        if self.buffer:
            return True
        deadline = self.now + round(timeout * 1e6)
        if self.on_the_way and self.on_the_way[0][1] <= deadline:
            self.now = self.on_the_way[0][1]
            self._arrive()
            return True
        self.now = deadline
        return False

    def read_edge_events(self, max_events=None):
        self._arrive()
        taken, self.buffer = self.buffer[:max_events], self.buffer[max_events:]
        self.now += 5
        return taken


def test_the_mark_never_passes_an_undelivered_edge_whatever_the_kernel_does():
    """Bursts, single edges and silences, each edge reaching the buffer anywhere up to
    EDGE_STAMP_LAG_US after its stamp. After every step of the pump the mark is behind
    every edge not yet delivered -- and it still catches up once the line goes quiet."""
    import random

    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US, EdgePump

    chance = random.Random(20261030)
    edges, stamp = [], 1_000_000
    for _ in range(400):
        stamp += chance.choice([1, 50, 2_000, 15_000, 40_000, 300_000])
        for _ in range(chance.choice([1, 1, 1, 3, 90])):  # 90 overflows one read
            stamp += chance.randint(1, 20)
            edges.append((stamp, stamp + chance.randint(0, EDGE_STAMP_LAG_US)))
    last = stamp

    kernel = _Kernel(edges, now_us=500_000)  # clear of 0, so no mark wraps below it
    delivered = set()
    pump = EdgePump(on_edge=lambda g, l, t: delivered.add(t), origin_ns=0)
    stamps, oldest = [s for s, _ in edges], 0  # stamps ascend; oldest undelivered
    steps = 0
    while kernel.on_the_way or kernel.buffer:
        assert pump.pump_once(kernel, kernel.now_us)
        steps += 1
        while oldest < len(stamps) and stamps[oldest] in delivered:
            oldest += 1
        mark = pump.delivered_through
        if mark is not None and oldest < len(stamps):
            assert mark < stamps[oldest], (steps, mark, stamps[oldest])
    for _ in range(3):
        pump.pump_once(kernel, kernel.now_us)
    assert pump.delivered_through >= last
    # The witness bounds the kernel's lag from above, so never falls short of it.
    assert pump.stamp_lag_max_us >= max(arrives - stamp for stamp, arrives in edges)


def test_each_read_bounds_the_kernels_stamp_to_buffer_lag_from_above():
    """The witness for the one assumption the edge mark rests on. When a read returns,
    every edge in it is in the buffer, so the clock then, less an edge's stamp, is at
    least how long the kernel took to buffer it. The pump keeps the largest, and counts
    the edges whose bound exceeded EDGE_STAMP_LAG_US: a quiet maximum confirms the
    assumption on the box, and a loud one is when a roll could misfile an edge."""
    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US, EdgePump

    request = _Request([[_edge(1, 40_000)], [_edge(2, 20_000), _edge(3, 45_000)], None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert (pump.stamp_lag_max_us, pump.stamp_lag_over) == (None, 0)

    pump.pump_once(request, request.now_us)  # read at 50 000 us
    assert (pump.stamp_lag_max_us, pump.stamp_lag_over) == (10_000, 0)
    pump.pump_once(request, request.now_us)
    assert 50_000 - 20_000 > EDGE_STAMP_LAG_US
    assert (pump.stamp_lag_max_us, pump.stamp_lag_over) == (30_000, 1)
    pump.pump_once(request, request.now_us)  # a wait that times out reads nothing
    assert (pump.stamp_lag_max_us, pump.stamp_lag_over) == (30_000, 1)


# Edges that arrive out of order, edges that are lost, and how the pump tells them apart.
# drivers/gpio/gpiolib-cdev.c (rpi-6.12.y): for a request of several lines, each edge's
# hard IRQ handler takes the request-wide number (edge_irq_handler, atomic_inc_return on
# lr->seqno), and that line's own IRQ thread later counts it on the line and puts it in
# the buffer (edge_irq_thread: line->line_seqno++, then linereq_put_event). Two lines'
# threads are not ordered, so the buffer can hold n+1 before n; one line's events reach
# it in that line's order. GPIO26 and GPIO27, the cameras' frame-time lines, fire
# together on every frame.


def _ev(seqno, gpio, line_seqno, rising, stamp_us=40_000):
    return _FakeEvent(seqno, gpio, rising, stamp_us * 1_000, line_seqno=line_seqno)


# Two cameras exposing together: the request's 1..7 alternate between GPIO26 and GPIO27,
# each line counting its own events and alternating rising and falling.
_FRAMES = {
    1: _ev(1, 26, 1, True), 2: _ev(2, 27, 1, True), 3: _ev(3, 26, 2, False),
    4: _ev(4, 27, 2, False), 5: _ev(5, 26, 3, True), 6: _ev(6, 27, 3, True),
    7: _ev(7, 26, 4, False),
}


def _frames(*seqnos):
    return [_FRAMES[s] for s in seqnos]


def _pump_through(pump, request, steps):
    """Each step moves the clock to its time and runs the pump once."""
    for now in steps:
        request.now = now
        pump.pump_once(request, request.now_us)


def test_two_lines_out_of_order_within_one_read_are_not_a_drop():
    """GPIO27's IRQ thread put 5 in the buffer before GPIO26's put 4. Nothing was lost,
    and the tracker used to report three losses for it, one of them negative: "1 lost
    between 3 and 5", "-2 lost between 5 and 4", "1 lost between 4 and 6"."""
    from wl_sync.rp1.gpio import EdgePump

    seen = []
    request = _Request([_frames(1, 2, 3, 5, 4, 6, 7), None])
    pump = EdgePump(on_edge=lambda g, l, t: seen.append(g), origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])  # the read; a wait that times out
    assert pump.error is None
    assert len(seen) == 7


def test_a_swap_that_straddles_two_reads_is_not_a_drop():
    """5 is read before 4 has reached the buffer at all. The gap stays open across the
    next read, which brings 4, and closes without a word."""
    from wl_sync.rp1.gpio import EdgePump

    request = _Request([_frames(1, 2, 3, 5), _frames(4, 6, 7), None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 50_100, 80_000])
    assert pump.error is None


def _unknown_line(error):
    """The report for numbers nothing explains names no cause it cannot know."""
    text = str(error)
    return text.startswith(f"{error.lost} edge event(s) lost on an unknown line") and all(
        possibility in text
        for possibility in (
            "coalesced interrupts",
            "overflow on a line that has not fired since",
            "later than the window",
        )
    )


def test_a_gap_that_never_fills_is_reported_once_after_the_window():
    """Number 4 never comes. It is held while it still could: through the read that
    follows, which empties the buffer only 10 ms after the gap was seen, inside the IRQ
    thread's allowance. It is reported once the pump's mark passes the moment the gap
    was seen -- here a wait that times out -- with a positive count, and only once. No
    line's numbers or levels say whose it was, so the report names no cause: it lists
    what could have done it."""
    from wl_sync.rp1 import gpio

    # GPIO27 counts on from 1 to 2 and its levels alternate: nothing on it says it lost
    # anything, so number 4 is a numbered interrupt that never arrived.
    events = [
        _ev(1, 26, 1, True), _ev(2, 27, 1, True), _ev(3, 26, 2, False),
        _ev(5, 26, 3, True), _ev(6, 27, 2, False),
    ]
    request = _Request([events, [_ev(7, 26, 4, False, stamp_us=55_000)], None, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)

    _pump_through(pump, request, [50_000])
    assert pump.error is None, "reported before the next read could bring it"
    _pump_through(pump, request, [60_000])  # a short read; the mark is 39 999
    assert pump.error is None, "reported while the IRQ thread could still deliver it"
    _pump_through(pump, request, [70_001])  # a wait that times out; the mark is 70 000
    error = pump.take_error()
    assert isinstance(error, gpio.UnaccountedEdgeError)
    assert error.lost == 1 and error.unaccounted == 1
    assert _unknown_line(error), str(error)
    _pump_through(pump, request, [100_000])
    assert pump.error is None, "one gap, reported twice"


def test_a_gap_in_one_lines_own_numbers_is_an_overflow():
    """One line's events reach the buffer in that line's order, so a gap in its own
    numbers is final the moment it is seen: the buffer was full, and kfifo_skip threw
    the oldest events away (linereq_put_event). It is reported at once, by line. The
    request-wide numbers the lost events carried are not reported a second time."""
    from wl_sync.rp1 import gpio

    request = _Request([_on_one_line(1, 2, 5), None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000])
    error = pump.take_error()
    assert "2 edge event(s) lost (kernel buffer overflow on GPIO20" in str(error)
    assert isinstance(error, gpio.EdgeBufferOverflowError)
    assert error.overflowed == {20: 2} and error.lost == 2
    _pump_through(pump, request, [80_000])  # the window closes on numbers 3 and 4
    assert pump.error is None


def test_two_events_at_the_same_level_on_one_line_are_a_coalesced_loss():
    """GPIO20 fell twice in a row, and nothing in the numbers says so. RP1's chained
    handler writes IRQRESET once per pin per pass (rp1_gpio_irq_handler), which clears
    every edge latched on it, so a rise and a fall closer together than the hard IRQ's
    latency take one IRQ, one number, one event. The level is the witness: with both
    edges detected the levels alternate, and two in a row at the same level mean an edge
    between them never became an event. It is final at once, and reported on its line."""
    from wl_sync.rp1 import gpio

    events = [
        _ev(1, 20, 1, True), _ev(2, 20, 2, False), _ev(3, 20, 3, False),
        _ev(4, 20, 4, True), _ev(5, 26, 1, True), _ev(6, 26, 2, True),
    ]
    request = _Request([events, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000])
    error = pump.take_error()
    assert isinstance(error, gpio.CoalescedEdgeError)
    assert error.coalesced == {20: 1, 26: 1} and error.lost == 2
    assert str(error).startswith(
        "2 edge event(s) lost (coalesced interrupts: 1 on GPIO20, 1 on GPIO26)"
    )
    _pump_through(pump, request, [80_000])
    assert pump.error is None


def test_alternating_levels_are_not_a_loss():
    from wl_sync.rp1.gpio import EdgePump

    request = _Request([_frames(1, 2, 3, 4, 5, 6, 7), None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    assert pump.error is None


def test_a_merged_pair_is_one_coalesced_loss_on_its_line_not_two():
    """GPIO27 fell and rose again before its IRQ thread ran. Both hard IRQs took a
    number, 4 and 5; the thread ran once, so one event carries 5 and the level it found,
    high (edge_irq_thread reads the level, gpiod_get_value_cansleep). pinctrl-rp1 handles
    edge IRQs with handle_edge_irq, which leaves the line unmasked while the thread runs.
    Two rising events in a row on GPIO27 report the loss at once, on its line; number 4,
    between them, is that same loss and is not reported again when its window closes."""
    from wl_sync.rp1 import gpio

    events = [
        _ev(1, 26, 1, True), _ev(2, 27, 1, True), _ev(3, 26, 2, False),
        _ev(5, 27, 2, True), _ev(6, 26, 3, True),
    ]
    request = _Request([events, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000])
    error = pump.take_error()
    assert "1 edge event(s) lost (coalesced interrupts on GPIO27)" in str(error)
    assert isinstance(error, gpio.CoalescedEdgeError)
    assert error.coalesced == {27: 1} and error.lost == 1
    _pump_through(pump, request, [80_000])
    assert pump.error is None, "the merge reported twice"


def test_the_irq_thread_race_that_reuses_a_number_loses_one_edges_level_not_a_count():
    """GPIO27's second hard IRQ landed while its thread was building the first event,
    after it had cleared the stamp and before it read the number (gpiolib-cdev.c L896,
    L913). The first event goes out with the second IRQ's number, 5, and the re-run
    sends the second edge with 5 again: number 4 is missing and 5 is doubled. Two edges,
    two events, but the thread read the level for the first event after the second edge
    had landed, so it reads rising where the edge fell. That one edge is lost from the
    record, and reported; number 4 is not reported again, and nothing reads negative."""
    from wl_sync.rp1 import gpio

    events = [
        _ev(1, 26, 1, True), _ev(2, 27, 1, True), _ev(3, 26, 2, False),
        _ev(5, 27, 2, True), _ev(5, 27, 3, True), _ev(6, 26, 3, True),
    ]
    request = _Request([events, None, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    error = pump.take_error()
    assert isinstance(error, gpio.CoalescedEdgeError)
    assert error.coalesced == {27: 1} and error.lost == 1
    _pump_through(pump, request, [110_000])
    assert pump.error is None


def test_the_irq_thread_race_with_its_levels_right_is_no_loss():
    """The same race when the thread read the level before the second edge: the levels
    alternate, the duplicate accounts for number 4, and there is nothing to report."""
    from wl_sync.rp1.gpio import EdgePump

    events = [
        _ev(1, 26, 1, True), _ev(2, 27, 1, True), _ev(3, 26, 2, False),
        _ev(5, 27, 2, False), _ev(5, 27, 3, True), _ev(6, 26, 3, True),
    ]
    request = _Request([events, None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    assert pump.error is None


def test_a_race_at_the_wrong_level_excuses_its_missing_number_once():
    """GPIO27's race at the wrong level: its first copy pairs with the event before it,
    and that pair already explains the missing number, 4. The repeat used to queue a
    second excuse for the same 4, and the spare one swallowed number 3, lost on GPIO21
    to a merge of three edges that leaves its levels alternating. Truth: one edge lost
    on GPIO27, one number nothing explains. Both are reported."""
    from wl_sync.rp1 import gpio

    first = [_ev(1, 21, 1, True), _ev(2, 27, 1, True)]
    race = [_ev(5, 27, 2, True), _ev(5, 27, 3, True), _ev(6, 21, 2, False)]
    request = _Request([first, race, None, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [45_000, 50_000])
    paired = pump.take_error()
    assert paired.coalesced == {27: 1} and paired.lost == 1
    _pump_through(pump, request, [80_000, 110_000])
    unexplained = pump.take_error()
    assert isinstance(unexplained, gpio.UnaccountedEdgeError) and unexplained.lost == 1


def test_a_race_on_a_lines_first_event_at_the_wrong_level_is_not_silent():
    """The race on GPIO20's very first event: there is no event before the first copy to
    pair with, so the repeat, whose level is the true one, is compared with it instead.
    Rising twice: the first copy carries the second edge's level, and the falling edge
    is lost from the record. The repeat explains the missing number."""
    from wl_sync.rp1 import gpio

    events = [_ev(1, 26, 1, True), _ev(3, 20, 1, True), _ev(3, 20, 2, True)]
    request = _Request([events, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    error = pump.take_error()
    assert isinstance(error, gpio.CoalescedEdgeError)
    assert error.coalesced == {20: 1} and error.lost == 1


def test_a_level_read_ahead_of_its_own_irq_counts_two():
    """Pinned, as documented: GPIO20's second edge landed before the thread read the
    level for the first event, but its hard IRQ came after the thread took the first
    event's number. Two events, the right numbers and stamps, the first at the wrong
    level: two pairs at one level, so two are counted for one damaged edge."""
    from wl_sync.rp1 import gpio

    events = [_ev(1, 20, 1, True), _ev(2, 20, 2, True), _ev(3, 20, 3, True)]
    request = _Request([events, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    assert pump.take_error().coalesced == {20: 2}


def test_a_reused_number_never_excuses_another_lines_edge_still_on_its_way():
    """The race on GPIO20 lost number 11. Between its two hard IRQs GPIO26 took 12 and
    GPIO27 took 13, and GPIO27's thread is slow: 13 arrives in the next read. The
    duplicate used to excuse the highest number missing below it, 13, the one still on
    its way; when 13 arrived there was nothing to fill, and 11 was reported lost though
    nothing was. An excuse now waits until the numbers in its range are final."""
    from wl_sync.rp1.gpio import EdgePump

    base = [_ev(9, 26, 1, True), _ev(10, 20, 1, False)]
    race = [_ev(12, 26, 2, False), _ev(14, 20, 2, True), _ev(14, 20, 3, False)]
    late = [_ev(13, 27, 1, True, stamp_us=49_990)]
    request = _Request([base, race, late, None, None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [45_000, 50_000, 50_100, 80_000, 110_000])
    assert pump.error is None


_U32 = 1 << 32  # both numbers are __u32 (uapi/linux/gpio.h, struct gpio_v2_line_event)


def test_the_u32_sequence_numbers_wrap_without_a_false_drop():
    """Both numbers are 32-bit and wrap to 0. At the camera lines' rate alone that is a
    matter of months, and the service runs as long as the box is up. A swap across the
    wrap is still only a swap."""
    from wl_sync.rp1.gpio import EdgePump

    top = _U32 - 1
    events = [
        _ev(top - 2, 26, top - 1, True), _ev(top - 1, 27, 7, True),
        _ev(0, 27, 8, False), _ev(top, 26, top, False),  # GPIO27's 0 before GPIO26's top
        _ev(1, 26, 0, True), _ev(2, 27, 9, True),
    ]
    request = _Request([events[:3], events[3:], None])
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 50_100, 80_000])
    assert pump.error is None


def test_a_real_gap_across_the_wrap_counts_what_is_missing():
    from wl_sync.rp1.gpio import DroppedEdgeError, EdgePump

    top = _U32 - 1
    events = [_ev(top - 1, 26, 3, True), _ev(top, 27, 3, True), _ev(1, 27, 4, False)]
    request = _Request([events, None])  # number 0 never comes
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    assert isinstance(pump.error, DroppedEdgeError) and pump.error.lost == 1
    assert _unknown_line(pump.error), str(pump.error)


def test_a_gap_is_not_held_forever_while_the_pump_is_behind():
    """A read that fills EDGE_READ_MAX does not move the mark, so while the pump works
    through a backlog the mark alone would hold a gap open for as long as the backlog
    lasted. The kernel's buffer holds at most 1024 events (gpiolib-cdev.c caps
    event_buffer_size at GPIO_V2_LINES_MAX * 16), so a missing event that was in it is
    among the next 1024 read, once the IRQ thread's allowance has passed."""
    from wl_sync.rp1.gpio import EDGE_READ_MAX, DroppedEdgeError, EdgePump

    def full(start):  # one line, its own count unbroken; the request's skips 101
        return [
            _FakeEvent(n + (n > 100), 20, n % 2 == 1, 1_000 * n, line_seqno=n)
            for n in range(start, start + EDGE_READ_MAX)
        ]

    script = [full(1 + EDGE_READ_MAX * k) for k in range(20)]
    request = _Request(script)
    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000, 50_100])  # the gap is seen in the 2nd read
    assert pump.error is None
    _pump_through(pump, request, [80_000 + k for k in range(18)])
    assert pump.delivered_through is None, "every read was full; the mark never moved"
    assert isinstance(pump.error, DroppedEdgeError)
    assert pump.error.lost == 1


def test_a_loss_held_while_check_health_takes_it_is_reported_exactly_once():
    """The pump adds a loss to the one it holds while check_health() takes and clears
    it, from another thread. Unlocked, a switch between the pump's read and its store
    put back a loss already taken: one review stress held 2.4 million and reported 4.7
    million. And a switch between its check and its second read of the slot could call
    combine on None and kill the edge thread. One lock now covers both sides."""
    import sys
    import threading

    from wl_sync.rp1.gpio import EdgePump, UnaccountedEdgeError

    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    holds, reported, failures = 20_000, [], []
    done = threading.Event()

    def hold():
        try:
            for _ in range(holds):
                pump._hold(UnaccountedEdgeError(unaccounted=1))
        except BaseException as error:  # noqa: BLE001 - the test reports it
            failures.append(error)
        finally:
            done.set()

    def take():
        while not done.is_set():
            error = pump.take_error()
            if error is not None:
                reported.append(error.lost)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=hold), threading.Thread(target=take)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        sys.setswitchinterval(interval)
    last = pump.take_error()
    assert failures == []
    counted = sum(reported) + (last.lost if last is not None else 0)
    assert counted == holds, f"held {holds}, reported {counted}"


def test_a_loss_report_survives_copy_and_pickle():
    import copy
    import pickle

    from wl_sync.rp1.gpio import DroppedEdgeError, EdgeBufferOverflowError

    error = EdgeBufferOverflowError(overflowed={20: 2}).combine(
        DroppedEdgeError(coalesced={27: 1}, unaccounted=3)
    )
    for again in (copy.copy(error), pickle.loads(pickle.dumps(error))):
        assert type(again) is type(error) and str(again) == str(error)
        assert again.lost == error.lost == 6


def test_known_limit_an_overflow_on_a_quiet_line_is_counted_twice():
    """WHAT IT CANNOT TELL, pinned. The buffer overflowed and threw away GPIO21's second
    event, number 3. GPIO21 does not fire again until long after the window has closed on
    3, so 3 is reported first as a loss on an unknown line; when GPIO21 next fires, its
    own numbers show the overflow, which is reported too. One edge, counted twice."""
    from wl_sync.rp1 import gpio

    first = [_ev(1, 21, 1, True), _ev(2, 20, 1, True)]
    request = _Request([first, [_ev(4, 20, 2, False)], None, [_ev(5, 21, 3, False)], None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [40_000, 50_000, 80_000])
    unknown = pump.take_error()
    assert isinstance(unknown, gpio.UnaccountedEdgeError) and unknown.lost == 1
    _pump_through(pump, request, [500_000, 600_000])
    overflow = pump.take_error()
    assert isinstance(overflow, gpio.EdgeBufferOverflowError)
    assert overflow.overflowed == {21: 1}


def test_known_limit_an_event_later_than_the_window_is_reported_lost_then_passed_on():
    """WHAT IT CANNOT TELL, pinned. GPIO26's thread buffered number 3 more than
    EDGE_STAMP_LAG_US after its stamp, past the window, which is the roll barrier's
    assumption failing. 3 is reported lost; when it does arrive it is passed on without
    a word. The stamp-lag witness counts it over its allowance, so the journal warns."""
    from wl_sync.rp1 import gpio

    first = [_ev(1, 26, 1, True), _ev(2, 27, 1, True), _ev(4, 27, 2, False)]
    late = [_ev(3, 26, 2, False, stamp_us=49_000)]
    seen = []
    request = _Request([first, None, late, None])
    pump = gpio.EdgePump(on_edge=lambda g, l, t: seen.append(t), origin_ns=0)
    _pump_through(pump, request, [50_000, 80_000])
    error = pump.take_error()
    assert isinstance(error, gpio.UnaccountedEdgeError) and error.lost == 1
    _pump_through(pump, request, [80_100, 120_000])
    assert 49_000 in seen and pump.error is None
    assert pump.stamp_lag_over >= 1


class _Bank(_Kernel):
    """A request of several lines on RP1's bank, as gpiolib-cdev.c fills its buffer.

    Each edge is (stamp_us, gpio, lag_us, merged). Its hard IRQ stamps it and takes the
    next request-wide number, so numbers follow stamps: one chained handler,
    rp1_gpio_irq_handler, demultiplexes the bank, in bit order. The line's IRQ thread
    counts it on the line and puts it in the buffer up to `lag_us` after the hard IRQ
    that woke it, never ahead of that line's previous event, since a line has one
    thread, and never before its own stamp. A merged edge took its number and woke the
    thread, and the line's next edge came before the thread ran, so that thread made one
    event of both: the next edge's stamp, number and level, buffered by the time the
    merged edge's lag allowed."""

    def __init__(self, edges, now_us):
        self.now = now_us
        self.buffer = []
        on_the_way, level, counted, last, woke = [], {}, {}, {}, {}
        for seqno, (stamp, gpio, lag, merged) in enumerate(sorted(edges), start=1):
            level[gpio] = not level.get(gpio, False)
            if merged:
                woke[gpio] = stamp + lag
                continue
            counted[gpio] = counted.get(gpio, 0) + 1
            pushed = max(stamp, woke.pop(gpio, stamp + lag))
            last[gpio] = arrives = max(pushed, last.get(gpio, 0))
            event = _FakeEvent(seqno, gpio, level[gpio], stamp * 1_000, counted[gpio])
            on_the_way.append((stamp, arrives, event))
        self.on_the_way = sorted(on_the_way, key=lambda edge: edge[1])

    def _arrive(self):
        while self.on_the_way and self.on_the_way[0][1] <= self.now:
            self.buffer.append(self.on_the_way.pop(0)[2])


@pytest.mark.parametrize("merge_chance", [0.0, 0.03])
def test_reordering_and_the_mark_never_disagree(merge_chance):
    """The cameras' two lines fire within microseconds of each other on every frame,
    their threads put each edge in the buffer anywhere up to EDGE_STAMP_LAG_US later,
    and a lick line chatters in bursts. Now and then a camera line glitches: an edge one
    to three microseconds before its real one, merged with it. Nothing lost reads as
    lost, every merge is reported, exactly once, on its line, and the mark stays behind
    every edge not yet delivered.

    THE TWO CANNOT DISAGREE: a gap is final only once the mark has passed the moment it
    was seen, and the mark passing a moment means every edge stamped before it has been
    delivered -- the missing one included, had it existed. Out-of-order arrival within
    a batch changes nothing, since the mark moves only after the whole batch."""
    import random

    from wl_sync.rp1 import gpio
    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US, EdgePump

    chance = random.Random(20261030)
    edges, frame, merged_on = [], 1_000_000, []
    for n in range(600):
        frame += chance.choice([3_000, 10_000, 33_333])
        for camera in (26, 27):
            stamp = frame + chance.randint(3, 6)
            edges.append((stamp, camera, chance.randint(0, EDGE_STAMP_LAG_US), False))
            if n > 20 and chance.random() < merge_chance:
                glitch = stamp - chance.randint(1, 3)
                edges.append((glitch, camera, chance.randint(0, EDGE_STAMP_LAG_US), True))
                merged_on.append(camera)
        if chance.random() < 0.1:
            lick = frame + chance.randint(0, 2_000)
            for k in range(chance.choice([1, 2, 30])):
                lag = chance.randint(0, EDGE_STAMP_LAG_US)
                edges.append((lick + 7 * k, 20, lag, False))
    assert merged_on or not merge_chance

    bank = _Bank(edges, now_us=500_000)
    stamps = sorted(stamp for stamp, _, _, merged in edges if not merged)
    delivered = []
    pump = EdgePump(on_edge=lambda g, l, t: delivered.append(t), origin_ns=0)
    reported, oldest = {}, 0

    def take():  # what check_health() does each second
        error = pump.take_error()
        if error is not None:
            assert isinstance(error, gpio.CoalescedEdgeError), error
            for line, count in error.coalesced.items():
                reported[line] = reported.get(line, 0) + count

    while bank.on_the_way or bank.buffer:
        assert pump.pump_once(bank, bank.now_us)
        take()
        done = sorted(delivered)  # both sorted, so they agree up to the oldest not done
        while oldest < min(len(stamps), len(done)) and done[oldest] == stamps[oldest]:
            oldest += 1
        if pump.delivered_through is not None and oldest < len(stamps):
            assert pump.delivered_through < stamps[oldest]
    pump.finish(bank, bank.now_us)
    take()
    assert reported == {line: merged_on.count(line) for line in set(merged_on)}


class _Arrivals(_Bank):
    """The kernel's buffer on a clock that moves: each entry is (arrives_us, event), the
    moment the line's IRQ thread put the event in the buffer."""

    def __init__(self, arrivals, now_us):
        self.now = now_us
        self.buffer = []
        self.on_the_way = sorted(
            ((event.timestamp_ns // 1_000, arrives, event) for arrives, event in arrivals),
            key=lambda edge: edge[1],
        )


def test_the_last_look_ends_on_the_window_not_on_a_count():
    """Cameras at 200 fps, and capture stops while the kernel still holds frame 6 and a
    real loss is held: number 11 never comes. With the cameras faster than EDGE_WAIT_S no
    wait times out, so a look that ended after a fixed number of passes forced its last
    read's gaps final. Its third pass read GPIO27's number 17 two microseconds before
    GPIO26's 16 arrived, and the end of a healthy frame was reported lost beside 11. The
    look now runs until the window has closed on every gap it holds: 16 arrives, and 11
    alone is reported."""
    from wl_sync.rp1 import gpio

    arrivals = []
    for k in range(1, 16):
        frame = 1_000_000 + 5_000 * k
        n = 2 * k - 1 + (k >= 6)  # number 11 never comes
        early = k == 8  # GPIO27's thread first, by 2 us
        first, second = (42, 40) if early else (50, 50)
        arrivals.append((frame + first, _ev(n, 26, k, k % 2, frame)))
        arrivals.append((frame + second, _ev(n + 1, 27, k, k % 2, frame + 2)))
    kernel = _Arrivals(arrivals, now_us=1_000_000)
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    while kernel.on_the_way[0][1] <= 1_025_100:  # the run, through frame 5
        assert pump.pump_once(kernel, kernel.now_us)
    kernel.now = 1_030_100  # frame 6 is in the buffer: stop
    pump.finish(kernel, kernel.now_us)
    error = pump.take_error()
    assert isinstance(error, gpio.UnaccountedEdgeError)
    assert error.lost == 1, "a frame caught out of order by the last read, called lost"


def test_a_stop_mid_stream_reports_nothing_when_nothing_was_lost():
    """The randomized bank of the test below, nothing lost, stopped at a random moment
    with the cameras faster than EDGE_WAIT_S, for many stops."""
    import random

    from wl_sync.rp1.gpio import EDGE_STAMP_LAG_US, EdgePump

    false = []
    for seed in range(40):
        chance = random.Random(seed)
        edges, frame = [], 1_000_000
        for _ in range(120):
            frame += chance.choice([3_000, 10_000])
            for camera in (26, 27):
                stamp = frame + chance.randint(3, 6)
                edges.append((stamp, camera, chance.randint(0, EDGE_STAMP_LAG_US), False))
        bank = _Bank(edges, now_us=500_000)
        stop = chance.randint(1_100_000, max(stamp for stamp, *_ in edges))
        pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
        while (bank.on_the_way or bank.buffer) and bank.now < stop:
            assert pump.pump_once(bank, bank.now_us)
            assert pump.take_error() is None
        pump.finish(bank, bank.now_us)
        if pump.take_error() is not None:
            false.append(seed)
    assert false == []


def test_a_gap_held_when_capture_stops_is_reported_not_dropped():
    """Capture stops a moment after a merge was seen, inside its window, with an edge
    still in the kernel's buffer. The last look delivers it, and keeps looking until the
    window closes on number 3, which never comes: it is reported, not dropped."""
    from wl_sync.rp1 import gpio

    seen = []
    kernel = _Arrivals(
        [(40_050, _ev(1, 26, 1, True)), (40_060, _ev(2, 27, 1, True)),
         (40_070, _ev(4, 27, 2, False)), (40_500, _ev(5, 26, 2, False))],
        now_us=40_100,
    )
    pump = gpio.EdgePump(on_edge=lambda g, l, t: seen.append(g), origin_ns=0)
    assert pump.pump_once(kernel, kernel.now_us)
    assert pump.error is None  # number 3 is held

    pump.finish(kernel, kernel.now_us)
    assert seen == [26, 27, 27, 26], "the last look did not deliver what was left"
    error = pump.take_error()
    assert isinstance(error, gpio.UnaccountedEdgeError) and error.lost == 1
    assert kernel.now - 40_100 < gpio.STOP_BUDGET_US, "the window closed; why wait on?"


def test_the_last_look_is_bounded_by_the_clock_on_a_line_that_never_goes_quiet():
    """A gap that never fills, and a line that chatters every 2 ms through the stop: no
    wait times out and the gap is always held. The look ends once STOP_BUDGET_US has
    passed, one pass over at most, and reports the gap."""
    from wl_sync.rp1 import gpio

    chatter = [(50_000 + 2_000 * k, _ev(3 + k, 26, 2 + k, k % 2 == 0)) for k in range(200)]
    kernel = _Arrivals([(49_000, _ev(1, 26, 1, False))] + chatter, now_us=49_500)
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert pump.pump_once(kernel, kernel.now_us)  # number 1
    kernel.now = 50_100
    assert pump.pump_once(kernel, kernel.now_us)  # number 3: 2 is missing, and held
    began = kernel.now
    pump.finish(kernel, kernel.now_us)
    assert kernel.now - began < gpio.STOP_BUDGET_US + round(gpio.EDGE_WAIT_S * 1e6)
    assert kernel.on_the_way, "the look ran until the line went quiet"
    assert pump.take_error().lost == 1


def test_the_last_look_is_bounded_by_its_passes_on_a_clock_that_stands_still():
    from wl_sync.rp1 import gpio

    request = _Request([[_ev(1, 26, 1, True), _ev(3, 26, 2, False)]])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [50_000])
    request.script = [[_ev(4 + k, 26, 3 + k, k % 2 == 0)] for k in range(200)]
    pump.finish(request, request.now_us)
    assert len(request.script) == 200 - gpio.STOP_PASSES
    assert pump.take_error().lost == 1


def test_the_last_look_delivers_a_backlog_larger_than_one_read():
    """Capture stops with 160 edges in the kernel's buffer, nothing lost and no gap held.
    A read that fills EDGE_READ_MAX says nothing about what is left, so the look must
    not end on it: ended there, it released 96 edges unread with nothing to say so."""
    from wl_sync.rp1.gpio import EDGE_READ_MAX, EdgePump

    seen = []
    request = _Request([[_ev(1, 26, 1, True)]])
    pump = EdgePump(on_edge=lambda g, l, t: seen.append(t), origin_ns=0)
    _pump_through(pump, request, [50_000])
    backlog = [_ev(2 + k, 26, 2 + k, k % 2 == 1, 40_001 + k) for k in range(160)]
    request.script = [backlog[k:k + EDGE_READ_MAX] for k in range(0, 160, EDGE_READ_MAX)]
    pump.finish(request, request.now_us)
    assert len(seen) == 161 and request.script == []
    assert pump.take_error() is None


def test_a_race_and_a_merge_on_one_event_count_one_loss():
    """GPIO20 rose and fell (3, 4) before its thread ran, merged; a third edge's hard IRQ
    (5) landed while the thread built that event, after the stamp was cleared, and the
    thread had read the level before the third edge: the first copy is falling, A's
    level, and pairs with the falling event before it; the repeat is rising, B's. Two
    numbers are missing, the merge's and the race's, and one edge is lost. The repeat's
    level differs from the first copy's, so the first copy carried its own level and the
    pair is the merge's: the repeat still explains the race's number."""
    from wl_sync.rp1 import gpio

    first = [_ev(1, 20, 1, True), _ev(2, 20, 2, False)]
    race = [_ev(5, 20, 3, False), _ev(5, 20, 4, True), _ev(6, 26, 1, True)]
    request = _Request([first, race, None, None])
    pump = gpio.EdgePump(on_edge=lambda *_: None, origin_ns=0)
    _pump_through(pump, request, [45_000, 50_000])
    error = pump.take_error()
    assert error.coalesced == {20: 1} and error.lost == 1
    _pump_through(pump, request, [80_000, 110_000])
    assert pump.take_error() is None, "the race's number reported as unexplained"
