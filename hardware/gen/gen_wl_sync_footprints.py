"""Generator for hardware/lib/wl-sync.pretty/*.kicad_mod -- the four custom footprints
Task 6 needs that aren't in any KiCad stock .pretty library: the 68-pin MDR male
(right-angle PCB mount), mini-XLR TA4M and TA5M (panel mount), and the 4-pin mini-DIN
(panel mount). The fifth and sixth symbols this task adds (the ACCES I/O DB37 connector
and the Raspberry Pi 5 GPIO header) reuse STOCK footprints instead -- see the report for
why: DB37 is a mechanically standardised D-sub shell (KiCad's own Connector_Dsub.pretty
already has it, real dimensions, not "a similar part"), and the Pi header reuses the same
bare 2x20 2.54mm THT footprint hardware/gen/gen_mule.py's own Pi-side header already uses
(FOOTPRINT_IDC40 in that file) -- the real Raspberry Pi GPIO header is exactly that part.

Written as raw .kicad_mod text (there is no stock footprint to extract from), following
the exact low-level shape kicad_pcb.py's extract_footprint()/Board.place() expect to read
back -- confirmed against a real pcbnew/kicad-footprint-generator-authored file
(Connector_Dsub.pretty's own DSUB-37 Housed variant, read directly while designing this):
no `uuid` on any child element and no `net` on any pad at the LIBRARY level (Board.place()
adds both -- a fresh uuid per graphic/pad, and net only for pads present in its own
pad_nets dict -- when an instance is actually placed; a library master carrying its own
uuid/net would leave a stray, wrong second one behind, or a net no instance's schematic
agrees with).

Sourcing and confidence, stated once here rather than scattered per symbol (also see the
task report for the full research trail):

- MDR68_Male_RightAngle: overall envelope (63.86 x 16.7 x 12.5mm) from MH Connectors
  3700-0121-01, a real, currently-distributed right-angle male 68-pin MDR/SCSI-3
  connector (corroborated across 5+ independent distributor listings). Contact pitch
  1.27mm and 2-row/34-per-row layout are the MDR format's own defining dimensions (3M's
  own "102 Series" datasheet literally titles the family ".050in Boardmount... Connectors"
  -- 0.050in = 1.27mm -- and every MDR68 datasheet found agrees on 2x34); row spacing
  2.84mm and jackscrew-to-jackscrew spacing 57.9mm are figures that appeared consistently
  across general MDR-68 dimensional references but were not confirmed against one single
  primary-source CAD drawing with a numeric callout for THIS specific dimension --
  flagged in the report as the one MDR68 number worth re-checking against the exact MPN
  Task 0 procures, same discipline that task's own brief already calls for.
- MiniXLR_TA4M/TA5M_Panel: panel/chassis thickness (6.35mm max) is Switchcraft's own
  published number for the TB-series panel-mount receptacle -- the real part this
  footprint models (TA4M/TA5M themselves are CABLE-mount only and TA4M is discontinued;
  see the report for why the footprint is the TB-series and the symbol keeps the
  spec's own TA4M/TA5M name). Panel bushing/cutout diameter (~10.9mm) is corroborated
  from the TA-series housing diameter (0.413in/10.5mm, Switchcraft's own catalog) but not
  confirmed against a primary numeric TB-series drawing -- the least-certain dimension on
  this board's panel-mount parts by the report's own account, flagged there for
  re-verification before panel machining. Internal contact-circle arrangement is a clean,
  evenly-spaced approximation, not measured -- lower risk than the cutout itself (fixing a
  contact position is a footprint edit; fixing a wrong cutout is a scrapped panel), but
  still worth checking against the real part before fab.
- MiniDIN_4_Panel: panel envelope, mounting-hole spacing/diameter, and through-panel
  bushing diameter are all read directly off a real, dimensioned manufacturer CAD drawing
  (Same Sky/CUI MD-SN series datasheet, the 4-pin MD-40SN row) -- the best-sourced of the
  four. Contact-circle diameter (7.0mm) is the mini-DIN family's own well-established
  standard (this connector class is only useful because it interoperates across
  manufacturers on a fixed contact geometry); exact per-pin angular position was
  approximated as four evenly-spaced positions rather than measured off the drawing's own
  small pin diagram, same lower-risk-than-cutout reasoning as the mini-XLR pins above.

No footprint below drills a PCB mounting hole for the mini-XLR/mini-DIN parts: both are
PANEL-mount (their own flange or bushing nut carries the mechanical load, screwed to the
sheet-metal panel, not to the board -- confirmed for the mini-DIN from Same Sky's own
"thin metal mounting ears" + "panel mount" feature list, and for the mini-XLR from
Switchcraft's "specially designed flange permits close mount on crowded panels" wording),
so their PCB footprint only needs solder pads. MDR68 is different and DOES get two
mechanical mounting holes: Task 0's own procurement table calls it "PCB mount" (its own
board anchor resists cable insertion/withdrawal and jackscrew torque, unlike the other
two), consistent with every real MDR/SCSI connector's own datasheet always specifying
board-side mounting-post holes.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kicad_pcb import _find_balanced, _split_children, _child_tag  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "lib" / "wl-sync.pretty"


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def fp_pad(number: str, x: float, y: float, size: float, drill: float, shape: str = "circle") -> str:
    return (
        f'\t(pad "{number}" thru_hole {shape}\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)})\n"
        f"\t\t(size {_fmt(size)} {_fmt(size)})\n"
        f"\t\t(drill {_fmt(drill)})\n"
        f'\t\t(layers "*.Cu" "*.Mask")\n'
        f"\t\t(remove_unused_layers no)\n"
        f"\t)"
    )


def fp_mounting_hole(x: float, y: float, size: float, drill: float) -> str:
    """A mechanical-only, non-plated, unnumbered hole -- no net, matching KiCad's own
    stock MountingHole_*.kicad_mod pad convention exactly (read directly before writing
    this: `(pad "" np_thru_hole circle ...)`)."""
    return (
        f'\t(pad "" np_thru_hole circle\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)})\n"
        f"\t\t(size {_fmt(size)} {_fmt(size)})\n"
        f"\t\t(drill {_fmt(drill)})\n"
        f'\t\t(layers "*.Cu" "*.Mask")\n'
        f"\t)"
    )


def fp_line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.12) -> str:
    return (
        f"\t(fp_line\n"
        f"\t\t(start {_fmt(x1)} {_fmt(y1)})\n"
        f"\t\t(end {_fmt(x2)} {_fmt(y2)})\n"
        f"\t\t(stroke (width {_fmt(width)}) (type solid))\n"
        f'\t\t(layer "{layer}")\n'
        f"\t)"
    )


def fp_rect(x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.12, fill: str = "no") -> str:
    return (
        f"\t(fp_rect\n"
        f"\t\t(start {_fmt(x1)} {_fmt(y1)})\n"
        f"\t\t(end {_fmt(x2)} {_fmt(y2)})\n"
        f"\t\t(stroke (width {_fmt(width)}) (type solid))\n"
        f"\t\t(fill {fill})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t)"
    )


def fp_circle(cx: float, cy: float, r: float, layer: str, width: float = 0.12, fill: str = "no") -> str:
    return (
        f"\t(fp_circle\n"
        f"\t\t(center {_fmt(cx)} {_fmt(cy)})\n"
        f"\t\t(end {_fmt(cx + r)} {_fmt(cy)})\n"
        f"\t\t(stroke (width {_fmt(width)}) (type solid))\n"
        f"\t\t(fill {fill})\n"
        f'\t\t(layer "{layer}")\n'
        f"\t)"
    )


def fp_text_ref(x: float, y: float, size: float = 1.0) -> str:
    return (
        f'\t(fp_text reference "REF**"\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)} 0)\n"
        f'\t\t(layer "F.SilkS")\n'
        f"\t\t(effects (font (size {_fmt(size)} {_fmt(size)}) (thickness 0.15)))\n"
        f"\t)"
    )


def fp_text_val(x: float, y: float, value: str, size: float = 1.0) -> str:
    return (
        f'\t(fp_text value "{value}"\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)} 0)\n"
        f'\t\t(layer "F.Fab")\n'
        f"\t\t(effects (font (size {_fmt(size)} {_fmt(size)}) (thickness 0.15)))\n"
        f"\t)"
    )


def assemble(modname: str, descr: str, tags: str, elements: list[str], attr: str = "through_hole") -> str:
    body = "\n".join(elements)
    return (
        f'(footprint "{modname}"\n'
        f"\t(version 20241229)\n"
        f'\t(generator "wl-sync-gen")\n'
        f'\t(generator_version "10.0")\n'
        f'\t(layer "F.Cu")\n'
        f'\t(descr "{descr}")\n'
        f'\t(tags "{tags}")\n'
        f'\t(attr {attr})\n'
        f"\t(duplicate_pad_numbers_are_jumpers no)\n"
        f"{body}\n"
        f"\t(embedded_fonts no)\n"
        f")\n"
    )


def self_check(modname: str, text: str, expected_numbered_pads: int) -> None:
    """Round-trip through kicad_pcb.py's OWN parser before writing to disk -- same
    discipline as gen_wl_sync_lib.py's self_check() for the symbol side."""
    start = text.find(f'(footprint "{modname}"')
    assert start == 0, f"{modname}: footprint block must start the file"
    block = _find_balanced(text, start)
    numbered = [
        c for c in _split_children(block)
        if _child_tag(c) == "pad" and not c.startswith('(pad ""')
    ]
    assert len(numbered) == expected_numbered_pads, (
        f"{modname}: {len(numbered)} numbered pads, expected {expected_numbered_pads}"
    )


# ---------------------------------------------------------------------------
# 1. MDR68_Male_RightAngle
# ---------------------------------------------------------------------------
def build_mdr68() -> str:
    PITCH = 1.27
    ROW_SPACING = 2.84
    N_COLS = 34
    PAD_SIZE, DRILL = 0.8, 0.5
    MOUNT_SPACING = 57.9
    MOUNT_SIZE, MOUNT_DRILL = 2.79, 2.79  # 4-40 UNC clearance, non-plated
    SHELL_W, SHELL_H = 63.86, 16.7  # MH Connectors 3700-0121-01 envelope

    row0_y, row1_y = 0.0, ROW_SPACING
    x0, x1 = 0.0, (N_COLS - 1) * PITCH
    field_cx = (x0 + x1) / 2
    field_cy = (row0_y + row1_y) / 2

    els = []
    for col in range(N_COLS):
        x = x0 + col * PITCH
        num = str(col + 1)
        els.append(fp_pad(num, x, row0_y, PAD_SIZE, DRILL, "rect" if col == 0 else "circle"))
    for col in range(N_COLS):
        x = x0 + col * PITCH
        num = str(col + 1 + N_COLS)
        els.append(fp_pad(num, x, row1_y, PAD_SIZE, DRILL, "circle"))

    mount_x0 = field_cx - MOUNT_SPACING / 2
    mount_x1 = field_cx + MOUNT_SPACING / 2
    els.append(fp_mounting_hole(mount_x0, field_cy, MOUNT_SIZE, MOUNT_DRILL))
    els.append(fp_mounting_hole(mount_x1, field_cy, MOUNT_SIZE, MOUNT_DRILL))

    shell_x0, shell_x1 = field_cx - SHELL_W / 2, field_cx + SHELL_W / 2
    shell_y0, shell_y1 = field_cy - SHELL_H / 2, field_cy + SHELL_H / 2
    els.append(fp_rect(shell_x0, shell_y0, shell_x1, shell_y1, "F.SilkS", 0.12))
    els.append(fp_rect(shell_x0 - 0.5, shell_y0 - 0.5, shell_x1 + 0.5, shell_y1 + 0.5, "F.CrtYd", 0.05))
    els.append(fp_rect(shell_x0, shell_y0, shell_x1, shell_y1, "F.Fab", 0.1))
    els.append(fp_text_ref(field_cx, shell_y0 - 1.5))
    els.append(fp_text_val(field_cx, shell_y1 + 1.5, "MDR68_Male_RightAngle"))
    els.append(f"\t(point\n\t\t(at {_fmt(x0)} {_fmt(row0_y)})\n\t\t(size 0.5)\n\t\t(layer \"F.Fab\")\n\t)")

    return assemble(
        "MDR68_Male_RightAngle",
        "68-pin Mini D Ribbon (MDR/SCSI-3), male, right-angle PCB mount, 1.27mm pitch "
        "2x34, two 4-40 clearance mounting/jackscrew holes at 57.9mm centres. Envelope "
        "and mounting spacing from MH Connectors 3700-0121-01; see hardware/README.md "
        "and the Task 6 report for sourcing and confidence per dimension.",
        "connector MDR SCSI-3 68pin right-angle PCB",
        els,
    )


# ---------------------------------------------------------------------------
# 2/3. MiniXLR_TA4M_Panel / TA5M_Panel (footprint models the real panel-mount part,
#    Switchcraft TB4M/TB5M -- see module docstring)
# ---------------------------------------------------------------------------
def build_minixlr(n_pins: int, modname: str, real_part: str) -> str:
    CONTACT_CIRCLE_D = 5.5
    BUSHING_D = 10.9
    PAD_SIZE, DRILL = 1.2, 0.7

    r = CONTACT_CIRCLE_D / 2
    els = []
    for i in range(n_pins):
        angle = math.radians(90 - i * (360 / n_pins))  # pin 1 at top, clockwise
        x, y = r * math.cos(angle), -r * math.sin(angle)
        els.append(fp_pad(str(i + 1), x, y, PAD_SIZE, DRILL, "rect" if i == 0 else "circle"))

    els.append(fp_circle(0, 0, BUSHING_D / 2, "F.SilkS", 0.12))
    els.append(fp_circle(0, 0, BUSHING_D / 2 + 0.5, "F.CrtYd", 0.05))
    els.append(fp_circle(0, 0, BUSHING_D / 2, "F.Fab", 0.1))
    els.append(fp_text_ref(0, -(BUSHING_D / 2 + 1.5)))
    els.append(fp_text_val(0, BUSHING_D / 2 + 1.5, modname))

    return assemble(
        modname,
        f"Switchcraft Tini-QG mini-XLR, {n_pins}-pin male, panel mount ({real_part} -- "
        f"the panel-mount member of the family; see hardware/README.md and the Task 6 "
        f"report for why this, not the cable-mount TA-series the symbol is named after, "
        f"is the real part modelled here). Bushing/cutout diameter and contact-circle "
        f"arrangement are approximate -- flagged in the report as needing re-verification "
        f"against the real part before panel machining.",
        f"connector XLR mini-XLR audio {n_pins}pin panel {real_part}",
        els,
    )


# ---------------------------------------------------------------------------
# 4. MiniDIN_4_Panel -- Same Sky/CUI MD-40SN, real dimensioned drawing (see docstring).
# ---------------------------------------------------------------------------
def build_minidin4() -> str:
    CONTACT_CIRCLE_D = 7.0  # mini-DIN family standard
    BUSHING_D = 10.0        # Same Sky MD-SN drawing, MD-40SN row
    PAD_SIZE, DRILL = 1.2, 0.7
    N_PINS = 4

    r = CONTACT_CIRCLE_D / 2
    els = []
    for i in range(N_PINS):
        angle = math.radians(90 - i * (360 / N_PINS))
        x, y = r * math.cos(angle), -r * math.sin(angle)
        els.append(fp_pad(str(i + 1), x, y, PAD_SIZE, DRILL, "rect" if i == 0 else "circle"))

    els.append(fp_circle(0, 0, BUSHING_D / 2, "F.SilkS", 0.12))
    els.append(fp_circle(0, 0, BUSHING_D / 2 + 0.5, "F.CrtYd", 0.05))
    els.append(fp_circle(0, 0, BUSHING_D / 2, "F.Fab", 0.1))
    els.append(fp_text_ref(0, -(BUSHING_D / 2 + 1.5)))
    els.append(fp_text_val(0, BUSHING_D / 2 + 1.5, "MiniDIN_4_Panel"))

    return assemble(
        "MiniDIN_4_Panel",
        "4-pin mini-DIN, panel mount, Same Sky (CUI) MD-40SN. Through-panel bushing "
        "diameter (10.0mm) from the manufacturer's own dimensioned drawing "
        "(sameskydevices.com/product/resource/md-sn.pdf); panel flange envelope "
        "38.5x15.25mm and 2x M? mounting ears at 30.0mm centres, 3.05mm dia, are on the "
        "same drawing but are panel-side hardware, not part of this PCB footprint (see "
        "hardware/README.md). Contact-circle diameter (7.0mm) is the mini-DIN family's "
        "own standard; exact per-pin angle is an even-spacing approximation, not "
        "measured off the drawing's own small pin diagram -- see the Task 6 report.",
        "connector mini-DIN power inlet panel MD-40SN",
        els,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        ("MDR68_Male_RightAngle.kicad_mod", build_mdr68(), 68),
        ("MiniXLR_TA4M_Panel.kicad_mod", build_minixlr(4, "MiniXLR_TA4M_Panel", "TB4M"), 4),
        ("MiniXLR_TA5M_Panel.kicad_mod", build_minixlr(5, "MiniXLR_TA5M_Panel", "TB5M"), 5),
        ("MiniDIN_4_Panel.kicad_mod", build_minidin4(), 4),
    ]
    for filename, text, n_pads in parts:
        modname = filename[: -len(".kicad_mod")]
        self_check(modname, text, n_pads)
        (OUT_DIR / filename).write_text(text)
        print(f"wrote {OUT_DIR / filename} ({len(text)} bytes), {n_pads} pads")


if __name__ == "__main__":
    main()
