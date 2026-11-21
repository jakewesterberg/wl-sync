"""Bench checks. Not part of the recording path.

    python -m wl_sync.bench loopback <segment or day directory> [--gpio 20]
    python -m wl_sync.bench stamps [--stamp-pin {17,18,19}] [--frames N] [--gpiochip PATH]
    python -m wl_sync.bench skew [--machines {2,3}] [--edges N] [--record CSV]
    python -m wl_sync.bench streams [--dense-pin {18,19}] [--duration S] [--rate R]
                                    [--marker] [--probe-only] [--record CSV]
    python -m wl_sync.bench override [--pin {18,19} [--clear]]
    python -m wl_sync.bench parity <segment or day directory> [<segment> ...]

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

THE STAMP MACHINES AGAINST EACH OTHER. `skew` is bench check B3a and `streams` B3b, with
no wire (Task 1.21). They run the shim through wl_sync.rp1.harness, never Rp1Backend, from
one thread, and `streams` writes one spare pin's RP1 registers to make highs shorter than
libgpiod can (`harness.PulseWriter`).
"""

from __future__ import annotations

import argparse
import math
import random
import signal
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from statistics import median, quantiles

from wl_sync.backend import CaptureDegraded
from wl_sync.barcode import FRAME_US, INTERVAL_US, decode_edges, encode, rises_us
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
        deploy/README.md:28-38 has the operator pass as --barcode-delay-us
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

    A FRAME DECODED WITH NO `B` IS `dropped` ON ONE COUNT, AND `unexpected` ON ANY OTHER
    TIMEBASE. On one count the box drops the `B` of a frame whose lead edge went
    unstamped, so the frame is on the wire and not in the log; a split process logs a
    `B` for every frame, so a frame without one there was never logged. Both fail.
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
            # On a one-count segment the run loop logs a `B` for every frame it emits
            # unless the frame's lead edge went unstamped (wl_sync/cli.py:880-884); a
            # "split" process's `B` is a software tick, read for every frame
            # (wl_sync/rp1/backend.py:1582-1585), so a frame with none there is not a drop.
            (report.dropped if one_count else report.unexpected).append(frame.value)
    for index, barcode in enumerate(barcodes):
        if barcode.value in seen:
            continue
        (report.unverifiable if index == 0 else report.missing).append(barcode.value)
    return report


# One frame a second, as the run loop emits them (barcode.INTERVAL_US).
STAMP_RUN_PERIOD_S = INTERVAL_US / 1_000_000

_NS_PER_US = 1_000

# The kind of miss whose cause text carries its distance (wl_sync/rp1/backend.py:1716-1724),
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


# What ends a harness run closed and summarised (`_harnessed`): pkill's SIGTERM, and the
# SIGHUP of an ssh session that drops.
_ENDING_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def _terminate(signum: int, frame) -> None:
    raise _Terminated(128 + signum)  # the status a SIGTERM'd process exits with


def _stamps(stamp_pin: int, frames: int, chip_path: str) -> int:
    """`python -m wl_sync.bench stamps`: on the box, with the shim. Writes no file."""
    # Imported here, as `wl-sync record` imports it (wl_sync/cli.py:1763-1769): building
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
    if backend.capture_failure:
        # No PIO machine runs at all (wl_sync/rp1/backend.py, `_configure`): the cause is
        # the shim, the block or the capture machine, not the start-up test.
        print(f"event-code capture cannot run ({backend.capture_failure}): this process "
              "records split, and has no stamps to count")
        backend.close()
        return 1
    if backend.stamp_test_failure:
        # The process is split (wl_sync/rp1/backend.py:1582-1585): each frame's B is the
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
        # (wl_sync/cli.py:1077-1079): a full stamp FIFO would explain short frames.
        backend.check_health()
    except CaptureDegraded as degraded:
        print(f"  capture degraded: {degraded}")
    if stopped is not None:
        return stopped.code
    return 0 if report.passed else 1


# ---------------------------------------------------------------------------
# B3a and B3b, with no wire (Task 1.21; the plan's bench section,
# docs/superpowers/plans/2026-11-07-one-clock.md:1990-2010). The hardware is
# wl_sync.rp1.harness, imported inside each command as `_stamps` imports the backend.
# "The design" below is .superpowers/sdd/2026-11-07-one-clock/b3-harness-design.md, which
# the user ruled on, 2026-11-20.
# ---------------------------------------------------------------------------

_WRAP = 1 << 32

# What --record writes, one line per driven rise, every value an integer RAW ns or a raw
# 32-bit count, so a verdict can be re-derived without rerunning the hour, with the comment
# lines before it: the RAW reading at the sync enable and, for `streams`, clk_sys and the
# width meter's machine and scale, which turn `meter_counts` into widths.
RECORD_COLUMNS = ("phase,index,pins,shape,rise_before_ns,rise_after_ns,fall_before_ns,"
                  "fall_after_ns,set_read_ns,clear_read_ns,meter_counts,rise_landed,"
                  "drained_at_ns,stamps")


def _signed(difference: int) -> int:
    """A difference of two 32-bit counts, read as the signed value nearest zero."""
    difference &= _WRAP - 1
    return difference - _WRAP if difference >= _WRAP // 2 else difference


def _us(ns: float) -> str:
    return f"{ns / _NS_PER_US:.1f}"


class _Recorder:
    """--record: the run's every rise as CSV lines (RECORD_COLUMNS), after comment lines
    that give the command and the RAW reading at the sync enable, which P counts from."""

    def __init__(self, path: Path, command: str) -> None:
        self._file = open(path, "w")  # noqa: SIM115 - closed with the rig
        self.phase = ""
        self.comment(f"wl-sync bench {command}")
        self._file.write(RECORD_COLUMNS + "\n")

    def comment(self, text: str) -> None:
        self._file.write(f"# {text}\n")

    def write(self, event) -> None:
        def field_of(value) -> str:
            return "" if value is None else str(int(value))

        stamps = " ".join(f"{sm}={p}" for sm in sorted(event.stamps) for p in event.stamps[sm])
        self._file.write(",".join([
            self.phase, str(event.index), "+".join(str(p) for p in event.pins), event.shape,
            field_of(event.rise_before), field_of(event.rise_after),
            field_of(event.fall_before), field_of(event.fall_after),
            *(field_of(read) for read in (event.reads or (None, None))),
            " ".join(str(count) for count in event.widths),
            str(int(event.rise_landed)), field_of(event.drained_at), stamps,
        ]) + "\n")

    def close(self) -> None:
        self._file.close()


def _drain_line(tally) -> str:
    drains = tally.get("drain_ns") or []
    timing = (f"; drain median {_us(median(drains))} us, max {_us(max(drains))} us"
              if drains else "")
    return (f"  words {tally['words']}, FIFO full {tally['full']}, slips {tally['slips']}, "
            f"level errors {tally['level_errors']}, carried {tally['carried']}{timing}")


# --- B3a -----------------------------------------------------------------------------

# Each edge's high: it does not matter here (the design, section 2.6), so long
# enough that the held loop, not the short exit, ends it (HELD_MIN_NS below).
SKEW_HOLD_NS = 20_000
SKEW_PERIOD_US = 2_000
# The pass line (plan :2058): the tick difference is identical on every event, and <= 1.
SKEW_MAX_TICKS = 1


@dataclass
class SkewReport:
    """What every stamp machine pushed for each rise on GPIO17, all of them reading it
    unclaimed and started by one sync enable. `differences` counts, for each pair of
    machines (i, j), each value of P_j - P_i over the edges both stamped once. `missed`
    and `extra` count by machine the edges with no stamp and the stamps past one.
    `running` is why the machines were not seen running, if they were not."""

    machines: list[int]
    capture_sm: int = 0
    edges: int = 0
    stamped: dict[int, int] = field(default_factory=dict)
    missed: dict[int, int] = field(default_factory=dict)
    extra: dict[int, int] = field(default_factory=dict)
    differences: dict[tuple[int, int], Counter] = field(default_factory=dict)
    running: str = ""
    stopped: bool = False
    seconds: float = 0.0
    words: int = 0
    full: int = 0
    slips: int = 0
    level_errors: int = 0
    carried: int = 0
    drain_ns: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        for sm in self.machines:
            self.stamped.setdefault(sm, 0)
        for index, i in enumerate(self.machines):
            for j in self.machines[index + 1:]:
                self.differences.setdefault((i, j), Counter())

    def add(self, event) -> None:
        self.edges += 1
        once: dict[int, int] = {}
        for sm in self.machines:
            found = event.stamps.get(sm, [])
            if not found:
                self.missed[sm] = self.missed.get(sm, 0) + 1
            elif len(found) > 1:
                self.extra[sm] = self.extra.get(sm, 0) + len(found) - 1
            else:
                self.stamped[sm] += 1
                once[sm] = found[0]
        for (i, j), counter in self.differences.items():
            if i in once and j in once:
                counter[_signed(once[j] - once[i])] += 1

    @property
    def verdict(self) -> str:
        if self.stopped:
            return "INCONCLUSIVE"
        constant = all(
            len(counter) == 1 and abs(next(iter(counter))) <= SKEW_MAX_TICKS
            and counter.total() == self.edges
            for counter in self.differences.values()
        )
        clean = not (self.missed or self.extra or self.words or self.full or self.slips
                     or self.level_errors or self.carried or self.running)
        whole = all(count == self.edges for count in self.stamped.values())
        return "PASS" if self.edges and constant and clean and whole else "FAIL"

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def summary(self) -> str:
        machines = ", ".join(f"SM{sm}" for sm in self.machines)
        lines = [
            f"start skew (B3a): {self.verdict}",
            f"  machines: capture SM{self.capture_sm} (GPIO0-16, nothing wired); stamp "
            f"{machines} on GPIO{BARCODE_PIN}, unclaimed; one sync enable",
        ]
        if self.running:
            lines.append(f"  machines not seen running: {self.running}")
        stamped = ", ".join(f"SM{sm} {n}" for sm, n in self.stamped.items())

        def by_machine(name: str, counts: dict[int, int]) -> str:
            if not counts:
                return f"{name} 0"
            return f"{name} " + ", ".join(f"SM{sm} {n}" for sm, n in sorted(counts.items()))

        lines.append(f"  edges {self.edges}; stamped {stamped}; "
                     f"{by_machine('missed', self.missed)}, {by_machine('extra', self.extra)}")
        pairs = []
        for (i, j), counter in self.differences.items():
            if len(counter) == 1:
                [(d, n)] = counter.items()
                pairs.append(f"SM{j}-SM{i}: {d} on {n} of {self.edges}")
            elif counter:
                values = ", ".join(f"{d} x{n}" for d, n in sorted(counter.items()))
                pairs.append(f"SM{j}-SM{i}: {values}")
            else:
                pairs.append(f"SM{j}-SM{i}: no edge stamped once by both")
        lines.append("  " + "; ".join(pairs))
        lines.append(_drain_line(vars(self)))
        lines.append(f"  {self.edges} edges in {self.seconds:.1f} s")
        return "\n".join(lines)


def skew_run(rig, edges: int, period_ns: int, report: SkewReport) -> SkewReport:
    """B3a on an open rig whose machines are enabled in sync: the running check on GPIO17
    (`Rig.running_check`), which ends the run on a miss, then `edges` rises on GPIO17
    every `period_ns`, each SKEW_HOLD_NS high and drained after its settle."""
    from wl_sync.rp1.harness import raw_ns, wait_until

    report.running = rig.running_check(BARCODE_PIN)
    if report.running:
        return report
    first = raw_ns()
    for k in range(edges):
        wait_until(first + k * period_ns)
        report.add(rig.edge(BARCODE_PIN, SKEW_HOLD_NS, "skew"))
    return report


# --- B3b -----------------------------------------------------------------------------

# WHICH PATH A HIGH TAKES THROUGH THE CAPTURE PROGRAM, from its width. tests/piosim.py runs
# the program's words (wl_sync/rp1/program.py:88-102): a high of 5.0 us or less is gone
# before the held test at address 9, at either sampling phase, and leaves by the exit at
# 10; one of 6.0 us or more takes the held loop at 8-9 at both; 5.5 us depends on the
# phase (tests/test_bench.py runs it). So a stamped high whose RAW upper bound is at most
# SHORT_EXIT_MAX_NS took the short exit, and one whose lower bound is at least HELD_MIN_NS
# the held loop. B3b must cover both (the user's ruling, 2026-11-20).
SHORT_EXIT_MAX_NS = 5_000
HELD_MIN_NS = 6_000

# The pass line (plan :2068) and what may judge it. The hour and the 10^5 events are the
# plan's (:2067). THE USER'S RULINGS, 2026-11-20 (Task 1.21's review): "1σ ≤ 0.0033 ppm"
# on A - B, a third of the line, as the design proposed (section 2.7), and "≥ 25% each":
# each path at least a quarter of A's fitted events, so that a cycle lost on one
# path in a few hundred events would show (0.5 us x 25 events a second / 400 = 0.03 ppm).
# Short of either, the run is INCONCLUSIVE. That a FAIL needs |A - B| over the line AND
# over 3 sigma is the harness's choice, which the review found needs no ruling: with
# sigma within its ruling, over the line is over 3 sigma.
B3B_LINE_PPM = 0.01
B3B_SIGMA_MAX_PPM = 0.0033
B3B_MIN_DURATION_S = 3_600
B3B_MIN_A_EVENTS = 100_000
B3B_PATH_SHARE_MIN = 0.25

# THE PROBE (the design, section 2.7, phase 1).
PROBE_RISES = 100
PROBE_SPACING_NS = 10_000_000
PROBE_HOLD_NS = 20_000
PROBE_SHAPE_PULSES = 200
PROBE_SHAPE_SPACING_NS = 5_000_000
# libgpiod's highs, held from when the rise is read back: back to back (no hold), then
# 10 us, 50 us and 1 ms. Back to back has no lower bound worth the name, so the run drives
# the other three.
HELD_SHAPES_NS = (("back-to-back", 0), ("10 us", 10_000), ("50 us", 50_000),
                  ("1 ms", 1_000_000))
RUN_HELD_SHAPES = ("10 us", "50 us", "1 ms")
# The register writer's holds, each a spin from the RAW read after its SET's read-back
# (`PulseWriter.pulse`, fix round 4). That read-back makes the hold a proven lower bound
# on the high, so 1 us, the high the program needs to see (wl_sync/rp1/backend.py:1023-1024),
# is enough at the least; and the upper bound is the hold plus two read-backs, so the
# holds stop at 2 us, to leave the read-backs room under SHORT_EXIT_MAX_NS. Until fix
# round 4 they were 1.5-2.5 us, timed from a store with no read-back (review I2). The
# run drives those the probe shows stamped every time and under SHORT_EXIT_MAX_NS for at least
# SHORT_SHARE_SHOWN of their highs: a harness choice, not a ruling, since it picks the
# holds and not the verdict.
SHORT_HOLDS_NS = (1_000, 1_500, 2_000)
SHORT_SHARE_SHOWN = 0.9

# THE RUN: one barcode frame a second on GPIO17 for B (wl_sync/barcode.py:3-11), and in
# the 800 ms of idle after it `--rate` dense events on the dense pin for A, IDLE_MARGIN_NS
# clear of either frame.
IDLE_MARGIN_NS = 20_000_000
MARKER_HOLD_NS = 20_000


class LineFit:
    """Ordinary least squares of y (P, unwrapped, in us) on x (RAW, in half-ns: a
    bracket's midpoint doubled, so it stays an integer), ON EXACT INTEGER SUMS. Float
    sums of squares of RAW ns, about 10^15 each, would lose the digits a 0.01 ppm line
    is read from. Each point is taken relative to the first."""

    def __init__(self) -> None:
        self.n = 0
        self._x0 = self._y0 = 0
        self._sx = self._sy = self._sxx = self._sxy = self._syy = 0

    def add(self, x2: int, y: int) -> None:
        if not self.n:
            self._x0, self._y0 = x2, y
        dx, dy = x2 - self._x0, y - self._y0
        self.n += 1
        self._sx += dx
        self._sy += dy
        self._sxx += dx * dx
        self._sxy += dx * dy
        self._syy += dy * dy

    def _sums(self) -> tuple[int, int, int]:
        n = self.n
        return (n * self._sxx - self._sx ** 2, n * self._sxy - self._sx * self._sy,
                n * self._syy - self._sy ** 2)

    def slope_us_per_us(self) -> Fraction:
        """P us per RAW us: (1 + RP1's ppm) / (1 + the CPU's)."""
        sxx, sxy, _ = self._sums()
        return Fraction(sxy, sxx) * 2 * _NS_PER_US

    def stderr_us_per_us(self) -> float:
        """The slope's standard error, from the residuals."""
        sxx, sxy, syy = self._sums()
        residual = Fraction(syy * sxx - sxy * sxy, sxx * self.n)
        return math.sqrt(float(residual / (self.n - 2) * self.n / sxx)) * 2 * _NS_PER_US


def slope_difference_ppm(a: LineFit, b: LineFit) -> tuple[float, float]:
    """(slope_a - slope_b) / slope_b in ppm, and its 1-sigma error."""
    slope_a, slope_b = a.slope_us_per_us(), b.slope_us_per_us()
    delta = float((slope_a - slope_b) / slope_b) * 1e6
    sigma = math.hypot(a.stderr_us_per_us(), b.stderr_us_per_us()) / float(slope_b) * 1e6
    return delta, sigma


def path_of(event) -> str | None:
    """Which path through the program a stamped high provably took, by THE SAME RULE
    WHOEVER DROVE IT (fix round 4): "short" when both its ends were read back
    (`Event.bounded`) and its upper bound is at most SHORT_EXIT_MAX_NS, "held" when its
    lower bound is at least HELD_MIN_NS, and None when neither bound decides. A libgpiod
    high on the dense pin is read back at both ends now, so one that is short enough
    counts as the writer's would; on the box its two ioctls made back-to-back highs of
    12.7 us median (box step 2), so it seldom will, and the run drives only libgpiod's
    held shapes."""
    upper, lower = event.upper_ns, event.lower_ns
    if event.bounded and upper is not None and upper <= SHORT_EXIT_MAX_NS:
        return "short"
    if lower is not None and lower >= HELD_MIN_NS:
        return "held"
    return None


def path_of_width(width_ns: float, uncertainty_ns: float) -> str | None:
    """Which path a stamped high took, FROM THE PIO'S OWN WIDTH METER (the user's ruling,
    2026-11-20, "A PIO width meter"; fix round 6): "short" when the widest the high can be,
    its measured width plus the meter's uncertainty, is at most SHORT_EXIT_MAX_NS; "held"
    when the narrowest it can be is at least HELD_MIN_NS; and None, neither, in the band
    between, where the program's path depends on the sampling phase (piosim: 5.5 us is
    held at one phase and short at the other). The bounds are the program's own,
    SHORT_EXIT_MAX_NS and HELD_MIN_NS. The CPU's bounds (`path_of`) are reported beside
    it, as information only: one RP1 read costs 5.1 us (box step 2), so they cannot prove a
    short high. The uncertainty is the meter's against the width the PIO saw, after its
    input synchroniser, not against the pad's (`meter.uncertainty_ns`): that is the width
    the capture machine's path depends on, read through the same synchroniser [inferred]."""
    if width_ns + uncertainty_ns <= SHORT_EXIT_MAX_NS:
        return "short"
    if width_ns - uncertainty_ns >= HELD_MIN_NS:
        return "held"
    return None


@dataclass
class ShapeResult:
    """One probe shape on the dense pin: how many pulses, how many A stamped once, how
    many B stamped at all, and each high's RAW bounds."""

    name: str
    writer: bool
    hold_ns: int
    pulses: int = 0
    stamped: int = 0
    other: int = 0
    uppers: list[int] = field(default_factory=list)
    lowers: list[int] = field(default_factory=list)
    # Highs stamped once whose bounds prove the short exit (`path_of`), and, for the
    # writer, what each SET's and each CLEAR's read-back cost in RAW (fix round 4).
    proven_short: int = 0
    set_reads: list[int] = field(default_factory=list)
    clear_reads: list[int] = field(default_factory=list)
    # The width meter's record of each high (fix round 6): its widths, how many it put on
    # each path (`path_of_width`), and the highs it measured not once.
    widths_ns: list[float] = field(default_factory=list)
    meter_paths: Counter = field(default_factory=Counter)
    meter_missed: int = 0
    meter_extra: int = 0

    @property
    def short_shown(self) -> bool:
        """A writer hold the run may drive: every high stamped by A alone, measured once by
        the meter, and at least SHORT_SHARE_SHOWN of them measured short."""
        return bool(self.writer and self.pulses and self.stamped == self.pulses
                    and not self.other and not self.meter_missed and not self.meter_extra
                    and self.meter_paths["short"] >= SHORT_SHARE_SHOWN * self.pulses)


@dataclass
class ProbeReport:
    """B3b's probe (the design, section 2.7, phase 1): the machines seen running
    on both pins, the dense pin's rises seen by A alone, GPIO17's by B alone, and each
    shape of high stamped."""

    dense_pin: int
    running: str = ""
    complete: bool = False
    dense_rises: int = 0
    dense_by_a: int = 0
    dense_by_b: int = 0
    barcode_rises: int = 0
    barcode_by_b: int = 0
    barcode_by_a: int = 0
    shapes: list[ShapeResult] = field(default_factory=list)
    # The width meter on P1's rises (one width each) and P2's (none): its own health.
    meter_missed: int = 0
    meter_extra: int = 0

    def short_holds(self) -> list[int]:
        return [shape.hold_ns for shape in self.shapes if shape.short_shown]

    def failure(self) -> str:
        """Why B3b cannot run, or "": nothing is judged of a probe cut short."""
        pin = self.dense_pin
        if self.running:
            if f"GPIO{pin}" in self.running:
                follow = ("try --dense-pin 19, then the 12->36 jumper" if pin == 18
                          else "B3b waits for the 12->36 jumper")
                return f"{self.running} ({follow}; the user's decision 4)"
            return self.running
        if not self.complete:
            return ""
        if self.dense_by_a != self.dense_rises:
            return (f"A stamped {self.dense_by_a} of GPIO{pin}'s {self.dense_rises} rises: "
                    f"the PIO does not see GPIO{pin} reliably, unclaimed")
        if self.dense_by_b or self.barcode_by_a:
            return (f"crosstalk: B stamped {self.dense_by_b} of GPIO{pin}'s rises and A "
                    f"{self.barcode_by_a} of GPIO{BARCODE_PIN}'s")
        if self.barcode_by_b != self.barcode_rises:
            return (f"B stamped {self.barcode_by_b} of GPIO{BARCODE_PIN}'s "
                    f"{self.barcode_rises} rises")
        for shape in self.shapes:
            if not shape.writer and (shape.stamped != shape.pulses or shape.other):
                return (f"A stamped {shape.stamped} of {shape.pulses} {shape.name} highs on "
                        f"GPIO{pin} (B {shape.other})")
        missed = self.meter_missed + sum(shape.meter_missed for shape in self.shapes)
        extra = self.meter_extra + sum(shape.meter_extra for shape in self.shapes)
        if missed or extra:
            return (f"the width meter measured highs on GPIO{pin} other than once each: "
                    f"{missed} missed, {extra} extra")
        if not self.short_holds():
            return self._short_line().strip()
        return ""

    def _short_line(self) -> str:
        holds = self.short_holds()
        head = f"  short-exit path (meter width <= {_us(SHORT_EXIT_MAX_NS)} us): "
        if holds:
            return head + f"shown, the run holds {', '.join(str(h) for h in holds)} ns"
        return head + ("not shown: no register-writer hold had every high stamped, measured "
                       f"once, and {SHORT_SHARE_SHOWN:.0%} of them measured short")

    def lines(self) -> list[str]:
        pin = self.dense_pin
        lines = []
        if self.running:
            lines.append(f"  machines not seen running: {self.running}")
        lines.append(f"  probe: GPIO{pin} rises {self.dense_rises}, stamped A "
                     f"{self.dense_by_a}, B {self.dense_by_b}; GPIO{BARCODE_PIN} rises "
                     f"{self.barcode_rises}, stamped B {self.barcode_by_b}, A "
                     f"{self.barcode_by_a}")
        held = [s for s in self.shapes if not s.writer and s.uppers]
        if held:
            lines.append(f"  highs on GPIO{pin} by libgpiod, lower..upper bound, median: " +
                         "; ".join(f"{s.name} {_us(median(s.lowers))}..{_us(median(s.uppers))}"
                                   f" us ({s.stamped} of {s.pulses} stamped, "
                                   f"{s.proven_short} proven short by the CPU)" for s in held))
        written = [s for s in self.shapes if s.writer and s.uppers]
        if written:
            lines.append(f"  highs on GPIO{pin} by the register writer, lower..upper bound: " +
                         "; ".join(f"hold {_us(s.hold_ns)} us: {s.stamped} of {s.pulses} "
                                   f"stamped, {s.proven_short} proven short by the CPU, median "
                                   f"{_us(median(s.lowers))}..{_us(median(s.uppers))} us, "
                                   f"max upper {_us(max(s.uppers))} us" for s in written))
            reads = [r for s in written for r in s.set_reads]
            clears = [r for s in written for r in s.clear_reads]
            lines.append(f"  register read-back, RAW: after the SET median {_us(median(reads))}"
                         f" us, max {_us(max(reads))} us; after the CLEAR median "
                         f"{_us(median(clears))} us, max {_us(max(clears))} us")
        metered = [s for s in self.shapes if s.widths_ns]
        if metered:
            lines.append("  width meter, median: " + "; ".join(
                f"{s.name if not s.writer else 'hold ' + _us(s.hold_ns) + ' us'} "
                f"{_us(median(s.widths_ns))} us (short {s.meter_paths['short']}, held "
                f"{s.meter_paths['held']}, neither {s.meter_paths[None]}"
                + (f", missed {s.meter_missed}" if s.meter_missed else "")
                + (f", extra {s.meter_extra}" if s.meter_extra else "") + ")"
                for s in metered))
        if self.complete:
            lines.append(self._short_line())
        return lines


def probe(rig, report: ProbeReport, a: int, b: int, short_holds: Sequence[int]) -> None:
    """Phase 1 of `streams`, about 30 s: P0, the running check on GPIO17 and on the dense
    pin; P1, PROBE_RISES rises on the dense pin; P2, as many on GPIO17; P3, each shape of
    high on the dense pin, libgpiod's then the register writer's."""
    from wl_sync.rp1.harness import raw_ns, wait_until

    pin = report.dense_pin
    report.running = rig.running_check(BARCODE_PIN) or rig.running_check(pin)
    if report.running:
        return
    due = raw_ns()
    for _ in range(PROBE_RISES):
        due += PROBE_SPACING_NS
        wait_until(due)
        event = rig.edge(pin, PROBE_HOLD_NS, "probe dense")
        report.dense_rises += 1
        report.dense_by_a += len(event.stamps[a]) == 1
        report.dense_by_b += len(event.stamps[b])
        report.meter_missed += not event.widths
        report.meter_extra += max(0, len(event.widths) - 1)
    for _ in range(PROBE_RISES):
        due += PROBE_SPACING_NS
        wait_until(due)
        event = rig.edge(BARCODE_PIN, PROBE_HOLD_NS, "probe barcode")
        report.barcode_rises += 1
        report.barcode_by_b += len(event.stamps[b]) == 1
        report.barcode_by_a += len(event.stamps[a])
        report.meter_extra += len(event.widths)  # the meter watches the dense pin alone
    shapes = [ShapeResult(name, False, hold) for name, hold in HELD_SHAPES_NS]
    shapes += [ShapeResult(f"writer {hold}", True, hold) for hold in short_holds]
    for shape in shapes:
        report.shapes.append(shape)
        for _ in range(PROBE_SHAPE_PULSES):
            due += PROBE_SHAPE_SPACING_NS
            wait_until(due)
            event = (rig.short(shape.hold_ns, shape.name) if shape.writer
                     else rig.edge(pin, shape.hold_ns, shape.name))
            shape.pulses += 1
            shape.stamped += len(event.stamps[a]) == 1
            shape.other += len(event.stamps[b])
            shape.uppers.append(event.upper_ns)
            if event.lower_ns is not None:
                shape.lowers.append(event.lower_ns)
            shape.proven_short += (len(event.stamps[a]) == 1 and path_of(event) == "short")
            if event.reads is not None:
                shape.set_reads.append(event.reads[0])
                shape.clear_reads.append(event.reads[1])
            shape.meter_missed += not event.widths
            shape.meter_extra += max(0, len(event.widths) - 1)
            if len(event.widths) == 1:
                width = rig.meter_width_ns(event.widths[0])
                shape.widths_ns.append(width)
                if len(event.stamps[a]) == 1:
                    shape.meter_paths[path_of_width(width, rig.meter_uncertainty_ns)] += 1
    report.complete = True


@dataclass
class StreamsReport:
    """B3b: each machine's count fitted against RAW through its own events (plan :2067),
    and the two slopes compared. A is the dense pin's machine, B GPIO17's."""

    dense_pin: int
    probe: ProbeReport
    asked_s: int = 0
    probe_only: bool = False
    a: int = 0
    b: int = 0
    enable_raw: int = 0
    ran: bool = False
    seconds: int = 0
    stopped: bool = False
    frames: int = 0
    a_events: int = 0
    a_stamped: int = 0
    a_missed: int = 0
    a_missed_writer: int = 0
    a_extra: int = 0
    b_events: int = 0
    b_stamped: int = 0
    b_missed: int = 0
    b_extra: int = 0
    crosstalk: int = 0
    # A's fitted highs by the width meter's path (`path_of_width`, fix round 6), which the
    # user's ">= 25% each" counts; and, as information only, by the CPU's bounds.
    held: int = 0
    short: int = 0
    unbounded: int = 0
    cpu_short: int = 0
    cpu_held: int = 0
    meter_missed: int = 0
    meter_extra: int = 0
    # The meter's counts to ns, from the rig (`Rig.meter_width_ns`).
    width_of: Callable[[int], float] | None = None
    meter_uncertainty_ns: float = 0.0
    wide: int = 0
    fit_a: LineFit = field(default_factory=LineFit)
    fit_b: LineFit = field(default_factory=LineFit)
    markers: int = 0
    marker: Counter = field(default_factory=Counter)
    words: int = 0
    full: int = 0
    slips: int = 0
    level_errors: int = 0
    carried: int = 0
    drain_ns: list[int] = field(default_factory=list)

    def _point(self, fit: LineFit, event, stamp: int) -> bool:
        """The rise's RAW estimate against its stamp, unwrapped near RAW's time since the
        sync enable (wl_sync/clockmap.py:276-281): P wraps at 2^32 us, 71.6 min
        (pio_capture.py:52-53), inside a long run. A bracket wider than
        BRACKET_WIDTH_MAX_NS (clockmap.py:41) is left out, as the fit leaves it."""
        from wl_sync.clockmap import BRACKET_WIDTH_MAX_NS, unwrap_near

        if event.bracket_ns > BRACKET_WIDTH_MAX_NS:
            self.wide += 1
            return False
        x2 = event.rise_ns_x2 - 2 * self.enable_raw
        fit.add(x2, unwrap_near(stamp, x2 // (2 * _NS_PER_US)))
        return True

    def _one(self, event, own: int, other: int) -> int | None:
        found = event.stamps[own]
        self.crosstalk += len(event.stamps[other])
        return found[0] if len(found) == 1 else None

    def add_dense(self, event, writer: bool) -> None:
        self.a_events += 1
        self._measured(event)
        stamp = self._one(event, self.a, self.b)
        if stamp is None:
            self.a_missed += not event.stamps[self.a]
            self.a_missed_writer += writer and not event.stamps[self.a]
            self.a_extra += max(0, len(event.stamps[self.a]) - 1)
            return
        self.a_stamped += 1
        if not self._point(self.fit_a, event, stamp):
            return
        self._credit(event)

    def _measured(self, event) -> None:
        """The meter's health on a dense-pin high: exactly one width."""
        self.meter_missed += not event.widths
        self.meter_extra += max(0, len(event.widths) - 1)

    def _credit(self, event) -> None:
        path = None
        if len(event.widths) == 1 and self.width_of is not None:
            path = path_of_width(self.width_of(event.widths[0]), self.meter_uncertainty_ns)
        if path == "short":
            self.short += 1
        elif path == "held":
            self.held += 1
        else:
            self.unbounded += 1
        cpu = path_of(event)
        self.cpu_short += cpu == "short"
        self.cpu_held += cpu == "held"

    def add_barcode(self, event) -> None:
        self.b_events += 1
        self.meter_extra += len(event.widths)  # the meter watches the dense pin alone
        stamp = self._one(event, self.b, self.a)
        if stamp is None:
            self.b_missed += not event.stamps[self.b]
            self.b_extra += max(0, len(event.stamps[self.b]) - 1)
            return
        self.b_stamped += 1
        self._point(self.fit_b, event, stamp)

    def add_marker(self, event) -> None:
        """Both pins in one `set_values` at a frame's lead: an A event and a B event, and
        A - B, which one lost tick on either moves by 1 with no fit. Informational: that
        the two pads change on one write is [unverified] (the user's decision 5)."""
        self.markers += 1
        self._measured(event)
        a, b = event.stamps[self.a], event.stamps[self.b]
        self.a_events += 1
        self.b_events += 1
        for found, name in ((a, "a"), (b, "b")):
            if len(found) == 1:
                setattr(self, f"{name}_stamped", getattr(self, f"{name}_stamped") + 1)
            elif not found:
                setattr(self, f"{name}_missed", getattr(self, f"{name}_missed") + 1)
            else:
                setattr(self, f"{name}_extra", getattr(self, f"{name}_extra") + len(found) - 1)
        if len(a) == 1:
            if self._point(self.fit_a, event, a[0]):
                self._credit(event)
        if len(b) == 1:
            self._point(self.fit_b, event, b[0])
        if len(a) == 1 and len(b) == 1:
            self.marker[_signed(a[0] - b[0])] += 1

    def difference(self) -> tuple[float, float] | None:
        if self.fit_a.n < 3 or self.fit_b.n < 3:
            return None
        return slope_difference_ppm(self.fit_a, self.fit_b)

    def faults(self) -> list[str]:
        found = []
        failure = self.probe.failure()
        if failure:
            found.append(f"probe: {failure}")
        missed = (f"A missed {self.a_missed} (register-writer highs "
                  f"{self.a_missed_writer}, libgpiod highs "
                  f"{self.a_missed - self.a_missed_writer})")
        if self.a_missed:
            found.append(missed)
        for name, count in (("A extra", self.a_extra),
                            ("B missed", self.b_missed), ("B extra", self.b_extra),
                            ("stamps by the other pin's machine", self.crosstalk),
                            ("code words", self.words), ("full FIFOs", self.full),
                            ("slips", self.slips), ("failed level reads", self.level_errors),
                            ("data words left waiting", self.carried),
                            ("width meter missed", self.meter_missed),
                            ("width meter extra", self.meter_extra)):
            if count:
                found.append(f"{name} {count}")
        difference = self.difference()
        if difference is not None:
            delta, sigma = difference
            if abs(delta) > B3B_LINE_PPM and abs(delta) > 3 * sigma:
                found.append(f"A - B is {delta:.4f} ppm, beyond the {B3B_LINE_PPM} ppm line")
        return found

    def shortfalls(self) -> list[str]:
        """Why a run with no fault still cannot pass."""
        found = []
        if self.stopped:
            found.append("stopped before its end")
        if self.seconds < B3B_MIN_DURATION_S:
            found.append(f"run {self.seconds} s of the {B3B_MIN_DURATION_S} s B3b needs")
        if self.fit_a.n < B3B_MIN_A_EVENTS:
            found.append(f"A fitted through {self.fit_a.n} events of the "
                         f"{B3B_MIN_A_EVENTS} B3b needs")
        for name, count in (("held path", self.held), ("short exit", self.short)):
            if count < B3B_PATH_SHARE_MIN * max(self.fit_a.n, 1):
                found.append(f"the {name} has {count} of A's {self.fit_a.n} fitted events, "
                             f"under {B3B_PATH_SHARE_MIN:.0%}")
        difference = self.difference()
        if difference is None:
            found.append("too few points to fit")
        elif difference[1] > B3B_SIGMA_MAX_PPM:
            found.append(f"1 sigma on A - B is {difference[1]:.5f} ppm, over "
                         f"{B3B_SIGMA_MAX_PPM} ppm")
        return found

    @property
    def verdict(self) -> str:
        if self.probe_only:
            return "FAIL" if self.probe.failure() else (
                "PASS" if self.probe.complete else "INCONCLUSIVE")
        if self.faults():
            return "FAIL"
        return "INCONCLUSIVE" if self.shortfalls() else "PASS"

    def summary(self) -> str:
        title = "independent streams (B3b)" + (" probe" if self.probe_only else "")
        lines = [f"{title}: {self.verdict}", *self.probe.lines()]
        if self.ran:
            pin = self.dense_pin
            fit_a, fit_b = self.fit_a, self.fit_b

            def slope(fit: LineFit) -> str:
                if fit.n < 3:
                    return "too few points to fit"
                return (f"slope {float(fit.slope_us_per_us()):.12f} +- "
                        f"{fit.stderr_us_per_us():.12f}")

            lines.append(f"  A (GPIO{pin}, SM{self.a}): {self.a_events} rises, "
                         f"{self.a_stamped} stamped, fit through {fit_a.n}: {slope(fit_a)}")
            lines.append(f"    held path (meter width >= {_us(HELD_MIN_NS)} us) {self.held}, "
                         f"short exit (meter width <= {_us(SHORT_EXIT_MAX_NS)} us) "
                         f"{self.short}, neither {self.unbounded}; by the CPU's bounds, "
                         f"for information, short {self.cpu_short}, held {self.cpu_held}; "
                         f"{self.wide} brackets wider than the fit takes")
            lines.append(f"  B (GPIO{BARCODE_PIN}, SM{self.b}): {self.frames} frames, "
                         f"{self.b_events} rises, {self.b_stamped} stamped, fit through "
                         f"{fit_b.n}: {slope(fit_b)}")
            difference = self.difference()
            if difference is not None:
                lines.append(f"  A - B: {difference[0]:.4f} ppm (line {B3B_LINE_PPM} ppm; "
                             f"1 sigma {difference[1]:.5f} ppm)")
            if self.markers:
                if len(self.marker) == 1:
                    [(d, n)] = self.marker.items()
                    told = f"{d} on {n} of {self.markers} frames"
                else:
                    told = ", ".join(f"{d} x{n}" for d, n in sorted(self.marker.items()))
                lines.append(f"  marker A-B: {told} (informational)")
            lines.append(f"  run {self.seconds} s of the {self.asked_s} s asked")
        lines.append(_drain_line(vars(self)))
        for fault in self.faults():
            lines.append(f"  fails: {fault}")
        if not self.probe_only and not self.faults():
            for shortfall in self.shortfalls():
                lines.append(f"  inconclusive: {shortfall}")
        return "\n".join(lines)


def _drive_frame(rig, report: StreamsReport, pulses, start_ns: int, marker: bool) -> None:
    """One barcode frame on GPIO17 against absolute deadlines (`pulse_deadlines`,
    wl_sync/rp1/gpio.py:217-240), each rise bracketed and drained, ending low as `_drive`
    ends every frame (backend.py:1608-1613). With `marker`, the lead rises with the dense
    pin in one call."""
    from wl_sync.rp1.gpio import pulse_deadlines
    from wl_sync.rp1.harness import wait_until

    end_ns = start_ns + sum(duration for _, duration in pulses) * _NS_PER_US
    level, lead = 0, True
    for want, deadline in [*pulse_deadlines(pulses, start_ns), (0, end_ns)]:
        wait_until(deadline)
        if want and not level:
            if lead and marker:
                report.add_marker(rig.marker((BARCODE_PIN, report.dense_pin),
                                             report.dense_pin, MARKER_HOLD_NS))
            else:
                report.add_barcode(rig.rise(BARCODE_PIN, "frame"))
            lead = False
        elif level and not want:
            rig.lines.set(BARCODE_PIN, False)
        level = want


def streams_run(rig, report: StreamsReport, duration_s: int, rate: int, marker: bool,
                short_holds: Sequence[int], seed: int) -> None:
    """Phase 2 of `streams`: each second, a frame on GPIO17, then `rate` events on the
    dense pin in the idle after it, alternating libgpiod's held highs and the writer's
    short ones, so both paths interleave over the same hour and a drift between RP1's
    crystal and the CPU's moves both slopes alike."""
    from wl_sync.rp1.harness import raw_ns, wait_until

    rng = random.Random(seed)
    held = [(name, hold) for name, hold in HELD_SHAPES_NS if name in RUN_HELD_SHAPES]
    short = [(f"writer {hold}", hold) for hold in short_holds]
    cycle = []
    for k in range(max(len(held), len(short))):
        cycle += [(False, *held[k % len(held)]), (True, *short[k % len(short)])]
    second_ns = INTERVAL_US * _NS_PER_US
    frame_ns = FRAME_US * _NS_PER_US
    span = second_ns - frame_ns - 2 * IDLE_MARGIN_NS
    start = raw_ns() + PROBE_SPACING_NS
    count = 0
    for second in range(duration_s):
        t0 = start + second * second_ns
        _drive_frame(rig, report, encode(rng.getrandbits(32)), t0, marker)
        report.frames += 1
        first = t0 + frame_ns + IDLE_MARGIN_NS
        for k in range(rate):
            wait_until(first + k * span // rate)
            writer, name, hold = cycle[count % len(cycle)]
            count += 1
            event = rig.short(hold, name) if writer else rig.edge(report.dense_pin, hold, name)
            report.add_dense(event, writer)
        report.seconds = second + 1


def _take_tally(report, rig) -> None:
    if rig is None:
        return
    tally = rig.tally
    report.words, report.full, report.slips = tally.words, tally.full, tally.slips
    report.level_errors, report.drain_ns = tally.level_errors, tally.drain_ns
    report.carried = rig.carried()


def _harnessed(title: str, open_rig: Callable, body: Callable, record: Path | None,
               command: str):
    """Open the rig, run `body(rig, recorder)`, and close the rig on every path: SIGTERM
    (pkill) raises `_Terminated`, as in `stamps`, so the writer is restored and the
    machines stopped before the summary. Returns (status or None, the rig, the SIGTERM).
    A rig that cannot open is printed with `title` and status 1."""
    from wl_sync.rp1.harness import HarnessError, PiolibError, WriterError

    recorder = _Recorder(record, command) if record is not None else None
    rig = stopped = closing = None
    outer: dict[int, object] = {}
    try:
        # SIGHUP as SIGTERM (review I3): an ssh session that drops sends it, and its
        # default would end the process between a writer pulse's two stores. But a SIGHUP
        # ignored already, as nohup leaves it, stays ignored, or the hour nohup was
        # launched to keep would end (review N2).
        for signum in _ENDING_SIGNALS:
            if signum == signal.SIGHUP and signal.getsignal(signum) == signal.SIG_IGN:
                continue
            outer[signum] = signal.signal(signum, _terminate)
        try:
            rig = open_rig()
        except (HarnessError, PiolibError, WriterError, OSError) as error:
            print(f"{title} cannot run: {error}")
            return 1, None, None, None
        if recorder is not None:
            rig.on_event = recorder.write
        body(rig, recorder)
    except _Terminated as terminated:
        stopped = terminated
    finally:
        # The run's own error, if one is in flight: read first, since inside the
        # `except` below sys.exc_info() is the close's (review N1).
        in_flight = sys.exc_info()[1]
        # Nothing may cut close() short: a second signal, or a Ctrl-C.
        ignored = {signum: signal.signal(signum, signal.SIG_IGN)
                   for signum in (*outer, signal.SIGINT)}
        try:
            if rig is not None:
                rig.close()
        except BaseException as error:  # noqa: BLE001 - reported after the summary (M2)
            closing = error
            if in_flight is not None:
                in_flight.add_note(f"and closing the rig raised: {error!r}")
        finally:
            if recorder is not None:
                recorder.close()
            for signum, handler in outer.items():
                signal.signal(signum, signal.SIG_DFL if handler is None else handler)
            signal.signal(signal.SIGINT, ignored[signal.SIGINT])
    return None, rig, stopped, closing


def _ended(stopped: _Terminated, done: str) -> int:
    """The line a run cut short by a signal ends on, and its exit status."""
    print(f"  stopped by {signal.Signals(stopped.code - 128).name} after {done}")
    return stopped.code


def _closing(closing: BaseException | None) -> bool:
    if closing is not None:
        print(f"  closing the rig failed: {closing!r}")
    return closing is not None


def _skew(machines: int, edges: int, period_us: int, chip_path: str,
          record: Path | None) -> int:
    """`python -m wl_sync.bench skew`: B3a, on the box, with the shim."""
    from wl_sync.rp1 import harness

    report = SkewReport(machines=[])
    started: list[int] = []

    def open_rig():
        return harness.Rig.open(chip_path, [BARCODE_PIN] * machines)

    def body(rig, recorder):
        nonlocal report
        report = SkewReport(machines=rig.block.stamp_sms, capture_sm=rig.block.capture_sm)
        if recorder is not None:
            recorder.phase = "skew"
        enable = rig.block.enable_in_sync()
        if recorder is not None:
            recorder.comment(f"enable_raw_ns={enable}")
        started.append(harness.raw_ns())
        skew_run(rig, edges, period_us * _NS_PER_US, report)

    status, rig, stopped, closing = _harnessed(
        "start skew (B3a)", open_rig, body, record,
        f"skew --machines {machines} --edges {edges}")
    if status is not None:
        return status
    if started:
        report.seconds = (harness.raw_ns() - started[0]) / 1e9
    _take_tally(report, rig)
    report.stopped = stopped is not None
    print(report.summary())
    failed_close = _closing(closing)
    if stopped is not None:
        return _ended(stopped, f"{report.edges} of {edges} edges")
    return 0 if report.passed and not failed_close else 1


def _streams(dense_pin: int, duration_s: int, rate: int, marker: bool, probe_only: bool,
             chip_path: str, record: Path | None, short_holds: Sequence[int],
             seed: int | None) -> int:
    """`python -m wl_sync.bench streams`: B3b, on the box, with the shim and the
    register writer."""
    from wl_sync.rp1 import harness

    report = StreamsReport(dense_pin=dense_pin, probe=ProbeReport(dense_pin),
                           asked_s=duration_s, probe_only=probe_only)

    def open_rig():
        return harness.Rig.open(chip_path, [dense_pin, BARCODE_PIN], dense_pin=dense_pin)

    def body(rig, recorder):
        report.a, report.b = rig.block.stamp_sms
        report.width_of = rig.meter_width_ns
        report.meter_uncertainty_ns = rig.meter_uncertainty_ns
        report.enable_raw = rig.block.enable_in_sync()
        if recorder is not None:
            recorder.comment(f"enable_raw_ns={report.enable_raw}")
            recorder.comment(f"A=SM{report.a} on GPIO{dense_pin}, B=SM{report.b} on "
                             f"GPIO{BARCODE_PIN}")
            # What turns meter_counts back into widths (fix round 6's review, Minor 7).
            recorder.comment(f"clk_sys_hz={rig.block.clk_sys_hz}")
            recorder.comment(f"meter=SM{rig.block.meter_sm} on GPIO{dense_pin}: width_ns = "
                             f"(2 count + 1) x {rig.meter_width_ns(0):g}, "
                             f"+-{rig.meter_uncertainty_ns:g}")
            recorder.phase = "probe"
        probe(rig, report.probe, report.a, report.b, short_holds)
        if probe_only or report.probe.failure():
            return
        if recorder is not None:
            recorder.phase = "run"
        report.ran = True
        streams_run(rig, report, duration_s, rate, marker, report.probe.short_holds(),
                    duration_s if seed is None else seed)

    status, rig, stopped, closing = _harnessed(
        "independent streams (B3b)", open_rig, body, record,
        f"streams --dense-pin {dense_pin} --duration {duration_s} --rate {rate}"
        + (" --marker" if marker else "") + (" --probe-only" if probe_only else ""))
    if status is not None:
        return status
    _take_tally(report, rig)
    report.stopped = stopped is not None
    print(report.summary())
    failed_close = _closing(closing)
    if stopped is not None:
        return _ended(stopped, f"{report.seconds} of {duration_s} s")
    return 0 if report.verdict == "PASS" and not failed_close else 1


def _override(pin: int | None, clear: bool, chip_path: str) -> int:
    """`python -m wl_sync.bench override`: the register writer's post-check and recovery
    (review I3), with no sudo.

    With no `--clear`, READ ONLY: each spare pin's CTRL and STATUS through the harness's map,
    since pinctrl does not show the override fields [inferred]. It exits 0 only when
    neither OUTOVER nor OEOVER is set. With `--clear`, `harness.clear_override` on the one
    pin named: libgpiod takes the line, the map is checked against it, and one CLEAR of
    OUTOVER is written only if every check passes."""
    from wl_sync.rp1 import harness

    if clear:
        try:
            print(harness.clear_override(pin, chip_path))
        except harness.NothingToClear as error:
            print(f"GPIO{pin}: NOT CLEARED: {error}")
            return 1
        except (harness.HarnessError, harness.WriterError, OSError) as error:
            print(f"GPIO{pin}: not cleared: {error}")
            return 1
        return 0
    try:
        found = harness.read_overrides((pin,) if pin is not None else SPARE_PINS)
    except OSError as error:
        print(f"the overrides cannot be read: {error}")
        return 1
    clean = True
    for each, ctrl, status, outover, oeover in found:
        line = (f"GPIO{each}: CTRL {ctrl:#010x} (FUNCSEL {ctrl & harness.FUNCSEL_MASK}, "
                f"OUTOVER {outover}, OEOVER {oeover}), STATUS {status:#010x}")
        if outover or oeover:
            clean = False
            line += ": an override is set; clear it with " + harness.clear_command(each)
        else:
            line += ": no override"
        print(line)
    return 0 if clean else 1


def _short_holds(text: str) -> tuple[int, ...]:
    """--short-holds-ns: each hold a positive integer of ns under SHORT_EXIT_MAX_NS, since
    a spin that long cannot make a short high."""
    try:
        holds = tuple(int(part) for part in text.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a list of integers: {text!r}") from error
    if not holds or any(not 0 < hold < SHORT_EXIT_MAX_NS for hold in holds):
        raise argparse.ArgumentTypeError(
            f"each hold must lie between 0 and {SHORT_EXIT_MAX_NS} ns: {text!r}")
    return holds


def _rate(text: str) -> int:
    """--rate: dense events a second, which must fit the 760 ms of idle with room for
    the longest held shape (1 ms), its settle and its drain."""
    rate = int(text)
    if not 1 <= rate <= 400:
        raise argparse.ArgumentTypeError(f"--rate must be 1-400 events a second, not {rate}")
    return rate


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


def _parity(paths: list[Path]) -> int:
    import tempfile

    from wl_sync import parity

    with tempfile.TemporaryDirectory(prefix="wl-sync-parity-") as workdir:
        passed, text = parity.run(paths, Path(workdir))
    print(text)
    return 0 if passed else 1


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
    skew = commands.add_parser(
        "skew", help="B3a: start skew between stamp machines on GPIO17, one sync enable"
    )
    skew.add_argument("--machines", type=int, choices=(2, 3), default=3,
                      help="stamp machines on GPIO17, beside the capture machine")
    skew.add_argument("--edges", type=int, default=100_000)
    skew.add_argument("--period-us", type=int, default=SKEW_PERIOD_US)
    skew.add_argument("--gpiochip", default="/dev/gpiochip0")
    skew.add_argument("--record", type=Path, help="one CSV line per edge")
    streams = commands.add_parser(
        "streams", help="B3b: a dense pin's machine against GPIO17's, against RAW"
    )
    streams.add_argument("--dense-pin", type=int, choices=SPARE_PINS, default=SPARE_PINS[0],
                         help="the spare pin machine A reads, unclaimed (decision 4: 19 "
                              "if 18 is blind)")
    streams.add_argument("--duration", type=int, default=B3B_MIN_DURATION_S,
                         help="seconds of run after the probe")
    streams.add_argument("--rate", type=_rate, default=100, help="dense events a second")
    streams.add_argument("--marker", action="store_true",
                         help="GPIO17 and the dense pin rise together at each frame's "
                              "lead (informational)")
    streams.add_argument("--probe-only", action="store_true")
    streams.add_argument("--short-holds-ns", type=_short_holds, default=SHORT_HOLDS_NS)
    streams.add_argument("--seed", type=int)
    streams.add_argument("--gpiochip", default="/dev/gpiochip0")
    streams.add_argument("--record", type=Path, help="one CSV line per edge")
    override = commands.add_parser(
        "override", help="the register writer's post-check, and its recovery: GPIO18's "
                         "and GPIO19's OUTOVER, read, or cleared on one pin"
    )
    override.add_argument("--pin", type=int, choices=SPARE_PINS,
                          help="one spare pin; both when not given")
    override.add_argument("--clear", action="store_true",
                          help="clear an override left set on --pin")
    override.add_argument("--gpiochip", default="/dev/gpiochip0")
    parity_command = commands.add_parser(
        "parity", help="Task 5b Step 4: a box segment's structure against the fake's, "
                       "replayed from the same inputs (wl_sync/parity.py)"
    )
    parity_command.add_argument("paths", type=Path, nargs="+",
                                help="segments, or directories of them")
    args = parser.parse_args(argv)
    if args.command == "override" and args.clear and args.pin is None:
        parser.error("override --clear needs --pin")

    if args.command == "stamps":
        return _stamps(args.stamp_pin, args.frames, args.gpiochip)
    if args.command == "skew":
        return _skew(args.machines, args.edges, args.period_us, args.gpiochip, args.record)
    if args.command == "streams":
        return _streams(args.dense_pin, args.duration, args.rate, args.marker,
                        args.probe_only, args.gpiochip, args.record, args.short_holds_ns,
                        args.seed)
    if args.command == "override":
        return _override(args.pin, args.clear, args.gpiochip)
    if args.command == "parity":
        return _parity(args.paths)
    report = loopback(_segment_of(args.path), args.gpio)
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
