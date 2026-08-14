# Sync-box breakout PCB — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fabricated, assembled and bench-verified 2U breakout board that carries every rig
signal between the behavioural task PC, the CM5-based sync box, the recording NI card and the Intan
RHS — plus a small event-path mule board fabbed first to de-risk the protocol early.

**Architecture:** One 430 × 240 mm 4-layer mixed-signal board. Digital crossings into ephys
chassis are optocoupled (28 channels); analog fan-out uses difference amplifiers and NRSE drive
rather than isolation (33 stages). Two buffer families handle the two level-shift directions.
Schematic is captured as one sheet per functional block so sheets can be reviewed and revised
independently.

**Tech Stack:** KiCad 10.0.5, `kicad-cli` for headless ERC/DRC/BOM/fab output, Python 3.11 for
netlist checks reusing this repo's existing test tooling.

**Spec:** [`../specs/2026-08-13-syncbox-breakout-pcb-design.md`](../specs/2026-08-13-syncbox-breakout-pcb-design.md)

## Global Constraints

- **Analog range convention: ±5 V board-wide.** Sources outside it are scaled at their own front
  end. Misc inputs carry switchable ÷1/÷2.
- **No on-board switching regulators** except the single isolated ±12 V DC-DC serving
  INTAN_GND. This follows from choosing optocouplers over digital isolators; adding a switcher
  would negate it.
- **Hand-solderable BOM.** Nothing finer than SOIC/TSSOP, no BGA or QFN, no passive below 0603.
  Every part must also exist in a turnkey assembler's parts library.
- **Pi GPIO is 3.3 V and not 5 V tolerant.** Task-PC DAQ outputs are 0–5 V. `74LVC541A` on 3.3 V
  for inbound, `74HCT541` on 5 V for outbound. Never one part for both.
- **PIO parallel capture reads a contiguous pin range.** Event data occupies GPIO0–15 and strobe
  GPIO16. This is fixed and not negotiable in layout.
- **Camera triggers must land on GPIO18 and GPIO19** — the only hardware PWM pins surviving the
  capture range.
- **Series resistors on GPIO0/1** plus `force_eeprom_read=0`, because the Pi probes those pins as
  I²C at boot.
- **Every panel input carries series resistance and clamp diodes to the rails.**
- **This repository is public.** Hardware docs state electrical facts; no private spec text.

## Who executes what

This plan mixes work an agent can do with work only a person with a soldering iron can do. Each
task is marked:

- **[agent]** — schematic capture, layout files, BOM, drawings, docs, checks via `kicad-cli`
- **[human]** — ordering, soldering, plugging things in, taking measurements
- **[human-led]** — agent produces the constraint set and floorplan; a person drives the
  interactive tool

## File structure

| Path | Responsibility |
|---|---|
| `hardware/README.md` | Electrical facts, board overview, how to rebuild fab outputs |
| `hardware/lib/wl-sync.kicad_sym` | Custom symbols (MDR68, M12 A-coded, ACCESIO DB37, GPIO header) |
| `hardware/lib/wl-sync.pretty/` | Custom footprints for the same |
| `hardware/mule/` | Event-path mule: project, schematic, layout, fab, bring-up procedure |
| `hardware/breakout/breakout.kicad_sch` | Root sheet — hierarchy only |
| `hardware/breakout/sheets/*.kicad_sch` | One sheet per functional block (Tasks 5–11) |
| `hardware/breakout/breakout.kicad_pcb` | Layout |
| `hardware/breakout/panel/` | Front and rear panel drawings (DXF) |
| `hardware/breakout/fab/` | Gerbers, drill, pick-and-place, BOM |
| `hardware/breakout/bringup.md` | Bench procedure and acceptance criteria |
| `tests/hardware/test_netlist.py` | Automated netlist assertions against the spec |

## Net naming convention

Every sheet uses these names. Cross-sheet consistency is checked in Task 12.

| Class | Pattern | Examples |
|---|---|---|
| Event codes | `EVT_D0`…`EVT_D15`, `EVT_STROBE` | with domain suffix below |
| Domain suffix | `_TPC` raw from task PC, `_PI` 3.3 V, `_NI` isolated, `_INTAN` isolated | `EVT_D0_TPC`, `EVT_D0_PI`, `EVT_D0_NI` |
| Sync/trigger | `BARCODE_PI`, `BARCODE_NI`, `BARCODE_INTAN`, `CAM_TRIG_EYE`, `CAM_TRIG_BEH` | |
| Behaviour digital | `PD1_COMP`, `PD2_COMP`, `ACC_TRIG`, `RWD_CMD`, `RWD_BTN`, `RWD_DLVR`, `STIM_TRIG`, `RHS_STIM_OUT` | |
| Analog sources | `A_` prefix | `A_EYE_LX`, `A_EYE_LY`, `A_EYE_RX`, `A_EYE_RY`, `A_EYE_LP`, `A_EYE_RP`, `A_PD1`, `A_PD2`, `A_AMB`, `A_ACC`, `A_JOY_X`, `A_JOY_Y`, `A_MIC`, `A_MISC1`…`A_MISC3` |
| Analog destinations | `_NI`, `_TPC` suffix; mux `MUX_OUT1`…`MUX_OUT8`; Intan `INTAN_AO1`…`INTAN_AO8` | `A_PD1_NI` |
| Power | `+12V`, `-12V`, `+5V`, `+3V3`, `AGND`, `DGND`, `NI_5V`, `NI_GND`, `ISO_P12`, `ISO_N12`, `INTAN_GND` | |

---

## Track 0 — schedule actions that must not wait

These are independent of every design task below and are on the critical path. Do them first.

### Task 0: Unblock procurement **[human]**

**Files:** `hardware/procurement-check.md` (create)

**Interfaces:**
- Produces: confirmed lead times feeding the Task 13 BOM lock and the schedule in spec §10.

- [ ] **Step 1: Order the NI cards**

12–13 week lead time, fixed and independent of this board's pace. **Verified against NI's own
product pages 2026-08-13; both Active.**

| Qty | Part | Each | For |
|---|---|---|---|
| 2 | **PXIe-6353** | $2,999 | recording chassis, one per rig |
| 2 | **PCIe-6343** | $1,880 | task PC, one per rig |
| 8 | `SHC68-68-EPM` | — | two per card; easily forgotten, same lead time |

**Not the 6363s the spec originally named.** Both selected cards carry 48 DIO with 32
hardware-timed lines on P0.<0..31> and two 68-pin connectors — identical to the 6363 on every
axis this design uses. The recording card runs 640 kS/s of 1.25 MS/s at a 40 kHz scan; the task
PC card runs ~18 kS/s of 500 kS/s. The 6363's extra rate is unused, and dropping a tier saves
$4,884 across two rigs. Reasoning in spec §9.3.

**One residual to raise with the NI rep:** SpikeGLX names the 6341, 6363 and 6366 as tested and
does not name the 6353. It presents identically through DAQmx, so the risk is low — but if you
want it eliminated, the recording card is where to spend the difference, not the task PC.

- [ ] **Step 2: Check stock and lead time on the schedule-critical connectors**

Query Digi-Key and Mouser for availability and lead time on each. Record the result and the date
checked in `hardware/procurement-check.md`.

| Part | Qty/board | Note |
|---|---|---|
| 68-pin MDR male, right-angle PCB mount | 4 | The risk item — 4–8 wk if unstocked |
| BNC, right-angle PCB mount, **isolated** | 30 | Volume makes even a short lead time matter. Isolated shells are required — see spec §5.6 |
| 3.5 mm TRS, PCB mount | 1 | remote reward button |
| **M12 A-coded 5-pos**, PCB solder, panel mount | 1 | `M12A-05PFFP-SF8001`. Keyed and screw-locking. Replaced the mini-DIN, which was discontinued **and** whose footprint was geometrically wrong |

- [ ] **Step 2b: Order ONE physical sample of each custom-footprint connector, now**

MDR68 male right-angle and the **M12 A-coded 5-position** inlet. (The mini-XLR samples are no longer needed — all rig-facing sensors became BNC once the Faraday cage was known about, which deleted those footprints and the topology risk with them.) These are the only
parts on the board with **custom footprints drawn from secondary sources** rather than from a
manufacturer CAD drawing, and they are the parts whose panel cutouts are machined to match. A
footprint error here scraps a panel rather than causing a rework.

Two dimensions could not be closed from any retrievable document and must be **measured against
the physical part** before Task 14 finalises placement: MDR68 row and mounting-hole spacing, and
the mini-XLR panel-cutout diameter.

**The topology risk was confirmed real and then designed out.** Task 7 pulled the manufacturer's
own datasheet and found the mini-DIN contacts **clustered in an arc, not evenly spaced** — the
exact failure mode flagged earlier as hypothetical. Both mini-DIN footprints had been drawn on
the even-spacing assumption and would very likely have failed to mate. The part was also
discontinued, so rather than redraw geometry for something nobody can buy, the inlet was
**re-selected**: an `M12A-05PFFP-SF8001`, A-coded and screw-locking, contact geometry taken from
IEC 61076-2-101 itself. Both wrong footprints and the even-spacing generator function were
deleted rather than deprecated.

**Still verify the M12 against a physical sample before machining.** Its panel cutout and shell
dimensions could not be pulled from Amphenol's own drawing, which is behind a scripted viewer, so
they carry the same low-confidence flag every other custom footprint here does.

Samples cost a few dollars and arrive long before the production order. Buying them with the
stock check costs nothing and closes the largest remaining fab risk.

- [ ] **Step 3: Record the outcome**

Write `hardware/procurement-check.md` with a row per part: manufacturer part number,
distributor, stock quantity, quoted lead time, date checked. If any part exceeds 4 weeks, flag
it — it changes the schedule in spec §10.3 and may force an alternate footprint.

- [ ] **Step 4: Resolve the four spec open items that gate schematic capture**

None of these can be answered from the design; each blocks a specific later step. Record answers
in `hardware/procurement-check.md` under a "Resolved before schematic" heading.

| Spec item | Question | Gates | If unresolved |
|---|---|---|---|
| §12.2 | Does SpikeGLX expose **NRSE** as an NI terminal configuration? | Task 10 Step 2 | Decision 5 collapses: without NRSE, 16 sources need 16 differential channels and NI has exactly 16, leaving zero headroom. Check SpikeGLX's NI setup dialog or its documentation |
| §12.5 | What is the **accelerometer's output range**? | Task 10 Step 1 | Sets the front-end scaling to the ±5 V convention. Ask whoever is building the custom device |
| §12.7 | Do the **misc analog ports need to be outputs** as well as inputs? | Task 10 Step 1 | An output-capable port is a different circuit — a driven buffer rather than a protected input |
| §12.9 | Are **asymmetric comparator make/break** thresholds wanted? | Task 10 Step 5 | Costs a second DAC channel per comparator; the `MCP4728` has four, so three asymmetric channels need a second DAC |

- [ ] **Step 5: Commit**

```bash
git add hardware/procurement-check.md
git commit -m "docs(hw): connector lead times and pre-schematic open items"
```

---

## Track 1 — the event-path mule

Small, fast, and it validates the riskiest electrical assumption while the main board is still
being drawn. Everything here is throwaway hardware whose output is an answer.

### Task 1: Toolchain and repository skeleton **[agent]**

**Files:**
- Create: `hardware/README.md`, `hardware/lib/wl-sync.kicad_sym`, `hardware/lib/wl-sync.pretty/`
- Create: `hardware/.gitignore`
- Modify: repository `.gitignore` if it swallows `*.bak` or KiCad lock files

**Interfaces:**
- Produces: a working `kicad-cli` and a library path every later task references.

- [ ] **Step 1: Install KiCad**

```bash
brew install --cask kicad
```

- [ ] **Step 2: Verify the CLI is on PATH and usable headlessly**

```bash
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli version
```

Expected: `10.0.5` or later. If `kicad-cli` is not on `PATH`, add a shim rather than relying on
the full path in later steps:

```bash
ln -sf /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli /opt/homebrew/bin/kicad-cli
kicad-cli version
```

- [ ] **Step 3: Create the hardware tree and a KiCad-aware gitignore**

`hardware/.gitignore`:

```gitignore
*-backups/
*.kicad_prl
fp-info-cache
~*.lck
*.bak
*.kicad_pcb-bak
*.kicad_sch-bak
```

- [ ] **Step 4: Write `hardware/README.md`**

Electrical facts only — this repository is public. Cover: what the board is, the two tracks
(mule and breakout), how to regenerate fab outputs with `kicad-cli`, and a pointer to the spec.
Do not reproduce private spec text.

- [ ] **Step 5: Commit**

```bash
git add hardware/ .gitignore
git commit -m "chore(hw): KiCad project skeleton and library paths"
```

### Task 2: Mule schematic **[agent]**

**Files:**
- Create: `hardware/mule/mule.kicad_pro`, `hardware/mule/mule.kicad_sch`

**Interfaces:**
- Consumes: library paths from Task 1.
- Produces: the level-shift and optocoupler topology that Tasks 6 and 11 reuse verbatim on the
  main board. If the mule's topology fails bring-up, those tasks change.

- [ ] **Step 1: Capture the inbound path — task PC to Pi**

Seventeen lines. `SN74LVC541APW` on +3V3, **three packages** — the '541 is an *octal* buffer, so
a package carries 8 channels, not the 20 its pin count suggests; two packages cover 16 of the 17
lines and strand the strobe. Each input gets 100 Ω series and a BAV99 clamp to +3V3/DGND, wired
as the series pair it is: signal on the midpoint, low side to DGND, **high side to +5V — not
to +3V3**. The '541 is an LVC part precisely because its inputs tolerate 5.5 V independent of
its supply, and that is what lets a 5 V DAQ drive a 3.3 V-powered buffer at all. A clamp to
+3V3 destroys that property: every normal logic high would sink ~13.5 mA through the diode,
over half the DAQ pin's rating, and ~230 mA across 17 lines into a rail with no way to sink it.
Referenced to +5V the diode never conducts in normal operation, and 5 V + Vf still sits under
the part's 6.5 V absolute-maximum input, so a real overvoltage fault is still caught.

Nets in: `EVT_D0_TPC`…`EVT_D15_TPC`, `EVT_STROBE_TPC` on a 2×20 IDC header.
Nets out: `EVT_D0_PI`…`EVT_D15_PI`, `EVT_STROBE_PI` on a 2×20 (40-pin) header **carrying the
real Pi 5 GPIO map** — data on GPIO0–15, strobe on GPIO16, at their actual header positions.
Bring-up check 7 runs PIO capture, which reads a contiguous GPIO range, so a sequentially
assigned header makes the mule's most valuable test impossible to run. Series resistors on
GPIO0/1 for the boot-time I²C probe.

- [ ] **Step 2: Capture the outbound path — Pi to 5 V equipment**

One `SN74HCT541PW` on +5V driving four test outputs from Pi GPIO17 (`BARCODE_PI` →
`BARCODE_OUT`), to prove the 3.3 V-in/5 V-out direction.

- [ ] **Step 3: Capture the isolated path**

Four optocoupler channels (one quad package) carrying `EVT_STROBE`, `BARCODE`, and two spares
into an isolated domain powered by a **`TMA-0505S`**-class isolated DC-DC (5 V in, isolated 5 V
out — a ±12 V dual-output part cannot produce this rail), terminating on a 4-way screw terminal.
Proves the isolation topology and lets the bench measure propagation delay and edge quality.

**The optocoupler must be logic-output** — `Isolator:HCPL-4661` or equivalent quad — **not a
phototransistor part such as PC847.** The main board mandates logic output because a
phototransistor's edge rate varies with load and with current-transfer ratio, the parameter that
drifts as the LED ages. Measuring the wrong part class here produces numbers that do not
transfer to the board this mule exists to de-risk.

- [ ] **Step 4: Capture power**

+5 V barrel jack in, `LD1117S33TR` to +3V3, decoupling of 100 nF per IC plus 10 µF bulk per rail.

- [ ] **Step 5: Run ERC**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/mule/erc.rpt hardware/mule/mule.kicad_sch
```

Expected: exit code 0, no unconnected pins, no power-input-not-driven.

- [ ] **Step 6: Commit**

```bash
git add hardware/mule/
git commit -m "feat(hw): mule schematic — 17-line event path, both shift directions, optos"
```

### Task 3: Mule layout and fab outputs **[human-led, agent produces placement]**

**Note on execution, corrected during execution:** this task was originally marked `[agent]` while
Task 14 was marked `[human-led]` for identical reasons — an inconsistency. `kicad-cli` exposes
`drc`, `export`, `import`, `render` and `upgrade` but **no Specctra DSN export**, so there is no
headless round-trip to an external autorouter and no way to route a board from the command line.
The agent generates the board file with outline, footprints placed to the floorplan (including
the isolation slot) and the netlist imported; a person routes it in pcbnew and runs DRC.
Placement is the design-critical half and stays with the agent; routing twelve parts on a
100 × 80 mm two-layer board is mechanical and pleasant work.

**Files:**
- Create: `hardware/mule/mule.kicad_pcb`, `hardware/mule/fab/`, `hardware/mule/mule-bom.csv`

**Interfaces:**
- Consumes: netlist from Task 2.
- Produces: orderable gerbers.

- [ ] **Step 1: Lay out a 100 × 80 mm 2-layer board**

IDC headers on opposite edges. Isolated domain in its own corner with a **≥2.5 mm clearance slot
under the optocouplers** — this is the barrier geometry the main board reuses at scale.

- [ ] **Step 2: Run DRC**

```bash
kicad-cli pcb drc --exit-code-violations -o hardware/mule/drc.rpt hardware/mule/mule.kicad_pcb
```

Expected: exit code 0.

- [ ] **Step 3: Generate fab outputs**

```bash
kicad-cli pcb export gerbers -o hardware/mule/fab/ hardware/mule/mule.kicad_pcb
kicad-cli pcb export drill   -o hardware/mule/fab/ hardware/mule/mule.kicad_pcb
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN,Qty" \
  -o hardware/mule/mule-bom.csv hardware/mule/mule.kicad_sch
```

- [ ] **Step 4: Visually verify the gerbers**

```bash
kicad-cli pcb render --side top -o /tmp/mule-top.png hardware/mule/mule.kicad_pcb
```

Read the render. Confirm the isolation slot is present and unbroken, and that no copper crosses
it.

- [ ] **Step 5: Commit**

```bash
git add hardware/mule/
git commit -m "feat(hw): mule layout, DRC clean, fab outputs"
```

### Task 4: Order and assemble the mule **[human]**

- [ ] **Step 1: Order 5 bare boards** from a prototype service, standard 2-layer service.
- [ ] **Step 2: Order the BOM** from `hardware/mule/mule-bom.csv`, plus a spare of each IC.
- [ ] **Step 3: Hand-assemble one board.** Passives first, then TSSOP ICs, then connectors.
- [ ] **Step 4: Continuity-check before powering.** With no ICs seated if using sockets: confirm
  no short between +5V, +3V3 and DGND; confirm the isolated domain has **no continuity of any
  kind** to DGND (this is the check that proves the barrier).

### Task 5: Mule bring-up **[human, procedure written by agent]**

**Files:**
- Create: `hardware/mule/bringup.md`

**Interfaces:**
- Produces: a pass/fail verdict on the level-shift topology that Tasks 6 and 11 depend on.

- [ ] **Step 1: Write the bring-up procedure**

`hardware/mule/bringup.md` with these acceptance criteria, each with a place to record the
measured value:

| # | Check | Pass criterion |
|---|---|---|
| 1 | Rails under load | +5 V ±5%, +3.3 V ±5% |
| 2 | Inbound level shift | 5 V input produces 3.3 V ±0.2 V at the Pi header, no overshoot above 3.6 V |
| 3 | Outbound level shift | 3.3 V input produces >4.5 V out |
| 4 | Isolation barrier | >10 MΩ between DGND and the isolated ground |
| 5 | Optocoupler propagation delay | recorded; constant across channels within 10 µs |
| 6 | Strobe edge quality | rise and fall <5 µs, monotonic, no ringing above logic thresholds |
| 7 | **PIO capture** | with `wl_sync` running on the sync-box module (CM5 or Pi 5 — same RP1, so either is valid), 10-minute run with **zero dropped words** and timestamps within ±100 µs |

- [ ] **Step 2: Run checks 1–6 with a scope.** Record every measured value in the file.
- [ ] **Step 3: Run check 7 against the existing `wl_sync` code.** This is the same acceptance
  gate the sync-box plan defines for its RP1 backend; the mule is what makes it runnable.
- [ ] **Step 4: Record the verdict and commit.**

```bash
git add hardware/mule/bringup.md
git commit -m "test(hw): mule bring-up results"
```

**If check 2 or 7 fails, stop and re-plan.** Those are the assumptions the main board's entire
digital section rests on, and discovering a problem here is the whole point of building the
mule.

---

## Track 2 — the breakout board

Tasks 6–11 are schematic sheets. They are independent and can be written in any order, but each
declares the nets it produces so later sheets can consume them.

### Task 6: Symbols, footprints, and the root sheet **[agent]**

**Files:**
- Create: `hardware/breakout/breakout.kicad_pro`, `hardware/breakout/breakout.kicad_sch`
- Modify: `hardware/lib/wl-sync.kicad_sym`, `hardware/lib/wl-sync.pretty/`

**Interfaces:**
- Produces: symbols and footprints every later sheet instantiates; the sheet hierarchy.

- [ ] **Step 1: Draw the custom symbols**

Not in KiCad's stock libraries: 68-pin MDR male, M12 A-coded 5-position,
the ACCESIO 37-pin D source, the Pi 5 40-pin header (with GPIO numbers as pin names, not BCM
positions — this is what makes the map in spec §4 checkable by eye).

- [ ] **Step 2: Draw the matching footprints**

Take dimensions from each manufacturer's drawing, not from a similar part. **Panel-mount
positions are the tightest tolerance on this board** — the enclosure is machined to match, so a
footprint error becomes a scrapped panel.

- [ ] **Step 3: Create the root sheet with ten hierarchical sheet symbols**

`power`, `taskpc-digital`, `pi-interface`, `analog-frontend`, `analog-ni`, `mux-intan`,
`comparators`, `opto-ni`, `opto-intan`, `control-usb-i2c`.

- [ ] **Step 4: Run ERC on the empty hierarchy**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
```

Expected: exit code 0 (empty sheets produce no violations).

- [ ] **Step 5: Commit**

```bash
git add hardware/lib/ hardware/breakout/
git commit -m "feat(hw): breakout symbols, footprints, sheet hierarchy"
```

### Task 7: Power sheet **[agent]**

**Files:** Create `hardware/breakout/sheets/power.kicad_sch`

**Interfaces:**
- Produces: `+12V`, `-12V`, `+5V`, `+3V3`, `AGND`, `DGND`, `ISO_P12`, `ISO_N12`, `INTAN_GND`.
  Every other sheet consumes these.

- [ ] **Step 1: Analog inlet and rails**

5-position M12 A-coded carrying +12 V, −12 V, +5 V, GND, shield. Reverse-polarity protection per rail, bulk
10 µF + 100 nF at entry.

- [ ] **Step 2: Derive the logic rails**

`TPS7A4901` from +12 V to +5 V, then `LD1117S33` to +3V3. Linear throughout — no switchers, per
the global constraint.

- [ ] **Step 3: The single isolated supply**

A 2 W isolated ±12 V DC-DC producing `ISO_P12` / `ISO_N12` referenced to `INTAN_GND`, with a
pi filter (10 µF – ferrite – 10 µF) on each output rail and LDO post-regulation. Budget: 8
difference-amplifier channels at ~2 mA plus 6 optocoupler output stages.

- [ ] **Step 4: The star point**

`AGND` and `DGND` joined at exactly one net tie at the inlet. Place a `NetTie_2` symbol so ERC
sees them as distinct nets everywhere else — this is what makes a plane-split error visible as a
rule violation instead of as noise on a recording.

- [ ] **Step 5: Run ERC and commit**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
git add hardware/breakout/sheets/power.kicad_sch
git commit -m "feat(hw): power sheet — linear rails, one isolated supply, AGND/DGND star"
```

### Task 8: Task PC digital sheet **[agent]**

**Files:** Create `hardware/breakout/sheets/taskpc-digital.kicad_sch`

**Interfaces:**
- Consumes: `+3V3`, `+5V`, `DGND` from Task 7.
- Produces: `EVT_D0_PI`…`EVT_D15_PI`, `EVT_STROBE_PI`, `RWD_CMD`, `RWD_DLVR`, `STIM_TRIG`, and
  the `*_TPC` raw nets that Task 11's optocouplers consume.

- [ ] **Step 1: Place the two MDR68 connectors**

Connector 0 for analog + AISENSE, Connector 1 for digital. Assign pins from the 6363 pinout
confirmed in spec §12 open item 3 — **do not proceed on the assumed split**; confirm it first
against NI's device pinout document and record the source in a schematic text field.

- [ ] **Step 2: Inbound buffers — 19 channels**

`SN74LVC541APW` on +3V3 for the 16 data lines, strobe, `RWD_CMD` and `STIM_TRIG` — 19 channels,
so **three packages** at 8 channels each. 100 Ω series and BAT54S clamps on every input, wired as
a series pair with the low side to DGND and the **high side to +5V, not +3V3** — see Task 2 for
why clamping a 5 V-tolerant input to the 3.3 V rail is wrong. Exactly as validated on the mule.

**Also produce a 5 V buffered copy for the optocouplers.** A second bank of `74HCT541` on +5 V
emits `EVT_D0_BUF`…`EVT_D15_BUF`, `EVT_STROBE_BUF`, `RWD_CMD_BUF`, `STIM_TRIG_BUF`, which is
what Task 11's NI optocouplers consume. Without it those 22 LED loads sit directly on the task
PC's DAQ pins — within the card's per-pin rating, but loading 22 timing-critical lines to save
one package is a poor trade.

- [ ] **Step 3: Outbound buffers — 4 channels**

`SN74HCT541PW` on +5 V driving `PD1_COMP`, `PD2_COMP`, `ACC_TRIG` and `RHS_STIM_OUT` back to the
task PC.

- [ ] **Step 4: Reward OR**

`RWD_CMD` and `RWD_BTN` into a `74HCT32`. `RWD_BTN` is first debounced: 10 kΩ pull-up, button to
ground, 100 nF to DGND, into a **single** `74HCT14` Schmitt inverter.

**A single inverter, not a pair — corrected 2026-08-14.** This step originally said "inverter
pair". Trace it: button open → pull-up → HIGH → inverter → LOW → inverter → **HIGH**. Two
inversions restore the polarity, so the debounced button would idle HIGH, and `HIGH OR anything`
is HIGH — **`RWD_DLVR` asserted continuously, the reward driver permanently open, and the
delivered line stuck true in every recording.** One inverter gives idle-low, active-high, which
is what an OR with an active-high `RWD_CMD` requires. State `RWD_CMD`'s assumed polarity on the
sheet so it is visible rather than inherited. OR output becomes `RWD_DLVR`, which drives the
reward-driver BNC and is buffered to Pi, NI and Intan.

- [ ] **Step 5: Run ERC and commit**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
git add hardware/breakout/sheets/taskpc-digital.kicad_sch
git commit -m "feat(hw): task PC digital sheet — 19 in, 4 out, reward OR"
```

### Task 9: Pi interface sheet **[agent]**

**Files:** Create `hardware/breakout/sheets/pi-interface.kicad_sch`

**Interfaces:**
- Consumes: `EVT_*_PI` from Task 8, `+3V3`/`DGND` from Task 7.
- Produces: `BARCODE_PI`, `CAM_TRIG_EYE`, `CAM_TRIG_BEH`.

- [ ] **Step 1: Place the 40-pin header and assign every pin**

Transcribe the GPIO map from spec §4 exactly. Add a schematic text block restating it so the
sheet is self-checking against the spec during review.

- [ ] **Step 2: Series resistors on GPIO0 and GPIO1**

330 Ω each. Annotate on the sheet: *"Pi probes GPIO0/1 as I²C at boot for a HAT ID; these
resistors limit contention. Requires `force_eeprom_read=0` in `config.txt`."*

- [ ] **Step 3: Buffer the three Pi outputs**

`BARCODE_PI`, `CAM_TRIG_EYE`, `CAM_TRIG_BEH` through `SN74HCT541PW` on +5 V. Barcode fans to
five loads (NI opto, Intan opto, and three spare positions); camera triggers fan to the BNC
outputs — one eye, four behavior.

- [ ] **Step 4: Route the internal USB header**

2.54 mm 4-pin header for the internal cable from a Pi USB-A port to the Task 12 bridge.

- [ ] **Step 5: Run ERC and commit**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
git add hardware/breakout/sheets/pi-interface.kicad_sch
git commit -m "feat(hw): Pi interface sheet — GPIO map, boot-contention resistors, trigger fan-out"
```

### Task 10: Analog sheets **[agent]**

**Files:**
- Create: `hardware/breakout/sheets/analog-frontend.kicad_sch`
- Create: `hardware/breakout/sheets/analog-ni.kicad_sch`
- Create: `hardware/breakout/sheets/mux-intan.kicad_sch`
- Create: `hardware/breakout/sheets/comparators.kicad_sch`

**Interfaces:**
- Consumes: `+12V`, `-12V`, `AGND`, `ISO_P12`, `ISO_N12`, `INTAN_GND` from Task 7.
- Produces: `A_*` source nets, `A_*_NI`, `A_*_TPC`, `INTAN_AO1`…`INTAN_AO8`, `PD1_COMP`,
  `PD2_COMP`, `ACC_TRIG`.

- [ ] **Step 1: Front ends — 16 sources**

Per source: **isolated** BNC, 1 kΩ series, BAV99 clamps to ±12 V, then a **difference-receiving
input stage** — not a plain buffer to `AGND`. Sensor connectors per spec §9.1; all rig-facing
sensors are BNC.

**Every front end senses its source against its own return, not against AGND** (spec §5.6). Each
BNC shell ties to `AGND` through **~10 Ω** to provide the return path and a DC reference, and the
input stage senses **centre against shell at the connector**. Use matched-resistor difference
amplifiers or integrated parts (`INA134`-class); a 4-resistor network needs 0.1% parts to reach
useful CMRR.

**Why, and it is not symmetry for its own sake:** the rig is inside a Faraday cage sound booth and
it is undecided whether the bulkhead bonds each shield to the cage shell. If it does, circulating
current flows in the shield — which is also the signal return — and a plain AGND-referenced
buffer would add that voltage straight to the signal. Differential receive turns it into common
mode. **The shield cannot simply be lifted instead: it is the return path.** This is what lets
the bulkhead decision be made, or reversed, without touching the board.

The **ACCESIO 37-pin D** brings in the six eye channels and gets the same treatment, sensed
against the ACCESIO's own AGND pins — it lives on a separately-earthed eye-tracker PC, so it has
a ground offset for the same reason.

The three misc inputs additionally get a ÷1/÷2 divider selected by a 3-pin jumper.

**Photodiodes are the exception in form, not in principle** (spec §6.3.1). They are passive, so
their input stage is a transimpedance amplifier rather than a difference amplifier: photodiode
across centre and shell, TIA summing junction on centre, **non-inverting input referenced to the
shell** rather than to AGND — which achieves the same rejection. Size compensation for **6 m** of
coax (~600 pF); tested range is 3–5 m. Photovoltaic mode, zero bias, since there is no power in
the booth. Follow with a low-pass that doubles as anti-aliasing.

- [ ] **Step 2: NI fan-out — 16 channels**

`OPA4192` buffer per channel, 100 Ω series to the MDR68. Run `AGND` to the connector's
**AISENSE** pin — this single wire is what makes NRSE work and is the easiest thing on the board
to omit by accident. Annotate it on the sheet.

- [ ] **Step 3: Task PC fan-out — 9 channels**

Same topology to the task PC's Connector 0. Channels: `A_EYE_LX`, `A_EYE_LY`, `A_EYE_RX`,
`A_EYE_RY`, `A_JOY_X`, `A_JOY_Y`, `A_MISC1`, `A_MISC2`, `A_MISC3`. AISENSE tied to `AGND` here
too.

- [ ] **Step 4: Mux bank and Intan difference amplifiers**

Eight `ADG1206YRUZ` on ±12 V, each with all 16 `A_*` sources on its inputs and `EN` tied high.
Address lines `MUX1_A0`…`MUX8_A3` (32 nets) go to Task 12. Each mux output feeds an `INA134`
difference amplifier whose reference is `INTAN_GND` and whose supply is `ISO_P12`/`ISO_N12`,
then a BNC.

- [ ] **Step 5: Comparators**

One `LM339` quad. Channels: `A_PD1`→`PD1_COMP`, `A_PD2`→`PD2_COMP`, `A_ACC`→`ACC_TRIG`, fourth
brought to `A_MISC1` unpopulated. 10 kΩ pull-ups; hysteresis by 1 MΩ feedback to the
non-inverting input. Threshold on each inverting input comes from an `MCP4728` quad I²C DAC.

- [ ] **Step 6: Run ERC and commit**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
git add hardware/breakout/sheets/
git commit -m "feat(hw): analog sheets — 16 front ends, NRSE fan-out, mux bank, comparators"
```

### Task 11: Optocoupler sheets **[agent]**

**Files:**
- Create: `hardware/breakout/sheets/opto-ni.kicad_sch`
- Create: `hardware/breakout/sheets/opto-intan.kicad_sch`

**Interfaces:**
- Consumes: `*_TPC` and Pi-sourced digital nets; `NI_5V`/`NI_GND` from the MDR68;
  `ISO_P12`/`INTAN_GND` from Task 7.
- Produces: `*_NI` and `*_INTAN` isolated copies.

- [ ] **Step 1: NI domain — 22 channels**

Quad logic-output optocouplers, SO-16, 6 packages (24 channels, 2 spare). Input side from
`DGND`, output side powered from `NI_5V` taken off the MDR68 connector. Channels: 16 data,
strobe, barcode, `RWD_CMD`, `RWD_DLVR`, `STIM_TRIG`, `RHS_STIM_OUT`.

**Part requirement rather than a fixed MPN**, because Task 0 gates availability: quad channel,
logic (totem-pole or open-collector) output rather than bare phototransistor, SO-16, ≥1 Mbd,
3.3–5 V output supply. Broadcom's `ACSL-6400` series is the reference candidate; confirm stock
before Task 13 locks the BOM, and if substituting, keep the logic-output requirement — a bare
phototransistor's edge rate varies with load and current-transfer ratio, which is exactly the
parameter that drifts as the LED ages.

**Fallback annotation:** if spec §12 open item 1 resolves against us and NI's +5 V cannot supply
the load, this domain takes a second isolated DC-DC. Leave the footprint.

- [ ] **Step 2: Intan domain — 6 channels**

One quad plus two channels of a second package. Outbound: strobe, barcode, `RWD_CMD`,
`RWD_DLVR`, `STIM_TRIG`. Inbound: `RHS_STIM_OUT` out of `INTAN_GND` into `DGND`.

- [ ] **Step 3: Set LED drive conservatively**

Size series resistors for roughly half the datasheet-recommended forward current. The timing
budget is hundreds of microseconds, so the speed cost is irrelevant and the current-transfer-
ratio margin over a decade of service is large.

- [ ] **Step 4: Run ERC and commit**

```bash
kicad-cli sch erc --exit-code-violations -o hardware/breakout/erc.rpt \
  hardware/breakout/breakout.kicad_sch
git add hardware/breakout/sheets/
git commit -m "feat(hw): optocoupler sheets — 28 isolated digital channels"
```

### Task 12: Control sheet and full-schematic verification **[agent]**

**Files:**
- Create: `hardware/breakout/sheets/control-usb-i2c.kicad_sch`
- Create: `tests/hardware/test_netlist.py`

**Interfaces:**
- Consumes: `MUX1_A0`…`MUX8_A3` from Task 10, the USB header from Task 9.
- Produces: a machine-checkable assertion that the schematic matches the spec.

- [ ] **Step 1: Capture the control chain**

`MCP2221A` USB-I²C bridge on the internal USB header, driving an I²C bus carrying two
`MCP23017` expanders (32 outputs → 32 mux address lines, exactly) and the `MCP4728` DAC. Set
distinct I²C addresses and record them in a schematic text field.

- [ ] **Step 2: Export the netlist**

```bash
kicad-cli sch export netlist --format kicadsexpr \
  -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
```

- [ ] **Step 3: Write netlist assertions**

`tests/hardware/test_netlist.py` — parse `breakout.net` and assert the spec's contract, so a
later edit that silently breaks it fails CI:

```python
import re
from pathlib import Path

NETLIST = Path(__file__).parents[2] / "hardware/breakout/breakout.net"


def nets():
    text = NETLIST.read_text()
    return {m.group(1) for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]+)"\)', text)}


def test_event_code_lines_reach_pi_and_ni():
    present = nets()
    for i in range(16):
        assert f"EVT_D{i}_PI" in present
        assert f"EVT_D{i}_NI" in present
    assert {"EVT_STROBE_PI", "EVT_STROBE_NI", "EVT_STROBE_INTAN"} <= present


def test_intan_gets_exactly_eight_analog_outputs():
    present = nets()
    assert {f"INTAN_AO{i}" for i in range(1, 9)} <= present
    assert "INTAN_AO9" not in present


def test_all_sixteen_sources_reach_ni():
    sources = [
        "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
        "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
        "A_MISC1", "A_MISC2", "A_MISC3",
    ]
    present = nets()
    assert len(sources) == 16
    for s in sources:
        assert f"{s}_NI" in present, f"{s} does not reach NI"


def test_reward_is_recorded_twice():
    assert {"RWD_CMD", "RWD_DLVR"} <= nets()


def test_agnd_and_dgnd_are_distinct_nets():
    present = nets()
    assert "AGND" in present and "DGND" in present
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/hardware/test_netlist.py -v
```

Expected: all pass. A failure here means a sheet does not implement the contract in spec §3.

- [ ] **Step 5: Run full ERC one final time**

```bash
kicad-cli sch erc --exit-code-violations --severity-all \
  -o hardware/breakout/erc.rpt hardware/breakout/breakout.kicad_sch
```

Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add hardware/breakout/ tests/hardware/
git commit -m "feat(hw): control sheet and netlist contract tests"
```

### Task 13: BOM lock and design review **[agent produces, human reviews]**

**Files:**
- Create: `hardware/breakout/breakout-bom.csv`, `hardware/breakout/design-review.md`

- [ ] **Step 1: Export the BOM**

```bash
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN,Manufacturer,Qty" \
  --group-by Value -o hardware/breakout/breakout-bom.csv hardware/breakout/breakout.kicad_sch
```

- [ ] **Step 2: Check every line against the Task 0 findings and against the hand-solder
  constraint.** No package finer than TSSOP, no passive below 0603, every MPN in stock or with a
  recorded lead time.

- [ ] **Step 3: Write the review checklist**

`hardware/breakout/design-review.md`, covering at minimum: no 5 V net reaches any Pi pin; AISENSE
tied to AGND on both NI connectors; AGND/DGND joined at exactly one net tie; every isolated
domain's supply referenced correctly; all four MDR68 pinouts verified against NI's document; the
GPIO map matches spec §4; every panel connector position matches the panel drawing.

- [ ] **Step 4: A person reviews and signs off in the file.** Do not proceed to layout without
  this — spec §10.3 has no slack for a respin.

- [ ] **Step 5: Commit**

```bash
git add hardware/breakout/
git commit -m "docs(hw): BOM lock and pre-layout design review"
```

### Task 14: Floorplan and layout **[human-led, agent produces constraints]**

**Files:**
- Create: `hardware/breakout/breakout.kicad_pcb`, `hardware/breakout/floorplan.md`

**Note on execution:** a 430 × 240 mm mixed-signal layout with 40 panel-critical connector
positions is interactive work. The agent produces the floorplan, the constraint set and the
verification commands; a person drives the tool.

- [ ] **Step 1: Write the floorplan**

`hardware/breakout/floorplan.md`, specifying: board outline to the 2U chassis interior; front
edge carries the rig-facing connectors (sensors, misc, camera triggers, reward, display sync,
manual button); rear edge carries 4× MDR68, 14 Intan BNC, the ±12 V inlet and the Pi's port
cutouts. Domain regions placed so each isolation barrier is a **single straight line** across
the board, never a zigzag: AGND front-centre, DGND centre, NI_GND and INTAN_GND each in their
own rear corner.

- [ ] **Step 1b: Place the Pi, its NVMe adapter, and the airflow path**

The sync box is a **CM5 Lite on the official CM5 IO Board**, booting from that board's onboard
M.2 socket — no PCIe flex cable, and never a GPIO-header HAT, whose ID EEPROM would claim GPIO0/1
and whose body would occupy the header the ribbon needs (spec §4.1). Position it **downstream of
the analog section in the airflow**, so its 10–15 W of exhaust does not wash over the analog
front ends or the difference amplifiers.

**This step decides whether the CM5 choice survives.** The IO Board is 160 × 90 mm against a Pi
5's 85 × 56 — about three times the shadow over a 430 × 240 mm board. It must sit over the rear
digital region only. **If it cannot, revert to a Pi 5 plus a PCIe-FPC NVMe adapter** and record
that in the floorplan. Either way the answer must precede panel machining, since the two options
place Ethernet, USB and power cutouts differently. Intake and exhaust are panel cutouts, so they belong in the
mechanical drawing from the start. If a fan is fitted, keep it away from the microphone preamp
and photodiode front ends — its motor is an electrical noise source as well as an acoustic one.

- [ ] **Step 2: Write the constraint set**

Board stack-up 4-layer (signal / GND / power / signal). ≥2.5 mm clearance slot under every
optocoupler and under the isolated DC-DC. Analog and digital plane split with the star tie at
the power inlet only. **The barcode net routed on the opposite side of the board from every
analog channel and kept away from the front edge** — it carries a high edge density and
coupling into a headstage is a QC failure on real data. Panel connector positions dimensioned
to ±0.1 mm.

- [ ] **Step 3: Place connectors first, everything else after.** Panel positions are the
  constraint; components serve them.

- [ ] **Step 4: Run DRC**

```bash
kicad-cli pcb drc --exit-code-violations --severity-all \
  -o hardware/breakout/drc.rpt hardware/breakout/breakout.kicad_pcb
```

Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add hardware/breakout/
git commit -m "feat(hw): breakout layout, DRC clean"
```

### Task 15: Panel drawings and fab outputs **[agent]**

**Files:** Create `hardware/breakout/panel/front.dxf`, `panel/rear.dxf`, `hardware/breakout/fab/`

- [ ] **Step 1: Export panel outlines from the board file**

```bash
kicad-cli pcb export dxf --layers User.Drawings \
  -o hardware/breakout/panel/ hardware/breakout/breakout.kicad_pcb
```

- [ ] **Step 2: Verify every cutout against its connector footprint.** Each panel hole must be
  derived from the placed footprint's position, not measured by hand. A mismatch scraps a panel.

- [ ] **Step 3: Generate fab outputs**

```bash
kicad-cli pcb export gerbers -o hardware/breakout/fab/ hardware/breakout/breakout.kicad_pcb
kicad-cli pcb export drill   -o hardware/breakout/fab/ hardware/breakout/breakout.kicad_pcb
kicad-cli pcb export pos --format csv --units mm \
  -o hardware/breakout/fab/breakout-pos.csv hardware/breakout/breakout.kicad_pcb
```

- [ ] **Step 4: Render and read both sides**

```bash
kicad-cli pcb render --side top    -o /tmp/breakout-top.png hardware/breakout/breakout.kicad_pcb
kicad-cli pcb render --side bottom -o /tmp/breakout-bot.png hardware/breakout/breakout.kicad_pcb
```

Confirm: isolation slots unbroken, no copper crossing them, barcode net away from the analog
region and the front edge.

- [ ] **Step 5: Commit**

```bash
git add hardware/breakout/
git commit -m "feat(hw): panel drawings and fab outputs"
```

### Task 16: Order, assemble, bring up **[human]**

**Files:** Create `hardware/breakout/bringup.md`

- [ ] **Step 1: Order 5 boards** (4-layer), the BOM, and 2 machined panel sets.
- [ ] **Step 2: Assemble one board.** Passives, then ICs, then connectors last — the connectors
  are the mechanically stiff parts and make earlier joints hard to reach.
- [ ] **Step 3: Before power, check with no ICs seated:** no short between any rail pair;
  >10 MΩ between DGND and each of NI_GND and INTAN_GND.
- [ ] **Step 4: Power the rails alone and measure each** against spec §8 before seating ICs.
- [ ] **Step 5: Run the acceptance tests**

| # | Check | Pass criterion |
|---|---|---|
| 1 | All rails under load | within ±5% |
| 2 | **No Pi pin exceeds 3.6 V**, all 17 event lines | pass/fail — check this before the Pi is ever connected |
| 3 | Isolation resistance, both domains | >10 MΩ |
| 4 | Every analog channel end to end | gain within 1%, offset <5 mV |
| 5 | NRSE rejection | inject 1 V between AGND and NI ground; NI-measured channel shifts <10 mV |
| 6 | Mux selection | each of 16 sources routable to each of 8 outputs under Pi control |
| 7 | Comparator thresholds | DAC-set threshold tracks within 2%, hysteresis measurable |
| 8 | Barcode coupling | record on Intan with a headstage attached; barcode-locked artifact below noise floor |
| 9 | Full event protocol | 10-minute run, zero dropped words, Pi and NI codes agree |

- [ ] **Step 6: Record results and commit.**

```bash
git add hardware/breakout/bringup.md
git commit -m "test(hw): breakout bring-up results"
```

**Check 2 is the one to run before anything else.** It is the only failure on this list that
destroys hardware rather than producing a measurement you dislike.
