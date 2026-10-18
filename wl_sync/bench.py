"""Bench checks that read a recorded segment back. Not part of the recording path.

    python -m wl_sync.bench loopback <segment or day directory> [--gpio 20]

THE LOOPBACK. With the barcode output jumpered to an edge input, every frame the box
emits lands in its own segment twice: as the `B` record it logged, and as the edges it
saw on the input. Decoding those edges and matching them to the `B` records tests the
barcode output itself, which no fake can do because the pin is the thing under test.
It was written for the defect it would have caught: emit_pulses left the pin HIGH
after every frame, so only a run's first barcode could ever decode.

Finding frames is delegated to `barcode.decode_edges`, never reimplemented here, so a
change to the codec cannot leave this check and the receivers disagreeing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from wl_sync.barcode import decode_edges
from wl_sync.log import BarcodeEmitted, Edge, read_log
from wl_sync.pins import BARCODE_PIN, GPIO_MAP
from wl_sync.segment import SEGMENT_PATTERN, segment_index_of

# pd1_comp's line, free while no breakout board is fitted: GPIO17 -> GPIO20 is header
# pin 11 -> pin 38.
DEFAULT_LOOP_GPIO = GPIO_MAP["pd1_comp"]


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

    @property
    def passed(self) -> bool:
        return bool(self.edge_count and self.matched and not self.missing
                    and not self.unexpected)

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
        if self.lead_offsets_us:
            lines.append(
                f"  lead edge after its logged tick: median "
                f"{median(self.lead_offsets_us):.0f} us, max {max(self.lead_offsets_us)} us"
            )
        return "\n".join(lines)


def loopback(path: Path, gpio: int = DEFAULT_LOOP_GPIO) -> LoopbackReport:
    """Match the barcodes this segment logged against the ones decoded from `gpio`.

    THE FIRST FRAME IS NOT CHECKABLE, and is reported as such rather than as a failure.
    A frame is found by a rising edge after IDLE_MIN_US of LOW, and nothing in the
    record precedes the first one, so the decoder cannot verify the idle before it.
    """
    _, records = read_log(path)
    barcodes = [r for r in records if isinstance(r, BarcodeEmitted)]
    edges = [(r.tick_us, r.level) for r in records if isinstance(r, Edge) and r.gpio == gpio]
    report = LoopbackReport(gpio=gpio, emitted=[b.value for b in barcodes],
                            edge_count=len(edges))
    if not edges:
        return report

    logged = {b.value: b.tick_us for b in barcodes}
    decoded = decode_edges(edges)
    seen = set()
    for frame in decoded:
        if frame.value in logged:
            report.matched += 1
            report.lead_offsets_us.append(frame.start_us - logged[frame.value])
            seen.add(frame.value)
        else:
            report.unexpected.append(frame.value)
    for index, barcode in enumerate(barcodes):
        if barcode.value in seen:
            continue
        (report.unverifiable if index == 0 else report.missing).append(barcode.value)
    return report


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
    args = parser.parse_args(argv)

    report = loopback(_segment_of(args.path), args.gpio)
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
