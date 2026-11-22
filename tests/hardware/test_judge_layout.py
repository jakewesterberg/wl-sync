"""hardware/gen/judge_layout.py, the automatic layout judge (task J1, 2026-11-21).

The judge runs under the Python that ships inside KiCad (it needs `pcbnew`) and calls
`kicad-cli`, so these tests are skipped where KiCad is not installed, which includes CI.
Each runs the judge in a subprocess, as a person would.

1. The committed, unrouted board gives the unrouted result: the gates fail on
   connectivity only (DRC's only extra class is `unconnected_items`, and the rules are
   proved loaded), and every layer-2 check is PASS or N/A.
2. For every layer-2 check, a NEGATIVE CONTROL: a scratch copy of the committed board with
   one deliberate violation of that rule, asserting that exactly that check FAILs and no
   other check leaves PASS/N/A. The copies are made with pcbnew in one subprocess; DRC is
   skipped on them (--skip-drc), since what is under test is the layer-2 check.
3. Region mode: a track outside the region, or on an unlisted net, fails the REGION gate,
   and checks whose nets are not listed report N/A.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
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

# Helpers the mutation snippets below are written against. Coordinates in mm.
PRELUDE = r'''
import json, sys, pcbnew
F = pcbnew.FromMM
def V(x, y): return pcbnew.VECTOR2I(F(x), F(y))
LAYER = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu, "B.Cu": pcbnew.B_Cu}
def net(name):
    n = b.FindNet(name)
    assert n is not None, name
    return n
def pad_xy(ref, num):
    p = b.FindFootprintByReference(ref).FindPadByNumber(num)
    return pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)
ADDED = []
def track(name, layer, pts, w=0.25):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2)); t.SetWidth(F(w))
        t.SetLayer(LAYER[layer]); t.SetNet(net(name)); b.Add(t); ADDED.append((t, name))
def via(name, x, y):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(x, y)); v.SetWidth(F(0.6)); v.SetDrill(F(0.3))
    v.SetNet(net(name)); b.Add(v); ADDED.append((v, name))
def zone(name, layer, x0, y0, x1, y1):
    z = pcbnew.ZONE(b)
    z.SetLayer(LAYER[layer]); z.SetNet(net(name))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    o = z.Outline(); o.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o.Append(F(x), F(y))
    z.SetZoneName(f"test_{name}"); b.Add(z)
def move(ref, x, y):
    b.FindFootprintByReference(ref).SetPosition(V(x, y))
def fpid(ref, lib, name):
    b.FindFootprintByReference(ref).SetFPID(pcbnew.LIB_ID(lib, name))
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
        assert item.GetNetname() == name, (dst, name, item.GetNetname())
    pcbnew.SaveBoard(dst, b)
'''

# check id -> (what the copy breaks, the mutation)
CONTROLS = {
    "G-10/foreign-net": ("G-10", "a track across x = 170 on RWD_BTN, not a G-11 net",
                         "track('RWD_BTN', 'F.Cu', [(168, 60), (172, 60)])"),
    "G-10/twice": ("G-10", "MUX1_A0, a G-11 net, across x = 170 twice",
                   "track('MUX1_A0', 'F.Cu', [(168, 60), (172, 60)]);"
                   "track('MUX1_A0', 'B.Cu', [(168, 62), (172, 62)])"),
    "G-10/corridor": ("G-10", "MUX1_A1 across x = 170 at y = 20, outside the corridor",
                      "track('MUX1_A1', 'F.Cu', [(165, 20), (175, 20)])"),
    "K-2": ("K-2", "a BARCODE_BUF track inside the AGND region",
            "track('BARCODE_BUF', 'B.Cu', [(200, 100), (205, 100)])"),
    "G-1": ("G-1", "an AGND pour on In1.Cu over a DGND pour on In2.Cu",
            "zone('AGND', 'In1.Cu', 300, 100, 310, 110);"
            "zone('DGND', 'In2.Cu', 305, 105, 315, 115)"),
    "G-3": ("G-3", "FAN_RTN poured on In1.Cu",
            "zone('FAN_RTN', 'In1.Cu', 100, 40, 110, 50)"),
    "G-4": ("G-4", "NT2 moved 20 mm from NT1", "move('NT2', 150.0, 98.5)"),
    "G-5": ("G-5", "CHASSIS_GND poured around J56",
            "zone('CHASSIS_GND', 'F.Cu', 160, 50, 168, 60)"),
    "G-6": ("G-6", "NT4 moved 25 mm from U63", "move('NT4', 125.0, 200.0)"),
    "G-7": ("G-7", "NT3 swapped for another footprint",
            "fpid('NT3', 'NetTie', 'NetTie-2_SMD_Pad0.5mm')"),
    "G-16": ("G-16", "an AGND track from J2 pin 62 onto unused pin 61",
             "track('AGND', 'F.Cu', [pad_xy('J2', '62'), pad_xy('J2', '61')])"),
    "G-17": ("G-17", "a pour on AO_GND_TPC", "zone('AO_GND_TPC', 'F.Cu', 250, 100, 255, 105)"),
    "G-18": ("G-18", "a pour on A_PD1_SHLD", "zone('A_PD1_SHLD', 'F.Cu', 390, 100, 395, 105)"),
    "G-19": ("G-19", "a pour on ACCESIO_AGND",
             "zone('ACCESIO_AGND', 'F.Cu', 250, 110, 255, 115)"),
    "G-20": ("G-20", "a pour on ACCESIO_SHELL",
             "zone('ACCESIO_SHELL', 'F.Cu', 250, 120, 255, 125)"),
    "X-6": ("X-6", "a track onto a BNC post-mount pad of J20",
            "track('RWD_BTN', 'F.Cu', [(412.92, 5.56), (412.92, 9.0)])"),
    "P-5": ("P-5", "a stub on U3 pin 7 (DNC)",
            "x, y = pad_xy('U3', '7'); track('unconnected-(U3-DNC-Pad7)', 'F.Cu', [(x, y), (x + 1.5, y)])"),
    "P-8": ("P-8", "a via on U63_VIN", "via('U63_VIN', 146.0, 158.0)"),
    "K-11": ("K-11", "a via on A_PD1_CLAMP", "via('A_PD1_CLAMP', 405.0, 45.0)"),
    "N-4": ("N-4", "J45 pin 8 routed, pin 14 and the NI_GND pins left bare",
            "x, y = pad_xy('J45', '8'); track('NI_5V', 'F.Cu', [(x, y), (x, y - 3)], 0.6)"),
    "N-1": ("N-1", "+5V leaving D3 at 0.5 mm",
            "track('+5V', 'F.Cu', [pad_xy('D3', '1'), (150.0, 85.08)], 0.5)"),
    "N-2": ("N-2", "-12V leaving D2 at 0.4 mm",
            "track('-12V', 'F.Cu', [pad_xy('D2', '2'), (195.0, 83.0)], 0.4)"),
    "N-10": ("N-10", "a 1.0 mm, 107 mm +5V track from D3 to R122 (107 squares)",
             "x, y = pad_xy('R122', '1'); d = pad_xy('D3', '1');"
             "track('+5V', 'F.Cu', [d, (x, d[1]), (x, y)], 1.0)"),
    "B-13": ("B-13", "a DGND track leaving C162 pad 1 from its inner end",
             "x, y = pad_xy('C162', '1'); track('DGND', 'F.Cu', [(x, y + 0.6), (x, y - 2.0)])"),
    "B-16": ("B-16", "an ISO_5V track on B.Cu 0.7 mm from U63's primary pin 1",
             "x, y = pad_xy('U63', '1'); track('ISO_5V', 'B.Cu', [(x - 3.5, y), (x - 2.2, y)])"),
    "B-17": ("B-17", "RWD_BTN across the AGND/Intan line at x = 200",
             "track('RWD_BTN', 'F.Cu', [(200, 163), (200, 167)])"),
    "B-18": ("B-18", "MUX_OUT1 across the line with no AGND sense trace beside it",
             "track('MUX_OUT1', 'F.Cu', [pad_xy('U38', '28'), pad_xy('U39', '3')])"),
    "N-9": ("N-9", "USB_DP_PI routed alone, with no DGND plane",
            "track('USB_DP_PI', 'F.Cu', [pad_xy('J18', '3'), pad_xy('U66', '13')])"),
    "K-33": ("K-33", "U70's output joined at U8's pin: legs 0 and 9 mm",
             "track('EVT_D0_BUF', 'F.Cu', [pad_xy('U70', '18'), pad_xy('U8', '18'), pad_xy('U56', '2')])"),
    "K-23": ("K-23", "A_MISC1_MID routed, its shield-leg twin not",
             "track('A_MISC1_MID', 'F.Cu', [pad_xy('J28', '5'), pad_xy('R62', '2')])"),
    "FIXED": ("FIXED", "J1 moved 1 mm", "move('J1', 174.5, 80.0)"),
}


def run_judge(board: Path, *extra: str, json_out: Path) -> tuple[int, dict, str]:
    p = subprocess.run([str(KICAD_PY), str(JUDGE), str(board), "--json", str(json_out), *extra],
                       capture_output=True, text=True, timeout=600)
    assert json_out.exists(), p.stdout[-2000:] + p.stderr[-2000:]
    return p.returncode, json.loads(json_out.read_text()), p.stdout


@pytest.fixture(scope="module")
def controls(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("controls")
    cases = [(str(d / f"{name.replace('/', '_')}.kicad_pcb"), textwrap.dedent(code))
             for name, (_, _, code) in CONTROLS.items()]
    (d / "cases.json").write_text(json.dumps(cases))
    (d / "driver.py").write_text(DRIVER)
    p = subprocess.run([str(KICAD_PY), str(d / "driver.py"), str(BOARD), str(d / "cases.json")],
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    return {name: Path(dst) for name, (dst, _) in zip(CONTROLS, cases)}


def test_unrouted_committed_board(tmp_path):
    code, out, stdout = run_judge(BOARD, json_out=tmp_path / "u.json")
    gates = {g["id"]: g for g in out["gates"]}
    assert code == 1
    # the rules are proved loaded by the control track, not assumed
    assert gates["DRC-RULES"]["status"] == "PASS", gates["DRC-RULES"]
    assert "N-1 INLET track width" in gates["DRC-RULES"]["evidence"][0]
    # DRC fails on unconnected items only, and shows the accepted 24
    assert gates["DRC"]["status"] == "FAIL"
    assert gates["DRC"]["evidence"][0] == \
        "every class: silk_edge_clearance 24, unconnected_items 499"
    assert gates["DRC"]["evidence"][1:] == \
        ["unconnected_items: 499, accepted 0 (DRC lists at most 499)"]
    assert gates["CONNECTIVITY"]["status"] == "FAIL"
    assert "= 1791 " in gates["CONNECTIVITY"]["evidence"][0]
    assert gates["PLACEMENT"]["status"] == "PASS"
    assert gates["REGION"]["status"] == "N/A"
    assert out["not_passing"] == ["DRC", "CONNECTIVITY"]
    statuses = {c["id"]: c["status"] for c in out["checks"]}
    assert set(statuses.values()) <= OK, statuses
    # an unrouted board has nothing to judge on a route; placement-level rules pass
    for rid in ("G-1", "G-3", "G-4", "G-5", "G-6", "G-7", "FIXED"):
        assert statuses[rid] == "PASS", rid
    for rid in ("G-10", "K-2", "N-4", "N-10", "B-18", "N-9", "K-33"):
        assert statuses[rid] == "N/A", rid
    assert out["metrics"]["connections"] == 1791
    assert out["metrics"]["vias"] == 0


def test_every_layer2_check_has_a_control():
    ids = {rid for rid, *_ in CONTROLS.values()}
    sys.path.insert(0, str(REPO / "hardware" / "gen"))
    src = JUDGE.read_text()
    declared = set()
    import re
    for m in re.finditer(r'self\.add\("([^"]+)"', src):
        declared.add(m.group(1))
    assert declared == ids, (declared ^ ids)


@pytest.mark.parametrize("name", list(CONTROLS))
def test_negative_control(name, controls, tmp_path):
    rid, what, _ = CONTROLS[name]
    code, out, stdout = run_judge(controls[name], "--skip-drc", json_out=tmp_path / "c.json")
    statuses = {c["id"]: c["status"] for c in out["checks"]}
    not_ok = {k: v for k, v in statuses.items() if v not in OK}
    assert not_ok == {rid: "FAIL"}, f"{what}: {not_ok}\n{stdout[-3000:]}"
    assert code == 1


def test_region_mode(tmp_path):
    src = tmp_path / "region.kicad_pcb"
    cases = [(str(src), "track('BARCODE_NI', 'F.Cu', [(20, 150), (20, 160)]);"
                        "track('BARCODE_NI', 'F.Cu', [(90, 150), (95, 150)]);"
                        "track('RWD_BTN', 'F.Cu', [(30, 150), (30, 155)])")]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    (tmp_path / "driver.py").write_text(DRIVER)
    subprocess.run([str(KICAD_PY), str(tmp_path / "driver.py"), str(BOARD),
                    str(tmp_path / "cases.json")], check=True, capture_output=True, timeout=300)
    nets = tmp_path / "nets.txt"
    nets.write_text("BARCODE_NI\nNI_5V\nNI_GND\n")
    code, out, stdout = run_judge(src, "--skip-drc", "--nets", str(nets),
                                  "--region", "0,100,70,240", json_out=tmp_path / "r.json")
    gates = {g["id"]: g for g in out["gates"]}
    assert gates["REGION"]["status"] == "FAIL"
    ev = " ".join(gates["REGION"]["evidence"])
    assert "BARCODE_NI" in ev and "outside the region" in ev
    assert "RWD_BTN" in ev and "net not listed" in ev
    statuses = {c["id"]: c["status"] for c in out["checks"]}
    # nets not listed are absent from a region copy: N/A, never PASS
    for rid in ("G-1", "G-3", "G-10", "N-1", "B-18", "K-23", "G-16", "X-6"):
        assert statuses[rid] == "N/A", (rid, statuses[rid])
    assert statuses["K-2"] == "PASS"   # BARCODE_NI is listed and routed, outside AGND
    assert code == 1
