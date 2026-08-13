# wl-sync breakout PCB

KiCad sources for the sync-box breakout board: a 2U rack-mount mixed-signal hub that sits
between the behavioural task PC, the Raspberry Pi 5 sync box, the recording NI card, and the
Intan RHS amplifier. Every signal that crosses between those devices terminates on this board,
is conditioned once, and is fanned out — so a rig is reproducible from the board rather than
from hand wiring.

Full design rationale, the signal contract, the grounding scheme, and the fabrication schedule
live in [`docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md`](../docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md).
The implementation plan (task breakdown, net naming convention, file layout) is at
[`docs/superpowers/plans/2026-08-13-breakout-pcb.md`](../docs/superpowers/plans/2026-08-13-breakout-pcb.md).
This repository is public: documentation here states electrical and mechanical facts and
tooling instructions only, and reproduces no text from either linked document's private source
material.

## Two tracks

- **`hardware/mule/`** — the event-path mule. A small, fast board carrying only the 17-line
  event-code path plus one instance of each level-shift and isolation stage the full board
  reuses. It exists to reach the bench while the breakout board is still in layout, so the
  level-shifting and optocoupler topology gets validated against real hardware early enough to
  revise the sheets that depend on it.
- **`hardware/breakout/`** — the breakout board itself. A root sheet plus one hierarchical sheet
  per functional block (power, task-PC digital, Pi interface, analog front end, analog-to-NI,
  mux-to-Intan, comparators, opto-to-NI, opto-to-Intan, USB/I²C control).

Neither tree exists yet as of this commit — this task only sets up the toolchain and shared
library paths both tracks build on.

## Toolchain

- **KiCad 10.0.5 or later.** `kicad-cli version` prints the installed version.
- **`kicad-cli` on `PATH`.** On macOS this is a symlink to
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
- **`hardware/sym-lib-table`** — a project-scoped symbol library table registering the stock
  KiCad libraries this design draws parts from: `Device`, `74xx`, `Amplifier_Operational`,
  `Comparator`, `Isolator`, `Interface_UART`, `Connector`, `power`. Extend it, don't replace it,
  if a later sheet needs a stock library not yet listed.
- **`hardware/lib/wl-sync.kicad_sym`** and **`hardware/lib/wl-sync.pretty/`** — this project's
  own symbols and footprints for parts KiCad doesn't ship: the 68-pin MDR connector, mini-XLR
  TA4M/TA5M, the 4-pin mini-DIN, the ACCESIO D-sub, and the Pi 5 GPIO header. Both are empty
  skeletons until the symbols/footprints task populates them.

## KiCad gotchas found the hard way

These cost real debugging time to find. Recorded here so later tasks — hand-authored or
generated — don't rediscover them.

- **A project's `sym-lib-table` must sit next to its `.kicad_pro`, not just anywhere upstream.**
  KiCad does not search parent directories for it. `hardware/sym-lib-table` satisfies a project
  whose `.kicad_pro` lives directly in `hardware/`; a project in `hardware/mule/` or
  `hardware/breakout/` needs its own copy (or symlink) in that same directory, or
  `kicad-cli sch erc` reports every stock symbol as unresolved (`lib_symbol_issues`) even though
  the schematic itself is correct. Confirmed empirically: identical schematic, identical stock
  symbol, ERC clean one directory level and not the next. That per-project copy will also need
  its own `wl-sync` entry (`uri` relative to that project's `${KIPRJMOD}`, pointing back up to
  `hardware/lib/wl-sync.kicad_sym`) once a sheet places a custom symbol — this table doesn't
  register `wl-sync` yet because the library is still empty.
- **In a `.kicad_sch`, `lib_symbols` entries must be keyed by the full lib id** —
  `(symbol "Device:R"`, not `(symbol "R"`. Keyed by the bare name, KiCad loads the file without
  error but silently fails to resolve pins: they collapse to the symbol origin, and both pins of
  a two-pin part merge into a single net. There is no error message — the netlist is just wrong.
- **Child unit symbols inside that same `lib_symbols` block keep bare names** —
  `(symbol "R_0_1"`, not `(symbol "Device:R_0_1"`. Only the parent symbol entry is prefixed;
  prefixing a child unit too makes KiCad refuse to load the file at all ("Failed to load
  schematic").

## Regenerating fab outputs

Once a track has a schematic and layout, the standard `kicad-cli` invocations are:

```bash
# Electrical rules check
kicad-cli sch erc --exit-code-violations -o hardware/<track>/erc.rpt hardware/<track>/<track>.kicad_sch

# Design rules check
kicad-cli pcb drc --exit-code-violations -o hardware/<track>/drc.rpt hardware/<track>/<track>.kicad_pcb

# Gerbers and drill files
kicad-cli pcb export gerbers -o hardware/<track>/fab/ hardware/<track>/<track>.kicad_pcb
kicad-cli pcb export drill -o hardware/<track>/fab/ hardware/<track>/<track>.kicad_pcb

# Bill of materials
kicad-cli sch export bom -o hardware/<track>/<track>-bom.csv hardware/<track>/<track>.kicad_sch
```

`<track>` is `mule` or `breakout`. All of these are deterministic from the checked-in source
files, and fab outputs (`fab/`, BOM CSVs) are committed alongside them so a board can be
re-ordered without re-running layout. `hardware/.gitignore` excludes only KiCad's own transient
files — backups, lock files, the footprint-info cache — not fab outputs.
