# Breakout board — pre-layout design review

Task 13. This is the checklist a person signs off in this file before layout (Task 14)
begins. It does not replace the automated contract (`tests/hardware/test_netlist.py`, the 11
structural checkers in `hardware/gen/`, and `kicad-cli sch erc`) — it exists because some of
what matters here cannot be checked by software at all, and because a human decision to
proceed is the thing spec §10.3 actually requires, not just a green test run.

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

### Mechanical — cannot be automated

- [ ] **Every custom footprint's critical dimensions verified against a physical sample.**
  `MDR68_Male_RightAngle` and `M12A_5_Panel` still carry **Low-confidence** dimensions from
  secondary sources (hardware/README.md's own sourcing table): MDR68's row spacing (2.84 mm)
  and mounting-hole spacing (57.9 mm) are corroborated across general references, not a
  primary CAD drawing for the exact ordered MPN; `M12A_5_Panel`'s panel cutout (12.5 mm) and
  external shell reference (14.5 mm) come from industry convention and a different
  manufacturer's datasheet respectively, not Amphenol LTW's own drawing (JS-gated, not
  fetchable by any automated tool used on this project so far). **This cannot be checked by
  software at all** — it requires a caliper and the physical part.
  **Human step, required before Task 14 commits panel positions to metal:** order the
  physical samples (plan Task 0, Step 2b already calls for exactly this), measure row
  spacing, mounting-hole spacing, and panel cutout diameter directly, and correct the
  footprint generator (`hardware/gen/gen_wl_sync_footprints.py`) if a measurement disagrees.
  A wrong connector footprint here scraps a machined panel, not a rework — this is the
  single highest-consequence unchecked item in this review.

### Procurement — see `hardware/procurement-check.md` for full detail

- [ ] **`ADG1206YRUZ-REEL7` shows 0 units in stock at DigiKey** (11-week manufacturer lead
  time; next restock late Nov/Dec 2026). 40 units needed across the 5-board run.
  **Human step:** confirm supply (Arrow, Mouser, or DigiKey backorder against the November
  restock) before the schedule assumes this part is available on demand.
- [ ] **MDR68 (`3700-0121-01`) was not found at DigiKey or Mouser** in this check — listed at
  RS Components and Distrelec, exact stock not obtained. This is the connector spec §10.2
  already named as the widest schedule error bar.
  **Human step:** a direct account-based stock/lead-time query at RS or Distrelec (or
  identify a pin-compatible alternate MDR68), before the production order.
- [ ] **`IH1215D`'s exact XP Power order code could not be conclusively confirmed** — evidence
  conflicts between a "D" (dual-output) suffix, consistent with this design's own use and
  with two other confirmed IH-series parts, and one direct fetch of XP Power's current
  product page naming the 12 V-in/dual/±15V/2W part `IH1215S`. Only 5 units needed
  total, but this is the sole supply for the entire Intan-isolated domain.
  **Human step:** confirm the order code directly with XP Power or a distributor part-number
  lookup before ordering — cheap to resolve, expensive to discover wrong after populating a
  board.
- [ ] **`INA105KU` and `SN74HCT14D` are obsolete as literally written in the BOM** — order the
  active tape-and-reel equivalents (`INA105KU/2K5`, `SN74HCT14DR`) instead. Both have ample
  stock; this is a paperwork correction, not a supply risk.
- [ ] **Three panel connector positions (`J4` manual reward button, `J5` remote reward 3.5 mm
  TRS jack, `J6` reward-driver-out BNC) are still generic 2-pin-header placeholders in the
  schematic**, not their real footprints — consistent with this project's own stated
  convention of deferring exact connector MPNs to layout, but flagged here because Task 14
  will need real footprints for these three positions before it can place them, and the BNC
  and TRS candidates identified in `procurement-check.md` §5.2 haven't been locked to a
  specific MPN yet.
  **Human step:** pick and lock the real MPN/footprint for these three positions before or
  during early layout — not a blocker for starting Task 14, but a blocker for finishing it.
- [ ] **5 components on this board are Do-Not-Populate by design** (`U62` `TMA-0505S`, `C135`,
  `C136`, `FB3`, `R170`) and this is invisible in `breakout-bom.csv` itself (no DNP column was
  requested in the BOM export). Confirm whoever hands the BOM to an assembler also hands them
  `procurement-check.md` §2, or these 5 positions risk being populated when they shouldn't be.

---

## What this review does not re-litigate

This task audits; it does not redesign. No schematic file was modified to produce this
review or `hardware/procurement-check.md`. If anything above reads as a real electrical
defect rather than a residual risk to sign off on, **stop — do not proceed to layout, and
scope a fix as its own task** rather than resolving it inside a review document. Nothing
found during this audit rose to that bar: every item above is either already closed by an
automated check, or a genuinely manual/procurement item that was always going to need a
human regardless of how carefully the schematic was built.

---

## Sign-off

**Layout (Task 14) does not begin until this section is completed by a person.**

- Reviewer name:
- Date:
- Every checklist item above independently confirmed (not merely read): ☐ Yes
- Physical samples of MDR68 and M12A_5 ordered, and measurement against them scheduled before
  Task 14 finalizes panel positions: ☐ Yes
- Procurement items in `hardware/procurement-check.md` §6 acted on (`ADG1206YRUZ`, MDR68,
  `IH1215D` at minimum): ☐ Yes
- Decision: ☐ Approved for layout ☐ Not yet — blocking items:

Signature:
