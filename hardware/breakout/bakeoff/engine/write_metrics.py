"""Judge an engine candidate on the NI island and write its metrics.json (task R1d).

    KiCad's python3 hardware/breakout/bakeoff/engine/write_metrics.py CANDIDATE_DIR

CANDIDATE_DIR holds breakout.kicad_pcb (run.sh's output). It runs the judge in island mode,
exactly as grade_all.py does (`judge_layout.py BOARD --nets ni-island/island.nets.txt
--region 0,100,70,240`, from the repository root), and writes beside the board:

- judge.txt, the judge's printout;
- metrics.json: {"judge": {"exit", "judge_commit", "metrics"}, "count_drc": {...}}, where
  `metrics` is the judge's metrics block as judge.json writes it, and `count_drc` is
  count_drc.py's count of the same board: tracks by layer, vias, the inner-layer signal mm
  split at y = 226 (R1d's In2.Cu metric, `In2.Cu.outside_j45`, and J45's exempt residue,
  `In2.Cu.j45`), and its re-run of the judge-staged DRC.

tests/hardware/test_route_ni_targets.py reads metrics.json. Exits with the judge's code.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BAKEOFF = HERE.parent
REPO = BAKEOFF.parents[2]
sys.path.insert(0, str(REPO))
from hardware.breakout.bakeoff import count_drc  # noqa: E402
from hardware.breakout.bakeoff.grade_all import JUDGE, NETS, REGION  # noqa: E402


def main(cand: Path) -> int:
    board = cand / "breakout.kicad_pcb"
    with tempfile.TemporaryDirectory() as td:
        jp = Path(td) / "judge.json"
        p = subprocess.run([sys.executable, str(JUDGE), str(board), "--nets", str(NETS),
                            "--region", REGION, "--json", str(jp)],
                           cwd=REPO, capture_output=True, text=True)
        text = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                         if "Debug" not in l and "assert" not in l.lower()
                         and not l.startswith("JSON: "))       # a temporary path
        (cand / "judge.txt").write_text(text.replace(str(REPO) + "/", "") + "\n")
        if not jp.exists():
            print(text)
            raise SystemExit(f"the judge wrote no JSON (exit {p.returncode})")
        j = json.loads(jp.read_text())
    commit = subprocess.run(["git", "log", "-1", "--format=%h", "--", str(JUDGE)], cwd=REPO,
                            capture_output=True, text=True).stdout.strip()
    c = count_drc.count(board)
    c.pop("board", None)
    out = {"judge": {"exit": p.returncode, "judge_commit": commit,
                     "command": f"judge_layout.py BOARD --nets "
                                f"{NETS.relative_to(REPO)} --region {REGION}",
                     "metrics": j["metrics"]},
           "count_drc": c}
    (cand / "metrics.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    m = j["metrics"]
    necks = {q["net"]: q["neck_mm"] for q in m["pours"]}
    i2 = c["inner_signal_mm"]["In2.Cu"]
    print(f"judge exit {p.returncode}; vias {m['vias']} "
          f"({m['vias_per_routed_connection']}/conn); In2.Cu outside J45 {i2['outside_j45']} "
          f"mm, J45 {i2['j45']} mm; length/MST {m['length']['ratio']}; necks {necks}; "
          f"DRC {c['drc']['classes']}")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
