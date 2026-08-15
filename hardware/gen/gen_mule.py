"""Generator for hardware/mule/mule.kicad_sch -- the event-path mule schematic.

Implements task-2-brief.md (.superpowers/sdd/2026-08-13-breakout-pcb/task-2-brief.md):
Step 1 (inbound task-PC -> Pi, 17 lines through 74LVC541A on +3V3), Step 2 (outbound Pi ->
5V equipment through 74HCT541 on +5V), Step 3 (isolated path through two logic-output
optocouplers and an isolated DC-DC converter), Step 4 (power: barrel jack, LDO,
decoupling/bulk caps), as revised by coordinator fix round 1 (see task-2-report.md,
"Fix round 1" section, for the full rationale behind each change from the original
submission): logic-output optocouplers instead of phototransistor-output, a genuinely
bidirectional input clamp, and a Pi-side header that matches the real Pi 5 GPIO map.

Uses the label-per-pin technique from hardware/gen/kicad_sch.py: every component pin gets a
global label at its exact schematic coordinate, and two pins are on the same net iff their
labels have the same text. No wires are drawn or routed.

Run directly: `python3 hardware/gen/gen_mule.py` (writes hardware/mule/mule.kicad_pro and
hardware/mule/mule.kicad_sch; hardware/mule/sym-lib-table is a separate, hand-maintained
file per the KiCad "project sym-lib-table must sit beside .kicad_pro" constraint).

Judgment calls made resolving gaps/ambiguity in the brief are documented at the point each
one is made below and summarized in task-2-report.md.
"""
from pathlib import Path

from kicad_sch import Sch, pin_pos, write_project_stub

OUT = Path(__file__).resolve().parent.parent / "mule"

# ---------------------------------------------------------------------------
# Footprint assignments -- the single source of truth hardware/gen/gen_mule_pcb.py
# imports (via Sch.footprints, populated as a side effect of every place() call below)
# rather than re-deriving.
#
# Task 2's review flagged that several parts are placed via a stand-in lib_id/Value pair
# (74xx:74LS541 valued SN74LVC541APW; Regulator_Linear:AP1117-15 valued LD1117S33TR --
# see kicad_sch.py's module docstring, gotcha 4, for why: the real part's own KiCad symbol
# uses `(extends ...)` and carries no pin geometry). That stand-in's ki_fp_filters would
# suggest the WRONG package for the part actually being fitted -- 74LS541's filter is
# "DIP?20*" (its Value's real part, SN74LVC541APW, is TSSOP-20/"PW"); AP1117-15's own
# footprint happens to be right (SOT-223-3_TabPin2, confirmed pin-for-pin identical to
# LD1117S33TR's real datasheet pinout: GND/VOUT(tab)/VIN -- see task-3-report.md) but that's
# a coincidence of this specific pair, not something to rely on for the next stand-in.
# Every footprint below is picked from the part actually being ordered, checked against its
# own datasheet or KiCad's own footprint recommendation for that exact part (not the
# stand-in symbol's filters) -- see task-3-report.md for the checks performed per part.
FOOTPRINT_TSSOP20 = "Package_SO:Texas_PW0020A_TSSOP-20_4.4x6.5mm_P0.65mm"  # SN74LVC541APW/SN74HCT541PW -- TI's own "PW" package code is literally TSSOP-20
FOOTPRINT_DIP8 = "Package_DIP:DIP-8_W7.62mm"  # HCPL-4661 -- unsuffixed Broadcom part number defaults to through-hole DIP-8 (SO-8 variants carry a package suffix)
FOOTPRINT_SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"  # LD1117S33TR -- ST datasheet: pin1 GND, pin2 VOUT (=tab), pin3 VIN
FOOTPRINT_SOT23 = "Package_TO_SOT_SMD:SOT-23"  # BAT54S -- this IS the real part (no stand-in), so its own recommended footprint is trustworthy as-is
FOOTPRINT_SIP7 = "Converter_DCDC:Converter_DCDC_TRACO_TMA-05xxS_12xxS_Single_THT"  # TMA-0505S -- also the real part directly, no stand-in
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"  # 100nF decoupling/bypass
FOOTPRINT_C_BULK = "Capacitor_SMD:C_0805_2012Metric"  # 10uF bulk -- one size up from the 0603 floor for a comfortable hand-solder joint at this capacitance
# J1, J2 -- bare 2x20 pin header, not a shrouded IDC box header: its pad numbering is the
# same odd/even (row1 odd, row2 even) scheme Conn_02x20_Odd_Even already assumes, same as
# a shrouded header's would be, but its courtyard is 9.9x51.81mm versus the shrouded
# part's 9.9x59.46mm -- the difference that makes the board's height requirement (see
# hardware/mule/floorplan.md) 90mm instead of the ~100mm a shrouded header would force.
# A bare header is also mechanically what it's mating with: the mule's Pi-side connector
# reproduces the real Pi 5's own physical GPIO pin positions specifically so a standard
# Pi ribbon cable (built for a bare, unshrouded 40-pin header) can connect directly.
FOOTPRINT_IDC40 = "Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical"
FOOTPRINT_HDR1X04 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
FOOTPRINT_HDR1X02 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
FOOTPRINT_SCREW1X04 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-4_1x04_P5.00mm_Horizontal"
# CUI/Same Sky PJ-102AH -- re-confirmed pin mapping directly against the manufacturer's own
# current datasheet (sameskydevices.com/product/resource/pj-102ah.pdf, "SCHEMATIC" block,
# p.2): terminal 1 = sleeve, terminal 2 = tip (its PCB pad sits nearest the barrel's own
# center-pin axis in the recommended layout, terminals 1/3 are grouped together well away
# from it), terminal 3 = normally-closed power-detect switch (unused here, left NC). This
# matches Task 2's inference from the schematic symbol's own pin geometry exactly: pin 1
# (solid rectangle+arc shape) = sleeve = DGND, pin 2 (zigzag spring-contact shape) = tip =
# +5V. Footprint pad 3 (the switch) simply carries no net below -- a real, unused pad, not
# an error.
FOOTPRINT_BARRELJACK = "Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal"

# ---------------------------------------------------------------------------
# Layout grid -- every coordinate must be an exact multiple of 1.27mm (the KiCad
# schematic connection grid), or a pin landing on a non-grid point produces an
# `endpoint_off_grid` ERC warning even though the label-based net is still
# correct. GRID() snaps any convenient round-mm value to the nearest valid one,
# so the numbers below can stay readable instead of hand-picked multiples.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


ROW0 = 50.8
ROW_PITCH = 10.16  # 17 channel rows: 50.8 .. 213.36

X_TPC_HDR = 15.24
X_R_IN = 76.2
X_CLAMP = 106.68
X_IC541 = 172.72
X_PI_HDR = 289.56

Y_U1 = 60.96   # SN74LVC541APW #1: EVT_D0..D7
Y_U2 = 137.16  # SN74LVC541APW #2: EVT_D8..D15
Y_U3 = 213.36  # SN74LVC541APW #3: EVT_STROBE (1 of 8 channels used)
Y_U4 = 289.56  # SN74HCT541PW: BARCODE (1 of 8 channels used), +5V domain

X_BARCODE_HDR = 386.08
Y_OUTBOUND = 289.56

X_OPTO = 172.72
Y_OPTO = GRID(360)
OPTO_PKG_PITCH = GRID(30.48)  # vertical spacing between the two HCPL-4661 packages
X_DCDC = 259.08
Y_DCDC = GRID(360)
X_SCREW = 320.04
Y_SCREW = GRID(360)
X_SPARE_HDR = GRID(100)
Y_SPARE = GRID(360)
X_ISO_TP = 320.04
Y_ISO_TP = GRID(390)

X_PWR = 15.24
Y_PWR = GRID(430)
X_BARREL = 15.24
Y_BARREL = GRID(460)
X_LDO = 60.96
Y_LDO = GRID(460)
X_CAPS = GRID(100)
Y_CAPS = GRID(460)
DECOUPLE_DX = GRID(20)
SPARE_HDR_DY = GRID(20)

X_GPIO01_R = GRID(231.14)  # GPIO0/1 boot-contention series resistors, between IC and header
Y_GPIO01_NOTE = GRID(30)

CHAN_A = {i: str(2 + i) for i in range(8)}   # 74LS541 unit-1 pin numbers: A0..A7
CHAN_Y = {i: str(18 - i) for i in range(8)}  # ...and Y0..Y7, paired by channel: Ai <-> Yi

# Raspberry Pi 40-pin header: physical pin number for each BCM GPIO, cross-checked against
# gpiozero's own pin-data table (not assumed) -- PIO parallel capture reads a *contiguous*
# GPIO range (GPIO0-15 data, GPIO16 strobe, per the plan's global constraints), so the
# bring-up PIO-capture test can only run against a header that presents these signals at
# their real physical positions, not a sequentially-numbered one.
GPIO_PHYSICAL_PIN = {
    0: 27, 1: 28, 2: 3, 3: 5, 4: 7, 5: 29, 6: 31, 7: 26,
    8: 24, 9: 21, 10: 19, 11: 23, 12: 32, 13: 33, 14: 8, 15: 10,
}
STROBE_GPIO_PHYSICAL_PIN = 36  # GPIO16
BARCODE_GPIO_PHYSICAL_PIN = 11  # GPIO17 -- named explicitly in the brief's own Step 2 text
# FIX ROUND 3 (IMPORTANT 2): all 8 real Pi GND positions, not just 2. The real board bonds
# every ground the Pi header provides; a mule that bonds only 2 of 8 doesn't produce
# crosstalk/edge-quality measurements that transfer to the real board, which is the
# board's entire purpose. Disjoint from every signal pin in GPIO_PHYSICAL_PIN above.
PI_GND_PHYSICAL_PINS = (6, 9, 14, 20, 25, 30, 34, 39)
GPIO0_1_SERIES_OHMS = "330"  # exact value from the plan's Task 9 Step 2, reused here


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    """Place a 2-pin part (Device:R or Device:C) between two labeled nets. Returns its
    reference designator so callers building up build()'s `refs` map (consumed by
    hardware/gen/gen_mule_pcb.py to know which physical part plays which role, rather than
    guessing from reference numbers) can record it."""
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    x1, y1 = pin_pos(x, y, pins["1"])
    sch.label(net1, x1, y1)
    x2, y2 = pin_pos(x, y, pins["2"])
    sch.label(net2, x2, y2)
    return ref


def bidirectional_clamp(sch, x, y, net_signal, net_hi, net_lo):
    """Place a BAT54S wired as a genuine two-rail bidirectional clamp.

    FIX ROUND 1 (coordinator-directed): the original submission used Diode:BAV99 wired as
    a doubled low-side-only clamp, on the grounds that BAV99 is common-anode (pin 2 "A"
    the shared anode, pins 1 and 3 both independently named "K" -- confirmed directly from
    the raw symbol text) and a common-anode pair cannot bidirectionally clamp one signal
    to two different rails using both diodes usefully: tying the shared anode to the
    signal makes one diode a correct high-side clamp but forces the other to conduct on
    every ordinary logic-high (anode=signal, cathode=DGND conducts whenever signal is
    above roughly one Schottky/silicon drop). The coordinator's review was that
    low-side-only protection inverts the mule's own safety argument, and that a
    correctly-wired series pair does this with one part. Re-checked BAV99 once more and it
    still isn't a series pair: a genuine series midpoint pin, like BAV99S's shared nodes,
    is named with both roles concatenated (e.g. "K1A2"); BAV99's pin 2 carries only "A".
    Diode:BAT54S is: pin 1 "A" (anode only), pin 2 "K" (cathode only), pin 3 "COM",
    explicitly the shared node -- KiCad's own description for the part reads "Dual
    schottky barrier diode, in series". That makes D1 = anode(pin1) -> cathode(pin3=COM)
    and D2 = anode(pin3=COM) -> cathode(pin2): wiring pin1(A)->DGND, pin3(COM)->signal,
    pin2(K)->[net_hi] gives D1 conducting when DGND exceeds signal by a diode drop (the
    low-side clamp) and D2 conducting when signal exceeds [net_hi] by a diode drop (the
    high-side clamp) -- both diodes doing correctly-oriented work, a real two-rail clamp
    from one part.

    FIX ROUND 2 (coordinator-directed, correcting fix round 1's own high-side target):
    fix round 1 wired net_hi to +3V3, on the coordinator's own then-instruction, and
    flagged as a concern that a Schottky's low forward drop (~0.3-0.4V) meant the
    high-side diode would conduct on every normal 5V logic-high, not just a fault --
    sinking ~10-15mA per line into the regulated 3.3V rail (up to ~230mA worst case
    across all 17 lines), continuously, since 5V (the signal's normal high level) sits
    well above 3.3V+Vf. That flagged concern turned out to be the actual bug: SN74LVC541A
    is chosen specifically because its inputs tolerate 5.5V independent of its own 3.3V
    supply -- clamping to +3V3 destroys exactly the property the part was chosen for, and
    permanently, on every code the task PC emits, not just during a fault. The fix is to
    reference the high-side clamp to +5V instead: net_hi is now "+5V", the rail the
    signal's normal high legitimately swings to, so D2 sees ~0V of forward bias in normal
    operation (signal and +5V both nominally 5V) and does not conduct -- only leakage
    current, microamps, confirmed by direct calculation rather than assumed (see
    task-2-report.md, "Fix round 2" section, for the arithmetic). +5V + Vf still lands
    around 5.3-5.4V, comfortably under the LVC541A's 6.5V absolute-maximum input rating,
    so a genuine overvoltage fault beyond the signal's normal range is still clamped.
    """
    ref = sch.next_ref("D")
    pins = sch.place("Diode", "BAT54S", ref, "BAT54S", x, y, footprint=FOOTPRINT_SOT23)
    px, py = pin_pos(x, y, pins["1"])  # anode only -> low rail
    sch.label(net_lo, px, py)
    px, py = pin_pos(x, y, pins["3"])  # COM, the genuine series midpoint -> signal
    sch.label(net_signal, px, py)
    px, py = pin_pos(x, y, pins["2"])  # cathode only -> high rail
    sch.label(net_hi, px, py)
    return ref


def place_541(sch, value, x, y, rail, channels: dict[int, tuple[str, str]]):
    """Place one 74LS541-bodied octal buffer (standing in for the real 541-family part
    named in `value` -- see task-2-report.md, "library gap" judgment call). `channels`
    maps channel index (0-7) -> (a_net, y_net) for USED channels; channels not present in
    the dict are tied off safely (input -> DGND, output -> no-connect). G1/G2 (output
    enable, active low) are tied to DGND on every instance so the buffer is permanently
    enabled; no tri-state control is needed on this board.
    """
    ref = sch.next_ref("U")
    pins = sch.place(
        "74xx", "74LS541", ref, value, x, y,
        footprint=FOOTPRINT_TSSOP20,
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
        sch.label("DGND", px, py)
    gx, gy = pin_pos(x, y, pins["10"])
    sch.label("DGND", gx, gy)
    vx, vy = pin_pos(x, y, pins["20"])
    sch.label(rail, vx, vy)
    # 100nF decoupling right at the IC, per brief step 4.
    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, "DGND", footprint=FOOTPRINT_C_SMALL)
    return ref, cap_ref


def header_pin(sch, pins, hdr_x, hdr_y, num, net):
    x, y = pin_pos(hdr_x, hdr_y, pins[num])
    sch.label(net, x, y)


def header_nc_remaining(sch, pins, hdr_x, hdr_y, used_numbers):
    for num, pin in pins.items():
        if num not in used_numbers:
            x, y = pin_pos(hdr_x, hdr_y, pin)
            sch.no_connect(x, y)


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs). `refs` maps a role name to the reference designator(s) that
    play it -- hardware/gen/gen_mule_pcb.py's single source of truth for "which physical
    part is this", so the PCB layout never has to guess a part's role from its bare
    reference number (fragile: reference numbers are an artifact of generation order, not
    a stable contract) or hand-duplicate the placement order above."""
    sch = Sch(project="mule")
    refs: dict = {
        "r_in": [], "clamp_diode": [], "gpio01_series_r": [],
        "buffers_3v3": [], "buffer_3v3_decouple_c": [],
        "opto_pkg": [], "opto_led_r": [], "opto_pullup_r": [], "opto_bypass_c": [],
        "spare_header": [], "dcdc_bypass_c": [], "bulk_c": [],
    }

    # === Step 1: inbound path, task PC -> Pi (17 lines) =====================
    refs["tpc_header"] = sch.next_ref("J")
    tpc_pins = sch.place(
        "Connector_Generic", "Conn_02x20_Odd_Even", refs["tpc_header"],
        "IDC-40 task-PC event bus", X_TPC_HDR, ROW0 + 8 * ROW_PITCH,
        footprint=FOOTPRINT_IDC40,
    )
    refs["pi_header"] = sch.next_ref("J")
    pi_pins = sch.place(
        "Connector_Generic", "Conn_02x20_Odd_Even", refs["pi_header"],
        "IDC-40 Pi event bus", X_PI_HDR, ROW0 + 8 * ROW_PITCH,
        footprint=FOOTPRINT_IDC40,
    )
    tpc_used, pi_used = set(), set()

    # JUDGMENT CALL: 3x SN74LVC541APW, not 2x. The brief's parenthetical ("two packages:
    # 16 data + strobe = 17 channels of 20 available across two devices") does not
    # reconcile: SN74LVC541APW is an octal (8-channel) buffer, so two packages provide
    # only 16 channels -- one short of the 17 lines the same step requires, and the
    # shortfall is exactly the strobe. Since EVT_STROBE must go through the same buffer
    # family as the data lines (propagation-delay matching against a parallel PIO
    # capture on the Pi depends on it -- a different buffer family for strobe alone would
    # skew data-vs-strobe timing), a third package carrying just the strobe channel is
    # the only way to keep every one of the 17 lines genuinely buffered. See
    # task-2-report.md for the full reasoning.
    ic_specs = [
        (Y_U1, 0, 8, "D"),    # U1: EVT_D0_TPC..D7_TPC
        (Y_U2, 8, 8, "D"),    # U2: EVT_D8_TPC..D15_TPC
        (Y_U3, 16, 1, "STROBE"),  # U3: EVT_STROBE_TPC only; 7 channels unused
    ]
    # FIX ROUND 1: GPIO0/GPIO1 (physical pins 27/28) need an extra 330R series resistor
    # between the buffer and the physical Pi pin -- the Pi probes those two pins as I2C at
    # boot looking for a HAT ID EEPROM, and a low-impedance buffer output driving them
    # contends with that probe (plan doc, Task 9 Step 2, same technique reused here). That
    # makes the buffer's own output, for exactly these 2 of 17 lines, a distinct net from
    # what reaches the header: EVT_D{0,1}_PI is defined as arriving at the Pi (consistent
    # with what it means for the other 15 lines), so the buffer output there gets its own
    # non-contract net name and the resistor bridges the two.
    def buffer_output_net(i, data_name, pi_net):
        return f"{data_name}_BUF" if i in (0, 1) else pi_net

    for y_ic, base_i, count, _kind in ic_specs:
        channels = {}
        for local_ch in range(count):
            i = base_i + local_ch
            data_name = f"EVT_D{i}" if i < 16 else "EVT_STROBE"
            pi_net = f"{data_name}_PI"
            channels[local_ch] = (f"{data_name}_CLAMP", buffer_output_net(i, data_name, pi_net))
        buf_ref, buf_cap_ref = place_541(sch, "SN74LVC541APW", X_IC541, y_ic, "+3V3", channels)
        refs["buffers_3v3"].append(buf_ref)
        refs["buffer_3v3_decouple_c"].append(buf_cap_ref)

    for i in range(17):
        y = ROW0 + i * ROW_PITCH
        data_name = f"EVT_D{i}" if i < 16 else "EVT_STROBE"
        tpc_net = f"{data_name}_TPC"
        clamp_net = f"{data_name}_CLAMP"
        pi_net = f"{data_name}_PI"

        r_in_ref = two_pin(sch, "Device", "R", "R", "100", X_R_IN, y, tpc_net, clamp_net, footprint=FOOTPRINT_R)
        refs["r_in"].append(r_in_ref)
        # FIX ROUND 2: high side clamps to +5V, not +3V3 -- see bidirectional_clamp()'s
        # docstring. +3V3 sits below the signal's normal 5V high, so a clamp referenced
        # to it would conduct continuously in normal operation, not just on a fault.
        clamp_ref = bidirectional_clamp(sch, X_CLAMP, y, clamp_net, "+5V", "DGND")
        refs["clamp_diode"].append(clamp_ref)

        # Task-PC side stays sequential (coordinator fix round 1: accepted as-is -- only
        # the Pi side needs to match a real physical header, since nothing on the task-PC
        # side reads a contiguous hardware pin range the way the Pi's PIO capture does).
        tpc_hdr_num = str(i + 1)
        header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, tpc_hdr_num, tpc_net)
        tpc_used.add(tpc_hdr_num)

        # Pi side: real physical GPIO position, not sequential (see GPIO_PHYSICAL_PIN).
        pi_hdr_num = str(GPIO_PHYSICAL_PIN[i] if i < 16 else STROBE_GPIO_PHYSICAL_PIN)
        header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, pi_hdr_num, pi_net)
        pi_used.add(pi_hdr_num)

        if i in (0, 1):
            buf_net = buffer_output_net(i, data_name, pi_net)
            gpio_r_ref = two_pin(
                sch, "Device", "R", "R", GPIO0_1_SERIES_OHMS, X_GPIO01_R, y, buf_net, pi_net,
                footprint=FOOTPRINT_R,
            )
            refs["gpio01_series_r"].append(gpio_r_ref)

    for line_idx, line in enumerate([
        "GPIO0/GPIO1 -- physical pins 27/28 -- are probed by the Pi as I2C at boot,",
        "looking for a HAT ID EEPROM. The 330R series resistors above limit contention",
        "with that probe. Requires force_eeprom_read=0 in config.txt.",
    ]):
        sch.text(line, X_GPIO01_R, Y_GPIO01_NOTE + line_idx * GRID(4))

    # A couple of DGND reference pins per header (return-path reference for the ribbon
    # cable), plus BARCODE_PI inbound on the Pi header at its real physical position
    # (GPIO17 -- named explicitly in the brief's own Step 2 text). Every other pin of both
    # 40-pin headers is explicitly no-connected below -- this mule only exercises 18 of
    # the 40 Pi-header signals, and leaving the rest genuinely floating would be an
    # unlabelled, ERC-ambiguous "maybe I forgot this" rather than a documented choice.
    header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, "18", "DGND")
    header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, "19", "DGND")
    tpc_used |= {"18", "19"}
    header_pin(
        sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH,
        str(BARCODE_GPIO_PHYSICAL_PIN), "BARCODE_PI",
    )
    pi_used.add(str(BARCODE_GPIO_PHYSICAL_PIN))
    for gnd_pin in PI_GND_PHYSICAL_PINS:
        header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, str(gnd_pin), "DGND")
        pi_used.add(str(gnd_pin))

    header_nc_remaining(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, tpc_used)
    header_nc_remaining(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, pi_used)

    # === Step 2: outbound path, Pi -> 5V equipment ===========================
    refs["buffer_5v"], refs["buffer_5v_decouple_c"] = place_541(
        sch, "SN74HCT541PW", X_IC541, Y_U4, "+5V",
        {0: ("BARCODE_PI", "BARCODE_OUT")},
    )
    refs["barcode_header"] = sch.next_ref("J")
    barcode_hdr_pins = sch.place(
        "Connector_Generic", "Conn_01x04", refs["barcode_header"],
        "BARCODE_OUT test points", X_BARCODE_HDR, Y_OUTBOUND,
        footprint=FOOTPRINT_HDR1X04,
    )
    for num in barcode_hdr_pins:
        x, y = pin_pos(X_BARCODE_HDR, Y_OUTBOUND, barcode_hdr_pins[num])
        # JUDGMENT CALL: "four test outputs" (brief step 2) but only one locked net name
        # (BARCODE_OUT, singular, in the net-naming contract). Implemented as four
        # physical probe points on the SAME net -- room for scope + DMM + logic analyzer
        # simultaneously without a splitter -- rather than four separately-driven buffer
        # channels tied together (which would parallel four active push-pull outputs on
        # one node). See task-2-report.md.
        sch.label("BARCODE_OUT", x, y)

    # === Step 3: isolated path ===============================================
    # FIX ROUND 1 (coordinator-directed): switched from PC847 (phototransistor output) to
    # Isolator:HCPL-4661 (logic/open-collector output). The mule's bring-up procedure
    # measures optocoupler propagation delay and strobe edge quality (checks 5-6); a
    # phototransistor's edge rate depends on external load and on CTR, which drifts as the
    # LED ages, so measuring one would validate a part class the real board (Task 11) does
    # not use. HCPL-4661 is a real logic-output part in KiCad's stock Isolator library.
    #
    # CORRECTION flagged rather than silently applied: HCPL-4661 is a 2-channel part, not
    # quad -- checked directly (Mouser/TME/RS-Online listings all say "Ch: 2" / "2-Channel",
    # and the physical pin budget confirms it: an 8-pin DIP is 4 LED pins + 2
    # open-collector outputs + VCC + GND, which is exactly a dual-channel part's pin count
    # and too few pins to fit 4 independent channels). Implemented as two HCPL-4661
    # packages, two channels each = four channels total, rather than inventing a quad
    # variant that doesn't exist or substituting a different vendor/output type without
    # checking first.
    #
    # HCPL-4661 (Isolator:HCPL-4661 in KiCad's stock library) extends Isolator:HCPL-263A
    # (its 8-pin dual-channel base graphic) and carries no pins of its own -- placed via
    # that root symbol per the extends-avoidance pattern (hardware/README.md gotcha 4),
    # Value overridden to the real part number.
    #
    # JUDGMENT CALL (unchanged from the original submission): TMA-0505S, not TMA-0512D.
    # The brief names "TMA0512D-class" but that literal part is 5V-in / +-12V-out (dual),
    # which cannot produce a rail actually named ISO_5V (a locked, contract net name).
    # TMA-0505S is the same Traco SIP7 family, 5V in / 5V out, single output -- the family
    # member that actually matches the required net name. "-class" in the brief's own
    # wording licenses picking the matching family member rather than the literal,
    # voltage-mismatched part number.
    # FIX ROUND 3 (IMPORTANT 1): per-channel LED series resistor, not one 330R for all
    # four. The strobe channel is driven from a 3.3V LVC541A output and the barcode
    # channel from a 5V HCT541 output; 330R on both gives roughly 4.8mA vs 10mA of LED
    # drive (see task-2-report.md, "Fix round 3" for the exact arithmetic) -- HCPL-4661's
    # recommended minimum I_F(on) is ~6.3mA, so the 3.3V-driven channel sits below spec,
    # and this family's propagation delay is strongly I_F-dependent, so the two
    # channels being measured (bring-up checks 5-6) would differ ~2x in drive and neither
    # number would transfer cleanly to the real board. 160R on the two 3.3V-driven
    # channels targets the same ~10mA as the 5V-driven channels get from 330R. The two
    # spare (bench-injection) channels keep 330R: their drive voltage is whatever the
    # bench injects, unknown at generation time, and 330R is the safer default against an
    # unexpectedly-high injected voltage than a resistor sized for 3.3V would be.
    OPTO_LED_PINS = {1: ("1", "2"), 2: ("4", "3")}  # channel -> (anode pin, cathode pin)
    OPTO_VO_PIN = {1: "7", 2: "6"}  # channel -> open-collector output pin
    opto_channels = [
        ("EVT_STROBE_PI", "EVT_STROBE_ISO", "1", "160"),   # 3.3V-driven (LVC541A)
        ("BARCODE_OUT", "BARCODE_ISO", "2", "330"),          # 5V-driven (HCT541)
        ("OPTO_SPARE1_IN", "OPTO_SPARE1_ISO", "3", "330"),   # bench-injected, unknown V
        ("OPTO_SPARE2_IN", "OPTO_SPARE2_ISO", "4", "330"),
    ]
    refs["screw_terminal"] = sch.next_ref("J")
    screw_pins = sch.place(
        "Connector", "Screw_Terminal_01x04", refs["screw_terminal"],
        "Isolated-domain outputs", X_SCREW, Y_SCREW,
        footprint=FOOTPRINT_SCREW1X04,
    )
    for pkg in range(2):  # two physical HCPL-4661 packages, two channels each
        pkg_y = Y_OPTO + pkg * OPTO_PKG_PITCH
        pkg_ref = sch.next_ref("U")
        refs["opto_pkg"].append(pkg_ref)
        pkg_pins = sch.place(
            "Isolator", "HCPL-263A", pkg_ref, "HCPL-4661", X_OPTO, pkg_y,
            footprint=FOOTPRINT_DIP8,
        )
        for ch in (1, 2):
            src_net, iso_net, screw_num, led_r_ohms = opto_channels[pkg * 2 + (ch - 1)]
            a_num, c_num = OPTO_LED_PINS[ch]
            vo_num = OPTO_VO_PIN[ch]
            led_net = f"{pkg_ref}_LED{ch}"
            ch_dy = -2.54 if ch == 1 else 2.54  # keep the 2 channels' passives from overlapping

            led_r_ref = two_pin(
                sch, "Device", "R", "R", led_r_ohms,
                X_OPTO - 30.48, pkg_y + ch_dy, src_net, led_net,
                footprint=FOOTPRINT_R,
            )
            refs["opto_led_r"].append(led_r_ref)
            ax, ay = pin_pos(X_OPTO, pkg_y, pkg_pins[a_num])
            sch.label(led_net, ax, ay)
            cx, cy = pin_pos(X_OPTO, pkg_y, pkg_pins[c_num])
            sch.label("DGND", cx, cy)

            # 1k pull-up, not the original 4k7: HCPL-4661 is a 10Mbd-class logic output: a
            # lower pull-up gives a faster, more representative edge for exactly the
            # propagation-delay/edge-quality measurement this fix is about.
            pullup_ref = two_pin(
                sch, "Device", "R", "R", "1k", X_OPTO + 30.48, pkg_y + ch_dy, "ISO_5V", iso_net,
                footprint=FOOTPRINT_R,
            )
            refs["opto_pullup_r"].append(pullup_ref)
            vox, voy = pin_pos(X_OPTO, pkg_y, pkg_pins[vo_num])
            sch.label(iso_net, vox, voy)

            header_pin(sch, screw_pins, X_SCREW, Y_SCREW, screw_num, iso_net)

        gx, gy = pin_pos(X_OPTO, pkg_y, pkg_pins["5"])
        sch.label("ISO_GND", gx, gy)
        vccx, vccy = pin_pos(X_OPTO, pkg_y, pkg_pins["8"])
        sch.label("ISO_5V", vccx, vccy)
        # CRITICAL fix (coordinator review): 100nF local bypass between VCC (pin 8) and
        # GND (pin 5), per the HCPL-4661 datasheet -- without it, an open-collector logic
        # optocoupler is prone to output chatter on transitions, which would directly
        # compromise the propagation-delay/edge-quality measurement this board exists to
        # take (bring-up checks 5-6), and Task 11 reuses this topology verbatim. Placed
        # right next to the package in the schematic; Task 3 (layout) needs to keep it
        # within ~7mm of the package on the real board, same as the datasheet asks.
        opto_bypass_ref = two_pin(
            sch, "Device", "C", "C", "100nF", X_OPTO, pkg_y + 20.32, "ISO_5V", "ISO_GND",
            footprint=FOOTPRINT_C_SMALL,
        )
        refs["opto_bypass_c"].append(opto_bypass_ref)

    for spare_idx in (1, 2):
        spare_ref = sch.next_ref("J")
        refs["spare_header"].append(spare_ref)
        hp = sch.place(
            "Connector_Generic", "Conn_01x02", spare_ref,
            f"Opto spare {spare_idx} bench injection", X_SPARE_HDR,
            Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY,
            footprint=FOOTPRINT_HDR1X02,
        )
        x1, y1 = pin_pos(X_SPARE_HDR, Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY, hp["1"])
        sch.label(f"OPTO_SPARE{spare_idx}_IN", x1, y1)
        x2, y2 = pin_pos(X_SPARE_HDR, Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY, hp["2"])
        sch.label("DGND", x2, y2)

    refs["dcdc"] = sch.next_ref("U")
    dcdc_pins = sch.place(
        "Converter_DCDC_Isolated", "TMA-0505S", refs["dcdc"], "TMA-0505S",
        X_DCDC, Y_DCDC,
        footprint=FOOTPRINT_SIP7,
    )
    dcdc_map = {"1": "+5V", "2": "DGND", "4": "ISO_GND", "6": "ISO_5V"}
    for num, net in dcdc_map.items():
        x, y = pin_pos(X_DCDC, Y_DCDC, dcdc_pins[num])
        sch.label(net, x, y)
    # CRITICAL fix (coordinator review, brief Step 4's "100nF per IC" -- U7 had none):
    # local bypass on both the DC-DC's input and output sides, right at the package, in
    # addition to the 10uF bulk caps already placed elsewhere on +5V/ISO_5V.
    dcdc_in_bypass_ref = two_pin(
        sch, "Device", "C", "C", "100nF", X_DCDC, Y_DCDC - 20.32, "+5V", "DGND",
        footprint=FOOTPRINT_C_SMALL,
    )
    dcdc_out_bypass_ref = two_pin(
        sch, "Device", "C", "C", "100nF", X_DCDC, Y_DCDC + 20.32, "ISO_5V", "ISO_GND",
        footprint=FOOTPRINT_C_SMALL,
    )
    refs["dcdc_bypass_c"] = [dcdc_in_bypass_ref, dcdc_out_bypass_ref]

    refs["iso_tp"] = sch.next_ref("J")
    iso_tp_pins = sch.place(
        "Connector_Generic", "Conn_01x02", refs["iso_tp"],
        "ISO_5V/ISO_GND test point", X_ISO_TP, Y_ISO_TP,
        footprint=FOOTPRINT_HDR1X02,
    )
    x1, y1 = pin_pos(X_ISO_TP, Y_ISO_TP, iso_tp_pins["1"])
    sch.label("ISO_5V", x1, y1)
    x2, y2 = pin_pos(X_ISO_TP, Y_ISO_TP, iso_tp_pins["2"])
    sch.label("ISO_GND", x2, y2)

    # === Step 4: power ========================================================
    refs["jack"] = sch.next_ref("J")
    jack_pins = sch.place(
        "Connector", "Barrel_Jack", refs["jack"], "+5V DC in", X_BARREL, Y_BARREL,
        footprint=FOOTPRINT_BARRELJACK,
    )
    # Pin/contact mapping read from the symbol's own graphic, not assumed: pin 1's lead
    # (at y=+2.54) traces to the solid rectangle+arc shape (the sleeve/ring contact);
    # pin 2's lead (at y=-2.54) traces to the zigzag spring-contact shape (the tip
    # contact). Standard 5.5/2.1mm DC barrel convention is center-positive, so tip (pin
    # 2) is +5V and sleeve (pin 1) is DGND.
    x1, y1 = pin_pos(X_BARREL, Y_BARREL, jack_pins["1"])  # sleeve
    sch.label("DGND", x1, y1)
    x2, y2 = pin_pos(X_BARREL, Y_BARREL, jack_pins["2"])  # tip
    sch.label("+5V", x2, y2)

    refs["ldo"] = sch.next_ref("U")
    ldo_pins = sch.place(
        "Regulator_Linear", "AP1117-15", refs["ldo"], "LD1117S33TR", X_LDO, Y_LDO,
        footprint=FOOTPRINT_SOT223,
    )
    ldo_map = {"3": "+5V", "1": "DGND", "2": "+3V3"}  # VI, GND, VO
    for num, net in ldo_map.items():
        x, y = pin_pos(X_LDO, Y_LDO, ldo_pins[num])
        sch.label(net, x, y)
    refs["ldo_decouple_c"] = two_pin(
        sch, "Device", "C", "C", "100nF", X_LDO, Y_LDO + DECOUPLE_DX, "+3V3", "DGND",
        footprint=FOOTPRINT_C_SMALL,
    )

    refs["bulk_c"].append(
        two_pin(sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS, "+5V", "DGND", footprint=FOOTPRINT_C_BULK)
    )
    refs["bulk_c"].append(two_pin(
        sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS + DECOUPLE_DX, "+3V3", "DGND",
        footprint=FOOTPRINT_C_BULK,
    ))
    refs["bulk_c"].append(two_pin(
        sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS + 2 * DECOUPLE_DX,
        "ISO_5V", "ISO_GND", footprint=FOOTPRINT_C_BULK,
    ))

    # Power-driven assertions for ERC (see kicad_sch.Sch.power_flag docstring) -- only for
    # +5V and DGND. +3V3, ISO_5V and ISO_GND are each already driven by a genuine
    # power_out pin (the LDO's VO, and the isolated DC-DC's +Vout/-Vout respectively);
    # adding a PWR_FLAG there too trips ERC's `pin_to_pin` rule ("Power output and Power
    # output are connected") -- two active power_out pins on one net is exactly the
    # pattern that check exists to catch, so a redundant flag isn't safety margin, it's
    # a false positive. +5V (barrel jack, a passive connector pin) and DGND (every GND
    # pin on the board is power_in) have no such natural driver and still need the flag.
    sch.power_flag("+5V", X_PWR, Y_PWR)
    sch.power_flag("DGND", X_PWR + GRID(15.24), Y_PWR)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "mule.kicad_sch"
    sch_path.write_text(sch.render())
    write_project_stub(OUT / "mule.kicad_pro")
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
