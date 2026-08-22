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
    """Ticks are 32-bit everywhere in this package, and Recorder's per-path unwrapper
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
