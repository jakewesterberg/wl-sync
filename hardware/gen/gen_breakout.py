"""Generator for hardware/breakout/breakout.kicad_sch -- the root sheet of the main
breakout board (docs/superpowers/plans/2026-08-13-breakout-pcb.md, Task 6). Places the
ten hierarchical sheet symbols the plan's file-structure table names
(hardware/breakout/sheets/*.kicad_sch, one per functional block) and nothing else: no
components, no nets, no lib_symbols. Tasks 7-12 each own filling in ONE child sheet file
(and, per the plan's own file list, creating it -- see the report for why this generator
does not pre-create ten empty placeholder files: confirmed empirically that a `(sheet
...)` referencing a not-yet-existing child file produces zero ERC violations, so nothing
here needs the file to exist to satisfy this task's own verification step, and creating
it anyway would just be scope this task's own brief does not list).

Uses kicad_sch.py's Sch.sheet() (added at this task) the same way gen_mule.py uses
Sch.place()/label(): compute grid coordinates, call the helper, let it emit the real
s-expression shape.
"""
from pathlib import Path

from kicad_sch import Sch, write_project_stub

OUT = Path(__file__).resolve().parent.parent / "breakout"

SHEET_NAMES = [
    "power", "taskpc-digital", "pi-interface", "analog-frontend", "analog-ni",
    "mux-intan", "comparators", "opto-ni", "opto-intan", "control-usb-i2c",
]
assert len(SHEET_NAMES) == 10

BOX_W, BOX_H = 76.2, 38.1
X0, Y0 = 20.32, 20.32
COL_PITCH, ROW_PITCH = 88.9, 60.96
N_COLS = 5


def build() -> Sch:
    sch = Sch(project="breakout")
    for i, name in enumerate(SHEET_NAMES):
        col, row = i % N_COLS, i // N_COLS
        x = X0 + col * COL_PITCH
        y = Y0 + row * ROW_PITCH
        sch.sheet(name, f"sheets/{name}.kicad_sch", x, y, BOX_W, BOX_H)
    return sch


def main():
    sch = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "breakout.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT / "breakout.kicad_pro")
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"{len(SHEET_NAMES)} sheet symbols placed: {SHEET_NAMES}")


if __name__ == "__main__":
    main()
