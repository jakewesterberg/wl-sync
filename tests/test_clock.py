import datetime

import pytest

from wl_sync.clock import (
    BARCODE_EPOCH,
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
    from wl_sync.clock import _MAX_BARCODE

    # Compute a datetime that would produce a value > _MAX_BARCODE
    seconds_over = _MAX_BARCODE + 1
    far_future = BARCODE_EPOCH + datetime.timedelta(seconds=seconds_over)
    with pytest.raises(ValueError, match="32-bit range"):
        value_from_clock(far_future)


def test_read_checkpoint_returns_none_for_out_of_range_value(tmp_path):
    """An out-of-range checkpoint is corrupt by definition and must degrade."""
    from wl_sync.clock import _MAX_BARCODE

    path = tmp_path / "checkpoint"
    path.write_text(f"{_MAX_BARCODE + 1000}\n")
    assert read_checkpoint(path) is None


def test_next_value_raises_when_clamp_exceeds_ceiling():
    """When the clamp would exceed the ceiling, it must raise rather than propagate the error."""
    from wl_sync.clock import _MAX_BARCODE

    now = at(2026, 8, 16, 12, 0)
    # A checkpoint already at the ceiling: checkpoint + 1 overflows it.
    stale_checkpoint = _MAX_BARCODE
    with pytest.raises(ValueError, match="32-bit range"):
        next_value(now, checkpoint=stale_checkpoint)
