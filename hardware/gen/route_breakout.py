"""The breakout board's routing driver (task R1b, 2026-11-24): the routed board is a build.

    python hardware/gen/route_breakout.py build [--through N]
    python hardware/gen/route_breakout.py route N

`build` places the board (gen_breakout_pcb.py --out build/route/placed), replays every
committed route file hardware/breakout/routes/stage-NN.json (through stage N, if given) onto
it with no search, fills the zones, and writes hardware/breakout/breakout.kicad_pcb and
hardware/breakout/routes/routed.nets (the nets of the stages replayed, one per line). The
generator's breakout.kicad_pro and breakout.kicad_dru are written beside the board if they
differ from the committed ones. `route N` searches stage N, whose plan is the module
hardware/gen/route/stages/sNN_<name>.py, on top of the stages before N, writes
routes/stage-NN.json, then builds through N. Both print KiCad's unrouted-connection count
over the routed nets and over the whole board.

So a placement nudge costs a rebuild, not a hand re-route: change ANCHORS in the generator,
then `build`. Copper comes only from the route files; nobody edits copper in pcbnew (the
routing plan's Global Constraints). Hence the two refusals, each before anything is written:

- STALE PLACEMENT OR RULES (Review Focus 1, 2). Each route file records the sha256 of the
  placed board it was routed on (the generator's bytes, uuids seeded) and of the rules
  (breakout.kicad_dru and the .kicad_pro's net_settings; Rules.sha256). A file whose hashes
  differ from this build's is refused, by name: its copper would land on moved pads or
  against changed clearances. Re-route that stage.
- UNREPLAYED COPPER (Review Focus 4). The committed board's tracks, vias and pours must be
  exactly the copper of the route files through some stage k (k = 0: none). Anything else
  is a hand edit that a rebuild would silently discard; it is refused. Put the edit in its
  stage's `fixed` list and rebuild. So a stage's copper leaves the board before its file
  does: to drop stages after k, `build --through k`, then delete their files.

A stage module defines STAGE (int), NAME (str), nets(board_nets) -> [net], zones() ->
[zone dict], layers(net) -> (layer, ...), order(nets) -> [net] and fixed() -> [{"track": ...}
| {"via": ...}] (the plan's stage interface). The driver replays `fixed` first, adds the
zones, then routes each net in `order`: pad by pad, nearest unjoined pad first, from the
pads and copper already joined. A module may instead define route(ctx) to route its nets
itself (ctx: grid, result, nets, obstacles, rules, route_net). The result is pruned of
dangling copper, with every earlier stage's copper and the extracted board's as `existing`.

It runs under a Python with numpy (the engine's search); pcbnew work runs in KiCad's own
Python (board_io.py, in a subprocess; $WL_KICAD_PYTHON overrides its path). The build
directory build/route/ is git-ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hardware.gen.route.rules import Rules  # noqa: E402
from hardware.gen.route.stage import (RouteResult, norm_track, norm_via,  # noqa: E402
                                      norm_zone, prune_dangling)

BREAKOUT = ROOT / "hardware" / "breakout"
BOARD = BREAKOUT / "breakout.kicad_pcb"
ROUTES = BREAKOUT / "routes"
ROUTED_NETS = ROUTES / "routed.nets"
GENERATOR = ROOT / "hardware" / "gen" / "gen_breakout_pcb.py"
STAGES = ROOT / "hardware" / "gen" / "route" / "stages"
BUILD = ROOT / "build" / "route"
PLACED = BUILD / "placed"
KICAD_PY = Path(os.environ.get(
    "WL_KICAD_PYTHON",
    "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/"
    "bin/python3"))
STAGE_FILE = re.compile(r"stage-(\d\d)\.json")


class Refused(Exception):
    """The driver will not build: the message says why and what to do."""


def say(*a):
    print(*a, flush=True)


# ---- the pieces -----------------------------------------------------------------------------
def kicad(*args: str) -> str:
    """board_io's command line in KiCad's Python; its stdout."""
    p = subprocess.run([str(KICAD_PY), "-m", "hardware.gen.route.board_io", *args],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    if p.returncode != 0:
        err = "\n".join(l for l in p.stderr.splitlines()
                        if "Debug" not in l and "assert" not in l)
        raise SystemExit(f"board_io {args[0]} failed:\n{err[-4000:]}")
    return p.stdout


def place() -> tuple[str, str]:
    """Run the generator into build/route/placed. Returns (placement_sha256, rules_sha256)."""
    PLACED.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([sys.executable, str(GENERATOR), "--out", str(PLACED)],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused(f"the generator failed:\n{p.stdout[-4000:]}{p.stderr[-4000:]}")
    placed = PLACED / BOARD.name
    return (hashlib.sha256(placed.read_bytes()).hexdigest(), Rules.load(PLACED).sha256)


def route_files() -> dict[int, Path]:
    """The committed route files, by stage."""
    out = {}
    for f in sorted(ROUTES.glob("stage-*.json")) if ROUTES.exists() else []:
        m = STAGE_FILE.fullmatch(f.name)
        if not m:
            raise Refused(f"{f.relative_to(ROOT)}: not named stage-NN.json")
        n = int(m.group(1))
        d = json.loads(f.read_text())
        if d.get("stage") != n:
            raise Refused(f"{f.relative_to(ROOT)} holds stage {d.get('stage')}, not {n}")
        out[n] = f
    return out


def check_fresh(files: dict[int, Path], placement: str, rules: str) -> list[RouteResult]:
    """Refuse a route file recorded against another placement or other rules."""
    out = []
    for n in sorted(files):
        f = files[n]
        r = RouteResult.from_json(f.read_text())
        rel = f.relative_to(ROOT)
        if r.placement_sha256 != placement:
            raise Refused(
                f"stale placement: {rel} was routed on placement {r.placement_sha256[:12]}, "
                f"and the generator now places {placement[:12]}. Its copper would land on "
                f"moved pads. Re-route it: route_breakout.py route {n}")
        if r.rules_sha256 != rules:
            raise Refused(
                f"stale rules: {rel} was routed under rules {r.rules_sha256[:12]}, and "
                f"breakout.kicad_dru / net_settings are now {rules[:12]}. Re-route it: "
                f"route_breakout.py route {n}")
        out.append(r)
    return out


def _copper_key(cu: dict) -> dict:
    """A copper set as sorted, comparable lists: tracks split into segments."""
    segs = []
    for t in cu["tracks"]:
        t = norm_track(t)
        for a, b in zip(t["pts"], t["pts"][1:]):
            segs.append(json.dumps([t["net"], t["layer"], t["width"], a, b]))
    vias = [json.dumps(norm_via(v), sort_keys=True) for v in cu["vias"]]
    zones = []
    for z in cu["zones"]:
        z = norm_zone(z)
        zones.append(json.dumps([z["net"], z["layer"], z["name"], z["outline"], z["priority"],
                                 z["connect"], z["min_width"]]))
    return {"tracks": sorted(segs), "vias": sorted(vias), "zones": sorted(zones)}


def _copper_of(r: RouteResult) -> dict:
    return {"tracks": r.tracks + [f["track"] for f in r.fixed if "track" in f],
            "vias": r.vias + [f["via"] for f in r.fixed if "via" in f], "zones": r.zones}


def check_committed(files: dict[int, Path]) -> int:
    """Refuse when the committed board carries copper that no prefix of the route files
    holds. Returns the stage the committed board was built through."""
    if not BOARD.exists():
        return 0
    got = BUILD / "committed-copper.json"
    kicad("copper", str(BOARD), str(got))
    have = _copper_key(json.loads(got.read_text()))
    acc = {"tracks": [], "vias": [], "zones": []}
    cands = [(0, _copper_key(acc))]
    for n in sorted(files):
        cu = _copper_of(RouteResult.from_json(files[n].read_text()))
        for k in acc:
            acc[k] = acc[k] + cu[k]
        cands.append((n, _copper_key(acc)))
    for n, want in cands:
        if want == have:
            return n
    n, want = min(cands, key=lambda c: sum(len(set(c[1][k]) ^ set(have[k])) for k in have))
    extra = {k: len(set(have[k]) - set(want[k])) for k in have}
    missing = {k: len(set(want[k]) - set(have[k])) for k in have}
    raise Refused(
        f"unreplayed copper: {BOARD.relative_to(ROOT)} has copper that the route files do "
        f"not hold (nearest: the stages through {n}; on the board and in no route file "
        f"{extra}, in the route files and not on the board {missing}). A rebuild would "
        f"discard it. Write a hand fix into its stage's `fixed` list, or restore the "
        f"board with git, then rebuild.")


def write_outputs(built: Path, nets: list[str]) -> None:
    ROUTES.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, BOARD)
    ROUTED_NETS.write_text("".join(n + "\n" for n in nets))
    for name in ("breakout.kicad_pro", "breakout.kicad_dru"):
        src, dst = PLACED / name, BREAKOUT / name
        if not dst.exists() or src.read_bytes() != dst.read_bytes():
            shutil.copyfile(src, dst)
            say(f"wrote {dst.relative_to(ROOT)} (the generator's changed)")


def replay(files: dict[int, Path], placement: str, rules: str, through: int | None) -> int:
    """Replay the route files through `through` onto the placed board and write the
    outputs, after both refusals."""
    use = {n: f for n, f in files.items() if through is None or n <= through}
    results = check_fresh(use, placement, rules)
    was = check_committed(files)
    say(f"committed board: the route files' copper through stage {was}; no hand edits")
    nets = sorted({n for r in results for n in r.nets})
    nets_file = BUILD / "routed.nets"
    nets_file.write_text("".join(n + "\n" for n in nets))
    built = BUILD / "out" / BOARD.name
    t = time.time()
    got = json.loads(kicad("apply", str(PLACED / BOARD.name), str(built), str(nets_file),
                           *[str(use[n]) for n in sorted(use)]).strip().splitlines()[-1])
    write_outputs(built, nets)
    stages = ", ".join(use[n].name for n in sorted(use)) or "none"
    say(f"replayed {len(use)} route file(s) ({stages}) in {time.time() - t:.0f} s; "
        f"wrote {BOARD.relative_to(ROOT)} and {ROUTED_NETS.relative_to(ROOT)}")
    say(f"connectivity: routed nets {got['routed']} unconnected over {got['nets']} nets; "
        f"whole board {got['board']} unconnected")
    return 0


# ---- build ----------------------------------------------------------------------------------
def build(through: int | None) -> int:
    placement, rules = place()
    say(f"placed: {PLACED.relative_to(ROOT)}/{BOARD.name} sha256 {placement[:12]}; "
        f"rules {rules[:12]}")
    return replay(route_files(), placement, rules, through)


# ---- route ----------------------------------------------------------------------------------
def stage_module(n: int):
    found = sorted(STAGES.glob(f"s{n:02d}_*.py")) if STAGES.exists() else []
    if len(found) != 1:
        raise Refused(f"stage {n}: expected one module {STAGES.relative_to(ROOT)}/"
                      f"s{n:02d}_<name>.py, found {[f.name for f in found]}")
    spec = importlib.util.spec_from_file_location(f"wl_route_stage_{n:02d}", found[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if getattr(mod, "STAGE", None) != n:
        raise Refused(f"{found[0].name}: STAGE is {getattr(mod, 'STAGE', None)}, not {n}")
    for name in ("NAME", "nets", "zones", "layers", "order", "fixed"):
        if not hasattr(mod, name):
            raise Refused(f"{found[0].name}: no {name} (the stage interface)")
    return mod


def route_net(grid, result: RouteResult, net: str, layers: tuple, **kw) -> list[str]:
    """Join `net`'s pads on `layers`: from its first pad (by name), the nearest unjoined
    pad each time, from the pads and copper joined so far. Returns the pads it failed to
    reach. Refuses a net that already has copper from an earlier stage, which this simple
    loop cannot tell joined from not."""
    from hardware.gen.route.search import Copper, route_connection
    pads = sorted((p for p in grid.pads if p.net == net),
                  key=lambda p: (p.ref, len(p.num), p.num))
    if len(pads) < 2:
        return []
    def laid():
        return [i for i in grid.copper_objs(net) if grid.objs[i].kind != "pad"]
    if laid():
        raise Refused(f"net {net} has copper from an earlier stage; the default loop joins "
                      f"only fresh nets (define route(ctx) in the stage module)")
    joined, left, failed = [pads[0]], pads[1:], []
    while left:
        left.sort(key=lambda p: (min(math.dist(p.at, q.at) for q in joined), p.ref,
                                 len(p.num), p.num))
        goal = left.pop(0)
        sources = list(joined) + ([Copper(net, layers=tuple(layers))]
                                  if laid() else [])
        path = route_connection(grid, net, sources, [goal], tuple(layers), **kw)
        if path is None:
            failed.append(goal.key)
            continue
        result.add_path(path)
        grid.add_copper(result)
        joined.append(goal)
    return failed


def search(n: int, mod, placement: str, rules: Rules, earlier: list[RouteResult]):
    from hardware.gen.route.grid import Grid
    obs_path = BUILD / "obstacles.json"
    kicad("extract", str(PLACED / BOARD.name), str(obs_path))
    obs = json.loads(obs_path.read_text())
    if obs["placement_sha256"] != placement:
        raise Refused("the extracted board is not the placed board")
    outer = obs["outline"]["outer"]
    window = (min(q[0] for q in outer), min(q[1] for q in outer),
              max(q[0] for q in outer), max(q[1] for q in outer))
    grid = Grid(window, rules=rules)
    grid.add_obstacles(obs)
    for r in earlier:
        grid.add_copper(r)
    res = RouteResult(stage=n, name=mod.NAME, placement_sha256=placement,
                      rules_sha256=rules.sha256)
    res.fixed = [{"track": norm_track(f["track"])} if "track" in f else {"via": norm_via(f["via"])}
                 for f in mod.fixed()]
    for f in res.fixed:
        res._net((f.get("track") or f.get("via"))["net"])
    grid.add_copper(res)
    for z in mod.zones():
        res.add_zone(**z)
    board_nets = sorted({p.net for p in grid.pads if p.net})
    nets = list(mod.nets(board_nets))
    for net in nets:
        res._net(net)
    failed = {}
    t = time.time()
    if hasattr(mod, "route"):
        ctx = SimpleNamespace(grid=grid, result=res, nets=nets, obstacles=obs, rules=rules,
                              route_net=route_net, failed=failed)
        mod.route(ctx)
    else:
        for net in mod.order(nets):
            bad = route_net(grid, res, net, tuple(mod.layers(net)))
            if bad:
                failed[net] = bad
    say(f"stage {n} {mod.NAME}: {len(nets)} nets searched in {time.time() - t:.0f} s")
    pruned, gone = prune_dangling(res, grid.pads, existing=list(earlier) + [obs])
    say(f"pruned: {gone}")
    return pruned, failed


def route(n: int) -> int:
    mod = stage_module(n)
    placement, rules_sha = place()
    files = route_files()
    later = sorted(k for k in files if k > n)
    if later:
        raise Refused(f"stage files after {n} exist ({[files[k].name for k in later]}): they "
                      f"were routed on the old stage {n}'s copper. First drop their copper "
                      f"from the board (route_breakout.py build --through {n}), then delete "
                      f"them, then route {n} and re-route them after it.")
    earlier = check_fresh({k: f for k, f in files.items() if k < n}, placement, rules_sha)
    check_committed(files)
    rules = Rules.load(PLACED)
    result, failed = search(n, mod, placement, rules, earlier)
    if failed:
        part = BUILD / f"stage-{n:02d}.partial.json"
        part.write_text(result.to_json())
        raise Refused(f"stage {n}: {sum(map(len, failed.values()))} connection(s) not "
                      f"routed, in {len(failed)} net(s): "
                      + "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(failed.items()))
                      + f". Nothing written; the partial result is {part.relative_to(ROOT)}")
    ROUTES.mkdir(parents=True, exist_ok=True)
    out = ROUTES / f"stage-{n:02d}.json"
    out.write_text(result.to_json())
    say(f"wrote {out.relative_to(ROOT)}: {len(result.tracks)} tracks, {len(result.vias)} "
        f"vias, {len(result.zones)} zones, {len(result.fixed)} fixed")
    return replay(route_files(), placement, rules_sha, n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="replay the route files onto a fresh placement")
    b.add_argument("--through", type=int, default=None, help="replay stages <= N only")
    r = sub.add_parser("route", help="search stage N, write its route file, then build")
    r.add_argument("stage", type=int)
    a = ap.parse_args(argv)
    try:
        return build(a.through) if a.cmd == "build" else route(a.stage)
    except Refused as e:
        print(f"REFUSED: {e}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
