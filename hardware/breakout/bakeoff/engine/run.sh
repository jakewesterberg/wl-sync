#!/bin/bash
# Our engine's bake-off candidate (task R1c): extract the package's board, route it with
# stage_ni.py (rules from the package), apply and fill, and write results/engine/:
# breakout.kicad_pcb, route-ni.json, stage.log and meta.json. Nothing else is read.
#   hardware/breakout/bakeoff/engine/run.sh [OUT_DIR]
set -euo pipefail
E=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$E/../ni-island" && pwd)
OUT=${1:-$E/../results/engine}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
NPY=${NPY:-/opt/anaconda3/bin/python}
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
T0=$(date +%s)
"$KPY" "$E/extract.py" "$PKG/breakout.kicad_pcb" "$WORK/obstacles.json" 2>&1 | grep -v -e Debug -e assert
T1=$(date +%s)
/usr/bin/time -p "$NPY" -u "$E/stage_ni.py" "$PKG" "$WORK/obstacles.json" "$OUT/route-ni.json" \
    > "$OUT/stage.log" 2>&1
T2=$(date +%s)
"$KPY" "$E/apply.py" "$PKG/breakout.kicad_pcb" "$OUT/route-ni.json" "$OUT/breakout.kicad_pcb" 2>&1 \
    | grep -v -e Debug -e assert | sed "s#$OUT/##"
T3=$(date +%s)
REAL=$(awk '/^real/ {print $2}' "$OUT/stage.log"); USER=$(awk '/^user/ {print $2}' "$OUT/stage.log")
cat > "$OUT/meta.json" <<JSON
{
  "wall_clock_s": $((T3 - T0)),
  "route_wall_clock_s": $REAL,
  "route_cpu_user_s": $USER,
  "cost": 0,
  "cost_unit": "USD",
  "load_average": "$(sysctl -n vm.loadavg)",
  "notes": "extract $((T1 - T0)) s, route $((T2 - T1)) s, apply and fill $((T3 - T2)) s; local machine"
}
JSON
grep -v slow "$OUT/stage.log"
echo "extract $((T1-T0))s  route $((T2-T1))s  apply+fill $((T3-T2))s  total $((T3-T0))s"
