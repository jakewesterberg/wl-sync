"""Generator for hardware/breakout/sheets/comparators.kicad_sch -- Task 10d of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-10-brief.md, Step 5 only -- Task 10 was split into 10a-10d, one sheet each. 10a
(analog-frontend, committed) produces the 16 A_* source nets this sheet draws three (plus
one more, unpopulated) of; 10b (analog-ni) and 10c (mux-intan) are separate sheets this
task does not touch.

THE SHEET'S JOB: one LM339 quad comparator turns three of Task 10a's own analog sources
into three digital trigger signals -- A_PD1->PD1_COMP, A_PD2->PD2_COMP, A_ACC->ACC_TRIG --
each with a DAC-set threshold (one MCP4728 quad I2C DAC channel per comparator) and
hysteresis (1M feedback, brief's own fixed value). The 4th LM339 section is brought out to
A_MISC1 but left UNPOPULATED (pull-up + hysteresis feedback DNP) -- a fourth thresholded
signal is a populate option, not a respin.

WHY A DAC AND NOT TRIMPOTS: the accelerometer threshold defines how much movement counts as
movement and gates task progression -- a behavioural parameter, and a task-gating parameter
that isn't recorded is a reproducibility hazard (spec Sec.6.5). An I2C-set voltage is a
number in a config file; a trimpot is a screwdriver position nobody wrote down.

THE DESTROY-HARDWARE CONSTRAINT, restated once here because it is the reason this sheet
exists at all rather than three bare comparators: all three comparator outputs wire
DIRECTLY (no buffer, no protection -- confirmed by reading pi-interface.kicad_sch's own
committed GPIO_PIN_SPEC, kind="direct" for GPIO20/21/25) to the sync module's own GPIO,
which is 3.3V and NOT 5V tolerant. Every open-collector pull-up on this sheet -- all four of
them, including the DNP one -- goes to +3V3, never +5V. Pulled to +5V, an open-collector
HIGH would put 5V straight onto an unprotected 3.3V input and destroy the module. Pulled to
+3V3 the module path is safe BY CONSTRUCTION, and the task-PC path still works: Task 8's own
outbound SN74HCT541PW buffer (taskpc-digital.kicad_sch) runs on +5V, and 74HCT's own input
threshold is TTL-compatible (~2.0V min, not a fraction of its own 5V supply), so a 3.3V high
from this sheet still reads as a valid logic HIGH into that buffer -- the 3.3V->5V up-shift
happens there, in a buffer that already exists, not on this sheet. This is exactly the class
of error that passes kicad-cli sch erc (a resistor to a valid, populated, correctly-typed
power net is not an ERC violation regardless of WHICH power net) and is found only by smoke
on real hardware -- check_breakout_comparators_netlist.py asserts it directly, with a
negative control that moves one pull-up to +5V and confirms the check fires.

WHY HYSTERESIS NEEDS A SERIES RESISTOR THIS SHEET ADDS, NOT JUST THE BRIEF'S OWN 1M
FEEDBACK: hysteresis is mandatory for two reasons (spec Sec.6.5) -- a photodiode crossing a
bare threshold on a slow display transition emits a burst of edges, and the accelerometer's
own motion-energy signal is noisy and slowly varying, chattering across a bare threshold
continuously. A 1M resistor from OUTPUT back to the '+' input, with '+' driven ONLY by the
analog source, gives hysteresis that rounds to zero: every one of Task 10a's own front ends
that reaches this sheet (A_PD1/A_PD2 via a passive RC anti-alias filter, ~1.6k Thevenin
looking back; A_ACC via an INA105's own SENSE=OUTPUT tie, near-zero Thevenin) is a
comparatively hard, low-impedance node next to 1M -- the source dominates the node, and the
1M feedback's own current has nowhere to make a real difference. Real, deliberately-sized
hysteresis needs an actual voltage divider between the source and the fed-back output swing,
so this sheet inserts a 10k series resistor between each POPULATED channel's own analog
source and the '+' node, upstream of the 1M feedback tie -- a component this sheet adds on
its own authority, not named in the brief, because "hysteresis is mandatory" is a functional
requirement the brief's own literal topology (feedback resistor alone, into a hard-driven
node) would not actually satisfy. See the on-sheet notes and check_breakout_comparators_
netlist.py's own module docstring for the full derivation (the comparator draws ~0 input
current, so superposition applies directly): with Rs=10k, Rf=1M (k=Rs/Rf=0.01), the trip
point is V_DAC*(1+k) minus up to k*(Voh-Vol) depending on output state -- a ~1% systematic
gain error and a ~33mV hysteresis band, chosen to sit comfortably inside spec's own bring-up
test 7 ("DAC-set threshold tracks within 2%, hysteresis measurable") on BOTH halves at once,
not just the half named "hysteresis": a bigger series resistor buys more (bench-measurable)
hysteresis at the direct cost of more gain error against the DAC-commanded value, and this
sheet's own k=0.01 is the specific point on that tradeoff this design picks, not the only
one that would "work".

WHY THE 4TH CHANNEL DIFFERS IN TOPOLOGY, NOT JUST POPULATION STATE: '+' is wired DIRECTLY to
A_MISC1 (no series resistor at all) and '-' directly to the DAC's own VOUTD -- both real,
permanent, never-floating connections, unlike the pull-up/feedback resistors, which are
placed but DNP. A series resistor for this channel would need to be DNP too for symmetry
with channels 1-3, but a DNP series resistor floats the '+' input the moment somebody
populates the feedback resistor alone (a real, silent hazard for a spare BJT comparator
input) -- so this channel accepts less hysteresis than channels 1-3 if it is ever fully
populated (A_MISC1's own source impedance, an INA105 output, is near zero -- same reasoning
as channels 1-3's OWN un-augmented '+' node) rather than risk a floating input in its
DEFAULT, as-shipped state. A documented simplification for a secondary "populate option"
channel, not a defect carried into the three primary ones.

PIN MAP VERIFICATION -- both parts checked pin-by-pin against a real, current datasheet
before this generator wired a single label, not assumed from the stock KiCad symbol alone
(task-10c-report.md's own reversed-source-terminal near miss is exactly the failure class
this step exists to rule out):

  LM339 -- TI SLCS006Z ("LM139, LM239, LM339, LM339B, LM139A, LM239A, LM339A, LM2901B,
  LM2901, LM2901AV, LM2901V Quad Differential Comparators", revised May 2025), Section 5,
  Table 5-1 "Pin Functions", D/DB/N/NS/PW/DYY/J (14-pin SOIC/SSOP/PDIP/SOP/TSSOP) column,
  fetched and read directly, not quoted from memory. Table 5-1 groups pins into four real
  comparators by DESCRIPTION text ("comparator 1".."comparator 4"), independent of the
  OUT1/OUT2/IN1x/IN2x pin NAMES (footnote 1, verbatim: "Some manufacturers transpose the
  names of channels 1 & 2. Electrically the pinouts are identical, just a difference in the
  channel naming convention.") -- so the fact that checked against KiCad's own
  Comparator:LM339 library entry is the ELECTRICAL GROUPING (which three pins share one
  real comparator), not which text label TI happens to print next to each. Cross-checked
  directly (kicad_sch.py's own extract_symbol()/unit_pins(), the same machinery
  sch.place() uses): KiCad's own 5 sub-units (LM339_1_1..LM339_5_1) group pins (4,5,2),
  (6,7,1), (10,11,13), (8,9,14), and (3,12) -- matching Table 5-1's own "comparator 1"
  (IN2-=4, IN2+=5, OUT2=2), "comparator 2" (IN1-=6, IN1+=7, OUT1=1), "comparator 4"
  (IN4-=10, IN4+=11, OUT4=13), "comparator 3" (IN3-=8, IN3+=9, OUT3=14), and VCC=3/GND=12
  EXACTLY -- every one of the 4 real comparators' own -,+,OUT triplet confirmed correct,
  and the power pins confirmed correct. (KiCad's own unit INDEX does not match TI's own
  "comparator N" number 1:1 -- unit 3 is TI's "comparator 4", unit 4 is TI's "comparator
  3" -- irrelevant electrically: LM339_UNIT_PINS below is keyed by KiCad's own unit index,
  the only number sch.place(unit=...) needs, and each triplet is independently confirmed
  regardless of which "comparator N" label TI happens to print next to it.) Absolute
  supply rating (family comparison table, page 1): 2-30V (LM339/LM339A) up to 2-36V
  (LM339B) -- this design's own +-12V (24V total) sits comfortably inside every variant's
  own rating, with the same margin every other +-12V-powered op-amp on this board already
  operates at.

  MCP4728 -- Microchip DS22187E ("MCP4728, 12-Bit Quad DAC with EEPROM Memory"), page 2,
  "Package Type" pin diagram (10-lead MSOP): VDD(1), SCL(2), SDA(3), LDAC-bar(4),
  RDY-bar/BSY-bar(5), VOUTA(6), VOUTB(7), VOUTC(8), VOUTD(9), VSS(10) -- matches KiCad's
  own Analog_DAC:MCP4728 library entry pin-for-pin, confirmed the same way (extract_
  symbol()/unit_pins()). Address scheme (page 1 Features, "I2C Interface: Address bits --
  User Programmable to EEPROM"; corroborated against Microchip's own datasheet a second,
  independent way via a live web search cross-check, not from this one document alone):
  the part has NO hardware address-select pins at all (confirmed by the pin table itself --
  every one of the 10 pins is accounted for as VDD/SCL/SDA/LDAC/RDY-BSY/4xVOUT/VSS, none
  reserved for address selection); the 3 address bits live in EEPROM, factory-default
  "000" -> 7-bit address 0x60 (0x60-0x67 are the 8 addresses this address-bit scheme can
  reach). See "I2C address" below for why this design keeps the factory default rather
  than reprogramming it.

Consumes (Task 10a, already committed): A_PD1, A_PD2, A_ACC, A_MISC1. Consumes (Task 7,
already committed): +12V, -12V, +3V3, AGND. Produces (this task's own net contract):
PD1_COMP, PD2_COMP, ACC_TRIG -- already referenced, as consumer-side placeholders, by
pi-interface.kicad_sch (Task 9, GPIO20/21/25, "direct" kind) and taskpc-digital.kicad_sch
(Task 8, its own outbound SN74HCT541PW bank) -- this sheet is the first to give them a REAL
driver (an open_collector-typed comparator output pin), not the first to name them. Also
exposes I2C_SDA/I2C_SCL (no bus pull-ups placed here -- see "I2C address" below) for Task
12 to consume.

I2C ADDRESS: 0x60, the factory default. Recorded here and in hardware/README.md (Task 12,
which builds control-usb-i2c.kicad_sch and drives this bus, needs this without re-deriving
it from the datasheet). Kept at the factory default deliberately, not reprogrammed: this
design places exactly ONE MCP4728 on the bus, so there is no second device at 0x60 to
collide with, and reprogramming an address nobody needs to move is a place a mistake could
be introduced for zero benefit. VOUTA/B/C/D drive PD1/PD2/ACC/MISC1's own threshold in that
order -- this sheet's own declared, arbitrary-but-fixed channel assignment (no datasheet
fixes it), matching plan.md's own note that ASYMMETRIC make/break thresholds would cost a
SECOND DAC channel per comparator (not implemented here: one DAC channel per comparator,
symmetric threshold, exactly as the brief specifies).

COORDINATE COLLISIONS -- this project has hit a real rail short from exactly this class of
defect before (analog-frontend.kicad_sch's own task-10a-report.md). Avoided here by
construction: every role (series resistor, comparator, feedback resistor, pull-up
resistor, DAC) gets its own distinct X, and ROW_DY (25.4mm) comfortably clears the tallest
local pin span actually on this sheet (the LM339's own power unit, +-7.62mm local -- the
largest of any part placed here, smaller by an order of magnitude than mux-intan's own
worst case), with the DAC and the LM339's own power unit each given a full extra row's
worth of clearance from the four real channel rows rather than sandwiched between them.
Verified empirically, not just argued: see check_breakout_comparators_netlist.py's own
`_check_no_coordinate_collisions()`, with a negative control confirming it fires.

Non-negotiables carried over from every prior child-sheet generator (hardware/README.md's
own "KiCad gotchas"; constraint numbers match this task's own brief):
  1. lib_symbols handled entirely by kicad_sch.py's own extract_symbol()/ensure_lib_symbol()
     (full lib id keys, bare child-unit names) -- this file never hand-writes lib_symbols
     text, so constraint 1 is satisfied by construction. Both LM339 and MCP4728 are REAL
     stock KiCad symbols (Comparator/Analog_DAC libraries) with no `(extends ...)` --
     confirmed directly reading each library's own raw text before use -- so neither needs
     the extends-flattening path extract_symbol() also handles.
  2. `(instances (path ...))` -- this sheet reads breakout.kicad_sch's own committed text for
     find_root_uuid()/find_sheet_instance_path(), exactly like every prior child sheet.
  3. Refdes seeding clears ALL SIX already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend, analog-ni, mux-intan) via merge_max_refs().
  4. Coordinate collisions -- see above.
  5. Verify every symbol's pin map against the real datasheet, pin by pin -- see "PIN MAP
     VERIFICATION" above.
  6. Footprint pad adjacency -- N/A to this sheet's own parts: nothing here relies on two
     SPECIFIC pads of one footprint being physically adjacent the way a jumper/shorting
     block does (task-10b-report.md's own finding); every part here is a normal IC or
     2-terminal passive with no such adjacency requirement.

hardware/breakout/sym-lib-table gained one new entry for this task: `Analog_DAC` (MCP4728).
`Comparator` (LM339) was already registered (Task 6). No new fp-lib-table entries needed --
Package_SO (SOIC-14, MSOP-10) and Device/Resistor_SMD/Capacitor_SMD were all already
registered.

Run directly: `python3 hardware/gen/gen_breakout_comparators.py` (writes
hardware/breakout/sheets/comparators.kicad_sch). Requires hardware/breakout/breakout.kicad_sch
and all six sibling sheets (power, taskpc-digital, pi-interface, analog-frontend, analog-ni,
mux-intan) to already exist on disk.
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
POWER_SCH = OUT / "power.kicad_sch"                      # already-committed sibling (Task 7)
TASKPC_SCH = OUT / "taskpc-digital.kicad_sch"             # already-committed sibling (Task 8)
PI_INTERFACE_SCH = OUT / "pi-interface.kicad_sch"         # already-committed sibling (Task 9)
ANALOG_FRONTEND_SCH = OUT / "analog-frontend.kicad_sch"   # already-committed sibling (Task 10a)
ANALOG_NI_SCH = OUT / "analog-ni.kicad_sch"               # already-committed sibling (Task 10b)
MUX_INTAN_SCH = OUT / "mux-intan.kicad_sch"               # already-committed sibling (Task 10c)
COMPARATORS_SHEETFILE = "sheets/comparators.kicad_sch"  # exactly as gen_breakout.py's own
# SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- Comparator/Device/Resistor_SMD/Capacitor_SMD already registered (Task 6);
# Package_SO already registered (Task 10a, for SOIC-8/14/TSSOP-28). Analog_DAC is a NEW
# sym-lib-table entry this task adds (see module docstring); MSOP-10 needs no NEW
# fp-lib-table entry -- it lives in the already-registered Package_SO.pretty.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"   # LM339
FOOTPRINT_MSOP10 = "Package_SO:MSOP-10_3x3mm_P0.5mm"        # MCP4728

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
ROW_DY = GRID(25.4)  # 10 x 2.54mm -- comfortably clears every local pin span actually on
# this sheet: an LM339 input/output unit reaches +-2.54mm locally, its own power unit
# +-7.62mm (this sheet's own tallest local span, still far smaller than mux-intan's own
# 28-pin TSSOP worst case), MCP4728 -10.16..+7.62mm. Rows 0-3 are the four real channels;
# row 4 (a full ROW_DY below row 3) is the MCP4728, its own dedicated slot rather than
# sandwiched between channel rows; row 5 (another full ROW_DY below that) is the LM339's
# own power unit -- every row gets the SAME clearance from its neighbours a real channel
# row gets from another, not a tighter one.

X_RSER = GRID(45)     # 10k series resistor (populated channels only) -- signal -> FB node
X_COMP = GRID(75)     # LM339 units (all 5: 4 comparators + power) and MCP4728 share this
# column -- safe because every placement at this X sits at its OWN distinct row (see
# ROW_DY above), never the same (X, Y) as anything else.
X_RFB = GRID(100)     # 1M hysteresis feedback resistor -- OUTPUT -> FB node
X_PULLUP = GRID(125)  # 10k pull-up resistor -- +3V3 -> OUTPUT
DECOUPLE_DX = GRID(15.24)  # reused verbatim from every prior analog sheet -- own distinct
# column, left of whichever IC it decouples.
DECOUPLE_DY = GRID(5.08)   # reused verbatim -- Device:C's own pins reach +-3.81mm locally,
# so 5.08+3.81=8.89mm reach, far inside ROW_DY's 25.4mm pitch.

X_NOTE1, Y_NOTE1 = GRID(180), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(180), GRID(95)
X_NOTE3, Y_NOTE3 = GRID(180), GRID(170)
X_NOTE4, Y_NOTE4 = GRID(180), GRID(245)
X_NOTE5, Y_NOTE5 = GRID(180), GRID(320)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# LM339 pin map -- see module docstring, "PIN MAP VERIFICATION", for the full datasheet
# cross-check. Keyed by KiCad's own unit index (1-5), the number sch.place(unit=...)
# needs; each (minus, plus, out) triplet is independently confirmed against TI's own
# Table 5-1, regardless of which "comparator N" label TI happens to print on that triplet.
# ---------------------------------------------------------------------------
LM339_UNIT_PINS = {
    1: ("4", "5", "2"),     # TI's own "comparator 1": IN2-, IN2+, OUT2
    2: ("6", "7", "1"),     # TI's own "comparator 2": IN1-, IN1+, OUT1
    3: ("10", "11", "13"),  # TI's own "comparator 4": IN4-, IN4+, OUT4
    4: ("8", "9", "14"),    # TI's own "comparator 3": IN3-, IN3+, OUT3
}
LM339_PIN_VPOS = "3"   # VCC (Table 5-1: "Positive supply")
LM339_PIN_VNEG = "12"  # GND (Table 5-1: "Negative supply") -- KiCad's own stock symbol
# already names this pin "V-", not "GND"/"VSS", so ERC's ground_pin_not_ground heuristic
# (which flags a ground-NAMED pin wired to a non-ground-looking net) never fires wiring it
# to -12V -- confirmed directly against the extracted symbol text, same situation
# gen_wl_sync_lib.py's own ADG1206YRUZ V- pin needed a hand rename for; LM339's stock
# symbol already has the safe name.

# MCP4728 pin map -- see module docstring, "PIN MAP VERIFICATION".
MCP4728_PIN_VDD = "1"
MCP4728_PIN_SCL = "2"
MCP4728_PIN_SDA = "3"
MCP4728_PIN_LDAC = "4"     # active-low; tied to AGND (permanently asserted -- immediate
# per-write update, no synchronized multi-channel update needed for a threshold that
# changes at configuration time, not in real time).
MCP4728_PIN_RDYBSY = "5"   # EEPROM-write status, open-drain; genuinely unused here.
MCP4728_PIN_VOUTA = "6"
MCP4728_PIN_VOUTB = "7"
MCP4728_PIN_VOUTC = "8"
MCP4728_PIN_VOUTD = "9"
MCP4728_PIN_VSS = "10"

# ---------------------------------------------------------------------------
# Channel contract -- (LM339 unit, analog source net (Task 10a), produced/local output
# net, this channel's own MCP4728 VOUT pin, populated?). PD1_COMP/PD2_COMP/ACC_TRIG are
# this task's own literal net-name contract (already referenced by pi-interface.kicad_sch
# and taskpc-digital.kicad_sch); A_MISC1_COMP is this sheet's own choice (the brief names
# no contract net for the unpopulated 4th channel -- nothing downstream consumes it yet).
# ---------------------------------------------------------------------------
CHANNELS = [
    # (unit, source_net,  out_net,        dac_pin,           populated)
    (1, "A_PD1", "PD1_COMP", MCP4728_PIN_VOUTA, True),
    (2, "A_PD2", "PD2_COMP", MCP4728_PIN_VOUTB, True),
    (3, "A_ACC", "ACC_TRIG", MCP4728_PIN_VOUTC, True),
    (4, "A_MISC1", "A_MISC1_COMP", MCP4728_PIN_VOUTD, False),
]
assert len(CHANNELS) == 4
assert len({c[0] for c in CHANNELS}) == 4
assert len({c[2] for c in CHANNELS}) == 4

COMP_ROW_Y = {unit: GRID(Y0 + (unit - 1) * ROW_DY) for unit, *_ in CHANNELS}
Y_DAC = GRID(Y0 + 4 * ROW_DY)
Y_PWR = GRID(Y0 + 5 * ROW_DY)


# ---------------------------------------------------------------------------
# Low-level helpers -- duplicated from prior generators' own lbl()/two_pin() rather than
# imported: kicad_sch.py, not any one generator, is this project's shared machinery (same
# convention every generator here follows).
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
    """`gnd` is REQUIRED, not defaulted -- this sheet decouples ICs against AGND only
    (unlike mux-intan's own decouple(), nothing here straddles into ISO_P12/ISO_N12/
    INTAN_GND), but an explicit parameter keeps that a checked fact of every call site
    rather than a silently-assumed default, matching mux-intan's own established
    discipline for exactly this class of mistake.
    """
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)


# ---------------------------------------------------------------------------
# One comparator channel
# ---------------------------------------------------------------------------


def comparator_channel(sch, y, cpins, minus_pin, plus_pin, out_pin, signal_net, out_net, thr_net, populated):
    """Wire one LM339 unit as a hysteretic, DAC-thresholded comparator.

    '-' = thr_net (this channel's own MCP4728 VOUTx, direct wire, ALWAYS -- the DAC
    package itself is already stuffed for the other channels regardless of this one's own
    populate state, so there is no DNP concern on a pure net connection). OUT = out_net
    (open-collector).

    '+' = fb_net, the summing node between the analog source and the fed-back output --
    see module docstring for the full hysteresis derivation. For a POPULATED channel,
    fb_net is a genuinely separate node (f"{out_net}_FB"), reached from signal_net through
    a real, populated 10k series resistor. For the DNP 4th channel, fb_net IS signal_net
    directly (no series resistor placed at all -- a DNP series resistor would float '+'
    the moment the feedback resistor alone got populated; see module docstring).

    Returns (pullup_ref, feedback_ref, series_ref_or_None).
    """
    lbl(sch, X_COMP, y, cpins, minus_pin, thr_net)
    lbl(sch, X_COMP, y, cpins, out_pin, out_net)

    fb_net = f"{out_net}_FB" if populated else signal_net
    lbl(sch, X_COMP, y, cpins, plus_pin, fb_net)

    r_ser = None
    if populated:
        r_ser = two_pin(sch, "Device", "R", "R", "10k", X_RSER, y, signal_net, fb_net, footprint=FOOTPRINT_R)
    r_fb = two_pin(
        sch, "Device", "R", "R", "1M", X_RFB, y, out_net, fb_net, footprint=FOOTPRINT_R, dnp=not populated,
    )
    r_pu = two_pin(
        sch, "Device", "R", "R", "10k", X_PULLUP, y, "+3V3", out_net, footprint=FOOTPRINT_R, dnp=not populated,
    )
    return r_pu, r_fb, r_ser


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, COMPARATORS_SHEETFILE)
    ref_start = merge_max_refs(
        find_max_refs(POWER_SCH.read_text()),
        find_max_refs(TASKPC_SCH.read_text()),
        find_max_refs(PI_INTERFACE_SCH.read_text()),
        find_max_refs(ANALOG_FRONTEND_SCH.read_text()),
        find_max_refs(ANALOG_NI_SCH.read_text()),
        find_max_refs(MUX_INTAN_SCH.read_text()),
    )

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {"lm339": None, "mcp4728": None, "pullups": [], "feedbacks": [], "series": []}

    # --- LM339: 4 comparator units + 1 power unit, one reference ---
    lm_ref = sch.next_ref("U")
    refs["lm339"] = lm_ref
    cpins = {}
    for unit, y in COMP_ROW_Y.items():
        cpins[unit] = sch.place("Comparator", "LM339", lm_ref, "LM339", X_COMP, y, unit=unit, footprint=FOOTPRINT_SOIC14)
    ppins = sch.place("Comparator", "LM339", lm_ref, "LM339", X_COMP, Y_PWR, unit=5, footprint=FOOTPRINT_SOIC14)
    lbl(sch, X_COMP, Y_PWR, ppins, LM339_PIN_VPOS, "+12V")
    lbl(sch, X_COMP, Y_PWR, ppins, LM339_PIN_VNEG, "-12V")
    decouple(sch, X_COMP - DECOUPLE_DX, Y_PWR - DECOUPLE_DY, "+12V", "AGND")
    decouple(sch, X_COMP - DECOUPLE_DX, Y_PWR + DECOUPLE_DY, "-12V", "AGND")

    # --- MCP4728: one instance, one reference ---
    dac_ref = sch.next_ref("U")
    refs["mcp4728"] = dac_ref
    dpins = sch.place("Analog_DAC", "MCP4728", dac_ref, "MCP4728", X_COMP, Y_DAC, footprint=FOOTPRINT_MSOP10)
    lbl(sch, X_COMP, Y_DAC, dpins, MCP4728_PIN_VDD, "+3V3")
    lbl(sch, X_COMP, Y_DAC, dpins, MCP4728_PIN_SCL, "I2C_SCL")
    # SCL is the DAC's own `input`-typed pin (SDA is `bidirectional`, which ERC accepts
    # undriven -- see module docstring). Nothing on THIS sheet drives SCL: Task 12's own
    # USB-I2C bridge (the real bus master) does not exist yet, so kicad-cli sch erc's own
    # `pin_not_driven` rule correctly reports it as undriven on the CURRENT, incomplete
    # project (confirmed empirically, same "genuine, not false, finding" situation
    # gen_breakout_taskpc_digital.py's own _place_outbound() already documented for
    # PD1_COMP/PD2_COMP/ACC_TRIG before this task existed). A PWR_FLAG clears it, same
    # "assert this net IS driven, even though ERC can't see why from here" mechanism
    # gen_breakout_power.py's own build() uses for ISO_P15_FILT/ISO_N15_FILT --
    # DELETE this call the moment Task 12 wires its own bus master's SCL driver to
    # I2C_SCL; left in place after that, it does NOT trip pin_to_pin the way a PWR_FLAG
    # on an open_collector net does (a real I2C master's SCL driver is typically a
    # push-pull or open-drain OUTPUT-class pin, not open_collector specifically), but it
    # is still redundant and should go -- flagged here and in this task's own report.
    sch.power_flag("I2C_SCL", GRID(30), Y_DAC)
    lbl(sch, X_COMP, Y_DAC, dpins, MCP4728_PIN_SDA, "I2C_SDA")
    lbl(sch, X_COMP, Y_DAC, dpins, MCP4728_PIN_LDAC, "AGND")
    px, py = pin_pos(X_COMP, Y_DAC, dpins[MCP4728_PIN_RDYBSY])
    sch.no_connect(px, py)
    thr_nets = {unit: f"{out_net}_THR" for unit, _src, out_net, _dac_pin, _pop in CHANNELS}
    for unit, _src, _out_net, dac_pin, _pop in CHANNELS:
        lbl(sch, X_COMP, Y_DAC, dpins, dac_pin, thr_nets[unit])
    lbl(sch, X_COMP, Y_DAC, dpins, MCP4728_PIN_VSS, "AGND")
    decouple(sch, X_COMP - DECOUPLE_DX, Y_DAC, "+3V3", "AGND")

    # --- Four channels ---
    for unit, source_net, out_net, _dac_pin, populated in CHANNELS:
        minus_pin, plus_pin, out_pin = LM339_UNIT_PINS[unit]
        r_pu, r_fb, r_ser = comparator_channel(
            sch, COMP_ROW_Y[unit], cpins[unit], minus_pin, plus_pin, out_pin,
            source_net, out_net, thr_nets[unit], populated,
        )
        refs["pullups"].append(r_pu)
        refs["feedbacks"].append(r_fb)
        if r_ser:
            refs["series"].append(r_ser)

    for line_idx, line in enumerate([
        "Comparators (Task 10d) -- one LM339 quad comparator. Three channels used:",
        "A_PD1->PD1_COMP, A_PD2->PD2_COMP, A_ACC->ACC_TRIG (Task 10a's own sources,",
        "spec Sec.6.5). Fourth (A_MISC1) brought out, unpopulated -- see below.",
        "",
        "Every '+' (non-inverting) input takes its own channel's analog source (through",
        "a 10k series resistor on populated channels -- see the hysteresis note below);",
        "every '-' (inverting) input takes a fixed threshold from one of the MCP4728's",
        "four VOUT channels, direct wire. Output is open-collector, pulled up to +3V3",
        "(never +5V -- see below) and fed back 1M to the '+' node for hysteresis.",
        "",
        "All three outputs (PD1_COMP/PD2_COMP/ACC_TRIG) reach the sync module's own GPIO",
        "DIRECTLY (pi-interface.kicad_sch, Task 9, GPIO20/21/25 -- 'direct' kind, no",
        "protection in between) and the task PC's own outbound buffer",
        "(taskpc-digital.kicad_sch, Task 8) -- confirmed by reading both sheets' own",
        "committed source before writing this one, not assumed.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "PULL-UPS GO TO +3V3, NEVER +5V -- THE DESTROY-HARDWARE CONSTRAINT. All three",
        "comparator outputs wire DIRECTLY to the sync module's own GPIO20/21/25, which",
        "is 3.3V and NOT 5V tolerant. Pulled to +5V, an open-collector HIGH would put 5V",
        "straight onto an unprotected 3.3V input and destroy the module.",
        "",
        "Pulled to +3V3 the module path is safe BY CONSTRUCTION, and the task-PC path",
        "still works: Task 8's own outbound SN74HCT541PW buffer runs on +5V, and",
        "74HCT's own input threshold is TTL-compatible (~2.0V min, not a fraction of",
        "its own 5V supply) -- a 3.3V high from this sheet still reads as a valid logic",
        "HIGH into that buffer. The 3.3V->5V up-shift happens there, in a buffer that",
        "already exists, not on this sheet.",
        "",
        "This is exactly the class of error that passes kicad-cli sch erc (a resistor",
        "to a valid, correctly-typed power net is not an ERC violation regardless of",
        "WHICH power net) and is found only by smoke on real hardware --",
        "check_breakout_comparators_netlist.py asserts every one of the four pull-ups",
        "(all three populated ones AND the fourth, DNP one) lands on +3V3 and never",
        "+5V, with a negative control that moves one to +5V and confirms it fires.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "HYSTERESIS IS MANDATORY, for two reasons (spec Sec.6.5): a photodiode crossing",
        "a bare threshold on a slow display transition emits a burst of edges; the",
        "accelerometer's own motion-energy signal is noisy and slowly varying, and",
        "chatters across a bare threshold continuously.",
        "",
        "A 1M feedback resistor alone, with '+' driven ONLY by the analog source, gives",
        "hysteresis that rounds to zero: every Task 10a front end reaching this sheet is",
        "a comparatively hard, low-impedance node next to 1M (A_PD1/A_PD2 ~1.6k Thevenin",
        "through their own RC anti-alias filter; A_ACC near-zero, an INA105's own",
        "SENSE=OUTPUT tie) -- the source dominates the node and the 1M feedback current",
        "has nowhere to make a real difference. So this sheet ALSO inserts a 10k series",
        "resistor between each POPULATED channel's own source and the '+' node, upstream",
        "of the 1M feedback tie -- a real, deliberate divider between the source and the",
        "fed-back output swing, not named in the brief but necessary for 'hysteresis is",
        "mandatory' to be functionally true rather than topologically present only.",
        "",
        "Math (comparator draws ~0 input current, so superposition applies): V+ =",
        "(V_signal*Rf + Vout*Rs)/(Rs+Rf). At the trip point V+ = V_DAC, so V_signal_trip",
        "= V_DAC*(1+k) - Vout*k, k=Rs/Rf=10k/1M=0.01. Rising (Vout~0V): 1.01*V_DAC.",
        "Falling (Vout~3.3V): 1.01*V_DAC - 33mV. ~33mV hysteresis band (comfortably",
        "bench-measurable -- plan.md's own bring-up test 7), ~1% systematic gain error",
        "(inside the SAME test's own 'tracks within 2%' half). k=0.01 is this sheet's",
        "own choice, not fixed by the brief (which fixes only Rf=1M) -- a bigger series",
        "resistor buys more hysteresis at the cost of more gain error against the",
        "DAC-commanded value; k=0.01 keeps both comfortably inside the bring-up budget.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "THRESHOLDS ARE I2C-DAC-SET, NOT TRIMPOTS (spec Sec.6.5): the accelerometer",
        "threshold in particular is a behavioural parameter that gates task progression,",
        "and an unrecorded task-gating parameter is a reproducibility hazard. One",
        "MCP4728 quad 12-bit DAC, VDD=+3V3, VSS=AGND -- NOT DGND: this keeps the",
        "threshold in the SAME ground domain as the AGND-referenced signal it is",
        "compared against, so a DGND-AGND offset never leaks into the threshold (+3V3",
        "itself is this project's own DGND-referenced logic rail, gen_breakout_power.py's",
        "own u1_map -- but a chip's own VDD SOURCE and its own GND REFERENCE are",
        "independent choices, and the DAC's own local return is what sets where its",
        "output voltage actually lands).",
        "",
        "LDAC tied to AGND (permanently asserted -- immediate per-write update; no",
        "synchronized multi-channel update is needed for a threshold that changes at",
        "configuration time, not in real time). RDY/BSY (EEPROM-write status) is",
        "genuinely unused here, no_connect.",
        "",
        "I2C address: 0x60, the factory default (address bits are EEPROM-programmable,",
        "not hardware-pin-set -- this part has no ADDR pins at all). Kept at default",
        "because this design places only ONE MCP4728 on the bus -- no collision to",
        "avoid. Recorded here and in hardware/README.md for Task 12, which drives this",
        "bus. I2C_SDA/I2C_SCL exposed as global labels; NO bus pull-ups placed on THIS",
        "sheet -- Task 12 owns the bus master and, with it, the one-set-of-pull-ups-",
        "per-bus sizing decision (plan.md's own control-usb-i2c step).",
        "",
        "VOUTA/B/C/D -> PD1/PD2/ACC/MISC1's own threshold, in that order -- this",
        "sheet's own declared, arbitrary-but-fixed channel assignment (no datasheet",
        "fixes it). ONE DAC channel per comparator, symmetric threshold -- plan.md's",
        "own note that ASYMMETRIC make/break thresholds would cost a SECOND DAC channel",
        "per comparator is not implemented here, exactly as the brief specifies.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "FOURTH CHANNEL (A_MISC1), UNPOPULATED: '+' wired DIRECTLY to A_MISC1 and '-'",
        "directly to the DAC's own VOUTD -- both real, permanent, never-floating",
        "connections regardless of what else here is populated (the LM339 and MCP4728",
        "packages are both already stuffed for the other 3 channels). Only the pull-up",
        "(10k) and the hysteresis feedback (1M) are DNP -- real land patterns on the",
        "PCB (populating them later is an assembly step, not a respin), just not",
        "stuffed by default.",
        "",
        "No series resistor is placed for this channel, unlike channels 1-3: with the",
        "feedback resistor ALSO DNP by default, a populated-but-orphaned series resistor",
        "would only float the '+' input the moment someone stuffed the feedback alone --",
        "simpler and safer to wire '+' straight to A_MISC1 and accept, if this channel",
        "is ever fully populated, somewhat less hysteresis than channels 1-3's own",
        "deliberate divider (A_MISC1's own source impedance, Task 10a's INA105 output,",
        "is near zero, same reasoning as channels 1-3's own un-augmented '+' node would",
        "have been). A documented simplification for a secondary 'populate option'",
        "channel, not a defect carried into the three primary ones.",
        "",
        "PIN MAP VERIFICATION, both parts, pin-by-pin against a real, current datasheet",
        "(TI SLCS006Z Table 5-1 for LM339; Microchip DS22187E page 2 for MCP4728), not",
        "assumed from the stock KiCad symbol alone -- see this generator's own module",
        "docstring for the full account, including how TI's own comparator-N numbering",
        "differs from (but electrically agrees with) KiCad's own unit index.",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "comparators.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
