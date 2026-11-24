"""The tuned NI-island candidate's metrics, pinned (task R1d, 2026-11-24).

numpy only (no KiCad), so it runs in CI. It reads the committed candidate under
hardware/breakout/bakeoff/tuned/ (the route file route-ni.json and metrics.json, the
judge's metrics block for its board with count_drc.py's In2.Cu split beside it; written by
engine/write_metrics.py) and holds R1d's four targets (bakeoff-report.md §6) no worse than
the values R1d ended with. The pins only ever tighten.

- vias, In2.Cu signal mm outside J45's inner rows (y < 226) and routed length / MST: at or
  under their pins;
- each plane's narrowest neck: at or over its pin;
- J45's residue, In2.Cu signal mm at y >= 226, is exempt (the controller's ruling,
  2026-11-24: N-13 leaves no outer-layer path out of those rows). It is never a bound;
  metrics.json must only record it;
- the route file's own counts agree with metrics.json (vias; In2.Cu signal length on both
  sides of y = 226), so the two cannot drift apart.

The committed bake-off result, results/engine/, stays as graded in task R1c; this is R1d's.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from hardware.gen.route.stage import RouteResult  # noqa: E402

REPO = Path(__file__).parents[2]
TUNED = REPO / "hardware" / "breakout" / "bakeoff" / "tuned"
PLANE_NETS = {"NI_GND", "NI_5V"}
J45_Y = 226.0

# R1d's pins, the tuned candidate's (2026-11-24): 119 vias (target 102); In2.Cu 0 mm outside
# y >= 226 (target 0; J45's 53.4 mm is exempt); length / MST 1.076 (target 1.153); necks
# 9 / 9 mm (target 10). R1c's (results/engine/, judge c735422), pinned first: 138; 0 (60.5 at
# J45); 1.232; 5.5 / 3.
MAX_VIAS = 119
MAX_IN2_OUTSIDE_J45_MM = 0.0
MAX_LENGTH_RATIO = 1.076
MIN_NECK_MM = {"NI_GND": 9.0, "NI_5V": 9.0}


@pytest.fixture(scope="module")
def metrics():
    return json.loads((TUNED / "metrics.json").read_text())


@pytest.fixture(scope="module")
def route():
    return RouteResult.from_json((TUNED / "route-ni.json").read_text())


def neck(v) -> float:
    """The judge writes a neck as mm, or '>12' past its last step."""
    return math.inf if isinstance(v, str) and v.startswith(">") else float(v)


def test_the_judge_passed(metrics):
    assert metrics["judge"]["exit"] == 0


def test_vias_no_worse(metrics):
    assert metrics["judge"]["metrics"]["vias"] <= MAX_VIAS


def test_in2_outside_j45_no_worse(metrics):
    assert metrics["count_drc"]["inner_signal_mm"]["In2.Cu"]["outside_j45"] <= \
        MAX_IN2_OUTSIDE_J45_MM


def test_j45_residue_recorded_not_bound(metrics):
    # exempt: recorded, never pinned
    v = metrics["count_drc"]["inner_signal_mm"]["In2.Cu"]["j45"]
    assert isinstance(v, (int, float)) and v >= 0


def test_length_ratio_no_worse(metrics):
    assert metrics["judge"]["metrics"]["length"]["ratio"] <= MAX_LENGTH_RATIO


def test_plane_necks_no_narrower(metrics):
    pours = {p["net"]: p for p in metrics["judge"]["metrics"]["pours"]}
    assert set(pours) == set(MIN_NECK_MM)
    for net, pin in MIN_NECK_MM.items():
        assert neck(pours[net]["neck_mm"]) >= pin, (net, pours[net]["neck_mm"])


def _in2_split(route: RouteResult) -> tuple[float, float]:
    lo = hi = 0.0
    tracks = list(route.tracks) + [f["track"] for f in route.fixed if "track" in f]
    for t in tracks:
        if t["layer"] != "In2.Cu" or t["net"] in PLANE_NETS:
            continue
        for (x1, y1), (x2, y2) in zip(t["pts"], t["pts"][1:]):
            L = math.hypot(x2 - x1, y2 - y1)
            a, b = min(y1, y2), max(y1, y2)
            if b <= J45_Y:
                lo += L
            elif a >= J45_Y:
                hi += L
            else:
                f = (J45_Y - a) / (b - a)
                lo, hi = lo + L * f, hi + L * (1 - f)
    return lo, hi


def test_route_file_agrees_with_metrics(metrics, route):
    vias = len(route.vias) + sum(1 for f in route.fixed if "via" in f)
    assert vias == metrics["judge"]["metrics"]["vias"] == metrics["count_drc"]["vias"]
    lo, hi = _in2_split(route)
    split = metrics["count_drc"]["inner_signal_mm"]["In2.Cu"]
    assert lo == pytest.approx(split["outside_j45"], abs=0.06)
    assert hi == pytest.approx(split["j45"], abs=0.06)
    assert lo + hi == pytest.approx(
        metrics["judge"]["metrics"]["inner_layer_signal"]["In2.Cu"]["signal_length_mm"],
        abs=0.1)
