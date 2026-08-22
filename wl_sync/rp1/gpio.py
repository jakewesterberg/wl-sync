"""GPIO arithmetic: pulse scheduling, edge timebase, and drop detection.

Pure, and tested on a laptop. The libgpiod calls that surround it live in
wl_sync.rp1.backend and are hardware-gated. Splitting them this way is the same
move Task 5a made for the PIO FIFO, and for the same reason: the parts that can
be wrong quietly are the parts worth testing, and none of them need a Pi.
"""

from __future__ import annotations

from collections.abc import Sequence

TICK_WRAP_US = 1 << 32


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

    Wrapped to 32 bits because every tick in this package is, and `Recorder`'s per-path
    unwrapper expects exactly that shape from all three paths.
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
        if self._previous is not None and seqno != self._previous + 1:
            missing = seqno - self._previous - 1
            raise DroppedEdgeError(
                f"{missing} edge event(s) lost between sequence {self._previous} and "
                f"{seqno}: the kernel's event buffer overflowed"
            )
        self._previous = seqno
