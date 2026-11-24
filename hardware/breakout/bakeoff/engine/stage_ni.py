"""Our engine's bake-off candidate (task R1c): route the NI-island package with
hardware/gen/route, by R1a's regression stage.

    python stage_ni.py PACKAGE_DIR OBSTACLES.json OUT_ROUTE.json

The engine gets what the cloud routers get and nothing more: the rules are read from the
package's own breakout.kicad_dru and breakout.kicad_pro (with its PANEL class), the nets
from its island.nets.txt, and the obstacles from its board (extract.py). The plan below is
R1a's scratch stage unchanged (task-R1a-report.md, "The NI island regression"): a port of
the Opus NI-trial router's phases (git show trial-ni-opus:hardware/breakout/
trial_route_ni.py: local, exits, escapes, joins) for the post-J3 placement. Pin 16 reaches
its bypass on F.Cu (J3), and pins 11-14 leave by an F.Cu stub to a via on their own line
(J2), which the engine finds itself under N-13 and N-16.
"""
import json
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(WT))
from hardware.gen.route.stage import assert_stayed_local, prune_dangling  # noqa: E402
from hardware.gen.route import (Area, Copper, Grid, RouteResult, Rules, ViaSite,  # noqa
                                rect_area, route_connection)

T0 = time.time()
PACKAGE = Path(sys.argv[1])
obs = json.load(open(sys.argv[2]))
island = set((PACKAGE / "island.nets.txt").read_text().split())
OUT = sys.argv[3]
R = Rules.load(PACKAGE)
print(f"rules from {PACKAGE.resolve().relative_to(WT)}: sha256 {R.sha256}; netclasses {sorted(R.netclasses)}", flush=True)
WIN = (0.0, 101.25, 68.75, 240.0)
g = Grid(WIN, rules=R)
g.add_obstacles(obs)
print(f"grid {g.nx}x{g.ny}, {len(g.objs)} objects, {time.time() - T0:.1f}s", flush=True)
res = RouteResult(stage=0, name="ni-island (R1c bake-off, engine)",
                  placement_sha256=obs["placement_sha256"], rules_sha256=R.sha256)
P = {p.key: p for p in g.pads if p.net in island}
STRADDLERS = ("U56", "U57", "U58", "U59", "U60", "U61", "U62")
F, B, I2 = "F.Cu", "B.Cu", "In2.Cu"
IN2_YMIN = 226.0      # In2.Cu only under J45 (N-13 leaves no outer-layer way out of rows B, C)
NO_IN2 = rect_area(0, 100, 70, IN2_YMIN, [I2])
log, failed = [], []


def commit(label, path):
    res.add_path(path)
    g.add_copper(res)
    log.append((label, path.net, len(path.cells), len(path.vias()), round(path.cost, 2)))
    return path


def term(key, layers=None):
    """A pad as a terminal. A straddler pad (B-13, routing-brief 4.1) only from the half
    away from the slot, so every track ending in it ends at or west of its centre."""
    p = P[key]
    if p.ref in STRADDLERS:
        # the pad's own copper, shrunk 10% toward its centre, west of its centre
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


import os
STOP = os.environ.get("STOP_AT")


def go(label, net, src, dst, layers, quiet=False, **kw):
    if STOP and label == STOP:
        Path(OUT).write_text(res.to_json())
        print("stopped at", label, "; partial route written")
        sys.exit(0)
    t = time.time()
    if os.environ.get("PROFILE_AT") == label:
        import cProfile
        import pstats
        kw["avoid"] = list(kw.get("avoid", [])) + [NO_IN2] + FENCES
        cProfile.runctx("route_connection(g, net, src, dst, layers, **kw)", globals(),
                        locals(), "pat.out")
        pstats.Stats("pat.out").sort_stats("tottime").print_stats(15)
        sys.exit(0)
    kw.setdefault("avoid", [])
    kw["avoid"] = list(kw["avoid"]) + [NO_IN2] + FENCES
    path = route_connection(g, net, src, dst, layers, **kw)
    dt = time.time() - t
    if path is None:
        if quiet:
            return None
        failed.append(label)
        print(f"FAILED {label} ({dt:.1f}s)", flush=True)
        return None
    if dt > 2:
        print(f"  slow {label}: {dt:.1f}s", flush=True)
    return commit(label, path)


def connect(a, b, layers=(F, B), **kw):
    net = P[a].net
    assert net == P[b].net, (a, b)
    return go(f"{a}-{b}", net, [term(a, [l for l in P[a].layers if l in layers])],
              [term(b, [l for l in P[b].layers if l in layers])], layers, **kw)


def tree(key, layers=(F, B), reach=8.0, pads=True, **kw):
    """Pad `key` to its net's copper within `reach` mm (then anywhere): its tracks and
    vias, and its pads if `pads` (never a straddler's, B-13)."""
    net = P[key].net
    x0, y0, x1, y1 = P[key].bbox
    srcs = [Copper(net, pads=False)]
    if pads:
        srcs += [q for q in P.values() if q.net == net and q.key != key and q.ref not in STRADDLERS]
    for clip in ((x0 - reach, y0 - reach, x1 + reach, y1 + reach), None):
        s2 = [Copper(net, pads=False, clip=clip)] + [q for q in srcs[1:] if clip is None or (
            clip[0] <= q.at[0] <= clip[2] and clip[1] <= q.at[1] <= clip[3])]
        if go(f"tree-{key}", net, s2, [term(key)], layers,
              quiet=clip is not None, **kw):
            return True
    return None


def to_plane(key, allow_tree=False):
    """An SMD pad of a plane net to the nearest via site on F.Cu (or to its net's copper
    that already has one)."""
    net = P[key].net
    x0, y0, x1, y1 = P[key].bbox
    goals = [ViaSite()] + ([Copper(net, clip=(x0 - 3, y0 - 3, x1 + 3, y1 + 3))]
                           if allow_tree else [])
    return go(f"plane-{key}", net, [term(key, [F])], goals, (F,), allow_via=False,
              max_cost=6.0)


def fixed(net, pts, layer, via_end=False):
    res.add_track(net, layer, R.width(net), pts)
    if via_end:
        res.add_via(net, pts[-1], *R.via)
    g.add_copper(res)
    log.append(("fixed", net, len(pts), int(via_end), 0))


FENCES = []


def add_fences():
    """B-13 / routing-brief 4.1, as the trial: beside each island-side pad of U56 to U62,
    F.Cu is closed outside the pads' own heights from their outer end toward the slot."""
    for ref in STRADDLERS:
        pads = [p for p in P.values() if p.ref == ref]
        if not pads:
            continue
        bands = sorted((p.bbox[1], p.bbox[3]) for p in pads)
        x_out = min(p.bbox[0] for p in pads)
        ylo, yhi = bands[0][0] - 1.5, bands[-1][1] + 1.5
        edges = [ylo] + [v for b in bands for v in b] + [yhi]
        for a, b in zip(edges[0::2], edges[1::2]):
            if b - a > 1e-6:      # track and via centres, as the trial's fence cells
                FENCES.append(rect_area(x_out - 0.35, a + 0.01, 72.0, b - 0.01, [F]))


OPTOS = [  # ref, (bypass for pins 10/9), (bypass for pins 15/16), {pin: pull-up}
    ("U56", "C121", "C127", {11: "R129", 12: "R127", 13: "R125", 14: "R123"}),
    ("U57", "C122", "C128", {11: "R137", 12: "R135", 13: "R133", 14: "R131"}),
    ("U58", "C123", "C129", {11: "R145", 12: "R143", 13: "R141", 14: "R139"}),
    ("U59", "C124", "C130", {11: "R153", 12: "R151", 13: "R149", 14: "R147"}),
    ("U61", "C126", "C132", {11: "R169", 12: "R167", 13: "R165", 14: "R163"}),
    ("U60", "C125", "C131", {11: "R161", 12: "R159", 13: "R157", 14: "R155"}),
]


def phase_local():
    # J45 pins 8 and 14 (NI_5V, N-4): identical 0.6 mm F.Cu stubs straight up between
    # their NI_GND neighbours to a via each at the same height (the trial's).
    for x in (P["J45.8"].at[0], P["J45.14"].at[0]):
        fixed("NI_5V", [(x, P["J45.8"].at[1]), (x, 227.6)], F, via_end=True)
    tree("C133.1", (F,))
    tree("C134.1", (F,))
    to_plane("C133.2")
    to_plane("C134.2")
    for ref, c1, c2, pulls in OPTOS:
        # K-35: each bypass to its own pin pair on F.Cu, then to the planes.
        connect(f"{ref}.10", f"{c1}.1", (F,), allow_via=False)
        connect(f"{ref}.9", f"{c1}.2", (F,), allow_via=False)
        connect(f"{ref}.15", f"{c2}.1", (F,), allow_via=False)
        connect(f"{ref}.16", f"{c2}.2", (F,), allow_via=False)
        # J2 (routing-brief 6): each of pins 11-14 leaves its outer end by an F.Cu stub to
        # a via on its own line (x 65.80 to 66.05); the band keeps the via there.
        for pin in (14, 13, 12, 11):
            key = f"{ref}.{pin}"
            y = P[key].at[1]
            go(f"pinvia-{key}", P[key].net, [term(key, [F])], [ViaSite()], (F,),
               allow_via=False, within=[rect_area(65.5, y - 0.06, 68.75, y + 0.06, [F])])
        for c in (c1, c2):
            to_plane(f"{c}.1", allow_tree=False)
            to_plane(f"{c}.2", allow_tree=False)
        for pin in (14, 13, 12, 11):          # K-34: each output to its own pull-up
            key = f"{ref}.{pin}"
            if P[key].net == "BARCODE_NI":
                continue
            tree(f"{pulls[pin]}.2", pads=False)
        for pin in (14, 13, 12, 11):
            to_plane(f"{pulls[pin]}.1", allow_tree=True)
        if ref == "U60":
            # K-4: BARCODE_NI ahead of every run to J45, straight to its pin 3 (after
            # U60's own pull-ups and their plane vias, which it would otherwise wall off).
            go("U60.13-J45.3", "BARCODE_NI", [Copper("BARCODE_NI")], [P["J45.3"]], (F, B),
               layer_cost={B: 1.6})
            tree("R157.2")
    # The U62 output filter (B-22: DNP, routed as fitted).
    connect("U62.6", "FB3.1", (F,), allow_via=False)
    tree("C135.1", (F,))
    connect("FB3.2", "C136.1", (F,), allow_via=False)
    tree("R170.1", (F,))
    to_plane("R170.2")
    to_plane("C135.2")
    to_plane("C136.2")


HV_X = 57.0
exit_of = {}


def phase_exits():
    """Each output's copper out of its cluster to F.Cu left of x = 57."""
    for ref, _, _, _ in OPTOS:
        for pin in (11, 12, 13, 14):
            net = P[f"{ref}.{pin}"].net
            if net == "BARCODE_NI":
                continue
            p = go(f"exit-{ref}.{pin}", net, [Copper(net)],
                   [rect_area(0.5, 101.5, HV_X, 225.0, [F])], (F, B))
            if p is not None:
                exit_of[net] = p


def j45_keys():
    keys = []
    for k, p in P.items():
        if p.ref == "J45" and p.net in R.panel_nets and p.net != "BARCODE_NI":
            keys.append((p.at[1], int(p.num), k))
    inner = (231.09, 232.995)
    keys.sort(key=lambda t: (min(abs(t[0] - y) for y in inner) > 0.02, t[1]))
    return [k for _, _, k in keys]


escape_end = {}
joined = set()
RUNWAY_HALF, RUNWAY = 0.72, 6.0


def runways(but):
    out = []
    for k, (x, y) in escape_end.items():
        if k != but and k not in joined:
            out.append(rect_area(x - RUNWAY_HALF, y - RUNWAY, x + RUNWAY_HALF, y + 0.05, [F, B]))
    return out


def phase_escapes(ymax=224.0):
    for k in j45_keys():
        net = P[k].net
        p = go(f"escape-{k}", net, [P[k]], [rect_area(0.5, 101.5, 68.0, ymax, [B])], (F, B, I2),
               avoid=runways(k), layer_cost={I2: 4.0})
        if p is not None:
            _, x, y = p.cells[-1]
            escape_end[k] = (x, y)


JOIN_XMAX = 50.0        # runs down the board stay clear of the exits' ends at x = 57


def l_corridor(sx, sy, ex, ey, half):
    """The trial's join corridor: up B.Cu from the escape, then across F.Cu at the exit's
    height, as rectangles `half` mm either side of the L (any layer)."""
    xcol = min(sx, JOIN_XMAX)
    pts = [(sx, sy), (sx, sy - 1.0), (xcol, sy - 2.0), (xcol, ey), (ex, ey)]
    out = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        out.append(rect_area(min(ax, bx) - half, min(ay, by) - half,
                             max(ax, bx) + half, max(ay, by) + half, [F, B]))
    return out


def phase_joins():
    for k in j45_keys():
        if k not in escape_end:
            continue
        net = P[k].net
        x, y = escape_end[k]
        src = rect_area(x - 0.01, y - 0.01, x + 0.01, y + 0.01, [B])
        goal = [Copper(net, clip=(0.0, 101.0, HV_X + 0.5, y - 1.0))]
        ex, ey = exit_of[net].cells[-1][1:] if net in exit_of else (HV_X, y - 2.0)
        for half in (1.5, 4.0, 10.0, None):
            within = None if half is None else l_corridor(x, y, ex, ey, half)
            p = go(f"join-{k}", net, [src], goal, (F, B), avoid=runways(k),
                   prefer={F: "h", B: "v"}, within=within, quiet=half is not None)
            if p is not None:
                joined.add(k)
                break


def planes():
    rect = [[WIN[0], WIN[1]], [WIN[2], WIN[1]], [WIN[2], WIN[3]], [WIN[0], WIN[3]]]
    for net, layer, name in (("NI_GND", "In1.Cu", "NI_GND plane (S-6)"),
                             ("NI_5V", "In2.Cu", "NI_5V plane (S-7)")):
        res.add_zone(net=net, layer=layer, outline=rect, priority=0, connect="thermal",
                     min_width=0.25, name=name, clearance=0.2, thermal_gap=0.5,
                     spoke_width=0.6)


add_fences()
for name, fn in (("local", phase_local), ("exits", phase_exits), ("escapes", phase_escapes),
                 ("joins", phase_joins)):
    t = time.time()
    fn()
    print(f"phase {name}: {time.time() - t:.1f}s, failed so far {len(failed)}", flush=True)
planes()
res, gone = prune_dangling(res, g.pads)
print(f"pruned: {gone}")
# Route, then measure (wl-shook): the U62 filter's nets stay at U62, BARCODE_NI (K-4) on
# its straight run from U60 to J45 pin 3.
ext = assert_stayed_local(res, g.pads, ["NI_5V_FB_RAW", "NI_5V_FB_FILT", "BARCODE_NI"], 3.0)
print("stayed local:", {n: (round(p, 2), round(c, 2)) for n, (p, c) in ext.items()})
Path(OUT).write_text(res.to_json())
print(f"tracks {len(res.tracks)} vias {len(res.vias)} zones {len(res.zones)}; "
      f"failed {len(failed)}: {failed}")
print(f"route wall-clock {time.time() - T0:.1f}s")
