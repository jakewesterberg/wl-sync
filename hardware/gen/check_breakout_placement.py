"""Check for hardware/breakout/breakout.kicad_pcb -- the breakout's generated placement
(layout task L4, plan Task 14 Step 3).

    python3 hardware/gen/check_breakout_placement.py [pcb_path] [contract_path]

What it asserts, on the board FILE as committed (it re-reads every footprint's `(at ...)` and
every pad out of the text, so it holds for a file pcbnew has since saved, and for a routed
one):

1. Every reference in hardware/breakout/netlist-contract.json is placed exactly once, and
   nothing else is placed except the two mechanical holes named in MECHANICAL.
2. Every pad carries the net the contract gives its pin, and no other.
3. Each panel connector sits at its panel position (PANEL below) within PANEL_TOL_MM, faces
   outward, and the connectors of each face are in the floorplan's order.
4. No part is in the wrong domain region. This is the check that matters most: a part on
   the wrong side of a barrier defeats the barrier with a clean netlist and a clean DRC.
5. No copper is in any slot or in any barrier's copper-free band, and the slots themselves
   are where the constraints put them, at the widths they rule.
6. The keep-outs that guard the barriers exist as rule areas on all four copper layers.
7. The project file carries the net classes, so that DRC is run against them.

HOW A PART'S DOMAIN IS DERIVED (check 4). The netlist contract has no "domain" field. It has
something better: which ground and which rails each part's pins are on. constraints.md §2
gives the four domains by their ground net and rails (SEEDS below). From those:

  - a part whose pins touch the seed nets of exactly ONE domain is in that domain;
  - every net such a part touches is thereby a net of that domain;
  - a part with no seed pin at all (a series resistor between two signal nets) takes the
    domain of its nets, if they agree; and so on to a fixed point.

Three kinds of part do not come out with one domain, and each kind is DECLARED here, from
the constraint that makes it legitimate, rather than inferred:

  - STRADDLERS: the parts constraints.md B-3, B-4, B-6 and B-13 put across a galvanic
    barrier. Each has a pin-to-side map (B-10, B-12, B-13), every pad is checked against
    it, and the map itself is cross-checked against the nets wherever the nets decide.
    Straddlers are kept out of the propagation above: an optocoupler's only seed pins are
    on its isolated side, and letting it lend that domain to its LED nets is exactly the
    error this check exists to catch.
  - DECLARED_HOME: parts that legitimately carry a pin of another domain while sitting
    wholly in one region (the Intan-side INA105s, B-17; the threshold DAC, G-12; the
    comparator feedback parts, K-26; and the like).
  - the star tie NT1, which is on the AGND/DGND line by definition (G-1, G-2).

What is left over is the set of nets that cross between two domains. It is compared with
EXPECTED_CROSSINGS, which is constraints.md G-11 and B-17 written out, so a net that
crosses a boundary without a constraint behind it fails the check.

Coordinates. The board file's frame is KiCad's: x to the right, y DOWN. floorplan.md's frame
is x to the right, Y from the front edge (0) to the back edge (240), drawn with the back at
the top. The two agree on x, and board y = 240 - floorplan Y, so pcbnew shows the board as
floorplan.md §7 draws it, from above, with nothing mirrored. Every number below is in the
BOARD frame; `fy()` converts where a floorplan figure is quoted.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kicad_pcb import (  # noqa: E402
    _child_at, _child_tag, _find_balanced, _split_children, place_point,
)

REPO = Path(__file__).resolve().parents[2]
PCB_PATH = REPO / "hardware" / "breakout" / "breakout.kicad_pcb"
CONTRACT_PATH = REPO / "hardware" / "breakout" / "netlist-contract.json"
PRO_PATH = REPO / "hardware" / "breakout" / "breakout.kicad_pro"
DRU_PATH = REPO / "hardware" / "breakout" / "breakout.kicad_dru"

BOARD_W, BOARD_H = 430.0, 240.0  # floorplan.md §1; constraints S-2 (nominal)


def fy(floorplan_y: float) -> float:
    """floorplan.md's Y (0 at the front edge) as a board-file y (0 at the back edge)."""
    return BOARD_H - floorplan_y


FRONT_Y, BACK_Y = fy(0.0), fy(BOARD_H)  # 240 and 0 in the board frame

# ---------------------------------------------------------------------------
# The floorplan, as geometry. floorplan.md §3.1 (regions), §3.2 (barriers), §3.3 (split).
# Rectangles are (x0, y0, x1, y1) in the board frame.
# ---------------------------------------------------------------------------
NI_X = 70.0           # the NI island's right-hand side, and its slotted line
NI_BACK_Y = fy(140)   # 100: the NI island's back
INTAN_X0, INTAN_X1 = 112.0, 238.0
INTAN_LINE_Y = fy(75)  # 165: the Intan island's back, one straight line with the T on it
SPLIT_X = 170.0       # the AGND/DGND split, from the back edge to the T

REGIONS: dict[str, list[tuple[float, float, float, float]]] = {
    "NI": [(0.0, NI_BACK_Y, NI_X, FRONT_Y)],
    "INTAN": [(INTAN_X0, INTAN_LINE_Y, INTAN_X1, FRONT_Y)],
    "DGND": [
        (0.0, BACK_Y, SPLIT_X, NI_BACK_Y),
        (NI_X, NI_BACK_Y, SPLIT_X, INTAN_LINE_Y),
        (NI_X, INTAN_LINE_Y, INTAN_X0, FRONT_Y),
    ],
    "AGND": [
        (SPLIT_X, BACK_Y, BOARD_W, INTAN_LINE_Y),
        (INTAN_X1, INTAN_LINE_Y, BOARD_W, FRONT_Y),
    ],
}

BAND_MM = 2.5        # B-7: the copper-free band along every barrier line
SLOT_MM = 2.5        # B-3: the slot under each straddler, at least, except the two below
SLOT_OPTO_MM = 2.0   # B-11, ruling R6 (2026-11-20): under the eight SOIC-16 optocouplers
SLOT_C162_MM = 1.5   # B-14, ruling R5 (2026-11-20): under C162 only
KEEPOUT_MARGIN_MM = 1.0  # B-8: the keep-out beyond each cutout
SPLIT_GAP_MM = 1.0   # G-9: the AGND/DGND gap on L2 and L3
_H = BAND_MM / 2

# Each barrier segment: (name, band rectangle, whether tracks may cross it).
# B-17: the AGND-facing part of the Intan line is crossed by the INA105s' 16 traces.
BANDS: list[tuple[str, tuple[float, float, float, float], bool]] = [
    ("ni_band_side", (NI_X - _H, NI_BACK_Y - _H, NI_X + _H, FRONT_Y), False),
    ("ni_band_back", (0.0, NI_BACK_Y - _H, NI_X + _H, NI_BACK_Y + _H), False),
    ("intan_band_dgnd", (INTAN_X0 - _H, INTAN_LINE_Y - _H, SPLIT_X, INTAN_LINE_Y + _H), False),
    ("intan_band_agnd", (SPLIT_X, INTAN_LINE_Y - _H, INTAN_X1 + _H, INTAN_LINE_Y + _H), True),
    ("intan_band_left", (INTAN_X0 - _H, INTAN_LINE_Y - _H, INTAN_X0 + _H, FRONT_Y), False),
    ("intan_band_right", (INTAN_X1 - _H, INTAN_LINE_Y - _H, INTAN_X1 + _H, FRONT_Y), False),
]
SPLIT_STRIP = (SPLIT_X - SPLIT_GAP_MM / 2, BACK_Y, SPLIT_X + SPLIT_GAP_MM / 2, INTAN_LINE_Y - _H)
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")

# ---------------------------------------------------------------------------
# Domains. constraints.md §2, the table "Their nets, as the netlist has them".
# ---------------------------------------------------------------------------
SEEDS: dict[str, tuple[str, ...]] = {
    "AGND": ("AGND", "+12V", "-12V"),
    "DGND": ("DGND", "+5V", "+3V3"),
    "NI": ("NI_GND", "NI_5V"),
    "INTAN": (
        "INTAN_GND", "ISO_P12", "ISO_N12", "ISO_5V",
        "ISO_P15_RAW", "ISO_N15_RAW", "ISO_P15_FILT", "ISO_N15_FILT",
    ),
}
SEED_DOMAIN = {net: dom for dom, nets in SEEDS.items() for net in nets}


def _opto(isolated: str) -> dict[str, str]:
    """B-10: pins 1 to 8 on the DGND side, pins 9 to 16 on the isolated side."""
    return {str(p): ("DGND" if p <= 8 else isolated) for p in range(1, 17)}


# Every part that sits ACROSS a galvanic barrier, with the side each pin is on.
# B-3, B-4, B-6 (the list), B-10 (the optocouplers), B-12 (the converters), B-13 (C162).
STRADDLERS: dict[str, dict[str, str]] = {
    **{f"U{n}": _opto("NI") for n in range(56, 62)},
    "U62": {"1": "DGND", "2": "DGND", "4": "NI", "6": "NI"},
    "U64": _opto("INTAN"),
    "U65": _opto("INTAN"),
    "U63": {"1": "DGND", "2": "DGND", "4": "INTAN", "6": "INTAN"},
    # B-12: pins 1 and 3 primary; pin 2 is no-connect, sits at the secondary end, and is
    # treated as Intan-side.
    "U2": {"1": "DGND", "3": "DGND", "2": "INTAN", "4": "INTAN", "5": "INTAN", "6": "INTAN"},
    "C162": {"1": "DGND", "2": "INTAN"},
}
# The barrier line each straddler is on: ("x" or "y", coordinate).
STRADDLER_LINE: dict[str, tuple[str, float]] = {
    **{f"U{n}": ("x", NI_X) for n in range(56, 63)},
    **{r: ("y", INTAN_LINE_Y) for r in ("U64", "U65", "U63", "U2", "C162")},
}
# The parts under which the slot has a RULED width, which the check holds it to exactly.
RULED_SLOT_MM: dict[str, tuple[float, str]] = {
    **{f"U{n}": (SLOT_OPTO_MM, "B-11, R6") for n in (56, 57, 58, 59, 60, 61, 64, 65)},
    "C162": (SLOT_C162_MM, "B-14, R5"),
}

# Parts that sit wholly in one region while carrying a pin of another domain, or whose
# nets alone do not decide. reference -> (region, the constraint that says so).
DECLARED_HOME: dict[str, tuple[str, str]] = {
    **{f"U{n}": ("INTAN", "B-17: the Intan-side INA105s are on the Intan island, with "
                 "MUX_OUTn and AGND reaching pins 3 and 2") for n in range(39, 54, 2)},
    "U55": ("AGND", "G-12: VSS is AGND although VDD is +3V3"),
    "C120": ("AGND", "G-12: directly across U55's VDD and VSS"),
    "C9": ("DGND", "P-3: at U2's primary pins, +12V to DGND"),
    "R112": ("AGND", "K-26: hysteresis network at the comparator pins"),
    "R115": ("AGND", "K-26"),
    "R118": ("AGND", "K-26"),
    "R120": ("AGND", "K-26 (DNP fourth channel)"),
    "R213": ("AGND", "K-26; G-13: U76's passives at U76"),
    "R214": ("AGND", "K-26"),
    "R218": ("AGND", "K-26; G-13"),
    "R219": ("AGND", "K-26"),
    "R223": ("AGND", "K-21: R221 to R223 beside J2"),
    "F4": ("DGND", "[engineering judgment], after P-2 and G-14: the +5V chain is kept "
                   "together on the DGND side of NT1; no row says which side F4 is on"),
    "D44": ("DGND", "G-24: the fan pair stays out of the AGND region"),
}
# A seed-net pad that is, by a named constraint, in another domain's region.
FOREIGN_SEED_PADS: dict[tuple[str, str], str] = {
    **{(f"U{n}", "2"): "B-17" for n in range(39, 54, 2)},
    ("U55", "1"): "G-12", ("C120", "1"): "G-12",
    ("U2", "3"): "P-3", ("C9", "1"): "P-3",
}
TIE_ON_SPLIT = "NT1"  # G-1, G-2

# The nets that cross between two domains' regions, and why each may.
EXPECTED_CROSSINGS: dict[str, str] = {
    **{n: "G-11: comparator output, LM339 on AGND, loads on DGND"
       for n in ("PD1_COMP", "PD2_COMP", "ACC_TRIG", "CAM_FRAME_EYE", "CAM_FRAME_BEH")},
    "A_MISC1_COMP": "G-11 as amended 2026-11-20: the DNP fourth comparator channel (D-2)",
    "I2C_SCL": "G-11: to U55", "I2C_SDA": "G-11: to U55",
    **{f"MUX{m}_A{a}": "G-11: mux address line from U67/U68"
       for m in range(1, 9) for a in range(4)},
    "CAM_FRAME_EYE_CLAMP": "G-11: frame-time input into U76",
    "CAM_FRAME_BEH_CLAMP": "G-11: frame-time input into U76",
    "CAM_FRAME_THR": "G-11",
    "PD1_COMP_PFI_BUF": "G-11 (PD1_COMP_PFI to J2 pin 38); the net that crosses is the "
                        "buffer's output, because R223 is beside J2 (K-21)",
    **{f"MUX_OUT{n}": "B-17: one of the 16 traces to the Intan-side INA105s"
       for n in range(1, 9)},
    # G-11 as amended 2026-11-20. J1's two ground pins are both AGND (G-15), so J1 is on
    # the AGND side of the star and the digital side's supply crosses beside NT1.
    "P5_RAW": "G-11 as amended: J1 pin 3 to F4, crossing at NT1 (G-15, P-2)",
    "FAN_12V_RAW": "G-11 as amended: F1 to D44, crossing at NT1 (G-24, P-2)",
}

# ---------------------------------------------------------------------------
# Panel positions. floorplan.md §2 gives the ORDER on each face and each item's width, and
# panel-elevations.md §2 the 18.0 mm BNC pitch; no source gives an X. The centres below are
# this placement's, recorded in hardware/breakout/placement.md §2, and M-2 makes them the
# panel's: "the panel is drawn from the placed footprints".
# ref -> (face, centre X along the edge)
# ---------------------------------------------------------------------------
PANEL_TOL_MM = 0.1  # constraints M-2

PANEL: dict[str, tuple[str, float]] = {
    # front, left to right (floorplan §2.1)
    "J45": ("front", 34.5), "J5": ("front", 81.0),
    "J46": ("front", 121.0), "J48": ("front", 139.0), "J50": ("front", 157.0),
    "J37": ("front", 175.0), "J39": ("front", 193.0), "J41": ("front", 211.0),
    "J43": ("front", 229.0), "J36": ("front", 275.0),
    # back, left to right (floorplan §2.2)
    "J13": ("back", 9.5), "J15": ("back", 27.5), "J17": ("back", 45.5), "J6": ("back", 63.5),
    "J3": ("back", 107.0), "J57": ("back", 182.0), "J2": ("back", 226.0),
    "J19": ("back", 298.0),
    "J29": ("back", 346.0), "J26": ("back", 364.0), "J24": ("back", 382.0),
    "J22": ("back", 400.0), "J20": ("back", 418.0),
}
FRONT_ORDER = ["J45", "J5", "J4", "J46", "J48", "J50", "J37", "J39", "J41", "J43", "J36"]
BACK_ORDER = ["J13", "J15", "J17", "J6", "J3", "J57", "J2", "J19",
              "J29", "J26", "J24", "J22", "J20"]
BNC_PITCH_MM = 18.0  # panel-elevations.md §2
BNC_GROUPS = [
    ["J46", "J48", "J50", "J37", "J39", "J41", "J43"],
    ["J13", "J15", "J17", "J6"],
    ["J29", "J26", "J24", "J22", "J20"],
]
FAN_STRIP_X = 310.0  # M-6 as amended; floorplan §2.1: no front connector at X >= 310

# How each connector family is located from its own pads, so the check does not depend on
# where a footprint's origin happens to be.
#   BNC: pad 1 is on the body's centreline, 14.45 mm behind the body's FRONT FACE, the
#        face the panel seats on: 5.56 mm from the face to the ground pins, 6.35 mm more
#        to the first signal row and 2.54 mm to the second (Amphenol 31-6575 rev A, side
#        view). Pad 3 is in the row nearer the panel. The jacks stand 20.7 mm beyond that
#        face; the footprint's F.Fab draws them, to 35.15 mm from pad 1.
#   MDR68: contact 36 is in the tail row nearest the edge, 5.10 mm from the PCB EDGE, which
#        is the drawing's datum (MH 3700-0121-01 rev 3.0, "PCB Layout (B)"); contact 1 is
#        in the row farthest in. Contacts 1 and 34 are the ends of the upper mating row,
#        and with the edge up contact 1 is at the right.
#   DB37: the two shell holes are 63.5 mm apart and 11.32 mm from the PCB edge (the stock
#        footprint's own name and description); pin 1's row is 1.42 mm farther in.
BNC_PAD1_TO_BODY_FACE_MM = 14.45
MDR_ROW1_TO_EDGE_MM = 5.10
DSUB_SHELL_TO_EDGE_MM = 11.32
# Depth, per family, from the manufacturers' drawings (the user, 2026-11-20: "Per family
# from the sources"; ruling R46: drawings in place of samples). Recorded with their sha256
# in hardware/datasheet-params.toml, [mdr68] and [bnc_dual_isolated].
#
#   MDR68: the drawing dimensions its tail rows from the PCB EDGE, so there is nothing to
#        choose: the footprint's y = 0 goes on the board edge. The flange is then 0.30 mm
#        inside the edge and the nose 4.70 mm beyond it.
#   BNC: the drawing gives no board-edge datum. The body's front face runs down to the
#        seating plane, so the board cannot project past it, and the part is a rear-mount
#        bulkhead part whose panel seats on that face. So the face is put ON the board
#        edge. [engineering judgment], inferred from the drawing rather than stated by it.
#   DB37: where the stock footprint puts it, flange at the edge.
#
# Until 2026-11-20 both the BNC and the MDR68 were placed by guesswork (first with a
# "face" on the edge that was the jack's tip and a mis-read datum, then 3.4 mm beyond it).
BNC_BODY_FACE_BEYOND_EDGE_MM = 0.0
PANEL_THICKNESS_MM = 3.0   # d3-panel-thickness.md

MECHANICAL = {"H1", "H2"}  # the CM5 IO Board's two inboard standoff holes (floorplan §6.6)

# ---------------------------------------------------------------------------
# Reading the board file.
# ---------------------------------------------------------------------------
_REF_RE = re.compile(r'^\(property\s+"Reference"\s+"([^"]*)"')
_PADHEAD_RE = re.compile(r'^\(pad\s+"([^"]*)"\s+(\S+)\s+(\S+)')
_SIZE_RE = re.compile(r'\(size\s+(-?[\d.]+)\s+(-?[\d.]+)\)')
_NET_RE = re.compile(r'\(net\s+(?:\d+\s+)?"((?:[^"\\]|\\.)*)"\)')
_FP_RE = re.compile(r'\(footprint\s+"([^"]*)"')
_XY_RE = re.compile(r'\(xy\s+(-?[\d.]+)\s+(-?[\d.]+)\)')
_START_END_RE = re.compile(
    r'\(start\s+(-?[\d.]+)\s+(-?[\d.]+)\)\s*\(end\s+(-?[\d.]+)\s+(-?[\d.]+)\)'
)


class Pad:
    __slots__ = ("ref", "num", "kind", "x", "y", "hx", "hy", "net")

    def __init__(self, ref, num, kind, x, y, hx, hy, net):
        self.ref, self.num, self.kind = ref, num, kind
        self.x, self.y, self.hx, self.hy, self.net = x, y, hx, hy, net

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x - self.hx, self.y - self.hy, self.x + self.hx, self.y + self.hy)

    def __repr__(self):
        return f"{self.ref}.{self.num or '(unnumbered)'}@({self.x:.3f},{self.y:.3f})"


class Footprint:
    __slots__ = ("ref", "lib_id", "x", "y", "rot", "pads", "attr")

    def __init__(self, ref, lib_id, x, y, rot, pads, attr):
        self.ref, self.lib_id, self.x, self.y, self.rot = ref, lib_id, x, y, rot
        self.pads, self.attr = pads, attr


def read_board(text: str) -> dict:
    """Footprints (with pads in board coordinates), Edge.Cuts shapes, rule areas and the
    count of routed items, out of a .kicad_pcb's text. Works on the generator's own output
    and on the file `kicad-cli pcb upgrade` or pcbnew saves from it."""
    top = _split_children(text.strip())
    footprints: list[Footprint] = []
    edges: list[list[tuple[float, float]]] = []
    zones: list[dict] = []
    routed = 0
    for child in top:
        tag = _child_tag(child)
        if tag == "footprint":
            kids = _split_children(child)
            ref = attr = None
            at = None
            for k in kids:
                t = _child_tag(k)
                if t == "property" and ref is None:
                    m = _REF_RE.match(k)
                    if m:
                        ref = m.group(1)
                elif t == "at" and at is None:
                    at = _child_at(k)
                elif t == "attr":
                    attr = k[1:-1].split()[1:]
            assert ref is not None and at is not None, child[:80]
            fx, fy_, rot = at
            pads = []
            for k in kids:
                if _child_tag(k) != "pad":
                    continue
                m = _PADHEAD_RE.match(k)
                lx, ly, ang = _child_at(k)
                sm = _SIZE_RE.search(k)
                w, h = float(sm.group(1)), float(sm.group(2))
                # A placed pad's angle is absolute (kicad_pcb.py, constraint 6).
                turned = round(ang) % 180 == 90
                assert round(ang) % 90 == 0, f"{ref} pad {m.group(1)}: angle {ang}"
                hx, hy = (h / 2, w / 2) if turned else (w / 2, h / 2)
                px, py = place_point(fx, fy_, rot, lx, ly)
                nm = _NET_RE.search(k)
                pads.append(Pad(ref, m.group(1), m.group(2), px, py, hx, hy,
                                nm.group(1) if nm else None))
            footprints.append(Footprint(ref, _FP_RE.match(child).group(1),
                                        fx, fy_, rot % 360, pads, attr or []))
        elif tag in ("gr_rect", "gr_poly", "gr_line") and '(layer "Edge.Cuts")' in child:
            if tag == "gr_rect":
                m = _START_END_RE.search(child)
                x0, y0, x1, y1 = (float(g) for g in m.groups())
                edges.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            elif tag == "gr_poly":
                edges.append([(float(a), float(b)) for a, b in _XY_RE.findall(child)])
            else:
                raise AssertionError("Edge.Cuts drawn as loose lines: not a shape this "
                                     "check can read as a slot")
        elif tag == "zone":
            name = re.search(r'\(name\s+"([^"]*)"\)', child)
            layers = re.search(r'\(layers\s+([^)]*)\)', child)
            keep = re.search(r'\(keepout(.*?)\)\s*\)', child, re.S)
            poly = child[child.find("(polygon"):]
            zones.append({
                "name": name.group(1) if name else "",
                "layers": set(re.findall(r'"([^"]+)"', layers.group(1))) if layers else set(),
                "keepout": dict(re.findall(r'\((\w+)\s+(\w+)\)', keep.group(1))) if keep else None,
                "pts": [(float(a), float(b)) for a, b in _XY_RE.findall(poly)],
            })
        elif tag in ("segment", "via", "arc"):
            routed += 1
    return {"footprints": footprints, "edges": edges, "zones": zones, "routed": routed}


# ---------------------------------------------------------------------------
# Geometry helpers. Every outline this board draws is rectilinear.
# ---------------------------------------------------------------------------
EPS = 1e-6


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def boxes_overlap(a, b, gap: float = 0.0) -> bool:
    return (a[0] < b[2] + gap - EPS and b[0] < a[2] + gap - EPS
            and a[1] < b[3] + gap - EPS and b[1] < a[3] + gap - EPS)


def box_in_box(inner, outer) -> bool:
    return (inner[0] >= outer[0] - EPS and inner[1] >= outer[1] - EPS
            and inner[2] <= outer[2] + EPS and inner[3] <= outer[3] + EPS)


def point_in_poly(x: float, y: float, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def box_hits_poly(box, pts) -> bool:
    """True if a rectangle and a rectilinear polygon share any area."""
    if not boxes_overlap(box, bbox(pts)):
        return False
    xs = sorted({box[0], box[2], *[p[0] for p in pts if box[0] < p[0] < box[2]]})
    ys = sorted({box[1], box[3], *[p[1] for p in pts if box[1] < p[1] < box[3]]})
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            if point_in_poly((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, pts):
                return True
    return False


def span_across(pts, axis: str, line: float, at: float) -> tuple[float, float] | None:
    """Where a rectilinear polygon lies ACROSS a barrier line, at position `at` along it.
    For a line x = c (axis "x") this is the polygon's x-extent on the scan line y = at; for
    a line y = c, its y-extent on x = at. None if the polygon is not there."""
    hits = []
    n = len(pts)
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        if axis == "x":            # scan along x at y = at; crossing edges are vertical
            if abs(x1 - x2) < EPS and min(y1, y2) - EPS < at < max(y1, y2) + EPS:
                hits.append(x1)
        else:
            if abs(y1 - y2) < EPS and min(x1, x2) - EPS < at < max(x1, x2) + EPS:
                hits.append(y1)
    if len(hits) < 2:
        return None
    lo = max((h for h in hits if h <= line + EPS), default=None)
    hi = min((h for h in hits if h >= line - EPS), default=None)
    return (lo, hi) if lo is not None and hi is not None else None


def region_of_point(x: float, y: float) -> set[str]:
    return {d for d, rects in REGIONS.items()
            if any(r[0] - EPS <= x <= r[2] + EPS and r[1] - EPS <= y <= r[3] + EPS
                   for r in rects)}


def box_in_region(box, domain: str) -> bool:
    """A rectangle lies inside a domain's region if every cell of it does. Regions are
    unions of rectangles, so cut the box at the region's own edges and test each piece."""
    rects = REGIONS[domain]
    xs = sorted({box[0], box[2], *[v for r in rects for v in (r[0], r[2]) if box[0] < v < box[2]]})
    ys = sorted({box[1], box[3], *[v for r in rects for v in (r[1], r[3]) if box[1] < v < box[3]]})
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
            if not any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects):
                return False
    return True


# ---------------------------------------------------------------------------
# Domain derivation (see the module docstring).
# ---------------------------------------------------------------------------
def derive_domains(contract: dict) -> dict:
    nets: dict[str, list[list[str]]] = contract["nets"]
    pins: dict[str, dict[str, str]] = {r: {} for r in contract["component_values"]}
    for net, nodes in nets.items():
        for ref, pin, _fn, _ty in nodes:
            pins[ref][pin] = net

    seed_touch = {r: {SEED_DOMAIN[n] for n in p.values() if n in SEED_DOMAIN}
                  for r, p in pins.items()}
    excluded = set(STRADDLERS) | set(DECLARED_HOME) | {TIE_ON_SPLIT}
    home: dict[str, str] = {r: next(iter(s)) for r, s in seed_touch.items()
                            if len(s) == 1 and r not in excluded}
    undeclared_multi = sorted(r for r, s in seed_touch.items()
                              if len(s) > 1 and r not in excluded)
    net_dom: dict[str, set[str]] = {n: set() for n in nets}
    changed = True
    while changed:
        changed = False
        for net, nodes in nets.items():
            if net in SEED_DOMAIN:
                d = {SEED_DOMAIN[net]}
            else:
                d = {home[r] for r, *_ in nodes if r in home}
            if d - net_dom[net]:
                net_dom[net] |= d
                changed = True
        for ref, p in pins.items():
            if ref in home or ref in excluded or seed_touch[ref]:
                continue
            d = set().union(*[net_dom[n] for n in p.values()]) if p else set()
            if len(d) == 1:
                home[ref] = next(iter(d))
                changed = True
    for ref, (dom, _why) in DECLARED_HOME.items():
        home[ref] = dom
    # A declared part lends its region to the nets only it decides (a feedback node
    # between two declared resistors), but never to a seed net.
    for ref in DECLARED_HOME:
        for net in pins[ref].values():
            if net not in SEED_DOMAIN and not net_dom[net]:
                net_dom[net] = {home[ref]}
    undecided = sorted(r for r in pins
                       if r not in home and r not in STRADDLERS and r != TIE_ON_SPLIT)

    # Which nets have pads in two regions, given where every part is to sit.
    crossing: set[str] = set()
    for net, nodes in nets.items():
        if net in SEED_DOMAIN:
            continue
        sides = set()
        for ref, pin, *_ in nodes:
            if ref in STRADDLERS:
                sides.add(STRADDLERS[ref][pin])
            elif ref in home:
                sides.add(home[ref])
        if len(sides) > 1:
            crossing.add(net)
    # A straddler's own nets are not crossings: the part is the crossing, and each of its
    # nets lives on one side. Take those out by asking whether the net has pads on two
    # sides once each straddler pin is counted on its declared side -- which is what the
    # loop above already did, so nothing more to remove.
    return {"pins": pins, "home": home, "net_dom": net_dom, "crossing": crossing,
            "undeclared_multi": undeclared_multi, "undecided": undecided,
            "seed_touch": seed_touch}


# ---------------------------------------------------------------------------
# The checks.
# ---------------------------------------------------------------------------
def run_checks(pcb_text: str, contract: dict,
               pro_text: str | None = None, dru_text: str | None = None) -> tuple[list[str], dict]:
    """(failures, report). An empty failure list is a pass."""
    fails: list[str] = []
    report: dict = {}
    board = read_board(pcb_text)
    fps = board["footprints"]
    by_ref: dict[str, Footprint] = {}

    # -- 1. every reference placed exactly once ---------------------------------
    seen: dict[str, int] = {}
    for f in fps:
        seen[f.ref] = seen.get(f.ref, 0) + 1
        by_ref[f.ref] = f
    wanted = set(contract["component_values"])
    for ref in sorted(wanted - set(seen)):
        fails.append(f"1: {ref} is in the netlist and not on the board")
    for ref in sorted(set(seen) - wanted - MECHANICAL):
        fails.append(f"1: {ref} is on the board and not in the netlist")
    for ref, n in sorted(seen.items()):
        if n != 1:
            fails.append(f"1: {ref} is placed {n} times")
    for ref in sorted(MECHANICAL):
        f = by_ref.get(ref)
        if f is None:
            fails.append(f"1: mechanical hole {ref} is missing")
        elif "board_only" not in f.attr:
            fails.append(f"1: {ref} is not marked board_only")
    report["placed"] = len(fps)
    report["netlist_refs"] = len(wanted)

    # -- 2. pad nets equal the contract's ----------------------------------------
    dom = derive_domains(contract)
    pins = dom["pins"]
    n_pads = 0
    for f in fps:
        want = pins.get(f.ref, {})
        got: dict[str, set] = {}
        for p in f.pads:
            if p.num:
                got.setdefault(p.num, set()).add(p.net)
        for pin, net in want.items():
            if pin not in got:
                fails.append(f"2: {f.ref} has no pad {pin} (net {net})")
            elif got[pin] != {net}:
                fails.append(f"2: {f.ref} pad {pin} is on {sorted(map(str, got[pin]))}, "
                             f"contract says {net}")
            else:
                n_pads += 1
        for pin, netset in got.items():
            if pin not in want and netset != {None}:
                fails.append(f"2: {f.ref} pad {pin} carries {sorted(map(str, netset))} "
                             f"and the contract gives that pin no net")
    report["pads_with_contract_net"] = n_pads

    # -- 3. panel positions -----------------------------------------------------
    centres: dict[str, float] = {}
    worst = 0.0
    for ref, (face, cx) in PANEL.items():
        f = by_ref.get(ref)
        if f is None:
            continue
        edge = FRONT_Y if face == "front" else BACK_Y
        inward = -1.0 if face == "front" else 1.0   # +y is toward the front edge
        pad = {}
        for p in f.pads:
            pad.setdefault(p.num, []).append(p)
        if f.lib_id.endswith("BNC_Dual_RA_Isolated"):
            got_x = pad["1"][0].x
            depth = (pad["1"][0].y - edge) * inward
            want_depth = BNC_PAD1_TO_BODY_FACE_MM - BNC_BODY_FACE_BEYOND_EDGE_MM
            outward_ok = (pad["3"][0].y - pad["1"][0].y) * inward < 0
        elif f.lib_id.endswith("MDR68_Male_RightAngle"):
            got_x = (pad["1"][0].x + pad["34"][0].x) / 2
            depth = (pad["36"][0].y - edge) * inward
            want_depth = MDR_ROW1_TO_EDGE_MM
            outward_ok = (pad["1"][0].y - pad["36"][0].y) * inward > 0
            # Handedness (the drawing's PCB Layout (B)): seen from the component side with
            # the connector's edge up, contact 1 is at the right. At the back edge that is
            # +x; at the front edge the part is turned half round and it is -x.
            if (pad["1"][0].x - pad["34"][0].x) * inward <= 0:
                fails.append(f"3: {ref} is mirrored: contact 1 is on the wrong side")
        elif "DSUB-37" in f.lib_id:
            sh = pad["SH"]
            got_x = (sh[0].x + sh[1].x) / 2
            depth = (sh[0].y - edge) * inward
            want_depth = DSUB_SHELL_TO_EDGE_MM
            outward_ok = (pad["1"][0].y - sh[0].y) * inward > 0
        else:
            fails.append(f"3: {ref}: no rule for locating a {f.lib_id}")
            continue
        centres[ref] = got_x
        err = max(abs(got_x - cx), abs(depth - want_depth))
        worst = max(worst, err)
        if abs(got_x - cx) > PANEL_TOL_MM:
            fails.append(f"3: {ref} is centred at X = {got_x:.3f}, panel position is {cx:.3f}")
        if abs(depth - want_depth) > PANEL_TOL_MM:
            fails.append(f"3: {ref} is {depth:.3f} mm in from the {face} edge at its "
                         f"locating pads, expected {want_depth:.3f}")
        if not outward_ok:
            fails.append(f"3: {ref} faces inward: its mating face is toward the board")
        if face == "front" and got_x >= FAN_STRIP_X:
            fails.append(f"3: {ref} is behind the intake fan strip (X >= {FAN_STRIP_X}), M-6")
    report["panel_connectors"] = len(centres)
    report["panel_worst_error_mm"] = worst
    j4 = by_ref.get("J4")
    if j4 is not None:
        centres["J4"] = sum(p.x for p in j4.pads) / len(j4.pads)
    for order, name in ((FRONT_ORDER, "front"), (BACK_ORDER, "back")):
        have = [r for r in order if r in centres]
        if sorted(have, key=lambda r: centres[r]) != have:
            fails.append(f"3: {name} face is out of the floorplan's order: "
                         f"{sorted(have, key=lambda r: centres[r])}")
    for group in BNC_GROUPS:
        for a, b in zip(group, group[1:]):
            if a in centres and b in centres and abs(centres[b] - centres[a] - BNC_PITCH_MM) > PANEL_TOL_MM:
                fails.append(f"3: {a} to {b} is {centres[b] - centres[a]:.3f} mm, "
                             f"pitch is {BNC_PITCH_MM}")

    # -- 4. domains ---------------------------------------------------------------
    for ref in dom["undeclared_multi"]:
        fails.append(f"4: {ref} has pins on the rails of {sorted(dom['seed_touch'][ref])} "
                     f"and is neither a declared straddler nor a declared exception")
    for ref in dom["undecided"]:
        fails.append(f"4: {ref}'s domain cannot be derived and is not declared")
    extra = sorted(dom["crossing"] - set(EXPECTED_CROSSINGS))
    gone = sorted(set(EXPECTED_CROSSINGS) - dom["crossing"])
    for net in extra:
        fails.append(f"4: net {net} crosses between two domains and no constraint allows it")
    for net in gone:
        fails.append(f"4: net {net} is declared as a crossing and does not cross")
    report["crossing_nets"] = len(dom["crossing"])

    wrong_side = 0
    counts = {d: 0 for d in REGIONS}
    for f in fps:
        if f.ref in MECHANICAL:
            continue
        if f.ref in STRADDLERS:
            sides = STRADDLERS[f.ref]
            for p in f.pads:
                if not p.num:
                    continue
                side = sides.get(p.num)
                if side is None:
                    fails.append(f"4: straddler {f.ref} has pad {p.num}, which B-10/B-12 "
                                 f"do not place")
                    continue
                if not box_in_region(p.box, side):
                    wrong_side += 1
                    fails.append(f"4: {p} is declared {side}-side and is not in the "
                                 f"{side} region")
                # the declaration against the netlist, wherever the netlist decides
                nd = dom["net_dom"].get(p.net or "", set())
                if p.net in SEED_DOMAIN and (f.ref, p.num) not in FOREIGN_SEED_PADS:
                    nd = {SEED_DOMAIN[p.net]}
                if len(nd) == 1 and nd != {side} and (f.ref, p.num) not in FOREIGN_SEED_PADS:
                    fails.append(f"4: {f.ref} pin {p.num} is declared {side}-side, but its "
                                 f"net {p.net} belongs to {sorted(nd)}")
            continue
        if f.ref == TIE_ON_SPLIT:
            for p in f.pads:
                want = SEED_DOMAIN.get(p.net or "")
                if want is None or not box_in_region(p.box, want):
                    wrong_side += 1
                    fails.append(f"4: {p} of the star tie is on {p.net} and not in that "
                                 f"net's region")
            continue
        home = dom["home"].get(f.ref)
        if home is None:
            continue
        counts[home] += 1
        for p in f.pads:
            if not box_in_region(p.box, home):
                wrong_side += 1
                fails.append(f"4: {p} belongs to {home} and is in "
                             f"{sorted(region_of_point(p.x, p.y)) or 'no region'}")
            if p.net in SEED_DOMAIN and SEED_DOMAIN[p.net] != home \
                    and (f.ref, p.num) not in FOREIGN_SEED_PADS:
                fails.append(f"4: {p} is on {p.net}, a {SEED_DOMAIN[p.net]} net, in the "
                             f"{home} region, and no constraint allows it")
    report["parts_per_region"] = counts
    report["straddlers"] = len(STRADDLERS)
    report["pads_on_wrong_side"] = wrong_side

    # -- 5. slots and bands ---------------------------------------------------------
    edges = board["edges"]
    outline = [e for e in edges if box_in_box((0, 0, BOARD_W, BOARD_H), bbox(e))]
    if len(outline) != 1 or bbox(outline[0]) != (0.0, 0.0, BOARD_W, BOARD_H):
        fails.append(f"5: the board outline is not {BOARD_W} x {BOARD_H} at the origin")
    slots = [e for e in edges if e not in outline]
    report["slots"] = len(slots)
    if len(slots) != 2:
        fails.append(f"5: {len(slots)} cutouts on Edge.Cuts, expected 2 (one per slotted line)")
    for s in slots:
        b = bbox(s)
        if not (b[0] > EPS and b[1] > EPS and b[2] < BOARD_W - EPS and b[3] < BOARD_H - EPS):
            fails.append(f"5: a slot reaches a board edge (B-5): {b}")
    in_slot = 0
    in_band = 0
    for f in fps:
        for p in f.pads:
            if any(box_hits_poly(p.box, s) for s in slots):
                in_slot += 1
                fails.append(f"5: {p} has copper in a slot")
            if f.ref in STRADDLERS:
                continue
            for name, rect, _tracks in BANDS:
                if boxes_overlap(p.box, rect):
                    in_band += 1
                    fails.append(f"5: {p} has copper in the barrier band {name} (B-7)")
            if boxes_overlap(p.box, SPLIT_STRIP):
                in_band += 1
                fails.append(f"5: {p} has copper in the AGND/DGND gap (G-9)")
    report["pads_in_slots"] = in_slot
    report["pads_in_bands"] = in_band

    margins: dict[str, float] = {}
    for ref, (axis, line) in STRADDLER_LINE.items():
        f = by_ref.get(ref)
        if f is None:
            continue
        numbered = [p for p in f.pads if p.num]
        along = [(p.y - p.hy, p.y + p.hy) if axis == "x" else (p.x - p.hx, p.x + p.hx)
                 for p in numbered]
        lo, hi = min(a for a, _ in along), max(b for _, b in along)
        want, rule = RULED_SLOT_MM.get(ref, (SLOT_MM, "B-3"))
        gap = math.inf
        for at in (lo, (lo + hi) / 2, hi):
            spans = [sp for s in slots if (sp := span_across(s, axis, line, at)) is not None]
            if not spans:
                fails.append(f"5: no slot under {ref} at {at:.2f} along its barrier line")
                break
            a, b = spans[0]
            width = b - a
            if ref in RULED_SLOT_MM:
                if abs(width - want) > 1e-3:
                    fails.append(f"5: the slot under {ref} is {width:.3f} mm, ruled {want} ({rule})")
            elif width < want - 1e-3:
                fails.append(f"5: the slot under {ref} is {width:.3f} mm, under {want} (B-3)")
            if abs((a + b) / 2 - line) > 1e-3:
                fails.append(f"5: the slot under {ref} is not centred on its barrier line")
            for p in numbered:
                lo_p, hi_p = (p.x - p.hx, p.x + p.hx) if axis == "x" else (p.y - p.hy, p.y + p.hy)
                gap = min(gap, a - hi_p if hi_p <= a + EPS else lo_p - b)
        margins[ref] = gap
        if gap <= 0:
            fails.append(f"5: {ref} has a pad on the slot")
    report["straddler_pad_to_slot_mm"] = {r: round(g, 3) for r, g in margins.items()}

    # -- 6. keep-outs ------------------------------------------------------------------
    zones = {z["name"]: z for z in board["zones"]}
    report["rule_areas"] = sorted(zones)
    for name, rect, tracks_ok in BANDS:
        z = zones.get(name)
        if z is None:
            fails.append(f"6: no rule area {name} (B-7, B-9)")
            continue
        if not set(COPPER_LAYERS) <= z["layers"]:
            fails.append(f"6: rule area {name} is not on all four copper layers")
        if not box_in_box(rect, bbox(z["pts"])):
            fails.append(f"6: rule area {name} does not cover its band {rect}")
        k = z["keepout"] or {}
        if k.get("copperpour") != "not_allowed" or k.get("vias") != "not_allowed":
            fails.append(f"6: rule area {name} lets a pour or a via in")
        if (k.get("tracks") == "allowed") != tracks_ok:
            fails.append(f"6: rule area {name}: tracks are {k.get('tracks')}, "
                         f"expected {'allowed (B-17)' if tracks_ok else 'not_allowed'}")
    for i, s in enumerate(slots):
        ok = False
        for z in board["zones"]:
            if not z["name"].endswith("slot_keepout") or not set(COPPER_LAYERS) <= z["layers"]:
                continue
            k = z["keepout"] or {}
            if any(k.get(w) != "not_allowed" for w in ("tracks", "vias", "copperpour")):
                continue
            # every point of the slot, grown by the margin, lies inside the keep-out
            m = KEEPOUT_MARGIN_MM - 1e-6
            if all(point_in_poly(x + dx, y + dy, z["pts"])
                   for x, y in s for dx in (-m, m) for dy in (-m, m)):
                ok = True
        if not ok:
            fails.append(f"6: slot {i} has no keep-out reaching {KEEPOUT_MARGIN_MM} mm "
                         f"beyond it on all four layers (B-8)")
    z = zones.get("agnd_dgnd_gap")
    if z is None or not {"In1.Cu", "In2.Cu"} <= z["layers"] \
            or not box_in_box(SPLIT_STRIP, bbox(z["pts"])):
        fails.append("6: no rule area agnd_dgnd_gap on L2 and L3 covering the split (G-9)")

    # -- 7. the project's rules -----------------------------------------------------------
    if pro_text is not None:
        try:
            pro = json.loads(pro_text)
            classes = {c["name"] for c in pro["net_settings"]["classes"]}
            patterns = pro["net_settings"]["netclass_patterns"]
        except (KeyError, ValueError, TypeError):
            classes, patterns = set(), []
        if len(classes) < 2:
            fails.append("7: breakout.kicad_pro carries no net classes; re-run "
                         "gen_breakout_pcb.py (gen_breakout.py rewrites the project as a stub)")
        else:
            assigned = [p["pattern"] for p in patterns]
            if len(assigned) != len(set(assigned)):
                fails.append("7: a net is assigned to two net classes")
            unknown = sorted(set(assigned) - set(contract["nets"]))
            if unknown:
                fails.append(f"7: net classes name nets the contract does not have: {unknown[:5]}")
        report["net_classes"] = sorted(classes)
    if dru_text is not None and "(rule" not in dru_text:
        fails.append("7: breakout.kicad_dru has no rules")

    report["routed_items"] = board["routed"]
    return fails, report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pcb = Path(argv[0]) if argv else PCB_PATH
    contract_path = Path(argv[1]) if len(argv) > 1 else CONTRACT_PATH
    if not pcb.exists():
        print(f"error: {pcb} does not exist; run hardware/gen/gen_breakout_pcb.py")
        return 1
    pro = pcb.with_suffix(".kicad_pro")
    dru = pcb.with_suffix(".kicad_dru")
    fails, report = run_checks(
        pcb.read_text(), json.loads(contract_path.read_text()),
        pro.read_text() if pro.exists() else None,
        dru.read_text() if dru.exists() else None,
    )
    print(f"{pcb.name}: {report.get('placed')} footprints placed, "
          f"{report.get('netlist_refs')} netlist references, "
          f"{report.get('pads_with_contract_net')} pads on their contract net")
    print(f"  panel connectors: {report.get('panel_connectors')}, worst error "
          f"{report.get('panel_worst_error_mm', 0):.4f} mm (tolerance {PANEL_TOL_MM})")
    print(f"  parts per region: {report.get('parts_per_region')}; "
          f"{report.get('straddlers')} straddlers; "
          f"pads on the wrong side: {report.get('pads_on_wrong_side')}")
    print(f"  nets crossing between domains: {report.get('crossing_nets')} "
          f"(declared: {len(EXPECTED_CROSSINGS)})")
    print(f"  slots: {report.get('slots')}; pads in a slot: {report.get('pads_in_slots')}; "
          f"pads in a band or the split gap: {report.get('pads_in_bands')}")
    print(f"  pad-to-slot margin per straddler (mm): {report.get('straddler_pad_to_slot_mm')}")
    print(f"  rule areas: {report.get('rule_areas')}")
    print(f"  net classes: {report.get('net_classes')}")
    print(f"  routed items (tracks, vias): {report.get('routed_items')}")
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails[:200]:
            print("  " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
