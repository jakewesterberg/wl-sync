import datetime
import os

import pytest

from wl_sync.clock import (
    BARCODE_EPOCH,
    CLOCK_TRUST_TOLERANCE_S,
    ClockTrust,
    evaluate_clock,
    next_value,
    read_checkpoint,
    value_from_clock,
    write_checkpoint,
)

UTC = datetime.timezone.utc


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_epoch_itself_is_zero():
    assert value_from_clock(BARCODE_EPOCH) == 0


def test_one_second_is_one_count():
    assert value_from_clock(at(2020, 1, 1, 0, 0, 1)) == 1


def test_value_is_seconds_since_epoch():
    assert value_from_clock(at(2020, 1, 2)) == 86_400


def test_naive_datetime_is_rejected():
    """A naive datetime silently means 'local time', which would shift every barcode."""
    with pytest.raises(ValueError, match="timezone-aware"):
        value_from_clock(datetime.datetime(2026, 8, 16, 12, 0))


def test_before_the_epoch_is_rejected():
    with pytest.raises(ValueError, match="before the barcode epoch"):
        value_from_clock(at(2019, 12, 31))


def test_healthy_clock_ignores_the_checkpoint():
    now = at(2026, 8, 16, 12, 0)
    assert next_value(now, checkpoint=None) == value_from_clock(now)
    assert next_value(now, checkpoint=10) == value_from_clock(now)


def test_clamp_binds_when_the_clock_goes_backwards():
    """The whole point: a backwards clock must never re-issue a used value. The clamp
    is checkpoint + 1 -- exactly enough for monotonicity, no staleness margin (that
    margin now lives only in the periodic checkpoint write, wl_sync.cli.run)."""
    now = at(2026, 8, 16, 12, 0)
    stale = value_from_clock(now) + 10_000
    assert next_value(now, checkpoint=stale) == stale + 1


def test_clamp_over_states_never_under_states():
    """When the clamp binds it must sit AHEAD of the checkpoint, so a gap is
    over-stated rather than claiming coverage the box did not have."""
    now = at(2026, 8, 16, 12, 0)
    stale = value_from_clock(now) + 1
    assert next_value(now, checkpoint=stale) > stale


def test_a_checkpoint_just_behind_a_healthy_clock_costs_no_margin():
    """A clean close checkpoints the exact last-emitted value, so a restart moments
    later must resume at the clock's own value -- not the clock value inflated by a
    whole checkpoint interval. That unconditional inflation used to misreport every
    ordinary clean restart as a ~61 s gap; the staleness margin now lives only in the
    periodic mid-run checkpoint write (wl_sync.cli.run), where the checkpoint really can
    be stale."""
    now = at(2026, 8, 16, 12, 0)
    checkpoint = value_from_clock(now) - 5
    assert next_value(now, checkpoint) == value_from_clock(now)


def test_checkpoint_round_trips(tmp_path):
    path = tmp_path / ".wl-sync-barcode-checkpoint"
    write_checkpoint(path, 212_000_000)
    assert read_checkpoint(path) == 212_000_000


def test_missing_checkpoint_reads_as_none(tmp_path):
    assert read_checkpoint(tmp_path / "absent") is None


def test_corrupt_checkpoint_reads_as_none(tmp_path):
    """A torn write must degrade to 'no checkpoint', never crash the box at 8am."""
    path = tmp_path / "checkpoint"
    path.write_text("not-a-number")
    assert read_checkpoint(path) is None


def test_clock_is_trusted_when_everything_agrees():
    now = at(2026, 8, 16, 12, 0)
    assert evaluate_clock(now, checkpoint=None, ntp_synchronized=True) == ClockTrust(
        trusted=True, reason=""
    )


def test_clock_behind_checkpoint_is_untrusted():
    now = at(2026, 8, 16, 12, 0)
    trust = evaluate_clock(
        now, checkpoint=value_from_clock(now) + 500, ntp_synchronized=True
    )
    assert trust.trusted is False
    assert "behind_checkpoint" in trust.reason


def test_a_crash_restart_on_a_healthy_clock_is_trusted():
    """The checkpoint is the exact last-emitted value, so on a clock that only moves
    forwards it can never lead the clock at all -- the box counted that value at a
    moment that has already passed. A crash restart therefore says nothing about the
    clock, which is the whole point: deploy/README.md tells the operator this flag
    usually means a missing CR2032, so a crashing process must not send anyone to check
    a battery."""
    now = at(2026, 8, 16, 12, 0)
    last_emitted = value_from_clock(now) - 1  # crashed a second ago
    assert evaluate_clock(now, last_emitted, ntp_synchronized=True).trusted is True


def test_a_backwards_correction_is_tolerated_up_to_the_tolerance():
    """What the tolerance is FOR, now that the checkpoint carries no margin of its own.
    A checkpoint leading the clock means the clock came back to a reading the box has
    already been past -- ordinarily a small NTP step on a healthy machine. It is flagged
    only once the lead exceeds CLOCK_TRUST_TOLERANCE_S."""
    now = at(2026, 8, 16, 12, 0)
    boundary = value_from_clock(now) + CLOCK_TRUST_TOLERANCE_S
    assert evaluate_clock(now, boundary - 1, ntp_synchronized=True).trusted is True
    assert evaluate_clock(now, boundary, ntp_synchronized=True).trusted is True
    assert evaluate_clock(now, boundary + 1, ntp_synchronized=True).trusted is False


def test_the_tolerance_tolerates_its_own_value():
    """`<`, not `<=`: a tolerance of N must tolerate a discrepancy of N, or the constant
    does not mean what it says. Kept as the literal numbers from the empirical
    reproduction that forced the fix -- clock 209120400 against checkpoint 209120460 was
    reported untrusted -- so the false positive cannot come back whatever the surrounding
    design does next."""
    now = BARCODE_EPOCH + datetime.timedelta(seconds=209_120_400)
    trust = evaluate_clock(now, checkpoint=209_120_460, ntp_synchronized=True)
    assert trust == ClockTrust(trusted=True, reason="")


def test_the_uniqueness_clamp_keeps_none_of_the_slack_trust_gets():
    """Two questions, one number. Trust tolerates a lead of a whole
    CLOCK_TRUST_TOLERANCE_S; uniqueness tolerates none, because any slack there ratchets
    under Restart=always. Asserted at the most tolerant checkpoint trust accepts, where
    the two answers differ most."""
    now = at(2026, 8, 16, 12, 0)
    most_tolerated = value_from_clock(now) + CLOCK_TRUST_TOLERANCE_S
    assert evaluate_clock(now, most_tolerated, ntp_synchronized=True).trusted is True
    assert next_value(now, most_tolerated) == most_tolerated + 1


def test_unsynchronised_ntp_is_untrusted():
    trust = evaluate_clock(at(2026, 8, 16, 12, 0), checkpoint=None, ntp_synchronized=False)
    assert trust.trusted is False
    assert "ntp_unsynchronized" in trust.reason


def test_unknown_ntp_state_is_not_by_itself_untrusted():
    """timedatectl may be absent (a container, a laptop). Unknown is not the same
    as unsynchronised, and must not cry wolf on every developer machine."""
    trust = evaluate_clock(at(2026, 8, 16, 12, 0), checkpoint=None, ntp_synchronized=None)
    assert trust.trusted is True


def test_reasons_accumulate():
    now = at(2026, 8, 16, 12, 0)
    trust = evaluate_clock(
        now, checkpoint=value_from_clock(now) + 500, ntp_synchronized=False
    )
    assert "behind_checkpoint" in trust.reason and "ntp_unsynchronized" in trust.reason


def test_value_from_clock_raises_at_32bit_ceiling():
    """The epoch has an end: around year 2156. A time that far out must raise."""
    from wl_sync.clock import MAX_BARCODE

    # Compute a datetime that would produce a value > MAX_BARCODE
    seconds_over = MAX_BARCODE + 1
    far_future = BARCODE_EPOCH + datetime.timedelta(seconds=seconds_over)
    with pytest.raises(ValueError, match="32-bit range"):
        value_from_clock(far_future)


def test_read_checkpoint_returns_none_for_out_of_range_value(tmp_path):
    """An out-of-range checkpoint is corrupt by definition and must degrade."""
    from wl_sync.clock import MAX_BARCODE

    path = tmp_path / "checkpoint"
    path.write_text(f"{MAX_BARCODE + 1000}\n")
    assert read_checkpoint(path) is None


def test_next_value_raises_when_clamp_exceeds_ceiling():
    """When the clamp would exceed the ceiling, it must raise rather than propagate the error."""
    from wl_sync.clock import MAX_BARCODE

    now = at(2026, 8, 16, 12, 0)
    # A checkpoint already at the ceiling: checkpoint + 1 overflows it.
    stale_checkpoint = MAX_BARCODE
    with pytest.raises(ValueError, match="32-bit range"):
        next_value(now, checkpoint=stale_checkpoint)


def test_the_checkpoint_and_its_directory_are_both_fsynced(tmp_path, monkeypatch):
    """Durability, which atomicity alone does not give.

    `write_checkpoint` writes a temp file and renames it, so a crash can never leave a
    truncated number that parses as a smaller one. That survives a PROCESS crash, where
    the page cache is untouched. It does not survive POWER LOSS: the bytes and the
    rename can both still be in the page cache, so the box comes back to a stale
    checkpoint and -- on a dead RTC, where the clamp is the only thing choosing values
    -- re-issues everything after it.

    BOTH fsyncs are needed, and the directory one is the easier to forget: fsyncing the
    file's contents says nothing about whether the rename that gave it its name reached
    the platter. Spies on the real call and matches by inode rather than by argument,
    so it asserts which OBJECTS were made durable rather than how the code was written.
    The temp file's inode becomes the checkpoint's inode on rename.
    """
    synced = []
    real_fsync = os.fsync

    def spy(fd):
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)

    path = tmp_path / "counter"
    write_checkpoint(path, 209034005)

    assert read_checkpoint(path) == 209034005
    assert path.stat().st_ino in synced, "the checkpoint's bytes were never fsynced"
    assert tmp_path.stat().st_ino in synced, "the rename was never fsynced"
