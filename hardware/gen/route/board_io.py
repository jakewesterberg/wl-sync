"""The routing engine's only contact with pcbnew (task R1b, 2026-11-24). KiCad's Python only.

    extract(board, out_json)          a placed board -> the obstacle file (stage.py's schema)
    apply(placed, route_files, out)   a placed board + route files -> the routed board, every
                                      zone filled, saved
    copper(board)                     a board's tracks, vias and pours, normalised as the
                                      route files hold them (the driver's "unreplayed
                                      copper" check)
    connectivity(board, nets)         KiCad's unrouted-connection count, over `nets` and over
                                      the whole board, after the zones are filled

Promoted from R1a's scratch extractor and applier (R1c kept copies in
hardware/breakout/bakeoff/engine/), and generalised:

- a footprint's copper that is not a pad (the net ties' bars, NT1 to NT4) is an obstacle
  too. The schema has no item for it, so it is written as a pad numbered "~shape" (a real
  pad number may be empty: J13's shell pads), with no net and `smd` false: clearance to
  every net, its own tie's included, as KiCad's DRC treats it;
- an arc track, a board drawing on a copper layer or a footprint text on one is refused,
  not dropped, since the engine has no model for it;
- `apply` refuses a route file that names a net or a layer the board does not have;
- `apply` seeds KiCad's uuid generator and saves without touching the project files, so the
  same inputs give the same bytes (the routing plan's Review Focus 5).

Command line (the driver, hardware/gen/route_breakout.py, calls these):

    <kicad python> -m hardware.gen.route.board_io extract BOARD OUT.json
    <kicad python> -m hardware.gen.route.board_io apply PLACED OUT NETS_FILE ROUTE.json...
    <kicad python> -m hardware.gen.route.board_io copper BOARD OUT.json

`apply` prints one JSON line, {"routed": n, "board": n, "nets": n}: the unrouted-connection
count over the nets in NETS_FILE (one per line) and over the whole board.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pcbnew

from .stage import (DESIGN_KEYS, LAYERS, OBSTACLES_SCHEMA, OBSTACLES_VERSION, RouteResult,
                    norm_track, norm_via, norm_zone, validate_obstacles)

LID = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu,
       "B.Cu": pcbnew.B_Cu}
assert tuple(LID) == LAYERS
CONNECT = {"thermal": pcbnew.ZONE_CONNECTION_THERMAL, "solid": pcbnew.ZONE_CONNECTION_FULL,
           "none": pcbnew.ZONE_CONNECTION_NONE,
           "tht_thermal": pcbnew.ZONE_CONNECTION_THT_THERMAL}
CONNECT_NAME = {v: k for k, v in CONNECT.items()}
MAXERR = pcbnew.FromMM(0.002)      # polygon approximation error for arcs and round pads
KIID_SEED = 20261124               # KiCad's uuid generator, for the items `apply` adds
SHAPE_NUM = "~shape"               # the pad number given to a footprint's non-pad copper
mm = pcbnew.ToMM
nm = pcbnew.FromMM


class BoardError(ValueError):
    pass


def _r(v: float) -> float:
    return round(float(v), 6)


def _chain(c) -> list:
    return [[_r(mm(c.CPoint(k).x)), _r(mm(c.CPoint(k).y))] for k in range(c.PointCount())]


def _one_polygon(poly, what: str) -> list:
    poly.Simplify()
    if poly.OutlineCount() != 1 or poly.HoleCount(0):
        raise BoardError(f"{what}: copper is {poly.OutlineCount()} outlines, not one simple "
                         f"polygon; the obstacle schema holds one per pad")
    return _chain(poly.Outline(0))


def _load(path: Path):
    b = pcbnew.LoadBoard(str(path))
    if b is None:
        raise BoardError(f"pcbnew could not load {path}")
    return b


# ---- extract --------------------------------------------------------------------------------
def extract(board: Path, out_json: Path) -> None:
    """Write `board`'s obstacles to `out_json` in stage.py's OBSTACLES_SCHEMA: the outline
    and its cut-outs (the slots), every pad's copper (its polygon on its first copper layer,
    KiCad's ERROR_OUTSIDE), every drill, the keep-out rule areas, the board's own clearance
    settings, and any tracks and vias already on it. `placement_sha256` is the sha256 of the
    file read."""
    board, out_json = Path(board), Path(out_json)
    b = _load(board)
    ds = b.GetDesignSettings()
    obs = {"schema": OBSTACLES_SCHEMA, "version": OBSTACLES_VERSION, "units": "mm",
           "board": board.name,
           "placement_sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
           "layers": list(LAYERS),
           "design": dict(zip(DESIGN_KEYS, (_r(mm(ds.m_CopperEdgeClearance)),
                                            _r(mm(ds.m_HoleClearance)),
                                            _r(mm(ds.m_HoleToHoleMin))))),
           "pads": [], "holes": [], "keepouts": [], "tracks": [], "vias": []}
    ol = pcbnew.SHAPE_POLY_SET()
    if not b.GetBoardPolygonOutlines(ol, False) or ol.OutlineCount() != 1:
        raise BoardError(f"{board}: the board outline is not one closed polygon")
    obs["outline"] = {"outer": _chain(ol.Outline(0)),
                      "holes": [_chain(ol.Hole(0, h)) for h in range(ol.HoleCount(0))]}
    for d in b.GetDrawings():
        if d.GetLayer() in LID.values():
            raise BoardError(f"a board drawing ({d.GetClass()}) on "
                             f"{b.GetLayerName(d.GetLayer())}: the engine has no model for it")
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            attr = p.GetAttribute()
            pos = p.GetPosition()
            at = [_r(mm(pos.x)), _r(mm(pos.y))]
            dr = p.GetDrillSize()
            if attr in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH) and dr.x > 0:
                # an oval drill as its circumscribed circle (stage.py)
                obs["holes"].append({"at": at, "diameter": _r(mm(max(dr.x, dr.y))),
                                     "plated": attr == pcbnew.PAD_ATTRIB_PTH,
                                     "net": p.GetNetname() if attr == pcbnew.PAD_ATTRIB_PTH
                                     else ""})
            # the layers it has copper on: a THT pad's inner-layer copper only where it
            # flashes (R1a report, concern 5)
            lays = [n for n, lid in LID.items() if p.IsOnLayer(lid) and p.FlashLayer(lid)]
            if attr == pcbnew.PAD_ATTRIB_NPTH or not lays:
                continue
            poly = pcbnew.SHAPE_POLY_SET()
            p.TransformShapeToPolygon(poly, LID[lays[0]], 0, MAXERR, pcbnew.ERROR_OUTSIDE)
            obs["pads"].append({
                "ref": ref, "num": p.GetNumber(), "net": p.GetNetname(), "layers": lays,
                "smd": attr in (pcbnew.PAD_ATTRIB_SMD, pcbnew.PAD_ATTRIB_CONN), "at": at,
                "polygon": _one_polygon(poly, f"{ref} pad {p.GetNumber()}")})
        for g in fp.GraphicalItems():
            if g.GetLayer() not in LID.values():
                continue
            if g.GetClass() != "PCB_SHAPE":
                raise BoardError(f"{ref}: a {g.GetClass()} on {b.GetLayerName(g.GetLayer())}: "
                                 f"the engine has no model for it")
            # copper that is not a pad, as a net tie's bar: a pad numbered SHAPE_NUM, no net
            poly = pcbnew.SHAPE_POLY_SET()
            g.TransformShapeToPolygon(poly, g.GetLayer(), 0, MAXERR, pcbnew.ERROR_OUTSIDE)
            pos = g.GetPosition()
            obs["pads"].append({
                "ref": ref, "num": SHAPE_NUM, "net": "", "layers": [b.GetLayerName(g.GetLayer())],
                "smd": False, "at": [_r(mm(pos.x)), _r(mm(pos.y))],
                "polygon": _one_polygon(poly, f"{ref} copper shape")})
    for z in b.Zones():
        if not z.GetIsRuleArea():
            continue
        forbids = [k for k, on in (("tracks", z.GetDoNotAllowTracks()),
                                   ("vias", z.GetDoNotAllowVias()),
                                   ("pours", z.GetDoNotAllowZoneFills())) if on]
        so = z.Outline()
        for i in range(so.OutlineCount()):
            obs["keepouts"].append({"name": z.GetZoneName(), "forbids": forbids,
                                    "layers": [n for n, lid in LID.items()
                                               if z.GetLayerSet().Contains(lid)],
                                    "polygon": _chain(so.Outline(i))})
    cu = copper_of(b)
    obs["tracks"], obs["vias"] = cu["tracks"], cu["vias"]
    validate_obstacles(obs)
    out_json.write_text(json.dumps(obs, indent=0) + "\n")


# ---- copper -------------------------------------------------------------------------------
def copper_of(b) -> dict:
    """A loaded board's tracks (one segment each), vias and pours (not rule areas), in the
    route file's shapes and rounding (stage.py's norm_track, norm_via, norm_zone)."""
    tracks, vias, zones = [], [], []
    for t in b.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            p = t.GetPosition()
            vias.append(norm_via({"net": t.GetNetname(), "at": [mm(p.x), mm(p.y)],
                                  "size": mm(t.GetWidth(pcbnew.F_Cu)),
                                  "drill": mm(t.GetDrillValue())}))
        elif cls == "PCB_TRACK":
            tracks.append(norm_track({"net": t.GetNetname(), "layer": b.GetLayerName(t.GetLayer()),
                                      "width": mm(t.GetWidth()),
                                      "pts": [[mm(t.GetStart().x), mm(t.GetStart().y)],
                                              [mm(t.GetEnd().x), mm(t.GetEnd().y)]]}))
        else:
            raise BoardError(f"a {cls} on the board: the engine has no model for it")
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        so = z.Outline()
        zones.append(norm_zone({
            "net": z.GetNetname(), "layer": b.GetLayerName(z.GetLayer()),
            "outline": [[mm(so.CVertex(k).x), mm(so.CVertex(k).y)]
                        for k in range(so.TotalVertices())],
            "priority": z.GetAssignedPriority(),
            "connect": CONNECT_NAME.get(z.GetPadConnection(), str(z.GetPadConnection())),
            "min_width": mm(z.GetMinThickness()), "name": z.GetZoneName()}))
    return {"tracks": tracks, "vias": vias, "zones": zones}


def copper(board: Path) -> dict:
    return copper_of(_load(Path(board)))


# ---- apply --------------------------------------------------------------------------------
def _net(b, name: str, where: str):
    n = b.FindNet(name)
    if n is None or (name and n.GetNetCode() <= 0):
        raise BoardError(f"{where}: net {name!r} is not on the board")
    return n


def _layer(name: str, where: str) -> int:
    if name not in LID:
        raise BoardError(f"{where}: layer {name!r} is not a copper layer of this board")
    return LID[name]


def _add_track(b, t: dict, where: str) -> None:
    net, layer = _net(b, t["net"], where), _layer(t["layer"], where)
    for (ax, ay), (bx, by) in zip(t["pts"], t["pts"][1:]):
        s = pcbnew.PCB_TRACK(b)
        s.SetStart(pcbnew.VECTOR2I(nm(ax), nm(ay)))
        s.SetEnd(pcbnew.VECTOR2I(nm(bx), nm(by)))
        s.SetWidth(nm(t["width"]))
        s.SetLayer(layer)
        s.SetNet(net)
        b.Add(s)


def _add_via(b, v: dict, where: str) -> None:
    o = pcbnew.PCB_VIA(b)
    o.SetPosition(pcbnew.VECTOR2I(nm(v["at"][0]), nm(v["at"][1])))
    o.SetViaType(pcbnew.VIATYPE_THROUGH)
    o.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    o.SetWidth(nm(v["size"]))
    o.SetDrill(nm(v["drill"]))
    o.SetNet(_net(b, v["net"], where))
    b.Add(o)


def _add_zone(b, z: dict, where: str) -> None:
    zz = pcbnew.ZONE(b)
    zz.SetLayer(_layer(z["layer"], where))
    zz.SetNet(_net(b, z["net"], where))
    zz.SetZoneName(z["name"])
    o = zz.Outline()
    o.NewOutline()
    for x, y in z["outline"]:
        o.Append(nm(x), nm(y))
    if "clearance" in z:
        zz.SetLocalClearance(nm(z["clearance"]))
    zz.SetMinThickness(nm(z["min_width"]))
    if z["connect"] not in CONNECT:
        raise BoardError(f"{where}: zone {z['name']!r} connect {z['connect']!r}")
    zz.SetPadConnection(CONNECT[z["connect"]])
    if "thermal_gap" in z:
        zz.SetThermalReliefGap(nm(z["thermal_gap"]))
    if "spoke_width" in z:
        zz.SetThermalReliefSpokeWidth(nm(z["spoke_width"]))
    zz.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    zz.SetAssignedPriority(int(z["priority"]))
    b.Add(zz)


def apply_to(b, route_files: list[Path]) -> None:
    """Add every route file's copper to the loaded board `b`, in file order: each file's
    fixed pieces, tracks, vias, then zones; then fill every zone."""
    pcbnew.KIID.SeedGenerator(KIID_SEED)
    for f in route_files:
        f = Path(f)
        r = RouteResult.from_json(f.read_text())
        where = f.name
        for x in r.fixed:
            if "track" in x:
                _add_track(b, norm_track(x["track"]), where)
            if "via" in x:
                _add_via(b, norm_via(x["via"]), where)
        for t in r.tracks:
            _add_track(b, t, where)
        for v in r.vias:
            _add_via(b, v, where)
        for z in r.zones:
            _add_zone(b, z, where)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())


def save(b, out: Path) -> None:
    """Save the board alone: the project files (.kicad_pro, .kicad_prl) are the
    generator's and are not rewritten."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not pcbnew.SaveBoard(str(out), b, True):
        raise BoardError(f"pcbnew could not save {out}")


def apply(placed: Path, route_files: list[Path], out: Path) -> None:
    """The placed board with every route file's tracks, vias and zones, the zones filled,
    saved to `out`."""
    b = _load(Path(placed))
    apply_to(b, route_files)
    save(b, out)


def connectivity_of(b, nets) -> dict:
    """KiCad's unrouted-connection count (pad clusters less one, per net) over `nets`, and
    over the whole board (GetUnconnectedCount, as the judge reads it). Zones must already be
    filled."""
    b.BuildConnectivity()
    conn = b.GetConnectivity()
    nets = set(nets)
    pads = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() > 0 and p.GetNetname() in nets:
                pads.setdefault(p.GetNetname(), []).append(p)
    routed = 0
    for ps in pads.values():
        seen, clusters = set(), 0
        for p in ps:
            k = p.m_Uuid.AsString()
            if k in seen:
                continue
            clusters += 1
            seen.add(k)
            # the pad's whole cluster through tracks, vias and filled zones (GetConnectedPads
            # gives only the pads touching it directly)
            seen.update(q.m_Uuid.AsString() for q in conn.GetConnectedItems(p)
                        if q.GetClass() == "PAD")
        routed += clusters - 1
    return {"routed": routed, "board": conn.GetUnconnectedCount(False), "nets": len(nets)}


def connectivity(board: Path, nets) -> dict:
    return connectivity_of(_load(Path(board)), nets)


def main(argv: list[str]) -> int:
    cmd, args = argv[0], argv[1:]
    if cmd == "extract":
        extract(Path(args[0]), Path(args[1]))
    elif cmd == "apply":
        placed, out, nets_file, files = Path(args[0]), Path(args[1]), Path(args[2]), args[3:]
        b = _load(placed)
        apply_to(b, [Path(f) for f in files])
        save(b, out)
        # connectivity from the saved file, as anyone opening it would see it
        nets = nets_file.read_text().split()
        print(json.dumps(connectivity(out, nets)))
    elif cmd == "copper":
        Path(args[1]).write_text(json.dumps(copper(Path(args[0]))) + "\n")
    else:
        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
