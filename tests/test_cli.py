import datetime
import json

import pytest

from wl_sync.backend import FakeBackend
from wl_sync.barcode import FRAME_US
from wl_sync.cli import CHECKPOINT_NAME, day_directory, main, probe_boot_id, run
from wl_sync.clock import read_checkpoint
from wl_sync.log import TICK_WRAP_US, BarcodeEmitted, Edge, read_log
from wl_sync.pins import EDGE_PINS
from wl_sync.segment import segment_name

UTC = datetime.timezone.utc


def _clock_from(start, step_s=1):
    """A now_fn advancing step_s per call, unbounded -- run() calls it once at start,
    once per emitted frame, and once at close."""
    state = {"n": 0}

    def now_fn():
        value = start + datetime.timedelta(seconds=step_s * state["n"])
        state["n"] += 1
        return value

    return now_fn


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


def test_barcode_and_edge_ticks_stay_on_one_timebase_across_a_wrap(tmp_path):
    """RP1's counter wraps every TICK_WRAP_US us (71.6 min, ~20x a day). E and W ticks
    are unwrapped by Recorder; B ticks were written raw, so after the first wrap every
    barcode's tick was k*2^32 us adrift of the very edges it exists to align. Invisible
    before this test because FakeBackend's counter never advances unless an edge is
    injected."""
    backend = FakeBackend()
    raw = iter([TICK_WRAP_US - 2_000_000, TICK_WRAP_US - 1_000_000, 0, 1_000_000])

    def tick_fn(_seconds):
        backend.inject_edge(EDGE_PINS[0], 1, next(raw))

    day = run(
        out=tmp_path,
        backend=backend,
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        duration_s=4,
        tick_fn=tick_fn,
    )
    _, records = read_log(day / segment_name(0))
    barcodes = [r.tick_us for r in records if isinstance(r, BarcodeEmitted)]
    edges = [r.tick_us for r in records if isinstance(r, Edge)]

    assert barcodes == sorted(barcodes), "B ticks jumped backwards across the wrap"
    assert edges == sorted(edges)
    assert max(edges) > TICK_WRAP_US, "test did not actually cross a wrap boundary"
    # run() reads now_us() at the top of each iteration, so iteration i+1's barcode
    # tick IS iteration i's injected edge tick. Equality across the wrap is the whole
    # claim: both paths report the same instant as the same number.
    assert barcodes[1:] == edges[: len(barcodes) - 1]


def test_the_barcode_tick_is_read_before_the_frame_is_emitted(tmp_path):
    """A frame takes 200 ms and BarcodeEmitted means the LEAD rising edge, which is
    what Barcode.start_us gives every receiving device. Reading the counter after
    emit_frame() returns put this box's own alignment record a systematic 200 ms behind
    every device it was aligning."""

    class _FrameAdvancesTheCounter(FakeBackend):
        def emit_pulses(self, pin, pulses):
            super().emit_pulses(pin, pulses)
            self._tick += FRAME_US

    day = run(
        out=tmp_path,
        backend=_FrameAdvancesTheCounter(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _seconds: None,
    )
    _, records = read_log(day / segment_name(0))
    ticks = [r.tick_us for r in records if isinstance(r, BarcodeEmitted)]
    assert ticks == [0, FRAME_US], "barcode tick is the frame's END, not its lead edge"


def test_a_dead_coin_cell_degrades_the_record_instead_of_refusing_to_start(tmp_path):
    """Spec Sec.7: on an untrustworthy clock the box RECORDS ANYWAY and marks the
    record -- "refusing to start would stop the first device of the day over a $0.30
    battery". A CM5 with a dead CR2032 reads 2000-01-01; that used to raise out of
    run(), leaving a junk day directory and, under Restart=always, an unbounded crash
    loop recording nothing."""
    day = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2000, 1, 1, 1, 0, tzinfo=UTC)),
        duration_s=3,
        tick_fn=lambda _seconds: None,
    )

    header, records = read_log(day / segment_name(0))
    assert header.clock_trusted is False
    assert header.clock_reason == "clock_before_epoch"
    assert [r.value for r in records if isinstance(r, BarcodeEmitted)] == [0, 1, 2]

    manifest = json.loads((day / "manifest.json").read_text())
    assert manifest["segments"][0]["clock_trusted"] is False
    assert manifest["segments"][0]["barcode_first"] == 0

    # No directory named after the bogus reading. The day comes from the resumed
    # barcode value, which with no checkpoint is the epoch itself.
    assert not (tmp_path / "2000-01-01_01").exists()
    assert day.name == "2020-01-01_01"


def test_a_dead_coin_cell_resumes_from_the_checkpoint_and_rejoins_that_day(tmp_path):
    """The information needed to recover is already in hand: run() reads the checkpoint
    immediately before this point. With one present, the resumed value implies the day
    the box was really recording, so a restart on a dead battery rejoins that directory
    rather than orphaning its segments."""
    healthy = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    run(tmp_path, FakeBackend(), _clock_from(healthy), 2, lambda _s: None)

    day = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(1970, 1, 1, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    assert day.name == "2026-08-16_01"
    manifest = json.loads((day / "manifest.json").read_text())
    assert len(manifest["segments"]) == 2
    assert manifest["segments"][1]["clock_trusted"] is False
    # Resumed at checkpoint + 1, so no value is ever re-issued on a dead clock.
    assert manifest["segments"][1]["barcode_first"] > manifest["segments"][0]["barcode_last"]


def test_the_checkpoint_is_never_behind_the_values_already_emitted(tmp_path):
    """THE invariant the high-water checkpoint exists to hold, checked at every point
    in a long run rather than at one convenient moment.

    The old code counted LOOP ITERATIONS, but an iteration is a 1 s sleep plus a 200 ms
    frame plus an fsync, so 60 iterations span well over 60 s and the emitted value
    outran the mark. A crash in that window, restarting on a stalled clock, resumed at
    checkpoint + 1 and re-issued barcode values already used -- two moments carrying one
    identity. build_manifest cannot see it either: first - last goes negative, fails the
    `> 1` test, and no gap is reported. Nothing in the suite caught it, which is why
    this test asserts the invariant continuously.
    """
    checkpoint_path = tmp_path / CHECKPOINT_NAME
    segment = tmp_path / "2026-08-16_01" / segment_name(0)
    breaches = []

    def last_emitted():
        values = [
            int(line.split(",")[2])
            for line in segment.read_text().splitlines()
            if line.startswith("B,")
        ]
        return values[-1] if values else None

    def tick_fn(_seconds):
        emitted = last_emitted()
        on_disk = read_checkpoint(checkpoint_path)
        if emitted is not None and (on_disk is None or on_disk < emitted):
            breaches.append((emitted, on_disk))

    # Two seconds of wall clock per iteration: the loop's real period exceeds 1 s, which
    # is exactly what makes an iteration count and a value count diverge.
    run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC), step_s=2),
        duration_s=130,
        tick_fn=tick_fn,
    )
    assert breaches == [], (
        f"checkpoint fell behind the emitted value {len(breaches)} times; "
        f"first breach: emitted={breaches[0][0]} checkpoint={breaches[0][1]}"
        if breaches
        else ""
    )


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
