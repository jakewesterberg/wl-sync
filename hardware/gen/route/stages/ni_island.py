"""The NI island's routing plan, tuned to Quilter's targets (task R1d, 2026-11-24).

    python hardware/gen/route/stages/ni_island.py PACKAGE_DIR OBSTACLES.json OUT_ROUTE.json

R1c's bake-off candidate (hardware/breakout/bakeoff/engine/stage_ni.py, graded as it
stands) is the starting point. This module is the island stage R5 and R12 can reuse:
`NiIsland(grid, result, rules, island_nets).route()` lays the whole island on a grid that
already holds the board's obstacles, and the CLI runs it on the bake-off package, as
stage_ni.py does, through engine/run.sh (`STAGE=` that file).

What R1d changed against stage_ni.py (TUNED, below; each knob is a keyword of NiIsland, and
NiIsland(..., **R1C) is stage_ni.py's plan again, copper for copper). Each was kept only
when the judge still exited 0 and no unmet target got worse; the tuning log, with the
changes tried and dropped, is in task-R1d-report.md:

- joins vertical on B.Cu, then one 45-degree run on F.Cu to the exit (join_shape "VD";
  stage_ni.py's L ran across F.Cu at the exit's height). Every vertical is parallel and
  every diagonal is parallel, so the layers cross freely and the run is octilinear:
  length / MST 1.232 to 1.072 (run r07);
- pin 16's NI_GND to the plane by its own via on the pin's line, as J2's outputs, in place
  of its bypass capacitor's ground via (gnd_via_at_pin16): the via splits the NI_5V
  pocket between the output groups, whose 3 mm neck it was;
- escapes price a via at 10 mm of track, not 3 (escape_via_cost): fewer layer changes
  round J45;
- a pull-up's NI_5V pad joins NI_5V copper within 5 mm, for up to 6 mm of track, before it
  takes a via of its own (pullup_plane_reach, pullup_plane_via_cost);
- joins east to west (join_order "-x"), not inner rows first.

What it keeps from stage_ni.py, by hand, as R1a found it: the OPTOS table; the NI_5V stubs
from J45 pins 8 and 14 (N-4); In2.Cu signal only under J45 (y >= 226: N-13 leaves no
outer-layer way out of J45's inner rows); each of pins 11-14 of U56-U61 by an F.Cu stub to a
via on its own line (J2); pin 16 to its bypass on F.Cu (J3); BARCODE_NI after U60's pull-ups
(K-4); the B-13 fences and the clipped straddler terminals.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from hardware.gen.route.search import (Area, Copper, ViaSite, rect_area,  # noqa: E402
                                       route_connection)
from hardware.gen.route.stage import assert_stayed_local, prune_dangling  # noqa: E402

F, B, I2 = "F.Cu", "B.Cu", "In2.Cu"
STRADDLERS = ("U56", "U57", "U58", "U59", "U60", "U61", "U62")
WIN = (0.0, 101.25, 68.75, 240.0)       # the island's window (its region is 0,100,70,240)
IN2_YMIN = 226.0                        # In2.Cu only under J45 (y >= 226)
J45_INNER = (231.09, 232.995)           # J45's inner rows: In2.Cu is their only way out
OPTOS = [  # ref, (bypass for pins 10/9), (bypass for pins 15/16), {pin: pull-up}
    ("U56", "C121", "C127", {11: "R129", 12: "R127", 13: "R125", 14: "R123"}),
    ("U57", "C122", "C128", {11: "R137", 12: "R135", 13: "R133", 14: "R131"}),
    ("U58", "C123", "C129", {11: "R145", 12: "R143", 13: "R141", 14: "R139"}),
    ("U59", "C124", "C130", {11: "R153", 12: "R151", 13: "R149", 14: "R147"}),
    ("U61", "C126", "C132", {11: "R169", 12: "R167", 13: "R165", 14: "R163"}),
    ("U60", "C125", "C131", {11: "R161", 12: "R159", 13: "R157", 14: "R155"}),
]

# The tuned values (R1d). Each knob is a keyword of NiIsland; R1C holds stage_ni.py's.
TUNED = dict(
    gnd_via_at_pin16=True,        # R1c: False (the capacitor's own ground via)
    pullup_plane_reach=5.0,       # R1c: 3.0
    pullup_plane_via_cost=6.0,    # R1c: None (the nearest via site or copper, whichever)
    hv_x=57.0,                    # exits end west of this x
    exit_via_cost=3.0,
    escape_ymax=224.0,            # escapes end at or north of this y
    escape_in2_cost=4.0,          # In2.Cu step multiplier (only under J45)
    escape_via_cost=10.0,         # R1c: 3.0
    join_via_cost=3.0,
    join_bend_cost=0.2,
    join_prefer=True,
    join_shape="VD",              # R1c: "L"
    join_halves=(1.5, 4.0, 10.0),  # the corridor's half-widths, widening on failure
    join_xmax=50.0,               # the L's column (join_shape "L" only)
    join_order="-x",              # R1c: "j45"
    # Rectangles (x0, y0, x1, y1) where no join may put a via. Empty here: keeping joins'
    # vias out of the strip west of x = 11 widens both planes' neck from 9 to 11 mm, but
    # costs 4 vias and 0.02 of length / MST (task-R1d-report.md, run r29).
    via_free=(),
    runway_half=0.72,             # escape runways (stage_ni.py's)
    runway_len=6.0,
)
R1C = {**TUNED, "gnd_via_at_pin16": False, "pullup_plane_reach": 3.0,
       "pullup_plane_via_cost": None, "escape_via_cost": 3.0, "join_shape": "L",
       "join_order": "j45"}


def band(a, b, half):
    """The segment a-b grown by `half` mm on every side, as an Area on the outer layers."""
    (ax, ay), (bx, by) = a, b
    L = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 or 1.0
    ux, uy = (bx - ax) / L * half, (by - ay) / L * half       # along, scaled to half
    nx, ny = -uy, ux                                           # across
    return Area(((ax - ux + nx, ay - uy + ny), (bx + ux + nx, by + uy + ny),
                 (bx + ux - nx, by + uy - ny), (ax - ux - nx, ay - uy - ny)), (F, B))


class NiIsland:
    def __init__(self, grid, result, rules, island_nets, log=print, **knobs):
        self.g, self.res, self.R = grid, result, rules
        self.say = log
        self.k = {**TUNED, **knobs}
        self.P = {p.key: p for p in grid.pads if p.net in island_nets}
        self.no_in2 = rect_area(0, 100, 70, IN2_YMIN, [I2])
        self.fences = []
        self.log, self.failed = [], []
        self.escape_end, self.joined, self.exit_of = {}, set(), {}

    # -- primitives (stage_ni.py's) ------------------------------------------------------
    def commit(self, label, path):
        self.res.add_path(path)
        self.g.add_copper(self.res)
        self.log.append((label, path.net, len(path.cells), len(path.vias()), round(path.cost, 2)))
        return path

    def term(self, key, layers=None):
        """A pad as a terminal. A straddler pad (B-13, routing-brief 4.1) only from the half
        away from the slot, so every track ending in it ends at or west of its centre."""
        p = self.P[key]
        if p.ref in STRADDLERS:
            cx, cy = p.at
            poly = [(cx + 0.9 * (x - cx), cy + 0.9 * (y - cy)) for x, y in p.polygon]
            out = []
            for a, b in zip(poly, poly[1:] + poly[:1]):        # clip to x <= cx
                if a[0] <= cx:
                    out.append(a)
                if (a[0] <= cx) != (b[0] <= cx):
                    t = (cx - a[0]) / (b[0] - a[0])
                    out.append((cx, a[1] + t * (b[1] - a[1])))
            return Area(tuple(out), tuple(layers or p.layers))
        return p

    def go(self, label, net, src, dst, layers, quiet=False, **kw):
        t = time.time()
        kw["avoid"] = list(kw.get("avoid", [])) + [self.no_in2] + self.fences
        path = route_connection(self.g, net, src, dst, layers, **kw)
        dt = time.time() - t
        if path is None:
            if not quiet:
                self.failed.append(label)
                self.say(f"FAILED {label} ({dt:.1f}s)")
            return None
        if dt > 2:
            self.say(f"  slow {label}: {dt:.1f}s")
        return self.commit(label, path)

    def connect(self, a, b, layers=(F, B), **kw):
        P = self.P
        net = P[a].net
        assert net == P[b].net, (a, b)
        return self.go(f"{a}-{b}", net, [self.term(a, [l for l in P[a].layers if l in layers])],
                       [self.term(b, [l for l in P[b].layers if l in layers])], layers, **kw)

    def tree(self, key, layers=(F, B), reach=8.0, pads=True, **kw):
        """Pad `key` to its net's copper within `reach` mm (then anywhere): its tracks and
        vias, and its pads if `pads` (never a straddler's, B-13)."""
        P = self.P
        net = P[key].net
        x0, y0, x1, y1 = P[key].bbox
        srcs = []
        if pads:
            srcs = [q for q in P.values() if q.net == net and q.key != key
                    and q.ref not in STRADDLERS]
        for clip in ((x0 - reach, y0 - reach, x1 + reach, y1 + reach), None):
            s2 = [Copper(net, pads=False, clip=clip)] + [q for q in srcs if clip is None or (
                clip[0] <= q.at[0] <= clip[2] and clip[1] <= q.at[1] <= clip[3])]
            if self.go(f"tree-{key}", net, s2, [self.term(key)], layers,
                       quiet=clip is not None, **kw):
                return True
        return None

    def to_plane(self, key, allow_tree=False, reach=3.0, max_cost=6.0, via_cost=None):
        """An SMD pad of a plane net to the nearest via site on F.Cu (or to its net's copper
        within `reach` mm that already has one). `via_cost` (R1d), if given, is what ending
        on a new via costs against joining that copper, in mm of track."""
        net = self.P[key].net
        x0, y0, x1, y1 = self.P[key].bbox
        goals = [ViaSite()] + ([Copper(net, clip=(x0 - reach, y0 - reach, x1 + reach,
                                                   y1 + reach))] if allow_tree else [])
        if via_cost and allow_tree:
            # first the net's copper alone, as far as via_cost of track; then a new via
            p = self.go(f"plane-{key}", net, [self.term(key, [F])], goals[1:], (F,),
                        allow_via=False, max_cost=via_cost, quiet=True)
            if p is not None:
                return p
        return self.go(f"plane-{key}", net, [self.term(key, [F])], goals, (F,),
                       allow_via=False, max_cost=max_cost)

    def fixed(self, net, pts, layer, via_end=False):
        self.res.add_track(net, layer, self.R.width(net), pts)
        if via_end:
            self.res.add_via(net, pts[-1], *self.R.via)
        self.g.add_copper(self.res)
        self.log.append(("fixed", net, len(pts), int(via_end), 0))

    def add_fences(self):
        """B-13 / routing-brief 4.1, as the trial: beside each island-side pad of U56 to U62,
        F.Cu is closed outside the pads' own heights from their outer end toward the slot."""
        for ref in STRADDLERS:
            pads = [p for p in self.P.values() if p.ref == ref]
            if not pads:
                continue
            bands = sorted((p.bbox[1], p.bbox[3]) for p in pads)
            x_out = min(p.bbox[0] for p in pads)
            ylo, yhi = bands[0][0] - 1.5, bands[-1][1] + 1.5
            edges = [ylo] + [v for b in bands for v in b] + [yhi]
            for a, b in zip(edges[0::2], edges[1::2]):
                if b - a > 1e-6:
                    self.fences.append(rect_area(x_out - 0.35, a + 0.01, 72.0, b - 0.01, [F]))

    # -- phases --------------------------------------------------------------------------
    def phase_local(self):
        P = self.P
        # J45 pins 8 and 14 (NI_5V, N-4): identical 0.6 mm F.Cu stubs straight up between
        # their NI_GND neighbours to a via each at the same height (the trial's).
        for x in (P["J45.8"].at[0], P["J45.14"].at[0]):
            self.fixed("NI_5V", [(x, P["J45.8"].at[1]), (x, 227.6)], F, via_end=True)
        self.tree("C133.1", (F,))
        self.tree("C134.1", (F,))
        self.to_plane("C133.2")
        self.to_plane("C134.2")
        for ref, c1, c2, pulls in OPTOS:
            # K-35: each bypass to its own pin pair on F.Cu, then to the planes.
            self.connect(f"{ref}.10", f"{c1}.1", (F,), allow_via=False)
            self.connect(f"{ref}.9", f"{c1}.2", (F,), allow_via=False)
            self.connect(f"{ref}.15", f"{c2}.1", (F,), allow_via=False)
            self.connect(f"{ref}.16", f"{c2}.2", (F,), allow_via=False)
            # J2 (routing-brief 6): each of pins 11-14 leaves its outer end by an F.Cu stub
            # to a via on its own line (x 65.80 to 66.05); the band keeps the via there.
            for pin in (14, 13, 12, 11):
                key = f"{ref}.{pin}"
                y = P[key].at[1]
                self.go(f"pinvia-{key}", P[key].net, [self.term(key, [F])], [ViaSite()], (F,),
                        allow_via=False,
                        within=[rect_area(65.5, y - 0.06, 68.75, y + 0.06, [F])])
            pin16 = None
            if self.k["gnd_via_at_pin16"]:
                # R1d: pin 16's NI_GND to the plane by a via on its own line, as J2's
                # outputs (x 65.80 to 66.05), in place of its capacitor's own ground via
                # (the capacitor is joined to pin 16 on F.Cu, K-35). The via sits in the
                # plane pocket between the output groups, which it splits (NI_5V's neck).
                key = f"{ref}.16"
                y = P[key].at[1]
                pin16 = self.go(f"gndvia-{key}", "NI_GND", [Copper("NI_GND", pads=True,
                                clip=(65.5, y - 0.4, 68.75, y + 0.4))], [ViaSite()], (F,),
                                allow_via=False,
                                within=[rect_area(65.5, y - 0.06, 68.75, y + 0.06, [F])])
            for c in (c1, c2):
                self.to_plane(f"{c}.1", allow_tree=False)
                if not (c == c2 and pin16 is not None):
                    self.to_plane(f"{c}.2", allow_tree=False)
            for pin in (14, 13, 12, 11):          # K-34: each output to its own pull-up
                key = f"{ref}.{pin}"
                if P[key].net == "BARCODE_NI":
                    continue
                self.tree(f"{pulls[pin]}.2", pads=False)
            for pin in (14, 13, 12, 11):
                self.to_plane(f"{pulls[pin]}.1", allow_tree=True,
                              reach=self.k["pullup_plane_reach"],
                              via_cost=self.k["pullup_plane_via_cost"])
            if ref == "U60":
                # K-4: BARCODE_NI ahead of every run to J45, straight to its pin 3 (after
                # U60's own pull-ups and their plane vias, which it would otherwise wall off).
                self.go("U60.13-J45.3", "BARCODE_NI", [Copper("BARCODE_NI")], [P["J45.3"]],
                        (F, B), layer_cost={B: 1.6})
                self.tree("R157.2")
        # The U62 output filter (B-22: DNP, routed as fitted).
        self.connect("U62.6", "FB3.1", (F,), allow_via=False)
        self.tree("C135.1", (F,))
        self.connect("FB3.2", "C136.1", (F,), allow_via=False)
        self.tree("R170.1", (F,))
        self.to_plane("R170.2")
        self.to_plane("C135.2")
        self.to_plane("C136.2")

    def j45_keys(self):
        """J45's panel signal pins: the inner rows first, then by pin number."""
        keys = []
        for k, p in self.P.items():
            if p.ref == "J45" and p.net in self.R.panel_nets and p.net != "BARCODE_NI":
                keys.append((p.at[1], int(p.num), k))
        keys.sort(key=lambda t: (min(abs(t[0] - y) for y in J45_INNER) > 0.02, t[1]))
        return [k for _, _, k in keys]

    def phase_exits(self):
        """Each output's copper out of its cluster to F.Cu west of hv_x (stage_ni.py's)."""
        for ref, _, _, _ in OPTOS:
            for pin in (11, 12, 13, 14):
                net = self.P[f"{ref}.{pin}"].net
                if net == "BARCODE_NI":
                    continue
                p = self.go(f"exit-{ref}.{pin}", net, [Copper(net)],
                            [rect_area(0.5, 101.5, self.k["hv_x"], 225.0, [F])], (F, B),
                            via_cost=self.k["exit_via_cost"])
                if p is not None:
                    self.exit_of[net] = p

    def runways(self, but):
        """Above each escape's end not yet joined, a lane kept clear on the outer layers
        (stage_ni.py's), so a later escape or join cannot wall it off."""
        h, ln = self.k["runway_half"], self.k["runway_len"]
        return [rect_area(x - h, y - ln, x + h, y + 0.05, [F, B])
                for k, (x, y) in self.escape_end.items() if k != but and k not in self.joined]

    def phase_escapes(self):
        """Each J45 signal pin out of the connector to B.Cu at y <= escape_ymax (stage_ni.py's
        escapes): the inner rows first, In2.Cu only under J45."""
        for k in self.j45_keys():
            net = self.P[k].net
            p = self.go(f"escape-{k}", net, [self.P[k]],
                        [rect_area(0.5, 101.5, 68.0, self.k["escape_ymax"], [B])], (F, B, I2),
                        avoid=self.runways(k), layer_cost={I2: self.k["escape_in2_cost"]},
                        via_cost=self.k["escape_via_cost"])
            if p is not None:
                _, x, y = p.cells[-1]
                self.escape_end[k] = (x, y)

    def l_corridor(self, sx, sy, ex, ey, half):
        """stage_ni.py's join corridor: up B.Cu from the escape, then across F.Cu at the
        exit's height, as rectangles `half` mm either side of the L (any layer)."""
        xcol = min(sx, self.k["join_xmax"])
        pts = [(sx, sy), (sx, sy - 1.0), (xcol, sy - 2.0), (xcol, ey), (ex, ey)]
        return [rect_area(min(ax, bx) - half, min(ay, by) - half,
                          max(ax, bx) + half, max(ay, by) + half, [F, B])
                for (ax, ay), (bx, by) in zip(pts, pts[1:])]

    def vd_corridor(self, sx, sy, ex, ey, half):
        """R1d's join corridor: up B.Cu from the escape to the height from which a 45-degree
        run up and right on F.Cu reaches the exit, then that run; each leg a band `half` mm
        either side (any layer). Every vertical is parallel and every diagonal is parallel,
        so the two layers cross freely and no join needs a second via."""
        yv = ey + (ex - sx)                 # the diagonal x + y = ex + ey through the exit
        if yv > sy - 1.0:                   # no room to go up first: the L instead
            return self.l_corridor(sx, sy, ex, ey, half)
        return [band(a, b, half) for a, b in (((sx, sy), (sx, yv)), ((sx, yv), (ex, ey)))]

    def phase_joins(self):
        """Each escape's end to its net's exit (stage_ni.py's joins), inside a corridor that
        widens on failure: the L (B.Cu up, F.Cu across; stage_ni.py's) or, R1d, VD (B.Cu up,
        F.Cu at 45 degrees), each layer preferring its own direction."""
        hv = self.k["hv_x"]
        for k in self.join_order():
            net = self.P[k].net
            x, y = self.escape_end[k]
            src = rect_area(x - 0.01, y - 0.01, x + 0.01, y + 0.01, [B])
            goal = [Copper(net, clip=(0.0, 101.0, hv + 0.5, y - 1.0))]
            ex, ey = (self.exit_of[net].cells[-1][1:] if net in self.exit_of
                      else (hv, y - 2.0))
            shape = self.k["join_shape"]
            prefer = None
            if self.k["join_prefer"]:
                prefer = {F: "h", B: "v"} if shape == "L" else {F: "ne", B: "v"}
            corridor = self.l_corridor if shape == "L" else self.vd_corridor
            for half in tuple(self.k["join_halves"]) + (None,):
                within = None if half is None else corridor(x, y, ex, ey, half)
                p = self.go(f"join-{k}", net, [src], goal, (F, B), avoid=self.runways(k),
                            prefer=prefer, within=within, quiet=half is not None,
                            via_avoid=[rect_area(*r, [F, B]) for r in self.k["via_free"]],
                            via_cost=self.k["join_via_cost"],
                            bend_cost=self.k["join_bend_cost"])
                if p is not None:
                    self.joined.add(k)
                    break

    def join_order(self):
        """stage_ni.py's order (`j45`: the inner rows first, then by pin number), or (R1d,
        `-x`) by the escape's end, east first."""
        keys = [k for k in self.j45_keys() if k in self.escape_end]
        if self.k["join_order"] == "-x":
            keys.sort(key=lambda k: (-self.escape_end[k][0], self.escape_end[k][1]))
        return keys

    def planes(self):
        rect = [[WIN[0], WIN[1]], [WIN[2], WIN[1]], [WIN[2], WIN[3]], [WIN[0], WIN[3]]]
        for net, layer, name in (("NI_GND", "In1.Cu", "NI_GND plane (S-6)"),
                                 ("NI_5V", "In2.Cu", "NI_5V plane (S-7)")):
            self.res.add_zone(net=net, layer=layer, outline=rect, priority=0, connect="thermal",
                              min_width=0.25, name=name, clearance=0.2, thermal_gap=0.5,
                              spoke_width=0.6)

    def route(self):
        self.add_fences()
        for name, fn in (("local", self.phase_local), ("exits", self.phase_exits),
                         ("escapes", self.phase_escapes),
                         ("joins", self.phase_joins)):
            t = time.time()
            fn()
            self.say(f"phase {name}: {time.time() - t:.1f}s, failed so far {len(self.failed)}")
        self.planes()
        return self.failed


def main(argv):
    import os
    from hardware.gen.route import Grid, RouteResult, Rules
    t0 = time.time()
    # NI_KNOBS: a JSON object of knobs over TUNED, for a tuning run (R1d's log used it);
    # NI_KNOBS=R1C runs stage_ni.py's plan
    raw = os.environ.get("NI_KNOBS") or "{}"
    knobs = R1C if raw == "R1C" else json.loads(raw)
    package, obs_path, out = Path(argv[0]), Path(argv[1]), Path(argv[2])
    obs = json.loads(obs_path.read_text())
    island = set((package / "island.nets.txt").read_text().split())
    R = Rules.load(package)
    print(f"rules sha256 {R.sha256}; knobs {({**TUNED, **knobs})}", flush=True)
    g = Grid(WIN, rules=R)
    g.add_obstacles(obs)
    res = RouteResult(stage=0, name="ni-island (R1d, tuned engine)",
                      placement_sha256=obs["placement_sha256"], rules_sha256=R.sha256)
    ni = NiIsland(g, res, R, island, log=lambda s: print(s, flush=True), **knobs)
    failed = ni.route()
    res, gone = prune_dangling(res, g.pads)
    print(f"pruned: {gone}")
    ext = assert_stayed_local(res, g.pads, ["NI_5V_FB_RAW", "NI_5V_FB_FILT", "BARCODE_NI"], 3.0)
    print("stayed local:", {n: (round(p, 2), round(c, 2)) for n, (p, c) in ext.items()})
    out.write_text(res.to_json())
    print(f"tracks {len(res.tracks)} vias {len(res.vias)} zones {len(res.zones)}; "
          f"failed {len(failed)}: {failed}")
    print(f"route wall-clock {time.time() - t0:.1f}s")
    return 0          # a failed connection shows in the judge's CONNECTIVITY gate


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
