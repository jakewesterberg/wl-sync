#!/bin/bash
# Our engine's bake-off candidate (task R1c): extract the package's board, route it with
# stage_ni.py (rules from the package), apply and fill, and write results/engine/:
# breakout.kicad_pcb, route-ni.json, stage.log and meta.json. It reads only the package and
# engine/, but stage_ni.py is a hand plan for this island (its docstring; meta.json says so).
#   hardware/breakout/bakeoff/engine/run.sh [OUT_DIR]
# Task R1d: STAGE= another stage script with stage_ni.py's arguments (R1d's is
# hardware/gen/route/stages/ni_island.py), and META_ATTEMPTS, META_SETUP, META_HAND_PLAN
# for meta.json's text ("{LINES}" in one becomes the stage's line count); each defaults to
# R1c's.
set -euo pipefail
E=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$E/../ni-island" && pwd)
OUT=${1:-$E/../results/engine}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
NPY=${NPY:-/opt/anaconda3/bin/python}
STAGE=${STAGE:-$E/stage_ni.py}
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
T0=$(date +%s)
"$KPY" "$E/extract.py" "$PKG/breakout.kicad_pcb" "$WORK/obstacles.json" 2>&1 | grep -v -e Debug -e assert
T1=$(date +%s)
/usr/bin/time -p "$NPY" -u "$STAGE" "$PKG" "$WORK/obstacles.json" "$OUT/route-ni.json" \
    > "$OUT/stage.log" 2>&1
T2=$(date +%s)
"$KPY" "$E/apply.py" "$PKG/breakout.kicad_pcb" "$OUT/route-ni.json" "$OUT/breakout.kicad_pcb" 2>&1 \
    | grep -v -e Debug -e assert | sed "s#$OUT/##"
T3=$(date +%s)
REAL=$(awk '/^real/ {print $2}' "$OUT/stage.log"); USER=$(awk '/^user/ {print $2}' "$OUT/stage.log")
LINES=$(wc -l < "$STAGE" | tr -d ' ')
ATTEMPTS=${META_ATTEMPTS:-"1 run here, of a plan already tuned on this island in R1a (R1a's scratchpad keeps at least 5 earlier routing runs, run1.log to run5.log, and 4 debugging scripts)"}
SETUP=${META_SETUP:-"a hand-written stage, engine/stage_ni.py ($LINES lines): R1a's port of the Opus NI-trial router's plan, adjusted on this island, each adjustment out of a failure (task-R1a-report.md, 'The NI island regression' and concern 2). Authoring time not recorded; R1a's scratch files are dated 09:05 to 10:53 on 2026-11-24, shared with the engine's own development"}
HAND_PLAN=${META_HAND_PLAN:-"OPTOS pin/capacitor/pull-up table; NI_5V stubs J45 pins 8, 14 to y 227.6; In2.Cu signal only under J45 (y >= 226); pins 11-14 by F.Cu stub to a via within +/-0.06 mm of the pin's line; pin 16 to its bypass on F.Cu; BARCODE_NI after U60's pull-ups; exits to x < 57; L-shaped join corridors (x <= 50, widening 1.5/4/10 mm); B-13 fences and clipped straddler terminals; phase order; plane rectangles"}
# meta.json by json.dumps (R1d fix round 1): the META_* strings may hold quotes or
# backslashes; "{LINES}" in one becomes the stage's line count.
WALL=$((T3 - T0)) NOTES="extract $((T1 - T0)) s, route $((T2 - T1)) s, apply and fill $((T3 - T2)) s" \
REAL="$REAL" CPU="$USER" LINES="$LINES" ATTEMPTS="$ATTEMPTS" SETUP="$SETUP" HAND_PLAN="$HAND_PLAN" \
LOAD="$(sysctl -n vm.loadavg)" "$NPY" - "$OUT/meta.json" <<'PY'
import json, os, sys
e = os.environ
t = lambda k: e[k].replace("{LINES}", e["LINES"])
meta = {"wall_clock_s": int(e["WALL"]),
        "wall_clock_scope": "routing only: extract, route, apply and fill, on a local machine; "
                            "not the writing or tuning of the hand plan",
        "route_wall_clock_s": float(e["REAL"]), "route_cpu_user_s": float(e["CPU"]),
        "cost": 0, "cost_unit": "USD", "attempts": t("ATTEMPTS"), "setup": t("SETUP"),
        "hand_plan": t("HAND_PLAN"), "load_average": e["LOAD"], "notes": e["NOTES"]}
open(sys.argv[1], "w").write(json.dumps(meta, indent=2) + "\n")
PY
grep -v slow "$OUT/stage.log"
echo "extract $((T1-T0))s  route $((T2-T1))s  apply+fill $((T3-T2))s  total $((T3-T0))s"
