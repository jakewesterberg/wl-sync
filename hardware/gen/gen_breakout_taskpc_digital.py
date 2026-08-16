"""Generator for hardware/breakout/sheets/taskpc-digital.kicad_sch -- Task 8 of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-8-brief.md): the breakout board's task-PC digital sheet. Both of the task PC's own
68-pin MDR connectors live here (Connector 0/1 split, spec Sec.9.2), the 19-channel inbound
protected/buffered path (task PC -> Pi at 3.3V, and a parallel 5V-buffered copy for Task
11's NI optocouplers), the 4-channel outbound path (board -> task PC), and the reward OR.
Net names are a contract other sheets and hardware/gen/check_taskpc_digital_netlist.py
depend on -- see CONTRACT nets below.

Uses the same label-per-pin technique as gen_mule.py/gen_breakout_power.py
(kicad_sch.py's Sch.place()/label()/no_connect()/power_flag()): every component pin gets
a global label at its exact schematic coordinate, two pins share a net iff their labels
share text, no wires are drawn.

Four stages, matching the brief's own step numbering:

  1. Place both task-PC MDR68 connectors. Connector 0/1 split (which port range rides
     which physical connector) is CONFIRMED, not re-derived here -- spec Sec.9.2 (closed
     2026-08-13, formerly open item 3), restated in a schematic text block per the
     brief's own "record the source in a schematic text field": Connector 0 = AI0-15 +
     P0.0-7 + P1.0-7 (analog + AISENSE); Connector 1 = AI16-31 + P0.8-31 + P2.0-7, of
     which this design uses only the digital range (P0.8-30, 23 lines) -- all 23 of the
     task PC's own digital lines, leaving connector 0 exclusively analog.

     FIX ROUND 1 (task-8-report.md, finding 2): the PHYSICAL pin assigned to each P0.x
     line on Connector 1, and to each AI channel + AISENSE on Connector 0, is now SOURCED
     -- NI's own "X Series User Manual: NI 632x/634x/635x/636x/637x/638x/639x Devices"
     (National Instruments part number 370784K-01, May 2019 edition, ni.com/manuals),
     Figure A-5 "NI PCIe-6323/6343 Pinout" -- the task PC's own card, named explicitly,
     not inferred from a similar model. This SHEET'S pre-fix-round-1 self, and every
     generator/checker constant below that used to read "sequential (this board's own
     choice)", instead now reads MDR1_PIN_BY_P0/MDR1_DGND_PINS/MDR0_AI_PIN/
     MDR0_AISENSE_PIN, transcribed verbatim from that figure (see those constants' own
     comments for the retrieval method and a cross-check note). Connector 0's 9
     task-PC analog channels (A_EYE_LX/LY/RX/RY, A_JOY_X/Y, A_MISC1/2/3 -- plan.md's own
     net-naming table + Task 10 Step 3, not invented here) and its AISENSE-to-AGND tie
     are wired on THIS sheet too (fix round 1, finding 3): KiCad only lets the file that
     PLACES a symbol instance attach labels to its pins, and Task 10 (which does not
     exist yet) will need Connector 0's AI pins live now that its own generator can name
     them by contract -- see _place_connector0()'s own docstring.
  2. Inbound path -- 19 channels (16 event data + strobe + RWD_CMD + STIM_TRIG), each:
     MDR68 pin -> 100R series -> BAT54S clamp (signal on the series midpoint, low side
     DGND, HIGH SIDE +5V -- never +3V3, see _bidirectional_clamp()'s own docstring for
     why, verbatim from Task 2's mule-board fix) -> fans out in PARALLEL to two
     independent buffer banks sharing that one protected node: SN74LVC541APW on +3V3
     (3 packages, 8ch each) producing the *_PI/RWD_CMD/STIM_TRIG contract nets, and
     SN74AHCT541PW on +5V (3 more packages) producing the *_BUF contract nets Task 11's
     NI optocouplers consume (ruling F2, .superpowers/sdd/2026-08-13-breakout-pcb/
     progress.md -- without this second bank, 22 LED loads would sit directly on the
     task PC's own DAQ pins). AHCT541, not the original HCT541 -- see "OUTPUT DRIVER PART:
     SN74AHCT541PW, NOT SN74HCT541PW" below for why.

     THAT RULING WAS APPLIED INCOMPLETELY, and this sheet's own third AHCT541 package now
     carries the rest of it: the `_BUF` bank named 19 SIGNALS for what is really a 30-LED
     problem, and the 11 unnamed LEDs got doubled onto pins that already had one. See
     SECOND_LEG_CHANNELS below for the full account and the numbers; the short version is
     that one AHCT541 output pin may drive exactly one ACSL-6400 LED (~7.33mA), never two.
  3. Outbound path -- 4 channels (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT -- each
     produced ELSEWHERE, by Task 10's comparators sheet or Task 11's opto-intan sheet,
     and consumed here BY NAME as this bank's own buffer inputs) through one more
     SN74AHCT541PW on +5V (6 of 8 channels used: those 4, plus 2 more producing
     PD1_COMP_BUF/PD2_COMP_BUF for opto-ni's own LEDs -- see COMPARATOR_OPTO_LEGS below,
     the second half of the same incompletely-applied ruling), driving Connector 1's own
     remaining 4 digital pins back to the task PC. No input protection network here (unlike step 2)
     -- brief Step 3 does not ask for one, and the general principle stated right before
     spec Sec.9 ("every panel input carries series resistance and clamp diodes") is
     specifically about INBOUND (to-the-board) signals; these are OUR OWN buffer's
     push-pull outputs driving INTO the task PC's DAQ input pins, not an externally
     sourced signal this board needs to protect itself against.
  4. Reward OR: RWD_CMD (already produced in stage 2, the LVC541 bank's own 3.3V output,
     ASSUMED active-HIGH -- idle-LOW, pulses HIGH on delivery, this design's other event
     lines' own convention; SN74LVC541APW does not invert) and a manual panel button
     (RWD_BTN, its own 10k pull-up + 100nF debounce) combine in a 74HCT32 OR gate to
     produce RWD_DLVR, which drives a reward-driver BNC (placeholder -- see
     _place_reward_or()'s own docstring) and is available by name for Task 11's
     NI/Intan optocouplers.

     FIX ROUND 1 (task-8-report.md, finding 1 -- CRITICAL): the debounce stage places a
     SINGLE 74HCT14 Schmitt inverter, not the task-8-brief.md-specified "Schmitt inverter
     PAIR" -- that literal brief text was wrong, confirmed by tracing the circuit it
     describes: 10k pull-up to +5V + switch to DGND idles RWD_BTN HIGH/reads LOW while
     pressed; a PAIR of series inversions cancels (net non-inverting), so the debounced
     copy would ALSO idle HIGH/read LOW while pressed -- ORed against an active-HIGH
     RWD_CMD, `HIGH OR anything` is permanently HIGH, so RWD_DLVR would assert
     CONTINUOUSLY (reward driver stuck open, "delivered" stuck true in every recording),
     not pulse on delivery. A SINGLE inversion instead makes the debounced copy idle-LOW/
     active-HIGH -- the polarity an OR combination with an active-HIGH RWD_CMD actually
     needs. See place_debounce_inverter()'s own docstring (singular now, was plural) and
     _place_reward_or()'s own polarity note.

OUTPUT DRIVER PART: SN74AHCT541PW, NOT SN74HCT541PW -- every one of this sheet's own
`_BUF`/outbound octal buffers (5 packages total across this sheet; a 6th, U15, lives on
pi-interface.kicad_sch) is the AHCT variant, not the plain HCT part every one of them
started out as. Each drives exactly one ACSL-6400/6420 LED at ~7.33mA (the SECOND_LEG_
CHANNELS comment above/gen_breakout_opto_ni.py's own derivation -- fixed by the LED's own
7.0mA worst-case switching threshold, not something a driver choice can lower). SN74HCT541
is rated IOL=6mA, so 7.33mA is 22% over -- comfortably inside its own 25mA per-pin absolute
maximum, but out of the datasheet's own guaranteed-VOL condition, and the margin against
that is thinner than treating the 6mA-rated V_OL figure as if it applied at 7.33mA would
suggest: solved self-consistently against the part's own ~55 ohm output impedance (not the
datasheet's single 6mA test point), the real current is closer to ~7.18mA, uncomfortably
close to the LED's own 7.0mA floor before even accounting for VF/VCC tolerance. SN74AHCT541
-- identical pinout, identical TSSOP-20 footprint (FOOTPRINT_TSSOP20 below, unchanged),
same "74xx:74LS541"-derived KiCad symbol family -- is rated IOL=8mA, which makes 7.33mA
fully compliant with margin to spare, and also doubles the part's own absolute maxima
(25mA -> 50mA per pin, 70mA -> 100mA per package), retiring the separate concern that U8/
U9/U10 (58.6mA each, three full/near-full packages) sat at 84% of a 70mA package limit.
Value/MPN edit only, confirmed by regenerating this sheet and diffing its own component
list against the pre-swap commit: same 5 refdes (U8-U11 here, U15 on pi-interface.kicad_
sch), same footprint, same pinout (both symbols extend the same "74LS541" KiCad library
parent -- see kicad_sch.py's own extract_symbol()/_flatten_extends()), zero refdes churn.
AHCT's faster edge rates are a placement-stage consideration (series termination on the
MDR68 cable runs), not a schematic-capture one -- see hardware/breakout/design-review.md.

PANEL-INSTRUMENTATION TASK (2026-08-15) -- Changes A and B, both landing entirely on this
sheet's own reward circuit (`_place_reward_or()`/`place_reward_oneshot()`):

  CHANGE A -- manual reward becomes a one-shot. One Nexperia 74HCT123D dual retriggerable
  monostable (place_reward_oneshot(), minted out of band as U69/R191/C149/C150) now sits
  between the debounce inverter's own output (RWD_BTN_DEB) and the OR gate's second input
  (now RWD_BTN_PULSE, the one-shot's own Q) -- every hand-delivered reward gets a fixed
  ~199ms pulse regardless of hold duration. Debounce capacitor changes from 100nF to 1uF
  (10k x 1uF = 10ms, spec Sec.3.1), covering both switch bounce and the longer remote-BNC
  cable run's own connector-mating transient. See place_reward_oneshot()'s own docstring
  for the full part selection, pin-map verification, and pulse-width derivation.

  CHANGE B -- the three reward positions become real connectors: the manual button (J4)
  is now Switch:SW_Push on a real panel/chassis-mount pushbutton footprint (recessed, spec
  Sec.3.1); the remote reward jack (J5) and reward-driver output (J6) are now real BNCs
  (Connector:Conn_Coaxial / BNC_PanelMountable_Vertical, the same footprint pi-
  interface.kicad_sch's own camera-trigger BNCs use) wired exactly as their Conn_01x02
  placeholders were. This removes the 3.5mm TRS this position's own procurement
  documentation used to describe from the design entirely -- confirmed by grep, no
  generator or committed sheet, past or present, ever placed a TRS symbol/footprint (see
  _place_reward_or()'s own docstring for the full account and why a BNC, not a swap for
  swap's sake, is the point: no mating transient, and a real mechanical lock).

  Reference designators: J4/J5/J6 keep their EXISTING numbers (in-place symbol/footprint/
  value edits at the same call sites, same position in this file's own next_ref() call
  order) -- constraint 3 (never renumber an existing refdes) is satisfied by construction
  here, not by out-of-band minting, since nothing about WHICH pin gets called moved. U69/
  R191/C149/C150 (the one-shot) ARE minted out of band, against the whole-board baseline
  (#PWR10/C148/D39/F1/FB3/J55/NT2/R190/U68) computed from every sibling sheet's own
  committed text before this task's edits -- see place_reward_oneshot()'s own docstring.

Run directly: `python3 hardware/gen/gen_breakout_taskpc_digital.py` (writes
hardware/breakout/sheets/taskpc-digital.kicad_sch). hardware/breakout/sym-lib-table
gained one new entry at this task's original commit -- Connector_Generic (Conn_01x02,
used for the three placeholder 2-pin connectors this task's own panel-instrumentation
follow-up later replaces with real parts -- see above) -- and two more at the panel-
instrumentation task: `Switch` (sym-lib-table, for Switch:SW_Push) and
`Button_Switch_THT` (fp-lib-table, for the panel pushbutton footprint) -- see
hardware/README.md. `Connector_Coaxial` (fp-lib-table) and `Connector` (sym-lib-table,
for Conn_Coaxial) were both already registered (Task 9), so the two new BNCs here needed
no further library entries.

Requires hardware/breakout/breakout.kicad_sch to already exist on disk (gen_breakout.py's
own output) -- build() reads it to compute this file's real root+sheet-symbol ancestor
path (kicad_sch.py's find_root_uuid()/find_sheet_instance_path(), same discipline
gen_breakout_power.py's own build() established at Task 7 fix round 1) rather than
defaulting to a self-referential one.
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_max_refs,
    find_root_uuid,
    find_sheet_instance_path,
    pin_pos,
    write_project_stub,
)

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
POWER_SCH = OUT / "power.kicad_sch"  # already-committed sibling sheet -- read to seed
# this sheet's own reference counters (see build()'s own comment, and kicad_sch.py's
# Sch.ref_start docstring, for why: without this, gen_breakout_power.py's own "J1"/"R1"/
# etc. and this sheet's own would collide, confirmed empirically to break
# `kicad-cli sch export netlist` (Warning: annotation errors) and
# check_mule_netlist.parse_component_values()'s own global-uniqueness assertion).
TASKPC_DIGITAL_SHEETFILE = "sheets/taskpc-digital.kicad_sch"  # exactly as breakout.kicad_sch's
# own (sheet ...) block spells its Sheetfile property (gen_breakout.py's SHEET_NAMES/
# f"sheets/{name}.kicad_sch") -- find_sheet_instance_path() matches on this literal string.

# ---------------------------------------------------------------------------
# Contract nets (task-8-brief.md's own net list, as handed down for this task -- every
# other sheet, and check_taskpc_digital_netlist.py, depend on these exact names). Rails
# consumed from Task 7: +5V, +3V3, DGND, AGND (AGND added fix round 1, for AISENSE).
#
# ANALOG_CONTRACT_NETS (fix round 1, finding 3): the 9 task-PC analog channel names are
# NOT this sheet's own invention -- plan.md's own net-naming table ("Analog sources: `A_`
# prefix ... A_EYE_LX, A_EYE_LY, A_EYE_RX, A_EYE_RY, ... A_JOY_X, A_JOY_Y ...  A_MISC1
# ...A_MISC3", "Analog destinations: `_NI`, `_TPC` suffix") plus Task 10 Step 3's own text
# ("Same topology to the task PC's Connector 0. Channels: A_EYE_LX, A_EYE_LY, A_EYE_RX,
# A_EYE_RY, A_JOY_X, A_JOY_Y, A_MISC1, A_MISC2, A_MISC3. AISENSE tied to AGND here too")
# fix the exact 9 base names and the AISENSE instruction; the `_TPC` suffix is this net's
# own domain marker (the buffered copy landing at the task-PC-facing MDR68 pin, same
# convention EVT_D0_TPC etc. already establish for digital).
# ---------------------------------------------------------------------------
ANALOG_CONTRACT_NETS = (
    [f"A_EYE_{s}_TPC" for s in ("LX", "LY", "RX", "RY")]
    + ["A_JOY_X_TPC", "A_JOY_Y_TPC"]
    + [f"A_MISC{n}_TPC" for n in (1, 2, 3)]
)
assert len(ANALOG_CONTRACT_NETS) == 9

CONTRACT_NETS = (
    [f"EVT_D{i}_TPC" for i in range(16)] + ["EVT_STROBE_TPC"]
    + [f"EVT_D{i}_PI" for i in range(16)] + ["EVT_STROBE_PI"]
    + [f"EVT_D{i}_BUF" for i in range(16)] + ["EVT_STROBE_BUF"]
    + ["RWD_CMD", "RWD_CMD_BUF", "RWD_BTN", "RWD_DLVR", "STIM_TRIG", "STIM_TRIG_BUF"]
    + ["PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"]
    # One optocoupler LED per driver pin (see SECOND_LEG_CHANNELS/COMPARATOR_OPTO_LEGS):
    # 5 second legs off U10's own spare channels + 2 comparator legs off the outbound
    # HCT541's own spares. Every one of these is a net this sheet PRODUCES and the opto
    # sheets consume, exactly like the *_BUF nets above.
    + ["EVT_STROBE_INTAN_BUF", "RWD_CMD_INTAN_BUF", "STIM_TRIG_INTAN_BUF"]
    + ["RWD_DLVR_BUF", "RWD_DLVR_INTAN_BUF", "PD1_COMP_BUF", "PD2_COMP_BUF"]
    # Finding F1 (2026-08-16): RHS_STIM_OUT's own optocoupler LED moves onto a buffered
    # leg, like every other opto-ni channel -- see RHS_STIM_OUT_BUF_LEGS. This is a net
    # this sheet PRODUCES and opto-ni.kicad_sch consumes, exactly like the *_BUF nets
    # above; RHS_STIM_OUT itself remains an INPUT here, driven by opto-intan's ACSL-6420.
    + ["RHS_STIM_OUT_BUF"]
    # Finding M3 (2026-08-16): the reward BNC moves behind a 100 ohm series resistor,
    # so the panel connector no longer sits on RWD_DLVR itself.
    + ["RWD_DLVR_BNC"]
    + ANALOG_CONTRACT_NETS
)
assert len(CONTRACT_NETS) == 16 + 1 + 16 + 1 + 16 + 1 + 6 + 4 + 7 + 1 + 1 + 9 == 79

# ---------------------------------------------------------------------------
# Real physical MDR68 pin assignment, SOURCED (fix round 1; task-8-report.md's own "Fix
# round 1" section documents full retrieval/verification method) -- NOT this sheet's own
# sequential guess, which is what fix round 1 replaces. Source: National Instruments, "X
# Series User Manual: NI 632x/634x/635x/636x/637x/638x/639x Devices", part number
# 370784K-01 (May 2019 edition, ni.com/manuals), Figure A-5 "NI PCIe-6323/6343 Pinout" --
# the task PC's own card (PCIe-6343), named explicitly in that figure's own title, not
# inferred from a similar model. Every value below is transcribed verbatim from that
# figure's own two 68-pin tables (Connector 0 = "(AI 0-15)", Connector 1 = "(AI 16-31)"),
# cross-checked by two independent parses of the source PDF's extracted text (one by hand,
# one script-driven) that agreed exactly before either was used here.
# ---------------------------------------------------------------------------
MDR1_PIN_BY_P0 = {  # Connector 1: P0.x (x = 8..31) -> physical MDR68 pin, per Figure A-5.
    8: "52", 9: "17", 10: "49", 11: "47", 12: "19", 13: "51", 14: "16", 15: "48",
    16: "11", 17: "10", 18: "43", 19: "42", 20: "41", 21: "6", 22: "5", 23: "38",
    24: "37", 25: "3", 26: "45", 27: "46", 28: "2", 29: "40", 30: "1", 31: "39",
}
assert set(MDR1_PIN_BY_P0) == set(range(8, 32))
# Every physical pin Figure A-5 itself labels "D GND" on Connector 1 -- all 12 tied to
# this design's own DGND net (not an arbitrary 2, this sheet's own PRE-fix-round-1
# choice): a real cable's every ground pin carries return current for the 19 digital
# lines riding the same connector, and there is no reason to leave any of them unused.
MDR1_DGND_PINS = ["4", "7", "9", "12", "13", "15", "18", "35", "36", "44", "50", "53"]
assert len(MDR1_DGND_PINS) == 12

# Connector 0: AIn (n = 0..8, the first 9 of the 16 available -- this design's own 9
# task-PC analog channels use only these; which of the 9 SIGNALS maps to which AI channel
# NUMBER is this sheet's own arbitrary-but-declared choice, same class of decision as
# spec open item 6's mux default -- AI channels are electrically interchangeable ADC mux
# inputs, unlike a P0.x digital line's fixed event-code bit weight, so no source needs to
# fix this the way Finding 2 needed for Connector 1) -> physical MDR68 pin, per Figure
# A-5. NRSE mode (spec Decision 5/Sec.9.2): each AIn is used as an independent
# single-ended channel referenced to AISENSE, so only the PRIMARY "AI n" reading applies
# here -- the parenthetical differential alt-name Figure A-5 also prints for these same
# physical pins (e.g. physical pin 34 reads "AI 8 (AI 0-)") is a DIFFERENTIAL-mode
# alternative this design does not use, not a second physical pin.
MDR0_AI_PIN = {
    0: "68", 1: "33", 2: "65", 3: "30", 4: "28", 5: "60", 6: "25", 7: "57", 8: "34",
}
assert set(MDR0_AI_PIN) == set(range(9))
MDR0_AISENSE_PIN = "62"  # Connector 0's own "AI SENSE" (not "AI SENSE 2", which is
# Connector 1's -- this design uses only Connector 0's analog bank; see spec Decision 5.

# The 9 task-PC analog channels in the fixed order they consume MDR0_AI_PIN's AI0..AI8
# (this sheet's own arbitrary channel-number assignment, declared here once rather than
# implied positionally) -- (contract net name, AI channel number).
ANALOG_CHANNELS = list(zip(ANALOG_CONTRACT_NETS, range(9)))
assert len(ANALOG_CHANNELS) == 9

# ---------------------------------------------------------------------------
# Footprints -- picked from the part actually being ordered (same discipline
# gen_breakout_power.py's own footprint block documents; see task-7-report.md and
# hardware/README.md for the sourcing behind every one of these already established
# elsewhere in this repo).
# ---------------------------------------------------------------------------
FOOTPRINT_TSSOP20 = "Package_SO:Texas_PW0020A_TSSOP-20_4.4x6.5mm_P0.65mm"  # SN74LVC541APW /
# SN74AHCT541PW -- TI's own "PW" package code is literally TSSOP-20 (gen_mule.py's own
# choice), and identical between SN74HCT541PW and SN74AHCT541PW -- see "OUTPUT DRIVER
# PART" above; unchanged by that swap
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"  # SN74HCT32D / SN74HCT14D -- TI's
# own "D" package code is the standard 3.9mm-body SOIC-14, JEDEC MS-012
FOOTPRINT_SOT23 = "Package_TO_SOT_SMD:SOT-23"  # BAT54S -- the real part, no stand-in
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"  # 100nF decoupling/debounce
FOOTPRINT_MDR68 = "wl-sync:MDR68_Male_RightAngle"
FOOTPRINT_HDR1X02 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
# Panel-instrumentation task (2026-08-15) -- the three reward positions upgrade from
# Conn_01x02 placeholders to real parts (see _place_reward_or()'s own docstring for the
# full account); the one-shot adds a real 16-pin logic IC. Every footprint below is a real
# stock KiCad entry, same "check the real part's own package" discipline as every other
# footprint in this file.
FOOTPRINT_SOIC16 = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"  # Nexperia 74HCT123D -- JEDEC
# MS-012 narrow-body SOIC-16, 1.27mm pitch, identical package class (just two more pins)
# to every other SOIC part already on this board (FOOTPRINT_SOIC14).
FOOTPRINT_BNC = "Connector_Coaxial:BNC_PanelMountable_Vertical"  # same real stock
# footprint pi-interface.kicad_sch's own 5 camera-trigger BNCs already use (that
# generator's own FOOTPRINT_BNC comment: "Isolated BNCs throughout... each shell lands on
# its own pad" -- spec Sec.9.1). Registered in fp-lib-table already (Task 9); no new
# library entry needed here.
FOOTPRINT_PANEL_PUSHBUTTON = "Button_Switch_THT:SW_PUSH-12mm"  # generic 12mm panel/
# chassis-mount momentary pushbutton -- a real stock KiCad footprint (2 electrical
# terminals, each broken out to a redundant pair of pads for mechanical strength -- read
# directly from the .kicad_mod, not assumed: pad numbers "1"/"1"/"2"/"2", so the footprint
# itself already ties same-numbered pads to one net; nothing here depends on a THIRD,
# ambiguous adjacency the way the Task 10a MISC shunt jumper did). Like every BNC on this
# board, the exact recessed-bezel manufacturer part is a layout-stage/procurement
# decision this schematic-capture task does not make (same status as FOOTPRINT_BNC's own
# MPN) -- see hardware/README.md.

# ---------------------------------------------------------------------------
# Layout grid -- every coordinate an exact multiple of 1.27mm (KiCad's schematic
# connection grid), same GRID() helper as every other generator in this project.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


# Connector 1 (digital, task PC) -- the hub the inbound/outbound stages both attach to.
X_CONN1, Y_CONN1 = GRID(20), GRID(150)
# Connector 0 (analog + AISENSE) -- placed, entirely unpopulated, for Task 10.
X_CONN0, Y_CONN0 = GRID(20), GRID(560)

# 19 inbound channel rows (16 data + strobe + RWD_CMD + STIM_TRIG).
ROW0 = GRID(38.1)
ROW_PITCH = GRID(10.16)  # channels 0..18 => Y = 38.1 .. 220.98
X_R_IN = GRID(140)
X_CLAMP = GRID(170)

# LVC541 bank (+3V3), 3 packages: data0-7, data8-15, strobe/RWD_CMD/STIM_TRIG (3 of 8).
X_LVC = GRID(220)
Y_LVC1, Y_LVC2, Y_LVC3 = GRID(60.96), GRID(137.16), GRID(213.36)

# HCT541 _BUF bank (+5V), 3 packages, same channel grouping as the LVC541 bank -- each
# package's INPUT pins tap the identical *_CLAMP net the LVC541 bank's own input pins
# use (fan-out at the already-protected node, not a second R/clamp network).
X_HCTBUF = GRID(310)
Y_HCTBUF1, Y_HCTBUF2, Y_HCTBUF3 = GRID(60.96), GRID(137.16), GRID(213.36)

# Outbound HCT541 (+5V), 1 package, 8 of 8 channels used (4 outbound + 2 comparator-opto
# legs -- see COMPARATOR_OPTO_LEGS -- + 2 RHS_STIM_OUT_BUF legs added at F1).
X_OUTBUF, Y_OUTBUF = GRID(220), GRID(260)

# F1 parallel-leg bank (+5V), 4 packages -- the SECOND output of every optocoupler LED
# pair. Its own column, well clear of every existing anchor (the rightmost of those is
# the X_NOTE4 text block at 400; the one-shot occupies 260-320), so nothing here can
# collide with an already-placed part. Same 76.2mm row pitch as the _BUF bank it mirrors.
X_PARALLEL = GRID(560)
Y_PARALLEL = [GRID(60.96), GRID(137.16), GRID(213.36), GRID(289.56)]

# Reward OR block.
X_RWD_BTN, Y_RWD_BTN = GRID(20), GRID(300)
X_RWD_JACK, Y_RWD_JACK = GRID(20), GRID(315)
X_RWD_PULLUP, Y_RWD_PULLUP = GRID(50), GRID(295)
X_RWD_CAP, Y_RWD_CAP = GRID(50), GRID(310)
X_U9, Y_U9 = GRID(90), GRID(295)   # 74HCT14 (debounce inverter pair + 4 unused + power)
GATE_DY = GRID(12.7)
X_U8, Y_U8 = GRID(180), GRID(295)  # 74HCT32 (reward OR + 3 unused gates + power)
X_RWD_BNC, Y_RWD_BNC = GRID(230), GRID(295)

# One-shot block (panel-instrumentation task, 2026-08-15) -- its own column, well clear
# of every existing anchor above (X_RWD_BTN..X_RWD_BNC top out at 230; X_U9 top out at
# 90+7*GATE_DY=~168.9 vertically) and of X_NOTE4=400/Y_NOTE4=15 (a different sheet
# region entirely).
#
# ONESHOT_ROW_DY=50.8mm -- NOT 25.4mm (comparators.kicad_sch's own ROW_DY, tried first
# and WRONG for this part): the 74HCT123's own MAXIMUM local pin reach is +-12.7mm
# (pins 3/8/11/16 -- Clr on units 1/2, GND/VCC on the power unit 3 -- all four sit
# exactly there, confirmed via unit_pins(), not merely the largest of a few candidates
# eyeballed), so two ADJACENT unit rows spaced by exactly 2x12.7=25.4mm put unit N's own
# Clr pin (reaching -12.7 from ITS row) exactly on top of unit (N+1)'s own GND/VCC pin
# (reaching +12.7 from ITS row) -- confirmed the hard way: the FIRST version of this
# sheet used 25.4mm and it silently merged +5V and DGND project-wide (unit 2's Clr,
# wired to DGND, landed on the exact same coordinate as unit 3's VCC, wired to +5V) --
# caught by `kicad-cli sch erc` (`pin_to_pin`, power.kicad_sch's own #PWR3/#PWR5) and by
# the multiple_net_names warning on THIS sheet, not by anything this generator itself
# checks. 50.8mm (4x the 12.7mm max reach, comfortably more than LM339's own ~3.3x
# margin for a smaller reach) leaves a 25.4mm clear gap between any two adjacent units'
# own reach envelopes -- verified empirically after the fix (0 ERC errors, and
# check_row_pitch_exceeds_2pin_span() -- which does not even cover multi-pin ICs, only
# 2-terminal parts -- still passes clean on every 2-pin part on this sheet).
X_ONESHOT, Y_ONESHOT = GRID(290), GRID(480)
ONESHOT_ROW_DY = GRID(50.8)
X_ONESHOT_R, X_ONESHOT_C = GRID(260), GRID(320)  # Rext / Cext columns, either side of U69
X_NOTE6, Y_NOTE6 = GRID(290), GRID(620)  # one-shot note block, below the three unit rows
# (Y_ONESHOT + 2*ONESHOT_ROW_DY = 582.6, comfortably clear at 620)

DECOUPLE_DX = GRID(15.24)

# Text-note anchors.
X_NOTE1, Y_NOTE1 = GRID(20), GRID(15)     # connector split source + P0.x offset
X_NOTE2, Y_NOTE2 = GRID(20), GRID(545)    # Connector 0 placeholder
X_NOTE3, Y_NOTE3 = GRID(20), GRID(390)    # reward-OR polarity concern
X_NOTE4, Y_NOTE4 = GRID(400), GRID(15)    # one LED per driver pin -- its own column,
# right of the HCT541 _BUF bank at X=310 (a TSSOP-20 symbol plus its own labels reaches
# well short of X=400), so nothing here overlaps a part or another note block.
NOTE_DY = GRID(5.08)

CHAN_A = {i: str(2 + i) for i in range(8)}   # 74x541 unit-1 pin numbers: A0..A7
CHAN_Y = {i: str(18 - i) for i in range(8)}  # ...and Y0..Y7, paired by channel: Ai+Yi=20


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint="", ref=None):
    """Place a 2-pin part between two labeled nets -- same pattern as gen_mule.py's and
    gen_breakout_power.py's own two_pin() (duplicated rather than imported: kicad_sch.py,
    not any one generator, is this project's shared machinery).

    `ref`, when given, is an EXPLICIT out-of-band refdes used instead of next_ref() --
    the same mechanism place_reward_oneshot() already uses for R191/C149/C150, needed
    again for the F1 packages' own decoupling capacitors. See PARALLEL_LEG_PACKAGES.
    """
    ref = ref or sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    x1, y1 = pin_pos(x, y, pins["1"])
    sch.label(net1, x1, y1)
    x2, y2 = pin_pos(x, y, pins["2"])
    sch.label(net2, x2, y2)
    return ref


def bidirectional_clamp(sch, x, y, net_signal, net_hi, net_lo):
    """Place a BAT54S wired as a genuine two-rail series-pair clamp -- IDENTICAL topology
    to gen_mule.py's own bidirectional_clamp(), validated on the mule board through two
    fix rounds (Task 2 fix rounds 1-2) and reused here VERBATIM per this task's own
    explicit instruction ("Exactly as validated on the mule").

    pin 1 "A" (anode only) -> net_lo (DGND); pin 3 "COM" (the genuine series midpoint,
    KiCad's own description: "Dual schottky barrier diode, in series") -> net_signal;
    pin 2 "K" (cathode only) -> net_hi. D1 = anode(1)->cathode(3=COM) conducts when DGND
    exceeds signal (low-side clamp); D2 = anode(3=COM)->cathode(2) conducts when signal
    exceeds net_hi (high-side clamp).

    net_hi MUST be +5V, never +3V3 (task-8-brief.md, Step 2, and see Task 2 for the full
    derivation this repeats): the '541 family here is chosen specifically because LVC
    inputs tolerate 5.5V independent of the device's own 3.3V supply -- clamping the high
    side to +3V3 instead would put every normal 5V logic high (the signal's OWN normal
    level, not a fault) into forward conduction, sinking ~13.5mA per line (over half the
    DAQ pin's 24mA rating) and ~230mA across the 17-line mule/19-line breakout-scale
    fan-in into a 3.3V rail with no way to sink it -- continuously, on every code the task
    PC emits, not just during a fault. Referenced to +5V (the signal's own normal high
    rail) the diode sees ~0V forward bias in normal operation and only genuinely
    overvoltage conditions (still safely under the LVC part's 6.5V absolute maximum) turn
    it on.
    """
    ref = sch.next_ref("D")
    pins = sch.place("Diode", "BAT54S", ref, "BAT54S", x, y, footprint=FOOTPRINT_SOT23)
    px, py = pin_pos(x, y, pins["1"])
    sch.label(net_lo, px, py)
    px, py = pin_pos(x, y, pins["3"])
    sch.label(net_signal, px, py)
    px, py = pin_pos(x, y, pins["2"])
    sch.label(net_hi, px, py)
    return ref


def place_octal_buffer(sch, libname, symname, value, x, y, rail, channels, refs, role_key,
                       footprint, ref=None, cap_ref=None):
    """Place one 74x541-family octal buffer (LVC541 on +3V3, or HCT541 on +5V -- same
    physical pinout either way, see CHAN_A/CHAN_Y: input Ai at KiCad pin 2+i, tri-state
    output Yi at pin 18-i, so Ai+Yi=20 for every channel of every instance -- the
    invariant check_taskpc_digital_netlist.py's own end-to-end walk depends on, exactly
    as check_mule_netlist.py's _walk_channel() already established for the mule).
    `channels` maps local channel index (0-7) -> (input_net, output_net) for USED
    channels; channels not present are tied off safely (input -> DGND, output ->
    no-connect) -- gen_mule.py's own place_541() precedent, never leave a CMOS buffer
    input floating even when its output drives nothing.

    Two channels MAY name the same output_net deliberately: that is finding F1's
    paralleling, and it needs nothing special here because a net is a net -- what makes
    it safe is that both channels also name the same INPUT net, which
    PARALLEL_LEG_PACKAGES constructs by mirroring rather than by re-listing, and which
    tests/hardware/test_netlist.py asserts independently.

    `ref`/`cap_ref`, when given, are EXPLICIT out-of-band refdes used instead of
    next_ref() -- see PARALLEL_LEG_PACKAGES for why F1's four new packages must be
    minted that way.
    """
    ref = ref or sch.next_ref("U")
    pins = sch.place(
        libname, symname, ref, value, x, y,
        footprint=footprint,
        extra_props={"Description": "8-bit buffer/line driver, 3-state outputs"},
    )
    for i in range(8):
        a_pin, y_pin = pins[CHAN_A[i]], pins[CHAN_Y[i]]
        if i in channels:
            a_net, y_net = channels[i]
            ax, ay = pin_pos(x, y, a_pin)
            sch.label(a_net, ax, ay)
            yx, yy = pin_pos(x, y, y_pin)
            sch.label(y_net, yx, yy)
        else:
            ax, ay = pin_pos(x, y, a_pin)
            sch.label("DGND", ax, ay)
            yx, yy = pin_pos(x, y, y_pin)
            sch.no_connect(yx, yy)
    for oe_pin in ("1", "19"):
        px, py = pin_pos(x, y, pins[oe_pin])
        sch.label("DGND", px, py)  # active-low output enable, tied permanently enabled
    gx, gy = pin_pos(x, y, pins["10"])
    sch.label("DGND", gx, gy)
    vx, vy = pin_pos(x, y, pins["20"])
    sch.label(rail, vx, vy)
    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, "DGND",
                      footprint=FOOTPRINT_C_SMALL, ref=cap_ref)
    refs.setdefault(role_key, []).append(ref)
    refs.setdefault(role_key + "_decouple_c", []).append(cap_ref)
    return ref


def place_reward_or_gate(sch, x, y, dy, in_a_net, in_b_net, out_net, rail, gnd, refs):
    """One 74LS32-rooted quad 2-input OR gate (no stock '74HCT32' symbol exists anywhere
    in KiCad's install -- grepped the full symbols directory; see module docstring --
    so, exactly like gen_mule.py's own '541-family stand-in, this places the closest
    pin-compatible relative that DOES exist, 74xx:74LS32 -- itself extends-free, real pin
    geometry, not a symbol needing Task 6's extends-flattening -- with Value overridden
    to the real ordered part, SN74HCT32D). KiCad represents this as FIVE separate
    `(symbol ...)` blocks sharing one reference: units 1-4 are the four independent OR
    gates (each with its own pins, per the raw library text), unit 5 carries the
    package's shared VCC(14)/GND(7) -- confirmed directly against the raw
    74xx.kicad_sym text, not assumed.

    Only unit 1 (pins 1,2->3) does real work: RWD_CMD OR the debounced RWD_BTN ->
    RWD_DLVR. Units 2-4 are genuinely unused gates on the same physical die -- per this
    file's own place_octal_buffer() precedent (never leave a CMOS gate input floating,
    even on a channel that drives nothing), both inputs of each unused gate are tied to
    `gnd` and its output no-connected.
    """
    ref = sch.next_ref("U")
    p1 = sch.place(
        "74xx", "74LS32", ref, "SN74HCT32D", x, y, unit=1, footprint=FOOTPRINT_SOIC14,
        extra_props={"Description": "Quad 2-input OR gate"},
    )
    ax, ay = pin_pos(x, y, p1["1"])
    sch.label(in_a_net, ax, ay)
    bx, by = pin_pos(x, y, p1["2"])
    sch.label(in_b_net, bx, by)
    yx, yy = pin_pos(x, y, p1["3"])
    sch.label(out_net, yx, yy)

    for unum, in_a, in_b, out_p in ((2, "4", "5", "6"), (3, "9", "10", "8"), (4, "12", "13", "11")):
        uy = y + (unum - 1) * dy
        up = sch.place("74xx", "74LS32", ref, "SN74HCT32D", x, uy, unit=unum, footprint=FOOTPRINT_SOIC14)
        for pn in (in_a, in_b):
            px, py = pin_pos(x, uy, up[pn])
            sch.label(gnd, px, py)
        px, py = pin_pos(x, uy, up[out_p])
        sch.no_connect(px, py)

    pwr_y = y + 4 * dy
    pp = sch.place("74xx", "74LS32", ref, "SN74HCT32D", x, pwr_y, unit=5, footprint=FOOTPRINT_SOIC14)
    gx, gy = pin_pos(x, pwr_y, pp["7"])
    sch.label(gnd, gx, gy)
    vx, vy = pin_pos(x, pwr_y, pp["14"])
    sch.label(rail, vx, vy)

    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)
    refs["reward_or_u"] = ref
    refs["reward_or_decouple_c"] = cap_ref
    return ref


def place_debounce_inverter(sch, x, y, dy, in_net, out_net, rail, gnd, refs):
    """One 74HC14-rooted hex Schmitt-trigger inverter (no stock '74HCT14' symbol exists
    either -- see module docstring; 74HC14 is itself extends-free -- 74LS14 is the one
    that extends IT, confirmed directly against the raw library text -- so this places
    74HC14 as the real, pin-bearing part, Value overridden to SN74HCT14D). Units 1-6 are
    the six independent inverters, unit 7 carries VCC(14)/GND(7) -- confirmed directly
    against the raw library text, same convention as the OR gate above.

    FIX ROUND 1 (task-8-report.md, finding 1 -- CRITICAL, corrects this function's own
    former plural name and behaviour): a SINGLE inverter (unit 1, pin 1->2), not the
    task-8-brief.md-specified "Schmitt inverter PAIR" this function used to place (two
    series units, net non-inverting). Traced from the actual circuit: RWD_BTN idles HIGH
    (10k pull-up to +5V, button shorts to DGND when pressed) and reads LOW while pressed.
    Two series inversions cancel, so a PAIR would leave the debounced copy ALSO idle-HIGH/
    read-LOW-while-pressed -- ORed downstream against an active-HIGH RWD_CMD (idle-LOW,
    pulses HIGH), `HIGH OR anything` is permanently HIGH: RWD_DLVR would assert
    CONTINUOUSLY (reward driver stuck open, "delivered" stuck true in every recording),
    not pulse only on an actual delivery. A SINGLE inversion instead makes the debounced
    copy idle-LOW/active-HIGH -- matching RWD_CMD's own assumed polarity, which is what
    an OR combination actually needs (see _place_reward_or()'s own polarity note, and
    check_taskpc_digital_netlist.py's own same-polarity structural check). Units 2-6 are
    genuinely unused: input tied to `gnd`, output no-connected, same discipline as every
    other unused gate on this sheet.
    """
    ref = sch.next_ref("U")
    p1 = sch.place(
        "74xx", "74HC14", ref, "SN74HCT14D", x, y, unit=1, footprint=FOOTPRINT_SOIC14,
        extra_props={"Description": "Hex Schmitt-trigger inverter"},
    )
    ix, iy = pin_pos(x, y, p1["1"])
    sch.label(in_net, ix, iy)
    ox, oy = pin_pos(x, y, p1["2"])
    sch.label(out_net, ox, oy)

    for unum, in_p, out_p in (
        (2, "3", "4"), (3, "5", "6"), (4, "9", "8"), (5, "11", "10"), (6, "13", "12"),
    ):
        uy = y + (unum - 1) * dy
        up = sch.place("74xx", "74HC14", ref, "SN74HCT14D", x, uy, unit=unum, footprint=FOOTPRINT_SOIC14)
        px, py = pin_pos(x, uy, up[in_p])
        sch.label(gnd, px, py)
        px, py = pin_pos(x, uy, up[out_p])
        sch.no_connect(px, py)

    pwr_y = y + 6 * dy
    pp = sch.place("74xx", "74HC14", ref, "SN74HCT14D", x, pwr_y, unit=7, footprint=FOOTPRINT_SOIC14)
    gx, gy = pin_pos(x, pwr_y, pp["7"])
    sch.label(gnd, gx, gy)
    vx, vy = pin_pos(x, pwr_y, pp["14"])
    sch.label(rail, vx, vy)

    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)
    refs["debounce_u"] = ref
    refs["debounce_decouple_c"] = cap_ref
    return ref


def place_reward_oneshot(sch, x, y, dy, in_net, out_net, rail, gnd, r_x, c_x, ic_ref, r_ref, c_ref, cdec_ref, refs):
    """Panel-instrumentation task (2026-08-15), Change A: one Nexperia 74HCT123D dual
    retriggerable monostable multivibrator ("one-shot"), so every hand-delivered reward
    gets a FIXED duration regardless of how long RWD_BTN is physically held -- the spec's
    own manual-reward paragraph (Sec.3.1): "A monostable gives every hand-delivered reward
    an identical duration regardless of how long the button is held, so manual rewards are
    countable rather than a per-press variable nothing measures."

    WIRING: `in_net` (RWD_BTN_DEB, the debounce inverter's own output -- idle-LOW,
    active-HIGH, unchanged by this task) feeds the B trigger input of unit 1. A is tied
    permanently LOW and RD-bar (reset) permanently HIGH, which the datasheet's own function
    table (Table 4, row "nRD=H, nA=L, nB=up-arrow") confirms is exactly the condition that
    fires one pulse on B's own rising edge -- RWD_BTN_DEB's ONLY transition per physical
    press, so retriggering (this part IS retriggerable -- see below) never extends a single
    continuous hold. `out_net` (a new LOCAL name, RWD_BTN_PULSE) is unit 1's own Q output
    (active-HIGH, matching RWD_BTN_DEB's own polarity) -- this is what the OR gate's own
    input B now reads, in place of RWD_BTN_DEB directly.

    PIN MAP -- VERIFIED PIN-BY-PIN AGAINST THE REAL DATASHEET, not assumed from the stock
    KiCad symbol alone (constraint 5): Philips/Nexperia "74HC123; 74HCT123 -- Dual
    retriggerable monostable multivibrator with reset", Rev. 03, 11 May 2004, Section 6.1
    "Pinning" (Fig. 5) and Section 6.2 "Pin description" (Table 3), 16-pin SO16/DIP16/
    SSOP16/TSSOP16: pin1=1A-bar, pin2=1B, pin3=1RD-bar, pin4=1Q-bar, pin5=2Q, pin6=2CEXT,
    pin7=2REXT/CEXT, pin8=GND, pin9=2A-bar, pin10=2B, pin11=2RD-bar, pin12=2Q-bar, pin13=1Q,
    pin14=1CEXT, pin15=1REXT/CEXT, pin16=VCC -- cross-checked directly against KiCad's own
    "74xx:74HCT123" library entry (kicad_sch.py's own extract_symbol()/unit_pins(), the
    same machinery sch.place() uses) and found to match EXACTLY: unit 1 -- {1:A(input),
    2:B(input), 3:Clr(input), 4:~Q(output), 13:Q(output), 14:Cext(input), 15:RCext(input)};
    unit 2 -- the mirror set on {9,10,11,12,5,6,7}; unit 3 (KiCad's own "power" pseudo-unit,
    same convention comparators.kicad_sch's own LM339 unit 5 already established) --
    {8:GND(power_in), 16:VCC(power_in)}. This is a genuine, current, real stock KiCad
    symbol -- no extends-flattening, no stand-in Value substitution needed the way this
    sheet's own 74LS32/74HC14 placements need for the OR gate/debounce inverter (module
    docstring, "no stock '74HCT32'/'74HCT14' symbol exists" -- both untrue here: KiCad's
    own library ships "74HCT123" by name).

    REAL ORDERED PART: Nexperia 74HCT123D (SO16), NOT a TI part -- checked and rejected:
    TI's own catalog entry for this function (CD74HCT123M/MT) is OBSOLETE, no longer
    manufactured (confirmed via live search, not assumed from the symbol's own generic
    "74HCT123" library name). Nexperia's 74HCT123D is current production and in stock at
    multiple major distributors (DigiKey, Mouser, TME, thousands of units each, checked
    2026-08-15) -- the function itself is a JEDEC-standard part (the datasheet's own
    Section 1: "specified in compliance with JEDEC standard no. 7A"), so any compliant
    vendor's part shares this identical pinout, the same cross-manufacturer-interoperable
    reasoning hardware/README.md's own M12A_5 sourcing record already establishes for a
    different part class.

    PULSE WIDTH -- 442k Rext (E96, 1%) + 1uF Cext (a common, small, hand-solderable 0603/
    0805 ceramic value), giving tW = K x Rext[kOhm] x Cext[pF] = 0.45 x 442 x 1,000,000 =
    198,900,000 ns = ~199 ms (K=0.45 at VCC=5.0V -- the SAME datasheet's own Table 10
    footnote [1], stated for the HCT variant specifically, not interpolated from the HC
    one). Cross-validated against a SECOND, independent data point in the SAME table
    (Table 10's own measured "output pulse width" row: CEXT=100nF, REXT=10kOhm, VCC=5.0V
    -> Typ=450us; the formula predicts 0.45x10x100,000=450,000ns=450us EXACTLY) -- this
    project's own established discipline of confirming a formula against a real measured
    data point before trusting it (see comparators.kicad_sch's own hysteresis derivation
    for the same pattern), not the formula alone. 442k sits inside the datasheet's own
    stated REXT range (2kOhm-1MOhm, VCC=5.0V) with wide margin either side; 1uF is far
    above the "CEXT > 10nF" threshold the linear formula requires to be valid (Cext<10nF
    needs Fig. 10's own graph instead -- not this design's case).

    WHY ~200 ms: no reward driver is chosen yet (spec Sec.2, open item 13 -- "the driver is
    unchosen"), so an exact fluid volume cannot be computed here. ~200 ms sits in the
    commonly-used range for a single commanded "drop" pulse in this class of behavioural
    rig (tens to a few hundred ms), giving a manual press roughly the same order of
    magnitude as one automated reward rather than an arbitrary duration -- and it is
    comfortably longer than this task's own 10 ms debounce time constant and NI/Intan/
    module-side logic propagation delays (all sub-microsecond to low-microsecond scale),
    so the one-shot's own duration is unambiguously what sets RWD_DLVR's pulse width, not
    an accidental near-collision with a faster timing constant elsewhere on this path.

    RETRIGGERABLE, DELIBERATELY NOT WORKED AROUND: the 74HC123/74HCT123 family retriggers
    (a NEW rising edge on B while Q is already HIGH restarts the timer, extending the
    pulse -- datasheet Section 1, item 2; TI's own SLVA720A app report, Section 3.2,
    Figure 3-3, confirms the general behaviour for the whole 123-style family). This does
    NOT undermine "fixed duration regardless of how long the button is held": a single
    continuous press produces exactly ONE rising edge on the ALREADY-debounced RWD_BTN_DEB
    (bounce is filtered upstream, before this part ever sees the signal), so holding the
    button for one second or ten produces the identical ~199 ms pulse either way --
    retriggering only matters for a SECOND physical press arriving inside the first
    pulse's own window, a genuinely different scenario (rapid repeated presses) than the
    one this task names, and even then the behaviour (the driver stays asserted across
    both presses rather than chopping into two truncated pulses) is not obviously wrong.
    Recorded here rather than hidden, matching this project's own standing practice for a
    real, named trade-off (e.g. RWD_CMD's own assumed polarity).

    UNUSED SECOND SECTION (unit 2): held in PERMANENT RESET (RD-bar tied to `gnd`, not
    merely left untriggered) -- the function table's own row 1 ("nRD=L, nA=X, nB=X: nQ=L")
    confirms this is what actually guarantees the section can never assert, not just tying
    A/B to safe levels (also done here, belt-and-suspenders, matching this project's own
    "never leave a CMOS input floating" rule for every other unused gate/inverter on this
    sheet). Its own CEXT/RCEXT pins are grounded too -- for a genuinely unused section this
    mirrors the datasheet's own Fig. 12 note 1 ("for minimum noise generation it is
    recommended to ground pins 6 (2CEXT) and 14 (1CEXT)" when a section's external timing
    network is not used) rather than contradicting it; unit 1's own pin 14 is NOT grounded
    (it is the real Cext node -- see "RC TOPOLOGY" below), so this note only applies to the
    section this task genuinely does not use.

    RC TOPOLOGY, confirmed from a primary TI source (SLVA720A "Designing With the
    SN74LVC1G123 Monostable Multivibrator", Figure 3-1, the same 123-family internal
    topology every device in this family shares): Rext bridges VCC to the REXT/CEXT pin;
    Cext bridges FROM THAT SAME NODE to the CEXT pin (the internal discharge-sense node) --
    R and C are in series, sharing one node, NOT each independently returned to a rail.
    Wired here exactly that way: `rail` -> R(ref `r_ref`) -> "{ic_ref}_RCEXT" (pin 15) ->
    C(ref `c_ref`) -> "{ic_ref}_CEXT" (pin 14).

    REFDES -- ALL FOUR new components (`ic_ref`/`r_ref`/`c_ref`/`cdec_ref`) are minted
    OUT OF BAND (explicit ref strings, never `sch.next_ref()`), for the identical reason
    R190/F1/J52-55/C147-148/#PWR9-10 already are on this board (constraint 3: never
    renumber an existing refdes): this file's own PRE-EXISTING next_ref() calls (every
    connector/resistor/capacitor/IC placed before this function runs) must come out
    byte-for-byte identical to before this task, and every downstream sibling sheet seeded
    its OWN reference counters by reading THIS file's committed maxima at ITS OWN
    generation time -- an ordinary next_ref() call here would either renumber something
    already placed later in this same file, or mint a number a sibling sheet already
    claimed for an unrelated part. See build()'s own comment for the whole-board baseline
    this was computed against, and this task's own report for the empirical refdes diff
    that confirms nothing else moved.
    """
    cpins = {}
    for unit, uy in ((1, y), (2, y + dy), (3, y + 2 * dy)):
        cpins[unit] = sch.place(
            "74xx", "74HCT123", ic_ref, "74HCT123D", x, uy, unit=unit,
            footprint=FOOTPRINT_SOIC16,
            extra_props={"Description": "Dual retriggerable monostable multivibrator with reset"} if unit == 1 else None,
        )

    # --- Unit 1: the real, used monostable ---
    lbl1 = cpins[1]
    for pin_num, net in (("1", gnd), ("2", in_net), ("3", rail)):
        px, py = pin_pos(x, y, lbl1[pin_num])
        sch.label(net, px, py)
    for pin_num in ("4",):  # ~Q -- unused complementary output, genuinely no-connect
        px, py = pin_pos(x, y, lbl1[pin_num])
        sch.no_connect(px, py)
    px, py = pin_pos(x, y, lbl1["13"])  # Q -- the real, used, active-HIGH pulse output
    sch.label(out_net, px, py)
    rcext_net = f"{ic_ref}_RCEXT"
    cext_net = f"{ic_ref}_CEXT"
    px, py = pin_pos(x, y, lbl1["15"])
    sch.label(rcext_net, px, py)
    px, py = pin_pos(x, y, lbl1["14"])
    sch.label(cext_net, px, py)

    # --- Unit 2: genuinely unused -- held in permanent reset, every pin tied off ---
    y2 = y + dy
    lbl2 = cpins[2]
    for pin_num in ("9", "10", "11", "6", "7"):  # A, B, Clr(reset), Cext, Rext/Cext
        px, py = pin_pos(x, y2, lbl2[pin_num])
        sch.label(gnd, px, py)
    for pin_num in ("5", "12"):  # Q, ~Q -- outputs, safe to no-connect
        px, py = pin_pos(x, y2, lbl2[pin_num])
        sch.no_connect(px, py)

    # --- Unit 3: power ---
    y3 = y + 2 * dy
    lbl3 = cpins[3]
    px, py = pin_pos(x, y3, lbl3["8"])
    sch.label(gnd, px, py)
    px, py = pin_pos(x, y3, lbl3["16"])
    sch.label(rail, px, py)

    # --- Rext (VCC -> REXT/CEXT node) and Cext (REXT/CEXT node -> CEXT node), in series
    # -- see "RC TOPOLOGY" above. ---
    r_pins = sch.place("Device", "R", r_ref, "442k", r_x, y, footprint=FOOTPRINT_R)
    px, py = pin_pos(r_x, y, r_pins["1"])
    sch.label(rail, px, py)
    px, py = pin_pos(r_x, y, r_pins["2"])
    sch.label(rcext_net, px, py)

    c_pins = sch.place("Device", "C", c_ref, "1uF", c_x, y, footprint=FOOTPRINT_C_SMALL)
    px, py = pin_pos(c_x, y, c_pins["1"])
    sch.label(rcext_net, px, py)
    px, py = pin_pos(c_x, y, c_pins["2"])
    sch.label(cext_net, px, py)

    cdec_pins = sch.place("Device", "C", cdec_ref, "100nF", x - DECOUPLE_DX, y3, footprint=FOOTPRINT_C_SMALL)
    px, py = pin_pos(x - DECOUPLE_DX, y3, cdec_pins["1"])
    sch.label(rail, px, py)
    px, py = pin_pos(x - DECOUPLE_DX, y3, cdec_pins["2"])
    sch.label(gnd, px, py)

    refs["oneshot_u"] = ic_ref
    refs["oneshot_rext"] = r_ref
    refs["oneshot_cext"] = c_ref
    refs["oneshot_decouple_c"] = cdec_ref


# ---------------------------------------------------------------------------
# The 19 inbound channels: (channel index, raw/MDR-side net, clamp/buffer-input net,
# LVC541-bank output net, HCT541-_BUF-bank output net). The first 17 (event data +
# strobe) carry the net-naming table's own explicit "_PI"/"_BUF" domain suffixes; the
# last two (RWD_CMD, STIM_TRIG) are named BARE at the LVC541 bank's own output -- the
# delegator's own contract net list gives these two no "_PI" variant at all, unlike
# every EVT_* channel -- so this sheet's inbound '541 output pin for RWD_CMD/STIM_TRIG
# IS the final contract net directly, not an internal name a downstream sheet would
# need to know to re-derive.
# ---------------------------------------------------------------------------
def _inbound_channel_specs():
    specs = []
    for i in range(16):
        specs.append((i, f"EVT_D{i}_TPC", f"EVT_D{i}_CLAMP", f"EVT_D{i}_PI", f"EVT_D{i}_BUF"))
    specs.append((16, "EVT_STROBE_TPC", "EVT_STROBE_CLAMP", "EVT_STROBE_PI", "EVT_STROBE_BUF"))
    specs.append((17, "RWD_CMD_TPC", "RWD_CMD_CLAMP", "RWD_CMD", "RWD_CMD_BUF"))
    specs.append((18, "STIM_TRIG_TPC", "STIM_TRIG_CLAMP", "STIM_TRIG", "STIM_TRIG_BUF"))
    return specs


INBOUND_CHANNELS = _inbound_channel_specs()
assert len(INBOUND_CHANNELS) == 19

# The 4 outbound channels: (raw/MDR-side output net (this sheet's own naming -- no other
# sheet needs to reference it by name), input net this sheet CONSUMES from elsewhere by
# name -- PD1_COMP/PD2_COMP/ACC_TRIG from Task 10's comparators sheet, RHS_STIM_OUT from
# Task 11's opto-intan sheet).
OUTBOUND_CHANNELS = [
    ("PD1_COMP", "PD1_COMP_TPC"),
    ("PD2_COMP", "PD2_COMP_TPC"),
    ("ACC_TRIG", "ACC_TRIG_TPC"),
    ("RHS_STIM_OUT", "RHS_STIM_OUT_TPC"),
]

# ---------------------------------------------------------------------------
# ONE OPTOCOUPLER LED PER DRIVER PIN -- the second leg of each doubled load.
#
# THE DEFECT THIS FIXES: the 19-channel `_BUF` bank above names 19 signals, but the board
# carries 30 optocoupler LEDs. The 11 unnamed ones ended up doubled onto driver pins that
# already had a leg: EVT_STROBE_BUF, RWD_CMD_BUF and STIM_TRIG_BUF each fed BOTH an NI
# ACSL-6400 LED and an Intan one from a single HCT541 output, and bare RWD_DLVR fed both
# of ITS optocouplers straight off the reward-OR gate. Each ACSL-6400 LED draws
# (5V - VOL - VF)/430R ~ 7.33mA (gen_breakout_opto_ni.py's own derivation), so a doubled
# pin sinks 14.7mA -- against SN74HCT541's own 6mA IOL, and against SN74HCT32's 4mA. The
# 74HCT32 case was the dangerous one: its LOW level is read by U14 (SN74LVC541APW,
# V_IL,max 0.8V), and an HCT gate's VOL at 3.7x its rated sink plausibly exceeds that,
# leaving RWD_DLVR_PI stuck HIGH -- the sync module recording reward-delivered
# continuously. That is the same failure signature as the reward-OR polarity defect this
# sheet already caught once at fix round 1.
#
# THE FIX: U10 (the _BUF bank's own third package, 3 of 8 channels used) has exactly 5
# spare channels, and this table spends all 5 of them -- one dedicated buffer pin per LED.
# The three *_INTAN_BUF legs tap the SAME *_CLAMP node their NI twin already taps (the
# established fan-out point: one protection network, N independent buffered copies -- see
# _place_inbound()), so they are genuinely parallel copies, not a re-derived signal. The
# two RWD_DLVR legs tap RWD_DLVR itself, which afterwards drives only CMOS inputs and a
# panel header -- ~0mA DC out of the 74HCT32.
#
# (channel index on U10, input net, output net, which optocoupler it drives)
SECOND_LEG_CHANNELS = [
    (3, "EVT_STROBE_CLAMP", "EVT_STROBE_INTAN_BUF", "opto-intan U64 channel 1"),
    (4, "RWD_CMD_CLAMP", "RWD_CMD_INTAN_BUF", "opto-intan U64 channel 3"),
    (5, "STIM_TRIG_CLAMP", "STIM_TRIG_INTAN_BUF", "opto-intan U65 channel 1"),
    (6, "RWD_DLVR", "RWD_DLVR_BUF", "opto-ni U60 channel 4"),
    (7, "RWD_DLVR", "RWD_DLVR_INTAN_BUF", "opto-intan U64 channel 4"),
]
assert len(SECOND_LEG_CHANNELS) == 5
assert {c[0] for c in SECOND_LEG_CHANNELS} == {3, 4, 5, 6, 7}, "must use exactly U10's 5 spare channels"

# The two comparator outputs whose NI optocoupler LED used to hang directly off the LM339
# -- a SECOND, independent defect on the same nets, and the one that survives even with
# the LM339's own supply rail correct. PD1_COMP/PD2_COMP wire DIRECTLY into the sync
# module's GPIO20/GPIO21 (pi-interface.kicad_sch, kind="direct"), and opto-ni.kicad_sch
# hung an ACSL-6400 LED on each of them whose anode sits on +5V through 430R. With the LED
# off, that node idles at ~3.6-3.8V (the LED's own leakage against the 10k pull-up to
# +3V3) -- ABOVE the 3.3V rail, held only by the module's ESD clamp; and with this board
# powered while the sync box is off, ~7mA is injected into an unpowered pad. These were
# the only 2 of the 24 NI optocoupler LEDs not driven from a buffered leg. They are now,
# from 2 of the outbound HCT541's own 4 spare channels -- the same package that already
# buffers these exact two nets toward the task PC, so no new part and no new protection
# network is involved. (output-only: no MDR68 pin, unlike OUTBOUND_CHANNELS.)
#
# (channel index on the outbound HCT541, input net, output net)
COMPARATOR_OPTO_LEGS = [
    (4, "PD1_COMP", "PD1_COMP_BUF"),
    (5, "PD2_COMP", "PD2_COMP_BUF"),
]

# ---------------------------------------------------------------------------
# FINDING F1(b) -- TWO BUFFER OUTPUTS PER OPTOCOUPLER LED (parametric-audit.md,
# 2026-08-16).
#
# The other half of F1. The resistor half alone (430R -> 249R, so the LED actually clears
# its 7-8mA switching floor) was implemented once, verified, and REVERTED, because it is
# not shippable on its own: 249R draws ~12.7mA, and one SN74AHCT541 output is budgeted at
# 7.5mA. Neither value works alone -- 430R starves the LED below its switching threshold,
# 249R overloads the driver -- so the two halves land together or not at all.
#
# Paralleling two outputs per LED halves the per-pin load to ~6.3mA and, just as
# importantly, fixes a PACKAGE-level limit that no per-pin check could see: the AHCT541's
# GND-pin absolute maximum is 75mA (SCLS269Q Sec.4.1) and U8/U9 each drive EIGHT LEDs.
# The drive is active-low and the event bus idles at 0x0000, so sixteen LEDs are lit
# CONTINUOUSLY -- a steady state, not a transient. At 249R unparalleled that is ~101mA
# through one ground pin.
#
# THE MIRRORS ARE COMPUTED, NOT RE-LISTED. Each new package takes the (input, output)
# pairs of the package it doubles, straight from the same tables that built the original
# -- so a channel cannot be added to one leg and forgotten on the other, and the "both
# outputs must share one input net" safety property (two tri-state outputs disagreeing is
# a short across the die, not a marginal load) holds by construction rather than by
# review. tests/hardware/test_netlist.py checks it independently anyway.
#
# REFDES ARE MINTED OUT OF BAND, exactly as U69/R191/C149-C150 already are on this sheet
# (see place_reward_oneshot()'s own REFDES note) and for the identical reason: an
# ordinary next_ref() call here would renumber every part this file places AFTER it, and
# every downstream sibling sheet seeded its own counters from this file's committed
# maxima. U70-U73 and C151-C154 sit above the whole board's current maximum (U69, C150),
# so they collide with nothing and move nothing.
#
# COUNT: this takes the board from 5 AHCT541 packages to 9. The audit estimated 9 from
# "30 LEDs plus ~12 other outputs"; the figure derived from the real netlist is the same
# 9 but for slightly different reasons -- 2 of those 30 LEDs are not buffer-driven at all
# (RHS_STIM_OUT's LED is driven by an ACSL-6420 output, and OPTO_INTAN_SPARE1_IN is an
# unpopulated spare), there are 8 rather than ~12 other outputs, and F1 itself adds a
# 29th buffer-driven LED by moving RHS_STIM_OUT's LED onto a buffered leg (see
# RHS_STIM_OUT_BUF_LEGS). 29 LEDs x 2 + 8 others = 66 channels = 9 packages, with 6
# spare -- which is also what finding F4 needs when the camera triggers are respread.
PARALLEL_LEG_REFS = [("U70", "C151"), ("U71", "C152"), ("U72", "C153"), ("U73", "C154")]

# RHS_STIM_OUT's own LED needs a buffered leg, which it did not have before F1. Its
# driver is an ACSL-6420 output (opto-intan's inbound channel), NOT a buffer, so it
# cannot be paralleled -- and at 249R its LED alone draws ~12.7mA, which with the 3.9k
# pull-up already on that net is ~13.9mA against the ACSL's own 13mA I_OL budget. It was
# the ONE channel of opto-ni's 24 whose LED hung directly on a non-buffer output; at 430R
# that was comfortably inside spec (8.6mA) and gen_breakout_opto_ni.py's own
# NI_CHANNELS comment records it as a deliberate, checked exception. The resistor change
# retires that exception, so the LED moves to a buffered leg like every other channel and
# the ACSL output is left driving only its pull-up (~1.2mA). U11's channel 3 already
# takes RHS_STIM_OUT as an input, so both legs sit on the package that already has it.
RHS_STIM_OUT_BUF_LEGS = [(6, "RHS_STIM_OUT", "RHS_STIM_OUT_BUF"),
                         (7, "RHS_STIM_OUT", "RHS_STIM_OUT_BUF")]


def _place_connector0(sch, refs):
    """Step 1, Connector 0: analog + AISENSE, per spec Sec.9.2's own confirmed split
    (AI0-15 + P0.0-7 + P1.0-7).

    FIX ROUND 1 (task-8-report.md, finding 3): pre-fix-round-1, this placed Connector 0
    with EVERY pin no_connect, reasoning that Task 10 (which owns the front-end/buffer
    circuitry the 9 task-PC analog channels need) should wire it. That is wrong division
    of labour, not just incomplete: KiCad only lets the .kicad_sch file that PLACES a
    symbol instance attach labels to its own pins, so if this sheet leaves Connector 0's
    AI pins unlabelled, Task 10's own generator (a DIFFERENT file) can never name them --
    the same constraint _place_connector1() already has to respect for Connector 1.
    Since this project's connectivity is entirely label-based (module docstring), the fix
    is straightforward: attach the 9 task-PC analog channels' own GLOBAL LABELS to
    Connector 0's AI pins HERE, now, using the plan's own fixed contract names
    (ANALOG_CONTRACT_NETS/ANALOG_CHANNELS above) -- Task 10 simply drives the same names
    from its own buffer outputs when it exists, exactly like every other cross-sheet net
    in this design already works. This sheet still does not build any analog circuitry
    (no op-amps, no series R, no clamps -- that is genuinely Task 10's own front-end/
    buffer work, per plan.md Task 10 Steps 1-3); it only makes the 10 pins Task 10 will
    need (9 AI channels + AISENSE) nameable from outside this file.

    Also ties AISENSE (MDR0_AISENSE_PIN, physical pin 62) directly to AGND -- spec
    Decision 5 and Task 10 Step 3's own text ("AISENSE tied to AGND here too"): this
    single wire is what makes NRSE work on the NI side (every AI channel reads against
    this one shared reference instead of needing 16 dedicated differential pairs), and is
    named in the finding as "the easiest thing on the board to omit by accident" --
    checked explicitly by check_taskpc_digital_netlist.py's own verify().

    Every one of Connector 0's other 58 pins (P0.0-7/P1.0-7/P2.x-on-this-connector, the
    7 spare AI channels AI9-AI15, D GND, +5V, AO0/AO1/AOGND, NC -- see MDR1_PIN_BY_P0's
    sibling table in this file's own "Fix round 1" report section for the full Connector
    0 pinout) stays no_connect, unchanged: none of them are in this task's own contract,
    and reaching for them now would be inventing wiring nobody has asked this sheet to
    carry. If Task 10 later needs one of those (say, a second DGND reference), it will
    hit the identical "only the placing file can label this pin" constraint this fix
    round resolves for the analog channels -- flagged here for whoever hits it next.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "MDR68_Male", ref,
        "Connector 0 (analog + AISENSE, task PC NI)",
        X_CONN0, Y_CONN0, footprint=FOOTPRINT_MDR68,
    )
    refs["conn0"] = ref
    used = set()

    for net_name, ai_chan in ANALOG_CHANNELS:
        pin_num = MDR0_AI_PIN[ai_chan]
        x, y = pin_pos(X_CONN0, Y_CONN0, pins[pin_num])
        sch.label(net_name, x, y)
        used.add(pin_num)

    x, y = pin_pos(X_CONN0, Y_CONN0, pins[MDR0_AISENSE_PIN])
    sch.label("AGND", x, y)
    used.add(MDR0_AISENSE_PIN)

    for n in range(1, 69):
        num = str(n)
        if num not in used:
            x, y = pin_pos(X_CONN0, Y_CONN0, pins[num])
            sch.no_connect(x, y)

    for line_idx, line in enumerate([
        "Connector 0 (analog + AISENSE): 9 task-PC analog channels + AISENSE wired here",
        "(fix round 1, finding 3); Task 10 drives these SAME contract net names from its",
        "own front-end/buffer outputs (plan.md Task 10 Step 3) -- no buffer/series-R/",
        "clamp circuitry lives on THIS sheet, only the labelled connection point does.",
        "Physical pins sourced from NI's X Series User Manual (370784K-01), Figure A-5",
        "\"NI PCIe-6323/6343 Pinout\", same source as Connector 1 below -- see this",
        "generator's own MDR0_AI_PIN/MDR0_AISENSE_PIN and task-8-report.md \"Fix round 1\".",
        "AI0=pin68=A_EYE_LX_TPC  AI1=pin33=A_EYE_LY_TPC  AI2=pin65=A_EYE_RX_TPC",
        "AI3=pin30=A_EYE_RY_TPC  AI4=pin28=A_JOY_X_TPC   AI5=pin60=A_JOY_Y_TPC",
        "AI6=pin25=A_MISC1_TPC  AI7=pin57=A_MISC2_TPC  AI8=pin34=A_MISC3_TPC",
        "AISENSE=pin62 -> AGND (NRSE reference -- spec Decision 5; the easiest wire on",
        "this board to omit by accident, so check_taskpc_digital_netlist.py asserts it).",
        "Remaining 58 pins (spare AI9-15, P0.0-7/P1.0-7/P2.x, D GND, +5V, AO*, NC) stay",
        "no_connect: outside this task's own contract -- see _place_connector0()'s own",
        "docstring.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)


def _place_connector1(sch, refs):
    """Step 1, Connector 1: digital, per spec Sec.9.2's own confirmed split (AI16-31 +
    P0.8-31 + P2.0-7). This design's own 23 task-PC digital lines (19 out + 4 in, spec
    Sec.3.1) all ride here, on the P0.8-P0.30 range of the 24 available on P0.8-31 --
    leaving P0.31 and all 8 of P2.0-7 spare.

    FIX ROUND 1 (task-8-report.md, finding 2): physical MDR68 pin assignment is now
    SOURCED, not this sheet's own PRE-fix-round-1 sequential guess (pins "1".."23" for
    the 23 signals, "24"/"25" for two arbitrary DGND references) -- that guess was
    flagged, correctly, as a real risk: "wrong means the cable delivers every event-code
    bit to the wrong DAQ line, and it would pass ERC, pass the channel-walk checker, and
    fail only on a bench." NI's own device pinout DOES exist and does fix this: "X Series
    User Manual: NI 632x/634x/635x/636x/637x/638x/639x Devices" (National Instruments
    370784K-01, May 2019, ni.com/manuals), Figure A-5 "NI PCIe-6323/6343 Pinout" -- naming
    the task PC's own card (PCIe-6343) explicitly. Every P0.x position and every D GND
    position below (MDR1_PIN_BY_P0/MDR1_DGND_PINS, module-level above) is transcribed
    verbatim from that figure, cross-checked by two independent parses of the source
    PDF's own extracted text that agreed exactly -- see task-8-report.md's "Fix round 1"
    for the retrieval method. This is no longer an open item needing bench confirmation
    before cabling; it is sourced from the same manual spec Sec.9.2/9.3 already cites for
    the connector-level split, just extended to the individual pin level.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "MDR68_Male", ref, "Connector 1 (digital, task PC NI)",
        X_CONN1, Y_CONN1, footprint=FOOTPRINT_MDR68,
    )
    refs["conn1"] = ref
    used = set()
    for line_idx, line in enumerate([
        "Connector 0/Connector 1 split confirmed from NI's own device specifications",
        "(PCIe-6343 task-PC card; identical to the 6353/6363 on every axis used here,",
        "spec Sec.9.3): Connector 0 = AI0-15 + P0.0-7 + P1.0-7 (analog+AISENSE).",
        "Connector 1 = AI16-31 + P0.8-31 + P2.0-7 (digital). See spec Sec.9.2, closed",
        "2026-08-13 (formerly open item 3).",
        "All 23 task-PC digital lines (19 out + 4 in) ride Connector 1, P0.8-P0.30 of",
        "the 24 available on P0.8-31. Event data bits occupy P0.8-P0.23, NOT P0.0-",
        "P0.15 -- MonkeyLogic must be configured for this non-zero-based line range.",
        "Physical MDR68 pin per P0.x line is SOURCED (fix round 1, finding 2), not",
        "sequential -- NI's own X Series User Manual (370784K-01), Figure A-5 \"NI",
        "PCIe-6323/6343 Pinout\": P0.8=pin52 P0.9=pin17 P0.10=pin49 P0.11=pin47",
        "P0.12=pin19 P0.13=pin51 P0.14=pin16 P0.15=pin48 P0.16=pin11 P0.17=pin10",
        "P0.18=pin43 P0.19=pin42 P0.20=pin41 P0.21=pin6 P0.22=pin5 P0.23=pin38",
        "P0.24=pin37(STROBE) P0.25=pin3(RWD_CMD) P0.26=pin45(STIM_TRIG)",
        "P0.27=pin46(PD1_COMP) P0.28=pin2(PD2_COMP) P0.29=pin40(ACC_TRIG)",
        "P0.30=pin1(RHS_STIM_OUT) P0.31=pin39 (spare, no_connect). D GND (all 12 real",
        "pins tied, not an arbitrary 2): 4,7,9,12,13,15,18,35,36,44,50,53.",
        "See this generator's own MDR1_PIN_BY_P0/MDR1_DGND_PINS and",
        "task-8-report.md \"Fix round 1\" for the full retrieval/cross-check method.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for ch_idx, raw_net, _clamp, _pi, _buf in INBOUND_CHANNELS:
        pin_num = MDR1_PIN_BY_P0[8 + ch_idx]
        x, y = pin_pos(X_CONN1, Y_CONN1, pins[pin_num])
        sch.label(raw_net, x, y)
        used.add(pin_num)

    for i, (_in_net, raw_out_net) in enumerate(OUTBOUND_CHANNELS):
        pin_num = MDR1_PIN_BY_P0[27 + i]
        x, y = pin_pos(X_CONN1, Y_CONN1, pins[pin_num])
        sch.label(raw_out_net, x, y)
        used.add(pin_num)

    for gnd_pin in MDR1_DGND_PINS:
        x, y = pin_pos(X_CONN1, Y_CONN1, pins[gnd_pin])
        sch.label("DGND", x, y)
        used.add(gnd_pin)

    for n in range(1, 69):
        num = str(n)
        if num not in used:
            x, y = pin_pos(X_CONN1, Y_CONN1, pins[num])
            sch.no_connect(x, y)

    return pins


def _place_inbound(sch, refs):
    """Step 2: 19 inbound channels, each MDR68 pin -> 100R -> BAT54S clamp (high side
    +5V) -> fans out in parallel to the LVC541 (+3V3) bank's own input pin AND the
    HCT541 _BUF bank's own input pin -- one protection network serving two independent
    buffered copies, not two separate R/clamp networks. See INBOUND_CHANNELS for the
    exact net names each of the 19 channels carries at every hop.
    """
    refs["inbound_r"] = []
    refs["inbound_clamp_d"] = []

    for ch_idx, raw_net, clamp_net, _pi_net, _buf_net in INBOUND_CHANNELS:
        y = ROW0 + ch_idx * ROW_PITCH
        r_ref = two_pin(sch, "Device", "R", "R", "100", X_R_IN, y, raw_net, clamp_net, footprint=FOOTPRINT_R)
        refs["inbound_r"].append(r_ref)
        d_ref = bidirectional_clamp(sch, X_CLAMP, y, clamp_net, "+5V", "DGND")
        refs["inbound_clamp_d"].append(d_ref)

    # LVC541 bank (+3V3): U1 data0-7, U2 data8-15, U3 strobe/RWD_CMD/STIM_TRIG (3 of 8).
    lvc_channels = [{}, {}, {}]
    for ch_idx, _raw, clamp_net, pi_net, _buf in INBOUND_CHANNELS:
        bank, local = divmod(ch_idx, 8)
        lvc_channels[bank][local] = (clamp_net, pi_net)
    for bank_idx, (x, y) in enumerate([(X_LVC, Y_LVC1), (X_LVC, Y_LVC2), (X_LVC, Y_LVC3)]):
        place_octal_buffer(
            sch, "74xx", "74LS541", "SN74LVC541APW", x, y, "+3V3",
            lvc_channels[bank_idx], refs, "lvc541", FOOTPRINT_TSSOP20,
        )

    # HCT541 _BUF bank (+5V): U4 data0-7_BUF, U5 data8-15_BUF, U6 strobe/RWD_CMD/
    # STIM_TRIG _BUF + the 5 second legs (8 of 8) -- same channel grouping, input taps the
    # SAME *_CLAMP net.
    buf_channels = [{}, {}, {}]
    for ch_idx, _raw, clamp_net, _pi, buf_net in INBOUND_CHANNELS:
        bank, local = divmod(ch_idx, 8)
        buf_channels[bank][local] = (clamp_net, buf_net)
    # One LED per driver pin -- see SECOND_LEG_CHANNELS' own comment for the defect these
    # five channels fix. They all land on the third package, which this fills to 8 of 8.
    for local, in_net, out_net, _dest in SECOND_LEG_CHANNELS:
        assert local not in buf_channels[2], f"second-leg channel {local} collides with an inbound channel"
        buf_channels[2][local] = (in_net, out_net)
    assert len(buf_channels[2]) == 8, f"expected the third _BUF package fully used, got {sorted(buf_channels[2])}"
    for bank_idx, (x, y) in enumerate([(X_HCTBUF, Y_HCTBUF1), (X_HCTBUF, Y_HCTBUF2), (X_HCTBUF, Y_HCTBUF3)]):
        place_octal_buffer(
            sch, "74xx", "74AHCT541", "SN74AHCT541PW", x, y, "+5V",
            buf_channels[bank_idx], refs, "hct541_buf", FOOTPRINT_TSSOP20,
        )

    for line_idx, line in enumerate([
        "ONE OPTOCOUPLER LED PER DRIVER PIN.",
        "",
        "Each ACSL-6400/6420 LED on this board is fed from +5V through 249R and draws",
        "~12.7mA as of finding F1 (2026-08-16; it was 430R/~7.33mA when this note was",
        "written, which under-drove every LED on the board -- see the F1 note block).",
        "The value cannot simply be reduced: it is pinned between the part's 7-15mA",
        "recommended band and its 7.0mA worst-case switching threshold. A driver pin",
        "with TWO LEDs on it sinks double, which is what this note block is about; F1",
        "separately gives each SINGLE LED two outputs, which is the opposite thing.",
        "",
        "The _BUF bank names 19 SIGNALS, but the board carries 30 LEDs. The 11 unnamed",
        "ones were doubled up: EVT_STROBE_BUF, RWD_CMD_BUF and STIM_TRIG_BUF each fed",
        "an NI LED AND an Intan LED from one HCT541 output (14.7mA vs 6mA IOL), and",
        "bare RWD_DLVR fed both of its optocouplers straight off the 74HCT32 reward-OR",
        "gate (14.7mA vs 4mA IOL). That last one was the dangerous one: RWD_DLVR's LOW",
        "is read by the LVC541 level-shifter (V_IL,max 0.8V), and an HCT gate's VOL at",
        "3.7x its rated sink plausibly exceeds that -- leaving RWD_DLVR_PI stuck HIGH,",
        "the sync module recording reward-delivered continuously. Same failure",
        "signature as the reward-OR polarity defect caught here at fix round 1.",
        "",
        "The third _BUF package's 5 spare channels are now spent, one LED per pin:",
        "  ch3 EVT_STROBE_CLAMP -> EVT_STROBE_INTAN_BUF   (opto-intan U64 ch1)",
        "  ch4 RWD_CMD_CLAMP    -> RWD_CMD_INTAN_BUF      (opto-intan U64 ch3)",
        "  ch5 STIM_TRIG_CLAMP  -> STIM_TRIG_INTAN_BUF    (opto-intan U65 ch1)",
        "  ch6 RWD_DLVR         -> RWD_DLVR_BUF           (opto-ni    U60 ch4)",
        "  ch7 RWD_DLVR         -> RWD_DLVR_INTAN_BUF     (opto-intan U64 ch4)",
        "",
        "The three *_INTAN_BUF legs tap the SAME *_CLAMP node their NI twin already",
        "taps -- the established fan-out point, one protection network with N buffered",
        "copies hanging off it, exactly as the LVC541 and HCT541 banks already share",
        "it. The two RWD_DLVR legs tap RWD_DLVR itself, which afterwards drives only",
        "CMOS inputs and a panel header: ~0mA DC out of the 74HCT32.",
        "",
        "DRIVER PART IS SN74AHCT541PW, NOT SN74HCT541PW (Value/MPN only -- identical",
        "pinout and TSSOP-20 footprint, both symbols extend the same 74LS541 KiCad",
        "part). Each LED is 7.33mA, fixed by the LED's own 7.0mA worst-case switching",
        "threshold, not a driver choice. HCT541's own rated IOL is 6mA (7.33mA is 22%",
        "over, and self-consistently against the part's own ~55ohm output impedance --",
        "not the single 6mA datasheet test point -- closer to 7.18mA against that same",
        "7.0mA floor). AHCT541's rated IOL is 8mA: 7.33mA is fully compliant, and both",
        "absolute maxima double (25mA->50mA/pin, 70mA->100mA/package), retiring the",
        "separate 58.6mA-of-70mA concern on U8/U9/U10 too.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)


def _place_outbound(sch, refs):
    """Step 3: 4 outbound channels through one more SN74AHCT541PW on +5V (4 of 8
    channels), driving Connector 1's own pins 20-23 back to the task PC. Input nets
    (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT) are produced ELSEWHERE (Task 10's
    comparators sheet; Task 11's opto-intan sheet) and consumed here by name -- this
    sheet does not need to know how they're generated, only that these names are the
    contract. No series-R/clamp input protection (unlike Step 2): these are OUR OWN
    push-pull buffer outputs driving INTO the task PC's DAQ input pins, not an
    externally sourced signal needing protection on ITS way in (see module docstring).

    TEMPORARY SCAFFOLDING, flagged loudly rather than left silent: as of this task,
    Tasks 10/11 (the real drivers of these 4 nets) do not exist yet, so
    kicad-cli sch erc's own `pin_not_driven` check correctly reports U7's own A0-A3 as
    undriven (confirmed empirically -- this is a genuine, not a false, finding on the
    CURRENT, incomplete project). A `sch.power_flag()` per net (same "assert this net IS
    driven, even though ERC can't see why from here" mechanism gen_breakout_power.py's
    own build() uses for ISO_P15_FILT/ISO_N15_FILT) clears it.

    UNLIKE the power sheet's own permanent flags, these four were NOT meant to survive
    Tasks 10/11: confirmed empirically (a throwaway test schematic, not guessed) that a
    PWR_FLAG (a `power_out`-typed pin) landing on the SAME net as an LM339 comparator's
    own `open_collector`-typed output pin -- Task 10's own named part for
    PD1_COMP/PD2_COMP/ACC_TRIG -- trips ERC's `pin_to_pin` rule: "Pins of type Open
    collector and Power output are connected". So whoever implements Task 10 (for
    PD1_COMP/PD2_COMP/ACC_TRIG) and Task 11 (for RHS_STIM_OUT, an optocoupler output
    task-11-brief.md itself describes as "totem-pole or open-collector") MUST delete
    that net's own `sch.power_flag()` call below the moment their own real driving pin
    is wired to it -- left in place, it will not merely be redundant, it will actively
    break their own sheet's ERC. Flagged again in this task's own report.

    TASK 10D UPDATE (comparators.kicad_sch, task-10d-report.md): PD1_COMP/PD2_COMP/
    ACC_TRIG now have exactly the real driver this docstring predicted -- one LM339's
    own open_collector output pin per net, wired on that sheet -- so their own three
    PWR_FLAGs are deleted below, precisely as instructed above. Confirmed empirically
    by regenerating this file against the real, now-committed comparators.kicad_sch and
    reading kicad-cli sch erc's own output, not assumed from the docstring's own
    prediction alone: before this fix, the whole-project ERC reported exactly the 3
    predicted `pin_to_pin` errors (plus one unrelated `pin_not_driven` on the DAC's own
    SCL pin, awaiting Task 12) -- 3 errors total; after, 0.

    TASK 11 UPDATE (opto-intan.kicad_sch): RHS_STIM_OUT -- the one net this docstring's
    own STILL_PENDING_OUTBOUND set was still tracking -- now ALSO has its own real
    driver (ACSL-6420's own VO3 open_collector pin, opto-intan.kicad_sch's own inbound
    channel), so its PWR_FLAG is deleted below too, by the identical mechanism and for
    the identical reason. Confirmed the same way: regenerated against the real,
    committed opto-intan.kicad_sch and read kicad-cli sch erc's own output (0 errors
    both before and after this specific deletion in isolation, since RHS_STIM_OUT's
    flag was the LAST one remaining and nothing else in this project's own current
    state depends on it) rather than assumed from the docstring's own prediction alone.
    STILL_PENDING_OUTBOUND is now empty -- all 4 outbound channels have a real driver.
    """
    channels = {i: (in_net, out_net) for i, (in_net, out_net) in enumerate(OUTBOUND_CHANNELS)}
    # Plus, at F1, this package's own last 2 spare channels: BOTH legs of
    # RHS_STIM_OUT_BUF. See RHS_STIM_OUT_BUF_LEGS for why that LED needed a buffered leg
    # at all once the resistor changed. Both legs land here rather than one here and one
    # on the parallel bank because channel 3 already takes RHS_STIM_OUT as its input, so
    # the whole pair sits on the package that already has the signal -- and a package
    # carrying both legs of one LED still only sinks that LED's ~12.7mA, nowhere near the
    # 75mA ground-pin limit. 8 of 8 channels used after this.
    for local, in_net, out_net in RHS_STIM_OUT_BUF_LEGS:
        assert local not in channels, f"RHS_STIM_OUT_BUF leg {local} collides with an outbound channel"
        channels[local] = (in_net, out_net)
    # Plus the 2 comparator-optocoupler legs -- see COMPARATOR_OPTO_LEGS' own comment.
    # These take the same 2 input nets channels 0/1 already take (PD1_COMP/PD2_COMP) and
    # produce a SECOND buffered copy each, for opto-ni.kicad_sch's own U61 LEDs, so that
    # nothing hangs an LED (and, through it, +5V) on a net that wires straight into the
    # sync module's own 3.3V-only GPIO. 6 of 8 channels used after this.
    for local, in_net, out_net in COMPARATOR_OPTO_LEGS:
        assert local not in channels, f"comparator-opto leg {local} collides with an outbound channel"
        channels[local] = (in_net, out_net)
    # STILL_PENDING_OUTBOUND -- the subset of OUTBOUND_CHANNELS' own input nets that
    # genuinely have no real driver yet. Empty as of Task 11 (see "TASK 11 UPDATE"
    # above) -- kept as a named, empty set rather than removing the loop entirely, so a
    # later edit that reintroduces an undriven outbound net has an obvious place to add
    # it back, matching this project's own established idiom for this exact situation
    # (gen_breakout_power.py's own ISO_P15_FILT/ISO_N15_FILT, this file's own PD1_COMP/
    # PD2_COMP/ACC_TRIG before Task 10d).
    STILL_PENDING_OUTBOUND = set()
    for flag_i, (in_net, _out_net) in enumerate(OUTBOUND_CHANNELS):
        if in_net not in STILL_PENDING_OUTBOUND:
            continue  # DELETED at Task 10d (PD1_COMP/PD2_COMP/ACC_TRIG) or Task 11
            # (RHS_STIM_OUT) -- see the docstring above.
        sch.power_flag(in_net, GRID(X_OUTBUF - 40.64), GRID(Y_OUTBUF + flag_i * 5.08))
    place_octal_buffer(
        sch, "74xx", "74AHCT541", "SN74AHCT541PW", X_OUTBUF, Y_OUTBUF, "+5V",
        channels, refs, "outbound_hct541", FOOTPRINT_TSSOP20,
    )
    for line_idx, line in enumerate([
        "All 4 outbound channels (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT) now have",
        "a real driver elsewhere in this project (Task 10d's LM339 for the first three,",
        "Task 11's ACSL-6420 for RHS_STIM_OUT) -- no PWR_FLAGs remain on this sheet for",
        "any of them. See _place_outbound()'s own docstring ('TASK 11 UPDATE') for the",
        "deletion history.",
        "",
        "CHANNELS 4/5 (PD1_COMP_BUF, PD2_COMP_BUF) ARE NOT OUTBOUND -- they do not reach",
        "Connector 1 at all. They exist so opto-ni's own U61 LEDs are driven from a",
        "buffered leg instead of hanging directly on PD1_COMP/PD2_COMP, which wire",
        "STRAIGHT into the sync module's GPIO20/GPIO21 (3.3V, not 5V tolerant). An LED",
        "there means +5V through 430R and the LED reaches that pin: idling ~3.6-3.8V",
        "with the LED off (above the 3.3V rail, held only by the module's ESD clamp),",
        "and injecting ~7mA into an unpowered pad whenever this board is on and the sync",
        "box is not. These were the only 2 of 24 NI optocoupler LEDs not driven from a",
        "buffered leg. Same input net as channels 0/1 -- a second buffered copy, not a",
        "re-derived signal.",
    ]):
        sch.text(line, GRID(X_OUTBUF - 40.64), GRID(Y_OUTBUF + 30 + line_idx * 5.08))


def _place_parallel_legs(sch, refs):
    """Finding F1(b): the SECOND buffer output of every optocoupler LED pair, on four new
    SN74AHCT541PW packages. See PARALLEL_LEG_REFS for the full account of why this exists
    and why its refdes are minted out of band.

    Every channel map below is DERIVED by mirroring the package it doubles, from the same
    tables that built the original -- never re-listed. That is what makes the "both
    outputs of a pair share one input net" property hold by construction: there is only
    one list of pairs, used twice.

    U73's own remaining 6 channels are genuine spares, tied off exactly as
    place_octal_buffer() ties off any unused channel (input -> DGND, output ->
    no-connect). They are the headroom finding F4 needs when the four camera triggers are
    given one buffer channel each.
    """
    # Mirror of the three-package _BUF bank -- rebuilt here by the identical arithmetic
    # _place_inbound() uses, so the two legs cannot drift apart.
    buf_channels = [{}, {}, {}]
    for ch_idx, _raw, clamp_net, _pi, buf_net in INBOUND_CHANNELS:
        bank, local = divmod(ch_idx, 8)
        buf_channels[bank][local] = (clamp_net, buf_net)
    for local, in_net, out_net, _dest in SECOND_LEG_CHANNELS:
        buf_channels[2][local] = (in_net, out_net)

    # Mirror of the outbound package's LED-driving channels ONLY. Its other four outputs
    # (PD1_COMP_TPC, PD2_COMP_TPC, ACC_TRIG_TPC, RHS_STIM_OUT_TPC) go to Connector 1's
    # own DAQ input pins and drive no LED at all, so they need no second leg -- and
    # paralleling them would be actively wrong, doubling the parts on a net for no load.
    # RHS_STIM_OUT_BUF's two legs both live on U11 (see _place_outbound), so it does not
    # appear here either.
    outbound_led_mirror = {i: (in_net, out_net) for i, (_l, in_net, out_net)
                           in enumerate(COMPARATOR_OPTO_LEGS)}

    banks = buf_channels + [outbound_led_mirror]
    assert len(banks) == len(PARALLEL_LEG_REFS) == len(Y_PARALLEL) == 4
    for (u_ref, c_ref), channels, y in zip(PARALLEL_LEG_REFS, banks, Y_PARALLEL):
        place_octal_buffer(
            sch, "74xx", "74AHCT541", "SN74AHCT541PW", X_PARALLEL, y, "+5V",
            channels, refs, "hct541_parallel", FOOTPRINT_TSSOP20, ref=u_ref, cap_ref=c_ref,
        )

    for line_idx, line in enumerate([
        "TWO BUFFER OUTPUTS PER OPTOCOUPLER LED (finding F1b, 2026-08-16).",
        "",
        "These four packages carry the SECOND output of every LED pair. They exist",
        "because the LED series resistor had to fall from 430R to 249R -- at 430R the",
        "LEDs were never guaranteed to switch (~5mA worst case against a 7.0mA I_FH",
        "minimum, because the +5V rail reaches them through F4 and D3 and the original",
        "sizing used a nominal 5.00V and a TYPICAL V_F) -- and 249R draws ~12.7mA,",
        "which one AHCT541 output cannot sink. Neither value is shippable alone.",
        "",
        "It also fixes a PACKAGE limit no per-pin check can see. AHCT541's GND-pin",
        "absolute maximum is 75mA; U8 and U9 drive eight LEDs each; the drive is",
        "active-low and the event bus idles at 0x0000, so sixteen LEDs are lit",
        "CONTINUOUSLY. At 249R unparalleled that is ~101mA through one ground pin.",
        "Paralleled it is ~51mA, and each output pin sees ~6.3mA of a 7.5mA budget.",
        "",
        "  U70  doubles U8   -- EVT_D0_BUF .. EVT_D7_BUF",
        "  U71  doubles U9   -- EVT_D8_BUF .. EVT_D15_BUF",
        "  U72  doubles U10  -- strobe/RWD_CMD/STIM_TRIG _BUF + the 5 second legs",
        "  U73  doubles U11's two LED channels -- PD1_COMP_BUF, PD2_COMP_BUF",
        "       (6 spare channels, the headroom finding F4's camera triggers need)",
        "",
        "Each pair shares ONE input net, and that is a safety requirement rather than",
        "tidiness: two tri-state outputs on one net are safe only while both are",
        "guaranteed to drive the same direction. If their inputs ever differed, one",
        "would source while the other sinks, limited by nothing but the two output",
        "stages. The channel maps here are MIRRORED from the tables that built the",
        "first leg rather than retyped, so the two cannot drift apart.",
        "",
        "REFDES U70-U73/C151-C154 are minted OUT OF BAND (explicit strings, never",
        "next_ref()), above the whole board's current maximum -- the same mechanism",
        "U69/R191/C149-C150 already use here. An ordinary next_ref() would renumber",
        "every part placed after it AND every downstream sibling sheet.",
    ]):
        sch.text(line, X_PARALLEL, GRID(380) + line_idx * NOTE_DY)


def _place_reward_or(sch, refs):
    """Step 4: RWD_CMD (already produced in stage 2) and the debounced-then-one-shot
    manual reward button combine in a 74HCT32 OR gate to produce RWD_DLVR.

    PANEL-INSTRUMENTATION TASK (2026-08-15), CHANGES A+B -- this function's own
    reward-position connectors and reward-button signal path both change here; see the
    module docstring's own "PANEL-INSTRUMENTATION TASK" section for the full account.
    Summary:

    CHANGE A (one-shot): RWD_BTN_DEB (the debounce inverter's own output, unchanged) no
    longer feeds the OR gate directly -- it now feeds place_reward_oneshot()'s own trigger
    input, and the OR gate's own second input becomes RWD_BTN_PULSE, that one-shot's
    fixed-width (~199 ms) output. See place_reward_oneshot()'s own docstring for the full
    derivation (part, pin map, RC topology, pulse-width arithmetic).

    CHANGE A (debounce RC): the debounce capacitor is now 1uF, not 100nF -- 10k x 1uF =
    10 ms (spec Sec.3.1's own literal figure), covering both switch bounce and any
    connector transient (the remote BNC's own cable run is longer than the panel button's
    own short internal wiring, and a longer run picks up more of a connector-mating
    transient than 10k x 100nF's own ~1 ms time constant comfortably covered).

    CHANGE B (real connectors, replacing three Conn_01x02 placeholders):
      - RWD_BTN (J4): Switch:SW_Push, a real 2-terminal panel/chassis-mount momentary
        pushbutton footprint (SW_PUSH-12mm) -- RECESSED per spec Sec.3.1 ("Panel button is
        recessed so a sleeve or cable cannot dispense fluid"), a bezel/mounting detail this
        schematic-capture step records as a requirement (on-sheet, below) rather than a
        fact the SYMBOL/FOOTPRINT choice can encode; the exact recessed-bezel manufacturer
        part remains a layout-stage/procurement decision, same status as every BNC's own
        MPN on this board.
      - Remote reward jack (J5): now a real BNC (Connector:Conn_Coaxial /
        BNC_PanelMountable_Vertical -- the SAME footprint pi-interface.kicad_sch's own 5
        camera-trigger BNCs use), wired in PARALLEL with J4 onto the identical RWD_BTN
        node -- unchanged electrically from the prior placeholder's own wiring, only the
        connector class changes. BNC IS THE POINT, not an arbitrary swap: unlike the 3.5mm
        TRS this position used to represent (spec Sec.9.6: "the reward remote became a
        BNC, which was its last use" -- see module docstring), a BNC's own center pin does
        not transiently short to the shield during mating the way a TRS tip/ring/sleeve
        does while sliding past the jack's own switching contacts -- so plugging in the
        handheld remote cannot itself fire a reward, and the coupling is a lock (a real
        TRS jack has none), not friction. THIS REMOVES THE 3.5MM TRS FROM THE DESIGN
        ENTIRELY -- confirmed by grep across every hardware/gen/*.py and every
        hardware/breakout/sheets/*.kicad_sch, past and present: no generator ever placed a
        TRS footprint or symbol (the position was always a generic Conn_01x02 placeholder
        that PROCUREMENT DOCUMENTATION described as "eventually a 3.5mm TRS" -- see
        hardware/procurement-check.md and hardware/breakout/design-review.md, both updated
        alongside this change), so there is no schematic-side component to delete, only
        the placeholder's own real-world referent to correct.
      - Reward driver out (J6): now a real BNC, same footprint/symbol as J5, wiring
        unchanged (RWD_DLVR / DGND).

    RWD_BTN wiring (unchanged in substance from before this task): J4 and J5 both land on
    the same RWD_BTN node (spec Sec.9.1's own "panel momentary button + remote jack, 1+1"
    -- either shorts the node to DGND when actuated), 10k pull-up to +5V, 1uF debounce cap
    to DGND (was 100nF -- see CHANGE A above).

    POLARITY -- FIX ROUND 1 (task-8-report.md, finding 1, CRITICAL), unaffected by this
    task and restated here unchanged: the debounce stage places a SINGLE Schmitt inversion
    (place_debounce_inverter()). Traced: RWD_BTN idles HIGH (10k pull-up to +5V) and reads
    LOW while pressed; ONE inversion makes RWD_BTN_DEB idle-LOW and read HIGH while pressed
    -- active-HIGH, which is what the new one-shot's own B trigger input wants (see
    place_reward_oneshot()'s own docstring). RWD_BTN_PULSE (the one-shot's own Q output)
    inherits that SAME active-HIGH polarity unchanged (Q is HIGH during the pulse, LOW at
    rest) -- so the OR gate's own polarity requirement (both inputs active-HIGH, unchanged
    from before this task) is still satisfied, just through one more stage. RWD_CMD is
    still ASSUMED ACTIVE-HIGH (idle-LOW, pulses HIGH on delivery) -- this design's other
    event lines' own convention, and SN74LVC541APW does not invert; still unverified
    against MonkeyLogic independently, still enforced by check_taskpc_digital_netlist.py's
    own same-polarity structural check, now extended to also confirm the one-shot's own
    Q output (not RWD_BTN_DEB) is what actually reaches the OR gate.

    RWD_DLVR also drives the reward-driver BNC (J6) and is available by name (no further
    buffering needed: the OR gate already runs on +5V, the same native logic level Task
    11's NI/Intan optocouplers consume elsewhere on this design, e.g. via the *_BUF bank
    above) for Task 11's opto-ni/opto-intan sheets to pick up directly.
    """
    btn_ref = sch.next_ref("J")
    btn_pins = sch.place(
        "Switch", "SW_Push", btn_ref,
        "Manual reward button (panel, recessed)", X_RWD_BTN, Y_RWD_BTN,
        footprint=FOOTPRINT_PANEL_PUSHBUTTON,
    )
    x, y = pin_pos(X_RWD_BTN, Y_RWD_BTN, btn_pins["1"])
    sch.label("RWD_BTN", x, y)
    x, y = pin_pos(X_RWD_BTN, Y_RWD_BTN, btn_pins["2"])
    sch.label("DGND", x, y)

    jack_ref = sch.next_ref("J")
    jack_pins = sch.place(
        "Connector", "Conn_Coaxial", jack_ref,
        "Remote reward BNC (parallel to panel button; locking -- no TRS mating transient)",
        X_RWD_JACK, Y_RWD_JACK, footprint=FOOTPRINT_BNC,
    )
    x, y = pin_pos(X_RWD_JACK, Y_RWD_JACK, jack_pins["1"])
    sch.label("RWD_BTN", x, y)
    x, y = pin_pos(X_RWD_JACK, Y_RWD_JACK, jack_pins["2"])
    sch.label("DGND", x, y)

    pullup_ref = two_pin(
        sch, "Device", "R", "R", "10k", X_RWD_PULLUP, Y_RWD_PULLUP, "+5V", "RWD_BTN",
        footprint=FOOTPRINT_R,
    )
    debounce_c_ref = two_pin(
        sch, "Device", "C", "C", "1uF", X_RWD_CAP, Y_RWD_CAP, "RWD_BTN", "DGND",
        footprint=FOOTPRINT_C_SMALL,
    )

    # Call order matches the X_U8/X_U9 constant naming deliberately: next_ref("U")
    # hands out references in CALL order (U1-U3 the LVC541 bank, U4-U6 the HCT541 _BUF
    # bank, U7 the outbound HCT541 -- see _place_inbound()/_place_outbound()), so the OR
    # gate has to be placed BEFORE the debounce inverters for it to actually become "U8"
    # (matching X_U8/Y_U8's own name) rather than "U9" -- confirmed against a real
    # kicad-cli sch export netlist run, not assumed; the reversed order was tried first
    # and produced exactly this mismatch (debounce inverters silently became U8, the OR
    # gate U9), harmless electrically (a component's real identity is its Value/pins,
    # never its bare reference number) but confusing for anyone reading this file
    # against the real output, so fixed here rather than left as a footgun. UNCHANGED by
    # this task -- the one-shot is minted entirely out of band (see its own docstring),
    # never through next_ref(), specifically so it cannot disturb this ordering or any
    # already-committed reference anywhere on the board.
    place_reward_or_gate(
        sch, X_U8, Y_U8, GATE_DY, "RWD_CMD", "RWD_BTN_PULSE", "RWD_DLVR", "+5V", "DGND", refs,
    )
    place_debounce_inverter(
        sch, X_U9, Y_U9, GATE_DY, "RWD_BTN", "RWD_BTN_DEB", "+5V", "DGND", refs,
    )
    place_reward_oneshot(
        sch, X_ONESHOT, Y_ONESHOT, ONESHOT_ROW_DY, "RWD_BTN_DEB", "RWD_BTN_PULSE",
        "+5V", "DGND", X_ONESHOT_R, X_ONESHOT_C,
        ic_ref="U69", r_ref="R191", c_ref="C149", cdec_ref="C150", refs=refs,
    )

    bnc_ref = sch.next_ref("J")
    bnc_pins = sch.place(
        "Connector", "Conn_Coaxial", bnc_ref,
        "Reward driver out (BNC)", X_RWD_BNC, Y_RWD_BNC, footprint=FOOTPRINT_BNC,
    )
    # FINDING M3, 2026-08-16 -- series resistance between the driving gate and the panel.
    # J6 used to sit directly on RWD_DLVR, i.e. a 74HCT32 gate output wired straight onto a
    # panel BNC that runs off-board to a solenoid driver. Every other panel connection on
    # this board carries series resistance -- every input has it plus a BAT54S clamp, every
    # Intan output has it -- and this was one of the six exceptions. An inductive kick from
    # the solenoid side, or a mis-plug onto a live rail, fed straight back into the gate.
    #
    # 100 ohm, not the 47 ohm the camera triggers get: this pin's problem is FAULT CURRENT,
    # not transmission-line matching. 100 ohm is this board's own established panel-series
    # value (the same figure every clamped input uses), and into a solenoid driver's own
    # high-impedance logic input it costs nothing. Refdes minted out of band.
    x, y = pin_pos(X_RWD_BNC, Y_RWD_BNC, bnc_pins["1"])
    sch.label("RWD_DLVR_BNC", x, y)
    x, y = pin_pos(X_RWD_BNC, Y_RWD_BNC, bnc_pins["2"])
    sch.label("DGND", x, y)
    rwd_series_ref = two_pin(
        sch, "Device", "R", "R", "100", GRID(X_RWD_BNC - 20.32), Y_RWD_BNC,
        "RWD_DLVR", "RWD_DLVR_BNC", footprint=FOOTPRINT_R, ref="R203",
    )
    refs["reward_bnc_series_r"] = rwd_series_ref

    refs["reward_btn_hdr"] = btn_ref
    refs["reward_jack_hdr"] = jack_ref
    refs["reward_bnc_hdr"] = bnc_ref
    refs["reward_pullup_r"] = pullup_ref
    refs["reward_debounce_c"] = debounce_c_ref

    for line_idx, line in enumerate([
        "Reward OR polarity (fix round 1, finding 1 -- CRITICAL): RWD_BTN idles HIGH",
        "(10k pull-up to +5V), reads LOW while pressed. A SINGLE 74HCT14 Schmitt",
        "inverter (NOT a pair) inverts ONCE, so RWD_BTN_DEB idles LOW and reads HIGH",
        "while pressed -- active-HIGH. RWD_BTN_DEB now feeds a one-shot (74HCT123D,",
        "~199ms fixed pulse -- panel-instrumentation task, 2026-08-15) whose own Q",
        "output, RWD_BTN_PULSE, is what actually reaches the OR gate -- same active-",
        "HIGH polarity, unchanged requirement. RWD_CMD polarity is ASSUMED ACTIVE-HIGH",
        "(idle-LOW, pulses HIGH on delivery -- this design's other event lines' own",
        "convention, and SN74LVC541APW does not invert): stated explicitly because this",
        "sheet cannot verify the task PC/MonkeyLogic side independently. VERIFY RWD_CMD's",
        "actual polarity against MonkeyLogic before commissioning (MonkeyLogic's own",
        "RewardPolarity setting must be HIGH to match this board -- hardware/README.md):",
        "an OR gate cannot correctly combine one active-HIGH and one active-LOW input.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "MANUAL REWARD ONE-SHOT (panel-instrumentation task, 2026-08-15, spec Sec.3.1):",
        "U69 (Nexperia 74HCT123D) gives every hand-delivered reward a FIXED ~199ms",
        "pulse regardless of how long RWD_BTN is held -- so a manual reward is",
        "countable, not a per-press variable, which is the point of recording",
        "commanded (RWD_CMD) and delivered (RWD_DLVR) separately at all: a manual",
        "reward is DELIVERED WITHOUT COMMANDED, derivable with no extra logic.",
        "",
        "Rext=442k (E96), Cext=1uF: tW = K x Rext[kOhm] x Cext[pF], K=0.45 at VCC=5.0V",
        "(datasheet Table 10, HCT-specific) = 0.45 x 442 x 1e6 = 198.9e6 ns = ~199ms.",
        "Cross-checked against the SAME table's own measured example (Cext=100nF,",
        "Rext=10k -> Typ=450us; formula predicts 450us exactly).",
        "",
        "A=DGND, B=RWD_BTN_DEB (trigger, rising edge), RD-bar=+5V (never reset) --",
        "datasheet Table 4's own function table, row 'nRD=H,nA=L,nB=up-arrow', fires",
        "one pulse on B's own rising edge. Q=RWD_BTN_PULSE (active-HIGH). RC network:",
        "+5V -> R191(442k) -> pin15(REXT/CEXT) -> C149(1uF) -> pin14(CEXT) -- R and C",
        "in series sharing one node, per TI SLVA720A Fig.3-1's own topology, NOT each",
        "independently returned to a rail.",
        "",
        "Retriggerable by design (datasheet Section 1) -- deliberately not worked",
        "around: a single continuous hold produces exactly one rising edge on the",
        "ALREADY-DEBOUNCED RWD_BTN_DEB (bounce filtered upstream), so hold duration",
        "cannot extend the pulse; retriggering only matters for a second physical",
        "press inside the first pulse's own ~199ms window, a different scenario than",
        "'how long the button is held'.",
        "",
        "Second section (unit 2) held in PERMANENT RESET (RD-bar->DGND, datasheet",
        "Table 4 row 1) -- genuinely inert, not merely untriggered. Pin map verified",
        "pin-by-pin against Philips/Nexperia Rev.03 (11 May 2004) Table 3/Fig.5, cross-",
        "checked against KiCad's own 74xx:74HCT123 library entry -- see",
        "place_reward_oneshot()'s own docstring for the full account. Real part:",
        "Nexperia 74HCT123D (TI's own CD74HCT123M/MT is obsolete, checked before",
        "choosing this vendor, not assumed from the symbol's own generic library name).",
    ]):
        sch.text(line, X_NOTE6, Y_NOTE6 + line_idx * NOTE_DY)


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs) -- `refs` maps a role name to the reference designator(s)
    that play it, same convention every generator in this project follows.

    `ref_start` (kicad_sch.py's Sch, added at this task): this is this project's SECOND
    real child sheet, after power.kicad_sch -- the first time two sheets' own
    independently-started reference counters can actually collide (both start "J"/"R"/
    "C"/"D"/"U" at 1). Originally seeded here by reading power.kicad_sch's own
    already-committed text (find_max_refs()) at Task 8's own generation time.

    PINNED, NOT RE-READ LIVE, as of the panel-instrumentation task (2026-08-15) -- see
    constraint 3 (never renumber an existing refdes). power.kicad_sch's own committed
    text has SINCE DIVERGED from what it was at Task 8: the fan-header addition (spec
    Sec.9.5, landing well after this file's own original commit) minted J52-55/C147-148
    out of band on power.kicad_sch, which moves ITS OWN find_max_refs() result for J from
    1 to 55 and for C from 21 to 148 (R/D/U are genuinely unaffected -- the fan headers
    only ever touch J/C/NT/#PWR/F, confirmed empirically: regenerating power.kicad_sch
    fresh reproduces R4/D3/U4 unchanged). Reading POWER_SCH.read_text() live at THIS
    point would recompute ref_start={'J':55,'C':148,...} and renumber every one of this
    sheet's OWN existing J- and C-prefixed components (5 connectors, 10 capacitors) the
    next time this file is regenerated for ANY reason, including a change that touches
    neither prefix -- confirmed empirically before this fix (an unmodified copy of this
    generator, run against the current power.kicad_sch, reproduces R24/D22/U13 exactly
    but J60/C158 instead of the historically-correct J6/C31). {'J':1,'C':21,'R':4,'D':3,
    'U':4} below is power.kicad_sch's own PRE-fan-header per-prefix maximum (J=1 is just
    J1, the M12 inlet; C=21 is Task 7's own original capacitor count, before the fan
    header's own out-of-band C147/C148) -- verified to reproduce this file's own
    committed reference counters {'J':6,'R':24,'D':22,'U':13,'C':31} exactly. Pinned here
    for the identical reason R190/F1/J52-55/C147-148/#PWR9-10 are all minted out of band
    elsewhere on this board: this is the same hazard, just arriving through a stale
    ref_start rather than through next_ref() directly, and pinning it is this task's own
    "pin generator seeds" instruction, applied at the point the hazard actually lives.
    """
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, TASKPC_DIGITAL_SHEETFILE)
    ref_start = {"J": 1, "C": 21, "R": 4, "D": 3, "U": 4}

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    _place_connector0(sch, refs)
    _place_connector1(sch, refs)
    _place_inbound(sch, refs)
    _place_outbound(sch, refs)
    _place_reward_or(sch, refs)
    # LAST, and after every next_ref()-using call above, deliberately: this places only
    # out-of-band refdes (U70-U73/C151-C154), so its position cannot shift any counter --
    # but keeping it last means that stays true even if it ever gains a next_ref() call.
    _place_parallel_legs(sch, refs)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "taskpc-digital.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
