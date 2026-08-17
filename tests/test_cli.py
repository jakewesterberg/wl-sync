import datetime
import json

import pytest

from wl_sync.backend import FakeBackend
from wl_sync.cli import day_directory, main, probe_boot_id, run

UTC = datetime.timezone.utc


def test_day_directory_uses_the_session_id_format(tmp_path):
    """SyncBoxLogHeader validates session_id as YYYY-MM-DD_NN and wl-preproc consumes
    it, so the directory carries that form rather than a bare date."""
    day = day_directory(tmp_path, datetime.date(2026, 8, 16))
    assert day.name == "2026-08-16_01"


def test_the_same_day_is_reused_by_a_restart(tmp_path):
    first = day_directory(tmp_path, datetime.date(2026, 8, 16))
    first.mkdir(parents=True)
    assert day_directory(tmp_path, datetime.date(2026, 8, 16)) == first


def test_a_short_run_writes_a_clean_segment_and_manifest(tmp_path):
    clock = iter(
        datetime.datetime(2026, 8, 16, 9, 0, second=s, tzinfo=UTC) for s in range(10)
    )
    day = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=lambda: next(clock),
        duration_s=3,
        tick_fn=lambda _seconds: None,
    )
    manifest = json.loads((day / "manifest.json").read_text())
    assert manifest["segments"][0]["closed"] == "clean"
    assert manifest["gaps"] == []
    assert manifest["segments"][0]["barcode_first"] is not None


def test_a_restart_adds_a_segment_and_records_the_gap(tmp_path):
    def at(hour, minute):
        return datetime.datetime(2026, 8, 16, hour, minute, tzinfo=UTC)

    first = iter([at(9, 0), at(9, 0), at(9, 0), at(9, 0)])
    run(tmp_path, FakeBackend(), lambda: next(first), 2, lambda _s: None)
    later = iter([at(9, 30), at(9, 30), at(9, 30), at(9, 30)])
    day = run(tmp_path, FakeBackend(), lambda: next(later), 2, lambda _s: None)

    manifest = json.loads((day / "manifest.json").read_text())
    assert len(manifest["segments"]) == 2
    assert len(manifest["gaps"]) == 1
    # Run 1's scripted clock does not advance, so its second frame is clamped
    # forward: run 1 emits v and v+1. Run 2 starts at v+1800. The manifest measures
    # last-witnessed to first-witnessed: (v+1800) - (v+1) = 1799, not 1800.
    assert manifest["gaps"][0]["seconds"] == 1_799


def test_a_quick_clean_restart_does_not_inflate_the_gap(tmp_path):
    """Task 8 review, Finding 1: next_value used to add CHECKPOINT_INTERVAL_S + 1 to
    EVERY checkpoint, including the exact barcode a clean close had just written -- so
    an ordinary restart seconds later was misreported as a ~61 s gap regardless of how
    little real time had actually passed. The margin now lives only in the periodic
    mid-run checkpoint, so a clean restart's gap must track the real elapsed time."""
    base = datetime.datetime(2026, 8, 16, 9, 0, 0, tzinfo=UTC)
    first = iter([base, base, base, base])
    run(tmp_path, FakeBackend(), lambda: next(first), 2, lambda _s: None)

    five_seconds_later = base + datetime.timedelta(seconds=5)
    later = iter([five_seconds_later] * 4)
    day = run(tmp_path, FakeBackend(), lambda: next(later), 2, lambda _s: None)

    manifest = json.loads((day / "manifest.json").read_text())
    assert len(manifest["gaps"]) == 1
    # Run 1 emits v, v+1 (clamped forward exactly as in the restart test above) and
    # checkpoints v+1 at its clean close. Run 2's clock reads v+5, so
    # next_value(v+5, checkpoint=v+1) = max(v+5, (v+1)+1) = v+5 -- not v+1+61, which is
    # what the pre-fix formula would have produced regardless of the real 5 s gap.
    assert manifest["gaps"][0]["seconds"] == 4


def test_main_requires_out():
    """argparse exits rather than returning, so the absence of --out must be asserted
    as a non-zero SystemExit, not a return code."""
    with pytest.raises(SystemExit) as excinfo:
        main(["record"])
    assert excinfo.value.code != 0


def test_main_runs_end_to_end(tmp_path):
    assert main(["record", "--out", str(tmp_path), "--fake", "--duration", "2"]) == 0
    days = list(tmp_path.glob("2*_01"))
    assert len(days) == 1
    assert (days[0] / "manifest.json").exists()


def test_probe_boot_id_does_not_raise():
    """Task 8 review, Finding 2: whichever branch this machine exercises -- a real
    /proc/sys/kernel/random/boot_id on Linux, or the "" degrade path elsewhere -- it
    must return a string, never raise."""
    assert isinstance(probe_boot_id(), str)
