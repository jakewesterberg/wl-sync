"""Generator for hardware/lib/wl-sync.pretty/*.kicad_mod -- the custom footprints Task 6
needs that aren't in any KiCad stock .pretty library: the 68-pin MDR male (right-angle PCB
mount), mini-XLR TA4M and TA5M (panel mount), and the 4-pin mini-DIN (panel mount) -- four
originally. The fifth and sixth symbols Task 6 adds (the ACCES I/O DB37 connector and the
Raspberry Pi 5 GPIO header) reuse STOCK footprints instead: DB37 is a mechanically
standardised D-sub shell (KiCad's own Connector_Dsub.pretty already has it, real
dimensions, not "a similar part"), and the Pi header reuses the same bare 2x20 2.54mm THT
footprint hardware/gen/gen_mule.py's own Pi-side header already uses (FOOTPRINT_IDC40 in
that file) -- the real Raspberry Pi GPIO header is exactly that part.

Task 7 fix round 1 (task-7-report.md, "Fix round 1") added a fifth custom footprint,
MiniDIN_5_Panel -- the +5V-rail fix's 5-pin inlet connector -- via a generalized
build_minidin(n_pins, ...) that also now produces MiniDIN_4_Panel (its `descr` text is
therefore regenerated too, not byte-identical to the original hand-written version; the
only material change is recording the MD-40SN/MD-50SN discontinued-part fact pulled from
the manufacturer's datasheet while researching the 5-pin part -- see MiniDIN_5_Panel's own
note below and hardware/README.md).

Task 7 fix round 2 (task-7-report.md, "Fix round 2") DELETED both mini-DIN footprints and
build_minidin() entirely, replacing the inlet with M12A_5_Panel. Reviewer finding: the
Same Sky MD-SN datasheet's own small per-pin-count diagrams show a CLUSTERED contact
layout for both MD-40SN and MD-50SN, not the even ring build_minidin() drew -- a real
topology defect that would very likely fail to mate as fabbed. Rather than re-drawing
against a part already confirmed discontinued (MD-40SN rev 1.06 2022-09-26, MD-50SN rev
1.07 2023-10-04 -- both verified word-for-word against the manufacturer PDF, see the old
docstring text preserved in git history), the inlet was moved to a currently-stocked,
keyed, locking, PCB-mount panel connector: Amphenol LTW M12A-05PFFP-SF8001, a 5-position
IEC 61076-2-101 A-coded M12 receptacle. See M12A_5_Panel's own note below and
hardware/README.md's "Custom connector footprints" section for the full selection
rationale and per-dimension sourcing.

Written as raw .kicad_mod text (there is no stock footprint to extract from), following
the exact low-level shape kicad_pcb.py's extract_footprint()/Board.place() expect to read
back -- confirmed against a real pcbnew/kicad-footprint-generator-authored file
(Connector_Dsub.pretty's own DSUB-37 Housed variant, read directly while designing this):
no `uuid` on any child element and no `net` on any pad at the LIBRARY level (Board.place()
adds both -- a fresh uuid per graphic/pad, and net only for pads present in its own
pad_nets dict -- when an instance is actually placed; a library master carrying its own
uuid/net would leave a stray, wrong second one behind, or a net no instance's schematic
agrees with).

Sourcing and confidence, stated once here rather than scattered per symbol -- and again,
in full and for real, in hardware/README.md's "Custom connector footprints" section, which
is the canonical in-repo copy of this table. (This docstring is the working notes it was
built from, not an alternate source of truth -- keep the two in sync if either changes.)

- MDR68_Male_RightAngle: overall envelope (63.86 x 16.7 x 12.5mm) from MH Connectors
  3700-0121-01, a real, currently-distributed right-angle male 68-pin MDR/SCSI-3
  connector (corroborated across 5+ independent distributor listings). Contact pitch
  1.27mm and 2-row/34-per-row layout are the MDR format's own defining dimensions (3M's
  own "102 Series" datasheet literally titles the family ".050in Boardmount... Connectors"
  -- 0.050in = 1.27mm -- and every MDR68 datasheet found agrees on 2x34); row spacing
  2.84mm and jackscrew-to-jackscrew spacing 57.9mm are figures that appeared consistently
  across general MDR-68 dimensional references but were not confirmed against one single
  primary-source CAD drawing with a numeric callout for THIS specific dimension --
  flagged in hardware/README.md as the one MDR68 number worth re-checking against the
  exact MPN Task 0 procures, same discipline that task's own brief already calls for.
- MiniXLR_TA4M/TA5M_Panel: panel/chassis thickness (6.35mm max) is Switchcraft's own
  published number for the TB-series panel-mount receptacle -- the real part this
  footprint models (TA4M/TA5M themselves are CABLE-mount only and TA4M is obsolete,
  independently confirmed against DigiKey, Switchcraft and Farnell; see hardware/README.md
  for why the footprint is the TB-series and the symbol keeps the spec's own TA4M/TA5M
  name). Panel bushing/cutout diameter (~10.9mm) is corroborated from the TA-series
  housing diameter (0.413in/10.5mm, Switchcraft's own catalog) but not confirmed against a
  primary numeric TB-series drawing -- the least-certain single dimension on this board's
  panel-mount parts, flagged in hardware/README.md for re-verification before panel
  machining. Internal contact-circle arrangement is a clean, evenly-spaced approximation,
  not measured off the real part -- and NOT merely a tolerance risk: real keyed circular
  connectors often cluster contacts in an arc rather than spacing them evenly around the
  full circle, so this could be a topology error (wrong contact layout, not just an
  angular offset) rather than a dimensional one. See hardware/README.md's "Contact
  arrangement" note -- verify against a physical sample before fab, don't just tweak the
  angle.
- M12A_5_Panel (Task 7 fix round 2, replacing MiniDIN_4_Panel/MiniDIN_5_Panel): the real
  part is Amphenol LTW M12A-05PFFP-SF8001 -- a 5-position, IEC 61076-2-101 A-coded M12
  connector, female sockets, PCB solder-pin termination, front-fastened panel mount,
  M12x1 threaded coupling (locking), IP68/IP69K. Confirmed ACTIVE/current-production with
  811 units in real stock at DigiKey -- checked directly against DigiKey's own product
  page, not assumed (Mouser's own page for this exact SKU could not be fetched directly
  in this environment; TME and OnlineComponents.com independently corroborate real stock
  of the same part number via search results). A-coding is
  a physical keying feature (this is the entire reason "coding" exists in the M12 spec):
  the connector cannot mate rotated. The screw-thread coupling is a true lock, not
  friction -- the panel this part sits on is rack-mounted and slides in and out.
  Contact geometry (four contacts on a 5.0mm-diameter pitch circle at 90 degree spacing,
  rotated 45 degrees off the keyway reference, plus a fifth contact at the exact centre;
  individual contact diameter 1.0mm nominal) is the IEC 61076-2-101 A-coding STANDARD
  geometry, not a per-manufacturer or per-footprint guess -- confirmed three ways: (1) the
  IEC 61076-2-101:2012 standard document itself (Table 1: A-coding, 5-way style, 5
  contacts -> 60V/4A, exactly matching every A-coded 5-position part found in this
  research, confirming this part is a genuine member of that standardised class); (2) a
  real, current Bulgin M12-series datasheet's own dimensioned "5 pole 'A' Code Front View"
  drawing (Ø5 contact circle, Ø1.0+-0.03 contact diameter, 45 degree +-30' angular
  reference -- a different manufacturer's real part in the SAME standardised class, since
  A-coding's whole purpose is cross-manufacturer interoperability on one fixed geometry);
  (3) an independent secondary description of the same standard ("four pins at the
  corners of a square... pin 5 in the centre"). This is a fundamentally different kind of
  claim than the mini-DIN's even-ring guess: A-coding is a real interoperability standard
  every A-coded M12 part must share, not an assumption about one manufacturer's unpublished
  layout. What is NOT independently confirmed against Amphenol LTW's own drawing
  specifically (JS-gated download, blocked by every automated fetch tried -- a real, human-
  operable 2D/3D CAD download exists on the product page and via DigiKey's own EDA/CAD
  models tab, satisfying "a real drawing is publicly available", but this generator could
  not extract its raw numbers): the exact panel cutout diameter (modelled here at 12.5mm,
  M12x1 thread + standard clearance, consistent across every M12 panel-mount datasheet
  checked) and the connector's own external shell reference diameter (modelled at 14.5mm,
  Bulgin's own M12-series housing dimension, repeated across several related drawings in
  the same datasheet). Both flagged in hardware/README.md for confirmation against
  Amphenol LTW's own drawing or a physical sample before panel machining -- same
  discipline as every other Low-confidence dimension in this file, not asserted with false
  certainty. The pin-1-through-4 ROTATIONAL numbering (which of the four symmetric corners
  is "1" vs "2" vs "3" vs "4") follows this file's own top-ish/clockwise convention
  (see build_minixlr()) rather than an independently confirmed manufacturer numbering --
  unlike the contact ARRANGEMENT (shape), this is safe to get wrong even before physical
  verification: the shell's own keying still prevents any rotated/wrong mating, and a
  mislabeled corner is a trivial net-reassignment fix, not a re-machined panel.

No footprint below drills a PCB mounting hole for the mini-XLR/M12 parts: both are
PANEL-mount (their own flange or threaded coupling nut carries the mechanical load,
screwed to the sheet-metal panel, not to the board -- confirmed for M12A_5_Panel from
Amphenol LTW's own "Panel Mount, Through Hole" + "Threaded" fastening-type fields, and for
the mini-XLR from Switchcraft's "specially designed flange permits close mount on crowded
panels" wording),
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
        "and mounting spacing from MH Connectors 3700-0121-01; see hardware/README.md's "
        "per-dimension sourcing table for confidence per dimension.",
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
        f"the panel-mount member of the family; see hardware/README.md for why this, not "
        f"the cable-mount TA-series the symbol is named after, is the real part modelled "
        f"here). Bushing/cutout diameter is approximate -- flagged in hardware/README.md "
        f"for re-verification against the real part before panel machining. Contact-circle "
        f"arrangement is an even-spacing approximation that may be a topology error, not "
        f"just a tolerance one -- see hardware/README.md; verify against a physical "
        f"sample before fab.",
        f"connector XLR mini-XLR audio {n_pins}pin panel {real_part}",
        els,
    )


# ---------------------------------------------------------------------------
# 4. M12A_5_Panel -- Amphenol LTW M12A-05PFFP-SF8001, real current-production part
#    (Task 7 fix round 2, task-7-report.md's "Fix round 2" -- replaces the deleted
#    MiniDIN_4_Panel/MiniDIN_5_Panel/build_minidin(), see module docstring and
#    hardware/README.md for the full selection rationale). Deliberately NOT
#    parameterized by pin count/coding the way build_minixlr()/the old build_minidin()
#    were: this generator has sourced numbers for exactly one real, chosen part, and
#    generalizing beyond that evidence (e.g. a would-be build_m12(n_pins, coding, ...))
#    would silently invite the same "plausible but unsourced" gap this whole fix exists
#    to close -- same one-real-part-at-a-time discipline build_mdr68() already follows.
# ---------------------------------------------------------------------------
def build_m12a_5pos() -> str:
    # IEC 61076-2-101 A-coding standard geometry (NOT a per-part guess -- A-coding's
    # entire purpose is cross-manufacturer mating interoperability on one fixed
    # contact layout, confirmed against the IEC 61076-2-101:2012 standard document
    # itself -- Table 1: A-coding, 5-way style, 5 contacts -> 60V/4A, matching this
    # exact part -- and independently corroborated by a real, current Bulgin M12-series
    # datasheet's own dimensioned "5 pole 'A' Code Front View" drawing: Ø5 contact
    # circle, Ø1.0+-0.03 contact diameter, 45 degree +-30' angular reference. See
    # hardware/README.md for the full three-way cross-check.):
    PCD = 5.0            # pitch circle diameter for the 4 outer contacts
    PIN_ANGLE0 = 45.0    # contact 1 sits 45 deg off the keyway reference (Bulgin's own
    # "45+-30'" callout); contacts 2-4 follow at 90 deg increments, clockwise -- a
    # diamond (NE/SE/SW/NW), not an N/E/S/W square. Pin 5 is the exact geometric centre.
    PAD_SIZE, DRILL = 2.0, 1.3   # real contact is 1.0mm dia nominal (IEC 61076-2-101
    # Sec.1 / Bulgin's own Ø1.0+-0.03); enlarged for a comfortable hand-solder joint,
    # same discipline as every other connector in this file.
    PANEL_CUTOUT = 12.5  # M12x1 thread + standard clearance -- consistent across every
    # M12 panel-mount datasheet checked; NOT independently confirmed against Amphenol
    # LTW's own drawing specifically (JS-gated download blocked every automated fetch
    # tried) -- flagged in hardware/README.md, verify before panel machining.

    r = PCD / 2
    els = []
    # Contacts 1-4: NE, SE, SW, NW, clockwise -- this file's own top-ish/clockwise pin-1
    # convention (see build_minixlr()), adapted for a 4-fold-symmetric diamond that has
    # no point due north. This ROTATIONAL numbering (which corner is "1") is NOT
    # independently confirmed against the manufacturer's own printed pin marking -- but
    # unlike the mini-DIN's contact-ARRANGEMENT error, getting this label wrong is safe:
    # the shell's own A-coding keying still makes a rotated/wrong mating physically
    # impossible, and a mislabeled corner is a net-reassignment fix, not a re-machined
    # panel. See hardware/README.md.
    for i in range(4):
        angle = math.radians(PIN_ANGLE0 - i * 90)
        x, y = r * math.cos(angle), -r * math.sin(angle)
        els.append(fp_pad(str(i + 1), x, y, PAD_SIZE, DRILL, "rect" if i == 0 else "circle"))
    els.append(fp_pad("5", 0, 0, PAD_SIZE, DRILL, "circle"))  # centre contact

    els.append(fp_circle(0, 0, PANEL_CUTOUT / 2, "F.SilkS", 0.12))
    els.append(fp_circle(0, 0, PANEL_CUTOUT / 2 + 0.5, "F.CrtYd", 0.05))
    els.append(fp_circle(0, 0, PANEL_CUTOUT / 2, "F.Fab", 0.1))
    els.append(fp_text_ref(0, -(PANEL_CUTOUT / 2 + 1.5)))
    els.append(fp_text_val(0, PANEL_CUTOUT / 2 + 1.5, "M12A_5_Panel"))

    return assemble(
        "M12A_5_Panel",
        "5-position M12 connector, IEC 61076-2-101 A-coded (keyed -- cannot mate "
        "rotated), screw-locking M12x1 coupling, panel mount, PCB solder-pin "
        "termination. Real part: Amphenol LTW M12A-05PFFP-SF8001 (female sockets, "
        "front-fastened, IP68/IP69K) -- confirmed active/current-production with real "
        "stock at DigiKey, 811 units, checked directly (see hardware/README.md). "
        "Contact geometry (Ø5.0mm pitch circle for 4 outer contacts at 90deg spacing, "
        "45deg off the keyway reference, 1 contact at centre) is the IEC 61076-2-101 A-coding STANDARD "
        "geometry, cross-confirmed against the standard itself and a real Bulgin M12 "
        "datasheet, not modelled as an even ring the way the mini-DIN this replaces was. "
        "Panel cutout (12.5mm, M12x1 thread + standard clearance) is consistent across "
        "every M12 panel-mount datasheet checked but not independently confirmed against "
        "this specific part's own drawing -- verify before panel machining, see "
        "hardware/README.md.",
        "connector M12 power inlet panel locking keyed A-coded",
        els,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        ("MDR68_Male_RightAngle.kicad_mod", build_mdr68(), 68),
        ("MiniXLR_TA4M_Panel.kicad_mod", build_minixlr(4, "MiniXLR_TA4M_Panel", "TB4M"), 4),
        ("MiniXLR_TA5M_Panel.kicad_mod", build_minixlr(5, "MiniXLR_TA5M_Panel", "TB5M"), 5),
        ("M12A_5_Panel.kicad_mod", build_m12a_5pos(), 5),
    ]
    for filename, text, n_pads in parts:
        modname = filename[: -len(".kicad_mod")]
        self_check(modname, text, n_pads)
        (OUT_DIR / filename).write_text(text)
        print(f"wrote {OUT_DIR / filename} ({len(text)} bytes), {n_pads} pads")


if __name__ == "__main__":
    main()
