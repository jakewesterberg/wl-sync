"""A* over the routing grid (task R1a): `route_connection`.

The search is the NI trial router's (`search()` in git show trial-ni-opus:hardware/
breakout/trial_route_ni.py): a heap A* over (cell, layer, heading), eight directions, turns
of at most 90 degrees, a cost for every bend and every via, ties broken on insertion order
so that a run is repeatable. What changed, for speed (the trial took about 15 minutes):

- the free-cell tests are numpy masks from `Grid.masks`, computed once per window, and the
  heap runs on a compact graph of the free cells, its neighbour table and heuristic built
  with numpy;
- terminals are numpy masks too, so a goal can be a large area;
- coarse to fine: a first search on 4 x 4 blocks, each free where any of its cells is,
  then the fine search in a corridor round the block path (2 blocks, then 6), then, if
  both fail, in the whole window. Pooling is optimistic, so every fine path has a block
  path and a block failure proves there is no fine path;
- the window is the terminals' bounding box grown by 5 mm, doubled on failure until it
  covers the whole grid, but only while the failed search reached the window's frame: a
  search that ran out inside the window proves a larger one holds no path either. Before
  the whole-window fine search, a bounded search back from the goals catches a goal that
  is sealed off, which would otherwise cost a search of everything reachable from the
  sources.

Costs are in mm of track: a step is its length (times the layer's multiplier), a via
costs `via_cost`, a 45-degree turn `bend_cost` and a 90-degree turn three times that (so a
chamfered corner, two 45-degree turns, is cheaper than a square one).

Terminals (`sources`, `goals`): a `stage.Pad`, an `Area` (cells inside a polygon on given
layers), `Copper(net)` (cells inside the net's copper already on the grid) or `ViaSite()`
(a goal only: any cell where the net may put a via; the path ends with one there).
"""
from __future__ import annotations

import heapq
import math
from array import array
from dataclasses import dataclass

import numpy as np

from .grid import LI, Grid, poly_signed_distance
from .rules import LAYERS
from .stage import Pad

SQ2 = math.sqrt(2.0)
DIRS = [(1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1)]
INSET = 0.05            # a terminal cell lies this far inside its pad or copper, if it can
COARSE = 4              # coarse blocks are 4 x 4 cells (0.2 mm at the default pitch)
CORRIDOR_BLOCKS = (2, 6)  # the fine search's corridor round the coarse path, in blocks
FINE_ONLY_CELLS = 40_000  # a window this small is searched fine at once
COARSE_WEIGHT = 1.5     # the coarse pass's heuristic weight: it only picks a corridor
CORRIDOR_WEIGHT = 1.0   # the corridor search is plain A* (1.2 was tried: no faster)
REVERSE_POPS = 300_000  # the bound on the search back from the goals (sealed-goal test)
# `prefer`'s step multipliers per direction (DIRS order): the axis itself 1, 45 degrees off
# it 1.4, square to it 3 (wl-shook lessons §5, the trial's HV_MULT). 'h' and 'v' are R1a's;
# 'ne' (up-right and down-left, the board's y growing downward) and 'nw' were added in task
# R1d, so a layer can carry the diagonal of an octilinear two-layer scheme.
PREFER = {"h": (1.0, 1.4, 3.0, 1.4, 1.0, 1.4, 3.0, 1.4),
          "v": (3.0, 1.4, 1.0, 1.4, 3.0, 1.4, 1.0, 1.4),
          "ne": (1.4, 1.0, 1.4, 3.0, 1.4, 1.0, 1.4, 3.0),
          "nw": (1.4, 3.0, 1.4, 1.0, 1.4, 3.0, 1.4, 1.0)}


@dataclass(frozen=True)
class Area:
    """Cells whose centres lie inside `polygon` on `layers`."""
    polygon: tuple
    layers: tuple

    @property
    def bbox(self):
        xs = [q[0] for q in self.polygon]
        ys = [q[1] for q in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def rect_area(x0, y0, x1, y1, layers) -> Area:
    return Area(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), tuple(layers))


@dataclass(frozen=True)
class Copper:
    """The copper `net` already has on the grid (tracks, vias, and its pads if `pads`),
    on `layers`, and only inside the rectangle `clip` (x0, y0, x1, y1) if one is given."""
    net: str
    pads: bool = False
    layers: tuple = LAYERS
    clip: tuple | None = None


@dataclass(frozen=True)
class ViaSite:
    """Goal: any cell where the net may place a via (the path ends with one)."""
    pass


@dataclass
class Path:
    """A routed connection. `cells` is [(layer, x, y)] in mm, from source to goal; a
    layer change at one point is a via. `end_via` puts a via at the last cell."""
    net: str
    width: float
    via: tuple
    cells: list
    cost: float
    end_via: bool = False

    def vias(self) -> list[dict]:
        out = []
        seen = set()
        pts = [(x, y) for (la, x, y), (lb, x2, y2) in zip(self.cells, self.cells[1:])
               if la != lb and (x, y) == (x2, y2)]
        if self.end_via:
            pts.append(self.cells[-1][1:])
        for p in pts:
            if p not in seen:
                seen.add(p)
                out.append({"net": self.net, "at": [p[0], p[1]], "size": self.via[0],
                            "drill": self.via[1]})
        return out

    def tracks(self) -> list[dict]:
        runs, cur = [], [self.cells[0]]
        for c in self.cells[1:]:
            if c[0] != cur[-1][0]:
                runs.append(cur)
                cur = [c]
            else:
                cur.append(c)
        runs.append(cur)
        out = []
        for run in runs:
            if len(run) < 2:
                continue
            pts = [run[0][1:]]
            for a, b, c in zip(run, run[1:], run[2:]):
                d1 = (round((b[1] - a[1]) * 1e4), round((b[2] - a[2]) * 1e4))
                d2 = (round((c[1] - b[1]) * 1e4), round((c[2] - b[2]) * 1e4))
                if d1 != d2:
                    pts.append(b[1:])
            pts.append(run[-1][1:])
            out.append({"net": self.net, "layer": run[0][0], "width": self.width,
                        "pts": [[x, y] for x, y in pts]})
        return out


# ---- terminals to cells -------------------------------------------------------------------
def _terminal_bbox(grid: Grid, t):
    if isinstance(t, (Pad, Area)):
        return t.bbox
    if isinstance(t, Copper):
        idx = grid.copper_objs(t.net)
        if not t.pads:
            idx = [i for i in idx if grid.objs[i].kind != "pad"]
        if not idx:
            return None
        bb = np.array([grid.objs[i].shape.bbox for i in idx])
        out = [bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()]
        if t.clip is not None:
            out = [max(out[0], t.clip[0]), max(out[1], t.clip[1]),
                   min(out[2], t.clip[2]), min(out[3], t.clip[3])]
            if out[0] > out[2] or out[1] > out[3]:
                return None
        return tuple(out)
    return None


def _terminal_mask(grid: Grid, t, box, lay, net) -> dict:
    """{layer index: bool array over the cell box}: the cells of terminal t on `lay`."""
    ix0, iy0, ix1, iy1 = box
    shape = (iy1 - iy0 + 1, ix1 - ix0 + 1)
    out = {}

    def put(L, ys, xs):
        m = (xs >= ix0) & (xs <= ix1) & (ys >= iy0) & (ys <= iy1)
        if not m.any():
            return
        a = out.setdefault(L, np.zeros(shape, dtype=bool))
        a[ys[m] - iy0, xs[m] - ix0] = True

    if isinstance(t, Pad):
        i = grid.pad_obj[(t.ref, t.num)]
        ys, xs = grid.inside_cells(i, INSET)
        for l in t.layers:
            if LI[l] in lay:
                put(LI[l], ys, xs)
    elif isinstance(t, Area):
        hit = _area_mask(grid, t, box)
        if hit is not None:
            sl, inside = hit
            for l in t.layers:
                if LI[l] in lay:
                    a = out.setdefault(LI[l], np.zeros(shape, dtype=bool))
                    a[sl] |= inside
    elif isinstance(t, Copper):
        if t.net != net:
            raise ValueError(f"Copper({t.net}) is not a terminal of {net}")
        for i in grid.copper_objs(t.net):
            o = grid.objs[i]
            if o.kind == "pad" and not t.pads:
                continue
            ys, xs = grid.inside_cells(i, INSET)
            if t.clip is not None:
                cx0, cy0 = grid.ix(t.clip[0]), grid.iy(t.clip[1])
                cx1, cy1 = grid.ix(t.clip[2]), grid.iy(t.clip[3])
                m = (xs >= cx0) & (xs <= cx1) & (ys >= cy0) & (ys <= cy1)
                ys, xs = ys[m], xs[m]
            for L in o.layers:
                if L in lay and LAYERS[L] in t.layers:
                    put(L, ys, xs)
    else:
        raise TypeError(f"not a terminal: {t!r}")
    return out


def _union(masks: list, shape) -> dict:
    out = {}
    for m in masks:
        for L, a in m.items():
            if L in out:
                out[L] |= a
            else:
                out[L] = a.copy()
    return out


# ---- the search ------------------------------------------------------------------------
class _Inf(dict):
    def __missing__(self, k):
        return 1e18


class _Neg(dict):
    def __missing__(self, k):
        return -1


class _Miss:
    """A search that found nothing. `touched`: it reached the window's frame (or hit its pop
    limit), so a larger window might still hold a path; if not, none can."""
    __slots__ = ("touched",)

    def __init__(self, touched: bool):
        self.touched = touched

    def __bool__(self):
        return False


DENSE_STATES = 30_000_000   # above this many states, best/came are dicts, not arrays


def _astar(W, H, free, via_ok, via_goal, lay, srcs, goals, hbox, pitch, via_cost, bend_cost,
           mult, prefer, max_pops, max_cost, max_turn=2, weight=1.0, headings=True):
    """free: {L: bool array (H, W)}, True = a track centre may go here. via_ok: bool array,
    True = a via may go here, or None for no layer change; via_goal: the same, where any
    via site is a goal, or None. srcs: local (L, idx), idx = iy * W + ix; goals: {L: bool
    array}. The window's frame must be blocked. Returns ([(L, idx)], cost), or a _Miss
    saying whether the search reached the window's frame.

    The search runs on a compact graph of the cells free on some layer: neighbour table and
    heuristic built with numpy, then a heap over (cell, layer, heading), heading 8 being
    "none yet" (a source, or just through a via)."""
    nl = len(lay)
    pos = {L: k for k, L in enumerate(lay)}
    anyf = np.zeros((H, W), dtype=bool)
    for L in lay:
        anyf |= free[L]
    ids = np.flatnonzero(anyf)
    nc = len(ids)
    if nc == 0:
        return _Miss(False)
    inv = np.full(W * H, -1, dtype=np.int64)
    inv[ids] = np.arange(nc)
    didx = [dx + dy * W for dx, dy in DIRS]
    nb = np.empty((nc, 8), dtype=np.int64)
    for d, o in enumerate(didx):
        nb[:, d] = inv[ids + o]
    nbr = array("q", nb.ravel().tobytes())
    fr = [array("b", free[L].ravel()[ids].astype(np.int8).tobytes()) for L in lay]
    vok = None if via_ok is None else array("b", via_ok.ravel()[ids].astype(np.int8).tobytes())
    vgo = None if via_goal is None else array("b", via_goal.ravel()[ids].astype(np.int8).tobytes())
    tx0, ty0, tx1, ty1 = hbox
    ix, iy = ids % W, ids // W
    dx = np.maximum(np.maximum(tx0 - ix, ix - tx1), 0)
    dy = np.maximum(np.maximum(ty0 - iy, iy - ty1), 0)
    hk = weight * min(mult[L] for L in lay) * pitch
    hv = hk * (np.maximum(dx, dy) + (SQ2 - 1.0) * np.minimum(dx, dy))
    hc = array("d", hv.astype(np.float64).tobytes())
    edge = array("b", ((ix <= 1) | (iy <= 1) | (ix >= W - 2) | (iy >= H - 2))
                 .astype(np.int8).tobytes())
    touched = False
    dlen = [pitch * (1.0 if ddx == 0 or ddy == 0 else SQ2) for ddx, ddy in DIRS]
    tcost = [0.0, bend_cost, 3.0 * bend_cost, 6.0 * bend_cost, 0.0]
    nexts = []
    for d in range(8):
        opts = sorted({(d + k) % 8 for k in range(-max_turn, max_turn + 1)},
                      key=lambda n: min(abs(n - d), 8 - abs(n - d)))
        nexts.append(tuple((n, tcost[min(abs(n - d), 8 - abs(n - d))]) for n in opts))
    nexts.append(tuple((n, 0.0) for n in range(8)))
    if not headings:                 # no bends counted: every state is heading 8
        nexts = [nexts[8]] * 9
    hd = [n if headings else 8 for n in range(8)]
    step = [[dlen[d] * mult[L] * (prefer[L][d] if prefer.get(L) else 1.0) for d in range(8)]
            for L in lay]
    nstates = nc * nl * 9
    if nstates <= DENSE_STATES:
        best = array("d", [1e18]) * nstates
        came = array("q", [-1]) * nstates
    else:
        best, came = _Inf(), _Neg()
    gz = np.zeros((nc, nl), dtype=np.int8)
    for L, a in goals.items():
        if L in pos:
            gz[:, pos[L]] = a.ravel()[ids]
    goal_c = array("b", gz.ravel().tobytes())        # indexed by c * nl + k
    heap = []
    cnt = 0
    for L, i in sorted(srcs):
        c = int(inv[i])
        if c < 0:
            continue
        s = (c * nl + pos[L]) * 9 + 8
        if best[s] == 0.0:
            continue
        best[s] = 0.0
        heap.append((hc[c], 0.0, cnt, 0.0, s))
        cnt += 1
    heapq.heapify(heap)
    push, pop = heapq.heappush, heapq.heappop
    others = [[k2 for k2 in range(nl) if k2 != k] for k in range(nl)]
    pops = 0
    while heap:
        _, _, _, gc, s = pop(heap)
        if gc > best[s]:
            continue
        pops += 1
        if pops > max_pops:
            return _Miss(True)
        cl, d = divmod(s, 9)
        c, k = divmod(cl, nl)
        if edge[c]:
            touched = True
        if goal_c[cl] or (vgo is not None and vgo[c]):
            path = []
            while s != -1:
                c2, k2 = divmod(s // 9, nl)
                q = (lay[k2], int(ids[c2]))
                if not path or path[-1] != q:
                    path.append(q)
                s = came[s]
            path.reverse()
            return path, gc
        if max_cost is not None and gc > max_cost:
            continue
        frk = fr[k]
        sc = step[k]
        b8 = c * 8
        for nd, tc in nexts[d]:
            ni = nbr[b8 + nd]
            if ni < 0 or not frk[ni]:
                continue
            ng = gc + sc[nd] + tc
            ns = (ni * nl + k) * 9 + hd[nd]
            if ng < best[ns]:
                best[ns] = ng
                came[ns] = s
                push(heap, (ng + hc[ni], -ng, cnt, ng, ns))
                cnt += 1
        if vok is not None and vok[c]:
            ng = gc + via_cost
            for k2 in others[k]:
                if not fr[k2][c]:
                    continue
                ns = (c * nl + k2) * 9 + 8
                if ng < best[ns]:
                    best[ns] = ng
                    came[ns] = s
                    push(heap, (ng + hc[c], -ng, cnt, ng, ns))
                    cnt += 1
    return _Miss(touched)


def route_connection(grid: Grid, net: str, sources: list, goals: list,
                     layers: tuple[str, ...], via_cost: float = 3.0, bend_cost: float = 0.2,
                     *, width: float | None = None, allow_via: bool = True,
                     layer_cost: dict | None = None, prefer: dict | None = None,
                     avoid: list | None = None, within: list | None = None,
                     margin: float = 5.0, max_pops: int = 4_000_000,
                     max_cost: float | None = None, via_avoid: list | None = None
                     ) -> Path | None:
    """The cheapest path for `net` from any source terminal to any goal terminal, on
    `layers`, or None if there is none.

    width: the net's (Rules.width) unless given. layer_cost: {layer: multiplier} on step
    length. prefer: {layer: 'h' | 'v' | 'ne' | 'nw'}: steps square to the axis cost 3x, 45
    degrees off it 1.4x (PREFER; wl-shook lessons §5, the trial's HV_MULT). avoid: [Area]
    the path may not enter (on their layers); within: [Area] the path must stay inside (any
    layer). max_cost: give up on states dearer than this (mm), e.g. to hold a plane drop
    short. via_avoid (task R1d):
    [Area] where no via may go (tracks may), e.g. to keep vias out of a band where they
    would wall off part of a plane."""
    rules = grid.rules
    w = rules.width(net) if width is None else width
    via = rules.via
    lay = sorted(LI[l] for l in layers)
    mult = {L: 1.0 for L in lay}
    for l, m in (layer_cost or {}).items():
        if LI[l] in mult:
            mult[LI[l]] = float(m)
    pref = {}
    for l, axis in (prefer or {}).items():
        if LI[l] in mult:
            pref[LI[l]] = list(PREFER[axis])
    goal_via = any(isinstance(g, ViaSite) for g in goals)
    goal_terms = [g for g in goals if not isinstance(g, ViaSite)]
    boxes = [b for b in (_terminal_bbox(grid, t) for t in list(sources) + goal_terms)
             if b is not None]
    if not boxes:
        return None
    bx0 = min(b[0] for b in boxes)
    by0 = min(b[1] for b in boxes)
    bx1 = max(b[2] for b in boxes)
    by1 = max(b[3] for b in boxes)
    full = (0, 0, grid.nx - 1, grid.ny - 1)
    m = margin
    while True:
        box = (max(0, grid.ix(bx0 - m)), max(0, grid.iy(by0 - m)),
               min(grid.nx - 1, grid.ix(bx1 + m)), min(grid.ny - 1, grid.iy(by1 + m)))
        got = _route_in(grid, net, w, via, lay, sources, goal_terms, goal_via, box, via_cost,
                        bend_cost, mult, pref, avoid, within, allow_via, max_pops, max_cost,
                        via_avoid)
        if isinstance(got, Path):
            return got
        if box == full or not got.touched:
            return None             # no larger window can hold a path
        m *= 2


def _route_in(grid, net, w, via, lay, sources, goal_terms, goal_via, box, via_cost,
              bend_cost, mult, pref, avoid, within, allow_via, max_pops, max_cost,
              via_avoid=None):
    ix0, iy0, ix1, iy1 = box
    W, H = ix1 - ix0 + 1, iy1 - iy0 + 1
    need_via = allow_via or goal_via
    blk, vblk = grid.masks(net, w, box, [LAYERS[L] for L in lay], via if need_via else None)
    for a in avoid or ():
        hit = _area_mask(grid, a, box)
        if hit is None:
            continue
        sl, inside = hit
        for l in a.layers:
            if LI[l] in blk:
                blk[LI[l]][sl] |= inside
    if within:
        ok = np.zeros((H, W), dtype=bool)
        for a in within:
            hit = _area_mask(grid, a, box)
            if hit is not None:
                ok[hit[0]] |= hit[1]
        for L in blk:
            blk[L] |= ~ok
    if vblk is not None:
        for a in via_avoid or ():
            hit = _area_mask(grid, a, box)
            if hit is not None:
                vblk[hit[0]] |= hit[1]
    fr = {L: ~blk[L] for L in lay}
    vf = None if vblk is None else ~vblk
    smask = _union([_terminal_mask(grid, t, box, lay, net) for t in sources], (H, W))
    gmask = _union([_terminal_mask(grid, t, box, lay, net) for t in goal_terms], (H, W))
    for L in list(gmask):
        gmask[L] &= fr[L]
    for L in list(smask):
        smask[L] &= fr[L]
        if L in gmask:
            smask[L] &= ~gmask[L]  # a source that is already a goal (a tree holding its pad)
    srcs = sorted((L, int(i)) for L, a in smask.items() for i in np.flatnonzero(a))
    has_goal = any(a.any() for a in gmask.values())
    if not srcs or (not has_goal and not goal_via):
        return _Miss(False)

    def fine(corridor=None):
        if corridor is None:
            f, v, ss, gg = fr, vf, srcs, gmask
        else:
            f = {L: fr[L] & corridor for L in lay}
            v = None if vf is None else vf & corridor
            cflat = corridor.ravel()
            ss = [c for c in srcs if cflat[c[1]]]
            gg = {L: a & corridor for L, a in gmask.items()}
            if not ss or (not any(a.any() for a in gg.values()) and not goal_via):
                return _Miss(True)
        return _astar(W, H, f, v if allow_via else None, v if goal_via else None, lay, ss,
                      gg, _hbox(gg), grid.pitch, via_cost, bend_cost, mult, pref,
                      max_pops, max_cost, weight=1.0 if corridor is None else CORRIDOR_WEIGHT)

    if W * H <= FINE_ONLY_CELLS:
        found = fine()
    else:
        found = None
        cpath = _coarse(W, H, fr, vf, lay, smask, gmask, goal_via, allow_via, grid.pitch,
                        via_cost, bend_cost, mult, pref, max_pops, max_cost)
        if isinstance(cpath, _Miss):
            return cpath            # optimistic pooling: no coarse path, no fine one
        Hc, Wc = -(-H // COARSE), -(-W // COARSE)
        hit = np.zeros((Hc, Wc), dtype=bool)
        for _, ci in cpath:
            hit[ci // Wc, ci % Wc] = True
        for k in CORRIDOR_BLOCKS:
            c = _dilate(hit, k)
            c = np.repeat(np.repeat(c, COARSE, axis=0), COARSE, axis=1)[:H, :W]
            found = fine(c)
            if found:
                break
        if not found and has_goal and not goal_via:
            # Before searching the whole window: is the goal side sealed off? A bounded
            # search back from the goals that runs out without reaching a source or the
            # window's frame proves there is no path, here or in any larger window.
            back = _astar(W, H, fr, vf if allow_via else None, None, lay,
                          sorted((L, int(i)) for L, a in gmask.items() for i in np.flatnonzero(a)),
                          smask, _hbox(smask), grid.pitch, via_cost, bend_cost, mult, pref,
                          REVERSE_POPS, max_cost)
            if isinstance(back, _Miss) and not back.touched:
                return back
        if not found:
            found = fine()
    if not found:
        return found
    path, cost = found
    cells = []
    for L, i in path:
        iy, ix = divmod(i, W)
        cells.append((LAYERS[L], grid.x(ix + ix0), grid.y(iy + iy0)))
    L, i = path[-1]
    end_via = goal_via and not (L in gmask and gmask[L].ravel()[i])
    return Path(net, w, via, cells, cost, end_via=end_via)


def _area_mask(grid, a, box):
    """(window slice, bool array) of the cells inside Area a, over its bbox only."""
    ix0, iy0, ix1, iy1 = box
    bx0, by0, bx1, by1 = a.bbox
    cx0, cy0 = max(ix0, grid.ix(bx0) - 1), max(iy0, grid.iy(by0) - 1)
    cx1, cy1 = min(ix1, grid.ix(bx1) + 1), min(iy1, grid.iy(by1) + 1)
    if cx1 < cx0 or cy1 < cy0:
        return None
    X = (grid.x0 + np.arange(cx0, cx1 + 1) * grid.pitch)[None, :]
    Y = (grid.y0 + np.arange(cy0, cy1 + 1) * grid.pitch)[:, None]
    inside = poly_signed_distance(np.asarray(a.polygon, float), X, Y) <= 0
    return (slice(cy0 - iy0, cy1 - iy0 + 1), slice(cx0 - ix0, cx1 - ix0 + 1)), inside


def _hbox(goals: dict):
    """(ix0, iy0, ix1, iy1) of the goal cells, for the heuristic; with none (a via
    anywhere) a box holding everything, so the search is Dijkstra."""
    ys, xs = [], []
    for a in goals.values():
        r = np.flatnonzero(a.any(axis=1))
        c = np.flatnonzero(a.any(axis=0))
        if len(r):
            ys += [r[0], r[-1]]
            xs += [c[0], c[-1]]
    if not ys:
        return (-10 ** 9, -10 ** 9, 10 ** 9, 10 ** 9)
    return (min(xs), min(ys), max(xs), max(ys))


def _dilate(a: np.ndarray, k: int) -> np.ndarray:
    out = a.copy()
    for _ in range(k):
        b = out.copy()
        b[1:, :] |= out[:-1, :]
        b[:-1, :] |= out[1:, :]
        b[:, 1:] |= out[:, :-1]
        b[:, :-1] |= out[:, 1:]
        out = b
    return out


def _coarse(W, H, fr, vf, lay, smask, gmask, goal_via, allow_via, pitch, via_cost,
            bend_cost, mult, pref, max_pops, max_cost):
    """The search on COARSE x COARSE blocks, each free where any of its fine cells is
    (on that layer; for a via, where any is a via site). Every fine path has a coarse one,
    so a coarse failure proves there is no fine path in the window. Headings and bends
    are not counted here, and the heuristic is weighted, since a block path is only a
    corridor for the fine search, which counts them. `max_cost` is not applied here: a
    block path can cost more than the fine path it stands for, and a cut on it would make
    a coarse failure unsound."""
    F = COARSE
    Hc, Wc = -(-H // F), -(-W // F)

    def pool(a, frame=True):
        p = np.zeros((Hc * F, Wc * F), dtype=bool)
        p[:H, :W] = a
        c = p.reshape(Hc, F, Wc, F).any(axis=(1, 3))
        if frame:
            c[0, :] = c[-1, :] = c[:, 0] = c[:, -1] = False
        return c

    cf = {L: pool(fr[L]) for L in lay}
    cv = None if vf is None else pool(vf)
    cs = {L: pool(a) & cf[L] for L, a in smask.items()}
    cg = {L: pool(a) & cf[L] for L, a in gmask.items()}
    srcs = sorted((L, int(i)) for L, a in cs.items() for i in np.flatnonzero(a))
    if not srcs or (not any(a.any() for a in cg.values()) and not goal_via):
        return _Miss(False)
    found = _astar(Wc, Hc, cf, cv if allow_via else None, cv if goal_via else None, lay, srcs,
                   cg, _hbox(cg), pitch * F, via_cost, bend_cost, mult, pref, max_pops,
                   None, weight=COARSE_WEIGHT, headings=False)
    return found if isinstance(found, _Miss) else found[0]
