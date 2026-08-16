"""Generator for hardware/breakout/sheets/analog-ni.kicad_sch -- Task 10b of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-10-brief.md, Steps 2 and 3 only -- Task 10 was split into 10a-10d, one sheet each. 10a
(analog-frontend, already committed) produces the 16 `A_*` source nets this sheet fans out;
10c (mux-intan) and 10d (comparators) are separate sheets this task does not touch.

THE FAN-OUT, restated once here rather than per channel: every one of the 16 `A_*` sources
(Task 10a) is buffered TWICE, independently, by its own `OPA4192`-value unity-gain follower
(`"+"` = source net, `"-"` shorted directly to `OUT` -- zero gain, matching this project's
own established "buffer" convention, e.g. analog-frontend.kicad_sch's INA105 SENSE=OUTPUT or
its Sallen-Key follower stages) + 100 ohm series resistor:

  (a) ALL 16, to the recording NI's own Connector 0 -- a 68-pin MDR THIS sheet places (the
      recording NI's OWN connector, physically distinct from the task PC's Connector 0 that
      Task 8 already placed on taskpc-digital.kicad_sch). AGND additionally ties to the
      connector's AISENSE pin -- the single wire that makes NRSE work (spec Decision 5): every
      AI channel reads against this one shared reference instead of needing 16 differential
      pairs. Task 11 places the recording NI's Connector 1 (digital) separately.
  (b) 9 of the 16 (no pupil, photodiode, ambient, accelerometer or microphone -- those reach
      NI and Intan only, spec's own asymmetry, not "fixed" here), to the task PC's Connector
      0 -- Task 8 (taskpc-digital.kicad_sch) already placed that connector and its own 9
      global labels (`A_EYE_LX_TPC` etc.); this sheet places NO connector for these, it only
      drives those SAME pre-existing label names from its own buffer outputs, exactly the
      cross-sheet-by-global-label convention every sheet in this project already uses.

Each of the 16 sources therefore gets driven by TWO independent op-amp buffers when it feeds
both destinations (e.g. A_EYE_LX -> one buffer -> A_EYE_LX_NI, AND a SEPARATE buffer ->
A_EYE_LX_TPC) -- never one op-amp output fanned into two cables, which would let one
destination's own load/fault couple into the other's.

AI-CHANNEL-NUMBER ASSIGNMENT (this sheet's own declared, arbitrary-but-fixed choice): which
of the 16 `A_*` sources rides AI0 vs AI1 vs ... AI15 is not fixed by any external source --
like Task 8's own note on its 9 AI-channel assignments, "an NI AI channel is an electrically
interchangeable ADC mux input with no fixed bit-weight semantics" (unlike a P0.x digital
line's bit weight), so no spec/manual needs to fix this the way NI's own manual fixes WHICH
PHYSICAL PIN carries a given AI channel NUMBER. `ALL_16_NETS` below (redefined from spec
Sec.3.2's own "16 sources" table, matching check_breakout_analog_frontend_netlist.py's own
canonical `ALL_16_NETS` order so the two sheets' own documentation reads the same way) is
consumed in list order: `ALL_16_NETS[i]` rides AI channel `i`.

PIN SOURCING -- physical MDR68 pins for AI0-AI15 + AISENSE on the recording NI's Connector 0,
same National Instruments "X Series User Manual: NI 632x/634x/635x/636x/637x/638x/639x
Devices" (part number 370784K-01, May 2019 edition, ni.com/manuals) Task 8 already sourced
Connector-0 physical pins from -- Figure A-5, "NI PCIe-6323/6343 Pinout" (the task PC's own
card family), THE SAME FIGURE, per this task's own brief ("Source physical pins from NI's X
Series User Manual (370784K-01), the same figure Task 8 used for the task PC -- do not assign
sequentially"). Retrieved fresh for this task from NI's OWN documentation host
(docs-be.ni.com/bundle/pcie-pxie-usb-63xx-features/raw/resource/enus/370784k.pdf -- an even
more authoritative source than Task 8's own Farnell/manualslib mirrors), text-extracted with
`pdftotext -layout` and read directly off Figure A-5's own two 68-pin column tables. The 9
values Task 8 already committed (AI0-AI8: 68,33,65,30,28,60,25,57,34; AISENSE: 62) were
independently re-derived here from this fresh extraction and matched EXACTLY before the
remaining 7 (AI9-AI15) were trusted -- same cross-check discipline Task 8's own report
describes ("parsed the Figure A-5 table twice independently... confirmed the two parses
agreed on every single pin... before using either").

**Worth recording plainly: the recording NI is a PXIe-6353, not a PCIe-6323/6343** (spec
Sec.9.3, "Recording: PXIe-6353. Task PC: PCIe-6343"), so Figure A-5 is not literally that
card's own named figure. Checked directly against the full manual text (grepped for every
"6353"/"6363"/"PXIe-6353" occurrence): the manual's own Appendix A has a SEPARATE figure for
this device family, Figure A-18 "NI PCIe-6353 and NI PCIe/PXIe-6363 Pinout" -- and the string
"PXIe-6353" appears NOWHERE in the manual at all (only "PCIe-6353" and "PCIe/PXIe-6363" are
named; the manual's own Appendix A groups devices that share one physical connector pinout
under one figure, e.g. the 6363 entry already covers its own PXIe variant this way). Rather
than treat this as a reason to deviate from the brief's own explicit instruction, Figure
A-18's own Connector-0 table was read directly too, as a cross-check: it is BYTE-FOR-BYTE
IDENTICAL to Figure A-5's on every one of the 17 pins this sheet actually uses (AI0-AI15,
AISENSE) -- confirmed by direct comparison of both figures' own extracted text, not assumed
from the "identical to the 6363 on every axis this design uses" line spec Sec.9.3 already
states. So the brief's own instruction to reuse Task 8's figure and an independent check
against the device-specific figure land on the exact same 17 physical pins; both are recorded
here rather than silently picking one.

MDR0_AI_PIN = {0:68, 1:33, 2:65, 3:30, 4:28, 5:60, 6:25, 7:57, 8:34, 9:66, 10:31, 11:63,
12:61, 13:26, 14:58, 15:23}; MDR0_AISENSE_PIN = 62.

OPA4192 VALUE SUBSTITUTION -- no stock KiCad symbol exists for OPA4192/OPA2192/OPA192 in any
package (confirmed directly against Amplifier_Operational.kicad_sym's own raw text: grepped
for "OPA4192"/"OPA192"/"OPA2192", zero matches, vs. a full family of OPA4197/OPA2197/OPA197xD
entries that DOES exist -- the same part family analog-frontend.kicad_sch already places).
Per this codebase's own established precedent for exactly this situation (hardware/README.md's
"541-family Value-substitution precedent"; analog-frontend.kicad_sch's own INA134->INA105
substitution, argued at length in ITS OWN module docstring): place the real, available,
pin-compatible relative and override Value to the real ordered part. OPA4192 (TI's "36-V,
Precision, Rail-to-Rail Input/Output... E-Trim" quad op-amp -- same E-Trim precision op-amp
family branding as OPA4197, confirmed via TI's own product page) ships in the identical
14-SOIC package as OPA4197 with the identical standard quad-op-amp pinout (unit 1:
out=1/-in=2/+in=3; unit 2: +in=5/-in=6/out=7; unit 3: out=8/-in=9/+in=10; unit 4:
+in=12/-in=13/out=14; shared power: V+=4/V-=11 -- confirmed directly against this project's
own already-placed `Amplifier_Operational:OPA4197xD` extraction, the SAME symbol placed here
with Value overridden to "OPA4192IDR", TI's own real SOIC-14 order code, confirmed via web
search directly against TI's/DigiKey's own product listings). 36 V total supply comfortably
covers this sheet's own +-12V rails (24V total). No new sym-lib-table/fp-lib-table entries
needed: Amplifier_Operational was already registered at Task 6.

SPARE CHANNEL TIE-OFF -- the task-PC bank's 9 channels do not fill its 3rd quad IC's own 4
units (1 real + 3 spare). Each spare unit is tied off exactly like analog-frontend.kicad_sch's
own mic_channel() spare 4th channel: `"+"` to AGND (a defined, non-floating input), `"-"`
shorted to its own `OUT` (unity-gain follower, so the output is defined too, never left to
oscillate). Each spare gets its OWN, uniquely-named private net (`TPC_SPARE<n>_FB`), never a
single shared name across multiple spares -- sharing one name would tie multiple independent
op-amp OUTPUTS together, a real conflict between two low-impedance push-pull drivers (ERC's
own `pin_to_pin` rule flags exactly this), not a harmless coincidence.

COORDINATE COLLISIONS -- this project has hit a real rail short from exactly this class of
defect before (analog-frontend.kicad_sch's own task-10a-report.md, and that file's own
X_DIV_SHLD comment: "this sheet's own history... already hit a real rail short from two rows'
own Y arithmetic coinciding exactly"). This sheet avoids it by construction, not merely by
argument: every "role" (real-channel op-amp column, series-resistor column, MDR68 connector,
per-IC power/decoupling column) gets its OWN, distinct X coordinate, so only same-role,
same-column items can ever coincide, and row pitch (ROW_DY, reused verbatim from
analog-frontend.kicad_sch's own already-proven value) comfortably clears every part's own
local pin span at that column. Verified empirically, not just argued: see
check_breakout_analog_ni_netlist.py's own `_check_no_coordinate_collisions()`, which parses
this sheet's real rendered text for every global_label's own (x, y) and asserts no two
DIFFERENT net names ever share one, with a negative control confirming the check fires.

Non-negotiables carried over from every prior child-sheet generator (hardware/README.md's own
"KiCad gotchas"; constraint numbers match this task's own brief):
  1. lib_symbols handled entirely by kicad_sch.py's own extract_symbol()/ensure_lib_symbol()
     (full lib id keys, bare child-unit names) -- this file never hand-writes lib_symbols
     text, so constraint 1 is satisfied by construction.
  2. `(instances (path ...))` -- this sheet reads breakout.kicad_sch's own committed text for
     find_root_uuid()/find_sheet_instance_path(), exactly like every prior child sheet.
  3. Refdes seeding clears ALL FOUR already-committed siblings (power, taskpc-digital,
     pi-interface, analog-frontend) via merge_max_refs() -- analog-frontend is the newest
     sibling and the one every prior generator's own "extend this" comment flagged as needing
     the same treatment once it existed.
  4. Coordinate collisions -- see above.

Run directly: `python3 hardware/gen/gen_breakout_analog_ni.py` (writes
hardware/breakout/sheets/analog-ni.kicad_sch). Requires hardware/breakout/breakout.kicad_sch
and all four sibling sheets to already exist on disk.
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
ANALOG_NI_SHEETFILE = "sheets/analog-ni.kicad_sch"  # exactly as gen_breakout.py's own
# SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- every one already registered by an earlier task (see module docstring);
# no hardware/breakout/sym-lib-table or fp-lib-table changes needed for this sheet.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"          # OPA4192IDR (OPA4197xD stand-in)
FOOTPRINT_MDR68 = "wl-sync:MDR68_Male_RightAngle"                  # recording NI Connector 0

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


ROW_DY = GRID(22.86)      # reused verbatim from analog-frontend.kicad_sch's own already-
# proven row pitch (task-10a-report.md) -- comfortably clears every local pin span used on
# this sheet too (checked directly against each part's own extracted pin geometry while
# writing this file, not assumed): a real channel unit's own 3 pins span +-2.54mm locally,
# a series resistor's own 2 pins span +-3.81mm -- both far inside half of 22.86mm.
DECOUPLE_DX = GRID(15.24)  # reused verbatim from analog-frontend.kicad_sch -- puts the
# per-IC power/decoupling column on its OWN distinct X, never shared with the real-channel
# op-amp column or the series-resistor column (see module docstring, COORDINATE COLLISIONS).
DECOUPLE_DY = GRID(5.08)

Y0 = GRID(20)

X_BUF_NI = GRID(60)    # NI bank: real-channel op-amp units
X_R_NI = GRID(85)      # NI bank: 100R series resistors
X_BUF_TPC = GRID(140)  # TPC bank: real-channel op-amp units (own column, own bank)
X_R_TPC = GRID(165)    # TPC bank: 100R series resistors

X_CONN, Y_CONN = GRID(230), GRID(90)  # recording NI Connector 0 (MDR68) -- its own block,
# far enough from both buffer banks' own columns (60/85/140/165) that its own pin X's
# (230-10.16=219.84 .. 230+10.16=240.16, checked directly against the real MDR68_Male
# symbol's own extracted pin geometry) can never coincide with either bank's own columns.

X_NOTE1, Y_NOTE1 = GRID(280), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(280), GRID(90)
X_NOTE3, Y_NOTE3 = GRID(280), GRID(150)
X_NOTE4, Y_NOTE4 = GRID(280), GRID(230)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# Channel contract -- redefined here from spec Sec.3.2's own "16 sources" table (same
# independence discipline every checker/generator pair in this project already follows;
# order matches check_breakout_analog_frontend_netlist.py's own canonical ALL_16_NETS).
# ---------------------------------------------------------------------------
ALL_16_NETS = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_NETS) == 16
assert len(set(ALL_16_NETS)) == 16

# The 9 of 16 that additionally reach the task PC (Task 10 Step 3's own literal list) --
# deliberately NOT "fixed" to include pupil/photodiode/ambient/accelerometer/mic, per this
# task's own explicit instruction to preserve that asymmetry.
TPC_CHANNEL_NAMES = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY",
    "A_JOY_X", "A_JOY_Y", "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(TPC_CHANNEL_NAMES) == 9
assert all(n in ALL_16_NETS for n in TPC_CHANNEL_NAMES)

# AI-channel-number assignment (this sheet's own declared choice -- see module docstring).
NI_CHANNELS = list(zip(ALL_16_NETS, range(16)))
assert len(NI_CHANNELS) == 16

# Physical MDR68 pin per AI channel, recording NI's own Connector 0 -- sourced verbatim from
# NI's X Series User Manual (370784K-01), Figure A-5 (cross-checked against Figure A-18; see
# module docstring PIN SOURCING). NOT assigned sequentially.
MDR0_AI_PIN = {
    0: "68", 1: "33", 2: "65", 3: "30", 4: "28", 5: "60", 6: "25", 7: "57", 8: "34",
    9: "66", 10: "31", 11: "63", 12: "61", 13: "26", 14: "58", 15: "23",
}
assert set(MDR0_AI_PIN) == set(range(16))
MDR0_AISENSE_PIN = "62"  # Connector 0's own "AI SENSE" (not "AI SENSE 2", Connector 1's own
# -- this design uses only Connector 0's analog bank, same as Task 8's own task-PC connector).

# Standard quad-op-amp pinout, confirmed directly against Amplifier_Operational:OPA4197xD's
# own extracted pin geometry (the symbol placed here, Value overridden -- see module
# docstring, OPA4192 VALUE SUBSTITUTION) -- same mapping analog-frontend.kicad_sch's own
# mic_channel() already established for this exact symbol.
QUAD_UNIT = {
    1: {"plus": "3", "minus": "2", "out": "1"},
    2: {"plus": "5", "minus": "6", "out": "7"},
    3: {"plus": "10", "minus": "9", "out": "8"},
    4: {"plus": "12", "minus": "13", "out": "14"},
}
QUAD_POWER_UNIT = 5
QUAD_VPOS_PIN = "4"
QUAD_VNEG_PIN = "11"


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


def decouple(sch, x, y, rail):
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, "AGND", footprint=FOOTPRINT_C_SMALL)


# ---------------------------------------------------------------------------
# The fan-out itself
# ---------------------------------------------------------------------------


def buffer_bank(sch, x_buf, x_r, y0, channel_names, suffix, spare_tag):
    """One bank of unity-gain buffer channels, 4 real op-amp units per OPA4192-value quad
    IC package, `ceil(len(channel_names)/4)` packages total. Each real channel: source net
    (`channel_names[i]`) -> op-amp `"+"` (unity-gain follower -- `"-"` shorted directly to
    `OUT`) -> 100R series -> final net (`f"{channel_names[i]}{suffix}"`). A package whose
    own 4 units are not all filled (only the task-PC bank's last package, 9 channels = 2
    full + 1 partial) ties its remaining unit(s) off safely -- see module docstring, SPARE
    CHANNEL TIE-OFF. Power/decoupling placed once per PACKAGE (not per channel -- a quad
    IC shares one pair of supply pins), on the SAME x_buf column but at a Y that can never
    coincide with any real channel row (checked directly against this sheet's own
    coordinate-collision self-test, not merely reasoned about).

    Returns the list of IC references placed, in package order.
    """
    ic_refs = []
    n = len(channel_names)
    n_ics = -(-n // 4)  # ceil division
    for k in range(n_ics):
        group = channel_names[4 * k : 4 * k + 4]
        ref = sch.next_ref("U")
        for i, name in enumerate(group):
            unit = i + 1
            y = GRID(y0 + (4 * k + i) * ROW_DY)
            roles = QUAD_UNIT[unit]
            pins = sch.place(
                "Amplifier_Operational", "OPA4197xD", ref, "OPA4192IDR", x_buf, y,
                unit=unit, footprint=FOOTPRINT_SOIC14,
            )
            final_net = f"{name}{suffix}"
            buf_net = f"{final_net}_BUF"
            lbl(sch, x_buf, y, pins, roles["plus"], name)
            lbl(sch, x_buf, y, pins, roles["minus"], buf_net)
            lbl(sch, x_buf, y, pins, roles["out"], buf_net)
            two_pin(sch, "Device", "R", "R", "100", x_r, y, buf_net, final_net, footprint=FOOTPRINT_R)
        for i in range(len(group), 4):
            unit = i + 1
            y = GRID(y0 + (4 * k + i) * ROW_DY)
            roles = QUAD_UNIT[unit]
            pins = sch.place(
                "Amplifier_Operational", "OPA4197xD", ref, "OPA4192IDR", x_buf, y,
                unit=unit, footprint=FOOTPRINT_SOIC14,
            )
            spare_net = f"{spare_tag}_SPARE{4 * k + i}_FB"  # unique per spare instance --
            # see module docstring, SPARE CHANNEL TIE-OFF, for why a shared name would be
            # a real defect (two op-amp outputs shorted together), not a harmless one.
            lbl(sch, x_buf, y, pins, roles["plus"], "AGND")
            lbl(sch, x_buf, y, pins, roles["minus"], spare_net)
            lbl(sch, x_buf, y, pins, roles["out"], spare_net)
        y_power = GRID(y0 + (4 * k - 0.5) * ROW_DY)  # half a row BEFORE the package's own
        # first real channel row -- never an integer multiple of ROW_DY away from ANY real
        # channel row in this bank (4k-0.5 is never an integer), so it cannot coincide with
        # one regardless of package index; see module docstring, COORDINATE COLLISIONS.
        pinsp = sch.place(
            "Amplifier_Operational", "OPA4197xD", ref, "OPA4192IDR", x_buf, y_power,
            unit=QUAD_POWER_UNIT, footprint=FOOTPRINT_SOIC14,
        )
        lbl(sch, x_buf, y_power, pinsp, QUAD_VPOS_PIN, "+12V")
        lbl(sch, x_buf, y_power, pinsp, QUAD_VNEG_PIN, "-12V")
        decouple(sch, x_buf - DECOUPLE_DX, y_power - DECOUPLE_DY, "+12V")
        decouple(sch, x_buf - DECOUPLE_DX, y_power + DECOUPLE_DY, "-12V")
        ic_refs.append(ref)
    return ic_refs


def place_recording_ni_connector0(sch):
    """The recording NI's own Connector 0 (analog + AISENSE) -- a NEW, physically distinct
    68-pin MDR from the task PC's own Connector 0 Task 8 already placed on
    taskpc-digital.kicad_sch. All 16 AI channels wired to their own buffered `_NI` final
    nets (buffer_bank()'s own output, driven from THIS sheet, at whatever coordinates that
    bank uses -- connectivity is by label name, not position, same convention every sheet
    in this project already uses). AISENSE (pin 62) tied directly to AGND -- the single
    wire that makes NRSE work (spec Decision 5) and "the easiest thing on the board to omit
    by accident" (this task's own brief) -- annotated on-sheet below, and asserted in
    check_breakout_analog_ni_netlist.py. Every other pin (7 doesn't apply here -- unlike
    the task PC's own 9-of-16-populated Connector 0, THIS connector's entire AI0-15 range
    is used) stays no_connect: P0.x/P1.x/P2.x, D GND, +5V, AO0/AO1/AOGND, NC -- outside
    this task's own contract (Task 11 owns Connector 1's digital range on a separate sheet;
    this connector's own P0/P1/P2 pins carry no signal this design routes through Connector
    0 at all).
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "MDR68_Male", ref,
        "Connector 0 (analog + AISENSE, recording NI)",
        X_CONN, Y_CONN, footprint=FOOTPRINT_MDR68,
    )
    used = set()
    for name, ai_chan in NI_CHANNELS:
        pin_num = MDR0_AI_PIN[ai_chan]
        x, y = pin_pos(X_CONN, Y_CONN, pins[pin_num])
        sch.label(f"{name}_NI", x, y)
        used.add(pin_num)

    x, y = pin_pos(X_CONN, Y_CONN, pins[MDR0_AISENSE_PIN])
    sch.label("AGND", x, y)
    used.add(MDR0_AISENSE_PIN)

    for n in range(1, 69):
        num = str(n)
        if num not in used:
            x, y = pin_pos(X_CONN, Y_CONN, pins[num])
            sch.no_connect(x, y)
    return ref


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, ANALOG_NI_SHEETFILE)
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
    # The values below are this sheet's own committed minima minus one (U31-U37, C72-C85, J36, R78-R102).
    # Verified the way the whole family was: pin, regenerate, re-export the netlist and
    # confirm netlist-contract.json is byte-identical. Do NOT verify by diffing the
    # .kicad_sch -- several sheets were last written by KiCad rather than by their
    # generator, so the file reformats even when nothing electrical changes.
    ref_start = {"C": 71, "J": 35, "R": 77, "U": 30}

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    refs["conn0_ni"] = place_recording_ni_connector0(sch)
    refs["ni_ic"] = buffer_bank(sch, X_BUF_NI, X_R_NI, Y0, ALL_16_NETS, "_NI", "NI")
    refs["tpc_ic"] = buffer_bank(sch, X_BUF_TPC, X_R_TPC, Y0, TPC_CHANNEL_NAMES, "_TPC", "TPC")

    for line_idx, line in enumerate([
        "Analog NI fan-out (Task 10b) -- every one of the 16 A_* sources (Task 10a) gets",
        "its OWN OPA4192-value unity-gain buffer ('+' = source, '-' shorted to OUT) + 100R",
        "series, independently for each destination it reaches (never one op-amp output",
        "fanned into two cables).",
        "",
        "(a) ALL 16, to the recording NI's own Connector 0 (MDR68, placed on THIS sheet --",
        "    physically distinct from the task PC's own Connector 0 on",
        "    taskpc-digital.kicad_sch). Task 11 places the recording NI's Connector 1",
        "    (digital) separately.",
        "(b) 9 of 16 (A_EYE_LX/LY/RX/RY, A_JOY_X/Y, A_MISC1-3 -- NOT pupil, photodiode,",
        "    ambient, accelerometer or mic: that asymmetry is deliberate, spec's own",
        "    design, not 'fixed' here), to the task PC's Connector 0 -- Task 8 already",
        "    placed that connector and its own 9 global labels (A_EYE_LX_TPC etc.); this",
        "    sheet places NO connector for these, it only drives those SAME pre-existing",
        "    label names from its own buffer outputs.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "AISENSE (recording NI Connector 0, physical pin 62) tied DIRECTLY to AGND.",
        "This single wire is what makes NRSE work (spec Decision 5): every AI channel",
        "reads against this one shared reference instead of needing 16 dedicated",
        "differential pairs. Named in this task's own brief as 'the easiest thing on the",
        "board to omit by accident' -- asserted explicitly in",
        "check_breakout_analog_ni_netlist.py, not left to the generic per-net checks.",
        "Without it, NI measures every channel against its own ground instead of ours,",
        "and the ground-offset rejection analog-frontend.kicad_sch's own differential",
        "front ends are built around silently does nothing on the NI side.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "Physical MDR68 pins (recording NI Connector 0, AI0-AI15 + AISENSE): sourced from",
        "NI's X Series User Manual (370784K-01), Figure A-5 'NI PCIe-6323/6343 Pinout' --",
        "the SAME figure Task 8 already used for the task PC's own Connector 0 (this",
        "task's own brief). Cross-checked directly against Figure A-18 'NI PCIe-6353 and",
        "NI PCIe/PXIe-6363 Pinout' (the recording card's own family, spec Sec.9.3): BOTH",
        "figures give the IDENTICAL AI0-15/AISENSE pin table -- see this generator's own",
        "module docstring for the full retrieval/cross-check method. NOT sequential:",
        "AI0=68 AI1=33 AI2=65 AI3=30 AI4=28 AI5=60 AI6=25 AI7=57 AI8=34 AI9=66 AI10=31",
        "AI11=63 AI12=61 AI13=26 AI14=58 AI15=23  AISENSE=62.",
        "AI-channel-number assignment (which A_* source rides which AI number) is this",
        "sheet's own declared choice (ALL_16_NETS list order) -- an NI AI channel is an",
        "electrically interchangeable ADC mux input, unlike a P0.x digital line's bit",
        "weight, so no external source fixes this the way Figure A-5 fixes the physical",
        "pin per AI NUMBER.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "OPA4192: no stock KiCad symbol exists for this part (checked directly against",
        "Amplifier_Operational.kicad_sym's own text). Placed here as",
        "Amplifier_Operational:OPA4197xD (already registered, Task 6) with Value",
        "overridden to 'OPA4192IDR' -- same '541-family Value-substitution precedent'",
        "(hardware/README.md) analog-frontend.kicad_sch's own INA134->INA105 already",
        "uses: OPA4192 is TI's real, current, 14-SOIC 'E-Trim' precision quad op-amp,",
        "same package and same standard quad pinout as OPA4197 -- see this generator's",
        "own module docstring for the full substitution rationale.",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "analog-ni.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
