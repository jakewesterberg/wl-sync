"""Bench checks. Not part of the recording path.

    python -m wl_sync.bench loopback <segment or day directory> [--gpio 20]
    python -m wl_sync.bench stamps [--stamp-pin {17,18,19}] [--frames N] [--gpiochip PATH]

THE LOOPBACK. With the barcode output jumpered to an edge input, every frame the box
emits lands in its own segment twice: as the `B` record it logged, and as the edges it
saw on the input. Decoding those edges and matching them to the `B` records tests the
barcode output itself, which no fake can do because the pin is the thing under test.
It was written for the defect it would have caught: emit_pulses left the pin HIGH
after every frame, so only a run's first barcode could ever decode.

Finding frames is delegated to `barcode.decode_edges`, never reimplemented here, so a
change to the codec cannot leave this check and the receivers disagreeing.

THE STAMPS. `stamps` runs the box's barcode against its own stamp machine, live, with
no segment: every frame's lead edge should be stamped, and each of its later rising
edges consumed. It also gives the lead edge's CPU bracket width for every frame, which
bench check B5 needs. Capture runs beside it, as the run loop's does, so the box is
loaded as it is when recording; with nothing wired, a code word or an edge is itself a
finding.
"""

from __future__ import annotations

import argparse
import random
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median, quantiles

from wl_sync.backend import CaptureDegraded
from wl_sync.barcode import INTERVAL_US, decode_edges, encode, rises_us
from wl_sync.clockmap import LEAD_STAMP_TOLERANCE
from wl_sync.log import BarcodeEmitted, Edge, read_log
from wl_sync.pins import (
    BARCODE_PIN,
    CODE_DATA_BASE,
    CODE_DATA_COUNT,
    CODE_STROBE_PIN,
    EDGE_PINS,
    GPIO_MAP,
    SPARE_PINS,
)
from wl_sync.segment import SEGMENT_PATTERN, segment_index_of

# pd1_comp's line, free while no breakout board is fitted: GPIO17 -> GPIO20 is header
# pin 11 -> pin 38.
DEFAULT_LOOP_GPIO = GPIO_MAP["pd1_comp"]


# The timebases that put W, B and E on one PIO count (wl_sync/log.py:201).
ONE_COUNT_TIMEBASES = ("rp1-pio-si", "rp1-pio")


@dataclass
class LoopbackReport:
    gpio: int
    emitted: list[int]
    edge_count: int
    matched: int = 0
    unverifiable: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    unexpected: list[int] = field(default_factory=list)
    lead_offsets_us: list[int] = field(default_factory=list)
    # The segment header's `timebase`, free text (wl_sync/log.py:201); None on a segment
    # written before the field existed.
    timebase: str | None = None
    # Decoded frames with no `B`, on a one-count segment only; on any other they are
    # `unexpected`. Both fail.
    dropped: list[int] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.edge_count and self.matched and not self.missing
                    and not self.unexpected and not self.dropped)

    def _offset_meaning(self) -> str:
        """What the lead offset's median is, by timebase (final review M9). On one count
        B is the lead edge's own stamp, so the offset is the edge input's, E's constant
        (B6), and no barcode delay (wl_sync/log.py:170-176). On split B is a software
        tick read before the frame goes out, and the median is the barcode delay that
        deploy/README.md:27-37 has the operator pass as --barcode-delay-us
        (wl_sync/log.py:177-184). Any other timebase, or none, is not a split
        process's, so it is named and the flag's value pointed at a split segment."""
        if self.timebase in ONE_COUNT_TIMEBASES:
            return ", E's constant (B6)"
        if self.timebase == "split":
            return ", the barcode delay to pass as --barcode-delay-us"
        named = ("no timebase in the header" if self.timebase is None
                 else f"timebase {self.timebase}")
        return f" ({named}; only a split segment's is a --barcode-delay-us)"

    def summary(self) -> str:
        lines = [f"loopback on GPIO{self.gpio}: {'PASS' if self.passed else 'FAIL'}",
                 f"  barcodes logged {len(self.emitted)}, edges recorded {self.edge_count}"]
        if not self.edge_count:
            lines.append(
                f"  no edges on GPIO{self.gpio}: is it jumpered to GPIO{BARCODE_PIN}, the "
                "barcode output (header pin 11)?"
            )
            return "\n".join(lines)
        lines.append(f"  decoded and matched {self.matched}")
        if self.unverifiable:
            lines.append(f"  not checkable, nothing precedes it: {self.unverifiable}")
        if self.missing:
            lines.append(f"  logged but never decoded: {self.missing}")
        if self.unexpected:
            lines.append(f"  decoded but never logged: {self.unexpected}")
        if self.dropped:
            lines.append(f"  decoded, but the box dropped its B (the log has none): "
                         f"{self.dropped}")
        if self.lead_offsets_us:
            offsets = self.lead_offsets_us
            line = (f"  lead edge after its logged tick{self._offset_meaning()}: median "
                    f"{median(offsets):.0f} us, max {max(offsets)} us")
            if len(offsets) >= 2:
                # n=1000 gives 999 cut points: index 499 is p50, 989 p99, 998 p99.9.
                # Inclusive, as the stamps summary does below, so the tail is not
                # extrapolated past the widest offset seen.
                cuts = quantiles(offsets, n=1000, method="inclusive")
                line += (f"; p50 {cuts[499]:.0f} us, p99 {cuts[989]:.0f} us, "
                         f"p99.9 {cuts[998]:.0f} us")
            lines.append(line)
        return "\n".join(lines)


def loopback(path: Path, gpio: int = DEFAULT_LOOP_GPIO) -> LoopbackReport:
    """Match the barcodes this segment logged against the ones decoded from `gpio`.

    THE FIRST FRAME IS NOT CHECKABLE, and is reported as such rather than as a failure.
    A frame is found by a rising edge after IDLE_MIN_US of LOW, and nothing in the
    record precedes the first one, so the decoder cannot verify the idle before it.
    """
    header, records = read_log(path)
    barcodes = [r for r in records if isinstance(r, BarcodeEmitted)]
    edges = [(r.tick_us, r.level) for r in records if isinstance(r, Edge) and r.gpio == gpio]
    report = LoopbackReport(gpio=gpio, emitted=[b.value for b in barcodes],
                            edge_count=len(edges), timebase=header.timebase)
    if not edges:
        return report

    logged = {b.value: b.tick_us for b in barcodes}
    decoded = decode_edges(edges)
    seen = set()
    one_count = header.timebase in ONE_COUNT_TIMEBASES
    for frame in decoded:
        if frame.value in logged:
            report.matched += 1
            report.lead_offsets_us.append(frame.start_us - logged[frame.value])
            seen.add(frame.value)
        else:
            # On a one-count segment the box logs a `B` for every frame it emits unless
            # it dropped it (docs/superpowers/specs/2026-10-30-one-clock-design.md:417);
            # a "split" process whose loopback failed logs a software `B` for every
            # frame, so a frame with none there is not a drop.
            (report.dropped if one_count else report.unexpected).append(frame.value)
    for index, barcode in enumerate(barcodes):
        if barcode.value in seen:
            continue
        (report.unverifiable if index == 0 else report.missing).append(barcode.value)
    return report


# One frame a second, as the run loop emits them (barcode.INTERVAL_US).
STAMP_RUN_PERIOD_S = INTERVAL_US / 1_000_000

_NS_PER_US = 1_000

# The kind of miss whose cause text carries its distance (wl_sync/rp1/backend.py:1350-1358),
# under which `missed` counts them all: keyed by the text, a drifting map gave one
# summary line per distance. The distances are kept in `far_us`.
STAMP_TOO_FAR = "the stamp lies too far from the frame's lead edge"


@dataclass
class StampRunReport:
    """What the stamp machine made of a run of the box's own frames.

    `missed` counts the frames with no lead stamp by kind: each fixed cause by its text,
    and every stamp too far from its lead edge under STAMP_TOO_FAR, with its distance in
    `far_us`. `edges_short` counts the stamped frames whose later rising edges were not
    all consumed; a frame with no lead stamp is counted in `missed` alone. `stamps_seen`
    is every stamp the frames accounted for: leads, plus consumed, plus between. `words`
    and `edges` are the records capture delivered meanwhile.
    """

    frames: int = 0
    stamped: int = 0
    missed: dict[str, int] = field(default_factory=dict)
    far_us: list[int] = field(default_factory=list)
    edges_short: int = 0
    between: int = 0
    stamps_seen: int = 0
    widths_ns: list[int] = field(default_factory=list)
    words: int = 0
    edges: int = 0

    @property
    def passed(self) -> bool:
        """Every lead edge stamped, every later edge consumed, and nothing between."""
        return bool(self.frames and self.stamped == self.frames
                    and self.edges_short == 0 and self.between == 0)

    def summary(self) -> str:
        lines = [
            f"barcode stamps: {'PASS' if self.passed else 'FAIL'}",
            f"  frames {self.frames}, stamped {self.stamped}",
        ]
        for kind, count in sorted(self.missed.items()):
            if kind == STAMP_TOO_FAR and self.far_us:
                low, high = min(self.far_us), max(self.far_us)
                span = f"{low}" if low == high else f"{low}-{high}"
                kind = (f"the stamp lies {span} us from the frame's lead edge as the CPU "
                        f"bracketed it ({LEAD_STAMP_TOLERANCE} us allowed)")
            lines.append(f"  missed {count}: {kind}")
        lines.append(
            f"  edges_short {self.edges_short}, between {self.between}, "
            f"stamps_seen {self.stamps_seen}"
        )
        lines.append(f"  words {self.words}, edges {self.edges}")
        if len(self.widths_ns) >= 2:
            # n=1000 gives the 999 cut points: index 989 is p99, 998 is p99.9. Inclusive:
            # the default, "exclusive", extrapolates past the widest bracket seen, which
            # in a run of minutes is most of the tail.
            cuts = quantiles(self.widths_ns, n=1000, method="inclusive")
            lines.append(
                f"  bracket width over {len(self.widths_ns)} frames: "
                f"median {median(self.widths_ns) / _NS_PER_US:.1f} us, "
                f"p99 {cuts[989] / _NS_PER_US:.1f} us, "
                f"p99.9 {cuts[998] / _NS_PER_US:.1f} us"
            )
        return "\n".join(lines)


def stamp_run(
    backend,
    values: Sequence[int],
    pace: Callable[[], None],
    report: StampRunReport | None = None,
) -> StampRunReport:
    """Emit each value's frame on the barcode pin, and count what the stamps made of it.

    `pace()` runs between frames, not before the first or after the last. After each
    frame, `last_frame` gives the match and the bracket width, and `take_pairs()` is
    drained: the backend keeps every pair until it is taken, and a run of 28,800 frames
    would otherwise hold them all. Counts go into `report` when one is given, so a run
    cut short still has them.
    """
    report = StampRunReport() if report is None else report
    for index, value in enumerate(values):
        if index:
            pace()
        pulses = encode(value)
        stamp = backend.emit_pulses(BARCODE_PIN, pulses)
        trace = backend.last_frame
        backend.take_pairs()
        report.frames += 1
        report.between += stamp.between
        if stamp.tick is None:
            # Only a stamp too far leaves its distance in the match: `nearest` is set
            # only for a lead the frame's rises align on outside its window, or for one
            # refused beyond reach (wl_sync/clockmap.py:302-304).
            far = None if trace is None else trace.lead.nearest
            kind = stamp.missed if far is None else STAMP_TOO_FAR
            report.missed[kind] = report.missed.get(kind, 0) + 1
            if far is not None:
                report.far_us.append(far)
        else:
            report.stamped += 1
        if trace is None:
            continue
        lead = trace.lead
        report.widths_ns.append(trace.width_ns)
        # The lead the rises aligned on is the match's `lead` whether or not it lay in
        # the window, and is in neither `between` nor `consumed` (Ruling R26).
        has_lead = lead.p is not None or lead.lead is not None
        report.stamps_seen += has_lead + lead.consumed + lead.between
        if stamp.tick is not None and lead.consumed != len(rises_us(pulses)) - 1:
            report.edges_short += 1
    return report


def _pacer(period_s: float) -> Callable[[], None]:
    """Sleeps until `period_s` after the previous frame began, so frames keep a fixed
    period however long each took to emit and match."""
    due = time.monotonic()

    def pace() -> None:
        nonlocal due
        due += period_s
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    return pace


class _Terminated(SystemExit):
    """What SIGTERM raises in `stamps`, which `pkill` sends to end S.5 early. The
    default ends the process where it stands, with no close() and no summary of the
    hours already run; raised, it unwinds through both."""


def _terminate(signum: int, frame) -> None:
    raise _Terminated(128 + signum)  # the status a SIGTERM'd process exits with


def _stamps(stamp_pin: int, frames: int, chip_path: str) -> int:
    """`python -m wl_sync.bench stamps`: on the box, with the shim. Writes no file."""
    # Imported here, as `wl-sync record` imports it (wl_sync/cli.py:1599-1604): building
    # it needs the shim and a GPIO chip, which `loopback` never touches.
    from wl_sync.rp1.backend import Rp1Backend

    words = edges = 0

    def on_word(tick: int, word: int) -> None:  # the word pump's thread alone
        nonlocal words
        words += 1

    def on_edge(gpio: int, level: int, tick: int) -> None:  # the edge pump's alone
        nonlocal edges
        edges += 1

    rng = random.Random(frames)
    values = [rng.getrandbits(32) for _ in range(frames)]
    report = StampRunReport()
    stopped: _Terminated | None = None
    backend = Rp1Backend(chip_path=chip_path, stamp_pin=stamp_pin)
    if backend.stamp_test_failure:
        # The process is split (wl_sync/rp1/backend.py:1217-1220): each frame's B is the
        # clock read before it, and nothing is stamped or matched. Were it run, every frame
        # would count as stamped (`stamp_run`), and it would pass on stamps never seen.
        print(f"start-up test failed ({backend.stamp_test_failure}): this process records "
              "split, and has no stamps to count")
        backend.close()
        return 1
    installed, outer = False, None
    try:
        # Inside the try, so the backend is closed even if installing fails.
        outer, installed = signal.signal(signal.SIGTERM, _terminate), True
        backend.start_strobed_capture(CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN,
                                      on_word)
        backend.start_edge_capture(EDGE_PINS, on_edge)
        stamp_run(backend, values, _pacer(STAMP_RUN_PERIOD_S), report)
    except _Terminated as terminated:
        stopped = terminated
    finally:
        if installed:
            # A second SIGTERM must not cut close() short and lose the summary.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            backend.close()
        finally:
            if installed:  # put back whatever close() does
                signal.signal(signal.SIGTERM, signal.SIG_DFL if outer is None else outer)
    # Read after close(), which has stopped both pumps, so neither count still moves.
    report.words, report.edges = words, edges
    print(report.summary())
    if stopped is not None:
        print(f"  stopped by SIGTERM after {report.frames} of {frames} frames")
    try:
        # What capture held at the end, which run() also asks for after close()
        # (wl_sync/cli.py:1088-1090): a full stamp FIFO would explain short frames.
        backend.check_health()
    except CaptureDegraded as degraded:
        print(f"  capture degraded: {degraded}")
    if stopped is not None:
        return stopped.code
    return 0 if report.passed else 1


def _segment_of(path: Path) -> Path:
    """A segment file as given, or a day directory's last segment."""
    if path.is_dir():
        segments = sorted(
            (p for p in path.glob(SEGMENT_PATTERN) if segment_index_of(p) is not None),
            key=segment_index_of,
        )
        if not segments:
            raise SystemExit(f"no segments in {path}")
        return segments[-1]
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wl_sync.bench")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("loopback", help="the box's own barcode, read back")
    check.add_argument("path", type=Path, help="a segment, or a day directory")
    check.add_argument("--gpio", type=int, default=DEFAULT_LOOP_GPIO,
                       help=f"the edge input jumpered to GPIO{BARCODE_PIN}")
    stamps = commands.add_parser(
        "stamps", help="the box's own barcode, stamped by its stamp machine, live"
    )
    stamps.add_argument(
        "--stamp-pin", type=int, choices=(BARCODE_PIN, *SPARE_PINS), default=BARCODE_PIN,
        help=f"GPIO{BARCODE_PIN} itself, or a spare pin, unwired, as a control "
             "(an unwired pin must stamp nothing)",
    )
    stamps.add_argument("--frames", type=int, default=60, help="one a second")
    stamps.add_argument("--gpiochip", default="/dev/gpiochip0",
                        help="RP1's GPIO bank, as for wl-sync record")
    args = parser.parse_args(argv)

    if args.command == "stamps":
        return _stamps(args.stamp_pin, args.frames, args.gpiochip)
    report = loopback(_segment_of(args.path), args.gpio)
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
