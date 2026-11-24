"""The routing grid: every obstacle as a distance field, thresholded per clearance case
(task R1a; numpy).

    g = Grid((x0, y0, x1, y1), pitch=0.05, rules=Rules.load(...))
    g.add_obstacles(json.load(open("obstacles.json")))     # stage.py's OBSTACLES_SCHEMA
    g.add_copper(result)                                   # a RouteResult's tracks and vias

The model is the NI trial router's (git show trial-ni-opus:hardware/breakout/
trial_route_ni.py, class Model), moved to numpy and made exact per pair of nets. The trial
kept one occupancy grid per clearance case, each cell holding the id of the one net whose
copper lay within that case's distance, so its cases had to be fixed in advance (it drew
every signal against N-13's 0.6 mm, panel net or not). Here every copper item, keep-out and
hole keeps its own signed distance field (mm, float32, negative inside the item) over its
bounding box grown by the largest distance any rule can ask, for the widest track any rule
asks for (`reach`; `masks` refuses a wider one, which would under-enforce). A query for one net, one width
and one window thresholds each field at the distance that pair of items needs, from
`Rules`: clearance(this net, that item, layer) + half the track's width, or + the via's
radius. So N-12, N-13, N-16 and the net-class clearances are each applied where they hold
and nowhere else, and a rule changed in the rule files changes the router.

A cell is a candidate track centre (or via centre); a track is drawn between centres of
neighbouring free cells. A small numeric margin (MARGIN, 0.01 mm) is added to every
threshold: it covers the dip of a straight 45-degree step past a convex corner (under
0.003 mm at this pitch) and the polygonisation of round pads. It is not a clearance.

What is held, and from what (`masks`):
- foreign copper (pads, tracks, vias of any other net, and of no net): the rules'
  clearance, per layer, for a track; the largest over the via's layers for a via;
- a via, also: hole to hole (`hole_to_hole`) from every drill, its own net's included
  (a drill is not a net: wl-shook's lessons §8); copper to hole (`hole_clearance`) from
  every foreign or unplated drill; and the rules' via-to-pad physical clearance (N-16's web)
  from every pad it applies to, its own net's included (`Rules.via_pad_web`, which knows the
  U3/U4 pad 9 exemption and which pads are SMD);
- a track, also: `hole_clearance` from every foreign or unplated drill;
- keep-outs (rule areas): no track copper on a layer that forbids tracks, no via where any
  layer forbids vias;
- the board edge and every slot: copper held `edge_clearance` from it, and nothing outside
  the board or inside a cut-out.
Zones are not obstacles: a pour is filled after routing and gives way to tracks.

Net ids are uint16 (the board has 747 nets); 0 is no net, foreign to everyone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .rules import LAYERS, Item, Rules
from .stage import Pad, validate_obstacles

LI = {name: i for i, name in enumerate(LAYERS)}
MARGIN = 0.01
NET_DTYPE = np.uint16
_REPO = Path(__file__).resolve().parents[3]


# ---- distance fields --------------------------------------------------------------------
def _seg_dist(X, Y, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    ll = vx * vx + vy * vy
    if ll == 0:
        return np.hypot(X - ax, Y - ay)
    t = np.clip(((X - ax) * vx + (Y - ay) * vy) / ll, 0.0, 1.0)
    return np.hypot(X - ax - t * vx, Y - ay - t * vy)


def poly_signed_distance(verts: np.ndarray, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Signed distance from grid points (X, Y) to a polygon: negative inside."""
    n = len(verts)
    ax, ay = verts[:, 0], verts[:, 1]
    bx, by = np.roll(ax, -1), np.roll(ay, -1)
    d = np.full(np.broadcast(X, Y).shape, np.inf)
    inside = np.zeros(d.shape, dtype=bool)
    chunk = max(1, int(4_000_000 // max(1, d.size)))
    for s in range(0, n, chunk):
        e = slice(s, min(n, s + chunk))
        AX, AY = ax[e, None, None], ay[e, None, None]
        BX, BY = bx[e, None, None], by[e, None, None]
        vx, vy = BX - AX, BY - AY
        ll = vx * vx + vy * vy
        ll = np.where(ll == 0, 1.0, ll)
        t = np.clip(((X - AX) * vx + (Y - AY) * vy) / ll, 0.0, 1.0)
        dd = np.hypot(X - AX - t * vx, Y - AY - t * vy)
        d = np.minimum(d, dd.min(axis=0))
        # crossing number: edges that straddle the point's y, crossed to its right
        cond = (AY > Y) != (BY > Y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = AX + (Y - AY) * (BX - AX) / (BY - AY)
        inside ^= np.logical_xor.reduce(cond & (X < xint), axis=0)
    return np.where(inside, -d, d)


def poly_inside(verts: np.ndarray, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """True where grid points (X, Y) lie inside a polygon (crossing number, edge by edge)."""
    inside = np.zeros(np.broadcast(X, Y).shape, dtype=bool)
    n = len(verts)
    for k in range(n):
        ax, ay = verts[k]
        bx, by = verts[(k + 1) % n]
        if ay == by:
            continue
        rows = (np.minimum(ay, by) < Y) & (Y <= np.maximum(ay, by))     # (ny, 1)
        if not rows.any():
            continue
        xint = ax + (Y - ay) * (bx - ax) / (by - ay)
        inside ^= rows & (X < xint)
    return inside


@dataclass(frozen=True)
class Shape:
    kind: str                 # 'poly', 'seg' or 'circle'
    data: tuple               # poly: vertices; seg: (ax, ay, bx, by, hw); circle: (cx, cy, r)

    @property
    def bbox(self):
        if self.kind == "poly":
            xs = [q[0] for q in self.data]
            ys = [q[1] for q in self.data]
            return min(xs), min(ys), max(xs), max(ys)
        if self.kind == "seg":
            ax, ay, bx, by, hw = self.data
            return min(ax, bx) - hw, min(ay, by) - hw, max(ax, bx) + hw, max(ay, by) + hw
        cx, cy, r = self.data
        return cx - r, cy - r, cx + r, cy + r

    def distance(self, X, Y):
        if self.kind == "poly":
            return poly_signed_distance(np.asarray(self.data, dtype=float), X, Y)
        if self.kind == "seg":
            ax, ay, bx, by, hw = self.data
            return _seg_dist(X, Y, ax, ay, bx, by) - hw
        cx, cy, r = self.data
        return np.hypot(X - cx, Y - cy) - r


@dataclass
class _Obj:
    kind: str                     # 'pad', 'track', 'via', 'hole', 'keepout', 'edge'
    net: int                      # uint16 id; 0 = no net
    netname: str
    layers: tuple                 # layer indices
    shape: Shape
    ctx: Item | None = None       # the item as the rules see it
    web: float = 0.0              # N-16: a via's copper this far from it, any net (pads)
    plated: bool = True           # holes
    forbids: frozenset = frozenset()
    name: str = ""
    rect: tuple = None            # (iy0, iy1, ix0, ix1) of its field, inclusive
    _field: np.ndarray = field(default=None, repr=False)


class Grid:
    """Obstacles over a window of the board, on a square grid of `pitch` mm."""

    def __init__(self, window: tuple[float, float, float, float], pitch: float = 0.05,
                 rules: Rules | None = None):
        self.rules = rules if rules is not None else Rules.load(_REPO / "hardware" / "breakout")
        self.pitch = float(pitch)
        x0, y0, x1, y1 = map(float, window)
        self.x0, self.y0 = x0, y0
        self.nx = int(round((x1 - x0) / self.pitch)) + 1
        self.ny = int(round((y1 - y0) / self.pitch)) + 1
        self.x1, self.y1 = x0 + (self.nx - 1) * self.pitch, y0 + (self.ny - 1) * self.pitch
        self.net_names = [""]
        self.net_ids = {"": 0}
        self.objs: list[_Obj] = []
        self.pads: list[Pad] = []
        self.pad_obj: dict[tuple, int] = {}
        self.outline: list[np.ndarray] = []         # outer first, then cut-outs
        self._added: set = set()
        self._arr = None
        self._on = None
        # How far an item's field must reach: the largest distance any rule asks between
        # it and a track or via centre, for the widest track the rules can ask for.
        # `masks` refuses a width wider than this budget (it would under-enforce).
        via_d, _ = self.rules.via
        self.max_half = max(self.rules.max_width / 2, via_d / 2)
        self.reach = self._need(self.max_half) + 2 * self.pitch

    def _need(self, half: float) -> float:
        """The largest threshold `masks` applies to an item's field for a track of half-width
        (or a via of radius) `half`."""
        r = self.rules
        _, via_drill = r.via
        return max(max(r.max_clearance, r.edge_clearance, r.hole_clearance) + half,
                   r.hole_to_hole + via_drill / 2) + MARGIN

    # -- coordinates -----------------------------------------------------------------------
    def ix(self, x: float) -> int:
        return int(round((x - self.x0) / self.pitch))

    def iy(self, y: float) -> int:
        return int(round((y - self.y0) / self.pitch))

    def x(self, ix: int) -> float:
        return round(self.x0 + ix * self.pitch, 6)

    def y(self, iy: int) -> float:
        return round(self.y0 + iy * self.pitch, 6)

    def net_id(self, name: str) -> int:
        nid = self.net_ids.get(name)
        if nid is None:
            nid = len(self.net_names)
            if nid > np.iinfo(NET_DTYPE).max:
                raise OverflowError("more nets than a uint16 holds")
            self.net_ids[name] = nid
            self.net_names.append(name)
        return nid

    # -- adding obstacles ----------------------------------------------------------------
    def _add(self, o: _Obj) -> int:
        bx0, by0, bx1, by1 = o.shape.bbox
        R = self.reach
        ix0 = max(0, int(math.floor((bx0 - R - self.x0) / self.pitch)))
        ix1 = min(self.nx - 1, int(math.ceil((bx1 + R - self.x0) / self.pitch)))
        iy0 = max(0, int(math.floor((by0 - R - self.y0) / self.pitch)))
        iy1 = min(self.ny - 1, int(math.ceil((by1 + R - self.y0) / self.pitch)))
        if ix1 < ix0 or iy1 < iy0:
            return -1                                   # wholly outside the window
        o.rect = (iy0, iy1, ix0, ix1)
        self.objs.append(o)
        self._arr = None
        return len(self.objs) - 1

    def field_of(self, i: int) -> np.ndarray:
        o = self.objs[i]
        if o._field is None:
            iy0, iy1, ix0, ix1 = o.rect
            X = (self.x0 + np.arange(ix0, ix1 + 1) * self.pitch)[None, :]
            Y = (self.y0 + np.arange(iy0, iy1 + 1) * self.pitch)[:, None]
            o._field = o.shape.distance(X, Y).astype(np.float32)
        return o._field

    def add_obstacles(self, obstacles: dict) -> None:
        """Add a board's obstacles, from `board_io.extract()`'s JSON (stage.py)."""
        validate_obstacles(obstacles)
        for k, v in obstacles["design"].items():
            if k in self.rules.board and abs(self.rules.board[k] - float(v)) > 1e-9:
                raise ValueError(f"the board's {k} is {v} mm, the rule files' "
                                 f"{self.rules.board[k]} mm")
        r = self.rules
        out = obstacles["outline"]
        polys = [out["outer"]] + list(out.get("holes", []))
        for k, poly in enumerate(polys):
            v = np.asarray(poly, dtype=float)
            self.outline.append(v)
            self._on = None
            for a, b in zip(v, np.roll(v, -1, axis=0)):
                self._add(_Obj("edge", 0, "", tuple(range(4)),
                               Shape("seg", (a[0], a[1], b[0], b[1], 0.0)),
                               name="outer" if k == 0 else f"cut-out {k}"))
        for d in obstacles["pads"]:
            p = Pad.from_dict(d)
            nid = self.net_id(p.net)
            lay = tuple(sorted(LI[l] for l in p.layers))
            ctx = r.item(p.net, "Pad", footprint=p.ref, pad_number=p.num,
                         pad_type="SMD" if p.smd else "Through-hole",
                         layers=frozenset(p.layers))
            web = r.via_pad_web(p.smd, p.ref, p.num, p.net, p.layers)
            i = self._add(_Obj("pad", nid, p.net, lay, Shape("poly", p.polygon), ctx, web,
                               name=p.key))
            if i >= 0:
                self.pads.append(p)
                self.pad_obj[(p.ref, p.num)] = i
        for h in obstacles["holes"]:
            nid = self.net_id(h["net"])
            self._add(_Obj("hole", nid, h["net"], tuple(range(4)),
                           Shape("circle", (h["at"][0], h["at"][1], h["diameter"] / 2)),
                           plated=bool(h["plated"])))
        for k in obstacles["keepouts"]:
            self.add_keepout(k["polygon"], k["layers"], k["forbids"], k.get("name", ""))
        for t in obstacles["tracks"]:
            self._add_track(t)
        for v in obstacles["vias"]:
            self._add_via(v)

    def add_keepout(self, polygon, layers, forbids=("tracks", "vias", "pours"), name=""):
        """A rule area: no track (or via) copper may overlap `polygon` on `layers`."""
        self._add(_Obj("keepout", 0, "", tuple(sorted(LI[l] for l in layers)),
                       Shape("poly", tuple(tuple(map(float, q)) for q in polygon)),
                       forbids=frozenset(forbids), name=name))

    def _add_track(self, t: dict) -> None:
        key = ("t", t["net"], t["layer"], round(t["width"], 4),
               tuple((round(q[0], 4), round(q[1], 4)) for q in t["pts"]))
        if key in self._added:
            return
        self._added.add(key)
        nid = self.net_id(t["net"])
        ctx = self.rules.item(t["net"], "Track", layers=frozenset([t["layer"]]))
        for a, b in zip(t["pts"], t["pts"][1:]):
            self._add(_Obj("track", nid, t["net"], (LI[t["layer"]],),
                           Shape("seg", (a[0], a[1], b[0], b[1], t["width"] / 2)), ctx))

    def _add_via(self, v: dict) -> None:
        key = ("v", v["net"], round(v["at"][0], 4), round(v["at"][1], 4))
        if key in self._added:
            return
        self._added.add(key)
        nid = self.net_id(v["net"])
        ctx = self.rules.item(v["net"], "Via")
        x, y = v["at"]
        self._add(_Obj("via", nid, v["net"], tuple(range(4)),
                       Shape("circle", (x, y, v["size"] / 2)), ctx))
        self._add(_Obj("hole", nid, v["net"], tuple(range(4)),
                       Shape("circle", (x, y, v["drill"] / 2)), plated=True))

    def add_copper(self, result) -> None:
        """Add a RouteResult's tracks, vias and fixed pieces (each once, however often it is
        called with the same result). Its zones are not obstacles (module docstring)."""
        for f in result.fixed:
            if "track" in f:
                self._add_track(f["track"])
            if "via" in f:
                self._add_via(f["via"])
        for t in result.tracks:
            self._add_track(t)
        for v in result.vias:
            self._add_via(v)

    # -- queries ---------------------------------------------------------------------------
    def _rects(self):
        if self._arr is None:
            self._arr = np.array([o.rect for o in self.objs], dtype=np.int64).reshape(-1, 4)
            self._nets = np.array([o.net for o in self.objs], dtype=NET_DTYPE)
        return self._arr

    def objs_in(self, box) -> np.ndarray:
        """Indices of objects whose field meets the cell box (ix0, iy0, ix1, iy1)."""
        a = self._rects()
        if not len(a):
            return np.zeros(0, dtype=np.int64)
        ix0, iy0, ix1, iy1 = box
        hit = (a[:, 0] <= iy1) & (a[:, 1] >= iy0) & (a[:, 2] <= ix1) & (a[:, 3] >= ix0)
        return np.nonzero(hit)[0]

    def _slices(self, o, box):
        ix0, iy0, ix1, iy1 = box
        oy0, oy1, ox0, ox1 = o.rect
        y0, y1 = max(iy0, oy0), min(iy1, oy1)
        x0, x1 = max(ix0, ox0), min(ix1, ox1)
        return ((slice(y0 - iy0, y1 - iy0 + 1), slice(x0 - ix0, x1 - ix0 + 1)),
                (slice(y0 - oy0, y1 - oy0 + 1), slice(x0 - ox0, x1 - ox0 + 1)))

    def board_mask(self, box) -> np.ndarray:
        """True where a cell centre lies on the board (inside the outer edge, outside
        every cut-out). Computed once for the grid, row by row."""
        if self._on is None:
            on = np.ones((self.ny, self.nx), dtype=bool)
            if self.outline:
                X = (self.x0 + np.arange(self.nx) * self.pitch)[None, :]
                step = max(1, 2_000_000 // self.nx)
                for r0 in range(0, self.ny, step):
                    Y = (self.y0 + np.arange(r0, min(self.ny, r0 + step)) * self.pitch)[:, None]
                    band = poly_inside(self.outline[0], X, Y)
                    for v in self.outline[1:]:
                        if v[:, 1].max() < Y.min() or v[:, 1].min() > Y.max():
                            continue
                        band &= ~poly_inside(v, X, Y)
                    on[r0:r0 + len(band)] = band
            self._on = on
        ix0, iy0, ix1, iy1 = box
        return self._on[iy0:iy1 + 1, ix0:ix1 + 1]

    def masks(self, net: str, width: float, box, layers, via: tuple[float, float] | None):
        """Blocked cells in the cell box (ix0, iy0, ix1, iy1), inclusive.

        Returns ({layer index: bool array, True = no track centre of `net` at `width`},
        bool array, True = no via centre of `net`, or None when `via` is None). `via` is
        (diameter, drill). The window's frame is blocked, so a search never leaves it."""
        r = self.rules
        nid = self.net_ids.get(net, -1)
        hw = width / 2
        half = max(hw, via[0] / 2) if via is not None else hw
        if self._need(half) > self.reach - 2 * self.pitch + 1e-9:
            raise ValueError(f"a {width} mm track is wider than this grid's clearance budget "
                             f"(the widest the rules ask for is {r.max_width} mm): items "
                             f"farther than {self.reach:.3f} mm would go unchecked")
        ix0, iy0, ix1, iy1 = box
        shape = (iy1 - iy0 + 1, ix1 - ix0 + 1)
        lay = sorted(LI[l] if isinstance(l, str) else l for l in layers)
        blk = {L: np.zeros(shape, dtype=bool) for L in lay}
        vblk = np.zeros(shape, dtype=bool) if via is not None else None
        rv, rd = (via[0] / 2, via[1] / 2) if via is not None else (0.0, 0.0)
        a_track = r.item(net, "Track")
        a_via = r.item(net, "Via")
        for i in self.objs_in(box):
            o = self.objs[i]
            f = self.field_of(i)
            wsl, fsl = self._slices(o, box)
            sub = f[fsl]
            k = o.kind
            if k == "edge":
                for L in lay:
                    blk[L][wsl] |= sub < r.edge_clearance + hw + MARGIN
                if vblk is not None:
                    vblk[wsl] |= sub < r.edge_clearance + rv + MARGIN
            elif k == "keepout":
                if "tracks" in o.forbids:
                    for L in lay:
                        if L in o.layers:
                            blk[L][wsl] |= sub < hw + MARGIN
                if vblk is not None and "vias" in o.forbids:
                    vblk[wsl] |= sub < rv + MARGIN
            elif k == "hole":
                foreign = o.net != nid or o.net == 0 or not o.plated
                if foreign:
                    for L in lay:
                        blk[L][wsl] |= sub < r.hole_clearance + hw + MARGIN
                if vblk is not None:
                    t = r.hole_to_hole + rd
                    if foreign:
                        t = max(t, r.hole_clearance + rv)
                    vblk[wsl] |= sub < t + MARGIN
            else:                                       # copper: pad, track, via
                own = o.net == nid and nid != 0
                if not own:
                    for L in lay:
                        if L in o.layers:
                            c = r.clearance(net, o.netname, LAYERS[L], a_track, o.ctx)
                            blk[L][wsl] |= sub < c + hw + MARGIN
                if vblk is not None:
                    t = -math.inf
                    if not own:
                        t = max(r.clearance(net, o.netname, LAYERS[L], a_via, o.ctx)
                                for L in o.layers)
                    if o.web:                  # physical_clearance (N-16), any net
                        t = max(t, o.web)
                    if t > -math.inf:
                        vblk[wsl] |= sub < t + rv + MARGIN
        on = self.board_mask(box)
        for L in lay:
            blk[L] |= ~on
            blk[L][0, :] = blk[L][-1, :] = True
            blk[L][:, 0] = blk[L][:, -1] = True
        if vblk is not None:
            vblk |= ~on
            vblk[0, :] = vblk[-1, :] = True
            vblk[:, 0] = vblk[:, -1] = True
        return blk, vblk

    def inside_cells(self, i: int, inset: float):
        """Cells (iy, ix) whose centres lie inside object i by at least `inset` mm (or, if
        none does, inside it at all)."""
        o = self.objs[i]
        f = self.field_of(i)
        ys, xs = np.nonzero(f <= -inset)
        if not len(ys):
            ys, xs = np.nonzero(f <= 0)
        return ys + o.rect[0], xs + o.rect[2]

    def copper_objs(self, net: str) -> list[int]:
        nid = self.net_ids.get(net)
        return [i for i, o in enumerate(self.objs)
                if nid is not None and o.net == nid and o.kind in ("track", "via", "pad")]
