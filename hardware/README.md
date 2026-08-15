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
the root sheet (`breakout.kicad_sch`, produced by `hardware/gen/gen_breakout.py`) plus two
populated child sheets so far: `sheets/power.kicad_sch` (produced by
`hardware/gen/gen_breakout_power.py`, Task 7) — the 5-position analog/logic inlet (widened
from 4 pins at Task 7 fix round 1, see below), the +3V3 logic rail regulated from +5V, and
the one isolated ±12V supply the other nine sheets draw power from; see that generator's own
module docstring and `check_breakout_power_netlist.py` for the design and its verification.
Task 7 fix round 1 (task-7-report.md's "Fix round 1") corrected two defects found in
review: the +5V rail was undersized regulating it down from +12V on board (fixed by
bringing +5V in directly from the external supply on a fifth inlet pin instead, budgeted
for 400mA), and the child sheet's own component `(instances (path ...))` ancestor chain was
self-referential rather than the real root+sheet-symbol chain a nested sheet needs (fixed
generically in `kicad_sch.py`, not just for this one sheet — see the "KiCad gotchas" entry
below). Task 7 fix round 2 (task-7-report.md's "Fix round 2") replaced the inlet
**connector** itself — not its electrical design — after a reviewer confirmed the mini-DIN
footprints modelled an evenly-spaced ring the real Same Sky MD-40SN/MD-50SN datasheet's own
diagrams contradict, and that both parts are discontinued besides. The inlet is now a
5-position, IEC 61076-2-101 A-coded, keyed, screw-locking M12 connector (Amphenol LTW
M12A-05PFFP-SF8001, current production, 811 units in stock directly confirmed at DigiKey)
— see "Custom connector footprints" below for the full selection rationale and
per-dimension sourcing.
And `sheets/taskpc-digital.kicad_sch` (produced by
`hardware/gen/gen_breakout_taskpc_digital.py`, Task 8) — both task-PC MDR68 connectors
(Connector 1 wired with the 23 digital lines; Connector 0 carries its 9 task-PC analog
channels plus the AISENSE-to-AGND tie, everything else no_connect pending Task 10's own
front-end/buffer circuitry), the 19-channel inbound protected/buffered path (100Ω +
BAT54S clamp per channel, high side +5V never +3V3, fanned out in parallel to a 3.3V
`SN74LVC541APW` bank and a 5V `SN74HCT541PW` `_BUF` bank for Task 11's optocouplers), the
4-channel outbound path, and the reward OR (`74HCT32` + a single debounced `74HCT14`
Schmitt inverter); see that generator's own module docstring and
`check_taskpc_digital_netlist.py` for the design and its verification. Task 8 is this
project's first case of two REAL sibling child sheets coexisting, which surfaced a
cross-sheet reference-collision gotcha `kicad_sch.py` needed a generic fix for — see the
"KiCad gotchas" entry below.

**Task 8 fix round 1** (`task-8-report.md`'s own "Fix round 1") corrected three findings
raised by the implementer's own report, all disclosed concerns the implementer was right
to flag: (1) **critical** — the reward-OR debounce stage placed a `74HCT14` Schmitt
*pair* (task-8-brief.md's own literal, incorrect text); two series inversions cancel, so
the debounced button would idle HIGH into an OR gate against an active-HIGH `RWD_CMD`,
asserting `RWD_DLVR` continuously. Fixed to a *single* inverter (idle-LOW/active-HIGH,
matching an OR gate's actual requirement); `RWD_CMD`'s assumed active-HIGH polarity is
now stated explicitly on-sheet, and the checker asserts the OR's two inputs are
same-polarity. (2) Connector 1's physical MDR68 pin assignment was this sheet's own
sequential guess; it is now sourced from NI's own "X Series User Manual" (National
Instruments 370784K-01, May 2019), Figure A-5 "NI PCIe-6323/6343 Pinout" — the task PC's
own card — for every P0.x and D GND position. (3) Connector 0's 9 task-PC analog channels
(`A_EYE_LX_TPC` etc., plan.md's own net-naming table) and its AISENSE-to-AGND tie are now
wired on this sheet (the same physical-pin source above), since only the file that places
a symbol can label its pins and Task 10 does not exist yet. See `task-8-report.md`'s own
"Fix round 1" section for the full retrieval/verification method and the reasoning behind
each fix.
And `sheets/pi-interface.kicad_sch` (produced by `hardware/gen/gen_breakout_pi_interface.py`,
Task 9) — the sync-module (Raspberry Pi 5 / Compute Module 5 IO Board, see this file's own
opening paragraph) 40-pin GPIO header, wired to spec Sec.4's own GPIO map (transcribed
verbatim as an on-sheet text block); the GPIO0/GPIO1 boot-contention 330Ω series resistors;
a `SN74HCT541PW` output buffer for the three module-sourced signals this sheet produces
(`BARCODE_PI` fanned to 5 loads — 2 Task-11 placeholders + 3 spare; `CAM_TRIG_EYE`/
`CAM_TRIG_BEH` fanned to 5 real panel BNC positions, 1 eye + 4 behavior); and the internal
2.54mm 1×4 USB header for Task 12's own USB-I²C bridge. Also, NOT in the brief's own literal
step list: one more `SN74LVC541APW` channel on +3V3, level-shifting `RWD_DLVR` — produced on
`taskpc-digital.kicad_sch` by a `74HCT32` OR gate powered from **+5V** — down to a
module-safe level before it reaches GPIO23. This project's own global constraint (the plan's
own "Pi GPIO is 3.3V and not 5V tolerant... never one part for both") makes wiring `RWD_DLVR`
to GPIO23 directly a real hazard, not a style nit — the same class of catch-and-fix-beyond-
the-literal-brief Task 8 fix round 1 already made for its own reward-OR polarity defect (see
above), applied here to a defect that never made it into a committed sheet in the first
place. See that generator's own module docstring and
`check_breakout_pi_interface_netlist.py` for the design and its verification — including the
GPIO-physical-pin walk (independently re-derived, not imported from the generator) this
task's own brief specifically asked for, with a negative control that moves one GPIO signal
to a wrong pin and confirms the checker fires (this defect class is invisible to ERC: a
transposed physical pin is still a fully-connected, 0-error netlist, and only fails on a
bench during PIO capture bring-up).

The remaining seven hierarchical sheet symbols (`analog-frontend`, `analog-ni`,
`mux-intan`, `comparators`, `opto-ni`, `opto-intan`, `control-usb-i2c`) each still
reference a `sheets/<name>.kicad_sch` child file that does not exist yet — later tasks
each create and populate their own (see "Sheet symbols referencing a child file that
doesn't exist yet" below for why the root sheet doesn't pre-create them).

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
  beads), and `NetTie` (the AGND/DGND star point) to `hardware/breakout/fp-lib-table`; Task 9
  added `Connector_Coaxial` (`BNC_PanelMountable_Vertical`, for the 5 camera-trigger panel
  positions on `pi-interface.kicad_sch`) — a real stock KiCad footprint, not a placeholder,
  though (like every connector in this project not yet locked to a specific ordered MPN) the
  exact manufacturer part is a layout-stage/procurement decision this schematic-capture task
  does not make.
- **`hardware/lib/wl-sync.kicad_sym`** and **`hardware/lib/wl-sync.pretty/`** — this project's
  own symbols and footprints for parts KiCad doesn't ship: the 68-pin MDR male connector,
  mini-XLR TA4M/TA5M, the M12A_5 power inlet (Task 7 fix round 2 — see below; a 4-pin then
  5-pin mini-DIN occupied this role through fix round 1), the mating DB37 for the ACCES I/O
  USB-AO16-8A analog source, the Raspberry Pi 5 GPIO header, and (Task 7) TPS7A4901/TPS7A3001
  — TI's ultralow-noise adjustable LDO pair — eight symbols total. The Pi header, the DB37
  connector, and (Task 7) TPS7A4901/TPS7A3001 all reuse stock footprints — a bare 2×20 2.54mm
  THT header, KiCad's own `Connector_Dsub` DB37, and a generic `Package_SO` HVSSOP-8-1EP
  PowerPAD, respectively — rather than needing new ones, so only four `.kicad_mod` files
  exist: MDR68, mini-XLR ×2, M12A_5. `hardware/breakout/sym-lib-table` and
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
`MDR68_Male_RightAngle`, `MiniXLR_TA4M_Panel`, `MiniXLR_TA5M_Panel`, and (Task 7 fix round 2)
`M12A_5_Panel` — all in `hardware/lib/wl-sync.pretty/`, generated by
`hardware/gen/gen_wl_sync_footprints.py`. The design spec calls these out as the tightest
tolerance on the board: the enclosure panel is machined to their cutouts, so a footprint
error scraps a panel rather than causing a rework. This section is the sourcing record every
one of those footprints' `descr` field and the two `MiniXLR_*` symbols' `Description`
property point back to — this is where that content actually lives, not a pointer to
somewhere else.

**`M12A_5_Panel` replaced a 4-pin then 5-pin mini-DIN inlet (`MiniDIN_4_Panel`,
`MiniDIN_5_Panel`) at Task 7 fix round 2** (task-7-report.md's "Fix round 2"), both now
deleted rather than deprecated: a reviewer independently pulled the Same Sky MD-SN
datasheet and confirmed the per-pin-count diagrams for both MD-40SN and MD-50SN show a
**clustered**, not evenly-spaced, contact layout — the same possible-topology-error risk
this section already flagged theoretically for the mini-XLR footprints below, but for the
mini-DIN this was a **confirmed** defect, not a residual risk, and as fabbed would very
likely have failed to mate. `build_minidin()` (the generator function) is deleted too, not
left behind — a wrong geometry generator is a trap for a later task. See "The M12 inlet:
selection and sourcing" below for the replacement connector and why it was chosen.

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
| `M12A_5_Panel` | Real part | Amphenol LTW M12A-05PFFP-SF8001 | Manufacturer product page + DigiKey's own product page | High — confirmed ACTIVE lifecycle status, 811 units in stock at DigiKey, checked directly against DigiKey's own product page; TME and OnlineComponents.com independently corroborate real stock of this exact part number (via search results, not a direct fetch) |
| `M12A_5_Panel` | Coding / keying | IEC 61076-2-101 A-code (5-position) | The connector's own part number and DigiKey's own listing; cross-checked against the IEC 61076-2-101:2012 standard document itself, Table 1 (A-coding, 5-way style, 5 contacts → 60V/4A, exact match) | High — a physical keying feature, not a modelling choice; the shell cannot mate rotated |
| `M12A_5_Panel` | Locking mechanism | M12×1 threaded coupling nut | Amphenol LTW product page ("Screw Thread"); Bulgin's own current M12 datasheet independently states "Locking Mechanism: Screw coupling" for the same standardised class | High — a true mechanical lock, not friction |
| `M12A_5_Panel` | Contact pitch circle diameter (4 outer contacts) | Ø5.0mm | A real, current Bulgin M12-series datasheet's own dimensioned "5 pole 'A' Code Front View" drawing, cross-confirmed as the IEC 61076-2-101 A-coding standard geometry (not a per-manufacturer choice — A-coding exists specifically so every compliant manufacturer's part shares this geometry) | High — standardised, cross-manufacturer geometry, not measured off this specific part |
| `M12A_5_Panel` | Contact angular arrangement | 4 contacts at 90° spacing, 45°±30′ off the keyway reference (a diamond, not N/E/S/W); 5th contact at the exact centre | Same Bulgin datasheet drawing, cross-checked against an independent secondary description of the same IEC standard ("four pins at the corners of a square… pin 5 in the centre") | High — this is the dimension the mini-DIN got wrong by assumption; here it is sourced from a real dimensioned drawing of the same standardised class, not modelled as an even ring |
| `M12A_5_Panel` | Individual contact diameter | 1.0mm nominal | IEC 61076-2-101 §1 ("Male connectors have round contacts ⌀0.6mm, ⌀0.76mm, ⌀0.8mm and ⌀1.0mm") and Bulgin's own "⌀1.0±0.03" callout on the same drawing, for A-coding up to 5 contacts | High |
| `M12A_5_Panel` | Pad size / drill (as modelled) | 2.0mm pad / 1.3mm drill | This generator's own choice, enlarged from the 1.0mm real contact for a comfortable hand-solder joint — same discipline as every other connector in this file | High (a design choice, not a sourcing claim) |
| `M12A_5_Panel` | Panel cutout diameter (as modelled) | 12.5mm | M12×1 thread + standard clearance, consistent across every M12 panel-mount datasheet checked (Bulgin, TE, general M12 references) | **Low — not independently confirmed against Amphenol LTW's own drawing for this specific part** (a real 2D drawing and 3D STEP/IGS model exist and are downloadable from the manufacturer's own product page and DigiKey's EDA/CAD models tab, but the download is JS-gated and blocked every automated fetch attempted while researching this fix) — verify before panel machining |
| `M12A_5_Panel` | External shell reference diameter (as modelled) | 14.5mm | Bulgin's own M12-series housing dimension, repeated consistently across several related drawings in the same current datasheet | **Low — same caveat as the panel cutout above** — this is a courtyard/silkscreen reference only, not the machining-critical dimension |
| `M12A_5_Panel` | Pin-1-through-4 rotational numbering (which corner is "1") | This generator's own top-ish/clockwise convention (see `MiniXLR_*`'s own convention) | Not independently confirmed against the manufacturer's own printed pin marking | **Low, but safe** — unlike the contact *arrangement* above, a mislabelled corner does not risk a mismating or a scrapped panel: A-coding's own keying still makes rotated insertion physically impossible, and a wrong label is a net-reassignment fix, not a re-machined panel. Verify against the physical part's own printed numbering before final assembly. |

Neither mini-XLR footprint nor `M12A_5_Panel` drills a PCB mounting hole: all three are
panel-mount parts whose own flange or threaded coupling nut carries the mechanical load
against the sheet-metal panel, not the board, so the PCB footprint needs only solder pads.
`MDR68_Male_RightAngle` is the exception and does get two mechanical mounting holes, matching
every real MDR/SCSI connector datasheet's own board-lock-post convention (it is a PCB-mount
part, not panel-mount).

### Contact arrangement: a possible topology error, not just a tolerance one

The mini-XLR footprints (`MiniXLR_TA4M_Panel`, `MiniXLR_TA5M_Panel`) place their contacts at
**evenly-spaced angular positions around a circle** — a modelling approximation, not a
measurement off the manufacturer's own drawing.

Framing this as only an angular-tolerance risk understates it. **Real keyed circular
connectors frequently cluster their contacts in an arc rather than spacing them evenly around
the full circle.** If that turns out to be true of the physical TB4M/TB5M parts, the
even-spacing model isn't off by a few degrees per contact — it has the **wrong topology**:
contacts in the wrong positions relative to each other, not merely each one nudged slightly
around a correctly-shaped ring. That is a different and larger class of mistake than a
dimensional tolerance error, because no amount of tightening the angle fixes a footprint
whose contact pattern is wrong in kind.

**This is not a theoretical risk — it is exactly what happened to this board's own mini-DIN
inlet, and it is why that connector is gone.** `MiniDIN_4_Panel`/`MiniDIN_5_Panel` used this
same even-spacing approximation; Task 7 fix round 2 confirmed, from the real Same Sky MD-SN
datasheet's own per-pin-count mechanical diagrams, that both MD-40SN and MD-50SN are
genuinely **clustered**, not a ring — the footprints would very likely have failed to mate as
fabbed. Rather than redraw against a part that turned out to be discontinued besides, the
inlet was replaced with a currently-stocked M12 connector whose contact geometry is drawn
from a real, dimensioned, standards-grounded source instead of an assumption (see "The M12
inlet: selection and sourcing" below) — this repo's own precedent for what "verify against a
physical sample, don't speculatively edit the angles" (below) is supposed to catch before a
panel gets machined, not after.

This is not something to resolve by further research or by speculatively editing the
mini-XLR footprint's contact angles. **Verify it against a physical sample connector before
any panel is machined:** get one physical TB4M and one TB5M, and check the real contact
arrangement (evenly spaced vs. clustered in an arc) against each part directly. If a real
part clusters its contacts, the footprint needs its arrangement corrected to match — a
footprint edit, not a redesign — before Task 14 commits panel positions to metal. The
procurement plan already includes buying one physical sample of each of these connectors
ahead of the production order for exactly this reason; this section is the checklist that
sample should be checked against.

### The M12 inlet: selection and sourcing

`M12A_5_Panel` (Task 7 fix round 2) models Amphenol LTW **M12A-05PFFP-SF8001**, selected
against the review finding's own priority order — each criterion checked, not assumed:

1. **Current production and stocked at a major distributor.** Confirmed ACTIVE lifecycle
   status and 811 units in stock directly on DigiKey's own product page — checked directly,
   not inferred from a search snippet. (Mouser's own page for this exact SKU could not be
   fetched directly in this environment; TME and OnlineComponents.com independently
   corroborate real stock of the same part number via search results. DigiKey alone already
   satisfies this criterion.)
2. **A real manufacturer drawing or CAD model is publicly available.** Amphenol LTW's own
   product page and DigiKey's EDA/CAD models tab both offer a 2D drawing and 3D STEP/IGS
   model for this exact part. (This generator could not extract that drawing's raw numbers —
   the download is JS-gated and every automated fetch attempted against it was blocked — but
   the hard requirement is that the drawing exists and is publicly reachable, which it does;
   see the confidence table above for exactly which two dimensions this leaves flagged.)
3. **≥5 contacts, carrying +12V/-12V/+5V/GND/shield.** 5-position: 4 outer contacts plus 1 at
   the centre. Pin-to-rail assignment is unchanged in substance from the mini-DIN's own — see
   `gen_breakout_power.py`'s `_place_inlet()`.
4. **Keyed.** IEC 61076-2-101 A-coding is a physical keying feature — the shell cannot mate
   rotated. This is a power connector feeding ±12V into analog circuitry; that needs to be
   structurally impossible, not merely unlikely.
5. **Locking.** M12×1 threaded coupling nut — a true mechanical lock, not friction. This
   chassis is rack-mounted and slides in and out.
6. **PCB-mount, panel-facing.** Front-fastened panel mount with PCB solder-pin termination,
   consistent with every other connector on this board.
7. **Modest panel footprint.** An M12 shell is a similar order of size to the mini-DIN bushing
   it replaces — comfortably modest next to this panel's 31 BNC positions and four 68-pin MDR
   connectors.

Contact geometry (four contacts on a Ø5.0mm pitch circle at 90° spacing, 45° off the keyway
reference, one contact at the exact centre) is the **IEC 61076-2-101 A-coding standard**
geometry, confirmed three independent ways rather than modelled as a guess: (1) the IEC
61076-2-101:2012 standard document itself, Table 1 — A-coding, 5-way style, 5 contacts →
60V/4A, an exact match for this part, confirming it is a genuine member of that standardised
class; (2) a real, current Bulgin M12-series datasheet's own dimensioned "5 pole 'A' Code
Front View" drawing (Ø5 contact circle, Ø1.0±0.03 contact diameter, 45°±30′ angular
reference) — a *different* manufacturer's real part in the same standardised class, since
A-coding's entire purpose is cross-manufacturer mating interoperability on one fixed
geometry; (3) an independent secondary description of the same standard. This is a
fundamentally different kind of claim than the mini-DIN's even-ring guess: A-coding is a real
interoperability standard every A-coded M12 part must share, not an assumption about one
manufacturer's unpublished layout.

### Priority order for re-checking against physical samples

The dimensions and arrangements flagged **Low** confidence above, in the order worth checking
once samples of the MDR68, mini-XLR, and M12 connectors are in hand:

1. **Mini-XLR contact arrangement** (evenly spaced vs. arc-clustered) — the most urgent open
   item: if this is wrong, it is a topology error that no tolerance margin fixes. See
   "Contact arrangement" above. (The mini-DIN's own version of this exact risk is no longer
   open — it was confirmed and fixed by replacing the connector at Task 7 fix round 2.)
2. **Mini-XLR panel bushing / cutout diameter (~10.9mm)** — the least-certain single dimension
   among the footprints in this table, and a wrong cutout diameter is the board's most direct
   scrap-the-panel failure mode.
3. **`M12A_5_Panel` panel cutout diameter (12.5mm) and external shell reference (14.5mm)** —
   modelled from general M12 industry convention and a different manufacturer's real M12
   datasheet respectively, not yet confirmed against Amphenol LTW's own drawing for this
   specific part (see "The M12 inlet" above for why: a real drawing exists but its download is
   JS-gated). Lower urgency than item 1 above only because the *shape* is already standards-
   confirmed, not because the number is unimportant — this is still a panel-machining
   dimension.
4. **MDR68 row spacing (2.84mm) and mounting-hole spacing (57.9mm)** — corroborated across
   general MDR-68 references, not confirmed against a primary drawing for the exact MPN
   procurement locks in.

## ADG1206 mux address truth table (Task 10c)

`hardware/breakout/sheets/mux-intan.kicad_sch` places eight `ADG1206YRUZ` 16:1 analog
multiplexers (28-lead TSSOP) as Intan's own channel-select bank — see
`hardware/gen/gen_breakout_mux_intan.py` and `hardware/gen/gen_wl_sync_lib.py`'s own
`ADG1206YRUZ` block comment for the full design and pin-table sourcing. This part has no
stock KiCad symbol in this package, so its 28-pin table was hand-built from the real
datasheet. Task 10c's own fix round 1 replaced an earlier two-pin hedge (physical pin 13
vs. 14, for the A3 address input — the originally committed version could not
independently confirm which one was A3 and wired both to the same net rather than guess
silently) with the definitive pin table below: two different physical pins tied to one net
is a short between whatever those pins actually are, not a safe hedge, and would have been
a real fault had the second candidate turned out to be a supply or another source pin
instead of NC.

Transcribed verbatim from Analog Devices' own ADG1206/ADG1207 Rev.0 datasheet, Table 4
"ADG1206 Pin Function Descriptions", 28-Lead TSSOP column:

| Pin | Name | Pin | Name |
|---|---|---|---|
| 1 | VDD | 15 | A2 |
| 2 | NC | 16 | A1 |
| 3 | NC | 17 | A0 |
| 4 | S16 | 18 | EN |
| 5 | S15 | 19 | S1 |
| 6 | S14 | 20 | S2 |
| 7 | S13 | 21 | S3 |
| 8 | S12 | 22 | S4 |
| 9 | S11 | 23 | S5 |
| 10 | S10 | 24 | S6 |
| 11 | S9 | 25 | S7 |
| 12 | GND | 26 | S8 |
| 13 | NC | 27 | VSS |
| 14 | A3 | 28 | D (drain / common output) |

A3 is pin 14; pin 13 (the hedge's other candidate) is genuinely NC, along with pins 2 and
3 — this part's only three NC pins, left unconnected on this board
(`check_breakout_mux_intan_netlist.py` asserts this directly — see below).

**EN is active-high**: Table 4's own description reads "When this pin is low, the device
is disabled and all switches are turned off. When this pin is high, the Ax logic inputs
determine which switch is turned on." This design ties EN to `+12V` on every mux (always
enabled — channel selection is by address only).

**Address truth table**, for Task 12 (the I²C GPIO-expander sheet that will drive
`MUX{n}_A0`..`MUX{n}_A3`) so the software side does not have to re-derive it from the
datasheet: A3-A2-A1-A0 read as a straight 4-bit binary count, EN high throughout.

| A3 A2 A1 A0 | Selects | A3 A2 A1 A0 | Selects |
|---|---|---|---|
| 0000 | S1 | 1000 | S9 |
| 0001 | S2 | 1001 | S10 |
| 0010 | S3 | 1010 | S11 |
| 0011 | S4 | 1011 | S12 |
| 0100 | S5 | 1100 | S13 |
| 0101 | S6 | 1101 | S14 |
| 0110 | S7 | 1110 | S15 |
| 0111 | S8 | 1111 | S16 |

Which of this board's own 16 `A_*` source nets rides physical `S1` vs. `S16` on every mux
is a separate, board-specific choice this sheet makes on its own (`ALL_16_NETS`/
`S_PIN_NUMBERS` list order in `gen_breakout_mux_intan.py`), not part of the datasheet's own
truth table above — see that generator's own module docstring for the full list.

`hardware/gen/check_breakout_mux_intan_netlist.py` asserts, independently of the
generator's own choices, that no two of a mux's own 21 signal pins (`S1`-`S16`/`A0`-`A3`/
`D`) ever share a net on one instance, and that the three NC pins (2, 3, 13) carry no real
net — so a two-pins-one-net hedge of this kind cannot pass silently again, on this part or
any future one.

## MCP4728 comparator-threshold DAC and I²C bus (Task 10d)

`hardware/breakout/sheets/comparators.kicad_sch` places one `LM339` quad comparator (three
channels used — `A_PD1`→`PD1_COMP`, `A_PD2`→`PD2_COMP`, `A_ACC`→`ACC_TRIG` — the fourth
brought out to `A_MISC1`, unpopulated) and one `MCP4728` quad 12-bit I²C DAC that sets each
comparator's own threshold — see `hardware/gen/gen_breakout_comparators.py` and
`hardware/gen/check_breakout_comparators_netlist.py` for the full design and its
verification. Recorded here, the same way the ADG1206 mux truth table above is, for Task 12
(the I²C bus master — `control-usb-i2c.kicad_sch`, an on-board USB-I²C bridge) so the
software side does not have to re-derive any of this from the datasheet or the schematic:

- **I²C address: `0x60`**, the MCP4728's own factory default (its address bits live in
  EEPROM, not on hardware pins — the part has none — Microchip DS22187E). Kept at default
  rather than reprogrammed: this design places exactly one MCP4728 on the bus, so there is
  no second device at `0x60` to collide with.
- **Channel map**: `VOUTA`→`PD1_COMP`'s own threshold, `VOUTB`→`PD2_COMP`'s, `VOUTC`→
  `ACC_TRIG`'s, `VOUTD`→the unpopulated 4th (`A_MISC1`) channel's — this board's own
  declared, arbitrary-but-fixed assignment (no datasheet fixes it), one DAC channel per
  comparator (symmetric threshold only; an asymmetric make/break threshold would cost a
  second DAC channel per comparator and is not implemented).
- **`LDAC` is tied to `AGND`** (permanently asserted — immediate per-write update; no
  synchronized multi-channel update is used).
- **Bus nets**: `I2C_SDA`/`I2C_SCL`, exposed as global labels on this sheet, no bus
  pull-ups placed here — Task 12 owns the bus master and, with it, the one-set-of-pull-ups-
  per-bus sizing decision once every I²C device on the board (this DAC, plus whatever
  address-expander parts Task 12 itself adds) is known.

**The pull-up rail on `PD1_COMP`/`PD2_COMP`/`ACC_TRIG` is `+3V3`, never `+5V`** — a
destroy-the-sync-module constraint (all three wire directly into its own 3.3 V-only GPIO,
no buffer in between), asserted directly by `check_breakout_comparators_netlist.py` with a
negative control that moves one pull-up to `+5V` and confirms the check fires. Relevant to
Task 12 only in that nothing about the I²C bus itself should ever change that fact — the
DAC's own threshold output is a separate signal path from the comparators' own
open-collector outputs, and the two must not be confused when wiring the bus.

## Opto-NI: 24 isolated digital channels and the recording NI's Connector 1 (Task 11)

`hardware/breakout/sheets/opto-ni.kicad_sch` is the galvanic barrier between the board's
own `DGND` domain and the recording NI card's own digital ground (`NI_GND`) — see
`hardware/gen/gen_breakout_opto_ni.py` and `hardware/gen/check_breakout_opto_ni_netlist.py`
for the full design and its verification. 24 channels (16 event-data bits plus
`EVT_STROBE_BUF`, `BARCODE_PI`, `RWD_CMD_BUF`, `RWD_DLVR`, `STIM_TRIG_BUF`, `RHS_STIM_OUT`,
`PD1_COMP`, `PD2_COMP` — corrected from the plan's own stale 22-channel figure once the two
photodiode-comparator channels reaching NI were accounted for), each through a Broadcom
`ACSL-6400` (quad, all-in-one, 15 MBd logic-output optocoupler — **not** phototransistor,
matching the mule's own validated part class) — six packages, no stock KiCad symbol existed
for this part so `hardware/lib/wl-sync.kicad_sym` gained a hand-built one this task (search
that generator's own module comment for "ACSL-6400" for the full Broadcom AV02-0235EN pin
sourcing, cross-confirmed against two independent figures in the same datasheet). This
sheet also places the recording NI's own **Connector 1** (digital) — Connector 0 (analog)
was already placed by Task 10b on `analog-ni.kicad_sch`.

**The drive topology is deliberately non-inverting, unlike the mule's own.** ACSL-6400's
own truth table is LED-ON → output LOW; wiring the LED the mule's own way (source → series
R → anode; cathode → local ground) would ship every NI-side event-code bit inverted — a
real, silent hazard for a 16-bit word, not merely a style choice. This sheet instead ties
each LED's anode to `+5V` through the series resistor and drives the cathode directly from
the source net, which cancels the inversion by relying only on the driving 74HCT541/74HCT32
push-pull output's ordinary ability to sink a few mA — see the generator's own module
docstring for the full derivation. LED series resistors are 430 Ω (≈7.33 mA, roughly half
ACSL-6400's own datasheet-recommended top-of-range forward current). NI-side pull-ups are
**3.9 kΩ, not the mule's 1 kΩ** — driven by NI's 250 mA/connector budget (24 output stages
already draw ~120–168 mA; 1 kΩ pull-ups would add another ~120 mA, at or over the budget) —
**fixed round 1 from an original 10 kΩ choice that fit the budget too but exceeded
ACSL-6400's own datasheet pull-up maximum (4 kΩ)**, derived only against the mule's own 1 kΩ
without checking the part's own spec. 3.9 kΩ is the largest E24 value at or under that 4 kΩ
limit, adds only ~31 mA (domain total 151–199 mA, 51–99 mA of headroom), and keeps every
logic-level margin the original analysis relied on (reasoned through explicitly in the
generator's own module docstring, not merely asserted) — see
`.superpowers/sdd/2026-08-13-breakout-pcb/task-11-report.md`'s own "Fix round 1" section.

**Connector 1's physical pins are sourced from NI's X Series User Manual (370784K-01),
Figure A-18** ("NI PCIe-6353 and NI PCIe/PXIe-6363 Pinout" — the recording NI's own PXIe-6353
family; "PXIe-6353" itself appears nowhere in the manual, only "PCIe-6353"/"PCIe/PXIe-6363",
which share one connector-pinout figure), fetched directly from NI's own documentation host
and independently re-derived rather than reused unchecked from Task 8's own Connector 1
table (a *different* physical card, PCIe-6343, Figure A-5) — found, on comparison, to be
byte-for-byte identical on all 24 P0.x positions, all 12 D GND positions, and both `+5V`
positions. **Power for the NI-side output stage is `NI_5V`/`NI_GND`, taken directly off
Connector 1's own `+5V` (pins 8, 14) and D GND (12 pins)** — switcher-free, already
referenced to NI's own ground, per this task's own brief — with a filtered isolated DC-DC
fallback footprint (`TMA-0505S`, the same part class the mule's own isolated 5 V rail uses)
left fully DNP, bridged onto `NI_5V` through a single DNP 0 Ω resistor so it contributes
nothing to the netlist's real behaviour unless deliberately populated.

## Opto-Intan: 6 isolated digital channels, one bidirectional package (Task 11)

`hardware/breakout/sheets/opto-intan.kicad_sch` is the galvanic barrier between `DGND` and
the Intan domain's own isolated ground (`INTAN_GND`) — see
`hardware/gen/gen_breakout_opto_intan.py` and
`hardware/gen/check_breakout_opto_intan_netlist.py` for the full design and its
verification. 6 channels: 5 outbound (`EVT_STROBE_BUF`/`BARCODE_PI`/`RWD_CMD_BUF`/
`RWD_DLVR`/`STIM_TRIG_BUF`, `DGND`→`INTAN_GND`) and 1 inbound (`RHS_STIM_OUT`, the one
signal originating inside the Intan domain, `INTAN_GND`→`DGND`), all out to BNCs (isolated
shells, this board's established convention).

**The second package is a Broadcom `ACSL-6420` ("quad, bi-directional 2/2"), not a second
`ACSL-6400`** — a correction this task's own pin-by-pin datasheet verification found, not
assumed going in. An all-in-one part's four channels share ONE VDD/GND domain on the output
side; opto-intan's own second package needs one outbound channel (`STIM_TRIG_BUF`) and one
inbound channel (`RHS_STIM_OUT`) simultaneously — opposite directions, which an all-in-one
part cannot provide without putting one channel's output on the wrong side of the barrier
entirely. `ACSL-6420`'s own 2/2 split (2 channels LED-on-VDD1/output-on-VDD2, 2 the reverse)
is built for exactly this; the remaining channel of each direction is a genuine spare. See
`hardware/gen/gen_wl_sync_lib.py`'s own module comment (search "ACSL-6420") for the full pin
table, sourced the same way as `ACSL-6400` (Broadcom AV02-0235EN, cross-confirmed against
two independent figures). Total across both opto sheets: 7×`ACSL-6400` + 1×`ACSL-6420` =
**eight quad packages, 30 channels** — the corrected count; the plan's own text still reads
28/6-packages, stale since before the two photodiode-comparator NI channels propagated.

**The Intan-side output stage runs from a new `ISO_5V` rail**, an `LD1117S50TR_SOT223`
LDO regulating `ISO_P12` down to 5 V, referenced to `INTAN_GND` — `ISO_P12`/`ISO_N12`
themselves are ±12 V, too high for the ACSL-6400/6420 family's own 5.5 V absolute maximum
VDD. `RHS_STIM_OUT`'s own inbound path gets the same series-resistor-plus-clamp input
protection every other panel input on this board carries, since it is a signal entering
from off-board Intan equipment via its own BNC — its assumed logic sense (active-HIGH,
TTL/CMOS-compatible) is flagged on-sheet as an assumption needing bench confirmation
against the real RHS hardware, the same class of residual `RWD_CMD`'s own polarity already
carries elsewhere in this project.

**Domain-disjointness verification is scoped to each sheet's own components, not a
project-wide rail scan** — found necessary, not stylistic: `check_breakout_opto_intan_
netlist.py`'s own first draft scanned every node on `ISO_5V`/`INTAN_GND`/`AGND`/etc.
project-wide and immediately flagged `mux-intan.kicad_sch`'s own 8 `INA105KU` difference
amplifiers (Task 10c) and `power.kicad_sch`'s own isolated DC-DC (Task 7) as "unexpected
straddling" — both real, already-reviewed, deliberate designs (the analog fan-out's own
partial-isolation strategy and the isolated supply itself), not defects. Fixed by scoping
both `check_breakout_opto_ni_netlist.py`'s and this sheet's own checker to the specific
component references each sheet's own checks independently discover, not a blind scan by
shared rail/net name.

**Cross-sheet consequence, not a scope overrun** (same class Task 8/9/10d already
established a precedent for): `taskpc-digital.kicad_sch`'s own temporary `PWR_FLAG` on
`RHS_STIM_OUT` — placed at Task 8 because nothing drove that net yet — is deleted here, the
moment this sheet wires a real `open_collector` driver onto it, exactly as that
generator's own docstring already specified. `check_breakout_pi_interface_netlist.py`'s own
`BARCODE_PI` fan-out count moved 5→6 at Task 11's `opto-ni.kicad_sch` (its first real load)
and 6→7 here (opto-intan's own).

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
- **A CHILD sheet's own components must NOT reuse that file's own root uuid as their
  `(instances (path ...))` prefix — that prefix has to be the ROOT project's uuid plus
  the placing SHEET SYMBOL's own uuid, read off the real parent file, not invented.**
  Every generator through Task 6 only ever built hierarchy ROOTS (`mule.kicad_sch`,
  `breakout.kicad_sch`), where `kicad_sch.py`'s `Sch` defaulting `(instances (path
  "/{self.root_uuid}" ...))` to the file's own identity uuid is exactly correct — a
  component placed directly on a root's own canvas really does carry just `/<that
  file's own uuid>` (confirmed against KiCad's own shipped
  `demos/complex_hierarchy/complex_hierarchy.kicad_sch`). Task 7's `power.kicad_sch` is
  this codebase's first CHILD sheet, and the same default is wrong there: found (not by
  ERC, which stayed silent throughout — see below) reviewing the committed file, all 47
  of its own placed instances carried `power.kicad_sch`'s own root uuid instead of
  breakout.kicad_sch's root uuid + the "power" sheet symbol's own uuid. Confirmed
  against the same KiCad demo project, this time its genuinely nested child sheet
  (`ampli_ht.kicad_sch`, placed TWICE from `complex_hierarchy.kicad_sch` as both
  "ampli_ht_vertical" and "ampli_ht_horizontal"): every one of its own components
  carries TWO `(path ...)` entries, `/<complex_hierarchy's root uuid>/<vertical sheet
  symbol's own uuid>` and `/<same root uuid>/<horizontal sheet symbol's own uuid>` —
  `ampli_ht.kicad_sch`'s OWN file-identity uuid appears in neither, proving only the
  PLACING sheet symbol's own uuid belongs in the chain, never the child file's own.
  **Silent for ERC and for `kicad-cli sch export netlist` alike**: confirmed empirically
  (regenerating this exact sheet before and after the fix and diffing the exported
  netlist byte-for-byte) that `kicad-cli sch export netlist` recomputes every
  component's own `sheetpath`/`tstamps` fields by walking the real `(sheet ...)` file
  hierarchy from the invoked root, never by reading a component's own `(instances (path
  ...))` bookkeeping at all — so this defect does not show up as any difference in ERC
  output OR in the exported netlist, only in the raw `.kicad_sch` source itself. It
  still matters: it is exactly the class of bug Task 3 hit on the mule board's own
  schematic-to-PCB cross-links (`hardware/gen/gen_mule_pcb.py`, fixed by reading the
  netlist's real component uuids instead of an in-process value), here one level up in
  the SAME "don't invent identity data, read the real committed artifact" family, and it
  silently breaks KiCad's own cross-probing between sheets and any future PCB
  generator's schematic cross-link for a child-sheet component. Fixed at Task 7 fix
  round 1 (task-7-report.md's "Fix round 1"), generically rather than sheet-specifically
  (Tasks 8-12 all build further child sheets): `Sch` now takes an explicit
  `instance_path_prefix` (default `None`, meaning "I am a hierarchy root" — unchanged
  behaviour for every existing root-only generator); a child-sheet generator computes
  the real prefix by reading its own parent `.kicad_sch` off disk with two new
  `kicad_sch.py` helpers, `find_root_uuid()` and `find_sheet_instance_path()` (a third,
  `find_all_instance_paths()`, lets a checker independently confirm every instance in a
  child sheet actually resolved correctly, rather than only trusting the generator ran
  the new code path) — see `gen_breakout_power.py`'s own `build()` and
  `check_breakout_power_netlist.py`'s `verify_instance_paths()`. Also gated
  `Sch.render()`'s `(sheet_instances ...)` block to root sheets only on the same
  evidence: `ampli_ht.kicad_sch` (real, nested, used twice) carries NO such block at
  all, unlike its own root parent's single `(path "/" (page "1"))` entry describing
  only itself.
- **Two sibling child sheets, each built by an independent `Sch` instance, silently mint
  DUPLICATE reference designators the moment both have real content.** `next_ref()`
  starts every prefix at 1 per `Sch` instance, which is exactly correct for a single
  sheet in isolation — but every generator through Task 7 only ever built the FIRST real
  child sheet to exist alongside another one (`power.kicad_sch`, Task 7), so this never
  actually collided with anything. Task 8 (`taskpc-digital.kicad_sch`) is this project's
  second real sheet, and its own independently-started "J1"/"R1"/"C1"/"D1"/"U1" collided
  head-on with `power.kicad_sch`'s OWN "J1" (the M12A_5 inlet) etc. — two physically
  different parts sharing one reference string, a genuine BOM/assembly ambiguity, not
  just cosmetic. Silent for `kicad-cli sch erc` (0 errors either way — ERC does not
  check cross-project reference uniqueness), but NOT silent for `kicad-cli sch export
  netlist`, which prints `Warning: schematic has annotation errors, please use the
  schematic editor to fix them`, and NOT silent for
  `hardware/gen/check_mule_netlist.py`'s own `parse_component_values()` — shared
  infrastructure `check_breakout_power_netlist.py` already depends on, and every future
  child-sheet checker will too — which hard-asserts globally unique references and
  raises immediately on the first duplicate it finds. A real KiCad user hits the
  identical situation hand-building a multi-sheet project and resolves it with
  Eeschema's own "Annotate Schematic" tool; this generator has no equivalent, so
  `kicad_sch.py`'s `Sch` gained an optional `ref_start: dict[str, int] | None` field
  (default `None`, unchanged behaviour for every generator that doesn't pass it) plus a
  new `find_max_refs(sch_text)` helper that reads a SIBLING sheet's own already-committed
  `.kicad_sch` text and returns `{prefix: highest number already used}` — a child-sheet
  generator seeds `Sch(..., ref_start=find_max_refs(sibling_text))` so its own
  `next_ref()` calls continue past whatever a sibling already used, rather than
  restarting at 1 and colliding. Same "read the real committed artifact, don't invent
  identity data" discipline `instance_path_prefix` already established for uuids,
  extended to reference numbers — see `gen_breakout_taskpc_digital.py`'s own `build()`.
  **A future child-sheet generator (Tasks 9-12) needs to seed from EVERY already-committed
  sibling sheet's own maxima, not only `power.kicad_sch`** — this fix handles two real
  sheets; a third and beyond needs the same treatment extended to read all of them.
  Also broke `check_breakout_power_netlist.py`'s own `RAIL_BYPASS_EXPECTED` regression
  guard, unrelatedly: `+5V`/`+3V3` are shared rails, and `_rail_bypass_cap_count()` counts
  bypass/bulk capacitors PROJECT-WIDE, so `taskpc-digital.kicad_sch`'s own per-IC
  decoupling on those two rails (legitimate, necessary, not a defect) changed the real
  total the moment a second real sheet existed to add any. Fixed by updating the two
  affected counts (recomputed directly against the regenerated netlist, not guessed) —
  flagged there as needing the SAME update again once Tasks 9-12 also decouple their own
  ICs on `+5V`/`+3V3`, an expected consequence of that function's own project-wide scope,
  not a one-time fix.

  **Extended at Task 9, exactly as flagged above**: `pi-interface.kicad_sch` is this
  project's THIRD real child sheet (after `power.kicad_sch` and `taskpc-digital.kicad_sch`,
  both already committed), so seeding past only one of them is no longer sufficient —
  `find_max_refs()` itself still only reads ONE sheet's text (unchanged), but
  `kicad_sch.py` gained a new `merge_max_refs(*ref_maps)` helper that takes the per-prefix
  MAXIMUM across as many `find_max_refs()` results as are passed in, so
  `gen_breakout_pi_interface.py`'s own `build()` seeds
  `Sch(..., ref_start=merge_max_refs(find_max_refs(power_text), find_max_refs(taskpc_text)))`
  — generic machinery in `kicad_sch.py`, not a sheet-specific workaround, so Tasks 10-12
  extend the same call with their own additional sibling(s) rather than re-solving this.
  Also updated `check_breakout_power_netlist.py`'s own `RAIL_BYPASS_EXPECTED` again, the
  same way Task 8 already did once: `pi-interface.kicad_sch` adds two more ICs on the
  shared `+5V`/`+3V3` rails (the `RWD_DLVR` level-shifter and the trigger-output buffer,
  one 100nF decoupler each), so `+5V`/`DGND` moved 8→9 and `+3V3`/`DGND` moved 5→6 —
  flagged there, again, as needing the SAME update once Tasks 10-12 add their own.
- **Two satellite components placed at a fixed offset from TWO DIFFERENT pins of the same
  multi-column connector can land on the exact same coordinate, even when the two pins
  themselves are visibly apart.** Found placing `pi-interface.kicad_sch`'s own GPIO0/GPIO1
  330Ω series resistors (Task 9): `wl-sync:RaspberryPi5_GPIO_Header`'s left (odd-numbered)
  and right (even-numbered) pin columns are built from ONE shared per-row Y coordinate list
  (`gen_wl_sync_lib.py`'s own `build_symbol()` — `ys[i]` indexes both `left[i]` and
  `right[i]`), so GPIO0 (physical pin 27, left column) and GPIO1 (physical pin 28, right
  column) — consecutive odd/even numbers — sit on the SAME row, differing only in X, not Y.
  Placing both resistors at one fixed `(X, y)` (`y` taken from the header pin's own Y,
  reused for both) stacked them on an identical point: `kicad-cli sch erc` reported
  `multiple_net_names` ("Both GPIO0_HDR and GPIO1_HDR are attached to the same items") on
  the first generation attempt. A first fix attempt (a per-instance Y offset between the
  two resistors) picked exactly 7.62mm — `Device:R`'s own pin-to-pin span (pin 1 at local Y
  +3.81, pin 2 at −3.81, confirmed via `unit_pins()`) — which moved the COLLISION one
  component over instead of removing it (R\<n\>'s own pin 2 landed exactly on R\<n+1\>'s own
  pin 1). An offset comfortably larger than the part's own pin span (12.7mm, this project's
  own `LOAD_DY` constant reused) cleared both. Worth checking for any future generator
  placing more than one satellite part per row of a two-column connector, not just this
  one header.
- **A `PWR_FLAG` left on a net that later gains a REAL driving pin doesn't just become
  redundant — it can actively break `kicad-cli sch erc`.** Confirmed at Task 10d:
  `taskpc-digital.kicad_sch` (Task 8) placed a `sch.power_flag()` on each of
  `PD1_COMP`/`PD2_COMP`/`ACC_TRIG`/`RHS_STIM_OUT` specifically because nothing drove them
  yet (Tasks 10/11 didn't exist) and an `input`-typed buffer pin with no driver anywhere in
  the project trips ERC's own `pin_not_driven` — a real, not a false, finding at the time.
  The moment `comparators.kicad_sch` (Task 10d) wired a genuine `open_collector` output
  pin onto three of those same four nets, the SURVIVING `PWR_FLAG`s tripped a NEW error —
  `pin_to_pin`, "Pins of type Open collector and Power output are connected" — confirmed
  empirically (3 errors, one per stale flag, before deleting the three stale flags, 0
  after — corrected here from an earlier, uncorroborated "4"; a later reviewer
  reconstructed this commit and measured 3, with no `I2C_SCL` involvement, matching the
  arithmetic directly: three surviving flags, three conflicts). Fixed by deleting
  exactly the three `sch.power_flag()` calls whose own net now has a real driver (not the
  fourth, `RHS_STIM_OUT`, which still has none — Task 11's own job) — see
  `gen_breakout_taskpc_digital.py`'s own `_place_outbound()`, which already flagged this
  exact deletion as required the moment Task 10 existed. A `PWR_FLAG` used as this
  project's own "assert this net is driven for now, pending a real driver" idiom (also
  used by `gen_breakout_power.py` for `ISO_P15_FILT`/`ISO_N15_FILT`, and by
  `gen_breakout_comparators.py` itself for `I2C_SCL`, pending Task 12) is therefore a
  standing TODO for whichever later task adds the real driver, not a one-time placeholder
  that quietly stops mattering — check its own net for a genuine driving pin before
  assuming a `PWR_FLAG` there is still needed.
- **`Sch.place()`'s new `dnp` parameter (Task 10d) is generation-time-only and changes
  NOTHING about connectivity** — a DNP-marked part's pins are wired exactly as instructed,
  fully present in both `kicad-cli sch erc`'s own analysis and the exported netlist's
  `(nets ...)` section (confirmed empirically: `comparators.kicad_sch`'s own two DNP
  resistors, the unpopulated 4th channel's pull-up and hysteresis feedback, appear in the
  exported netlist's connectivity graph identically to every populated resistor). What DOES
  change: each DNP component's own `(comp (ref ...) ...)` block in the exported netlist
  gains a value-less `(property (name "dnp"))` marker, present ONLY when `dnp=yes` and
  absent entirely otherwise (not `(value "no")`) — confirmed directly against the real
  export, not assumed from the `.kicad_sch` source's own `(dnp yes/no)` field shape. This
  is what lets a checker confirm a "populate option" part is genuinely marked unpopulated
  (`check_breakout_comparators_netlist.py`'s own `parse_dnp_refs()`) without re-parsing the
  raw schematic source for it.

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

For the breakout board's own library, root sheet, and (Tasks 7-9) its power,
task-PC-digital, and pi-interface child sheets. Order matters here in a way it didn't
before Task 7 fix round 1: `gen_breakout_power.py` now *reads*
`hardware/breakout/breakout.kicad_sch` (to compute its own components' real
root+sheet-symbol ancestor path -- see the "KiCad gotchas" entry above and
`gen_breakout_power.py`'s own `build()`), so `gen_breakout.py` must have already run and
written that file, not merely conceptually precede it in the hierarchy -- running
`gen_breakout_power.py` first raises `FileNotFoundError`, loudly, not silently.
`gen_breakout_taskpc_digital.py` (Task 8) reads BOTH `hardware/breakout/breakout.kicad_sch`
(same reason) AND `hardware/breakout/sheets/power.kicad_sch` (to seed its own reference
counters past whatever `power.kicad_sch` already used -- see the "KiCad gotchas" entry
above, "duplicate reference designators"), so it must run after BOTH of those.
`gen_breakout_pi_interface.py` (Task 9) reads `hardware/breakout/breakout.kicad_sch`
(same reason again) AND BOTH `hardware/breakout/sheets/power.kicad_sch` AND
`hardware/breakout/sheets/taskpc-digital.kicad_sch` (to seed its own reference counters
past EVERY already-committed sibling's own maxima via the new `merge_max_refs()` -- see
the "KiCad gotchas" entry above, "Extended at Task 9"), so it must run after all three:

```bash
python3 hardware/gen/gen_wl_sync_lib.py                             # hardware/lib/wl-sync.kicad_sym
python3 hardware/gen/gen_wl_sync_footprints.py                      # hardware/lib/wl-sync.pretty/*.kicad_mod
python3 hardware/gen/gen_breakout.py                                # hardware/breakout/breakout.kicad_sch -- must run before the next three lines
python3 hardware/gen/gen_breakout_power.py                          # hardware/breakout/sheets/power.kicad_sch -- must run before the next two lines
python3 hardware/gen/gen_breakout_taskpc_digital.py                 # hardware/breakout/sheets/taskpc-digital.kicad_sch -- must run before the next line
python3 hardware/gen/gen_breakout_pi_interface.py                   # hardware/breakout/sheets/pi-interface.kicad_sch
kicad-cli sch upgrade hardware/breakout/breakout.kicad_sch           # see "Format upgrade" below
kicad-cli sch upgrade hardware/breakout/sheets/power.kicad_sch       # ditto -- a child sheet is its own .kicad_sch file
kicad-cli sch upgrade hardware/breakout/sheets/taskpc-digital.kicad_sch  # ditto
kicad-cli sch upgrade hardware/breakout/sheets/pi-interface.kicad_sch    # ditto
kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net hardware/breakout/breakout.kicad_sch
python3 hardware/gen/check_breakout_power_netlist.py /tmp/breakout.net  # verifies the power sheet's own netlist AND (reading breakout.kicad_sch/power.kicad_sch directly, not the netlist -- see its own module docstring) the instance-path fix, not just that ERC passed
python3 hardware/gen/check_taskpc_digital_netlist.py /tmp/breakout.net  # verifies the task-PC digital sheet's own netlist end to end (both buffer banks) AND its own instance-path fix
python3 hardware/gen/check_breakout_pi_interface_netlist.py /tmp/breakout.net  # verifies the sync-module interface sheet's own netlist end to end (every GPIO on its real physical pin) AND its own instance-path fix
```

Regenerating `breakout.kicad_sch`, `power.kicad_sch`, and `taskpc-digital.kicad_sch` mints
fresh UUIDs on every one of their own components too (same byte-reproducibility caveat
below), which is harmless in isolation but means a LATER child sheet generated against a
freshly-regenerated parent embeds THAT run's UUIDs in its own `(instances (path ...))`
chain -- regenerating only `pi-interface.kicad_sch` against the ALREADY-COMMITTED (not
freshly regenerated) `breakout.kicad_sch`/`power.kicad_sch`/`taskpc-digital.kicad_sch` is
the one that reproduces the actually-committed state; regenerating all four together (as
the recipe above does) is equally correct but changes every earlier file's own bytes too,
which is real churn to commit unless those earlier sheets are ALSO meant to change this
run.

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
