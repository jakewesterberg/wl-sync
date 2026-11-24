"""hardware/gen/route, the routing engine (task R1a, 2026-11-24).

numpy only: no KiCad. These run in CI (.github/workflows/ci.yml installs `.[dev,hw]`).

The obstacle fixtures are hand-built in the schema `hardware/gen/route/stage.py` defines
(OBSTACLES_SCHEMA), which `board_io.extract()` writes from a real board (task R1b). The
clearances and widths every assertion compares against are read from `Rules`, which reads
`hardware/breakout/breakout.kicad_dru` and `breakout.kicad_pro`; none is written here,
except in `test_rules_match_dru`, whose job is to pin them to the files.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from hardware.gen.route import Grid, Rules, RouteResult, route_connection  # noqa: E402
from hardware.gen.route.stage import (Pad, assert_stayed_local, net_order, pads_of,  # noqa: E402
                                      prune_dangling)

REPO = Path(__file__).parents[2]
BREAKOUT = REPO / "hardware" / "breakout"


@pytest.fixture(scope="module")
def rules():
    return Rules.load(BREAKOUT)


# ---- fixtures in the obstacle schema ------------------------------------------------------
def rect(cx, cy, w, h):
    return [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
            [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]


def pad(ref, num, net, cx, cy, w=1.0, h=1.0, layers=("F.Cu",), smd=True):
    return {"ref": ref, "num": num, "net": net, "layers": list(layers), "smd": smd,
            "at": [cx, cy], "polygon": rect(cx, cy, w, h)}


def obstacles(pads=(), keepouts=(), holes=(), tracks=(), vias=()):
    """A 40 x 20 mm board, origin at its corner, with the given items."""
    return {"schema": "wl-sync-route-obstacles", "version": 1, "units": "mm",
            "board": "fixture", "placement_sha256": "0" * 64,
            "layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
            "outline": {"outer": rect(20, 10, 40, 20), "holes": []},
            "pads": list(pads), "holes": list(holes), "keepouts": list(keepouts),
            "tracks": list(tracks), "vias": list(vias)}


def grid_for(obs, rules):
    g = Grid((0.0, 0.0, 40.0, 20.0), rules=rules)
    g.add_obstacles(obs)
    return g


def path_length(path):
    total = 0.0
    for t in path.tracks():
        for (ax, ay), (bx, by) in zip(t["pts"], t["pts"][1:]):
            total += math.hypot(bx - ax, by - ay)
    return total


def seg_poly_distance(p, q, poly):
    """Least distance from segment p-q to a convex polygon's boundary or inside (0)."""
    def pt_seg(px, py, ax, ay, bx, by):
        vx, vy = bx - ax, by - ay
        ll = vx * vx + vy * vy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / ll))
        return math.hypot(px - ax - t * vx, py - ay - t * vy)
    best = math.inf
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        best = min(best, pt_seg(*p, *a, *b), pt_seg(*q, *a, *b),
                   pt_seg(*a, *p, *q), pt_seg(*b, *p, *q))
    return best


# ---- the tests ----------------------------------------------------------------------------
def test_rules_match_dru(rules):
    """Review focus 2: the engine's clearances are the rule files', read at run time."""
    text = (BREAKOUT / "breakout.kicad_dru").read_text()
    block = re.search(r'\(rule "N-13 panel-side nets, outer layers"(.*?)\(constraint',
                      text, re.S).group(1)
    in_file = re.findall(r"A\.NetName == '([^']+)'", block)
    assert len(set(in_file)) == 129
    assert rules.panel_nets == frozenset(in_file)
    first = in_file[0]
    assert rules.clearance(first, "DGND", "F.Cu") == 0.6
    assert rules.clearance(first, "DGND", "B.Cu") == 0.6
    assert rules.clearance(first, "DGND", "In1.Cu") == 0.2
    assert rules.clearance("DGND", first, "F.Cu") == 0.6          # either way round
    assert rules.clearance("DGND", "+3V3", "F.Cu") == 0.2
    # N-12, outer layers only
    assert rules.clearance("ISO_P15_RAW", "ISO_N15_FILT", "F.Cu") == 0.6
    assert rules.clearance("ISO_N15_RAW", "ISO_P15_FILT", "B.Cu") == 0.6
    assert rules.clearance("ISO_P15_RAW", "ISO_N15_RAW", "In2.Cu") == 0.2
    assert rules.width("P12_RAW") == 1.0
    assert rules.width("NI_5V") == 0.6
    assert rules.width("DGND") == 0.2
    assert rules.via == (0.6, 0.3)
    assert len(rules.sha256) == 64


def test_route_two_pads_straight(rules):
    a = pad("R1", "1", "SIG_A", 10.0, 10.0)
    b = pad("R2", "1", "SIG_A", 20.0, 10.0)
    g = grid_for(obstacles([a, b]), rules)
    (pa,), (pb,) = pads_of(g, "R1"), pads_of(g, "R2")
    path = route_connection(g, "SIG_A", [pa], [pb], ("F.Cu",))
    assert path is not None
    assert path.vias() == []
    assert path_length(path) <= 1.05 * 10.0
    assert {t["layer"] for t in path.tracks()} == {"F.Cu"}
    assert all(t["width"] == rules.width("SIG_A") for t in path.tracks())


def test_route_respects_clearance(rules):
    net = sorted(rules.panel_nets)[0]               # 0.6 mm on F.Cu (N-13)
    a = pad("R1", "1", net, 10.0, 10.0)
    b = pad("R2", "1", net, 30.0, 10.0)
    wall = pad("R3", "1", "OTHER", 20.0, 10.0, w=1.0, h=6.0)
    g = grid_for(obstacles([a, b, wall]), rules)
    (pa,), (pb,) = pads_of(g, "R1"), pads_of(g, "R2")
    path = route_connection(g, net, [pa], [pb], ("F.Cu",))
    assert path is not None
    need = rules.clearance(net, "OTHER", "F.Cu") + rules.width(net) / 2
    assert need == pytest.approx(0.7)
    poly = wall["polygon"]
    ys = [y for _, x, y in path.cells]
    assert max(ys) > 13.0 or min(ys) < 7.0, "the path did not detour round the pad"
    for _, x, y in path.cells:
        assert seg_poly_distance((x, y), (x, y), poly) >= need - 1e-9
    for t in path.tracks():
        for p, q in zip(t["pts"], t["pts"][1:]):
            assert seg_poly_distance(p, q, poly) >= need - 1e-9


def test_route_takes_via_when_blocked(rules):
    a = pad("R1", "1", "SIG_A", 10.0, 10.0)
    b = pad("J1", "1", "SIG_A", 30.0, 10.0, w=1.7, h=1.7,
            layers=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), smd=False)
    hole = {"at": [30.0, 10.0], "diameter": 1.0, "plated": True, "net": "SIG_A"}
    wall = {"name": "wall", "layers": ["F.Cu"], "forbids": ["tracks", "vias", "pours"],
            "polygon": rect(20.0, 10.0, 1.0, 20.0)}
    g = grid_for(obstacles([a, b], keepouts=[wall], holes=[hole]), rules)
    (pa,), (pb,) = pads_of(g, "R1"), pads_of(g, "J1")
    path = route_connection(g, "SIG_A", [pa], [pb], ("F.Cu", "B.Cu"))
    assert path is not None
    vias = path.vias()
    assert len(vias) == 1
    assert (vias[0]["size"], vias[0]["drill"]) == rules.via == (0.6, 0.3)
    assert vias[0]["at"][0] < 19.5                    # on the source's side of the wall
    layers = [t["layer"] for t in path.tracks()]
    assert layers[0] == "F.Cu" and layers[-1] == "B.Cu"
    # and with no wall, no via
    g2 = grid_for(obstacles([a, b], holes=[hole]), rules)
    (pa,), (pb,) = pads_of(g2, "R1"), pads_of(g2, "J1")
    assert route_connection(g2, "SIG_A", [pa], [pb], ("F.Cu", "B.Cu")).vias() == []


def test_n16_via_web(rules):
    """N-16: a via's copper at least 0.2 mm from any SMD pad's copper, its own net's too.
    The source is an SMD pad on F.Cu and the goal one on B.Cu, so a via is unavoidable; the
    shortest way puts it in, or against, a pad, and an own-net pad sits beside each."""
    pads = [pad("R1", "1", "SIG_A", 10.0, 10.0, w=1.2, h=1.2),
            pad("R2", "1", "SIG_A", 30.0, 10.0, w=1.2, h=1.2, layers=("B.Cu",)),
            pad("R3", "1", "SIG_A", 11.5, 10.0, w=0.8, h=3.0),          # own net, F.Cu
            pad("R4", "1", "SIG_A", 28.5, 10.0, w=0.8, h=3.0, layers=("B.Cu",))]
    g = grid_for(obstacles(pads), rules)
    (pa,), (pb,) = pads_of(g, "R1"), pads_of(g, "R2")
    path = route_connection(g, "SIG_A", [pa], [pb], ("F.Cu", "B.Cu"))
    assert path is not None
    vias = path.vias()
    assert len(vias) >= 1
    web = rules.via_pad_web(smd=True, ref="R1", num="1")
    assert web == 0.2
    for v in vias:
        r = v["size"] / 2
        for p in pads:
            d = seg_poly_distance(tuple(v["at"]), tuple(v["at"]), p["polygon"])
            inside = (min(q[0] for q in p["polygon"]) <= v["at"][0] <= max(q[0] for q in p["polygon"])
                      and min(q[1] for q in p["polygon"]) <= v["at"][1] <= max(q[1] for q in p["polygon"]))
            assert not inside, f"via {v['at']} in pad {p['ref']}"
            assert d - r >= web - 1e-9, f"via {v['at']} {d - r:.3f} mm from {p['ref']}"


def test_deterministic(rules):
    def run():
        pads = [pad("R1", "1", "SIG_A", 5.0, 5.0), pad("R2", "1", "SIG_A", 35.0, 15.0),
                pad("R3", "1", "SIG_B", 5.0, 15.0), pad("R4", "1", "SIG_B", 35.0, 5.0),
                pad("R5", "1", "OTHER", 20.0, 10.0, w=4.0, h=4.0)]
        g = grid_for(obstacles(pads), rules)
        res = RouteResult(stage=0, name="fixture", placement_sha256="0" * 64,
                          rules_sha256=rules.sha256)
        for net, (s, t) in (("SIG_A", ("R1", "R2")), ("SIG_B", ("R3", "R4"))):
            (ps,), (pt,) = pads_of(g, s), pads_of(g, t)
            p = route_connection(g, net, [ps], [pt], ("F.Cu", "B.Cu"))
            assert p is not None
            res.add_path(p)
            g.add_copper(res)
        return res.to_json()
    a, b = run(), run()
    assert a == b
    assert RouteResult.from_json(a).to_json() == a


def test_prune_dangling(rules):
    """A stub to a via whose other side was never used, joined from its middle: the via
    goes and the stub is cut back to the join; a plane net's via stays (it has its pour).
    A second net's stub ends in its via, and its join starts inside the via's copper, off
    the centre: the via goes, and a bridge keeps the two joined."""
    a = Pad.from_dict(pad("R1", "1", "SIG_A", 10.0, 10.0))
    b = Pad.from_dict(pad("R2", "1", "SIG_A", 11.0, 14.0))
    g = Pad.from_dict(pad("C1", "2", "GND_X", 20.0, 10.0))
    res = RouteResult(stage=0, name="prune", rules_sha256=rules.sha256)
    res.add_track("SIG_A", "F.Cu", 0.2, [[10.0, 10.0], [12.0, 10.0]])
    res.add_via("SIG_A", [12.0, 10.0], 0.6, 0.3)
    res.add_track("SIG_A", "F.Cu", 0.2, [[11.0, 10.0], [11.0, 14.0]])
    res.add_track("SIG_B", "F.Cu", 0.2, [[10.0, 20.0], [12.0, 20.0]])
    res.add_via("SIG_B", [12.0, 20.0], 0.6, 0.3)
    res.add_track("SIG_B", "F.Cu", 0.2, [[12.0, 20.2], [12.0, 24.0]])
    c = Pad.from_dict(pad("R3", "1", "SIG_B", 10.0, 20.0))
    d = Pad.from_dict(pad("R4", "1", "SIG_B", 12.0, 24.0))
    res.add_track("GND_X", "F.Cu", 0.6, [[20.0, 10.0], [21.0, 10.0]])
    res.add_via("GND_X", [21.0, 10.0], 0.6, 0.3)
    res.add_zone(net="GND_X", layer="In1.Cu", outline=rect(20, 10, 10, 10), name="gnd")
    out, gone = prune_dangling(res, [a, b, g, c, d])
    assert gone == {"vias": 2, "tracks": 0, "trimmed": 1, "bridged": 1}
    assert [v["net"] for v in out.vias] == ["GND_X"]
    assert {"net": "SIG_B", "layer": "F.Cu", "width": 0.2,
            "pts": [[12.0, 20.0], [12.0, 20.2]]} in out.tracks
    stub = [t for t in out.tracks if t["pts"][0][1] == t["pts"][1][1] == 10.0 and t["net"] == "SIG_A"]
    assert stub == [{"net": "SIG_A", "layer": "F.Cu", "width": 0.2,
                     "pts": [[10.0, 10.0], [11.0, 10.0]]}]
    again, gone2 = prune_dangling(out, [a, b, g, c, d])
    assert gone2 == {"vias": 0, "tracks": 0, "trimmed": 0, "bridged": 0}
    assert again.to_json() == out.to_json()


def test_order_and_measure(rules):
    """wl-shook's power-first, shortest-first order, and its route-then-measure check."""
    pads = [Pad.from_dict(pad("R1", "1", "SIG_A", 0.0, 0.0)), Pad.from_dict(pad("R2", "1", "SIG_A", 3.0, 0.0)),
            Pad.from_dict(pad("R3", "1", "SIG_B", 0.0, 5.0)), Pad.from_dict(pad("R4", "1", "SIG_B", 1.0, 5.0)),
            Pad.from_dict(pad("C1", "1", "PWR", 0.0, 9.0)), Pad.from_dict(pad("C2", "1", "PWR", 9.0, 9.0))]
    by = {}
    for p in pads:
        by.setdefault(p.net, []).append(p)
    assert net_order(by, power={"PWR"}) == ["PWR", "SIG_B", "SIG_A"]
    res = RouteResult()
    res.add_track("SIG_A", "F.Cu", 0.2, [[0.0, 0.0], [3.0, 0.0]])
    res.add_track("SIG_B", "F.Cu", 0.2, [[0.0, 5.0], [0.0, 15.0], [1.0, 15.0], [1.0, 5.0]])
    ext = assert_stayed_local(res, pads, ["SIG_A"], slack_mm=0.5)
    assert ext["SIG_A"] == (3.0, 3.0)
    with pytest.raises(AssertionError, match="SIG_B"):
        assert_stayed_local(res, pads, ["SIG_A", "SIG_B"], slack_mm=2.0)
