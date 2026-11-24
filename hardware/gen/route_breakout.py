"""The breakout board's routing driver (task R1b, 2026-11-24): the routed board is a build.

    python hardware/gen/route_breakout.py build [--through N]
    python hardware/gen/route_breakout.py route N

`build` places the board (gen_breakout_pcb.py --out build/route/placed), replays every
committed route file hardware/breakout/routes/stage-NN.json (through stage N, if given) onto
it with no search, fills the zones, and writes hardware/breakout/breakout.kicad_pcb,
hardware/breakout/routes/built.json (the stamp, below) and the stage contract the judge
reads (task R2, fix round 1), each the union over the stages replayed:

- routes/complete.nets: the nets the stages complete, one per line. A stage module may
  declare COMPLETE (a list, or a function complete(nets) given the nets the stage touched);
  the default is the nets the stage routes with tracks or vias of its own (the default loop,
  route(ctx) or fixed()). A zone never completes a net.
- routes/touched.nets: every net with copper of the stages, zones included.
- routes/due.txt: the judge's check IDs, or check/clause keys (G-1/overlap), that must PASS
  from that stage on: each stage module's DUE.

The route file records its stage's `complete` and `due`; a route file whose record is not
its module's (when the module is there and imports) is refused, as `fixed` is. The judge
runs `judge_layout.py BOARD --complete-nets routes/complete.nets`, and reads touched.nets
and due.txt beside it. (routes/routed.nets, R1b's list of every touched net, is retired: a
zone listed there made a plane net look complete.) The generator's breakout.kicad_pro
and breakout.kicad_dru are written beside the board if they differ from the committed ones.
`route N` searches stage N, whose plan is the module hardware/gen/route/stages/sNN_<name>.py,
on top of the stages before N, and builds through N; its route file reaches routes/ only
once that build has succeeded. Both print KiCad's unrouted-connection count over the
complete nets and over the whole board.

So a placement nudge costs a rebuild, not a hand re-route: change ANCHORS in the generator,
then `build`. Copper comes only from the route files, and route files are generated: a
hand fix is written into its stage module's fixed(), which `route N` carries into the
route file (the routing plan's Global Constraints). Nobody edits copper in pcbnew. Hence
three refusals, each before anything is written:

- STALE PLACEMENT OR RULES (Review Focus 1, 2). Each route file records the sha256 of the
  placed board it was routed on (the generator's bytes, uuids seeded) and Rules.sha256 (the
  parsed .kicad_dru, the .kicad_pro's net_settings and its board-level rules after KiCad's
  defaults). A file whose hashes differ from this build's is refused, by name: its copper
  would land on moved pads or against changed clearances. Re-route that stage.
- A HAND-EDITED ROUTE FILE. A route file whose `fixed` differs from its stage module's
  fixed() is refused (when the module is there and imports): the fix belongs in the module.
- HAND EDITS ON THE BOARD (Review Focus 4). The committed board must be what the driver
  last wrote: built.json holds digests of its tracks, vias, pours (fills aside), copper
  drawings and text, footprints and everything else, taken from the file's own top-level
  blocks, and the board is compared with it. Anything else (a track drawn in pcbnew, a
  moved footprint, a filled rectangle on B.Cu) is a hand edit that a rebuild would
  silently discard, and is refused. A board with no stamp is accepted only if its copper
  equals the route files' through some stage.

A stage module defines STAGE (int), NAME (str), nets(board_nets) -> [net], zones() ->
[zone dict], layers(net) -> (layer, ...), order(nets) -> [net] and fixed() -> [{"track": ...}
| {"via": ...}] (the plan's stage interface), and may define COMPLETE and DUE (above). The
driver lays `fixed` first, adds the zones, then routes each net in `order`: pad by pad,
nearest unjoined pad first, from the pads and copper already joined, starting from the
net's fixed copper if it has some. A module may instead define route(ctx) to route its nets itself (ctx: grid, result, nets,
obstacles, rules, route_net, failed). The result is pruned of dangling copper, with every
earlier stage's copper and the extracted board's as `existing`.

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
COMPLETE_NETS = ROUTES / "complete.nets"
TOUCHED_NETS = ROUTES / "touched.nets"
DUE = ROUTES / "due.txt"
RETIRED = ROUTES / "routed.nets"          # R1b's list; removed by the next build
STAMP = ROUTES / "built.json"
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
    if p.returncode == 3:           # board_io's BoardError: a board the engine cannot model
        msg = [l for l in p.stderr.splitlines() if l.startswith("BoardError: ")]
        raise Refused((msg[-1][len("BoardError: "):] if msg else p.stderr[-2000:]))
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


def module_fixed(n: int):
    """Stage n's module's fixed(), as a route file holds it, or None if there is no module
    or it does not import."""
    try:
        mod = stage_module(n)
    except Refused:
        return None
    except Exception as e:                                   # noqa: BLE001
        say(f"note: stage {n}'s module does not import ({type(e).__name__}: {e}); its "
            f"route file's `fixed` is not checked against it")
        return None
    return RouteResult(fixed=_norm_fixed(mod.fixed())).as_dict()["fixed"]


def declared_complete(mod, touched) -> list | None:
    """A stage module's COMPLETE, resolved: a list, or complete(nets) called with the nets
    the stage touched; None when the module declares none (the default applies)."""
    c = getattr(mod, "COMPLETE", None)
    if c is None:
        return None
    return sorted(set(c(sorted(touched)) if callable(c) else c))


def bad_due(due) -> list[str]:
    """DUE entries that are neither a judge check ID nor a check/clause key, from the
    judge's own CLASSES (judge_layout imports without pcbnew; fix round 2)."""
    sys.path.insert(0, str(ROOT / "hardware" / "gen"))
    import judge_layout as jl
    keys = set(jl.CLASSES) | {k for rid in jl.CLASSES for k in jl.clause_keys(rid)}
    return sorted(d for d in set(due) if d not in keys)


def module_contract(n: int, r: RouteResult):
    """(due, complete) as stage n's module gives them for its route file `r`, or None if
    there is no module or it does not import."""
    try:
        mod = stage_module(n)
    except Exception:                                        # noqa: BLE001
        return None                       # module_fixed has already said why, if it did
    c = declared_complete(mod, r.nets)
    return (sorted(set(getattr(mod, "DUE", []))),
            sorted(r.track_nets()) if c is None else c)


def _norm_fixed(fixed) -> list:
    return [{"track": norm_track(f["track"])} if "track" in f else {"via": norm_via(f["via"])}
            for f in fixed]


def check_fresh(files: dict[int, Path], placement: str, rules: str,
                check_fixed: bool = True) -> list[RouteResult]:
    """Refuse a route file recorded against another placement or other rules, or whose
    `fixed` is not its stage module's fixed()."""
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
        want = module_fixed(n) if check_fixed else None
        if want is not None and r.as_dict()["fixed"] != want:
            raise Refused(
                f"{rel}'s `fixed` is not what stage {n}'s module's fixed() gives. Route "
                f"files are generated and never edited by hand: put the fix in the "
                f"module's fixed(), then route_breakout.py route {n}")
        contract = module_contract(n, r) if check_fixed else None
        if contract is not None:
            due, complete = contract
            got = (sorted(set(r.due)), r.complete_nets())
            if got != (due, complete):
                what = [k for k, a, b in (("DUE", got[0], due), ("COMPLETE", got[1], complete))
                        if a != b]
                raise Refused(
                    f"{rel}'s {' and '.join(what)} is not what stage {n}'s module now "
                    f"declares (route file: due {got[0]}, complete {got[1]}; module: due "
                    f"{due}, complete {complete}). The route file records the stage's "
                    f"contract; route it again: route_breakout.py route {n}")
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


# ---- the stamp: what the driver last wrote ----------------------------------------------------
_COPPER = re.compile(r'\(layers? [^()]*"(F|B|In\d+)\.Cu"')
REPLAYABLE = {"segment": "tracks", "via": "vias", "zone": "zones"}


def _top_blocks(text: str) -> list[str]:
    """The top-level children of a (kicad_pcb ...) file, quote-aware."""
    out, depth, start, i, inq, n = [], 0, 0, 0, False, len(text)
    while i < n:
        c = text[i]
        if inq:
            if c == "\\":
                i += 1
            elif c == '"':
                inq = False
        elif c == '"':
            inq = True
        elif c == "(":
            depth += 1
            if depth == 2:
                start = i
        elif c == ")":
            if depth == 2:
                out.append(text[start:i + 1])
            depth -= 1
        i += 1
    return out


def _strip_fills(block: str) -> str:
    """A zone without its (filled_polygon ...) children: a refill is no hand edit."""
    out, k = [], 0
    while True:
        j = block.find("(filled_polygon", k)
        if j < 0:
            out.append(block[k:])
            return "".join(out)
        out.append(block[k:j])
        depth, m = 0, j
        while True:
            if block[m] == "(":
                depth += 1
            elif block[m] == ")":
                depth -= 1
                if depth == 0:
                    break
            m += 1
        k = m + 1


def board_digests(text: str) -> tuple[dict, dict, list[str]]:
    """Digests of a board file's top-level blocks by kind: tracks (segments and arcs), vias,
    zones (fills aside), copper drawings (any other item on a copper layer: shapes, text,
    dimensions), footprints, other. Returns (digests, counts, the copper items the driver
    cannot replay, as kind and layer)."""
    groups = {k: [] for k in ("tracks", "vias", "zones", "copper drawings", "footprints",
                              "other")}
    odd = []
    for b in _top_blocks(text):
        kind = re.match(r"\(([A-Za-z_]+)", b).group(1)
        if kind == "footprint":
            groups["footprints"].append(b)
        elif kind in REPLAYABLE:
            groups[REPLAYABLE[kind]].append(_strip_fills(b) if kind == "zone" else b)
        elif kind == "arc":
            groups["tracks"].append(b)
            odd.append("an arc track")
        elif _COPPER.search(b) and kind not in ("version", "generator", "generator_version",
                                                 "general", "paper", "layers", "setup",
                                                 "net", "embedded_fonts"):
            groups["copper drawings"].append(b)
            layer = _COPPER.search(b).group(0).split('"')[1]
            odd.append(f"a {kind} on {layer}")
        else:
            groups["other"].append(b)
    dig = {k: hashlib.sha256("\0".join(sorted(v)).encode()).hexdigest()
           for k, v in groups.items()}
    return dig, {k: len(v) for k, v in groups.items()}, odd


def write_stamp(board: Path, files: dict[int, Path], through: int) -> None:
    dig, counts, _ = board_digests(board.read_text())
    STAMP.write_text(json.dumps({
        "schema": "wl-sync-route-built", "version": 1, "board": board.name,
        "through": through,
        "route_files": {files[n].name: hashlib.sha256(files[n].read_bytes()).hexdigest()
                        for n in sorted(files)},
        "digests": dig, "counts": counts}, indent=1, sort_keys=True) + "\n")


def check_committed(files: dict[int, Path]) -> None:
    """Refuse a committed board that is not what the driver last wrote (built.json); with
    no stamp, one whose copper no prefix of the route files holds."""
    if not BOARD.exists():
        return
    dig, counts, odd = board_digests(BOARD.read_text())
    if STAMP.exists():
        stamp = json.loads(STAMP.read_text())
        changed = [k for k in dig if stamp["digests"].get(k) != dig[k]]
        if not changed:
            say(f"committed board: as the driver last wrote it (through stage "
                f"{stamp['through']}); no hand edits")
            return
        delta = ", ".join(f"{k} ({stamp['counts'].get(k)} -> {counts[k]})" for k in changed)
        why = (f" It holds copper the driver cannot replay: {', '.join(sorted(set(odd)))}."
               if odd else "")
        raise Refused(
            f"hand edits: {BOARD.relative_to(ROOT)} is not what the driver last wrote "
            f"({STAMP.relative_to(ROOT)}); these differ: {delta}.{why} A rebuild would "
            f"discard them. Put a copper fix in its stage module's fixed() and route that "
            f"stage; move a part in the generator's ANCHORS; or restore the board with git. "
            f"Then rebuild.")
    if odd:
        raise Refused(f"{BOARD.relative_to(ROOT)} holds copper the driver cannot replay: "
                      f"{', '.join(sorted(set(odd)))}")
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
            say(f"committed board: no stamp; its copper is the route files' through stage {n}")
            return
    n, want = min(cands, key=lambda c: sum(len(set(c[1][k]) ^ set(have[k])) for k in have))
    extra = {k: len(set(have[k]) - set(want[k])) for k in have}
    missing = {k: len(set(want[k]) - set(have[k])) for k in have}
    raise Refused(
        f"unreplayed copper: {BOARD.relative_to(ROOT)} has no stamp ({STAMP.name}), and its "
        f"copper is not the route files' through any stage (nearest: through {n}; on the "
        f"board only {extra}; in the route files only {missing}). Restore the board with "
        f"git, or rebuild from a clean board.")


def contract_of(results: list[RouteResult]) -> dict[str, list[str]]:
    """The stage contract of the stages replayed: complete, touched and due, each the union
    over the stages."""
    return {"complete": sorted({n for r in results for n in r.complete_nets()}),
            "touched": sorted({n for r in results for n in r.copper_nets()}),
            "due": sorted({d for r in results for d in r.due})}


def _lines(xs) -> str:
    return "".join(x + "\n" for x in xs)


def write_outputs(built: Path, contract: dict, files: dict[int, Path], through: int) -> None:
    ROUTES.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, BOARD)
    COMPLETE_NETS.write_text(_lines(contract["complete"]))
    TOUCHED_NETS.write_text(_lines(contract["touched"]))
    DUE.write_text(_lines(contract["due"]))
    if RETIRED.exists():
        RETIRED.unlink()
        say(f"removed {RETIRED.relative_to(ROOT)} (retired: see complete.nets)")
    write_stamp(BOARD, files, through)
    for name in ("breakout.kicad_pro", "breakout.kicad_dru"):
        src, dst = PLACED / name, BREAKOUT / name
        if not dst.exists() or src.read_bytes() != dst.read_bytes():
            shutil.copyfile(src, dst)
            say(f"wrote {dst.relative_to(ROOT)} (the generator's changed)")


def replay(files: dict[int, Path], placement: str, rules: str, through: int | None,
           promote: tuple[Path, Path] | None = None) -> int:
    """Replay the route files through `through` onto the placed board and write the
    outputs, after the refusals. `promote` (a new route file's build path, and its place in
    routes/) is moved into routes/ only once the apply has succeeded."""
    use = {n: f for n, f in files.items() if through is None or n <= through}
    results = check_fresh(use, placement, rules)
    check_committed(files)
    contract = contract_of(results)
    nets_file = BUILD / "complete.nets"
    nets_file.write_text(_lines(contract["complete"]))
    built = BUILD / "out" / BOARD.name
    t = time.time()
    got = json.loads(kicad("apply", str(PLACED / BOARD.name), str(built), str(nets_file),
                           *[str(use[n]) for n in sorted(use)]).strip().splitlines()[-1])
    if promote is not None:
        os.replace(*promote)
        use = {n: (promote[1] if f == promote[0] else f) for n, f in use.items()}
        say(f"wrote {promote[1].relative_to(ROOT)}")
    write_outputs(built, contract, use, max(use, default=0))
    stages = ", ".join(use[n].name for n in sorted(use)) or "none"
    say(f"replayed {len(use)} route file(s) ({stages}) in {time.time() - t:.0f} s; "
        f"wrote {BOARD.relative_to(ROOT)}, {COMPLETE_NETS.name} "
        f"({len(contract['complete'])} nets), {TOUCHED_NETS.name} "
        f"({len(contract['touched'])} nets) and {DUE.name} ({len(contract['due'])} entries)")
    say(f"connectivity: complete nets {got['routed']} unconnected over {got['nets']} nets; "
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


def route_net(grid, result: RouteResult, net: str, layers: tuple, earlier=frozenset(),
              **kw) -> list[str]:
    """Join `net`'s pads on `layers`: the nearest unjoined pad each time, from the pads and
    copper joined so far. A net with fixed copper of this stage starts from that copper
    (every pad is then a goal); otherwise from its first pad by name. Returns the pads it
    failed to reach. Refuses a net in `earlier` (copper from an earlier stage), which this
    simple loop cannot tell joined from not."""
    from hardware.gen.route.search import Copper, route_connection
    if net in earlier:
        raise Refused(f"net {net} has copper from an earlier stage; the default loop joins "
                      f"only nets new to this stage (define route(ctx) in the stage module)")
    pads = sorted((p for p in grid.pads if p.net == net),
                  key=lambda p: (p.ref, len(p.num), p.num))

    def laid():
        return [i for i in grid.copper_objs(net) if grid.objs[i].kind != "pad"]
    anchors = []                    # where this stage's fixed copper of the net lies
    for f in result.fixed:
        if "track" in f and f["track"]["net"] == net:
            anchors += [tuple(q) for q in f["track"]["pts"]]
        if "via" in f and f["via"]["net"] == net:
            anchors.append(tuple(f["via"]["at"]))
    if len(pads) < 2 and not anchors:
        return []
    if anchors:
        joined, left = [], list(pads)
    else:
        joined, left = [pads[0]], pads[1:]
    failed = []
    while left:
        left.sort(key=lambda p: (min(math.dist(p.at, q) for q in
                                     [j.at for j in joined] + anchors), p.ref,
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
    earlier_nets = set()
    for r in earlier:
        grid.add_copper(r)
        cu = _copper_of(r)
        earlier_nets |= {c["net"] for c in cu["tracks"] + cu["vias"]}
    res = RouteResult(stage=n, name=mod.NAME, placement_sha256=placement,
                      rules_sha256=rules.sha256)
    res.fixed = _norm_fixed(mod.fixed())
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
                              route_net=route_net, failed=failed, earlier_nets=earlier_nets)
        mod.route(ctx)
    else:
        for net in mod.order(nets):
            bad = route_net(grid, res, net, tuple(mod.layers(net)), earlier_nets)
            if bad:
                failed[net] = bad
    say(f"stage {n} {mod.NAME}: {len(nets)} nets searched in {time.time() - t:.0f} s")
    pruned, gone = prune_dangling(res, grid.pads, existing=list(earlier) + [obs])
    say(f"pruned: {gone}")
    # the stage contract (task R2, fix round 1), recorded in the route file
    c = declared_complete(mod, pruned.nets)
    unknown = sorted(set(c or []) - set(board_nets))
    if unknown:
        raise Refused(f"stage {n}: COMPLETE names {unknown}, which are not nets of the board")
    pruned.complete = c
    pruned.due = sorted(set(getattr(mod, "DUE", [])))
    return pruned, failed


def route(n: int) -> int:
    mod = stage_module(n)
    wrong = bad_due(getattr(mod, "DUE", []))
    if wrong:                            # before the search, not after it (fix round 2)
        raise Refused(f"stage {n}: DUE names {wrong}, which are neither a check ID nor a "
                      f"check/clause key of judge_layout.py (it prints each clause's key)")
    placement, rules_sha = place()
    files = route_files()
    later = sorted(k for k in files if k > n)
    if later:
        raise Refused(f"stage files after {n} exist ({[files[k].name for k in later]}): they "
                      f"were routed on the old stage {n}'s copper. Delete them, route {n}, "
                      f"then re-route them after it.")
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
    new = BUILD / f"stage-{n:02d}.json"
    new.write_text(result.to_json())
    say(f"routed stage {n}: {len(result.tracks)} tracks, {len(result.vias)} vias, "
        f"{len(result.zones)} zones, {len(result.fixed)} fixed")
    files = dict(files)
    files[n] = new
    return replay(files, placement, rules_sha, n, promote=(new, ROUTES / new.name))


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
