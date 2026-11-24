"""hardware/gen/judge_layout.py, the automatic layout judge (task J1, 2026-11-21).

The judge runs under the Python that ships inside KiCad (it needs `pcbnew`) and calls
`kicad-cli`, so these tests are skipped where KiCad is not installed, which includes CI.
Each runs the judge in a subprocess, as a person would.

1. The committed, unrouted board gives the unrouted result: the gates fail on DRC's
   unconnected items and connectivity (and the rules are proved loaded); every layer-2 check
   is PASS or N/A except the two that cannot be checked on it (G-1's mounting hardware, and
   FIXED, given the board itself as its reference), which are NOT CHECKED and say why.
   FIXED's default reference, the board on the local main, has a test of its own.
2. NEGATIVE CONTROLS, in pairs (fix round 1): a BASE scratch board on which the clause under
   test passes, and a CONTROL that changes one thing in it. The control must FAIL that clause
   with the planted violation named in its evidence, and every other check must report the
   same status on both boards, so a control cannot pass by failing for some other reason.
   The boards are made from the committed board with pcbnew in one subprocess; DRC is
   skipped (--skip-drc), since what is under test is layer 2.
3. The reviewer's three reproductions (fix round 1, C-1 and I-2): n1, n10 and b13.
4. Region mode, and --allow-not-checked.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
JUDGE = REPO / "hardware" / "gen" / "judge_layout.py"
BOARD = REPO / "hardware" / "breakout" / "breakout.kicad_pcb"
KICAD_PY = Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
                "Versions/Current/bin/python3")

pytestmark = pytest.mark.skipif(
    not KICAD_PY.exists() or shutil.which("kicad-cli") is None,
    reason="needs KiCad 10's bundled Python and kicad-cli")

OK = {"PASS", "N/A"}

# Helpers the mutation snippets are written against. Coordinates in mm.
PRELUDE = r'''
import json, sys, pcbnew
F = pcbnew.FromMM
def V(x, y): return pcbnew.VECTOR2I(F(x), F(y))
LAYER = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu, "B.Cu": pcbnew.B_Cu}
def net(name):
    n = b.FindNet(name)
    assert n is not None, name
    return n
def pad(ref, num):
    return b.FindFootprintByReference(ref).FindPadByNumber(num)
def pad_xy(ref, num):
    p = pad(ref, num)
    return pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
def track(name, layer, pts, w=0.25):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2)); t.SetWidth(F(w))
        t.SetLayer(LAYER[layer]); t.SetNet(net(name)); b.Add(t); ADDED.append((t, name))
def via(name, x, y):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(x, y)); v.SetWidth(F(0.6)); v.SetDrill(F(0.3))
    v.SetNet(net(name)); b.Add(v); ADDED.append((v, name))
def zonepoly(name, layer, pts, zname=None):
    z = pcbnew.ZONE(b)
    z.SetLayer(LAYER[layer]); z.SetNet(net(name))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)   # solid, as the trial boards' planes
    o = z.Outline(); o.NewOutline()
    for x, y in pts:
        o.Append(F(x), F(y))
    z.SetZoneName(zname or f"test_{name}_{layer}"); b.Add(z)
def zone(name, layer, x0, y0, x1, y1):
    zonepoly(name, layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
def move(ref, x, y):
    b.FindFootprintByReference(ref).SetPosition(V(x, y))
def fpid(ref, lib, name):
    b.FindFootprintByReference(ref).SetFPID(pcbnew.LIB_ID(lib, name))
def set_pad_net(ref, num, name):
    pad(ref, num).SetNet(net(name))
def nudge_area(name, dx):
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetZoneName() == name:
            z.Outline().Move(V(dx, 0))
def rule_area(zname, x0, y0, x1, y1):   # a keep-out on the four copper layers
    z = pcbnew.ZONE(b)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
    z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET()
    for l in LAYER.values():
        ls.AddLayer(l)
    z.SetLayerSet(ls)
    o = z.Outline(); o.NewOutline()
    for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        o.Append(F(x), F(y))
    z.SetZoneName(zname); b.Add(z)
def strip_all():   # every pad netless: a region copy with nothing listed, clean on every gate
    for f in b.GetFootprints():
        for p in f.Pads():
            p.SetNetCode(0)
def via_in_pads(refs_pins):
    for ref, num in refs_pins:
        x, y = pad_xy(ref, num)
        via(pad(ref, num).GetNetname(), x, y)
SMD_CU = None
def _smd_cu():
    global SMD_CU
    if SMD_CU is None:
        SMD_CU = []
        for f in b.GetFootprints():
            for p in f.Pads():
                if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and p.IsOnLayer(pcbnew.F_Cu):
                    SMD_CU.append((p, p.GetEffectivePolygon(pcbnew.F_Cu, pcbnew.ERROR_OUTSIDE),
                                   pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)))
    return SMD_CU
def _gap(poly, x, y):
    return pcbnew.ToMM(int(poly.SquaredDistance(V(x, y)) ** 0.5))
def via_beside_pads(refs_pins, web=0.25):
    """Join each pad to a via of its own net beside it, by an L-shaped 0.2 mm F.Cu stub
    (along x, then along y). The via is `web` mm clear of every SMD pad (N-16 asks 0.2),
    0.75 mm from the vias already added, and the stub touches no other net's pad. A
    straddler's pad (U56 to U62) is left westward from its outer end, as B-13 asks."""
    global SMD_CU
    SMD_CU = None
    placed = []
    for ref, num in refs_pins:
        pd_ = pad(ref, num); name = pd_.GetNetname()
        px, py = pad_xy(ref, num)
        bb = pd_.GetBoundingBox()
        straddler = ref in ("U56", "U57", "U58", "U59", "U60", "U61", "U62")
        sx = pcbnew.ToMM(bb.GetLeft()) + 0.1 if straddler else px
        near = [(q, poly) for q, poly, qx, qy in _smd_cu() if abs(qx - px) < 4 and abs(qy - py) < 4]
        cands = sorted(((abs(i * 0.05) + abs(j * 0.05), i, j) for i in range(-40, 41)
                        for j in range(-40, 41)), key=lambda t: (t[0], t[1], t[2]))
        for _d, i, j in cands:
            vx, vy = round(sx + i * 0.05, 3), round(py + j * 0.05, 3)
            if straddler and vx > sx - 0.25:
                continue
            if any(abs(vx - ax) < 0.75 and abs(vy - ay) < 0.75 and
                   (vx - ax) ** 2 + (vy - ay) ** 2 < 0.75 ** 2 for ax, ay in placed):
                continue
            if any(_gap(poly, vx, vy) < 0.3 + web for q, poly in near):
                continue
            pts = [(sx, py), (vx, py), (vx, vy)]
            samples = [(a[0] + (c[0] - a[0]) * t / 20, a[1] + (c[1] - a[1]) * t / 20)
                       for a, c in zip(pts, pts[1:]) for t in range(21)]
            if any(q.GetNetname() != name and _gap(poly, x, y) < 0.15
                   for q, poly in near for x, y in samples):
                continue
            break
        else:
            raise AssertionError(f"no via site beside {ref}.{num}")
        track(name, 'F.Cu', [p for k, p in enumerate(pts) if k == 0 or p != pts[k - 1]], 0.2)
        via(name, vx, vy)
        placed.append((vx, vy))
'''

DRIVER = PRELUDE + r'''
cases = json.loads(open(sys.argv[2]).read())
for dst, code in cases:
    b = pcbnew.LoadBoard(sys.argv[1])
    ADDED = []
    exec(code)
    # pcbnew gives a track the net of a foreign pad it lands on; a control that did so
    # would test something else, so refuse it
    b.BuildConnectivity()
    for item, name in ADDED:
        assert item.GetNetname() == name, (dst, name, item.GetNetname(), code[:80])
    pcbnew.SaveBoard(dst, b)
'''

# ---- bases -------------------------------------------------------------------------------
TRIVIAL = "track('RWD_BTN', 'F.Cu', [(28, 28), (31, 28)])"   # makes the copy differ
G10 = ("track('MUX1_A0', 'F.Cu', [(168, 60), (172, 60)]);"
       "track('MUX1_A1', 'F.Cu', [(168, 62), (172, 62)])")
BC_DGND = "track('BARCODE_BUF', 'B.Cu', [(120, 150), (125, 150)])"
BNI = "track('BARCODE_NI', 'F.Cu', [(10, 200), (15, 200)])"
BINTAN = "track('BARCODE_INTAN', 'F.Cu', [(120, 205), (125, 205)])"
G1 = "zone('AGND', 'In1.Cu', 300, 100, 310, 110); zone('DGND', 'In2.Cu', 315, 100, 325, 110)"
H1 = "track('DGND', 'F.Cu', [(31, 77), (33, 77)])"
FANF = "zone('FAN_RTN', 'F.Cu', 100, 40, 110, 50)"
CHASSIS = "track('CHASSIS_GND', 'F.Cu', [(165, 55), (161, 59), (161, 101.5), (163.5, 101.5)])"
CHASSIS_CUT = ("track('CHASSIS_GND', 'F.Cu', [(165, 55), (161, 59), (161, 80)]);"
               "track('CHASSIS_GND', 'F.Cu', [(161, 85), (161, 101.5), (163.5, 101.5)])")
G16 = "x, y = pad_xy('J2', '62'); track('AGND', 'F.Cu', [(x, y), (x, y - 2.0)])"
POUR_ZONE = "zone('{net}', 'F.Cu', 250, 110, 255, 115)"


def routed(net, y):   # a protected net with a little copper of its own, so G-17 to G-20 apply
    return f"track('{net}', 'F.Cu', [(250, {y}), (253, {y})])"
S7 = "zone('NI_GND', 'In1.Cu', 10, 150, 20, 160)"
X6 = "track('RWD_BTN', 'F.Cu', [(410.4, 4.0), (410.4, 8.0)])"
P5 = "track('U3_FB', 'F.Cu', [pad_xy('C13', '1'), pad_xy('U3', '2')], 0.2)"
P8 = "track('U63_VIN', 'F.Cu', [pad_xy('L1', '2'), pad_xy('U63', '2')], 0.5)"
K11 = "track('A_PD1_CLAMP', 'F.Cu', [(186, 211), (188, 211)])"
NI_PLANES = ("zone('NI_GND', 'In1.Cu', 1, 101, 68, 239); zone('NI_5V', 'In2.Cu', 1, 101, 68, 239);"
             "via_beside_pads([(f'C{n}', p) for n in range(121, 135) for p in ('1', '2')]"
             " + [(f'U{u}', p) for u in range(56, 62) for p in ('9', '10', '15', '16')])")
NI_GND_NOTCH = ("zonepoly('NI_GND', 'In1.Cu', [(1, 101), (68, 101), (68, 239), (38.6, 239),"
                " (38.6, 231.3), (34.4, 231.3), (34.4, 239), (1, 239)]);"
                "zone('NI_5V', 'In2.Cu', 1, 101, 68, 239);"
                "via_beside_pads([(f'C{n}', p) for n in range(121, 135) for p in ('1', '2')]"
                " + [(f'U{u}', p) for u in range(56, 62) for p in ('9', '10', '15', '16')])")
N4_STUBS = ("zone('NI_GND', 'In1.Cu', 1, 101, 68, 239);"
            "x, y = pad_xy('J45', '8'); track('NI_5V', 'F.Cu', [(x, y), (x, y - 2.0)], 0.6);"
            "x, y = pad_xy('J45', '14'); track('NI_5V', 'F.Cu', [(x, y), (x, y - 2.0)], 0.6)")
N4_ONE = ("zone('NI_GND', 'In1.Cu', 1, 101, 68, 239);"
          "x, y = pad_xy('J45', '8'); track('NI_5V', 'F.Cu', [(x, y), (x, y - 2.0)], 0.6)")


def n1(w):
    return (f"track('+5V', 'F.Cu', [pad_xy('D3', '1'), (150.0, 85.08)], {w});"
            "via('+5V', 150.0, 85.08); zone('+5V', 'In2.Cu', 140, 80, 152, 90)")


def n2(w):
    return (f"track('-12V', 'F.Cu', [pad_xy('D2', '2'), (195.0, 83.0)], {w});"
            "via('-12V', 195.0, 83.0); zone('-12V', 'In2.Cu', 193, 78, 205, 88)")


def rv_n1(w):   # the reviewer's n1 (j1-review/mutate.py), with the track width varied
    return ("zone('+5V', 'F.Cu', 151.0, 83.0, 156.5, 87.0);"
            f"track('+5V', 'F.Cu', [(151.2, 85.08), (145.0, 85.08)], {w});"
            "via('+5V', 145.0, 85.08); zone('+5V', 'In2.Cu', 138.0, 80.0, 146.0, 90.0)")


def rv_n10(y0, y1):   # the reviewer's n10, with the strip's width varied
    return ("track('+5V', 'F.Cu', [(154.5, 85.08), (152.0, 85.08), (152.0, 88.0)], 1.0);"
            "via('+5V', 152.0, 88.0);"
            f"zone('+5V', 'B.Cu', 78.0, {y0}, 152.5, {y1});"
            "via('+5V', 78.5, 88.0);"
            "track('+5V', 'F.Cu', [(78.5, 88.0), (78.5, 113.5), (76.325, 113.5)], 1.0)")


def n10_tracks(w):
    return (f"x, y = pad_xy('R122', '1'); d = pad_xy('D3', '1');"
            f"track('+5V', 'F.Cu', [d, (x, d[1]), (x, y)], {w})")


def rv_b13(y):   # the reviewer's b13, with where the track starts in C162 pad 1 varied
    return f"track('DGND', 'F.Cu', [(148.0, {y}), (145.0, 160.1), (143.5, 159.92)], 0.25)"


B13_DIRECT = "track('DGND', 'F.Cu', [(148.0, 162.6), (145.8, 159.92), (143.5, 159.92)], 0.25)"
B13_DETOUR = ("track('DGND', 'F.Cu', [(148.0, 162.6), (146.0, 161.2), (146.0, 155.0),"
              " (143.5, 157.5), (143.5, 159.92)], 0.25)")
B13A = "track('EVT_D0_BUF', 'F.Cu', [({x}, 112.175), (75.0, 112.175)])"
B16 = "x, y = pad_xy('U63', '1'); track('ISO_5V', 'B.Cu', [(x - {a}, y), (x - {b}, y)])"
B16S = "x, y = pad_xy('U2', '1'); track('DGND', 'B.Cu', [(x, y), (x, {y2})])"
AGND8 = ";".join(f"track('AGND', 'F.Cu', [({200 + k}, 163), ({200 + k}, 167)])" for k in range(8))
AGND9 = AGND8 + ";track('AGND', 'F.Cu', [(208, 163), (208, 167)])"
B18_MUX = ("p28 = pad_xy('U38', '28'); p3 = pad_xy('U39', '3'); p2 = pad_xy('U39', '2');"
           "track('MUX_OUT1', 'F.Cu', [p28, (p3[0], p28[1]), p3]);"
           "Lm = abs(p3[0] - p28[0]) + abs(p3[1] - p28[1]);")


def b18_sense(dy):   # the sense trace straight up from U39 pin 2, dy shorter than MUX_OUT1
    return (B18_MUX + f"ys = p2[1] - Lm + {dy};"
            "track('AGND', 'F.Cu', [p2, (p2[0], ys)]); via('AGND', p2[0], ys);"
            "zone('AGND', 'In1.Cu', p2[0] - 4, ys - 3, p2[0] + 4, min(ys + 3, 163.5))")


B18_FAR = (B18_MUX + "track('AGND', 'F.Cu', [p2, (p2[0], 163.2), (187.2, 163.2)]);"
           "via('AGND', 187.2, 163.2); zone('AGND', 'In1.Cu', 184, 160.5, 190, 163.6)")
B18_NOSENSE = B18_MUX + "zone('AGND', 'In1.Cu', 171.6, 148, 179.6, 154)"


def n9(dm_end=150.0, dm_y=120.45, pour_x1=152, vbus=False):
    return ("track('USB_DP_PI', 'F.Cu', [(140, 120), (150, 120)], 0.2);"
            f"track('USB_DM_PI', 'F.Cu', [(140, {dm_y}), ({dm_end}, {dm_y})], 0.2);"
            f"zone('DGND', 'In1.Cu', 138, 118, {pour_x1}, 123)"
            + (";x, y = pad_xy('J18', '1'); track('USB_VBUS_PI', 'F.Cu', [(x, y), (x - 2, y)])"
               if vbus else ""))


def k33(jx):
    return ("a = pad_xy('U8', '18'); c = pad_xy('U70', '18'); l = pad_xy('U56', '2');"
            "via('EVT_D0_BUF', *a); via('EVT_D0_BUF', *c); via('EVT_D0_BUF', *l);"
            f"j = (a[0] + {jx}, a[1]);"
            "track('EVT_D0_BUF', 'B.Cu', [a, j]); track('EVT_D0_BUF', 'B.Cu', [j, c]);"
            "track('EVT_D0_BUF', 'B.Cu', [j, l])")


def k23(shld_end=204, shld_via=False):
    return ("track('A_MISC1_MID', 'F.Cu', [(199, 99), (204, 99)]);"
            f"track('A_MISC1_SHLD_MID', 'F.Cu', [(199, 101), ({shld_end}, 101)])"
            + (";via('A_MISC1_SHLD_MID', 199, 101)" if shld_via else ""))


# N-16: a via on R24 pad 2's own net (RWD_BTN), 0.25 mm above the pad. The controls put one
# in the pad, and one 0.10 mm above it.
N16_BASE = "via('RWD_BTN', 83.0, 182.725)"
# For the U3/U4 cases: an INTAN_GND via in open island too, so that B-16 (which looks at
# INTAN_GND copper) is not N/A on the base and PASS on the board with the U3 via.
N16_INTAN = N16_BASE + ";via('INTAN_GND', 130.0, 205.0)"


# Each case: (check, clause prefix, base, control, evidence the control must print,
#             clause status on the base [PASS unless stated]).
CASES = {
    "G-10/foreign": ("G-10", "only G-11 nets cross", G10,
                     G10 + ";track('RWD_BTN', 'F.Cu', [(168, 64), (172, 64)])", "RWD_BTN on F.Cu"),
    "G-10/twice": ("G-10", "each G-11 net crosses once", G10,
                   G10 + ";track('MUX1_A0', 'B.Cu', [(168, 61), (172, 61)])",
                   "MUX1_A0 crosses 2 times"),
    "G-10/corridor-y": ("G-10", "every crossing at y", G10,
                        "track('MUX1_A0', 'F.Cu', [(168, 60), (172, 60)]);"
                        "track('MUX1_A1', 'F.Cu', [(168, 40), (172, 40)])", "MUX1_A1 on F.Cu at (170, 40.00)"),
    "G-10/one-corridor": ("G-10", "one corridor", G10,
                          "track('MUX1_A0', 'F.Cu', [(168, 60), (172, 60)]);"
                          "track('MUX1_A1', 'F.Cu', [(168, 70), (172, 70)])",
                          "gap of 10.00 mm between y = 60.00 and 70.00"),
    "K-2": ("K-2", "no barcode copper in AGND", BC_DGND,
            "track('BARCODE_BUF', 'B.Cu', [(200, 100), (205, 100)])", "inside AGND"),
    "K-4/island": ("K-4", "inside its own island", BNI,
                   "track('BARCODE_NI', 'F.Cu', [(150, 205), (155, 205)])", "outside the NI island"),
    # SHORT on a net not yet joined (a stub): NOT CHECKED on the base, never PASS (R2 fix
    # round 1, M-1); the control's detour is over 1.5x already, a FAIL
    "K-4/short": ("K-4", "the shortest path", BNI,
                  BNI + ";track('BARCODE_NI', 'F.Cu', [(6, 196), (54, 196), (6, 199), (54, 199),"
                  " (6, 202), (54, 202), (6, 205), (54, 205)])", "over 1.5x", "NOT CHECKED"),
    "K-4/clear": ("K-4", "Intan barcode clear", BINTAN,
                  BINTAN + ";track('INTAN_AO1', 'F.Cu', [(120, 206), (125, 206)])",
                  "within 2.5 mm of track INTAN_AO1"),
    "G-1/pads": ("G-1", "only NT1 has pads on both", G1, G1 + ";set_pad_net('C9', '1', 'AGND')",
                 "['C9', 'NT1']"),
    "G-1/overlap": ("G-1", "no AGND/DGND pour overlap", G1,
                    "zone('AGND', 'In1.Cu', 300, 100, 310, 110);"
                    "zone('DGND', 'In2.Cu', 305, 105, 315, 115)", "pour overlap"),
    "G-1/H1": ("G-1", "the 10 holes of MECHANICAL", H1, "track('DGND', 'F.Cu', [(28, 77), (33, 77)])",
               "copper touches H1"),
    "G-3/tie": ("G-3", "only its tie joins", FANF, FANF + ";set_pad_net('C161', '1', 'FAN_RTN')",
                "FAN_RTN: parts joining it to DGND"),
    "G-3/plane": ("G-3", "not poured on a plane layer", FANF,
                  "zone('FAN_RTN', 'In1.Cu', 100, 40, 110, 50)", "FAN_RTN poured on a plane layer"),
    "G-4": ("G-4", "NT2 within", TRIVIAL, TRIVIAL + ";move('NT2', 150.0, 98.5)", "NT2 at (150.00,98.50)"),
    "G-5/distance": ("G-5", "NT3 within", CHASSIS, CHASSIS + ";move('NT3', 145.0, 101.5)",
                     "NT3 at (145.00,101.50)"),
    "G-5/pour": ("G-5", "no CHASSIS_GND pour", CHASSIS,
                 CHASSIS + ";zone('CHASSIS_GND', 'B.Cu', 155, 70, 160, 75)", "CHASSIS_GND poured"),
    "G-5/trace": ("G-5", "J56 reaches NT3 by tracks", CHASSIS, CHASSIS_CUT,
                  "does not reach NT3 pad 1"),
    "G-6/distance": ("G-6", "NT4 within", TRIVIAL, TRIVIAL + ";move('NT4', 125.0, 200.0)",
                     "mm from U63 pad 6"),
    "G-6/pins": ("G-6", "ISO_5V_RTN has exactly its five pins", TRIVIAL,
                 TRIVIAL + ";set_pad_net('TP7', '1', 'ISO_5V_RTN')", "('TP7', '1')"),
    "G-7": ("G-7", "the four ties as drawn", TRIVIAL,
            TRIVIAL + ";fpid('NT3', 'NetTie', 'NetTie-2_SMD_Pad0.5mm')", "NT3 is NetTie-2_SMD_Pad0.5mm"),
    "G-16/bare": ("G-16", "unused pins bare", G16,
                  "track('AGND', 'F.Cu', [pad_xy('J2', '62'), pad_xy('J2', '61')])",
                  "(unconnected-(J2-Pad61)) touched"),
    "G-16/pin62": ("G-16", "pin 62 on AGND", TRIVIAL, TRIVIAL + ";set_pad_net('J2', '62', 'DGND')",
                   "J2 pad 62 is on DGND"),
    "G-17": ("G-17", "no pour on the net", routed("AO_GND_TPC", 100),
             routed("AO_GND_TPC", 100) + ";" + POUR_ZONE.format(net="AO_GND_TPC"), "on AO_GND_TPC"),
    "G-18": ("G-18", "no pour on the net", routed("A_PD1_SHLD", 102),
             routed("A_PD1_SHLD", 102) + ";" + POUR_ZONE.format(net="A_PD1_SHLD"), "on A_PD1_SHLD"),
    "G-19": ("G-19", "no pour on the net", routed("ACCESIO_AGND", 104),
             routed("ACCESIO_AGND", 104) + ";" + POUR_ZONE.format(net="ACCESIO_AGND"), "on ACCESIO_AGND"),
    "G-20/pour": ("G-20", "no pour on the net", routed("ACCESIO_SHELL", 106),
                  routed("ACCESIO_SHELL", 106) + ";" + POUR_ZONE.format(net="ACCESIO_SHELL"),
                  "on ACCESIO_SHELL"),
    "G-20/R224": ("G-20", "R224 within", TRIVIAL, TRIVIAL + ";move('R224', 334.5, 40.0)", "to J19.SH"),
    "S-7/layer": ("S-7", "each plane net on its plane layer", S7,
                  "zone('NI_GND', 'In2.Cu', 10, 150, 20, 160)", "S-6/S-7 put NI_GND on In1.Cu"),
    "S-7/region": ("S-7", "each plane-net pour inside", S7,
                   S7 + ";zone('NI_GND', 'In1.Cu', 75, 150, 85, 160)", "outside the NI region"),
    "X-6": ("X-6", "post-mount pads clear", X6, "track('RWD_BTN', 'F.Cu', [(411.0, 4.0), (411.0, 8.0)])",
            "J20.(unnumbered) (412.92,5.56) (no net) touched"),
    "P-5/pin7": ("P-5", "pin 7 gets no copper", P5,
                 P5 + ";x, y = pad_xy('U3', '7'); track('unconnected-(U3-DNC-Pad7)', 'F.Cu',"
                 " [(x, y), (x + 1.5, y)])", "U3.7"),
    "P-5/short": ("P-5", "FB and NR/SS on short traces", P5,
                  P5 + ";track('U3_FB', 'F.Cu', [(150, 205), (165, 205), (165, 210)])", "over 1.5x",
                  "NOT CHECKED"),
    "P-5/caps": ("P-5", "input and output capacitors", P5, P5 + ";move('C14', 139.5, 196.0)", "C14."),
    # the four cases whose control adds or drops the only via carry N16_BASE's via on both
    # boards, so that N-16 is PASS on both and not N/A on one
    "P-8/via": ("P-8", "no via", P8 + ";" + N16_BASE,
                P8 + ";via('U63_VIN', 145.875, 160.78);" + N16_BASE, "via on U63_VIN"),
    "P-8/layer": ("P-8", "F.Cu only", P8, P8 + ";track('U63_VIN', 'B.Cu', [(146, 158), (146.3, 158)])",
                  "U63_VIN off F.Cu"),
    "P-8/pins": ("P-8", "two pins only", P8, P8 + ";set_pad_net('TP1', '1', 'U63_VIN')", "('TP1', '1')"),
    "P-8/short": ("P-8", "short (SHORT)", P8,
                  P8 + ";track('U63_VIN', 'F.Cu', [(118, 150), (132, 150)])", "over 1.5x"),
    "K-11/via": ("K-11", "no via", K11 + ";" + N16_BASE,
                 K11 + ";via('A_PD1_CLAMP', 187, 211);" + N16_BASE, "via on A_PD1_CLAMP"),
    "K-11/layer": ("K-11", "F.Cu only", K11,
                   K11 + ";track('A_PD1_CLAMP', 'B.Cu', [(190, 213), (190.3, 213)])", "A_PD1_CLAMP off F.Cu"),
    "K-11/short": ("K-11", "short (SHORT)", K11,
                   K11 + ";track('A_PD1_CLAMP', 'F.Cu', [(186, 213), (199, 213), (186, 214.5),"
                   " (199, 214.5)])", "over 1.5x", "NOT CHECKED"),
    "K-35/joined": ("K-35", "a capacitor at each pin pair", NI_PLANES,
                    NI_PLANES.replace("for p in ('1', '2')]", "for p in ('1', '2') if (n, p) != (127, '2')]"),
                    "U56 pins 15/16: C127 at 2.25 / 3.98 mm, NOT joined"),
    "K-35/at": ("K-35", "a capacitor at each pin pair", NI_PLANES,
                NI_PLANES + ";move('C127', 55.0, 112.25)", "no capacitor with both pads within 5.0 mm"),
    "N-4/symmetric": ("N-4", "symmetric copper", NI_PLANES,
                      NI_PLANES + ";x, y = pad_xy('J45', '8'); track('NI_5V', 'F.Cu', [(x, y), (x, y - 3.0)], 0.6)",
                      "pin 8: pour on ['In2.Cu'], 3.0 mm"),
    "N-4/nipin": ("N-4", "both NI_5V pins", N4_STUBS, N4_ONE, "J45 pin 14 (NI_5V) has no copper"),
    "N-4/gndpin": ("N-4", "all twelve NI_GND pins used", NI_PLANES, NI_GND_NOTCH, "['53']"),
    "N-1/width": ("N-1", "every track before the In2.Cu plane", n1(1.0), n1(0.5), "0.50 mm track"),
    "N-1/reviewer-n1": ("N-1", "every track before the In2.Cu plane", rv_n1(1.0), rv_n1(0.4),
                        "0.40 mm track (151.20,85.08)-(145.00,85.08)"),
    "N-2/width": ("N-2", "every track before the In2.Cu plane", n2(0.8), n2(0.4), "0.40 mm track"),
    "N-10/tracks": ("N-10", "at most 83 squares", n10_tracks(2.0), n10_tracks(1.0), "R122 pad 1: 10"),
    "N-10/reviewer-n10": ("N-10", "at most 83 squares", rv_n10(84.0, 92.0), rv_n10(87.5, 88.5),
                          "proved over", "NOT CHECKED"),
    "B-13/reviewer-b13": ("B-13", "each trace leaves its pad from the outer end", rv_b13(162.6),
                          rv_b13(163.1), "leaves C162.1 (148.00,162.93) from its inner end"),
    "B-13/shortest": ("B-13", "C162.1 to U63.1 by the shortest trace", B13_DIRECT, B13_DETOUR,
                      "allowance 2.0 mm"),
    "B-13-ALL": ("B-13-ALL", "every straddler pad left from its outer end", B13A.format(x=73.0),
                 B13A.format(x=72.2), "leaves U56.2 ("),
    "B-16/primary": ("B-16", "no Intan copper within", B16.format(a=6.0, b=5.0),
                     B16.format(a=3.5, b=2.2), "Intan copper within 2.5 mm of primary U63.1"),
    "B-16/secondary": ("B-16", "no DGND copper under a secondary pin", B16S.format(y2=170.0),
                       B16S.format(y2="pad_xy('U2', '2')[1]"), "DGND copper under secondary U2.2"),
    "B-17/foreign": ("B-17", "only MUX_OUTn and AGND cross",
                     AGND8 + ";track('RWD_BTN', 'F.Cu', [(209, 160), (209, 163)])",
                     AGND8 + ";track('RWD_BTN', 'F.Cu', [(209, 163), (209, 167)])", "RWD_BTN crosses at (209.00, 165)"),
    "B-17/agnd": ("B-17", "AGND crosses at most eight", AGND8, AGND9, "AGND crosses 9 times"),
    "B-18/beside": ("B-18", "an AGND crossing beside it", b18_sense(0.0) + ";" + N16_BASE,
                    B18_NOSENSE + ";" + N16_BASE,
                    "MUX_OUT1 at x = 174.3"),
    "B-18/length": ("B-18", "MUX_OUTn and its sense trace within", b18_sense(0.0), b18_sense(3.0),
                    "its sense trace 6.0"),
    "B-18/at-mux": ("B-18", "the sense trace taken from the pour", b18_sense(0.0), B18_FAR,
                    "mm from U38"),
    "N-9/length": ("N-9", "lengths within 1.0 mm", n9(), n9(dm_end=151.5), "D- 11.50 mm"),
    "N-9/coupled": ("N-9", "coupled", n9(), n9(dm_y=122.0), "USB_DP_PI: 0% of its length"),
    "N-9/plane": ("N-9", "over unbroken DGND", n9(), n9(pour_x1=145), "samples not over a DGND fill"),
    "N-9/vbus": ("N-9", "J18 pin 1", n9(), n9(vbus=True), "J18 pin 1 touched"),
    "K-33": ("K-33", "legs at most 10 mm", k33(4.5), k33(1.5), "legs 1.50 mm (U8.18)"),
    "K-23/length": ("K-23", "each leg's twin within 1.0 mm", k23(), k23(shld_end=206),
                    "A_MISC1_SHLD_MID 7.00 mm"),
    "K-23/vias": ("K-23", "each leg's twin with as many vias", k23() + ";" + N16_BASE,
                  k23(shld_via=True) + ";" + N16_BASE,
                  "A_MISC1_SHLD_MID 1 vias"),
    "N-16/in-pad": ("N-16", "no via's hole or annulus overlaps", N16_BASE,
                    "via('RWD_BTN', *pad_xy('R24', '2'))", "(RWD_BTN) on F.Cu: the via overlaps the pad"),
    "N-16/web": ("N-16", "every via 0.2 mm from every SMD pad", N16_BASE,
                 "via('RWD_BTN', 83.0, 182.875)", "(RWD_BTN) on F.Cu: under 0.2 mm apart"),
    # N-16's exemption is pad 9 of U3/U4 only: a via in U3's pad 4, on the same net as
    # pad 9 and beside it, still fails
    "N-16/near-U3": ("N-16", "no via's hole or annulus overlaps", N16_INTAN,
                     N16_INTAN + ";via('INTAN_GND', *pad_xy('U3', '4'))",
                     "and U3.4 (141.85,"),
    "FIXED/J1": ("FIXED", "37 fixed parts", TRIVIAL, TRIVIAL + ";move('J1', 174.5, 80.0)", "J1 moved"),
    "FIXED/straddler": ("FIXED", "37 fixed parts", TRIVIAL, TRIVIAL + ";move('U60', 70.0, 169.5)",
                        "U60 moved"),
    "FIXED/rule-area": ("FIXED", "the 17 rule areas", TRIVIAL,
                        TRIVIAL + ";nudge_area('agnd_dgnd_gap', 0.3)", "rule area agnd_dgnd_gap differs"),
    # task R0: an added rule area FAILs; only a mounting hole's own keep-out may be added
    "FIXED/added-area": ("FIXED", "the 17 rule areas", TRIVIAL,
                         TRIVIAL + ";rule_area('extra_keepout', 350, 100, 352, 102)",
                         "rule area extra_keepout is not in the reference"),
}


# N-16's exemption (fix round 2): vias under pad 9 of U3 and U4, as P-6 asks. A pair that
# must AGREE, not a negative control: the via board is judged like the base.
N16_POWERPAD = N16_INTAN + ";via('INTAN_GND', *pad_xy('U3', '9')); via('INTAN_GND', *pad_xy('U4', '9'))"


def key(code: str) -> str:
    return hashlib.sha1(code.encode()).hexdigest()[:12]


def run_judge(board: Path, *extra: str, json_out: Path) -> tuple[int, dict, str]:
    p = subprocess.run([str(KICAD_PY), str(JUDGE), str(board), "--json", str(json_out), *extra],
                       capture_output=True, text=True, timeout=900)
    assert json_out.exists(), p.stdout[-2000:] + p.stderr[-2000:]
    return p.returncode, json.loads(json_out.read_text()), p.stdout


def make_boards(d: Path, codes) -> dict[str, Path]:
    cases = [(str(d / f"{key(c)}.kicad_pcb"), c) for c in dict.fromkeys(codes)]
    (d / "cases.json").write_text(json.dumps(cases))
    (d / "driver.py").write_text(DRIVER)
    p = subprocess.run([str(KICAD_PY), str(d / "driver.py"), str(BOARD), str(d / "cases.json")],
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stderr[-3000:]
    return {c: Path(dst) for dst, c in cases}


@pytest.fixture(scope="module")
def judged(tmp_path_factory):
    """Every base and control board, made once and judged once (keyed by its code)."""
    d = tmp_path_factory.mktemp("controls")
    codes = [c for case in CASES.values() for c in (case[2], case[3])] + [N16_POWERPAD]
    boards = make_boards(d, codes)
    out = {}
    for code, path in boards.items():
        rc, js, stdout = run_judge(path, "--skip-drc", "--reference", str(BOARD),
                                   json_out=path.with_suffix(".json"))
        out[code] = (rc, js, stdout)
    return out


def checks(js) -> dict:
    return {c["id"]: c for c in js["checks"]}


def clause(check: dict, prefix: str) -> dict:
    found = [c for c in check.get("clauses", []) if c["clause"].startswith(prefix)]
    assert found, (prefix, [c["clause"] for c in check.get("clauses", [])], check)
    bad = [c for c in found if c["status"] == "FAIL"]
    return (bad or found)[0]


@pytest.mark.parametrize("name", list(CASES))
def test_negative_control(name, judged):
    rid, prefix, base, control, evidence, *rest = CASES[name]
    base_status = rest[0] if rest else "PASS"
    _, bj, bout = judged[base]
    rc, cj, cout = judged[control]
    bc, cc = checks(bj), checks(cj)
    # the base passes the clause under test (or, where the rule allows no pass on the
    # judge's bound, reports NOT CHECKED), and the control fails it, naming the violation
    assert clause(bc[rid], prefix)["status"] == base_status, (name, bc[rid], bout[-3000:])
    c = clause(cc[rid], prefix)
    assert c["status"] == "FAIL", (name, cc[rid], cout[-3000:])
    assert evidence in " | ".join(c["evidence"]), (name, evidence, c["evidence"])
    assert cc[rid]["status"] == "FAIL"
    # the control changes one thing: no other check moves
    moved = {k: (bc[k]["status"], cc[k]["status"]) for k in bc
             if k != rid and bc[k]["status"] != cc[k]["status"]}
    assert not moved, (name, moved)
    assert rc == 1


def test_n16_exempts_the_powerpads(judged):
    """P-6 asks for vias under pad 9 of U3 and U4; N-16 exempts exactly those pads. With
    a via in each, N-16 still passes, and no check differs from the base."""
    _, bj, _ = judged[N16_INTAN]
    _, pj, pout = judged[N16_POWERPAD]
    bc, pc = checks(bj), checks(pj)
    assert bc["N-16"]["status"] == pc["N-16"]["status"] == "PASS", (pc["N-16"], pout[-2000:])
    assert "4 vias" in " | ".join(pc["N-16"]["clauses"][0]["evidence"])
    moved = {k: (bc[k]["status"], pc[k]["status"]) for k in bc if bc[k]["status"] != pc[k]["status"]}
    assert not moved, moved


def test_ni_planes_bases_pass_n16(judged):
    """J2 fix round 2 (m-3): the NI_PLANES boards join each pad to its plane by a via beside
    it, not in it, so N-16 passes there and the "no other check moves" guard covers N-16 on
    every K-35 and N-4 control too."""
    for code in (NI_PLANES, NI_GND_NOTCH):
        n16 = checks(judged[code][1])["N-16"]
        assert n16["status"] == "PASS", n16


def test_every_layer2_check_has_a_control():
    declared = set(re.findall(r'\bA\("([^"]+)"', JUDGE.read_text()))
    covered = {case[0] for case in CASES.values()}
    assert declared == covered, (declared ^ covered)


def test_unrouted_committed_board(tmp_path):
    # the board as its own reference: FIXED's NOT CHECKED path, on any branch or clone
    code, out, stdout = run_judge(BOARD, "--reference", str(BOARD), json_out=tmp_path / "u.json")
    gates = {g["id"]: g for g in out["gates"]}
    assert code == 1
    # the rules are proved loaded by the control track, not assumed
    assert gates["DRC-RULES"]["status"] == "PASS", gates["DRC-RULES"]
    assert "N-1 INLET track width" in gates["DRC-RULES"]["evidence"][0]
    assert gates["DRC"]["evidence"][0] == \
        "every class: silk_edge_clearance 24, unconnected_items 499"
    assert gates["DRC"]["evidence"][1:] == \
        ["unconnected_items: 499, accepted 0 (DRC lists at most 499)"]
    assert "= 1791 " in gates["CONNECTIVITY"]["evidence"][0]
    assert gates["PLACEMENT"]["status"] == "PASS"
    assert gates["REGION"]["status"] == "N/A"
    st = {c["id"]: c["status"] for c in out["checks"]}
    # G-1's mounting hardware is not on the board, and FIXED's reference is this very
    # file: both NOT CHECKED, printed with their reasons
    assert {k for k, v in st.items() if v not in OK} == {"G-1", "FIXED"}, st
    assert st["G-1"] == st["FIXED"] == "NOT CHECKED"
    assert out["not_passing"] == ["DRC", "CONNECTIVITY", "G-1", "FIXED"]
    assert "[NOT CHECKED] no mounting-hardware path" in stdout
    assert "is this board itself; give --reference" in stdout
    assert "NOT CHECKED, counted: G-1, FIXED" in stdout
    for rid in ("G-3", "G-4", "G-5", "G-6", "G-7", "G-20", "P-5"):
        assert st[rid] == "PASS", rid
    for rid in ("G-10", "K-2", "N-4", "N-10", "B-18", "N-9", "K-33", "K-35", "N-16"):
        assert st[rid] == "N/A", rid
    assert out["metrics"]["connections"] == 1791


@pytest.fixture(scope="module")
def stripped(tmp_path_factory):
    """Fix round 2. A copy of the board with every pad's net removed, judged as a region
    copy listing no net: every gate passes (DRC is run) and every rule check is N/A, so the
    exit code turns on the waiver alone. Plus the same copy with J1 moved (FIXED FAILs)."""
    d = tmp_path_factory.mktemp("stripped")
    clean, moved = "strip_all()", "strip_all(); move('J1', 174.5, 80.0)"
    boards = make_boards(d, [clean, moved])
    nets = d / "none.txt"
    nets.write_text("NO_SUCH_NET\n")

    def run(which, *extra):
        board = boards[clean if which == "clean" else moved]
        out = d / f"{which}-{key(' '.join(extra))}.json"
        return run_judge(board, "--nets", str(nets), *extra, json_out=out)
    return run, boards[clean]


def test_waiver_base_is_clean(stripped):
    run, _ = stripped
    code, out, _ = run("clean", "--reference", str(BOARD))
    assert out["not_passing"] == [] and code == 0
    assert {g["id"]: g["status"] for g in out["gates"]}["DRC"] == "PASS"


def test_unnamed_not_checked_counts(stripped):
    run, board = stripped
    # FIXED against the board itself: NOT CHECKED. Naming another rule does not waive it.
    code, out, stdout = run("clean", "--reference", str(board), "--allow-not-checked", "G-1")
    assert out["not_passing"] == ["FIXED"] and code == 1
    assert "NOT CHECKED, counted: FIXED" in stdout
    assert "named in --allow-not-checked but N/A, so not waived: G-1" in stdout


def test_named_not_checked_is_waived(stripped):
    run, board = stripped
    code, out, stdout = run("clean", "--reference", str(board), "--allow-not-checked", "FIXED")
    assert out["not_passing"] == [] and out["not_checked_allowed"] == ["FIXED"] and code == 0
    assert "WAIVED by --allow-not-checked: FIXED" in stdout
    assert "against a reference: the reference (--reference" in stdout   # its reason, printed


def test_waiver_never_covers_a_fail(stripped):
    run, _ = stripped
    code, out, stdout = run("moved", "--reference", str(BOARD), "--allow-not-checked", "FIXED")
    assert out["not_passing"] == ["FIXED"] and code == 1
    assert {c["id"]: c["status"] for c in out["checks"]}["FIXED"] == "FAIL"
    assert "named in --allow-not-checked but FAIL, so not waived: FIXED" in stdout


def test_skip_drc_never_exits_0(stripped):
    run, board = stripped
    code, out, stdout = run("clean", "--skip-drc", "--reference", str(board),
                            "--allow-not-checked", "FIXED")
    assert out["not_passing"] == ["DRC"] and code == 1
    assert out["not_checked_allowed"] == ["FIXED"]
    assert "DRC was skipped (--skip-drc): this run cannot exit 0" in stdout


def test_gates_cannot_be_named(stripped):
    run, _ = stripped
    for gate in ("DRC", "CONNECTIVITY", "REGION"):
        p = subprocess.run([str(KICAD_PY), str(JUDGE), str(BOARD), "--skip-drc",
                            "--allow-not-checked", f"G-1,{gate}"],
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 2 and "can never be waived" in p.stdout, (gate, p.stdout[-500:])
    p = subprocess.run([str(KICAD_PY), str(JUDGE), str(BOARD), "--allow-not-checked"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 2   # a blanket waiver is not accepted: it needs a list of IDs


def test_allow_not_checked(tmp_path):
    code, out, stdout = run_judge(BOARD, "--skip-drc", "--reference", str(BOARD),
                                  "--allow-not-checked", "G-1,FIXED", json_out=tmp_path / "a.json")
    # the gates are never waived: the skipped DRC and the failing connectivity both stand
    assert out["not_passing"] == ["DRC", "CONNECTIVITY"]
    assert out["not_checked_allowed"] == ["G-1", "FIXED"]
    assert "WAIVED by --allow-not-checked: G-1" in stdout
    assert "no mounting-hardware path: the screws and standoffs" in stdout
    assert code == 1


def _local_main() -> str | None:
    p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", "--short",
                        "refs/heads/main"], capture_output=True, text=True)
    return p.stdout.strip() or None


def test_fixed_defaults_to_main(tmp_path):
    """With no --reference or --baseline, FIXED's reference is the board as committed on the
    local `main` (J2 review, I-2): named in the evidence, whether or not this branch's board
    differs from it."""
    sha = _local_main()
    if sha is None:
        pytest.skip("no local main branch (a clone with only origin/main)")
    code, out, stdout = run_judge(BOARD, "--skip-drc", json_out=tmp_path / "m.json")
    fixed = checks(out)["FIXED"]
    evidence = " | ".join(e for c in fixed["clauses"] for e in c["evidence"])
    main_text = subprocess.run(["git", "-C", str(REPO), "show",
                                "main:hardware/breakout/breakout.kicad_pcb"],
                               capture_output=True, text=True, check=True).stdout
    if main_text == BOARD.read_text():
        assert fixed["status"] == "NOT CHECKED" and "is this board itself" in evidence, fixed
    else:
        # a branch whose board differs from main's: FIXED really compares; where nothing
        # fixed has moved it names its reference
        assert fixed["status"] in ("PASS", "FAIL"), fixed
    if fixed["status"] != "FAIL":
        assert f"main ({sha}):hardware/breakout/breakout.kicad_pcb" in evidence, fixed
    assert code == 1


def test_reviewer_n10_reach_is_not_a_pass(judged):
    """C-1: N-10 once passed with 1 of 30 LED resistors reached. The unreached ones are now
    NOT CHECKED, and the check as a whole cannot PASS."""
    _, js, stdout = judged[n10_tracks(2.0)]
    n10 = checks(js)["N-10"]
    assert clause(n10, "at most 83 squares")["status"] == "PASS"
    reach = clause(n10, "every LED resistor reached")
    assert reach["status"] == "NOT CHECKED"
    assert "1 of 30 reached" in reach["evidence"][0]
    assert n10["status"] == "NOT CHECKED"
    assert "[NOT CHECKED] every LED resistor reached" in stdout


def test_reviewer_n1_outer_pour_is_not_passed(judged):
    """C-1: with a 1.0 mm track, the reviewer's n1 board still crosses an outer-layer +5V
    area whose width the judge does not measure, so N-1 does not PASS."""
    _, js, _ = judged[rv_n1(1.0)]
    n1c = checks(js)["N-1"]
    assert clause(n1c, "pours of +5V on the way")["status"] == "NOT CHECKED"
    assert n1c["status"] == "NOT CHECKED"


def test_region_mode(tmp_path):
    boards = make_boards(tmp_path, ["track('BARCODE_NI', 'F.Cu', [(20, 150), (20, 160)]);"
                                    "track('BARCODE_NI', 'F.Cu', [(90, 150), (95, 150)]);"
                                    "track('RWD_BTN', 'F.Cu', [(30, 150), (30, 155)])"])
    src = next(iter(boards.values()))
    nets = tmp_path / "nets.txt"
    nets.write_text("BARCODE_NI\nNI_5V\nNI_GND\n")
    code, out, stdout = run_judge(src, "--skip-drc", "--nets", str(nets),
                                  "--region", "0,100,70,240", json_out=tmp_path / "r.json")
    gates = {g["id"]: g for g in out["gates"]}
    assert gates["REGION"]["status"] == "FAIL"
    ev = " ".join(gates["REGION"]["evidence"])
    assert "BARCODE_NI" in ev and "outside the region" in ev
    assert "RWD_BTN" in ev and "net not listed" in ev
    st = {c["id"]: c["status"] for c in out["checks"]}
    # nets not listed are absent from a region copy: N/A, never PASS
    for rid in ("G-1", "G-3", "G-10", "N-1", "B-18", "K-23", "G-16", "X-6", "P-5"):
        assert st[rid] == "N/A", (rid, st[rid])
    assert st["K-2"] == "PASS"          # BARCODE_NI is listed and routed, outside AGND
    assert st["K-4"] == "FAIL"          # but a piece of it lies outside the NI island
    assert code == 1


# ---- partial mode (task R2): --routed-nets FILE, PENDING, and the accepted readings -------
# A board routed through stage N is judged on the nets routed so far (routes/routed.nets,
# one net per line). A PROHIBITION is judged on the copper present, listed or not; a
# REQUIREMENT is PENDING until every net it needs is listed.
G10_FOREIGN = CASES["G-10/foreign"][3]      # G10 plus RWD_BTN across x = 170


def gates_of(js) -> dict:
    return {g["id"]: g for g in js["gates"]}


ALL_NETS = sorted(json.loads((REPO / "hardware/breakout/netlist-contract.json").read_text())["nets"])


def write_contract(d: Path, complete=(), touched=None, due=()) -> Path:
    """The stage contract the driver writes (R2 fix round 1): complete.nets, touched.nets
    and due.txt in one directory. touched defaults to every net, so the NET-LISTS gate stays
    out of a test that is not about it."""
    d.mkdir(parents=True, exist_ok=True)
    for name, xs in (("complete.nets", complete), ("touched.nets", ALL_NETS if touched is None
                                                    else touched), ("due.txt", due)):
        (d / name).write_text("".join(x + "\n" for x in xs))
    return d / "complete.nets"


@pytest.fixture(scope="module")
def partial(tmp_path_factory):
    """Scratch boards for partial mode, and a runner that writes the contract files and
    passes --complete-nets (touched.nets and due.txt are read beside it)."""
    d = tmp_path_factory.mktemp("partial")
    boards = make_boards(d, [P8, NI_PLANES, G10, G10_FOREIGN])

    def run(board: Path, *names: str, extra=(), touched=None, due=()):
        tag = key("|".join([str(board), " ".join(names), " ".join(extra), str(touched),
                            " ".join(due)]))
        f = write_contract(d / f"c-{tag}", names, touched, due)
        return run_judge(board, "--complete-nets", str(f), *extra,
                         json_out=d / f"out-{tag}.json")
    return boards, run


NO_DRC = ("--skip-drc", "--reference", str(BOARD))


def test_partial_connectivity_counts_listed_nets(partial):
    boards, run = partial
    # base: U63_VIN routed (L1.2 to U63.2) and listed; the gate counts the listed nets only
    _, out, stdout = run(boards[P8], "U63_VIN", extra=NO_DRC)
    g = gates_of(out)["CONNECTIVITY"]
    ev = " | ".join(g["evidence"])
    assert g["status"] == "PASS", g
    assert "listed nets: 0 unconnected over 1 complete net" in ev, ev
    # the whole-board count is still printed
    assert "GetUnconnectedCount(False) = 1790" in ev and "GetUnconnectedCount(False) = 1790" in stdout
    # control: the same net listed on the unrouted board FAILs, and names the net
    _, out, _ = run(BOARD, "U63_VIN", extra=NO_DRC)
    g = gates_of(out)["CONNECTIVITY"]
    ev = " | ".join(g["evidence"])
    assert g["status"] == "FAIL", g
    assert "listed nets: 1 unconnected over 1 complete net" in ev and "U63_VIN 1" in ev, ev
    assert "GetUnconnectedCount(False) = 1791" in ev


def test_partial_drc_accepts_only_unlisted_unconnected(partial):
    _, run = partial
    # base: nothing listed. Every unconnected_items entry is on an unlisted net and is set
    # aside; the other classes keep the accepted set (24 silk_edge_clearance)
    code, out, stdout = run(BOARD, extra=("--reference", str(BOARD),
                                          "--allow-not-checked", "G-1,FIXED"))
    g = gates_of(out)["DRC"]
    assert g["status"] == "PASS", g
    assert g["evidence"][0] == "every class: silk_edge_clearance 24", g["evidence"]
    assert g["data"]["unconnected_set_aside"] == 499, g["data"]
    assert g["data"]["unconnected_on_listed"] == 0
    # a partial run passes with requirements PENDING: exit 0
    assert out["not_passing"] == [] and code == 0, (out["not_passing"], stdout[-2000:])
    assert checks(out)["N-4"]["status"] == "PENDING"
    # control: list a net DRC reported unconnected; its entries now count, and are named
    net = g["data"]["set_aside_nets"][0]
    code, out, _ = run(BOARD, net, extra=("--reference", str(BOARD),
                                          "--allow-not-checked", "G-1,FIXED"))
    g = gates_of(out)["DRC"]
    ev = " | ".join(g["evidence"])
    assert g["status"] == "FAIL", g
    assert f"[{net}]" in ev and "on a listed net" in ev, ev
    assert g["data"]["unconnected_on_listed"] >= 1
    assert g["data"]["unconnected_on_listed"] + g["data"]["unconnected_set_aside"] == 499
    assert "DRC" in out["not_passing"] and code == 1


def test_partial_requirement_pending(partial):
    boards, run = partial
    # base: the NI planes board, where N-4 PASSes; with both its nets listed it still does
    _, out, _ = run(boards[NI_PLANES], "NI_5V", "NI_GND", extra=NO_DRC)
    assert checks(out)["N-4"]["status"] == "PASS", checks(out)["N-4"]
    # control: N-4's nets unlisted. N-4 is PENDING, not PASS, and says what it waits on
    _, out, stdout = run(boards[NI_PLANES], "U63_VIN", extra=NO_DRC)
    n4 = checks(out)["N-4"]
    assert n4["status"] == "PENDING", n4
    assert n4["class"] == "requirement"
    for c in n4["clauses"]:
        assert c["status"] == "PENDING" and c["so_far"] == "PASS", c
        assert c["class"] == "requirement" and c["pending_on"], c
    assert "[PENDING] all twelve NI_GND pins used" in stdout
    assert "so far PASS" in stdout
    # per clause: with NI_5V listed and NI_GND not, only the NI_GND clause waits
    _, out, _ = run(boards[NI_PLANES], "NI_5V", extra=NO_DRC)
    n4 = checks(out)["N-4"]
    st = {c["clause"]: c["status"] for c in n4["clauses"]}
    assert st == {"all twelve NI_GND pins used": "PENDING", "both NI_5V pins (8, 14) used": "PASS",
                  "symmetric copper": "PASS"}, st
    assert n4["status"] == "PENDING"
    # and on the unrouted board, where N-4 has no copper at all: PENDING, not N/A
    _, out, _ = run(BOARD, extra=NO_DRC)
    assert checks(out)["N-4"]["status"] == "PENDING", checks(out)["N-4"]


def test_partial_prohibition_still_fails(partial):
    boards, run = partial
    nets = ("MUX1_A0", "MUX1_A1", "RWD_BTN")
    # base: two G-11 nets cross x = 170 and are listed: the prohibitions PASS, the corridor
    # (a requirement on all of G-11's nets) waits
    _, out, _ = run(boards[G10], *nets, extra=NO_DRC)
    g10 = checks(out)["G-10"]
    assert clause(g10, "only G-11 nets cross")["status"] == "PASS", g10
    assert clause(g10, "only G-11 nets cross")["class"] == "prohibition"
    assert clause(g10, "one corridor")["status"] == "PENDING", g10
    assert g10["status"] == "PENDING" and g10["class"] == "prohibition+requirement"
    # control: a track across x = 170 on a listed non-G-11 net FAILs G-10
    code, out, stdout = run(boards[G10_FOREIGN], *nets, extra=NO_DRC)
    g10 = checks(out)["G-10"]
    c = clause(g10, "only G-11 nets cross")
    assert c["status"] == "FAIL" and "RWD_BTN on F.Cu" in " | ".join(c["evidence"]), c
    assert g10["status"] == "FAIL" and "G-10" in out["not_passing"] and code == 1
    # judged on the copper present: unlisted, the same copper still FAILs
    _, out, _ = run(boards[G10_FOREIGN], extra=NO_DRC)
    assert clause(checks(out)["G-10"], "only G-11 nets cross")["status"] == "FAIL"


def test_pending_not_allowed_without_routed_nets(judged):
    """Without --routed-nets nothing is PENDING: every board of the controls, judged whole."""
    assert judged
    for code, (rc, js, stdout) in judged.items():
        assert "PENDING" not in stdout, code[:80]
        for c in js["checks"]:
            assert c["status"] != "PENDING", (code[:80], c["id"])
            assert all(cl["status"] != "PENDING" for cl in c.get("clauses", [])), (code[:80], c)
        assert js["complete_nets_file"] is None


def _judge_module():
    import importlib.util
    import sys
    sys.path.insert(0, str(JUDGE.parent))
    spec = importlib.util.spec_from_file_location("judge_layout_under_test", JUDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readings_accepted(judged):
    """The user accepted all 27 READINGS on 2026-11-22 (judge-readings-2026-11-22.md). The
    reading stays marked [judge's reading], so a reader can still tell it from a sourced
    value, with the acceptance beside it."""
    j = _judge_module()
    assert len(j.READINGS) == 27
    assert set(j.READINGS) <= set(j.ACCEPTED_READINGS), set(j.READINGS) - set(j.ACCEPTED_READINGS)
    assert all(j.ACCEPTED_READINGS[k] == "2026-11-22" for k in j.READINGS)
    _, js, stdout = judged[TRIVIAL]
    assert "[judge's reading] [reading accepted 2026-11-22]" in stdout
    with_reading = [c for c in js["checks"] if c.get("judges_reading")]
    assert with_reading
    assert all(c["reading_accepted"] == "2026-11-22" for c in with_reading)
    assert js["readings_accepted"] == {k: "2026-11-22" for k in j.READINGS}
    # G-1's and FIXED's readings grew after the ruling (task R0, mounting holes): said so
    assert set(j.READINGS_EXTENDED_SINCE_RULING) == {"G-1", "FIXED"}
    fixed = checks(js)["FIXED"]
    assert "task R0" in fixed["reading_extended_since_ruling"]


def test_every_clause_is_classified(judged):
    """Each check, and each of its clauses, is a prohibition or a requirement (task R2), so
    partial mode knows which to judge on the copper present and which to hold PENDING."""
    j = _judge_module()
    declared = set(re.findall(r'\bA\("([^"]+)"', JUDGE.read_text()))
    assert set(j.CLASSES) == declared, set(j.CLASSES) ^ declared
    seen = set()
    for code, (rc, js, stdout) in judged.items():
        for c in js["checks"]:
            assert c["class"] in ("prohibition", "requirement", "prohibition+requirement"), c
            for cl in c.get("clauses", []):
                if cl["clause"] == "the check ran":
                    continue
                assert cl["class"] in ("prohibition", "requirement"), (c["id"], cl)
                assert cl["key"] in j.clause_keys(c["id"]), (c["id"], cl)   # its DUE key
                seen.add((c["id"], cl["class"]))
    assert ("N-4", "requirement") in seen and ("G-10", "prohibition") in seen
    # each check's keys are unique to one class, and every pattern compiles
    for rid, specs in j.CLASSES.items():
        kinds = {}
        for key_, pat, kind, nets, why in specs:
            re.compile(pat)
            assert kinds.setdefault(key_, kind) == kind, (rid, key_)
            assert (nets is None) == (kind == "prohibition"), (rid, key_)


def test_contract_bad_argument(tmp_path):
    """Exit 2 on a bad contract: a name that is not a net, a missing file (the sibling
    touched.nets included), a --due entry that is neither a check ID nor a clause key,
    --touched-nets or --due without --complete-nets, and partial mode with --nets."""
    good = write_contract(tmp_path / "good", ["U63_VIN"], ["U63_VIN"], ["G-1/overlap", "S-7"])
    unknown = write_contract(tmp_path / "unknown", ["U63_VIN", "NO_SUCH_NET"])
    baddue = write_contract(tmp_path / "baddue", due=["G-1/no-such-clause"])
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    (lonely / "complete.nets").write_text("U63_VIN\n")       # no touched.nets beside it
    for extra, said in (
            (["--complete-nets", str(unknown)], "NO_SUCH_NET"),
            (["--routed-nets", str(unknown)], "NO_SUCH_NET"),        # the old name
            (["--complete-nets", str(tmp_path / "missing.nets")], "cannot read"),
            (["--complete-nets", str(lonely / "complete.nets")], "--touched-nets: cannot read"),
            (["--complete-nets", str(baddue)], "G-1/no-such-clause"),
            (["--due", str(good.parent / "due.txt")], "give --complete-nets too"),
            (["--complete-nets", str(good), "--nets", str(good)], "Give one or the other")):
        p = subprocess.run([str(KICAD_PY), str(JUDGE), str(BOARD), "--skip-drc", *extra],
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 2, (extra, p.stdout[-800:], p.stderr[-800:])
        assert said in p.stdout, (extra, p.stdout[-800:])


# ---- the stage contract (R2 fix round 1): complete, touched and due ----------------------
PLANE_NETS = ["AGND", "DGND", "NI_GND", "INTAN_GND", "+12V", "-12V", "+5V", "+3V3", "NI_5V",
              "ISO_P12", "ISO_N12", "ISO_5V"]
# The reviewer's scratch R3 (task-R2-review.md, C-1): 15 rectangles for the 12 plane nets,
# grounds on In1.Cu and rails on In2.Cu, each inside its region; no track or via.
RV_R3 = ";".join([
    "zone('NI_GND','In1.Cu',1,101,69,239)", "zone('INTAN_GND','In1.Cu',113,166,237,239)",
    "zone('DGND','In1.Cu',1,1,169,99)", "zone('DGND','In1.Cu',71,99,169,164)",
    "zone('DGND','In1.Cu',71,164,111,239)", "zone('AGND','In1.Cu',171,1,429,164)",
    "zone('AGND','In1.Cu',239,164,429,239)", "zone('NI_5V','In2.Cu',1,101,69,239)",
    "zone('ISO_5V','In2.Cu',113,166,150,239)", "zone('ISO_P12','In2.Cu',151,166,190,239)",
    "zone('ISO_N12','In2.Cu',191,166,237,239)", "zone('+5V','In2.Cu',1,1,169,99)",
    "zone('+3V3','In2.Cu',71,99,169,164)", "zone('+12V','In2.Cu',171,1,429,164)",
    "zone('-12V','In2.Cu',239,164,429,239)"])
# The same planes as a DRC-clean R3 would draw them: one outline per domain, no two zones of
# a net touching (the reviewer's touch, zones_intersect), islands removed, and a via beside
# one pad of each of the three rails no through-hole pin reaches (else isolated_copper),
# on a 0.8 mm stub, their classes' width.
R3_OUTLINES = [
    ("DGND", "In1.Cu", [(1, 1), (169, 1), (169, 164), (111, 164), (111, 239), (71, 239),
                        (71, 99), (1, 99)]),
    ("AGND", "In1.Cu", [(171, 1), (429, 1), (429, 239), (239, 239), (239, 164), (171, 164)]),
    ("NI_GND", "In1.Cu", [(1, 101), (69, 101), (69, 239), (1, 239)]),
    ("INTAN_GND", "In1.Cu", [(113, 166), (237, 166), (237, 239), (113, 239)]),
    ("+5V", "In2.Cu", [(1, 1), (169, 1), (169, 98), (1, 98)]),
    ("+3V3", "In2.Cu", [(71, 100), (169, 100), (169, 164), (111, 164), (111, 239), (71, 239)]),
    ("+12V", "In2.Cu", [(171, 1), (429, 1), (429, 163), (171, 163)]),
    ("-12V", "In2.Cu", [(239, 165), (429, 165), (429, 239), (239, 239)]),
    ("NI_5V", "In2.Cu", [(1, 101), (69, 101), (69, 239), (1, 239)]),
    ("ISO_5V", "In2.Cu", [(113, 166), (149, 166), (149, 239), (113, 239)]),
    ("ISO_P12", "In2.Cu", [(151, 166), (189, 166), (189, 239), (151, 239)]),
    ("ISO_N12", "In2.Cu", [(191, 166), (237, 166), (237, 239), (191, 239)]),
]


def r3_planes(skip=()):
    z = [f"zonepoly('{n}', '{ln}', {pts}); [q.SetIslandRemovalMode("
         f"pcbnew.ISLAND_REMOVAL_MODE_ALWAYS) for q in b.Zones() "
         f"if q.GetZoneName() == 'test_{n}_{ln}']" for n, ln, pts in R3_OUTLINES if n not in skip]
    return ";".join(z + ["via_beside_pads([('C79', '1'), ('C92', '1'), ('C113', '1')])",
                         "[t.SetWidth(F(0.8)) for t, n in ADDED if t.GetClass() == 'PCB_TRACK']"])


R3 = r3_planes()
R3_NO_ISO_N12 = r3_planes(skip=("ISO_N12",))
R3_DUE = ["S-7", "G-1/overlap", "G-3"]
# M-1: U3_FB (4 pads) joined U3.2 to R1.2 only, as the reviewer's probe
HALF_FB = "track('U3_FB','F.Cu',[pad_xy('U3','2'), pad_xy('R1','2')], 0.25)"


def g11_all(drop=()):
    """A 0.8 mm F.Cu track across x = 170 for every G-11 net, inside the split's gap, 2 mm
    apart from y = 52, the stretch about NT1 left clear (a pad there would take the track's
    net)."""
    ys = [y for y in range(52, 166, 2) if not 88 <= y <= 104]
    nets = [n for n in sorted(_judge_module().G11_NETS) if n not in drop]
    assert len(nets) <= len(ys)
    return ";".join(f"track('{n}', 'F.Cu', [(169.6, {y}), (170.4, {y})])"
                    for n, y in zip(nets, ys))


@pytest.fixture(scope="module")
def contract(tmp_path_factory):
    d = tmp_path_factory.mktemp("contract")
    codes = {"rv": RV_R3, "r3": R3, "r3-no-iso-n12": R3_NO_ISO_N12, "half": HALF_FB,
             "g11": g11_all(), "g11-less": g11_all(drop=("+3V3",))}
    boards = make_boards(d, list(codes.values()))
    boards = {k: boards[c] for k, c in codes.items()}

    def run(which, complete=(), touched=None, due=(), extra=("--skip-drc",)):
        tag = key("|".join([which, " ".join(complete), str(touched), " ".join(due),
                            " ".join(extra)]))
        f = write_contract(d / f"c-{tag}", complete, touched, due)
        return run_judge(boards[which], "--complete-nets", str(f), "--reference", str(BOARD),
                         *extra, json_out=d / f"out-{tag}.json")
    return run


def test_contract_r3_planes_pass(contract):
    """R3's shape: the plane nets touched, none complete, DUE = S-7, the G-1 plane clause and
    G-3. The DRC-clean planes exit 0, DRC run, G-1 waived for its hardware clause; the
    reviewer's own board (which DRC faults for zones_intersect and isolated_copper, its own
    drawing) fails on the skipped DRC alone."""
    code, out, stdout = contract("r3", touched=PLANE_NETS, due=R3_DUE,
                                 extra=("--allow-not-checked", "G-1"))
    assert out["not_passing"] == [] and code == 0, (out["not_passing"], stdout[-3000:])
    g = gates_of(out)
    assert g["CONNECTIVITY"]["status"] == g["NET-LISTS"]["status"] == g["DRC"]["status"] == "PASS"
    st = checks(out)
    s7 = st["S-7"]
    assert s7["status"] == "PASS" and {c["key"]: c["status"] for c in s7["clauses"]} == \
        {"S-7/layer": "PASS", "S-7/region": "PASS", "S-7/poured": "PASS"}, s7
    assert all(c["due"] == "S-7" for c in s7["clauses"])
    assert clause(st["G-1"], "no AGND/DGND pour overlap")["due"] == "G-1/overlap"
    assert st["G-3"]["status"] == "PASS"
    for rid in ("K-35", "N-10", "N-1", "B-18"):        # the reviewer's due-too-early checks
        assert st[rid]["status"] == "PENDING", st[rid]
    code, out, _ = contract("rv", touched=PLANE_NETS, due=R3_DUE,
                            extra=("--skip-drc", "--allow-not-checked", "G-1"))
    assert out["not_passing"] == ["DRC"], out["not_passing"]
    assert checks(out)["S-7"]["status"] == "PASS"


def test_contract_r3_missing_plane_fails_s7(contract):
    code, out, _ = contract("r3-no-iso-n12", touched=PLANE_NETS, due=R3_DUE)
    s7 = checks(out)["S-7"]
    c = clause(s7, "every plane net poured")
    assert c["status"] == "FAIL" and c["class"] == "requirement", c
    assert "ISO_N12: 0 mm2 of fill on In2.Cu inside the INTAN region" in c["evidence"], c
    assert s7["status"] == "FAIL" and "S-7" in out["not_passing"] and code == 1
    # not due, the same missing plane waits: S-7's requirement is PENDING, so far FAIL
    _, out, _ = contract("r3-no-iso-n12", touched=PLANE_NETS)
    c = clause(checks(out)["S-7"], "every plane net poured")
    assert c["status"] == "PENDING" and c["so_far"] == "FAIL", c


def test_contract_due_not_met_fails(contract):
    """A DUE check that is not met FAILs the run, N/A included: N-1 on the R3 planes (D3
    has no copper) and K-35 (the bypass capacitors are not joined)."""
    code, out, stdout = contract("r3", touched=PLANE_NETS, due=R3_DUE + ["N-1", "K-35/joined"])
    st = checks(out)
    assert st["N-1"]["status"] == "FAIL", st["N-1"]
    ev = " | ".join(e for c in st["N-1"]["clauses"] for e in c["evidence"])
    assert "DUE (N-1): N/A, not PASS" in ev, ev
    k35 = clause(st["K-35"], "a capacitor at each pin pair")
    assert k35["status"] == "FAIL" and k35["due_was"] == "FAIL" and "NOT joined" in \
        " | ".join(k35["evidence"]), k35
    assert {"N-1", "K-35"} <= set(out["not_passing"]) and code == 1
    assert "DUE (K-35/joined)" in stdout


def test_contract_requirement_not_due_is_pending(contract):
    """On the R3 planes K-35 is not due and its nets are touched, not complete: PENDING, with
    what it found so far (FAIL: not joined) kept; it does not count against the exit code."""
    _, out, stdout = contract("r3", touched=PLANE_NETS, due=R3_DUE)
    c = clause(checks(out)["K-35"], "a capacitor at each pin pair")
    assert c["status"] == "PENDING" and c["so_far"] == "FAIL" and c["pending_on"], c
    assert "K-35" not in out["not_passing"]
    assert "(K-35/joined; requirement; so far FAIL" in stdout


def test_contract_copper_on_an_unlisted_net_fails(contract):
    code, out, _ = contract("r3", touched=[n for n in PLANE_NETS if n != "DGND"], due=R3_DUE)
    g = gates_of(out)["NET-LISTS"]
    assert g["status"] == "FAIL" and g["data"]["stray"] == ["DGND"], g
    assert "copper on 1 net(s) in neither list: DGND" in g["evidence"][0]
    assert "NET-LISTS" in out["not_passing"] and code == 1
    # listed complete instead, it is accounted for (and CONNECTIVITY then counts it)
    _, out, _ = contract("r3", complete=["DGND"], touched=[n for n in PLANE_NETS if n != "DGND"],
                         due=R3_DUE)
    assert gates_of(out)["NET-LISTS"]["status"] == "PASS"
    assert gates_of(out)["CONNECTIVITY"]["status"] == "FAIL"


def test_short_on_an_unjoined_net_never_passes(contract):
    """M-1: U3_FB joined U3.2 to R1.2 only (4.73 mm against an 8.98 mm MST) is not a SHORT
    PASS, in partial mode with its nets complete, due, or in full mode."""
    fb = ["U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS"]
    _, out, _ = contract("half", complete=fb)
    c = clause(checks(out)["P-5"], "FB and NR/SS on short traces")
    assert c["status"] == "NOT CHECKED", c
    assert "U3_FB: 4.73 mm routed against 8.98 mm MST; the net is not fully joined" in \
        c["evidence"][0], c
    assert checks(out)["P-5"]["status"] != "PASS"
    _, out, _ = contract("half", due=["P-5/short"])
    c = clause(checks(out)["P-5"], "FB and NR/SS on short traces")
    assert c["status"] == "FAIL" and c["due_was"] == "NOT CHECKED", c


def test_every_g11_net_crosses(contract):
    """M-3: G-11 lists the nets that must cross x = 170, so each crosses at least once.
    Base: all 48 cross (2 mm apart: one corridor). Control: +3V3 left out."""
    _, out, _ = contract("g11", due=["G-10"])
    g10 = checks(out)["G-10"]
    assert clause(g10, "every G-11 net crosses x = 170")["status"] == "PASS", g10
    assert "all 48 G-11 nets on the board cross" in \
        clause(g10, "every G-11 net crosses x = 170")["evidence"]
    _, out, _ = contract("g11-less", due=["G-10"])
    c = clause(checks(out)["G-10"], "every G-11 net crosses x = 170")
    assert c["status"] == "FAIL" and "1 G-11 net(s) do not cross: +3V3" in c["evidence"][-1], c
    # not due and +3V3 not complete: PENDING
    _, out, _ = contract("g11-less")
    c = clause(checks(out)["G-10"], "every G-11 net crosses x = 170")
    assert c["status"] == "PENDING" and c["so_far"] == "FAIL", c
