"""Counts behind bakeoff-report.md §2 and §3 (task R1c write-up, fix round 1, 2026-11-24).

Run with KiCad's Python from the repository root, on any candidate boards:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hardware/breakout/bakeoff/count_drc.py hardware/breakout/bakeoff/results/*/breakout.kicad_pcb

For each board it prints one JSON object:

- `tracks_by_layer`: track pieces per copper layer, by layer id under KiCad's canonical name.
  Quilter's export renames In1.Cu and In2.Cu ("Ground Layer 1", "Power Layer 2"); the id does
  not change. Also `vias`.
- `inner_signal_mm`: signal track length (every net but the plane nets NI_GND and NI_5V) on
  In1.Cu and In2.Cu, split at y = J45_Y (226 mm) into `outside_j45` (y < 226) and `j45` (y >= 226),
  each track clipped at the line. The J45 part is R1d's exempt residue: J45's inner rows,
  where N-13 leaves no outer-layer path.
- `rule_areas`: each rule area's copper layers.
- `drc`: KiCad's DRC with the repository's rules, staged exactly as the judge stages a board
  (hardware/breakout's .kicad_pro, .kicad_dru and library tables beside it as
  breakout.kicad_pcb, `../lib` linked; `kicad-cli pcb drc --severity-all --refill-zones`).
  Its `clearance` entries are counted by the rule they name, and by where the first item
  lies, in this order, so the places do not overlap: `j45`, y >= 226; `opto_field`,
  x >= 55 and y < 226 (the optocouplers U56 to U61 and their pull-ups, x 55 to 68.75);
  `elsewhere`, the rest. KiCad's count moves a little with the zone refill, so a re-run
  can differ by a few from the judge's.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BREAKOUT = REPO / "hardware" / "breakout"
CLI = shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
J45_Y = 226.0           # J45's rows and the stage's IN2_YMIN: y >= 226 is J45's
OPTO_X = 55.0           # the optocoupler field, x >= 55 (below J45)
PLANE_NETS = frozenset({"NI_GND", "NI_5V"})


def split_at(y1: float, y2: float, length: float, cut: float = J45_Y) -> tuple[float, float]:
    """A straight track's length below (y < cut) and at or beyond (y >= cut) the line."""
    lo, hi = min(y1, y2), max(y1, y2)
    if hi <= cut:
        return length, 0.0
    if lo >= cut:
        return 0.0, length
    f = (cut - lo) / (hi - lo)
    return length * f, length * (1.0 - f)


def place(x: float, y: float) -> str:
    if y >= J45_Y:
        return "j45"
    if x >= OPTO_X:
        return "opto_field"
    return "elsewhere"


def staged_drc(board: Path) -> dict:
    import re
    work = Path(tempfile.mkdtemp(prefix="count-drc-"))
    try:
        b = work / "hardware" / "breakout"
        b.mkdir(parents=True)
        os.symlink(REPO / "hardware" / "lib", work / "hardware" / "lib")
        for name in ("breakout.kicad_pro", "breakout.kicad_dru", "fp-lib-table", "sym-lib-table"):
            shutil.copyfile(BREAKOUT / name, b / name)
        pcb = b / "breakout.kicad_pcb"
        shutil.copyfile(board, pcb)
        out = b / "drc.json"
        subprocess.run([CLI, "pcb", "drc", "--severity-all", "--refill-zones", "--format",
                        "json", "-o", str(out), str(pcb)], check=True, capture_output=True)
        d = json.loads(out.read_text())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    classes, rules, places = {}, {}, {"j45": 0, "opto_field": 0, "elsewhere": 0}
    for v in d["violations"]:
        classes[v["type"]] = classes.get(v["type"], 0) + 1
        if v["type"] != "clearance":
            continue
        m = re.search(r"rule '([^']+)'|netclass '([^']+)'", v["description"])
        r = (m.group(1) or f"netclass {m.group(2)}") if m else v["description"]
        rules[r] = rules.get(r, 0) + 1
        p = v["items"][0]["pos"]
        places[place(p["x"], p["y"])] += 1
    return {"classes": classes, "clearance_by_rule": rules, "clearance_by_place": places}


def count(board: Path) -> dict:
    import pcbnew
    b = pcbnew.LoadBoard(str(board))
    mm = pcbnew.ToMM
    canon = {pcbnew.F_Cu: "F.Cu", pcbnew.In1_Cu: "In1.Cu", pcbnew.In2_Cu: "In2.Cu",
             pcbnew.B_Cu: "B.Cu"}
    tracks, vias = {}, 0
    inner = {n: {"outside_j45": 0.0, "j45": 0.0} for n in ("In1.Cu", "In2.Cu")}
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            vias += 1
            continue
        ln = canon.get(t.GetLayer(), b.GetLayerName(t.GetLayer()))
        tracks[ln] = tracks.get(ln, 0) + 1
        if ln in inner and t.GetNetname() not in PLANE_NETS:
            lo, hi = split_at(mm(t.GetStart().y), mm(t.GetEnd().y), mm(t.GetLength()))
            inner[ln]["outside_j45"] += lo
            inner[ln]["j45"] += hi
    areas = {}
    for z in b.Zones():
        if z.GetIsRuleArea():
            areas[z.GetZoneName()] = sorted(canon[l] for l in canon if z.GetLayerSet().Contains(l))
    return {"board": str(board), "tracks_by_layer": dict(sorted(tracks.items())), "vias": vias,
            "inner_signal_mm": {k: {kk: round(vv, 1) for kk, vv in v.items()}
                                for k, v in inner.items()},
            "rule_areas": dict(sorted(areas.items())), "drc": staged_drc(board)}


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(count(Path(arg).resolve()), indent=1), flush=True)
