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
     P0.0-7 + P1.0-7 (analog + AISENSE; Task 10 wires it, this file only places the
     connector and leaves every pin no_connect -- see _place_connector0()'s own
     docstring for why that split of labour, not an electrical decision, is the right
     one here); Connector 1 = AI16-31 + P0.8-31 + P2.0-7, of which this design uses only
     the digital range (P0.8-30, 23 lines) -- all 23 of the task PC's own digital lines,
     leaving connector 0 exclusively analog. See _place_connector1()'s own docstring for
     the physical-MDR-pin assignment (this board's OWN sequential choice, since no
     source -- spec or NI's own device manual -- fixes individual pin positions; only the
     PORT-LEVEL split is sourced) and the P0.8-P0.23 event-code offset MonkeyLogic needs.
  2. Inbound path -- 19 channels (16 event data + strobe + RWD_CMD + STIM_TRIG), each:
     MDR68 pin -> 100R series -> BAT54S clamp (signal on the series midpoint, low side
     DGND, HIGH SIDE +5V -- never +3V3, see _bidirectional_clamp()'s own docstring for
     why, verbatim from Task 2's mule-board fix) -> fans out in PARALLEL to two
     independent buffer banks sharing that one protected node: SN74LVC541APW on +3V3
     (3 packages, 8ch each) producing the *_PI/RWD_CMD/STIM_TRIG contract nets, and
     SN74HCT541PW on +5V (3 more packages) producing the *_BUF contract nets Task 11's
     NI optocouplers consume (ruling F2, .superpowers/sdd/2026-08-13-breakout-pcb/
     progress.md -- without this second bank, 22 LED loads would sit directly on the
     task PC's own DAQ pins).
  3. Outbound path -- 4 channels (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT -- each
     produced ELSEWHERE, by Task 10's comparators sheet or Task 11's opto-intan sheet,
     and consumed here BY NAME as this bank's own buffer inputs) through one more
     SN74HCT541PW on +5V (4 of 8 channels used), driving Connector 1's own remaining 4
     digital pins back to the task PC. No input protection network here (unlike step 2)
     -- brief Step 3 does not ask for one, and the general principle stated right before
     spec Sec.9 ("every panel input carries series resistance and clamp diodes") is
     specifically about INBOUND (to-the-board) signals; these are OUR OWN buffer's
     push-pull outputs driving INTO the task PC's DAQ input pins, not an externally
     sourced signal this board needs to protect itself against.
  4. Reward OR: RWD_CMD (already produced in stage 2, the LVC541 bank's own 3.3V output)
     and a manual panel button (RWD_BTN, its own 10k pull-up + 100nF debounce + 74HCT14
     Schmitt PAIR -- 2 series inversions, net non-inverting) combine in a 74HCT32 OR
     gate to produce RWD_DLVR, which drives a reward-driver BNC (placeholder -- see
     _place_reward_or()'s own docstring) and is available by name for Task 11's
     NI/Intan optocouplers.

Run directly: `python3 hardware/gen/gen_breakout_taskpc_digital.py` (writes
hardware/breakout/sheets/taskpc-digital.kicad_sch). hardware/breakout/sym-lib-table
gained one new entry for this task -- Connector_Generic (Conn_01x02, used for the three
placeholder 2-pin connectors: manual reward button, remote reward jack, reward-driver
BNC) -- see hardware/README.md.

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
# consumed from Task 7: +5V, +3V3, DGND.
# ---------------------------------------------------------------------------
CONTRACT_NETS = (
    [f"EVT_D{i}_TPC" for i in range(16)] + ["EVT_STROBE_TPC"]
    + [f"EVT_D{i}_PI" for i in range(16)] + ["EVT_STROBE_PI"]
    + [f"EVT_D{i}_BUF" for i in range(16)] + ["EVT_STROBE_BUF"]
    + ["RWD_CMD", "RWD_CMD_BUF", "RWD_BTN", "RWD_DLVR", "STIM_TRIG", "STIM_TRIG_BUF"]
    + ["PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"]
)
assert len(CONTRACT_NETS) == 16 + 1 + 16 + 1 + 16 + 1 + 6 + 4 == 61

# ---------------------------------------------------------------------------
# Footprints -- picked from the part actually being ordered (same discipline
# gen_breakout_power.py's own footprint block documents; see task-7-report.md and
# hardware/README.md for the sourcing behind every one of these already established
# elsewhere in this repo).
# ---------------------------------------------------------------------------
FOOTPRINT_TSSOP20 = "Package_SO:Texas_PW0020A_TSSOP-20_4.4x6.5mm_P0.65mm"  # SN74LVC541APW /
# SN74HCT541PW -- TI's own "PW" package code is literally TSSOP-20 (gen_mule.py's own choice)
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"  # SN74HCT32D / SN74HCT14D -- TI's
# own "D" package code is the standard 3.9mm-body SOIC-14, JEDEC MS-012
FOOTPRINT_SOT23 = "Package_TO_SOT_SMD:SOT-23"  # BAT54S -- the real part, no stand-in
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"  # 100nF decoupling/debounce
FOOTPRINT_MDR68 = "wl-sync:MDR68_Male_RightAngle"
FOOTPRINT_HDR1X02 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"

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

# Outbound HCT541 (+5V), 1 package, 4 of 8 channels used.
X_OUTBUF, Y_OUTBUF = GRID(220), GRID(260)

# Reward OR block.
X_RWD_BTN, Y_RWD_BTN = GRID(20), GRID(300)
X_RWD_JACK, Y_RWD_JACK = GRID(20), GRID(315)
X_RWD_PULLUP, Y_RWD_PULLUP = GRID(50), GRID(295)
X_RWD_CAP, Y_RWD_CAP = GRID(50), GRID(310)
X_U9, Y_U9 = GRID(90), GRID(295)   # 74HCT14 (debounce inverter pair + 4 unused + power)
GATE_DY = GRID(12.7)
X_U8, Y_U8 = GRID(180), GRID(295)  # 74HCT32 (reward OR + 3 unused gates + power)
X_RWD_BNC, Y_RWD_BNC = GRID(230), GRID(295)

DECOUPLE_DX = GRID(15.24)

# Text-note anchors.
X_NOTE1, Y_NOTE1 = GRID(20), GRID(15)     # connector split source + P0.x offset
X_NOTE2, Y_NOTE2 = GRID(20), GRID(545)    # Connector 0 placeholder
X_NOTE3, Y_NOTE3 = GRID(20), GRID(390)    # reward-OR polarity concern
NOTE_DY = GRID(5.08)

CHAN_A = {i: str(2 + i) for i in range(8)}   # 74x541 unit-1 pin numbers: A0..A7
CHAN_Y = {i: str(18 - i) for i in range(8)}  # ...and Y0..Y7, paired by channel: Ai+Yi=20


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    """Place a 2-pin part between two labeled nets -- same pattern as gen_mule.py's and
    gen_breakout_power.py's own two_pin() (duplicated rather than imported: kicad_sch.py,
    not any one generator, is this project's shared machinery)."""
    ref = sch.next_ref(ref_prefix)
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


def place_octal_buffer(sch, libname, symname, value, x, y, rail, channels, refs, role_key, footprint):
    """Place one 74x541-family octal buffer (LVC541 on +3V3, or HCT541 on +5V -- same
    physical pinout either way, see CHAN_A/CHAN_Y: input Ai at KiCad pin 2+i, tri-state
    output Yi at pin 18-i, so Ai+Yi=20 for every channel of every instance -- the
    invariant check_taskpc_digital_netlist.py's own end-to-end walk depends on, exactly
    as check_mule_netlist.py's _walk_channel() already established for the mule).
    `channels` maps local channel index (0-7) -> (input_net, output_net) for USED
    channels; channels not present are tied off safely (input -> DGND, output ->
    no-connect) -- gen_mule.py's own place_541() precedent, never leave a CMOS buffer
    input floating even when its output drives nothing.
    """
    ref = sch.next_ref("U")
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
    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, "DGND", footprint=FOOTPRINT_C_SMALL)
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


def place_debounce_inverters(sch, x, y, dy, in_net, mid_net, out_net, rail, gnd, refs):
    """One 74HC14-rooted hex Schmitt-trigger inverter (no stock '74HCT14' symbol exists
    either -- see module docstring; 74HC14 is itself extends-free -- 74LS14 is the one
    that extends IT, confirmed directly against the raw library text -- so this places
    74HC14 as the real, pin-bearing part, Value overridden to SN74HCT14D). Units 1-6 are
    the six independent inverters, unit 7 carries VCC(14)/GND(7) -- confirmed directly
    against the raw library text, same convention as the OR gate above.

    Units 1 and 2 (pins 1->2, then 3->4) are wired in SERIES -- task-8-brief.md's own
    "Schmitt inverter PAIR", not a single stage: two inversions cancel, so the debounced
    copy keeps -- does not flip -- RWD_BTN's own raw polarity, the standard technique for
    a clean, non-inverting, Schmitt-buffered debounce. Units 3-6 are genuinely unused:
    input tied to `gnd`, output no-connected, same discipline as every other unused gate
    on this sheet.
    """
    ref = sch.next_ref("U")
    p1 = sch.place(
        "74xx", "74HC14", ref, "SN74HCT14D", x, y, unit=1, footprint=FOOTPRINT_SOIC14,
        extra_props={"Description": "Hex Schmitt-trigger inverter"},
    )
    ix, iy = pin_pos(x, y, p1["1"])
    sch.label(in_net, ix, iy)
    ox, oy = pin_pos(x, y, p1["2"])
    sch.label(mid_net, ox, oy)

    y2 = y + dy
    p2 = sch.place("74xx", "74HC14", ref, "SN74HCT14D", x, y2, unit=2, footprint=FOOTPRINT_SOIC14)
    ix2, iy2 = pin_pos(x, y2, p2["3"])
    sch.label(mid_net, ix2, iy2)
    ox2, oy2 = pin_pos(x, y2, p2["4"])
    sch.label(out_net, ox2, oy2)

    for unum, in_p, out_p in ((3, "5", "6"), (4, "9", "8"), (5, "11", "10"), (6, "13", "12")):
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


def _place_connector0(sch, refs):
    """Step 1, Connector 0: analog + AISENSE, per spec Sec.9.2's own confirmed split
    (AI0-15 + P0.0-7 + P1.0-7). Placed here (both task-PC MDR68 connectors are the
    physical pair on ONE device, and Step 1 explicitly asks for both), but left ENTIRELY
    UNPOPULATED -- every one of its 68 pins no_connect -- because this sheet has no
    analog content or channel list to assign it (Task 10 owns the 9 task-PC analog
    channels: A_EYE_LX/LY/RX/RY, A_JOY_X/Y, A_MISC1/2/3, per the plan's own Task 10 Step
    3). This is a real, deliberate placeholder, not an oversight: a KiCad symbol
    instance's pins can only be labelled from the SAME .kicad_sch file that places it,
    so Connector 0's own physical wiring has to happen wherever this specific J
    instance lives -- flagged in this task's own report as a cross-task consideration
    for whoever implements Task 10, the same way gen_breakout.py's own root sheet
    leaves 9 child sheet files for later tasks to create.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "MDR68_Male", ref,
        "Connector 0 (analog + AISENSE, task PC NI) -- reserved for Task 10",
        X_CONN0, Y_CONN0, footprint=FOOTPRINT_MDR68,
    )
    for n in range(1, 69):
        x, y = pin_pos(X_CONN0, Y_CONN0, pins[str(n)])
        sch.no_connect(x, y)
    refs["conn0"] = ref
    for line_idx, line in enumerate([
        "Connector 0 (analog + AISENSE): placed here, left fully unpopulated.",
        "Task 10 wires the 9 task-PC analog channels (A_EYE_LX/LY/RX/RY, A_JOY_X/Y,",
        "A_MISC1/2/3, spec Sec.3.2) onto this same J{} instance.".format(ref),
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)


def _place_connector1(sch, refs):
    """Step 1, Connector 1: digital, per spec Sec.9.2's own confirmed split (AI16-31 +
    P0.8-31 + P2.0-7). This design's own 23 task-PC digital lines (19 out + 4 in, spec
    Sec.3.1) all ride here, on the P0.8-P0.30 range of the 24 available on P0.8-31 --
    leaving P0.31 and all 8 of P2.0-7 spare.

    Physical MDR68 pin assignment (pins "1".."23" for the 23 signals, "24"/"25" for two
    DGND references, everything else no_connect) is THIS SHEET'S OWN sequential choice,
    not sourced from an NI connector-pinout diagram: confirmed directly against
    gen_wl_sync_lib.py's own MDR68_Male docstring, no source anywhere in this project's
    spec or plan fixes which PHYSICAL SCSI/MDR pin carries which individual P0.x/P2.x
    line -- only the PORT-LEVEL (connector 0 vs 1) split is sourced (spec Sec.9.2). Same
    precedent as gen_mule.py's own task-PC header ("Task-PC side stays sequential...
    only the Pi side needs to match a real physical header" -- nothing on the task PC
    side of THIS board reads a contiguous hardware pin range either). Confirm against
    NI's own device pinout/SHC68-68-EPM cable documentation before this connector is
    cabled at commissioning -- same open-item class as hardware/README.md's own MDR68
    row-spacing/mounting-hole flags.
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
        "This sheet assigns Connector 1's own physical MDR68 pins 1-23 SEQUENTIALLY to",
        "these 23 lines (pin1=P0.8/EVT_D0 ... pin16=P0.23/EVT_D15, pin17=P0.24/STROBE,",
        "pin18=P0.25/RWD_CMD, pin19=P0.26/STIM_TRIG, pin20-23=P0.27-30/PD1_COMP,",
        "PD2_COMP,ACC_TRIG,RHS_STIM_OUT) -- this board's OWN choice, not yet confirmed",
        "against NI's SCSI/MDR pin-position diagram for the real SHC68-68-EPM cable;",
        "see this generator's own _place_connector1() docstring.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for i, (ch_idx, raw_net, _clamp, _pi, _buf) in enumerate(INBOUND_CHANNELS):
        pin_num = str(ch_idx + 1)
        x, y = pin_pos(X_CONN1, Y_CONN1, pins[pin_num])
        sch.label(raw_net, x, y)
        used.add(pin_num)

    for i, (_in_net, raw_out_net) in enumerate(OUTBOUND_CHANNELS):
        pin_num = str(20 + i)
        x, y = pin_pos(X_CONN1, Y_CONN1, pins[pin_num])
        sch.label(raw_out_net, x, y)
        used.add(pin_num)

    for gnd_pin in ("24", "25"):
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
    # STIM_TRIG _BUF (3 of 8) -- same channel grouping, input taps the SAME *_CLAMP net.
    buf_channels = [{}, {}, {}]
    for ch_idx, _raw, clamp_net, _pi, buf_net in INBOUND_CHANNELS:
        bank, local = divmod(ch_idx, 8)
        buf_channels[bank][local] = (clamp_net, buf_net)
    for bank_idx, (x, y) in enumerate([(X_HCTBUF, Y_HCTBUF1), (X_HCTBUF, Y_HCTBUF2), (X_HCTBUF, Y_HCTBUF3)]):
        place_octal_buffer(
            sch, "74xx", "74HCT541", "SN74HCT541PW", x, y, "+5V",
            buf_channels[bank_idx], refs, "hct541_buf", FOOTPRINT_TSSOP20,
        )


def _place_outbound(sch, refs):
    """Step 3: 4 outbound channels through one more SN74HCT541PW on +5V (4 of 8
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

    UNLIKE the power sheet's own permanent flags, these four are NOT meant to survive
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
    """
    channels = {i: (in_net, out_net) for i, (in_net, out_net) in enumerate(OUTBOUND_CHANNELS)}
    for flag_i, (in_net, _out_net) in enumerate(OUTBOUND_CHANNELS):
        # DELETE this call (only this call, not the rest of _place_outbound) once the
        # sheet that produces `in_net` for real (Task 10 for the first three, Task 11
        # for RHS_STIM_OUT) wires its own driving pin to it -- see the docstring above.
        sch.power_flag(in_net, GRID(X_OUTBUF - 40.64), GRID(Y_OUTBUF + flag_i * 5.08))
    place_octal_buffer(
        sch, "74xx", "74HCT541", "SN74HCT541PW", X_OUTBUF, Y_OUTBUF, "+5V",
        channels, refs, "outbound_hct541", FOOTPRINT_TSSOP20,
    )
    for line_idx, line in enumerate([
        "PD1_COMP/PD2_COMP/ACC_TRIG/RHS_STIM_OUT: the 4 PWR_FLAGs to the left of U7 are",
        "TEMPORARY -- they exist only because Task 10 (comparators) and Task 11 (opto-",
        "intan) have not been built yet. DELETE each one the moment its own real driver",
        "(LM339 open_collector output / optocoupler output) is wired to that net --",
        "left in place, a PWR_FLAG trips ERC's pin_to_pin rule against an",
        "open_collector driver on the same net (confirmed empirically, see",
        "_place_outbound()'s own docstring in gen_breakout_taskpc_digital.py).",
    ]):
        sch.text(line, GRID(X_OUTBUF - 40.64), GRID(Y_OUTBUF + 30 + line_idx * 5.08))


def _place_reward_or(sch, refs):
    """Step 4: RWD_CMD (already produced in stage 2) and the debounced manual reward
    button combine in a 74HCT32 OR gate to produce RWD_DLVR.

    RWD_BTN: a 2-pin panel momentary-button connector and a 2-pin remote-jack connector,
    wired in PARALLEL onto the same node (spec Sec.9.1's own "panel momentary button +
    remote jack, 1+1" -- either shorts the node to DGND when actuated), 10k pull-up to
    +5V, 100nF debounce cap to DGND -- exactly task-8-brief.md's own literal component
    list. Both connectors are Connector_Generic:Conn_01x02 placeholders (this project has
    no dedicated panel pushbutton/jack footprint yet, same status as the reward-driver
    BNC below -- flagged in this task's own report, not a scope this task's brief asks
    it to resolve, unlike the MDR68/M12A_5 connectors that DID get dedicated custom
    footprints because their panel-cutout tolerance is the tightest on the board).

    POLARITY, stated explicitly because it is not free of a real judgment call: the
    literal circuit above (pull-up to +5V, switch to DGND, two SERIES Schmitt inverters
    -- net non-inverting) makes the debounced signal read LOW while the button is held
    (idle HIGH via the pull-up). A plain 74HCT32 OR gate only produces a sensible
    "either-input-asserts" result if BOTH inputs share one active-level convention; this
    design's other event signals (EVT_STROBE, event data bits) are idle-LOW/pulse-HIGH,
    the standard DAQ/TTL convention, and RWD_CMD -- an unmodified copy of the task PC's
    own raw line, since SN74LVC541APW is a NON-inverting buffer -- almost certainly
    shares it. Implemented here exactly as specified (10k PULL-UP, not pull-down, and a
    Schmitt PAIR, not a single inverting stage) rather than silently "corrected" against
    an assumption this task cannot verify independently -- flagged on-sheet (see the
    text block below) and in this task's own report for confirmation against the task
    PC/MonkeyLogic's actual RWD_CMD polarity before commissioning.

    RWD_DLVR also drives a reward-driver BNC -- placeholder Conn_01x02 for the same
    reason as the button/jack above -- and is available by name (no further buffering
    needed: the OR gate already runs on +5V, the same native logic level Task 11's
    NI/Intan optocouplers consume elsewhere on this design, e.g. via the *_BUF bank
    above) for Task 11's opto-ni/opto-intan sheets to pick up directly.
    """
    btn_ref = sch.next_ref("J")
    btn_pins = sch.place(
        "Connector_Generic", "Conn_01x02", btn_ref,
        "Manual reward button (panel)", X_RWD_BTN, Y_RWD_BTN, footprint=FOOTPRINT_HDR1X02,
    )
    x, y = pin_pos(X_RWD_BTN, Y_RWD_BTN, btn_pins["1"])
    sch.label("RWD_BTN", x, y)
    x, y = pin_pos(X_RWD_BTN, Y_RWD_BTN, btn_pins["2"])
    sch.label("DGND", x, y)

    jack_ref = sch.next_ref("J")
    jack_pins = sch.place(
        "Connector_Generic", "Conn_01x02", jack_ref,
        "Remote reward jack (parallel to panel button)", X_RWD_JACK, Y_RWD_JACK,
        footprint=FOOTPRINT_HDR1X02,
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
        sch, "Device", "C", "C", "100nF", X_RWD_CAP, Y_RWD_CAP, "RWD_BTN", "DGND",
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
    # against the real output, so fixed here rather than left as a footgun.
    place_reward_or_gate(
        sch, X_U8, Y_U8, GATE_DY, "RWD_CMD", "RWD_BTN_DEB", "RWD_DLVR", "+5V", "DGND", refs,
    )
    place_debounce_inverters(
        sch, X_U9, Y_U9, GATE_DY, "RWD_BTN", "RWD_BTN_INV1", "RWD_BTN_DEB", "+5V", "DGND", refs,
    )

    bnc_ref = sch.next_ref("J")
    bnc_pins = sch.place(
        "Connector_Generic", "Conn_01x02", bnc_ref,
        "Reward driver out (BNC, placeholder)", X_RWD_BNC, Y_RWD_BNC, footprint=FOOTPRINT_HDR1X02,
    )
    x, y = pin_pos(X_RWD_BNC, Y_RWD_BNC, bnc_pins["1"])
    sch.label("RWD_DLVR", x, y)
    x, y = pin_pos(X_RWD_BNC, Y_RWD_BNC, bnc_pins["2"])
    sch.label("DGND", x, y)

    refs["reward_btn_hdr"] = btn_ref
    refs["reward_jack_hdr"] = jack_ref
    refs["reward_bnc_hdr"] = bnc_ref
    refs["reward_pullup_r"] = pullup_ref
    refs["reward_debounce_c"] = debounce_c_ref

    for line_idx, line in enumerate([
        "Reward OR polarity: RWD_BTN idles HIGH (10k pull-up to +5V), reads LOW while",
        "pressed. The 74HCT14 pair is NON-inverting (2 series Schmitt stages), so",
        "RWD_BTN_DEB also reads LOW while pressed. RWD_CMD is assumed idle-LOW/pulse-",
        "HIGH (this design's other event lines' convention, and SN74LVC541APW does not",
        "invert). VERIFY RWD_CMD's actual polarity against MonkeyLogic before",
        "commissioning: if RWD_CMD is active-HIGH as assumed, this OR gate is correct;",
        "if RWD_CMD instead turns out active-LOW, RWD_DLVR would read HIGH almost",
        "continuously instead of pulsing on delivery.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs) -- `refs` maps a role name to the reference designator(s)
    that play it, same convention every generator in this project follows.

    `ref_start` (kicad_sch.py's Sch, added at this task): this is this project's SECOND
    real child sheet, after power.kicad_sch -- the first time two sheets' own
    independently-started reference counters can actually collide (both start "J"/"R"/
    "C"/"D"/"U" at 1). Seeded here by reading power.kicad_sch's own already-committed
    text (find_max_refs()) -- not hard-coded, not trusted from any in-process value --
    same "read the real committed artifact" discipline instance_path (below) already
    follows for uuids. If Task 9-12's own generators need the same treatment, they
    should seed from EVERY already-committed sibling sheet's own maxima (this one
    included), not only power.kicad_sch.
    """
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, TASKPC_DIGITAL_SHEETFILE)
    ref_start = find_max_refs(POWER_SCH.read_text())

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    _place_connector0(sch, refs)
    _place_connector1(sch, refs)
    _place_inbound(sch, refs)
    _place_outbound(sch, refs)
    _place_reward_or(sch, refs)

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
