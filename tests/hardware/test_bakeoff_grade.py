"""hardware/breakout/bakeoff/grade_all.py, the bake-off's blind grading (task R1c).

The judge itself needs KiCad and is replaced here by a stub that writes a judge-shaped JSON;
what is tested is the blinding, the refusals and the tables."""
from __future__ import annotations

import json
import random

import pytest

from hardware.breakout.bakeoff import grade_all as ga

TOOLS = ["deeppcb", "engine", "quilter"]


def judge_json(vias):
    return {
        "gates": [{"id": "DRC", "status": "PASS", "evidence": ["every class: silk_edge_clearance 24"]},
                  {"id": "CONNECTIVITY", "status": "PASS", "evidence": ["GetUnconnectedCount(False) = 0"]}],
        "checks": [{"id": "N-16", "status": "PASS", "evidence": [], "clauses": [
            {"clause": "no via's hole or annulus overlaps an SMD pad", "status": "PASS", "evidence": ["x"]},
            {"clause": "every via 0.2 mm from every SMD pad, its own net's included",
             "status": "FAIL", "evidence": ["v1", "v2"]}]}],
        "metrics": {"vias": vias, "connections": 144, "routed_connections": 144,
                    "inner_layer_signal": {"In1.Cu": {"signal_length_mm": 0.0},
                                           "In2.Cu": {"signal_length_mm": 60.5}},
                    "pours": [{"net": "NI_GND", "layer": "In1.Cu", "islands": 1, "neck_mm": 3}],
                    "length": {"ratio": 1.2, "signal_track_mm": 10, "mst_lower_bound_mm": 8},
                    "layer_changes": {"mean_vias_per_routed_net": 2, "max": 5}},
        "not_passing": []}


@pytest.fixture
def results(tmp_path):
    r = tmp_path / "results"
    for i, t in enumerate(TOOLS):
        (r / t).mkdir(parents=True)
        (r / t / "board.kicad_pcb").write_text(f"(kicad_pcb {t})")
        (r / t / "meta.json").write_text(json.dumps({"wall_clock_s": 100 + i, "cost": i,
                                                    "cost_unit": "credits", "notes": t,
                                                    "attempts": f"{i + 1} runs",
                                                    "setup": f"setup {t}"}))
    return r


def test_deal_labels_every_tool_once_in_a_random_order():
    seen = set()
    for seed in range(40):
        key = ga.deal(TOOLS, random.Random(seed))
        assert sorted(key) == ["A", "B", "C"] and sorted(key.values()) == TOOLS
        seen.add(tuple(key.values()))
    assert len(seen) > 1


def test_candidates_wants_exactly_one_board_per_tool(results):
    assert sorted(ga.candidates(results)) == TOOLS
    (results / "engine" / "second.kicad_pcb").write_text("")
    with pytest.raises(SystemExit, match="expected one .kicad_pcb, found 2"):
        ga.candidates(results)


def test_main_blinds_the_summary_and_keeps_the_key_apart(results, tmp_path, monkeypatch):
    calls = []

    def fake_judge(board, outdir, kicad_py, reference=None):
        calls.append(board)
        assert reference is None
        assert board.name == "breakout.kicad_pcb" and board.parent == outdir
        j = judge_json(vias=len(calls))
        j["_exit"] = 0
        return j

    monkeypatch.setattr(ga, "judge", fake_judge)
    out = tmp_path / "grading"
    assert ga.main(["--results", str(results), "--out", str(out)]) == 0
    kd = tmp_path / "grading-key"
    assert sorted(p.name for p in out.iterdir()) == ["A", "B", "C", "summary.md"]
    assert sorted(p.name for p in kd.iterdir()) == ["blind-key.json", "costs.md"]
    key = json.loads((kd / "blind-key.json").read_text())
    assert sorted(key.values()) == TOOLS and len(calls) == 3
    for label, tool in key.items():
        assert (out / label / "breakout.kicad_pcb").read_text() == f"(kicad_pcb {tool})"
    s = (out / "summary.md").read_text()
    # the fixed note on partial blinding says "our engine", and ties it to no label;
    # nothing else in the summary names a tool
    assert "The blinding is partial" in s and "setup differs" in s
    assert "\n".join(ga.BLINDING) in s
    assert not any(t in s.replace("\n".join(ga.BLINDING), "") for t in TOOLS)
    assert "| vias within 0.2 mm of an SMD pad (N-16) | 2 | 2 | 2 |" in s
    assert "| vias in SMD pads (N-16 clause 1) | 0 | 0 | 0 |" in s
    assert "| NI_GND on In1.Cu: islands / neck mm | 1 / 3 |" in s
    c = (kd / "costs.md").read_text()
    for label, tool in key.items():
        i = TOOLS.index(tool)
        assert (f"| {label} | {100 + i} | not recorded | {i} credits | {i + 1} runs | "
                f"setup {tool} | {tool} |") in c
    with pytest.raises(SystemExit, match="already holds a grading"):
        ga.main(["--results", str(results), "--out", str(out)])


def test_the_key_directory_may_not_sit_inside_the_summary_folder(results, tmp_path):
    out = tmp_path / "grading"
    with pytest.raises(SystemExit, match="must not be inside"):
        ga.main(["--results", str(results), "--out", str(out), "--key-dir", str(out / "key")])


def test_a_candidate_may_name_its_fixed_reference(results, tmp_path, monkeypatch):
    meta = results / "quilter" / "meta.json"
    m = json.loads(meta.read_text())
    m["judge_reference"] = "ni-island-region/breakout.kicad_pcb"
    meta.write_text(json.dumps(m))
    seen = {}

    def fake_judge(board, outdir, kicad_py, reference=None):
        seen[board.read_text()] = reference
        j = judge_json(vias=1)
        j["_exit"] = 0
        return j

    monkeypatch.setattr(ga, "judge", fake_judge)
    monkeypatch.setattr(ga, "HERE", tmp_path)
    (tmp_path / "ni-island-region").mkdir()
    (tmp_path / "ni-island-region" / "breakout.kicad_pcb").write_text("(kicad_pcb region)")
    assert ga.main(["--results", str(results), "--out", str(tmp_path / "g")]) == 0
    assert seen["(kicad_pcb quilter)"] == (tmp_path / "ni-island-region" / "breakout.kicad_pcb").resolve()
    assert seen["(kicad_pcb engine)"] is None and seen["(kicad_pcb deeppcb)"] is None
    m["judge_reference"] = "missing.kicad_pcb"
    meta.write_text(json.dumps(m))
    with pytest.raises(SystemExit, match="judge_reference .* does not exist"):
        ga.main(["--results", str(results), "--out", str(tmp_path / "g2")])
