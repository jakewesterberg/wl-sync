"""An automatic layout judge for hardware/breakout/breakout.kicad_pcb (task J1, 2026-11-21).

Run it with the Python that ships inside KiCad 10.0.5 (it needs `pcbnew`):

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hardware/gen/judge_layout.py BOARD [--baseline UNROUTED_BOARD] [--nets NETS_FILE] \\
        [--region x0,y0,x1,y1] [--json OUT.json] [--kicad-happy] [--skip-drc]

It grades a routed board, or a region-only copy of it, against this board's own rules and
prints comparable numbers, so two candidate layouts can be ranked without a person. It does
not sign a board off: a person still does that once before fab (routing-brief.md §7).

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
   a numeric reading and marks it [judge's reading] (READINGS below). N/A means the rule's
   nets are absent from the board, or carry no copper yet; it never means PASS.

3. Metrics (numbers, not pass/fail), per board and against --baseline.

4. With --kicad-happy, kicad-happy's analyze_pcb.py --full on the board and the baseline,
   reporting only what is new in the board. Advisory: it never changes the exit code. Its
   return-path figures are re-measured against the adjacent plane (see kicad_happy()).

Exit code 0 only when every gate and every rule check is PASS or N/A.

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
            "routing-brief.md §4.3 expect; a filled pour island that spans the line counts "
            "as one crossing of its net",
    "K-2": "\"barcode-family net\": every net named BARCODE_* or CAM_SYNC_* (constraints "
           "§5.1 cites NET:1883-1931 and 1996-2028, which hold all 21), not only the 13 "
           "routing-brief §4.4 lists; \"enters, over or under\": any of its copper "
           "(track, via or pour) with area inside an AGND rectangle of REGIONS",
    "G-1": "\"no pour overlap\": the AGND and DGND fills, projected onto one plane from "
           "all four layers, share no area; \"no mounting-hardware path\": H1 and H2 "
           "carry no net and no copper touches them",
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
    "N-4": "\"symmetric copper\": pins 8 and 14 each reach the NI_5V pour by track paths "
           "whose lengths differ by at most 1.0 mm, through the same number of vias and "
           "the same narrowest width",
    "N-1": "\"+5V from D3 to its plane\": every track reachable from D3 pad 1 through "
           "tracks and vias before the first contact with a pour",
    "N-10": "squares along the least-squares copper path from D3 pad 1 to each 249 ohm "
            "LED resistor on +5V: track length/width, plus ln(d/0.3 mm)/pi for each "
            "stretch through a pour (two 0.3 mm contacts in an unbroken sheet); vias add "
            "nothing",
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

        def node(k, x, y):
            if k not in self.key:
                self.key[k] = len(self.xy)
                self.xy.append((x, y))
                self.adj.append([])
            return self.key[k]

        def edge(a, b, length, w, kind):
            self.adj[a].append((b, length, w, kind))
            self.adj[b].append((a, length, w, kind))

        ends = []  # (node, layer, x, y, w)
        for s in segs:
            a = node(("e", s.layer, round(s.x1, 4), round(s.y1, 4)), s.x1, s.y1)
            b = node(("e", s.layer, round(s.x2, 4), round(s.y2, 4)), s.x2, s.y2)
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
            vn = node(("v", i), v.x, v.y)
            self.via_nodes.append(vn)
            for n, ln, x, y, w in ends:
                if ln in v.layers and math.hypot(x - v.x, y - v.y) <= v.r + 1e-3:
                    edge(vn, n, 0.0, None, "via")
        self.pad_node = {}
        pad_nodes = []
        for p in pads:
            pn = node(("p", p.ref, p.num, round(p.x, 4), round(p.y, 4)), p.x, p.y)
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

    def shortest(self, sources, targets=None, use_zones=True, weight="length"):
        """Dijkstra. Returns (dist, parent) where parent[n] = (prev, kind, width)."""
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
                if e is not None:
                    ex, ey = self.xy[e[0]]
                    if d >= e[1] + self._zcost(math.hypot(ux - ex, uy - ey), weight) - 1e-9:
                        continue  # reached through the pour already; nothing to improve
                else:
                    zone_entry[zi] = (u, d)
                for v in self.contacts[zi]:
                    if v == u:
                        continue
                    vx, vy = self.xy[v]
                    nd = d + self._zcost(math.hypot(ux - vx, uy - vy), weight)
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
# Results
# ---------------------------------------------------------------------------------------
class Result:
    def __init__(self, rid, title, layer):
        self.id, self.title, self.layer = rid, title, layer
        self.status = NA
        self.evidence: list[str] = []
        self.reading = None
        self.note = None

    def set(self, status, *evidence):
        self.status = status
        self.evidence.extend(evidence)
        return self

    def as_dict(self):
        d = {"id": self.id, "title": self.title, "status": self.status,
             "evidence": self.evidence}
        if self.reading:
            d["judges_reading"] = self.reading
        if self.note:
            d["note"] = self.note
        return d


class Judge:
    def __init__(self, model: Model, contract: dict, nets_filter=None, region=None):
        self.m = model
        self.contract = contract
        self.nets_filter = nets_filter
        self.region = region
        self.dom = cbp.derive_domains(contract)
        self.net_dom = self.dom["net_dom"]
        present = set(model.pad_nets)
        if nets_filter is not None:
            present &= nets_filter
        self.present = present
        self.results: list[Result] = []

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
        try:
            fn(r)
        except Exception as e:  # a check that crashes is not a pass
            r.status = NOT_CHECKED
            r.evidence.append(f"the check raised {type(e).__name__}: {e}")
        self.results.append(r)
        return r

    def fp_xy(self, ref):
        f = self.m.fps.get(ref)
        return None if f is None else (mm(f.GetPosition().x), mm(f.GetPosition().y))

    # ===================================================================================
    # Layer 2
    # ===================================================================================
    def run_rules(self):
        self.add("G-10", "Only G-11's nets cross the AGND/DGND split, once each, in the "
                 "corridor near NT1", self.g10, "G-10")
        self.add("K-2", "No barcode-family net enters, overlies or underlies the AGND "
                 "region", self.k2, "K-2")
        self.add("G-1", "AGND and DGND join at NT1 only", self.g1, "G-1")
        self.add("G-3", "FAN_RTN, CHASSIS_GND, ISO_5V_RTN: one tie each, never flooded "
                 "into their plane", self.g3, "G-3")
        self.add("G-4", "NT2 at the inlet beside NT1", self.g4, "G-4")
        self.add("G-5", "NT3 at the star; J56 reaches it by a dedicated trace", self.g5, "G-5")
        self.add("G-6", "NT4 at U63's secondary pins; ISO_5V_RTN has its five pins",
                 self.g6, "G-6")
        self.add("G-7", "The four net ties are NetTie-2_SMD_Pad2.0mm as drawn", self.g7)
        self.add("G-16", "J2/J36: no copper on any unused (ground) pin; pin 62 on AGND",
                 self.g16, "G-16")
        self.add("G-17", "AO_GND_TPC is a trace only: no pour carries or touches it",
                 lambda r: self.no_pour(r, ["AO_GND_TPC"]))
        self.add("G-18", "The ten *_SHLD nets: no pour on or touching them",
                 lambda r: self.no_pour(r, SHIELD_NETS))
        self.add("G-19", "ACCESIO_AGND kept out of every pour",
                 lambda r: self.no_pour(r, ["ACCESIO_AGND"]))
        self.add("G-20", "ACCESIO_SHELL: no pour on or touching J19's shell pads",
                 lambda r: self.no_pour(r, ["ACCESIO_SHELL"]))
        self.add("X-6", "BNC post-mount pads: every net and pour clear by 0.2 mm", self.x6)
        self.add("P-5", "U3/U4 pin 7 gets no copper; FB and NR/SS traces short", self.p5,
                 "SHORT")
        self.add("P-8", "U63_VIN: one short F.Cu trace, no via, no pour", self.p8, "SHORT")
        self.add("K-11", "A_PD1_CLAMP, A_PD2_CLAMP: F.Cu only, no via, short", self.k11,
                 "SHORT")
        self.add("N-4", "J45: both NI_5V pins with symmetric copper, all twelve NI_GND "
                 "pins used", self.n4, "N-4")
        self.add("N-1", "+5V from D3 to its plane at least 1.0 mm",
                 lambda r: self.partial_width(r, "D3", "1", "+5V", 1.0), "N-1")
        self.add("N-2", "-12V from D2 to its plane at least 0.8 mm",
                 lambda r: self.partial_width(r, "D2", "2", "-12V", 0.8), "N-1")
        self.add("N-10", "+5V: at most 83 squares from D3 to the farthest LED resistor",
                 self.n10, "N-10")
        self.add("B-13", "C162 and U63: shortest traces, left from the pads' outer ends",
                 self.b13, "B-13")
        self.add("B-16", "No Intan copper at U2/U63's primary pins, no DGND copper under "
                 "their secondary pins", self.b16, "B-16")
        self.add("B-17", "Only the sixteen MUX_OUTn/AGND traces cross the AGND/Intan line",
                 self.b17)
        self.add("B-18", "Each MUX_OUTn beside its own AGND sense trace, equal lengths",
                 self.b18, "B-18")
        self.add("N-9", "USB pair coupled, matched, over unbroken DGND; J18 pin 1 bare",
                 self.n9, "N-9")
        self.add("K-33", "Paralleled LED-driver outputs join by short, equal traces",
                 self.k33, "K-33")
        self.add("K-23", "The three /1-/2 dividers' legs laid out symmetrically", self.k23,
                 "K-23")
        self.add("FIXED", "Panel connectors, straddlers, NT1, J1, slots and rule areas "
                 "unmoved (routing-brief §1, §5)", self.fixed)

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
                    out.append((i.net, i.layer, (b[1] + b[3]) / 2, f"pour y {b[1]:.1f}-{b[3]:.1f}"))
        return out

    def g10(self, r):
        xs = self.split_crossings()
        if not xs and self.state(G11_NETS) != "routed":
            return r.set(NA, "no copper crosses x = 170 and no G-11 net is routed")
        nt1 = self.fp_xy("NT1") or (170.0, 94.5)
        per = {}
        for net, ln, y, kind in xs:
            per.setdefault(net, []).append((ln, y, kind))
        bad = []
        for net, lst in sorted(per.items()):
            for ln, y, kind in lst:
                where = f"{net} on {ln} at (170, {y:.2f}) [{kind}], {abs(y - nt1[1]):.1f} mm from NT1"
                if net not in G11_NETS:
                    bad.append(f"not a G-11 net: {where}")
                elif not (CORRIDOR_Y[0] <= y <= CORRIDOR_Y[1]):
                    bad.append(f"outside the corridor y {CORRIDOR_Y[0]:.0f}-{CORRIDOR_Y[1]:.0f}: {where}")
            if net in G11_NETS and len(lst) > 1:
                bad.append(f"{net} crosses {len(lst)} times: "
                           + ", ".join(f"{ln} y={y:.2f}" for ln, y, _ in lst))
        if xs:
            ys = [y for _, _, y, _ in xs]
            r.evidence.append(f"{len(xs)} crossings by {len(per)} nets, y {min(ys):.1f} to "
                              f"{max(ys):.1f}; farthest from NT1 {max(abs(y - nt1[1]) for y in ys):.1f} mm")
        r.set(FAIL if bad else PASS, *bad)

    # -- K-2 -----------------------------------------------------------------------------
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
        r.set(FAIL if bad else PASS, *(bad or [f"{len(fam)} nets checked, no copper in AGND"]))

    # -- G-1 -----------------------------------------------------------------------------
    def g1(self, r):
        if self.state(["AGND", "DGND"]) == "absent":
            return r.set(NA, "AGND and DGND are not on this board")
        bad = []
        both = sorted({p.ref for p in self.m.pads if p.net == "AGND"}
                      & {p.ref for p in self.m.pads if p.net == "DGND"})
        if both != ["NT1"]:
            bad.append(f"parts with pads on both AGND and DGND: {both} (only NT1 may)")
        a = [i for i in self.m.islands if i.net == "AGND"]
        d = [i for i in self.m.islands if i.net == "DGND"]
        for ia in a:
            for idd in d:
                if bbox_hit(ia.bbox, idd.bbox):
                    ov = overlap_mm2(ia.poly, idd.poly)
                    if ov > 0.01:
                        c = pcbnew.SHAPE_POLY_SET(ia.poly)
                        c.BooleanIntersection(idd.poly)
                        bad.append(f"pour overlap {ov:.2f} mm2 in plan: {ia!r} over {idd!r}, "
                                   f"at {tuple(round(v, 1) for v in poly_bbox(c))}")
        for ref in sorted(cbp.MECHANICAL):
            for p in [p for p in self.m.pads if p.ref == ref]:
                if p.net:
                    bad.append(f"{p!r} carries net {p.net}")
                hits = self.m.touching(p)
                bad.extend(f"copper touches {p!r}: {h}" for h in hits)
        r.set(FAIL if bad else PASS, *(bad or [
            f"only NT1 has pads on both; {len(a)} AGND and {len(d)} DGND pour islands, "
            "no overlap; H1, H2 bare"]))
        r.note = "the mounting hardware itself (a screw or standoff joining two pours) " \
                 "is not on the board and is not checked"

    # -- G-3 -----------------------------------------------------------------------------
    def g3(self, r):
        ties = [("FAN_RTN", "NT2", "DGND"), ("CHASSIS_GND", "NT3", "DGND"),
                ("ISO_5V_RTN", "NT4", "INTAN_GND")]
        if self.state([t[0] for t in ties]) == "absent":
            return r.set(NA, "none of FAN_RTN, CHASSIS_GND, ISO_5V_RTN is on this board")
        bad, ok = [], []
        for net, tie, plane in ties:
            if net not in self.present:
                continue
            both = sorted({p.ref for p in self.m.pads if p.net == net}
                          & {p.ref for p in self.m.pads if p.net == plane})
            if both != [tie]:
                bad.append(f"{net}: parts joining it to {plane}: {both} (only {tie} may)")
            for zm in self.m.zones_meta:
                if zm["net"] == net and set(zm["layers"]) & {"In1.Cu", "In2.Cu"}:
                    bad.append(f"{net} poured on a plane layer: '{zm['name']}' {zm['layers']}")
            ok.append(f"{net} joins {plane} only at {tie}")
        r.set(FAIL if bad else PASS, *(bad or ok))

    # -- G-4 to G-7 ----------------------------------------------------------------------
    def _near(self, r, a, b, limit, what):
        pa, pb = self.fp_xy(a), self.fp_xy(b)
        if pa is None or pb is None:
            return r.set(NA, f"{a} or {b} is not on this board")
        d = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
        r.set(PASS if d <= limit else FAIL,
              f"{a} at ({pa[0]:.2f},{pa[1]:.2f}), {b} at ({pb[0]:.2f},{pb[1]:.2f}): "
              f"{d:.2f} mm {what} (limit {limit} mm)")

    def g4(self, r):
        if self.state(["FAN_RTN"]) == "absent":
            return r.set(NA, "FAN_RTN is not on this board")
        self._near(r, "NT2", "NT1", 10.0, "apart")

    def g5(self, r):
        if self.state(["CHASSIS_GND"]) == "absent":
            return r.set(NA, "CHASSIS_GND is not on this board")
        self._near(r, "NT3", "NT1", 10.0, "apart")
        bad = [f"CHASSIS_GND poured: '{zm['name']}' {zm['layers']}"
               for zm in self.m.zones_meta if zm["net"] == "CHASSIS_GND"]
        if "CHASSIS_GND" in self.m.copper_nets:
            g = self.m.graph("CHASSIS_GND")
            a, b = g.pad("J56", "1"), g.pad("NT3", "1")
            if a is not None and b is not None:
                dist, _ = g.shortest([a], [b], use_zones=False)
                if b in dist:
                    r.evidence.append(f"J56 to NT3 by tracks: {dist[b]:.1f} mm")
                else:
                    bad.append("J56 pad 1 does not reach NT3 pad 1 by tracks")
        else:
            r.evidence.append("CHASSIS_GND not routed yet; the trace is not judged")
        if bad:
            r.status = FAIL
            r.evidence.extend(bad)

    def g6(self, r):
        if self.state(["ISO_5V_RTN"]) == "absent":
            return r.set(NA, "ISO_5V_RTN is not on this board")
        p, q = self.m.pad_by.get(("NT4", "1")), self.m.pad_by.get(("U63", "6"))
        if p is None or q is None:
            return r.set(FAIL, "NT4 pad 1 or U63 pad 6 is missing")
        d = math.hypot(p.x - q.x, p.y - q.y)
        want = {("U63", "6"), ("C137", "2"), ("C138", "2"), ("C162", "2"), ("NT4", "1")}
        got = {(x.ref, x.num) for x in self.m.pads if x.net == "ISO_5V_RTN"}
        bad = []
        if d > 10.0:
            bad.append(f"NT4 pad 1 {p!r} is {d:.2f} mm from U63 pad 6 {q!r} (limit 10 mm)")
        if got != want:
            bad.append(f"ISO_5V_RTN pads are {sorted(got)}, G-6 names {sorted(want)}")
        r.set(FAIL if bad else PASS, *(bad or [f"NT4 pad 1 is {d:.2f} mm from U63 pad 6; "
                                                "the net has its five pins"]))

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
            if name != "NetTie-2_SMD_Pad2.0mm" or nums != ["1", "2"]:
                bad.append(f"{ref} is {name} with pads {nums}")
            else:
                ok.append(f"{ref} {name}")
        r.set(FAIL if bad else PASS, *(bad or ok))

    # -- pads that must get no copper ----------------------------------------------------
    def in_region(self, p):
        if self.region is None:
            return True
        x0, y0, x1, y1 = self.region
        return x0 <= p.x <= x1 and y0 <= p.y <= y1

    def bare_pads(self, r, pads, clearance, label):
        if self.region is not None:
            pads = [p for p in pads if self.in_region(p)]
            if not pads:
                return r.set(NA, f"no {label} inside the region")
        bad = []
        for p in pads:
            for h in self.m.touching(p, clearance):
                bad.append(f"{p!r} ({p.net or 'no net'}) touched by {h}")
        if bad:
            return r.set(FAIL, *bad)
        if not self.m.copper_nets:
            return r.set(NA, f"no copper on the board yet; {len(pads)} {label} bare")
        r.set(PASS, f"{len(pads)} {label}: no copper within {clearance} mm")

    def g16(self, r):
        nets = self.contract["nets"]
        unused = set()
        for net, nodes in nets.items():
            if net.startswith("unconnected-"):
                for ref, pin, *_ in nodes:
                    if ref in ("J2", "J36"):
                        unused.add((ref, pin))
        pads = [p for p in self.m.pads if (p.ref, p.num) in unused]
        if not pads:
            return r.set(NA, "J2 and J36 are not on this board")
        bad = []
        for ref in ("J2", "J36"):
            p62 = self.m.pad_by.get((ref, "62"))
            if p62 is not None and p62.net != "AGND" and "AGND" in self.present:
                bad.append(f"{ref} pad 62 is on {p62.net}, not AGND")
        if bad:
            return r.set(FAIL, *bad)
        self.bare_pads(r, pads, 0.0, "unused J2/J36 pins")

    def x6(self, r):
        pads = [p for p in self.m.pads if p.lib == "BNC_Dual_RA_Isolated" and not p.num]
        if not pads:
            return r.set(NA, "no BNC body on this board")
        self.bare_pads(r, pads, 0.2, "BNC post-mount pads")

    def no_pour(self, r, nets):
        present = [n for n in nets if n in self.present]
        bad = []
        for zm in self.m.zones_meta:   # a pour drawn on the net, filled or not
            if zm["net"] in set(nets):
                bad.append(f"pour '{zm['name']}' on {zm['net']}, {zm['layers']}, "
                           f"{len([i for i in self.m.islands if i.zone == (zm['name'] or '') and i.net == zm['net']])} "
                           "filled island(s)")
        own = [p for p in self.m.pads if p.net in set(nets)]
        others = [i for i in self.m.islands if i.net not in set(nets)]
        for p in own:
            for ln in p.layers:
                pp = p.poly(ln, self.m.lid[ln])
                for i in others:
                    if i.layer == ln and bbox_hit(poly_bbox(pp), i.bbox) \
                            and overlap_mm2(pp, i.poly) > 1e-4:
                        bad.append(f"{i!r} touches {p!r} ({p.net})")
        for s in self.m.segs:
            if s.net in set(nets):
                for i in others:
                    if i.layer == s.layer and bbox_hit(s.bbox, i.bbox) \
                            and overlap_mm2(s.poly(), i.poly) > 1e-4:
                        bad.append(f"{i!r} touches {s!r}")
        if bad:
            return r.set(FAIL, *bad)
        if not present:
            return r.set(NA, f"none of {', '.join(nets[:3])}{'...' if len(nets) > 3 else ''} "
                         "is on this board")
        if not self.m.islands and not any(n in self.m.copper_nets for n in present):
            return r.set(NA, "no pour on the board and the nets are not routed yet")
        r.set(PASS, f"{len(present)} nets: no pour of their own, none touching their "
                    f"{len(own)} pads or their tracks")

    # -- P-5, P-8, K-11 ------------------------------------------------------------------
    def short_net(self, net):
        pads = [(p.x, p.y) for p in self.m.pads if p.net == net]
        L = self.m.track_length(net)
        lb = mst_length(pads)
        return L, lb

    def p5(self, r):
        pin7 = [self.m.pad_by.get((u, "7")) for u in ("U3", "U4")]
        pin7 = [p for p in pin7 if p is not None and self.in_region(p)]
        nets = ["U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS"]
        bad = []
        for p in pin7:
            for h in self.m.touching(p):
                bad.append(f"{p!r} (DNC) touched by {h}")
        routed = [n for n in nets if n in self.m.copper_nets]
        for n in routed:
            L, lb = self.short_net(n)
            line = f"{n}: {L:.2f} mm routed against {lb:.2f} mm MST"
            if lb > 0 and L > SHORT_RATIO * lb + 1e-6:
                bad.append(line + f" (over {SHORT_RATIO}x)")
            else:
                r.evidence.append(line)
            if self.m.vias_of(n):
                r.evidence.append(f"{n} has {len(self.m.vias_of(n))} via(s)")
        if bad:
            return r.set(FAIL, *bad)
        if not pin7 and self.state(nets) == "absent":
            return r.set(NA, "U3, U4 and their FB/NR nets are not on this board")
        if not routed:
            return r.set(NA, "pin 7 bare; FB and NR/SS nets not routed yet")
        r.set(PASS, "pin 7 of U3 and U4 bare")

    def no_via_f_only(self, r, nets, two_pins=None):
        if self.na_if(r, nets):
            return
        bad = []
        for n in nets:
            if n not in self.m.copper_nets:
                continue
            for v in self.m.vias_of(n):
                bad.append(f"via on {n}: {v!r}")
            for s in self.m.segs_of(n):
                if s.layer != "F.Cu":
                    bad.append(f"{n} off F.Cu: {s!r}")
            for zm in self.m.zones_meta:
                if zm["net"] == n:
                    bad.append(f"pour '{zm['name']}' on {n}")
            L, lb = self.short_net(n)
            line = f"{n}: {L:.2f} mm routed against {lb:.2f} mm MST"
            if lb > 0 and L > SHORT_RATIO * lb + 1e-6:
                bad.append(line + f" (over {SHORT_RATIO}x)")
            else:
                r.evidence.append(line)
        if two_pins:
            for n, want in two_pins.items():
                got = sorted((p.ref, p.num) for p in self.m.pads if p.net == n)
                if got and got != sorted(want):
                    bad.append(f"{n} has pads {got}, P-8 names {sorted(want)}")
        r.set(FAIL if bad else PASS, *bad)

    def p8(self, r):
        self.no_via_f_only(r, ["U63_VIN"], {"U63_VIN": [("L1", "2"), ("U63", "2")]})
        if r.status != NA and "+5V" in self.m.copper_nets:
            for s in self.m.segs_of("U63_VIN"):
                for i in self.m.islands_of("+5V"):
                    if i.layer == s.layer and overlap_mm2(s.poly(), i.poly) > 1e-4:
                        r.status = FAIL
                        r.evidence.append(f"U63_VIN touches {i!r}")

    def k11(self, r):
        self.no_via_f_only(r, ["A_PD1_CLAMP", "A_PD2_CLAMP"])

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
        vias = sum(1 for _, v, _, _ in path if v in vn)
        return dist[t], (min(widths) if widths else None), vias

    def used(self, g: NetGraph, ref, num):
        n = g.pad(ref, num)
        return n is not None and (bool(g.adj[n]) or g.is_contact(n))

    def n4(self, r):
        if self.na_if(r, ["NI_5V", "NI_GND"]):
            return
        bad = []
        g5, gg = self.m.graph("NI_5V"), self.m.graph("NI_GND")
        gnd = [num for ref, num, *_ in self.contract["nets"]["NI_GND"] if ref == "J45"]
        unused = [n for n in gnd if not self.used(gg, "J45", n)]
        if unused:
            bad.append(f"J45 NI_GND pins with no copper: {sorted(unused, key=int)} "
                       f"(of {len(gnd)})")
        legs = {}
        for num in ("8", "14"):
            if not self.used(g5, "J45", num):
                bad.append(f"J45 pin {num} (NI_5V) has no copper")
                continue
            legs[num] = self.to_plane(g5, g5.pad("J45", num))
        if len(legs) == 2:
            (l8, w8, v8), (l14, w14, v14) = legs["8"], legs["14"]
            r.evidence.append(f"pin 8 to the NI_5V pour: {l8} mm, narrowest {w8}, vias {v8}; "
                              f"pin 14: {l14} mm, narrowest {w14}, vias {v14}")
            if l8 is None or l14 is None:
                bad.append("a NI_5V pin of J45 reaches no NI_5V pour")
            elif abs(l8 - l14) > 1.0 or v8 != v14 or (w8 or 0) != (w14 or 0):
                bad.append(f"asymmetric: |{l8:.2f} - {l14:.2f}| mm, vias {v8}/{v14}, "
                           f"narrowest {w8}/{w14}")
        r.set(FAIL if bad else PASS, *bad)

    # -- N-1, N-2 ------------------------------------------------------------------------
    def partial_width(self, r, ref, num, net, wmin):
        if self.na_if(r, [net]):
            return
        g = self.m.graph(net)
        start = g.pad(ref, num)
        if start is None:
            return r.set(NA, f"{ref} pad {num} is not on {net} here")
        seen, stack, narrow, plane, n_seg = {start}, [start], [], g.is_contact(start), 0
        counted = set()
        while stack:
            u = stack.pop()
            if g.is_contact(u) and u != start:
                plane = True
                continue
            if g.is_contact(u):
                continue
            for v, L, w, kind in g.adj[u]:
                if kind == "track":
                    key = (min(u, v), max(u, v), round(L, 4))
                    if key not in counted:
                        counted.add(key)
                        n_seg += 1
                        if w + 1e-6 < wmin:
                            ux, uy = g.xy[u]
                            vx, vy = g.xy[v]
                            narrow.append(f"{w:.2f} mm track ({ux:.2f},{uy:.2f})-({vx:.2f},{vy:.2f})")
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        if not n_seg and not plane:
            return r.set(NA, f"{ref} pad {num} has no copper yet")
        r.set(FAIL if narrow else PASS,
              f"{n_seg} track pieces from {ref} pad {num} before the pour; "
              f"{'reaches' if plane else 'reaches no'} {net} pour", *narrow)

    # -- N-10 ----------------------------------------------------------------------------
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
        dist, parent = g.shortest([start], list(tnodes), weight="squares")
        reached = [(dist[n], tnodes[n], n) for n in tnodes if n in dist]
        missing = sorted(f"{a}.{b}" for n, (a, b) in tnodes.items() if n not in dist)
        if not reached:
            return r.set(NA, f"no LED resistor is reached from D3 yet ({len(missing)} not)")
        sq, (ref, pin), n = max(reached)
        path = g.path(parent, n)
        track_sq = sum((math.hypot(g.xy[u][0] - g.xy[v][0], g.xy[u][1] - g.xy[v][1]) / w)
                       for u, v, kind, w in path if kind == "track" and w)
        r.set(FAIL if sq > 83.0 else PASS,
              f"farthest LED resistor {ref} pad {pin}: {sq:.1f} squares "
              f"({track_sq:.1f} in tracks, {sq - track_sq:.1f} through pours); limit 83",
              f"{len(reached)} of {len(tnodes)} LED resistors reached"
              + (f"; not reached: {missing[:6]}" if missing else ""))

    # -- B-13 ----------------------------------------------------------------------------
    def b13(self, r):
        pads = {k: self.m.pad_by.get(k) for k in
                [("C162", "1"), ("C162", "2"), ("U63", "1"), ("U63", "2"), ("U63", "4"), ("U63", "6")]}
        if any(p is None for p in pads.values()):
            return r.set(NA, "C162 or U63 is not on this board")
        nets = ["DGND", "ISO_5V_RTN", "U63_VIN", "ISO_5V"]
        if self.state(nets) == "absent":
            return r.set(NA, "none of C162's and U63's nets is on this board")
        has = [k for k in (("C162", "1"), ("C162", "2"), ("U63", "2"), ("U63", "4"))
               if self.m.graph(pads[k].net).adj[self.m.graph(pads[k].net).pad(*k)]
               or self.m.graph(pads[k].net).is_contact(self.m.graph(pads[k].net).pad(*k))]
        if not has:
            return r.set(NA, "no copper on C162 or U63 pins 2 and 4 yet")
        bad = []
        for a, b in ((("C162", "1"), ("U63", "1")), (("C162", "2"), ("U63", "6"))):
            pa, pb = pads[a], pads[b]
            g = self.m.graph(pa.net)
            if not g.adj[g.pad(*a)] and not g.is_contact(g.pad(*a)):
                continue
            dist, _ = g.shortest([g.pad(*a)], [g.pad(*b)], use_zones=False)
            straight = math.hypot(pa.x - pb.x, pa.y - pb.y)
            nb = g.pad(*b)
            if nb not in dist:
                bad.append(f"{pa!r} does not reach {pb!r} by tracks ({pa.net})")
            elif dist[nb] > straight + 2.0:
                bad.append(f"{pa!r} to {pb!r}: {dist[nb]:.2f} mm of track against "
                           f"{straight:.2f} mm straight")
            else:
                r.evidence.append(f"{pa!r} to {pb!r}: {dist[nb]:.2f} mm ({straight:.2f} straight)")
        for k in (("C162", "1"), ("C162", "2"), ("U63", "2"), ("U63", "4")):
            p = pads[k]
            pp = p.poly("F.Cu", self.m.lid["F.Cu"])
            for s in self.m.segs_of(p.net):
                for x, y in ((s.x1, s.y1), (s.x2, s.y2)):
                    if s.layer in p.layers and pp.SquaredDistance(V(x, y)) == 0:
                        if abs(y - INTAN_LINE_Y) < abs(p.y - INTAN_LINE_Y) - 1e-3:
                            bad.append(f"{s!r} leaves {p!r} from its inner end, "
                                       f"({x:.2f},{y:.2f})")
        c2 = pads[("C162", "2")]
        for i in self.m.islands_of("INTAN_GND"):
            if i.layer == "F.Cu" and overlap_mm2(c2.poly("F.Cu", self.m.lid["F.Cu"], 0.01), i.poly) > 1e-5:
                bad.append(f"C162 pad 2 touches {i!r}")
        r.set(FAIL if bad else PASS, *bad)

    # -- B-16 ----------------------------------------------------------------------------
    def b16(self, r):
        primary = [("U2", "1"), ("U2", "3"), ("U63", "1"), ("U63", "2")]
        secondary = [("U2", "2"), ("U2", "4"), ("U2", "5"), ("U2", "6"), ("U63", "4"), ("U63", "6")]
        intan = {n for n, d in self.net_dom.items() if d == {"INTAN"}}
        dgnd = {n for n, d in self.net_dom.items() if d == {"DGND"}}
        if not any(k in self.m.pad_by for k in primary + secondary):
            return r.set(NA, "U2 and U63 are not on this board")
        if self.state(intan | dgnd) != "routed":
            return r.set(NA, "no Intan- or DGND-domain copper routed yet")
        bad = []
        for k in primary:
            p = self.m.pad_by.get(k)
            if p is None:
                continue
            bad += [f"Intan copper within {BAND_W} mm of primary {p!r}: {h}"
                    for h in self._near_pad(p, BAND_W, intan)]
        for k in secondary:
            p = self.m.pad_by.get(k)
            if p is None:
                continue
            bad += [f"DGND copper under secondary {p!r}: {h}"
                    for h in self._near_pad(p, 0.0, dgnd)]
        r.set(FAIL if bad else PASS, *(bad or ["no Intan copper at the primary pins, no "
                                                "DGND copper under the secondary ones"]))

    def _near_pad(self, p, grow, nets):
        """Copper of `nets` within `grow` of the pad's outline, in plan, on any layer."""
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
        bad = []
        cnt = {}
        for x, net, ln, s in xs:
            cnt[net] = cnt.get(net, 0) + 1
            if net not in MUX_OUT and net != "AGND":
                bad.append(f"{net} crosses at ({x:.2f}, 165) on {ln}")
        for n in MUX_OUT:
            if cnt.get(n, 0) > 1:
                bad.append(f"{n} crosses {cnt[n]} times")
        if cnt.get("AGND", 0) > 8:
            bad.append(f"AGND crosses {cnt['AGND']} times (eight sense traces)")
        r.set(FAIL if bad else PASS, f"{len(xs)} crossings: "
              + ", ".join(f"{k} x{v}" for k, v in sorted(cnt.items())), *bad)

    def b18(self, r):
        if self.na_if(r, MUX_OUT, "MUX_OUT1 to 8"):
            return
        xs = self.intan_crossings()
        bad = []
        g = self.m.graph("AGND") if "AGND" in self.m.copper_nets else None
        for net in MUX_OUT:
            if net not in self.m.copper_nets:
                continue
            ina = next((ref for ref, pin, *_ in self.contract["nets"][net] if pin == "3"), None)
            mux = next((ref for ref, pin, *_ in self.contract["nets"][net] if pin == "28"), None)
            mine = [c for c in xs if c[1] == net]
            if len(mine) != 1:
                bad.append(f"{net} crosses y = 165 {len(mine)} times")
                continue
            i = xs.index(mine[0])
            nbrs = [xs[j] for j in (i - 1, i + 1) if 0 <= j < len(xs)]
            pair = [c for c in nbrs if c[1] == "AGND" and abs(c[0] - mine[0][0]) <= 1.5]
            if not pair:
                bad.append(f"{net} at x = {mine[0][0]:.2f}: no AGND crossing beside it "
                           f"within 1.5 mm (neighbours {[(round(c[0], 2), c[1]) for c in nbrs]})")
            L = self.m.track_length(net)
            if g is None or ina is None:
                bad.append(f"{net}: no AGND sense trace routed to {ina} pin 2")
                continue
            start = g.pad(ina, "2")
            ls, _, _ = self.to_plane(g, start) if start is not None else (None, None, 0)
            if ls is None or ls == 0.0:
                bad.append(f"{net}: {ina} pin 2 has no AGND sense trace to the pour")
                continue
            # where the sense trace meets the pour
            targets = [n for n in range(len(g.xy)) if g.is_contact(n)]
            dist, _ = g.shortest([start], targets, use_zones=False)
            t = min((n for n in targets if n in dist), key=lambda n: dist[n])
            mx = self.fp_xy(mux)
            dm = math.hypot(g.xy[t][0] - mx[0], g.xy[t][1] - mx[1]) if mx else float("inf")
            line = (f"{net} {L:.2f} mm, sense {ls:.2f} mm from {ina}.2 to the pour at "
                    f"({g.xy[t][0]:.2f},{g.xy[t][1]:.2f}), {dm:.1f} mm from {mux}")
            if abs(L - ls) > 1.0 or dm > 10.0:
                bad.append(line)
            else:
                r.evidence.append(line)
        r.set(FAIL if bad else PASS, *bad)

    # -- N-9 -----------------------------------------------------------------------------
    def n9(self, r):
        nets = ["USB_DP_PI", "USB_DM_PI"]
        bad = []
        vb = self.m.pad_by.get(("J18", "1"))
        if vb is not None:
            bad += [f"J18 pin 1 touched by {h}" for h in self.m.touching(vb)]
        if self.state(nets) != "routed":
            if bad:
                return r.set(FAIL, *bad)
            return self.na_if(r, nets)
        L = {n: self.m.track_length(n) for n in nets}
        r.evidence.append(f"lengths D+ {L['USB_DP_PI']:.2f} mm, D- {L['USB_DM_PI']:.2f} mm")
        if abs(L["USB_DP_PI"] - L["USB_DM_PI"]) > 1.0:
            bad.append(f"mismatch {abs(L['USB_DP_PI'] - L['USB_DM_PI']):.2f} mm")
        own = [p for p in self.m.pads if p.net in nets]
        dgnd = {ln: [i for i in self.m.islands_of("DGND") if i.layer == ln] for ln in self.m.cu}
        for a, b in ((nets[0], nets[1]), (nets[1], nets[0])):
            sa, sb = self.m.segs_of(a), self.m.segs_of(b)
            tot = close = 0.0
            gaps = 0
            for s in sa:
                n = max(1, int(s.length / 0.25))
                for k in range(n):
                    t = (k + 0.5) / n
                    x, y = s.x1 + t * (s.x2 - s.x1), s.y1 + t * (s.y2 - s.y1)
                    tot += s.length / n
                    if any(seg_point_dist(x, y, o.x1, o.y1, o.x2, o.y2)[0] - (s.w + o.w) / 2 <= 0.4
                           for o in sb if o.layer == s.layer):
                        close += s.length / n
                    if any(math.hypot(x - p.x, y - p.y) <= 1.0 + p.reach
                           for p in own):
                        continue
                    ln = ADJACENT_PLANE[s.layer] if s.layer in ("F.Cu", "B.Cu") else None
                    if not any(i.poly.Contains(V(x, y)) for i in dgnd.get(ln, [])):
                        gaps += 1
            if tot and close / tot < 0.8:
                bad.append(f"{a}: {100 * close / tot:.0f}% of its length coupled to {b}")
            if gaps:
                bad.append(f"{a}: {gaps} samples not over a DGND fill on the adjacent layer")
        r.set(FAIL if bad else PASS, *bad)

    # -- K-33 ----------------------------------------------------------------------------
    def k33(self, r):
        pairs = [("U8", "U70"), ("U9", "U71"), ("U11", "U73"), ("U10", "U72")]
        nets = self.contract["nets"]
        legs = []
        for a, b in pairs:
            for net, nodes in nets.items():
                fa = [(ref, pin, fn) for ref, pin, fn, _ in nodes if ref == a and fn.startswith("Y")]
                fb = [(ref, pin, fn) for ref, pin, fn, _ in nodes if ref == b and fn.startswith("Y")]
                if fa and fb:
                    legs.append((net, (a, fa[0][1]), (b, fb[0][1])))
        if self.na_if(r, [n for n, *_ in legs], "the LED-driver output nets"):
            return
        bad = []
        for net, pa, pb in legs:
            if net not in self.m.copper_nets:
                continue
            g = self.m.graph(net)
            na, nb = g.pad(*pa), g.pad(*pb)
            others = [g.pad(ref, pin) for ref, pin, *_ in nets[net]
                      if (ref, pin) not in (pa, pb) and g.pad(ref, pin) is not None]
            da, _ = g.shortest([na])
            db, _ = g.shortest([nb])
            dl, _ = g.shortest(others)
            common = [n for n in range(len(g.xy)) if n in da and n in db and n in dl]
            if not common:
                bad.append(f"{net}: {pa[0]}.{pa[1]} and {pb[0]}.{pb[1]} not joined to the LED yet")
                continue
            j = min(common, key=lambda n: da[n] + db[n] + dl[n])
            la, lb = da[j], db[j]
            line = (f"{net}: legs {la:.2f} mm ({pa[0]}.{pa[1]}) and {lb:.2f} mm "
                    f"({pb[0]}.{pb[1]}), junction ({g.xy[j][0]:.2f},{g.xy[j][1]:.2f})")
            if abs(la - lb) > 1.0 or max(la, lb) > 10.0:
                bad.append(line)
            else:
                r.evidence.append(line)
        r.set(FAIL if bad else PASS, *bad)

    # -- K-23 ----------------------------------------------------------------------------
    def k23(self, r):
        pairs = []
        for m in ("A_MISC1", "A_MISC2", "A_MISC3"):
            pairs += [(f"{m}_MID", f"{m}_SHLD_MID"), (f"{m}_DIV", f"{m}_SHLD_DIV")]
        if self.na_if(r, [n for p in pairs for n in p], "the divider nets"):
            return
        bad = []
        for a, b in pairs:
            if a not in self.m.copper_nets and b not in self.m.copper_nets:
                continue
            la, lb = self.m.track_length(a), self.m.track_length(b)
            va, vb = len(self.m.vias_of(a)), len(self.m.vias_of(b))
            line = f"{a} {la:.2f} mm/{va} vias, {b} {lb:.2f} mm/{vb} vias"
            if abs(la - lb) > 1.0 or va != vb:
                bad.append(line)
            else:
                r.evidence.append(line)
        r.set(FAIL if bad else PASS, *bad)
        r.note = "K-23's other half (a 2-gang block fitted and moved by hand, clear of the " \
                 "BNC bodies) is placement and not judged here"

    # -- FIXED ---------------------------------------------------------------------------
    def fixed(self, r):
        ref = cbp.read_board(COMMITTED_PCB.read_text())
        got = cbp.read_board(self.m.path.read_text())
        refs = sorted(set(cbp.PANEL) | set(cbp.STRADDLERS) | {"NT1", "J1"})
        a = {f.ref: f for f in ref["footprints"]}
        b = {f.ref: f for f in got["footprints"]}
        bad = []
        for k in refs:
            if k not in b:
                bad.append(f"{k} is missing")
                continue
            fa, fb = a[k], b[k]
            if abs(fa.x - fb.x) > 1e-3 or abs(fa.y - fb.y) > 1e-3 or abs((fa.rot - fb.rot) % 360) > 1e-3:
                bad.append(f"{k} moved: ({fa.x:.3f},{fa.y:.3f},{fa.rot:g}) -> "
                           f"({fb.x:.3f},{fb.y:.3f},{fb.rot:g})")

        def norm_edges(e):
            return sorted(tuple(sorted((round(x, 3), round(y, 3)) for x, y in s)) for s in e)
        if norm_edges(ref["edges"]) != norm_edges(got["edges"]):
            bad.append("Edge.Cuts (outline or slots) differs from the committed board")
        za = {z["name"]: (sorted((round(x, 3), round(y, 3)) for x, y in z["pts"]), z["keepout"])
              for z in ref["zones"] if z["keepout"]}
        zb = {z["name"]: (sorted((round(x, 3), round(y, 3)) for x, y in z["pts"]), z["keepout"])
              for z in got["zones"] if z["keepout"]}
        for name in sorted(set(za) | set(zb)):
            if za.get(name) != zb.get(name):
                bad.append(f"rule area {name} differs from the committed board")
        r.set(FAIL if bad else PASS, *(bad or [
            f"{len(refs)} parts, Edge.Cuts and {len(za)} rule areas as committed"]))

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
        out = {"vias": len(vias), "connections": conns, "routed_connections": routed,
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


def drc_gates(board: Path, work: Path):
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
    unc = len(drc.get("unconnected_items", []))
    if unc:
        counts["unconnected_items"] = unc
    g = Result("DRC", "DRC classes against the accepted set (24 silk_edge_clearance, "
               "nothing else)", 1)
    bad = []
    for k, n in sorted(counts.items()):
        want = ACCEPTED_DRC.get(k, 0)
        if n != want:
            bad.append(f"{k}: {n}, accepted {want}"
                       + (" (DRC lists at most 499)" if k == "unconnected_items" and n >= 499 else ""))
    for k, want in ACCEPTED_DRC.items():
        if k not in counts:
            bad.append(f"{k}: 0, accepted {want} (B-21: the straddlers' own outlines)")
    off = sorted(r for r in silk_refs if r not in cbp.STRADDLERS)
    if off:
        bad.append(f"silk_edge_clearance on parts that are not straddlers: {off}")
    g.set(FAIL if bad or rules.status != PASS else PASS,
          "every class: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "none"),
          *bad)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", type=Path)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--nets", type=Path)
    ap.add_argument("--region")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--kicad-happy", action="store_true")
    ap.add_argument("--skip-drc", action="store_true",
                    help="skip layer 1's DRC (reported NOT CHECKED; for quick runs)")
    a = ap.parse_args(argv)
    if pcbnew is None:
        print("judge_layout.py needs KiCad's Python (import pcbnew failed):\n  " + KICAD_PY)
        return 2
    board = a.board.resolve()
    contract = json.loads(cbp.CONTRACT_PATH.read_text())
    nets_filter = set(a.nets.read_text().split()) if a.nets else None
    region = tuple(float(v) for v in a.region.split(",")) if a.region else None

    work = Path(tempfile.mkdtemp(prefix="judge-"))
    try:
        gates = []
        if a.skip_drc:
            gates.append(Result("DRC", "DRC classes against the accepted set", 1).set(
                NOT_CHECKED, "--skip-drc"))
        else:
            gl, _drc, _counts = drc_gates(board, work)
            gates += gl
        staged = stage(board, work / "model")
        m = Model(staged)
        m.path = board  # FIXED reads the original file's text
        cg = Result("CONNECTIVITY", "KiCad's unrouted-connection count after filling every "
                    "zone (routing-brief §1)", 1)
        cg.set(PASS if m.unconnected == 0 else FAIL,
               f"GetUnconnectedCount(False) = {m.unconnected} after filling "
               f"{len(m.zones_meta)} pours")
        gates.append(cg)
        gates.append(placement_gate(board, contract, nets_filter))
        gates.append(region_gate(m, nets_filter, region))

        j = Judge(m, contract, nets_filter, region)
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

        failing = [g.id for g in gates if g.status not in (PASS, NA)] + \
                  [r.id for r in j.results if r.status not in (PASS, NA)]
        code = 0 if not failing else 1
        out = {
            "board": str(board), "baseline": str(a.baseline) if a.baseline else None,
            "nets_file": str(a.nets) if a.nets else None, "region": region,
            "gates": [g.as_dict() for g in gates],
            "checks": [r.as_dict() for r in j.results],
            "metrics": metrics, "baseline_metrics": base_metrics,
            "metrics_delta": delta(metrics, base_metrics) if base_metrics else None,
            "kicad_happy": kh, "judges_readings": READINGS,
            "not_passing": failing, "exit_code": code,
        }
        jp = a.json or (Path(tempfile.gettempdir()) / f"judge-{board.stem}.json")
        jp.write_text(json.dumps(out, indent=1, default=str))

        P = print
        P(f"judge_layout: {board}")
        if nets_filter is not None or region:
            P(f"  region copy: {len(nets_filter or [])} listed nets, region {region}")
        P("LAYER 1  GATES")
        for g in gates:
            P(f"  [{g.status:^11}] {g.id}: {g.title}")
            for e in g.evidence[:12]:
                P(f"      {e}")
        P("LAYER 2  RULES NO TOOL CHECKS ([judge's reading] where the rule leaves a number open)")
        for r in j.results:
            P(f"  [{r.status:^11}] {r.id}: {r.title}" + ("  [judge's reading]" if r.reading else ""))
            for e in r.evidence[:8]:
                P(f"      {e}")
            if len(r.evidence) > 8:
                P(f"      ... {len(r.evidence) - 8} more in the JSON")
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
        P(f"RESULT: exit {code}" + (f"; not passing: {', '.join(failing)}" if failing else
                                    "; every gate and rule check PASS or N/A"))
        P(f"JSON: {jp}")
        return code
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
