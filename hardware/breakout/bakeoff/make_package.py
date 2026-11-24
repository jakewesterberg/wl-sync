"""Build the NI-island bake-off package (task R1c, Step 1, 2026-11-24).

Run with KiCad's Python, from anywhere:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hardware/breakout/bakeoff/make_package.py [OUT_DIR] [--panel-class]

OUT_DIR defaults to hardware/breakout/bakeoff/ni-island. It writes a self-contained KiCad
project that a cloud router can open:

- breakout.kicad_pcb: hardware/breakout/breakout.kicad_pcb with every pad outside the NI
  island set to no net, the island copy of the NI trial (make_island.py there) and of R1a.
  The island nets are the nets every one of whose pads lies in x 0..70, y 100..240.
  Every footprint, slot, rule area and the outline are unchanged. No track, via or pour:
  the routers start unrouted, as our engine did. The script refuses a board that has any.
- island.nets.txt: the island's nets, one per line (the judge's --nets).
- breakout.kicad_dru: copied unchanged.
- breakout.kicad_pro: the repository's, for routers that read net classes and not the
  .kicad_dru (DeepPCB uploads only the .kicad_pcb and .kicad_pro), with the three
  board-level clearances KiCad otherwise defaults (copper to edge 0.5 mm, to a hole
  0.25 mm, hole to hole 0.25 mm) written out. N-4's width and N-14's via are already its
  net classes.

  --panel-class adds a net class PANEL (0.6 mm clearance) holding the island's N-13 nets.
  It is off by default because it makes the island unroutable: a net class's clearance
  holds on every layer, N-13's on the outer layers only, and at 0.6 mm on every layer no
  0.2 mm track passes between two J45 pads (2.54 mm pitch, 1.4 mm pads: 1.14 mm between
  them, 0.2 + 2 x 0.6 = 1.4 mm needed), so 11 of the 12 panel nets on J45's two inner rows,
  boxed in by J45's own pads, cannot be reached on any layer (the twelfth, pin 2, is at its
  row's end). With it, our engine fails exactly those 11 escapes (task R1c report).
  RULES.md gives N-13 in words instead.
- fp-lib-table, footprints/<nickname>.pretty: every footprint the board uses, copied from
  its library (KiCad's stock libraries and hardware/lib/wl-sync.pretty), under the same
  nickname, so the board's footprint ids resolve through ${KIPRJMOD} alone.
- sym-lib-table, symbols/wl-sync.kicad_sym: the same for the project's own symbol library.

RULES.md is written by hand, not by this script.
"""
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BREAKOUT = REPO / "hardware" / "breakout"
sys.path.insert(0, str(REPO))
from hardware.gen.route.rules import Rules  # noqa: E402  (pure Python)

STOCK_FP = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")
LOCAL_FP = {"wl-sync": REPO / "hardware" / "lib" / "wl-sync.pretty"}
REGION = (0.0, 100.0, 70.0, 240.0)          # x0, y0, x1, y1 (mm): the NI island
CONNECTIONS = 144                           # routing-brief.md §1
PANEL_CLEARANCE = 0.6                       # N-13
BOARD_RULES = {"min_copper_edge_clearance": 0.5, "min_hole_clearance": 0.25,
               "min_hole_to_hole": 0.25}    # KiCad 10.0.5's defaults (rules.py KICAD_DEFAULTS)


def island_copy(src: Path, dst: Path) -> list[str]:
    b = pcbnew.LoadBoard(str(src))
    tracks = len(list(b.GetTracks()))
    pours = [z.GetZoneName() for z in b.Zones() if not z.GetIsRuleArea()]
    if tracks or pours:
        raise SystemExit(f"{src} has {tracks} tracks/vias and pours {pours}: the package "
                         "must start unrouted")
    mm = pcbnew.ToMM
    pads = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname():
                pads.setdefault(p.GetNetname(), []).append(p.GetPosition())
    x0, y0, x1, y1 = REGION
    inside = lambda v: x0 <= mm(v.x) <= x1 and y0 <= mm(v.y) <= y1  # noqa: E731
    island = sorted(n for n, ps in pads.items() if all(inside(v) for v in ps))
    keep = set(island)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() and p.GetNetname() not in keep:
                p.SetNetCode(0)
    b.BuildConnectivity()
    unc = b.GetConnectivity().GetUnconnectedCount(False)
    if unc != CONNECTIONS:
        raise SystemExit(f"island copy has {unc} unrouted connections, not {CONNECTIONS}")
    pcbnew.SaveBoard(str(dst), b)
    for ext in (".kicad_pro", ".kicad_prl"):     # SaveBoard may write its own project
        p = dst.with_suffix(ext)
        if p.exists() and ext == ".kicad_prl":
            p.unlink()
    return island


def project(island: list[str], panel_class: bool) -> dict:
    pro = json.loads((BREAKOUT / "breakout.kicad_pro").read_text())
    ns = pro["net_settings"]
    rules = pro["board"]["design_settings"]["rules"]
    for k, v in BOARD_RULES.items():
        rules.setdefault(k, v)
    if not panel_class:
        return pro
    panel = sorted(set(island) & Rules.load(BREAKOUT).panel_nets)
    taken = [p["pattern"] for p in ns["netclass_patterns"] if p["pattern"] in panel]
    if taken:
        raise SystemExit(f"N-13 nets already in a class: {taken}")
    default = next(c for c in ns["classes"] if c["name"] == "Default")
    cls = dict(default, name="PANEL", clearance=PANEL_CLEARANCE)
    ns["classes"].append(cls)
    ns["netclass_patterns"] += [{"netclass": "PANEL", "pattern": n} for n in panel]
    return pro


def footprint_ids(board: Path) -> list[tuple[str, str]]:
    ids = set(re.findall(r'^\t\(footprint "([^":]+):([^"]+)"', board.read_text(), re.M))
    return sorted(ids)


def main(out: Path, panel_class: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pcb = out / "breakout.kicad_pcb"
    island = island_copy(BREAKOUT / "breakout.kicad_pcb", pcb)
    (out / "island.nets.txt").write_text("\n".join(island) + "\n")
    shutil.copyfile(BREAKOUT / "breakout.kicad_dru", out / "breakout.kicad_dru")
    (out / "breakout.kicad_pro").write_text(json.dumps(project(island, panel_class), indent=2) + "\n")

    fpdir = out / "footprints"
    if fpdir.exists():
        shutil.rmtree(fpdir)
    nicks = {}
    for nick, name in footprint_ids(pcb):
        src = LOCAL_FP.get(nick, STOCK_FP / f"{nick}.pretty") / f"{name}.kicad_mod"
        if not src.exists():
            raise SystemExit(f"footprint {nick}:{name} not found at {src}")
        d = fpdir / f"{nick}.pretty"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / src.name)
        nicks.setdefault(nick, 0)
        nicks[nick] += 1
    lines = ["(fp_lib_table", "\t(version 7)"]
    for nick in sorted(nicks):
        lines.append(f'\t(lib (name "{nick}") (type "KiCad") '
                     f'(uri "${{KIPRJMOD}}/footprints/{nick}.pretty") (options "") '
                     f'(descr "copied from its library for the bake-off package"))')
    (out / "fp-lib-table").write_text("\n".join(lines + [")"]) + "\n")

    symdir = out / "symbols"
    symdir.mkdir(exist_ok=True)
    shutil.copyfile(REPO / "hardware" / "lib" / "wl-sync.kicad_sym", symdir / "wl-sync.kicad_sym")
    sym = (BREAKOUT / "sym-lib-table").read_text().replace(
        "${KIPRJMOD}/../lib/wl-sync.kicad_sym", "${KIPRJMOD}/symbols/wl-sync.kicad_sym")
    if "../lib" in sym:
        raise SystemExit("sym-lib-table still names ../lib")
    (out / "sym-lib-table").write_text(sym)

    n = sum(nicks.values())
    print(f"{pcb}: {len(island)} island nets, {CONNECTIONS} connections; "
          f"{n} footprints from {len(nicks)} libraries")
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
            print(f"  {f.relative_to(out)} sha256 {hashlib.sha256(f.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--panel-class"]
    main(Path(args[0]).resolve() if args else HERE / "ni-island", "--panel-class" in sys.argv[1:])
