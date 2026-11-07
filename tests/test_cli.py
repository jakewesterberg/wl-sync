import collections
import datetime
import json
import logging
import math
import os
import queue
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

from wl_sync import cli, manifest, sessions
from wl_sync.backend import (
    STAMP_NOT_SEEN,
    STAMP_TOO_FAR_PREFIX,
    STAMPS_NOT_STARTED,
    STAMPS_OFF_RISES,
    STAMPS_STOPPED,
    CaptureLoss,
    FakeBackend,
    FrameStamp,
)
from wl_sync.barcode import FRAME_US, IDLE_MIN_US, N_BITS
from wl_sync.calibration import (
    RATE_FILE_NAME,
    Calibration,
    KernelNtp,
    read_calibration,
    write_calibration,
)
from wl_sync.cli import (
    CHECKPOINT_NAME,
    _ValueSource,
    main,
    probe_boot_id,
    probe_ntp_synchronized,  # the real one: the fixture below replaces cli's copy
    run,
)
from wl_sync.endpoint import serve
from wl_sync.clockmap import ClockPair, TickScale
from wl_sync.clock import (
    BARCODE_EPOCH,
    CLOCK_TRUST_TOLERANCE_S,
    MAX_BARCODE,
    read_checkpoint,
    value_from_clock,
    write_checkpoint,
)
from wl_sync.log import (
    TICK_WRAP_US,
    BarcodeEmitted,
    CodeWord,
    Edge,
    Loss,
    SyncBoxLogHeader,
    read_log,
)
from wl_sync.manifest import build_manifest
from wl_sync.pins import EDGE_PINS
from wl_sync.segment import SegmentWriter, segment_name
from concurrent.futures import Future

from wl_sync.sessions import (
    OpenSession,
    SessionId,
    SessionRequest,
    WrongSession,
    read_open,
    unassigned_dir,
    unassigned_id,
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
    directly in tests/test_clock.py.

    The kernel's NTP state and the board's serial are the host's too, so the kernel
    probe answers None ("cannot be asked", which closes the SI gate) and the serial is
    "test-serial". A test that wants the gate open or no serial says so."""
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda: None)
    monkeypatch.setattr(cli, "probe_kernel_ntp", lambda: None)
    monkeypatch.setattr(cli, "probe_board_serial", lambda: "test-serial")


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


def test_a_session_open_across_midnight_keeps_its_id(tmp_path):
    """Spec 3.1: a session that is open when the date rolls over keeps its id. The
    visit, not the calendar, decides the folder, so a restart after midnight must record
    into the session opened yesterday rather than minting a folder for today. This closes
    a coverage gap (docs/next-session.md 5): the resume test above uses a clock on the
    session's own date and so cannot tell "resume the open session" from "name the folder
    after today"."""
    session_id = SessionId.parse("2026-08-16_02")
    write_open(
        tmp_path,
        OpenSession(session_id, datetime.datetime(2026, 8, 16, 23, 0, tzinfo=UTC), "r1"),
    )
    folder = tmp_path / "2026-08-16_02" / "syncbox"
    folder.mkdir(parents=True)

    result = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(datetime.datetime(2026, 8, 17, 0, 30, tzinfo=UTC)),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    assert result == folder
    header, _ = read_log(folder / segment_name(0))
    assert header.session_id == "2026-08-16_02"
    assert not list(tmp_path.glob("**/2026-08-17*"))


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
    before this test because FakeBackend's counter never advances unless a test calls
    set_now_us()."""
    backend = FakeBackend()
    raw = iter([TICK_WRAP_US - 2_000_000, TICK_WRAP_US - 1_000_000, 0, 1_000_000])

    def tick_fn(_seconds):
        tick = next(raw)
        backend.set_now_us(tick)  # the clock reads the instant the edge is stamped
        backend.inject_edge(EDGE_PINS[0], 1, tick)

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
    # run() records the stamp FakeBackend.emit_pulses returns, its counter as the frame
    # starts, and nothing between the injected edge and that frame moves the counter, so
    # iteration i+1's barcode tick IS iteration i's injected edge tick. Equality across
    # the wrap is the whole claim: both paths report the same instant as the same number.
    assert barcodes[1:] == edges[: len(barcodes) - 1]


def test_the_barcode_tick_is_the_frames_lead_edge_not_its_end(tmp_path):
    """A frame takes 200 ms and BarcodeEmitted means the LEAD rising edge, which is
    what Barcode.start_us gives every receiving device. The tick is the stamp emit_pulses
    returns for that edge, here the fake's counter as the frame starts. A loop that read
    the counter after emit_frame() returned would record the frame's end instead, a
    systematic 200 ms behind every device it aligns."""

    class _FrameAdvancesTheCounter(FakeBackend):
        def emit_pulses(self, pin, pulses):
            stamp = super().emit_pulses(pin, pulses)
            self._tick += FRAME_US
            return stamp

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


# --- B is the lead edge's stamp, and a missed stamp drops its B (one-clock spec §2.5) ---


# The journal's lines, verbatim: spec §2.5, with the episode's close and the WARNING as
# the plan's Task 1.7 rewords them for c-direct (no GPIO17->GPIO19 loopback).
_DROPPED = (
    "barcode {} dropped: {}. Every device recorded the frame; this log has no B for it"
)
_DROPPED_IN_A_ROW = "{} barcodes dropped in a row, {} to {}: {}"
_BETWEEN = (
    "{} stamps on GPIO17 between frames, before barcode {}, discarded: late stamps of the "
    "frame before, a fit that has drifted, or something else driving the barcode line"
)
_TOO_FAR = (
    STAMP_TOO_FAR_PREFIX
    + "1500 us from the frame's lead edge as the CPU bracketed it (1000 us allowed)"
)
_ADVICE = {
    STAMP_NOT_SEEN: (
        "no stamp came for their lead edges; with the service stopped, python -m "
        "wl_sync.bench stamps shows whether the stamp machine still sees GPIO17"
    ),
    _TOO_FAR: (
        "their stamps lay outside the lead edges' windows, so the RAW->P fit had lost them"
    ),
    STAMPS_NOT_STARTED: (
        "the stamp machine did not start; restart once nothing else holds the PIO block's "
        "state machines"
    ),
    STAMPS_STOPPED: (
        "the stamp drain stopped (the barcode stamping ERROR says why); a restart starts "
        "it again"
    ),
    STAMPS_OFF_RISES: (
        "the stamps did not line up with the frames' rises: the stamp machine missed "
        "edges, or the RAW->P fit had drifted past the frame's TRAIL; python -m "
        "wl_sync.bench stamps shows which"
    ),
}


class _StampLagsTheClock(FakeBackend):
    """Stamps each lead edge 37 us after the clock the run loop can read: two clocks, as
    on the box, where the stamp machine counts the edge and `now_us()` is the CPU's."""

    def emit_pulses(self, pin, pulses):
        super().emit_pulses(pin, pulses)
        return FrameStamp(self._tick + 37)


class _StampsBetween(FakeBackend):
    """Finds `between[n]` stamps between frames before frame n (1-based), as Rp1Backend
    counts the ones it discards (FrameStamp.between)."""

    def __init__(self, between):
        super().__init__()
        self._between = between

    def emit_pulses(self, pin, pulses):
        stamp = super().emit_pulses(pin, pulses)
        return FrameStamp(stamp.tick, stamp.missed, self._between.get(len(self.emitted), 0))


def _dropping(tmp_path, backend, frames, caplog=None, tick_fn=None):
    """Record `frames` frames on `backend`, with cli's journal at INFO in `caplog`; return
    the values that went onto the wire and the `B` values written."""
    if caplog is not None:
        caplog.set_level(logging.INFO, logger=cli.log.name)
    tick_fn = tick_fn or (lambda _s: None)
    folder = run(tmp_path, backend, _clock_from(START), frames, tick_fn)
    return _wire_values(backend), _values(folder / segment_name(0))


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_barcode_is_recorded_at_its_stamp_not_at_the_clock_read_before_it(tmp_path):
    """A `B` is its frame's lead edge as the backend stamped it (spec §2.5, "The protocol
    change"). The loop used to read the clock just before the frame and record that,
    which on the box is the CPU's reading, not the stamp machine's count of the edge."""
    backend = _StampLagsTheClock()

    def tick_fn(_seconds):
        backend.set_now_us(backend.now_us() + 1_000_000)

    day = run(tmp_path, backend, _clock_from(START), 3, tick_fn)
    _, records = read_log(day / segment_name(0))
    ticks = [r.tick_us for r in records if isinstance(r, BarcodeEmitted)]
    assert ticks == [37, 1_000_037, 2_000_037]


def test_a_dropped_barcode_writes_no_b_record_and_one_error(tmp_path, caplog):
    """A frame whose lead edge has no stamp went onto the wire, and every device recorded
    it; only this log has no `B` for it (the user, 2026-10-30: drop it with an ERROR).
    The box records on."""
    backend = FakeBackend()
    backend.miss_stamps({2})
    wire, written = _dropping(tmp_path, backend, 4, caplog)

    assert written == [wire[0], wire[2], wire[3]]
    assert _errors(caplog) == [_DROPPED.format(wire[1], "its lead edge was not stamped")]


def test_a_run_of_dropped_barcodes_is_one_episode_named_when_it_ends(tmp_path, caplog):
    """A stamp machine that has stopped misses every frame, and an ERROR a frame would be
    86,400 lines a day. An episode is one ERROR at its first drop and one naming the
    whole range at the next frame kept."""
    backend = FakeBackend()
    backend.miss_stamps({2, 3, 4})
    seen = []  # how many ERRORs had been logged by the end of each iteration
    wire, written = _dropping(
        tmp_path, backend, 6, caplog, tick_fn=lambda _s: seen.append(len(_errors(caplog)))
    )

    assert written == [wire[0], wire[4], wire[5]]
    assert _errors(caplog) == [
        _DROPPED.format(wire[1], STAMP_NOT_SEEN),
        _DROPPED_IN_A_ROW.format(3, wire[1], wire[3], _ADVICE[STAMP_NOT_SEEN]),
    ]
    assert seen == [0, 1, 1, 1, 2, 2]  # the range is named with frame 5, the next kept


def test_a_single_dropped_barcode_is_not_also_reported_as_a_run(tmp_path, caplog):
    """An episode of one is named by its first ERROR alone, whether the next frame kept
    ends it or the run does."""
    backend = FakeBackend()
    backend.miss_stamps({2, 4})
    wire, written = _dropping(tmp_path, backend, 4, caplog)

    assert written == [wire[0], wire[2]]
    assert _errors(caplog) == [
        _DROPPED.format(wire[1], STAMP_NOT_SEEN),
        _DROPPED.format(wire[3], STAMP_NOT_SEEN),
    ]


def test_an_episode_open_at_the_end_is_named_at_the_end(tmp_path, caplog):
    """No frame is kept after the last drops, so the run's end closes the episode."""
    backend = FakeBackend()
    backend.miss_stamps({3, 4})
    seen = []
    wire, written = _dropping(
        tmp_path, backend, 4, caplog, tick_fn=lambda _s: seen.append(len(_errors(caplog)))
    )

    assert written == wire[:2]
    assert _errors(caplog) == [
        _DROPPED.format(wire[2], STAMP_NOT_SEEN),
        _DROPPED_IN_A_ROW.format(2, wire[2], wire[3], _ADVICE[STAMP_NOT_SEEN]),
    ]
    assert seen == [0, 0, 1, 1]  # named after the loop, not by any frame


def test_the_run_ends_saying_how_many_barcodes_were_dropped(tmp_path, caplog):
    backend = FakeBackend()
    backend.miss_stamps({2, 3, 5})
    _dropping(tmp_path, backend, 6, caplog)

    summary = [m for m in caplog.messages if m.startswith("barcodes:")]
    assert summary == ["barcodes: 6 emitted, 3 dropped this run"]


def test_stamps_between_frames_are_a_warning_naming_the_next_barcode(tmp_path, caplog):
    """Stamps found between two frames are discarded with a WARNING, which names the
    frame they came before (spec §2.5 step 4). They cost that frame nothing: its `B` is
    kept."""
    backend = _StampsBetween({2: 3})
    wire, written = _dropping(tmp_path, backend, 3, caplog)

    assert written == wire
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [_BETWEEN.format(3, wire[1])]
    assert _errors(caplog) == []


def test_a_dropped_last_barcode_does_not_rewind_the_checkpoint(tmp_path):
    """The checkpoint is the last value EMITTED, not the last `B` written (spec §2.5,
    "Two rules"). Read from the segment it would be the second value, behind a third that
    every device recorded, and the next start could issue that value again."""
    backend = FakeBackend()
    backend.miss_stamps({3})
    wire, written = _dropping(tmp_path, backend, 3)

    assert written == wire[:2]
    assert read_checkpoint(tmp_path / CHECKPOINT_NAME) == wire[2]


def test_a_long_episode_of_drops_does_not_lose_a_counter_wrap(tmp_path):
    """The box clock counts a wrap of the 32-bit counter only if it is read at least once
    per 2^32 us (71.6 min), and on a quiet rig the run loop's barcode was what read it. A
    dropped frame records no barcode, so an episode of drops longer than a wrap lost one,
    and every later tick was placed 4294.967296 s early. The loop now reads the clock every
    frame, kept or dropped. Here each frame is 1.5e9 us on, so the four dropped frames
    span a wrap that only they could have seen."""
    backend = FakeBackend()
    backend.miss_stamps(range(2, 6))

    def tick_fn(_seconds):
        backend.set_now_us(backend.now_us() + 1_500_000_000)

    day = run(tmp_path, backend, _clock_from(START), 6, tick_fn)
    _, records = read_log(day / segment_name(0))
    ticks = [r.tick_us for r in records if isinstance(r, BarcodeEmitted)]
    assert ticks == [0, 7_500_000_000]


@pytest.mark.parametrize(
    "cause",
    [STAMP_NOT_SEEN, _TOO_FAR, STAMPS_NOT_STARTED, STAMPS_STOPPED, STAMPS_OFF_RISES],
    ids=["not-stamped", "too-far", "not-started", "drain-stopped", "off-the-rises"],
)
def test_an_episode_closes_with_the_advice_for_its_cause(tmp_path, caplog, cause):
    """There is no loopback to check (c-direct, the user, 2026-11-07), so the episode's
    close says what to do about the kind of miss it held."""
    backend = FakeBackend()
    backend.miss_stamps({2, 3}, cause)
    wire, _ = _dropping(tmp_path, backend, 4, caplog)

    assert _errors(caplog) == [
        _DROPPED.format(wire[1], cause),
        f"2 barcodes dropped in a row, {wire[1]} to {wire[2]}: " + _ADVICE[cause],
    ]


@pytest.mark.parametrize(
    ("misses", "kinds"),
    [
        (
            [({2}, STAMP_NOT_SEEN), ({3, 4}, STAMPS_STOPPED)],
            [STAMP_NOT_SEEN, STAMPS_STOPPED],
        ),
        (
            [({2}, STAMPS_STOPPED), ({3, 4}, STAMP_NOT_SEEN)],
            [STAMPS_STOPPED, STAMP_NOT_SEEN],
        ),
        (
            # Two distances are one kind: the cause carries its distance.
            [
                ({2}, _TOO_FAR),
                ({3}, _TOO_FAR.replace("1500", "2500")),
                ({4}, STAMPS_STOPPED),
            ],
            [_TOO_FAR, STAMPS_STOPPED],
        ),
    ],
    ids=["not-stamped-then-stopped", "stopped-then-not-stamped", "too-far-twice"],
)
def test_an_episode_of_two_causes_gives_each_advice_once_in_order(
    tmp_path, caplog, misses, kinds
):
    """One episode can hold several kinds of miss: a drain that stops after frames went
    unstamped, say. Its close gives each kind's advice once, in the order first seen."""
    backend = FakeBackend()
    for frames, cause in misses:
        backend.miss_stamps(frames, cause)
    wire, _ = _dropping(tmp_path, backend, 5, caplog)

    assert _errors(caplog) == [
        _DROPPED.format(wire[1], misses[0][1]),
        _DROPPED_IN_A_ROW.format(3, wire[1], wire[3], "; ".join(_ADVICE[k] for k in kinds)),
    ]


def test_a_cause_with_no_advice_closes_its_episode_as_it_is(tmp_path, caplog):
    """A cause the run loop has no advice for is still named, so a backend's new cause is
    never lost from the close."""
    backend = FakeBackend()
    backend.miss_stamps({2, 3}, "x")
    wire, _ = _dropping(tmp_path, backend, 4, caplog)

    assert _errors(caplog)[-1] == f"2 barcodes dropped in a row, {wire[1]} to {wire[2]}: x"


def test_no_journal_line_names_gpio19_or_a_loopback(tmp_path, caplog):
    """c-direct (the user, 2026-11-07): the stamp machine reads GPIO17 itself, and nothing
    is wired to GPIO19. Spec §2.5's texts sent the operator to a GPIO17->GPIO19 loopback
    that does not exist. One run writes every line those texts became: a first drop and
    a close for each cause, and the WARNING."""
    backend = _StampsBetween({1: 2})
    causes = [
        STAMP_NOT_SEEN, _TOO_FAR, STAMPS_NOT_STARTED, STAMPS_STOPPED, STAMPS_OFF_RISES
    ]
    for n, cause in enumerate(causes):
        backend.miss_stamps({3 * n + 2, 3 * n + 3}, cause)
    with caplog.at_level(logging.DEBUG):
        _dropping(tmp_path, backend, 3 * len(causes) + 1)

    assert len(_errors(caplog)) == 2 * len(causes)
    assert any(m.startswith("2 stamps on GPIO17 between frames") for m in caplog.messages)
    assert "GPIO19" not in caplog.text
    assert "loopback" not in caplog.text


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
        stamp = super().emit_pulses(pin, pulses)
        self._frames += 1
        if self._frames >= self._crash_on:
            raise _Crash
        return stamp


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


def test_the_journal_names_the_first_barcode_actually_emitted(tmp_path, caplog):
    """The start line said "first barcode N" with the value _resume() computed, but the
    first frame reads the clock again and usually emits a later one: on the box on
    2026-10-19 the line said ...321 and the first B record was ...322. An operator
    matching the journal against the data needs the value that went onto the wire, so it
    gets its own line, once, after the first frame; the start line names the floor."""
    start = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        folder = run(
            out=tmp_path,
            backend=FakeBackend(),
            now_fn=_clock_from(start),
            duration_s=3,
            tick_fn=lambda _seconds: None,
        )
    _, records = read_log(folder / segment_name(0))
    first = next(r.value for r in records if isinstance(r, BarcodeEmitted))
    assert first == value_from_clock(start) + 1  # the case the old line got wrong
    assert [m for m in caplog.messages if m.startswith("first barcode")] == [
        f"first barcode {first} emitted"
    ]
    assert f"barcodes from {value_from_clock(start)}" in caplog.messages[0]


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
    # Port 0, so CI never fights over 8740. The token file is left as shipped: it does
    # not exist on a CI runner, and a box with no token file must still record (R18).
    argv[argv.index("--listen") + 1] = "127.0.0.1:0"
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
    failure = (
        "edge capture: 3 edge event(s) lost (kernel buffer overflow on GPIO20): "
        "the edge pump fell a whole kernel buffer behind"
    )
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

    def start_edge_capture(self, pins, on_edge, on_loss=None):
        super().start_edge_capture(pins, on_edge, on_loss)
        thread = threading.Thread(
            target=lambda: [on_edge(pins[0], level, 10 + level) for level in (1, 0, 1)],
            name="capture",
        )
        thread.start()
        thread.join(10.0)
        assert not thread.is_alive(), "the capture thread did not finish"


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


class _ReportsALossFromACaptureThread(FakeBackend):
    """Reports a lost edge from its own thread, between the two edges around it, the way
    Rp1Backend's edge pump does: GPIO20 rose twice, so an edge between never became an
    event of its own."""

    def start_edge_capture(self, pins, on_edge, on_loss=None):
        super().start_edge_capture(pins, on_edge, on_loss)

        def capture():
            on_edge(pins[0], 1, 10)
            on_loss(
                CaptureLoss(
                    cause="coalesced", count=1, bound="at_least", gpio=pins[0],
                    after=12, span_us=2, ahead=True,
                )
            )
            on_edge(pins[0], 1, 12)

        thread = threading.Thread(target=capture, name="capture")
        thread.start()
        thread.join(10.0)
        assert not thread.is_alive(), "the capture thread did not finish"


def test_a_loss_record_from_a_capture_thread_is_written_by_the_segments_owner(
    tmp_path, monkeypatch
):
    """An L record takes the path every record takes: a capture thread hands it to the
    QueuedSink, and only the sink's owner thread writes the segment."""
    writers = []
    original = SegmentWriter.write

    def spy(self, record):
        writers.append((threading.current_thread().name, record))
        original(self, record)

    monkeypatch.setattr(SegmentWriter, "write", spy)
    day = run(
        tmp_path,
        _ReportsALossFromACaptureThread(),
        _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        2,
        lambda _s: None,
    )

    assert {name for name, r in writers if isinstance(r, Loss)} == {"wl-sync-sink"}
    _, records = read_log(day / segment_name(0))
    edges_and_losses = [r for r in records if isinstance(r, (Edge, Loss))]
    assert edges_and_losses == [
        Edge(tick_us=10, gpio=EDGE_PINS[0], level=1),
        Loss(
            tick_us=12, path="E", gpio=EDGE_PINS[0], count=1, bound="at_least",
            cause="coalesced", before_us=10, after_us=12,
        ),
        Edge(tick_us=12, gpio=EDGE_PINS[0], level=1),
    ]


class _ReportsALossNoRecordCanHold(FakeBackend):
    """Reports, from its own thread between two edges, a loss the format refuses: an
    overflow with no line, as a bug in building the report would give."""

    def start_edge_capture(self, pins, on_edge, on_loss=None):
        super().start_edge_capture(pins, on_edge, on_loss)

        def capture():
            on_edge(pins[0], 1, 10)
            on_loss(
                CaptureLoss(
                    cause="buffer_overflow", count=1, bound="exact", gpio=None,
                    after=12, span_us=2, ahead=True,
                )
            )
            on_edge(pins[0], 0, 12)

        thread = threading.Thread(target=capture, name="capture")
        thread.start()
        thread.join(10.0)
        assert not thread.is_alive(), "the capture thread did not finish"


def test_a_loss_record_that_cannot_be_made_is_journalled_and_the_run_goes_on(
    tmp_path, caplog
):
    """The record is dropped, the capture thread lives, both edges are in the segment,
    and the journal has the Recorder's ERROR and, once, the health check's."""
    with caplog.at_level(logging.ERROR):
        day = run(
            tmp_path,
            _ReportsALossNoRecordCanHold(),
            _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
            3,
            lambda _s: None,
        )
    _, records = read_log(day / segment_name(0))
    assert [(r.tick_us, r.level) for r in records if isinstance(r, Edge)] == [(10, 1), (12, 0)]
    assert [r for r in records if isinstance(r, Loss)] == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([m for m in errors if "was refused" in m and "buffer_overflow" in m]) == 1
    degraded = [m for m in errors if m.startswith("capture degraded")]
    assert len(degraded) == 1 and "edge capture: 1 L record(s) refused" in degraded[0]


def test_a_refusal_during_a_persisting_backend_failure_gets_its_own_health_line(caplog):
    """The health report keys an episode on the kinds of BOTH checks. The edge thread
    stays dead, so the backend raises the same thing every call; one call in, the
    Recorder refuses an L record. That refusal is a new episode, and gets its own line,
    naming it. Keyed on the first failure only, it would be swallowed by the backend's
    episode, and only the Recorder's own ERROR would say so."""
    from wl_sync.backend import CaptureDegraded

    class _DeadEdgeThread(FakeBackend):
        def check_health(self) -> None:
            raise CaptureDegraded(
                [("edge capture: stopped", "the edge capture thread stopped unexpectedly")]
            )

    class _RefusesOnce:
        def __init__(self) -> None:
            self.calls = 0

        def check_health(self) -> None:
            self.calls += 1
            if self.calls == 2:
                raise CaptureDegraded(
                    [("edge capture: L record refused",
                      "edge capture: 1 L record(s) refused and left out of the segment")]
                )

    health = cli._HealthReport(_DeadEdgeThread(), _RefusesOnce())
    with caplog.at_level(logging.ERROR, logger=cli.log.name):
        for _ in range(3):
            health()
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "thread stopped" in lines[0] and "refused" not in lines[0]
    assert "thread stopped" in lines[1] and "1 L record(s) refused" in lines[1]
    assert sum("refused" in line for line in lines) == 1


def test_a_run_that_loses_nothing_writes_no_loss_record(tmp_path):
    day = run(
        tmp_path,
        _DeliversFromACaptureThread(),
        _clock_from(datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)),
        2,
        lambda _s: None,
    )
    _, records = read_log(day / segment_name(0))
    assert [r for r in records if isinstance(r, Edge)] != []
    assert [r for r in records if isinstance(r, Loss)] == []


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
        stamp = super().emit_pulses(pin, pulses)
        self.frame_starts.append(self._clock.now)
        index = len(self.frame_starts) - 1
        self._clock.now += self._fixed_s if self._frame_s is None else self._frame_s[index]
        self.frame_ends.append(self._clock.now)
        return stamp


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


# --- session requests (spec 3.1-3.4) -------------------------------------------------

DAY = datetime.date(2026, 8, 16)
START = datetime.datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def _request(kind, argument):
    return SessionRequest(kind, argument, Future())


def _requesting(requests, script):
    """A tick_fn that puts `script[n]` on the queue after iteration n (1-based).

    A request is drained after the frame of the iteration that follows, so one scripted
    at n is carried out in iteration n + 1 -- a known place, with no timing in it."""
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        for request in script.get(state["n"], ()):
            requests.put(request)

    return tick_fn


def _values(path):
    return [
        int(line.split(",")[2])
        for line in path.read_text().splitlines()
        if line.startswith("B,")
    ]


def _has_trailer(path):
    return any(line.startswith("T,") for line in path.read_text().splitlines())


def _record(tmp_path, script, iterations, start=START):
    requests = queue.SimpleQueue()
    folder = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(start),
        duration_s=iterations,
        tick_fn=_requesting(requests, script),
        requests=requests,
    )
    return folder


def test_an_open_rolls_the_recording_into_the_new_session(tmp_path):
    """An open at iteration 2 closes the between-sessions segment after frame 2 and
    records frame 3 onwards into the new session's folder, which the run then reports
    as the folder it last recorded into."""
    opening = _request("open", "r1")
    folder = _record(tmp_path, {1: [opening]}, 4)

    assert opening.reply.result(timeout=0).session.session_id == SessionId.parse("2026-08-16_01")
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert folder == session
    before = unassigned_dir(tmp_path, DAY) / segment_name(0)
    assert _has_trailer(before)
    assert len(_values(before)) == 2
    after = _values(session / segment_name(0))
    assert len(after) == 2 and after[0] > _values(before)[-1]
    header, _ = read_log(session / segment_name(0))
    assert header.session_id == "2026-08-16_01"
    assert read_open(tmp_path).session_id == SessionId.parse("2026-08-16_01")


def test_a_close_rolls_the_recording_back_between_sessions(tmp_path):
    opening, closing = _request("open", "r1"), _request("close", "2026-08-16_01")
    folder = _record(tmp_path, {1: [opening], 3: [closing]}, 6)

    assert closing.reply.result(timeout=0) == SessionId.parse("2026-08-16_01")
    session_segment = tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0)
    assert _has_trailer(session_segment)
    assert len(_values(session_segment)) == 2
    assert folder.parent.parent.name == "unassigned"
    # seg-000 of the unassigned folder is the one from BEFORE the open (frames 1-2); the
    # post-close frames (5-6) are in seg-001, which is a segment of its own.
    assert len(_values(folder / segment_name(0))) == 2
    after = folder / segment_name(1)
    assert len(_values(after)) == 2
    assert _has_trailer(after)
    header, _ = read_log(after)
    assert header.session_id == "2026-08-16_00"
    assert header.segment_index == 1
    assert read_open(tmp_path) is None


def test_an_open_while_open_closes_the_old_session_and_says_so(tmp_path, caplog):
    first, second = _request("open", "r1"), _request("open", "r2")
    with caplog.at_level(logging.INFO, logger=cli.log.name):
        _record(tmp_path, {1: [first], 3: [second]}, 6)

    assert second.reply.result(timeout=0).closed == first.reply.result(timeout=0).session.session_id
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("2026-08-16_01" in m and "2026-08-16_02" in m for m in errors)
    assert _has_trailer(tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0))


def test_an_open_on_a_dead_clock_is_named_by_the_barcode_day(tmp_path):
    opening = _request("open", "r1")
    _record(tmp_path, {1: [opening]}, 3, start=datetime.datetime(2000, 1, 1, tzinfo=UTC))
    assert opening.reply.result(timeout=0).session.session_id == SessionId.parse("2020-01-01_01")


def test_session_errors_reach_the_caller_and_recording_continues(tmp_path):
    wrong, garbled = _request("close", "2026-08-16_07"), _request("close", "nonsense")
    folder = _record(tmp_path, {1: [wrong, garbled]}, 4)

    with pytest.raises(WrongSession):
        wrong.reply.result(timeout=0)
    with pytest.raises(ValueError):
        garbled.reply.result(timeout=0)
    assert folder == unassigned_dir(tmp_path, DAY)
    assert len(_values(folder / segment_name(0))) == 4


def test_a_cancelled_request_is_never_carried_out(tmp_path):
    cancelled, live = _request("open", "r1"), _request("open", "r2")
    assert cancelled.reply.cancel()
    _record(tmp_path, {1: [cancelled, live]}, 3)
    assert live.reply.result(timeout=0).session.session_id == SessionId.parse("2026-08-16_01")
    assert read_open(tmp_path).request_id == "r2"
    assert [p.name for p in tmp_path.glob("2026-08-16_*")] == ["2026-08-16_01"]


def test_a_roll_in_the_last_iteration_does_not_rewind_the_checkpoint(tmp_path):
    """The new writer holds no barcode yet when the run ends, so reading the checkpoint
    from it alone rewinds to the run's first value and a restart re-issues values."""
    opening = _request("open", "r1")
    folder = _record(tmp_path, {2: [opening]}, 3)

    emitted = _values(unassigned_dir(tmp_path, DAY) / segment_name(0))
    assert len(emitted) == 3
    assert not _values(folder / segment_name(0))
    assert read_checkpoint(tmp_path / CHECKPOINT_NAME) == emitted[-1]


def test_an_open_that_fails_after_saving_state_still_records_into_that_session(
    tmp_path, monkeypatch
):
    """write_open saves the state and then fsyncs the directory. If the fsync raises, the
    state names the new session while the caller hears of a failure; the retry is then
    `reused` and rolls nowhere. The recording must follow the state file regardless."""
    real = sessions.write_open

    def saves_then_fails(root, session):
        real(root, session)
        raise OSError("directory fsync failed")

    failing, retry = _request("open", "r1"), _request("open", "r1")
    monkeypatch.setattr(sessions, "write_open", saves_then_fails)
    requests = queue.SimpleQueue()
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        if state["n"] == 1:
            requests.put(failing)
        if state["n"] == 3:
            monkeypatch.setattr(sessions, "write_open", real)
            requests.put(retry)

    run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(START),
        duration_s=6,
        tick_fn=tick_fn,
        requests=requests,
    )

    assert isinstance(failing.reply.exception(timeout=0), OSError)
    assert retry.reply.result(timeout=0).reused
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert len(_values(unassigned_dir(tmp_path, DAY) / segment_name(0))) == 2
    assert len(_values(session / segment_name(0))) == 4


def test_a_close_that_fails_after_clearing_state_still_records_between_sessions(
    tmp_path, monkeypatch
):
    real = sessions.clear_open

    def clears_then_fails(root):
        real(root)
        raise OSError("directory fsync failed")

    opening, closing = _request("open", "r1"), _request("close", "2026-08-16_01")
    requests = queue.SimpleQueue()
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        if state["n"] == 1:
            requests.put(opening)
        if state["n"] == 3:
            monkeypatch.setattr(sessions, "clear_open", clears_then_fails)
            requests.put(closing)

    folder = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(START),
        duration_s=6,
        tick_fn=tick_fn,
        requests=requests,
    )

    assert isinstance(closing.reply.exception(timeout=0), OSError)
    assert read_open(tmp_path) is None
    assert folder.is_relative_to(tmp_path / "unassigned")
    assert len(_values(tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0))) == 2
    assert len(_values(folder / segment_name(1))) == 2


def test_a_roll_does_not_parse_again_a_crashed_segment_the_start_parsed(
    tmp_path, monkeypatch
):
    """Rolling out of a folder rebuilds its manifest on the run loop's thread. A segment
    left without a trailer by an earlier crash is parsed in full by that rebuild, and a
    large one held the loop for seconds: no barcode frame, no flush of the new segment,
    and the GIL held against the word pump, at the open itself (spec Sec.2's anti-goal).

    The start of the run has already parsed it, before capture began, and nothing has
    written to it since. So the roll must reuse that parse rather than repeat it."""
    earlier = START - datetime.timedelta(hours=1)
    with pytest.raises(_Crash):
        run(tmp_path, FakeBackend(), _clock_from(earlier), None, _crash_after(5))
    crashed = unassigned_dir(tmp_path, DAY) / segment_name(0)
    assert not _has_trailer(crashed)

    parses = collections.Counter()
    real = manifest.summarise_segment

    def counting(path):
        parses[path] += 1
        return real(path)

    monkeypatch.setattr(manifest, "summarise_segment", counting)
    opening = _request("open", "r1")
    _record(tmp_path, {1: [opening]}, 3)

    assert opening.reply.result(timeout=0).session.session_id == SessionId.parse("2026-08-16_01")
    # Rebuilt twice -- at the start, and when the open rolled out of the folder -- and
    # parsed once, at the start.
    assert json.loads((crashed.parent / "manifest.json").read_text())["segments"][0][
        "closed"
    ] == "crash"
    assert parses[crashed] == 1


def test_a_session_entered_by_a_roll_has_a_manifest_at_once(tmp_path):
    """A folder's manifest was written only when the recording left it or the run ended,
    so a session entered by an open had none for the whole visit: `ls <id>/syncbox/`
    showed segments and no manifest, and deploy/README.md calls the manifest the thing
    to read.

    It lists no segments yet. The one being written is left out, as the start of a run
    leaves out its own: listed now, it could only say "crash", since it has no trailer
    until it closes."""
    requests = queue.SimpleQueue()
    put_requests = _requesting(requests, {1: [_request("open", "r1")]})
    written = tmp_path / "2026-08-16_01" / "syncbox" / "manifest.json"
    state = {"n": 0}
    seen = {}

    def tick_fn(seconds):
        state["n"] += 1
        if state["n"] == 2:  # just after iteration 2, which carried the open out
            seen["manifest"] = json.loads(written.read_text()) if written.exists() else None
        put_requests(seconds)

    run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(START),
        duration_s=4,
        tick_fn=tick_fn,
        requests=requests,
    )

    assert seen["manifest"] is not None, "no manifest.json in the session it rolled into"
    assert seen["manifest"]["day"] == "2026-08-16_01"
    assert seen["manifest"]["segments"] == []
    # The end of the run still rebuilds it, with the segment now closed.
    final = json.loads(written.read_text())
    assert [(s["file"], s["closed"]) for s in final["segments"]] == [(segment_name(0), "clean")]


# --- clock trust at a roll -------------------------------------------------------------

STEP_S = 2 * CLOCK_TRUST_TOLERANCE_S


class _SteppableWall:
    """A now_fn that advances a second per call, as _clock_from does, and can be STEPPED:
    what an NTP step does, moving the wall clock and leaving the monotonic one alone."""

    def __init__(self, start):
        self._next = start

    def __call__(self):
        reading = self._next
        self._next += datetime.timedelta(seconds=1)
        return reading

    def step(self, seconds):
        self._next += datetime.timedelta(seconds=seconds)

    def set(self, when):
        self._next = when


class _StepsTheWallDuringAFrame(FakeBackend):
    """Steps `wall` while frame `frame` (1-based) is on the wire: after the loop chose
    that frame's value, and before it carries out a request that iteration drains."""

    def __init__(self, wall, frame, seconds):
        super().__init__()
        self._wall, self._frame, self._seconds = wall, frame, seconds
        self._frames = 0

    def emit_pulses(self, pin, pulses):
        stamp = super().emit_pulses(pin, pulses)
        self._frames += 1
        if self._frames == self._frame:
            self._wall.step(self._seconds)
        return stamp


def _ntp_answers(monkeypatch, synced):
    """The NTP probe answers `state["synced"]` at the moment it is asked."""
    state = {"synced": synced}
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda: state["synced"])
    return state


def _either_side_of_an_open(tmp_path, now_fn, *, backend=None, then=None):
    """Record three frames with an open queued after the first, which carries it out
    after frame 2; `then()` runs between frames 1 and 2, before the open reaches the loop.
    Returns the paths of the between-sessions segment the run began in and of the
    session segment the open rolled into."""
    requests = queue.SimpleQueue()
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        if state["n"] == 1:
            if then is not None:
                then()
            requests.put(_request("open", "r1"))

    run(
        out=tmp_path,
        backend=backend or FakeBackend(),
        now_fn=now_fn,
        duration_s=3,
        tick_fn=tick_fn,
        requests=requests,
    )
    return (
        unassigned_dir(tmp_path, DAY) / segment_name(0),
        tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0),
    )


def _trust(segment):
    header, _ = read_log(segment)
    return header.clock_trusted, header.clock_reason


def test_a_session_opened_after_ntp_syncs_is_trusted(tmp_path, monkeypatch):
    """THE FAULT FOUND ON THE BOX, 2026-10-19. After a power-up the unit started at
    12.5 s and NTP synced at 33.5 s, and every segment that day said
    ntp_unsynchronized: trust was judged once, when the process started, and every roll
    reused it. A segment opened after the sync must say what is true when it opens.

    The run's first segment keeps the judgement made at the start."""
    ntp = _ntp_answers(monkeypatch, synced=False)
    before, after = _either_side_of_an_open(
        tmp_path, _clock_from(START), then=lambda: ntp.update(synced=True)
    )

    assert _trust(before) == (False, "ntp_unsynchronized")
    assert _trust(after) == (True, "")


def test_a_roll_while_ntp_is_still_unsynchronized_says_so(tmp_path, monkeypatch):
    _ntp_answers(monkeypatch, synced=False)
    _, after = _either_side_of_an_open(tmp_path, _clock_from(START))
    assert _trust(after) == (False, "ntp_unsynchronized")


def test_a_probe_that_fails_at_a_roll_never_upgrades_trust(tmp_path, monkeypatch):
    """A FAILED PROBE IS NOT AN ANSWER. On the box timedatectl exists, so None at a roll
    means the probe failed (a timeout, a D-Bus hiccup), not that it cannot be asked.
    Taken as "not untrusted", it turned the start's definite False into trusted: NTP
    still unsynchronised, the values tracking an unverified clock, the session marked
    good. The roll keeps the run's last answer instead."""
    ntp = _ntp_answers(monkeypatch, synced=False)
    before, after = _either_side_of_an_open(
        tmp_path, _clock_from(START), then=lambda: ntp.update(synced=None)
    )

    assert _trust(before) == (False, "ntp_unsynchronized")
    assert _trust(after) == (False, "ntp_unsynchronized")


def test_a_probe_that_has_never_answered_is_not_untrusted_at_a_roll(tmp_path):
    """The other half of the rule. A laptop or a CI container cannot ask at all, so every
    probe in the run says None, and a roll there is judged on the values alone, as the
    start is (tests/test_clock.py)."""
    _, after = _either_side_of_an_open(tmp_path, _clock_from(START))
    assert _trust(after) == (True, "")


def test_a_run_that_began_clamped_ahead_says_so_at_every_roll(tmp_path, monkeypatch):
    """No step in THIS run, and still off the clock. An earlier run left the checkpoint
    100 s ahead of a correct clock, so this run starts clamped at checkpoint + 1 and its
    floor keeps every value 100 s ahead for as long as it lasts. NTP is synchronised
    throughout, so only the value check catches the roll. The first segment names the
    cause; the roll names what it measured."""
    write_checkpoint(tmp_path / CHECKPOINT_NAME, value_from_clock(START) + 100)
    _ntp_answers(monkeypatch, synced=True)
    before, after = _either_side_of_an_open(tmp_path, _clock_from(START))

    assert _trust(before) == (False, "clock_behind_checkpoint")
    assert _trust(after) == (False, "barcode_off_clock")


def test_a_dead_coin_cell_recovers_trust_at_the_first_roll_after_ntp(tmp_path, monkeypatch):
    """The recovery path for a dead CR2032. The run starts before the epoch and records on
    its floor from an hour-old checkpoint. NTP then sets the clock, the next value jumps
    to it, and a session opened after that is trusted: the values are wall time again."""
    write_checkpoint(tmp_path / CHECKPOINT_NAME, value_from_clock(START) - 3600)
    ntp = _ntp_answers(monkeypatch, synced=False)
    wall = _SteppableWall(datetime.datetime(2000, 1, 1, tzinfo=UTC))

    def ntp_sets_the_clock():
        wall.set(START)
        ntp.update(synced=True)

    before, after = _either_side_of_an_open(tmp_path, wall, then=ntp_sets_the_clock)

    assert _trust(before) == (False, "clock_before_epoch")
    assert _values(before)[-1] == value_from_clock(START)
    assert _trust(after) == (True, "")


def test_a_backward_step_leaves_a_later_roll_untrusted(tmp_path, monkeypatch):
    """A SYNCHRONISED CLOCK DOES NOT MAKE THE VALUES RIGHT. The wall clock steps back
    past the tolerance between two frames; the run's floor (_ValueSource) holds every
    later value ahead of it, for the rest of the run, while NTP reports itself synced
    throughout. Re-asking NTP alone would mark the session trusted, and its values are
    two minutes off. The start was trusted; the roll is not, and that is correct."""
    _ntp_answers(monkeypatch, synced=True)
    wall = _SteppableWall(START)
    before, after = _either_side_of_an_open(tmp_path, wall, then=lambda: wall.step(-STEP_S))

    assert _trust(before) == (True, "")
    assert _trust(after) == (False, "barcode_off_clock")


def test_a_forward_step_at_a_roll_is_untrusted(tmp_path, monkeypatch):
    """The other direction. The wall clock steps forward past the tolerance while the
    frame before the roll is on the wire, so the next frame -- the session's first --
    jumps to the clock. Across this boundary the values claim minutes passed where one
    frame did, which is exactly the gap arithmetic the flag says not to believe."""
    _ntp_answers(monkeypatch, synced=True)
    wall = _SteppableWall(START)
    before, after = _either_side_of_an_open(
        tmp_path, wall, backend=_StepsTheWallDuringAFrame(wall, frame=2, seconds=STEP_S)
    )

    assert _trust(before) == (True, "")
    assert _trust(after) == (False, "barcode_off_clock")
    assert _values(after)[0] - _values(before)[-1] > CLOCK_TRUST_TOLERANCE_S


def test_a_forward_step_the_values_have_followed_is_trusted(tmp_path, monkeypatch):
    """Not crying wolf. The values re-read the wall clock at every frame, so a clock that
    stepped forward BEFORE the last frame has been followed, and the values agree with it
    again by the roll. This is the box whose coin cell read slow at power-up and which NTP
    then corrected. A rule that measured the values against the start rather than
    against what is being emitted would flag every later segment of that day: the fault
    this fixes, in another form."""
    _ntp_answers(monkeypatch, synced=True)
    wall = _SteppableWall(START)
    before, after = _either_side_of_an_open(tmp_path, wall, then=lambda: wall.step(STEP_S))

    # The jump is inside the segment before the roll, not across it.
    first, last = _values(before)
    assert last - first > CLOCK_TRUST_TOLERANCE_S
    assert _values(after)[0] - last < CLOCK_TRUST_TOLERANCE_S
    assert _trust(after) == (True, "")


def test_a_roll_on_a_dead_clock_still_says_so(tmp_path):
    """A pre-epoch clock names no barcode value to compare with, and value_from_clock
    raises there. The roll must report it, not die of it: spec Sec.7, the box records
    anyway."""
    opening = _request("open", "r1")
    _record(tmp_path, {1: [opening]}, 3, start=datetime.datetime(2000, 1, 1, tzinfo=UTC))
    assert _trust(tmp_path / "2020-01-01_01" / "syncbox" / segment_name(0)) == (
        False,
        "clock_before_epoch",
    )


def test_a_roll_says_how_far_its_clock_can_be_trusted(tmp_path, monkeypatch, caplog):
    """journald is the box's status surface (spec Sec.7, Sec.10). The start's line says
    whether the clock is trusted; a roll's line said only where it was recording, so a
    segment that changed its judgement at a roll changed it silently."""
    _ntp_answers(monkeypatch, synced=True)
    wall = _SteppableWall(START)
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        _either_side_of_an_open(tmp_path, wall, then=lambda: wall.step(-STEP_S))

    [line] = [m for m in caplog.messages if m.startswith("recording into")]
    assert "clock_trusted=False" in line
    assert "clock_reason=barcode_off_clock" in line


def test_the_value_being_emitted_now_advances_on_the_monotonic_clock():
    """What a roll compares with the wall clock: the last value chosen, plus the whole
    seconds the monotonic clock -- the one the loop paces on -- has advanced since. The
    wall clock is not read, or the check would compare the clock with itself."""
    mono = {"now": 50.0}
    wall = {"now": BARCODE_EPOCH + datetime.timedelta(seconds=1000.5)}
    source = _ValueSource(lambda: wall["now"], first_value=1000, mono_fn=lambda: mono["now"])

    assert source() == 1000
    assert source.emitting() == 1000
    mono["now"] = 50.99
    assert source.emitting() == 1000
    mono["now"] = 51.0
    assert source.emitting() == 1001
    wall["now"] += datetime.timedelta(seconds=STEP_S)  # a step moves the wall clock only
    mono["now"] = 55.5
    assert source.emitting() == 1005


# --- the per-process id and the barcode delay, in every header (2026-10-30) ------------


def _headers(root):
    """Every segment header under `root`, by the segment's path relative to it."""
    return {
        path.relative_to(root): read_log(path)[0]
        for path in sorted(root.rglob("seg-*.log"))
    }


def _open_then_close(tmp_path, backend=None, **run_kwargs):
    """Three segments from one process: between sessions, the visit, between sessions."""
    requests = queue.SimpleQueue()
    script = {1: [_request("open", "r1")], 3: [_request("close", "2026-08-16_01")]}
    run(
        out=tmp_path,
        backend=backend or FakeBackend(),
        now_fn=_clock_from(START),
        duration_s=6,
        tick_fn=_requesting(requests, script),
        requests=requests,
        **run_kwargs,
    )
    headers = _headers(tmp_path)
    assert len(headers) == 3
    return headers


def test_every_segment_of_one_process_carries_its_id_and_a_restart_a_new_one(
    tmp_path, caplog
):
    """THE CONTRACT wl-preproc will read a roll by. Until now it took every segment as
    its own clock, which is true only while every roll changes folders. A roll that stays
    in its folder breaks that, so the header says which process wrote it: the same id
    across a roll, whose ticks share one clock, and a new id after a restart, whose ticks
    do not."""
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        first = _open_then_close(tmp_path)

    ids = {header.process_uuid for header in first.values()}
    assert len(ids) == 1 and None not in ids, ids
    [process] = ids
    assert uuid.UUID(process).version == 4
    assert str(uuid.UUID(process)) == process
    # The start's journal line names it, so an operator can find the process a segment
    # came from.
    assert process in caplog.messages[0]

    _record(tmp_path, {}, 2, start=START + datetime.timedelta(minutes=5))  # a restart
    restarted = [h for path, h in _headers(tmp_path).items() if path not in first]
    assert len(restarted) == 1
    assert restarted[0].process_uuid not in (None, process)


def test_every_segment_names_its_backends_timebase(tmp_path):
    """The header says which clocks its ticks are on, as the backend that made them
    declares it, in every segment of the process: the first and each one after a roll."""

    class _Split(FakeBackend):
        timebase = "split"

    assert {h.timebase for h in _open_then_close(tmp_path / "a").values()} == {"fake"}
    headers = _open_then_close(tmp_path / "b", backend=_Split())
    assert {h.timebase for h in headers.values()} == {"split"}


def test_a_measured_barcode_delay_reaches_every_segment(tmp_path):
    headers = _open_then_close(tmp_path, barcode_delay_us=1234)
    assert {header.barcode_delay_us for header in headers.values()} == {1234}


def test_without_a_measured_delay_every_segment_says_unmeasured(tmp_path):
    headers = _open_then_close(tmp_path)
    assert {header.barcode_delay_us for header in headers.values()} == {None}


def _main_captures_run(monkeypatch, tmp_path):
    """main() with run() replaced by a recorder of the arguments it was given."""
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return tmp_path

    monkeypatch.setattr(cli, "run", fake_run)
    return seen


def test_the_barcode_delay_is_given_on_the_command_line(tmp_path, monkeypatch):
    """How the systemd unit tells the box what bench day measured."""
    seen = _main_captures_run(monkeypatch, tmp_path)
    main(["record", "--out", str(tmp_path), "--fake", "--barcode-delay-us", "850"])
    assert seen["barcode_delay_us"] == 850

    seen.clear()
    main(["record", "--out", str(tmp_path), "--fake", "--barcode-delay-us", "0"])
    assert seen["barcode_delay_us"] == 0

    seen.clear()
    assert cli.BARCODE_DELAY_MAX_US == 100_000
    main(["record", "--out", str(tmp_path), "--fake", "--barcode-delay-us", "100000"])
    assert seen["barcode_delay_us"] == 100_000

    seen.clear()
    main(["record", "--out", str(tmp_path), "--fake"])
    assert seen["barcode_delay_us"] is None


@pytest.mark.parametrize(
    "value",
    [
        "-1",
        "1.5",
        "850us",
        "",
        # int() accepts all four of these. None is how anyone writes a delay in a unit.
        "8_50",
        "+850",
        " 850",
        "٨٥٠",  # 850 in Arabic-Indic digits
        # Past the cap: 850 us typed in nanoseconds, and a whole second.
        "850000",
        "1000000",
    ],
)
def test_a_barcode_delay_that_is_not_a_whole_non_negative_number_is_refused(
    tmp_path, monkeypatch, value
):
    """A typo in the unit is configuration the operator must see, as with --listen: an
    argparse error (exit 2), not a header carrying a wrong correction for a whole day.
    ASCII digits only, and no more than BARCODE_DELAY_MAX_US."""
    seen = _main_captures_run(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["record", "--out", str(tmp_path), "--fake", "--barcode-delay-us", value])
    assert excinfo.value.code == 2
    assert seen == {}


# --- one roll in place, when NTP first syncs (2026-10-30) ------------------------------


HANG = object()  # an `answer` meaning: timedatectl hangs, so the probe hits its timeout

# The real probe's own default (`probe_ntp_synchronized`), which the start and every
# open or close use.
PROBE_DEFAULT_TIMEOUT_S = 5.0


class _SteppableMonotonic(_Monotonic):
    """_Monotonic whose wall clock can be stepped, as NTP's first sync usually steps it:
    the wall clock moves and the monotonic one does not."""

    def step_wall(self, seconds):
        self._wall += datetime.timedelta(seconds=seconds)


def _awaiting_ntp(
    tmp_path, monkeypatch, answer, frames, script=None, timeouts=None,
    clock=None, frame_s=None, after_sleep=None, backend=None,
):
    """Record `frames` frames on a fake monotonic clock, with the NTP probe answering
    `answer(now)` at monotonic `now`, and return the monotonic times it was asked at.

    The run starts at 100 s, where the start's probe is asked. Frame n goes out at
    99 + n s, and the loop polls after it, so a poll is due on whole seconds. `script`
    queues requests as `_requesting` does: one queued at n is carried out after frame
    n + 1.

    The probe is injected as the tests above inject it, by replacing
    `cli.probe_ntp_synchronized`, and takes the real one's `timeout_s`. An answer of
    HANG holds the clock for the whole timeout, as subprocess.run does before it kills
    timedatectl, and then answers None. `timeouts`, if given, collects the timeout each
    call was made with, None where the caller left the default.

    `clock` replaces the default _Monotonic, `frame_s` makes each frame hold the pin
    that long (_FramesTakeRealTime), and `after_sleep()` runs after every sleep.
    `backend`, if given, replaces the FakeBackend, and `frame_s` is then not used."""
    if clock is None:
        clock = _Monotonic(wall_at_start=START + datetime.timedelta(seconds=0.5))
    asked = []

    def probe(timeout_s=None):
        asked.append(clock.now)
        if timeouts is not None:
            timeouts.append(timeout_s)
        reply = answer(clock.now)
        if reply is HANG:
            clock.now += PROBE_DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s
            return None
        return reply

    monkeypatch.setattr(cli, "probe_ntp_synchronized", probe)
    requests = queue.SimpleQueue()
    put = _requesting(requests, script or {})

    def tick_fn(seconds):
        put(seconds)
        clock.sleep(seconds)
        if after_sleep is not None:
            after_sleep()

    if backend is None:
        backend = FakeBackend() if frame_s is None else _FramesTakeRealTime(clock, frame_s)
    run(tmp_path, backend, clock.wall, frames, tick_fn, mono_fn=clock, requests=requests)
    return asked


def _segments(folder):
    return sorted(path.name for path in folder.glob("seg-*.log"))


def test_a_run_begun_before_ntp_synced_rolls_once_in_place_when_it_syncs(
    tmp_path, monkeypatch, caplog
):
    """THE REST OF THE BOOT-TRUST FIX, the user's option 2. After a power-up the box
    starts before NTP syncs, so its first segment says ntp_unsynchronized, and between
    sessions that segment can run for hours: judging trust again at an open or close
    (2b420c0) ends it only when a session changes. Once NTP first says yes, the box
    rolls once, into the same folder, and the new segment is judged as any roll is."""
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        asked = _awaiting_ntp(tmp_path, monkeypatch, lambda now: now >= 105, frames=40)

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    before, after = folder / segment_name(0), folder / segment_name(1)
    assert _trust(before) == (False, "ntp_unsynchronized")
    assert _trust(after) == (True, "")
    assert _has_trailer(before)
    first, second = read_log(before)[0], read_log(after)[0]
    assert second.process_uuid == first.process_uuid
    assert second.segment_index == first.segment_index + 1
    # Asked at the start, then 10 s later, when it said yes, and never again.
    assert asked == [100.0, 110.0]
    # The yes came after frame 11, and the roll after the next frame, frame 12.
    # One recording: the frame after the roll is the next value, and the manifest sees
    # neither a gap nor an overlap.
    assert len(_values(before)) == 12
    assert _values(after)[0] == _values(before)[-1] + 1
    summary = json.loads((folder / "manifest.json").read_text())
    assert (summary["gaps"], summary["overlaps"]) == ([], [])
    [line] = [m for m in caplog.messages if m.startswith("recording into")]
    assert "NTP synchronized" in line and str(after) in line


def test_the_ntp_roll_waits_for_a_frame_whose_barcode_was_kept(tmp_path, monkeypatch):
    """wl-preproc reads a roll in place as adjacent when the values step by 1 or 2 across
    it, and the grid can already skip one value there (wl_sync/log.py:135-141). A `B`
    dropped just before the roll would make the step 3, which reads as a gap. So the roll
    waits for a frame whose `B` was kept (spec §2.5, "Two rules")."""
    backend = FakeBackend()
    backend.miss_stamps({12})
    _awaiting_ntp(tmp_path, monkeypatch, lambda now: now >= 105, frames=40, backend=backend)

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    before, after = _values(folder / segment_name(0)), _values(folder / segment_name(1))
    # The yes came after frame 11, as in the test above. Frame 12's B was dropped, so the
    # roll came after frame 13, the first frame kept.
    assert len(before) == 12
    assert before[-1] == before[-2] + 2
    assert after[0] == before[-1] + 1


def test_a_session_roll_with_a_dropped_barcode_still_leaves_the_ntp_roll_nothing_to_do(
    tmp_path, monkeypatch
):
    """A session roll in the iteration the NTP roll waits into drops it, even when that
    iteration's `B` was dropped: the session's roll has already judged its own segment.
    Passes against the sources before Task 1.7, which never kept a roll due; it pins the
    order of the two rules, and fails if the dropped `B` is looked at first."""
    backend = FakeBackend()
    backend.miss_stamps({12})
    _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 105, frames=20,
        script={11: [_request("open", "r1")]}, backend=backend,
    )

    # The poll after frame 11 hears yes; the open is carried out after frame 12.
    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0)]
    assert _trust(session / segment_name(0)) == (True, "")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (lambda now: False, [100.0, 110.0, 120.0, 130.0]),
        # Each failed poll doubles the wait: 10 s, then 20 s, then 40 s.
        (lambda now: False if now < 101 else None, [100.0, 110.0, 130.0]),
    ],
    ids=["no", "no-then-cannot-ask"],
)
def test_no_roll_while_ntp_keeps_saying_no_or_cannot_be_asked(
    tmp_path, monkeypatch, answer, expected
):
    """None is "could not ask", never "yes": a probe that times out must not start a
    segment marked as synced. The run keeps asking."""
    asked = _awaiting_ntp(tmp_path, monkeypatch, answer, frames=35)

    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]
    assert asked == expected


def test_a_hanging_ntp_probe_costs_the_poll_no_frame(tmp_path, monkeypatch):
    """A POLL MUST NOT COST A FRAME. timedatectl can hang (D-Bus, timedated), and the
    probe's own timeout is 5 s, which a roll at an open or close can afford once, but a
    poll repeating every 10 s cannot: every hang would skip five barcodes. The poll asks
    with NTP_POLL_TIMEOUT_S instead, and a hang is None, which never rolls. The start's
    probe keeps the default.

    Frames hold the pin for 200 ms, as on the box (barcode.FRAME_US), so this measures
    the real budget: the ~0.75 s between a frame's end and the next grid point. With
    frames that took no time, any timeout up to a whole second would pass."""
    assert cli.NTP_POLL_TIMEOUT_S == 0.3
    timeouts = []
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: False if now < 101 else HANG, frames=80,
        timeouts=timeouts, frame_s=FRAME_US / 1e6,
    )

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0)]
    # Every frame went out on the grid: 80 consecutive values, none skipped.
    values = _values(folder / segment_name(0))
    assert values == list(range(values[0], values[0] + 80))
    assert timeouts == [None] + [cli.NTP_POLL_TIMEOUT_S] * (len(timeouts) - 1)
    # Polled as each frame ends, 0.2 s into its second. The waits double from the end of
    # each hung probe: 10 s, then 20, then 40.
    assert asked == pytest.approx([100.0, 110.2, 131.2, 172.2])


def test_the_poll_probe_is_bounded_by_its_own_timeout(monkeypatch):
    """The real probe, with subprocess injected: it hands timedatectl the timeout it is
    given, 5 s by default for a roll, and reads a timeout as None, "could not ask"."""
    seen = []

    def hangs(command, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", hangs)

    assert probe_ntp_synchronized(timeout_s=cli.NTP_POLL_TIMEOUT_S) is None
    assert probe_ntp_synchronized() is None
    assert seen == [cli.NTP_POLL_TIMEOUT_S, PROBE_DEFAULT_TIMEOUT_S]


def test_failed_polls_back_off_to_the_cap_and_a_definite_answer_resets(
    tmp_path, monkeypatch
):
    """After a failed poll the wait doubles, up to NTP_POLL_BACKOFF_MAX_S, so a box whose
    timedatectl keeps failing is asked ever less often. Any definite answer puts it
    back to NTP_POLL_INTERVAL_S. The cap is lowered here to 40 s, to reach it in a
    short run; the shipped one is 300 s."""
    assert cli.NTP_POLL_BACKOFF_MAX_S == 300
    monkeypatch.setattr(cli, "NTP_POLL_BACKOFF_MAX_S", 40.0)

    def answer(now):
        if now < 101 or now >= 210:
            return False
        return None  # the polls at 110, 130 and 170 fail

    asked = _awaiting_ntp(tmp_path, monkeypatch, answer, frames=131)

    # Waits of 10, 20, 40, then 40 again (80, capped), then back to 10 after the "no".
    assert asked == [100.0, 110.0, 130.0, 170.0, 210.0, 220.0, 230.0]
    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]


def test_a_run_that_cannot_ask_ntp_never_polls(tmp_path, monkeypatch):
    """A laptop or a container: no definite answer at the start, so nothing to wait for."""
    asked = _awaiting_ntp(tmp_path, monkeypatch, lambda now: None, frames=35)

    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]
    assert asked == [100.0]


def test_a_run_begun_synced_never_polls_and_never_rolls(tmp_path, monkeypatch):
    """Synced at the start, so its first segment is already judged synced. NTP saying
    no later is not asked about, and would not undo a roll this run never needed."""
    asked = _awaiting_ntp(tmp_path, monkeypatch, lambda now: now < 101, frames=35)

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0)]
    assert _trust(folder / segment_name(0)) == (True, "")
    assert asked == [100.0]


SYNC_STEP_S = 3600


@pytest.mark.parametrize("step_lands", ["at the poll that hears yes", "before that frame"])
def test_the_ntp_roll_is_judged_after_a_frame_taken_after_the_sync(
    tmp_path, monkeypatch, step_lands
):
    """NTP'S FIRST SYNC USUALLY STEPS THE CLOCK, and says it is synchronised in the same
    call. A step landing after this iteration's frame and before the poll, judged at
    once, compared the value from before the step with the clock after it, and spent
    the one roll on a segment flagged barcode_off_clock until a session changed. The
    roll now waits for the next frame, whose value is read after the step, so it is
    trusted wherever the step lands. Found by review, with a probe this is built on."""
    clock = _SteppableMonotonic(wall_at_start=START + datetime.timedelta(seconds=0.5))
    stepped = []

    def step_once():
        if not stepped:
            clock.step_wall(SYNC_STEP_S)
            stepped.append(clock.now)

    def answer(now):
        if now < 105:
            return False
        if step_lands == "at the poll that hears yes":
            step_once()
        return True

    def after_sleep():
        if step_lands == "before that frame" and clock.now >= 105:
            step_once()

    asked = _awaiting_ntp(
        tmp_path, monkeypatch, answer, frames=40, clock=clock, after_sleep=after_sleep
    )

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    before, after = folder / segment_name(0), folder / segment_name(1)
    assert asked == [100.0, 110.0]
    assert _trust(before) == (False, "ntp_unsynchronized")
    assert _trust(after) == (True, "")
    # The jump is inside the segment already flagged, not across the roll.
    assert _values(before)[-1] - _values(before)[0] > SYNC_STEP_S
    assert _values(after)[0] == _values(before)[-1] + 1


def test_a_session_roll_in_the_next_iteration_leaves_the_ntp_roll_nothing_to_do(
    tmp_path, monkeypatch
):
    """The NTP roll waits one iteration. A session opened in that iteration has already
    asked NTP and judged its own segment, so the NTP roll is dropped rather than adding
    an empty segment straight after it."""
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 105, frames=20,
        script={11: [_request("open", "r1")]},
    )

    # The poll after frame 11 hears yes; the open is carried out after frame 12.
    assert asked == [100.0, 110.0, 111.0]
    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0)]
    assert _trust(session / segment_name(0)) == (True, "")


def test_ntp_is_asked_at_most_once_every_ten_seconds(tmp_path, monkeypatch):
    """A probe costs 9-62 ms on the box, on the run loop's thread, where a frame is due
    every second. Ten seconds is counted from the last time anything asked, a session
    open's own probe included."""
    assert cli.NTP_POLL_INTERVAL_S == 10
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: False, frames=35,
        script={4: [_request("open", "r1")]},
    )

    # The start; the open, carried out after frame 5; then every 10 s from the open.
    assert asked == [100.0, 104.0, 114.0, 124.0, 134.0]
    assert _segments(tmp_path / "2026-08-16_01" / "syncbox") == [segment_name(0)]


def test_a_session_opened_after_the_ntp_roll_still_rolls(tmp_path, monkeypatch):
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 105, frames=20,
        script={14: [_request("open", "r1")]},
    )

    between = unassigned_dir(tmp_path, DAY)
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(between) == [segment_name(0), segment_name(1)]
    assert _segments(session) == [segment_name(0)]
    assert _trust(between / segment_name(1)) == (True, "")
    assert _has_trailer(between / segment_name(1))
    assert len(_values(between / segment_name(1))) == 3  # frames 13 to 15
    assert _trust(session / segment_name(0)) == (True, "")
    assert len({h.process_uuid for h in _headers(tmp_path).values()}) == 1
    assert asked == [100.0, 110.0, 114.0]


def test_a_session_opened_before_sync_whose_probe_says_yes_leaves_no_ntp_roll(
    tmp_path, monkeypatch
):
    """The open's own probe found NTP synced and judged the session's segment on it, so
    the run is no longer waiting: no poll, and no roll in the session's folder."""
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 103, frames=30,
        script={4: [_request("open", "r1")]},
    )

    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0)]
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0)]
    assert _trust(session / segment_name(0)) == (True, "")
    assert asked == [100.0, 104.0]


def test_a_session_opened_before_sync_rolls_in_its_own_folder_when_ntp_syncs(
    tmp_path, monkeypatch
):
    """The case wl-preproc will see. The open's probe still says no, so the session's
    first segment is unsynchronised; NTP then syncs and the roll lands in the session's
    folder, as seg-001 of the same process, with no gap between the two."""
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 108, frames=20,
        script={4: [_request("open", "r1")]},
    )

    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0), segment_name(1)]
    before, after = session / segment_name(0), session / segment_name(1)
    assert _trust(before) == (False, "ntp_unsynchronized")
    assert _trust(after) == (True, "")
    assert _has_trailer(before)
    first, second = read_log(before)[0], read_log(after)[0]
    assert (second.session_id, second.segment_index) == (first.session_id, 1)
    assert second.process_uuid == first.process_uuid
    assert _values(after)[0] == _values(before)[-1] + 1
    summary = json.loads((session / "manifest.json").read_text())
    assert [s["file"] for s in summary["segments"]] == [segment_name(0), segment_name(1)]
    assert summary["gaps"] == []
    assert asked == [100.0, 104.0, 114.0]


def test_once_rolled_for_ntp_a_run_stops_asking_even_if_ntp_says_no_again(
    tmp_path, monkeypatch
):
    """Once, for the first sync. A session open later in the run asks for itself, and
    its "no" judges that segment, but the poll does not come back."""
    asked = _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: 105 <= now < 115, frames=40,
        script={19: [_request("open", "r1")]},
    )

    assert _segments(unassigned_dir(tmp_path, DAY)) == [segment_name(0), segment_name(1)]
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0)]
    assert _trust(session / segment_name(0)) == (False, "ntp_unsynchronized")
    assert asked == [100.0, 110.0, 119.0]


def test_a_reused_open_still_rolls_nowhere_while_the_run_waits_for_ntp(
    tmp_path, monkeypatch
):
    """The roll in place is its own path. The early return that keeps a retried open
    (same request_id, so `reused`) from rolling is unchanged."""
    first, retry = _request("open", "r1"), _request("open", "r1")
    _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: False, frames=10,
        script={2: [first], 6: [retry]},
    )

    assert retry.reply.result(timeout=0).reused
    session = tmp_path / "2026-08-16_01" / "syncbox"
    assert _segments(session) == [segment_name(0)]
    assert len(_values(session / segment_name(0))) == 7  # frames 4 to 10


# --- the NTP roll, when the sync changed the day (D8, 2026-10-30) ------------------------

DEAD_RTC_DAY = datetime.date(2020, 1, 1)  # where a dead clock's value 0 names its folder
DEAD_RTC_AT_START = datetime.datetime(2000, 1, 1, tzinfo=UTC)  # what a dead CR2032 reads


def _dead_rtc_clock():
    """A box whose RTC is dead: the wall clock reads 2000-01-01 until the sync steps it to
    START (2026-08-16, which the barcode value then names as its day)."""
    return _SteppableMonotonic(wall_at_start=DEAD_RTC_AT_START)


def _ntp_steps_to_start(clock):
    """An NTP probe's answer: no before 105 s, then yes, the first yes stepping the wall
    clock to START as NTP's first sync does."""
    stepped = []

    def answer(now):
        if now < 105:
            return False
        if not stepped:
            clock.step_wall((START - clock.wall()).total_seconds())
            stepped.append(now)
        return True

    return answer


def test_a_dead_rtc_whose_sync_steps_across_midnight_rolls_into_todays_unassigned(
    tmp_path, monkeypatch, caplog
):
    """THE D8 DEFECT, found in the 2026-10-30 final review. A box whose RTC is dead starts
    on 2000-01-01, so with no session open it records into unassigned/2020-01-01. NTP's
    first sync steps the clock to today and the roll in place kept the trusted segment in
    that folder, with that day's stand-in id and today's barcode values. The roll now
    follows the day the value after the step names, as a close does."""
    clock = _dead_rtc_clock()
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        _awaiting_ntp(
            tmp_path, monkeypatch, _ntp_steps_to_start(clock), frames=40, clock=clock
        )

    old, new = unassigned_dir(tmp_path, DEAD_RTC_DAY), unassigned_dir(tmp_path, DAY)
    assert _segments(old) == [segment_name(0)]
    assert _segments(new) == [segment_name(0)]  # the index its own folder gives
    before, after = old / segment_name(0), new / segment_name(0)
    assert _has_trailer(before)
    assert _trust(before)[0] is False
    assert _trust(after) == (True, "")
    first, second = read_log(before)[0], read_log(after)[0]
    assert first.session_id == str(unassigned_id(DEAD_RTC_DAY))
    assert second.session_id == str(unassigned_id(DAY))
    assert second.process_uuid == first.process_uuid
    assert second.segment_index == 0
    # The old segment began on 2020's values; the new one's first value is read after
    # the step and names today. (The frame between the poll and the roll is in the old one.)
    assert cli._barcode_day(_values(before)[0]) == DEAD_RTC_DAY
    assert cli._barcode_day(_values(after)[0]) == DAY
    [line] = [m for m in caplog.messages if m.startswith("recording into")]
    assert "NTP synchronized" in line and str(after) in line
    # Each folder's manifest was rebuilt: the one left lists its closed segment.
    for folder in (old, new):
        assert (folder / "manifest.json").exists()


def test_a_session_open_when_ntp_syncs_across_midnight_is_not_split(tmp_path, monkeypatch):
    """A session is never split across folders by a clock step: with one open, the roll
    stays in the session's folder even though the step changed the day."""
    clock = _dead_rtc_clock()
    _awaiting_ntp(
        tmp_path, monkeypatch, _ntp_steps_to_start(clock), frames=40, clock=clock,
        script={2: [_request("open", "r1")]},
    )

    session = tmp_path / f"{DEAD_RTC_DAY.isoformat()}_01" / "syncbox"
    assert _segments(session) == [segment_name(0), segment_name(1)]
    first, second = (read_log(session / segment_name(n))[0] for n in (0, 1))
    assert second.session_id == first.session_id
    assert second.process_uuid == first.process_uuid
    assert _trust(session / segment_name(1)) == (True, "")
    assert not unassigned_dir(tmp_path, DAY).exists()


@pytest.mark.parametrize("rtc", ["still dead", "now right"])
def test_a_restart_after_the_day_changing_roll_resumes_in_todays_unassigned(
    tmp_path, monkeypatch, rtc
):
    """A restart agrees with where the roll left the recording, one segment after the
    roll's. With the RTC still dead, _resume names the folder from the checkpoint's
    value (today's); with a clock that now reads right, from the clock's own date."""
    clock = _dead_rtc_clock()
    _awaiting_ntp(
        tmp_path, monkeypatch, _ntp_steps_to_start(clock), frames=40, clock=clock
    )

    dead = rtc == "still dead"
    restart = _SteppableMonotonic(
        wall_at_start=DEAD_RTC_AT_START if dead else clock.wall()
    )
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda timeout_s=None: True)
    run(tmp_path, FakeBackend(), restart.wall, 3, restart.sleep, mono_fn=restart)

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    assert _segments(unassigned_dir(tmp_path, DEAD_RTC_DAY)) == [segment_name(0)]
    header = read_log(folder / segment_name(1))[0]
    assert header.session_id == str(unassigned_id(DAY))
    assert _trust(folder / segment_name(1)) == (
        (False, "clock_before_epoch") if dead else (True, "")
    )


# --- the frame grid after a wall-clock step (2026-10-30) -------------------------------

REPLACED = "frame grid re-placed"


class _SlewingMonotonic(_SteppableMonotonic):
    """_SteppableMonotonic whose wall clock runs `ppm` parts per million fast (slow, if
    negative) against the monotonic one: NTP's slew at its 500 ppm maximum, in a world
    where the monotonic clock does not share it. On Linux CLOCK_MONOTONIC is slewed with
    the wall clock, so the box never sees even this much."""

    def __init__(self, ppm, **kwargs):
        super().__init__(**kwargs)
        self._rate = 1 + ppm * 1e-6

    def wall(self):
        elapsed = (self.now - self._start) * self._rate
        return self._wall + datetime.timedelta(seconds=elapsed)


class _ReadsTheWall(_FramesTakeRealTime):
    """Records the wall clock each frame reads, and steps it by `steps[n]` seconds while
    frame n (1-based) is on the wire, after that frame's value was read.

    No fake time passes between a value's reading and its frame's lead edge, so the wall
    clock at the edge is the one the value was read from."""

    def __init__(self, clock, frame_s=FRAME_US / 1e6, steps=None):
        super().__init__(clock, frame_s)
        self.walls: list[datetime.datetime] = []
        self._steps = steps or {}

    def emit_pulses(self, pin, pulses):
        self.walls.append(self._clock.wall())
        stamp = super().emit_pulses(pin, pulses)
        step = self._steps.get(len(self.walls))
        if step is not None:
            self._clock.step_wall(step)
        return stamp


def _record_reading_the_wall(tmp_path, clock, frames, steps=None, frame_s=FRAME_US / 1e6):
    """Record `frames` frames paced on `clock`; return the backend and the values."""
    backend = _ReadsTheWall(clock, frame_s, steps)
    folder = run(tmp_path, backend, clock.wall, frames, clock.sleep, mono_fn=clock)
    return backend, _values(folder / segment_name(0))


def _phase(wall):
    """How far into its second the wall clock read."""
    return wall.microsecond / 1e6


def test_a_step_onto_a_second_edge_re_places_the_grid_and_no_value_runs_ahead(
    tmp_path, caplog
):
    """THE GRID WAS PLACED ONCE, AT START, so a wall-clock step moved it for the rest of
    the process, and since 56a89f8 a step is expected: a box that powers up before NTP
    syncs rolls in place at the first sync, which steps a dead or drifted RTC. Here a
    forward step of just under half a second puts every frame 2 ms before a second
    edge. Frame 11, the first to read the stepped clock, wakes 4 ms late -- one tick of
    the box's 250 Hz kernel timer, which the 2026-10-19 soak saw 420 times in 4 h -- and
    reads the next second. The floor (`last + 1`) then carried that second: every value
    after it ran one second ahead of the wall clock it is meant to BE, for the rest of
    the process, and the clamp warned that a healthy clock was not moving forwards.

    The step is now seen at frame 11's own reading, and the grid is placed mid-second
    again, as at start."""
    clock = _SteppableMonotonic(jitter=[0.0] * 9 + [0.004])  # frames 11, 21: 4 ms late
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        backend, values = _record_reading_the_wall(tmp_path, clock, 30, steps={10: 0.498})

    # Every value is the wall second its frame read: none runs a second ahead.
    assert values == [value_from_clock(wall) for wall in backend.walls]
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    [line] = [m for m in caplog.messages if REPLACED in m]
    assert "0.998" in line  # where the stepped grid had put the frames
    # Frame 11 read the stepped clock where the old grid put it, 4 ms late; from frame
    # 12 on, every frame reads mid-second again.
    assert _phase(backend.walls[10]) == pytest.approx(0.002, abs=1e-6)
    assert all(0.5 <= _phase(wall) <= 0.505 for wall in backend.walls[11:])


def test_a_step_of_seconds_and_a_fraction_is_followed_and_the_grid_re_placed(
    tmp_path, caplog
):
    """What NTP's first sync gives an RTC that read slow: a step of a whole number of
    seconds and a fraction, landing whenever it lands -- here while frame 10 is on the
    wire. The values jump with the clock and then follow it, and the grid moves to the
    stepped clock's half seconds rather than staying where the old clock had them."""
    clock = _SteppableMonotonic(jitter=(0.0, 0.004))
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        backend, values = _record_reading_the_wall(
            tmp_path, clock, 30, steps={10: SYNC_STEP_S + 0.65}
        )

    assert values == [value_from_clock(wall) for wall in backend.walls]
    # The step, a second of running, and the step's 0.65 s carrying frame 11 over an
    # edge; then one value a second.
    assert values[10] - values[9] == SYNC_STEP_S + 2
    assert values[10:] == list(range(values[10], values[10] + 20))
    [line] = [m for m in caplog.messages if REPLACED in m]
    assert "0.150" in line
    assert all(0.5 <= _phase(wall) <= 0.505 for wall in backend.walls[11:])
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("fraction", [0.0, 0.35, 0.65])
@pytest.mark.parametrize("step_lands", ["at the poll that hears yes", "before that frame"])
def test_an_ntp_step_with_a_fraction_re_places_the_grid_and_its_roll_is_trusted(
    tmp_path, monkeypatch, caplog, step_lands, fraction
):
    """What NTP's first sync really gives: whole seconds and a fraction, on frames that
    hold the pin for 200 ms as on the box. The whole-hour step of the 56a89f8 test above
    leaves the grid where it was; a fraction moves it, so the grid is placed again after
    the first frame that reads the stepped clock, and the roll is still judged after a
    frame read after the step.

    THE ONE VALUE A RE-PLACE MAY SKIP CAN FALL ACROSS THE ROLL. A step landing at the poll
    that hears yes is first read by the next frame, the roll follows that frame, and only
    then is the grid placed again. At 0.35 s that frame reads 0.85 into its second, its
    200 ms and the decoder's idle pass the next half second, and the new segment begins
    two values on. Both values are the seconds their frames read, 1.65 s apart."""
    clock = _SteppableMonotonic(wall_at_start=START + datetime.timedelta(seconds=0.5))
    stepped = []

    def step_once():
        if not stepped:
            clock.step_wall(SYNC_STEP_S + fraction)
            stepped.append(clock.now)

    def answer(now):
        if now < 105:
            return False
        if step_lands == "at the poll that hears yes":
            step_once()
        return True

    def after_sleep():
        if step_lands == "before that frame" and clock.now >= 105:
            step_once()

    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        asked = _awaiting_ntp(
            tmp_path, monkeypatch, answer, frames=40, clock=clock,
            frame_s=FRAME_US / 1e6, after_sleep=after_sleep,
        )

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    assert _trust(folder / segment_name(0)) == (False, "ntp_unsynchronized")
    assert _trust(folder / segment_name(1)) == (True, "")
    # Asked at the start and once more, when it said yes. When that poll falls depends
    # on where the step put the grid.
    assert asked[0] == 100.0 and len(asked) == 2
    re_placed = [m for m in caplog.messages if REPLACED in m]
    assert len(re_placed) == (1 if fraction > cli.GRID_PHASE_TOLERANCE_S else 0)
    before, after = _values(folder / segment_name(0)), _values(folder / segment_name(1))
    assert before[-1] - before[0] > SYNC_STEP_S  # the jump is in the flagged segment
    assert after[0] - before[-1] in (1, 2)
    assert after == list(range(after[0], after[0] + len(after)))


def test_the_re_place_line_never_says_a_whole_second(tmp_path, caplog):
    """A phase within half a millisecond of the next edge printed as "1.000 s into its
    second", which no clock reads. It is rounded before it wraps, so it says 0.000."""
    clock = _SteppableMonotonic()
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        _record_reading_the_wall(tmp_path, clock, 14, steps={10: 0.4997})

    [line] = [m for m in caplog.messages if REPLACED in m]
    assert "falling 0.000 s into" in line, line


@pytest.mark.parametrize("ppm", [500, -500], ids=["slewing fast", "slewing slow"])
def test_jitter_and_slew_alone_never_re_place_the_grid(tmp_path, caplog, ppm):
    """NOT CRYING WOLF. Wakes 4 ms late move when a frame reads the wall clock, not
    where the wall clock is against the monotonic one, so they cannot move the grid. Slew
    can only if the monotonic clock does not share it, which on the box it does: here it
    does not, at NTP's 500 ppm maximum, for five minutes. The frames drift 0.15 s from
    mid-second, every one still reads its own second, and the grid stays put."""
    clock = _SlewingMonotonic(ppm, jitter=(0.0, 0.004, 0.004, 0.0, 0.000_3))
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        backend, values = _record_reading_the_wall(tmp_path, clock, 300)

    assert [m for m in caplog.messages if REPLACED in m] == []
    assert values == [value_from_clock(wall) for wall in backend.walls]
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    # The slew really did move the frames: 299 s at 500 ppm is 0.15 s.
    assert abs(_phase(backend.walls[-1]) - 0.5) == pytest.approx(0.15, abs=0.005)


@pytest.mark.parametrize("frame_s", [FRAME_US / 1e6, 0.0], ids=["200 ms frame", "instant"])
@pytest.mark.parametrize("step_s", [0.498, 0.5, 0.7, 0.3, -0.3, -0.45])
def test_the_frame_after_a_re_place_leaves_the_decoder_idle_and_reads_a_new_second(
    tmp_path, caplog, step_s, frame_s
):
    """Re-placing moves the grid under a frame that has already gone out, so the next
    grid point can sit less than a second after it. The next frame must still leave the
    decoder its IDLE_MIN_US of LOW, and must read a later second than the frame before:
    two frames in one second would clamp a healthy clock. That holds wherever the step
    puts the frame that sees it, including exactly on a second edge (0.5 s, with frame
    11 waking on time), and however long a frame takes."""
    clock = _SteppableMonotonic(jitter=(0.004, 0.0))  # frame 11 wakes on time
    with caplog.at_level(logging.INFO, logger="wl_sync.cli"):
        backend, values = _record_reading_the_wall(
            tmp_path, clock, 14, steps={10: step_s}, frame_s=frame_s
        )

    idles = [
        later - earlier
        for earlier, later in zip(backend.frame_ends, backend.frame_starts[1:])
    ]
    assert min(idles) >= IDLE_MIN_US / 1e6, idles
    seconds = [value_from_clock(wall) for wall in backend.walls]
    assert all(later > earlier for earlier, later in zip(seconds, seconds[1:])), seconds
    assert values == seconds
    assert len([m for m in caplog.messages if REPLACED in m]) == 1


# ---- the endpoint, wired into `wl-sync record` (Task 7) ---------------------------------

WAIT_S = 10.0
TOKEN = "s3cret-token-value"


class _Rig:
    """run() on a thread against a real endpoint on port 0. The tick sleeps 0.05 s so an
    HTTP call can land between two iterations; every call and join is bounded so a
    regression fails instead of hanging the suite."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.requests = queue.SimpleQueue()
        self.server = serve(tmp_path, self.requests, TOKEN, "127.0.0.1", 0)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.folder = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _tick(self, _seconds):
        # Paced while the test is talking to the endpoint, instant afterwards, so a
        # slow CI box cannot let the loop end before the HTTP calls land.
        if not self.done.is_set():
            time.sleep(0.05)

    def _run(self):
        self.folder = run(
            out=self.tmp_path,
            backend=FakeBackend(),
            now_fn=_clock_from(START),
            duration_s=200,
            tick_fn=self._tick,
            requests=self.requests,
        )

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if method == "POST":
            request.add_header("Authorization", f"Bearer {TOKEN}")
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=WAIT_S) as response:
            return json.loads(response.read())

    def finish(self):
        self.done.set()
        self.thread.join(WAIT_S)
        assert not self.thread.is_alive(), "run() did not finish"
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def rig(tmp_path):
    made = _Rig(tmp_path)
    yield made
    # On ANY exit, a failed assertion included: without this a failing test left run()
    # ticking at 0.05 s for its 200 frames (about 10 s) before the thread ended.
    made.done.set()
    if made.thread.is_alive():
        made.thread.join(WAIT_S)
    made.server.shutdown()
    made.server.server_close()


def test_a_task_pc_can_open_and_close_a_session_end_to_end(rig):
    """The whole path a task PC takes: POST open, GET the id back, POST close, over real
    HTTP into a running loop. The session's syncbox/ holds a segment closed with a
    trailer, so the visit's recording really landed under its id.

    Closes a coverage gap: run(requests=) and serve() were each tested alone, but
    nothing proved that a real HTTP call reaches a running loop and moves the
    recording."""
    rig.thread.start()
    opened = rig.call("POST", "/session/open", {"request_id": "r1"})
    session_id = opened["session_id"]
    assert rig.call("GET", "/session")["session_id"] == session_id

    assert rig.call("POST", "/session/close", {"session_id": session_id})["closed"]
    rig.finish()

    segments = sorted((rig.tmp_path / session_id / "syncbox").glob("*.log"))
    assert segments and all(_has_trailer(path) for path in segments)


def test_a_retried_open_after_a_lost_reply_gets_the_same_session(rig):
    """The task PC lost the reply to its open and asks again with the same request_id.
    It must get the session it already opened, not a second one, and the answer says the
    session is still open (`closed` null).

    Closes a coverage gap: idempotence was tested on the request queue, never through
    HTTP into the live loop, which is where a lost reply actually happens."""
    rig.thread.start()
    first = rig.call("POST", "/session/open", {"request_id": "r1"})
    second = rig.call("POST", "/session/open", {"request_id": "r1"})
    assert second["session_id"] == first["session_id"]
    assert second["closed"] is None
    rig.finish()


def _unassigned_segments(root):
    return list((root / "unassigned").glob("*/syncbox/*.log"))


def test_an_unreadable_token_file_logs_an_error_and_the_box_still_records(tmp_path, caplog):
    """R18: under Restart=always anything that raises at start-up is a crash loop that
    records nothing, and the recording is the box's whole job. A token file that is
    missing serves GET only, says so at ERROR with the path, and records."""
    missing = tmp_path / "no-such-token"
    with caplog.at_level(logging.ERROR):
        code = main(
            ["record", "--fake", "--out", str(tmp_path / "rig"), "--duration", "1",
             "--listen", "127.0.0.1:0", "--session-token-file", str(missing)]
        )
    assert code == 0
    assert _unassigned_segments(tmp_path / "rig")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(str(missing) in message for message in errors)


def test_an_empty_token_file_logs_an_error_and_the_box_still_records(tmp_path, caplog):
    """An empty file (after stripping) would otherwise become the token "" -- a secret
    anyone could present. It is treated as no token, loudly."""
    token_file = tmp_path / "token"
    token_file.write_text("  \n")
    with caplog.at_level(logging.ERROR):
        code = main(
            ["record", "--fake", "--out", str(tmp_path / "rig"), "--duration", "1",
             "--listen", "127.0.0.1:0", "--session-token-file", str(token_file)]
        )
    assert code == 0
    assert _unassigned_segments(tmp_path / "rig")
    assert any(r.levelno >= logging.ERROR and str(token_file) in r.getMessage()
               for r in caplog.records)


def test_a_listen_address_that_cannot_bind_logs_an_error_and_the_box_still_records(
    tmp_path, caplog
):
    """R18: the port is already taken. The box records without the endpoint rather than
    crash-looping, and the token is never in the log."""
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    try:
        with caplog.at_level(logging.INFO):
            code = main(
                ["record", "--fake", "--out", str(tmp_path / "rig"), "--duration", "1",
                 "--listen", "127.0.0.1:%d" % blocker.getsockname()[1],
                 "--session-token-file", str(token_file)]
            )
    finally:
        blocker.close()
    assert code == 0
    assert _unassigned_segments(tmp_path / "rig")
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("value", ["8740", "host:", ":8740", "host:abc", "host:65536", "host:-1"])
def test_a_malformed_listen_value_is_an_argparse_error(tmp_path, value):
    """A typo in --listen is configuration the operator must see, not something to
    swallow: argparse exits 2. Closes a coverage gap: --listen's parsing had no test, and
    an unknown flag also exits 2, so only the positive case below makes this one mean
    something."""
    with pytest.raises(SystemExit) as excinfo:
        main(["record", "--fake", "--out", str(tmp_path), "--duration", "1",
              "--listen", value])
    assert excinfo.value.code == 2


def test_a_well_formed_listen_value_is_accepted(tmp_path):
    """The positive half of the malformed-value test: a valid HOST:PORT must run."""
    assert main(["record", "--fake", "--out", str(tmp_path), "--duration", "1",
                 "--listen", "127.0.0.1:0"]) == 0


# ---- recorded untested paths, from the session-identity build ---------------------------


def test_a_corrupt_state_file_records_between_sessions_and_logs_it(tmp_path, caplog):
    """Pins present behaviour (it does not discriminate a regression in read_open, which
    its unit tests do). At the run level a corrupt state file must not refuse to start:
    the run records into unassigned/<day>/syncbox, logs the corruption at ERROR naming the
    file, and leaves the file where it is for the operator to look at."""
    state = tmp_path / sessions.SESSION_STATE_NAME
    state.write_text("{not json")
    with caplog.at_level(logging.ERROR):
        folder = run(
            out=tmp_path,
            backend=FakeBackend(),
            now_fn=_clock_from(START),
            duration_s=2,
            tick_fn=lambda _s: None,
        )
    assert folder == unassigned_dir(tmp_path, DAY)
    header, _ = read_log(folder / segment_name(0))
    assert header.session_id == "2026-08-16_00"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("is corrupt" in m and str(state) in m for m in errors), errors
    assert state.read_text() == "{not json"


def test_a_hand_deleted_session_folder_is_recreated_and_recorded_into(tmp_path):
    """Pins present behaviour. The state file says 2026-08-16_02 is open but its folder
    was deleted by hand. The run does not refuse and does not fall back to unassigned/:
    it mkdirs the folder again and records there, as segment 0 of a fresh folder, still
    under the open session's id. The state file is untouched, so the session stays open."""
    session_id = SessionId.parse("2026-08-16_02")
    write_open(
        tmp_path, OpenSession(session_id, datetime.datetime(2026, 8, 16, 8, 0, tzinfo=UTC), "r1")
    )
    folder = tmp_path / "2026-08-16_02" / "syncbox"
    assert not folder.exists()

    result = run(
        out=tmp_path,
        backend=FakeBackend(),
        now_fn=_clock_from(START),
        duration_s=2,
        tick_fn=lambda _s: None,
    )
    assert result == folder
    header, _ = read_log(folder / segment_name(0))
    assert header.session_id == "2026-08-16_02"
    assert not (tmp_path / "unassigned").exists()
    assert read_open(tmp_path).session_id == session_id


def test_a_token_file_without_listen_logs_a_warning_and_records(tmp_path, caplog):
    """Pins present behaviour: with no --listen there is no endpoint for a token to
    protect, so the flag is ignored, loudly, at WARNING, and the box records."""
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    with caplog.at_level(logging.WARNING):
        code = main(
            ["record", "--fake", "--out", str(tmp_path / "rig"), "--duration", "1",
             "--session-token-file", str(token_file)]
        )
    assert code == 0
    assert _unassigned_segments(tmp_path / "rig")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("--session-token-file" in r.getMessage() and "--listen" in r.getMessage()
               for r in warnings)
    assert TOKEN not in caplog.text


def test_a_present_but_unreadable_token_file_logs_an_error_and_the_box_records(
    tmp_path, caplog
):
    """Pins present behaviour: a token file that exists but cannot be read (mode 000) is
    the same as a missing one -- ERROR with the path and the exception type, GET-only
    endpoint, and the box records. It does not refuse to start (R18)."""
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    token_file.chmod(0)
    try:
        if os.access(token_file, os.R_OK):
            pytest.skip("this user can read a mode-000 file (root)")
        with caplog.at_level(logging.ERROR):
            code = main(
                ["record", "--fake", "--out", str(tmp_path / "rig"), "--duration", "1",
                 "--listen", "127.0.0.1:0", "--session-token-file", str(token_file)]
            )
    finally:
        token_file.chmod(0o600)
    assert code == 0
    assert _unassigned_segments(tmp_path / "rig")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(str(token_file) in m and "PermissionError" in m and "GET only" in m
               for m in errors), errors
    assert TOKEN not in caplog.text


def test_the_endpoint_is_shut_down_when_run_raises(tmp_path, monkeypatch):
    """main() starts the endpoint before run() and must stop it on the way out of a run
    that raises: no serving thread is left, and the port refuses connections. A
    try/finally removed from main() fails this."""
    servers = []
    real_serve = cli.serve

    def spy(*args, **kwargs):
        servers.append(real_serve(*args, **kwargs))
        return servers[-1]

    def boom(**_kwargs):
        raise RuntimeError("run blew up")

    monkeypatch.setattr(cli, "serve", spy)
    monkeypatch.setattr(cli, "run", boom)
    with pytest.raises(RuntimeError, match="run blew up"):
        main(["record", "--fake", "--out", str(tmp_path), "--duration", "1",
              "--listen", "127.0.0.1:0"])

    (server,) = servers
    port = server.server_address[1]
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline and any(
        t.name == "wl-sync-endpoint" and t.is_alive() for t in threading.enumerate()
    ):
        time.sleep(0.01)
    assert not [t for t in threading.enumerate() if t.name == "wl-sync-endpoint"]
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2.0).close()


# THE ROLL BARRIER (session-identity spec Sec.3.4, amended 2026-10-30; wl-preproc's ask
# 4b). wl-preproc never reads unassigned/, so a code word captured before a close and
# delivered after the roll was a session's last code lost.


class _SlowWordPump(FakeBackend):
    """Delivers code words from a pump thread of its own, as Rp1Backend does, on a clock
    that runs in real time, and says how far it has delivered by the word pump's rule: the
    clock read just before a poll, once that poll has delivered everything it found.

    A word can be held back from the pump -- a pump starved of the CPU, say. A held pump
    does not poll at all, so it cannot move its mark past what it holds."""

    _LATE_S = 0.05  # how long after the run loop first asks a "late" word is delivered

    def __init__(self) -> None:
        super().__init__()
        self._origin_ns = time.monotonic_ns()
        self._lock = threading.Lock()
        self._captured: collections.deque = collections.deque()
        self._held_until = 0.0
        self._held_until_asked = False
        self._then = None
        self._through = None
        self._stop = threading.Event()
        self._thread = None
        self.asked: list[float] = []  # time.monotonic() at each delivered_through()

    def now_us(self) -> int:
        return ((time.monotonic_ns() - self._origin_ns) // 1000) & 0xFFFF_FFFF

    def capture(self, word, *, held_s=0.0, then=None):
        """A strobe now. The pump does not reach it for `held_s`. `then` runs on the pump
        thread right after the poll that delivers it."""
        with self._lock:
            self._captured.append((self.now_us(), word))
            self._held_until = max(self._held_until, time.monotonic() + held_s)
            self._then = then

    def capture_delivered_late(self, word, *, then=None):
        """A strobe now, which the pump reaches only _LATE_S after the run loop next asks
        how far delivery has got -- after the boundary is read, wherever that falls."""
        with self._lock:
            self._captured.append((self.now_us(), word))
            self._held_until = math.inf
            self._held_until_asked = True
            self._then = then

    def start_strobed_capture(self, data_base, data_count, strobe_pin, on_word, on_loss=None):
        super().start_strobed_capture(data_base, data_count, strobe_pin, on_word, on_loss)
        self._thread = threading.Thread(target=self._pump, name="slow-words", daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        began = self.now_us()
        with self._lock:
            if time.monotonic() < self._held_until:
                return
            found = list(self._captured)
            self._captured.clear()
            then, self._then = (self._then, None) if found else (None, self._then)
        for tick, word in found:
            self._on_word(tick, word)
        self._through = (began - 1) & 0xFFFF_FFFF
        if then is not None:
            then()

    def _pump(self) -> None:
        while not self._stop.is_set():
            self._poll()
            time.sleep(0.001)

    def delivered_through(self):
        self.asked.append(time.monotonic())
        with self._lock:
            if self._held_until_asked:
                self._held_until_asked = False
                self._held_until = time.monotonic() + self._LATE_S
        marks = super().delivered_through()
        marks["event-code capture"] = self._through
        return marks

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        with self._lock:
            self._held_until = 0.0  # the final drain, as Rp1Backend's
        self._poll()


def _visit(tmp_path, backend, before_close, iterations=5):
    """Open at iteration 2, then at the end of iteration 3 run `before_close` and request
    the close, which iteration 4 carries out after its frame. Returns each CodeWord's tick
    by word, in the session's segment and in the between-sessions segment after it."""
    requests = queue.SimpleQueue()
    opening, closing = _request("open", "r1"), _request("close", "2026-08-16_01")
    state = {"n": 0}

    def tick_fn(_seconds):
        state["n"] += 1
        if state["n"] == 1:
            requests.put(opening)
        if state["n"] == 3:
            before_close()
            requests.put(closing)

    run(tmp_path, backend, _clock_from(START), iterations, tick_fn, requests=requests)
    assert closing.reply.result(timeout=0) == SessionId.parse("2026-08-16_01")

    def words(path):
        _, records = read_log(path)
        return {r.word: r.tick_us for r in records if isinstance(r, CodeWord)}

    session = tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0)
    assert _has_trailer(session)
    return words(session), words(unassigned_dir(tmp_path, DAY) / segment_name(1))


def _boundaries(monkeypatch):
    """Every boundary a roll takes, as the run loop hands it to the barrier. Taken at the
    barrier rather than at Recorder.now, which the run loop also reads once a frame, so
    that a run of dropped barcodes cannot lose a counter wrap: those reads are not
    boundaries."""
    taken = []
    original = cli._await_delivery

    def spy(recorder, boundary):
        taken.append(boundary)
        original(recorder, boundary)

    monkeypatch.setattr(cli, "_await_delivery", spy)
    return taken


def test_a_word_captured_before_a_close_but_delivered_after_it_lands_in_the_session(
    tmp_path,
):
    """The ask. The word is captured before the close is requested, and its pump delivers
    it only after the run loop has taken the close. It used to land in whichever segment
    was open when it drained -- the between-sessions one, which wl-preproc never reads --
    and the session lost its last code."""
    backend = _SlowWordPump()
    in_session, after = _visit(
        tmp_path, backend, lambda: backend.capture_delivered_late(0x42)
    )
    assert 0x42 in in_session, f"landed after the close: {after}"
    assert 0x42 not in after


def test_a_word_captured_after_the_boundary_lands_in_the_new_segment(
    tmp_path, monkeypatch, caplog
):
    """The barrier waits for what was captured up to the boundary and no further. A word
    captured just after it -- right after the poll that delivered the last word before it
    -- and still undelivered when that poll has moved the mark past the boundary, goes to
    the next segment, and the roll does not wait for it."""
    taken = _boundaries(monkeypatch)
    backend = _SlowWordPump()

    def before_close():
        backend.capture_delivered_late(
            0x42, then=lambda: backend.capture(0x43, held_s=1.0)
        )

    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        in_session, after = _visit(tmp_path, backend, before_close)

    boundary = taken[-1]  # the close's
    assert in_session[0x42] <= boundary < after[0x43]
    assert 0x43 not in in_session
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


class _EdgesStalled(FakeBackend):
    """An edge path whose pump has stopped moving its mark."""

    def __init__(self, mark: int) -> None:
        super().__init__()
        self.mark = mark
        self.asked: list[float] = []

    def delivered_through(self):
        self.asked.append(time.monotonic())
        marks = super().delivered_through()
        marks["edge capture"] = self.mark
        return marks


def test_a_stalled_path_costs_the_roll_its_bound_and_a_warning_but_not_the_roll(
    tmp_path, caplog
):
    """A path that has not caught up within ROLL_BARRIER_TIMEOUT_S is named in a WARNING,
    with its mark and the boundary, and the roll goes ahead. A stalled path is already
    the health check's to report, so this is not a second failure mode."""
    write_open(tmp_path, OpenSession(SessionId.parse("2026-08-16_01"), START, "r1"))
    backend = _EdgesStalled(mark=4_000_000)
    backend.set_now_us(9_000_000)
    requests = queue.SimpleQueue()
    closing = _request("close", "2026-08-16_01")
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        folder = run(
            tmp_path, backend, _clock_from(START), 3,
            _requesting(requests, {1: [closing]}), requests=requests,
        )

    assert closing.reply.result(timeout=0) == SessionId.parse("2026-08-16_01")
    waited = backend.asked[-1] - backend.asked[0]
    assert cli.ROLL_BARRIER_TIMEOUT_S - 0.01 <= waited < cli.ROLL_BARRIER_TIMEOUT_S + 0.1
    assert _has_trailer(tmp_path / "2026-08-16_01" / "syncbox" / segment_name(0))
    assert folder == unassigned_dir(tmp_path, DAY)
    assert len(_values(folder / segment_name(0))) == 1
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "edge capture" in warning
    assert "4000000" in warning and "9000000" in warning


class _BarrierCosts(_FramesTakeRealTime):
    """Each roll's barrier costs `cost_s` of the run loop's time."""

    def __init__(self, clock, frame_s, cost_s) -> None:
        super().__init__(clock, frame_s)
        self._cost_s = cost_s
        self._charged_after = None

    def delivered_through(self):
        if self._charged_after != len(self.frame_starts):
            self._charged_after = len(self.frame_starts)
            self._clock.now += self._cost_s
        return super().delivered_through()


def test_a_roll_keeps_the_barcode_cadence_even_when_its_barrier_runs_to_the_bound(
    tmp_path,
):
    """A barrier sits on the run loop between a frame and the next. Charged its whole
    bound at an open and at a close, frames still go out once a second on the grid."""
    clock = _Monotonic()
    backend = _BarrierCosts(clock, FRAME_US / 1e6, cli.ROLL_BARRIER_TIMEOUT_S)
    requests = queue.SimpleQueue()
    script = {1: [_request("open", "r1")], 3: [_request("close", "2026-08-16_01")]}
    scripted = _requesting(requests, script)

    def tick_fn(seconds):
        clock.sleep(seconds)
        scripted(seconds)

    run(tmp_path, backend, clock.wall, 6, tick_fn, mono_fn=clock, requests=requests)
    assert backend._charged_after is not None, "no roll asked how far delivery had got"
    assert backend.frame_starts == pytest.approx([100.0, 101.5, 102.5, 103.5, 104.5, 105.5])


class _ScriptedStampLag(FakeBackend):
    """stamp_lag() gives each scripted reading in turn, then the last one again."""

    def __init__(self, script) -> None:
        super().__init__()
        self._script = list(script)

    def stamp_lag(self):
        return self._script.pop(0) if len(self._script) > 1 else self._script[0]


def test_an_edge_later_than_the_barrier_allows_is_warned_of_once_per_episode(
    tmp_path, caplog
):
    """The edge mark rests on the kernel buffering an edge within EDGE_STAMP_LAG_US of
    stamping it. The edge pump bounds that lag from above on every read; the health
    report warns when edges go over, once per episode as it reports failures, and the
    end of the run says the largest bound seen, so a quiet run confirms the assumption."""
    from wl_sync.backend import StampLag

    def lag(max_us, over):
        return StampLag(max_us=max_us, over=over, allowance_us=20_000)

    # One reading per health check: six iterations, then once more after stopping.
    script = [None, lag(3_000, 0), lag(31_000, 1), lag(31_000, 4), lag(31_000, 4),
              lag(45_000, 5), lag(45_000, 5)]
    with caplog.at_level(logging.INFO, logger=cli.log.name):
        run(tmp_path, _ScriptedStampLag(script), _clock_from(START), len(script) - 1,
            lambda _s: None)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, warnings
    assert "31000 us" in warnings[0] and "20000 us" in warnings[0]
    assert "45000 us" in warnings[1]
    [summary] = [m for m in caplog.messages if m.startswith("edges reached the pump")]
    assert "45000 us" in summary and "20000 us" in summary and "5 over" in summary


def test_a_backend_with_no_edge_lag_says_nothing_about_it(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=cli.log.name):
        run(tmp_path, FakeBackend(), _clock_from(START), 2, lambda _s: None)
    assert not [m for m in caplog.messages if m.startswith("edges reached the pump")]


# --- one PIO rate per process, and the next one measured and kept (spec §2.2) -----------


# What the kernel reports while NTP disciplines MONO through its PLL (calibration.si_gate).
SYNCED = KernelNtp(unsynchronized=False, pll=True, offset_ns=0)
# The journal's lines, verbatim from the plan's Task 1.8.
_NO_CALIBRATION = (
    "no usable PIO rate calibration ({}): ticks count RP1's crystal, timebase rp1-pio, "
    "until a later start finds one"
)
_DELAY_NOT_USED = (
    "--barcode-delay-us {} is not used: this backend stamps the lead edge itself "
    "(barcode_delay_us {})"
)
# Fix round 1 (Ruling R23): the start's line for a board whose serial cannot be read, and
# witness 2's for a box with no calibration applied, verbatim from the ruling.
_NO_SERIAL = (
    "this process's PIO rate cannot be kept for the next start: this board's serial "
    "cannot be read"
)
_FIRST_MEASUREMENT = (
    "this process measured RP1's rate at {} ppb against NTP over {} s; with no "
    "calibration applied its ticks run at RP1's own rate"
)


def _p_at(mono_ns, true_ppb):
    """P at a MONO reading, for a PIO crystal `true_ppb` fast of SI: MONO in us, at the
    crystal's rate."""
    return mono_ns * (10**9 + true_ppb) // 10**12


def _pair_train(spacing_s, count, true_ppb=0, step_at_s=None):
    """`count` narrow pairs `spacing_s` apart from MONO 0, with RAW = MONO. From
    `step_at_s` on, REALTIME-MONO is 5 ms more, a step past the estimator's 1 ms
    (calibration.REALTIME_STEP_NS)."""
    spacing_ns = round(spacing_s * 1e9)
    return [
        ClockPair(
            _p_at(n * spacing_ns, true_ppb),
            n * spacing_ns,
            n * spacing_ns,
            1_000,
            0 if step_at_s is None or n * spacing_s < step_at_s else 5_000_000,
        )
        for n in range(count)
    ]


def _writes(path, keeper, pairs, after=None):
    """Feed `pairs` to `keeper`, one a frame, and return each write as (process hour,
    baseline hours). `after(n)` runs after frame n (0-based)."""
    writes, last = [], None
    for n, pair in enumerate(pairs):
        keeper.frame([pair], True, SYNCED)
        if after is not None:
            after(n)
        kept = read_calibration(path, "test-serial")[0] if path.is_file() else None
        if kept is not None and kept != last:
            writes.append((pair.mono_ns / 3.6e12, kept.baseline_s / 3600))
            last = kept
    return writes


def _calibration(rate_ppb, p_per_raw_ppb=None, serial="test-serial"):
    """A calibration as an earlier process on this board would have left it."""
    return Calibration(
        serial=serial,
        rate_ppb=rate_ppb,
        p_per_raw_ppb=rate_ppb if p_per_raw_ppb is None else p_per_raw_ppb,
        baseline_s=28_800.0,
        pairs=28_801,
        residual_rms_us=2.5,
        boot_id="an-earlier-boot",
        written_at=datetime.datetime(2026, 11, 6, 17, 0, tzinfo=UTC),
    )


class _PioClock(FakeBackend):
    """A backend counting a PIO crystal `true_ppb` off SI, as the one-clock RP1 backend
    does (spec §2.2): it takes the calibration and names its timebase from it, stamps each
    lead edge itself (`barcode_delay_us` 0, spec §2.6), and hands over one pair a frame.

    Frame n (0-based) has its lead edge at MONO n * `pair_spacing_s`, with RAW = MONO, and
    is stamped at that MONO's P, which `now_us()` then reads too. `p` keeps each frame's P
    unwrapped; the stamp and `now_us()` are wrapped, as every raw tick is."""

    def __init__(self, true_ppb=0, pair_spacing_s=1.0, barcode_delay_us=0):
        super().__init__()
        self.true_ppb = true_ppb
        self._spacing_ns = round(pair_spacing_s * 1e9)
        self.barcode_delay_us = barcode_delay_us
        self.applied = []  # every calibration apply_calibration was given
        self.p = []
        self._pairs = []

    def apply_calibration(self, calibration):
        self.applied.append(calibration)
        self.timebase = "rp1-pio" if calibration is None else "rp1-pio-si"
        return TickScale(calibration.rate_ppb if calibration else 0)

    def emit_pulses(self, pin, pulses):
        mono_ns = len(self.emitted) * self._spacing_ns
        p = _p_at(mono_ns, self.true_ppb)
        self.set_now_us(p)
        stamp = super().emit_pulses(pin, pulses)
        self.p.append(p)
        if stamp.tick is not None:
            self._pairs.append(ClockPair(p, mono_ns, mono_ns, 1_000, 0))
        return stamp

    def take_pairs(self):
        pairs, self._pairs = self._pairs, []
        return pairs


def _ntp_and_kernel_synced(monkeypatch):
    """The SI gate open from the start: NTP says yes, and the kernel is disciplined."""
    monkeypatch.setattr(cli, "probe_ntp_synchronized", lambda timeout_s=None: True)
    monkeypatch.setattr(cli, "probe_kernel_ntp", lambda: SYNCED)


def test_a_usable_calibration_scales_every_tick_and_names_the_timebase(tmp_path, caplog):
    """One rate for the process, from the calibration this board left (spec §2.2): every
    tick is P scaled by it, the header says so, and the start's journal names the rate,
    its baseline, its pairs and when it was written. The crystal here runs as fast as the
    file says, so the ticks are SI microseconds from the first frame."""
    calibration = _calibration(rate_ppb=100_000)
    write_calibration(tmp_path / RATE_FILE_NAME, calibration)
    backend = _PioClock(true_ppb=100_000)

    def tick_fn(_seconds):
        # An edge and a word at the clock now, which is the frame's P.
        backend.inject_edge(EDGE_PINS[0], 1, backend.now_us())
        backend.inject_word(backend.now_us(), 7)

    with caplog.at_level(logging.INFO, logger=cli.log.name):
        folder = run(tmp_path, backend, _clock_from(START), 4, tick_fn)

    header, records = read_log(folder / segment_name(0))
    assert backend.applied == [calibration]
    assert header.timebase == "rp1-pio-si"
    barcodes = [r.tick_us for r in records if isinstance(r, BarcodeEmitted)]
    assert barcodes == [TickScale(100_000).tick(p) for p in backend.p]
    assert barcodes == [0, 1_000_000, 2_000_000, 3_000_000]
    assert [r.tick_us for r in records if isinstance(r, Edge)] == barcodes
    assert [r.tick_us for r in records if isinstance(r, CodeWord)] == barcodes
    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and "PIO rate calibration" in r.getMessage()
    ]
    for part in ("+100000 ppb", "28800 s", "28801 pairs", "2026-11-06T17:00:00+00:00"):
        assert part in line, line


def test_without_a_calibration_the_box_records_at_rp1s_rate_and_says_why(tmp_path, caplog):
    """A box with no usable calibration (a new install, a lost file, another board's)
    records anyway, at RP1's own rate, says so in the header and says why in the journal
    (spec §2.2, "Otherwise r = 1"), until its next start (the user's Q1)."""
    backend = _PioClock(true_ppb=100_000)
    with caplog.at_level(logging.INFO, logger=cli.log.name):
        folder = run(tmp_path, backend, _clock_from(START), 3, lambda _s: None)

    header, records = read_log(folder / segment_name(0))
    assert backend.applied == [None]
    assert header.timebase == "rp1-pio"
    assert [r.tick_us for r in records if isinstance(r, BarcodeEmitted)] == backend.p
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [_NO_CALIBRATION.format("no calibration file")]


def test_the_rate_never_changes_within_a_process(tmp_path, monkeypatch):
    """A rate that moved mid-session would bend B against the one straight line each
    device is fitted with (spec §2.2, "Why one rate per process"). So neither the NTP roll,
    nor the keeper's write mid-run, nor a session's roll after that write moves it: every
    segment names one timebase, and every B is P scaled by the rate applied at the start,
    though the process measures another and has written it for the next."""
    write_calibration(tmp_path / RATE_FILE_NAME, _calibration(rate_ppb=100_000))
    monkeypatch.setattr(cli, "probe_kernel_ntp", lambda: SYNCED)
    backend = _PioClock(true_ppb=150_000, pair_spacing_s=600)
    written_by = []  # how many frames had gone out when the file held this process's rate

    def after_sleep():
        if not written_by:
            kept, _ = read_calibration(tmp_path / RATE_FILE_NAME, "test-serial")
            if kept.rate_ppb == 150_000:
                written_by.append(len(backend.emitted))

    _awaiting_ntp(
        tmp_path, monkeypatch, lambda now: now >= 105, frames=40, backend=backend,
        script={24: [_request("open", "r1")]}, after_sleep=after_sleep,
    )

    folder = unassigned_dir(tmp_path, DAY)
    assert _segments(folder) == [segment_name(0), segment_name(1)]
    assert _segments(tmp_path / "2026-08-16_01" / "syncbox") == [segment_name(0)]
    headers = _headers(tmp_path)
    assert [h.timebase for h in headers.values()] == ["rp1-pio-si"] * 3
    barcodes = sorted(
        r.tick_us
        for path in headers
        for r in read_log(tmp_path / path)[1]
        if isinstance(r, BarcodeEmitted)
    )
    assert backend.applied == [_calibration(rate_ppb=100_000)]
    assert barcodes == [TickScale(100_000).tick(p) for p in backend.p]
    # The gate opened at frame 12, the first after the poll at 110 s heard yes, and an
    # hour of baseline at 600 s a pair is six pairs on: the write fell mid-run, between
    # the NTP roll (after frame 12) and the open's (after frame 25).
    assert written_by == [18]


def test_the_pairs_are_taken_every_frame_where_no_keeper_runs(tmp_path):
    """A backend that scales nothing runs no keeper, but the loop still takes its pairs
    each frame: the RP1 backend holds every pair until it is taken
    (wl_sync/rp1/backend.py:425-427), and left there they would pile up, one a second,
    for the life of the process."""

    class _CountsTakes(FakeBackend):
        takes = 0

        def take_pairs(self):
            self.takes += 1
            return super().take_pairs()

    backend = _CountsTakes()
    run(tmp_path, backend, _clock_from(START), 5, lambda _s: None)
    assert backend.takes == 5


def test_a_one_hour_baseline_is_written_and_rewritten_hourly(tmp_path):
    """Spec §2.2, "Persisting it": first written once the baseline reaches 1 h, then
    rewritten every hour. Pairs 600 s apart: six span 3000 s, seven 3600 s, thirteen
    7200 s."""
    path = tmp_path / RATE_FILE_NAME
    keeper = cli._RateKeeper(path, "test-serial", None, "this-boot")
    pairs = _pair_train(600, 13, true_ppb=42_000)
    for pair in pairs[:6]:
        keeper.frame([pair], True, SYNCED)
    assert not path.exists()

    keeper.frame([pairs[6]], True, SYNCED)
    first, why = read_calibration(path, "test-serial")
    assert why == ""
    assert (first.serial, first.boot_id) == ("test-serial", "this-boot")
    assert (first.rate_ppb, first.p_per_raw_ppb, first.pairs, first.baseline_s) == (
        42_000, 42_000, 7, 3600.0,
    )
    # When it was written, on the wall clock: not the run loop's injected one.
    age = datetime.datetime.now(UTC) - first.written_at
    assert datetime.timedelta(0) <= age < datetime.timedelta(minutes=1)

    for pair in pairs[7:12]:
        keeper.frame([pair], True, SYNCED)
    assert read_calibration(path, "test-serial")[0] == first

    keeper.frame([pairs[12]], True, SYNCED)
    second, _ = read_calibration(path, "test-serial")
    assert (second.pairs, second.baseline_s) == (13, 7200.0)


def test_no_calibration_is_written_before_ntp_has_synced(tmp_path):
    """The SI rate is P against NTP-disciplined MONO, so a pair counts only while MONO is
    known to be disciplined (calibration.si_gate). Hours of pairs before that write
    nothing, and the baseline written begins at the first pair the gate let through
    (spec §2.2: "The estimator collects nothing until the gate opens")."""
    path = tmp_path / RATE_FILE_NAME
    keeper = cli._RateKeeper(path, "test-serial", None, "")
    pairs = iter(_pair_train(600, 47))
    closed = [
        (False, SYNCED),  # NTP's last definite answer is no
        (None, SYNCED),  # NTP has never answered
        (True, None),  # the kernel cannot be asked
        (True, KernelNtp(unsynchronized=True, pll=True, offset_ns=0)),
        (True, KernelNtp(unsynchronized=False, pll=False, offset_ns=0)),
    ]
    for ntp_known, kernel in closed:
        for _ in range(8):  # 4800 s each
            keeper.frame([next(pairs)], ntp_known, kernel)
    assert not path.exists()

    for _ in range(6):
        keeper.frame([next(pairs)], True, SYNCED)
    assert not path.exists()
    keeper.frame([next(pairs)], True, SYNCED)
    assert read_calibration(path, "test-serial")[0].pairs == 7


def test_nothing_is_written_when_the_board_serial_cannot_be_read(
    tmp_path, monkeypatch, caplog
):
    """A file is applied only on the board whose serial it names (spec §2.2), so one
    written without a serial could never be applied. The process records and measures as
    ever, and writes no file, not even a temporary. It says so once, at the start: such a
    board would otherwise record uncalibrated at every start, with nothing in the journal
    to say why no calibration ever comes. The same run with this board's serial is the
    control: it writes one, and says nothing of the kind."""
    _ntp_and_kernel_synced(monkeypatch)

    def record(out, serial):
        monkeypatch.setattr(cli, "probe_board_serial", lambda: serial)
        backend = _PioClock(pair_spacing_s=600)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=cli.log.name):
            folder = run(out, backend, _clock_from(START), 8, lambda _s: None)
        assert len(_values(folder / segment_name(0))) == 8
        files = sorted(p.name for p in out.iterdir() if p.name.startswith(RATE_FILE_NAME))
        return files, [r.getMessage() for r in caplog.records]

    no_file = _NO_CALIBRATION.format("no calibration file")
    assert record(tmp_path / "unknown", None) == ([], [no_file, _NO_SERIAL])
    assert record(tmp_path / "known", "test-serial") == ([RATE_FILE_NAME], [no_file])


@pytest.mark.parametrize(
    "apart_ppb, warned", [(6_000, True), (-6_000, True), (4_000, False)]
)
def test_a_live_raw_ratio_far_from_the_files_warns_once(
    tmp_path, caplog, apart_ppb, warned
):
    """Spec §2.2's first witness: the P/RAW ratio, measured live within about 10 s, against
    the file's. More than 5 ppm apart, the file most likely came from other hardware,
    though its serial matches. Every narrow pair counts, gated or not: RAW is not NTP's."""
    applied = _calibration(rate_ppb=100_000, p_per_raw_ppb=100_000)
    keeper = cli._RateKeeper(tmp_path / RATE_FILE_NAME, "test-serial", applied, "")
    seen = []  # warnings logged by the end of each frame
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        for pair in _pair_train(1, 60, true_ppb=100_000 + apart_ppb):
            keeper.frame([pair], None, None)
            seen.append(len(caplog.records))

    warnings = [r.getMessage() for r in caplog.records]
    if not warned:
        assert warnings == []
        return
    [warning] = warnings
    assert f"{100_000 + apart_ppb:+d} ppb" in warning and f"{apart_ppb:+d} ppb" in warning
    # At the 11th pair, 1 s apart, where the ratio first spans 10 s; never again.
    assert seen.index(1) == 10


@pytest.mark.parametrize(
    "applied_ppb, true_ppb, apart_ppb",
    [
        (100_000, 101_500, 1_500),
        (100_000, 98_500, -1_500),
        (100_000, 100_900, None),
        (None, 1_500, 1_500),  # no calibration applied: the applied rate is 0
        (None, -900, None),
    ],
)
def test_this_processs_estimate_far_from_the_applied_rate_warns_once(
    tmp_path, caplog, applied_ppb, true_ppb, apart_ppb
):
    """Spec §2.2's second witness: this process's own SI estimate, once its baseline
    reaches 1 h, against the rate it applied. More than 1 ppm apart is a WARNING naming
    the difference."""
    applied = None
    if applied_ppb is not None:
        # The file's P/RAW is the crystal's, so the first witness has nothing to say.
        applied = _calibration(rate_ppb=applied_ppb, p_per_raw_ppb=true_ppb)
    keeper = cli._RateKeeper(tmp_path / RATE_FILE_NAME, "test-serial", applied, "")
    seen = []
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        for pair in _pair_train(600, 13, true_ppb=true_ppb):
            keeper.frame([pair], True, SYNCED)
            seen.append(len(caplog.records))

    warnings = [r.getMessage() for r in caplog.records]
    if apart_ppb is None:
        assert warnings == []
        return
    [warning] = warnings
    if applied is None:
        # A first measurement, not a fault (Ruling R23): its own wording.
        assert warning == _FIRST_MEASUREMENT.format(f"{true_ppb:+d}", 3600), warning
    else:
        assert warning.startswith("this process measures the PIO rate at"), warning
        assert f"{apart_ppb:+d} ppb" in warning, warning
    # At the 7th pair, 600 s apart, where the baseline first spans an hour.
    assert seen.index(1) == 6


@pytest.mark.parametrize(
    "apart_ppb, warned", [(5_000, 0), (5_001, 1), (-5_000, 0), (-5_001, 1)]
)
def test_the_raw_ratio_witness_warns_only_above_5_ppm(tmp_path, caplog, apart_ppb, warned):
    """"A difference above 5 ppm" (spec §2.2): exactly 5 ppm is silent. Pairs 1000 s
    apart make every P exact, so the ratio is exactly the crystal's."""
    applied = _calibration(rate_ppb=100_000, p_per_raw_ppb=100_000)
    keeper = cli._RateKeeper(tmp_path / RATE_FILE_NAME, "test-serial", applied, "")
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        for pair in _pair_train(1000, 3, true_ppb=100_000 + apart_ppb):
            keeper.frame([pair], None, None)
    assert len(caplog.records) == warned


@pytest.mark.parametrize(
    "apart_ppb, warned", [(1_000, 0), (1_001, 1), (-1_000, 0), (-1_001, 1)]
)
def test_the_rate_witness_warns_only_above_1_ppm(tmp_path, caplog, apart_ppb, warned):
    """"A difference above 1 ppm" (spec §2.2): exactly 1 ppm is silent. Pairs 1000 s
    apart make every P exact, and five of them span 4000 s."""
    applied = _calibration(rate_ppb=100_000, p_per_raw_ppb=100_000 + apart_ppb)
    keeper = cli._RateKeeper(tmp_path / RATE_FILE_NAME, "test-serial", applied, "")
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        for pair in _pair_train(1000, 5, true_ppb=100_000 + apart_ppb):
            keeper.frame([pair], True, SYNCED)
    assert len(caplog.records) == warned


def test_the_witness_texts_quote_the_thresholds_they_test(tmp_path, monkeypatch, caplog):
    """Each text says the threshold its constant holds, so the two cannot drift apart."""
    monkeypatch.setattr(cli, "RAW_RATIO_WITNESS_PPB", 2_000)
    monkeypatch.setattr(cli, "RATE_WITNESS_PPB", 500)
    applied = _calibration(rate_ppb=100_000, p_per_raw_ppb=100_000)
    keeper = cli._RateKeeper(tmp_path / RATE_FILE_NAME, "test-serial", applied, "")
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        for pair in _pair_train(1000, 5, true_ppb=103_000):
            keeper.frame([pair], True, SYNCED)
    ratio, rate = [r.getMessage() for r in caplog.records]
    assert "more than 2 ppm" in ratio, ratio
    assert "more than 0.5 ppm" in rate, rate


def test_a_calibration_that_cannot_be_written_is_an_error_and_recording_goes_on(
    tmp_path, monkeypatch, caplog
):
    """A full disk or a read-only root (the plan's review focus 4). Here a directory
    stands where the file goes, so write_durably's rename refuses it after the temporary
    is written (wl_sync/clock.py:126-132). One ERROR, recording goes on, nothing is left
    beside the file, and the next hour's write succeeds."""
    path = tmp_path / RATE_FILE_NAME
    path.mkdir()
    _ntp_and_kernel_synced(monkeypatch)
    backend = _PioClock(true_ppb=42_000, pair_spacing_s=600)
    beside = []  # what stood beside the file after each frame

    def tick_fn(_seconds):
        beside.append(
            [p.name for p in tmp_path.iterdir() if p.name.startswith(f"{path.name}.")]
        )
        if len(backend.emitted) == 7:  # the hour's write has failed
            path.rmdir()

    with caplog.at_level(logging.INFO, logger=cli.log.name):
        folder = run(tmp_path, backend, _clock_from(START), 13, tick_fn)

    [error] = _errors(caplog)
    assert str(path) in error
    assert beside == [[]] * 13
    assert _has_trailer(folder / segment_name(0))
    assert len(_values(folder / segment_name(0))) == 13
    kept, why = read_calibration(path, "test-serial")
    assert why == "" and (kept.pairs, kept.rate_ppb) == (13, 42_000)


def test_a_write_that_keeps_failing_is_one_error_until_one_succeeds(tmp_path, caplog):
    """Once per episode, as the run loop's other journals are: a disk that stays full
    fails every hour, and one ERROR says so. A write that succeeds ends the episode, so a
    later failure is reported again."""
    path = tmp_path / RATE_FILE_NAME
    keeper = cli._RateKeeper(path, "test-serial", None, "")
    pairs = iter(_pair_train(600, 25))

    def hours(n):  # six pairs 600 s apart are an hour, and one attempt
        for _ in range(6 * n):
            keeper.frame([next(pairs)], True, SYNCED)

    with caplog.at_level(logging.ERROR, logger=cli.log.name):
        path.mkdir()
        keeper.frame([next(pairs)], True, SYNCED)
        hours(2)  # 3600 s and 7200 s: both fail
        assert len(_errors(caplog)) == 1
        path.rmdir()
        hours(1)  # 10800 s: written
        assert read_calibration(path, "test-serial")[0].pairs == 19
        path.unlink()
        path.mkdir()
        hours(1)  # 14400 s: fails, a new episode
    assert len(_errors(caplog)) == 2


def test_a_baseline_a_realtime_step_restarts_never_overwrites_a_longer_one(tmp_path):
    """A REALTIME step restarts the estimator's baseline (wl_sync/calibration.py:170-176).
    The step moves MONO's phase, not RP1's rate, so the estimate from before it stays the
    better one (Ruling R23), and the file is rewritten only once the new baseline is an
    hour longer than the last one tried. Pairs 600 s apart, a step at 3.5 h: writes at 1,
    2 and 3 h of process, then none until 7.5 h, on a 4 h baseline, and hourly again."""
    path = tmp_path / RATE_FILE_NAME
    keeper = cli._RateKeeper(path, "test-serial", None, "")
    pairs = _pair_train(600, 55, true_ppb=7_000, step_at_s=12_600)
    assert _writes(path, keeper, pairs) == [
        (1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (7.5, 4.0), (8.5, 5.0),
    ]


def test_after_a_failed_write_and_a_realtime_step_the_next_waits_an_hour_past_the_attempt(
    tmp_path, caplog
):
    """The mark is the last attempt, failed or not, so the rule holds after a failure, and
    costs a delay there: the file holds nothing longer than the new baseline. The attempt
    at 1 h fails (a directory where the file goes), a step at 1.5 h restarts the
    baseline, and the first write lands at 3.5 h, on a 2 h baseline, an hour past the
    failed attempt's; then hourly."""
    path = tmp_path / RATE_FILE_NAME
    path.mkdir()
    keeper = cli._RateKeeper(path, "test-serial", None, "")
    pairs = _pair_train(600, 28, true_ppb=7_000, step_at_s=5_400)

    def after(n):
        if n == 6:  # the attempt at 1 h has failed
            path.rmdir()

    with caplog.at_level(logging.ERROR, logger=cli.log.name):
        assert _writes(path, keeper, pairs, after) == [(3.5, 2.0), (4.5, 3.0)]
    assert len(_errors(caplog)) == 1


def test_a_keeper_that_fails_is_dropped_with_one_error_and_the_box_records_on(
    tmp_path, monkeypatch, caplog
):
    """The keeper's file is for the next start, and nothing it does may stop this one (the
    plan's Global Constraints: "The box records anyway"). A failure is one ERROR naming
    it; the keeper is dropped for the rest of the process, its pairs still taken; and
    recording goes on, as roll() does when a manifest rebuild fails."""
    calls = []

    def broken(self, pairs, ntp_known, kernel):
        calls.append(len(pairs))
        raise RuntimeError("an estimator fault")

    monkeypatch.setattr(cli._RateKeeper, "frame", broken)
    backend = _PioClock()
    with caplog.at_level(logging.INFO, logger=cli.log.name):
        folder = run(tmp_path, backend, _clock_from(START), 4, lambda _s: None)

    [error] = _errors(caplog)
    assert "RuntimeError" in error and "an estimator fault" in error, error
    assert calls == [1]
    assert backend.take_pairs() == []
    assert len(_values(folder / segment_name(0))) == 4
    assert _has_trailer(folder / segment_name(0))


def test_a_measured_barcode_delay_is_not_used_where_the_backend_stamps_the_edge(
    tmp_path, caplog
):
    """`--barcode-delay-us` is the delay from the clock's reading to the edge on the wire.
    A backend that stamps the lead edge itself declares its own, 0 (spec §2.6). A flag
    given anyway would describe a software delay that no longer exists, so it is not used,
    and a WARNING says so; every segment of the process carries the backend's."""
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        headers = _open_then_close(
            tmp_path / "flag", backend=_PioClock(barcode_delay_us=0), barcode_delay_us=850
        )
    assert {header.barcode_delay_us for header in headers.values()} == {0}
    assert [m for m in caplog.messages if m.startswith("--barcode-delay-us")] == [
        _DELAY_NOT_USED.format(850, 0)
    ]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=cli.log.name):
        headers = _open_then_close(tmp_path / "none", backend=_PioClock(barcode_delay_us=0))
    assert {header.barcode_delay_us for header in headers.values()} == {0}
    assert not [m for m in caplog.messages if m.startswith("--barcode-delay-us")]
