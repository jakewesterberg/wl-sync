"""Generator for hardware/breakout/sheets/mux-intan.kicad_sch -- Task 10c of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-10-brief.md, Step 4 only -- Task 10 was split into 10a-10d, one sheet each. 10a
(analog-frontend, committed) produces the 16 `A_*` source nets this sheet consumes; 10b
(analog-ni, committed) fans those same 16 out to the recording NI and 9 to the task PC --
a SEPARATE, parallel consumer of the same 16 labels, not something this sheet builds on
top of. 10d (comparators) is a separate sheet this task does not touch.

THE SHEET'S JOB, restated once here: give Intan a software-selectable window onto 8 of the
16 `A_*` sources. Eight `ADG1206YRUZ` 16:1 muxes, each with EVERY ONE of the 16 `A_*`
sources on its own S1-S16 inputs (so any source can reach any of Intan's 8 channels) and
`EN` tied high (always enabled -- selection is by address only). Each mux's own single D
output (pin 28, this part's own one common drain -- see gen_wl_sync_lib.py's own
ADG1206YRUZ block comment) feeds `MUX_OUT{n}` (plan.md's own net-naming table) into one
`INA105KU` difference amplifier, whose OUTPUT reaches Intan via a 100R series resistor and
a BNC as `INTAN_AO{n}`.

WHY A DIFFERENCE AMPLIFIER HERE, AND WHY IT IS NOT REDUNDANT WITH 10a'S OWN FRONT ENDS:
10a's 16 difference receivers reject a ground offset between each SENSOR's own return and
this board's AGND -- their OUTPUT (the `A_*` net) already lands safely in the AGND domain.
This sheet's own difference amplifiers solve a DIFFERENT, one-level-up problem: Intan's own
analog inputs are single-ended BNC (spec Sec.5.5), so there is no differential receiver on
the INTAN SIDE to exploit, and INTAN_GND is a genuinely isolated domain that can (and, per
spec Sec.5.3/8, structurally does) sit at a different potential than AGND. So EACH of these
8 amplifiers is wired exactly like a 10a front end, one domain further out: `"+"` takes the
signal (`MUX_OUT{n}`, already AGND-referenced), `"-"` takes AGND ITSELF (not a sensor's
shield -- AGND is this stage's own "own return" to reject as common mode), and `REF` (the
pin that sets where the OUTPUT lands) takes `INTAN_GND` instead of AGND. Output = MUX_OUTn
- AGND + INTAN_GND -- the AGND-to-INTAN_GND ground offset is rejected exactly the way 10a's
own front ends reject a sensor's shield-to-AGND offset, and the result lands correctly in
INTAN_GND's own domain. This is the textbook "ground-loop-breaker" application for this
exact part (INA105's own datasheet names it), not a novel circuit.

INA105KU substitutes for the brief's named `INA134` -- INA134 has no stock KiCad symbol and
is an audio-specific part; INA105 fills the identical topological role (TI's own general-
purpose precision unity-gain difference amplifier) and is the SAME part 10a's own 13 plain
difference receivers already use, under this project's own established '541-family
Value-substitution precedent' (hardware/README.md). Pin roles (REF=1, "-"=2, "+"=3, V-=4,
SENSE=5, OUT=6, V+=7, NC=8) are unchanged from 10a's own diffamp_ina105() -- redefined here
independently rather than imported (this project's own "never import from a sibling
generator/checker" discipline), because THIS sheet's own REF/"-" wiring is different (REF=
INTAN_GND not AGND, "-"=AGND not a shield net) -- 10a's own diffamp_ina105() hardcodes REF=
AGND and cannot be reused as-is.

ISOLATION: EACH INA105KU IS THE ONE PART PER CHANNEL THAT STRADDLES THE DOMAIN BOUNDARY BY
DESIGN -- its own "-" pin sits on AGND while its power (V+/V-=ISO_P12/ISO_N12), REF, and
SENSE/OUT all sit in the INTAN_GND domain. This is the SAME class of deliberate straddle
check_breakout_power_netlist.py's own pre-existing isolation-barrier check (`ISO_NETS` vs.
`NON_ISO_NETS`, checked PIN-level not reference-level, specifically so U2 -- the isolated
DC-DC converter -- straddles by design without tripping it) already tolerates: no single
PIN of any INA105KU here sits on two nets at once, only the REFERENCE (component) has some
pins in AGND and others in ISO_P12/ISO_N12/INTAN_GND, exactly the pattern that checker's
own docstring explains is legitimate. Nothing else on this sheet straddles: the muxes'
S1-S16/D/EN/VDD/VSS/GND are ALL either the 16 (already-AGND-referenced) `A_*` nets,
`MUX_OUT{n}` (also AGND-domain), or `+12V`/`-12V`/`AGND` themselves -- never `ISO_P12`/
`ISO_N12`/`INTAN_GND`. The BNCs' own shells go directly to `INTAN_GND` (never `AGND`) --
Intan's shield IS its return (spec Sec.5.5), so a solid bond is the correct termination at
a dedicated output jack, unlike 10a's own rig-facing BNCs (which bond through ~10R because
that shield ALSO carries a sensing reference an external, possibly-bonded-elsewhere shield
needs headroom against -- not this stage's situation at all: this BNC's shell has no other
job).

ADG1206YRUZ: no stock KiCad symbol exists in the 28-lead TSSOP package this design's own
hand-solderability constraint requires (the brief itself names "ADG1206 in TSSOP-28" as
"the worst case and is acceptable" -- i.e. TSSOP-28, not the only LFCSP-32 option KiCad
ships a differently-configured relative of in its stock Analog_Switch library, is the
package this board commits to). Built as a new custom symbol in hardware/lib/wl-sync.
kicad_sym (gen_wl_sync_lib.py, following the TPS7A4901/TPS7A3001 precedent -- a hand-built
symbol with a REAL, datasheet-sourced pin table, not a Value-overridden stand-in, because
no stock symbol shares this part's real 28-pin TSSOP numbering). Pin table is Analog
Devices' own ADG1206/ADG1207 Rev.0 datasheet, Table 4 "ADG1206 Pin Function Descriptions"
(28-Lead TSSOP), transcribed verbatim -- see that generator's own ADG1206YRUZ block
comment for the full table, the fix-round-1 account of the two-pin A3 hedge it replaced
(task-10c-report.md), and the address truth table (also hardware/README.md). A3 is pin
14; pins 2, 3, and 13 are this part's only NC pins and carry no net on this sheet.
Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm (stock, already registered) models the real part;
no new fp-lib-table/sym-lib-table entries needed (Amplifier_Difference, Connector_Coaxial,
Device, Package_SO, and wl-sync were all already registered by Task 6/10a).

EN TIED HIGH, TO +12V SPECIFICALLY -- this task's own "Consumes" list names `+12V`/`-12V`/
`AGND`/`ISO_P12`/`ISO_N12`/`INTAN_GND` and nothing else; +12V is both already-present on
this sheet (every mux's own VDD) and an unambiguous, always-valid CMOS logic high referenced
to GND/VSS for ANY digital input, so tying EN there needs no new rail this task's own
contract doesn't already name. EN is confirmed active-high by Table 4 itself (see
gen_wl_sync_lib.py's own ADG1206YRUZ block comment): low disables the device (all switches
off), high enables it and lets A0-A3 select -- exactly the always-enabled, address-only
behaviour this tie is meant to produce. (The 32 address lines, MUX1_A0..MUX8_A3, are the OPPOSITE
case -- genuinely left for Task 12's own I2C expanders to drive, appearing here only as
`isolated_pin_label` ERC warnings awaiting that task, exactly like 10a/10b's own *_TPC/*_BUF
labels awaited THEIR consuming tasks.)

S1-S16/A0-A3 PIN NAMES are Analog Devices' own (Table 4 of the real datasheet -- see
gen_wl_sync_lib.py's own ADG1206YRUZ block comment, fix round 1, task-10c-report.md), not
this project's reconstruction: physical pin 19 IS S1, physical pin 14 IS A3, a sourced
fact, not a choice. What remains this SHEET's own declared, arbitrary-but-fixed convention
(matching gen_breakout_analog_ni.py's own precedent for its AI-channel-number assignment):
which of the 16 `A_*` sources rides physical pin "S1" vs. "S9" on every mux -- no
datasheet fixes a board's own source-to-channel assignment. `ALL_16_NETS` below is
consumed in list order against `S_PIN_NUMBERS` (also list order): `ALL_16_NETS[i]` rides
the physical pin at `S_PIN_NUMBERS[i]` on EVERY one of the 8 muxes (same order on all 8,
so a given address value selects the same relative source on every mux). The address-
value-to-S-number truth table itself (0000 selects S1 ... 1111 selects S16, EN high
throughout) is Table 4's own fact too, recorded for Task 12 in hardware/README.md ("ADG1206
mux address truth table").

COORDINATE COLLISIONS -- this project has hit a real rail short from exactly this class of
defect before (analog-frontend.kicad_sch's own task-10a-report.md). Avoided here by
construction: every role (mux column, mux decoupling, diffamp column, diffamp decoupling,
series-R column, BNC column) gets its own distinct X, and ROW_DY (63.5mm) is sized against
THIS sheet's own tallest part's real pin span -- the custom ADG1206YRUZ symbol's own 16-row
left column spans +-19.05mm locally (computed from gen_wl_sync_lib.py's build_ic_symbol()
own PITCH=2.54mm), leaving a full 25.4mm clear gap between consecutive rows' own mux pin
spans, let alone the much smaller INA105KU/resistor/BNC footprints at the same row. Verified
empirically, not just argued: see check_breakout_mux_intan_netlist.py's own
`_check_no_coordinate_collisions()`, with a negative control confirming it fires.

Non-negotiables carried over from every prior child-sheet generator (hardware/README.md's
own "KiCad gotchas"; constraint numbers match this task's own brief):
  1. lib_symbols handled entirely by kicad_sch.py's own extract_symbol()/ensure_lib_symbol()
     (full lib id keys, bare child-unit names) -- this file never hand-writes lib_symbols
     text, so constraint 1 is satisfied by construction.
  2. `(instances (path ...))` -- this sheet reads breakout.kicad_sch's own committed text for
     find_root_uuid()/find_sheet_instance_path(), exactly like every prior child sheet.
  3. Refdes seeding clears ALL FIVE already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend, analog-ni) via merge_max_refs().
  4. Coordinate collisions -- see above.
  5. Footprint pad adjacency -- N/A to this sheet's own parts: nothing here relies on two
     SPECIFIC pads of one footprint being physically adjacent the way a jumper/shorting
     block does (task-10b-report.md's own finding). (Fix round 1, task-10c-report.md,
     removed the one thing on this sheet that had come close -- a two-pin A3 hedge relying
     on two independent traces to one net, never on physical pad adjacency; the real
     datasheet made the hedge unnecessary, so this constraint now has nothing at all to
     check on this sheet.)

Run directly: `python3 hardware/gen/gen_breakout_mux_intan.py` (writes
hardware/breakout/sheets/mux-intan.kicad_sch). Requires hardware/breakout/breakout.kicad_sch
and all five sibling sheets (power, taskpc-digital, pi-interface, analog-frontend,
analog-ni) to already exist on disk, and hardware/lib/wl-sync.kicad_sym to already carry
ADG1206YRUZ (gen_wl_sync_lib.py, run first).
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
POWER_SCH = OUT / "power.kicad_sch"                     # already-committed sibling (Task 7)
TASKPC_SCH = OUT / "taskpc-digital.kicad_sch"            # already-committed sibling (Task 8)
PI_INTERFACE_SCH = OUT / "pi-interface.kicad_sch"        # already-committed sibling (Task 9)
ANALOG_FRONTEND_SCH = OUT / "analog-frontend.kicad_sch"  # already-committed sibling (Task 10a)
ANALOG_NI_SCH = OUT / "analog-ni.kicad_sch"              # already-committed sibling (Task 10b)
MUX_INTAN_SHEETFILE = "sheets/mux-intan.kicad_sch"  # exactly as gen_breakout.py's own
# SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- Amplifier_Difference/Connector_Coaxial/Device/Package_SO/wl-sync all
# already registered (Task 6/10a); no sym-lib-table/fp-lib-table changes needed.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"          # INA105KU
FOOTPRINT_TSSOP28 = "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm"      # ADG1206YRUZ
FOOTPRINT_BNC = "Connector_Coaxial:BNC_PanelMountable_Vertical"  # same isolated BNC every
# other panel BNC on this board uses (spec Sec.9.1's "Isolated BNCs throughout").

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
ROW_DY = GRID(63.5)  # 25 x 2.54mm -- comfortably clears the custom ADG1206YRUZ symbol's
# own real pin span (+-19.05mm locally, 16-row left column at build_ic_symbol()'s own
# PITCH=2.54mm -- see gen_wl_sync_lib.py): a 38.1mm-tall part gets a 63.5mm row pitch,
# leaving a full 25.4mm clear gap between consecutive rows' own mux pin spans. Every other
# part on this sheet (INA105KU/SOIC-8, a 2-pin resistor, a 2-pin BNC) has a far smaller
# local pin span, so this one figure governs the whole sheet's own row safety margin.

X_MUX = GRID(50)          # ADG1206YRUZ column
X_DIFFAMP = GRID(120)     # INA105KU column
X_R_OUT = GRID(150)       # 100R series (diffamp output -> BNC)
X_BNC = GRID(175)         # Intan analog-out BNC column
DECOUPLE_DX = GRID(15.24)  # reused verbatim from every prior analog sheet -- own distinct
# column, left of whichever IC it decouples (X_MUX-DECOUPLE_DX and X_DIFFAMP-DECOUPLE_DX
# are themselves two more distinct X's: 50-15.24=34.76, 120-15.24=104.76 -- neither
# coincides with X_MUX/X_DIFFAMP/X_R_OUT/X_BNC/each other).
DECOUPLE_DY = GRID(5.08)  # reused verbatim -- Device:C's own pins reach +-3.81mm locally,
# so 5.08+3.81=8.89mm is this column's own real Y-reach, far inside ROW_DY's 63.5mm pitch.

X_NOTE1, Y_NOTE1 = GRID(210), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(210), GRID(110)
X_NOTE3, Y_NOTE3 = GRID(210), GRID(190)
X_NOTE4, Y_NOTE4 = GRID(210), GRID(270)
X_NOTE5, Y_NOTE5 = GRID(210), GRID(340)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# Channel contract -- redefined here from spec Sec.3.2's own "16 sources" table (same
# independence discipline every checker/generator pair in this project already follows;
# order matches check_breakout_analog_frontend_netlist.py's/gen_breakout_analog_ni.py's own
# canonical ALL_16_NETS order).
# ---------------------------------------------------------------------------
ALL_16_NETS = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_NETS) == 16
assert len(set(ALL_16_NETS)) == 16

# ADG1206YRUZ's own S1-S16 physical pins, in S1..S16 order -- Table 4 of the real
# datasheet (gen_wl_sync_lib.py's own ADG1206_PINS["left"]), a sourced fact as of fix
# round 1 (task-10c-report.md). ALL_16_NETS[i] rides S_PIN_NUMBERS[i] on every one of the
# 8 muxes -- THAT pairing (which A_* source is "S1") is this sheet's own declared,
# arbitrary-but-fixed convention (see module docstring); the S-number-to-physical-pin
# side of it is not.
S_PIN_NUMBERS = ["19", "20", "21", "22", "23", "24", "25", "26", "11", "10", "9", "8", "7", "6", "5", "4"]
assert len(S_PIN_NUMBERS) == 16
assert len(set(S_PIN_NUMBERS)) == 16

N_MUX = 8

# ADG1206YRUZ non-S pins used by this sheet (see gen_wl_sync_lib.py's own ADG1206_PINS).
# Pins 2, 3, and 13 are this part's only NC pins (Table 4) and are not labelled by this
# sheet at all -- left genuinely unconnected, like every other NC pin in this project.
MUX_PIN_VDD = "1"
MUX_PIN_D = "28"  # single common output -- Table 4 names it "D"; pin 2 is NC, not a
# second drain (fix round 1, task-10c-report.md, corrected the prior D1/D2-tied-together
# assumption, which had reasoned by analogy from ADG1207's separate DA/DB pins instead of
# the ADG1206's own real, single-output pin table).
MUX_PIN_GND = "12"
MUX_PIN_VSS = "27"
MUX_PIN_EN = "18"
MUX_PIN_A0 = "17"
MUX_PIN_A1 = "16"
MUX_PIN_A2 = "15"
MUX_PIN_A3 = "14"

# INA105 pin roles -- real TI datasheet pinout (SOIC-8), redefined here independently of
# gen_breakout_analog_frontend.py's own diffamp_ina105() (whose REF/"-" wiring differs from
# this sheet's own -- see module docstring for why that helper isn't reused as-is).
INA105_REF = "1"
INA105_MINUS = "2"
INA105_PLUS = "3"
INA105_VNEG = "4"
INA105_SENSE = "5"
INA105_OUT = "6"
INA105_VPOS = "7"


# ---------------------------------------------------------------------------
# Low-level helpers -- duplicated from prior generators' own lbl()/two_pin() rather than
# imported: kicad_sch.py, not any one generator, is this project's shared machinery (same
# convention every generator here follows).
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
    """Unlike every prior sheet's own decouple() (which hardcodes AGND as the second pin
    -- correct for every IC those sheets place, since all of them live in the AGND domain),
    this sheet places ICs in TWO different domains (muxes in AGND; INA105KUs in ISO_P12/
    ISO_N12/INTAN_GND) -- `gnd` is REQUIRED, not defaulted, specifically so a call site
    cannot accidentally decouple an isolated-domain IC to AGND by falling through to a
    hardcoded default (exactly the class of mistake this task's own brief warns against:
    "none references AGND").
    """
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, gnd, footprint=FOOTPRINT_C_SMALL)


# ---------------------------------------------------------------------------
# One mux + difference-amplifier + output stage
# ---------------------------------------------------------------------------


def mux_and_diffamp(sch, y, n):
    """One of the 8 Intan channels, at row `y`: ADG1206YRUZ (all 16 A_* sources on its
    S1-S16, single D output to MUX_OUT{n}, EN tied to +12V, address on MUX{n}_A0..A3)
    -> INA105KU ("+"=MUX_OUT{n}, "-"=AGND, REF=INTAN_GND, SENSE=OUT unity gain, powered
    ISO_P12/ISO_N12) -> 100R series -> INTAN_AO{n} -> BNC (shell to INTAN_GND). Returns
    (mux_ref, diffamp_ref, bnc_ref).
    """
    mux_out_net = f"MUX_OUT{n}"

    # --- ADG1206YRUZ ---
    mux_ref = sch.next_ref("U")
    mpins = sch.place(
        "wl-sync", "ADG1206YRUZ", mux_ref, "ADG1206YRUZ", X_MUX, y, footprint=FOOTPRINT_TSSOP28,
    )
    for source_net, pin_num in zip(ALL_16_NETS, S_PIN_NUMBERS):
        lbl(sch, X_MUX, y, mpins, pin_num, source_net)
    lbl(sch, X_MUX, y, mpins, MUX_PIN_D, mux_out_net)
    lbl(sch, X_MUX, y, mpins, MUX_PIN_VDD, "+12V")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_VSS, "-12V")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_GND, "AGND")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_EN, "+12V")  # tied high -- active-high per Table 4,
    # always enabled
    lbl(sch, X_MUX, y, mpins, MUX_PIN_A0, f"MUX{n}_A0")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_A1, f"MUX{n}_A1")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_A2, f"MUX{n}_A2")
    lbl(sch, X_MUX, y, mpins, MUX_PIN_A3, f"MUX{n}_A3")
    # pins 2, 3, 13 (NC) are typed no_connect in the symbol itself -- no explicit marker
    # needed, same precedent as 10a's own INA105KU pin 8. No hedge pin to label (fix round
    # 1, task-10c-report.md): Table 4 confirms A3 is pin 14 alone.
    decouple(sch, X_MUX - DECOUPLE_DX, y - DECOUPLE_DY, "+12V", "AGND")
    decouple(sch, X_MUX - DECOUPLE_DX, y + DECOUPLE_DY, "-12V", "AGND")

    # --- INA105KU difference amplifier ---
    da_ref = sch.next_ref("U")
    apins = sch.place(
        "Amplifier_Difference", "INA105KU", da_ref, "INA105KU", X_DIFFAMP, y, footprint=FOOTPRINT_SOIC8,
    )
    buf_net = f"INTAN_AO{n}_BUF"
    lbl(sch, X_DIFFAMP, y, apins, INA105_REF, "INTAN_GND")   # output lands in INTAN_GND
    lbl(sch, X_DIFFAMP, y, apins, INA105_MINUS, "AGND")      # the offset being rejected
    lbl(sch, X_DIFFAMP, y, apins, INA105_PLUS, mux_out_net)  # the selected source
    lbl(sch, X_DIFFAMP, y, apins, INA105_VNEG, "ISO_N12")
    lbl(sch, X_DIFFAMP, y, apins, INA105_SENSE, buf_net)     # unity gain: SENSE=OUT
    lbl(sch, X_DIFFAMP, y, apins, INA105_OUT, buf_net)
    lbl(sch, X_DIFFAMP, y, apins, INA105_VPOS, "ISO_P12")
    # pin 8 (NC) typed no_connect in the library itself -- no marker needed (10a precedent).
    decouple(sch, X_DIFFAMP - DECOUPLE_DX, y - DECOUPLE_DY, "ISO_P12", "INTAN_GND")
    decouple(sch, X_DIFFAMP - DECOUPLE_DX, y + DECOUPLE_DY, "ISO_N12", "INTAN_GND")

    # --- 100R series -> BNC, shell to INTAN_GND ---
    final_net = f"INTAN_AO{n}"
    two_pin(sch, "Device", "R", "R", "100", X_R_OUT, y, buf_net, final_net, footprint=FOOTPRINT_R)
    bnc_ref = sch.next_ref("J")
    bpins = sch.place(
        "Connector", "Conn_Coaxial", bnc_ref, f"Intan analog output {n} (BNC)",
        X_BNC, y, footprint=FOOTPRINT_BNC,
    )
    lbl(sch, X_BNC, y, bpins, "1", final_net)
    lbl(sch, X_BNC, y, bpins, "2", "INTAN_GND")

    return mux_ref, da_ref, bnc_ref


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, MUX_INTAN_SHEETFILE)
    ref_start = merge_max_refs(
        find_max_refs(POWER_SCH.read_text()),
        find_max_refs(TASKPC_SCH.read_text()),
        find_max_refs(PI_INTERFACE_SCH.read_text()),
        find_max_refs(ANALOG_FRONTEND_SCH.read_text()),
        find_max_refs(ANALOG_NI_SCH.read_text()),
    )

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {"mux": [], "diffamp": [], "bnc": []}

    for n in range(1, N_MUX + 1):
        y = GRID(Y0 + (n - 1) * ROW_DY)
        mux_ref, da_ref, bnc_ref = mux_and_diffamp(sch, y, n)
        refs["mux"].append(mux_ref)
        refs["diffamp"].append(da_ref)
        refs["bnc"].append(bnc_ref)

    for line_idx, line in enumerate([
        "Intan mux bank (Task 10c) -- eight ADG1206YRUZ 16:1 analog multiplexers, each",
        "with ALL 16 A_* sources (Task 10a) on its own S1-S16 inputs, so any source can",
        "reach any of Intan's 8 channels. EN tied to +12V (active-high per Table 4,",
        "always enabled -- selection is by address only). Each mux's own single D",
        "output (pin 28, this part's own one common drain) feeds MUX_OUT{n} into one",
        "INA105KU difference amplifier -> 100R series -> BNC as INTAN_AO1..INTAN_AO8.",
        "",
        "Address lines MUX1_A0..MUX8_A3 (32 nets) are exposed here and consumed by Task",
        "12's own I2C GPIO expanders -- they show as isolated_pin_label ERC warnings on",
        "THIS sheet until that task adds a driver, same pattern as every prior child",
        "sheet's own labels awaiting a later task (10a's *_TPC, 10b's *_BUF). Address",
        "truth table (A3 A2 A1 A0 = 0000 selects S1 ... 1111 selects S16, EN high",
        "throughout) is in hardware/README.md for Task 12.",
        "",
        "Why 8 of 16, in software rather than a jumper: Intan's own ceiling is 8 analog",
        "inputs (2 base controller + 6 I/O Expander). The mux makes the choice of which",
        "8 a recorded, changeable software state (the sync module's own I2C write),",
        "instead of a physical fact a jumper would require documenting for a decade.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "THE MUX SITS IN AGND, AHEAD OF the difference amplifier (spec Sec.6.3) -- its",
        "on-resistance (~100-200R typical) is harmless into the INA105KU's own",
        "high(er)-impedance input, and charge injection from an address change appears",
        "only at switch time, never during a steady recording. Every mux pin used here",
        "(S1-S16, D, EN, VDD/VSS/GND) is one of the 16 A_* nets, MUX_OUT{n}, or",
        "+12V/-12V/AGND -- NEVER ISO_P12/ISO_N12/INTAN_GND. The mux itself never",
        "straddles the isolation boundary.",
        "",
        "EACH INA105KU IS THE ONE PART PER CHANNEL THAT DOES STRADDLE IT, BY DESIGN --",
        "its own '-' pin reads AGND while its power (ISO_P12/ISO_N12), REF, and",
        "SENSE/OUT all sit in INTAN_GND. This is the same class of deliberate straddle",
        "check_breakout_power_netlist.py's own isolation-barrier check already permits",
        "for U2 (the isolated DC-DC): checked PIN-level, not reference-level, so one",
        "component with SOME pins on each side is correct, not a violation. No INA105KU",
        "here references AGND anywhere except this one designed '-' input; none of the",
        "eight ever ties its REF pin to AGND.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "WHY A DIFFERENCE AMPLIFIER HERE (not redundant with 10a's own front ends):",
        "Intan's analog inputs are single-ended BNC, so the shield IS the return and",
        "there is no differential receiver on Intan's own side to exploit (spec",
        "Sec.5.5). INTAN_GND can sit at a different potential than AGND (it is a",
        "genuinely isolated domain, spec Sec.5.3/8) -- the SAME ground-offset problem",
        "10a's own front ends solve for a sensor's shield-to-AGND offset, one domain",
        "further out. Wired identically to a 10a front end: '+' = signal (MUX_OUT{n},",
        "already AGND-referenced), '-' = AGND itself (this stage's own 'return' to",
        "reject as common mode), REF = INTAN_GND (where the output lands). This is",
        "INA105's own datasheet-documented 'ground-loop-breaker' application, not a",
        "new circuit invented for this sheet.",
        "",
        "BNC shells go DIRECTLY to INTAN_GND, never AGND, and never through a bond",
        "resistor -- unlike 10a's own rig-facing BNCs (~10R to AGND, because THAT",
        "shield also carries a sensing reference against a possibly-bonded-elsewhere",
        "cage). This BNC's shell has no other job: it is Intan's own return, full stop.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "ADG1206YRUZ: no stock KiCad symbol exists in the 28-lead TSSOP package this",
        "board's own hand-solderability constraint requires (KiCad's stock",
        "Analog_Switch library only carries a differently-configured 32-lead LFCSP",
        "relative, ADG1207BCPZ -- confirmed directly against that symbol's own",
        "Footprint property). Built as a NEW custom symbol in hardware/lib/wl-sync.",
        "kicad_sym (gen_wl_sync_lib.py), following the TPS7A4901/TPS7A3001 precedent:",
        "a real, datasheet-sourced pin table, not a Value-overridden stand-in (no",
        "stock symbol shares this part's real 28-pin numbering to stand in for).",
        "",
        "PIN MAP, DEFINITIVE (fix round 1, task-10c-report.md): Table 4 of Analog",
        "Devices' own ADG1206/ADG1207 Rev.0 datasheet, 28-Lead TSSOP column,",
        "transcribed verbatim -- see gen_wl_sync_lib.py's own ADG1206YRUZ block comment",
        "for the full 28-pin table. A3 is pin 14. Pins 2, 3, and 13 are this part's",
        "only NC pins and carry no net on this sheet.",
        "",
        "NO HEDGE: the originally committed version of this symbol could not",
        "independently confirm pin 13 vs. 14 for A3 (12+ fetch attempts, mostly",
        "blocked/timed out rather than conflicting) and wired BOTH pins to the same",
        "A3 net rather than guess silently. That was not a safe hedge -- two different",
        "physical pins tied to one net is a short between whatever those pins actually",
        "are, and would have been a fault, not a redundancy, had pin 13 turned out to",
        "be a supply or source pin instead of NC. This fix round replaced the hedge",
        "with the real datasheet table above.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "S1-S16/A0-A3 pin NAMES are the datasheet's own (Table 4, fix round 1,",
        "task-10c-report.md) -- physical pin 19 IS S1, physical pin 14 IS A3, a",
        "sourced fact, not a choice. WHICH A_* source rides which mux pin (S1 vs.",
        "S9) is still this sheet's own declared, arbitrary-but-fixed choice",
        "(ALL_16_NETS/S_PIN_NUMBERS list order) -- like gen_breakout_analog_ni.py's",
        "own AI-channel-number assignment, no datasheet fixes a board's own source-",
        "to-channel assignment. Same order used on all 8 muxes, so one address value",
        "selects the same relative source everywhere. Address truth table (0000",
        "selects S1 ... 1111 selects S16, EN high) is in hardware/README.md.",
        "",
        "INA105KU substitutes for the brief's named INA134 (no stock KiCad symbol,",
        "audio-specific part) -- same substitution 10a's own 13 plain difference",
        "receivers already use, hardware/README.md's '541-family Value-substitution",
        "precedent'. Pin roles redefined independently of 10a's own diffamp_ina105()",
        "(REF=AGND, hardcoded) since this sheet's own REF wiring differs (REF=",
        "INTAN_GND) -- that helper is not reused as-is.",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "mux-intan.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
