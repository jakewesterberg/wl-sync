# wl-sync breakout PCB

KiCad sources for the sync-box breakout board: a 2U rack-mount mixed-signal hub that sits
between the behavioural task PC, the sync-box module, the recording NI card, and the Intan RHS
amplifier. Every signal that crosses between those devices terminates on this board, is
conditioned once, and is fanned out — so a rig is reproducible from the board rather than from
hand wiring. "Sync-box module" is deliberately not a single product name: the module carries a
BCM2712 SoC and RP1 I/O controller either way, so its GPIO map and pinout are identical whether
it ships as a Raspberry Pi 5 or as a Compute Module 5 (Lite) on the official CM5 IO Board — the
choice between the two is a floorplan/placement decision this repository's own sources do not
yet lock in, not an electrical one.

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
placement rationale and the constraints a router must respect. `hardware/breakout/` carries
the root sheet (`breakout.kicad_sch`, produced by `hardware/gen/gen_breakout.py`) plus one
populated child sheet so far: `sheets/power.kicad_sch` (produced by
`hardware/gen/gen_breakout_power.py`, Task 7) — the analog inlet, the linear +5V/+3V3 logic
rails, and the one isolated ±12V supply the other nine sheets draw power from; see that
generator's own module docstring and `check_breakout_power_netlist.py` for the design and
its verification. The remaining nine hierarchical sheet symbols (`taskpc-digital`,
`pi-interface`, `analog-frontend`, `analog-ni`, `mux-intan`, `comparators`, `opto-ni`,
`opto-intan`, `control-usb-i2c`) each still reference a `sheets/<name>.kicad_sch` child file
that does not exist yet — later tasks each create and populate their own (see "Sheet symbols
referencing a child file that doesn't exist yet" below for why the root sheet doesn't
pre-create them).

## Toolchain

- **KiCad 10.0.5 or later.** `kicad-cli version` prints the installed version.
- **`kicad-cli` on `PATH`.** On macOS this is a symlink to
  `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
- **`hardware/sym-lib-table`** — a project-scoped symbol library table registering the stock
  KiCad libraries this design draws parts from. Each project directory's own copy is the one
  that actually matters (see gotchas below); as of Task 7, `hardware/breakout/sym-lib-table`
  registers `Device`, `74xx`, `Amplifier_Operational`, `Comparator`, `Isolator`,
  `Interface_UART`, `Connector`, `power`, and `wl-sync` (Task 6), plus `Diode`,
  `Converter_DCDC_Isolated`, and `Regulator_Linear` — added this task for the power sheet's
  reverse-polarity Schottky diodes, its one isolated DC/DC converter, and
  `LD1117S33TR_SOT223` respectively. Extend it, don't replace it, if a later sheet needs a
  stock library not yet listed.
- **`hardware/mule/fp-lib-table`** and **`hardware/breakout/fp-lib-table`** — the same idea,
  one level down and for footprints: a project-scoped footprint library table registering the
  `.pretty` libraries each project's layout draws footprints from (stock KiCad libraries, plus
  `wl-sync` once a sheet places a custom footprint). Each must sit beside its own `.kicad_pro`,
  same constraint as `sym-lib-table` (see gotchas below) — a per-project copy, not the root
  `hardware/` directory. Task 7 added `Diode_SMD`, `Inductor_SMD` (the pi filters' ferrite
  beads), and `NetTie` (the AGND/DGND star point) to `hardware/breakout/fp-lib-table`.
- **`hardware/lib/wl-sync.kicad_sym`** and **`hardware/lib/wl-sync.pretty/`** — this project's
  own symbols and footprints for parts KiCad doesn't ship: the 68-pin MDR male connector,
  mini-XLR TA4M/TA5M, the 4-pin mini-DIN, the mating DB37 for the ACCES I/O USB-AO16-8A analog
  source, the Raspberry Pi 5 GPIO header, and (Task 7) TPS7A4901/TPS7A3001 — TI's ultralow-noise
  adjustable LDO pair, eight symbols total. The Pi header, the DB37 connector, and (Task 7)
  TPS7A4901/TPS7A3001 all reuse stock footprints — a bare 2×20 2.54mm THT header, KiCad's own
  `Connector_Dsub` DB37, and a generic `Package_SO` HVSSOP-8-1EP PowerPAD, respectively —
  rather than needing new ones, so only four new `.kicad_mod` files exist:
  MDR68, mini-XLR ×2, mini-DIN). `hardware/breakout/sym-lib-table` and
  `hardware/breakout/fp-lib-table` both register `wl-sync`; `hardware/mule/`'s copies do not
  (the mule places no custom parts). See "Custom connector footprints" below for the TA-vs-TB
  naming split and the per-dimension sourcing/confidence behind the four hand-built footprints.
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

## Custom connector footprints: TA/TB naming, sourcing, and confidence

Four `wl-sync` footprints have no KiCad stock equivalent and were built from scratch:
`MDR68_Male_RightAngle`, `MiniXLR_TA4M_Panel`, `MiniXLR_TA5M_Panel`, and `MiniDIN_4_Panel`
(`hardware/lib/wl-sync.pretty/`, generated by `hardware/gen/gen_wl_sync_footprints.py`). The
design spec calls these out as the tightest tolerance on the board: the enclosure panel is
machined to their cutouts, so a footprint error scraps a panel rather than causing a rework.
This section is the sourcing record every one of those footprints' `descr` field and the two
`MiniXLR_*` symbols' `Description` property point back to — this is where that content
actually lives, not a pointer to somewhere else.

### TA4M/TA5M are the wrong part number for a panel mount

**TA4M is obsolete and cable-mount only.** The mini-XLR male connector that actually
panel/chassis-mounts is the same Switchcraft Tini-QG family's **TB4M** (4-pin) and **TB5M**
(5-pin), not TA4M/TA5M. This has been confirmed independently against DigiKey, Switchcraft's
own catalog, and Farnell: all three agree the TA-series is cable-mount only, DigiKey lists
TA4M as obsolete, and the TB-series is the family's panel/chassis-mount member. Every
mini-XLR position on this board is panel-mounted, so ordering to the TA4M/TA5M part number
would buy a connector that cannot be mounted where it needs to go.

**The footprint models TB4M/TB5M. The symbol is still named `MiniXLR_TA4M`/`MiniXLR_TA5M`.**
That split is deliberate, not an inconsistency to fix:

- The **footprint** determines what actually gets soldered down and what panel cutout gets
  machined, so it has to model the mechanically real, orderable, panel-mountable part —
  TB4M/TB5M. Modelling TA4M/TA5M's own cable-mount dimensions for a panel position would be
  the real error, since TA4M/TA5M cannot panel-mount at all.
- The **symbol** keeps the `TA4M`/`TA5M` name because that is the identifier the spec, the
  plan, and any BOM or procurement step already use for this board position. Renaming the
  symbol would break that continuity for no electrical benefit: pin count and function are
  identical between the TA- and TB-series members of the family, only mounting style differs
  — and mounting style is exactly what the footprint, not the symbol, is responsible for.

Procurement should order **TB4M/TB5M**, not TA4M/TA5M, regardless of which name appears on
the schematic symbol.

### Per-dimension sourcing and confidence

| Footprint | Dimension | Value | Source | Confidence |
|---|---|---|---|---|
| `MDR68_Male_RightAngle` | Overall envelope | 63.86×16.7×12.5mm | MH Connectors 3700-0121-01 — a real, currently-distributed right-angle male 68-pin MDR/SCSI-3 connector | High — corroborated across 5+ independent distributor listings |
| `MDR68_Male_RightAngle` | Contact pitch | 1.27mm | MDR/SCSI-3 format's own defining dimension (3M "102 Series" datasheet: ".050in Boardmount") | High — format-defining; every MDR68 datasheet checked agrees |
| `MDR68_Male_RightAngle` | Row layout | 2 rows × 34 contacts | Same as pitch | High |
| `MDR68_Male_RightAngle` | Row spacing | 2.84mm | General MDR-68 dimensional references (multiple, mutually consistent secondary sources) | **Low — not confirmed against a primary numeric CAD drawing for this specific MPN** |
| `MDR68_Male_RightAngle` | Mounting-hole (jackscrew) spacing | 57.9mm | Same as row spacing | **Low — same caveat** |
| `MDR68_Male_RightAngle` | Mounting-hole size | 2.79mm (4-40 UNC clearance) | Standard 4-40 UNC clearance-hole practice | Medium |
| `MiniXLR_TA4M_Panel` / `MiniXLR_TA5M_Panel` | Panel/chassis thickness accommodated | 6.35mm max | Switchcraft's own published number for the TB-series panel-mount receptacle | High — primary source, correct part series |
| `MiniXLR_TA4M_Panel` / `MiniXLR_TA5M_Panel` | Panel bushing / cutout diameter | ~10.9mm | Derived from the TA-series housing diameter (0.413in/10.5mm, Switchcraft catalog) plus one distributor's TB5M listing (~10.7mm) | **Low — the least-certain dimension of any of the four footprints; no primary TB-series numeric drawing found** |
| `MiniXLR_TA4M_Panel` / `MiniXLR_TA5M_Panel` | Contact-circle diameter | 5.5mm | Modelled, not measured off the real part | Medium |
| `MiniXLR_TA4M_Panel` / `MiniXLR_TA5M_Panel` | Contact angular arrangement | Evenly spaced around the full circle | Approximation — see "Contact arrangement" below | **Low — possible topology error, not just a tolerance one** |
| `MiniDIN_4_Panel` | Through-panel bushing diameter | 10.0mm | Same Sky (CUI) MD-SN series datasheet, MD-40SN row — a real, dimensioned manufacturer CAD drawing | High — best-sourced dimension of any of the four footprints |
| `MiniDIN_4_Panel` | Panel flange envelope | 38.5×15.25mm | Same drawing | High (panel-side reference only, not part of the PCB footprint itself) |
| `MiniDIN_4_Panel` | Mounting-ear spacing / diameter | 30.0mm centres, 3.05mm dia | Same drawing | High (ditto — panel-side reference only) |
| `MiniDIN_4_Panel` | Contact-circle diameter | 7.0mm | Mini-DIN family's own standard geometry (the connector class only works because this is fixed across manufacturers) | High |
| `MiniDIN_4_Panel` | Contact angular arrangement | Evenly spaced around the full circle | Approximation, not measured off the manufacturer drawing's own small pin diagram — see "Contact arrangement" below | **Low — possible topology error, not just a tolerance one** |

Neither mini-XLR footprint nor the mini-DIN footprint drills a PCB mounting hole: all three
are panel-mount parts whose own flange or bushing nut carries the mechanical load against the
sheet-metal panel, not the board, so the PCB footprint needs only solder pads.
`MDR68_Male_RightAngle` is the exception and does get two mechanical mounting holes, matching
every real MDR/SCSI connector datasheet's own board-lock-post convention (it is a PCB-mount
part, not panel-mount).

### Contact arrangement: a possible topology error, not just a tolerance one

The mini-XLR (`MiniXLR_TA4M_Panel`, `MiniXLR_TA5M_Panel`) and mini-DIN (`MiniDIN_4_Panel`)
footprints place their contacts at **evenly-spaced angular positions around a circle** — a
modelling approximation, not a measurement off either manufacturer's own drawing.

Framing this as only an angular-tolerance risk understates it. **Real keyed circular
connectors frequently cluster their contacts in an arc rather than spacing them evenly around
the full circle.** If that turns out to be true of the physical TB4M/TB5M or MD-40SN parts,
the even-spacing model isn't off by a few degrees per contact — it has the **wrong topology**:
contacts in the wrong positions relative to each other, not merely each one nudged slightly
around a correctly-shaped ring. That is a different and larger class of mistake than a
dimensional tolerance error, because no amount of tightening the angle fixes a footprint whose
contact pattern is wrong in kind.

This is not something to resolve by further research or by speculatively editing the
footprint's contact angles. **Verify it against a physical sample connector before any panel
is machined:** get one physical TB4M, one TB5M, and one MD-40SN, and check the real contact
arrangement (evenly spaced vs. clustered in an arc) against each part directly. If a real part
clusters its contacts, the footprint needs its arrangement corrected to match — a footprint
edit, not a redesign — before Task 14 commits panel positions to metal. The procurement plan
already includes buying one physical sample of each of these four connectors ahead of the
production order for exactly this reason; this section is the checklist that sample should be
checked against.

### Priority order for re-checking against physical samples

The dimensions and arrangements flagged **Low** confidence above, in the order worth checking
once samples of the MDR68, mini-XLR, and mini-DIN connectors are in hand:

1. **Mini-XLR and mini-DIN contact arrangement** (evenly spaced vs. arc-clustered) — the most
   urgent: if this is wrong, it is a topology error that no tolerance margin fixes. See
   "Contact arrangement" above.
2. **Mini-XLR panel bushing / cutout diameter (~10.9mm)** — the least-certain single dimension
   among the four footprints, and a wrong cutout diameter is the board's most direct
   scrap-the-panel failure mode.
3. **MDR68 row spacing (2.84mm) and mounting-hole spacing (57.9mm)** — corroborated across
   general MDR-68 references, not confirmed against a primary drawing for the exact MPN
   procurement locks in.

## KiCad gotchas found the hard way

These cost real debugging time to find. Recorded here so later tasks — hand-authored or
generated — don't rediscover them.

- **A project's `sym-lib-table` must sit next to its `.kicad_pro`, not just anywhere upstream.**
  KiCad does not search parent directories for it. `hardware/sym-lib-table` satisfies a project
  whose `.kicad_pro` lives directly in `hardware/`; a project in `hardware/mule/` or
  `hardware/breakout/` needs its own copy (or symlink) in that same directory, or
  `kicad-cli sch erc` reports every stock symbol as unresolved (`lib_symbol_issues`) even though
  the schematic itself is correct. Confirmed empirically: identical schematic, identical stock
  symbol, ERC clean one directory level and not the next. A per-project copy also needs its own
  `wl-sync` entry (`uri` relative to that project's `${KIPRJMOD}`, pointing back up to
  `hardware/lib/wl-sync.kicad_sym`) once a sheet places a custom symbol —
  `hardware/breakout/sym-lib-table` has one as of Task 6; `hardware/mule/sym-lib-table` does
  not, since the mule places no custom parts.
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
  yields a real, well-formed block with properties but no pins — silent at generation time.
  Tasks 2 and 3 sidestepped this by naming the extends-free root symbol instead and overriding
  its `Value` property to the real ordered part number (the mule's inbound buffers place
  `74xx:74LS541` with `Value` set to `SN74LVC541APW`, since the '541 pinout is identical across
  the LS/HCT/AHC/AHCT/LVC sub-families — this is standard KiCad practice, not a workaround
  unique to generated schematics, and the mule itself was left exactly as it shipped). Task 6
  resolved the underlying question properly instead of continuing to sidestep it — see the
  dedicated entry below, "A symbol using `(extends "Parent")` does not resolve just by
  embedding both the parent and the child in `lib_symbols`."
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
- **A symbol using `(extends "Parent")` does not resolve just by embedding both the parent
  and the child in `lib_symbols`, even keyed correctly.** Task 2 left this as an open
  question ("not yet known whether KiCad accepts a lib_symbols entry embedding the extends
  chain the way it does when the GUI places such a part"); Task 6 answered it empirically and
  the answer is no. Embedding `74xx:74LS541` (the extends-free parent) and `74xx:74HCT541`
  (the child, its own `(extends "74LS541")` left exactly as the stock library writes it) side
  by side, keyed by full lib id like any other entry, and placing an instance of the child:
  every one of its pins reports `label_dangling` in ERC and the exported netlist carries zero
  nodes for it — regardless of whether the project's own `sym-lib-table` also has a real,
  resolvable entry for the parent's library (tried both ways, identical failure). KiCad's own
  GUI does not merely copy text when it places a derived symbol — it flattens parent and child
  into one self-contained block with no live `extends` reference left in it, and only a
  schematic already containing that flattened form resolves. `hardware/gen/kicad_sch.py`'s
  `extract_symbol()` now does the same flattening (`_flatten_extends()`): the parent's full
  unit geometry (pins and graphics, its child sub-symbols renamed from the parent's bare name
  to the child's) merged with the child's own top-level property overrides. Verified the same
  way as everything else here — a real `kicad-cli sch erc` + netlist export round-trip on the
  flattened output, confirmed clean (0 errors, correct pin names/numbers), not just that the
  text looks plausible. A symbol using `extends` can now be placed directly by its real name,
  no stand-in Value substitution needed; the mule's own `74LS541`-valued-`SN74LVC541APW`
  workaround was left as-is (already fabricated, not worth touching) rather than migrated.
- **A hierarchical sheet symbol referencing a child `.kicad_sch` file that does not exist yet
  does not fail ERC.** `kicad-cli sch erc` on a root sheet with a `(sheet ...)` whose
  `Sheetfile` points at a nonexistent file reports 0 violations — it silently treats the
  missing sheet as empty, the same as a genuinely empty one. So a root sheet's ten hierarchical
  sheet symbols do not need their ten child files to exist on disk for the root sheet's own
  ERC run to pass; each later task creates its own child file when it populates that sheet
  (confirmed this holds for `kicad-cli sch export svg` too — it plots an empty page for the
  missing child rather than erroring).
- **A real, pcbnew-authored `(sheet ...)` block carries no `(pin ...)` entries until its child
  sheet actually defines hierarchical labels for the parent to expose.** Confirmed against
  KiCad's own shipped demo (`demos/complex_hierarchy`): its `ampli_ht_vertical` and
  `ampli_ht_horizontal` sheet symbols have zero pins. A childless placeholder sheet symbol is
  therefore not a simplification this generator is taking — it is what KiCad itself writes for
  the same situation.
- **`kicad-cli sch export netlist`'s `pinfunction` field is always `"{pin name}_{pin
  number}"`, never the bare pin name.** E.g. a pin named `GPIO2` at number `3` exports as
  `(pinfunction "GPIO2_3")`; a pin named plainly `"1"` at number `"1"` (this project's generic
  connectors use bare position numbers as names — see `hardware/gen/gen_wl_sync_lib.py`)
  exports as `(pinfunction "1_1")`, which reads like a doubled/duplicated value until you check
  a pin with a distinct name and number and see the same `_{number}` suffix appended there too.
  Worth knowing before writing a netlist-contract checker (as
  `hardware/gen/check_mule_netlist.py` and any later `tests/hardware/test_netlist.py` are) that
  asserts on pin names rather than only pin numbers — comparing against the bare name directly
  fails even when resolution is completely correct.
- **Some multi-pin stock symbols split their pins across "unit 0" (KiCad's "common to all
  units" pseudo-unit) and a numbered real unit, instead of keeping every pin in one block.**
  Found at Task 7 placing `Converter_DCDC_Isolated:IH1215D` (a 2W isolated ±15V DC/DC
  converter): its library entry keeps 5 of its 6 pins (`-Vin`/`+Vin`/`-Vout`/`+Vout`/`0V`) in
  a block named `IH0503D_0_0` (unit 0) and only the 6th (`NC`) in `IH0503D_1_1` (unit 1).
  `hardware/gen/kicad_sch.py`'s `unit_pins(block, symname, unit=1)` — written against every
  part used through Task 6, none of which split this way — looked only at unit 1's own block
  and silently returned a 1-pin result instead of 6. Not silent for long: the next line that
  tried `pins["1"]` (the power pin this generator actually needed) raised `KeyError`, so this
  one surfaced as a loud crash rather than constraint 1's usual silent-wrong-netlist failure —
  but a symbol whose split pins are simply the ones actually used by the caller could still
  fail Task 6's exact way. Fixed generically in `unit_pins()` itself (not special-cased to
  this one symbol): it now merges unit 0's pins with the requested unit's own, matching how
  KiCad's GUI actually renders a placed instance (unit-0 content is shown regardless of which
  unit is selected). Confirmed harmless for every symbol already in use — for all of them,
  unit 0 either doesn't exist in the library entry or exists as pin-less body graphics only,
  so the merge is a no-op there.
- **A series element (a pi filter's ferrite bead, say) that bridges two DIFFERENTLY-NAMED
  nets breaks ERC's `power_pin_not_driven` check on the downstream one, even though real
  current still reaches it.** `Device:FerriteBead`'s pins are typed `passive`, and ERC's
  check is evaluated per NET NAME, not by tracing through passive components across a
  net-name boundary — so a node like the power sheet's `ISO_P15_FILT` (between a pi filter's
  ferrite bead and a regulator's `IN` pin) has no `power_out`-typed pin on IT specifically,
  even though the isolated DC/DC's own `power_out` pin drives the net one hop upstream
  (`ISO_P15_RAW`, on the OTHER side of the ferrite). Same fix as any other undriven
  power-input net: `sch.power_flag()` on the downstream node — see
  `hardware/gen/gen_breakout_power.py`'s `build()` for the two instances this took
  (`ISO_P15_FILT`/`ISO_N15_FILT`), the first time this codebase's generators needed a
  PWR_FLAG on something other than a literal contract-rail or entry-point net.

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

For the breakout board's own library, root sheet, and (Task 7) its power child sheet:

```bash
python3 hardware/gen/gen_wl_sync_lib.py                             # hardware/lib/wl-sync.kicad_sym
python3 hardware/gen/gen_wl_sync_footprints.py                      # hardware/lib/wl-sync.pretty/*.kicad_mod
python3 hardware/gen/gen_breakout.py
python3 hardware/gen/gen_breakout_power.py                          # hardware/breakout/sheets/power.kicad_sch
kicad-cli sch upgrade hardware/breakout/breakout.kicad_sch           # see "Format upgrade" below
kicad-cli sch upgrade hardware/breakout/sheets/power.kicad_sch       # ditto -- a child sheet is its own .kicad_sch file
kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net hardware/breakout/breakout.kicad_sch
python3 hardware/gen/check_breakout_power_netlist.py /tmp/breakout.net  # verifies the power sheet's own netlist, not just that ERC passed
```

The library generators each run a structural self-check (round-tripping every symbol/
footprint through `kicad_sch.py`'s/`kicad_pcb.py`'s own parser) before writing anything to
disk, and both `.kicad_sym`/`.kicad_mod` outputs additionally pass `kicad-cli sym upgrade`/
`kicad-cli fp upgrade` cleanly (exit 0, "library was not updated" — already current-format,
well-formed KiCad). Symbol/footprint correctness beyond "well-formed" (constraint 1's
silent-wrong-netlist failure mode) is not something a lone `.kicad_sym`/`.kicad_mod` file can
prove on its own — see the Task 6 report for the scratch-schematic + netlist-export proof run
against every custom symbol.

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
