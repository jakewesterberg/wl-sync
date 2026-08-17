"""`wl-sync record` -- the run loop that makes this a sync box rather than a library.

The box is the first device on each day and the last off, and it runs continuously
while every other device starts and stops blocks inside that run. So a SESSION IS A DAY,
one segment file per process run, and a restart joins the day rather than beginning a
new one. systemd's Restart=always is what makes the stitching automatic.
"""

from __future__ import annotations

import argparse
import datetime
import signal
import subprocess
import time
from pathlib import Path

from wl_sync.backend import FakeBackend
from wl_sync.clock import (
    CHECKPOINT_INTERVAL_S,
    evaluate_clock,
    next_value,
    read_checkpoint,
    write_checkpoint,
)
from wl_sync.log import BarcodeEmitted, SyncBoxLogHeader
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


def run(out: Path, backend, now_fn, duration_s: float | None, tick_fn) -> Path:
    """Record until `duration_s` elapses or a termination signal arrives.

    `now_fn` and `tick_fn` are injected so the whole loop is testable without sleeping:
    tests pass a scripted clock and a no-op sleep.
    """
    started = now_fn()
    day = day_directory(out, started.date())
    day.mkdir(parents=True, exist_ok=True)

    # Rebuild first, so any segment left trailer-less by a crash is recorded as such
    # before this run adds its own.
    write_manifest(day)

    checkpoint_path = out / CHECKPOINT_NAME
    checkpoint = read_checkpoint(checkpoint_path)
    trust = evaluate_clock(started, checkpoint, probe_ntp_synchronized())
    first_value = next_value(started, checkpoint)

    index = next_segment_index(day)
    header = SyncBoxLogHeader(
        schema_version=1,
        session_id=day.name,
        rig=out.name,
        boot_id=f"{started.timestamp():.0f}",
        written_at=started,
        gpio_map=GPIO_MAP,
        segment_index=index,
        clock_trusted=trust.trusted,
        clock_reason=trust.reason,
    )
    writer = SegmentWriter(day / segment_name(index), header)

    recorder = Recorder(backend, sink=writer)
    recorder.capture_codes(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN)
    recorder.capture_edges(EDGE_PINS)

    values = _ValueSource(now_fn, first_value)
    generator = BarcodeGenerator(backend, BARCODE_PIN, values)

    stopping = _StopFlag()
    for received in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(received, stopping)
        except ValueError:
            pass  # not the main thread, e.g. under a test runner

    elapsed = 0.0
    since_checkpoint = 0
    while not stopping.set and (duration_s is None or elapsed < duration_s):
        value = generator.emit_frame()
        writer.write(BarcodeEmitted(tick_us=backend.now_us(), value=value))
        writer.flush()
        since_checkpoint += 1
        if since_checkpoint >= CHECKPOINT_INTERVAL_S:
            write_checkpoint(checkpoint_path, value)
            since_checkpoint = 0
        tick_fn(1.0)
        elapsed += 1.0

    writer.close(now_fn())
    write_checkpoint(checkpoint_path, writer.barcode_last or first_value)
    write_manifest(day)
    return day


class _ValueSource:
    """Barcode values from the wall clock, anchored at the value this run resumed on."""

    def __init__(self, now_fn, first_value: int) -> None:
        self._now_fn = now_fn
        self._first = first_value
        self._emitted = 0

    def __call__(self) -> int:
        value = max(next_value(self._now_fn(), None), self._first + self._emitted)
        self._emitted += 1
        return value


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
