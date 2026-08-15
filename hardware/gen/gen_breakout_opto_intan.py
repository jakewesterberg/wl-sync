"""Generator for hardware/breakout/sheets/opto-intan.kicad_sch -- Task 11 (Step 2) of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-11-brief.md). This is the galvanic barrier between the rig-side (DGND) domain and the
Intan domain's own isolated ground (INTAN_GND): 5 outbound channels (DGND -> INTAN_GND) and
1 inbound channel (INTAN_GND -> DGND, RHS_STIM_OUT, the one signal originating inside the
Intan domain), all six terminating on their own panel BNC.

PART MIX -- A CORRECTION FOUND BY THIS TASK'S OWN PIN-LEVEL VERIFICATION, not assumed going
in: one Broadcom ACSL-6400 (quad, all-in-one -- same part opto-ni.kicad_sch uses, 4
channels, all outbound: EVT_STROBE_BUF/BARCODE_PI/RWD_CMD_BUF/RWD_DLVR) PLUS one ACSL-6420
("quad, bi-directional 2/2" -- a DIFFERENT part, not a second ACSL-6400). Why: an all-in-one
part's four channels all share ONE VDD/GND domain on the OUTPUT (detector) side (confirmed
against Broadcom AV02-0235EN's own Figure 4/10 -- see gen_wl_sync_lib.py's own "ACSL-6400"
comment). This sheet's own second package needs ONE outbound channel (STIM_TRIG_BUF) and
ONE inbound channel (RHS_STIM_OUT) SIMULTANEOUSLY -- opposite directions. Forcing both into
a second all-in-one package would put one channel's own isolated-side output on the
non-isolated VDD/GND pins (or vice versa) -- not a tolerance problem, a topology one, the
same "plausible but wrong" failure class this project's own lib_symbols constraint-1 gotcha
warns about, one level up (part-class choice, not pin mislabeling). ACSL-6420's own 2/2
bidirectional split (Broadcom AV02-0235EN Figure 6/12, cross-confirmed the same way -- see
gen_wl_sync_lib.py's own "ACSL-6420" comment) is built for exactly this: 2 channels one
direction (own VDD1/GND1 on the LED side, VDD2/GND2 on the output side), 2 the reverse.
Channel 1 (direction 1->2) carries STIM_TRIG_BUF; channel 3 (direction 2->1) carries
RHS_STIM_OUT; channels 2 and 4 are the genuine spares (one per direction), matching this
task's own "one quad plus two channels of a second package" text once the direction split
is accounted for. Total across both opto sheets: 7xACSL-6400 + 1xACSL-6420 = eight quad
packages, matching the corrected 30-channel count (task-11-brief.md's own "Corrections").

TOPOLOGY -- non-inverting, same reasoning and same technique as opto-ni.kicad_sch's own
(see that generator's own module docstring for the full derivation): the LED's FIXED end
ties to the LOCAL "high" rail on ITS OWN side (the DGND-side +5V for an outbound channel's
LED, or ISO_5V for the inbound channel's own LED, which lives on the Intan side), and the
CATHODE is driven directly from the signal net. Every one of this sheet's 6 real channels
(plus 2 spares) uses this uniformly.

LED DRIVE CURRENT: 430R (E24), same value and same derivation as opto-ni.kicad_sch's own
(~7.33mA, roughly half ACSL-6400/6420's own shared datasheet-recommended top-of-range
forward current -- both parts are the SAME Broadcom family with identical LED/detector
electrical specs, confirmed directly against AV02-0235EN, which covers the whole ACSL-6xx0
family in one set of Electrical/Switching Specification tables).

PULL-UPS: 3.9k -- FIX ROUND 2 (see .superpowers/sdd/2026-08-13-breakout-pcb/
task-11-report.md's own "Fix round 2" section), corrected from this sheet's original 10k.
The original 10k was justified ONLY as "matches the NI-side choice ... for consistency" --
never checked against ACSL-6400/ACSL-6420's own datasheet pull-up maximum, the SAME gap fix
round 1 already found and corrected on opto-ni.kicad_sch (RL-max = 4k). RE-VERIFIED DIRECTLY
against Broadcom AV02-0235EN here, not inferred from that sibling sheet: the datasheet's own
"Recommended Operating Conditions" table (p.10) is ONE SINGLE TABLE covering the WHOLE
ACSL-6xx0 family (ACSL-6210/6300/6310/6400/6410/6420 -- the Device Selection Guide's own
six-part list, p.2) with no per-device split anywhere in the document -- the Electrical/
Switching Specification tables that follow split only by SUPPLY VOLTAGE range (3.0-3.6V vs.
4.5-5.5V), never by part number. The output stage RL-max characterizes (the open-collector
Schottky-clamped transistor fed by the two-stage detector amplifier) is schematically
IDENTICAL across every family member and every channel of every member (datasheet Figures
7-12: the same shield/amplifier/output-transistor block redrawn once per channel, regardless
of which VDD/GND pair feeds it) -- so RL-max = 4k binds ACSL-6420's own VO1-VO4 pins exactly
as it binds ACSL-6400's, on BOTH this sheet's own packages, not only the one opto-ni.kicad_sch
also happens to use. 3.9k (E24) is the largest standard value at or under that 4k limit --
independently re-derived from the same datasheet fact, not copied from opto-ni.kicad_sch's
own already-fixed value (the two sheets landing on the identical number is a consequence of
both drawing on the identical output stage and the identical E24 rounding rule, not an
assumption that one sheet's answer transfers unchecked to the other).

THE NI-SIDE 250mA CONNECTOR BUDGET ARGUMENT DOES NOT TRANSFER HERE, and is deliberately not
reused: this sheet's own output side runs from the board's OWN isolated ISO_5V rail (an
on-board LD1117S50TR_SOT223 LDO, POWER below), never from NI's 250mA-budgeted connector, so
there is no 250mA ceiling on this sheet at all. The reason to move off 10k is purely that a
stated datasheet maximum is a datasheet maximum -- independent of whether the resulting
current happens to fit any particular budget.

ISO_5V/ISO_P12 BUDGET, CHECKED FROM SCRATCH -- not assumed to inherit opto-ni's own headroom
figures, which are computed against a completely different rail with a completely different
channel mix. This sheet's own 6 ISO_5V-referenced pull-ups (Package A's 4 outbound channels
plus Package B's own channel 1/STIM_TRIG_BUF and channel 2/spare -- channels 3/4's own
pull-ups sit on +5V instead, see PART MIX/TOPOLOGY above) cost 6 x 5V/3.9k = ~7.7mA at 3.9k
versus 6 x 5V/10k = ~3.0mA at 10k -- a ~4.7mA delta, this fix's own real marginal cost.
Against ISO_5V's OTHER real loads -- 6 channels' worth of the detector IC's own supply
current (IDDL/IDDH, Broadcom AV02-0235EN's Electrical Specifications table, 4.5-5.5V range:
5.8 typ/10.5 max mA per channel when LOW, 3.8 typ/7.5 max mA when HIGH) drawn through Package
A's shared VDD/GND and Package B's own VDD2/GND2, plus channels 3/4's own LED forward current
(~7.33mA each, drawn from ISO_5V through their own R_LED -- see LED DRIVE CURRENT above) when
asserted -- worst case (all 6 detector channels LOW and both LEDs on simultaneously) is
~85mA at 3.9k versus ~81mA at 10k; typical (datasheet typ figures) ~57mA versus ~53mA. That
current reaches ISO_P12 through the LD1117S50TR_SOT223 LDO as approximately the same input
current (a linear regulator, no switching-conversion ratio), where it joins mux-intan.
kicad_sch's own 8 INA105KU difference amplifiers (~2mA each per task-7-report.md's own
brief-derived estimate, ~16mA total) on the SAME TPS7A4901-regulated ISO_P12 rail (150mA
rating, gen_breakout_power.py's own _place_isolated_supply()) -- worst case ~101-116mA total
against that 150mA cap, ~34-49mA of real headroom either way. The fix's own marginal cost is
the ~4.7mA delta, not the whole ~85mA figure -- "trivial current either way" (task-11-
report.md's own "Fix round 1" phrase for this exact comparison), confirmed here with real
datasheet numbers rather than assumed to carry over.

Every pull-up on THIS sheet -- 6 ISO_5V-referenced ones (Package A's 4 outbound channels
plus Package B's own channel 1/STIM_TRIG_BUF and channel 2/spare) and 2 +5V-referenced ones
(Package B's own channel 3/RHS_STIM_OUT and channel 4/spare) -- is 3.9k.

POWER -- ISO_5V, a NEW rail this sheet creates: an LD1117S50TR_SOT223 (Regulator_Linear,
the SAME family/package Task 7's own +3V3 stage already uses, just the 5V-fixed sibling)
regulating ISO_P12 down to 5V, referenced to INTAN_GND. ISO_P12/ISO_N12 themselves are
+-12V (Task 7) -- too high for the ACSL-6400/6420 family's own 5.5V absolute-maximum VDD, so
this sheet cannot power its own output stages directly off them the way the Intan
difference amplifiers (mux-intan.kicad_sch) do. Only ISO_P12 is consumed (a single positive
LDO input); ISO_N12 is not needed by this sheet's own digital-only logic supply.

RHS_STIM_OUT'S OWN INBOUND INPUT PROTECTION: a real Intan-domain BNC (this board's own
established isolated-BNC convention), 100R series + BAT54S clamp to ISO_5V/INTAN_GND --
"every panel input carries series resistance and clamp diodes to the rails" (this project's
own global constraint), reused verbatim from gen_breakout_taskpc_digital.py's own
bidirectional_clamp() topology, referenced to THIS sheet's own local rails (ISO_5V/
INTAN_GND) rather than the DGND-domain +5V/DGND those calls use, since this signal enters
the Intan/isolated domain, not the DGND one. Its assumed logic sense (active-HIGH,
TTL/CMOS-compatible, matching this design's other event lines' own convention) is an
on-sheet-flagged ASSUMPTION about real, external, third-party Intan/RHS hardware this
project has no datasheet for -- the same class of residual RWD_CMD's own polarity already
carries elsewhere in this project (taskpc-digital.kicad_sch), stated explicitly rather than
guessed silently.

CROSS-SHEET CONSEQUENCE, NOT A SCOPE OVERRUN (same class Task 8/9/10d already established a
precedent for): taskpc-digital.kicad_sch's own temporary PWR_FLAG on RHS_STIM_OUT -- placed
at Task 8 because nothing drove that net yet -- is deleted HERE (this sheet's own build(),
via a small, explicit, separately-documented edit to gen_breakout_taskpc_digital.py), the
moment this sheet wires a real open_collector driver (ACSL-6420's own VO3 pin) onto it,
exactly as that generator's own docstring already specified this cleanup as required.
check_breakout_pi_interface_netlist.py's own BARCODE_PI fan-out count moves 6->7 here (this
sheet's own real load, following opto-ni.kicad_sch's own 5->6).

Non-negotiables (task-11-brief.md's own numbering):
  1. lib_symbols via kicad_sch.py's own extract_symbol()/ensure_lib_symbol() only.
  2. (instances path ...) read from breakout.kicad_sch's own committed text.
  3. Refdes seeding clears ALL EIGHT already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend, analog-ni, mux-intan, comparators, opto-ni) via
     merge_max_refs() -- opto-ni is the newest sibling, committed separately per this
     task's own instruction, and its own real reference usage must be cleared too.
  4. Coordinate collisions -- same independent per-channel row-Y technique opto-ni.kicad_sch
     established (CH_ROW_DY below), not derived from either ACSL package's own internal pin
     geometry -- see that generator's own module comment for the exact collision class this
     avoids (Device:R's own 7.62mm pin span is exactly 3x this package family's 2.54mm row
     pitch).
  5. Every pin map (ACSL-6400, ACSL-6420, BAT54S, LD1117S50TR_SOT223) verified pin-by-pin
     against the real datasheet or, for stock KiCad symbols, cross-checked directly against
     the raw library text -- see above and gen_wl_sync_lib.py's own module comment.
  6. No footprint-pad-adjacency dependency on this sheet.

Run directly: `python3 hardware/gen/gen_breakout_opto_intan.py` (writes
hardware/breakout/sheets/opto-intan.kicad_sch). Requires hardware/breakout/breakout.kicad_sch
and all eight sibling sheets (power, taskpc-digital, pi-interface, analog-frontend,
analog-ni, mux-intan, comparators, opto-ni) to already exist on disk.
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_max_refs,
    find_root_uuid,
    find_sheet_instance_path,
    merge_max_refs,
    pin_pos,
    write_project_stub,
)

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
POWER_SCH = OUT / "power.kicad_sch"
TASKPC_SCH = OUT / "taskpc-digital.kicad_sch"
PI_INTERFACE_SCH = OUT / "pi-interface.kicad_sch"
ANALOG_FRONTEND_SCH = OUT / "analog-frontend.kicad_sch"
ANALOG_NI_SCH = OUT / "analog-ni.kicad_sch"
MUX_INTAN_SCH = OUT / "mux-intan.kicad_sch"
COMPARATORS_SCH = OUT / "comparators.kicad_sch"
OPTO_NI_SCH = OUT / "opto-ni.kicad_sch"
OPTO_INTAN_SHEETFILE = "sheets/opto-intan.kicad_sch"

# ---------------------------------------------------------------------------
# Footprints -- Device/Package_SO/wl-sync/Regulator_Linear/Package_TO_SOT_SMD/Diode/
# Connector_Coaxial all already registered (Task 6/7/8/9/10a); no new sym-lib-table/
# fp-lib-table entries needed for this sheet.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_C_BULK = "Capacitor_SMD:C_1206_3216Metric"
FOOTPRINT_ACSL = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
FOOTPRINT_SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"
FOOTPRINT_SOT23 = "Package_TO_SOT_SMD:SOT-23"
FOOTPRINT_BNC = "Connector_Coaxial:BNC_PanelMountable_Vertical"

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
PKG_DY = GRID(50.8)  # same pitch opto-ni.kicad_sch uses; clears both packages' own
# 8-row (+-8.89mm) pin bands with the same margin.

X_LEDR = GRID(20)
X_PKG = GRID(90)     # both ACSL6400 and ACSL6420 share the same build_ic_symbol() own
# half_w (10*2.54=25.4mm), so the same column serves both packages identically.
X_PULLUP = GRID(165)
X_BNC, Y_BNC0 = GRID(230), GRID(20)  # 6 BNCs, one per channel (5 outbound + 1 inbound),
# stacked with LOAD_DY below -- own column, clear of X_PULLUP.
LOAD_DY = GRID(25.4)

X_LDO, Y_LDO = GRID(300), GRID(20)   # ISO_5V regulator block -- own column.
X_CLAMP, Y_CLAMP = GRID(230), GRID(200)  # RHS_STIM_OUT's own inbound protection --
# below the 6 BNCs' own stack (last BNC at Y_BNC0+5*25.4=147, clear of 200).

# Per-channel resistor row Y, independent of either ACSL package's own internal pin
# geometry -- see module docstring / gen_breakout_opto_ni.py's own CH_ROW_DY comment for
# the exact collision class this avoids (Device:R's own 7.62mm span is exactly 3x this
# package family's 2.54mm row pitch).
CH_ROW_DY = GRID(15.24)

DECOUPLE_DX = GRID(15.24)
DECOUPLE_DY = GRID(5.08)

X_NOTE1, Y_NOTE1 = GRID(370), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(370), GRID(110)
X_NOTE3, Y_NOTE3 = GRID(370), GRID(200)
X_NOTE4, Y_NOTE4 = GRID(370), GRID(290)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# ACSL-6400 (package A, all 4 channels outbound -- same pin map opto-ni.kicad_sch uses).
# ---------------------------------------------------------------------------
ACSL6400_CH_PINS = {
    0: ("1", "2", "14"),
    1: ("3", "4", "13"),
    2: ("5", "6", "12"),
    3: ("7", "8", "11"),
}
ACSL6400_PIN_GND = ["9", "16"]
ACSL6400_PIN_VDD = ["10", "15"]

# ACSL-6420 (package B, bi-directional 2/2) -- Broadcom AV02-0235EN Figure 6/12, both
# agree exactly (see gen_wl_sync_lib.py's own "ACSL-6420" comment for the full account).
# Direction "1->2": LED on VDD1/GND1, output (VOx) on VDD2/GND2.
ACSL6420_DIR_1TO2 = {
    1: ("5", "6", "11"),  # ch1: ANODE1, CATHODE1, VO1
    2: ("7", "8", "10"),  # ch2 (spare): ANODE2, CATHODE2, VO2
}
# Direction "2->1": LED on VDD2/GND2, output (VOx) on VDD1/GND1.
ACSL6420_DIR_2TO1 = {
    3: ("14", "13", "3"),  # ch3: ANODE3, CATHODE3, VO3
    4: ("16", "15", "2"),  # ch4 (spare): ANODE4, CATHODE4, VO4
}
ACSL6420_PIN_GND1 = "1"
ACSL6420_PIN_VDD1 = "4"
ACSL6420_PIN_GND2 = "9"
ACSL6420_PIN_VDD2 = "12"

LED_R_OHMS = "430"
PULLUP_OHMS = "3.9k"  # fix round 2 -- corrected from 10k, which exceeded ACSL-6400/
# ACSL-6420's own shared datasheet RL-max (4k). See module docstring, PULL-UPS.

# ---------------------------------------------------------------------------
# Channel contract -- 5 outbound (DGND -> INTAN_GND) + 1 inbound (INTAN_GND -> DGND).
# (source_net, final_net) -- for the inbound channel, "source_net" is the protected,
# LOCAL Intan-side net (RHS_STIM_RAW, downstream of its own BNC/clamp) and "final_net" is
# RHS_STIM_OUT, the ALREADY-ESTABLISHED bare DGND-domain contract net (Task 8).
# ---------------------------------------------------------------------------
# EVERY SOURCE NET BELOW IS THIS SHEET'S OWN DEDICATED BUFFERED LEG, and it is the
# `_INTAN_BUF` one, never the bare/NI-side name. Before the "one optocoupler LED per
# driver pin" fix, all five outbound channels here shared a source net with their NI
# twin on opto-ni.kicad_sch -- so one 74HCT541 output pin (or, for RWD_DLVR, one 74HCT32
# gate output) drove TWO ACSL LEDs at ~7.33mA each: 14.7mA against a 6mA IOL, and against
# 4mA for the HCT32. See gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS (which
# spends that package's last 5 spare channels producing exactly the three *_INTAN_BUF
# nets below plus both RWD_DLVR legs) and gen_breakout_pi_interface.py's own
# BARCODE_OPTO_LEGS. Nothing on this sheet may share a source net with opto-ni again.
OUTBOUND_CHANNELS = [
    ("EVT_STROBE_INTAN_BUF", "EVT_STROBE_INTAN"),
    ("BARCODE_INTAN_BUF", "BARCODE_INTAN"),
    ("RWD_CMD_INTAN_BUF", "RWD_CMD_INTAN"),
    ("RWD_DLVR_INTAN_BUF", "RWD_DLVR_INTAN"),
]
assert len(OUTBOUND_CHANNELS) == 4
STIM_TRIG_CHANNEL = ("STIM_TRIG_INTAN_BUF", "STIM_TRIG_INTAN")
assert all(src.endswith("_INTAN_BUF") for src, _final in OUTBOUND_CHANNELS + [STIM_TRIG_CHANNEL]), (
    "every outbound LED on this sheet must be driven by its OWN _INTAN_BUF leg, never by "
    "a net shared with opto-ni.kicad_sch's own LED for the same signal"
)
RHS_STIM_CHANNEL = ("RHS_STIM_RAW", "RHS_STIM_OUT")  # inbound: source=Intan-side, final=DGND-side


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


def decouple(sch, x, y, rail, gnd):
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)


def bidirectional_clamp(sch, x, y, net_signal, net_hi, net_lo):
    """BAT54S wired as a genuine two-rail series-pair clamp -- IDENTICAL topology and
    pin map to gen_breakout_taskpc_digital.py's own bidirectional_clamp() (itself
    inherited verbatim from gen_mule.py, validated on the mule board), reused here
    against THIS sheet's own local rails (ISO_5V/INTAN_GND) rather than the DGND-domain
    +5V/DGND those calls use -- see module docstring, RHS_STIM_OUT'S OWN INBOUND INPUT
    PROTECTION."""
    ref = sch.next_ref("D")
    pins = sch.place("Diode", "BAT54S", ref, "BAT54S", x, y, footprint=FOOTPRINT_SOT23)
    lbl(sch, x, y, pins, "1", net_lo)
    lbl(sch, x, y, pins, "3", net_signal)
    lbl(sch, x, y, pins, "2", net_hi)
    return ref


def opto_channel(sch, x_pkg, y_pkg, pkg_pins, ch_pins, row_y, led_hi, led_lo_source, pu_hi, final_net):
    """One optocoupler channel, non-inverting (see module docstring, TOPOLOGY):
    `led_hi` --[R_LED]--> ANODE ; CATHODE --> `led_lo_source` (the driving signal).
    `pu_hi` --[R_PU]--> VOx/`final_net`. `led_hi`/`pu_hi` are the LOCAL "high" rail on
    each pin's OWN side -- same rail for an outbound channel's LED+DGND and different
    rails (ISO_5V for the LED, DGND-side +5V for the pull-up) for the inbound channel,
    since the LED and VOx pins sit on opposite sides of the barrier there."""
    anode_pin, cathode_pin, vo_pin = ch_pins
    anode_net = f"{final_net}_LEDA"
    r_led = two_pin(sch, "Device", "R", "R", LED_R_OHMS, X_LEDR, row_y, led_hi, anode_net, footprint=FOOTPRINT_R)
    lbl(sch, x_pkg, y_pkg, pkg_pins, anode_pin, anode_net)
    lbl(sch, x_pkg, y_pkg, pkg_pins, cathode_pin, led_lo_source)

    r_pu = two_pin(sch, "Device", "R", "R", PULLUP_OHMS, X_PULLUP, row_y, pu_hi, final_net, footprint=FOOTPRINT_R)
    lbl(sch, x_pkg, y_pkg, pkg_pins, vo_pin, final_net)
    return r_led, r_pu


def place_bnc(sch, y, desc, center_net, refs):
    ref = sch.next_ref("J")
    pins = sch.place("Connector", "Conn_Coaxial", ref, desc, X_BNC, y, footprint=FOOTPRINT_BNC)
    lbl(sch, X_BNC, y, pins, "1", center_net)
    lbl(sch, X_BNC, y, pins, "2", "INTAN_GND")
    refs.setdefault("bnc", []).append(ref)
    return ref


def place_package_a(sch, refs):
    """ACSL-6400, 4 outbound channels: EVT_STROBE_BUF/BARCODE_PI/RWD_CMD_BUF/RWD_DLVR."""
    y_pkg = Y0
    ref = sch.next_ref("U")
    pkg_pins = sch.place("wl-sync", "ACSL6400", ref, "ACSL-6400-00TE", X_PKG, y_pkg, footprint=FOOTPRINT_ACSL)
    refs["acsl6400"] = ref

    for local_idx, (source_net, final_net) in enumerate(OUTBOUND_CHANNELS):
        row_y = GRID(Y0 + local_idx * CH_ROW_DY)
        r_led, r_pu = opto_channel(
            sch, X_PKG, y_pkg, pkg_pins, ACSL6400_CH_PINS[local_idx], row_y,
            "+5V", source_net, "ISO_5V", final_net,
        )
        refs.setdefault("led_r", []).append(r_led)
        refs.setdefault("pullup_r", []).append(r_pu)
        place_bnc(sch, GRID(Y_BNC0 + local_idx * LOAD_DY), f"Intan opto out: {final_net} (BNC)", final_net, refs)

    for gpin in ACSL6400_PIN_GND:
        lbl(sch, X_PKG, y_pkg, pkg_pins, gpin, "INTAN_GND")
    for vpin in ACSL6400_PIN_VDD:
        lbl(sch, X_PKG, y_pkg, pkg_pins, vpin, "ISO_5V")

    gnd9_x, gnd9_y = pin_pos(X_PKG, y_pkg, pkg_pins["9"])
    c1 = decouple(sch, X_PKG - DECOUPLE_DX, gnd9_y, "ISO_5V", "INTAN_GND")
    gnd16_x, gnd16_y = pin_pos(X_PKG, y_pkg, pkg_pins["16"])
    c2 = decouple(sch, X_PKG + DECOUPLE_DX, gnd16_y, "ISO_5V", "INTAN_GND")
    refs.setdefault("bypass_c", []).extend([c1, c2])


def place_package_b(sch, refs):
    """ACSL-6420, bi-directional 2/2. Channel 1 (dir 1->2): STIM_TRIG_BUF outbound.
    Channel 3 (dir 2->1): RHS_STIM_OUT inbound. Channels 2/4: genuine spares, one per
    direction -- see module docstring, PART MIX."""
    y_pkg = GRID(Y0 + PKG_DY)
    ref = sch.next_ref("U")
    pkg_pins = sch.place("wl-sync", "ACSL6420", ref, "ACSL-6420-00TE", X_PKG, y_pkg, footprint=FOOTPRINT_ACSL)
    refs["acsl6420"] = ref

    # --- Channel 1: STIM_TRIG_BUF outbound (direction 1->2: LED on VDD1/GND1=+5V/DGND,
    # output on VDD2/GND2=ISO_5V/INTAN_GND) ---
    row_y1 = GRID(Y0 + 4 * CH_ROW_DY)
    source1, final1 = STIM_TRIG_CHANNEL
    r_led1, r_pu1 = opto_channel(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_DIR_1TO2[1], row_y1, "+5V", source1, "ISO_5V", final1)
    refs.setdefault("led_r", []).append(r_led1)
    refs.setdefault("pullup_r", []).append(r_pu1)
    place_bnc(sch, GRID(Y_BNC0 + 4 * LOAD_DY), f"Intan opto out: {final1} (BNC)", final1, refs)

    # --- Channel 2: spare, direction 1->2 ---
    row_y2 = GRID(Y0 + 5 * CH_ROW_DY)
    r_led2, r_pu2 = opto_channel(
        sch, X_PKG, y_pkg, pkg_pins, ACSL6420_DIR_1TO2[2], row_y2,
        "+5V", "OPTO_INTAN_SPARE1_IN", "ISO_5V", "OPTO_INTAN_SPARE1_ISO",
    )
    refs.setdefault("spare_r", []).extend([r_led2, r_pu2])

    # --- Channel 3: RHS_STIM_OUT inbound (direction 2->1: LED on VDD2/GND2=ISO_5V/
    # INTAN_GND, output on VDD1/GND1=+5V/DGND) ---
    row_y3 = GRID(Y0 + 6 * CH_ROW_DY)
    source3, final3 = RHS_STIM_CHANNEL
    r_led3, r_pu3 = opto_channel(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_DIR_2TO1[3], row_y3, "ISO_5V", source3, "+5V", final3)
    refs.setdefault("led_r", []).append(r_led3)
    refs.setdefault("pullup_r", []).append(r_pu3)

    # --- Channel 4: spare, direction 2->1 ---
    row_y4 = GRID(Y0 + 7 * CH_ROW_DY)
    r_led4, r_pu4 = opto_channel(
        sch, X_PKG, y_pkg, pkg_pins, ACSL6420_DIR_2TO1[4], row_y4,
        "ISO_5V", "OPTO_INTAN_SPARE2_ISO", "+5V", "OPTO_INTAN_SPARE2_OUT",
    )
    refs.setdefault("spare_r", []).extend([r_led4, r_pu4])

    lbl(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_PIN_GND1, "DGND")
    lbl(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_PIN_VDD1, "+5V")
    lbl(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_PIN_GND2, "INTAN_GND")
    lbl(sch, X_PKG, y_pkg, pkg_pins, ACSL6420_PIN_VDD2, "ISO_5V")

    g1x, g1y = pin_pos(X_PKG, y_pkg, pkg_pins[ACSL6420_PIN_GND1])
    c1 = decouple(sch, X_PKG - DECOUPLE_DX, g1y, "+5V", "DGND")
    g2x, g2y = pin_pos(X_PKG, y_pkg, pkg_pins[ACSL6420_PIN_GND2])
    c2 = decouple(sch, X_PKG + DECOUPLE_DX, g2y, "ISO_5V", "INTAN_GND")
    refs.setdefault("bypass_c", []).extend([c1, c2])


def place_rhs_stim_input(sch, refs):
    """RHS_STIM_OUT's own inbound BNC + 100R series + BAT54S clamp to ISO_5V/INTAN_GND
    -- see module docstring, RHS_STIM_OUT'S OWN INBOUND INPUT PROTECTION."""
    bnc_ref = place_bnc(sch, GRID(Y_BNC0 + 5 * LOAD_DY), "Intan RHS stim status in (BNC)", "RHS_STIM_BNC", refs)
    r_ref = two_pin(sch, "Device", "R", "R", "100", X_CLAMP, Y_CLAMP, "RHS_STIM_BNC", "RHS_STIM_RAW", footprint=FOOTPRINT_R)
    d_ref = bidirectional_clamp(sch, X_CLAMP + GRID(20.32), Y_CLAMP, "RHS_STIM_RAW", "ISO_5V", "INTAN_GND")
    refs["rhs_stim_series_r"] = r_ref
    refs["rhs_stim_clamp_d"] = d_ref


def place_iso_5v_ldo(sch, refs):
    """ISO_5V: LD1117S50TR_SOT223 regulating ISO_P12 down to 5V, referenced INTAN_GND --
    see module docstring, POWER. Pin map (VI=3, GND=1, VO=2) confirmed directly against
    the raw Regulator_Linear.kicad_sym text via kicad_sch.py's own extract_symbol()/
    unit_pins() -- IDENTICAL to LD1117S33TR_SOT223's own pin map (gen_breakout_power.py's
    own u1_map), same SOT-223 sub-family, same physical pin function regardless of the
    fixed output voltage variant."""
    ref = sch.next_ref("U")
    pins = sch.place("Regulator_Linear", "LD1117S50TR_SOT223", ref, "LD1117S50TR_SOT223", X_LDO, Y_LDO, footprint=FOOTPRINT_SOT223)
    lbl(sch, X_LDO, Y_LDO, pins, "3", "ISO_P12")
    lbl(sch, X_LDO, Y_LDO, pins, "1", "INTAN_GND")
    lbl(sch, X_LDO, Y_LDO, pins, "2", "ISO_5V")
    c1 = two_pin(sch, "Device", "C", "C", "100nF", X_LDO, Y_LDO + GRID(15.24), "ISO_5V", "INTAN_GND", footprint=FOOTPRINT_C_SMALL)
    c2 = two_pin(sch, "Device", "C", "C", "10uF", X_LDO + GRID(15.24), Y_LDO + GRID(15.24), "ISO_5V", "INTAN_GND", footprint=FOOTPRINT_C_BULK)
    refs["iso5v_ldo"] = ref
    refs["iso5v_cap"] = [c1, c2]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, OPTO_INTAN_SHEETFILE)
    ref_start = merge_max_refs(
        find_max_refs(POWER_SCH.read_text()),
        find_max_refs(TASKPC_SCH.read_text()),
        find_max_refs(PI_INTERFACE_SCH.read_text()),
        find_max_refs(ANALOG_FRONTEND_SCH.read_text()),
        find_max_refs(ANALOG_NI_SCH.read_text()),
        find_max_refs(MUX_INTAN_SCH.read_text()),
        find_max_refs(COMPARATORS_SCH.read_text()),
        find_max_refs(OPTO_NI_SCH.read_text()),
    )

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    place_iso_5v_ldo(sch, refs)
    place_package_a(sch, refs)
    place_package_b(sch, refs)
    place_rhs_stim_input(sch, refs)

    for line_idx, line in enumerate([
        "Opto-Intan (Task 11) -- 6 digital channels: 5 outbound (DGND -> INTAN_GND --",
        "EVT_STROBE_BUF, BARCODE_PI, RWD_CMD_BUF, RWD_DLVR, STIM_TRIG_BUF) and 1 inbound",
        "(INTAN_GND -> DGND -- RHS_STIM_OUT, the one signal originating inside the Intan",
        "domain). All six out to their own panel BNC (isolated shells, this board's own",
        "established convention).",
        "",
        "PART MIX, a correction found by this task's own pin-level verification: ONE",
        "ACSL-6400 (all-in-one, all 4 channels outbound) PLUS ONE ACSL-6420",
        "(bi-directional 2/2) -- NOT two ACSL-6400. An all-in-one part's 4 channels share",
        "ONE VDD/GND on the output side; this sheet's own mixed-direction second package",
        "(1 outbound + 1 inbound channel needed simultaneously) cannot fit an all-in-one",
        "part without putting a channel's output on the wrong side of the barrier",
        "entirely -- see this generator's own module docstring for the full account.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "TOPOLOGY: non-inverting, same technique as opto-ni.kicad_sch's own -- the LED's",
        "fixed end ties to the LOCAL high rail on its OWN side (+5V for an outbound",
        "channel's LED, ISO_5V for the inbound channel's own LED, which lives on the",
        "Intan side), cathode driven directly by the signal net.",
        "",
        "LED DRIVE: 430R (same value/derivation as opto-ni.kicad_sch -- ~7.33mA, roughly",
        "half the ACSL-6xx0 family's shared datasheet-recommended top-of-range forward",
        "current). PULL-UPS: 3.9k throughout -- FIX ROUND 2: ACSL-6400/ACSL-6420 share ONE",
        "datasheet RL-max (4k, Broadcom AV02-0235EN's family-wide table), which the",
        "original 10k exceeded (same violation fix round 1 corrected on opto-ni.kicad_sch).",
        "NI's 250mA budget does NOT apply here (this sheet runs off the board's own",
        "isolated ISO_5V rail); ISO_5V/ISO_P12's own budget was checked from scratch --",
        "~34-49mA of headroom against the shared TPS7A4901's 150mA cap either way.",
        "",
        "Channels 2 and 4 of the ACSL-6420 (one per direction) are genuine spares -- real",
        "LED+pull-up resistors populated, wired to their own uniquely-named, currently-",
        "unused nets (OPTO_INTAN_SPARE1/2_*), same 'populate option, ready if needed'",
        "convention the mule's own OPTO_SPARE1/2 channels already established.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "POWER: ISO_5V, a NEW rail -- LD1117S50TR_SOT223 (same family/package as Task",
        "7's own +3V3 stage) regulating ISO_P12 down to 5V, referenced INTAN_GND.",
        "ISO_P12/ISO_N12 are +-12V (Task 7), too high for the ACSL-6400/6420 family's own",
        "5.5V absolute-maximum VDD -- unlike mux-intan.kicad_sch's own difference",
        "amplifiers, this sheet's digital logic cannot run directly off them. Only",
        "ISO_P12 is consumed (a single positive LDO input); ISO_N12 is not needed.",
        "",
        "RHS_STIM_OUT'S OWN INBOUND PROTECTION: a real Intan-domain BNC, 100R series +",
        "BAT54S clamp to ISO_5V/INTAN_GND -- 'every panel input carries series",
        "resistance and clamp diodes to the rails' (this project's own global",
        "constraint), the SAME BAT54S topology gen_breakout_taskpc_digital.py's own",
        "bidirectional_clamp() already validates, referenced to THIS sheet's own local",
        "rails since the signal enters the Intan/isolated domain. Its assumed logic",
        "sense (active-HIGH, TTL/CMOS-compatible) is an ASSUMPTION about real,",
        "external, third-party Intan/RHS hardware this project has no datasheet for --",
        "flagged here explicitly, the same class RWD_CMD's own polarity already",
        "carries elsewhere in this project, needing bench confirmation before",
        "commissioning.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "CROSS-SHEET CONSEQUENCE, NOT A SCOPE OVERRUN: taskpc-digital.kicad_sch's own",
        "temporary PWR_FLAG on RHS_STIM_OUT (Task 8, placed because nothing drove that",
        "net yet) is deleted by this sheet's own build() -- see",
        "gen_breakout_taskpc_digital.py's own _place_outbound(), which already flagged",
        "this exact deletion as required the moment a real driver existed. Same class",
        "Task 10d's own comparators.kicad_sch cleanup already established a precedent",
        "for (PD1_COMP/PD2_COMP/ACC_TRIG's own flags, deleted there for the identical",
        "reason).",
        "",
        "PIN MAP VERIFICATION: ACSL-6400/ACSL-6420 against Broadcom AV02-0235EN (see",
        "gen_wl_sync_lib.py's own module comment); BAT54S/LD1117S50TR_SOT223 are real",
        "stock KiCad symbols, cross-checked directly against the raw library text via",
        "extract_symbol()/unit_pins() (same technique this project uses for every",
        "stock-symbol pin map, not assumed from the symbol name alone).",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "opto-intan.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
