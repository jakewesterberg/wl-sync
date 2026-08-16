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
IEC 61076-2-101 A-coded M12 receptacle.

AUDIT STEP 5 (2026-08-15) then DELETED M12A_5_Panel too, under ruling R1, leaving the
THREE footprints this file writes today. The part the design actually procures is Phoenix
Contact 1551833, which terminates in AWG 22-20 wire and mounts through the panel at
3-4 N.m -- it has no PCB tails, so no board-mount footprint of any dimensions could have
been right, and as built every cable insertion loaded PCB pads directly. The inlet is now
a stock 5-way 2.54 mm header. See the retired section 4 below, gen_breakout_power.py's own
FOOTPRINT_INLET_HEADER comment, and parametric-audit.md's R1 entry.

Both mini-DIN and M12 failed the same way, one dimensionally and one categorically: a
footprint that was correct about a part and wrong about the one being bought. That is why
`check_footprint_geometry.py` now reads these files back off disk and asserts their real
geometry against `hardware/datasheet-params.toml`, rather than trusting this generator.

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

- MDR68_Male_RightAngle: REBUILT 2026-08-15 (audit step 5) from **MH drawing 3700-0121-01
  rev 3.0**, dated 2015-03-27 -- a real primary document, replacing the previous version's
  distributor-corroborated figures. Every dimension now comes from `[mdr68]` in
  hardware/datasheet-params.toml rather than being re-typed here.

  The rebuild was not a refinement. The previous footprint drilled contacts at **0.5 mm
  against a required 0.85 mm** -- the pins do not fit, so the board could not be
  assembled -- and laid them out as 2 rows at 2.84 mm where the drawing shows **4 staggered
  rows at 1.905 mm**. Both were invisible to every netlist check on this board.

  One claim made about the old footprint during the audit was wrong and is corrected here:
  it was said to have "no mounting holes at all". It had two, at 2.79 mm on 57.9 mm centres,
  which against the drawing's 2.77 mm on 57.93 mm were very nearly right. The drill and the
  row arrangement were the defects; the mounting holes were not.

  What is NOT dimensioned by the drawing, and is therefore an ASSUMPTION carried in the
  footprint's own `descr`: which contact numbers land in which tail row. See
  MDR68_TAIL_ORDER below and hardware/breakout/d3-panel-thickness.md.
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
- M12A_5_Panel: RETIRED 2026-08-15 (ruling R1). Its per-dimension sourcing is preserved in
  git history and summarised in the retired section 4 below. The short version: the contact
  geometry was sourced well (IEC 61076-2-101 A-coding, cross-checked three ways), and it
  made no difference, because the procured part is not board-mount at all.

No footprint below drills a PCB mounting hole for the mini-XLR parts: they are PANEL-mount,
their own flange carrying the mechanical load, screwed to the sheet-metal panel rather than
to the board (Switchcraft's "specially designed flange permits close mount on crowded
panels"), so their PCB footprint only needs solder pads. MDR68 is different and DOES get two
mechanical mounting holes: it is a PCB-mount part whose own board anchor resists cable
insertion/withdrawal and jackscrew torque, and MH drawing rev 3.0 dimensions them directly
(2.77 mm diameter on 57.93 mm centres).
"""
from __future__ import annotations

import math
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kicad_pcb import _find_balanced, _split_children, _child_tag  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "lib" / "wl-sync.pretty"
PARAMS_PATH = Path(__file__).resolve().parent.parent / "datasheet-params.toml"

_PARAMS_CACHE: dict | None = None


def _params() -> dict:
    """hardware/datasheet-params.toml -- the primary-sources-only dimension file every
    parametric checker on this board already reads. Footprints read it too as of audit
    step 5, so a drawing revision is one edit there rather than a hunt through generators
    for re-typed numbers. That is not hypothetical: the MDR68 rebuild exists because the
    previous version's dimensions were typed in from distributor corroboration and one of
    them (a 0.5 mm drill against a required 0.85 mm) made the board unbuildable."""
    global _PARAMS_CACHE
    if _PARAMS_CACHE is None:
        with PARAMS_PATH.open("rb") as fh:
            _PARAMS_CACHE = tomllib.load(fh)
    return _PARAMS_CACHE


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
# WHICH CONTACT NUMBERS LAND IN WHICH TAIL ROW. This is the one thing MH drawing rev 3.0
# does NOT state. It is isolated here as a single named constant, with every candidate
# written out, so switching is a one-line edit and a regenerate rather than an archaeology
# exercise.
#
# CORRECTED 2026-08-16: this was first written as "scrambles 68 signals on a board that
# passes ERC and every test", which overstates it. All four candidate arrangements produce
# DIFFERENT HOLE PATTERNS, not merely different numbering -- the four tail rows carry X phases
# (0,1,0,1), (1,0,1,0), (0,1,1,0) and (1,0,0,1) on the 1.27 mm mating grid, and all four are
# distinct. A pin would have to move 1.27 mm laterally AND 1.905 mm in depth to seat in the
# wrong one, which is far beyond any contact compliance. So a wrong choice is caught when the
# connector will not physically seat -- a respin, which is expensive, but NOT the silent
# data-corruption hazard it was described as. It also means the answer is visible in any
# drawing, 3D model or photograph that shows the hole pattern, which is a much lower bar than
# a manufacturer confirmation.
#
# The unknown is smaller than it looks. A right-angle contact runs straight back from its
# mating position, so each contact's X is FIXED by the mating interface (2 rows x 34 at
# 1.27 mm); the 4-row stagger only redistributes DEPTH, and it exists solely so an 0.85 mm
# drill is manufacturable at a 1.27 mm mating pitch. Mating row A (contacts 1-34) reaches
# the board shallower than row B (35-68). That leaves four candidates, not twenty-four:
#
#   "A_odd,A_even,B_odd,B_even"   <- SELECTED. Rows ordered from the datum edge inward.
#   "A_even,A_odd,B_even,B_odd"
#   "A_odd,A_even,B_even,B_odd"
#   "A_even,A_odd,B_odd,B_even"
#
# Selected because it is the arrangement in which each mating row's contacts stay in mating
# order as they fan out (odd numbers to the nearer tail row, even to the farther), which is
# the ordinary construction for a staggered ribbon-style tail field.
#
# CONFIRM AGAINST MH's OWN DRAWING OR 3D MODEL BEFORE FAB and record the answer in
# hardware/breakout/d3-panel-thickness.md. Until then the footprint's own descr says
# PIN MAP UNVERIFIED, and check_footprint_geometry.py asserts that note is still there.
MDR68_TAIL_ORDER = "A_odd,A_even,B_odd,B_even"

# Parity is the 0-based slot index within a mating row: slot 0 holds contact 1 (ODD), slot 1
# holds contact 2 (EVEN). So "odd contacts" is parity 0, not 1 -- an inversion worth naming,
# because writing it the other way round is a silent 1.27 mm shift of half the field.
_A_ODD, _A_EVEN = ("A", 0), ("A", 1)
_B_ODD, _B_EVEN = ("B", 0), ("B", 1)

MDR68_TAIL_ORDERS = {
    "A_odd,A_even,B_odd,B_even": [_A_ODD, _A_EVEN, _B_ODD, _B_EVEN],
    "A_even,A_odd,B_even,B_odd": [_A_EVEN, _A_ODD, _B_EVEN, _B_ODD],
    "A_odd,A_even,B_even,B_odd": [_A_ODD, _A_EVEN, _B_EVEN, _B_ODD],
    "A_even,A_odd,B_odd,B_even": [_A_EVEN, _A_ODD, _B_ODD, _B_EVEN],
}


def build_mdr68() -> str:
    """68-pin MDR male, right-angle PCB mount, rebuilt from MH drawing 3700-0121-01 rev 3.0.

    Every dimension is read from `[mdr68]` in hardware/datasheet-params.toml. Nothing here
    is re-typed, so a drawing revision propagates by editing that file alone -- the same
    discipline every parametric checker on this board already follows.

    Geometry, and why it is self-consistent (worth recording, because it is what confirms
    the drawing has been read correctly rather than plausibly):

      68 contacts = 4 tail rows x 17. Within a row the X pitch is the drawing's
      `contact_stagger_mm` (2.54); adjacent rows are offset by `contact_pitch_mm` (1.27),
      i.e. half the stagger. The array therefore spans 16 * 2.54 + 1.27 = 41.91 mm, which
      is EXACTLY the drawing's own `contact_array_width_mm`. That agreement is the check
      that the 4-row reading is right.

      Row Y positions come from `row_offsets_from_edge_mm` = [5.10, 1.905, 3.81, 5.715],
      read as "first row 5.10 mm from the datum edge, then uniform 1.905 mm increments"
      (5.10, 7.005, 8.910, 10.815). The alternative reading -- four absolute offsets --
      would place two rows 0.615 mm apart with 0.85 mm holes, which is not a connector.

      Nearest neighbours are the diagonal ones: sqrt(1.27^2 + 1.905^2) = 2.289 mm. With a
      1.40 mm land that leaves 0.89 mm of copper, and with the 0.85 mm drill, 1.44 mm. The
      stagger is what buys that; 68 contacts at a flat 1.27 mm pitch could not be drilled
      0.85 mm at all, which is precisely why the as-built 2-row/0.5 mm version was both
      unbuildable and self-consistent-looking.

    Mounting holes: `mounting_hole_dia_mm` (2.77) at `mounting_hole_span_mm` (57.93),
    non-plated, centred on the contact array. Their Y is NOT dimensioned by the drawing and
    is placed at the array's own centre -- an assembly-fit detail, not a fab-stopper, and
    recorded as derived rather than read.
    """
    p = _params()["mdr68"]
    PITCH = p["contact_pitch_mm"]              # 1.27 -- mating interface pitch
    STAGGER = p["contact_stagger_mm"]          # 2.54 -- within-row tail pitch
    DRILL = p["contact_hole_dia_mm"]           # 0.85 -- THE fab-stopper
    PAD_SIZE = 1.40                            # 0.275 mm annular ring; 0.89 mm gap diagonally
    ROW_OFFSETS = p["row_offsets_from_edge_mm"]
    ROW0_Y, ROW_DY = ROW_OFFSETS[0], ROW_OFFSETS[1]
    MOUNT_DRILL = p["mounting_hole_dia_mm"]    # 2.77
    MOUNT_SPACING = p["mounting_hole_span_mm"] # 57.93
    SHELL_W = p["overall_width_mm"]            # 63.86
    SHELL_D = p["body_depth_mm"]               # 51.32
    N_PER_MATING_ROW = 34

    order = MDR68_TAIL_ORDERS[MDR68_TAIL_ORDER]

    els = []
    for row_index, (mating_row, parity) in enumerate(order):
        y = ROW0_Y + row_index * ROW_DY
        base = 0 if mating_row == "A" else N_PER_MATING_ROW
        # Contacts of this mating row whose 1-based position within the row has this parity.
        positions = [i for i in range(N_PER_MATING_ROW) if i % 2 == parity]
        for slot, i in enumerate(positions):
            num = str(base + i + 1)
            x = i * PITCH
            first = num == "1"
            els.append(fp_pad(num, x, y, PAD_SIZE, DRILL, "rect" if first else "circle"))
            del slot

    all_x = [i * PITCH for i in range(N_PER_MATING_ROW)]
    field_cx = (min(all_x) + max(all_x)) / 2
    field_cy = ROW0_Y + 1.5 * ROW_DY           # centre of the four rows

    els.append(fp_mounting_hole(field_cx - MOUNT_SPACING / 2, field_cy, MOUNT_DRILL, MOUNT_DRILL))
    els.append(fp_mounting_hole(field_cx + MOUNT_SPACING / 2, field_cy, MOUNT_DRILL, MOUNT_DRILL))

    # Body outline: the drawing's overall width and depth, with the mating face at Y=0 (the
    # datum the row offsets are measured from) and the body extending back over the board.
    shell_x0, shell_x1 = field_cx - SHELL_W / 2, field_cx + SHELL_W / 2
    shell_y0, shell_y1 = 0.0, SHELL_D
    els.append(fp_rect(shell_x0, shell_y0, shell_x1, shell_y1, "F.SilkS", 0.12))
    els.append(fp_rect(shell_x0 - 0.5, shell_y0 - 0.5, shell_x1 + 0.5, shell_y1 + 0.5, "F.CrtYd", 0.05))
    els.append(fp_rect(shell_x0, shell_y0, shell_x1, shell_y1, "F.Fab", 0.1))
    els.append(fp_text_ref(field_cx, shell_y0 - 1.5))
    els.append(fp_text_val(field_cx, shell_y1 + 1.5, "MDR68_Male_RightAngle"))
    els.append(f"\t(point\n\t\t(at {_fmt(0.0)} {_fmt(ROW0_Y)})\n\t\t(size 0.5)\n\t\t(layer \"F.Fab\")\n\t)")

    return assemble(
        "MDR68_Male_RightAngle",
        f"68-pin Mini D Ribbon (MDR/SCSI-3), male, right-angle PCB mount. Rebuilt from MH "
        f"drawing 3700-0121-01 rev 3.0 (2015-03-27): {DRILL}mm contact holes in 4 staggered "
        f"rows of 17 at {ROW_DY}mm, {STAGGER}mm within-row pitch on two {PITCH}mm phases, "
        f"array {p['contact_array_width_mm']}mm wide, two {MOUNT_DRILL}mm non-plated "
        f"mounting holes at {MOUNT_SPACING}mm centres. Replaces a version that drilled "
        f"0.5mm (pins do not fit) in 2 rows at 2.84mm. "
        f"PIN MAP UNVERIFIED: the drawing does not state which contact numbers land in "
        f"which tail row; this footprint assumes {MDR68_TAIL_ORDER} (see "
        f"gen_wl_sync_footprints.py's MDR68_TAIL_ORDER for the alternatives and "
        f"hardware/breakout/d3-panel-thickness.md for how to close it). Confirm against "
        f"MH's own drawing or 3D model before fab.",
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
# 4. (retired) M12A_5_Panel -- deleted 2026-08-15 under ruling R1.
#
# build_m12a_5pos() modelled Amphenol LTW M12A-05PFFP-SF8001, a board-mount M12
# receptacle, and its geometry was sourced carefully: the IEC 61076-2-101 A-coding contact
# layout was cross-checked three ways rather than guessed, which is exactly the discipline
# the mini-DIN failure taught. That work is not what was wrong with it.
#
# What was wrong is that the board does not use a board-mount M12 at all. The part actually
# procured is Phoenix Contact 1551833, which terminates in AWG 22-20 WIRE and mounts
# through the panel at 3-4 N.m -- it has no PCB tails, so no footprint of any dimensions
# could have been right. As built, cable insertion and coupling-nut torque loaded PCB pads
# directly.
#
# The inlet is now a stock 5-way 2.54 mm header where those flying leads land; see
# gen_breakout_power.py's own FOOTPRINT_INLET_HEADER comment. The M12 connector remains a
# purchased part in the order BOM -- it stopped being a board component, not a part.
#
# Kept as a comment rather than deleted silently because "this footprint was correct about
# a part and wrong about the one being bought" is the same shape as every other finding
# this audit closed, and the next person adding a panel connector should meet it here.
# The implementation is in git history.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. BNC_Dual_RA_Isolated -- Amphenol RF 031-6575 (finding F6)
# ---------------------------------------------------------------------------
def fp_pad_oval(number: str, x: float, y: float, w: float, h: float, drill: float) -> str:
    return (
        f'\t(pad "{number}" thru_hole oval\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)})\n"
        f"\t\t(size {_fmt(w)} {_fmt(h)})\n"
        f"\t\t(drill {_fmt(drill)})\n"
        f'\t\t(layers "*.Cu" "*.Mask")\n'
        f"\t\t(remove_unused_layers no)\n"
        f"\t)"
    )


def fp_pad_oval_unnumbered(x: float, y: float, w: float, h: float, drill: float) -> str:
    """A PLATED but unnumbered pad -- soldered, carries no net. This is KiCad's own
    convention for the 031-6575's two ground/retention terminals (read directly out of
    Connector_Coaxial.pretty before writing this), and it is the right one here: on the
    INDEPENDENTLY-isolated variant these lugs are mechanical retention, not a shared shell
    return, so giving them a pad number would invite a net that must not exist."""
    return (
        f'\t(pad "" thru_hole oval\n'
        f"\t\t(at {_fmt(x)} {_fmt(y)})\n"
        f"\t\t(size {_fmt(w)} {_fmt(h)})\n"
        f"\t\t(drill {_fmt(drill)})\n"
        f'\t\t(layers "*.Cu" "*.Mask")\n'
        f"\t\t(remove_unused_layers no)\n"
        f"\t)"
    )


def build_bnc_dual() -> str:
    """Dual-port right-angle isolated BNC jack, from Amphenol customer outline drawing
    31-6575 rev A (2013-04-18). Finding F6's replacement for BNC_PanelMountable_Vertical.

    WHY A CUSTOM FOOTPRINT WHEN KICAD SHIPS TWO CANDIDATES. Both stock footprints draw the
    identical 6-hole pattern -- 4 x 0.89 mm signal, 2 x 2.01 mm ground -- and both match the
    drawing on it. They differ in two ways that matter, and each stock part gets one of them
    right:

      BNC_Amphenol_031-6575_Horizontal has the correct BODY (14.40 x 36.20 mm, exactly this
      drawing's own body_width_mm/body_depth_mm) and the correct NET TOPOLOGY (pads 1-4
      independent), but places its two ground terminals at y = -8.89 and -8.79 -- a 0.1 mm
      asymmetry that is simply an error.

      BNC_Win_364A2x95_Horizontal is symmetric, but draws a 15.00 x 39.10 mm body (a
      different manufacturer's part) and numbers FOUR pads "3", commoning both shells with
      the ground terminals, because it models a FRONT-isolated connector.

    The audit's handoff recommended the Winchester geometry. That is wrong on this board in
    the way that matters most: the 031-6575 is INDEPENDENTLY isolated -- each shell isolated
    from the panel AND from its neighbour (bnc_dual_isolated.note) -- and ten channels here
    carry a separately-routed shield per connector (A_PD1_SHLD, A_MIC_SHLD, A_MISC1_SHLD
    and the rest), each landing on its own resistor position. Commoning the shells in pairs
    would defeat the deferred bulkhead-bonding scheme entirely and produce a perfectly clean
    netlist while doing it.

    So this footprint takes the Amphenol body and topology, and fixes the asymmetry.

    LAYOUT, and why the two centreline holes are the centre conductors. The ports are
    stacked VERTICALLY at 16.00 mm (port_pitch_mm), so in a top-down footprint the two
    barrels project onto the same point -- which is why the stock Amphenol footprint draws
    only one bayonet circle, and why that is correct rather than the "self-contradictory"
    single-body drawing it was read as. Both barrels being on the centreline puts both
    centre conductors there too, with the shells splayed left and right. KiCad's Amphenol
    and Winchester footprints were drawn independently from two manufacturers' drawings and
    AGREE on this, which is the corroboration standing in for a numbered callout.

      pad 1 = port A (lower) centre    (0, 0)
      pad 2 = port A shell             (-2.54, 0)
      pad 3 = port B (upper) centre    (0, -2.54)
      pad 4 = port B shell             (+2.54, 0)
      unnumbered ground/retention      (+-5.08, -8.89)

    Units 1 and 2 of the BNC_Dual_RA_Isolated symbol map to ports A and B respectively.

    PIN MAP UNVERIFIED, on the same terms as MDR68_TAIL_ORDER: which of the two ports is
    "upper" is an assumption, and swapping it is a net-reassignment fix rather than a
    re-machined panel -- the shells are independent either way, so no short can result.
    """
    p = _params()["bnc_dual_isolated"]
    SIG_DRILL = p["pcb_signal_hole_dia_mm"]      # 0.89
    GND_DRILL = p["pcb_ground_hole_dia_mm"]      # 2.01
    S = p["pcb_spacings_mm"]                     # [10.16, 6.35, 5.08, 2.54]
    GND_GAP, ROW_GAP, SHELL_X, PITCH = S[0], S[1], S[2], S[3]
    BODY_W = p["body_width_mm"]                  # 14.4
    BODY_D = p["body_depth_mm"]                  # 36.2
    SIG_PAD = 1.6
    GND_PAD_W, GND_PAD_H = 3.5, 7.0

    gnd_y = -(PITCH + ROW_GAP)                   # -2.54 - 6.35 = -8.89

    els = [
        fp_pad("1", 0.0, 0.0, SIG_PAD, SIG_DRILL, "rect"),
        fp_pad("2", -PITCH, 0.0, SIG_PAD, SIG_DRILL, "circle"),
        fp_pad("3", 0.0, -PITCH, SIG_PAD, SIG_DRILL, "circle"),
        fp_pad("4", PITCH, 0.0, SIG_PAD, SIG_DRILL, "circle"),
        fp_pad_oval_unnumbered(-GND_GAP / 2, gnd_y, GND_PAD_W, GND_PAD_H, GND_DRILL),
        fp_pad_oval_unnumbered(GND_GAP / 2, gnd_y, GND_PAD_W, GND_PAD_H, GND_DRILL),
    ]
    # All four of the drawing's own spacings must fall out of the geometry above rather than
    # being placed independently -- which is what makes the layout self-checking. The shells
    # sit at +-PITCH, so their span IS the drawing's third figure; assert it rather than
    # re-typing it, since a silent disagreement here is exactly the class of error that
    # produced this rebuild.
    assert abs(2 * PITCH - SHELL_X) < 1e-9, (
        f"shell-to-shell span {2 * PITCH} does not match the drawing's {SHELL_X} mm "
        f"(bnc_dual_isolated.pcb_spacings_mm[2])"
    )
    assert abs(-gnd_y - (PITCH + ROW_GAP)) < 1e-9, "ground row offset disagrees with the drawing"

    # Body: mating face forward (negative Y), PCB-side base at +1.05, exactly the drawing's
    # 14.40 x 36.20 envelope.
    x0, x1 = -BODY_W / 2, BODY_W / 2
    y1 = 1.05
    y0 = y1 - BODY_D
    els.append(fp_rect(x0, y0, x1, y1, "F.Fab", 0.1))
    els.append(fp_rect(x0, -14.45, x1, y1, "F.SilkS", 0.12))          # PCB-side base only
    els.append(fp_rect(x0 - 0.5, y0 - 0.5, x1 + 0.5, y1 + 0.5, "F.CrtYd", 0.05))
    # The bayonet: ONE circle, because both barrels are on the centreline, one above the
    # other. Drawn at the panel-hole diameter so the panel cutout is visible in layout.
    els.append(fp_circle(0.0, y0 + p["panel_hole_dia_mm"] / 2 + 1.0, p["panel_hole_dia_mm"] / 2, "F.Fab", 0.1))
    els.append(fp_text_ref(0.0, y1 + 1.5))
    els.append(fp_text_val(0.0, y1 + 3.2, "BNC_Dual_RA_Isolated"))

    return assemble(
        "BNC_Dual_RA_Isolated",
        f"Dual-port 50 ohm BNC jack, RIGHT ANGLE, each shell INDEPENDENTLY isolated from "
        f"the panel and from its neighbour. Amphenol RF 031-6575, customer outline drawing "
        f"31-6575 rev A (2013-04-18). Ports stacked vertically at {p['port_pitch_mm']}mm; "
        f"panel holes {p['panel_hole_dia_mm']}mm at the same pitch, {p['panel_thread']}. "
        f"PCB pattern: 4 x {SIG_DRILL}mm signal (pad 1 = port A centre, 2 = port A shell, "
        f"3 = port B centre, 4 = port B shell) and 2 x {GND_DRILL}mm plated, unnumbered "
        f"ground/retention terminals. Body {BODY_W} x {BODY_D}mm. Replaces "
        f"BNC_PanelMountable_Vertical, whose axis was perpendicular to the board (F6). "
        f"NOT KiCad's own BNC_Win_364A2x95_Horizontal, which commons both shells (it models "
        f"a FRONT-isolated part) and draws a different manufacturer's body. PIN MAP "
        f"UNVERIFIED: which port is upper is assumed, not read off a numbered callout -- "
        f"harmless (independent shells, so no short is possible) but confirm before fab.",
        "connector BNC coaxial dual right-angle isolated panel Amphenol 031-6575",
        els,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        ("MDR68_Male_RightAngle.kicad_mod", build_mdr68(), 68),
        ("MiniXLR_TA4M_Panel.kicad_mod", build_minixlr(4, "MiniXLR_TA4M_Panel", "TB4M"), 4),
        ("MiniXLR_TA5M_Panel.kicad_mod", build_minixlr(5, "MiniXLR_TA5M_Panel", "TB5M"), 5),
        ("BNC_Dual_RA_Isolated.kicad_mod", build_bnc_dual(), 4),
    ]
    for filename, text, n_pads in parts:
        modname = filename[: -len(".kicad_mod")]
        self_check(modname, text, n_pads)
        (OUT_DIR / filename).write_text(text)
        print(f"wrote {OUT_DIR / filename} ({len(text)} bytes), {n_pads} pads")

    # Ruling R1: M12A_5_Panel is deleted, not merely unused. Removing it here rather than
    # only stopping writing it means a stale copy on someone's disk cannot silently keep
    # resolving -- the same reasoning that made this generator delete the two mini-DIN
    # footprints outright at Task 7 fix round 2 rather than leaving them orphaned.
    for stale in ("M12A_5_Panel.kicad_mod",):
        victim = OUT_DIR / stale
        if victim.exists():
            victim.unlink()
            print(f"deleted {victim} (ruling R1 -- panel-mount part, not a board component)")


if __name__ == "__main__":
    main()
