"""An automatic layout judge for hardware/breakout/breakout.kicad_pcb (task J1, 2026-11-21).

Run it with the Python that ships inside KiCad 10.0.5 (it needs `pcbnew`):

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hardware/gen/judge_layout.py BOARD [--baseline UNROUTED_BOARD] [--nets NETS_FILE] \\
        [--region x0,y0,x1,y1] [--complete-nets COMPLETE_NETS [--touched-nets FILE]
        [--due FILE]] [--json OUT.json] [--kicad-happy] [--skip-drc]

It grades a routed board, or a region-only copy of it, against this board's own rules and
prints comparable numbers, so two candidate layouts can be ranked without a person. It does
not sign a board off: a person still does that once before fab (routing-brief.md §7).

PARTIAL MODE (task R2, 2026-11-24; the stage contract of fix round 1). A board routed
through stage N is judged against the stage contract route_breakout.py writes, each list one
per line, the union over the stages so far:

- `--complete-nets routes/complete.nets`: the nets the stages complete (every pad joined).
  CONNECTIVITY counts these nets' unrouted connections (the whole board's count is printed
  beside it), and DRC sets aside an `unconnected_items` entry only when every item in it
  lies on a net that is not complete. Every other DRC class keeps the accepted set.
  `--routed-nets` is its old name.
- `--touched-nets` (default: touched.nets beside it): every net with copper. Copper on a
  net in neither list FAILs the NET-LISTS gate.
- `--due` (default: due.txt beside it): check IDs, or check/clause keys such as
  `G-1/overlap`, that the stages declare must PASS. A due check or clause is judged on the
  copper present, and anything but PASS (N/A included) is a FAIL.

Each layer-2 clause is a PROHIBITION, which any copper present can break and which is judged
on all the copper, or a REQUIREMENT, which needs copper to exist; a requirement that is not
due is PENDING until every net it needs is complete (CLASSES below; the class and the key
are printed beside each clause, and a PENDING clause prints what it found so far). Without
the flags everything is judged and nothing is ever PENDING.

FOUR LAYERS.

1. Gates. DRC with --severity-all and breakout's rules loaded: the board is staged in a
   temporary directory as `breakout.kicad_pcb` beside copies of breakout.kicad_pro,
   breakout.kicad_dru, fp-lib-table and sym-lib-table, with `../lib` resolving to
   hardware/lib, because kicad-cli loads rules and net classes only from files named after
   the board. That the rules were loaded is PROVED, not assumed: a second staged copy gets
   one 0.5 mm track on `P5_RAW`, which only the custom rule "N-1 INLET track width" can
   flag, and only when the project's net classes put `P5_RAW` in INLET. The DRC classes are
   compared with the accepted set (24 silk_edge_clearance, all on the twelve straddlers,
   B-21; routing-brief.md §5 and §7). Then KiCad's connectivity count after filling every
   zone (routing-brief.md §1); the placement check (check_breakout_placement.py, §7 item 1);
   and, with --nets/--region, every track, via and zone vertex inside the region and on a
   listed net.

2. This board's rules that no tool checks (routing-brief.md §7 item 5 and §5), each read
   from constraints.md. Each reports PASS, FAIL, N/A or NOT CHECKED, the rule ID and
   evidence a person can find in pcbnew. Where a rule leaves a number open, the judge states
   a numeric reading and marks it [judge's reading] (READINGS below); the user accepted
   all 27 on 2026-11-22 (ACCEPTED_READINGS), and the mark stays, so a reading can still be
   told from a sourced value. N/A means the rule's nets are absent from the board, or carry
   no copper yet; it never means PASS. PENDING (partial mode only) means a requirement
   whose nets are not all routed yet.

3. Metrics (numbers, not pass/fail), per board and against --baseline.

4. With --kicad-happy, kicad-happy's analyze_pcb.py --full on the board and the baseline,
   reporting only what is new in the board. Advisory: it never changes the exit code. Its
   return-path figures are re-measured against the adjacent plane (see kicad_happy()).

Exit code 0 only when every gate and every rule check is PASS or N/A (or PENDING, in
partial mode), or a NOT CHECKED named in --allow-not-checked; 2 on a bad argument.

Coordinates are the board file's (x right, y down, back edge y = 0, front edge y = 240),
in mm.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_breakout_placement as cbp  # noqa: E402  (REGIONS, domains, crossings)

try:
    import pcbnew  # noqa: E402
except ImportError:  # pragma: no cover - the judge needs KiCad's Python
    pcbnew = None

REPO = cbp.REPO
BREAKOUT = REPO / "hardware" / "breakout"
COMMITTED_PCB = BREAKOUT / "breakout.kicad_pcb"
KICAD_PY = ("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
            "Versions/Current/bin/python3")
KICAD_HAPPY = (Path.home() / ".claude/plugins/cache/kicad-happy/kicad-happy/2.1.0/"
               "skills/kicad/scripts/analyze_pcb.py")

PASS, FAIL, NA, NOT_CHECKED = "PASS", "FAIL", "N/A", "NOT CHECKED"
PENDING = "PENDING"   # partial mode only: a requirement whose nets are not all routed yet

# ---------------------------------------------------------------------------------------
# What the sources fix. Every number here is either quoted from a named source or is in
# READINGS, the judge's own readings of words the sources leave open.
# ---------------------------------------------------------------------------------------
ACCEPTED_DRC = {"silk_edge_clearance": 24}          # B-21; routing-brief.md §5, §7
SPLIT_X = cbp.SPLIT_X                                # 170: the AGND/DGND split
INTAN_LINE_Y = cbp.INTAN_LINE_Y                      # 165: the Intan island's back
INTAN_X1 = cbp.INTAN_X1                              # 238: the island's right-hand side
CORRIDOR_Y = (50.0, 165.0)  # floorplan.md §3.3 (floorplan Y 75 to 190); routing-brief §4.3
# G-11's nets (the placement check's EXPECTED_CROSSINGS less the eight MUX_OUTn, which
# cross into the Intan island, B-17) and the two rails G-11 names, +12V and +3V3.
G11_NETS = frozenset(
    [n for n in cbp.EXPECTED_CROSSINGS if not n.startswith("MUX_OUT")] + ["+12V", "+3V3"])
MUX_OUT = [f"MUX_OUT{n}" for n in range(1, 9)]
PLANE_NETS = frozenset([                             # S-6 (L2) and S-7 (L3)
    "AGND", "DGND", "NI_GND", "INTAN_GND",
    "+12V", "-12V", "+5V", "+3V3", "NI_5V", "ISO_P12", "ISO_N12", "ISO_5V"])
SHIELD_NETS = ["A_PD1_SHLD", "A_PD2_SHLD", "A_MIC_SHLD", "A_AMB_SHLD", "A_ACC_SHLD",
               "A_JOY_X_SHLD", "A_JOY_Y_SHLD", "A_MISC1_SHLD", "A_MISC2_SHLD",
               "A_MISC3_SHLD"]                       # G-18: the ten rig-facing shields
BAND_W = cbp.BAND_MM                                 # 2.5, B-7

READINGS = {
    "G-10": "\"one corridor ... as close to NT1 as the floorplan allows\": every crossing "
            "lies on x = 170 between y = 50 and 165, the stretch floorplan.md §3.3 and "
            "routing-brief.md §4.3 expect; \"one corridor\": sorted by y, no two successive "
            "crossings more than 5 mm apart (about ten tracks at the 0.2 mm width and gap); a "
            "filled pour island that spans the line counts as one crossing of its net",
    "K-4": "\"the shortest path inside their own islands\": BARCODE_NI's copper inside the "
           "NI region and BARCODE_INTAN/_BNC's inside the Intan region, each at most 1.5 x "
           "its MST (SHORT); \"clear of INTAN_AO* and the INA105s\": no Intan barcode track "
           "within 2.5 mm (the B-7 band) of INTAN_AO* copper or an INA105 pad",
    "G-20": "\"R224 is placed at J19\": an R224 pad within 10 mm of a J19 shell pad",
    "S-7": "S-6 puts the four grounds on L2 and S-7 the rails on L3: a plane net's pour on "
           "In1.Cu/In2.Cu must be on that layer (outer-layer areas such as P-6's and P-12's "
           "are allowed); every plane-net fill lies inside its domain's REGIONS rectangles",
    "P-5": "\"at the pins\": the nearest same-net pads of each of C11/C14 (U3), C17/C20 (U4) "
           "and of the FB and NR/SS parts within 6 mm of the LDO's pin (the placement has "
           "5.25 mm at most)",
    "K-35": "\"a 100 nF bypass sits at each optocoupler supply pin pair\": for each "
            "ACSL-6400's pairs 10/9 and 15/16, its own capacitor on that rail and ground with "
            "each pad within 5 mm of its pin and joined to it (KiCad's own connectivity, by "
            "track or plane)",
    "B-13-ALL": "routing-brief.md §4.1: \"every straddler pad is left from its outer end\": "
                "no track end inside a straddler's pad nearer its barrier line than the "
                "pad's centre",
    "FIXED": "the reference is --reference, else --baseline, else the board as committed "
             "on main; never the working-tree file, which is the routed board itself. "
             "\"Rule areas as in the reference\": each of the reference's is on the board, "
             "unchanged, and the board has no other, except a mounting hole's keep-out "
             "mh_<ref> for a hole of MOUNTING_HOLES (task R0), whose shape PLACEMENT checks",
    "K-2": "\"barcode-family net\": every net named BARCODE_* or CAM_SYNC_* (constraints "
           "§5.1 cites NET:1883-1931 and 1996-2028, which hold all 21), not only the 13 "
           "routing-brief §4.4 lists; \"enters, over or under\": any of its copper "
           "(track, via or pour) with area inside an AGND rectangle of REGIONS",
    "G-1": "\"no pour overlap\": the AGND and DGND fills, projected onto one plane from "
           "all four layers, share no area; \"no mounting-hardware path\": every hole of "
           "MECHANICAL (H1 and H2, the IO Board's standoffs; H3 to H10, the board's own, "
           "task R0) carries no net and no copper touches it",
    "G-3": "\"never flood it into the plane it ties to\": no pour of FAN_RTN, "
           "CHASSIS_GND or ISO_5V_RTN on In1.Cu or In2.Cu, the plane layers",
    "G-4": "\"beside NT1\": NT2's centre within 10 mm of NT1's",
    "G-5": "\"at the inlet star cluster\": NT3's centre within 10 mm of NT1's; "
           "\"dedicated trace\": no pour on CHASSIS_GND, and J56 reaches NT3 by tracks",
    "G-6": "\"at U63's secondary pins\": NT4 pad 1 within 10 mm of U63 pad 6",
    "G-16": "\"every other ground pin\": every pad of J2 and J36 whose netlist net is "
            "unconnected-*, since the netlist does not mark which are NI ground pins",
    "SHORT": "\"short\" / \"as short as placement allows\": routed track length of the net "
             "at most 1.5 x the octilinear minimum spanning tree of its pads",
    "N-4": "\"symmetric copper\": pins 8 and 14 joined by NI_5V pours on the same layers, "
           "and the tracks hanging off each (through tracks and vias, up to a pour contact) "
           "within 1.0 mm in total length, with the same narrowest width and via count",
    "N-1": "\"+5V from D3 to its plane\": its plane is its pour on In2.Cu (S-7, L3); every "
           "track reachable from D3 pad 1 through tracks, vias and outer-layer pours of the "
           "net before that plane; an outer-layer pour on the way is NOT CHECKED (its "
           "width is not measured)",
    "N-10": "squares along the least-squares copper path from D3 pad 1 to each 249 ohm "
            "LED resistor on +5V: track length/width (vias add nothing), plus, for each "
            "crossing of a pour, a LOWER bound: the larger of ln(d/0.3 mm)/pi (an unbounded "
            "sheet) and the integral of dt/L(t) over the pour's cross-sections. A path over "
            "83 on that bound FAILs; a track-only path at most 83 PASSes (a single path is an "
            "upper bound); a path through a pour at most 83 is NOT CHECKED",
    "B-13": "\"shortest trace\": a track-only path no longer than the pads' straight "
            "distance plus 2.0 mm; \"from the outer end\": every track end inside "
            "C162 pads 1 and 2 and U63 pads 2 and 4 no nearer y = 165 than the pad centre",
    "B-16": "\"under or beside\" a primary pin: within 2.5 mm (the B-7 band) of the pad, "
            "on any layer; \"under\" a secondary pin: overlapping the pad in plan",
    "B-18": "\"side by side\": each MUX_OUTn crossing of y = 165 has an AGND crossing "
            "within 1.5 mm with no other crossing between; \"the same length\": within "
            "1.0 mm; \"taken from the AGND plane at that channel's mux\": the sense "
            "trace meets the pour within 10 mm of the mux's centre",
    "N-9": "\"coupled\": at least 80% of each trace's length within a 0.4 mm edge gap "
           "of the other on the same layer; \"matched\": lengths within 1.0 mm; "
           "\"over unbroken DGND\": every 0.25 mm sample of both traces over a DGND fill "
           "on the adjacent layer (F.Cu->In1.Cu, B.Cu->In2.Cu), except within 1.0 mm "
           "of the pair's own pads",
    "K-33": "\"short, equal traces\": the two legs from the paralleled outputs to their "
            "junction each at most 10 mm, differing by at most 1.0 mm",
    "K-23": "\"laid out symmetrically\": for each header, the routed length of each "
            "signal-leg divider net (X_MID, X_DIV) and its shield-leg twin (X_SHLD_MID, "
            "X_SHLD_DIV) within 1.0 mm, with equal via counts",
    "NECK": "a pour's narrowest neck: the first width w (0.1 mm steps to 2 mm, 0.5 mm to "
            "6 mm, 1 mm to 12 mm) at which eroding the fill by w/2 raises the count of "
            "pieces of at least 5 mm2",
    "REF": "a track's reference plane is the plane layer next to it in S-1's order "
           "(F.Cu->In1.Cu, B.Cu->In2.Cu, and for a track on a plane layer the other plane), "
           "since the board has no stack-up block to say which is nearer",
}
# The user's ruling on READINGS (task R2): "all 27 accepted as written", 2026-11-22, on the
# sheet .superpowers/sdd/2026-11-07-one-clock/judge-readings-2026-11-22.md, which quotes
# each reading. Written out key by key, so that a reading added later is not accepted by
# default. A reading stays marked [judge's reading] beside its acceptance: it is still the
# judge's number, not a source's.
READINGS_RULING = ("the user, 2026-11-22: all 27 accepted as written "
                   "(.superpowers/sdd/2026-11-07-one-clock/judge-readings-2026-11-22.md)")
ACCEPTED_READINGS = {
    "G-10": "2026-11-22", "K-4": "2026-11-22", "G-20": "2026-11-22", "S-7": "2026-11-22",
    "P-5": "2026-11-22", "K-35": "2026-11-22", "B-13-ALL": "2026-11-22",
    "FIXED": "2026-11-22", "K-2": "2026-11-22", "G-1": "2026-11-22", "G-3": "2026-11-22",
    "G-4": "2026-11-22", "G-5": "2026-11-22", "G-6": "2026-11-22", "G-16": "2026-11-22",
    "SHORT": "2026-11-22", "N-4": "2026-11-22", "N-1": "2026-11-22", "N-10": "2026-11-22",
    "B-13": "2026-11-22", "B-16": "2026-11-22", "B-18": "2026-11-22", "N-9": "2026-11-22",
    "K-33": "2026-11-22", "K-23": "2026-11-22", "NECK": "2026-11-22", "REF": "2026-11-22",
}
# Two readings were extended on this branch after the sheet was written (it quotes them as
# they stood on main): what the user accepted is the sheet's text; the extension has not
# been put to the user. Printed beside the acceptance.
READINGS_EXTENDED_SINCE_RULING = {
    "G-1": "extended by task R0 (a0915f8, mounting holes) after the sheet: the sheet's "
           "reading names H1 and H2 only; H3 to H10 were added",
    "FIXED": "extended by task R0 (a0915f8, mounting holes) after the sheet: its \"rule "
             "areas as in the reference\" sentence, with the mh_<hole> exception, is not on "
             "the sheet",
}
SHORT_RATIO = 1.5
# The plane layer next to each copper layer in S-1's order (L1 signal, L2 ground, L3
# power, L4 signal); for the two plane layers, the other plane.
ADJACENT_PLANE = {"F.Cu": "In1.Cu", "In1.Cu": "In2.Cu", "In2.Cu": "In1.Cu", "B.Cu": "In2.Cu"}


def mm(v: float) -> float:
    return v / 1e6


def nm(v: float) -> int:
    return int(round(v * 1e6))


def V(x: float, y: float):
    return pcbnew.VECTOR2I(nm(x), nm(y))


def octi(dx: float, dy: float) -> float:
    a, b = abs(dx), abs(dy)
    return max(a, b) + (math.sqrt(2) - 1) * min(a, b)


def mst_length(pts) -> float:
    """Prim's minimum spanning tree under the 45-degree (octilinear) metric."""
    pts = list(dict.fromkeys((round(x, 3), round(y, 3)) for x, y in pts))
    if len(pts) < 2:
        return 0.0
    inf = float("inf")
    best = [inf] * len(pts)
    used = [False] * len(pts)
    best[0] = 0.0
    total = 0.0
    for _ in range(len(pts)):
        i = min((k for k in range(len(pts)) if not used[k]), key=lambda k: best[k])
        used[i] = True
        total += best[i]
        for k in range(len(pts)):
            if not used[k]:
                d = octi(pts[i][0] - pts[k][0], pts[i][1] - pts[k][1])
                if d < best[k]:
                    best[k] = d
    return total


def seg_point_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)), t


def rect_poly(x0, y0, x1, y1):
    p = pcbnew.SHAPE_POLY_SET()
    p.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        p.Append(nm(x), nm(y))
    return p


def overlap_mm2(a, b) -> float:
    c = pcbnew.SHAPE_POLY_SET(a)
    c.BooleanIntersection(b)
    return c.Area() / 1e12


def poly_bbox(p):
    bb = p.BBox()
    return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))


def bbox_hit(a, b, gap=0.0):
    return a[0] <= b[2] + gap and b[0] <= a[2] + gap and a[1] <= b[3] + gap and b[1] <= a[3] + gap


def short_list(names, n=6) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" ... (+{len(names) - n})" if len(names) > n else "")


MAXERR = 5000  # 5 um polygon approximation


# ---------------------------------------------------------------------------------------
# The board as the judge sees it: straight track pieces, vias, pads and filled pour
# islands, each with its net, layer and plan-view bounding box.
# ---------------------------------------------------------------------------------------
class Seg:
    __slots__ = ("net", "layer", "x1", "y1", "x2", "y2", "w", "obj", "lid")

    def __init__(self, net, layer, lid, x1, y1, x2, y2, w, obj):
        self.net, self.layer, self.lid = net, layer, lid
        self.x1, self.y1, self.x2, self.y2, self.w, self.obj = x1, y1, x2, y2, w, obj

    @property
    def length(self):
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def bbox(self):
        h = self.w / 2
        return (min(self.x1, self.x2) - h, min(self.y1, self.y2) - h,
                max(self.x1, self.x2) + h, max(self.y1, self.y2) + h)

    def poly(self):
        p = pcbnew.SHAPE_POLY_SET()
        if isinstance(self.obj, pcbnew.PCB_TRACK) and self.obj.GetClass() == "PCB_TRACK":
            self.obj.TransformShapeToPolygon(p, self.lid, 0, MAXERR, pcbnew.ERROR_INSIDE)
        else:  # an arc's chord: rebuild it as a straight track of the same width
            t = pcbnew.PCB_TRACK(None)
            t.SetStart(V(self.x1, self.y1))
            t.SetEnd(V(self.x2, self.y2))
            t.SetWidth(nm(self.w))
            t.SetLayer(self.lid)
            t.TransformShapeToPolygon(p, self.lid, 0, MAXERR, pcbnew.ERROR_INSIDE)
        return p

    def __repr__(self):
        return (f"track {self.net} {self.layer} w{self.w:.2f} "
                f"({self.x1:.2f},{self.y1:.2f})-({self.x2:.2f},{self.y2:.2f})")


class Via:
    __slots__ = ("net", "x", "y", "r", "layers", "obj")

    def __init__(self, net, x, y, r, layers, obj):
        self.net, self.x, self.y, self.r, self.layers, self.obj = net, x, y, r, layers, obj

    @property
    def bbox(self):
        return (self.x - self.r, self.y - self.r, self.x + self.r, self.y + self.r)

    def poly(self, lid):
        p = pcbnew.SHAPE_POLY_SET()
        self.obj.TransformShapeToPolygon(p, lid, 0, MAXERR, pcbnew.ERROR_INSIDE)
        return p

    def __repr__(self):
        return f"via {self.net} ({self.x:.2f},{self.y:.2f})"


class Pad:
    __slots__ = ("ref", "num", "net", "layers", "x", "y", "attr", "obj", "lib", "_poly",
                 "reach")

    def __init__(self, ref, num, net, layers, x, y, attr, obj, lib):
        self.ref, self.num, self.net, self.layers = ref, num, net, layers
        self.x, self.y, self.attr, self.obj, self.lib = x, y, attr, obj, lib
        self._poly = {}
        sz = obj.GetSize(obj.GetLayerSet().CuStack()[0]) if layers else None
        # half the pad's diagonal: no copper of the pad lies farther from its centre
        self.reach = math.hypot(mm(sz.x), mm(sz.y)) / 2 if sz is not None else 0.0

    def poly(self, layer_name, lid, clearance=0.0):
        key = (layer_name, round(clearance, 4))
        if key not in self._poly:
            p = pcbnew.SHAPE_POLY_SET()
            self.obj.TransformShapeToPolygon(p, lid, nm(clearance), MAXERR, pcbnew.ERROR_INSIDE)
            self._poly[key] = p
        return self._poly[key]

    def __repr__(self):
        return f"{self.ref}.{self.num or '(unnumbered)'} ({self.x:.2f},{self.y:.2f})"


class Island:
    __slots__ = ("net", "layer", "zone", "poly", "bbox", "area")

    def __init__(self, net, layer, zone, poly):
        self.net, self.layer, self.zone, self.poly = net, layer, zone, poly
        self.bbox = poly_bbox(poly)
        self.area = poly.Area() / 1e12

    def __repr__(self):
        b = self.bbox
        return (f"pour {self.net} '{self.zone}' {self.layer} "
                f"({b[0]:.1f},{b[1]:.1f})-({b[2]:.1f},{b[3]:.1f})")


class Model:
    def __init__(self, path: Path, fill: bool = True):
        self.path = path
        b = pcbnew.LoadBoard(str(path))
        self.board = b
        if fill:
            pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        b.BuildConnectivity()
        self.unconnected = b.GetConnectivity().GetUnconnectedCount(False)
        self.lid = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
        self.cu = list(self.lid)
        self.segs: list[Seg] = []
        self.vias: list[Via] = []
        for t in b.GetTracks():
            cls = t.GetClass()
            net = t.GetNetname()
            if cls == "PCB_VIA":
                layers = [b.GetLayerName(l) for l in t.GetLayerSet().CuStack()
                          if b.GetLayerName(l) in self.lid]
                self.vias.append(Via(net, mm(t.GetPosition().x), mm(t.GetPosition().y),
                                     mm(t.GetWidth(pcbnew.F_Cu)) / 2, layers, t))
                continue
            lname = b.GetLayerName(t.GetLayer())
            w = mm(t.GetWidth())
            if cls == "PCB_ARC":
                pts = _arc_points(t)
            else:
                pts = [(mm(t.GetStart().x), mm(t.GetStart().y)),
                       (mm(t.GetEnd().x), mm(t.GetEnd().y))]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                self.segs.append(Seg(net, lname, t.GetLayer(), x1, y1, x2, y2, w, t))
        self.pads: list[Pad] = []
        self.fps = {}
        for f in b.GetFootprints():
            ref = f.GetReference()
            self.fps[ref] = f
            lib = str(f.GetFPID().GetLibItemName())
            for p in f.Pads():
                layers = [b.GetLayerName(l) for l in p.GetLayerSet().CuStack()
                          if b.GetLayerName(l) in self.lid]
                if not layers:
                    continue
                self.pads.append(Pad(ref, p.GetNumber(), p.GetNetname(), layers,
                                     mm(p.GetPosition().x), mm(p.GetPosition().y),
                                     p.GetAttribute(), p, lib))
        self.pad_by = {(p.ref, p.num): p for p in self.pads if p.num}
        self.islands: list[Island] = []
        self.zones_meta = []
        self.rule_areas = []
        for z in b.Zones():
            if z.GetIsRuleArea():
                self.rule_areas.append(z)
                continue
            layers = [b.GetLayerName(l) for l in z.GetLayerSet().Seq()
                      if b.GetLayerName(l) in self.lid]
            self.zones_meta.append({"net": z.GetNetname(), "name": z.GetZoneName(),
                                    "layers": layers, "zone": z})
            for ln in layers:
                fp = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(self.lid[ln]))
                fp.Unfracture()
                for i in range(fp.OutlineCount()):
                    self.islands.append(Island(z.GetNetname(), ln, z.GetZoneName() or "",
                                               fp.UnitSet(i)))
        self.pad_nets = {p.net for p in self.pads if p.net}
        self.copper_nets = ({s.net for s in self.segs} | {v.net for v in self.vias}
                            | {i.net for i in self.islands})
        self._graphs = {}
        self._items = {}

    # -- per-net views ---------------------------------------------------------------
    def segs_of(self, net):
        return [s for s in self.segs if s.net == net]

    def vias_of(self, net):
        return [v for v in self.vias if v.net == net]

    def islands_of(self, net):
        return [i for i in self.islands if i.net == net]

    def track_length(self, net):
        return sum(s.length for s in self.segs if s.net == net)

    def graph(self, net) -> "NetGraph":
        if net not in self._graphs:
            self._graphs[net] = NetGraph(self, net)
        return self._graphs[net]

    def copper_items(self, nets=None, layers=None):
        """(item, layer name, polygon thunk, bbox) for every track piece, via and pour
        island, optionally only on some nets or layers."""
        if nets is None and layers is not None and len(layers) == 1:
            key = layers[0]
            if key not in self._items:
                self._items[key] = self._copper_items(None, layers)
            return self._items[key]
        return self._copper_items(nets, layers)

    def _copper_items(self, nets, layers):
        out = []
        for s in self.segs:
            if (nets is None or s.net in nets) and (layers is None or s.layer in layers):
                out.append((s, s.layer, s.poly, s.bbox))
        for v in self.vias:
            if nets is None or v.net in nets:
                for ln in v.layers:
                    if layers is None or ln in layers:
                        out.append((v, ln, (lambda v=v, ln=ln: v.poly(self.lid[ln])), v.bbox))
        for i in self.islands:
            if (nets is None or i.net in nets) and (layers is None or i.layer in layers):
                out.append((i, i.layer, (lambda i=i: i.poly), i.bbox))
        return out

    def touching(self, pad: Pad, clearance=0.0, nets=None, exclude_net=None):
        """Copper items (not pads) that overlap `pad`, grown by `clearance`, on any of
        the pad's layers."""
        hits = []
        for ln in pad.layers:
            pp = pad.poly(ln, self.lid[ln], clearance)
            pb = poly_bbox(pp)
            for item, il, thunk, bb in self.copper_items(nets, [ln]):
                if exclude_net is not None and item.net == exclude_net:
                    continue
                if not bbox_hit(pb, bb):
                    continue
                if overlap_mm2(pp, thunk()) > 1e-4:
                    hits.append(f"{item!r} on {ln}")
        return hits


def _arc_points(t):
    s, m, e = t.GetStart(), t.GetMid(), t.GetEnd()
    (x1, y1), (x2, y2), (x3, y3) = [(mm(p.x), mm(p.y)) for p in (s, m, e)]
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [(x1, y1), (x3, y3)]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(y - uy, x - ux) for x, y in ((x1, y1), (x2, y2), (x3, y3)))

    def norm(a):
        return a % (2 * math.pi)
    sweep = norm(a3 - a1)
    if not (norm(a2 - a1) <= sweep):
        sweep -= 2 * math.pi
    n = max(2, int(abs(sweep) * r / 0.25) + 1)
    return [(ux + r * math.cos(a1 + sweep * k / n), uy + r * math.sin(a1 + sweep * k / n))
            for k in range(n + 1)]


# ---------------------------------------------------------------------------------------
# One net's copper as a graph: track ends, vias and pads are nodes; tracks are edges with
# a length and a width; a pour island joins every node that touches it, and crossing it
# costs the straight distance between the two contacts (or, for squares, the sheet
# estimate of READINGS["N-10"]).
# ---------------------------------------------------------------------------------------
class NetGraph:
    def __init__(self, model: Model, net: str):
        self.net = net
        self.xy = []
        self.key = {}
        self.adj = []
        self.zone_of = {}      # node -> list of island ids
        self.contacts = []     # island id -> list of nodes
        self.islands = model.islands_of(net)
        segs = model.segs_of(net)
        vias = model.vias_of(net)
        pads = [p for p in model.pads if p.net == net]

        self.rad = []          # a node's contact radius: half a track's width, a via's or
                               # a pad's half-extent, for the pour bounds of N-10

        def node(k, x, y, r=0.0):
            if k not in self.key:
                self.key[k] = len(self.xy)
                self.xy.append((x, y))
                self.adj.append([])
                self.rad.append(r)
            i = self.key[k]
            self.rad[i] = max(self.rad[i], r)
            return i

        def edge(a, b, length, w, kind):
            self.adj[a].append((b, length, w, kind))
            self.adj[b].append((a, length, w, kind))

        ends = []  # (node, layer, x, y, w)
        for s in segs:
            a = node(("e", s.layer, round(s.x1, 4), round(s.y1, 4)), s.x1, s.y1, s.w / 2)
            b = node(("e", s.layer, round(s.x2, 4), round(s.y2, 4)), s.x2, s.y2, s.w / 2)
            edge(a, b, s.length, s.w, "track")
            ends.append((a, s.layer, s.x1, s.y1, s.w))
            ends.append((b, s.layer, s.x2, s.y2, s.w))
        # T-junctions: an end that lands on another piece's middle
        for n, ln, x, y, w in ends:
            for s in segs:
                if s.layer != ln:
                    continue
                d, t = seg_point_dist(x, y, s.x1, s.y1, s.x2, s.y2)
                if d <= max(w, s.w) / 2 and 1e-6 < t < 1 - 1e-6:
                    a = self.key[("e", s.layer, round(s.x1, 4), round(s.y1, 4))]
                    b = self.key[("e", s.layer, round(s.x2, 4), round(s.y2, 4))]
                    if n not in (a, b):
                        edge(n, a, t * s.length, s.w, "track")
                        edge(n, b, (1 - t) * s.length, s.w, "track")
        self.via_nodes = []
        for i, v in enumerate(vias):
            vn = node(("v", i), v.x, v.y, v.r)
            self.via_nodes.append(vn)
            for n, ln, x, y, w in ends:
                if ln in v.layers and math.hypot(x - v.x, y - v.y) <= v.r + 1e-3:
                    edge(vn, n, 0.0, None, "via")
        self.pad_node = {}
        pad_nodes = []
        for p in pads:
            pn = node(("p", p.ref, p.num, round(p.x, 4), round(p.y, 4)), p.x, p.y, p.reach)
            self.pad_node.setdefault((p.ref, p.num), pn)
            pad_nodes.append((p, pn))
            reach = p.reach
            for n, ln, x, y, w in ends:
                if ln in p.layers and abs(x - p.x) <= reach + w and abs(y - p.y) <= reach + w:
                    pp = p.poly(ln, model.lid[ln])
                    if pp.SquaredDistance(V(x, y)) <= nm(w / 2) ** 2:
                        edge(pn, n, 0.0, None, "pad")
            for i, v in enumerate(vias):
                if abs(v.x - p.x) > reach or abs(v.y - p.y) > reach:
                    continue
                if any(ln in v.layers for ln in p.layers):
                    ln = next(ln for ln in p.layers if ln in v.layers)
                    if p.poly(ln, model.lid[ln]).SquaredDistance(V(v.x, v.y)) == 0:
                        edge(pn, self.via_nodes[i], 0.0, None, "pad")
        for zi, isl in enumerate(self.islands):
            members = []
            for p, pn in pad_nodes:
                # A pad joins a pour of its own net when the fill comes within the pad's
                # reach plus a thermal gap: KiCad's fill spokes span that gap.
                if isl.layer in p.layers and bbox_hit((p.x, p.y, p.x, p.y), isl.bbox, p.reach + 0.6):
                    if isl.poly.SquaredDistance(V(p.x, p.y)) <= nm(p.reach + 0.6) ** 2:
                        members.append(pn)
            for i, v in enumerate(vias):
                if isl.layer in v.layers and bbox_hit(v.bbox, isl.bbox, 0.1):
                    if isl.poly.SquaredDistance(V(v.x, v.y)) <= nm(v.r + 0.05) ** 2:
                        members.append(self.via_nodes[i])
            for n, ln, x, y, w in ends:
                if ln == isl.layer and isl.poly.SquaredDistance(V(x, y)) <= nm(w / 2 + 0.01) ** 2:
                    members.append(n)
            members = list(dict.fromkeys(members))
            self.contacts.append(members)
            for n in members:
                self.zone_of.setdefault(n, []).append(zi)

    def pad(self, ref, num):
        return self.pad_node.get((ref, num))

    def shortest(self, sources, targets=None, use_zones=True, weight="length", hop=None):
        """Dijkstra. Returns (dist, parent) where parent[n] = (prev, kind, width).
        `hop(island, u, v, d)` replaces the cost of crossing a pour (squares only)."""
        dist = {}
        parent = {}
        heap = [(0.0, s) for s in sources]
        for s in sources:
            dist[s] = 0.0
            parent[s] = None
        remaining = set(targets) if targets is not None else None
        done = set()
        zone_entry = {}  # island -> (entry node, dist at entry)
        while heap:
            d, u = heapq.heappop(heap)
            if u in done or d > dist.get(u, float("inf")):
                continue
            done.add(u)
            if remaining is not None:
                remaining.discard(u)
                if not remaining:
                    break
            for v, L, w, kind in self.adj[u]:
                c = L if weight == "length" else (L / w if w else 0.0)
                nd = d + c
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    parent[v] = (u, kind, w)
                    heapq.heappush(heap, (nd, v))
            if not use_zones:
                continue
            ux, uy = self.xy[u]
            for zi in self.zone_of.get(u, []):
                e = zone_entry.get(zi)
                if e is not None and hop is None:
                    ex, ey = self.xy[e[0]]
                    if d >= e[1] + self._zcost(math.hypot(ux - ex, uy - ey), weight) - 1e-9:
                        continue  # reached through the pour already; nothing to improve
                else:
                    zone_entry[zi] = (u, d)
                for v in self.contacts[zi]:
                    if v == u:
                        continue
                    vx, vy = self.xy[v]
                    dd = math.hypot(ux - vx, uy - vy)
                    nd = d + (hop(zi, u, v, dd) if hop is not None and weight != "length"
                              else self._zcost(dd, weight))
                    if nd < dist.get(v, float("inf")):
                        dist[v] = nd
                        parent[v] = (u, "pour", zi)
                        heapq.heappush(heap, (nd, v))
        return dist, parent

    @staticmethod
    def _zcost(d, weight):
        if weight == "length":
            return d
        return max(0.0, math.log(d / 0.3) / math.pi) if d > 0.3 else 0.0

    def path(self, parent, t):
        out = []
        while parent.get(t) is not None:
            u, kind, w = parent[t]
            out.append((u, t, kind, w))
            t = u
        return list(reversed(out))

    def is_contact(self, n):
        return bool(self.zone_of.get(n))


# ---------------------------------------------------------------------------------------
# Results. A layer-2 check is a list of CLAUSES, one per thing its rule asks; each clause
# is PASS, FAIL, N/A or NOT CHECKED with its own evidence. The check is FAIL if any clause
# fails, else NOT CHECKED if any clause could not be checked, else PASS if any passed,
# else N/A. A check whose nets are absent or unrouted is N/A as a whole. In partial mode
# PENDING ranks below NOT CHECKED and above PASS: a check with a clause still waiting is
# PENDING unless another clause FAILs or cannot be checked.
# ---------------------------------------------------------------------------------------
class Result:
    def __init__(self, rid, title, layer):
        self.id, self.title, self.layer = rid, title, layer
        self.status = NA
        self.evidence: list[str] = []
        self.reading = None
        self.reading_key = None
        self.cls = None     # layer 2: "prohibition", "requirement" or both (CLASSES)
        self.data = None    # a gate's structured figures, for the JSON
        self.clauses: list[dict] = []
        self.whole = None   # a status for the whole check (N/A, PENDING, or a crash)
        self.so_far = None  # a whole-check PENDING: the status it had before
        self.pending_on: list[str] = []

    def set(self, status, *evidence):
        """A gate's status, or a whole-check status (N/A: nets absent or unrouted)."""
        self.status = status
        self.whole = status
        self.evidence.extend(evidence)
        return self

    def clause(self, name, status, *evidence):
        self.clauses.append({"clause": name, "status": status, "evidence": list(evidence)})
        return status

    def finish(self):
        if self.whole is not None or not self.clauses:
            return self
        st = [c["status"] for c in self.clauses]
        self.status = (FAIL if FAIL in st else NOT_CHECKED if NOT_CHECKED in st
                       else PENDING if PENDING in st else PASS if PASS in st else NA)
        return self

    def as_dict(self):
        d = {"id": self.id, "title": self.title, "status": self.status,
             "evidence": self.evidence}
        if self.cls:
            d["class"] = self.cls
        if self.so_far is not None:
            d["so_far"] = self.so_far
            d["pending_on"] = self.pending_on
        if self.clauses:
            d["clauses"] = self.clauses
        if self.data is not None:
            d["data"] = self.data
        if self.reading:
            d["judges_reading"] = self.reading
            d["reading_accepted"] = ACCEPTED_READINGS.get(self.reading_key)
            if self.reading_key in READINGS_EXTENDED_SINCE_RULING:
                d["reading_extended_since_ruling"] = \
                    READINGS_EXTENDED_SINCE_RULING[self.reading_key]
        return d

    def reading_mark(self) -> str:
        """The summary's provenance for a check that uses a reading: still the judge's,
        and accepted by the user on the date ACCEPTED_READINGS gives."""
        if not self.reading:
            return ""
        s = "  [judge's reading]"
        if self.reading_key in ACCEPTED_READINGS:
            s += f" [reading accepted {ACCEPTED_READINGS[self.reading_key]}]"
        if self.reading_key in READINGS_EXTENDED_SINCE_RULING:
            s += " [extended since the ruling, task R0]"
        return s


GATE_IDS = frozenset(["DRC-RULES", "DRC", "CONNECTIVITY", "PLACEMENT", "REGION", "NET-LISTS"])
GAP_MM = 5.0        # G-10 [judge's reading]: no gap wider than this inside the corridor
AT_PINS_MM = 6.0    # P-5 [judge's reading]: "at the pins"
AT_J19_MM = 10.0    # G-20 [judge's reading]: "R224 is placed at J19"
K35_MM = 5.0        # K-35 [judge's reading]: "sits at each supply pin pair"
CLEAR_MM = BAND_W   # K-4 [judge's reading]: "clear of INTAN_AO* and the INA105s"
NEAR_TIE_MM = 10.0  # G-4, G-5, G-6 [judge's reading]
N16_WEB_MM = 0.2    # N-16: via copper to any SMD pad (constraints.md; not a reading)
N16_EXEMPT_PARTS, N16_EXEMPT_PAD = ("U3", "U4"), "9"   # N-16's exemption: P-6's PowerPADs

# Rules that bear on routing and that layer 2 does not judge, printed with every run so
# that "every check PASS or N/A" is not read as "every rule met".
NOT_JUDGED = [
    ("DRC", "B-5 to B-9, B-14, B-23 (slots, keep-outs, bands), N-1 to N-8 class widths, "
            "N-11 to N-14, S-5, X-7: by DRC with the rules loaded (layer 1)"),
    ("PLACEMENT", "K-5 to K-8, K-14, K-17, K-25 to K-28, K-30, K-31, K-34, K-36, K-37, "
                  "P-1, P-7, P-9, P-13, P-14, G-2, G-12 to G-15, G-21 to G-23, B-10 to "
                  "B-12, B-19, M-*: placement, held by the PLACEMENT gate only as far as "
                  "check_breakout_placement.py checks it"),
    ("a person", "K-1/K-3 (undecided, Open question 2), K-4's DGND-side clause, K-9, "
                 "K-12, K-15, K-18 to K-22, K-24, K-29, K-32, P-4, P-6, P-10, P-12, G-8, "
                 "G-24, X-9, X-10, V-4: not implemented"),
]

# ---------------------------------------------------------------------------------------
# Task R2: every layer-2 clause is a PROHIBITION or a REQUIREMENT. A prohibition is broken
# by copper that is present (or by placement or netlist facts that hold now), so partial
# mode judges it on the board as it stands, on every net. A requirement needs copper to
# exist (a path, a length, a pair, a ratio), so, unless a stage has declared it DUE, it is
# PENDING until every net it names is in --complete-nets. Per check: (key, which with the
# check's ID is the clause's DUE key, e.g. "G-1/overlap"; clause pattern, matched from the
# clause name's start; class; for a requirement, the nets it needs, as a function of the
# Judge; why).
# ---------------------------------------------------------------------------------------
PRO, REQ = "prohibition", "requirement"


def _nets(*names):
    return lambda j: frozenset(names)


SHORT_WHY = ("SHORT: routed length against the MST of the net's pads, which needs the "
             "whole net routed")
NO_POUR = [
    ("own-pour", r"no pour on the net", PRO, None, "any pour of the net breaks it"),
    ("touch", r"no other pour touches its copper", PRO, None,
     "any other net's pour touching its pads or tracks breaks it"),
]
CLASSES = {
    "G-10": [
        ("foreign", r"only G-11 nets cross", PRO, None, "any other net's copper across x = 170 breaks it"),
        ("once", r"each G-11 net crosses once", PRO, None,
         "a second crossing breaks it; later copper only adds crossings"),
        ("stretch", r"every crossing at y", PRO, None, "a crossing outside y 50 to 165 breaks it"),
        ("corridor", r"one corridor", REQ, lambda j: G11_NETS,
         "a gap between crossings can close when another G-11 net crosses: judged once every "
         "G-11 net is routed"),
        ("all-cross", r"every G-11 net crosses x = 170", REQ, lambda j: G11_NETS,
         "G-11 lists the nets that must cross: needs each one's crossing to exist (fix round "
         "1, M-3)"),
    ],
    "K-2": [("agnd", r"no barcode copper in AGND", PRO, None,
             "any barcode-family copper inside AGND breaks it")],
    "K-4": [
        ("island", r"inside its own island", PRO, None, "any piece outside its island breaks it"),
        ("short", r"the shortest path \(SHORT\)", REQ,
         _nets("BARCODE_NI", "BARCODE_INTAN", "BARCODE_INTAN_BNC"), SHORT_WHY),
        ("clear", r"Intan barcode clear of", PRO, None,
         "any Intan barcode track within 2.5 mm of INTAN_AO* copper or an INA105 pad breaks it"),
    ],
    "G-1": [
        ("pads", r"only NT1 has pads on both", PRO, None, "a netlist fact of the placed board"),
        ("overlap", r"no AGND/DGND pour overlap", PRO, None, "any overlapping fill breaks it"),
        ("holes", r"the \d+ holes of MECHANICAL", PRO, None,
         "a net on a hole, or any copper touching one, breaks it"),
        ("hardware", r"no mounting-hardware path", PRO, None,
         "a property of hardware, not copper: NOT CHECKED at every stage"),
    ],
    "G-3": [
        ("tie", r"only its tie joins it to its plane", PRO, None, "a netlist fact of the placed board"),
        ("plane", r"not poured on a plane layer", PRO, None, "any such pour breaks it"),
    ],
    "G-4": [("near", r"NT2 within", PRO, None, "placement: holds on the board as placed")],
    "G-5": [
        ("near", r"NT3 within", PRO, None, "placement: holds on the board as placed"),
        ("pour", r"no CHASSIS_GND pour", PRO, None, "any CHASSIS_GND pour breaks it"),
        ("trace", r"J56 reaches NT3 by tracks", REQ, _nets("CHASSIS_GND"),
         "needs the J56-to-NT3 track to exist"),
    ],
    "G-6": [
        ("near", r"NT4 within", PRO, None, "placement: holds on the board as placed"),
        ("pins", r"ISO_5V_RTN has exactly its five pins", PRO, None, "a netlist fact of the placed board"),
    ],
    "G-7": [("ties", r"the four ties as drawn", PRO, None, "footprints: hold on the board as placed")],
    "G-16": [
        ("pin62", r"pin 62 on AGND", PRO, None, "a netlist fact of the placed board"),
        ("bare", r"unused pins bare", PRO, None, "any copper on an unused pin breaks it"),
    ],
    "G-17": NO_POUR, "G-18": NO_POUR, "G-19": NO_POUR,
    "G-20": NO_POUR + [("r224", r"R224 within", PRO, None, "placement: holds on the board as placed")],
    "S-7": [
        ("layer", r"each plane net on its plane layer", PRO, None,
         "any plane-net pour on the other plane layer breaks it"),
        ("region", r"each plane-net pour inside its domain's region", PRO, None,
         "any fill outside its domain breaks it"),
        ("poured", r"every plane net poured on its plane layer", REQ, lambda j: PLANE_NETS,
         "S-7 \"L3 carries ...\" and S-6's L2 grounds: needs each plane's pour to exist (fix "
         "round 1, I-2)"),
    ],
    "X-6": [("clear", r"post-mount pads clear by", PRO, None,
             "any copper within 0.2 mm of a retention terminal breaks it")],
    "P-5": [
        ("pin7", r"pin 7 gets no copper", PRO, None, "any copper on pin 7 breaks it"),
        ("short", r"FB and NR/SS on short traces \(SHORT\)", REQ,
         _nets("U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS"), SHORT_WHY),
        ("caps", r"input and output capacitors within", PRO, None, "placement: pad distances"),
        ("fbnr", r"FB and NR/SS parts within", PRO, None, "placement: pad distances"),
    ],
    "P-8": [
        ("via", r"no via", PRO, None, "any U63_VIN via breaks it"),
        ("layer", r"F\.Cu only", PRO, None, "any U63_VIN track off F.Cu breaks it"),
        ("pour", r"no pour", PRO, None, "any U63_VIN pour breaks it"),
        ("short", r"short \(SHORT\)", REQ, _nets("U63_VIN"), SHORT_WHY),
        ("pins", r"two pins only", PRO, None, "a netlist fact of the placed board"),
        ("plus5v", r"no contact with \+5V", PRO, None, "any U63_VIN track touching +5V copper breaks it"),
    ],
    "K-11": [
        ("via", r"no via", PRO, None, "any via on the two clamp nets breaks it"),
        ("layer", r"F\.Cu only", PRO, None, "any clamp-net track off F.Cu breaks it"),
        ("pour", r"no pour", PRO, None, "any clamp-net pour breaks it"),
        ("short", r"short \(SHORT\)", REQ, _nets("A_PD1_CLAMP", "A_PD2_CLAMP"), SHORT_WHY),
    ],
    "N-16": [
        ("in-pad", r"no via's hole or annulus overlaps", PRO, None, "any via in an SMD pad breaks it"),
        ("web", r"every via 0\.2 mm from every SMD pad", PRO, None,
         "any via within 0.2 mm of an SMD pad breaks it"),
    ],
    "K-35": [
        ("joined", r"a capacitor at each pin pair", REQ, lambda j: j.k35_nets(),
         "\"joined\": needs the bypass capacitors' supply and ground copper to exist"),
        ("other-part", r"U\d+ \(.*\) supply pair", REQ, lambda j: j.k35_nets(),
         "the same requirement, for a part whose pins K-35 does not name"),
    ],
    "N-4": [
        ("gnd-pins", r"all twelve NI_GND pins used", REQ, _nets("NI_GND"), "needs copper on every pin"),
        ("5v-pins", r"both NI_5V pins", REQ, _nets("NI_5V"), "needs copper on both pins"),
        ("symmetric", r"symmetric copper", REQ, _nets("NI_5V"), "compares the two pins' finished copper"),
    ],
    "N-1": [
        ("width", r"every track before the", REQ, _nets("+5V"),
         "a width along the path to the plane, which needs the path and the plane"),
        ("pours", r"pours of \S+ on the way", REQ, _nets("+5V"), "the same path"),
        ("reaches", r"reaches its", REQ, _nets("+5V"), "needs the path to reach the plane"),
    ],
    "N-2": [
        ("width", r"every track before the", REQ, _nets("-12V"),
         "a width along the path to the plane, which needs the path and the plane"),
        ("pours", r"pours of \S+ on the way", REQ, _nets("-12V"), "the same path"),
        ("reaches", r"reaches its", REQ, _nets("-12V"), "needs the path to reach the plane"),
    ],
    "N-10": [
        ("squares", r"at most 83 squares", REQ, _nets("+5V"), "squares along a finished path"),
        ("reached", r"every LED resistor reached", REQ, _nets("+5V"), "needs every path to exist"),
    ],
    "B-13": [
        ("c162-1", r"C162\.1 to U63\.1", REQ, lambda j: j.nets_of_pins(("C162", "1")),
         "needs the C162-to-U63 trace to exist"),
        ("c162-2", r"C162\.2 to U63\.6", REQ, lambda j: j.nets_of_pins(("C162", "2")),
         "needs the C162-to-U63 trace to exist"),
        ("outer-end", r"each trace leaves its pad from the outer end", PRO, None,
         "any track end in a pad's inner half breaks it"),
        ("pad2", r"C162 pad 2 never on the INTAN_GND plane", PRO, None,
         "any INTAN_GND fill touching the pad breaks it"),
    ],
    "B-13-ALL": [("outer-end", r"every straddler pad left from its outer end", PRO, None,
                  "any track end in a straddler pad's inner half breaks it")],
    "B-16": [
        ("primary", r"no Intan copper within", PRO, None, "any Intan copper there breaks it"),
        ("secondary", r"no DGND copper under a secondary pin", PRO, None, "any DGND copper there breaks it"),
    ],
    "B-17": [
        ("only", r"only MUX_OUTn and AGND cross", PRO, None, "any other net crossing y = 165 breaks it"),
        ("agnd-count", r"AGND crosses at most eight times", PRO, None,
         "a ninth crossing breaks it; later copper only adds crossings"),
    ],
    "B-18": [
        ("once", r"each MUX_OUTn crosses once", REQ, lambda j: frozenset(MUX_OUT + ["AGND"]),
         "\"once\" needs the crossing to exist"),
        ("beside", r"an AGND crossing beside it", REQ, lambda j: frozenset(MUX_OUT + ["AGND"]),
         "needs both traces of the pair"),
        ("sense", r"an AGND sense trace", REQ, lambda j: frozenset(MUX_OUT + ["AGND"]),
         "needs the sense trace to exist"),
        ("length", r"MUX_OUTn and its sense trace within", REQ, lambda j: frozenset(MUX_OUT + ["AGND"]),
         "compares two finished lengths"),
        ("at-mux", r"the sense trace taken from the pour", REQ, lambda j: frozenset(MUX_OUT + ["AGND"]),
         "needs the sense trace and the AGND pour"),
    ],
    "N-9": [
        ("vbus", r"J18 pin 1", PRO, None, "any copper on J18 pin 1 breaks it"),
        ("length", r"lengths within", REQ, _nets("USB_DP_PI", "USB_DM_PI"), "compares finished lengths"),
        ("coupled", r"coupled", REQ, _nets("USB_DP_PI", "USB_DM_PI"), "needs both traces of the pair"),
        ("dgnd", r"over unbroken DGND", REQ, _nets("USB_DP_PI", "USB_DM_PI", "DGND"),
         "needs the pair and the DGND plane under it"),
    ],
    "K-33": [
        ("joined", r"both outputs joined to the LED", REQ, lambda j: j.k33_nets(),
         "needs the legs to exist"),
        ("legs", r"legs at most 10 mm", REQ, lambda j: j.k33_nets(), "compares two finished legs"),
    ],
    "K-23": [
        ("length", r"each leg's twin within 1\.0 mm", REQ, lambda j: j.k23_nets(),
         "compares finished lengths"),
        ("vias", r"each leg's twin with as many vias", REQ, lambda j: j.k23_nets(),
         "compares finished via counts"),
        ("block", r"a 2-gang block", REQ, lambda j: j.k23_nets(),
         "a property of the finished block: NOT CHECKED once due"),
    ],
    "FIXED": [
        ("parts", r"\d+ fixed parts", PRO, None, "a moved fixed part breaks it"),
        ("edges", r"Edge\.Cuts", PRO, None, "a changed outline or slot breaks it"),
        ("areas", r"the \d+ rule areas", PRO, None, "a changed or added rule area breaks it"),
        ("reference", r"against a reference", PRO, None, "no reference: NOT CHECKED at every stage"),
    ],
}


def check_class(rid) -> str:
    kinds = sorted({k for _, _, k, _, _ in CLASSES.get(rid, [])})
    return "+".join(kinds) if kinds else PRO


def clause_keys(rid) -> list[str]:
    """The DUE keys of a check's clauses, "G-1/overlap" and so on."""
    return [f"{rid}/{k}" for k in dict.fromkeys(s[0] for s in CLASSES.get(rid, []))]


class Judge:
    def __init__(self, model: Model, contract: dict, nets_filter=None, region=None,
                 reference=None, complete=None, due=()):
        self.m = model
        self.contract = contract
        self.nets_filter = nets_filter
        self.region = region
        self.reference = reference      # (label, text) for FIXED
        # partial mode (task R2; the stage contract of fix round 1): the nets the stages
        # complete, and what they declare DUE (check IDs, or check/clause keys)
        self.complete = None if complete is None else frozenset(complete)
        self.due = frozenset(due)
        self._open = {}
        self.dom = cbp.derive_domains(contract)
        self.net_dom = self.dom["net_dom"]
        self.pin_net = {(ref, pin): net for net, nodes in contract["nets"].items()
                        for ref, pin, *_ in nodes}
        present = set(model.pad_nets)
        if nets_filter is not None:
            present &= nets_filter
        self.present = present
        self.results: list[Result] = []
        self._edges = {}

    # the nets a requirement needs (CLASSES)
    def nets_of_pins(self, *pins):
        return frozenset(self.pin_net[p] for p in pins if p in self.pin_net)

    OPTOS = [f"U{n}" for n in range(56, 62)] + ["U64", "U65"]

    def k35_nets(self):
        pins, out = self.dom["pins"], set()
        for u in self.OPTOS:
            if u not in pins:
                continue
            nums = (("10", "9", "15", "16")
                    if self.contract["component_values"][u].startswith("ACSL-6400") else ("9", "12"))
            out |= {pins[u][k] for k in nums if pins[u].get(k)}
        return frozenset(out)

    def k33_legs(self):
        """The paralleled LED-driver outputs: (net, (ref, pin), (ref, pin)) per pair."""
        legs = []
        for a, b in (("U8", "U70"), ("U9", "U71"), ("U11", "U73"), ("U10", "U72")):
            for net, nodes in self.contract["nets"].items():
                fa = [pin for ref, pin, fn, _ in nodes if ref == a and fn.startswith("Y")]
                fb = [pin for ref, pin, fn, _ in nodes if ref == b and fn.startswith("Y")]
                if fa and fb:
                    legs.append((net, (a, fa[0]), (b, fb[0])))
        return legs

    def k33_nets(self):
        return frozenset(n for n, *_ in self.k33_legs())

    K23_PAIRS = [p for m_ in ("A_MISC1", "A_MISC2", "A_MISC3")
                 for p in ((f"{m_}_MID", f"{m_}_SHLD_MID"), (f"{m_}_DIV", f"{m_}_SHLD_DIV"))]

    def k23_nets(self):
        return frozenset(n for p in self.K23_PAIRS for n in p)

    # -- task R2: classes, DUE and PENDING in partial mode ---------------------------
    def net_open(self, net) -> int:
        """KiCad's unrouted connections of one net (0: every pad joined)."""
        if net not in self._open:
            self._open[net] = listed_unconnected(self.m, {net}).get(net, 0)
        return self._open[net]

    def classify(self, r: Result):
        """Each clause's class and key (CLASSES). In partial mode: a DUE clause (its key, or
        its check's ID, in --due) is judged on the copper present and anything but PASS is
        a FAIL; a requirement that is not due waits, PENDING, until every net it needs is
        complete; a prohibition is always judged on all the copper."""
        specs = CLASSES.get(r.id, [])
        r.cls = check_class(r.id)
        partial = self.complete is not None
        due_whole = r.id in self.due
        due_keys = {d for d in self.due if d.startswith(r.id + "/")}
        matched = set()
        for c in r.clauses:
            spec = None if c["clause"] == "the check ran" else \
                next((s for s in specs if re.match(s[1], c["clause"])), None)
            # a crash is never held back as PENDING; a clause CLASSES does not name is
            # judged on the copper present, the stricter treatment
            c["class"] = (None if c["clause"] == "the check ran" else
                          spec[2] if spec else "unclassified")
            c["key"] = f"{r.id}/{spec[0]}" if spec else None
            if not partial:
                continue
            if due_whole or c["key"] in due_keys:
                matched.add(c["key"])
                self._due(c, r.id if due_whole else c["key"])
            elif spec is not None and spec[2] == REQ:
                missing = sorted(set(spec[3](self)) & self.present - self.complete)
                if missing:
                    c["so_far"] = c["status"]
                    c["status"] = PENDING
                    c["pending_on"] = missing
        if not partial:
            return
        # a due clause the check did not write is not a PASS either; for a check due as a
        # whole, every clause it always writes (not the ones with \d, \S or .*, which
        # exist only in some cases)
        want = set(due_keys)
        if due_whole:
            want |= {f"{r.id}/{s[0]}" for s in specs if not re.search(r"\\[dS]|\.\*", s[1])}
        if r.whole is not None and (due_whole or due_keys):
            # a due check that is N/A as a whole is not a PASS
            why = r.evidence[-1] if r.evidence else r.whole
            r.whole = None
            if due_whole and not want - matched:
                r.clauses.append(self._due({"clause": "the check as a whole", "status": NA,
                                            "evidence": [why], "class": None, "key": None},
                                           r.id))
        for key in sorted(want - matched):
            pat = next((s for s in specs if f"{r.id}/{s[0]}" == key), None)
            r.clauses.append(self._due(
                {"clause": re.sub(r"\\(.)", r"\1", pat[1]) if pat else key, "status": NA,
                 "evidence": ["the check wrote no such clause: its copper is not there"
                              + (f" ({r.evidence[-1]})" if r.evidence else "")],
                 "class": pat[2] if pat else None, "key": key}, r.id if due_whole else key))
        if r.whole is None and not due_whole:
            # a requirement that wrote no clause because its nets carry no copper yet (a
            # SHORT clause, say) would otherwise leave the check PASS: it is PENDING too.
            # Clauses that exist only in some cases (patterns with \d, \S or .*) are left.
            for key, pat, kind, nets, _ in specs:
                if kind != REQ or re.search(r"\\[dS]|\.\*", pat) or \
                        f"{r.id}/{key}" in due_keys or \
                        any(re.match(pat, c["clause"]) for c in r.clauses):
                    continue
                missing = sorted(set(nets(self)) & self.present - self.complete)
                if missing:
                    r.clauses.append({"clause": re.sub(r"\\(.)", r"\1", pat), "status": PENDING,
                                      "evidence": ["not yet evaluated: its nets carry no copper "
                                                   "yet"], "class": REQ,
                                      "key": f"{r.id}/{key}", "so_far": NA,
                                      "pending_on": missing})
            return
        if r.whole != NA or due_whole:
            return
        # a whole-check N/A ("no copper routed yet") that a requirement still waits on
        need_all = set()
        for _, _, kind, nets, _ in specs:
            if kind == REQ:
                need_all |= set(nets(self)) & self.present
        missing = sorted(need_all - self.complete)
        if missing:
            r.so_far, r.whole, r.status, r.pending_on = NA, PENDING, PENDING, missing
            r.evidence.append(f"PENDING: a requirement of this check waits on {len(missing)} "
                              f"net(s) not complete: {short_list(missing)}")

    @staticmethod
    def _due(c, source):
        c["due"] = source
        if c["status"] != PASS:
            c["due_was"] = c["status"]
            c["evidence"] = [f"DUE ({source}): {c['status']}, not PASS"] + list(c["evidence"])
            c["status"] = FAIL
        return c

    # nets' state for the N/A rule
    def state(self, nets):
        nets = [n for n in nets if n in self.present]
        if not nets:
            return "absent"
        if not any(n in self.m.copper_nets for n in nets):
            return "unrouted"
        return "routed"

    def na_if(self, r: Result, nets, label=None) -> bool:
        s = self.state(nets)
        if s == "absent":
            r.set(NA, f"none of {label or 'its nets'} is on this board")
            return True
        if s == "unrouted":
            r.set(NA, f"{label or 'its nets'}: no copper routed yet")
            return True
        return False

    def add(self, rid, title, fn, reading=None):
        r = Result(rid, title, 2)
        r.reading = READINGS.get(reading) if reading else None
        r.reading_key = reading if r.reading else None
        try:
            fn(r)
        except Exception as e:  # a check that crashes is not a pass
            r.whole = None
            r.clause("the check ran", NOT_CHECKED, f"it raised {type(e).__name__}: {e}")
        self.classify(r)
        self.results.append(r.finish())
        return r

    def fp_xy(self, ref):
        f = self.m.fps.get(ref)
        return None if f is None else (mm(f.GetPosition().x), mm(f.GetPosition().y))

    def in_region(self, p):
        if self.region is None:
            return True
        x0, y0, x1, y1 = self.region
        return x0 <= p.x <= x1 and y0 <= p.y <= y1

    @staticmethod
    def verdict(bad):
        return FAIL if bad else PASS

    # ===================================================================================
    # Layer 2
    # ===================================================================================
    def run_rules(self):
        A = self.add
        A("G-10", "Only G-11's nets cross the AGND/DGND split, once each, in one corridor "
          "near NT1", self.g10, "G-10")
        A("K-2", "No barcode-family net enters, overlies or underlies the AGND region",
          self.k2, "K-2")
        A("K-4", "The isolated barcode nets: inside their islands, short, clear of the "
          "Intan outputs", self.k4, "K-4")
        A("G-1", "AGND and DGND join at NT1 only", self.g1, "G-1")
        A("G-3", "FAN_RTN, CHASSIS_GND, ISO_5V_RTN: one tie each, never flooded into "
          "their plane", self.g3, "G-3")
        A("G-4", "NT2 at the inlet beside NT1", self.g4, "G-4")
        A("G-5", "NT3 at the star; J56 reaches it by a dedicated trace", self.g5, "G-5")
        A("G-6", "NT4 at U63's secondary pins; ISO_5V_RTN has its five pins", self.g6, "G-6")
        A("G-7", "The four net ties are NetTie-2_SMD_Pad2.0mm as drawn", self.g7)
        A("G-16", "J2/J36: no copper on any unused (ground) pin; pin 62 on AGND",
          self.g16, "G-16")
        A("G-17", "AO_GND_TPC is a trace only: no pour carries or touches it",
          lambda r: self.no_pour(r, ["AO_GND_TPC"]))
        A("G-18", "The ten *_SHLD nets: no pour on or touching them",
          lambda r: self.no_pour(r, SHIELD_NETS))
        A("G-19", "ACCESIO_AGND kept out of every pour",
          lambda r: self.no_pour(r, ["ACCESIO_AGND"]))
        A("G-20", "ACCESIO_SHELL: no pour on or touching J19's shell pads; R224 at J19",
          self.g20, "G-20")
        A("S-7", "Plane nets poured on their own layer (S-6, S-7) and inside their own "
          "domain's region", self.s7, "S-7")
        A("X-6", "BNC post-mount pads: every net and pour clear by 0.2 mm", self.x6)
        A("P-5", "U3/U4: pin 7 bare; FB and NR/SS short; capacitors and FB/NR parts at "
          "the pins", self.p5, "P-5")
        A("P-8", "U63_VIN: one short F.Cu trace, no via, no pour, no +5V contact",
          self.p8, "SHORT")
        A("K-11", "A_PD1_CLAMP, A_PD2_CLAMP: F.Cu only, no via, short", self.k11, "SHORT")
        A("N-16", "No via in an SMD pad, and every via 0.2 mm from every SMD pad, its own "
          "net's included (P-6's U3/U4 pad 9 exempt)", self.n16)
        A("K-35", "A bypass capacitor at each optocoupler's isolated supply pin pair",
          self.k35, "K-35")
        A("N-4", "J45: both NI_5V pins with symmetric copper, all twelve NI_GND pins used",
          self.n4, "N-4")
        A("N-1", "+5V from D3 to its L3 plane at least 1.0 mm",
          lambda r: self.partial_width(r, "D3", "1", "+5V", 1.0), "N-1")
        A("N-2", "-12V from D2 to its L3 plane at least 0.8 mm",
          lambda r: self.partial_width(r, "D2", "2", "-12V", 0.8), "N-1")
        A("N-10", "+5V: at most 83 squares from D3 to the farthest LED resistor",
          self.n10, "N-10")
        A("B-13", "C162 and U63: shortest traces, left from the pads' outer ends",
          self.b13, "B-13")
        A("B-13-ALL", "Every straddler pad left from its outer end (routing-brief §4.1)",
          self.b13_all, "B-13-ALL")
        A("B-16", "No Intan copper at U2/U63's primary pins, no DGND copper under their "
          "secondary pins", self.b16, "B-16")
        A("B-17", "Only the sixteen MUX_OUTn/AGND traces cross the AGND/Intan line",
          self.b17)
        A("B-18", "Each MUX_OUTn beside its own AGND sense trace, equal lengths",
          self.b18, "B-18")
        A("N-9", "USB pair coupled, matched, over unbroken DGND; J18 pin 1 bare",
          self.n9, "N-9")
        A("K-33", "Paralleled LED-driver outputs join by short, equal traces", self.k33,
          "K-33")
        A("K-23", "The three /1-/2 dividers' legs laid out symmetrically", self.k23, "K-23")
        A("FIXED", "Panel connectors, straddlers, NT1, J1, slots and rule areas as in the "
          "reference (routing-brief §1, §5)", self.fixed, "FIXED")

    # -- G-10 ----------------------------------------------------------------------------
    def split_crossings(self):
        out = []
        for s in self.m.segs:
            sa, sb = s.x1 > SPLIT_X, s.x2 > SPLIT_X
            if sa == sb:
                continue
            t = (SPLIT_X - s.x1) / (s.x2 - s.x1)
            y = s.y1 + t * (s.y2 - s.y1)
            if 0.0 <= y < INTAN_LINE_Y:
                out.append((s.net, s.layer, y, "track"))
        strip = rect_poly(SPLIT_X - 1e-3, 0.0, SPLIT_X + 1e-3, INTAN_LINE_Y)
        for i in self.m.islands:
            if i.bbox[0] < SPLIT_X < i.bbox[2] and i.bbox[1] < INTAN_LINE_Y:
                c = pcbnew.SHAPE_POLY_SET(i.poly)
                c.BooleanIntersection(strip)
                if c.Area() > 0:
                    b = poly_bbox(c)
                    out.append((i.net, i.layer, (b[1] + b[3]) / 2,
                                f"pour y {b[1]:.1f}-{b[3]:.1f}"))
        return out

    def corridor(self):
        """The split crossings as a corridor: its span and distances from NT1 (layer 3)."""
        xs = self.split_crossings()
        nt1 = self.fp_xy("NT1") or (170.0, 94.5)
        if not xs:
            return {"crossings": 0}
        ys = sorted(y for _, _, y, _ in xs)
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        return {"crossings": len(xs), "span_y": [round(ys[0], 2), round(ys[-1], 2)],
                "span_mm": round(ys[-1] - ys[0], 2),
                "widest_gap_mm": round(max(gaps), 2) if gaps else 0.0,
                "nearest_to_NT1_mm": round(min(abs(y - nt1[1]) for y in ys), 2),
                "farthest_from_NT1_mm": round(max(abs(y - nt1[1]) for y in ys), 2)}

    def g10(self, r):
        xs = self.split_crossings()
        if not xs and self.state(G11_NETS) != "routed":
            return r.set(NA, "no copper crosses x = 170 and no G-11 net is routed")
        nt1 = self.fp_xy("NT1") or (170.0, 94.5)
        per = {}
        for net, ln, y, kind in xs:
            per.setdefault(net, []).append((ln, y, kind))

        def where(net, ln, y, kind):
            return f"{net} on {ln} at (170, {y:.2f}) [{kind}], {abs(y - nt1[1]):.1f} mm from NT1"
        foreign = [where(n, *c) for n, lst in sorted(per.items()) for c in lst if n not in G11_NETS]
        r.clause("only G-11 nets cross", self.verdict(foreign), *(foreign or [
            f"{len(xs)} crossings by {len(per)} nets, all in G-11"]))
        twice = [f"{n} crosses {len(lst)} times: " + ", ".join(f"{ln} y={y:.2f}" for ln, y, _ in lst)
                 for n, lst in sorted(per.items()) if n in G11_NETS and len(lst) > 1]
        r.clause("each G-11 net crosses once", self.verdict(twice), *twice)
        out = [where(n, *c) for n, lst in sorted(per.items()) for c in lst
               if not CORRIDOR_Y[0] <= c[1] <= CORRIDOR_Y[1]]
        r.clause(f"every crossing at y {CORRIDOR_Y[0]:.0f} to {CORRIDOR_Y[1]:.0f}",
                 self.verdict(out), *out)
        # "Those cross once each": G-11 is the list of nets that must cross, so each crosses
        # at least once (fix round 1, M-3)
        absent = sorted(n for n in G11_NETS & self.present if n not in per)
        r.clause("every G-11 net crosses x = 170", self.verdict(absent),
                 *([f"{len(absent)} G-11 net(s) do not cross: {short_list(absent, 12)}"] if absent
                   else [f"all {len(G11_NETS & self.present)} G-11 nets on the board cross"]))
        cor = self.corridor()
        ys = sorted(y for _, _, y, _ in xs)
        gaps = [(b - a, a, b) for a, b in zip(ys, ys[1:]) if b - a > GAP_MM]
        r.clause(f"one corridor: no gap over {GAP_MM} mm between crossings",
                 self.verdict(gaps),
                 *([f"gap of {g:.2f} mm between y = {a:.2f} and {b:.2f}" for g, a, b in gaps]
                   + ([f"span y {cor['span_y'][0]} to {cor['span_y'][1]} "
                       f"({cor['span_mm']} mm), {cor['nearest_to_NT1_mm']} to "
                       f"{cor['farthest_from_NT1_mm']} mm from NT1"] if xs else
                      ["nothing crosses x = 170"])))

    # -- K-2, K-4 ------------------------------------------------------------------------
    def outside(self, item_poly, bb, domain):
        """Area of a copper item outside a domain's region rectangles, in mm2."""
        p = item_poly()
        total = p.Area() / 1e12
        inside = sum(overlap_mm2(p, rect_poly(*rc)) for rc in cbp.REGIONS[domain]
                     if bbox_hit(bb, rc))
        return total - inside

    def k2(self, r):
        fam = sorted(n for n in self.contract["nets"]
                     if n.startswith("BARCODE_") or n.startswith("CAM_SYNC_"))
        if self.na_if(r, fam, "the barcode family"):
            return
        rects = [rect_poly(*rc) for rc in cbp.REGIONS["AGND"]]
        bad = []
        for item, ln, thunk, bb in self.m.copper_items(set(fam)):
            for rc, rp in zip(cbp.REGIONS["AGND"], rects):
                if bbox_hit(bb, rc) and bb[2] > rc[0] and bb[3] > rc[1]:
                    a = overlap_mm2(thunk(), rp)
                    if a > 1e-4:
                        bad.append(f"{item!r} on {ln}: {a:.3f} mm2 inside AGND {rc}")
                        break
        r.clause("no barcode copper in AGND", self.verdict(bad),
                 *(bad or [f"{len(fam)} nets checked, no copper in AGND"]))

    def k4(self, r):
        home = {"BARCODE_NI": "NI", "BARCODE_INTAN": "INTAN", "BARCODE_INTAN_BNC": "INTAN"}
        if self.na_if(r, list(home), "BARCODE_NI, BARCODE_INTAN, BARCODE_INTAN_BNC"):
            return
        routed = [n for n in home if n in self.m.copper_nets and n in self.present]
        bad = []
        for n in routed:
            for item, ln, thunk, bb in self.m.copper_items({n}):
                a = self.outside(thunk, bb, home[n])
                if a > 1e-3:
                    bad.append(f"{item!r} on {ln}: {a:.3f} mm2 outside the {home[n]} island")
        r.clause("inside its own island", self.verdict(bad), *bad)
        self.short_clause(r, routed, "the shortest path (SHORT)")
        intan = [n for n in ("BARCODE_INTAN", "BARCODE_INTAN_BNC") if n in routed]
        if intan:
            ao = {n for n in self.contract["nets"] if n.startswith("INTAN_AO")}
            inas = {f"U{k}" for k in range(39, 54, 2)}
            near = []
            for s in [s for s in self.m.segs if s.net in intan]:
                grown = pcbnew.SHAPE_POLY_SET()
                t = s.obj
                t.TransformShapeToPolygon(grown, s.lid, nm(CLEAR_MM), MAXERR, pcbnew.ERROR_INSIDE)
                gb = poly_bbox(grown)
                for item, ln, thunk, bb in self.m.copper_items(ao):
                    if bbox_hit(gb, bb) and overlap_mm2(grown, thunk()) > 1e-4:
                        near.append(f"{s!r} within {CLEAR_MM} mm of {item!r}")
                for p in self.m.pads:
                    if p.ref in inas and bbox_hit(gb, (p.x, p.y, p.x, p.y), p.reach):
                        if overlap_mm2(grown, p.poly(p.layers[0], self.m.lid[p.layers[0]])) > 1e-4:
                            near.append(f"{s!r} within {CLEAR_MM} mm of {p!r}")
            r.clause(f"Intan barcode clear of INTAN_AO* and the INA105s by {CLEAR_MM} mm",
                     self.verdict(near), *near)

    # -- G-1 -----------------------------------------------------------------------------
    def g1(self, r):
        if self.state(["AGND", "DGND"]) == "absent":
            return r.set(NA, "AGND and DGND are not on this board")
        both = sorted({p.ref for p in self.m.pads if p.net == "AGND"}
                      & {p.ref for p in self.m.pads if p.net == "DGND"})
        r.clause("only NT1 has pads on both", self.verdict(both != ["NT1"]),
                 f"parts with pads on both AGND and DGND: {both}")
        a = [i for i in self.m.islands if i.net == "AGND"]
        d = [i for i in self.m.islands if i.net == "DGND"]
        ov = []
        for ia in a:
            for idd in d:
                if bbox_hit(ia.bbox, idd.bbox):
                    area = overlap_mm2(ia.poly, idd.poly)
                    if area > 0.01:
                        c = pcbnew.SHAPE_POLY_SET(ia.poly)
                        c.BooleanIntersection(idd.poly)
                        ov.append(f"pour overlap {area:.2f} mm2 in plan: {ia!r} over {idd!r}, "
                                  f"at {tuple(round(v, 1) for v in poly_bbox(c))}")
        r.clause("no AGND/DGND pour overlap in plan", self.verdict(ov),
                 *(ov or [f"{len(a)} AGND and {len(d)} DGND pour islands"]))
        hw = []
        for ref in sorted(cbp.MECHANICAL):
            for p in [p for p in self.m.pads if p.ref == ref]:
                if p.net:
                    hw.append(f"{p!r} carries net {p.net}")
                hw.extend(f"copper touches {p!r}: {h}" for h in self.m.touching(p))
        r.clause(f"the {len(cbp.MECHANICAL)} holes of MECHANICAL (H1, H2 the IO Board's "
                 f"standoffs; H3-H10 the board's mounting holes) carry no net and no copper",
                 self.verdict(hw), *hw)
        r.clause("no mounting-hardware path", NOT_CHECKED,
                 "the screws and standoffs are not on the board; whether one joins two "
                 "pours through hardware cannot be read from it")

    # -- G-3 to G-7 ----------------------------------------------------------------------
    def g3(self, r):
        ties = [("FAN_RTN", "NT2", "DGND"), ("CHASSIS_GND", "NT3", "DGND"),
                ("ISO_5V_RTN", "NT4", "INTAN_GND")]
        if self.state([t[0] for t in ties]) == "absent":
            return r.set(NA, "none of FAN_RTN, CHASSIS_GND, ISO_5V_RTN is on this board")
        join, flood = [], []
        for net, tie, plane in ties:
            if net not in self.present:
                continue
            both = sorted({p.ref for p in self.m.pads if p.net == net}
                          & {p.ref for p in self.m.pads if p.net == plane})
            if both != [tie]:
                join.append(f"{net}: parts joining it to {plane}: {both} (only {tie} may)")
            for zm in self.m.zones_meta:
                if zm["net"] == net and set(zm["layers"]) & {"In1.Cu", "In2.Cu"}:
                    flood.append(f"{net} poured on a plane layer: '{zm['name']}' {zm['layers']}")
        r.clause("only its tie joins it to its plane", self.verdict(join), *join)
        r.clause("not poured on a plane layer", self.verdict(flood), *flood)

    def _near(self, r, a, b, limit, name):
        pa, pb = self.fp_xy(a), self.fp_xy(b)
        if pa is None or pb is None:
            return r.clause(name, NA, f"{a} or {b} is not on this board")
        d = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
        r.clause(name, PASS if d <= limit else FAIL,
                 f"{a} at ({pa[0]:.2f},{pa[1]:.2f}), {b} at ({pb[0]:.2f},{pb[1]:.2f}): "
                 f"{d:.2f} mm apart (limit {limit} mm)")

    def g4(self, r):
        if self.state(["FAN_RTN"]) == "absent":
            return r.set(NA, "FAN_RTN is not on this board")
        self._near(r, "NT2", "NT1", NEAR_TIE_MM, f"NT2 within {NEAR_TIE_MM} mm of NT1")

    def g5(self, r):
        if self.state(["CHASSIS_GND"]) == "absent":
            return r.set(NA, "CHASSIS_GND is not on this board")
        self._near(r, "NT3", "NT1", NEAR_TIE_MM, f"NT3 within {NEAR_TIE_MM} mm of NT1")
        pours = [f"CHASSIS_GND poured: '{zm['name']}' {zm['layers']}"
                 for zm in self.m.zones_meta if zm["net"] == "CHASSIS_GND"]
        r.clause("no CHASSIS_GND pour", self.verdict(pours), *pours)
        if not self.m.segs_of("CHASSIS_GND"):
            r.clause("J56 reaches NT3 by tracks", NA, "CHASSIS_GND has no track yet")
            return
        g = self.m.graph("CHASSIS_GND")
        a, b = g.pad("J56", "1"), g.pad("NT3", "1")
        dist, _ = g.shortest([a], [b], use_zones=False) if a is not None else ({}, None)
        r.clause("J56 reaches NT3 by tracks", PASS if b in dist else FAIL,
                 f"J56 to NT3 by tracks: {dist[b]:.1f} mm" if b in dist else
                 "J56 pad 1 does not reach NT3 pad 1 by tracks")

    def g6(self, r):
        if self.state(["ISO_5V_RTN"]) == "absent":
            return r.set(NA, "ISO_5V_RTN is not on this board")
        p, q = self.m.pad_by.get(("NT4", "1")), self.m.pad_by.get(("U63", "6"))
        if p is None or q is None:
            r.clause(f"NT4 within {NEAR_TIE_MM} mm of U63 pad 6", FAIL, "NT4.1 or U63.6 missing")
        else:
            d = math.hypot(p.x - q.x, p.y - q.y)
            r.clause(f"NT4 within {NEAR_TIE_MM} mm of U63 pad 6",
                     PASS if d <= NEAR_TIE_MM else FAIL,
                     f"NT4 pad 1 {p!r} is {d:.2f} mm from U63 pad 6 {q!r}")
        want = {("U63", "6"), ("C137", "2"), ("C138", "2"), ("C162", "2"), ("NT4", "1")}
        got = {(x.ref, x.num) for x in self.m.pads if x.net == "ISO_5V_RTN"}
        r.clause("ISO_5V_RTN has exactly its five pins", self.verdict(got != want),
                 f"pads {sorted(got)}" + ("" if got == want else f"; G-6 names {sorted(want)}"))

    def g7(self, r):
        if self.state(["AGND", "DGND", "FAN_RTN", "CHASSIS_GND", "ISO_5V_RTN",
                       "INTAN_GND"]) == "absent":
            return r.set(NA, "none of the four ties' nets is on this board")
        bad, ok = [], []
        for ref in ("NT1", "NT2", "NT3", "NT4"):
            f = self.m.fps.get(ref)
            if f is None:
                bad.append(f"{ref} is not on the board")
                continue
            name = str(f.GetFPID().GetLibItemName())
            nums = sorted(p.GetNumber() for p in f.Pads())
            (bad if name != "NetTie-2_SMD_Pad2.0mm" or nums != ["1", "2"] else ok).append(
                f"{ref} is {name} with pads {nums}")
        r.clause("the four ties as drawn", self.verdict(bad), *(bad or ok))

    # -- pads that must get no copper ----------------------------------------------------
    def bare_clause(self, r, name, pads, clearance, label):
        if self.region is not None:
            pads = [p for p in pads if self.in_region(p)]
            if not pads:
                return r.clause(name, NA, f"no {label} inside the region")
        bad = [f"{p!r} ({p.net or 'no net'}) touched by {h}"
               for p in pads for h in self.m.touching(p, clearance)]
        if bad:
            return r.clause(name, FAIL, *bad)
        if not self.m.copper_nets:
            return r.clause(name, NA, f"no copper on the board yet; {len(pads)} {label} bare")
        r.clause(name, PASS, f"{len(pads)} {label}: no copper within {clearance} mm")

    def g16(self, r):
        unused = {(ref, pin) for net, nodes in self.contract["nets"].items()
                  if net.startswith("unconnected-") for ref, pin, *_ in nodes
                  if ref in ("J2", "J36")}
        pads = [p for p in self.m.pads if (p.ref, p.num) in unused]
        if not pads:
            return r.set(NA, "J2 and J36 are not on this board")
        bad = [f"{ref} pad 62 is on {self.m.pad_by[(ref, '62')].net}, not AGND"
               for ref in ("J2", "J36") if (ref, "62") in self.m.pad_by
               and self.m.pad_by[(ref, "62")].net != "AGND" and "AGND" in self.present]
        if "AGND" in self.present:
            r.clause("pin 62 on AGND", self.verdict(bad), *bad)
        self.bare_clause(r, "unused pins bare", pads, 0.0, "unused J2/J36 pins")

    def x6(self, r):
        pads = [p for p in self.m.pads if p.lib == "BNC_Dual_RA_Isolated" and not p.num]
        if not pads:
            return r.set(NA, "no BNC body on this board")
        self.bare_clause(r, "post-mount pads clear by 0.2 mm", pads, 0.2, "BNC post-mount pads")

    def no_pour(self, r, nets):
        ns = set(nets)
        present = [n for n in nets if n in self.present]
        own_pour = [f"pour '{zm['name']}' on {zm['net']}, {zm['layers']}"
                    for zm in self.m.zones_meta if zm["net"] in ns]
        touch = []
        others = [i for i in self.m.islands if i.net not in ns]
        for p in [p for p in self.m.pads if p.net in ns]:
            for ln in p.layers:
                pp = p.poly(ln, self.m.lid[ln])
                for i in others:
                    if i.layer == ln and bbox_hit(poly_bbox(pp), i.bbox) \
                            and overlap_mm2(pp, i.poly) > 1e-4:
                        touch.append(f"{i!r} touches {p!r} ({p.net})")
        for s in [s for s in self.m.segs if s.net in ns]:
            for i in others:
                if i.layer == s.layer and bbox_hit(s.bbox, i.bbox) \
                        and overlap_mm2(s.poly(), i.poly) > 1e-4:
                    touch.append(f"{i!r} touches {s!r}")
        if not own_pour and not touch:
            if not present:
                return r.set(NA, f"none of {', '.join(nets[:3])}"
                             f"{'...' if len(nets) > 3 else ''} is on this board")
            if not any(n in self.m.copper_nets for n in present):
                return r.set(NA, "the nets have no copper yet and no pour touches them")
        r.clause("no pour on the net", self.verdict(own_pour), *own_pour)
        r.clause("no other pour touches its copper", self.verdict(touch), *touch)

    def g20(self, r):
        self.no_pour(r, ["ACCESIO_SHELL"])
        if r.whole is not None and r.whole == NA and "ACCESIO_SHELL" not in self.present:
            return
        r.whole = None
        a = [p for p in self.m.pads if p.ref == "R224" and p.net == "ACCESIO_SHELL"]
        b = [p for p in self.m.pads if p.ref == "J19" and p.net == "ACCESIO_SHELL"]
        if not a or not b:
            return r.clause(f"R224 within {AT_J19_MM} mm of J19", NA, "R224 or J19 absent")
        d, pa, pb = min(((math.hypot(p.x - q.x, p.y - q.y), p, q) for p in a for q in b),
                        key=lambda t: t[0])
        r.clause(f"R224 within {AT_J19_MM} mm of J19", PASS if d <= AT_J19_MM else FAIL,
                 f"{pa!r} to {pb!r}: {d:.2f} mm")

    # -- S-7 -----------------------------------------------------------------------------
    def s7(self, r):
        grounds = {"AGND", "DGND", "NI_GND", "INTAN_GND"}
        planes = [n for n in PLANE_NETS if n in self.present]
        if not planes:
            return r.set(NA, "no plane net is on this board")
        isl = [i for i in self.m.islands if i.net in PLANE_NETS]
        if not isl:
            return r.set(NA, "no plane net is poured yet")
        wrong = []
        for zm in self.m.zones_meta:
            n = zm["net"]
            if n not in PLANE_NETS:
                continue
            want = "In1.Cu" if n in grounds else "In2.Cu"
            for ln in zm["layers"]:
                if ln in ("In1.Cu", "In2.Cu") and ln != want:
                    wrong.append(f"'{zm['name']}' {n} on {ln}; S-6/S-7 put {n} on {want}")
        r.clause("each plane net on its plane layer (grounds L2, rails L3)",
                 self.verdict(wrong), *(wrong or [f"{len(isl)} plane-net pour islands"]))
        out = []
        for i in isl:
            dom = cbp.SEED_DOMAIN[i.net]
            a = self.outside(lambda i=i: i.poly, i.bbox, dom)
            if a > 0.01:
                out.append(f"{i!r}: {a:.2f} mm2 outside the {dom} region")
        r.clause("each plane-net pour inside its domain's region", self.verdict(out), *out)
        # S-7's own words, "L3 carries ...", and S-6's L2 grounds: every plane net on the
        # board has a fill on its plane layer, inside its domain's region (fix round 1, I-2)
        unpoured, poured = [], []
        for n in sorted(planes):
            want = "In1.Cu" if n in grounds else "In2.Cu"
            dom = cbp.SEED_DOMAIN[n]
            area = sum(overlap_mm2(i.poly, rect_poly(*rc)) for i in isl
                       if i.net == n and i.layer == want
                       for rc in cbp.REGIONS[dom] if bbox_hit(i.bbox, rc))
            (poured if area > 1.0 else unpoured).append(
                f"{n}: {area:.0f} mm2 of fill on {want} inside the {dom} region")
        r.clause("every plane net poured on its plane layer, in its region",
                 self.verdict(unpoured), *(unpoured or [f"{len(poured)} plane nets poured"]
                                           + poured))

    # -- P-5, P-8, K-11 ------------------------------------------------------------------
    def short_net(self, net):
        pads = [(p.x, p.y) for p in self.m.pads if p.net == net]
        return self.m.track_length(net), mst_length(pads)

    def short_clause(self, r, nets, name="short (SHORT)"):
        """SHORT over the nets with copper. On a net whose pads are not all joined the
        routed length is only a lower bound on the finished one (copper is never taken
        up): over the ratio it is a FAIL; under it, NOT CHECKED, never a PASS (fix round
        1, M-1)."""
        bad, open_, ok = [], [], []
        for n in nets:
            if n not in self.m.copper_nets:
                continue
            L, lb = self.short_net(n)
            line = f"{n}: {L:.2f} mm routed against {lb:.2f} mm MST"
            k = self.net_open(n)
            if lb > 0 and L > SHORT_RATIO * lb + 1e-6:
                bad.append(line + f" (over {SHORT_RATIO}x"
                           + (", already, with the net not yet joined)" if k else ")"))
            elif k:
                open_.append(line + f"; the net is not fully joined ({k} unrouted "
                             "connection(s)), so the length is only a lower bound")
            else:
                ok.append(line)
        if bad or open_ or ok:
            r.clause(name, FAIL if bad else NOT_CHECKED if open_ else PASS,
                     *(bad or open_ or ok))

    def at_pins(self, r, name, parts, ic):
        far, ok = [], []
        for c in parts:
            cps = [p for p in self.m.pads if p.ref == c and p.net]
            ips = [p for p in self.m.pads if p.ref == ic and p.net]
            pairs = [(math.hypot(p.x - q.x, p.y - q.y), p, q) for p in cps for q in ips
                     if p.net == q.net and not p.net.startswith("unconnected")]
            if not pairs:
                continue
            d, p, q = min(pairs, key=lambda t: t[0])
            (far if d > AT_PINS_MM else ok).append(f"{p!r} to {q!r}: {d:.2f} mm")
        if far or ok:
            r.clause(name, self.verdict(far), *(far or ok))

    def p5(self, r):
        if not any(u in self.m.fps for u in ("U3", "U4")) or \
                self.state(["U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS"]) == "absent":
            return r.set(NA, "U3, U4 and their FB/NR nets are not on this board")
        pin7 = [p for p in (self.m.pad_by.get((u, "7")) for u in ("U3", "U4"))
                if p is not None and self.in_region(p)]
        bad = [f"{p!r} (DNC) touched by {h}" for p in pin7 for h in self.m.touching(p)]
        r.clause("pin 7 gets no copper", self.verdict(bad), *bad)
        self.short_clause(r, ["U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS"],
                          "FB and NR/SS on short traces (SHORT)")
        self.at_pins(r, f"input and output capacitors within {AT_PINS_MM} mm of the pins",
                     ["C11", "C14"], "U3")
        self.at_pins(r, f"input and output capacitors within {AT_PINS_MM} mm of the pins",
                     ["C17", "C20"], "U4")
        self.at_pins(r, f"FB and NR/SS parts within {AT_PINS_MM} mm of the pins",
                     ["R1", "R2", "C13", "C12"], "U3")
        self.at_pins(r, f"FB and NR/SS parts within {AT_PINS_MM} mm of the pins",
                     ["R3", "R4", "C19", "C18"], "U4")

    def via_layer_pour(self, r, nets):
        vias = [f"via on {n}: {v!r}" for n in nets for v in self.m.vias_of(n)]
        r.clause("no via", self.verdict(vias), *vias)
        off = [f"{n} off F.Cu: {s!r}" for n in nets for s in self.m.segs_of(n) if s.layer != "F.Cu"]
        r.clause("F.Cu only", self.verdict(off), *off)
        pours = [f"pour '{zm['name']}' on {zm['net']}" for zm in self.m.zones_meta
                 if zm["net"] in set(nets)]
        r.clause("no pour", self.verdict(pours), *pours)
        self.short_clause(r, nets)

    def p8(self, r):
        if self.na_if(r, ["U63_VIN"]):
            return
        self.via_layer_pour(r, ["U63_VIN"])
        got = sorted((p.ref, p.num) for p in self.m.pads if p.net == "U63_VIN")
        r.clause("two pins only, L1.2 and U63.2", self.verdict(got != [("L1", "2"), ("U63", "2")]),
                 f"pads {got}")
        touch = []
        for s in self.m.segs_of("U63_VIN"):
            for item, ln, thunk, bb in self.m.copper_items({"+5V"}, [s.layer]):
                if bbox_hit(s.bbox, bb) and overlap_mm2(s.poly(), thunk()) > 1e-4:
                    touch.append(f"{s!r} touches {item!r}")
        r.clause("no contact with +5V", self.verdict(touch), *touch)

    def k11(self, r):
        nets = ["A_PD1_CLAMP", "A_PD2_CLAMP"]
        if self.na_if(r, nets):
            return
        self.via_layer_pour(r, nets)

    # -- N-16 ----------------------------------------------------------------------------
    def n16(self, r):
        """constraints.md N-16 (task J2, fix round 1). DRC holds it too, by a
        physical_clearance rule in breakout.kicad_dru; this check holds it without DRC
        (--skip-drc, region copies) and names each via and pad."""
        vias = [v for v in self.m.vias if self.in_region(v)]
        if not vias:
            return r.set(NA, "no via on this board" if self.region is None
                         else "no via inside the region")
        # Exempt (constraints.md N-16): pad 9 of U3 and U4, the PowerPADs P-6 asks vias
        # under. Paste-only aperture pads have no copper layer, so Model.pads holds none.
        smd = [p for p in self.m.pads if p.attr == pcbnew.PAD_ATTRIB_SMD and p.layers
               and not (p.ref in N16_EXEMPT_PARTS and p.num == N16_EXEMPT_PAD)]
        inside, near = [], []
        for v in vias:
            for p in smd:
                for ln in set(p.layers) & set(v.layers):
                    # the pad's own outline, grown by the web, against the via's box: a
                    # custom pad's primitives can reach past its anchor's size
                    grown = p.poly(ln, self.m.lid[ln], N16_WEB_MM)
                    if not bbox_hit(poly_bbox(grown), v.bbox):
                        continue
                    vp = v.poly(self.m.lid[ln])
                    what = f"{v!r} and {p!r} ({p.net or 'no net'}) on {ln}"
                    if overlap_mm2(p.poly(ln, self.m.lid[ln]), vp) > 1e-4:
                        inside.append(what + ": the via overlaps the pad")
                    elif overlap_mm2(grown, vp) > 1e-4:
                        near.append(what + f": under {N16_WEB_MM} mm apart")
        r.clause("no via's hole or annulus overlaps an SMD pad", self.verdict(inside),
                 *(inside or [f"{len(vias)} vias, {len(smd)} SMD pads: none overlaps"]))
        r.clause(f"every via {N16_WEB_MM} mm from every SMD pad, its own net's included",
                 self.verdict(near),
                 *(near or [f"{len(vias)} vias: none within {N16_WEB_MM} mm of an SMD pad"]))

    # -- K-35 ----------------------------------------------------------------------------
    def k35(self, r):
        pins = self.dom["pins"]
        optos = self.OPTOS
        pairs = []
        for u in optos:
            if u not in pins:
                continue
            if self.contract["component_values"][u].startswith("ACSL-6400"):
                pairs += [(u, "10", "9"), (u, "15", "16")]       # K-35's own pin list
            elif {pins[u].get("9"), pins[u].get("12")} <= self.m.copper_nets & self.present:
                r.clause(f"{u} ({self.contract['component_values'][u]}) supply pair", NOT_CHECKED,
                         "K-35 names the ACSL-6400's pins 10/15 against 9/16; this part's "
                         "isolated supply pins differ and no source pairs them")
        pairs = [(u, v, g) for u, v, g in pairs
                 if pins[u].get(v) in self.present and pins[u].get(g) in self.present]
        if not pairs:
            r.clauses = []
            return r.set(NA, "the optocouplers' isolated supplies are not on this board")
        pairs = [(u, v, g) for u, v, g in pairs
                 if pins[u][v] in self.m.copper_nets and pins[u][g] in self.m.copper_nets]
        if not pairs:
            r.clauses = []
            return r.set(NA, "the isolated supplies are not routed yet")
        caps = {}
        for ref, val in self.contract["component_values"].items():
            if ref.startswith("C") and ref in pins and len(pins[ref]) == 2:
                caps[ref] = pins[ref]
        bad, ok = [], []
        used = set()
        for u, v, gp in pairs:
            rail, gnd = pins[u][v], pins[u][gp]
            pv, pg = self.m.pad_by.get((u, v)), self.m.pad_by.get((u, gp))
            cand = []
            for c, cp in caps.items():
                if c in used or set(cp.values()) != {rail, gnd}:
                    continue
                cr = self.m.pad_by.get((c, next(k for k, n in cp.items() if n == rail)))
                cg = self.m.pad_by.get((c, next(k for k, n in cp.items() if n == gnd)))
                if cr is None or cg is None:
                    continue
                dr, dg = math.hypot(cr.x - pv.x, cr.y - pv.y), math.hypot(cg.x - pg.x, cg.y - pg.y)
                cand.append((max(dr, dg), c, cr, cg, dr, dg))
            cand.sort(key=lambda t: t[0])
            if not cand or cand[0][0] > K35_MM:
                bad.append(f"{u} pins {v}/{gp} ({rail}/{gnd}): no capacitor with both pads "
                           f"within {K35_MM} mm of the pins"
                           + (f" (nearest {cand[0][1]}: {cand[0][4]:.2f} / {cand[0][5]:.2f} mm)"
                              if cand else ""))
                continue
            _, c, cr, cg, dr, dg = cand[0]
            used.add(c)
            joined = []
            for cpad, ipad in ((cr, pv), (cg, pg)):
                cl = self.kicad_cluster(cpad)
                joined.append(cl is not None and ipad.obj.m_Uuid.AsString() in cl)
            line = (f"{u} pins {v}/{gp}: {c} at {dr:.2f} / {dg:.2f} mm, "
                    f"{'joined' if all(joined) else 'NOT joined'} to both pins")
            (ok if all(joined) else bad).append(line)
        r.clause(f"a capacitor at each pin pair (both pads within {K35_MM} mm, joined)",
                 self.verdict(bad), *(bad or ok))

    def track_path(self, net, a, b):
        if net not in self.m.copper_nets:
            return None
        g = self.m.graph(net)
        na, nb = g.pad(*a), g.pad(*b)
        if na is None or nb is None:
            return None
        dist, _ = g.shortest([na], [nb], use_zones=False)
        return dist.get(nb)

    # -- N-4 -----------------------------------------------------------------------------
    def to_plane(self, g: NetGraph, node):
        """Track length from a node to the first pour contact, the narrowest track on the
        way, and the vias passed."""
        if g.is_contact(node):
            return 0.0, None, 0
        targets = [n for n in range(len(g.xy)) if g.is_contact(n)]
        if not targets:
            return None, None, 0
        dist, parent = g.shortest([node], targets, use_zones=False)
        reach = [t for t in targets if t in dist]
        if not reach:
            return None, None, 0
        t = min(reach, key=lambda n: dist[n])
        path = g.path(parent, t)
        widths = [w for _, _, kind, w in path if kind == "track" and w]
        vn = set(g.via_nodes)
        return dist[t], (min(widths) if widths else None), sum(1 for _, v, _, _ in path if v in vn)

    def used(self, g: NetGraph, ref, num):
        n = g.pad(ref, num)
        return n is not None and (bool(g.adj[n]) or g.is_contact(n))

    def pin_copper(self, g: NetGraph, ref, num):
        """A pin's own copper: the layers on which a pour of its net joins it, and the
        tracks hanging off it (through tracks and vias, stopping at pour contacts)."""
        start = g.pad(ref, num)
        layers = sorted({g.islands[z].layer for z in g.zone_of.get(start, [])})
        seen, stack, counted = {start}, [start], {}
        vn = set(g.via_nodes)
        vias = 0
        while stack:
            u = stack.pop()
            if u != start and g.is_contact(u):
                continue
            for v, L, w, kind in g.adj[u]:
                if kind == "track":
                    counted[(min(u, v), max(u, v), round(L, 4))] = w
                if v not in seen:
                    seen.add(v)
                    vias += v in vn
                    stack.append(v)
        widths = [w for w in counted.values() if w]
        total = sum(k[2] for k in counted)
        return layers, round(total, 3), (min(widths) if widths else None), vias

    def n4(self, r):
        if self.na_if(r, ["NI_5V", "NI_GND"]):
            return
        g5, gg = self.m.graph("NI_5V"), self.m.graph("NI_GND")
        gnd = [num for ref, num, *_ in self.contract["nets"]["NI_GND"] if ref == "J45"]
        unused = [n for n in gnd if not self.used(gg, "J45", n)]
        r.clause("all twelve NI_GND pins used", self.verdict(unused),
                 f"J45 NI_GND pins with no copper: {sorted(unused, key=int)} (of {len(gnd)})"
                 if unused else f"{len(gnd)} NI_GND pins have copper")
        bare = [n for n in ("8", "14") if not self.used(g5, "J45", n)]
        r.clause("both NI_5V pins (8, 14) used", self.verdict(bare),
                 *[f"J45 pin {n} (NI_5V) has no copper" for n in bare])
        if bare:
            return r.clause("symmetric copper", NA, "a pin is bare")
        c8, c14 = self.pin_copper(g5, "J45", "8"), self.pin_copper(g5, "J45", "14")
        line = (f"pin 8: pour on {c8[0]}, {c8[1]} mm of track off it, narrowest {c8[2]}, "
                f"vias {c8[3]}; pin 14: pour on {c14[0]}, {c14[1]} mm, narrowest {c14[2]}, "
                f"vias {c14[3]}")
        asym = (c8[0] != c14[0] or abs(c8[1] - c14[1]) > 1.0 or c8[2] != c14[2]
                or c8[3] != c14[3])
        r.clause("symmetric copper", self.verdict(asym), line)

    # -- N-1, N-2 ------------------------------------------------------------------------
    def partial_width(self, r, ref, num, net, wmin, plane_layer="In2.Cu"):
        """From the pad, walk tracks, vias and any pour of the net that is NOT on its plane
        layer (S-7: L3), and stop at the first contact with its L3 pour."""
        if self.na_if(r, [net]):
            return
        g = self.m.graph(net)
        start = g.pad(ref, num)
        if start is None:
            return r.set(NA, f"{ref} pad {num} is not on {net} here")
        seen, stack, narrow, counted = {start}, [start], [], set()
        reached, crossed = False, set()
        while stack:
            u = stack.pop()
            zs = g.zone_of.get(u, [])
            if any(g.islands[z].layer == plane_layer for z in zs):
                reached = True
                continue      # on the plane: the rule's copper ends here
            for z in zs:      # an outer-layer pour of the net: walk across it
                crossed.add(z)
                for v in g.contacts[z]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            for v, L, w, kind in g.adj[u]:
                if kind == "track":
                    key = (min(u, v), max(u, v), round(L, 4))
                    if key not in counted:
                        counted.add(key)
                        if w + 1e-6 < wmin:
                            ux, uy = g.xy[u]
                            vx, vy = g.xy[v]
                            narrow.append(f"{w:.2f} mm track ({ux:.2f},{uy:.2f})-({vx:.2f},{vy:.2f})")
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        if not counted and not crossed and not reached:
            return r.set(NA, f"{ref} pad {num} has no copper yet")
        r.clause(f"every track before the {plane_layer} plane at least {wmin} mm",
                 self.verdict(narrow), *(narrow or [f"{len(counted)} track pieces from {ref} "
                                                    f"pad {num} before the plane"]))
        if crossed:
            r.clause(f"pours of {net} on the way are at least {wmin} mm wide", NOT_CHECKED,
                     *[f"the path crosses {g.islands[z]!r}; the width of pour copper on the "
                       "current's path is not measured" for z in sorted(crossed)])
        r.clause(f"reaches its {plane_layer} plane", PASS if reached else NOT_CHECKED,
                 f"{ref} pad {num} reaches a {net} pour on {plane_layer}" if reached else
                 f"no {net} pour on {plane_layer} is reached from {ref} pad {num}; the rule "
                 "is a width to the plane")

    # -- N-10 ----------------------------------------------------------------------------
    def island_edges(self, isl):
        k = id(isl)
        if k not in self._edges:
            e = []
            p = isl.poly
            chains = [p.COutline(0)] + [p.CHole(0, h) for h in range(p.HoleCount(0))]
            for ch in chains:
                n = ch.PointCount()
                pts = [(mm(ch.CPoint(i).x), mm(ch.CPoint(i).y)) for i in range(n)]
                e += [(pts[i], pts[(i + 1) % n]) for i in range(n)]
            self._edges[k] = e
        return self._edges[k]

    def slab_bound(self, isl, p, q, rp, rq):
        """A lower bound, in squares, on the resistance of a pour island between two
        contacts: R >= integral of dt / L(t), where L(t) is the length of the island's
        cross-section on the line perpendicular to p-q at distance t from p (cutting the
        sheet with perfect conductors along those lines can only lower R). Integrated
        with 0.25 mm (at most 400) midpoint steps from the edge of one contact to the
        other's."""
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        a, b = rp, d - rq
        if b <= a:
            return 0.0
        ux, uy = (q[0] - p[0]) / d, (q[1] - p[1]) / d
        nx, ny = -uy, ux
        edges = self.island_edges(isl)
        n = max(4, min(400, int((b - a) / 0.25)))
        dt = (b - a) / n
        total = 0.0
        for k in range(n):
            t = a + (k + 0.5) * dt
            cx, cy = p[0] + t * ux, p[1] + t * uy
            ss = []
            for (x1, y1), (x2, y2) in edges:
                d1 = (x1 - cx) * ux + (y1 - cy) * uy
                d2 = (x2 - cx) * ux + (y2 - cy) * uy
                if (d1 > 0) != (d2 > 0):
                    f = d1 / (d1 - d2)
                    ix, iy = x1 + f * (x2 - x1), y1 + f * (y2 - y1)
                    ss.append((ix - cx) * nx + (iy - cy) * ny)
            ss.sort()
            L = sum(ss[i + 1] - ss[i] for i in range(0, len(ss) - 1, 2))
            if L > 0:
                total += dt / L
        return total

    def kicad_cluster(self, pad):
        try:
            items = self.m.board.GetConnectivity().GetConnectedItems(pad.obj)
            return {i.m_Uuid.AsString() for i in items}
        except Exception:
            return None

    def n10(self, r):
        if self.na_if(r, ["+5V"]):
            return
        g = self.m.graph("+5V")
        start = g.pad("D3", "1")
        cv = self.contract["component_values"]
        res = [(ref, pin) for ref, pin, *_ in self.contract["nets"]["+5V"]
               if ref.startswith("R") and cv.get(ref) == "249"]
        tnodes = {g.pad(ref, pin): (ref, pin) for ref, pin in res if g.pad(ref, pin) is not None}
        if start is None or not tnodes:
            return r.set(NA, "D3 or the LED resistors are not on this board")
        cache = {}

        def hop(zi, u, v, dd):
            key = (zi, min(u, v), max(u, v))
            if key in cache:
                return cache[key]
            return NetGraph._zcost(dd, "squares")   # the cheaper bound until refined
        # Refine: replace each pour crossing on the current best paths by its slab bound,
        # and search again, until no path uses an unrefined crossing. The result is then
        # the least lower bound over all single paths.
        for _ in range(8):
            dist, parent = g.shortest([start], list(tnodes), weight="squares", hop=hop)
            fresh = False
            for n in tnodes:
                if n not in dist:
                    continue
                for u, v, kind, zi in g.path(parent, n):
                    if kind == "pour":
                        key = (zi, min(u, v), max(u, v))
                        if key not in cache:
                            cache[key] = max(NetGraph._zcost(math.hypot(
                                g.xy[u][0] - g.xy[v][0], g.xy[u][1] - g.xy[v][1]), "squares"),
                                self.slab_bound(g.islands[zi], g.xy[u], g.xy[v],
                                                g.rad[u], g.rad[v]))
                            fresh = True
            if not fresh:
                break
        reached = sorted(((dist[n], tnodes[n], n) for n in tnodes if n in dist),
                         key=lambda t: -t[0])
        unreached = sorted(f"{a}.{b}" for n, (a, b) in tnodes.items() if n not in dist)
        lines, over, via_pour = [], [], []
        for sq, (ref, pin), n in reached:
            path = g.path(parent, n)
            pours = [g.islands[zi] for _, _, kind, zi in path if kind == "pour"]
            track = sum(math.hypot(g.xy[u][0] - g.xy[v][0], g.xy[u][1] - g.xy[v][1]) / w
                        for u, v, kind, w in path if kind == "track" and w)
            line = (f"{ref} pad {pin}: {sq:.1f} squares ({track:.1f} in tracks"
                    + (f", at least {sq - track:.1f} through {len(pours)} pour crossing(s)"
                       if pours else "") + ")")
            if sq > 83.0:
                over.append(line)
            elif pours:
                via_pour.append(line)
            lines.append(line)
        if over:
            r.clause("at most 83 squares to each reached LED resistor", FAIL, *over[:6],
                     "a pour crossing is counted at its lower bound, so these are proved over")
        elif via_pour:
            r.clause("at most 83 squares to each reached LED resistor", NOT_CHECKED,
                     *via_pour[:4], "these paths cross a pour, whose resistance the judge "
                     "bounds only from below; under 83 on a lower bound is not a pass")
        elif reached:
            r.clause("at most 83 squares to each reached LED resistor", PASS,
                     f"farthest: {lines[0]}", "track-only paths, an upper bound on resistance")
        if unreached:
            d3 = self.m.pad_by.get(("D3", "1"))
            cl = self.kicad_cluster(d3) if d3 is not None else None
            conn = [x for x in unreached if cl is not None and
                    self.m.pad_by[tuple(x.split("."))].obj.m_Uuid.AsString() in cl]
            r.clause("every LED resistor reached", NOT_CHECKED,
                     f"{len(reached)} of {len(tnodes)} reached from D3 by the judge's copper "
                     f"graph; not reached: {unreached[:8]}{'...' if len(unreached) > 8 else ''}",
                     (f"KiCad connects {len(conn)} of them to D3 ({conn[:4]}): the judge's graph "
                      "misses that copper" if conn else
                      "KiCad does not connect them to D3 either: not routed yet"))
        else:
            r.clause("every LED resistor reached", PASS, f"all {len(tnodes)} reached")

    # -- B-13 ----------------------------------------------------------------------------
    def inner_ends(self, p, axis, line):
        """Track ends inside pad p nearer the barrier line than the pad's centre."""
        out = []
        for s in self.m.segs_of(p.net):
            if s.layer not in p.layers:
                continue
            pp = p.poly(s.layer, s.lid)
            for x, y in ((s.x1, s.y1), (s.x2, s.y2)):
                if pp.SquaredDistance(V(x, y)) == 0:
                    c_end, c_pad = (x, p.x) if axis == "x" else (y, p.y)
                    if abs(c_end - line) < abs(c_pad - line) - 1e-3:
                        out.append(f"{s!r} leaves {p!r} from its inner end, ({x:.2f},{y:.2f})")
        return out

    def b13(self, r):
        keys = [("C162", "1"), ("C162", "2"), ("U63", "1"), ("U63", "2"), ("U63", "4"), ("U63", "6")]
        pads = {k: self.m.pad_by.get(k) for k in keys}
        if any(p is None for p in pads.values()):
            return r.set(NA, "C162 or U63 is not on this board")
        if self.state(["DGND", "ISO_5V_RTN", "U63_VIN", "ISO_5V"]) == "absent":
            return r.set(NA, "none of C162's and U63's nets is on this board")

        def has(k):
            g = self.m.graph(pads[k].net)
            n = g.pad(*k)
            return n is not None and (bool(g.adj[n]) or g.is_contact(n))
        if not any(has(k) for k in (("C162", "1"), ("C162", "2"), ("U63", "2"), ("U63", "4"))):
            return r.set(NA, "no copper on C162 or U63 pins 2 and 4 yet")
        for a, b in ((("C162", "1"), ("U63", "1")), (("C162", "2"), ("U63", "6"))):
            pa, pb = pads[a], pads[b]
            name = f"{a[0]}.{a[1]} to {b[0]}.{b[1]} by the shortest trace"
            if not has(a):
                r.clause(name, NA, f"{pa!r} has no copper yet")
                continue
            g = self.m.graph(pa.net)
            dist, _ = g.shortest([g.pad(*a)], [g.pad(*b)], use_zones=False)
            straight = math.hypot(pa.x - pb.x, pa.y - pb.y)
            nb = g.pad(*b)
            if nb not in dist:
                r.clause(name, FAIL, f"{pa!r} does not reach {pb!r} by tracks ({pa.net})")
            else:
                r.clause(name, PASS if dist[nb] <= straight + 2.0 else FAIL,
                         f"{pa!r} to {pb!r}: {dist[nb]:.2f} mm of track against "
                         f"{straight:.2f} mm straight (allowance 2.0 mm)")
        inner = [e for k in (("C162", "1"), ("C162", "2"), ("U63", "2"), ("U63", "4"))
                 for e in self.inner_ends(pads[k], "y", INTAN_LINE_Y)]
        r.clause("each trace leaves its pad from the outer end", self.verdict(inner), *inner)
        c2 = pads[("C162", "2")]
        touch = [f"C162 pad 2 touches {i!r}" for i in self.m.islands_of("INTAN_GND")
                 if i.layer == "F.Cu" and
                 overlap_mm2(c2.poly("F.Cu", self.m.lid["F.Cu"], 0.01), i.poly) > 1e-5]
        r.clause("C162 pad 2 never on the INTAN_GND plane", self.verdict(touch), *touch)

    def b13_all(self, r):
        pads = [p for p in self.m.pads if p.ref in cbp.STRADDLERS and p.num
                and p.net in self.present and p.net in self.m.copper_nets]
        if not any(p.ref in cbp.STRADDLERS and p.net in self.present for p in self.m.pads):
            return r.set(NA, "no straddler's nets are on this board")
        def ended_in(p):
            for s in self.m.segs_of(p.net):
                if s.layer in p.layers and abs(s.x1 - p.x) + abs(s.y1 - p.y) + abs(s.x2 - p.x) \
                        + abs(s.y2 - p.y) < 1e9:
                    pp = p.poly(s.layer, s.lid)
                    if any(pp.SquaredDistance(V(x, y)) == 0 for x, y in ((s.x1, s.y1), (s.x2, s.y2))):
                        return True
            return False
        withcu = [p for p in pads if ended_in(p)]
        if not withcu:
            return r.set(NA, "no track ends in a straddler pad yet")
        inner = []
        for p in withcu:
            axis, line = cbp.STRADDLER_LINE[p.ref]
            inner += self.inner_ends(p, axis, line)
        r.clause("every straddler pad left from its outer end", self.verdict(inner),
                 *(inner or [f"{len(withcu)} straddler pads with tracks"]))

    # -- B-16 ----------------------------------------------------------------------------
    def b16(self, r):
        primary = [("U2", "1"), ("U2", "3"), ("U63", "1"), ("U63", "2")]
        secondary = [("U2", "2"), ("U2", "4"), ("U2", "5"), ("U2", "6"), ("U63", "4"), ("U63", "6")]
        intan = {n for n, d in self.net_dom.items() if d == {"INTAN"}}
        dgnd = {n for n, d in self.net_dom.items() if d == {"DGND"}}
        if not any(k in self.m.pad_by for k in primary + secondary):
            return r.set(NA, "U2 and U63 are not on this board")
        own = {self.m.pad_by[k].net for k in primary + secondary if k in self.m.pad_by}
        if self.state(own) != "routed":
            return r.set(NA, "U2's and U63's own nets have no copper yet")
        a = [f"Intan copper within {BAND_W} mm of primary {self.m.pad_by[k]!r}: {h}"
             for k in primary if k in self.m.pad_by
             for h in self._near_pad(self.m.pad_by[k], BAND_W, intan)]
        r.clause(f"no Intan copper within {BAND_W} mm of a primary pin", self.verdict(a), *a)
        b = [f"DGND copper under secondary {self.m.pad_by[k]!r}: {h}"
             for k in secondary if k in self.m.pad_by
             for h in self._near_pad(self.m.pad_by[k], 0.0, dgnd)]
        r.clause("no DGND copper under a secondary pin", self.verdict(b), *b)

    def _near_pad(self, p, grow, nets):
        hits = []
        pp = p.poly(p.layers[0], self.m.lid[p.layers[0]], grow)
        pb = poly_bbox(pp)
        for item, ln, thunk, bb in self.m.copper_items(nets):
            if bbox_hit(pb, bb) and overlap_mm2(pp, thunk()) > 1e-4:
                hits.append(f"{item!r} on {ln}")
        return hits

    # -- B-17, B-18 ----------------------------------------------------------------------
    def intan_crossings(self):
        out = []
        for s in self.m.segs:
            if (s.y1 > INTAN_LINE_Y) == (s.y2 > INTAN_LINE_Y):
                continue
            t = (INTAN_LINE_Y - s.y1) / (s.y2 - s.y1)
            x = s.x1 + t * (s.x2 - s.x1)
            if SPLIT_X <= x <= INTAN_X1:
                out.append((x, s.net, s.layer, s))
        return sorted(out, key=lambda c: c[0])

    def b17(self, r):
        xs = self.intan_crossings()
        if not xs and self.state(MUX_OUT) != "routed":
            return r.set(NA, "nothing crosses y = 165 between x = 170 and 238, and no "
                         "MUX_OUTn is routed")
        cnt = {}
        for x, net, ln, s in xs:
            cnt[net] = cnt.get(net, 0) + 1
        foreign = [f"{net} crosses at ({x:.2f}, 165) on {ln}" for x, net, ln, s in xs
                   if net not in MUX_OUT and net != "AGND"]
        r.clause("only MUX_OUTn and AGND cross", self.verdict(foreign),
                 *(foreign or [f"{len(xs)} crossings: "
                               + ", ".join(f"{k} x{v}" for k, v in sorted(cnt.items()))]))
        r.clause("AGND crosses at most eight times (the sense traces)",
                 self.verdict(cnt.get("AGND", 0) > 8), f"AGND crosses {cnt.get('AGND', 0)} times")

    def b18(self, r):
        if self.na_if(r, MUX_OUT, "MUX_OUT1 to 8"):
            return
        xs = self.intan_crossings()
        g = self.m.graph("AGND") if "AGND" in self.m.copper_nets else None
        cl = {k: [] for k in ("once", "beside", "sense", "length", "at mux")}
        bad = {k: [] for k in cl}
        for net in [n for n in MUX_OUT if n in self.m.copper_nets]:
            ina = next((ref for ref, pin, *_ in self.contract["nets"][net] if pin == "3"), None)
            mux = next((ref for ref, pin, *_ in self.contract["nets"][net] if pin == "28"), None)
            mine = [c for c in xs if c[1] == net]
            (bad if len(mine) != 1 else cl)["once"].append(f"{net} crosses y = 165 {len(mine)} times")
            if len(mine) == 1:
                i = xs.index(mine[0])
                nbrs = [xs[j] for j in (i - 1, i + 1) if 0 <= j < len(xs)]
                pair = [c for c in nbrs if c[1] == "AGND" and abs(c[0] - mine[0][0]) <= 1.5]
                (cl if pair else bad)["beside"].append(
                    f"{net} at x = {mine[0][0]:.2f}; neighbours "
                    f"{[(round(c[0], 2), c[1]) for c in nbrs]}")
            start = g.pad(ina, "2") if g is not None and ina else None
            ls = self.to_plane(g, start)[0] if start is not None else None
            if not ls:
                bad["sense"].append(f"{net}: {ina} pin 2 has no AGND sense trace to the pour")
                continue
            cl["sense"].append(f"{net}: {ina}.2 to the AGND pour by {ls:.2f} mm of track")
            L = self.m.track_length(net)
            (bad if abs(L - ls) > 1.0 else cl)["length"].append(
                f"{net} {L:.2f} mm, its sense trace {ls:.2f} mm")
            targets = [n for n in range(len(g.xy)) if g.is_contact(n)]
            dist, _ = g.shortest([start], targets, use_zones=False)
            t = min((n for n in targets if n in dist), key=lambda n: dist[n])
            mx = self.fp_xy(mux)
            dm = math.hypot(g.xy[t][0] - mx[0], g.xy[t][1] - mx[1]) if mx else float("inf")
            (bad if dm > 10.0 else cl)["at mux"].append(
                f"{net}: sense meets the pour at ({g.xy[t][0]:.2f},{g.xy[t][1]:.2f}), "
                f"{dm:.1f} mm from {mux}")
        names = {"once": "each MUX_OUTn crosses once",
                 "beside": "an AGND crossing beside it, within 1.5 mm",
                 "sense": "an AGND sense trace from the INA105's pin 2 to the pour",
                 "length": "MUX_OUTn and its sense trace within 1.0 mm in length",
                 "at mux": "the sense trace taken from the pour within 10 mm of the mux"}
        for k, name in names.items():
            if bad[k] or cl[k]:
                r.clause(name, self.verdict(bad[k]), *(bad[k] or cl[k]))

    # -- N-9 -----------------------------------------------------------------------------
    def n9(self, r):
        nets = ["USB_DP_PI", "USB_DM_PI"]
        vb = self.m.pad_by.get(("J18", "1"))
        vbus = [f"J18 pin 1 touched by {h}" for h in self.m.touching(vb)] if vb else []
        if self.state(nets) != "routed" and not vbus:
            return self.na_if(r, nets)
        if vb is not None:
            r.clause("J18 pin 1 (USB_VBUS_PI) gets no copper", self.verdict(vbus), *vbus)
        if self.state(nets) != "routed":
            return
        L = {n: self.m.track_length(n) for n in nets}
        r.clause("lengths within 1.0 mm", self.verdict(abs(L[nets[0]] - L[nets[1]]) > 1.0),
                 f"D+ {L[nets[0]]:.2f} mm, D- {L[nets[1]]:.2f} mm")
        own = [p for p in self.m.pads if p.net in nets]
        dgnd = {ln: [i for i in self.m.islands_of("DGND") if i.layer == ln] for ln in self.m.cu}
        loose, gaps = [], []
        for a, b in ((nets[0], nets[1]), (nets[1], nets[0])):
            sa, sb = self.m.segs_of(a), self.m.segs_of(b)
            tot = close = 0.0
            miss = 0
            first = None
            for s in sa:
                n = max(1, int(s.length / 0.25))
                for k in range(n):
                    t = (k + 0.5) / n
                    x, y = s.x1 + t * (s.x2 - s.x1), s.y1 + t * (s.y2 - s.y1)
                    tot += s.length / n
                    if any(seg_point_dist(x, y, o.x1, o.y1, o.x2, o.y2)[0] - (s.w + o.w) / 2 <= 0.4
                           for o in sb if o.layer == s.layer):
                        close += s.length / n
                    if any(math.hypot(x - p.x, y - p.y) <= 1.0 + p.reach for p in own):
                        continue
                    ln = ADJACENT_PLANE[s.layer] if s.layer in ("F.Cu", "B.Cu") else None
                    if not any(i.poly.Contains(V(x, y)) for i in dgnd.get(ln, [])):
                        miss += 1
                        first = first or (round(x, 2), round(y, 2), s.layer)
            if tot and close / tot < 0.8:
                loose.append(f"{a}: {100 * close / tot:.0f}% of its length within 0.4 mm of {b}")
            if miss:
                gaps.append(f"{a}: {miss} samples not over a DGND fill on the adjacent layer, "
                            f"first at {first}")
        r.clause("coupled: 80% within a 0.4 mm gap", self.verdict(loose), *loose)
        r.clause("over unbroken DGND", self.verdict(gaps), *gaps)

    # -- K-33 ----------------------------------------------------------------------------
    def k33(self, r):
        nets = self.contract["nets"]
        legs = self.k33_legs()
        if self.na_if(r, [n for n, *_ in legs], "the LED-driver output nets"):
            return
        apart, ok, bad = [], [], []
        for net, pa, pb in legs:
            if net not in self.m.copper_nets:
                continue
            g = self.m.graph(net)
            na, nb = g.pad(*pa), g.pad(*pb)
            others = [g.pad(ref, pin) for ref, pin, *_ in nets[net]
                      if (ref, pin) not in (pa, pb) and g.pad(ref, pin) is not None]
            if na is None or nb is None or not others:
                continue
            da, _ = g.shortest([na])
            db, _ = g.shortest([nb])
            dl, _ = g.shortest(others)
            common = [n for n in range(len(g.xy)) if n in da and n in db and n in dl]
            if not common:
                apart.append(f"{net}: {pa[0]}.{pa[1]} and {pb[0]}.{pb[1]} not joined to the LED")
                continue
            j = min(common, key=lambda n: da[n] + db[n] + dl[n])
            line = (f"{net}: legs {da[j]:.2f} mm ({pa[0]}.{pa[1]}) and {db[j]:.2f} mm "
                    f"({pb[0]}.{pb[1]}), junction ({g.xy[j][0]:.2f},{g.xy[j][1]:.2f})")
            (bad if abs(da[j] - db[j]) > 1.0 or max(da[j], db[j]) > 10.0 else ok).append(line)
        if apart:
            r.clause("both outputs joined to the LED", FAIL, *apart)
        if bad or ok:
            r.clause("legs at most 10 mm and within 1.0 mm", self.verdict(bad), *(bad or ok))

    # -- K-23 ----------------------------------------------------------------------------
    def k23(self, r):
        pairs = self.K23_PAIRS
        if self.na_if(r, [n for p in pairs for n in p], "the divider nets"):
            return
        lb, lo, vb, vo = [], [], [], []
        for a, b in pairs:
            if a not in self.m.copper_nets and b not in self.m.copper_nets:
                continue
            la, lb_ = self.m.track_length(a), self.m.track_length(b)
            va, vb_ = len(self.m.vias_of(a)), len(self.m.vias_of(b))
            (lb if abs(la - lb_) > 1.0 else lo).append(f"{a} {la:.2f} mm, {b} {lb_:.2f} mm")
            (vb if va != vb_ else vo).append(f"{a} {va} vias, {b} {vb_} vias")
        r.clause("each leg's twin within 1.0 mm in length", self.verdict(lb), *(lb or lo))
        r.clause("each leg's twin with as many vias", self.verdict(vb), *(vb or vo))
        r.clause("a 2-gang block fitted and moved by hand, clear of the BNC bodies",
                 NOT_CHECKED, "access by hand is not a property the board file holds")

    # -- FIXED ---------------------------------------------------------------------------
    def fixed(self, r):
        if self.reference is None:
            return r.clause("against a reference", NOT_CHECKED, "no reference board")
        label, ref_text = self.reference
        text = self.m.path.read_text()
        if text == ref_text:
            return r.clause("against a reference", NOT_CHECKED,
                            f"the reference ({label}) is this board itself; give --reference")
        ref = cbp.read_board(ref_text)
        got = cbp.read_board(text)
        refs = sorted(set(cbp.PANEL) | set(cbp.STRADDLERS) | {"NT1", "J1"})
        a = {f.ref: f for f in ref["footprints"]}
        b = {f.ref: f for f in got["footprints"]}
        moved = []
        for k in refs:
            if k not in b or k not in a:
                moved.append(f"{k} is missing")
                continue
            fa, fb = a[k], b[k]
            if abs(fa.x - fb.x) > 1e-3 or abs(fa.y - fb.y) > 1e-3 or abs((fa.rot - fb.rot) % 360) > 1e-3:
                moved.append(f"{k} moved: ({fa.x:.3f},{fa.y:.3f},{fa.rot:g}) -> "
                             f"({fb.x:.3f},{fb.y:.3f},{fb.rot:g})")
        r.clause(f"{len(refs)} fixed parts where the reference has them", self.verdict(moved),
                 *(moved or [f"reference: {label}"]))

        def norm_edges(e):
            return sorted(tuple(sorted((round(x, 3), round(y, 3)) for x, y in s)) for s in e)
        r.clause("Edge.Cuts (outline and slots) as the reference",
                 self.verdict(norm_edges(ref["edges"]) != norm_edges(got["edges"])))
        za = {z["name"]: (sorted((round(x, 3), round(y, 3)) for x, y in z["pts"]), z["keepout"])
              for z in ref["zones"] if z["keepout"]}
        zb = {z["name"]: (sorted((round(x, 3), round(y, 3)) for x, y in z["pts"]), z["keepout"])
              for z in got["zones"] if z["keepout"]}
        diff = [f"rule area {n} differs" for n in sorted(za) if za[n] != zb.get(n)]
        # an added rule area FAILs, unless it is a mounting hole's keep-out (task R0), whose
        # shape and position the PLACEMENT gate checks: a board from before the holes, as
        # reference, still grades a board that has them
        hole_areas = {f"mh_{h}" for h in cbp.MOUNTING_HOLES}
        added = sorted(set(zb) - set(za))
        diff += [f"rule area {n} is not in the reference" for n in added if n not in hole_areas]
        holes = [n for n in added if n in hole_areas]
        note = [f"added, mounting-hole keep-outs (M-16, PLACEMENT checks them): "
                f"{', '.join(holes)}"] if holes else []
        r.clause(f"the {len(za)} rule areas as the reference", self.verdict(diff),
                 *(diff or note))

    # ===================================================================================
    # Layer 3
    # ===================================================================================
    def metrics(self):
        m = self.m
        scope = self.present
        conns = 0
        pads_by_net = {}
        for p in m.pads:
            if p.net and p.net in scope:
                pads_by_net.setdefault(p.net, []).append(p)
        for net, ps in pads_by_net.items():
            conns += len(ps) - 1   # KiCad's count: pads less one, per net
        routed = conns - m.unconnected
        vias = [v for v in m.vias if v.net in scope]
        out = {"corridor": self.corridor(),
               "vias": len(vias), "connections": conns, "routed_connections": routed,
               "vias_per_routed_connection": round(len(vias) / routed, 3) if routed > 0 else None}
        inner = {}
        for ln in ("In1.Cu", "In2.Cu"):
            ss = [s for s in m.segs if s.layer == ln and s.net not in PLANE_NETS and s.net in scope]
            inner[ln] = {"signal_length_mm": round(sum(s.length for s in ss), 1),
                         "signal_segments": len(ss),
                         "nets": sorted({s.net for s in ss})[:12]}
        out["inner_layer_signal"] = inner
        pours = []
        for zm in m.zones_meta:
            for ln in zm["layers"]:
                isl = [i for i in m.islands if i.net == zm["net"] and i.zone == (zm["name"] or "")
                       and i.layer == ln]
                pours.append({"net": zm["net"], "zone": zm["name"], "layer": ln,
                              "islands": len(isl),
                              "area_mm2": round(sum(i.area for i in isl), 1),
                              "neck_mm": self.neck(isl)})
        out["pours"] = pours
        out["bypass"] = self.bypass()
        total, lb, per = 0.0, 0.0, []
        for net, ps in pads_by_net.items():
            if net in PLANE_NETS or net not in {s.net for s in m.segs}:
                continue
            L = m.track_length(net)
            b = mst_length([(p.x, p.y) for p in ps])
            total += L
            lb += b
            if b > 1.0:
                per.append((L / b, net, L, b))
        per.sort(reverse=True)
        out["length"] = {"signal_track_mm": round(total, 1), "mst_lower_bound_mm": round(lb, 1),
                         "ratio": round(total / lb, 3) if lb else None,
                         "worst": [{"net": n, "ratio": round(q, 2), "routed_mm": round(L, 1),
                                    "mst_mm": round(b, 1)} for q, n, L, b in per[:5]]}
        vc = {}
        for v in vias:
            vc[v.net] = vc.get(v.net, 0) + 1
        routed_nets = {s.net for s in m.segs if s.net in scope}
        counts = [vc.get(n, 0) for n in routed_nets]
        out["layer_changes"] = {
            "nets_routed": len(routed_nets),
            "mean_vias_per_routed_net": round(sum(counts) / len(counts), 2) if counts else None,
            "max": max(counts) if counts else None,
            "top": sorted(({"net": n, "vias": c} for n, c in vc.items()),
                          key=lambda d: -d["vias"])[:5]}
        return out

    def neck(self, islands):
        if not islands:
            return None
        poly = pcbnew.SHAPE_POLY_SET()
        for i in islands:
            poly.Append(i.poly)

        def sig(p):
            return sum(1 for k in range(p.OutlineCount()) if p.Outline(k).Area() / 1e12 >= 5.0)
        base = sig(poly)
        steps = [round(0.1 * k, 1) for k in range(1, 21)] + [2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6] \
            + [7, 8, 9, 10, 11, 12]
        for w in steps:
            d = pcbnew.SHAPE_POLY_SET(poly)
            d.Deflate(nm(w / 2), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAXERR)
            if sig(d) > base:
                return w
        return ">12"

    BYPASS = [
        ("K-35", [f"C{n}" for n in range(121, 135)], [f"U{n}" for n in range(56, 62)]),
        ("G-12", ["C120"], ["U55"]),
        ("P-3", ["C9"], ["U2"]),
        ("P-5", ["C11", "C14"], ["U3"]),
        ("P-5", ["C17", "C20"], ["U4"]),
        ("P-9", ["C161", "C155", "C156"], ["U63", "L1"]),
    ]

    def bypass(self):
        out = []
        for rule, caps, ics in self.BYPASS:
            for c in caps:
                cps = [p for p in self.m.pads if p.ref == c and p.num]
                if not cps or not any(p.net in self.present for p in cps):
                    continue
                cx = sum(p.x for p in cps) / len(cps)
                cy = sum(p.y for p in cps) / len(cps)
                # the IC this capacitor serves: the candidate with a pin on the capacitor's
                # nets nearest to it
                best = None
                for ic in ics:
                    pins = [p for p in self.m.pads if p.ref == ic and p.net in {q.net for q in cps}]
                    if not pins:
                        continue
                    d = min(math.hypot(p.x - cx, p.y - cy) for p in pins)
                    if best is None or d < best[0]:
                        best = (d, ic)
                for cp in cps:
                    entry = {"rule": rule, "cap": c, "pad": cp.num, "net": cp.net}
                    cand = [p for p in self.m.pads if p.net == cp.net and
                            (p.ref == best[1] if best else p.ref in ics)]
                    if not cand:
                        entry["path"] = "no IC pin on this net"
                        out.append(entry)
                        continue
                    if cp.net not in self.m.copper_nets:
                        entry.update(ic=cand[0].ref, pin=cand[0].num, path="not routed")
                        out.append(entry)
                        continue
                    g = self.m.graph(cp.net)
                    s = g.pad(cp.ref, cp.num)
                    tg = {g.pad(p.ref, p.num): p for p in cand}
                    dist, parent = g.shortest([s], list(tg))
                    reach = [n for n in tg if n in dist]
                    if not reach:
                        p0 = min(cand, key=lambda p: math.hypot(p.x - cp.x, p.y - cp.y))
                        entry.update(ic=p0.ref, pin=p0.num, path="not connected")
                    else:
                        n = min(reach, key=lambda n: dist[n])
                        kinds = {k for _, _, k, _ in g.path(parent, n)}
                        entry.update(ic=tg[n].ref, pin=tg[n].num, path_mm=round(dist[n], 2),
                                     via="plane" if "pour" in kinds else "track")
                    out.append(entry)
        return out


# ---------------------------------------------------------------------------------------
# Layer 1
# ---------------------------------------------------------------------------------------
def stage(src: Path, root: Path) -> Path:
    """Copy `src` into root/hardware/breakout/breakout.kicad_pcb beside breakout's project,
    rules and library tables, with root/hardware/lib pointing at the repository's."""
    b = root / "hardware" / "breakout"
    b.mkdir(parents=True, exist_ok=True)
    lib = root / "hardware" / "lib"
    if not lib.exists():
        os.symlink(REPO / "hardware" / "lib", lib)
    for name in ("breakout.kicad_pro", "breakout.kicad_dru", "fp-lib-table", "sym-lib-table"):
        shutil.copyfile(BREAKOUT / name, b / name)
    dst = b / "breakout.kicad_pcb"
    shutil.copyfile(src, dst)
    return dst


def kicad_cli() -> str:
    return shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


def run_drc(pcb: Path, refill: bool = True) -> dict:
    out = pcb.with_suffix(".drc.json")
    cmd = [kicad_cli(), "pcb", "drc", "--severity-all", "--format", "json", "-o", str(out)]
    if refill:
        cmd.append("--refill-zones")
    subprocess.run(cmd + [str(pcb)], check=True, capture_output=True, text=True)
    return json.loads(out.read_text())


DRC_NET = re.compile(r"\[([^\]]+)\]")   # "PTH pad 1 [FAN_RTN] of J55": the item's net


def split_unconnected(items, routed):
    """Partial mode: DRC's unconnected_items entries, as (set aside, counted). An entry is
    set aside only when every item in it names a net and none of those nets is listed;
    anything else (a listed net, or an item whose net the judge cannot read) counts."""
    aside, counted = [], []
    for v in items:
        nets = [DRC_NET.search(it.get("description", "")) for it in v.get("items", [])]
        if nets and all(nets) and not any(m.group(1) in routed for m in nets):
            aside.append(v)
        else:
            counted.append(v)
    return aside, counted


def drc_gates(board: Path, work: Path, routed=None):
    staged = stage(board, work / "main")
    drc = run_drc(staged)
    # the control: one 0.5 mm track on P5_RAW, off the board, which only the custom rule
    # "N-1 INLET track width" can flag, and only once the project puts P5_RAW in INLET
    ctl = stage(board, work / "control")
    b = pcbnew.LoadBoard(str(ctl))
    net = b.FindNet("P5_RAW")
    if net is None:
        net = pcbnew.NETINFO_ITEM(b, "P5_RAW")
        b.Add(net)
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(V(-20.0, -20.0))
    t.SetEnd(V(-10.0, -20.0))
    t.SetWidth(nm(0.5))
    t.SetLayer(pcbnew.B_Cu)
    t.SetNet(net)
    b.Add(t)
    pcbnew.SaveBoard(str(ctl), b)
    cdrc = run_drc(ctl, refill=False)
    proof = [v for v in cdrc["violations"] if v["type"] == "track_width"
             and "N-1 INLET track width" in v["description"]
             and any("P5_RAW" in i.get("description", "") for i in v["items"])]
    rules = Result("DRC-RULES", "breakout's custom rules and net classes are loaded", 1)
    seen = sorted(set(re.findall(r"rule '([^']+)'", " ".join(
        v["description"] for v in drc["violations"] + cdrc["violations"]))))
    if proof:
        rules.set(PASS, "control: a 0.5 mm P5_RAW track gives track_width \""
                  + proof[0]["description"] + "\"", f"custom rule names seen: {seen}")
    else:
        rules.set(FAIL, "the control track on P5_RAW was not flagged by 'N-1 INLET track "
                  "width': the rules or the net classes did not load")

    counts = {}
    silk_refs = {}
    for v in drc["violations"]:
        counts[v["type"]] = counts.get(v["type"], 0) + 1
        if v["type"] == "silk_edge_clearance":
            for it in v["items"]:
                m = re.search(r" of (\S+) on ", it.get("description", ""))
                if m:
                    silk_refs[m.group(1)] = silk_refs.get(m.group(1), 0) + 1
    unc_items = drc.get("unconnected_items", [])
    partial = []
    if routed is None:
        unc = len(unc_items)
    else:
        aside, counted = split_unconnected(unc_items, routed)
        unc = len(counted)
        aside_nets = sorted({m.group(1) for v in aside for it in v["items"]
                             for m in [DRC_NET.search(it["description"])]})
        partial.append(f"partial (--complete-nets): {len(aside)} of {len(unc_items)} "
                       f"unconnected_items set aside, every item on a net not complete "
                       f"({len(aside_nets)} nets); {len(counted)} on a listed net (or a net "
                       "the judge could not read), which count")
        partial += [f"unconnected on a listed net: " + " - ".join(
            it.get("description", "?") for it in v.get("items", [])) for v in counted[:6]]
        if len(unc_items) >= 499:
            partial.append("DRC lists at most 499 unconnected_items, so an entry on a listed "
                           "net can be missing here: the CONNECTIVITY gate's count over the "
                           "listed nets is the complete one")
    if unc:
        counts["unconnected_items"] = unc
    g = Result("DRC", "DRC classes against the accepted set (24 silk_edge_clearance, "
               "nothing else)", 1)
    if routed is not None:
        g.data = {"unconnected_listed_by_drc": len(unc_items),
                  "unconnected_set_aside": len(aside), "unconnected_on_listed": len(counted),
                  "set_aside_nets": aside_nets}
    bad = []
    for k, n in sorted(counts.items()):
        want = ACCEPTED_DRC.get(k, 0)
        if n != want:
            bad.append(f"{k}: {n}, accepted {want}"
                       + (" (DRC lists at most 499)" if k == "unconnected_items" and n >= 499 else "")
                       + (" (on listed nets)" if k == "unconnected_items" and routed is not None
                          else ""))
    for k, want in ACCEPTED_DRC.items():
        if k not in counts:
            bad.append(f"{k}: 0, accepted {want} (B-21: the straddlers' own outlines)")
    off = sorted(r for r in silk_refs if r not in cbp.STRADDLERS)
    if off:
        bad.append(f"silk_edge_clearance on parts that are not straddlers: {off}")
    g.set(FAIL if bad or rules.status != PASS else PASS,
          "every class: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "none"),
          *bad, *partial)
    if rules.status != PASS:
        g.evidence.append("not trusted: the rules were not shown to be loaded")
    return [rules, g], drc, counts


def placement_gate(board: Path, contract: dict, nets_filter) -> Result:
    r = Result("PLACEMENT", "check_breakout_placement.py on the board", 1)
    fails, report = cbp.run_checks(board.read_text(), contract,
                                   (BREAKOUT / "breakout.kicad_pro").read_text(),
                                   (BREAKOUT / "breakout.kicad_dru").read_text())
    dropped = 0
    if nets_filter is not None:
        pin_net = {(ref, pin): net for net, nodes in contract["nets"].items()
                   for ref, pin, *_ in nodes}
        keep = []
        for f in fails:
            # a pad the region copy stripped on purpose: its contract net is not listed
            m = (re.match(r"^2: (\S+) pad (\S+) is on \['None'\], contract says", f)
                 or re.match(r"^4: (\w+)\.(\w+)@\([^)]*\) .* is on None ", f))
            if m and pin_net.get((m.group(1), m.group(2))) not in nets_filter:
                dropped += 1
                continue
            keep.append(f)
        fails = keep
    extra = [f"{dropped} findings set aside: pads of unlisted nets that carry no net, "
             "as a region copy should"] \
        if nets_filter is not None else []
    r.set(FAIL if fails else PASS, *(fails[:40] or ["PASS"]), *extra,
          f"routed items: {report.get('routed_items')}")
    return r


def listed_unconnected(m: Model, nets) -> dict:
    """Partial mode: KiCad's unrouted-connection count per listed net, after the zone fill
    (pad clusters through tracks, vias and filled zones, less one, per net). The same count
    as route/board_io.py's connectivity_of, which the driver prints."""
    conn = m.board.GetConnectivity()
    pads = {}
    for f in m.board.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() > 0 and p.GetNetname() in nets:
                pads.setdefault(p.GetNetname(), []).append(p)
    per = {}
    for net, ps in pads.items():
        seen, clusters = set(), 0
        for p in ps:
            k = p.m_Uuid.AsString()
            if k in seen:
                continue
            clusters += 1
            seen.add(k)
            seen.update(q.m_Uuid.AsString() for q in conn.GetConnectedItems(p)
                        if q.GetClass() == "PAD")
        per[net] = clusters - 1
    return per


def connectivity_gate(m: Model, complete) -> Result:
    cg = Result("CONNECTIVITY", "KiCad's unrouted-connection count after filling every "
                "zone (routing-brief §1)", 1)
    whole = (f"GetUnconnectedCount(False) = {m.unconnected} after filling "
             f"{len(m.zones_meta)} pours")
    if complete is None:
        return cg.set(PASS if m.unconnected == 0 else FAIL, whole)
    per = listed_unconnected(m, complete)
    total = sum(per.values())
    open_ = sorted((n for n, k in per.items() if k), key=lambda n: (-per[n], n))
    cg.title = ("KiCad's unrouted-connection count over the complete nets "
                "(--complete-nets), after filling every zone (routing-brief §1)")
    cg.data = {"listed_nets": len(complete), "listed_unconnected": total,
               "whole_board_unconnected": m.unconnected}
    cg.set(PASS if total == 0 else FAIL,
           f"listed nets: {total} unconnected over {len(complete)} complete "
           f"net{'' if len(complete) == 1 else 's'}"
           + (f" ({', '.join(f'{n} {per[n]}' for n in open_[:12])}"
              f"{' ...' if len(open_) > 12 else ''})" if open_ else ""),
           f"whole board: {whole} (the nets not complete are not finished yet; not a gate "
           "in partial mode)")
    return cg


def net_lists_gate(m: Model, complete, touched) -> Result:
    """Partial mode (fix round 1, folds M-4): every net with copper on the board is in
    complete.nets or touched.nets, so the board and the driver's lists agree. Copper on a
    net in neither is copper no stage put there."""
    g = Result("NET-LISTS", "every net with copper is in --complete-nets or --touched-nets", 1)
    if complete is None:
        return g.set(NA, "no --complete-nets given")
    stray = sorted(n for n in m.copper_nets - set(complete) - set(touched) if n)
    g.data = {"complete": len(complete), "touched": len(touched), "stray": stray}
    return g.set(FAIL if stray else PASS,
                 *([f"copper on {len(stray)} net(s) in neither list: {short_list(stray, 12)}"]
                   if stray else [f"{len(m.copper_nets)} nets with copper, every one listed "
                                  f"({len(complete)} complete, {len(touched)} touched)"]))


def region_gate(m: Model, nets_filter, region) -> Result:
    r = Result("REGION", "every track, via and pour vertex inside the region and on a "
               "listed net", 1)
    if nets_filter is None and region is None:
        return r.set(NA, "no --nets or --region given")
    x0, y0, x1, y1 = region if region else (-1e9, -1e9, 1e9, 1e9)
    bad = []

    def inside(x, y, h=0.0):
        return x0 - 1e-6 <= x - h and x + h <= x1 + 1e-6 and y0 - 1e-6 <= y - h and y + h <= y1 + 1e-6
    for s in m.segs:
        if nets_filter is not None and s.net not in nets_filter:
            bad.append(f"{s!r}: net not listed")
        for x, y in ((s.x1, s.y1), (s.x2, s.y2)):
            if not inside(x, y, s.w / 2):
                bad.append(f"{s!r}: outside the region")
                break
    for v in m.vias:
        if nets_filter is not None and v.net not in nets_filter:
            bad.append(f"{v!r}: net not listed")
        if not inside(v.x, v.y, v.r):
            bad.append(f"{v!r}: outside the region")
    for zm in m.zones_meta:
        z = zm["zone"]
        if nets_filter is not None and zm["net"] not in nets_filter:
            bad.append(f"pour '{zm['name']}' on {zm['net']}: net not listed")
        o = z.Outline()
        for i in range(o.TotalVertices()):
            p = o.CVertex(i)
            if not inside(mm(p.x), mm(p.y)):
                bad.append(f"pour '{zm['name']}' vertex ({mm(p.x):.2f},{mm(p.y):.2f}) outside")
                break
    r.set(FAIL if bad else PASS, *(bad[:40] or [
        f"{len(m.segs)} track pieces, {len(m.vias)} vias, {len(m.zones_meta)} pours, all inside "
        f"{region} and on listed nets"]))
    return r


# ---------------------------------------------------------------------------------------
# Layer 4: kicad-happy, advisory
# ---------------------------------------------------------------------------------------
def kicad_happy(board_staged: Path, base_staged: Path | None, model: Model, work: Path) -> dict:
    """Run analyze_pcb.py --full on both boards and keep what is new in the board.

    Its return_path_continuity samples each track every 2 mm and looks for copper on a
    reference layer it takes from the board's stack-up; with no stack-up block (this
    board has none, routing-brief.md §2) it falls back to F.Cu <-> B.Cu and maps every
    inner layer to F.Cu (analyze_pcb.py, _build_reference_layer_map and
    analyze_return_path_continuity). So a F.Cu track over a filled In1.Cu plane is judged
    against B.Cu. The judge re-measures each such net against the adjacent plane
    (READINGS["REF"]) with the same 2 mm sampling and 0.5 mm radius, and reports the
    analyzer's figure only beside its own."""
    if not KICAD_HAPPY.exists():
        return {"status": NOT_CHECKED, "why": f"{KICAD_HAPPY} not found"}
    py = shutil.which("python3", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin") or "python3"

    def run(pcb, tag):
        out = work / f"kh-{tag}.json"
        p = subprocess.run([py, str(KICAD_HAPPY), "--full", "-o", str(out), str(pcb)],
                           capture_output=True, text=True, cwd=str(work))
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(p.stderr[-400:])
        return json.loads(out.read_text())
    try:
        a = run(board_staged, "board")
        b = run(base_staged, "baseline") if base_staged else None
    except Exception as e:
        return {"status": NOT_CHECKED, "why": f"analyze_pcb.py failed: {e}"}

    def key(f):
        who = ",".join(sorted(f.get("components") or [])) or ",".join(sorted(f.get("nets") or []))
        return (f.get("rule_id"), who)
    base_keys = {key(f) for f in (b or {}).get("findings", [])}
    new = [f for f in a.get("findings", []) if key(f) not in base_keys]
    by_rule = {}
    for f in new:
        by_rule.setdefault(f.get("rule_id"), [])
        if key(f)[1] not in by_rule[f.get("rule_id")]:
            by_rule[f.get("rule_id")].append(key(f)[1])
    stack = (a.get("setup") or {}).get("stackup") or []
    base_rp = {e["net"] for e in (b or {}).get("return_path_continuity") or []}
    rp = []
    for e in a.get("return_path_continuity") or []:
        if e["net"] in base_rp:
            continue
        net = e["net"]
        theirs = e["reference_plane_coverage_pct"]
        again = coverage(model, net, kh_layer)          # their method, their layer
        gl = []
        mine = coverage(model, net, adj_layer, gaps=gl)  # their method, the adjacent plane
        away = [g for g in gl if not own_hole(model, net, g[0], g[1])]
        fill = coverage(model, net, adj_layer, fills_only=True)
        reproduced = again is not None and abs(again - theirs) <= 2.0
        layer_only = mine is not None and mine >= 95.0
        holes_only = mine is not None and not away
        artefact = (not stack) and reproduced and (layer_only or holes_only)
        rp.append({"net": net, "kicad_happy_pct": theirs,
                   "reproduced_on_their_layer_pct": again,
                   "adjacent_plane_pct": mine, "adjacent_fill_only_pct": fill,
                   "adjacent_gap_samples": len(gl),
                   "gaps_away_from_own_vias_and_pins": away[:10],
                   "n_gaps_away": len(away),
                   "verdict": ("analyzer artefact: with no stack-up it samples the opposite "
                               "outer layer (B.Cu under F.Cu, F.Cu under an inner layer); "
                               + ("on the adjacent plane the same method finds >= 95%"
                                  if layer_only else
                                  "on the adjacent plane every remaining gap sample is in the "
                                  "clearance hole of the net's own via or through-hole pin"))
                   if artefact else
                   f"stands on the adjacent plane: {len(gl)} gap samples, {len(away)} of them "
                   "away from the net's own vias and through-hole pins"})
    return {"status": "ran", "new_findings": len(new),
            "new_by_rule": {k: sorted(v)[:12] + (["..."] if len(v) > 12 else [])
                            for k, v in sorted(by_rule.items())},
            "board_stackup_layers": len(stack),
            "return_path": rp}


KH_FALLBACK = {"F.Cu": "B.Cu", "B.Cu": "F.Cu"}   # analyze_pcb.py with no stack-up


def kh_layer(layer):
    """The layer analyze_pcb.py samples under a track when the board has no stack-up:
    ref_layer_map.get(layer, "B.Cu" if layer == "F.Cu" else "F.Cu")."""
    return KH_FALLBACK.get(layer, "B.Cu" if layer == "F.Cu" else "F.Cu")


def adj_layer(layer):
    return ADJACENT_PLANE[layer]


def coverage(model: Model, net: str, ref_layer, fills_only=False, gaps=None):
    """Percent of the net's track samples with copper on `ref_layer(track layer)` within
    0.5 mm, by analyze_pcb.py's own method: a sample every 2 mm along each piece of at
    least 0.1 mm, both ends included; a sample is covered by a pour fill within 0.5 mm, a
    track within 0.5 mm of its edge, or a pad within 0.5 mm of its half-diagonal, of any
    net (CopperPresence.has_coverage_near). Its pad index is keyed by the layer names the
    file gives, so a through-hole pad, whose layers read "*.Cu", is never found on any
    layer; only SMD pads are credited here too. Vias are not credited, as there. With
    fills_only, pours alone count."""
    tot = hit = 0
    for s in model.segs_of(net):
        if s.length < 0.1:
            continue
        ln = ref_layer(s.layer)
        n = max(2, int(s.length / 2.0) + 1)
        isl = [i for i in model.islands if i.layer == ln]
        trk = [t for t in model.segs if t.layer == ln]
        pads = [p for p in model.pads if ln in p.layers and p.attr == pcbnew.PAD_ATTRIB_SMD]
        for k in range(n):
            t = k / (n - 1)
            x, y = s.x1 + t * (s.x2 - s.x1), s.y1 + t * (s.y2 - s.y1)
            tot += 1
            if any(bbox_hit(i.bbox, (x, y, x, y), 0.5)
                   and i.poly.SquaredDistance(V(x, y)) <= nm(0.5) ** 2 for i in isl):
                hit += 1
                continue
            if fills_only:
                continue
            if any(seg_point_dist(x, y, o.x1, o.y1, o.x2, o.y2)[0] <= 0.5 + o.w / 2 for o in trk
                   if abs(o.x1 - x) < 30 or abs(o.x2 - x) < 30):
                hit += 1
                continue
            if any(math.hypot(x - p.x, y - p.y) <= 0.5 + p.reach for p in pads
                   if abs(p.x - x) < 10 and abs(p.y - y) < 10):
                hit += 1
                continue
            if gaps is not None:
                gaps.append((round(x, 2), round(y, 2), s.layer, ln))
    return round(100.0 * hit / tot, 1) if tot else None


def own_hole(model: Model, net: str, x: float, y: float) -> bool:
    """True if (x, y) is at one of the net's own vias or through-hole pads, whose
    clearance hole in the plane below is inherent to changing layer there."""
    return (any(math.hypot(x - v.x, y - v.y) <= v.r + 0.5 for v in model.vias_of(net))
            or any(p.net == net and p.attr == pcbnew.PAD_ATTRIB_PTH
                   and math.hypot(x - p.x, y - p.y) <= p.reach + 0.5 for p in model.pads))


# ---------------------------------------------------------------------------------------
def delta(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: delta(a[k], b.get(k)) for k in a if k in b and delta(a[k], b.get(k)) is not None}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return round(a - b, 3)
    return None


def fmt_metrics(mt: dict) -> list[str]:
    L = []
    L.append(f"  vias {mt['vias']}; connections {mt['connections']}, routed "
             f"{mt['routed_connections']}; vias per routed connection "
             f"{mt['vias_per_routed_connection']}")
    for ln, d in mt["inner_layer_signal"].items():
        L.append(f"  {ln}: signal tracks {d['signal_length_mm']} mm in {d['signal_segments']} "
                 f"pieces {d['nets'][:6]}")
    for p in mt["pours"]:
        L.append(f"  pour {p['net']} '{p['zone']}' {p['layer']}: {p['islands']} island(s), "
                 f"{p['area_mm2']} mm2, narrowest neck {p['neck_mm']} mm")
    if not mt["pours"]:
        L.append("  no pours")
    ln = mt["length"]
    L.append(f"  signal track {ln['signal_track_mm']} mm against an octilinear MST of "
             f"{ln['mst_lower_bound_mm']} mm: ratio {ln['ratio']}")
    for w in ln["worst"][:3]:
        L.append(f"    worst {w['net']}: {w['ratio']} ({w['routed_mm']}/{w['mst_mm']} mm)")
    lc = mt["layer_changes"]
    L.append(f"  layer changes: {lc['mean_vias_per_routed_net']} vias per routed net on "
             f"average, max {lc['max']} {[(d['net'], d['vias']) for d in lc['top'][:3]]}")
    by = mt["bypass"]
    routed = [b for b in by if "path_mm" in b]
    L.append(f"  bypass: {len(by)} capacitor pads in scope, {len(routed)} connected; "
             + ", ".join(f"{b['cap']}.{b['pad']}->{b['ic']}.{b['pin']} {b['path_mm']} mm "
                         f"({b['via']})" for b in routed[:6]))
    return L


def reference_of(a):
    """FIXED's reference: --reference, else --baseline, else the committed board on main.
    The working-tree breakout.kicad_pcb is never the reference: it is where the routed
    board lives, so it would be compared with itself."""
    if a.reference:
        return (f"--reference {a.reference}", a.reference.read_text())
    if a.baseline:
        return (f"--baseline {a.baseline}", a.baseline.read_text())
    rel = "hardware/breakout/breakout.kicad_pcb"
    try:
        sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "main"],
                             capture_output=True, text=True, check=True).stdout.strip()
        text = subprocess.run(["git", "-C", str(REPO), "show", f"main:{rel}"],
                              capture_output=True, text=True, check=True).stdout
        return (f"main ({sha}):{rel}", text)
    except (subprocess.CalledProcessError, OSError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", type=Path)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--nets", type=Path)
    ap.add_argument("--region")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--kicad-happy", action="store_true")
    ap.add_argument("--skip-drc", action="store_true",
                    help="skip layer 1's DRC, for development only: the DRC gate is then "
                         "NOT CHECKED and the run can never exit 0")
    ap.add_argument("--reference", type=Path,
                    help="the board FIXED compares with (default: --baseline if given, else "
                         "hardware/breakout/breakout.kicad_pcb as committed on main)")
    ap.add_argument("--allow-not-checked", metavar="IDS", default="",
                    help="comma-separated layer-2 rule IDs (e.g. G-1,K-23) whose NOT CHECKED "
                         "does not count against the exit code; never a gate, never FAIL")
    ap.add_argument("--complete-nets", "--routed-nets", dest="complete_nets", type=Path,
                    metavar="FILE",
                    help="partial mode (task R2; the stage contract, fix round 1): the nets "
                         "the stages so far complete, one per line (hardware/breakout/routes/"
                         "complete.nets). Connectivity and DRC's unconnected_items count these "
                         "nets only; a requirement that is not due waits, PENDING, until all "
                         "its nets are complete; prohibitions are judged on all copper. "
                         "--routed-nets is the old name")
    ap.add_argument("--touched-nets", type=Path, metavar="FILE",
                    help="with --complete-nets: every net with copper of the stages "
                         "(default: touched.nets beside the complete-nets file). Copper on a "
                         "net in neither list FAILs the NET-LISTS gate")
    ap.add_argument("--due", type=Path, metavar="FILE",
                    help="with --complete-nets: check IDs or check/clause keys (G-1/overlap), "
                         "one per line, that must PASS (default: due.txt beside the "
                         "complete-nets file). A due item is judged on the copper present, "
                         "and anything but PASS is a FAIL")
    a = ap.parse_args(argv)
    waive = [w.strip() for w in a.allow_not_checked.split(",") if w.strip()]
    if any(w in GATE_IDS for w in waive):
        print(f"--allow-not-checked: {sorted(set(waive) & GATE_IDS)} are layer-1 gates, "
              "which can never be waived")
        return 2
    if pcbnew is None:
        print("judge_layout.py needs KiCad's Python (import pcbnew failed):\n  " + KICAD_PY)
        return 2
    board = a.board.resolve()
    contract = json.loads(cbp.CONTRACT_PATH.read_text())
    nets_filter = set(a.nets.read_text().split()) if a.nets else None
    region = tuple(float(v) for v in a.region.split(",")) if a.region else None
    complete = touched = None
    due: list[str] = []
    if a.complete_nets is None and (a.touched_nets or a.due):
        print("--touched-nets and --due belong to partial mode: give --complete-nets too")
        return 2
    if a.complete_nets is not None:
        if a.nets or a.region:
            print("--complete-nets judges the whole board as routed so far; --nets/--region "
                  "judge a region copy. Give one or the other")
            return 2
        a.touched_nets = a.touched_nets or a.complete_nets.parent / "touched.nets"
        a.due = a.due or a.complete_nets.parent / "due.txt"
        lists = {}
        for flag, path in (("--complete-nets", a.complete_nets),
                           ("--touched-nets", a.touched_nets), ("--due", a.due)):
            try:
                lists[flag] = sorted({ln.strip() for ln in path.read_text().splitlines()
                                      if ln.strip()})
            except OSError as e:
                print(f"{flag}: cannot read {path}: {e}")
                return 2
        complete, touched, due = (lists["--complete-nets"], lists["--touched-nets"],
                                  lists["--due"])
        for flag, names in (("--complete-nets", complete), ("--touched-nets", touched)):
            unknown = sorted(set(names) - set(contract["nets"]))
            if unknown:
                print(f"{flag}: {len(unknown)} name(s) that are not nets of this board "
                      f"(netlist-contract.json): {short_list(unknown, 12)}")
                return 2
        keys = {k for rid in CLASSES for k in clause_keys(rid)}
        bad = sorted(d for d in due if d not in CLASSES and d not in keys)
        if bad:
            print(f"--due: {len(bad)} entr(ies) that are neither a check ID nor a check/clause "
                  f"key: {short_list(bad, 12)}")
            return 2

    work = Path(tempfile.mkdtemp(prefix="judge-"))
    try:
        gates = []
        if a.skip_drc:
            gates.append(Result("DRC", "DRC classes against the accepted set", 1).set(
                NOT_CHECKED, "--skip-drc"))
        else:
            gl, _drc, _counts = drc_gates(board, work, complete)
            gates += gl
        staged = stage(board, work / "model")
        m = Model(staged)
        m.path = board  # FIXED reads the original file's text
        gates.append(connectivity_gate(m, complete))
        gates.append(net_lists_gate(m, complete, touched))
        gates.append(placement_gate(board, contract, nets_filter))
        gates.append(region_gate(m, nets_filter, region))

        j = Judge(m, contract, nets_filter, region, reference_of(a), complete, due)
        j.run_rules()
        metrics = j.metrics()
        base_metrics = base_m = None
        base_staged = None
        if a.baseline:
            base_staged = stage(a.baseline.resolve(), work / "baseline")
            base_m = Model(base_staged)
            base_metrics = Judge(base_m, contract, nets_filter, region).metrics()
        kh = None
        if a.kicad_happy:
            kh = kicad_happy(staged, base_staged, m, work)

        unknown = sorted(set(waive) - {r.id for r in j.results})
        if unknown:
            print(f"--allow-not-checked: no layer-2 check is named {unknown}")
            return 2
        everything = gates + j.results
        # A gate that did not PASS (or is not N/A) always fails the run, NOT CHECKED
        # included: a skipped DRC can never be waived. A rule check's NOT CHECKED is waived
        # only when its ID is named; a FAIL is never waived.
        # PENDING exists only in partial mode, and only on a requirement whose nets are
        # not all listed: it is not yet due, so it does not count against the exit code.
        failing = [g.id for g in gates if g.status not in (PASS, NA)] + \
                  [r.id for r in j.results if r.status not in (PASS, NA, PENDING)
                   and not (r.status == NOT_CHECKED and r.id in waive)]
        pending = [r.id for r in j.results if r.status == PENDING]
        waived = [r for r in j.results if r.status == NOT_CHECKED and r.id in waive]
        named_not_nc = [(r.id, r.status) for r in j.results
                        if r.id in waive and r.status != NOT_CHECKED]
        code = 0 if not failing else 1
        out = {
            "board": str(board), "baseline": str(a.baseline) if a.baseline else None,
            "nets_file": str(a.nets) if a.nets else None, "region": region,
            "gates": [g.as_dict() for g in gates],
            "checks": [r.as_dict() for r in j.results],
            "metrics": metrics, "baseline_metrics": base_metrics,
            "metrics_delta": delta(metrics, base_metrics) if base_metrics else None,
            "kicad_happy": kh, "judges_readings": READINGS,
            "readings_accepted": {k: ACCEPTED_READINGS.get(k) for k in READINGS},
            "readings_ruling": READINGS_RULING,
            "readings_extended_since_ruling": READINGS_EXTENDED_SINCE_RULING,
            "complete_nets_file": str(a.complete_nets) if a.complete_nets else None,
            "touched_nets_file": str(a.touched_nets) if a.complete_nets else None,
            "due_file": str(a.due) if a.complete_nets else None,
            "complete_nets": complete, "touched_nets": touched, "due": due,
            "pending": pending,
            "classes": {rid: [{"key": f"{rid}/{key}", "clause": pat, "class": k, "why": why}
                              for key, pat, k, _, why in specs]
                        for rid, specs in CLASSES.items()},
            "not_passing": failing, "not_checked_allowed": [r.id for r in waived],
            "waiver_named_but_not_not_checked": named_not_nc, "drc_skipped": a.skip_drc,
            "exit_code": code,
            "not_judged": NOT_JUDGED,
        }
        jp = a.json or (Path(tempfile.gettempdir()) / f"judge-{board.stem}.json")
        jp.write_text(json.dumps(out, indent=1, default=str))

        P = print
        P(f"judge_layout: {board}")
        if nets_filter is not None or region:
            P(f"  region copy: {len(nets_filter or [])} listed nets, region {region}")
        if complete is not None:
            P(f"  partial: {len(complete)} complete net(s) ({a.complete_nets}), "
              f"{len(touched)} touched ({a.touched_nets}), {len(due)} due ({a.due}): "
              f"{short_list(due, 12) or 'none'}")
            P("  A prohibition is judged on all the copper; a due check or clause must PASS; "
              "a requirement that is not due is PENDING until every net it needs is complete")
        P("LAYER 1  GATES")
        for g in gates:
            P(f"  [{g.status:^11}] {g.id}: {g.title}")
            for e in g.evidence[:12]:
                P(f"      {e}")
        P("LAYER 2  RULES NO TOOL CHECKS ([judge's reading] where the rule leaves a number "
          f"open; {READINGS_RULING}); each check's class in parentheses")
        for r in j.results:
            P(f"  [{r.status:^11}] {r.id}: {r.title}  ({r.cls})"
              + ("  DUE" if r.id in due else "") + r.reading_mark())
            if not r.clauses:
                for e in r.evidence[:4]:
                    P(f"      {e}")
            for c in r.clauses:
                if c["status"] == NA and r.status != NA:
                    continue
                note = "; ".join(x for x in (c.get("key"), c.get("class")) if x) or "-"
                if c.get("due"):
                    note += f"; DUE ({c['due']})"
                if c["status"] == PENDING:
                    note += (f"; so far {c['so_far']}; waits on {len(c['pending_on'])} net(s) "
                             f"not complete: {short_list(c['pending_on'], 4)}")
                P(f"      - [{c['status']}] {c['clause']}  ({note})")
                lim = 6 if c["status"] in (FAIL, NOT_CHECKED) else 2
                for e in c["evidence"][:lim]:
                    P(f"          {e}")
                if len(c["evidence"]) > lim:
                    P(f"          ... {len(c['evidence']) - lim} more in the JSON")
        P("NOT JUDGED HERE (rules that bear on routing and that no layer-2 check holds):")
        for who, what in NOT_JUDGED:
            P(f"  {who}: {what}")
        P("LAYER 3  METRICS")
        P(" board:")
        for line in fmt_metrics(metrics):
            P(line)
        if base_metrics:
            P(" baseline:")
            for line in fmt_metrics(base_metrics):
                P(line)
        if kh is not None:
            P("LAYER 4  kicad-happy (advisory; never changes the exit code)")
            if kh.get("status") != "ran":
                P(f"  {kh.get('status')}: {kh.get('why')}")
            else:
                P(f"  {kh['new_findings']} findings new in the board; distinct references "
                  "by rule: " + "; ".join(f"{k} x{len(v)}" for k, v in kh["new_by_rule"].items()))
                for k, v in kh["new_by_rule"].items():
                    P(f"    {k}: {', '.join(v[:8])}")
                art = [e for e in kh["return_path"] if e["verdict"].startswith("analyzer")]
                real = [e for e in kh["return_path"] if not e["verdict"].startswith("analyzer")]
                P(f"  return-path continuity: {len(kh['return_path'])} nets flagged by "
                  f"kicad-happy; {len(art)} rejected as analyzer artefacts, {len(real)} stand")
                P("    net: kicad-happy % | its method on its layer, reproduced % | its method "
                  "on the adjacent plane % | adjacent fill only %")
                for e in real:
                    P(f"    stands   {e['net']}: {e['kicad_happy_pct']} | "
                      f"{e['reproduced_on_their_layer_pct']} | {e['adjacent_plane_pct']} | "
                      f"{e['adjacent_fill_only_pct']}; gaps {e['adjacent_gap_samples']}, "
                      f"{e['n_gaps_away']} away from its own vias/pins "
                      f"{e['gaps_away_from_own_vias_and_pins'][:2]}")
                for e in art:
                    P(f"    rejected {e['net']}: (set aside) | on the wrong layer "
                      f"{e['reproduced_on_their_layer_pct']} | {e['adjacent_plane_pct']} | "
                      f"{e['adjacent_fill_only_pct']}; gaps {e['adjacent_gap_samples']}, "
                      f"{e['n_gaps_away']} away from its own vias/pins")
                if "CP-002" in kh["new_by_rule"]:
                    P("  note: CP-002 (\"no opposite-layer copper under\" a part) reads the same "
                      "no-stack-up fallback layer, B.Cu; the judge does not re-measure it")
        nc = [x for x in everything if x.status == NOT_CHECKED and x not in waived]
        P(f"RESULT: exit {code}" + (f"; not passing: {', '.join(failing)}" if failing else
                                    "; every gate and rule check PASS or N/A"
                                    + (" or PENDING" if complete is not None else "")
                                    + (" except the waived NOT CHECKED below" if waived else "")))
        if pending:
            P(f"  PENDING (requirements not due, on nets not yet complete; not counted): "
              f"{', '.join(pending)}")
        if a.skip_drc:
            P("  DRC was skipped (--skip-drc): this run cannot exit 0, whatever is waived")
        if nc:
            P(f"  NOT CHECKED, counted: {', '.join(x.id for x in nc)}"
              "  (a rule check's may be waived by naming it in --allow-not-checked; a gate's "
              "never)")
        for r in waived:
            P(f"  WAIVED by --allow-not-checked: {r.id}")
            for c in r.clauses:
                if c["status"] == NOT_CHECKED:
                    P(f"      {c['clause']}: {c['evidence'][0] if c['evidence'] else ''}")
        for rid, st in named_not_nc:
            P(f"  named in --allow-not-checked but {st}, so not waived: {rid}")
        P(f"JSON: {jp}")
        return code
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
