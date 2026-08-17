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

# How far the checkpoint may legitimately lead the wall clock before the clock is
# distrusted. NOT a write cadence: it was `CHECKPOINT_INTERVAL_S` until 2026-08-17,
# when the checkpoint became the exact last-emitted value written every second, and
# nothing schedules writes any more. Its only remaining job is evaluate_clock's
# tolerance for ordinary small backwards corrections -- an NTP step, typically -- which
# are not worth an operator's attention. Renamed so nobody reads a cadence into it.
CLOCK_TRUST_TOLERANCE_S = 60

# Public because wl_sync.cli's degraded resume path needs the ceiling too: a corrupt
# maximal checkpoint plus a dead RTC would otherwise produce 2**32, which
# barcode.encode rejects -- the crash loop this whole degradation exists to prevent.
MAX_BARCODE = (1 << 32) - 1


def value_from_clock(now: datetime.datetime) -> int:
    """Barcode value for an instant: whole seconds since the epoch."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock reading must be timezone-aware")
    seconds = int((now - BARCODE_EPOCH).total_seconds())
    if seconds < 0:
        raise ValueError(f"clock reads before the barcode epoch: {now.isoformat()}")
    if seconds > MAX_BARCODE:
        raise ValueError(f"barcode value out of 32-bit range: {seconds}")
    return seconds


def next_value(now: datetime.datetime, checkpoint: int | None) -> int:
    """The value to resume at, clamped so it can never repeat or go backwards.

    The clamp is `checkpoint + 1` -- exactly enough to guarantee monotonicity, no more.
    NO STALENESS MARGIN IS APPLIED ANYWHERE, at either end. The checkpoint is the exact
    last-emitted value, rewritten every second (wl_sync.cli.run), so there is nothing to
    compensate for: `checkpoint + 1` is the first value provably never used.

    Two earlier designs both put a margin somewhere and both were wrong, in the same
    direction and for the same reason -- a margin the restart cannot verify becomes a
    ratchet under Restart=always. Adding `+ CHECKPOINT_INTERVAL_S` HERE inflated every
    ordinary restart's reported gap by a whole interval even when the restart followed
    within seconds. Moving it into a periodic high-water WRITE fixed that but let a
    crash loop compound it instead: each run left a mark ahead of what it had emitted
    and the next run resumed past that mark, so 25 crash restarts over 75 s of real time
    put the counter 1449 s ahead of the wall clock -- destroying the premise that a
    barcode IS seconds since 2020. Writing the exact value every second removes the
    margin rather than relocating it, and no ratchet is possible without one.

    When the clamp binds it lands strictly ahead of the checkpoint, so no value is ever
    re-issued however broken the clock is. It binds only when the clock has stopped
    moving forwards; on a healthy clock the clock value dominates and a restart resumes
    exactly where wall time says it should.
    """
    from_clock = value_from_clock(now)
    if checkpoint is None:
        return from_clock
    value = max(from_clock, checkpoint + 1)
    if value > MAX_BARCODE:
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
        if value < 0 or value > MAX_BARCODE:
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
    uniqueness, where `checkpoint + 1` with no slack is exactly right and any slack
    becomes a ratchet. This asks "is the wall clock telling the truth?", where a little
    slack is the whole point.

    The checkpoint is the exact last-emitted value, so on a clock that only moves
    forwards it can never lead the clock at all: the box counted that value at a moment
    that has already passed. Any lead therefore means the clock came back to a reading
    the box has already been past. `CLOCK_TRUST_TOLERANCE_S` is how much of that is
    ordinary -- a small NTP correction, which happens on healthy machines and is not
    worth sending anyone to check a CR2032 (deploy/README.md). A bare
    `value_from_clock(now) <= checkpoint` was not a clock test at all while the
    checkpoint was a deliberate future value: a healthy clock and a crash 70 s into a
    run reported clock_behind_checkpoint, exactly where wl-preproc most needs to believe
    gap arithmetic.

    `<`, NOT `<=`, so a tolerance of N tolerates a discrepancy of N. `<=` would make
    `CLOCK_TRUST_TOLERANCE_S = 60` mean "59 s is fine, 60 s is not", which is not what
    the constant says.

    THE COST, STATED: a backwards jump smaller than the tolerance is not flagged, and
    the clamp will then hold the counter still across it, so a gap can be UNDER-stated
    by up to the tolerance without a mark. That is the deliberate price of not crying
    wolf on ordinary NTP corrections; uniqueness is unaffected, because the clamp still
    guarantees it. Spec Sec.7 records this.

    `ntp_synchronized=None` means "could not tell" -- timedatectl absent, as on a
    laptop or in a container -- and is NOT treated as untrusted. Crying wolf on every
    developer machine would train people to ignore the flag on the one box where it
    means something.
    """
    reasons = []
    if (
        checkpoint is not None
        and value_from_clock(now) + CLOCK_TRUST_TOLERANCE_S < checkpoint
    ):
        reasons.append("clock_behind_checkpoint")
    if ntp_synchronized is False:
        reasons.append("ntp_unsynchronized")
    return ClockTrust(trusted=not reasons, reason=",".join(reasons))
