"""`wl-sync record` -- the run loop that makes this a sync box rather than a library.

The box is the first device on each day and the last off, and it runs continuously
while every other device starts and stops blocks inside that run. Each process run
writes one segment into the folder of the session that is open -- or, with none open,
into the between-sessions folder for the day -- and a restart joins that folder rather
than beginning a new one. systemd's Restart=always is what makes the stitching automatic.
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
from collections.abc import Callable
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.barcode import IDLE_MIN_US
from wl_sync.clock import (
    BARCODE_EPOCH,
    MAX_BARCODE,
    ClockTrust,
    evaluate_clock,
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

# `logging`, not `print`: stdout is block-buffered when it is a pipe, which is what
# systemd gives a service, so prints can sit unflushed in a daemon that runs all day.
# journald is this box's only status surface (spec Sec.7, Sec.10) -- the heartbeat LED
# is a buffered leg off BARCODE_RAW and cannot be driven by software.
log = logging.getLogger(__name__)


def probe_ntp_synchronized() -> bool | None:
    """True/False from timedatectl, or None when it cannot be asked -- a laptop, a
    container. None is 'unknown', which evaluate_clock deliberately does not treat as
    untrusted."""
    try:
        completed = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
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
    """The date a barcode value implies: it IS seconds since the 2020 epoch (Sec.4).

    ONE EXPRESSION, USED BY EVERYTHING THAT NAMES A FOLDER FROM A VALUE. _resume() names
    the start-up folder with it and a session request names its folder with it, so a dead
    clock cannot name the one after 1970 while the other names it after 2020 -- two
    spellings of the same fact are free to drift apart.
    """
    return (BARCODE_EPOCH + datetime.timedelta(seconds=value)).date()


def _segment_factory(
    folder: Path,
    session_id: SessionId,
    *,
    rig: str,
    written_at: datetime.datetime,
    trust: ClockTrust,
) -> tuple[Path, Callable[[], SegmentWriter]]:
    """The path of the next segment in `folder`, and a zero-argument factory that writes it.

    THE ONLY PLACE A SEGMENT HEADER IS DEFINED. The start of run() and every roll build
    their segment here, so a field added to the header cannot reach the first segment of
    a run and miss the ones after a session change.

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
    )
    path = folder / segment_name(index)

    def make() -> SegmentWriter:
        folder.mkdir(parents=True, exist_ok=True)
        return SegmentWriter(path, header)

    return path, make


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
) -> Path:
    """Record until `duration_s` elapses or a termination signal arrives.

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

    # Resolved BEFORE the day directory is created, because on a dead-RTC clock the
    # directory must not be named after the bogus reading. See _resume().
    checkpoint_path = out / CHECKPOINT_NAME
    checkpoint = read_checkpoint(checkpoint_path)
    first_value, trust, day_date = _resume(started, checkpoint, probe_ntp_synchronized())

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
        folder, session_id, rig=out.name, written_at=started, trust=trust
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
        "recording %s (session %s): first barcode %d, clock_trusted=%s clock_reason=%s boot_id=%s",
        writer_path,
        session_id,
        first_value,
        trust.trusted,
        trust.reason or "-",
        probe_boot_id() or "-",
    )

    values = _ValueSource(now_fn, first_value)
    generator = BarcodeGenerator(backend, BARCODE_PIN)
    health = _HealthReport(backend)

    # The last barcode of the segments this run has rolled away from. A roll leaves the
    # new writer with no barcode yet, so a run that rolls in its final iteration would
    # otherwise read "no barcodes were written" at the end and rewind the checkpoint to
    # `first_value`; a restart would then reuse values, which must be unique across the
    # box (spec Sec.4). See the end of the loop.
    carried_last: int | None = None

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
        between visits.

        A failure IN the roll propagates; the caller completes the future first.
        """
        nonlocal folder, session_id, carried_last
        current = read_open(out)
        if current is not None:
            new_folder, new_id = session_dir(out, current.session_id), current.session_id
            if folder == new_folder:
                return
        else:
            if folder.is_relative_to(out / "unassigned"):
                return
            new_folder, new_id = unassigned_dir(out, day), unassigned_id(day)

        old_folder, outgoing = folder, sink.sink
        _, make_writer = _segment_factory(
            new_folder, new_id, rig=out.name, written_at=now_fn(), trust=trust
        )
        sink.roll(make_writer, now_fn())
        # The outgoing writer's span is final now that its trailer is written.
        if outgoing.barcode_last is not None:
            carried_last = outgoing.barcode_last
        folder, session_id = new_folder, new_id
        log.info("recording into %s (session %s)", new_folder, new_id)

        try:
            write_manifest(old_folder)
        except Exception as error:  # noqa: BLE001 - recording matters more than the summary
            # Not rebuilt by a later start either: a folder that has been left is never
            # entered again, so its manifest stays as it is until someone rebuilds it.
            log.error("could not rebuild the manifest of %s: %s", old_folder, error)

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
            # is unwrapped on its own path -- a raw counter would diverge from the E and W
            # ticks it exists to align by 2^32 us at every 71.6-minute wrap.
            tick = backend.now_us()
            generator.emit_frame(value)
            frame_end = mono_fn()
            recorder.record_barcode(tick, value)
            # A barrier, not a marker: it returns once everything queued above it is on
            # disk, which keeps spec Sec.4's one-second crash-loss bound exact.
            sink.flush()
            health()

            # AFTER THE FRAME IS ON DISK AND BEFORE PACING: the frame just emitted belongs
            # to the session that was open when it was emitted, and the next one to the
            # session this makes. Drained with get_nowait() because the loop must never
            # wait on an endpoint it does not need.
            while requests is not None:
                try:
                    request = requests.get_nowait()
                except queue.Empty:
                    break
                # False for a future the caller cancelled (its HTTP request timed out):
                # carrying that out would open a session nobody is waiting for.
                if request.reply.set_running_or_notify_cancel():
                    carry_out(request, value)

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

    def __init__(self, now_fn, first_value: int) -> None:
        self._now_fn = now_fn
        self._first = first_value
        self._last: int | None = None
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
        if from_clock is not None and from_clock >= floor:
            self._clamped = False
            return self._issue(from_clock)
        if not self._clamped:
            # EDGE-TRIGGERED, deliberately. Spec Sec.7 names "time moved backwards" as a
            # trust condition and nothing recorded it mid-run -- the header is written
            # at startup and must not be rewritten afterwards, so a journal line is the
            # only honest place for it. Warning once per episode rather than once per
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
        return self._issue(floor)

    def _issue(self, value: int) -> int:
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
        self._last = value
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
    """

    def __init__(self, backend) -> None:
        self._backend = backend
        # The kinds of a CaptureDegraded, or the message of any other failure.
        self._reported: frozenset[str] | str | None = None

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
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    print(f"recorded {day}")
    return 0
