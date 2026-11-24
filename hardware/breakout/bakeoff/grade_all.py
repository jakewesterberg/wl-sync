"""Grade every bake-off candidate blind (task R1c, Step 3, 2026-11-24).

    python3 hardware/breakout/bakeoff/grade_all.py [--results DIR] [--out DIR]

Each candidate is a directory `results/<tool>/` holding one `.kicad_pcb` (the board as the
tool returned it, or our engine's) and, optionally, `meta.json`:

    {"wall_clock_s": 118, "cost": 0, "cost_unit": "USD", "notes": "..."}

The script:

1. gives each candidate a blind label, A, B, C..., in an order drawn at random (so the
   label says nothing about the tool), and copies its board to `<out>/<label>/breakout.kicad_pcb`;
2. writes the key, label to tool, to `<out>/blind-key.json`, a file of its own. Nothing else
   it writes names a tool;
3. runs the judge in island mode on each copy, with KiCad's Python:
   `judge_layout.py <copy> --nets ni-island/island.nets.txt --region 0,100,70,240
   --json <out>/<label>/judge.json`, its printout kept as `<out>/<label>/judge.txt`. The judge
   stages each board itself as `breakout.kicad_pcb` beside the repository's own
   breakout.kicad_pro, breakout.kicad_dru and library tables, and proves the rules loaded
   (its DRC-RULES gate). So every candidate is graded against the board's real rules, not
   the package's;
4. writes `<out>/summary.md`: every gate, every check's status, and the stage protocol's
   metrics (vias; signal mm on In1.Cu and In2.Cu; plane islands and narrowest neck; routed
   length / MST; vias in pads; N-16's count), by label;
5. writes `<out>/costs.md`, wall-clock and cost by label, from each meta.json. It is kept
   apart from the summary because a cost can identify a tool (ours costs nothing): read it
   after the scoring.

It refuses an `--out` that already holds a grading, so a second run cannot re-deal the
labels over the first.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import string
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
JUDGE = REPO / "hardware" / "gen" / "judge_layout.py"
KICAD_PY = ("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
            "Versions/Current/bin/python3")
NETS = HERE / "ni-island" / "island.nets.txt"
REGION = "0,100,70,240"


def candidates(results: Path) -> dict[str, Path]:
    found = {}
    for d in sorted(p for p in results.iterdir() if p.is_dir()):
        boards = sorted(d.glob("*.kicad_pcb"))
        if len(boards) != 1:
            raise SystemExit(f"{d}: expected one .kicad_pcb, found {len(boards)}")
        found[d.name] = boards[0]
    if not found:
        raise SystemExit(f"no candidate under {results}")
    if len(found) > len(string.ascii_uppercase):
        raise SystemExit("more candidates than labels")
    return found


def deal(tools: list[str], rng=None) -> dict[str, str]:
    """label -> tool, in a random order."""
    order = list(tools)
    (rng or secrets.SystemRandom()).shuffle(order)
    return {string.ascii_uppercase[i]: t for i, t in enumerate(order)}


def judge(board: Path, outdir: Path, kicad_py: str = KICAD_PY) -> dict:
    jp = outdir / "judge.json"
    cmd = [kicad_py, str(JUDGE), str(board), "--nets", str(NETS), "--region", REGION,
           "--json", str(jp)]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    text = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                     if "Debug" not in l and "assert" not in l.lower())
    (outdir / "judge.txt").write_text(text + "\n")
    if not jp.exists():
        raise SystemExit(f"the judge wrote no JSON for {board} (exit {p.returncode}); "
                         f"see {outdir / 'judge.txt'}")
    j = json.loads(jp.read_text())
    j["_exit"] = p.returncode
    return j


# ---- the summary --------------------------------------------------------------------------
def clause_count(check: dict | None, start: str) -> str:
    """How many items a clause names: 0 when it passes (its one line is a summary)."""
    if not check:
        return "-"
    for c in check.get("clauses", []):
        if c["clause"].startswith(start):
            return "0" if c["status"] == "PASS" else (
                "n/a" if c["status"] == "N/A" else str(len(c["evidence"])))
    return check["status"]


def metric_rows(j: dict) -> list[tuple[str, str]]:
    m = j["metrics"]
    checks = {c["id"]: c for c in j["checks"]}
    inner = m.get("inner_layer_signal", {})
    rows = [("vias", str(m["vias"])),
            ("connections routed", f"{m['routed_connections']} / {m['connections']}"),
            ("signal mm on In1.Cu", str(inner.get("In1.Cu", {}).get("signal_length_mm", "-"))),
            ("signal mm on In2.Cu", str(inner.get("In2.Cu", {}).get("signal_length_mm", "-")))]
    pours = sorted(m.get("pours", []), key=lambda p: (p["layer"], p["net"]))
    if not pours:
        rows.append(("plane islands / narrowest neck", "no pour"))
    for p in pours:
        rows.append((f"{p['net']} on {p['layer']}: islands / neck mm",
                     f"{p['islands']} / {p['neck_mm']}"))
    ln = m.get("length", {})
    rows.append(("routed length / MST",
                 f"{ln.get('ratio')} ({ln.get('signal_track_mm')} / {ln.get('mst_lower_bound_mm')} mm)"))
    rows.append(("vias in SMD pads (N-16 clause 1)", clause_count(checks.get("N-16"), "no via")))
    rows.append(("vias within 0.2 mm of an SMD pad (N-16)", clause_count(checks.get("N-16"), "every via")))
    lc = m.get("layer_changes", {})
    rows.append(("vias per routed net, mean / max", f"{lc.get('mean_vias_per_routed_net')} / {lc.get('max')}"))
    return rows


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def summary(results: dict[str, dict]) -> str:
    labels = sorted(results)
    L = ["# NI-island bake-off: blind grading", "",
         f"Judge: `hardware/gen/judge_layout.py --nets ni-island/island.nets.txt --region "
         f"{REGION}`, against the repository's own rules. Candidates: {', '.join(labels)}. "
         "The key is in blind-key.json; wall-clock and cost are in costs.md.", "",
         "## Result", ""]
    L += table(["", *labels], [["judge exit (0: every gate and check PASS or N/A)",
                                *[str(results[l]["_exit"]) for l in labels]],
                               ["not passing", *[", ".join(results[l]["not_passing"]) or "-"
                                                 for l in labels]]])
    L += ["", "## Gates", ""]
    ids = [g["id"] for g in results[labels[0]]["gates"]]
    rows = []
    for gid in ids:
        rows.append([gid, *[next((g["status"] for g in results[l]["gates"] if g["id"] == gid), "-")
                            for l in labels]])
    rows.append(["DRC classes", *[next((g["evidence"][0] for g in results[l]["gates"]
                                        if g["id"] == "DRC" and g["evidence"]), "-")
                                  .replace("every class: ", "") for l in labels]])
    rows.append(["connectivity count", *[next((g["evidence"][0] for g in results[l]["gates"]
                                         if g["id"] == "CONNECTIVITY" and g["evidence"]), "-")
                                   for l in labels]])
    L += table(["Gate", *labels], rows)
    L += ["", "## Every check", ""]
    cids = [c["id"] for c in results[labels[0]]["checks"]]
    L += table(["Check", *labels],
               [[cid, *[next((c["status"] for c in results[l]["checks"] if c["id"] == cid), "-")
                        for l in labels]] for cid in cids])
    L += ["", "## Metrics (the stage protocol's table)", ""]
    per = {l: dict(metric_rows(results[l])) for l in labels}
    names = list(dict.fromkeys(n for l in labels for n, _ in metric_rows(results[l])))
    L += table(["Metric", *labels], [[n, *[per[l].get(n, "-") for l in labels]] for n in names])
    return "\n".join(L) + "\n"


def costs(metas: dict[str, dict]) -> str:
    labels = sorted(metas)
    L = ["# NI-island bake-off: wall-clock and cost by label", "",
         "Read after the scoring: a cost can identify a tool.", ""]
    L += table(["Label", "wall-clock s", "cost", "notes"],
               [[l, str(metas[l].get("wall_clock_s", "-")),
                 f"{metas[l].get('cost', '-')} {metas[l].get('cost_unit', '')}".strip(),
                 str(metas[l].get("notes", "")).replace("|", "/")] for l in labels])
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", type=Path, default=HERE / "results")
    ap.add_argument("--out", type=Path, default=HERE / "grading")
    ap.add_argument("--kicad-python", default=KICAD_PY)
    a = ap.parse_args(argv)
    found = candidates(a.results)
    out = a.out
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} already holds a grading; move it away first")
    out.mkdir(parents=True, exist_ok=True)
    key = deal(list(found))
    (out / "blind-key.json").write_text(json.dumps(key, indent=1) + "\n")
    results, metas = {}, {}
    for label, tool in sorted(key.items()):
        d = out / label
        d.mkdir()
        shutil.copyfile(found[tool], d / "breakout.kicad_pcb")
        mp = found[tool].parent / "meta.json"
        metas[label] = json.loads(mp.read_text()) if mp.exists() else {}
        print(f"judging {label} ...", flush=True)
        results[label] = judge(d / "breakout.kicad_pcb", d, a.kicad_python)
    (out / "summary.md").write_text(summary(results))
    (out / "costs.md").write_text(costs(metas))
    print(f"wrote {out / 'summary.md'}, {out / 'costs.md'}; key in {out / 'blind-key.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
