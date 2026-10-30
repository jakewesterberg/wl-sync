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


def test_consecutive_sequence_numbers_pass():
    tracker = SeqnoTracker()
    for seqno in (1, 2, 3, 4):
        tracker.check(seqno)


def test_a_gap_in_the_sequence_numbers_is_raised():
    """libgpiod numbers every edge event, so a KERNEL-side drop is detectable rather
    than inferred. The kernel's event buffer overflows silently under a fast line, and
    a silently short edge record is a trial that looks like it never happened."""
    tracker = SeqnoTracker()
    tracker.check(1)
    tracker.check(2)
    with pytest.raises(DroppedEdgeError, match="3"):
        tracker.check(6)


def test_the_first_event_sets_the_baseline_whatever_its_number():
    """A request joined mid-stream starts wherever the kernel's counter already is."""
    tracker = SeqnoTracker()
    tracker.check(9_000)
    tracker.check(9_001)


class _FakeEvent:
    class Type:
        RISING_EDGE = "rising"
        FALLING_EDGE = "falling"

    def __init__(self, seqno, offset, rising, timestamp_ns):
        self.global_seqno = seqno
        self.line_offset = offset
        self.event_type = self.Type.RISING_EDGE if rising else self.Type.FALLING_EDGE
        self.timestamp_ns = timestamp_ns


def test_events_are_delivered_on_the_shared_timebase():
    from wl_sync.rp1.gpio import EdgePump

    seen = []
    pump = EdgePump(on_edge=lambda g, l, t: seen.append((g, l, t)), origin_ns=1_000)
    pump.handle([_FakeEvent(1, 26, True, 1_000 + 5_000_000)])
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
    pump.handle([_FakeEvent(1, 20, True, 0)])
    pump.handle([_FakeEvent(9, 21, False, 1_000)])   # seqnos 2..8 lost
    pump.handle([_FakeEvent(10, 22, True, 2_000)])   # must still arrive

    assert seen == [20, 21, 22], "capture stopped after a drop"
    assert isinstance(pump.error, DroppedEdgeError)


def test_a_failing_consumer_stops_the_pump_and_is_held():
    """Unlike a drop, a sink that raises means records are NOT being kept. Continuing
    would spin, delivering into something that cannot accept -- so it stops, and says
    so, instead of dying quietly and letting the segment simply end."""
    from wl_sync.rp1.gpio import EdgePump

    def explode(gpio, level, tick):
        raise OSError("the sink is gone")

    pump = EdgePump(on_edge=explode, origin_ns=0)
    assert pump.handle([_FakeEvent(1, 20, True, 0)]) is False
    assert isinstance(pump.error, OSError)


def test_a_healthy_pump_reports_no_error():
    from wl_sync.rp1.gpio import EdgePump

    pump = EdgePump(on_edge=lambda *_: None, origin_ns=0)
    assert pump.handle([_FakeEvent(1, 20, True, 0)]) is True
    assert pump.error is None


def test_one_overflow_is_one_report_not_one_per_edge_after_it():
    """The tracker raised BEFORE it moved its baseline, so after one gap every later,
    perfectly consecutive edge was measured against the stale number and reported a
    fresh, growing loss: one 7-event overflow became an ERROR line every second for
    the rest of the day, each claiming more edges lost than the last."""
    tracker = SeqnoTracker()
    tracker.check(1)
    with pytest.raises(DroppedEdgeError):
        tracker.check(9)
    tracker.check(10)  # consecutive with 9, so nothing is lost
    tracker.check(11)


# How far the edge pump has delivered (`EdgePump.delivered_through`), which the run loop's
# roll barrier waits on (wl_sync.cli._await_delivery). The rule rests on two kernel facts,
# drivers/gpio/gpiolib-cdev.c: an edge is STAMPED in the hard IRQ handler and only PUT IN
# THE BUFFER by the IRQ thread afterwards; and a read returns early only when the buffer
# is empty. libgpiod's wait_edge_events is select() on the request's fd, readable while
# the buffer holds anything.


class _Request:
    """A libgpiod LineRequest, as EdgePump uses it, scripted step by step: None is a wait
    that times out, and a list is a wait that finds events followed by the read that
    returns that list. `now_us` is the box clock, moved only by the test."""

    def __init__(self, script, now_us=50_000):
        self.script = list(script)
        self.now = now_us
        self.waited = []
        self.asked = []

    def now_us(self):
        return self.now

    def wait_edge_events(self, timeout):
        self.waited.append(timeout)
        if self.script[0] is None:
            self.script.pop(0)
            return False
        return True

    def read_edge_events(self, max_events=None):
        self.asked.append(max_events)
        return self.script.pop(0)


def _edge(seqno, stamp_us):
    return _FakeEvent(seqno, 20, True, stamp_us * 1_000)


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
