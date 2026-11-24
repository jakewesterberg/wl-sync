"""hardware/gen/route_breakout.py, the breakout's routing driver (task R1b, 2026-11-24).

The routed board is a build product. `gen_breakout_pcb.py --out build/route/placed` places
the board into the build directory, and the driver replays every committed route file
(`hardware/breakout/routes/stage-NN.json`) onto it, then writes
`hardware/breakout/breakout.kicad_pcb`. These tests hold the driver to the plan's Review
Focus items 1, 4 and 5: a route file recorded against another placement is refused, copper
on the committed board that no route file holds is refused, and two builds are
byte-identical.

Every test runs the driver as a person would, in a subprocess, on a copy of the hardware
tree (hardware/gen, hardware/lib and the breakout's sources) in a temporary directory, so
the committed board is never written. The driver needs KiCad 10's bundled Python (pcbnew)
and kicad-cli, so these tests are skipped where KiCad is not installed, which includes CI.
The one test that searches (`route N`) also needs numpy.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
KICAD_PY = Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
                "Versions/Current/bin/python3")

pytestmark = pytest.mark.skipif(
    not KICAD_PY.exists() or shutil.which("kicad-cli") is None,
    reason="needs KiCad 10's bundled Python and kicad-cli")

# What the tree copy needs: the generator and the driver, the local footprints, and the
# breakout's sources (the netlist, the contract, the committed board and rule files).
BREAKOUT_FILES = ("breakout.kicad_pcb", "breakout.kicad_pro", "breakout.kicad_dru",
                  "breakout.net", "netlist-contract.json", "fp-lib-table", "sym-lib-table")

# A two-pad net in open space on F.Cu (R195 pad 2 at (118.0, 87.325), D43 pad 2 at
# (120.875, 88.5)), for the hand-written route files.
NET = "BARCODE_HB_LED"
TRACK = {"net": NET, "layer": "F.Cu", "width": 0.25,
         "pts": [[118.0, 87.325], [118.0, 88.5], [120.875, 88.5]]}


def make_tree(dst: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "hardware" / "gen", dst / "hardware" / "gen", ignore=ignore)
    shutil.copytree(REPO / "hardware" / "lib", dst / "hardware" / "lib", ignore=ignore)
    (dst / "hardware" / "breakout").mkdir(parents=True)
    for f in BREAKOUT_FILES:
        shutil.copy2(REPO / "hardware" / "breakout" / f, dst / "hardware" / "breakout" / f)
    if (REPO / "hardware" / "breakout" / "routes").exists():
        shutil.copytree(REPO / "hardware" / "breakout" / "routes",
                        dst / "hardware" / "breakout" / "routes")
    return dst


def drive(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(tree / "hardware" / "gen" / "route_breakout.py"),
                           *args], cwd=tree, capture_output=True, text=True, timeout=1800)


def kicad(code: str, *args: str) -> str:
    """Run `code` in KiCad's Python with `args` as sys.argv[1:]; its stdout."""
    p = subprocess.run([str(KICAD_PY), "-c", code, *args], capture_output=True, text=True,
                       timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    return p.stdout


BOARD_PCB = Path("hardware/breakout/breakout.kicad_pcb")
PLACED_PCB = Path("build/route/placed/breakout.kicad_pcb")

FOOTPRINTS = r'''
import json, sys, pcbnew
mm = pcbnew.ToMM
out = []
b = pcbnew.LoadBoard(sys.argv[1])
for fp in b.GetFootprints():
    pads = sorted((p.GetNumber(), p.GetNetname(), round(mm(p.GetPosition().x), 4),
                   round(mm(p.GetPosition().y), 4)) for p in fp.Pads())
    out.append([fp.GetReference(), fp.GetFPIDAsString(), round(mm(fp.GetPosition().x), 4),
                round(mm(fp.GetPosition().y), 4), round(fp.GetOrientationDegrees(), 4),
                b.GetLayerName(fp.GetLayer()), pads])
copper = [t.GetClass() for t in b.GetTracks()] + [z.GetZoneName() for z in b.Zones()
                                                  if not z.GetIsRuleArea()]
print(json.dumps({"footprints": sorted(out), "copper": copper}))
'''

TRACKS = r'''
import json, sys, pcbnew
mm = pcbnew.ToMM
b = pcbnew.LoadBoard(sys.argv[1])
out = []
for t in b.GetTracks():
    if t.GetClass() == "PCB_TRACK":
        out.append({"net": t.GetNetname(), "layer": b.GetLayerName(t.GetLayer()),
                    "width": round(mm(t.GetWidth()), 4),
                    "pts": [[round(mm(t.GetStart().x), 4), round(mm(t.GetStart().y), 4)],
                            [round(mm(t.GetEnd().x), 4), round(mm(t.GetEnd().y), 4)]]})
print(json.dumps(out))
'''

VIAS = r'''
import json, sys, pcbnew
mm = pcbnew.ToMM
b = pcbnew.LoadBoard(sys.argv[1])
print(json.dumps([[round(mm(t.GetPosition().x), 4), round(mm(t.GetPosition().y), 4)]
                  for t in b.GetTracks() if t.GetClass() == "PCB_VIA"]))
'''

ADD_TRACK = r'''
import sys, pcbnew
F = pcbnew.FromMM
b = pcbnew.LoadBoard(sys.argv[1])
t = pcbnew.PCB_TRACK(b)
t.SetStart(pcbnew.VECTOR2I(F(118.0), F(87.325))); t.SetEnd(pcbnew.VECTOR2I(F(118.0), F(88.5)))
t.SetWidth(F(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNet(b.FindNet("BARCODE_HB_LED"))
b.Add(t)
b.Save(sys.argv[1])
'''


def connectivity(out: str) -> tuple[int, int]:
    m = re.search(r"connectivity: routed nets (\d+) unconnected .*; whole board (\d+) "
                  r"unconnected", out)
    assert m, out
    return int(m.group(1)), int(m.group(2))


def hashes(tree: Path) -> tuple[str, str]:
    """(placement_sha256, rules_sha256) of the tree's last build, as a route file needs."""
    sys.path.insert(0, str(REPO))
    from hardware.gen.route.rules import Rules
    placed = tree / PLACED_PCB
    return (hashlib.sha256(placed.read_bytes()).hexdigest(),
            Rules.load(placed.parent).sha256)


def write_stage(tree: Path, n: int, **copper) -> Path:
    place, rules = hashes(tree)
    d = {"stage": n, "name": "test", "placement_sha256": place, "rules_sha256": rules,
         "nets": sorted({c["net"] for k in ("tracks", "vias", "zones")
                         for c in copper.get(k, [])}),
         "tracks": copper.get("tracks", []), "vias": copper.get("vias", []),
         "zones": copper.get("zones", []), "fixed": []}
    p = tree / "hardware" / "breakout" / "routes" / f"stage-{n:02d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    return p


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """A tree copy built once with no stages: (tree, the driver's result)."""
    tree = make_tree(tmp_path_factory.mktemp("route") / "wt")
    p = drive(tree, "build", "--through", "0")
    return tree, p


@pytest.fixture
def tree(base, tmp_path):
    """A fresh copy of the base tree, its build directory included."""
    src, _ = base
    dst = tmp_path / "wt"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def test_build_with_no_stages_is_placement(base):
    tree, p = base
    assert p.returncode == 0, p.stdout + p.stderr
    assert connectivity(p.stdout) == (0, 1791)
    built = json.loads(kicad(FOOTPRINTS, str(tree / BOARD_PCB)))
    placed = json.loads(kicad(FOOTPRINTS, str(tree / PLACED_PCB)))
    assert len(built["footprints"]) == 577
    assert built["footprints"] == placed["footprints"]
    assert built["copper"] == [] and placed["copper"] == []
    assert (tree / "hardware" / "breakout" / "routes" / "routed.nets").read_text() == ""


def test_committed_board_is_the_build(base):
    """The committed board is what the driver builds from the committed sources and route
    files: a generator or route-file change that was not rebuilt shows here."""
    tree, p = base
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tree / BOARD_PCB).read_bytes() == (REPO / BOARD_PCB).read_bytes()
    for f in ("routed.nets", "built.json"):
        committed = REPO / "hardware" / "breakout" / "routes" / f
        assert committed.read_text() == (tree / "hardware/breakout/routes" / f).read_text()


def test_rebuild_is_byte_identical(tree):
    # a track, a via and a filled zone, so that the zone fill's order is under test too
    write_stage(tree, 1, tracks=[TRACK],
                vias=[{"net": NET, "at": [120.875, 90.5], "size": 0.6, "drill": 0.3}],
                zones=[{"net": "DGND", "layer": "In1.Cu", "name": "test_DGND",
                        "outline": [[100.0, 20.0], [140.0, 20.0], [140.0, 40.0], [100.0, 40.0]],
                        "priority": 0, "connect": "thermal", "min_width": 0.25}])
    p1 = drive(tree, "build")
    assert p1.returncode == 0, p1.stdout + p1.stderr
    first = (tree / BOARD_PCB).read_bytes()
    assert b"test_DGND" in first and b"(filled_polygon" in first
    p2 = drive(tree, "build")
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert (tree / BOARD_PCB).read_bytes() == first


def test_replay_refuses_stale_placement(tree):
    stage = write_stage(tree, 1, tracks=[TRACK])
    gen = tree / "hardware" / "gen" / "gen_breakout_pcb.py"
    text = gen.read_text()
    nudge = 'ANCHORS["J56"] = (165.0, 55.0, 0)'
    assert nudge in text
    gen.write_text(text.replace(nudge, 'ANCHORS["J56"] = (165.0, 55.05, 0)'))
    before = (tree / BOARD_PCB).read_bytes()
    p = drive(tree, "build")
    assert p.returncode == 1, p.stdout + p.stderr
    assert stage.name in p.stdout + p.stderr
    assert "placement" in (p.stdout + p.stderr).lower()
    assert (tree / BOARD_PCB).read_bytes() == before


def test_replay_refuses_stale_rules(tree):
    """A board-level rule in the .kicad_pro (copper to edge) is a rule the engine reads, so
    a route file recorded under the old value is refused (Review Focus 2; fix round 1, C-1).
    The generator merges the committed .kicad_pro, so the edit reaches the build."""
    stage = write_stage(tree, 1, tracks=[TRACK])
    pro = tree / "hardware" / "breakout" / "breakout.kicad_pro"
    d = json.loads(pro.read_text())
    d["board"]["design_settings"]["rules"]["min_copper_edge_clearance"] = 0.6
    pro.write_text(json.dumps(d, indent=2) + "\n")
    p = drive(tree, "build")
    assert p.returncode == 1, p.stdout + p.stderr
    assert stage.name in p.stdout and "stale rules" in p.stdout


def test_a_comment_in_the_rules_is_not_stale(tree):
    """A comment in breakout.kicad_dru changes no rule: the route file still replays."""
    write_stage(tree, 1, tracks=[TRACK])
    gen = tree / "hardware" / "gen" / "gen_breakout_pcb.py"
    line = '"# N-8: 0.2 mm is the floor for every net.",'
    text = gen.read_text()
    assert line in text
    gen.write_text(text.replace(line, '"# N-8: 0.2 mm is the floor for every net (sic).",'))
    p = drive(tree, "build")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "(sic)" in (tree / "hardware" / "breakout" / "breakout.kicad_dru").read_text()


def test_refuses_unreplayed_copper(tree):
    kicad(ADD_TRACK, str(tree / BOARD_PCB))
    before = (tree / BOARD_PCB).read_bytes()
    nets = (tree / "hardware" / "breakout" / "routes" / "routed.nets").read_bytes()
    p = drive(tree, "build")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "REFUSED: hand edits" in p.stdout and "tracks (0 -> 1)" in p.stdout
    assert (tree / BOARD_PCB).read_bytes() == before
    assert (tree / "hardware" / "breakout" / "routes" / "routed.nets").read_bytes() == nets


EDITS = {
    # a filled rectangle drawn on B.Cu (fix round 1, I-2: was discarded unseen)
    "rectangle": r'''
s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_RECT)
s.SetStart(pcbnew.VECTOR2I(F(200), F(100))); s.SetEnd(pcbnew.VECTOR2I(F(205), F(104)))
s.SetFilled(True); s.SetLayer(pcbnew.B_Cu); s.SetWidth(F(0.2)); b.Add(s)
''',
    # copper text on F.Cu
    "text": r'''
t = pcbnew.PCB_TEXT(b); t.SetText("HAND"); t.SetPosition(pcbnew.VECTOR2I(F(200), F(100)))
t.SetLayer(pcbnew.F_Cu); b.Add(t)
''',
    # an arc track (the driver cannot replay one; was a traceback, M-8)
    "arc": r'''
a = pcbnew.PCB_ARC(b); a.SetStart(pcbnew.VECTOR2I(F(200), F(100)))
a.SetMid(pcbnew.VECTOR2I(F(201), F(101))); a.SetEnd(pcbnew.VECTOR2I(F(202), F(100)))
a.SetWidth(F(0.25)); a.SetLayer(pcbnew.F_Cu); a.SetNet(b.FindNet("DGND")); b.Add(a)
''',
    # a footprint moved by hand (fix round 1, M-1)
    "moved": r'''
fp = b.FindFootprintByReference("R195")
fp.SetPosition(pcbnew.VECTOR2I(fp.GetPosition().x + F(0.5), fp.GetPosition().y))
''',
}
WANT = {"rectangle": ("copper drawings (0 -> 1)", "a gr_rect on B.Cu"),
        "text": ("copper drawings (0 -> 1)", "a gr_text on F.Cu"),
        "arc": ("tracks (0 -> 1)", "an arc track"),
        "moved": ("footprints (577 -> 577)", "")}


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_refuses_every_hand_edit(tree, edit):
    code = ("import sys, pcbnew\nF = pcbnew.FromMM\nb = pcbnew.LoadBoard(sys.argv[1])\n"
            + EDITS[edit] + "\nb.Save(sys.argv[1])\n")
    kicad(code, str(tree / BOARD_PCB))
    before = (tree / BOARD_PCB).read_bytes()
    p = drive(tree, "build")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "REFUSED: hand edits" in p.stdout and "Traceback" not in p.stdout + p.stderr
    for w in WANT[edit]:
        assert w in p.stdout, p.stdout
    assert (tree / BOARD_PCB).read_bytes() == before


def test_route_file_roundtrip(tree):
    write_stage(tree, 1, tracks=[TRACK])
    p = drive(tree, "build")
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads(kicad(TRACKS, str(tree / BOARD_PCB)))
    want = [{"net": NET, "layer": "F.Cu", "width": 0.25, "pts": [a, b]}
            for a, b in zip(TRACK["pts"], TRACK["pts"][1:])]
    assert sorted(got, key=json.dumps) == sorted(want, key=json.dumps)
    assert (tree / "hardware/breakout/routes/routed.nets").read_text() == NET + "\n"
    routed, _board = connectivity(p.stdout)
    assert routed == 0


PROBE_STAGE = '''"""A one-net stage for test_route_breakout.py: route BARCODE_HB_LED on F.Cu."""
STAGE = 1
NAME = "probe"


def nets(board_nets):
    return [n for n in board_nets if n == "BARCODE_HB_LED"]


def zones():
    return ZONES


def layers(net):
    return LAYERS


def order(nets):
    return sorted(nets)


def fixed():
    return FIXED


ZONES = []
LAYERS = ("F.Cu",)
FIXED = []
'''
FIXED_VIA = {"via": {"net": NET, "at": [116.5, 90.0], "size": 0.6, "drill": 0.3}}
FIXED_TRACK = {"track": {"net": NET, "layer": "F.Cu", "width": 0.25,
                         "pts": [[118.0, 87.325], [118.0, 88.5]]}}


def probe(tree: Path, **kw) -> Path:
    """Write the probe stage module, with ZONES, LAYERS or FIXED replaced."""
    text = PROBE_STAGE
    for k, v in kw.items():
        old = {"zones": "ZONES = []", "layers": 'LAYERS = ("F.Cu",)', "fixed": "FIXED = []"}[k]
        text = text.replace(old, f"{k.upper()} = {v!r}")
    stages = tree / "hardware" / "gen" / "route" / "stages"
    stages.mkdir(exist_ok=True)
    f = stages / "s01_probe.py"
    f.write_text(text)
    return f


STAGE_01 = Path("hardware/breakout/routes/stage-01.json")


def test_reroute_with_changed_copper(tree):
    """Route, change the plan, route again: the normal loop (fix round 1, C-2). Then the
    board builds, through 0 and in full."""
    pytest.importorskip("numpy")
    probe(tree)
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    probe(tree, zones=[{"net": "DGND", "layer": "In1.Cu", "name": "probe_DGND",
                        "outline": [[100.0, 20.0], [140.0, 20.0], [140.0, 40.0],
                                    [100.0, 40.0]],
                        "priority": 0, "connect": "thermal", "min_width": 0.25}])
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads((tree / STAGE_01).read_text())["zones"][0]["name"] == "probe_DGND"
    assert b"probe_DGND" in (tree / BOARD_PCB).read_bytes()
    for args in (("build",), ("build", "--through", "0"), ("build",)):
        p = drive(tree, *args)
        assert p.returncode == 0, p.stdout + p.stderr


def test_failed_route_leaves_the_tree_buildable(tree):
    """A stage that cannot route (its pads are on F.Cu, it may use In1.Cu only) writes
    nothing, and the tree still builds."""
    pytest.importorskip("numpy")
    probe(tree, layers=("In1.Cu",))
    before = (tree / BOARD_PCB).read_bytes()
    p = drive(tree, "route", "1")
    assert p.returncode == 1 and "not routed" in p.stdout, p.stdout + p.stderr
    assert not (tree / STAGE_01).exists()
    assert (tree / BOARD_PCB).read_bytes() == before
    p = drive(tree, "build")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tree / BOARD_PCB).read_bytes() == before


def test_fixed_via_is_replayed_and_edits_go_through_the_module(tree):
    """A fixed() via reaches the route file and the board. A route file's `fixed` edited by
    hand is refused, pointing at the module; editing fixed() and routing carries the edit
    (fix round 1, I-3)."""
    pytest.importorskip("numpy")
    probe(tree, fixed=[FIXED_VIA])
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads((tree / STAGE_01).read_text())["fixed"] == [FIXED_VIA]
    assert len(json.loads(kicad(VIAS, str(tree / BOARD_PCB)))) == 1
    # a hand edit to the route file is refused
    d = json.loads((tree / STAGE_01).read_text())
    moved = {"via": dict(FIXED_VIA["via"], at=[116.5, 91.0])}
    d["fixed"] = [moved]
    (tree / STAGE_01).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    p = drive(tree, "build")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "stage-01.json's `fixed`" in p.stdout and "module's fixed()" in p.stdout, p.stdout
    # the same edit made in the module, then routed, goes through
    probe(tree, fixed=[moved])
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads((tree / STAGE_01).read_text())["fixed"] == [moved]
    assert json.loads(kicad(VIAS, str(tree / BOARD_PCB))) == [[116.5, 91.0]]
    assert drive(tree, "build").returncode == 0


def test_a_net_with_fixed_copper_routes_onto_it(tree):
    """The default loop starts from the stage's own fixed copper of a net instead of
    refusing the net (fix round 1, I-3)."""
    pytest.importorskip("numpy")
    probe(tree, fixed=[FIXED_TRACK])
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert connectivity(p.stdout) == (0, 1790)
    got = json.loads(kicad(TRACKS, str(tree / BOARD_PCB)))
    assert {"net": NET, "layer": "F.Cu", "width": 0.25,
            "pts": [[118.0, 87.325], [118.0, 88.5]]} in got


def test_route_writes_a_stage_and_builds(tree):
    pytest.importorskip("numpy")
    stages = tree / "hardware" / "gen" / "route" / "stages"
    stages.mkdir(exist_ok=True)
    (stages / "s01_probe.py").write_text(PROBE_STAGE)
    p = drive(tree, "route", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    f = tree / "hardware" / "breakout" / "routes" / "stage-01.json"
    d = json.loads(f.read_text())
    place, rules = hashes(tree)
    assert (d["stage"], d["name"], d["nets"]) == (1, "probe", [NET])
    assert (d["placement_sha256"], d["rules_sha256"]) == (place, rules)
    assert d["tracks"] and all(t["net"] == NET and t["layer"] == "F.Cu" for t in d["tracks"])
    assert connectivity(p.stdout) == (0, 1790)
    got = json.loads(kicad(TRACKS, str(tree / BOARD_PCB)))
    assert got and {t["net"] for t in got} == {NET}
    # and the stage file replays: a plain build gives the same board
    before = (tree / BOARD_PCB).read_bytes()
    p2 = drive(tree, "build")
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert (tree / BOARD_PCB).read_bytes() == before


EXTRACT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from hardware.gen.route import board_io
from hardware.gen.route.stage import validate_obstacles
board_io.extract(Path(sys.argv[2]), Path(sys.argv[3]))
d = json.load(open(sys.argv[3]))
validate_obstacles(d)
print(json.dumps({"pads": len(d["pads"]), "holes": len(d["holes"]),
                  "cutouts": len(d["outline"]["holes"]), "design": d["design"],
                  "keepouts": sorted({k["name"] for k in d["keepouts"]}),
                  "ties": sorted(p["ref"] for p in d["pads"] if p["num"] == "~shape"),
                  "sha": d["placement_sha256"],
                  "mh": sum(1 for h in d["holes"] if not h["plated"] and h["diameter"] == 3.2)}))
'''


def test_extract_writes_the_obstacle_schema(base, tmp_path):
    tree, p = base
    assert p.returncode == 0, p.stdout + p.stderr
    placed = tree / PLACED_PCB
    d = json.loads(kicad(EXTRACT, str(REPO), str(placed), str(tmp_path / "obs.json")))
    assert d["sha"] == hashlib.sha256(placed.read_bytes()).hexdigest()
    assert d["design"] == {"min_copper_edge_clearance": 0.5, "min_hole_clearance": 0.25,
                           "min_hole_to_hole": 0.25}
    assert d["cutouts"] == 2                                # the two slots
    assert len(d["keepouts"]) == 17 and "mh_H3" in d["keepouts"]
    assert d["mh"] == 8                                     # H3 to H10 (M-16)
    assert d["ties"] == ["NT1", "NT2", "NT3", "NT4"]        # the net ties' copper bars
    assert d["pads"] > 2500
