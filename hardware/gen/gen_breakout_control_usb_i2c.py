"""Generator for hardware/breakout/sheets/control-usb-i2c.kicad_sch -- Task 12 (Part A) of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-12-brief.md). This is the LAST schematic-capture sheet on this board: an on-board
MCP2221A USB-I2C bridge, on the internal 4-pin USB header pi-interface.kicad_sch (Task 9)
already placed, driving an I2C bus that carries two MCP23017 16-bit GPIO expanders (32
outputs -> the 32 mux address lines mux-intan.kicad_sch (Task 10c) exposed but left
undriven) and the MCP4728 quad DAC comparators.kicad_sch (Task 10d) already placed and
powered (this sheet only CONNECTS to it -- does not duplicate it).

WHY THIS CHAIN HANGS OFF USB, NOT GPIO (task-12-brief.md's own framing, restated once here):
mux selection and DAC thresholds are configuration-time state with no timing requirement --
nothing here needs a real-time GPIO edge or a PIO capture window the way EVT_D0-15/STROBE
do. The sync-module's own 40-pin header is already fully assigned (pi-interface.kicad_sch's
own GPIO_PIN_SPEC, all 28 GPIO-named pins accounted for), so putting this bus on USB instead
of GPIO is not a workaround for a full header -- it is the correct call on its own electrical
merits, and incidentally also the only option left.

THREE I2C DEVICES, THREE DISTINCT ADDRESSES -- no collision:
  - MCP23017 #1: 0x20 (A2=A1=A0=0, all tied DGND) -- drives MUX1-4's own A0-A3 (16 lines).
  - MCP23017 #2: 0x21 (A2=A1=0/DGND, A0=1/+3V3) -- drives MUX5-8's own A0-A3 (16 lines).
  - MCP4728 (comparators.kicad_sch, Task 10d): 0x60, the part's own EEPROM factory default
    (no ADDR pins exist on this part at all -- comparators.kicad_sch's own module docstring).
Checked directly here, not assumed: {0x20, 0x21, 0x60} has three distinct elements. Recorded
in hardware/README.md (this task) for whoever writes the control software, alongside the
already-recorded MCP23017 GPIO-to-MUX{n}_A{m} channel map below.

32 OUTPUTS, 32 LINES -- EXACT FIT, NO SPARES: two MCP23017 GPB0-7/GPA0-7 banks (16 pins each)
against MUX1_A0..MUX8_A3 (8 muxes x 4 address bits = 32 nets, mux-intan.kicad_sch's own
contract). Every one of an MCP23017's 16 I/O pins is genuinely usable as an OUTPUT here
(this board's use is address-line drive, output-only by construction) even though the part's
own Features section flags GPA7/GPB7 as "output only" -- a real hardware limitation for a
part used bidirectionally, but irrelevant to a design that only ever needs 16 outputs per
chip. GPIO-TO-MUX ASSIGNMENT (this sheet's own declared, arbitrary-but-fixed convention -- no
datasheet fixes a GPIO-to-address-line assignment, the same class of board-specific choice
mux-intan.kicad_sch's own ALL_16_NETS/S_PIN_NUMBERS pairing already is): each expander's own
GPA0-7 drives its first TWO muxes' own A0-A3 (GPA0-3 -> mux N's A0-A3, GPA4-7 -> mux N+1's
A0-A3), GPB0-7 the next two the same way -- see expander_channel_map() below. Expander 1
(0x20) covers MUX1-4; expander 2 (0x21) covers MUX5-8.

MCP2221A POWER MODE -- 3.3V SELF-POWERED, NOT USB BUS-POWERED, A DELIBERATE DEPARTURE FROM
THE MOST-OBVIOUS "PLUG A USB DEVICE IN, POWER IT FROM VBUS" READING OF THIS SHEET'S OWN JOB,
made only after checking the real datasheet (Microchip DS20005565B) rather than assumed:

  Section 1.6.2, "MCP2221A POWER OPTIONS", names exactly two: "USB Bus Powered (5V)" and
  "3.3V Self Powered". Section 1.6.2.1's own text is explicit about the electrical
  consequence that decides this for a board that already has its own regulated 3.3V rail:
  "The provided VDD voltage has a direct influence on the voltage levels present on the GPIO
  and UART TX/RX pins. When VDD is 5V, ... a logical '1' around 5V ... For applications that
  require a 3.3V logical '1' level, VDD must be connected to a power supply providing the
  3.3V voltage. ... It is necessary to also connect the VUSB pin ... to the 3.3V power
  supply rail." This board's own I2C bus already has TWO other devices running at 3.3V (both
  MCP23017s here, and the MCP4728 on comparators.kicad_sch, whose own VDD=+3V3 -- that
  sheet's own module docstring) -- powering the bridge itself at 5V from USB_VBUS_PI would
  put a 5V-referenced SDA/SCL driver on a bus whose other three devices expect a 3.3V-
  referenced one, an unverified (and, per this device's own datasheet text quoted above,
  actually WRONG-by-default) logic-level assumption -- exactly the class of hazard this
  project's own "destroy-hardware constraint" discipline (comparators.kicad_sch's own +3V3-
  not-+5V pull-up rule; pi-interface.kicad_sch's own RWD_DLVR level shift) exists to catch
  BEFORE it reaches silicon, not after. Section 1.6.2.3's own "3.3V Self-Powered" text
  describes precisely this board's own situation: "many embedded applications are using 3.3V
  power supplies. When such option is available in the target system, MCP2221A can be
  powered up from the existing 3.3V power supply rail" -- so VDD and VUSB both go to +3V3
  (this board's own existing rail, Task 7's own regulator -- no new LDO needed the way that
  section's own Figure 1-5 example draws one, since that example assumes NO pre-existing
  3.3V rail and this board already has one), matching the datasheet's own documented
  alternative to bus-powered mode, not an improvised one.

  D313 (DC Characteristics, Table 4-1): "USB Voltage VUSB min 3.0V max 3.6V ... Voltage on
  the VUSB pin must be in this range for proper USB operation" -- +3V3 sits inside this
  range. Absolute Maximum Ratings Note 1: "VUSB must always be <= VDD + 0.3V" -- with both
  tied to the identical +3V3 node, VUSB=VDD exactly, trivially inside that bound.

  USB_VBUS_PI (the internal header's own pin 1, driven by the sync module's own USB-A port)
  IS THEREFORE DELIBERATELY LEFT UNCONNECTED ON THIS SHEET -- not an omission, and not what
  "drive those nets" (task-12-brief.md's own Part A text) leaves undone: D+/D-/GND (the
  header's other three pins) all get real, meaningful connections (D+/D- to the bridge's own
  USB transceiver pins; GND is already DGND, already driven). VBUS itself has no required
  destination in the 3.3V Self-Powered configuration -- confirmed directly against the
  datasheet's own Figure 1-5 ("Using an Externally Provided 3.3V Power Supply"), whose own
  "5V (USB Bus)" arrow feeds only an external LDO's own input, never a pin of the MCP2221A
  itself. This sheet's own regulated +3V3 already exists (Task 7), so this design does not
  even need that external LDO step -- VBUS truly has nothing left to do here. Left as the
  one honestly-reported residual isolated_pin_label warning (see hardware/README.md and this
  task's own report for the accounted-for before/after count), not silenced by inventing a
  spurious connection with no real electrical purpose.

I2C BUS PULL-UPS -- 2.2k on I2C_SDA and I2C_SCL, this sheet's own sizing decision
(comparators.kicad_sch's own module docstring explicitly defers this: "Task 12 owns the bus
master and, with it, the one-set-of-pull-ups-per-bus sizing decision"). Bus speed is capped
by the slowest participant's own spec ceiling -- the MCP2221A's own Features list "I2C
Master: Up to 400 kHz clock rate" (Fast-mode). Standard two-sided derivation (NXP UM10204
I2C-bus specification, the same reasoning any Fast-mode I2C pull-up sizing uses):
  Rp_min (sink-current floor): (VDD - VOL_max) / IOL_max = (3.3V - 0.4V) / 3mA = ~967 ohm.
  Rp_max (rise-time ceiling, Fast-mode tr_max=300ns, Cb~100pF -- a modest board estimate,
    4 devices + short internal traces, well under the spec's own 400pF full-bus ceiling):
    tr_max / (0.8473 x Cb) = 300ns / (0.8473 x 100pF) = ~3.5k ohm.
2.2k (E24) sits comfortably inside [967R, 3.5k] on both ends, the same "pick a round E24
value well inside a derived-not-assumed window" discipline this project's own resistor
choices consistently use elsewhere (e.g. opto-ni.kicad_sch's own 3.9k pull-up derivation).

PIN MAP VERIFICATION, both parts, pin-by-pin against the real, current Microchip datasheet
(this task's own non-negotiable #5), not assumed from the stock KiCad symbol alone:

  MCP2221A -- Microchip DS20005565B ("MCP2221A USB 2.0 to I2C/UART Protocol Converter with
  GPIO"), page 3, Table 1-1 "Pinout Description" (PDIP/SOIC/SSOP column) plus page 2's own
  "Package Types" pin diagram, fetched directly and read page-image, not quoted from memory:
  1=VDD, 2=GP0, 3=GP1, 4=RESET (input, internal pull-up), 5=URx (UART Rx, input), 6=UTx
  (UART Tx, output), 7=GP2, 8=GP3, 9=SDA, 10=SCL, 11=VUSB, 12=D-, 13=D+, 14=VSS -- matches
  KiCad's own Interface_USB:MCP2221AxSL library entry pin-for-pin (14-pin SOIC, confirmed
  directly via kicad_sch.py's own extract_symbol()/unit_pins(), the same machinery
  sch.place() uses), including pin TYPES (SDA/SCL/GP0-3 bidirectional, URx input, UTx
  output, VUSB passive, RESET input) relevant to how this sheet leaves several pins
  unconnected below.

  MCP23017 -- Microchip DS20001952D ("MCP23017/MCP23S17 16-Bit I/O Expander with Serial
  Interface"), page 1's own "Package Types" pin diagram (28-pin SOIC/SPDIP/SSOP column,
  the MCP23017 side specifically -- MCP23S17, drawn alongside it, is the SPI variant and
  is NOT what this sheet places), fetched directly and read page-image: 1-8=GPB0-7, 9=VDD,
  10=VSS, 11=NC, 12=SCK (this part's own package-diagram label for its I2C SCL pin -- the
  same physical pin is literally printed "SCK" on BOTH the I2C (MCP23017) and SPI
  (MCP23S17) package variants, since the two share one physical die/pinout and only the
  FUNCTIONAL BLOCK DIAGRAM on the same datasheet page distinguishes "SCL -> I2C" for the
  MCP23017 specifically -- confirmed by reading both the pin diagram AND the block diagram
  on the same datasheet page, not just one), 13=SDA, 14=NC, 15=A0, 16=A1, 17=A2,
  18=RESET (input, active-low, no internal pull-up documented for this part -- unlike the
  MCP2221A's own RST -- so this sheet ties it directly to +3V3 rather than leaving it
  unconnected), 19=INTB, 20=INTA, 21-28=GPA0-7 -- matches KiCad's own
  Interface_Expansion:MCP23017x-x-SO library entry pin-for-pin (28-pin SOIC-28W, confirmed
  the same way).

Non-negotiables (task-12-brief.md's own numbering):
  1. lib_symbols handled entirely by kicad_sch.py's own extract_symbol()/ensure_lib_symbol()
     (full lib id keys, bare child-unit names) -- this file never hand-writes lib_symbols
     text, so constraint 1 is satisfied by construction. Neither MCP2221AxSL nor
     MCP23017x-x-SO uses `(extends ...)` -- confirmed directly reading each library's own
     raw text before use (both carry their own complete pin geometry).
  2. `(instances (path ...))` -- this sheet reads breakout.kicad_sch's own committed text for
     find_root_uuid()/find_sheet_instance_path(), exactly like every prior child sheet.
  3. Refdes seeding clears ALL NINE already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend, analog-ni, mux-intan, comparators, opto-ni, opto-intan)
     via merge_max_refs().
  4. Coordinate collisions -- every role (bridge IC, expander ICs, decoupling caps, bus
     pull-ups) gets its own distinct X column, and ROW_DY (76.2mm) comfortably clears the
     tallest local pin span actually on this sheet (MCP23017's own SOIC-28W, +-25.4mm
     locally -- confirmed via unit_pins()), leaving a full 25.4mm clear gap between rows,
     the SAME absolute clearance mux-intan.kicad_sch's own ROW_DY already established as
     safe. Verified empirically, not just argued: see
     check_breakout_control_usb_i2c_netlist.py's own `_check_no_coordinate_collisions()`
     and the shared check_row_pitch_guard.py guard, both with a negative control confirming
     they fire.
  5. Every pin map verified pin-by-pin against the real datasheet -- see above. No hedged/
     two-candidate pins anywhere on this sheet.
  6. Footprint pad adjacency -- N/A to this sheet's own parts: nothing here relies on two
     SPECIFIC pads of one footprint being physically adjacent the way a jumper/shorting
     block does (task-10b-report.md's own finding); every part here is a normal IC or
     2-terminal passive with no such adjacency requirement.

hardware/breakout/sym-lib-table gained two new entries for this task: `Interface_USB`
(MCP2221AxSL) and `Interface_Expansion` (MCP23017x-x-SO). No new fp-lib-table entries
needed -- both parts' own SOIC footprints live in the already-registered Package_SO.pretty
(SOIC-14_3.9x8.7mm_P1.27mm, already used by comparators.kicad_sch's own LM339; and
SOIC-28W_7.5x17.9mm_P1.27mm, new to this sheet but already inside a registered library).
Both are hand-solderable 1.27mm-pitch SOIC packages -- this task introduces no second
exception to the MCP4728's own documented one (MSOP-10 at 0.5mm, comparators.kicad_sch).

Run directly: `python3 hardware/gen/gen_breakout_control_usb_i2c.py` (writes
hardware/breakout/sheets/control-usb-i2c.kicad_sch). Requires
hardware/breakout/breakout.kicad_sch and all nine sibling sheets (power, taskpc-digital,
pi-interface, analog-frontend, analog-ni, mux-intan, comparators, opto-ni, opto-intan) to
already exist on disk.
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_root_uuid,
    find_sheet_instance_path,
    pin_pos,
    write_project_stub,
)

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
CONTROL_USB_I2C_SHEETFILE = "sheets/control-usb-i2c.kicad_sch"  # exactly as
# gen_breakout.py's own SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- Device already registered (Task 6); Package_SO already registered
# (Task 10a). Interface_USB/Interface_Expansion are NEW sym-lib-table entries this task
# adds (see module docstring); neither needs a NEW fp-lib-table entry (both footprints
# live in the already-registered Package_SO.pretty).
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"    # MCP2221AxSL
FOOTPRINT_SOIC28W = "Package_SO:SOIC-28W_7.5x17.9mm_P1.27mm"  # MCP23017x-x-SO

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
ROW_DY = GRID(76.2)  # 30 x 2.54mm -- comfortably clears MCP23017's own real pin span
# (+-25.4mm locally, SOIC-28W's 14-row-per-side layout at build_ic_symbol()-class
# PITCH=2.54mm -- confirmed via unit_pins()): a 50.8mm-tall part gets a 76.2mm row pitch,
# leaving a full 25.4mm clear gap between consecutive rows -- the SAME absolute clearance
# mux-intan.kicad_sch's own ROW_DY already established as safe for this project's largest
# ICs. MCP2221A's own local span (+-20.32mm, SOIC-14) is smaller still, so the same pitch
# clears it with even more margin.

X_BRIDGE = GRID(20)    # MCP2221A column
X_EXP = GRID(90)       # both MCP23017 instances share this column (different rows -- safe,
# see ROW_DY above)
X_PULLUP = GRID(160)   # I2C bus pull-up column -- its own, shared by nothing else
DECOUPLE_DX = GRID(15.24)  # reused verbatim from every prior sheet's own decoupling column
DECOUPLE_DY = GRID(5.08)   # reused verbatim -- Device:C's own pins reach +-3.81mm locally,
# so two decouplers at +-DECOUPLE_DY around one placement Y clear each other by 2.54mm
# (established, already-proven-safe pattern -- see e.g. gen_breakout_opto_ni.py's own
# per-package bypass-cap pair or gen_breakout_comparators.py's own LM339 power decouplers).

Y_BRIDGE = Y0
Y_EXP1 = GRID(Y0 + ROW_DY)
Y_EXP2 = GRID(Y0 + 2 * ROW_DY)

Y_PULLUP_SCL = Y0
Y_PULLUP_SDA = GRID(Y0 + 15.24)  # > Device:R's own 7.62mm pin-to-pin span, clears the
# SCL pull-up above it (same discipline as every other resistor-pair spacing in this
# project -- e.g. pi-interface.kicad_sch's own GRID(12.7) GPIO0/1 series-resistor offset).

X_NOTE1, Y_NOTE1 = GRID(280), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(280), GRID(110)
X_NOTE3, Y_NOTE3 = GRID(280), GRID(190)
X_NOTE4, Y_NOTE4 = GRID(280), GRID(260)
X_NOTE5, Y_NOTE5 = GRID(280), GRID(330)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# MCP2221A pin map -- see module docstring, "PIN MAP VERIFICATION".
# ---------------------------------------------------------------------------
MCP2221A_PIN_VDD = "1"
MCP2221A_PIN_GP0 = "2"
MCP2221A_PIN_GP1 = "3"
MCP2221A_PIN_RESET = "4"
MCP2221A_PIN_URX = "5"
MCP2221A_PIN_UTX = "6"
MCP2221A_PIN_GP2 = "7"
MCP2221A_PIN_GP3 = "8"
MCP2221A_PIN_SDA = "9"
MCP2221A_PIN_SCL = "10"
MCP2221A_PIN_VUSB = "11"
MCP2221A_PIN_DM = "12"  # D-
MCP2221A_PIN_DP = "13"  # D+
MCP2221A_PIN_VSS = "14"
MCP2221A_UNUSED_PINS = [
    MCP2221A_PIN_GP0, MCP2221A_PIN_GP1, MCP2221A_PIN_RESET,
    MCP2221A_PIN_URX, MCP2221A_PIN_UTX, MCP2221A_PIN_GP2, MCP2221A_PIN_GP3,
]  # RESET has a documented internal pull-up (Table 1-1); GP0-3/URx/UTx are simply not
# needed by this design (I2C bridging only) -- same "genuinely unused, no_connect"
# precedent comparators.kicad_sch's own MCP4728 RDY/BSY pin already established.

# ---------------------------------------------------------------------------
# MCP23017 pin map -- see module docstring, "PIN MAP VERIFICATION".
# ---------------------------------------------------------------------------
MCP23017_PIN_VDD = "9"
MCP23017_PIN_VSS = "10"
MCP23017_PIN_SCL = "12"  # package-diagram label "SCK" -- see module docstring for why
# that is this part's own real pin-12 label, not a mislabeling.
MCP23017_PIN_SDA = "13"
MCP23017_PIN_A0 = "15"
MCP23017_PIN_A1 = "16"
MCP23017_PIN_A2 = "17"
MCP23017_PIN_RESET = "18"
MCP23017_PIN_INTB = "19"
MCP23017_PIN_INTA = "20"
MCP23017_UNUSED_PINS = [MCP23017_PIN_INTB, MCP23017_PIN_INTA]  # genuinely unused --
# nothing on this board consumes an interrupt from this bus (config-time/polling only,
# and no spare sync-module GPIO exists to route one to regardless).

GPA_PIN_NUMS = ["21", "22", "23", "24", "25", "26", "27", "28"]  # GPA0..GPA7
GPB_PIN_NUMS = ["1", "2", "3", "4", "5", "6", "7", "8"]          # GPB0..GPB7

# ---------------------------------------------------------------------------
# Two MCP23017 instances -- (I2C address, its own A2/A1/A0 tie net, the 4 mux numbers its
# own 16 GPIO pins drive). Address = 0x20 + (A2<<2 | A1<<1 | A0) -- this part's own
# EEPROM-free, hardware-pin address scheme (DS20001952D Table 1-1's own "Three Hardware
# Address Pins" feature). Checked distinct from each other AND from the MCP4728's own 0x60
# (comparators.kicad_sch) below the class definitions.
# ---------------------------------------------------------------------------
EXPANDERS = [
    {"addr": 0x20, "a2": "DGND", "a1": "DGND", "a0": "DGND", "mux_base": 1},
    {"addr": 0x21, "a2": "DGND", "a1": "DGND", "a0": "+3V3", "mux_base": 5},
]
assert len({e["addr"] for e in EXPANDERS}) == len(EXPANDERS)  # distinct amongst themselves
MCP4728_ADDR = 0x60  # comparators.kicad_sch's own recorded factory-default address
assert MCP4728_ADDR not in {e["addr"] for e in EXPANDERS}  # and distinct from the DAC


def expander_channel_map(mux_base: int) -> list[tuple[str, str]]:
    """16 (pin_number, MUX{n}_A{bit} net) pairs for one MCP23017, this sheet's own
    declared, arbitrary-but-fixed GPIO-to-address-line convention (see module docstring):
    GPA0-3 drive mux `mux_base`'s own A0-A3, GPA4-7 drive mux `mux_base+1`'s, GPB0-3 drive
    mux `mux_base+2`'s, GPB4-7 drive mux `mux_base+3`'s."""
    pins = GPA_PIN_NUMS + GPB_PIN_NUMS
    nets = [f"MUX{mux_base + local_mux}_A{bit}" for local_mux in range(4) for bit in range(4)]
    assert len(pins) == len(nets) == 16
    return list(zip(pins, nets))


PULLUP_OHMS = "2.2k"  # see module docstring, I2C BUS PULL-UPS, for the full two-sided
# (sink-current floor / rise-time ceiling) derivation.

# ---------------------------------------------------------------------------
# Low-level helpers -- duplicated from prior generators' own lbl()/two_pin() rather than
# imported (this project's own established discipline).
# ---------------------------------------------------------------------------


def lbl(sch, x, y, pins, pin_num, net):
    px, py = pin_pos(x, y, pins[pin_num])
    sch.label(net, px, py)


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    lbl(sch, x, y, pins, "1", net1)
    lbl(sch, x, y, pins, "2", net2)
    return ref


def decouple(sch, x, y, rail, gnd):
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)


# ---------------------------------------------------------------------------
# MCP2221A USB-I2C bridge
# ---------------------------------------------------------------------------


def place_bridge(sch, refs):
    ref = sch.next_ref("U")
    pins = sch.place(
        "Interface_USB", "MCP2221AxSL", ref, "MCP2221A", X_BRIDGE, Y_BRIDGE,
        footprint=FOOTPRINT_SOIC14,
    )
    refs["bridge"] = ref

    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_VDD, "+3V3")
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_VUSB, "+3V3")  # 3.3V self-powered mode
    # -- see module docstring, MCP2221A POWER MODE.
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_VSS, "DGND")
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_SDA, "I2C_SDA")
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_SCL, "I2C_SCL")
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_DM, "USB_DM_PI")
    lbl(sch, X_BRIDGE, Y_BRIDGE, pins, MCP2221A_PIN_DP, "USB_DP_PI")
    # USB_VBUS_PI (the header's own pin 1) is deliberately NOT referenced anywhere on this
    # sheet -- see module docstring, MCP2221A POWER MODE, for the full, datasheet-sourced
    # reasoning (3.3V self-powered mode needs no VBUS connection at all).

    for pin_num in MCP2221A_UNUSED_PINS:
        x, y = pin_pos(X_BRIDGE, Y_BRIDGE, pins[pin_num])
        sch.no_connect(x, y)

    c_vdd = decouple(sch, X_BRIDGE - DECOUPLE_DX, Y_BRIDGE - DECOUPLE_DY, "+3V3", "DGND")
    c_vusb = two_pin(
        sch, "Device", "C", "C", "220nF", X_BRIDGE - DECOUPLE_DX, Y_BRIDGE + DECOUPLE_DY,
        "+3V3", "DGND", footprint=FOOTPRINT_C_SMALL,
    )  # VUSB's own bypass, datasheet-specified 0.22-0.47uF window (module docstring) --
    # both VDD and VUSB tie to the identical +3V3 node in this power mode, so both
    # decouplers are topologically VDD<->DGND caps even though one is sized for VUSB's
    # own documented requirement specifically.
    refs["bridge_decouple_c"] = [c_vdd, c_vusb]
    return ref


# ---------------------------------------------------------------------------
# One MCP23017 GPIO expander
# ---------------------------------------------------------------------------


def place_expander(sch, y, spec, refs):
    ref = sch.next_ref("U")
    pins = sch.place(
        "Interface_Expansion", "MCP23017x-x-SO", ref, f"MCP23017 (I2C 0x{spec['addr']:02X})",
        X_EXP, y, footprint=FOOTPRINT_SOIC28W,
    )
    refs.setdefault("expanders", []).append(ref)

    lbl(sch, X_EXP, y, pins, MCP23017_PIN_VDD, "+3V3")
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_VSS, "DGND")
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_SCL, "I2C_SCL")
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_SDA, "I2C_SDA")
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_A2, spec["a2"])
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_A1, spec["a1"])
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_A0, spec["a0"])
    lbl(sch, X_EXP, y, pins, MCP23017_PIN_RESET, "+3V3")  # tied high, permanently
    # deasserted -- no internal pull-up documented for this pin (unlike the MCP2221A's own
    # RST), so it must be tied externally rather than left floating.

    for pin_num in MCP23017_UNUSED_PINS:
        px, py = pin_pos(X_EXP, y, pins[pin_num])
        sch.no_connect(px, py)
    # NC pins (11, 14) carry no label at all -- typed no_connect in the symbol itself, same
    # precedent as every other NC pin in this project (e.g. mux-intan.kicad_sch's own
    # ADG1206YRUZ pins 2/3/13).

    for pin_num, net in expander_channel_map(spec["mux_base"]):
        lbl(sch, X_EXP, y, pins, pin_num, net)

    c = decouple(sch, X_EXP - DECOUPLE_DX, y, "+3V3", "DGND")
    refs.setdefault("expander_decouple_c", []).append(c)
    return ref


# ---------------------------------------------------------------------------
# I2C bus pull-ups
# ---------------------------------------------------------------------------


def place_pullups(sch, refs):
    r_scl = two_pin(sch, "Device", "R", "R", PULLUP_OHMS, X_PULLUP, Y_PULLUP_SCL, "+3V3", "I2C_SCL", footprint=FOOTPRINT_R)
    r_sda = two_pin(sch, "Device", "R", "R", PULLUP_OHMS, X_PULLUP, Y_PULLUP_SDA, "+3V3", "I2C_SDA", footprint=FOOTPRINT_R)
    refs["pullup_scl"] = r_scl
    refs["pullup_sda"] = r_sda


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, CONTROL_USB_I2C_SHEETFILE)
    # PINNED IN FULL, not re-derived live -- the F1/F7 task, 2026-08-16.
    #
    # `ref_start` used to be recomputed here by reading every already-committed sibling
    # sheet at generation time. That is a latent renumbering bomb: the moment ANY sibling
    # gains a refdes above its old maximum, every counter this sheet seeds moves, and
    # regenerating this file for an unrelated one-line reason silently renumbers all of
    # its own parts -- and, transitively, everything downstream that seeded past THEM.
    #
    # It was not hypothetical by the time this was written. taskpc-digital.kicad_sch had
    # gained U69/R191/C149-C150 (the panel-instrumentation one-shot) and then U70-U73/
    # C151-C154 (finding F1's parallel buffer legs), and power.kicad_sch had gained D44
    # (finding F7's fan-branch diode). Running this generator UNMODIFIED against the
    # current siblings was confirmed to rewrite this sheet's whole refdes space.
    #
    # The values below are this sheet's own committed minima minus one (U66-U68, C143-C146, R188-R189).
    # Verified the way the whole family was: pin, regenerate, re-export the netlist and
    # confirm netlist-contract.json is byte-identical. Do NOT verify by diffing the
    # .kicad_sch -- several sheets were last written by KiCad rather than by their
    # generator, so the file reformats even when nothing electrical changes.
    # "R" = 187 was already pinned individually before this change, and its reasoning is
    # preserved because it is the narrower case that made the general one obvious:
    # comparators.kicad_sch's own R190 (channel 4's series resistor, an explicit refdes
    # placed above the whole board's prior "R" range precisely so no sibling has to move
    # for it) must not shift this sheet's own resistor numbering, and this sheet reads
    # COMPARATORS_SCH directly. 187 is opto-intan.kicad_sch's own true resistor count
    # (seeded at 170) -- the correct seed for this sheet's two I2C bus pull-ups (R188,
    # R189) regardless of what comparators.kicad_sch's text now also contains.
    ref_start = {"C": 142, "R": 187, "U": 65}

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    place_bridge(sch, refs)
    for spec, y in zip(EXPANDERS, (Y_EXP1, Y_EXP2)):
        place_expander(sch, y, spec, refs)
    place_pullups(sch, refs)

    for line_idx, line in enumerate([
        "Control / USB-I2C (Task 12) -- an on-board MCP2221A USB-I2C bridge, on the",
        "internal 4-pin USB header pi-interface.kicad_sch (Task 9) already placed, driving",
        "an I2C bus that carries two MCP23017 16-bit GPIO expanders (32 outputs -> the 32",
        "mux address lines MUX1_A0..MUX8_A3, mux-intan.kicad_sch/Task 10c -- exact fit, no",
        "spares) and the MCP4728 quad DAC (comparators.kicad_sch/Task 10d, already placed",
        "and powered -- this sheet only connects to it, does not duplicate it).",
        "",
        "This chain hangs off USB, not GPIO: mux selection and DAC thresholds are",
        "configuration-time state with no timing requirement, and the sync module's own",
        "40-pin header is already fully assigned regardless.",
        "",
        "I2C ADDRESSES -- three devices, three distinct addresses, no collision:",
        f"  MCP23017 #1: 0x{EXPANDERS[0]['addr']:02X}  (A2=A1=A0=0, all DGND) -> MUX1-4's A0-A3",
        f"  MCP23017 #2: 0x{EXPANDERS[1]['addr']:02X}  (A2=A1=0/DGND, A0=1/+3V3) -> MUX5-8's A0-A3",
        f"  MCP4728:     0x{MCP4728_ADDR:02X}  (comparators.kicad_sch, Task 10d's own factory",
        "               default -- checked here, not re-derived, and confirmed distinct)",
        "Also recorded in hardware/README.md for whoever writes the control software.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "GPIO-TO-MUX-ADDRESS-LINE MAP (this sheet's own declared, arbitrary-but-fixed",
        "convention -- no datasheet fixes this): each expander's own GPA0-3 drives its",
        "first mux's A0-A3, GPA4-7 the next mux's, GPB0-3 the next, GPB4-7 the last --",
        "16 pins, 4 muxes, exactly. Expander 1 (0x20): GPA0-3->MUX1_A0-3,",
        "GPA4-7->MUX2_A0-3, GPB0-3->MUX3_A0-3, GPB4-7->MUX4_A0-3. Expander 2 (0x21): the",
        "same pattern shifted to MUX5-8. See mux-intan.kicad_sch / hardware/README.md's",
        "own 'ADG1206 mux address truth table' for A3-A2-A1-A0=0000->S1 .. 1111->S16,",
        "EN active-high (unchanged by this sheet -- still hardwired to +12V there).",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "MCP2221A POWER MODE: 3.3V SELF-POWERED (Microchip DS20005565B Sec.1.6.2.3), NOT",
        "USB bus-powered. VDD and VUSB both tie to +3V3 (this board's own existing rail,",
        "Task 7) -- the datasheet is explicit that VDD sets the GPIO/SDA/SCL logic-'1'",
        "level (Sec.1.6.2.1), and this bus's other two device classes (both MCP23017s",
        "here, and comparators.kicad_sch's own MCP4728) already run at +3V3; powering the",
        "bridge from 5V USB_VBUS_PI would put a 5V-referenced driver on a 3.3V-expecting",
        "bus -- the same class of hazard this project's own destroy-hardware discipline",
        "(comparators.kicad_sch's +3V3-not-+5V pull-ups; pi-interface.kicad_sch's",
        "RWD_DLVR level shift) exists to catch before silicon, not after.",
        "",
        "USB_VBUS_PI (the internal header's own pin 1) IS DELIBERATELY LEFT UNCONNECTED",
        "on this sheet -- not an omission. 3.3V self-powered mode needs no VBUS",
        "connection at all (confirmed directly against the datasheet's own Figure 1-5,",
        "whose '5V (USB Bus)' arrow feeds only an external LDO's input, never a pin of",
        "the part itself -- and this board already has its own +3V3, so it does not even",
        "need that LDO step). D+/D-/GND (the header's other 3 pins) all get real",
        "connections. This is the one honestly-reported residual isolated_pin_label",
        "warning this task leaves -- see hardware/README.md for the accounted-for",
        "before/after ERC warning count.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "I2C BUS PULL-UPS: 2.2k on I2C_SDA/I2C_SCL (this sheet's own sizing decision --",
        "comparators.kicad_sch's own module docstring explicitly defers it here). Two-",
        "sided derivation (NXP UM10204, Fast-mode 400kHz -- the MCP2221A's own I2C",
        "master ceiling): Rp_min=(VDD-VOL_max)/IOL_max=(3.3-0.4)/3mA=~967R (sink-current",
        "floor); Rp_max=tr_max/(0.8473*Cb)=300ns/(0.8473*100pF)=~3.5k (rise-time",
        "ceiling, Cb~100pF estimated for 4 devices + short internal traces, well under",
        "the spec's own 400pF full-bus ceiling). 2.2k (E24) sits comfortably inside",
        "[967R, 3.5k] on both ends.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "PIN MAP VERIFICATION, both parts, pin-by-pin against the real, current",
        "Microchip datasheet, not assumed from the stock KiCad symbol alone:",
        "MCP2221A -- DS20005565B, page 3 Table 1-1 + page 2 pin diagram (14-pin",
        "PDIP/SOIC/SSOP column). MCP23017 -- DS20001952D, page 1 pin diagram (28-pin",
        "SOIC/SPDIP/SSOP, the MCP23017 side, not its MCP23S17 SPI sibling drawn",
        "alongside it). See this generator's own module docstring for the full",
        "transcription and the pin-12 'SCK'-label sourcing account.",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "control-usb-i2c.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
