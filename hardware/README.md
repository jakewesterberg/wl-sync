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

As of this commit, `hardware/mule/` carries a generated schematic (`mule.kicad_sch`, produced
by `hardware/gen/gen_mule.py`) and a generated, placed-but-unrouted board (`mule.kicad_pcb`,
produced by `hardware/gen/gen_mule_pcb.py`) — see `hardware/mule/floorplan.md` for the
placement rationale and the constraints a router must respect. `hardware/breakout/` does not
exist yet.

## Toolchain

- **KiCad 10.0.5 or later.** `kicad-cli version` prints the installed version.
- **`kicad-cli` on `PATH`.** On macOS this is a symlink to
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
- **`hardware/sym-lib-table`** — a project-scoped symbol library table registering the stock
  KiCad libraries this design draws parts from: `Device`, `74xx`, `Amplifier_Operational`,
  `Comparator`, `Isolator`, `Interface_UART`, `Connector`, `power`. Extend it, don't replace it,
  if a later sheet needs a stock library not yet listed.
- **`hardware/mule/fp-lib-table`** — the same idea, one level down and for footprints: a
  project-scoped footprint library table registering the stock KiCad `.pretty` libraries the
  layout draws footprints from. It must sit beside `mule.kicad_pro`, same constraint as
  `sym-lib-table` (see gotchas below) — a per-project copy, not the root `hardware/` directory.
- **`hardware/lib/wl-sync.kicad_sym`** and **`hardware/lib/wl-sync.pretty/`** — this project's
  own symbols and footprints for parts KiCad doesn't ship: the 68-pin MDR connector, mini-XLR
  TA4M/TA5M, the 4-pin mini-DIN, the ACCESIO D-sub, and the Pi 5 GPIO header. Both are empty
  skeletons until the symbols/footprints task populates them.
- **`hardware/gen/`** — a reusable Python framework for generating KiCad files programmatically,
  in two halves that mirror each other:
  - `kicad_sch.py` builds `.kicad_sch` files: pull a symbol's definition out of a stock KiCad
    library, place symbol instances on a grid, and attach a global label at each pin's
    connection point so connectivity is by label name rather than wire geometry. `gen_mule.py`
    uses it to produce `hardware/mule/mule.kicad_sch`; `check_mule_netlist.py` parses the
    exported netlist back out and asserts the contract nets are actually correct, not just
    ERC-clean (see the gotchas below for why that distinction matters).
  - `kicad_pcb.py` builds `.kicad_pcb` files the same way, applied to layout instead of
    schematic capture: pull a footprint's definition out of a stock KiCad `.pretty` library,
    place instances at a floorplan's coordinates, and assign each pad's net directly from the
    schematic's own exported netlist (via `check_mule_netlist.parse_netlist()`, imported rather
    than re-implemented) so the ratsnest pcbnew computes at load time matches the schematic
    exactly. `gen_mule_pcb.py` uses it to produce `hardware/mule/mule.kicad_pcb` — board
    outline, isolation slot, every footprint placed, netlist imported, but **not routed**:
    `kicad-cli` has no netlist-import or Specctra-DSN subcommand, so there is no headless path
    to routing a board from the command line. See `hardware/mule/floorplan.md` for the
    placement rationale.

  Later schematic-capture and layout tasks can import either module directly rather than
  re-deriving the technique.

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
- **Some stock symbols use `(extends "ParentName")` and carry no pin geometry of their own.**
  `74xx:74HCT541` extends `74LS541`; `Regulator_Linear:LD1117S33TR_SOT223` extends `AP1117-15`;
  `Converter_DCDC_Isolated:TMA-0512D` extends `TMA-0505D`. This is common wherever a stock
  library represents several pin-compatible parts (a logic-family variant, a regulator's
  different fixed-voltage options, a DC/DC converter's input/output voltage options) as one
  base symbol plus thin per-variant overrides of just its `Reference`/`Value`/`Footprint`/
  `Datasheet` properties. Extracting the named symbol textually (as the constraints above do)
  yields a real, well-formed block with properties but no pins — silent at generation time, and
  it is not yet known whether KiCad accepts a `lib_symbols` entry embedding the extends chain
  the way it does when the GUI places such a part. `hardware/gen/kicad_sch.py` sidesteps the
  question rather than answering it: `extract_symbol()` asserts loudly if the requested symbol
  uses `extends`, and every caller is expected to name the extends-free root symbol instead,
  overriding its `Value` property to the real ordered part number (the mule's inbound buffers
  place `74xx:74LS541` with `Value` set to `SN74LVC541APW`, since the '541 pinout is identical
  across the LS/HCT/AHC/AHCT/LVC sub-families — this is standard KiCad practice, not a
  workaround unique to generated schematics).
- **A raw newline byte inside a `(text "...")` element's quoted string makes KiCad refuse
  the whole file** ("Failed to load schematic") — found adding an on-sheet documentation
  note and initially writing it as one multi-line Python string. Isolated by testing two
  minimal cases: a single-line `text` containing literal parentheses (loads fine) against
  a two-line `text` with an embedded `\n` and no parentheses at all (fails) — so it's
  specifically the embedded newline, not "special characters" in general. `Sch.text()`
  (`hardware/gen/kicad_sch.py`) now asserts against this; call it once per line for a
  multi-line note instead.
- **A `.kicad_pcb` has no `lib_symbols`-style shared library section.** Every placed
  footprint carries its *entire* graphic definition (silkscreen, courtyard, fab-layer
  outline, pads) inline, copied verbatim from the library `.kicad_mod` file — confirmed
  against a real pcbnew-saved board before writing `hardware/gen/kicad_pcb.py`, not
  assumed from the schematic side's different behavior. Coordinates inside a placed
  footprint stay in the footprint's own local frame; only the top-level `(at X Y ROT)`
  moves and rotates it, which is the opposite of a schematic symbol's pins needing a
  manual offset for each label (`kicad_sch.py`'s `pin_pos()`).
- **A quoted string in a `.kicad_mod` file can contain literal, individually-balanced
  parentheses** — e.g. `Resistor_SMD:R_0603_1608Metric`'s own `descr` field: `"...square
  (rectangular) end terminal... (Body size source: ...)"`. A paren-depth scanner that
  doesn't treat `"..."` as opaque still happens to balance correctly here (each string's
  parens pair within themselves), which is a property of today's specific strings, not
  something safe to rely on — `hardware/gen/kicad_pcb.py`'s `_find_balanced()` is
  quote-aware for exactly this reason. (The schematic side has the identical latent gap —
  several stock symbols' `Description` fields contain literal parentheses too — untouched
  here since it has not caused an actual failure; flagged for whoever next edits
  `kicad_sch.py`.)
- **Some newer footprint-generator output uses a `(point ...)` element** (a pin-1 anchor
  marker, e.g. `Package_DIP:DIP-8_W7.62mm`) that needs the same fresh per-instance `uuid` as
  every `fp_line`/`fp_rect`/`fp_circle`/`fp_poly`/`fp_arc`/`fp_text`/`pad` child — omitting
  it produced a `lib_footprint_mismatch` DRC warning on every DIP-8 instance. Found by
  running `kicad-cli pcb drc` and reading exactly what it reported, not by inspection. It
  also needs its **position** rewritten: `point` is the one footprint child whose `(at X Y)`
  in a placed instance is in absolute BOARD coordinates rather than the footprint's local
  frame, at `rot=0` as much as under rotation. Copied through verbatim it lands at its
  local offset from the board origin — on this board, both DIP-8 markers stacked in the
  top-left corner. Established by measuring every point-bearing instance in KiCad's own
  demo boards (all in `demos/pic_programmer`): each equals the library-local point put
  through the footprint's placement transform.
- **In a placed footprint, a pad's or text element's `(at X Y ANGLE)` angle is ABSOLUTE,
  even though its X/Y stay in the footprint's local frame.** Placing a footprint at a
  non-zero rotation means writing `library angle + rotation` (normalised into 0–360) on
  every pad, `fp_text` and property, not copying the library's angles through. In KiCad's
  `demos/pic_programmer`, a `DIP-14_W7.62mm_LongPads` at `rot=90` has all 14 pads at
  `(at LX LY 90)` while the library master has no pad angle at all, and its
  `fp_text "${REFERENCE}"` (library angle 90) is written as 180; a `DSUB-9` at `rot=-90`
  writes pad angle 270. Getting this wrong is silent for a pad whose shape is invariant
  under the rotation (a circle at any angle, a square roundrect at multiples of 90°) and is
  a real geometry error otherwise — an oval, rectangular, keyed or chamfered pad is laid
  down in the wrong orientation at the right centre. Proof, from the gerbers of the same
  footprint plotted both ways at `rot=90`: `%ADD11O,1.600000X2.400000` with the offset
  applied against `%ADD11O,2.400000X1.600000` without it, i.e. the oval pad's long axis in
  the wrong direction. It is also exactly what `kicad-cli pcb drc` reports as
  `lib_footprint_mismatch` on rotated instances — the check re-orients the library master
  to the instance's rotation before comparing, so a pad left at its library angle differs
  from the master by precisely the rotation. (That check only runs at all when a project's
  `fp-lib-table` can resolve the footprint's library; an empty directory with no
  `fp-lib-table` reports nothing either way, which is what makes it easy to mis-attribute.)

## Byte-reproducibility

Regenerating `mule.kicad_sch` or `mule.kicad_pcb` from the same generator and inputs
produces a file that is **semantically identical but not byte-identical** to the one
already checked in. Every placed symbol, footprint, pad, and graphic element gets a fresh
random UUID on each run (`uuid.uuid4()`, `hardware/gen/kicad_sch.py`'s and
`kicad_pcb.py`'s `uid()`), and KiCad has no notion of a "canonical" UUID a generator could
reuse instead. A `git diff` after regenerating will show the whole file as changed even
when nothing about the design did — this is expected, not a sign the generator is broken
or non-deterministic in any way that matters: net names, pin/pad connectivity, placement
coordinates, and part values are all fully determined by the generator's own code and
inputs, and `hardware/gen/check_mule_netlist.py`'s job is exactly to verify that the
*meaning* reproduces, independent of the UUIDs a run happens to mint.

## Regenerating fab outputs

For the mule, the generators run in this order — the PCB generator reads the schematic's
own exported netlist as its source of connectivity (see `hardware/gen/gen_mule_pcb.py`'s
module docstring), so it depends on a fresh export existing, not just a fresh schematic:

```bash
python3 hardware/gen/gen_mule.py
kicad-cli sch upgrade hardware/mule/mule.kicad_sch                  # see "Format upgrade" below
kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net hardware/mule/mule.kicad_sch
python3 hardware/gen/check_mule_netlist.py hardware/mule/mule.net   # verifies the netlist, not just that ERC passed
python3 hardware/gen/gen_mule_pcb.py
kicad-cli pcb upgrade hardware/mule/mule.kicad_pcb                  # see "Format upgrade" below
```

**Format upgrade.** Each generator writes the file-format version its grammar was written
against and validated on — `20231120` for the schematic, `20241229` for the board, both
stamped `generator "wl-sync-gen"`. The committed `mule.kicad_sch` and
`mule.kicad_pcb` are the upgraded files — `kicad-cli sch upgrade` / `kicad-cli pcb upgrade`
re-save them in the current format (`generator "eeschema"` / `"pcbnew"`), which is what
KiCad itself would write the first time a person opens and saves either file. The upgrade
steps are part of the recipe, not optional polish: skip them and regeneration produces a
semantically identical file that differs from the committed artifact in its version stamp,
its generator name, and a set of formatting normalisations KiCad applies (for instance a
footprint placed at `rot=270` is re-saved as `-90`).

Order matters in one place: `gen_mule_pcb.py` reads the exported netlist for both
connectivity and the schematic cross-link UUIDs each footprint's `(path ...)` points at, so
the netlist export has to come from the same `mule.kicad_sch` that is being committed.
Export it before running the PCB generator, not after.

Then the standard `kicad-cli` invocations, once a track has a schematic and layout:

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

`hardware/mule/mule.net` itself is not committed — see `.gitignore` and the
byte-reproducibility note above for why a mechanically-regenerable, driftable-if-stale
artifact stays out of the tree the same way `mule.kicad_sch`'s netlist export always has.

`<track>` is `mule` or `breakout`. All of these are deterministic from the checked-in source
files, and fab outputs (`fab/`, BOM CSVs) are committed alongside them so a board can be
re-ordered without re-running layout. `hardware/.gitignore` excludes only KiCad's own transient
files — backups, lock files, the footprint-info cache — not fab outputs.
