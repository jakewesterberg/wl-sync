"""The barcode counter, which is a clock.

Until 2026-08-16 the counter was "how many barcodes this lab has ever emitted", held
in memory and passed in as a constructor argument. It is now "seconds since 2020",
derived from the wall clock, and that change exists for one reason: it makes an OUTAGE
VISIBLE IN THE SHARED KEY. A device that recorded a barcode either side of a gap
computes the gap from the values alone, and a naive consumer assuming "one barcode =
one second" stays correct across it. Contiguous numbering would have made 1000 and 1001
five hundred seconds apart and silently mis-placed every trial after the boundary.

The cost is a dependency on a clock that survives power loss, which is what the CM5 IO
Board's CR2032 is for. The clamp below is what keeps a broken clock from turning a
precision problem into a correctness one. The 32-bit range is absolute: any value that
cannot fit is treated as corruption and degraded rather than propagated.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

BARCODE_EPOCH = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
CHECKPOINT_INTERVAL_S = 60
_MAX_BARCODE = (1 << 32) - 1


def value_from_clock(now: datetime.datetime) -> int:
    """Barcode value for an instant: whole seconds since the epoch."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock reading must be timezone-aware")
    seconds = int((now - BARCODE_EPOCH).total_seconds())
    if seconds < 0:
        raise ValueError(f"clock reads before the barcode epoch: {now.isoformat()}")
    if seconds > _MAX_BARCODE:
        raise ValueError(f"barcode value out of 32-bit range: {seconds}")
    return seconds


def next_value(now: datetime.datetime, checkpoint: int | None) -> int:
    """The value to resume at, clamped so it can never repeat or go backwards.

    The clamp is `checkpoint + 1` -- exactly enough to guarantee monotonicity, no more.
    The staleness margin that used to live here (`+ CHECKPOINT_INTERVAL_S`) has moved to
    the PERIODIC mid-run checkpoint write instead (wl_sync.cli.run): that write is the
    only one that can be stale, because up to one interval of barcodes may have been
    emitted after it was last read back. A clean close checkpoints the exact
    last-emitted value, which carries no staleness at all, so applying the interval
    margin unconditionally here used to inflate every ordinary restart's reported gap by
    a whole checkpoint interval even when the restart followed within seconds.

    When the clamp binds it still lands strictly ahead of the checkpoint, so a gap is
    OVER-stated rather than under-stated -- landing behind would claim coverage the box
    did not have, which is the failure that matters.
    """
    from_clock = value_from_clock(now)
    if checkpoint is None:
        return from_clock
    value = max(from_clock, checkpoint + 1)
    if value > _MAX_BARCODE:
        raise ValueError(f"barcode value out of 32-bit range: {value}")
    return value


def read_checkpoint(path: Path) -> int | None:
    """The last checkpointed value, or None if absent or unreadable.

    Unreadable degrades to None rather than raising: a torn checkpoint must not stop
    the first device of the day from starting. The cost of None is only that the
    clamp cannot bind, and the clock is then the sole source -- which is the normal
    case anyway. An out-of-range value is treated as corrupt and also returns None.
    """
    try:
        value = int(path.read_text().strip())
        if value < 0 or value > _MAX_BARCODE:
            return None
        return value
    except (OSError, ValueError):
        return None


def write_checkpoint(path: Path, value: int) -> None:
    """Write atomically, so a crash mid-write cannot leave a truncated number that
    happens to parse as a much smaller one."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{value}\n")
    temporary.replace(path)


@dataclass(frozen=True, slots=True)
class ClockTrust:
    trusted: bool
    reason: str


def evaluate_clock(
    now: datetime.datetime, checkpoint: int | None, ntp_synchronized: bool | None
) -> ClockTrust:
    """Whether gap arithmetic across this boundary can be believed.

    TWO DIFFERENT QUESTIONS ARE ASKED OF THE SAME NUMBER, and they need different
    tolerances. `next_value`'s clamp asks "could this value have been used already?" --
    uniqueness, where `checkpoint + 1` with no slack is exactly right and any slack is
    an inflated gap. This asks "is the wall clock telling the truth?" -- and here the
    checkpoint is DELIBERATELY a future value, `value + CHECKPOINT_INTERVAL_S` written
    ahead of what the run had emitted. Testing `value_from_clock(now) <= checkpoint`
    therefore stopped being a clock test and became a "did we crash recently" test: a
    healthy clock and a crash 70 s into a run reported clock_behind_checkpoint, which
    sends an operator to check a CR2032 over a crashing process (deploy/README.md) and
    makes wl-preproc distrust gap arithmetic at exactly the boundaries where it matters.
    One whole interval of tolerance is the most a correctly-written checkpoint can be
    ahead of a truthful clock, so anything beyond it is the clock's fault.

    THE BOUNDARY, exactly `CHECKPOINT_INTERVAL_S` ahead, is genuinely ambiguous: it is
    both a restart landing in the same second as the last checkpoint write, and a clock
    stalled exactly one interval. From this number alone the two are indistinguishable.
    It is resolved toward distrust because `clock_trusted: false` costs a downstream
    PRECISION claim, while a missed bad clock would let wl-preproc believe gap
    arithmetic it should not -- the same over-states-never-under-states asymmetry the
    clamp itself is built on.

    `ntp_synchronized=None` means "could not tell" -- timedatectl absent, as on a
    laptop or in a container -- and is NOT treated as untrusted. Crying wolf on every
    developer machine would train people to ignore the flag on the one box where it
    means something.
    """
    reasons = []
    if (
        checkpoint is not None
        and value_from_clock(now) + CHECKPOINT_INTERVAL_S <= checkpoint
    ):
        reasons.append("clock_behind_checkpoint")
    if ntp_synchronized is False:
        reasons.append("ntp_unsynchronized")
    return ClockTrust(trusted=not reasons, reason=",".join(reasons))
