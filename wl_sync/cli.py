"""`wl-sync record` -- the run loop that makes this a sync box rather than a library.

The box is the first device on each day and the last off, and it runs continuously
while every other device starts and stops blocks inside that run. So a SESSION IS A DAY,
one segment file per process run, and a restart joins the day rather than beginning a
new one. systemd's Restart=always is what makes the stitching automatic.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.clock import (
    BARCODE_EPOCH,
    CHECKPOINT_INTERVAL_S,
    ClockTrust,
    evaluate_clock,
    next_value,
    read_checkpoint,
    write_checkpoint,
)
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
from wl_sync.service import BarcodeGenerator, Recorder

CHECKPOINT_NAME = ".wl-sync-barcode-checkpoint"

# `logging`, not `print`: stdout is block-buffered when it is a pipe, which is what
# systemd gives a service, so prints can sit unflushed in a daemon that runs all day.
# journald is this box's only status surface (spec Sec.7, Sec.10) -- the heartbeat LED
# is a buffered leg off BARCODE_RAW and cannot be driven by software.
log = logging.getLogger(__name__)


def day_directory(out: Path, today: datetime.date) -> Path:
    """`<out>/YYYY-MM-DD_01`. Index 01 because a day is one session; the `_NN` form is
    kept because SyncBoxLogHeader validates it and wl-preproc consumes it."""
    return out / f"{today.isoformat()}_01"


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
    writing to. With no checkpoint at all it lands on 2020-01-01_01: still not the true
    date, but honest, constant, and -- unlike a directory named after whatever the junk
    clock happened to read -- it does not mint a fresh orphan directory on every boot.
    """
    if started >= BARCODE_EPOCH:
        return (
            next_value(started, checkpoint),
            evaluate_clock(started, checkpoint, ntp_synchronized),
            started.date(),
        )

    first_value = 0 if checkpoint is None else checkpoint + 1
    day_date = (BARCODE_EPOCH + datetime.timedelta(seconds=first_value)).date()
    return first_value, ClockTrust(False, "clock_before_epoch"), day_date


def run(out: Path, backend, now_fn, duration_s: float | None, tick_fn) -> Path:
    """Record until `duration_s` elapses or a termination signal arrives.

    `now_fn` and `tick_fn` are injected so the whole loop is testable without sleeping:
    tests pass a scripted clock and a no-op sleep.
    """
    started = now_fn()

    # Resolved BEFORE the day directory is created, because on a dead-RTC clock the
    # directory must not be named after the bogus reading. See _resume().
    checkpoint_path = out / CHECKPOINT_NAME
    checkpoint = read_checkpoint(checkpoint_path)
    first_value, trust, day_date = _resume(started, checkpoint, probe_ntp_synchronized())

    day = day_directory(out, day_date)
    day.mkdir(parents=True, exist_ok=True)

    # Rebuild first, so any segment left trailer-less by a crash is recorded as such
    # before this run adds its own.
    write_manifest(day)

    index = next_segment_index(day)
    header = SyncBoxLogHeader(
        schema_version=1,
        session_id=day.name,
        rig=out.name,
        boot_id=probe_boot_id(),
        written_at=started,
        gpio_map=GPIO_MAP,
        segment_index=index,
        clock_trusted=trust.trusted,
        clock_reason=trust.reason,
    )
    writer_path = day / segment_name(index)
    writer = SegmentWriter(writer_path, header)

    recorder = Recorder(backend, sink=writer)
    recorder.capture_codes(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN)
    recorder.capture_edges(EDGE_PINS)

    # journald is the only status surface this box has -- spec Sec.7 calls it "the
    # honest status surface" and Sec.10 records that no software-controllable panel
    # indicator exists, because the heartbeat LED is a buffered leg off BARCODE_RAW
    # itself. One line, everything an operator needs to tell a healthy start from a
    # degraded one without opening a file.
    log.info(
        "recording %s: first barcode %d, clock_trusted=%s clock_reason=%s boot_id=%s",
        writer_path,
        first_value,
        trust.trusted,
        trust.reason or "-",
        header.boot_id or "-",
    )

    values = _ValueSource(now_fn, first_value)
    generator = BarcodeGenerator(backend, BARCODE_PIN, values)

    stopping = _StopFlag()
    for received in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(received, stopping)
        except ValueError:
            pass  # not the main thread, e.g. under a test runner

    # Cover the values this run is about to emit BEFORE emitting any of them. Without
    # this, a crash inside the first interval left the previous run's exact-value
    # checkpoint on disk, and the restart's `checkpoint + 1` re-issued every barcode
    # this run had already used. The clean close below overwrites it with the exact
    # last value, so an ordinary restart still inflates nothing.
    write_checkpoint(checkpoint_path, first_value + CHECKPOINT_INTERVAL_S)
    checkpointed_at = first_value

    elapsed = 0.0
    while not stopping.set and (duration_s is None or elapsed < duration_s):
        # Sampled BEFORE emitting: a frame takes 200 ms (barcode.FRAME_US) and the
        # record means the LEAD rising edge, which is what every receiving device
        # reports. Recorded through the Recorder rather than straight to the writer so
        # the tick is unwrapped on its own path -- a raw counter would diverge from the
        # E and W ticks it exists to align by 2^32 us at every 71.6-minute wrap.
        tick = backend.now_us()
        value = generator.emit_frame()
        recorder.record_barcode(tick, value)
        writer.flush()
        if value - checkpointed_at >= CHECKPOINT_INTERVAL_S:
            # A high-water mark, and TRIGGERED ON VALUE RATHER THAN ITERATION COUNT.
            # Counting iterations was wrong: one iteration is a 1 s sleep PLUS a 200 ms
            # frame plus an fsync, so 60 iterations span appreciably more than 60 s and
            # the emitted value outran the mark by roughly the accumulated overhead --
            # a crash in that window resumed at checkpoint + 1 and re-issued barcode
            # values already used, which is the one thing the clamp exists to prevent.
            # Triggering on value makes "the checkpoint is never behind the last value
            # emitted" true by construction, whatever the loop's real period is.
            #
            # The residual exposure is the microseconds between emitting a frame and
            # writing this: to matter, the clock would have to jump forwards by more
            # than the remaining margin, crash instantly, and then read BACKWARDS on
            # restart -- and a forward-jumped clock re-read after a restart dominates
            # the clamp anyway. Carrying the staleness margin here rather than in
            # next_value() is what keeps an ordinary restart from inflating the gap.
            write_checkpoint(checkpoint_path, value + CHECKPOINT_INTERVAL_S)
            checkpointed_at = value
        tick_fn(1.0)
        elapsed += 1.0

    writer.close(now_fn())
    # `or` would conflate a legitimate barcode value of 0 -- the epoch itself, which a
    # dead-RTC start really does emit -- with "no barcodes were written".
    last = first_value if writer.barcode_last is None else writer.barcode_last
    write_checkpoint(checkpoint_path, last)
    write_manifest(day)
    return day


class _ValueSource:
    """Barcode values from the wall clock, anchored at the value this run resumed on.

    The floor -- `first_value + emitted` -- is what keeps the counter unique when the
    clock stalls, jumps backwards, or cannot be read at all. An unreadable clock is not
    hypothetical: below the 2020 epoch `value_from_clock` raises, and a run that started
    on a dead RTC would otherwise die on its first frame having already been rescued at
    startup by _resume(). One second of run time is one unit of floor, so the floor
    alone is a correct (if imprecise) barcode source for as long as the run lasts.
    """

    def __init__(self, now_fn, first_value: int) -> None:
        self._now_fn = now_fn
        self._first = first_value
        self._emitted = 0
        self._clamped = False

    def __call__(self) -> int:
        floor = self._first + self._emitted
        try:
            from_clock = next_value(self._now_fn(), None)
        except ValueError:
            from_clock = None  # pre-epoch or out of range: the floor is all we have
        self._emitted += 1
        if from_clock is not None and from_clock >= floor:
            self._clamped = False
            return from_clock
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
        return floor


class _StopFlag:
    def __init__(self) -> None:
        self.set = False

    def __call__(self, _signum, _frame) -> None:
        self.set = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wl-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="record a day")
    record.add_argument("--out", type=Path, required=True, help="rig session root")
    record.add_argument("--fake", action="store_true", help="run on the in-memory backend")
    record.add_argument("--duration", type=float, default=None, help="stop after S seconds")
    args = parser.parse_args(argv)

    # To stderr, which systemd routes to the journal via StandardError=journal. Only
    # the entry point configures handlers; `run()` is a library call and must not.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(message)s",
    )

    if not args.fake:
        print("only --fake is available until the RP1 backend (Task 5b) lands")
        return 2

    day = run(
        out=args.out,
        backend=FakeBackend(),
        now_fn=lambda: datetime.datetime.now(datetime.timezone.utc),
        duration_s=args.duration,
        tick_fn=time.sleep,
    )
    print(f"recorded {day}")
    return 0
