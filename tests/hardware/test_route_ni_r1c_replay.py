"""NiIsland with R1c's knobs still lays R1c's copper (task R1d fix round 1).

hardware/gen/route/stages/ni_island.py is R1c's hand plan (bakeoff/engine/stage_ni.py) made
reusable, and R5 and R12 will edit it. Its R1C knob set must keep reproducing the graded
bake-off candidate: this extracts the NI-island package's board with KiCad's Python (as
engine/run.sh does), routes it with NiIsland(**R1C), and compares the route file with the
committed results/engine/route-ni.json on tracks, vias, zones, fixed, nets and
placement_sha256. (Not rules_sha256: R1b redefined it after results/engine/ was written;
results/engine/meta.json's rules_sha256_note.) It needs KiCad, like the other board tests,
so CI skips it; the route takes about a minute.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("numpy")

REPO = Path(__file__).parents[2]
BAKEOFF = REPO / "hardware" / "breakout" / "bakeoff"
KICAD_PY = Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
                "Versions/Current/bin/python3")

pytestmark = pytest.mark.skipif(
    not KICAD_PY.exists() or shutil.which("kicad-cli") is None,
    reason="needs KiCad 10's bundled Python and kicad-cli")


def test_r1c_knobs_reproduce_the_graded_candidate(tmp_path, monkeypatch):
    from hardware.gen.route.stages import ni_island
    pkg = BAKEOFF / "ni-island"
    obs = tmp_path / "obstacles.json"
    subprocess.run([str(KICAD_PY), str(BAKEOFF / "engine" / "extract.py"),
                    str(pkg / "breakout.kicad_pcb"), str(obs)], check=True,
                   capture_output=True)
    out = tmp_path / "route-ni.json"
    monkeypatch.setenv("NI_KNOBS", "R1C")
    assert ni_island.main([str(pkg), str(obs), str(out)]) == 0
    got = json.loads(out.read_text())
    want = json.loads((BAKEOFF / "results" / "engine" / "route-ni.json").read_text())
    for key in ("tracks", "vias", "zones", "fixed", "nets", "placement_sha256"):
        assert got[key] == want[key], key
