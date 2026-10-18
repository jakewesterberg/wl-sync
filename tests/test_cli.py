import datetime
import json
import logging
import signal
import threading
from pathlib import Path

import pytest

from wl_sync import cli
from wl_sync.backend import FakeBackend
from wl_sync.barcode import FRAME_US, IDLE_MIN_US, N_BITS
from wl_sync.cli import (
    CHECKPOINT_NAME,
    _ValueSource,
    main,
    probe_boot_id,
    run,
)
from wl_sync.clock import (
    BARCODE_EPOCH,
    MAX_BARCODE,
    read_checkpoint,
    value_from_clock,
    write_checkpoint,
)
from wl_sync.log import TICK_WRAP_US, SyncBoxLogHeader, BarcodeEmitted, Edge, read_log
from wl_sync.manifest import build_manifest
from wl_sync.pins import EDGE_PINS
from wl_sync.segment import SegmentWriter, segment_name
from wl_sync.sessions import (
    OpenSession,
    SessionId,
    unassigned_dir,
    write_open,
)

UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def _host_ntp_is_unknown(monkeypatch):
    """run() asks the HOST whether NTP has synced, through timedatectl. A laptop has no
    timedatectl and answers None ("unknown", which evaluate_clock trusts), but a Linux CI
    runner has one and reports whatever its own clock is doing -- so three of these tests
    failed in CI on 2026-10-18 whenever the runner had not yet synced, and passed on the
    next push. Every test here is about the run loop, not the host, so the probe answers
    "unknown" unless a test says otherwise. evaluate_clock's NTP handling is tested
    directly in tests/test_clock.py."""
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda: None)


def _clock_from(start, step_s=1):
    """A now_fn advancing step_s per call, unbounded -- run() calls it once at start,
    once per emitted frame, and once at close."""
    state = {"n": 0}

    def now_fn():
        value = start + datetime.timedelta(seconds=step_s * state["n"])
        state["n"] += 1
        return value

    return now_fn


def test_with_no_session_open_the_box_records_between_sessions(tmp_path):
    """The box records all day, and most of the day no animal is in the rig. Those
    segments belong to no visit, so they go under `unassigned/<date>/syncbox` and carry
    the day's `_00` id -- the id `next_id` never mints, so nothing can mistake them for
    a real session. The old `<date>_01` folder must not appear: it claimed a visit that
    did not happen."""
    folder = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    assert folder == tmp_path / "unassigned" / "2026-08-16" / "syncbox"
    header, _ = read_log(folder / segment_name(0))
    assert header.session_id == "2026-08-16_00"
    assert not (tmp_path / "2026-08-16_01").exists()


def test_a_restart_resumes_into_the_open_session(tmp_path):
    """A restart is not a visit (spec 3.1): the state file says a session is open, so a
    restarted service keeps recording into that session's folder as a new segment, and
    the header names the session -- not the between-sessions stand-in -- whatever the
    box's clock says the date is."""
    session_id = SessionId.parse("2026-08-16_02")
    write_open(
        tmp_path,
        OpenSession(session_id, datetime.datetime(2026, 8, 16, 8, 0, tzinfo=UTC), "r1"),
    )
    folder = tmp_path / "2026-08-16_02" / "syncbox"
    folder.mkdir(parents=True)
    previous = SyncBoxLogHeader(
        schema_version=1,
        session_id="2026-08-16_02",
        rig=tmp_path.name,
        boot_id="",
        written_at=datetime.datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        gpio_map=cli.GPIO_MAP,
        segment_index=0,
        clock_trusted=True,
        clock_reason="",
    )
    SegmentWriter(folder / segment_name(0), previous).close(
        datetime.datetime(2026, 8, 16, 8, 30, tzinfo=UTC)
    )

    result = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    assert result == folder
    header, _ = read_log(folder / segment_name(1))
    assert header.session_id == "2026-08-16_02"
    assert header.segment_index == 1
    assert not (tmp_path / "unassigned").exists()


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
    """Task 8 review, Finding 1: next_value used to add a whole checkpoint interval to
    EVERY checkpoint, including the exact barcode a clean close had just written -- so
    an ordinary restart seconds later was misreported as a ~61 s gap regardless of how
    little real time had actually passed. No margin is applied anywhere now, so a
    restart's gap must track the real elapsed time."""
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
    # run() reads now_us() immediately before emitting each frame, and nothing between
    # the injected edge and that read moves the fake's counter, so iteration i+1's
    # barcode tick IS iteration i's injected edge tick. Equality across the wrap is the
    # whole claim: both paths report the same instant as the same number.
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
    assert not list(tmp_path.glob("**/2000-01-01*"))
    assert day == tmp_path / "unassigned" / "2020-01-01" / "syncbox"


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
    assert day == unassigned_dir(tmp_path, datetime.date(2026, 8, 16))
    manifest = json.loads((day / "manifest.json").read_text())
    assert len(manifest["segments"]) == 2
    assert manifest["segments"][1]["clock_trusted"] is False
    # Resumed at checkpoint + 1, so no value is ever re-issued on a dead clock.
    assert manifest["segments"][1]["barcode_first"] > manifest["segments"][0]["barcode_last"]


def test_a_maximal_checkpoint_on_a_dead_clock_does_not_crash_loop(tmp_path):
    """Breakage 4. The degraded path resumes at `checkpoint + 1` with no ceiling check,
    so a checkpoint sitting exactly at MAX_BARCODE -- corruption that still parses, or
    the year 2156 -- yields 2**32, which barcode.encode rejects. That is the C2 crash
    loop again in a new guise: the box refuses to start, on a dead battery, forever.
    read_checkpoint already degrades anything ABOVE the ceiling to None, so the ceiling
    itself is the one value that reaches this code."""
    write_checkpoint(tmp_path / CHECKPOINT_NAME, MAX_BARCODE)

    day = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2000, 1, 1, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    header, records = read_log(day / segment_name(0))
    assert header.clock_trusted is False
    assert header.clock_reason == "clock_before_epoch,checkpoint_exhausted"
    # Started over at the epoch rather than raising. Uniqueness is already unrecoverable
    # once the 32-bit space is spent; recording under a loud marker beats not recording.
    assert [r.value for r in records if isinstance(r, BarcodeEmitted)] == [0, 1]


def test_only_a_pre_epoch_reading_degrades(tmp_path):
    """The degradation is deliberately narrow. A naive datetime silently means "local
    time" and would shift every barcode -- that is a caller bug, not a dead battery, and
    it must keep raising with its own message rather than being swallowed by the
    dead-RTC path."""
    with pytest.raises(ValueError, match="timezone-aware"):
        run(
            tmp_path,
            FakeBackend(),
            lambda: datetime.datetime(2026, 8, 16, 9, 0),  # no tzinfo
            1,
            lambda _s: None,
        )


def test_the_checkpoint_equals_the_last_value_emitted(tmp_path):
    """THE invariant, and it is now an EQUALITY rather than a bound -- checked after
    every iteration of a long run rather than at one convenient moment.

    Two earlier designs asserted only `checkpoint >= last emitted`, which is a claim
    about the loop's period and is exactly as strong as the argument behind it. Both
    arguments were wrong. Counting loop ITERATIONS was wrong because an iteration is a
    1 s sleep plus a 200 ms frame plus an fsync, so the emitted value outran a mark
    written every sixtieth one. Writing a high-water mark was wrong because the excess
    ratchets across restarts (see the crash-loop test below). Storing the exact value
    every second makes the invariant exact and leaves nothing to argue about.

    Two seconds of wall clock per iteration, because a loop period over 1 s is what made
    an iteration count and a value count diverge in the first place.
    """
    checkpoint_path = tmp_path / CHECKPOINT_NAME
    segment = unassigned_dir(tmp_path, datetime.date(2026, 8, 16)) / segment_name(0)
    mismatches = []

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
        if on_disk != emitted:
            mismatches.append((emitted, on_disk))

    run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC), step_s=2),
        duration_s=130,
        tick_fn=tick_fn,
    )
    assert not mismatches, (
        f"checkpoint disagreed with the last emitted value {len(mismatches)} times; "
        f"first: emitted={mismatches[0][0]} checkpoint={mismatches[0][1]}"
    )


def test_a_crash_loop_never_ratchets_the_counter_ahead_of_the_clock(tmp_path, caplog):
    """THE REGRESSION THIS DESIGN EXISTS TO PREVENT, and it only shows under a LOOP.

    A startup high-water write left every run's mark a whole 60 s interval ahead of what
    it had actually emitted. One crash costs one interval and looks survivable; the next
    restart resumes past that mark and leaves its own, so under Restart=always it
    compounds without bound. Measured before the fix: 25 crash restarts over 75 s of real
    time on a PERFECT clock put the counter 1449 s ahead of wall time, with 24 spurious
    "wall clock is not moving forwards" warnings and clock_trusted: false from the third
    restart onwards. That is precisely the runaway spec Sec.4 records as fatal -- it
    destroys the premise that a barcode IS seconds since 2020 -- and it contradicts
    deploy/README.md's promise that an ordinary crash restart does not set the flag.

    All three symptoms are asserted, because all three were user-visible.
    """
    base = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    restarts, seconds_per_run = 25, 3

    with caplog.at_level(logging.WARNING, logger="wl_sync.cli"):
        for restart in range(restarts):
            started = base + datetime.timedelta(seconds=restart * seconds_per_run)
            with pytest.raises(_Crash):
                run(
                    tmp_path,
                    FakeBackend(),
                    _clock_from(started),
                    None,
                    _crash_after(seconds_per_run),
                )
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]

    wall_now = base + datetime.timedelta(seconds=restarts * seconds_per_run)
    checkpoint = read_checkpoint(tmp_path / CHECKPOINT_NAME)
    drift = checkpoint - value_from_clock(wall_now)
    assert drift <= 0, f"counter ran {drift} s ahead of a perfectly healthy wall clock"

    # Rebuilt here rather than read from disk: run() writes the manifest at its START,
    # so the file left behind is missing the final crashed segment. This is what restart
    # 26 would see.
    manifest = build_manifest(unassigned_dir(tmp_path, base.date()))
    assert len(manifest["segments"]) == restarts
    untrusted = [s["file"] for s in manifest["segments"] if not s["clock_trusted"]]
    assert untrusted == [], f"a healthy clock was distrusted in {untrusted}"
    assert warnings == [], f"{len(warnings)} spurious clock warnings on a healthy clock"


class _Crash(Exception):
    """Abandons run() mid-loop, leaving the segment trailer-less exactly as a real
    crash does."""


def _crash_after(iterations):
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        if state["n"] >= iterations:
            raise _Crash

    return tick_fn


def test_a_crash_restart_on_a_healthy_clock_is_not_blamed_on_the_battery(tmp_path):
    """End-to-end shape of the false positive: a perfect clock, a crash 70 s in, a
    restart 1 s later. deploy/README.md tells the operator clock_trusted: false most
    often means a missing CR2032, so a crashing process used to send someone to check a
    battery -- and spec Sec.7's purpose for the field is to let wl-preproc distrust gap
    arithmetic across THAT boundary, which is exactly where the arithmetic matters."""
    base = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    with pytest.raises(_Crash):
        run(tmp_path, FakeBackend(), _clock_from(base), None, _crash_after(70))

    restart = base + datetime.timedelta(seconds=71)
    day = run(tmp_path, FakeBackend(), _clock_from(restart), 2, lambda _s: None)

    header, _ = read_log(day / segment_name(1))
    assert header.clock_trusted is True
    assert header.clock_reason == ""
    assert json.loads((day / "manifest.json").read_text())["segments"][0]["closed"] == "crash"


def test_a_crash_never_lets_the_restart_re_issue_a_barcode(tmp_path):
    """The uniqueness guarantee under the conditions that actually threaten it: a crash
    mid-run AND a clock that does not move afterwards. Every value the restart emits
    must be strictly greater than every value the crashed segment already used.

    Two seconds of clock per iteration, because that is what makes the iteration count
    and the value count diverge -- with a 1 s iteration the two agree and even the old
    code happens to be safe."""
    base = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    with pytest.raises(_Crash):
        run(tmp_path, FakeBackend(), _clock_from(base, step_s=2), None, _crash_after(95))

    stalled = base + datetime.timedelta(seconds=30)  # clock fell backwards and stopped
    day = run(tmp_path, FakeBackend(), lambda: stalled, 5, lambda _s: None)

    def values(index):
        _, records = read_log(day / segment_name(index))
        return [r.value for r in records if isinstance(r, BarcodeEmitted)]

    assert min(values(1)) > max(values(0))


def _wire_values(backend):
    """Barcode values that actually reached the pin, read back from the pulse train.

    Deliberately NOT read from the log. The hazard under test is a frame that went out
    and was never recorded, so the log is precisely the witness that goes missing; the
    wire is the only account of it that survives the window.
    """
    values = []
    for _pin, pulses in backend.emitted:
        value = 0
        for level, _duration in pulses[2 : 2 + N_BITS]:
            value = (value << 1) | level
        values.append(value)
    return values


class _CrashOnTheWire(FakeBackend):
    """Puts the frame fully onto the pin, then dies.

    Models the window a real crash opens between a value becoming irreversible -- it is
    on the wire, every receiving device has it -- and the checkpoint recording that it
    was spent. `_crash_after` cannot reach this window: it raises from tick_fn, which
    runs after the checkpoint is already written.
    """

    def __init__(self, crash_on: int) -> None:
        super().__init__()
        self._crash_on = crash_on
        self._frames = 0

    def emit_pulses(self, pin, pulses):
        super().emit_pulses(pin, pulses)
        self._frames += 1
        if self._frames >= self._crash_on:
            raise _Crash


def test_a_frame_on_the_wire_is_never_re_issued_after_a_crash(tmp_path):
    """The emitted-but-unmarked window: uniqueness must survive a crash landing between
    the frame reaching the pin and the checkpoint recording that it did.

    A repeated barcode is two different moments carrying one identity, which is the one
    thing the value must never be -- so the test asserts on the wire, across both runs,
    rather than on either run's log.
    """
    base = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    crashed = _CrashOnTheWire(crash_on=5)
    with pytest.raises(_Crash):
        run(tmp_path, crashed, _clock_from(base), None, lambda _s: None)

    # The clock stops dead, so the clamp -- not the clock -- chooses the restart value.
    restarted = FakeBackend()
    run(tmp_path, restarted, lambda: base, 5, lambda _s: None)

    repeated = sorted(set(_wire_values(crashed)) & set(_wire_values(restarted)))
    assert not repeated, f"re-issued barcode value(s) {repeated}"


def test_the_value_source_alone_never_repeats_a_value(tmp_path):
    """Uniqueness has to hold where the value is CHOSEN, not where it is emitted.

    The checkpoint can only record a decision that has already been made, so once the
    value is marked before it goes to the pin, nothing downstream may alter it -- a
    second clamp inside the emitter would silently make the checkpoint disagree with
    the wire, which is the same defect one line further down.

    The failing shape is a clock that jumps forward and then stalls: the source's floor
    advances by one per call while the clock reading does not, so a reading that already
    won once wins again.
    """
    readings = iter(
        [
            BARCODE_EPOCH + datetime.timedelta(seconds=1000),
            BARCODE_EPOCH + datetime.timedelta(seconds=1100),
            BARCODE_EPOCH + datetime.timedelta(seconds=1100),
        ]
    )
    source = _ValueSource(lambda: next(readings), first_value=1000)

    values = [source(), source(), source()]

    assert len(set(values)) == len(values), f"repeated a value: {values}"


def test_the_value_source_refuses_a_floor_past_the_ceiling(tmp_path):
    """The ceiling guard has to cover the FLOOR branch, not just the clock branch.

    `value_from_clock` raises above the ceiling and `__call__` catches it, so a clock
    reading past 2^32 degrades to the floor. The floor itself had no such guard and
    walked into `barcode.encode`, which raises the identical error from a place that
    says nothing about where the bad value came from. Reachable without waiting for
    2156: a checkpoint corrupted to the ceiling still parses, and one second later the
    floor is past it.

    Asserted on the source rather than through `run()` deliberately -- both paths raise
    the same ValueError, so only the unit contract "never hands out a value that cannot
    be encoded" distinguishes them.
    """
    source = _ValueSource(lambda: BARCODE_EPOCH, first_value=MAX_BARCODE)

    assert source() == MAX_BARCODE  # the last encodable value is still issued

    with pytest.raises(ValueError, match="out of 32-bit range"):
        source()


class _CrashBeforeTheWire(FakeBackend):
    """Dies after the checkpoint marks the value and before it reaches the pin.

    The window mark-before-emit deliberately OPENS, in exchange for closing the one
    where a value reaches the wire unmarked. Its cost is a burned value: marked spent,
    never emitted.
    """

    def emit_pulses(self, pin, pulses):
        raise _Crash


def test_burning_a_value_per_restart_does_not_outrun_the_clock(tmp_path, caplog):
    """A REGRESSION GUARD ON THE FIX ITSELF, not a test that discriminates against the
    old code. Run against the pre-fix sources it raises TypeError on `None - int`,
    because the old loop crashes inside emit_pulses before it ever writes a checkpoint
    -- an artifact of there being nothing to measure, not evidence of a ratchet. Read
    its failure there as "not applicable", not as "the old code was worse".

    It exists because the previous attempt at this mechanism died exactly here. A
    startup high-water write burned a whole 60 s interval per restart REGARDLESS of
    cadence, so it ran away even at the shipped RestartSec=1: 25 restarts over 75 s put
    the counter 1449 s ahead of a healthy clock. Mark-before-emit also burns on every
    restart, so the question is not whether it burns but at what RATE.

    One value per restart, against a clock that advances one per second, means any
    cadence of a second or slower self-corrects rather than compounds -- which is what
    deploy/wl-sync.service pins with RestartSec=1. Measured here at that cadence: -1 s,
    i.e. still behind the clock after 25 crashes. Faster than one restart per second it
    does creep, one value at a time; the unit forbids that cadence, and the failure is
    rate-bounded rather than unbounded, which is the difference from the design that
    was rejected.

    The absent warnings are asserted for the same reason they were last time: spurious
    "clock is not moving forwards" lines were one of the three user-visible symptoms of
    the runaway, and deploy/README.md promises an ordinary crash restart is quiet.
    """
    base = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    restarts = 25

    with caplog.at_level(logging.WARNING, logger="wl_sync.cli"):
        for restart in range(restarts):
            started = base + datetime.timedelta(seconds=restart)
            with pytest.raises(_Crash):
                run(tmp_path, _CrashBeforeTheWire(), lambda s=started: s, None, lambda _s: None)

    wall_now = base + datetime.timedelta(seconds=restarts)
    checkpoint = read_checkpoint(tmp_path / CHECKPOINT_NAME)
    drift = checkpoint - value_from_clock(wall_now)

    assert drift <= 0, f"counter ran {drift} s ahead of a perfectly healthy wall clock"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_main_requires_out():
    """argparse exits rather than returning, so the absence of --out must be asserted
    as a non-zero SystemExit, not a return code."""
    with pytest.raises(SystemExit) as excinfo:
        main(["record"])
    assert excinfo.value.code != 0


def test_main_runs_end_to_end(tmp_path):
    assert main(["record", "--out", str(tmp_path), "--fake", "--duration", "2"]) == 0
    folders = list(tmp_path.glob("unassigned/*/syncbox"))
    assert len(folders) == 1
    assert (folders[0] / "manifest.json").exists()


def test_a_start_says_what_it_started(tmp_path, caplog):
    """journald is the only status surface this box has -- spec Sec.7 calls it "the
    honest status surface" and Sec.10 records that no software-controllable panel
    indicator exists. There was no logging in cli.py at all, so only systemd's own
    start/stop lines ever appeared and `journalctl -u wl-sync -f` showed nothing about
    what the box was actually doing."""
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        day = run(
            out=tmp_path,
            backend=FakeBackend(),
            now_fn=_clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            duration_s=2,
            tick_fn=lambda _seconds: None,
        )
    message = caplog.messages[0]
    assert str(day / segment_name(0)) in message
    assert "clock_trusted=True" in message
    assert "clock_reason" in message and "boot_id" in message
    assert str(value_from_clock(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC))) in message


def test_a_clock_that_stops_moving_forwards_is_reported(tmp_path, caplog):
    """Spec Sec.7 names "time moved backwards" as a trust condition, and nothing
    recorded it mid-run: the header is written at startup and must not be rewritten, so
    a journal line is the only honest place for it. Edge-triggered -- a stalled clock
    binds every second, and 86,400 identical lines a day would bury the journal."""
    frozen = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    with caplog.at_level(logging.WARNING, logger="wl_sync.cli"):
        run(tmp_path, FakeBackend(), lambda: frozen, 5, lambda _s: None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "the clamp report must be edge-triggered, not per frame"
    assert "not moving forwards" in warnings[0].getMessage()


def test_a_healthy_clock_reports_nothing(tmp_path, caplog):
    """The counterpart that keeps the warning meaningful. The first frame of a healthy
    run reads the same second run() started in, so a naive `<=` test would cry wolf on
    every single start."""
    with caplog.at_level(logging.WARNING, logger="wl_sync.cli"):
        run(
            tmp_path,
            FakeBackend(),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            5,
            lambda _s: None,
        )
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def _unit_sections():
    """The shipped unit, parsed into {section: [line, ...]}. Comments dropped."""
    unit = Path(__file__).resolve().parent.parent / "deploy" / "wl-sync.service"
    sections, current = {}, None
    for raw in unit.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = []
        else:
            sections[current].append(line)
    return sections


def test_the_shipped_unit_starts_a_mode_the_cli_actually_accepts(tmp_path):
    """ExecStart omitted --fake, so main() printed "only --fake is available..." and
    exited 2 immediately. Following deploy/README.md's literal `systemctl enable --now
    wl-sync` left the unit `failed` within ~5 s -- the shipped deployment did not work
    at all. Run the unit's own argv rather than asserting on its text, so the two
    cannot drift apart."""
    exec_start = [
        line for line in _unit_sections()["Service"] if line.startswith("ExecStart=")
    ]
    assert len(exec_start) == 1
    argv = exec_start[0].split("=", 1)[1].split()[1:]  # drop the binary path
    assert "--fake" in argv, "the unit must ship in fake mode until bench acceptance"

    argv[argv.index("--out") + 1] = str(tmp_path)
    assert main([*argv, "--duration", "1"]) == 0


def test_the_start_rate_limit_key_is_in_the_section_systemd_reads_it_from():
    """StartLimitIntervalSec is a [Unit] key. Under [Service] modern systemd logs
    "Unknown key name ... ignoring" and applies the default 5 starts / 10 s anyway --
    so the line was inert and a fast crash loop still ended in `failed`, which is
    exactly the unattended scenario this box must survive."""
    sections = _unit_sections()
    assert "StartLimitIntervalSec=0" in sections["Unit"]
    assert not any(
        line.startswith("StartLimitIntervalSec") for line in sections["Service"]
    )


def test_probe_boot_id_does_not_raise():
    """Task 8 review, Finding 2: whichever branch this machine exercises -- a real
    /proc/sys/kernel/random/boot_id on Linux, or the "" degrade path elsewhere -- it
    must return a string, never raise."""
    assert isinstance(probe_boot_id(), str)


def test_run_restores_the_signal_handlers_it_installed(tmp_path):
    """`run()` is a LIBRARY CALL and `main()` is the entry point, so the handlers it
    installs belong to the call, not to the process.

    It installed SIGINT and SIGTERM handlers and never put back what was there. The
    caller's own shutdown handling was silently replaced by a flag object that stays
    behind after run() returns and does nothing but swallow the signal -- so the second
    call into this library leaves a process that cannot be stopped by the handler its
    owner installed. Nothing in this package notices, because main() is the only caller
    today and it wants exactly these handlers.
    """
    installed = []

    def sentinel(_signum, _frame):
        installed.append("mine")

    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    for received in previous:
        signal.signal(received, sentinel)
    try:
        run(tmp_path, FakeBackend(), _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)), 1, lambda _s: None)
        still_mine = {s: signal.getsignal(s) for s in previous}
        assert still_mine == {s: sentinel for s in previous}, (
            f"run() kept its own handlers: {still_mine}"
        )
    finally:
        for received, handler in previous.items():
            signal.signal(received, handler)


class _CountsHealthChecks(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.health_checks = 0

    def check_health(self) -> None:
        self.health_checks += 1


def test_the_run_loop_checks_the_backends_health_every_iteration(tmp_path):
    """Every RP1 capture path fails by going QUIET: a dead pump thread, an overflowed
    kernel buffer, a PIO FIFO that dropped words. `check_health()` is what turns each
    into a complaint, its docstring says the run loop must call it once per iteration --
    and nothing called it, so a day could be recorded with half the rig's signals absent
    and a clean trailer on the end."""
    backend = _CountsHealthChecks()
    run(
        tmp_path,
        backend,
        _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        5,
        lambda _s: None,
    )
    # Once per iteration, and once more after capture stops (see the end-of-run test).
    assert backend.health_checks == 5 + 1


class _ScriptedHealth(FakeBackend):
    """check_health() raises each scripted message in turn; None is a healthy call."""

    def __init__(self, script) -> None:
        super().__init__()
        self._script = iter(script)

    def check_health(self) -> None:
        message = next(self._script, None)
        if message is not None:
            raise RuntimeError(message)


def test_a_failed_capture_path_is_logged_and_recording_continues(tmp_path, caplog):
    """Stopping the box over a degraded capture path loses more than it saves: the other
    paths, the barcodes every other device aligns on, the rest of the day. So the failure
    goes to the journal -- this box's only status surface -- and the loop carries on."""
    failure = "edge capture: 3 edge event(s) lost: the kernel's event buffer overflowed"
    with caplog.at_level(logging.ERROR, logger="wl_sync.cli"):
        day = run(
            tmp_path,
            _ScriptedHealth([failure]),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            3,
            lambda _s: None,
        )

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(failure in message for message in errors), errors
    _, records = read_log(day / segment_name(0))
    assert len([r for r in records if isinstance(r, BarcodeEmitted)]) == 3
    assert json.loads((day / "manifest.json").read_text())["segments"][0]["closed"] == "clean"


def test_a_persisting_capture_failure_is_logged_once_per_episode(tmp_path, caplog):
    """A dead capture thread never comes back, so check_health() raises the same thing
    every second -- 86,400 identical lines a day would bury the journal, the reason the
    stalled-clock warning is edge-triggered too. A recovery ends the episode, and the
    same failure after it is news again."""
    script = ["A", "A", "A", None, "A", "B", "B"]
    with caplog.at_level(logging.ERROR, logger="wl_sync.cli"):
        run(
            tmp_path,
            _ScriptedHealth(script),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            len(script),
            lambda _s: None,
        )
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 3, errors
    assert [m.split(": ")[-1] for m in errors] == ["A", "A", "B"]


class _DeliversFromACaptureThread(FakeBackend):
    """Delivers its edges from its own thread, the way Rp1Backend's edge pump does."""

    def start_edge_capture(self, pins, on_edge):
        super().start_edge_capture(pins, on_edge)
        thread = threading.Thread(
            target=lambda: [on_edge(pins[0], level, 10 + level) for level in (1, 0, 1)],
            name="capture",
        )
        thread.start()
        thread.join()


def test_no_capture_thread_ever_writes_the_segment(tmp_path, monkeypatch):
    """SegmentWriter is not thread-safe, and its flush() fsyncs. A capture thread that
    writes into it either races the emit loop -- measured: 313 of 2000 record_count
    increments lost, so the trailer disagrees with the records above it -- or, under a
    lock, blocks on the disk and stops draining an 8-deep PIO FIFO. The run loop has to
    give the segment a single owner thread, which is QueuedSink's job."""
    writers = []
    original = SegmentWriter.write

    def spy(self, record):
        writers.append((threading.current_thread().name, record))
        original(self, record)

    monkeypatch.setattr(SegmentWriter, "write", spy)
    day = run(
        tmp_path,
        _DeliversFromACaptureThread(),
        _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        2,
        lambda _s: None,
    )

    assert [name for name, r in writers if isinstance(r, Edge)] != []
    assert "capture" not in {name for name, _ in writers}
    _, records = read_log(day / segment_name(0))
    assert [r.level for r in records if isinstance(r, Edge)] == [1, 0, 1]


class _DeliversWhileClosing(FakeBackend):
    """Stopping capture delivers what was still in flight, as a final FIFO drain does."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.inject_edge(EDGE_PINS[0], 1, 999)
        self.closed = True


def test_capture_stops_before_the_segment_closes(tmp_path):
    """Order matters at the end of a run. Close the segment while capture is still live
    and its last records hit a closed sink -- refused, so the tail of every run is
    lost, which is where the day's last trials are. Capture stops first, then the
    segment is closed behind everything it delivered."""
    backend = _DeliversWhileClosing()
    day = run(
        tmp_path,
        backend,
        _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        2,
        lambda _s: None,
    )
    assert backend.closed
    _, records = read_log(day / segment_name(0))
    assert [r.tick_us for r in records if isinstance(r, Edge)] == [999]
    assert json.loads((day / "manifest.json").read_text())["segments"][0]["closed"] == "clean"


class _Monotonic:
    """A fake monotonic clock, with a wall clock that runs with it.

    `jitter` is added to each sleep in turn, because a real sleep wakes late, never early.
    """

    def __init__(self, start=100.0, wall_at_start=None, jitter=()):
        self.now = start
        self._start = start
        self._wall = wall_at_start or datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
        self._jitter = list(jitter)
        self._sleeps = 0

    def __call__(self) -> float:
        return self.now

    def wall(self):
        return self._wall + datetime.timedelta(seconds=self.now - self._start)

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"asked to sleep {seconds} s"
        late = self._jitter[self._sleeps % len(self._jitter)] if self._jitter else 0.0
        self._sleeps += 1
        self.now += seconds + late


class _FramesTakeRealTime(FakeBackend):
    """A frame occupies the pin for real time, as Rp1Backend.emit_pulses does."""

    def __init__(self, clock: _Monotonic, frame_s) -> None:
        super().__init__()
        self._clock = clock
        self._frame_s = frame_s if isinstance(frame_s, list) else None
        self._fixed_s = None if isinstance(frame_s, list) else frame_s
        self.frame_starts: list[float] = []
        self.frame_ends: list[float] = []

    def emit_pulses(self, pin, pulses):
        super().emit_pulses(pin, pulses)
        self.frame_starts.append(self._clock.now)
        index = len(self.frame_starts) - 1
        self._clock.now += self._fixed_s if self._frame_s is None else self._frame_s[index]
        self.frame_ends.append(self._clock.now)


def _paced(tmp_path, clock, backend, frames):
    run(tmp_path, backend, clock.wall, frames, clock.sleep, mono_fn=clock)
    return backend


def test_frames_start_once_per_second_however_long_a_frame_takes(tmp_path):
    """A frame holds the pin for 200 ms, and the loop then slept a fixed second after
    it, so on hardware the box ran at 1.2 s per iteration while every bound it states
    is per second: the crash-loss bound, the checkpoint's staleness, the fsync window.
    FakeBackend emits instantly, which is why no test saw it. The first frame goes out
    at once; every frame after it lands on the next wall-clock half second."""
    clock = _Monotonic()
    backend = _paced(tmp_path, clock, _FramesTakeRealTime(clock, FRAME_US / 1e6), 5)
    assert backend.frame_starts == pytest.approx([100.0, 101.5, 102.5, 103.5, 104.5])


def test_an_overrun_skips_to_the_next_second_rather_than_bursting(tmp_path):
    """An iteration that overruns a second boundary -- a slow fsync, a stalled disk --
    must not be repaid with frames emitted back to back. Two frames inside one decoder
    window would be indistinguishable from a corrupted one."""
    clock = _Monotonic()
    backend = _paced(tmp_path, clock, _FramesTakeRealTime(clock, 1.3), 3)
    assert backend.frame_starts == pytest.approx([100.0, 102.5, 104.5])


def test_a_late_frame_still_leaves_the_decoder_its_idle(tmp_path):
    """A frame found by the decoder needs IDLE_MIN_US of LOW before its lead edge. Pacing
    to whole seconds handled a frame that crossed a boundary, but one delayed WITHIN its
    second -- two fsyncs in write_checkpoint, say -- was followed by the next almost at
    once, and that next frame could not decode."""
    clock = _Monotonic()
    backend = _paced(tmp_path, clock, _FramesTakeRealTime(clock, [0.2, 0.95, 0.2, 0.2]), 4)
    idles = [
        later - earlier
        for earlier, later in zip(backend.frame_ends, backend.frame_starts[1:])
    ]
    assert min(idles) >= IDLE_MIN_US / 1e6, idles


def test_a_healthy_clock_never_trips_the_clamp_however_the_run_is_phased(tmp_path, caplog):
    """The barcode value is the wall second a frame reads. An exact one-second period
    that happens to sit on a second boundary straddles it with ordinary wake-up
    jitter, reads the same second twice, and the clamp reports "not moving forwards"
    on a healthy clock, for the whole run. The 1.2 s period never could; a period
    of exactly 1 s has to be placed away from the boundary, mid-second."""
    clock = _Monotonic(
        wall_at_start=datetime.datetime(2026, 8, 16, 9, 0, 0, 999_950, tzinfo=UTC),
        jitter=(0.0, 0.000_3),
    )
    with caplog.at_level(logging.WARNING, logger="wl_sync.cli"):
        _paced(tmp_path, clock, _FramesTakeRealTime(clock, FRAME_US / 1e6), 60)
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def _hardware_stand_in(monkeypatch):
    """Replaces the RP1 constructor, which needs libpiolib, and records its argument."""
    chips = []

    def hardware(chip_path):
        chips.append(chip_path)
        return FakeBackend()

    monkeypatch.setattr(cli, "_hardware_backend", hardware, raising=False)
    return chips


def test_without_fake_the_box_records_on_the_rp1_backend(tmp_path, monkeypatch):
    """main() refused anything but --fake, "until the RP1 backend (Task 5b) lands". The
    backend landed and stayed unreachable: the one mode that records the rig was the
    one the entry point would not start."""
    chips = _hardware_stand_in(monkeypatch)
    assert main(["record", "--out", str(tmp_path), "--duration", "1"]) == 0
    assert chips == ["/dev/gpiochip0"]


def test_the_gpio_chip_is_chosen_on_the_command_line(tmp_path, monkeypatch):
    """Which chardev carries RP1's bank has moved between releases, and bench day
    confirms it by label. That is a fact about the rig, so it is set where the rig is
    configured -- the unit's argv -- rather than by editing code on the box."""
    chips = _hardware_stand_in(monkeypatch)
    main(["record", "--out", str(tmp_path), "--duration", "1", "--gpiochip", "/dev/gpiochip4"])
    assert chips == ["/dev/gpiochip4"]


class _DegradedWithACount(FakeBackend):
    """Raises the same KIND of failure each second, with a count that keeps changing,
    the way a kernel buffer that keeps overflowing does."""

    def __init__(self, kinds_by_call) -> None:
        super().__init__()
        self._calls = iter(kinds_by_call)
        self._n = 0

    def check_health(self) -> None:
        from wl_sync.backend import CaptureDegraded

        kinds = next(self._calls, None)
        self._n += 1
        if kinds:
            raise CaptureDegraded([(kind, f"{kind}: {self._n} lost") for kind in kinds])


def test_a_failure_whose_message_changes_is_still_one_episode(tmp_path, caplog):
    """Episodes were keyed on the message text, and a dropped-edge message carries a
    count. A buffer that kept overflowing therefore read as a new failure every second,
    which is the journal-burying this report exists to prevent. What identifies an
    episode is which paths are failing and how, not the numbers in the message."""
    script = [["edges"], ["edges"], ["edges"], ["edges", "codes"], ["edges", "codes"]]
    with caplog.at_level(logging.ERROR, logger="wl_sync.cli"):
        run(
            tmp_path,
            _DegradedWithACount(script),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            len(script),
            lambda _s: None,
        )
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2, errors
    assert "codes" in errors[1]


class _CloseFails(FakeBackend):
    def close(self) -> None:
        self.inject_edge(EDGE_PINS[0], 1, 999)
        raise OSError("release failed")


def test_a_failure_stopping_capture_still_closes_the_segment_cleanly(tmp_path, caplog):
    """If backend.close() raised, the segment was never closed: no trailer, so a run that
    recorded perfectly read as a crash, and the records close() had just delivered never
    reached disk. Capture stopping badly is worth an ERROR line, not the day's record."""
    with caplog.at_level(logging.ERROR, logger="wl_sync.cli"):
        day = run(
            tmp_path,
            _CloseFails(),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            2,
            lambda _s: None,
        )
    assert json.loads((day / "manifest.json").read_text())["segments"][0]["closed"] == "clean"
    _, records = read_log(day / segment_name(0))
    assert [r.tick_us for r in records if isinstance(r, Edge)] == [999]
    assert any("release failed" in r.getMessage() for r in caplog.records)


def test_a_failure_held_at_the_end_of_a_run_is_still_reported(tmp_path, caplog):
    """check_health() ran only inside the loop, so whatever capture was holding when the
    run ended -- the last second, or the final FIFO drain close() does -- was never
    reported to anyone."""
    late = "event-code capture: the PIO RX FIFO was full when drained"
    with caplog.at_level(logging.ERROR, logger="wl_sync.cli"):
        run(
            tmp_path,
            _ScriptedHealth([None, None, late]),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            2,
            lambda _s: None,
        )
    assert any(late in r.getMessage() for r in caplog.records)
