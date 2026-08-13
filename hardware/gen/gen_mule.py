"""Generator for hardware/mule/mule.kicad_sch -- the event-path mule schematic.

Implements task-2-brief.md (.superpowers/sdd/2026-08-13-breakout-pcb/task-2-brief.md):
Step 1 (inbound task-PC -> Pi, 17 lines through 74LVC541A on +3V3), Step 2 (outbound Pi ->
5V equipment through 74HCT541 on +5V), Step 3 (isolated path through a quad optocoupler and
an isolated DC-DC converter), Step 4 (power: barrel jack, LDO, decoupling/bulk caps).

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
X_BAV = 106.68
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

CHAN_A = {i: str(2 + i) for i in range(8)}   # 74LS541 unit-1 pin numbers: A0..A7
CHAN_Y = {i: str(18 - i) for i in range(8)}  # ...and Y0..Y7, paired by channel: Ai <-> Yi


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2):
    """Place a 2-pin part (Device:R or Device:C) between two labeled nets."""
    pins = sch.place(libname, symname, sch.next_ref(ref_prefix), value, x, y)
    x1, y1 = pin_pos(x, y, pins["1"])
    sch.label(net1, x1, y1)
    x2, y2 = pin_pos(x, y, pins["2"])
    sch.label(net2, x2, y2)


def bav99_low_side_clamp(sch, x, y, net_signal, net_gnd):
    """Place a BAV99 wired as a doubled low-side (undershoot) clamp.

    JUDGMENT CALL (see task-2-report.md for the full derivation): the stock BAV99 symbol
    (Diode:BAV99) is a common-anode pair -- pin 2 "A" is the shared anode, pins 1 and 3
    are two independent cathodes both named "K" (confirmed from the raw symbol text, and
    cross-checked against Diode:BAV99S, whose *genuinely* dual-role pins are named with
    both roles concatenated, e.g. "K1A2" -- plain BAV99's pin 2 carries only "A", meaning
    it is not a dual-role series node). A common-anode pair cannot bidirectionally clamp a
    single signal to two different rails using both diodes usefully: tying the shared
    anode to the signal makes the "high-side" diode correct but forces the other diode to
    conduct on every ordinary logic-high (anode=signal, cathode=DGND conducts whenever
    signal > ~0.7V), and tying the shared anode to +3V3 makes one diode a permanent short
    across the supply if its cathode ever reaches DGND. The only non-broken uses are
    single-direction: shared anode -> DGND with both cathodes -> signal (doubled
    undershoot/ESD clamp, used here), or shared anode -> signal with one cathode -> a rail
    (single-direction, wastes the other diode). This design uses the doubled low-side
    form and relies on SN74LVC541APW's datasheet-specified 5.5V input tolerance (the
    documented reason that part was chosen at all -- see hardware/README.md and the
    global constraints) for high-side protection, rather than hard-clamping to +3V3,
    which would otherwise sink a continuous ~10mA per line into the regulated 3.3V rail
    every time the task PC legitimately drives a normal 5V logic-high (backfeeding a
    rail an LDO cannot sink is worse than the undershoot case this clamp is for).
    """
    pins = sch.place("Diode", "BAV99", sch.next_ref("D"), "BAV99", x, y)
    for num in ("1", "3"):  # both cathodes -> signal (paralleled, doubled current capacity)
        px, py = pin_pos(x, y, pins[num])
        sch.label(net_signal, px, py)
    px, py = pin_pos(x, y, pins["2"])  # shared anode -> DGND
    sch.label(net_gnd, px, py)


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
    two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, "DGND")
    return ref


def header_pin(sch, pins, hdr_x, hdr_y, num, net):
    x, y = pin_pos(hdr_x, hdr_y, pins[num])
    sch.label(net, x, y)


def header_nc_remaining(sch, pins, hdr_x, hdr_y, used_numbers):
    for num, pin in pins.items():
        if num not in used_numbers:
            x, y = pin_pos(hdr_x, hdr_y, pin)
            sch.no_connect(x, y)


def build() -> Sch:
    sch = Sch(project="mule")

    # === Step 1: inbound path, task PC -> Pi (17 lines) =====================
    tpc_pins = sch.place(
        "Connector_Generic", "Conn_02x20_Odd_Even", sch.next_ref("J"),
        "IDC-40 task-PC event bus", X_TPC_HDR, ROW0 + 8 * ROW_PITCH,
    )
    pi_pins = sch.place(
        "Connector_Generic", "Conn_02x20_Odd_Even", sch.next_ref("J"),
        "IDC-40 Pi event bus", X_PI_HDR, ROW0 + 8 * ROW_PITCH,
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
    for y_ic, base_i, count, _kind in ic_specs:
        channels = {}
        for local_ch in range(count):
            i = base_i + local_ch
            data_name = f"EVT_D{i}" if i < 16 else "EVT_STROBE"
            channels[local_ch] = (f"{data_name}_CLAMP", f"{data_name}_PI")
        place_541(sch, "SN74LVC541APW", X_IC541, y_ic, "+3V3", channels)

    for i in range(17):
        y = ROW0 + i * ROW_PITCH
        data_name = f"EVT_D{i}" if i < 16 else "EVT_STROBE"
        tpc_net = f"{data_name}_TPC"
        clamp_net = f"{data_name}_CLAMP"
        pi_net = f"{data_name}_PI"

        two_pin(sch, "Device", "R", "R", "100", X_R_IN, y, tpc_net, clamp_net)
        bav99_low_side_clamp(sch, X_BAV, y, clamp_net, "DGND")

        hdr_num = str(i + 1)
        header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, hdr_num, tpc_net)
        tpc_used.add(hdr_num)
        header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, hdr_num, pi_net)
        pi_used.add(hdr_num)

    # A couple of DGND reference pins per header (return-path reference for the ribbon
    # cable), plus BARCODE_PI inbound on the Pi header (see Step 2). Every other pin of
    # both 40-pin headers is explicitly no-connected below -- this mule only exercises 18
    # of the 40 Pi-header signals, and leaving the rest genuinely floating would be an
    # unlabelled, ERC-ambiguous "maybe I forgot this" rather than a documented choice.
    header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, "18", "DGND")
    header_pin(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, "19", "DGND")
    tpc_used |= {"18", "19"}
    header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, "18", "BARCODE_PI")
    header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, "19", "DGND")
    header_pin(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, "20", "DGND")
    pi_used |= {"18", "19", "20"}

    header_nc_remaining(sch, tpc_pins, X_TPC_HDR, ROW0 + 8 * ROW_PITCH, tpc_used)
    header_nc_remaining(sch, pi_pins, X_PI_HDR, ROW0 + 8 * ROW_PITCH, pi_used)

    # === Step 2: outbound path, Pi -> 5V equipment ===========================
    place_541(
        sch, "SN74HCT541PW", X_IC541, Y_U4, "+5V",
        {0: ("BARCODE_PI", "BARCODE_OUT")},
    )
    barcode_hdr_pins = sch.place(
        "Connector_Generic", "Conn_01x04", sch.next_ref("J"),
        "BARCODE_OUT test points", X_BARCODE_HDR, Y_OUTBOUND,
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
    # JUDGMENT CALL: PC847 (Isolator:PC847) chosen for the quad optocoupler. The brief
    # names no MPN ("Four optocoupler channels (one quad package)"), and the plan's own
    # Task 11 explicitly treats the main board's optocoupler as "a part requirement
    # rather than a fixed MPN". PC847 is SOIC-16 (hand-solderable), open-collector
    # phototransistor output (matches the pull-up-to-ISO_5V topology below), and is one
    # of the most common/available parts in this exact "quad logic optocoupler" category.
    #
    # JUDGMENT CALL: TMA-0505S, not TMA-0512D. The brief names "TMA0512D-class" but that
    # literal part is 5V-in / +-12V-out (dual), which cannot produce a rail actually named
    # ISO_5V (a locked, contract net name). TMA-0505S is the same Traco SIP7 family, 5V
    # in / 5V out, single output -- the family member that actually matches the required
    # net name. "-class" in the brief's own wording licenses picking the matching family
    # member rather than the literal, voltage-mismatched part number.
    opto_channels = [
        ("EVT_STROBE_PI", "EVT_STROBE_ISO", "1"),
        ("BARCODE_OUT", "BARCODE_ISO", "2"),
        ("OPTO_SPARE1_IN", "OPTO_SPARE1_ISO", "3"),
        ("OPTO_SPARE2_IN", "OPTO_SPARE2_ISO", "4"),
    ]
    # One reference (one physical PC847 package), placed as 4 separate unit instances
    # below -- standard KiCad practice for a multi-channel part, same as how a quad
    # NAND's 4 gates each get their own symbol graphic under one shared reference.
    opto_ref = sch.next_ref("U")
    screw_pins = sch.place(
        "Connector", "Screw_Terminal_01x04", sch.next_ref("J"),
        "Isolated-domain outputs", X_SCREW, Y_SCREW,
    )
    for idx, (src_net, iso_net, screw_num) in enumerate(opto_channels, start=1):
        unit_pins = sch.place(
            "Isolator", "PC847", opto_ref, "PC847", X_OPTO, Y_OPTO + (idx - 1) * 25.4,
            unit=idx,
        )
        led_net = f"U7_LED{idx}"
        anode_num, cathode_num = sorted(
            (n for n, p in unit_pins.items() if p.x < 0), key=lambda n: -unit_pins[n].y
        )
        collector_num, emitter_num = sorted(
            (n for n, p in unit_pins.items() if p.x > 0), key=lambda n: -unit_pins[n].y
        )
        two_pin(
            sch, "Device", "R", "R", "330",
            X_OPTO - 30.48, Y_OPTO + (idx - 1) * 25.4, src_net, led_net,
        )
        ax, ay = pin_pos(X_OPTO, Y_OPTO + (idx - 1) * 25.4, unit_pins[anode_num])
        sch.label(led_net, ax, ay)
        cx, cy = pin_pos(X_OPTO, Y_OPTO + (idx - 1) * 25.4, unit_pins[cathode_num])
        sch.label("DGND", cx, cy)
        two_pin(
            sch, "Device", "R", "R", "4k7",
            X_OPTO + 30.48, Y_OPTO + (idx - 1) * 25.4, "ISO_5V", iso_net,
        )
        collx, colly = pin_pos(X_OPTO, Y_OPTO + (idx - 1) * 25.4, unit_pins[collector_num])
        sch.label(iso_net, collx, colly)
        emx, emy = pin_pos(X_OPTO, Y_OPTO + (idx - 1) * 25.4, unit_pins[emitter_num])
        sch.label("ISO_GND", emx, emy)

        header_pin(sch, screw_pins, X_SCREW, Y_SCREW, screw_num, iso_net)

    for spare_idx in (1, 2):
        hp = sch.place(
            "Connector_Generic", "Conn_01x02", sch.next_ref("J"),
            f"Opto spare {spare_idx} bench injection", X_SPARE_HDR,
            Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY,
        )
        x1, y1 = pin_pos(X_SPARE_HDR, Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY, hp["1"])
        sch.label(f"OPTO_SPARE{spare_idx}_IN", x1, y1)
        x2, y2 = pin_pos(X_SPARE_HDR, Y_SPARE + (spare_idx - 1) * SPARE_HDR_DY, hp["2"])
        sch.label("DGND", x2, y2)

    dcdc_pins = sch.place(
        "Converter_DCDC_Isolated", "TMA-0505S", sch.next_ref("U"), "TMA-0505S",
        X_DCDC, Y_DCDC,
    )
    dcdc_map = {"1": "+5V", "2": "DGND", "4": "ISO_GND", "6": "ISO_5V"}
    for num, net in dcdc_map.items():
        x, y = pin_pos(X_DCDC, Y_DCDC, dcdc_pins[num])
        sch.label(net, x, y)

    iso_tp_pins = sch.place(
        "Connector_Generic", "Conn_01x02", sch.next_ref("J"),
        "ISO_5V/ISO_GND test point", X_ISO_TP, Y_ISO_TP,
    )
    x1, y1 = pin_pos(X_ISO_TP, Y_ISO_TP, iso_tp_pins["1"])
    sch.label("ISO_5V", x1, y1)
    x2, y2 = pin_pos(X_ISO_TP, Y_ISO_TP, iso_tp_pins["2"])
    sch.label("ISO_GND", x2, y2)

    # === Step 4: power ========================================================
    jack_pins = sch.place(
        "Connector", "Barrel_Jack", sch.next_ref("J"), "+5V DC in", X_BARREL, Y_BARREL,
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

    ldo_pins = sch.place(
        "Regulator_Linear", "AP1117-15", sch.next_ref("U"), "LD1117S33TR", X_LDO, Y_LDO,
    )
    ldo_map = {"3": "+5V", "1": "DGND", "2": "+3V3"}  # VI, GND, VO
    for num, net in ldo_map.items():
        x, y = pin_pos(X_LDO, Y_LDO, ldo_pins[num])
        sch.label(net, x, y)
    two_pin(sch, "Device", "C", "C", "100nF", X_LDO, Y_LDO + DECOUPLE_DX, "+3V3", "DGND")

    two_pin(sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS, "+5V", "DGND")
    two_pin(sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS + DECOUPLE_DX, "+3V3", "DGND")
    two_pin(
        sch, "Device", "C", "C", "10uF", X_CAPS, Y_CAPS + 2 * DECOUPLE_DX,
        "ISO_5V", "ISO_GND",
    )

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

    return sch


def main():
    sch = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "mule.kicad_sch"
    sch_path.write_text(sch.render())
    write_project_stub(OUT / "mule.kicad_pro")
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")


if __name__ == "__main__":
    main()
