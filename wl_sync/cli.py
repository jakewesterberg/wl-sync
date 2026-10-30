"""`wl-sync record` -- the run loop that makes this a sync box rather than a library.

The box is the first device on each day and the last off, and it runs continuously
while every other device starts and stops blocks inside that run. Each process run
writes one or more segments, all carrying its `process_uuid`. The first goes into the
folder of the session that is open -- or, with none open, into the between-sessions
folder for the day. A roll begins another: when a session opens or closes, and once
when NTP first syncs. A restart joins the open session's folder, or the day's
between-sessions folder, rather than beginning a new one. systemd's Restart=always is
what makes the stitching automatic.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import logging
import math
import queue
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.barcode import IDLE_MIN_US
from wl_sync.clock import (
    BARCODE_EPOCH,
    MAX_BARCODE,
    ClockTrust,
    evaluate_clock,
    evaluate_roll,
    next_value,
    read_checkpoint,
    write_checkpoint,
)
from wl_sync.endpoint import serve
from wl_sync.log import SyncBoxLogHeader
from wl_sync.manifest import write_manifest
from wl_sync.pins import (
    BARCODE_PIN,
    CODE_DATA_BASE,
    CODE_DATA_COUNT,
    CODE_STROBE_PIN,
    EDGE_PINS,
    GPIO_MAP,
)
from wl_sync.segment import SegmentWriter, next_segment_index, segment_name
from wl_sync.service import BarcodeGenerator, QueuedSink, Recorder
from wl_sync.session import SessionId
from wl_sync.sessions import (
    SessionError,
    SessionRequest,
    close_session,
    open_session,
    read_open,
    session_dir,
    unassigned_dir,
    unassigned_id,
)

CHECKPOINT_NAME = ".wl-sync-barcode-checkpoint"

# LOW the decoder must see before a lead edge, plus margin for the work between the
# sleep ending and the pin rising: a checkpoint's two fsyncs, the tick read.
_IDLE_BEFORE_FRAME_S = IDLE_MIN_US / 1e6 + 0.1

# How long the run loop waits between asking NTP whether it has synced, while a run that
# began unsynchronised waits for the first sync (see run()). A probe runs timedatectl on
# the loop's own thread, between two frames, and costs 9 ms warm and 62 ms cold on
# wl-sync-rig0, where a frame is due every second. Ten seconds ends the unsynchronised
# stretch within ten seconds of the sync, where it used to last until a session changed.
NTP_POLL_INTERVAL_S = 10.0

# A POLL MUST NOT COST A FRAME, so its probe has its own timeout. The probe's default
# 5 s, kept for the start and for a roll at an open or close, would let a hung
# timedatectl (D-Bus, timedated) hold the loop past five frames at every poll. Measured
# on wl-sync-rig0, the probe takes 62 ms cold and 9 ms warm, so 0.3 s is about five times
# the cold case.
#
# A BUDGET, NOT A GUARANTEE. The poll runs after a 200 ms frame, and the loop then has
# ~0.75 s before the next grid point, so a probe that hangs to its timeout fits with
# ~0.45 s to spare. Anything else slow in the same second eats that spare: a flush
# fsync has been measured at 229 ms. A slow fsync together with a hung probe could
# still skip one frame. A poll that times out is None, "could not ask", which never
# triggers the roll.
NTP_POLL_TIMEOUT_S = 0.3

# After a poll that fails (None), the wait before the next one doubles, up to this cap,
# so a box whose timedatectl keeps failing is asked less and less often. Any definite
# answer puts the wait back to NTP_POLL_INTERVAL_S.
NTP_POLL_BACKOFF_MAX_S = 300.0

# The longest a roll waits for the capture paths to deliver what they captured up to its
# boundary, and how often it looks. See _await_delivery for the arithmetic.
ROLL_BARRIER_TIMEOUT_S = 0.2
_BARRIER_POLL_S = 0.001

# `logging`, not `print`: stdout is block-buffered when it is a pipe, which is what
# systemd gives a service, so prints can sit unflushed in a daemon that runs all day.
# journald is this box's only status surface (spec Sec.7, Sec.10) -- the heartbeat LED
# is a buffered leg off BARCODE_RAW and cannot be driven by software.
log = logging.getLogger(__name__)


def probe_ntp_synchronized(timeout_s: float = 5) -> bool | None:
    """True/False from timedatectl, or None when it cannot be asked -- a laptop, a
    container, or a call that failed or ran past `timeout_s`. None is 'unknown', which
    evaluate_clock deliberately does not treat as untrusted. At a roll, run() replaces
    it with the run's last definite answer, so a failure never upgrades trust.

    5 s by default, for the start and for a roll at an open or close, which happen
    rarely and can afford it (2b420c0 kept it: a shorter one turns more probes into
    failures). The poll for NTP's first sync passes NTP_POLL_TIMEOUT_S instead."""
    try:
        completed = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() == "yes"


def probe_boot_id() -> str:
    """The OS boot session's id, or "" where the platform does not expose one.

    Distinguishes "the process restarted" from "the machine rebooted" -- under
    Restart=always those look identical from a timestamp, and telling them apart is the
    first question a crash loop raises. Empty means "could not tell", the same honest
    non-answer probe_ntp_synchronized() gives.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _resume(
    started: datetime.datetime, checkpoint: int | None, ntp_synchronized: bool | None
) -> tuple[int, ClockTrust, datetime.date]:
    """The value to start at, what to say about the clock, and which day to record into.

    THE BOX RECORDS ANYWAY. Spec Sec.7 is explicit that an untrustworthy clock degrades
    the record and never blocks the rig, "because refusing to start would stop the first
    device of the day over a $0.30 battery". `value_from_clock` raises below the 2020
    epoch and that raise stays -- it is the right low-level contract -- but it is caught
    HERE, one level up, where the checkpoint that makes recovery possible is in hand. A
    CM5 with a dead or missing CR2032 typically reads 2000-01-01 and a Pi with no RTC
    and no network reads 1970; under Restart=always, propagating that was an unbounded
    crash loop recording nothing.

    THE DAY DIRECTORY COMES FROM THE BARCODE VALUE, NOT THE BOGUS CLOCK. A barcode value
    IS seconds since 2020 (Sec.4), so the resumed value is the box's own surviving
    timebase and implies a date. With a checkpoint present that lands on the day the box
    was really recording, so a restart on a dead battery rejoins the day it was already
    writing to. With no checkpoint at all it lands on unassigned/2020-01-01/syncbox: still not
    the true date, but honest, constant, and -- unlike a directory named after whatever the junk
    clock happened to read -- it does not mint a fresh orphan directory on every boot.

    ONLY a pre-epoch reading degrades. A naive datetime is a caller bug that silently
    means "local time" and would shift every barcode, and a reading past the 32-bit
    ceiling (~2156) cannot be encoded into a frame at all; both keep raising, and both
    fall through to value_from_clock's own message rather than being caught here.
    """
    aware = started.tzinfo is not None and started.utcoffset() is not None
    if not (aware and started < BARCODE_EPOCH):
        return (
            next_value(started, checkpoint),
            evaluate_clock(started, checkpoint, ntp_synchronized),
            started.date(),
        )

    reasons = ["clock_before_epoch"]
    first_value = 0 if checkpoint is None else checkpoint + 1
    if first_value > MAX_BARCODE:
        # A checkpoint sitting exactly at the ceiling -- corruption that still parses,
        # or the year 2156 -- makes `checkpoint + 1` equal 2**32, which barcode.encode
        # rejects. Raising here would be the C2 crash loop again in a new guise, so the
        # exhausted checkpoint is treated as no checkpoint: start at the epoch and say
        # so. Uniqueness is already unrecoverable once the space is spent; recording
        # under a loud marker beats not recording. read_checkpoint already degrades
        # anything ABOVE the ceiling to None, so this is the one remaining value.
        reasons.append("checkpoint_exhausted")
        first_value = 0
    return first_value, ClockTrust(False, ",".join(reasons)), _barcode_day(first_value)


def _barcode_day(value: int) -> datetime.date:
    """The date a barcode value implies: it IS seconds since the 2020 epoch (Sec.4), and
    the epoch is UTC, so this is a UTC date.

    ONE EXPRESSION FOR EVERY FOLDER NAMED FROM A VALUE. A session request names its
    folder with it, and so does _resume() when the clock reads before the epoch, so a
    dead clock cannot name the one after 1970 while the other names it after 2020 --
    two spellings of the same fact are free to drift apart.

    NOT EVERY FOLDER IS NAMED FROM A VALUE. On a clock that reads after the epoch,
    _resume() names the start-up folder by the clock's own date, `started.date()`, which
    is how session-identity spec Sec.3.2 decides "the day": the wall-clock date on a
    trusted clock, the date the resumed value implies on a dead one. That is the
    intended behaviour, not a second spelling of this one.
    """
    return (BARCODE_EPOCH + datetime.timedelta(seconds=value)).date()


def _segment_factory(
    folder: Path,
    session_id: SessionId,
    *,
    rig: str,
    written_at: datetime.datetime,
    trust: ClockTrust,
    process_uuid: str,
    barcode_delay_us: int | None,
) -> tuple[Path, Callable[[], SegmentWriter]]:
    """The path of the next segment in `folder`, and a zero-argument factory that writes it.

    THE ONLY PLACE A SEGMENT HEADER IS DEFINED. The start of run() and every roll build
    their segment here, so a field added to the header cannot reach the first segment of
    a run and miss the ones after a session change. `process_uuid` and `barcode_delay_us`
    have no defaults here for the same reason: a call that forgot one would write a
    header saying "written before this field existed" or "unmeasured", and be wrong.

    A FACTORY, NOT A WRITER, because QueuedSink.roll() calls it on the owner thread after
    the old segment's trailer is written. Opening the file here, on the run loop's thread,
    would put two live segments under one sink. The folder is made inside the factory for
    the same reason: a failure to create it must surface through roll(), as a held error,
    rather than as a raise that leaves the old segment half-handled.

    The index is read when this is called, not when the factory runs. That is safe because
    only the run loop creates segments, and the outgoing segment is already on disk when
    this is called, so a roll into the folder it leaves would still see it. Were another
    process to add one in between, segments are opened with "x", so the collision fails
    loudly rather than overwriting. At the start of run() the folder may not exist yet,
    and a missing folder simply has no segments.
    """
    index = next_segment_index(folder)
    header = SyncBoxLogHeader(
        schema_version=1,
        session_id=str(session_id),
        rig=rig,
        boot_id=probe_boot_id(),
        written_at=written_at,
        gpio_map=GPIO_MAP,
        segment_index=index,
        clock_trusted=trust.trusted,
        clock_reason=trust.reason,
        process_uuid=process_uuid,
        barcode_delay_us=barcode_delay_us,
    )
    path = folder / segment_name(index)

    def make() -> SegmentWriter:
        folder.mkdir(parents=True, exist_ok=True)
        return SegmentWriter(path, header)

    return path, make


def _await_delivery(recorder: Recorder, boundary: int) -> None:
    """Wait until every capture path has delivered everything it captured up to
    `boundary`, or for ROLL_BARRIER_TIMEOUT_S. The roll that follows then closes the
    outgoing segment behind all of it (session-identity spec Sec.3.4, amended 2026-10-30).

    WHY WAITING IS ENOUGH. The capture threads deliver into the QueuedSink, which keeps
    queue order, and the roll is queued behind everything delivered before it. So all
    that each path has delivered when this returns lands in the outgoing segment. The B
    records are the run loop's own, written before it got here. A record captured after
    the boundary lands in the new segment, unless it is delivered while this waits.

    "UP TO THE BOUNDARY" MEANS CAPTURED BY THEN on the box clock, which the boundary is
    read from. An edge's tick is that clock, stamped by the kernel. A code word's tick
    counts the RP1 crystal, which drifts from the box clock by its ppm over a run, so a
    word captured just before the boundary can carry a tick just after it.

    THE WAIT IN NORMAL OPERATION. A roll runs after a frame, and the next frame is due
    about 0.8 s after that one ends.
      * The word pump polls every millisecond (rp1.backend._WORD_POLL_S), so it passes
        the boundary within a few milliseconds.
      * The edge pump moves its mark after each wait of EDGE_WAIT_S, 20 ms: to T - 1 when
        the wait times out, and to T - 20 ms - 1 when a read empties the kernel's buffer,
        where T is the clock read just before the wait (rp1.gpio.EdgePump.pump_once). On
        an idle line, the first wait to begin after the boundary times out within
        2 x 20 ms = 40 ms of it. On a busy line, the first wait to begin 20 ms after the
        boundary does so by 40 ms and ends by 60 ms.
      * With ~5 ms for Python's thread switch interval, that is about 65 ms at most. The
        rest of an open's or close's iteration is normally tens of milliseconds too (the
        flush's fsync, the state file's two, the trailer's, two manifest rebuilds, and
        the NTP probe: 62 ms cold, 9 ms warm on wl-sync-rig0), so a roll does not cost a
        frame in normal operation.
    The edge pump's wait was 0.2 s, which alone would have made the idle case 0.4 s.

    WITH A PATH STALLED, THE BOUND IS A BUDGET, NOT A GUARANTEE. The wait then runs to
    ROLL_BARRIER_TIMEOUT_S, 0.2 s. Beside a cold probe and two of the worst fsyncs seen
    (229 ms) that is 0.2 + 0.062 + 2 x 0.229 = 0.72 s, inside the 0.8 s. But the
    iteration also holds the state file's fsyncs, a probe allowed 5 s, and, when two
    requests are queued, two rolls and two waits, so a frame can be skipped. A path
    whose thread has stopped is not waited for at all (Rp1Backend.delivered_through),
    which removes the likeliest way here.

    A PATH THAT DOES NOT CATCH UP IS NAMED, AND THE ROLL GOES AHEAD. A WARNING gives the
    path, its mark and the boundary, and what the path delivers late lands in the new
    segment. Nothing is raised: a stalled path is check_health()'s to report.

    Real time, not the run loop's injected clocks: what it waits on is other threads.
    """
    deadline = time.monotonic() + ROLL_BARRIER_TIMEOUT_S
    while True:
        behind = {
            path: mark
            for path, mark in recorder.delivered_through().items()
            if mark is None or mark < boundary
        }
        if not behind or time.monotonic() >= deadline:
            break
        time.sleep(_BARRIER_POLL_S)
    for path, mark in sorted(behind.items()):
        log.warning(
            "rolling before %s has delivered everything captured up to the boundary: "
            "delivered through %s, boundary %d us; what it delivers late lands in the "
            "next segment",
            path,
            "nothing yet" if mark is None else f"{mark} us",
            boundary,
        )


@contextlib.contextmanager
def _handling(handler, *signums):
    """Install `handler` for the duration of the block, then put back what was there.

    RESTORED ON THE WAY OUT, because `run()` is a library call and `main()` is the entry
    point -- these handlers belong to the call, not to the process. Left installed, the
    caller's own shutdown handling stays replaced by a flag object that does nothing but
    swallow the signal, so a later call into this library leaves a process that cannot
    be stopped by the handler its owner installed. Nothing in this package noticed,
    because `main()` is the only caller today and wants exactly these handlers.

    Only signals successfully replaced are recorded, so off the main thread -- under a
    test runner, say -- this installs nothing and restores nothing.
    """
    replaced = {}
    for received in signums:
        try:
            replaced[received] = signal.signal(received, handler)
        except ValueError:
            pass  # not the main thread
    try:
        yield
    finally:
        for received, previous in replaced.items():
            signal.signal(received, previous)


def run(
    out: Path, backend, now_fn, duration_s: float | None, tick_fn,
    mono_fn=time.monotonic,
    requests: queue.SimpleQueue[SessionRequest] | None = None,
    barcode_delay_us: int | None = None,
) -> Path:
    """Record until `duration_s` elapses or a termination signal arrives.

    `barcode_delay_us` is the bench loopback's measured delay from a `B` tick to the
    frame's lead edge, written into every segment's header; None until it is measured
    (see SyncBoxLogHeader.barcode_delay_us).

    `requests` is how the task PC's open and close reach the recording (spec 3.1-3.4).
    The endpoint puts a SessionRequest on it and waits on its future; this loop carries
    it out between two emit iterations, so a session changes at a whole-second boundary
    and the capture threads, which only ever hold the QueuedSink, never see the folder
    change. None means no endpoint, and the loop behaves exactly as it did before.

    `now_fn`, `tick_fn` and `mono_fn` are injected so the whole loop is testable without
    sleeping: tests pass a scripted wall clock, a no-op sleep and, where pacing is under
    test, a fake monotonic clock. Pacing reads `mono_fn`, never the wall clock, which can
    jump in both directions.
    """
    started = now_fn()
    started_mono = mono_fn()  # the same instant, so the pacing grid can be placed on it
    # This process's id, in every segment it writes (SyncBoxLogHeader.process_uuid).
    # Made HERE, once per call, not at import: the Recorder below, and with it the one
    # clock every tick is placed on, is also made once per call, and the id names that
    # clock. main() calls run() once, so one call is one process.
    process_uuid = str(uuid.uuid4())

    # Resolved BEFORE the day directory is created, because on a dead-RTC clock the
    # directory must not be named after the bogus reading. See _resume().
    checkpoint_path = out / CHECKPOINT_NAME
    checkpoint = read_checkpoint(checkpoint_path)
    # The run's last DEFINITE answer from the NTP probe, refreshed whenever a probe
    # answers. A roll whose probe fails falls back to it (see ask_ntp).
    ntp_known = probe_ntp_synchronized()
    # When NTP was last asked, on the monotonic clock: the start's probe follows
    # `started_mono` at once. The poll in the loop waits `ntp_poll_wait_s` from it:
    # NTP_POLL_INTERVAL_S, doubled after each failed poll up to NTP_POLL_BACKOFF_MAX_S.
    ntp_asked_at = started_mono
    ntp_poll_wait_s = NTP_POLL_INTERVAL_S
    # Whether NTP has said yes at any point in this run. Until it has, a run whose last
    # definite answer is no keeps asking, and rolls once at the first yes (the loop).
    ntp_synced = ntp_known is True
    # Set when the poll hears the first yes; the roll it asks for waits for the next
    # iteration's frame (see the loop).
    ntp_roll_due = False
    # How many rolls this run has made, so the loop can tell whether a session roll
    # judged a segment in a given iteration.
    rolls = 0
    first_value, trust, day_date = _resume(started, checkpoint, ntp_known)

    # WHICH FOLDER THIS RUN RECORDS INTO is decided by the state file, not the date. A
    # session that is open stays open across a restart AND across midnight (spec 3.1: "a
    # restart is not a visit"), so its folder is resumed whatever today's date is.
    # Otherwise no animal is in the rig and the segments go to the between-sessions
    # area, named for the day the barcode value implies -- still `day_date` from
    # _resume(), so a dead clock never names a folder after 1970 or 2000. `read_open`
    # degrades a missing or corrupt state file to None, so a lost state file records
    # between sessions rather than refusing to start.
    current = read_open(out)
    if current is not None:
        session_id = current.session_id
        folder = session_dir(out, session_id)
    else:
        session_id = unassigned_id(day_date)
        folder = unassigned_dir(out, day_date)
    folder.mkdir(parents=True, exist_ok=True)

    # Rebuild first, so any segment left trailer-less by a crash is recorded as such
    # before this run adds its own.
    write_manifest(folder)

    writer_path, make_writer = _segment_factory(
        folder,
        session_id,
        rig=out.name,
        written_at=started,
        trust=trust,
        process_uuid=process_uuid,
        barcode_delay_us=barcode_delay_us,
    )
    writer = make_writer()
    # ONE OWNER THREAD FOR THE SEGMENT, on every backend. The RP1 backend delivers from
    # capture threads, and SegmentWriter is neither thread-safe nor quick: its flush()
    # fsyncs, so a capture thread calling into it would block on the disk and stop
    # draining an 8-deep PIO FIFO. Wrapped for FakeBackend too, so the tests run the
    # path the box runs. See wl_sync.service.QueuedSink.
    sink = QueuedSink(writer)

    recorder = Recorder(backend, sink=sink)
    recorder.capture_codes(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN)
    recorder.capture_edges(EDGE_PINS)

    # journald is the only status surface this box has -- spec Sec.7 calls it "the
    # honest status surface" and Sec.10 records that no software-controllable panel
    # indicator exists, because the heartbeat LED is a buffered leg off BARCODE_RAW
    # itself. One line, everything an operator needs to tell a healthy start from a
    # degraded one without opening a file.
    log.info(
        "recording %s (session %s): barcodes from %d, clock_trusted=%s clock_reason=%s "
        "boot_id=%s process_uuid=%s",
        writer_path,
        session_id,
        first_value,
        trust.trusted,
        trust.reason or "-",
        probe_boot_id() or "-",
        process_uuid,
    )

    values = _ValueSource(now_fn, first_value, mono_fn)
    generator = BarcodeGenerator(backend, BARCODE_PIN)
    health = _HealthReport(backend)

    # The last barcode of the segments this run has rolled away from. A roll leaves the
    # new writer with no barcode yet, so a run that rolls in its final iteration would
    # otherwise read "no barcodes were written" at the end and rewind the checkpoint to
    # `first_value`; a restart would then reuse values, which must be unique across the
    # box (spec Sec.4). See the end of the loop.
    carried_last: int | None = None

    def ask_ntp(*, polling: bool = False) -> None:
        """Ask NTP whether it has synced, and note when it was asked.

        A FAILED PROBE NEVER UPGRADES TRUST. None means "could not ask". On the box,
        where timedatectl exists, that is a probe that timed out or hit a D-Bus fault,
        not an answer, and taken as "not untrusted" it turned the start's definite
        False into a trusted segment. So a None keeps the run's last answer. A run
        whose probe has never answered (a laptop, a container) still holds None,
        and is judged as the start judges it. A None never counts as the first sync.

        `polling` is the loop's poll for the first sync: its probe is bounded by
        NTP_POLL_TIMEOUT_S, and its failure doubles the wait before the next poll. A
        definite answer from any probe resets the wait.
        """
        nonlocal ntp_known, ntp_asked_at, ntp_synced, ntp_poll_wait_s
        if polling:
            answer = probe_ntp_synchronized(timeout_s=NTP_POLL_TIMEOUT_S)
        else:
            answer = probe_ntp_synchronized()
        ntp_asked_at = mono_fn()
        if answer is None:
            if polling:
                ntp_poll_wait_s = min(2 * ntp_poll_wait_s, NTP_POLL_BACKOFF_MAX_S)
        else:
            ntp_known = answer
            ntp_poll_wait_s = NTP_POLL_INTERVAL_S
        if answer is True:
            ntp_synced = True

    def roll(new_folder: Path, new_id: SessionId, why: str) -> None:
        """Close the current segment and continue in a new one in `new_folder`, which
        may be the folder it is already in. `why` is for the journal.

        THE NEW SEGMENT IS JUDGED HERE, never carried over from the start: a box powered
        up before NTP synced otherwise flags every segment of the run. The caller asks
        NTP first (ask_ntp) so that the wall and monotonic readings sit together, and
        the wall reading is the header's `written_at`, so a segment says when it was
        judged. See clock.evaluate_roll for why NTP alone is not enough.

        A failure IN the roll propagates: the sink is dead and can record nothing.

        THE BOUNDARY IS READ FIRST, and every record captured up to it lands in the
        outgoing segment (_await_delivery). At an open or close that is after the request
        has been taken and carried out, so all that was captured before the request is on
        the outgoing side. wl-preproc never reads unassigned/, so before this a code word
        captured before a close and delivered after the roll was lost to its session.
        The roll when NTP first syncs waits too: it costs a few tens of milliseconds, and
        every segment boundary then means the same thing.
        """
        nonlocal folder, session_id, carried_last, rolls
        _await_delivery(recorder, recorder.now())
        rolls += 1
        old_folder, outgoing = folder, sink.sink
        written_at = now_fn()
        roll_trust = evaluate_roll(written_at, values.emitting(), ntp_known)
        new_segment, make_writer = _segment_factory(
            new_folder,
            new_id,
            rig=out.name,
            written_at=written_at,
            trust=roll_trust,
            process_uuid=process_uuid,
            barcode_delay_us=barcode_delay_us,
        )
        sink.roll(make_writer, now_fn())
        # The outgoing writer's span is final now that its trailer is written.
        if outgoing.barcode_last is not None:
            carried_last = outgoing.barcode_last
        folder, session_id = new_folder, new_id
        log.info(
            "recording into %s (session %s, %s): clock_trusted=%s clock_reason=%s",
            new_segment,
            new_id,
            why,
            roll_trust.trusted,
            roll_trust.reason or "-",
        )

        # BOTH FOLDERS' MANIFESTS. The old one now has its closed segment to record. The
        # new one is written now, not when the recording leaves it, so a session entered
        # by an open has a manifest for the whole visit rather than none; its new segment
        # is left out, as a start leaves out its own (see build_manifest). A roll in
        # place has one folder, rebuilt once: a first rebuild with nothing left out
        # would list the new segment as a crash, since it has no trailer yet.
        #
        # ON THE RUN LOOP'S THREAD, so these must be cheap, and they are: a segment some
        # earlier rebuild in this process summarised is not parsed again while it is
        # unchanged (manifest._summarise_cached). The start has summarised the folder
        # it began in, and a roll into a folder summarises that one. What a roll can
        # still pay for is a crashed segment in a folder no rebuild in this process has
        # seen: after a restart in the middle of a visit, a crashed segment in that day's
        # unassigned/ folder is parsed once, at the visit's close.
        if new_folder == old_folder:
            rebuilds = [(new_folder, new_segment)]
        else:
            rebuilds = [(old_folder, None), (new_folder, new_segment)]
        for rebuilt, still_open in rebuilds:
            try:
                write_manifest(rebuilt, excluding=still_open)
            except Exception as error:  # noqa: BLE001 - recording matters more than the summary
                # Not retried. A session folder that has been left is never entered
                # again, so its manifest stays as it is until someone rebuilds it; an
                # unassigned/ folder is rebuilt when the recording next comes into it.
                log.error("could not rebuild the manifest of %s: %s", rebuilt, error)

    def follow_state(day: datetime.date) -> None:
        """Make the recording follow the state file, rolling only if it does not already.

        THE STATE FILE IS WHAT A RESTART WOULD RESUME, so it is what the recording must
        match -- after EVERY request, including one that failed. open_session saves the
        state and only then fsyncs the directory; if that fsync raises, the state already
        names the new session while the caller was told the open failed. Left in the old
        folder, the retry (same request_id, so `reused`) would roll nowhere and the box
        would record the new animal into the previous one's folder until a restart: a
        session folder holding two animals. Reading the state back instead of trusting the
        outcome closes that, for opens and closes alike. A refusal changes no state, so
        this is a no-op for it.

        No session open and already between sessions stays put: there is no midnight roll
        between visits. THESE EARLY RETURNS ARE WHAT KEEP A REUSED REQUEST FROM ROLLING.
        The one roll that stays in its folder, when NTP first syncs, does not come
        through here; it calls roll() itself (see the loop).

        A failure IN the roll propagates; the caller completes the future first.
        """
        current = read_open(out)
        if current is not None:
            new_folder, new_id = session_dir(out, current.session_id), current.session_id
            if folder == new_folder:
                return
            why = "session opened"
        else:
            if folder.is_relative_to(out / "unassigned"):
                return
            new_folder, new_id = unassigned_dir(out, day), unassigned_id(day)
            why = "session closed"
        ask_ntp()
        roll(new_folder, new_id, why)

    def carry_out(request: SessionRequest, value: int) -> None:
        """Do what `request` asks and complete its future, whatever happens.

        THE FUTURE IS COMPLETED BEFORE ANYTHING PROPAGATES, because an HTTP caller blocked
        on it would otherwise sit out its whole timeout on a request that already failed.

        A refusal or a failure in open_session/close_session goes to the caller, and
        recording then follows whatever the state file says (see follow_state) -- which is
        the current folder unless the failure came after the state was saved. A failure IN
        the roll is different: the sink is dead and can record nothing, so it is set on the
        future and then raised. Under systemd `Restart=always` the box resumes from the
        state file it saved.
        """
        day = _barcode_day(value)
        error: Exception | None = None
        result = None
        try:
            if request.kind == "open":
                result = open_session(out, request.argument, now_fn(), day)
                if not result.reused:
                    # Logged as soon as the state changed, so a roll that then fails
                    # still leaves the forced close in the journal (spec 3.1).
                    if result.closed is not None:
                        log.error(
                            "session %s opened while %s was still open: %s was closed "
                            "to make room",
                            result.session.session_id, result.closed, result.closed,
                        )
                    log.info(
                        "opened session %s into %s",
                        result.session.session_id,
                        session_dir(out, result.session.session_id),
                    )
            else:
                result = SessionId.parse(request.argument)
                close_session(out, result)
                log.info("closed session %s", result)
        except Exception as caught:  # noqa: BLE001 - handed to the caller, see above
            error = caught
            if not isinstance(caught, SessionError):
                log.error("%s request failed: %r", request.kind, caught)

        try:
            follow_state(day)
        except BaseException as roll_error:
            request.reply.set_exception(roll_error)
            raise
        if error is not None:
            request.reply.set_exception(error)
        else:
            request.reply.set_result(result)

    stopping = _StopFlag()
    with _handling(stopping, signal.SIGINT, signal.SIGTERM):
        elapsed = 0.0
        # THE GRID: the wall clock's half seconds, located once on the monotonic clock.
        # A barcode value is the wall second the frame reads. A one-second period sitting
        # on a second boundary straddles it with ordinary wake-up jitter, reads one second
        # twice, and has the clamp report a healthy clock as "not moving forwards" for
        # the whole run. Mid-second keeps every reading half a second from either edge.
        # CLOCK_MONOTONIC follows the wall clock's NTP rate adjustments, so the grid
        # stays put unless the wall clock is stepped.
        paced_from = started_mono + (0.5 - started.timestamp() % 1.0) % 1.0
        announced_first = False
        while not stopping.set and (duration_s is None or elapsed < duration_s):
            value = values()

            # THE EXACT VALUE, EVERY SECOND. No high-water mark, no interval: "the
            # checkpoint is never behind the last value emitted" stops being an argument
            # about the loop's period and becomes `checkpoint == last emitted`, true by
            # construction. Two earlier designs carried a margin instead and both ratcheted
            # -- most recently a startup high-water write that a crash loop compounded, 25
            # restarts over 75 s leaving the counter 1449 s ahead of a perfectly healthy
            # clock. A margin the restart cannot verify is a ratchet; the fix is to have no
            # margin, not a better one. write_checkpoint is atomic and fsynced, and at
            # ~30 bytes/s this is ~2.6 MB/day on the NVMe spec Sec.4.1 chose explicitly.
            #
            # MARKED BEFORE IT REACHES THE WIRE, which is a reversal: until 2026-08-22 the
            # frame was emitted first, on the reasoning that "recorded but not marked" was
            # the dangerous ordering. That reasoning weighed the wrong pair. A crash between
            # emit_frame() and this line left a value on the wire that no checkpoint knew
            # was spent, and a restart on a stalled clock re-issued it -- two moments under
            # one identity, the one failure the value must never have.
            #
            # The reverse failure is a value marked and never emitted, which skips it. That
            # is not a lie: a barcode value IS seconds since 2020, so a skipped value reads
            # as the one-second outage that genuinely occurred. One ordering is
            # self-describing, the other is false; that asymmetry is the whole argument.
            write_checkpoint(checkpoint_path, value)

            # Sampled BETWEEN the mark and the frame, not at the top of the iteration. A
            # frame takes 200 ms (barcode.FRAME_US) and the record means the LEAD rising
            # edge, which is what every receiving device reports, so reading the counter
            # after emit_frame() returns puts this box's own alignment record a systematic
            # 200 ms behind everything it is aligning. Reading it before write_checkpoint
            # would be wrong in the same direction and for the same reason -- that call
            # fsyncs twice, so the tick would predate the edge by however long the medium
            # took. Immediately before emission is the only placement that stays true.
            #
            # Recorded through the Recorder rather than straight to the writer so the tick
            # is unwrapped on the Recorder's shared clock -- a raw counter would diverge from the
            # E and W ticks it exists to align by 2^32 us at every 71.6-minute wrap.
            tick = backend.now_us()
            generator.emit_frame(value)
            frame_end = mono_fn()
            recorder.record_barcode(tick, value)
            if not announced_first:
                # THE VALUE THAT WENT ONTO THE WIRE, which the start line cannot know: it
                # names the floor _resume() computed, and the first frame reads the clock
                # again, so it usually emits a later value (on the box, 2026-10-19: the
                # start line said ...321, the first B record was ...322).
                log.info("first barcode %d emitted", value)
                announced_first = True
            # A barrier, not a marker: it returns once everything queued above it is on
            # disk, which keeps spec Sec.4's one-second crash-loss bound exact.
            sink.flush()
            health()

            # AFTER THE FRAME IS ON DISK AND BEFORE PACING: the frame just emitted belongs
            # to the session that was open when it was emitted, and the next one to the
            # session this makes. Drained with get_nowait() because the loop must never
            # wait on an endpoint it does not need.
            rolls_before_requests = rolls
            while requests is not None:
                try:
                    request = requests.get_nowait()
                except queue.Empty:
                    break
                # False for a future the caller cancelled (its HTTP request timed out):
                # carrying that out would open a session nobody is waiting for.
                if request.reply.set_running_or_notify_cancel():
                    carry_out(request, value)

            # ONE ROLL IN PLACE, WHEN NTP FIRST SYNCS (the user's option 2, 2026-10-30).
            # After a power-up the box starts before NTP syncs, so its first segment says
            # ntp_unsynchronized, and a roll at an open or close re-judges that only when
            # a session changes, which between visits can be hours away. So while the
            # run's last definite answer is no, the loop asks again every
            # NTP_POLL_INTERVAL_S, and at the first yes it rolls once into a new segment IN
            # THE SAME FOLDER, judged as any roll is (evaluate_roll). The new segment
            # carries the same process_uuid and the next segment_index, which is how a
            # reader tells this roll from a restart.
            #
            # THE ROLL WAITS ONE ITERATION. NTP's first sync usually steps the clock and
            # reports itself synchronised in the same call. A step that lands after
            # this iteration's frame and before the poll would be judged against a value
            # read before it: past the tolerance that is barcode_off_clock, and the one
            # roll would be spent on it. So the yes sets `ntp_roll_due`, and the roll
            # happens after the next frame, whose value is read after the step. If a
            # session opened or closed in that iteration, its roll has already asked NTP
            # and judged a segment, so the NTP roll is dropped.
            #
            # A BUDGET FOR THE POLL: its probe gives up after NTP_POLL_TIMEOUT_S, which
            # fits in the ~0.75 s after the frame with ~0.45 s to spare, and each poll
            # that fails doubles the wait before the next, up to NTP_POLL_BACKOFF_MAX_S
            # (see ask_ntp).
            #
            # Once NTP has said yes in this run it is not asked here again: at the start,
            # at this roll, or at an open or close, whose roll has already judged its own
            # segment. A None is "could not ask" and changes nothing. Placed after the
            # requests, so that an open in this iteration counts as the last ask.
            if ntp_roll_due:
                ntp_roll_due = False
                if rolls == rolls_before_requests:
                    roll(folder, session_id, "NTP synchronized")
            if (
                not ntp_synced
                and ntp_known is False
                and mono_fn() - ntp_asked_at >= ntp_poll_wait_s
            ):
                ask_ntp(polling=True)
                ntp_roll_due = ntp_synced

            # THE NEXT GRID POINT THAT LEAVES THE DECODER ITS IDLE, not a fixed second
            # after the frame. On hardware a frame holds the pin for 200 ms, so
            # `tick_fn(1.0)` made the period 1.2 s while every bound this loop promises
            # is per second. A frame is found by a rising edge after IDLE_MIN_US of LOW,
            # so a grid point too close behind a late frame is skipped, as is any point
            # already past: a debt is never repaid with frames back to back. The target
            # is always past this frame's end, so two frames can never share a second.
            now = mono_fn()
            earliest = max(now, frame_end + _IDLE_BEFORE_FRAME_S)
            target = paced_from + math.ceil(earliest - paced_from)
            tick_fn(max(0.0, target - now))
            elapsed += 1.0

        # Capture stops FIRST, so what it still had in flight reaches a sink that is open.
        # Closing the sink drains its queue and then closes the segment behind it, and
        # only after that is the writer's barcode span final.
        #
        # A capture that fails to stop does not cost the segment its trailer. Without
        # one a run that recorded perfectly reads as a crash, and whatever close() had
        # just delivered would never reach the disk.
        try:
            backend.close()
        except Exception as error:  # noqa: BLE001 - logged; the segment still closes
            log.error("capture did not stop cleanly, closing the segment anyway: %s", error)
        # Once more after stopping: whatever capture held at the end -- the last second,
        # the final FIFO drain -- would otherwise never be reported.
        health()
        health.summary()
        sink.close(now_fn())
        # `or` would conflate a legitimate barcode value of 0 -- the epoch itself, which a
        # dead-RTC start really does emit -- with "no barcodes were written".
        # Read through the sink, not the local `writer`: a roll replaces the segment
        # writer, and the first one's span stops at the roll.
        # NEVER REWIND: a roll in the last iteration leaves the current writer with no
        # barcode, and the value emitted before the roll lives only in the one carried.
        barcode_last = sink.sink.barcode_last
        if barcode_last is None:
            barcode_last = carried_last
        last = first_value if barcode_last is None else barcode_last
        write_checkpoint(checkpoint_path, last)
        write_manifest(folder)
        return folder


class _ValueSource:
    """Barcode values from the wall clock, anchored at the value this run resumed on.

    THE ONLY PLACE A BARCODE VALUE IS CHOSEN, which is what lets the caller mark a value
    as spent before it reaches the wire and know that the mark and the frame agree.
    BarcodeGenerator held a second clamp until 2026-08-22; a clamp downstream of the
    mark emits something other than what was marked, so it moved here.

    The floor -- one past the last value issued -- is what keeps the counter unique when
    the clock stalls, jumps backwards, or cannot be read at all. An unreadable clock is
    not hypothetical: below the 2020 epoch `value_from_clock` raises, and a run that
    started on a dead RTC would otherwise die on its first frame having already been
    rescued at startup by _resume(). One second of run time is one unit of floor, so the
    floor alone is a correct (if imprecise) barcode source for as long as the run lasts.
    """

    def __init__(self, now_fn, first_value: int, mono_fn=time.monotonic) -> None:
        self._now_fn = now_fn
        self._mono_fn = mono_fn
        self._first = first_value
        self._last: int | None = None
        self._last_chosen_at = 0.0  # mono_fn() when `_last` was chosen
        self._clamped = False

    def __call__(self) -> int:
        # `_last + 1` rather than `_first + calls`: both are monotonic, but this one is
        # TIGHTER whenever the clock has run ahead of the start value, and it is exact
        # rather than inferred. It is also the whole of the uniqueness guarantee now --
        # BarcodeGenerator used to hold a second clamp and no longer does, because a
        # clamp downstream of the checkpoint can emit something other than what was
        # marked. One decision, made here, recorded before it becomes irreversible.
        floor = self._first if self._last is None else self._last + 1
        try:
            from_clock = next_value(self._now_fn(), None)
        except ValueError:
            from_clock = None  # pre-epoch or out of range: the floor is all we have
        chosen_at = self._mono_fn()  # beside the wall reading: see emitting()
        if from_clock is not None and from_clock >= floor:
            self._clamped = False
            return self._issue(from_clock, chosen_at)
        if not self._clamped:
            # EDGE-TRIGGERED, deliberately. Spec Sec.7 names "time moved backwards" as a
            # trust condition and nothing recorded it mid-run -- a header is written
            # when its segment opens and must not be rewritten afterwards, so a journal
            # line is the only honest place for it until a roll opens the next segment
            # (clock.evaluate_roll). Warning once per episode rather than once per
            # frame, because a stalled clock binds every second and 86,400 identical
            # lines a day would bury everything else in the journal.
            log.warning(
                "wall clock is not moving forwards (reads %s, need > %d): barcode "
                "values are coming from the monotonic floor. Gap arithmetic across "
                "this run will be imprecise; the values stay unique.",
                "unreadable" if from_clock is None else from_clock,
                floor,
            )
        self._clamped = True
        return self._issue(floor, chosen_at)

    def emitting(self) -> int:
        """The value the box is on now: the last one chosen, plus the whole seconds the
        monotonic clock has advanced since it was chosen.

        WHAT A ROLL'S TRUST IS JUDGED ON (clock.evaluate_roll), which compares it with
        the wall clock. A roll is carried out in the iteration whose frame it follows,
        so the last value alone is right in the ordinary case. It is advanced anyway,
        because the work between a frame and the roll -- the flush barrier, the open's
        fsyncs -- can stall on a slow disk, and the seconds of a stall would otherwise
        read as the wall clock running ahead of the values.

        ON THE MONOTONIC CLOCK, the one the loop paces on, so that this is the box's own
        count. Reading the wall clock here would compare the wall clock with itself, and
        a step would never be seen.

        ANCHORED WHERE THE VALUE WAS CHOSEN, not where its frame went out. A value is
        the wall second read at that instant, so advancing it from the same instant
        compares like with like. The frame follows a few milliseconds later, after the
        checkpoint's two fsyncs.

        Good to ONE SECOND. The value is a whole second, and the wall clock was some
        fraction into it when it was read, so on a clock that agrees with the values
        this trails value_from_clock(now) by 0 or 1. The tolerance it is judged against
        is 60 s.

        Called only after a value has been chosen, since a roll follows a frame. The
        start value stands in before that.
        """
        if self._last is None:
            return self._first
        return self._last + math.floor(self._mono_fn() - self._last_chosen_at)

    def _issue(self, value: int, chosen_at: float) -> int:
        """Record the decision and hand it back, refusing anything unencodable.

        THE CEILING IS CHECKED HERE BECAUSE HERE IS THE ONLY PLACE A VALUE IS CHOSEN.
        It used to guard the clock branch alone -- `value_from_clock` raises above the
        ceiling and the caller catches it -- while the floor branch walked past it into
        `barcode.encode`, which raises the identical error from somewhere far less
        informative. Collapsing the two branches into one decision point makes a single
        guard cover both by construction, rather than by remembering to write it twice.
        """
        if value > MAX_BARCODE:
            raise ValueError(f"barcode value out of 32-bit range: {value}")
        self._last, self._last_chosen_at = value, chosen_at
        return value


class _HealthReport:
    """`backend.check_health()`, called every iteration and reported once per episode.

    A FAILURE IS LOGGED AND RECORDING CONTINUES. A degraded capture path still leaves
    the other paths and the barcodes every other device aligns on, and stopping the box
    would lose all of those for the rest of the day.

    EDGE-TRIGGERED, for the reason the stalled-clock warning is. A capture thread that
    has died never comes back, so check_health() raises the same thing every second,
    and 86,400 identical lines a day would bury the journal. A healthy call ends the
    episode, so the same failure after a recovery is reported again.

    IT ALSO WATCHES THE EDGE WITNESS (`backend.stamp_lag()`). A roll's boundary assumes
    the kernel buffers an edge within an allowance of stamping it (rp1.gpio,
    EDGE_STAMP_LAG_US), and the edge pump bounds that delay from above on every read.
    Edges going over are a WARNING, once per episode: an episode lasts while each check
    finds more of them. `summary()` gives the largest bound of the run, so a quiet run
    says the assumption held.
    """

    def __init__(self, backend) -> None:
        self._backend = backend
        # The kinds of a CaptureDegraded, or the message of any other failure.
        self._reported: frozenset[str] | str | None = None
        self._lag_over = 0
        self._lag_episode = False

    def __call__(self) -> None:
        try:
            self._backend.check_health()
        except Exception as error:  # noqa: BLE001 - reported, never fatal; see above
            # Keyed on WHICH paths are failing and how, never on the message: a buffer
            # that keeps overflowing reports a different count every second, and keyed
            # on text that is a new "episode" every second.
            episode = getattr(error, "kinds", None) or str(error)
            if episode != self._reported:
                log.error("capture degraded, still recording: %s", error)
            self._reported = episode
        else:
            self._reported = None
        self._watch_stamp_lag()

    def _watch_stamp_lag(self) -> None:
        lag = self._backend.stamp_lag()
        over = 0 if lag is None else lag.over
        if over > self._lag_over:
            if not self._lag_episode:
                log.warning(
                    "an edge reached the pump up to %d us after the kernel stamped it, "
                    "more than the %d us a roll's boundary allows the kernel to buffer "
                    "it in (%d edges over so far). The bound includes the pump's own "
                    "delay, so the kernel may still be within it; if not, an edge "
                    "stamped just before a roll can land in the next segment",
                    lag.max_us,
                    lag.allowance_us,
                    lag.over,
                )
            self._lag_episode = True
        else:
            self._lag_episode = False
        self._lag_over = over

    def summary(self) -> None:
        """The run's largest edge witness, if the backend has one, for the journal."""
        lag = self._backend.stamp_lag()
        if lag is not None:
            log.info(
                "edges reached the pump at most %d us after the kernel stamped them this "
                "run; a roll's boundary allows the kernel %d us (%d over)",
                lag.max_us,
                lag.allowance_us,
                lag.over,
            )


class _StopFlag:
    def __init__(self) -> None:
        self.set = False

    def __call__(self, _signum, _frame) -> None:
        self.set = True


def _hardware_backend(chip_path: str):
    """The RP1 backend, imported only here: building it needs libpiolib and a GPIO chip,
    which exist on the box and nowhere else, so `--fake` never touches either."""
    from wl_sync.rp1.backend import Rp1Backend

    return Rp1Backend(chip_path=chip_path)


def _listen_address(text: str) -> tuple[str, int]:
    """Parse `--listen HOST:PORT`. A typo here is configuration the operator must see,
    so it is an argparse error (exit 2), unlike a failure to BIND, which is logged and
    survived. `rpartition` so a host that itself contains colons still splits on the
    port."""
    host, _, port_text = text.rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        port = -1
    if not host or not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"expected HOST:PORT with a port from 0 to 65535, got {text!r}"
        )
    return host, port


# The largest --barcode-delay-us accepted: 100 ms. The real delay is the software path
# from reading the counter to the pin rising, microseconds to a few milliseconds. More
# than this is a unit or measurement error, such as nanoseconds typed as microseconds,
# and every header of the day would carry it as a correction.
BARCODE_DELAY_MAX_US = 100_000


def _barcode_delay(text: str) -> int:
    """Parse `--barcode-delay-us`: whole microseconds, from 0 to BARCODE_DELAY_MAX_US,
    as ASCII digits only. Anything else is an argparse error (exit 2), for the reason
    `--listen` is one.

    Digits only, because int() also takes a sign, surrounding spaces, underscores
    ("8_50") and non-ASCII digits, none of which is how anyone writes a delay in a unit
    file. That also refuses a negative delay, which is a sign error: the tick is read
    before the frame goes out, so the edge follows it."""
    if not (text.isascii() and text.isdigit()) or int(text) > BARCODE_DELAY_MAX_US:
        raise argparse.ArgumentTypeError(
            f"expected a whole number of microseconds from 0 to {BARCODE_DELAY_MAX_US}, "
            f"in digits, got {text!r}"
        )
    return int(text)


def _read_token(path: Path | None) -> str | None:
    """The bearer token, or None -- meaning the endpoint answers GET only.

    THE BOX MUST RECORD REGARDLESS (spec 2, anti-goal), and under `Restart=always`
    anything that raises at start-up is a crash loop that records nothing. So a token
    file that is missing, unreadable or empty is logged at ERROR and degrades to
    read-only. The path is logged, never the contents: the journal is not a secret store.
    """
    if path is None:
        return None
    try:
        token = path.read_text().strip()
    except (OSError, UnicodeDecodeError) as error:
        log.error(
            "session token file %s is unreadable (%s); the endpoint will serve GET only",
            path, type(error).__name__,
        )
        return None
    if not token:
        log.error(
            "session token file %s is empty; the endpoint will serve GET only", path
        )
        return None
    return token


def _start_endpoint(out: Path, listen: tuple[str, int] | None, token_file: Path | None,
                    requests: queue.SimpleQueue):
    """Start the session endpoint, or return None and log why not.

    A `--listen` that cannot bind (port in use, no such address) is logged at ERROR and
    the box records without the endpoint, for the same reason as _read_token: a crash
    loop records nothing.
    """
    if listen is None:
        if token_file is not None:
            log.warning("--session-token-file given without --listen; it is ignored")
        return None
    token = _read_token(token_file)
    host, port = listen
    try:
        return serve(out, requests, token, host, port)
    except (OSError, ValueError, OverflowError) as error:
        # ValueError covers UnicodeError (a hostname that will not encode) and
        # OverflowError a port socket rejects; nothing at start-up may raise (R18).
        log.error(
            "session endpoint cannot listen on %s:%s (%s); recording without it",
            host, port, error,
        )
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wl-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="record a day")
    record.add_argument("--out", type=Path, required=True, help="rig session root")
    record.add_argument("--fake", action="store_true", help="run on the in-memory backend")
    record.add_argument("--duration", type=float, default=None, help="stop after S seconds")
    record.add_argument(
        "--gpiochip",
        default="/dev/gpiochip0",
        help="chardev carrying RP1's GPIO bank; it has moved between releases, so "
        "confirm it by label (gpioinfo) rather than trusting the default",
    )
    record.add_argument(
        "--listen",
        type=_listen_address,
        default=None,
        metavar="HOST:PORT",
        help="serve the session endpoint here; default none, meaning no endpoint. "
        "IPv6 is not supported: use an IPv4 address or a hostname",
    )
    record.add_argument(
        "--session-token-file",
        type=Path,
        default=None,
        help="file holding the bearer token (one line) that POSTs must present; "
        "without it the endpoint serves GET only",
    )
    record.add_argument(
        "--barcode-delay-us",
        type=_barcode_delay,
        default=None,
        metavar="N",
        help="the bench loopback's measured delay from a B record's tick to the frame's "
        "lead edge, in whole microseconds, written into every segment header; default "
        "none, meaning unmeasured",
    )
    args = parser.parse_args(argv)

    # To stderr, which systemd routes to the journal via StandardError=journal. Only
    # the entry point configures handlers; `run()` is a library call and must not.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(message)s",
    )

    requests: queue.SimpleQueue[SessionRequest] = queue.SimpleQueue()
    server = _start_endpoint(args.out, args.listen, args.session_token_file, requests)
    try:
        day = run(
            out=args.out,
            backend=FakeBackend() if args.fake else _hardware_backend(args.gpiochip),
            now_fn=lambda: datetime.datetime.now(datetime.timezone.utc),
            duration_s=args.duration,
            tick_fn=time.sleep,
            requests=requests if server is not None else None,
            barcode_delay_us=args.barcode_delay_us,
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    print(f"recorded {day}")
    return 0
