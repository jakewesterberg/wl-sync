# Breakout board — pre-layout design review

Task 13. This is the checklist a person signs off in this file before layout (Task 14)
begins. It does not replace the automated contract (`tests/hardware/test_netlist.py`, the 13
structural checkers in `hardware/gen/`, and `kicad-cli sch erc`) — it exists because some of
what matters here cannot be checked by software at all, and because a human decision to
proceed is the thing spec §10.3 actually requires, not just a green test run.

> **REFRESHED 2026-08-16, and the reason matters more than the edits.** This document was
> first written *before* `parametric-audit.md` existed. That audit then found **21 findings,
> seven of which would have prevented the board working** — an under-driven optocoupler LED on
> 30 channels, a saturating transimpedance stage, an isolated supply over its per-rail rating,
> a fuse defeated by its own placement, 31 BNCs on a footprint pointing at the chassis lid.
> None of them was visible to any check this document was signing off on.
>
> **That is not a failure of the checklist below; it is a statement about what it covers.**
> Every automated item here asks a *topological* question — is this pin on the right net, do
> the isolated domains stay disjoint, is the bit order preserved. Every parametric-audit
> finding asked an *operating-point* question — does this current clear a datasheet minimum,
> does this converter's rating cover its load, does this footprint's axis match the enclosure.
> A board can be perfectly connected and still not work.
>
> All 21 findings are now closed (`parametric-audit.md`), and the gap is partly closed
> structurally too: `tests/hardware/test_netlist.py` gained parametric assertions with
> negative controls, and `check_footprint_geometry.py` reads real `.kicad_mod` files off disk
> — nothing in this toolchain read footprint geometry at all before, which is how a 0.5 mm
> drill for a 0.85 mm pin survived seventy-odd commits.
>
> **Read the closing section before signing.** Its original conclusion — that nothing found
> rose to the "stop, do not proceed" bar — was true of the review that produced it and was
> overtaken within the day.

## The gate, stated plainly

**Layout must not start until a person has read this document, confirmed the items below,
and signed the block at the bottom.** This is not a formality. Spec §10.3 gives this board
"almost no slack for a respin" — the fab-to-bench-test window is 10–14 weeks against a
January deadline, and a defect caught after fab costs 4–6 weeks, which the schedule does not
have. Every automated check in this repository proves the schematic is *internally
consistent* with a contract someone wrote down; it cannot prove that contract is the right
one, and it cannot look at a physical part. That is what this review, and the person signing
it, are for.

**Do not skip straight to Task 14 because every box below can be checked "automated."** The
automated items are automated *because* a real defect got through ERC and into a committed
sheet at least once already on this board (see each item's own history below) — they are
proof this project's own past mistakes don't recur, not proof there are no new ones.

---

## Checklist

Each item states what it protects, whether it is automated (and by what), and what a human
still needs to do. Check items **only** after independently confirming them — re-reading this
document is not confirmation.

### Electrical — covered by the automated contract

- [ ] **No `+5V` net reaches a sync-module GPIO pin.** The module (Raspberry Pi 5 / CM5 IO
  Board) is 3.3 V and not 5 V tolerant. This exact hazard reached two committed sheets by
  omission during the build — `RWD_DLVR`'s own `+5V` origin at `pi-interface.kicad_sch` (Task
  9) and the comparator pull-up domain at `comparators.kicad_sch` (Task 10d) — before being
  caught.
  **Automated:** `tests/hardware/test_netlist.py::test_no_5v_reaches_sync_module_gpio`
  (contract check #6 of 12), with a negative control that adds a direct `+5V`→GPIO short and
  confirms the check fires. Currently passing.
  **Human step:** none required beyond confirming the test ran clean in this task's
  verification (it did — see the report).

- [ ] **Every comparator pull-up is on `+3V3`, never `+5V`**, for the identical reason — all
  three comparator outputs (`PD1_COMP`, `PD2_COMP`, `ACC_TRIG`) wire directly into the sync
  module's GPIO. Same class of defect as above, caught the same way during the build.
  **Automated:** `test_comparator_pullups_on_3v3_never_5v` (contract check #7), plus the
  identical check extended to the I²C bus pull-ups added at Task 12
  (`test_i2c_pullups_on_3v3_never_5v`, check #8) — both with negative controls (moving one
  pull-up to `+5V` and confirming the failure). Currently passing.

- [ ] **`AISENSE` tied to `AGND` on both NI connectors** (task-PC NI and recording NI each
  carry one) — the single wire that makes NRSE work (spec §5.5, decision 5 in §2): without
  it, NI's own ground rejection has nothing to reject against, silently. This was dropped
  once, at Task 8, and only caught in that task's own fix round 1.
  **Automated:** `test_aisense_tied_to_agnd_on_every_ni_connector` (contract check #9) —
  checks the real physical AISENSE pin (NI connector pin 62, both cards) is on `AGND`, not
  just that both nets exist. Negative control drops the tie on one connector and confirms
  the failure names it. Currently passing.

- [ ] **`AGND` and `DGND` joined at exactly one net tie.** Two ground planes bridged in more
  than one place reintroduces the loop current the whole split exists to avoid.
  **Automated two ways:** `check_breakout_power_netlist.py` confirms `power.kicad_sch` places
  exactly one `NetTie_2`; `test_agnd_dgnd_share_no_pin` (contract check #11) confirms `AGND`
  and `DGND` are not *also* shorted somewhere else on the board (share no pin outside that
  tie). **Independently re-confirmed this task**, directly against `breakout-bom.csv`: the
  whole-board BOM lists exactly one `NT`-prefixed reference (`NT1`, value `NetTie_2`) — if a
  second net tie existed anywhere on the board, `--group-by Value` would show it on the same
  row (`NT1,NT2`) or a second row, and neither appears.

- [ ] **Both isolated domains pin-disjoint from non-isolated rails, except by-design
  straddlers.** `NI_5V`/`NI_GND` and `ISO_P12`/`ISO_N12`/`INTAN_GND`/`ISO_5V` must never share
  a physical *pin* with `+12V`/`-12V`/`+5V`/`+3V3`/`AGND`/`DGND` — a component that
  legitimately has pins on both sides by design (an optocoupler's LED vs. detector side, the
  isolated DC-DC's primary vs. secondary, an INA105's `-`/signal input vs. its own REF/power)
  is fine; a single pin present on both sides is always a real short.
  **Automated:** `test_isolated_domains_pin_disjoint_from_non_isolated` (contract check #10),
  checked at the pin level specifically so legitimate straddlers never trigger it (see the
  test's own docstring) — with a negative control (a phantom pin forced onto both an isolated
  and a non-isolated rail) confirming it fires. Currently passing.

### Electrical — automated, but sourced from a single transcription (spot-check recommended)

- [ ] **All four MDR68 pinouts verified against NI's X Series User Manual (370784K-01),
  Figures A-5 and A-18.** Wiring the wrong physical pin to a signal is invisible to ERC (a
  transposed pin is still a fully-connected, 0-error netlist) and would only surface on a
  bench during bring-up.
  **Automated, self-consistency + negative controls:** `check_taskpc_digital_netlist.py`
  (task-PC Connector 1, Fig. A-5), `check_breakout_analog_ni_netlist.py` (recording NI
  Connector 0's AI channels, Fig. A-5/A-18 cross-checked), and
  `check_breakout_opto_ni_netlist.py` (recording NI Connector 1, Fig. A-18) each assert every
  signal lands on its documented physical pin, with a negative control (a permuted pin) per
  file, confirmed firing.
  **What this does NOT cover:** every one of those checkers' own pin tables was typed out
  once from a single reading of the NI manual (each checker's table is independently
  *re-typed*, not code-imported from its sibling generator — real drift protection — but all
  ultimately trace to one transcription pass, not two people reading the PDF separately). A
  shared misreading of the manual would pass every automated check here.
  **Human step — do this before layout, not after:** pull NI manual 370784K-01 yourself,
  open Figures A-5 and A-18, and spot-check a handful of pins per connector (at minimum: the
  AISENSE pin, one AI channel, one P0.x line, one D GND) against
  `check_taskpc_digital_netlist.py`'s `MDR1_PIN_BY_P0`/`MDR0_AI_PIN` and
  `check_breakout_analog_ni_netlist.py`'s `MDR0_AI_PIN` tables. Cheap, and this is exactly the
  failure class ("correct-looking, self-consistent, silently wrong") every other check in
  this project is designed to catch — except this one link in the chain, where it can't.

- [ ] **The GPIO map matches spec §4**, with the event-code range contiguous on GPIO0–16 and
  camera triggers on the hardware-PWM pins (18, 19).
  **Automated, and materially stronger than the MDR68 case above:**
  `check_breakout_pi_interface_netlist.py`'s GPIO-physical-pin walk re-derives the
  GPIO→physical-pin table from scratch (explicitly *not* imported from the generator's own
  `GPIO_PIN_SPEC`) and cross-checks it three independent ways before use — against
  `gen_mule.py`'s own table (GPIO0–17), against `gen_wl_sync_lib.py`'s independently-built,
  inverted table (GPIO0–27), and against the real exported netlist from the committed sheet.
  Every GPIO's signal is walked to its real physical pin; a negative control (moving
  `EVT_D5_PI` to GPIO6's own physical pin) confirms the failure fires and names the wrong
  pin. Currently passing.
  **Human step:** given the three-way cross-check, a spot-check here is lower-value than for
  MDR68 above — optional, not required for sign-off.

### Electrical — automated at the sheet level

- [ ] **The `Conn_02x03` jumper's pin numbering matches its footprint's real pad adjacency.**
  A symbol/footprint mismatch here previously (fix round 2 → fix round 3, task-10b-report.md)
  made every reachable shorting-block position leave one leg's INA105 input floating —
  invisible to every netlist/connectivity check, because a jumper header's own pins carry no
  connectivity until a shunt is added.
  **Automated:** `check_breakout_analog_frontend_netlist.py`'s
  `verify_footprint_pad_adjacency()` reads the real `.kicad_mod` pad geometry directly (not
  the schematic symbol's art) and asserts the four pin-pairs this design's shorting blocks
  rely on are actually at the header's own minimum pitch. Negative control (a jumper pin
  moved off its real footprint position) confirmed firing. **Ran clean in this task's own
  verification** — see the report.
  **Human step:** none required; this is the one item in this list that is fully closed by
  tooling, because a reviewer already found and fixed the exact failure mode it checks for.

### Electrical — layout-stage, not checkable from the schematic

- [ ] **AHCT541's faster edges on the MDR68 cable runs warrant a series-termination
  review during placement.** `U8`–`U11` and `U15` moved from `SN74HCT541PW` to
  `SN74AHCT541PW` (Value/MPN only — identical pinout and TSSOP-20 footprint, 8 mA IOL vs
  6 mA, see `hardware/README.md`'s own per-driver-pin load table and its footnote). AHCT
  is a faster logic family than HCT (shorter propagation delay, faster edge rates at the
  same load), and every one of these five packages drives a signal that leaves the board
  over an MDR68 cable run (the `_BUF`/`_INTAN_BUF`/outbound nets, `EVT_D0..15` and the
  rest of the digital contract) — a load dominated by cable inductance/capacitance, not
  the on-board trace. Faster edges into that load raise (not create) the odds of
  reflections/ringing on an unterminated or under-terminated run. This is not checkable
  from a netlist or schematic — it depends on real trace length and the cable's own
  characteristic impedance, neither of which exists before layout.
  **Not automated; nothing to run today.**
  **Human step, during Task 14 layout, not before:** when routing the five buffer
  packages' own outputs toward `J3`/`J45` (the MDR68 digital connectors), size and place
  series-termination resistors (typically 22–33 Ω, close to the driver) if trace length
  and observed ringing warrant it — a bench/simulation call at layout time, not a
  schematic-capture one. Flagged here so it is not silently missed between "the part
  swap is done" and "the board is routed."

### Mechanical — cannot be automated

- [x] **RESOLVED 2026-08-16 — both custom footprints are now built from primary drawings,
  and one of them was unbuildable.** This item used to read "verify against a physical
  sample", with MDR68's row spacing (2.84 mm) and mounting-hole spacing (57.9 mm) flagged
  **Low-confidence** from secondary sources. Getting the actual drawing changed the picture
  entirely: `MDR68_Male_RightAngle` drilled its contacts at **0.5 mm against a required
  0.85 mm** — *the pins do not fit*, so the board could not have been assembled — and laid
  them out as 2 rows at 2.84 mm where MH drawing 3700-0121-01 rev 3.0 shows **4 staggered
  rows of 17 at 1.905 mm**. Both were invisible to ERC, to every netlist checker, and to the
  whole test suite.

  Rebuilt from that drawing; `M12A_5_Panel` deleted outright under ruling R1 (the procured
  Phoenix 1551833 has no PCB tails at all, so no board-mount footprint could have been
  right); `BNC_Dual_RA_Isolated` built from Amphenol drawing 31-6575 rev A. Every dimension
  now comes from `[mdr68]` / `[bnc_dual_isolated]` in `hardware/datasheet-params.toml` and is
  **asserted against the real `.kicad_mod` on disk** by `check_footprint_geometry.py`.

  **The lesson to carry into Task 14:** "corroborated across five independent distributor
  listings" produced a footprint that could not be fabricated. A caliper on a sample would
  have caught it; so would the drawing, sooner and cheaper.

- [ ] **The MDR68 tail-row-to-contact-number map — the one genuine unknown left, and the only
  unrecoverable one.** MH rev 3.0 gives the tail *geometry* but not which contact numbers
  land in which of the four staggered rows. Wrong, it scrambles 68 signals on a board that
  passes ERC and every test. It is smaller than it sounds: the tails run straight back from
  the mating contacts, so each contact's X is fixed and only depth is redistributed — **four**
  candidate arrangements, not twenty-four, all written out beside `MDR68_TAIL_ORDER` in
  `hardware/gen/gen_wl_sync_footprints.py`. The footprint's own `descr` carries
  `PIN MAP UNVERIFIED` and `check_footprint_geometry.py` asserts that note is still present,
  so it cannot be lost track of.
  **Human step, required before fab — not before Task 14:** open MH's own drawing or 3D model
  and confirm the arrangement, then record the answer in
  `hardware/breakout/d3-panel-thickness.md`. Minutes with a document already in hand.
  Switching is a one-line edit and a regenerate.

- [ ] **The dual BNC's protrusion through the panel.** Not dimensioned on drawing 31-6575
  rev A, and it sets the board's set-back from the panel — see
  `hardware/breakout/panel-elevations.md` §3. Measure on a sample or take it from the 3D
  model.
  **Human step:** needed before Task 14 fixes the board outline, not before it starts.

### Mechanical — deferred to Task 15, not checkable yet

- [ ] **Every panel connector position matches the panel drawing.** This is one of the
  brief's own checklist items (`docs/superpowers/plans/2026-08-13-breakout-pcb.md`, Task 13
  Step 3) and it is **deliberately not checked in this review, not silently dropped.**

  *Partly closed 2026-08-16:* `hardware/breakout/panel-elevations.md` now exists — face
  allocation, horizontal budget and the full machining schedule, all sourced from
  `datasheet-params.toml`. It is an **elevation, not a DXF**, so it fixes what goes on which
  face and how wide it all is, and it flags that both faces fit with only ~25–28 mm of slack
  across a 450 mm span. What it cannot do is check placed coordinates, because there is still
  no `breakout.kicad_pcb`. The rest of this item stands: Task 15 (`hardware/breakout/panel/
  front.dxf`, `panel/rear.dxf`) is the task that produces the panel DXF, and Task 14
  (layout — has not run as of this review) is what first places connectors at real board
  coordinates. Checking connector-position-vs-panel-drawing agreement at Task 13 is not
  possible in principle, not merely undone — the two artifacts being compared don't exist
  yet.
  **Human step, required before panels are machined — not before Task 14 starts:** Task
  15's own Step 2 ("Verify every cutout against its connector footprint... A mismatch scraps
  a panel") is where this item actually gets checked, against real data, for the first time.
  Whoever signs off after Task 15 runs must independently confirm every connector position
  in the panel DXF matches its footprint's real placed coordinates in `breakout.kicad_pcb`
  — with the same "independently confirmed, not merely read" discipline every other item in
  this document requires — before the panel is sent to be machined. A clean Task 15 run is
  not itself that confirmation.

### Procurement — see `hardware/procurement-check.md` for full detail

- [ ] **`ADG1206YRUZ-REEL7` — corrected, fix round 1: not scarce.** DigiKey itself still
  shows 0 units (11-week lead, next restock late Nov/Dec 2026), but Arrow (713 units) and
  LCSC (86 units) both stock the exact `-REEL7` SKU today, and a pin-compatible successor
  (`ADG5206`) is stocked directly at DigiKey too. See `hardware/procurement-check.md`
  §5.1/§6 for the full correction.
  **Human step:** order from Arrow or LCSC (or DigiKey's own November restock) — no longer a
  schedule-pacing risk, but still confirm supply before the production order.
- [ ] **MDR68 (`3700-0121-01`) — corrected, fix round 1: a watch item, not the widest error
  bar.** Still not found at DigiKey or Mouser, but RS Components and Distrelec both show
  real regional stock (~541 units at RS, search-result level, not an authenticated lookup)
  that the original check couldn't retrieve. See `hardware/procurement-check.md` §5.2/§6.
  **Human step:** a direct account-based stock/lead-time query at RS or Distrelec before the
  production order — a search snippet is not a live quote, even though this is no longer the
  board's standout schedule risk.
- [ ] **`IH1215D` — resolved, fix round 1: the code is correct, not ambiguous.** XP Power's
  IH-series suffix denotes package (`D` = DIP-14, `S` = SIP-7), not output count — the whole
  family is dual-output regardless. This board's through-hole footprint
  (`Converter_DCDC_XP_POWER-IHxxxxD_THT`) makes `D` the right suffix exactly as written; this
  should not be re-opened without new evidence. See `hardware/procurement-check.md` §5.3/§6
  for the full resolution.
  **Human step:** none on the code itself. DigiKey holds 0 direct stock (16-week lead; ~38
  units via Marketplace cover the 5-unit need) — still the domain's sole supply by design, so
  confirm a quote before ordering.
- [ ] **`INA105KU` and `SN74HCT14D` are obsolete as literally written in the BOM** — order the
  active tape-and-reel equivalents (`INA105KU/2K5`, `SN74HCT14DR`) instead. Both have ample
  stock; this is a paperwork correction, not a supply risk.
- [x] **RESOLVED 2026-08-15 (panel-instrumentation task) — the three reward panel positions
  are now real footprints, not generic 2-pin-header placeholders.** `J4` (manual reward
  button) is `Button_Switch_THT:SW_PUSH-12mm`, a real recessed panel/chassis-mount
  momentary pushbutton (spec §3.1: recessed "so a sleeve or cable cannot dispense fluid").
  `J5` (remote reward jack) and `J6` (reward-driver-out) are both
  `wl-sync:BNC_Dual_RA_Isolated` — the same footprint every other BNC on this board uses.
  *(Updated 2026-08-16: was `Connector_Coaxial:BNC_PanelMountable_Vertical` until finding F6
  retired that part board-wide. `J5` and `J6` are the only two BNCs that do NOT share a dual
  body with a sibling, because spec §9.6 puts the reward remote on the front face and the
  driver output on the back, and a dual body is one piece of plastic through one panel — so
  each gets its own body with a deliberate, wired spare port.)* **This also removes the 3.5 mm TRS from the design entirely**: `J5` was
  its last use (spec §9.6), and no TRS footprint or symbol exists anywhere on the board any
  more (confirmed by grep across every generator and committed sheet, and asserted directly
  by `check_taskpc_digital_netlist.py`'s own whole-board footprint scan, with a negative
  control). Exact MPN for all three positions remains a layout-stage/procurement decision —
  the SAME, already-accepted convention this project uses for every other BNC on the board
  (footprint locked, MPN deferred), not a new open item. See
  `hardware/procurement-check.md`'s own panel-instrumentation addendum (2026-08-15) for the
  updated sourcing notes.
  **Human step:** pick and lock the real MPN for the recessed pushbutton (`J4`) before or
  during early layout, same as every BNC's own MPN — not a blocker for starting Task 14.
- [ ] **9 components on this board are Do-Not-Populate by design** — `opto-ni.kicad_sch`'s
  isolated-5V fallback (`U62` `TMA-0505S`, `C135`, `C136`, `FB3`, `R170`),
  `comparators.kicad_sch`'s unpopulated fourth comparator channel (`R121` 2.2k pull-up,
  `R120` 1M hysteresis feedback), and `analog-frontend.kicad_sch`'s two photodiode gain-trim
  positions (`R196`, `R197`, bridging `A_PD*_CLAMP` to `A_PD*_RAWOUT`).
  *(Count corrected 7 → 9 on 2026-08-16: the two photodiode positions arrived with findings
  F3/M1, and `R121` is 2.2k not 10k as of M4. Both re-derived from `(dnp yes)` in the
  committed sheets, not from this list.)* This is invisible in `breakout-bom.csv` itself (no DNP column was
  requested in the BOM export). Confirm whoever hands the BOM to an assembler also hands them
  `procurement-check.md` §2, or these 7 positions risk being populated when they shouldn't be.
  *(Count corrected 2026-08-15 — this said five, having missed the comparator sheet's own
  two; both counts are now derived from `(dnp yes)` in the committed sheet sources.)*

---

## What this review does not re-litigate

This task audits; it does not redesign. No schematic file was modified to produce this
review or `hardware/procurement-check.md`. If anything above reads as a real electrical
defect rather than a residual risk to sign off on, **stop — do not proceed to layout, and
scope a fix as its own task** rather than resolving it inside a review document.

> **The original text here read: "Nothing found during this audit rose to that bar."**
> That was true of the review that produced it, and it was overtaken within the day.
> `parametric-audit.md` then found **seven findings that would have prevented the board
> working**, and every one of them was a real defect rather than a residual risk. They were
> scoped and fixed as their own tasks, exactly as the paragraph above instructs — which is
> the rule working, not failing.
>
> The sentence is preserved rather than quietly deleted because **why it was wrong is the
> most useful thing in this document.** It was not careless. It was a correct summary of a
> review that asked topological questions of a board whose problems were parametric. A clean
> sweep is evidence about the questions you asked, not about the board.
>
> So: **do not read a fully-ticked checklist below as "the board is fine."** Read it as "the
> failure modes we knew to look for are absent." The parametric audit and
> `check_footprint_geometry.py` have since widened what "we knew to look for" covers — LED
> drive against datasheet minima, per-package ground current, isolated-supply loading, fuse
> coordination, footprint geometry against the drawing. That is wider than it was, and it is
> still not everything.

---

## Sign-off

**Layout (Task 14) does not begin until this section is completed by a person.**

- Reviewer name:
- Date:
- Every checklist item above independently confirmed (not merely read): ☐ Yes
- `parametric-audit.md` read, and its 21 findings accepted as closed: ☐ Yes
- Physical samples of the **MDR68** and the **dual BNC** (`031-6575`) ordered, and measurement
  against them scheduled before Task 14 finalizes panel positions: ☐ Yes
  *(The M12 left this list on 2026-08-16 — it is panel furniture with flying leads, not a
  board component, so nothing about it is a PCB dimension any more.)*
- Procurement items in `hardware/procurement-check.md` §6 acted on (`ADG1206YRUZ`, MDR68,
  `IH1215D` at minimum): ☐ Yes
- Decision: ☐ Approved for layout ☐ Not yet — blocking items:

Signature:

---

### Carried into Task 14 and Task 15 — not blockers for starting layout

Recorded here so signing this document does not lose them:

| Item | Needed by | Where |
|---|---|---|
| MDR68 tail-row-to-pin map confirmed against MH's drawing | **before fab** | `d3-panel-thickness.md` |
| Dual BNC protrusion through the panel measured | before the board outline is fixed | `panel-elevations.md` §3 |
| Standoff height chosen (it sets where the BNC row lands on the panel) | during layout | `panel-elevations.md` §3 |
| Panel connector positions checked against real placed coordinates | **before panels are machined** | Task 15 Step 2 |
| Recessed pushbutton (`J4`) MPN locked | during early layout | `procurement-check.md` |
