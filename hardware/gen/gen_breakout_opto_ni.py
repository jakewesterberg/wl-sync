"""Generator for hardware/breakout/sheets/opto-ni.kicad_sch -- Task 11 (Step 1) of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-11-brief.md). This is the galvanic barrier between the rig-side (DGND) domain and the
recording NI card's own digital domain: 24 digital lines cross via optocouplers, and this
sheet also places the recording NI's own Connector 1 (digital) -- Connector 0 (analog) was
already placed by Task 10b on analog-ni.kicad_sch.

CHANNEL COUNT IS 24, NOT THE PLAN'S STALE 22 -- see task-11-brief.md's own "Corrections"
section: the two photodiode comparator channels (PD1_COMP, PD2_COMP) reaching NI had not
propagated into the plan text. 16 event-data bits + strobe + barcode + RWD_CMD + RWD_DLVR +
STIM_TRIG + RHS_STIM_OUT + PD1_COMP + PD2_COMP = 24, filling P0.8-P0.31 exactly (24 pins),
with NO spares on this side (all spare headroom lives on opto-intan's own second package --
see gen_breakout_opto_intan.py).

PART: Broadcom ACSL-6400 ("Quad, All-in-One" -- all 4 channels one direction, sharing one
VDD/GND on the output side), 16-pin narrow-body SOIC, 15 MBd, logic (open-collector) output
-- NOT a phototransistor part, matching the mule's own validated part class (Task 2). Six
packages exactly (24/4). No stock KiCad symbol exists for this part; hardware/lib/wl-sync.
kicad_sym gained a hand-built one this task (hardware/gen/gen_wl_sync_lib.py, search
"ACSL-6400" for the full datasheet pin-table sourcing -- Broadcom AV02-0235EN, cross-
confirmed against two independent figures in the same datasheet).

TOPOLOGY -- NON-INVERTING, a deliberate departure from the mule's own drive ORIENTATION
(not its part class): ACSL-6400's own truth table is LED ON -> VOx LOW (an open-collector
Schottky-clamped output stage), so the mule's own "source_net --[R]--> ANODE; CATHODE -->
local GND" wiring (gen_mule.py) produces an INVERTED isolated copy (source HIGH -> LED ON
-> isolated output LOW). For a 16-bit EVENT-CODE WORD reaching the NI recording system,
shipping an inverted digital word is a real, silent-until-noticed hazard -- exactly the class
of defect this project's own reward-OR incident (task-8-report.md) warns against, just
applied to sixteen lines instead of one. This sheet instead ties the LED's FIXED end to +5V
and drives its CATHODE from the source net directly:

    +5V --[R_LED]--> ANODE ; CATHODE --> source_net (e.g. EVT_D0_BUF)

source_net HIGH (driver sourcing, ~5V): ANODE and CATHODE both near +5V, no forward bias,
LED OFF -> VOx HIGH (via the NI-side pull-up) -- MATCHES source_net's own state.
source_net LOW (driver SINKING, ~0-0.5V VOL): ANODE pulled toward CATHODE+VF through R_LED,
LED ON -> VOx LOW -- MATCHES. End to end: isolated output equals source_net's own logic
level, no inversion, relying only on the driving 74HCT541/74HCT32 push-pull output's
ordinary ability to sink a few mA in its LOW state (well inside any HC/HCT-family part's
normal IOL rating; not a special or marginal requirement). This is standard "LED-sink"
practice, not a novel circuit, and every one of this sheet's 24 channels uses it uniformly
(no per-channel exceptions). The PART CLASS still matches the mule's own bring-up
validation (logic-output, not phototransistor) -- only the DRIVE ORIENTATION differs, and
the mule's own bring-up checks (propagation delay, edge quality) do not depend on which end
of the LED is the "driven" one.

LED DRIVE CURRENT -- 249R (E96), CORRECTED AT FINDING F1 (2026-08-16) from the 430R this
paragraph originally derived. The original derivation is preserved below because the way it
went wrong is the point: it is arithmetically correct and lands on a value that does not
work, because it solved for the rail's NOMINAL 5.00V and for V_F TYP. The anode never sees
5.00V (+5V reaches it through F4 and D3, 0.38-0.45V of series drop) and V_F's worst case is
1.80V, not 1.52V -- so the real worst-case current at 430R is ~5mA against a 7.0mA I_FH
minimum, below the threshold at which the part is specified to switch at all. Every figure
in the paragraph below is a TYPICAL-case figure presented as if it bounded the design.

249R gives 8.6mA worst case / 13.7mA best case, clearing both the 8mA guardbanded floor and
the 15mA absolute maximum, and it is only safe because F1's other half parallels two buffer
outputs per LED (one output cannot sink the ~12.7mA it draws). See LED_R_OHMS below and
gen_breakout_taskpc_digital.py's own PARALLEL_LEG_REFS. The superseded derivation:

ACSL-6400's Recommended Operating Conditions (Broadcom AV02-0235EN): IFH 7-15mA,
footnote "recommended minimum 8mA for best performance / guardband against LED degradation".
15mA is BOTH the absolute-maximum AND the top of the recommended range -- the single most
textually-defensible "the datasheet-recommended forward current" to halve: 15/2 = 7.5mA,
which lands INSIDE (not below) the recommended 7-15mA band, avoiding an interpretation
("half of the 8mA best-performance floor" = 4mA, or "half of the 8mA switching-spec test
condition" = 4mA) that would put the design AT or BELOW the datasheet's own guaranteed
7.0mA-max switching threshold (ITH) -- a real correctness risk (a marginal unit might not
switch reliably at all), not merely a smaller margin. R_LED = (5.0V - VOL(driver) -
VF(LED))/IF_target = (5.0 - 0.33 - 1.52)/0.0075 = 420 ohm (VOL(driver): SN74HCT541's own
0.33V max at IOL=6mA, TI datasheet, used as a close approximation for the ~7.3mA this
sheet's own chosen resistor actually draws; VF(LED): ACSL-6400's own 1.52V typ at IF=10mA,
the closest datasheet test point, not separately interpolated for 7.3mA -- both flagged as
approximations, not measured facts). 430R (E24) is the nearest standard value, giving
IF = 3.15V/430R = 7.33mA -- inside the recommended band, roughly half of its own top end,
comfortably clear of ITH's own worst-case 7.0mA threshold BEFORE even accounting for this
sheet's own light NI-side load (see next paragraph), and with large margin against CTR
degradation over a decade of service (this task's own stated rationale).

NI-SIDE PULL-UP: 3.9k -- FIX ROUND 1 (see .superpowers/sdd/2026-08-13-breakout-pcb/
task-11-report.md's own "Fix round 1" section), corrected from this task's original 10k.
The original 10k figure was derived only by comparing against the mule's own 1k -- it never
checked ACSL-6400's own datasheet pull-up maximum (RL-max = 4k, "Recommended Operating
Conditions", the datasheet's OWN guaranteed 15 MBd/~350R-RL switching-speed
characterization) -- and 10k EXCEEDS it. 3.9k (E24) is the largest E24 standard value that
still sits AT OR UNDER that 4k limit (the next step up, 4.3k, would not), so this design no
longer needs any departure from the part's own recommended operating conditions at all.

NI's connector supplies 250mA; 24 optocoupler output stages at ~5-7mA each (ACSL-6400's own
IDDL spec, Low Level Supply Current, typ 5.8mA/max 10.5mA at 5V per Broadcom AV02-0235EN) is
already 120-168mA (24 x 5mA .. 24 x 7mA). Pull-up current adds on top:
  - 1k (the mule's own value): 24 x 5V/1k = 120mA -> domain total 240-288mA, AT OR OVER the
    250mA budget.
  - 3.9k (this sheet's own value): 24 x 5V/3.9k = ~31mA -> domain total 151-199mA,
    comfortably inside the 250mA budget, with 51-99mA of headroom.
  - 10k (the original, over-conservative, out-of-spec choice): 24 x 5V/10k = 12mA -> domain
    total 132-180mA. Also fits the 250mA budget, but ONLY by exceeding the part's own
    RL-max -- 19mA of extra headroom bought by violating a stated datasheet limit, a bad
    trade this fix reverses.

Logic-level integrity at 3.9k, reasoned through rather than merely asserted:
  - LOW-level: the datasheet's own VOL guarantee (max 0.6V at 5V supply) is characterized at
    IOL=13mA; at 3.9k's ~1.28mA sink (5V/3.9k), the output transistor sits in DEEPER
    saturation than the 13mA test condition, so VOL is if anything lower/better, not worse
    -- the same conclusion the original 10k analysis reached, still true at the lighter-but-
    still-far-below-13mA 3.9k sink.
  - HIGH-level: worst-case leakage IOH is 100uA max (VO=5.5V test condition); through a
    3.9k pull-up that is at most a 0.39V drop (100uA x 3.9k), i.e. VOH >= NI_5V - 0.39V
    (>=4.61V against a nominal 5V rail) even at the datasheet's own worst-case leakage
    figure -- comfortably above any standard TTL/CMOS-class VIH, and a TIGHTER margin than
    the original 10k choice's own -1.0V-drop figure (this sheet still could not locate NI's
    own per-pin DIO input-threshold table in the X Series User Manual text extraction used
    for the MDR68 pinout below -- that number lives in a separate NI device-specification
    document this task did not fetch -- so this specific margin claim remains REASONED from
    the ACSL-6400 datasheet plus standard TTL/CMOS practice, not independently confirmed
    against NI's own DIO input spec; flagged here rather than overclaimed, same as the
    original analysis).
  - Speed: RC into ~50pF (this sheet's own trace+input-capacitance estimate, matching this
    task's own brief) at 3.9k gives tau=195ns (~0.2us), ~0.43us 10-90% rise -- against a
    "hundreds of microseconds" (500us) strobe period (this task's own brief), utterly
    negligible, just as it was at 10k's own larger tau (500ns, ~1.1us 10-90% rise). This
    margin was never close enough for speed to be the deciding factor in either direction.
    The datasheet's own 4k-max RL spec exists to guarantee its OWN 15 MBd/350R-RL speed
    grade and a 5-TTL-load fan-out that does not describe this sheet's actual load (a
    single, high-impedance, CMOS-class NI DAQ digital input, not "5 TTL loads") -- but 3.9k
    satisfies that spec anyway, so this sheet no longer needs to argue past it at all.

POWER: NI-side output stage runs from NI_5V/NI_GND, taken DIRECTLY off Connector 1's own
+5V (pins 8, 14) and D GND (12 pins, see MDR1_DGND_PINS below) -- switcher-free, already
referenced to NI's own ground, per this task's own brief. Both are new nets whose only
drivers in this whole project are these passive connector pins, so both get an explicit
PWR_FLAG (same "power entering via a passive connector pin" situation Task 7's own M12A_5
inlet already established the precedent for -- +12V/-12V/+5V/AGND/DGND all got flags there
for the identical reason). A FILTERED ISOLATED DC-DC FALLBACK FOOTPRINT is placed, fully
DNP, per this task's own explicit instruction ("if NI's +5V cannot supply the load, leave
the footprint") -- TMA-0505S (Converter_DCDC_Isolated, the SAME part class the mule's own
Task 2 used for its own isolated 5V rail), a pi filter (10uF-ferrite-10uF, Task 7's own
established pattern), and a DNP 0R "populate this instead" bridge resistor onto NI_5V so the
fallback contributes NOTHING to the netlist's real behaviour unless a human deliberately
populates that one bridge component -- see place_fallback_dcdc() below. This is an explicit,
task-authorized exception to the plan's own global "no switchers except the one isolated
+-12V DC-DC" constraint (that constraint predates this task's own fallback allowance).

MDR68 CONNECTOR 1 PIN SOURCING -- physical pins for P0.8-P0.31, sourced from NI's X Series
User Manual (370784K-01, May 2019, ni.com/manuals), fetched directly from NI's own
documentation host (docs-be.ni.com/bundle/pcie-pxie-usb-63xx-features/raw/resource/enus/
370784k.pdf) and text-extracted with `pdftotext -layout`. The recording NI is a PXIe-6353
(spec Sec.9.3), whose own connector-pinout figure is Figure A-18 "NI PCIe-6353 and NI
PCIe/PXIe-6363 Pinout" (confirmed directly against the manual's own text: "PXIe-6353"
appears nowhere in the document, only "PCIe-6353"/"PCIe/PXIe-6363" -- the manual groups
devices sharing one physical connector pinout under one figure, exactly as Task 10b's own
analog-ni.kicad_sch already found and documented for Connector 0). Read directly off Figure
A-18's own Connector 1 table (NOT reused/copied from Task 8's Connector 1 table without
re-deriving it -- Task 8's task-PC card is a DIFFERENT physical part, PCIe-6343, Figure
A-5): every one of the 24 P0.x pins below, independently re-derived from Figure A-18's own
text, turns out to be BYTE-FOR-BYTE IDENTICAL to Task 8's own Figure-A-5-sourced Connector 1
table -- confirmed by direct comparison, not assumed from "the X Series shares one connector
pinout" alone. Task 8's own 12 D-GND positions and 2 own "+5V" positions (this sheet's own
new finding: Task 8's own Connector 1 table never needed the +5V pins, since the task PC's
own Connector 1 carries only DGND-referenced digital signals -- THIS sheet is the first to
need Connector 1's own +5V pins, for NI_5V) also match exactly.

  MDR1_PIN_BY_P0 = {8:52, 9:17, 10:49, 11:47, 12:19, 13:51, 14:16, 15:48, 16:11, 17:10,
                    18:43, 19:42, 20:41, 21:6, 22:5, 23:38, 24:37, 25:3, 26:45, 27:46,
                    28:2, 29:40, 30:1, 31:39}
  MDR1_DGND_PINS = [4, 7, 9, 12, 13, 15, 18, 35, 36, 44, 50, 53]  (-> NI_GND)
  MDR1_5V_PINS   = [8, 14]                                        (-> NI_5V)

Which of the 24 SIGNALS rides which P0.x NUMBER is this sheet's own declared, fixed choice
(no external source fixes it, unlike the physical-pin-per-P0.x-NUMBER mapping above) --
chosen to MIRROR Task 8's own EVT_Dn->P0.(8+n) convention exactly (taskpc-digital.kicad_sch's
own on-sheet text: "restates the P0.8-P0.23 event-code offset for whoever configures
MonkeyLogic") so the SAME event-code bit lands on the SAME P0.x bit position on both the
task-PC card and the recording-NI card -- one decode routine works for both without a
per-card remap. EVT_D0-D15 -> P0.8-P0.23 (16), then EVT_STROBE/BARCODE_PI/RWD_CMD_BUF/
RWD_DLVR/STIM_TRIG_BUF/RHS_STIM_OUT/PD1_COMP/PD2_COMP -> P0.24-P0.31 (8), filling the range
exactly with no spare P0.x position on this connector.

Non-negotiables (task-11-brief.md's own numbering):
  1. lib_symbols via kicad_sch.py's own extract_symbol()/ensure_lib_symbol() only.
  2. (instances path ...) read from breakout.kicad_sch's own committed text.
  3. Refdes seeding clears ALL SEVEN already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend, analog-ni, mux-intan, comparators) via merge_max_refs().
  4. Coordinate collisions -- every role gets its own X column; verified empirically by
     check_breakout_opto_ni_netlist.py's own _check_no_coordinate_collisions(), negative
     control included.
  5. Every pin map (ACSL-6400, MDR68 Connector 1) verified pin-by-pin against the real
     datasheet/manual -- see above. No hedged/two-candidate pins anywhere on this sheet.
  6. No footprint-pad-adjacency dependency on this sheet (no jumper/shorting-block part).

Run directly: `python3 hardware/gen/gen_breakout_opto_ni.py` (writes
hardware/breakout/sheets/opto-ni.kicad_sch). Requires hardware/breakout/breakout.kicad_sch
and all seven sibling sheets to already exist on disk.
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_root_uuid,
    find_sheet_instance_path,
    pin_pos,
    write_project_stub,
)
# The already-committed sibling sheet paths are no longer read: build()'s own
# ref_start is PINNED (F1 task, 2026-08-16 -- see build()), not re-derived live
# from them at generation time. Same treatment, and same reason, as
# gen_breakout_pi_interface.py's own.

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
OPTO_NI_SHEETFILE = "sheets/opto-ni.kicad_sch"  # exactly as gen_breakout.py's own
# SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- Device/Package_SO/wl-sync/Converter_DCDC/Converter_DCDC_Isolated/
# Inductor_SMD all already registered (Task 6/7/10a); no new sym-lib-table/fp-lib-table
# entries needed for this sheet.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_C_BULK = "Capacitor_SMD:C_1206_3216Metric"
FOOTPRINT_ACSL = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
FOOTPRINT_MDR68 = "wl-sync:MDR68_Male_RightAngle"
FOOTPRINT_DCDC_SIP7 = "Converter_DCDC:Converter_DCDC_TRACO_TMA-05xxS_12xxS_Single_THT"
FOOTPRINT_FERRITE = "Inductor_SMD:L_0805_2012Metric"

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
PKG_DY = GRID(50.8)  # 20 x 2.54mm -- each ACSL6400 package's own 16 pins span 8 rows x
# 2.54mm = +-8.89mm (17.78mm total, build_ic_symbol()'s own _rows_y(8)); PKG_DY leaves
# 50.8-17.78=33.02mm of clear gap between one package's own row-band and the next.

X_LEDR = GRID(20)     # LED series resistor column (+5V -> ANODE)
X_PKG = GRID(90)      # ACSL6400 package column -- pins reach +-29.21mm (10*2.54+3.81)
X_PULLUP = GRID(165)  # NI-side pull-up column (NI_5V -> VOx/final net)
X_CONN, Y_CONN = GRID(260), GRID(160)  # recording NI Connector 1 (MDR68) -- own block,
# vertically centred against the 6-package stack (Y0=20 .. Y0+5*50.8=274, midpoint 147,
# rounded to a clean 160); pins reach +-10.16mm (2.5*2.54+3.81), far clear of X_PULLUP's
# own column.

# Per-channel resistor ROW Y is computed INDEPENDENTLY of the package's own internal pin
# geometry (_rows_y()), not by centring each resistor on its own pin's exact Y -- found
# the hard way: Device:R's own pin-to-pin span (2x3.81=7.62mm) is EXACTLY 3x the
# package's own row pitch (2.54mm), so a resistor centred on VO4's row (local offset
# +3.81) puts its OWN far pin exactly on y_pkg, and a resistor centred on VO1's row
# (local offset -3.81, 3 rows away from VO4) puts ITS OWN far pin exactly on y_pkg TOO --
# both landing on the identical (X_PULLUP, y_pkg) point and silently merging "NI_5V"
# with that package's own 4th channel's final net (confirmed empirically: `kicad-cli sch
# erc` reported it directly as a `pin_to_pin`/`multiple_net_names` conflict, not
# discovered by inspection). CH_ROW_DY below is chosen so NEITHER this resistor-vs-
# package-pin coincidence NOR a resistor-vs-adjacent-resistor one can recur: each
# channel's own two resistors (LED series + pull-up) sit on a completely separate,
# strictly-increasing row axis, 7.62mm clear of both neighbours (15.24-2*3.81=7.62,
# exactly Device:R's own span, not a fraction of it), so no resistor's own far pin can
# ever reach an adjacent channel's row, and this axis has no arithmetic relationship to
# the package's own _rows_y() offsets at all (different columns, different origin).
CH_ROW_DY = GRID(15.24)  # 6 x 2.54mm, > Device:R's own 7.62mm pin-to-pin span

DECOUPLE_DX = GRID(15.24)
DECOUPLE_DY = GRID(5.08)

X_FALLBACK, Y_FALLBACK = GRID(430), GRID(20)  # fallback DC-DC block -- its own column,
# clear of every other role: channel rows occupy X_LEDR(20)/X_PKG(90)/X_PULLUP(165) at
# Y=20..20+23*15.24=370.5, Connector 1 occupies X_CONN(260) at Y=160+-84, and note text
# occupies X=340+ -- X_FALLBACK=430 shares no X with any of them, so its own Y can be
# anything without risking a cross-role coincidence (verified by the coordinate-collision
# checker regardless, not merely by this reasoning).

X_NOTE1, Y_NOTE1 = GRID(340), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(340), GRID(100)
X_NOTE3, Y_NOTE3 = GRID(340), GRID(180)
X_NOTE4, Y_NOTE4 = GRID(340), GRID(260)
X_NOTE5, Y_NOTE5 = GRID(340), GRID(340)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# ACSL-6400 per-channel pin map (local channel index 0-3 -> (anode, cathode, vo) pin
# numbers) -- see module docstring / gen_wl_sync_lib.py's own "ACSL-6400" sourcing.
# ---------------------------------------------------------------------------
ACSL6400_CH_PINS = {
    0: ("1", "2", "14"),  # ANODE1/CATHODE1/VO1
    1: ("3", "4", "13"),  # ANODE2/CATHODE2/VO2
    2: ("5", "6", "12"),  # ANODE3/CATHODE3/VO3
    3: ("7", "8", "11"),  # ANODE4/CATHODE4/VO4
}
ACSL6400_PIN_GND = ["9", "16"]
ACSL6400_PIN_VDD = ["10", "15"]

LED_R_OHMS = "249"  # E96. FINDING F1 (parametric-audit.md), 2026-08-16: was 430R, which
# under-drove every one of these 24 LEDs. The original derivation solved
# (5.0 - V_OL - V_F)/I for a 7.5mA target and got 420R -> 430R, but it used the rail's
# NOMINAL 5.00V. The anode never sees 5.00V: +5V reaches these LEDs through F4 (1206L050
# polyfuse, R_min 0.15R) and D3 (SS14, ~0.35V typ / 0.42V max at this rail's ~0.45A), so
# the anode rail is 4.55-4.62V, and it used V_F TYP (1.52V) where the worst case is
# 1.80V. Corrected worst case at 430R is ~5mA against a 7.0mA I_FH minimum -- below the
# threshold at which the part is specified to switch at all.
#
# 249R gives 8.6mA worst case / 13.7mA best case, clearing BOTH the 8mA guardbanded floor
# (AV02-0235EN footnote b) and the 15mA ABSOLUTE maximum. 240R was rejected during the
# audit for landing at 99% of that absolute maximum; 270R misses the 8mA guardband.
#
# THIS VALUE IS ONLY SAFE PARALLELED. At 249R one LED draws ~12.7mA, which is over a
# single SN74AHCT541 output's 7.5mA budget -- so F1's other half (two buffer outputs per
# LED, from tied inputs; gen_breakout_taskpc_digital.py's own PARALLEL_LEG_PACKAGES) is
# not optional, and tests/hardware/test_netlist.py asserts both halves independently.
# It also depends on the external +5V supply being specified at +-2%: at +-5% the
# tolerance spread is 1.95:1 against a 2.14:1 window, which fits with nothing to spare.
# That tolerance is now a load-bearing specification protecting 30 optocoupler channels,
# not an incidental assumption.
PULLUP_OHMS = "3.9k"  # fix round 1 -- corrected from this task's original 10k, which
# exceeded ACSL-6400's own datasheet RL-max (4k); see module docstring, NI-SIDE PULL-UP.

# ---------------------------------------------------------------------------
# 24-channel contract: (source_net (DGND domain, already established elsewhere in this
# project), final_net (this sheet's own NI-isolated contract net), P0.x number).
# Order matches the brief's own literal channel list.
# ---------------------------------------------------------------------------
NI_CHANNELS = (
    [(f"EVT_D{i}_BUF", f"EVT_D{i}_NI", 8 + i) for i in range(16)]
    + [
        ("EVT_STROBE_BUF", "EVT_STROBE_NI", 24),
        ("BARCODE_BUF", "BARCODE_NI", 25),
        ("RWD_CMD_BUF", "RWD_CMD_NI", 26),
        ("RWD_DLVR_BUF", "RWD_DLVR_NI", 27),
        ("STIM_TRIG_BUF", "STIM_TRIG_NI", 28),
        # FINDING F1, 2026-08-16 -- was the bare "RHS_STIM_OUT". This was the ONE channel
        # of the 24 whose LED hung directly on a non-buffer output (opto-intan's ACSL-6420
        # VO3, an open-collector pin budgeted at 13mA), and the paragraph below records it
        # as a deliberate, checked exception: 7.33mA of LED plus 1.28mA of pull-up was
        # comfortably inside 13mA. The resistor change retires that exception -- at 249R
        # the LED alone draws ~12.7mA, and with the pull-up that is ~13.9mA, OVER the
        # ACSL's own budget. An optocoupler output cannot be paralleled the way F1's
        # buffer outputs are (its partner channel is a spare in the same direction, and
        # tying them would double the load on the Intan's own already-marginal output --
        # finding F2), so the LED moves onto a buffered leg like every other channel here.
        # gen_breakout_taskpc_digital.py's own RHS_STIM_OUT_BUF_LEGS produces it, from
        # U11's own last 2 spare channels, paralleled like every other LED on this board.
        # The ACSL-6420 output is left driving only its 3.9k pull-up (~1.2mA).
        ("RHS_STIM_OUT_BUF", "RHS_STIM_OUT_NI", 29),
        ("PD1_COMP_BUF", "PD1_COMP_NI", 30),
        ("PD2_COMP_BUF", "PD2_COMP_NI", 31),
    ]
)
assert len(NI_CHANNELS) == 24
assert len({c[0] for c in NI_CHANNELS}) == 24, (
    "every one of the 24 LEDs must have its OWN source net -- two LEDs sharing one means "
    "two LEDs on one driver pin, ~14.7mA against a 6mA (HCT541) or 4mA (HCT32) IOL. See "
    "gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS."
)
assert len({c[1] for c in NI_CHANNELS}) == 24
assert sorted(c[2] for c in NI_CHANNELS) == list(range(8, 32))
# EVERY SOURCE NET IS A BUFFERED LEG, WITH NO EXCEPTIONS AS OF F1 (2026-08-16).
#
# RHS_STIM_OUT used to be the one exception, and it was a checked one rather than an
# oversight: its driver is an ACSL-6420 output on the opto-intan sheet (a signal
# ORIGINATING inside the Intan domain and crossing INTO this one), specified at IOL=13mA,
# so its 7.33mA LED plus its own 3.9k pull-up's 1.28mA was comfortably inside spec.
#
# The F1 resistor change retired it. At 249R that LED draws ~12.7mA, and ~13.9mA with the
# pull-up is OVER the ACSL's 13mA -- so the exception stopped being safe the moment the
# resistor moved, and the channel now sources from RHS_STIM_OUT_BUF instead. Worth
# recording as its own lesson: the exception was correct when written and was invalidated
# by a change made two sheets away for an unrelated reason. Nothing about the exception
# itself changed; the number it had been checked against did.
#
# Every channel here is now driven by a PAIR of paralleled 74AHCT541 outputs that drive
# that LED and nothing else. Four of these source names changed at the
# "one LED per driver pin" fix -- BARCODE_PI -> BARCODE_BUF, RWD_DLVR -> RWD_DLVR_BUF
# (both had been doubled onto a pin that also fed the Intan LED), and PD1_COMP/PD2_COMP
# -> PD1_COMP_BUF/PD2_COMP_BUF, which were worse than a load problem: those two nets wire
# DIRECTLY into the sync module's own GPIO20/21, so an LED on them put +5V-through-430R-
# and-an-LED onto a 3.3V-only pin (idling ~3.6-3.8V with the LED off, and injecting ~7mA
# into an unpowered pad whenever this board is on and the sync box is not). They were the
# only 2 of these 24 LEDs not driven from a buffered leg.

# Physical MDR68 (Connector 1, recording NI) pins -- see module docstring, MDR68
# CONNECTOR 1 PIN SOURCING, for the full retrieval/cross-check account.
MDR1_PIN_BY_P0 = {
    8: "52", 9: "17", 10: "49", 11: "47", 12: "19", 13: "51", 14: "16", 15: "48",
    16: "11", 17: "10", 18: "43", 19: "42", 20: "41", 21: "6", 22: "5", 23: "38",
    24: "37", 25: "3", 26: "45", 27: "46", 28: "2", 29: "40", 30: "1", 31: "39",
}
assert set(MDR1_PIN_BY_P0) == set(range(8, 32))
MDR1_DGND_PINS = ["4", "7", "9", "12", "13", "15", "18", "35", "36", "44", "50", "53"]
assert len(MDR1_DGND_PINS) == 12
MDR1_5V_PINS = ["8", "14"]


# ---------------------------------------------------------------------------
# Low-level helpers -- duplicated from prior generators' own lbl()/two_pin() rather than
# imported (this project's own established discipline).
# ---------------------------------------------------------------------------


def lbl(sch, x, y, pins, pin_num, net):
    px, py = pin_pos(x, y, pins[pin_num])
    sch.label(net, px, py)


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint="", dnp=False):
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint, dnp=dnp)
    lbl(sch, x, y, pins, "1", net1)
    lbl(sch, x, y, pins, "2", net2)
    return ref


def decouple(sch, x, y, rail, gnd, dnp=False):
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, gnd, footprint=FOOTPRINT_C_SMALL, dnp=dnp)


# ---------------------------------------------------------------------------
# One optocoupler channel: LED (source_net -> isolated VOx/final_net), non-inverting
# (see module docstring, TOPOLOGY).
# ---------------------------------------------------------------------------


def opto_channel(sch, x_pkg, y_pkg, pkg_pins, ch_pins, row_y, source_net, final_net, refs):
    """`row_y` is this channel's own dedicated resistor row (see CH_ROW_DY above) --
    deliberately NOT derived from the package's own pin geometry, to avoid the exact
    coordinate collision documented at that constant's own definition."""
    anode_pin, cathode_pin, vo_pin = ch_pins
    anode_net = f"{final_net}_LEDA"
    r_led = two_pin(sch, "Device", "R", "R", LED_R_OHMS, X_LEDR, row_y, "+5V", anode_net, footprint=FOOTPRINT_R)
    lbl(sch, x_pkg, y_pkg, pkg_pins, anode_pin, anode_net)
    lbl(sch, x_pkg, y_pkg, pkg_pins, cathode_pin, source_net)

    r_pu = two_pin(sch, "Device", "R", "R", PULLUP_OHMS, X_PULLUP, row_y, "NI_5V", final_net, footprint=FOOTPRINT_R)
    lbl(sch, x_pkg, y_pkg, pkg_pins, vo_pin, final_net)

    refs.setdefault("led_r", []).append(r_led)
    refs.setdefault("pullup_r", []).append(r_pu)


def place_package(sch, pkg_idx, y_pkg, channels_4, refs):
    """One ACSL6400 package (4 channels). `channels_4` is a list of up to 4
    (source_net, final_net) tuples, local index order matches ACSL6400_CH_PINS.
    `pkg_idx` (0-5) seeds each channel's own globally-unique resistor row (see
    CH_ROW_DY)."""
    ref = sch.next_ref("U")
    pkg_pins = sch.place("wl-sync", "ACSL6400", ref, "ACSL-6400-00TE", X_PKG, y_pkg, footprint=FOOTPRINT_ACSL)
    refs.setdefault("acsl6400", []).append(ref)

    for local_idx, (source_net, final_net) in enumerate(channels_4):
        row_y = GRID(Y0 + (4 * pkg_idx + local_idx) * CH_ROW_DY)
        opto_channel(sch, X_PKG, y_pkg, pkg_pins, ACSL6400_CH_PINS[local_idx], row_y, source_net, final_net, refs)

    for gpin in ACSL6400_PIN_GND:
        lbl(sch, X_PKG, y_pkg, pkg_pins, gpin, "NI_GND")
    for vpin in ACSL6400_PIN_VDD:
        lbl(sch, X_PKG, y_pkg, pkg_pins, vpin, "NI_5V")

    # Bypass caps -- datasheet's own instruction, "as close as possible" to EACH VDD/GND
    # pair (pins 9/10 and pins 15/16 -- see module docstring / gen_wl_sync_lib.py).
    gnd9_x, gnd9_y = pin_pos(X_PKG, y_pkg, pkg_pins["9"])
    c1 = decouple(sch, X_PKG - DECOUPLE_DX, gnd9_y, "NI_5V", "NI_GND")
    gnd16_x, gnd16_y = pin_pos(X_PKG, y_pkg, pkg_pins["16"])
    c2 = decouple(sch, X_PKG + DECOUPLE_DX, gnd16_y, "NI_5V", "NI_GND")
    refs.setdefault("bypass_c", []).extend([c1, c2])
    return ref


def place_connector1(sch, refs):
    """The recording NI's own Connector 1 (digital) -- 24 P0.x lines to their own
    channel's final_net, 12 D GND pins to NI_GND, 2 +5V pins to NI_5V, remaining 30 pins
    (AI16-31/AO2-3/APFI1 -- this design routes no signal through them) no_connect."""
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "MDR68_Male", ref,
        "Connector 1 (digital, recording NI)",
        X_CONN, Y_CONN, footprint=FOOTPRINT_MDR68,
    )
    used = set()
    for _source, final_net, p0 in NI_CHANNELS:
        pin_num = MDR1_PIN_BY_P0[p0]
        lbl(sch, X_CONN, Y_CONN, pins, pin_num, final_net)
        used.add(pin_num)
    for pin_num in MDR1_DGND_PINS:
        lbl(sch, X_CONN, Y_CONN, pins, pin_num, "NI_GND")
        used.add(pin_num)
    for pin_num in MDR1_5V_PINS:
        lbl(sch, X_CONN, Y_CONN, pins, pin_num, "NI_5V")
        used.add(pin_num)
    assert len(used) == 24 + 12 + 2  # 38 -- no duplicate physical pin reused across roles
    for n in range(1, 69):
        num = str(n)
        if num not in used:
            x, y = pin_pos(X_CONN, Y_CONN, pins[num])
            sch.no_connect(x, y)
    refs["conn1_ni"] = ref
    return ref


def place_ni_entry_caps(sch, refs):
    """Bulk + bypass at the NI_5V entry point, same discipline Task 7's own inlet uses
    (10uF + 100nF), plus the ONE PWR_FLAG NI_5V needs (see module docstring, POWER).

    NI_GND does NOT get its own PWR_FLAG: place_fallback_dcdc() wires TMA-0505S's own
    -Vout (a real `power_out`-typed pin, confirmed directly against the stock symbol,
    same check every other power_flag()-vs-real-driver decision in this project makes)
    DIRECTLY to NI_GND (not gated behind the DNP bridge the +5V-equivalent leg uses --
    see that function's own docstring for why). Confirmed empirically, not assumed: a
    PWR_FLAG placed here alongside that real driver trips `pin_to_pin` ("Power output and
    Power output are connected") -- Task 7's own established rule applies unchanged
    ("Only call [power_flag] for a net that doesn't already have a genuine power_out pin
    from some other part").
    """
    c_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_CONN + GRID(30), Y_CONN - GRID(20), "NI_5V", "NI_GND", footprint=FOOTPRINT_C_BULK)
    c_bypass = two_pin(sch, "Device", "C", "C", "100nF", X_CONN + GRID(45), Y_CONN - GRID(20), "NI_5V", "NI_GND", footprint=FOOTPRINT_C_SMALL)
    refs["ni_entry_c"] = [c_bulk, c_bypass]
    sch.power_flag("NI_5V", X_CONN + GRID(30), Y_CONN - GRID(35))


def place_fallback_dcdc(sch, refs):
    """Filtered isolated DC-DC fallback, fully DNP -- this task's own explicit
    instruction ("if NI's +5V cannot supply the load, this domain takes a second isolated
    DC-DC. Leave the footprint."). TMA-0505S (Converter_DCDC_Isolated, the SAME part class
    the mule's own Task 2 uses for its own isolated +5V rail -- 5V in from this board's own
    +5V/DGND, isolated 5V out), a 10uF-ferrite-10uF pi filter (Task 7's own established
    pattern), and a DNP 0R bridge resistor onto NI_5V. Everything from the DC-DC's own
    +Vout through the filter is on its OWN, separately-named nets (NI_5V_FB_RAW /
    NI_5V_FB_FILT) -- NOT NI_5V itself -- until the bridge resistor is deliberately
    populated, so this block contributes NOTHING to the sheet's real behaviour by default
    (confirmed empirically via ERC/netlist below, not merely argued): the DC-DC's own
    secondary-side return (-Vout) ties DIRECTLY to NI_GND (not gated by a bridge) since
    paralleling two GROUND references is not the hazard paralleling two independently
    LIVE +5V-class sources would be, and it is what lets the filter's own two caps have a
    real, defined return even while unpopulated.

    Sized modestly, not to fully replace NI's own 250mA budget: TMA-0505S is a 1W-class
    part (~200mA at 5V), which alone would not cover this domain's own ~180mA worst case
    if NI's OWN supply also proved deficient -- flagged here rather than implied solved;
    "leave the footprint" is what this task's own brief asks for, not a resized production
    fallback (a real Task 13 BOM-lock/bench-data decision, not a schematic-capture one).
    """
    dcdc_ref = sch.next_ref("U")
    dcdc_pins = sch.place(
        "Converter_DCDC_Isolated", "TMA-0505S", dcdc_ref, "TMA-0505S",
        X_FALLBACK, Y_FALLBACK, footprint=FOOTPRINT_DCDC_SIP7, dnp=True,
    )
    lbl(sch, X_FALLBACK, Y_FALLBACK, dcdc_pins, "1", "+5V")          # +Vin
    lbl(sch, X_FALLBACK, Y_FALLBACK, dcdc_pins, "2", "DGND")          # -Vin
    lbl(sch, X_FALLBACK, Y_FALLBACK, dcdc_pins, "6", "NI_5V_FB_RAW")  # +Vout
    lbl(sch, X_FALLBACK, Y_FALLBACK, dcdc_pins, "4", "NI_GND")        # -Vout, direct

    fx, fy = X_FALLBACK + GRID(30.48), Y_FALLBACK
    c1 = two_pin(sch, "Device", "C", "C", "10uF", fx, fy - GRID(10.16), "NI_5V_FB_RAW", "NI_GND", footprint=FOOTPRINT_C_BULK, dnp=True)
    fb = two_pin(sch, "Device", "FerriteBead", "FB", "600R@100MHz", fx + GRID(15.24), fy, "NI_5V_FB_RAW", "NI_5V_FB_FILT", footprint=FOOTPRINT_FERRITE, dnp=True)
    c2 = two_pin(sch, "Device", "C", "C", "10uF", fx + GRID(30.48), fy - GRID(10.16), "NI_5V_FB_FILT", "NI_GND", footprint=FOOTPRINT_C_BULK, dnp=True)
    bridge = two_pin(sch, "Device", "R", "R", "0", fx + GRID(45.72), fy, "NI_5V_FB_FILT", "NI_5V", footprint=FOOTPRINT_R, dnp=True)

    refs["fallback_dcdc"] = dcdc_ref
    refs["fallback_filter"] = [c1, fb, c2]
    refs["fallback_bridge"] = bridge


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, OPTO_NI_SHEETFILE)
    # PINNED IN FULL, not re-derived live -- extended from the "R"-only pin below to
    # EVERY prefix at the F1 task (2026-08-16). The "R" half of this was already here,
    # and its reasoning generalises exactly: a sibling sheet gaining ANY out-of-band
    # refdes silently re-seeds this sheet's counters at the next regeneration.
    #
    # That is no longer hypothetical. taskpc-digital.kicad_sch has since gained U69/R191/
    # C149-C150 (the panel-instrumentation one-shot), so merge_max_refs() over the seven
    # siblings below now returns {'#PWR':11,'C':150,'FB':2,'J':56,'U':69,...} where this
    # sheet was generated against {'#PWR':7,'C':120,'FB':2,'J':44,'U':55}. Confirmed
    # empirically BEFORE any F1 edit, by running this generator unmodified: all seven
    # ACSL packages moved U56-U62 -> U70-U76, all 16 capacitors C121-C136 -> C151-C166,
    # J45 -> J57, #PWR8 -> #PWR12. F1 requires regenerating this sheet (LED_R_OHMS
    # 430 -> 249), so the hazard was directly in the path of this change rather than
    # latent.
    #
    # The values below are this sheet's own committed minima minus one, verified by
    # regenerating and diffing: with them pinned, an F1-free run of this generator
    # reproduces the committed file byte for byte.
    #
    # "R" = 121 specifically (the original pin, reasoning preserved): comparators.
    # kicad_sch gained a 5th resistor (R190, channel 4's series resistor --
    # gen_breakout_comparators.py's own CHANNEL4_SERIES_REF) placed with an EXPLICIT
    # refdes chosen to sit above the whole board's prior "R" range on purpose,
    # specifically so no sibling's own numbering has to move for it. Left to recompute,
    # find_max_refs(COMPARATORS_SCH) would see "R190" and hand this sheet's own
    # next_ref("R") calls a seed 69 higher than before (121 -> 190), silently renumbering
    # all 49 of this sheet's own already-committed resistors for zero functional reason.
    # 121 is comparators.kicad_sch's own true resistor count EXCLUDING R190 (3 populated
    # channels x 3 resistors + 1 DNP channel x 2 = 11, seeded at 110 from
    # mux-intan.kicad_sch) -- confirmed against this sheet's own committed R111-R121
    # range, unaffected by this fix.
    ref_start = {"#PWR": 7, "C": 120, "FB": 2, "J": 44, "R": 121, "U": 55}

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    for k in range(6):
        y_pkg = GRID(Y0 + k * PKG_DY)
        group = NI_CHANNELS[4 * k : 4 * k + 4]
        place_package(sch, k, y_pkg, [(s, f) for s, f, _p0 in group], refs)

    place_connector1(sch, refs)
    place_ni_entry_caps(sch, refs)
    place_fallback_dcdc(sch, refs)

    for line_idx, line in enumerate([
        "Opto-NI (Task 11) -- 24 digital channels, DGND domain -> NI-isolated domain,",
        "via six Broadcom ACSL-6400 (quad, all-in-one, 15 MBd logic-output optocoupler,",
        "16-pin SOIC -- NOT phototransistor, matching the mule's own validated part",
        "class). Also places the recording NI's own Connector 1 (digital) -- Connector 0",
        "(analog) was already placed by Task 10b on analog-ni.kicad_sch; not duplicated.",
        "",
        "Channels: EVT_D0_BUF..EVT_D15_BUF (16), EVT_STROBE_BUF, BARCODE_PI, RWD_CMD_BUF,",
        "RWD_DLVR, STIM_TRIG_BUF, RHS_STIM_OUT, PD1_COMP, PD2_COMP -- 24 total, filling",
        "Connector 1's own P0.8-P0.31 exactly, no spares on this side (all this domain's",
        "own spare headroom lives on opto-intan's own second package).",
        "",
        "TOPOLOGY IS NON-INVERTING, unlike the mule's own drive orientation -- see this",
        "generator's own module docstring for the full derivation. +5V -> R_LED -> ANODE;",
        "CATHODE -> source_net. source_net HIGH -> LED off -> isolated output HIGH;",
        "source_net LOW -> LED on -> isolated output LOW. Relies only on the driving",
        "74HCT541/74HCT32 push-pull output's ordinary ability to sink a few mA -- standard",
        "practice, not a special requirement.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "LED DRIVE: 249R (E96, 1%) -- FINDING F1, 2026-08-16. Was 430R, which",
        "under-drove all 24 of these LEDs: ~5mA worst case against the ACSL-6xx0's own",
        "7.0mA I_FH minimum, i.e. not guaranteed to switch at all. The original sizing",
        "was arithmetically right and physically wrong -- it solved (5.0V - VOL - VF)/I",
        "using the rail's NOMINAL 5.00V and VF TYP (1.52V), but +5V reaches these anodes",
        "through F4 and D3 (0.38-0.45V of drop, so 4.55-4.62V) and VF's worst case is",
        "1.80V. 249R gives 8.6mA worst / 13.7mA best: clear of the 8mA guardbanded floor",
        "(AV02-0235EN footnote b) and of the 15mA ABSOLUTE maximum. 240R was rejected at",
        "99% of that maximum; 270R misses the 8mA floor.",
        "",
        "THIS VALUE IS ONLY SAFE PARALLELED: at 249R one LED draws ~12.7mA, over a",
        "single SN74AHCT541 output's 7.5mA budget, so every LED here is driven by TWO",
        "outputs from tied inputs (taskpc-digital's U70-U73). It also assumes the",
        "external +5V supply is specified at +-2% -- at +-5% the tolerance spread is",
        "1.95:1 against a 2.14:1 window, which fits with nothing to spare. That",
        "tolerance is a load-bearing specification now, not an assumption.",
        "",
        "NI-SIDE PULL-UP: 3.9k -- fix round 1, corrected from this task's original 10k,",
        "which compared only against the mule's own 1k and never checked ACSL-6400's",
        "own datasheet RL-max (4k); 10k exceeded it. 3.9k (E24) is the largest E24 value",
        "at or under that limit. 24 stages at ~5-7mA (IDDL) draw 120-168mA against NI's",
        "250mA budget; 3.9k pull-ups add ~31mA (domain total 151-199mA, 51-99mA",
        "headroom) -- 10k added only ~12mA, but only by violating the part's own spec.",
        "Logic-level integrity holds (worst-case VOH >= NI_5V-0.39V at the datasheet's",
        "own worst-case leakage; VOL only improves at the lighter sink current); edge",
        "rate (tau~0.2us into ~50pF) was never close enough to the 500us strobe to",
        "decide this either way -- see the module docstring for the full derivation.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "POWER: NI-side output stage runs from NI_5V/NI_GND, taken DIRECTLY off",
        "Connector 1's own +5V (pins 8, 14) and D GND (12 pins) -- switcher-free,",
        "already referenced to NI's own ground, per this task's own brief. NI_5V's only",
        "real driver in this project is those passive connector pins, so it gets an",
        "explicit PWR_FLAG (same situation Task 7's own M12A_5 inlet already established",
        "the precedent for). NI_GND does NOT need one: the fallback DC-DC's own -Vout",
        "pin (real power_out, wired directly, see below) already drives it.",
        "",
        "FALLBACK: a filtered isolated DC-DC footprint (TMA-0505S, same part class the",
        "mule's own Task 2 uses), fully DNP, bridged onto NI_5V through a DNP 0R",
        "resistor -- contributes nothing to this sheet's real behaviour unless someone",
        "deliberately populates that one bridge, per this task's own explicit 'leave the",
        "footprint' instruction. See place_fallback_dcdc()'s own docstring for the full",
        "reasoning, including why this is a documented, task-authorized exception to the",
        "plan's own general 'no switchers except the isolated +-12V DC-DC' constraint.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "MDR68 CONNECTOR 1 PIN SOURCING: NI's X Series User Manual (370784K-01),",
        "Figure A-18 'NI PCIe-6353 and NI PCIe/PXIe-6363 Pinout' -- the recording NI's",
        "OWN card family (PXIe-6353, spec Sec.9.3; 'PXIe-6353' itself appears nowhere in",
        "the manual, only 'PCIe-6353'/'PCIe/PXIe-6363', which share one connector",
        "pinout figure). NOT reused from Task 8's own Figure-A-5 table without re-",
        "deriving: independently re-read from Figure A-18 and found BYTE-FOR-BYTE",
        "identical on all 24 P0.x positions, all 12 D GND positions, and both +5V",
        "positions -- see this generator's own module docstring for the full table and",
        "retrieval method. NOT sequential.",
        "",
        "Which of the 24 SIGNALS rides which P0.x NUMBER is this sheet's own declared,",
        "fixed choice (no manual fixes this the way it fixes physical-pin-per-P0.x-",
        "NUMBER) -- chosen to mirror Task 8's own EVT_Dn->P0.(8+n) convention so the",
        "SAME event-code bit lands on the SAME P0.x bit position on both the task-PC",
        "and recording-NI cards.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "PIN MAP VERIFICATION, ACSL-6400: Broadcom AV02-0235EN, cross-confirmed against",
        "TWO independent figures in the same datasheet (Figure 4, pin/functional",
        "diagram; Figure 10, schematic diagram) -- both agree exactly, on every pin.",
        "See hardware/gen/gen_wl_sync_lib.py's own module comment (search 'ACSL-6400')",
        "for the full transcription and sourcing account.",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "opto-ni.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
