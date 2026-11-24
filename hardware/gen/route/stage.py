"""The routing engine's two file schemas, and the results it builds (task R1a).

Pure Python: no numpy and no pcbnew. `board_io.py` (KiCad's Python, task R1b) imports this
to write the obstacle file and to read route files; the engine (numpy) imports it to read
the one and write the other.

THE OBSTACLE FILE (OBSTACLES_SCHEMA), written by `board_io.extract(board, out_json)` from a
placed board and read by `Grid.add_obstacles`. Coordinates are the board file's, in mm
(x right, y down). Polygons are lists of [x, y], not closed (the last vertex is not the
first repeated), in either winding.

    {"schema": "wl-sync-route-obstacles", "version": 1, "units": "mm",
     "board": "breakout.kicad_pcb", "placement_sha256": "<sha256 of the placed board file>",
     "layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
     "design": {"min_copper_edge_clearance": 0.5, "min_hole_clearance": 0.25,
                "min_hole_to_hole": 0.25},                        (optional: the board's own)
     "outline": {"outer": [[x, y], ...],                          the board edge
                 "holes": [[[x, y], ...], ...]},                  slots and other cut-outs
     "pads":  [{"ref": "U56", "num": "11", "net": "EVT_D0_NI",   net "" for none
                "layers": ["F.Cu"], "smd": true,               the copper layers it is on
                "at": [x, y],                                   its anchor
                "polygon": [[x, y], ...]}],                     its copper, grown (KiCad's
                                                                ERROR_OUTSIDE), on its first
                                                                copper layer
     "holes": [{"at": [x, y], "diameter": 3.2, "plated": false, "net": ""}],
                                                                every drill: a PTH pad's, an
                                                                NPTH pad's, a mounting hole
     "keepouts": [{"name": "ni_slot_keepout", "layers": ["F.Cu", ...],
                   "forbids": ["tracks", "vias", "pours"],       any of those three
                   "polygon": [[x, y], ...]}],                  rule areas
     "tracks": [{"net": "...", "layer": "F.Cu", "width": 0.2, "pts": [[x, y], [x, y]]}],
     "vias":   [{"net": "...", "at": [x, y], "size": 0.6, "drill": 0.3}]}
                                                                copper already on the board

An oval drill is given as its circumscribed circle's diameter (`diameter`), which is safe
for clearance. A pad whose copper differs by layer is given by its first copper layer's.

THE ROUTE FILE (RouteResult.to_json), one per stage (plan, "File structure"): sorted, four
decimal places.

    {"stage": 3, "name": "star-inlet", "placement_sha256": "...", "rules_sha256": "...",
     "nets": ["P12_RAW", ...],
     "tracks": [{"net": "P12_RAW", "layer": "F.Cu", "width": 1.0,
                 "pts": [[170.0, 81.5], [172.0, 81.5]]}],
     "vias":   [{"net": "P12_RAW", "at": [172.0, 81.5], "size": 0.6, "drill": 0.3}],
     "zones":  [{"net": "DGND", "layer": "In1.Cu", "outline": [[x, y], ...], "priority": 0,
                 "connect": "thermal", "min_width": 0.25, "name": "DGND_L2"}],
     "fixed":  []}

A zone may also carry "clearance", "thermal_gap" and "spoke_width" (mm); without them the
net class's clearance and KiCad's thermal defaults apply. `fixed` holds hand-specified
tracks and vias in the same shapes, which a stage replays verbatim before it searches.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

OBSTACLES_SCHEMA = "wl-sync-route-obstacles"
OBSTACLES_VERSION = 1
LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
FORBIDS = frozenset({"tracks", "vias", "pours"})


class SchemaError(ValueError):
    pass


def validate_obstacles(d: dict) -> None:
    """Refuse an obstacle file that is not this schema's, naming the first fault."""
    if d.get("schema") != OBSTACLES_SCHEMA or d.get("version") != OBSTACLES_VERSION:
        raise SchemaError(f"not a {OBSTACLES_SCHEMA} v{OBSTACLES_VERSION} file: "
                          f"{d.get('schema')!r} v{d.get('version')!r}")
    if d.get("units") != "mm":
        raise SchemaError(f"units must be mm, not {d.get('units')!r}")
    for key in ("outline", "pads", "holes", "keepouts", "tracks", "vias"):
        if key not in d:
            raise SchemaError(f"missing {key!r}")
    if len(d["outline"].get("outer") or []) < 3:
        raise SchemaError("outline.outer needs at least three vertices")
    for p in d["pads"]:
        for k in ("ref", "num", "net", "layers", "smd", "at", "polygon"):
            if k not in p:
                raise SchemaError(f"pad {p.get('ref')}.{p.get('num')}: missing {k!r}")
        if not set(p["layers"]) <= set(LAYERS):
            raise SchemaError(f"pad {p['ref']}.{p['num']}: layers {p['layers']}")
        if len(p["polygon"]) < 3:
            raise SchemaError(f"pad {p['ref']}.{p['num']}: polygon has under three vertices")
    for h in d["holes"]:
        for k in ("at", "diameter", "plated", "net"):
            if k not in h:
                raise SchemaError(f"hole at {h.get('at')}: missing {k!r}")
    for k in d["keepouts"]:
        if not set(k.get("forbids", ())) <= FORBIDS or not set(k.get("layers", ())) <= set(LAYERS):
            raise SchemaError(f"keepout {k.get('name')!r}: forbids/layers not understood")
    for t in d["tracks"]:
        if t.get("layer") not in LAYERS or len(t.get("pts", ())) < 2:
            raise SchemaError(f"track {t}: layer or points")


@dataclass(frozen=True)
class Pad:
    """A pad, from the obstacle file. Also a terminal for `route_connection`."""
    ref: str
    num: str
    net: str
    layers: tuple
    smd: bool
    at: tuple
    polygon: tuple

    @classmethod
    def from_dict(cls, d: dict) -> "Pad":
        return cls(d["ref"], str(d["num"]), d["net"], tuple(d["layers"]), bool(d["smd"]),
                   tuple(d["at"]), tuple(tuple(map(float, q)) for q in d["polygon"]))

    @property
    def key(self) -> str:
        return f"{self.ref}.{self.num}"

    @property
    def bbox(self):
        xs = [q[0] for q in self.polygon]
        ys = [q[1] for q in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def pads_of(grid, ref: str, num: str | None = None) -> list[Pad]:
    """The grid's pads of footprint `ref` (pad `num` only, if given), by pad number."""
    out = [p for p in grid.pads if p.ref == ref and (num is None or p.num == str(num))]
    return sorted(out, key=lambda p: (len(p.num), p.num))


# ---- route files -----------------------------------------------------------------------
def _r(v: float) -> float:
    v = round(float(v), 4)
    return 0.0 if v == 0 else v


def _pt(q) -> list:
    return [_r(q[0]), _r(q[1])]


def norm_track(t: dict) -> dict:
    return {"net": t["net"], "layer": t["layer"], "width": _r(t["width"]),
            "pts": [_pt(q) for q in t["pts"]]}


def norm_via(v: dict) -> dict:
    return {"net": v["net"], "at": _pt(v["at"]), "size": _r(v["size"]), "drill": _r(v["drill"])}


def norm_zone(z: dict) -> dict:
    out = {"net": z["net"], "layer": z["layer"], "outline": [_pt(q) for q in z["outline"]],
           "priority": int(z.get("priority", 0)), "connect": z.get("connect", "thermal"),
           "min_width": _r(z.get("min_width", 0.25)), "name": z.get("name", "")}
    for k in ("clearance", "thermal_gap", "spoke_width"):
        if k in z:
            out[k] = _r(z[k])
    return out


def _track_key(t):
    return (t["net"], t["layer"], t["width"], t["pts"])


@dataclass
class RouteResult:
    """A stage's copper: what the search drew, plus the planes and fixed pieces."""
    stage: int = 0
    name: str = ""
    placement_sha256: str = ""
    rules_sha256: str = ""
    nets: list = field(default_factory=list)
    tracks: list = field(default_factory=list)
    vias: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    fixed: list = field(default_factory=list)

    def _net(self, n):
        if n not in self.nets:
            self.nets.append(n)

    def add_track(self, net, layer, width, pts):
        self._net(net)
        self.tracks.append(norm_track({"net": net, "layer": layer, "width": width, "pts": pts}))

    def add_via(self, net, at, size, drill):
        self._net(net)
        v = norm_via({"net": net, "at": at, "size": size, "drill": drill})
        if v not in self.vias:
            self.vias.append(v)

    def add_zone(self, **z):
        self._net(z["net"])
        self.zones.append(norm_zone(z))

    def add_path(self, path) -> None:
        """Add a `search.Path`: its tracks and vias."""
        for t in path.tracks():
            self.add_track(t["net"], t["layer"], t["width"], t["pts"])
        for v in path.vias():
            self.add_via(v["net"], v["at"], v["size"], v["drill"])

    def as_dict(self) -> dict:
        tracks = sorted((norm_track(t) for t in self.tracks), key=_track_key)
        vias = sorted((norm_via(v) for v in self.vias), key=lambda v: (v["net"], v["at"]))
        zones = sorted((norm_zone(z) for z in self.zones),
                       key=lambda z: (z["net"], z["layer"], z["name"], z["outline"]))
        fixed = sorted(({"track": norm_track(f["track"])} if "track" in f else
                        {"via": norm_via(f["via"])} for f in self.fixed),
                       key=lambda f: json.dumps(f, sort_keys=True))
        return {"stage": int(self.stage), "name": self.name,
                "placement_sha256": self.placement_sha256, "rules_sha256": self.rules_sha256,
                "nets": sorted(set(self.nets)), "tracks": tracks, "vias": vias,
                "zones": zones, "fixed": fixed}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=1, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "RouteResult":
        d = json.loads(text)
        r = cls(stage=d["stage"], name=d["name"], placement_sha256=d["placement_sha256"],
                rules_sha256=d["rules_sha256"], nets=list(d["nets"]))
        r.tracks = [norm_track(t) for t in d["tracks"]]
        r.vias = [norm_via(v) for v in d["vias"]]
        r.zones = [norm_zone(z) for z in d["zones"]]
        r.fixed = list(d.get("fixed", []))
        return r


# ---- pruning ------------------------------------------------------------------------------
def _pt_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    ll = vx * vx + vy * vy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / ll))
    return math.hypot(px - ax - t * vx, py - ay - t * vy), t


def _in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def _poly_dist(x, y, poly):
    n = len(poly)
    return min(_pt_seg(x, y, *poly[i], *poly[(i + 1) % n])[0] for i in range(n))


def prune_dangling(result: "RouteResult", pads: list) -> tuple["RouteResult", dict]:
    """Remove copper that leads nowhere, as KiCad's connectivity sees it (an item's anchor,
    a track end or a via centre, inside another item of its net): a via joined on fewer
    than two layers (a via of a net with a zone in the result is kept: it reaches the
    pour), and a track end touching nothing, cut back to the last point where other copper
    of its net meets the track (or the track dropped). Repeated until nothing changes.
    A via that goes may still have joined two tracks on its one layer (a track can start
    inside its copper, off its centre), so each track end inside it is first bridged to its
    centre by a short track; no connection is lost.
    Fixed pieces are anchors and are never removed. Returns a new result, with every
    track as one segment, and counts of what went.

    Why: a search that starts from a net's copper may leave from the middle of an earlier
    piece, so a stub's tail, or a via whose other side was never used, is left behind
    (the trial router's prune(); DRC reports them as via_dangling or track_dangling)."""
    zoned = {(z["net"], z["layer"]) for z in result.zones}
    zoned_nets = {n for n, _ in zoned}
    segs = []                         # [net, layer, half-width, a, b, fixed]
    for t, fx in ([(t, False) for t in result.tracks] +
                  [(f["track"], True) for f in result.fixed if "track" in f]):
        for a, b in zip(t["pts"], t["pts"][1:]):
            segs.append([t["net"], t["layer"], t["width"] / 2, list(a), list(b), fx])
    vias = ([dict(v, fixed=False) for v in result.vias] +
            [dict(f["via"], fixed=True) for f in result.fixed if "via" in f])
    pad_by_net = {}
    for p in pads:
        pad_by_net.setdefault(p.net, []).append(p)
    gone = {"vias": 0, "tracks": 0, "trimmed": 0, "bridged": 0}

    def touches(net, layer, x, y, skip):
        """Does a track end at (x, y) on `layer` meet other copper of `net`: the end inside
        another track or a via, or its round end's copper reaching a pad?"""
        for k, (n, l, hw, a, b, _) in enumerate(segs):
            if k != skip and n == net and l == layer and _pt_seg(x, y, *a, *b)[0] <= hw + 1e-6:
                return True
        for v in vias:
            if v["net"] == net and math.hypot(x - v["at"][0], y - v["at"][1]) <= v["size"] / 2 + 1e-6:
                return True
        hw = segs[skip][2]
        return any(layer in p.layers and (_in_poly(x, y, p.polygon) or
                                          _poly_dist(x, y, p.polygon) <= hw + 1e-6)
                   for p in pad_by_net.get(net, ()))

    changed = True
    while changed:
        changed = False
        keep = []
        for v in vias:
            if v["net"] in zoned_nets or v["fixed"]:
                keep.append(v)
                continue
            x, y = v["at"]
            r = v["size"] / 2
            layers = set()
            for n, l, hw, a, b, _ in segs:
                if n == v["net"] and (_pt_seg(x, y, *a, *b)[0] <= hw + 1e-6 or
                                      math.hypot(a[0] - x, a[1] - y) <= r + 1e-6 or
                                      math.hypot(b[0] - x, b[1] - y) <= r + 1e-6):
                    layers.add(l)
            for p in pad_by_net.get(v["net"], ()):
                if _in_poly(x, y, p.polygon) or math.hypot(p.at[0] - x, p.at[1] - y) <= r:
                    layers.update(p.layers)
            if len(layers) >= 2:
                keep.append(v)
            else:
                # The via still joins what touches it on its one layer (a track may start
                # inside its copper, off its centre): bridge each such end to the centre
                # on that layer before the via goes, so no connection is lost.
                for n, l, hw, a, b, _ in list(segs):
                    if n != v["net"]:
                        continue
                    for q in (a, b):
                        dq = math.hypot(q[0] - x, q[1] - y)
                        if 1e-6 < dq <= r + 1e-6:
                            segs.append([n, l, hw, [x, y], list(q), False])
                            gone["bridged"] += 1
                gone["vias"] += 1
                changed = True
        vias = keep
        k = 0
        while k < len(segs):
            n, l, hw, a, b, fx = segs[k]
            ends = [touches(n, l, *a, k), touches(n, l, *b, k)]
            if fx or all(ends):
                k += 1
                continue
            changed = True
            if not any(ends):
                del segs[k]
                gone["tracks"] += 1
                continue
            o, e = (a, b) if ends[0] else (b, a)        # o connected, e dangling
            # how far along o -> e other copper of the net still meets this track
            best = 0.0
            for j, (n2, l2, hw2, a2, b2, _) in enumerate(segs):
                if j != k and n2 == n and l2 == l:
                    for q in (a2, b2):
                        d, t = _pt_seg(q[0], q[1], *o, *e)
                        if d <= hw + 1e-6:
                            best = max(best, t)
            for v in vias:
                if v["net"] == n:
                    d, t = _pt_seg(v["at"][0], v["at"][1], *o, *e)
                    if d <= hw + 1e-6:
                        best = max(best, t)
            if best <= 1e-9:
                del segs[k]
                gone["tracks"] += 1
                continue
            cut = [o[0] + best * (e[0] - o[0]), o[1] + best * (e[1] - o[1])]
            segs[k] = [n, l, hw, o, cut, False]
            gone["trimmed"] += 1
            k += 1
    out = RouteResult(stage=result.stage, name=result.name,
                      placement_sha256=result.placement_sha256,
                      rules_sha256=result.rules_sha256, nets=list(result.nets))
    for n, l, hw, a, b, fx in segs:
        if not fx and math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
            out.add_track(n, l, 2 * hw, [a, b])
    for v in vias:
        if not v["fixed"]:
            out.add_via(v["net"], v["at"], v["size"], v["drill"])
    out.zones = [dict(z) for z in result.zones]
    out.fixed = [dict(f) for f in result.fixed]
    return out, gone


# ---- order and measure (borrowed from wl-shook's router, hardware/gen/shook_router.py) -----
def _span(points) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def net_order(pads_by_net: dict, power=frozenset()) -> list[str]:
    """The order to route nets in, after wl-shook's net_order(): the nets in `power` first
    (widest copper, hardest to fit; longest span first), then every other net shortest
    first, since a short net that fails is usually a real obstruction and a long one that
    fails is usually just late. Ties go by name, so the order is repeatable."""
    def span(n):
        return _span([p.at for p in pads_by_net[n]])
    nets = sorted(pads_by_net)
    first = sorted((n for n in nets if n in power), key=lambda n: (-span(n), n))
    rest = sorted((n for n in nets if n not in power), key=lambda n: (span(n), n))
    return first + rest


def net_extent(result: "RouteResult", pads: list, net: str) -> tuple[float, float]:
    """(span of the net's pads, span of the pads and its copper together), as bounding-box
    diagonals in mm: wl-shook's switching_net_extent(), for any net."""
    pts = [p.at for p in pads if p.net == net]
    if not pts:
        raise ValueError(f"no pad of {net}")
    cu = list(pts)
    for t in result.tracks:
        if t["net"] == net:
            cu += [tuple(q) for q in t["pts"]]
    for v in result.vias:
        if v["net"] == net:
            cu.append(tuple(v["at"]))
    return _span(pts), _span(cu)


def assert_stayed_local(result: "RouteResult", pads: list, nets, slack_mm: float) -> dict:
    """Route, then measure (wl-shook's assert_switching_nets_stayed_local()): every net in
    `nets` has its copper within `slack_mm` of its pads' own span. DRC does not care where
    a net goes, so a stage that needs a net kept local says so here. Returns the extents;
    raises AssertionError naming every net that wandered."""
    ext = {n: net_extent(result, pads, n) for n in sorted(nets)}
    bad = [f"{n}: copper spans {c:.2f} mm from a {p:.2f} mm pad cluster "
           f"(allowed {p + slack_mm:.2f})" for n, (p, c) in ext.items() if c > p + slack_mm]
    assert not bad, "nets left their clusters: " + "; ".join(bad)
    return ext
