"""Generator for hardware/breakout/sheets/power.kicad_sch -- Task 7 of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-7-brief.md): the breakout board's power sheet. Every other sheet consumes the nine
contract nets this file must produce (CONTRACT_NETS below) -- net names are a contract, so
this generator's own report (task-7-report.md) documents every judgment call made resolving
a gap in the brief, the same way gen_mule.py's did for the mule.

Uses the same label-per-pin technique as gen_mule.py/gen_breakout.py (kicad_sch.py's
Sch.place()/label()/no_connect()/power_flag()): every component pin gets a global label at
its exact schematic coordinate, two pins share a net iff their labels share text, no wires
are drawn. Global labels connect across the WHOLE PROJECT by name regardless of sheet
boundaries (KiCad semantics, not a property of this framework) -- exactly what makes a net
name declared on this ONE child sheet a real, enforceable, project-wide contract the other
nine sheets can just re-emit the same label text to consume, with no hierarchical sheet
pins needed on the `(sheet ...)` symbol gen_breakout.py already placed for "power".

Four stages, matching the brief's own step numbering. TASK 7 FIX ROUND 1
(task-7-report.md, "Fix round 1") revised stages 1 and 2, and TASK 7 FIX ROUND 2
(task-7-report.md, "Fix round 2") revised the inlet CONNECTOR (not its electrical
design -- see _place_inlet()'s own docstring) -- noted inline below, and see those
report sections for the full defect writeups and the numbers behind each decision:

  1. Analog inlet: 5-position M12 connector, IEC 61076-2-101 A-coded, keyed and
     screw-locking (widened from a 4-pin mini-DIN at fix round 1 to carry +12V/-12V/
     GND/shield plus +5V; the mini-DIN itself replaced at fix round 2 -- its footprint
     modelled an evenly-spaced ring the real Same Sky part's own datasheet diagram
     contradicts, and that part is discontinued besides), one reverse-polarity Schottky
     diode per rail (oriented per rail -- see _place_inlet()'s docstring for the
     derivation), bulk 10uF+100nF at entry on all three rails, and the AGND/DGND star
     point (exactly one NetTie_2).
  2. The +3V3 logic rail: LD1117S33TR_SOT223 (+5V->+3V3) only. Originally this stage
     also regulated +12V down to +5V via a TPS7A4901; FIX ROUND 1 REMOVED THAT
     REGULATOR -- the brief's own worst-case optocoupler-LED budget (~180-260mA) exceeds
     its 150mA rating, and a bigger linear or a switching regulator were both rejected
     (thermal budget and switcher-count-consistency respectively; see
     _place_logic_rails()'s own docstring and task-7-report.md). +5V now comes directly
     from the inlet's own third rail instead, budgeted for 400mA.
  3. The single isolated supply (UNCHANGED by fix round 1): IH1215D (2W, 12V-in, +-15V-out
     isolated DC/DC -- the real, stock XP Power part that actually meets the brief's 2W
     figure; see task-7-report.md for why +-15V raw, not +-12V direct, and for the
     1W-class alternatives in the same stock library that were rejected as under-spec), a
     pi filter (10uF-ferrite-10uF) on each output rail, then TPS7A4901 again (positive)
     and TPS7A3001 (negative, TPS7A4901's datasheet-documented "negative counterpart" --
     hardware/lib's own custom symbols, see gen_wl_sync_lib.py) post-regulating the raw
     +-15V down to the clean ISO_P12/ISO_N12 contract nets referenced to INTAN_GND.
  4. PWR_FLAGs: +12V, -12V, +5V (new at fix round 1 -- see build()'s own comment for why),
     AGND, DGND, plus ISO_P15_FILT/ISO_N15_FILT -- the pi filters' own downstream nodes,
     found empirically to need one too; see build()'s own comment for why exactly these
     seven and none of the others, mirroring gen_mule.py's power_flag() reasoning.

FAN HEADERS -- added after this file's original commit, bounded addition per spec
Sec.9.5 ("Fan headers -- added 2026-08-15, and they were missing"): four Conn_01x03
chassis fan headers on their own dedicated FAN_12V/FAN_RTN nets, a polyfuse (F1) between
+12V and FAN_12V, local bulk capacitance on FAN_12V, and a second star-point NetTie_2
(NT2) joining FAN_RTN to DGND. See _place_fan_headers()'s own docstring for the full
electrical design and the out-of-band reference-minting discipline this addition
required (power.kicad_sch is the first sheet this project ever generated, so every
other sheet already seeded its own reference numbering past this file's PRE-fan-header
state -- see that docstring for why an ordinary next_ref() call would have silently
collided with an already-committed sibling sheet). CONTRACT_NETS is UNCHANGED (still 9)
-- FAN_12V/FAN_RTN never leave this sheet, so they are not part of the cross-sheet
contract.

Run directly: `python3 hardware/gen/gen_breakout_power.py` (writes
hardware/breakout/sheets/power.kicad_sch). hardware/breakout/sym-lib-table and
fp-lib-table both needed new entries for this task -- see task-7-report.md for exactly
which, and why each was a stock KiCad library already installed alongside the ones Task 6
registered, not a new custom one.

Requires hardware/breakout/breakout.kicad_sch to already exist on disk (gen_breakout.py's
own output, committed as of Task 6) -- build() reads it to compute this file's real
root+sheet-symbol ancestor path (kicad_sch.py's find_root_uuid()/find_sheet_instance_path(),
added at fix round 1) rather than defaulting to a self-referential one. See
kicad_sch.py's Sch class docstring and task-7-report.md's "Fix round 1" for why this
matters even though it does not change kicad-cli sch erc/export netlist's own output.
"""
from pathlib import Path

from kicad_sch import Sch, find_root_uuid, find_sheet_instance_path, pin_pos, write_project_stub

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
POWER_SHEETFILE = "sheets/power.kicad_sch"  # exactly as breakout.kicad_sch's own (sheet
# ...) block spells its Sheetfile property (gen_breakout.py's SHEET_NAMES/f"sheets/
# {name}.kicad_sch") -- find_sheet_instance_path() matches on this literal string.

# ---------------------------------------------------------------------------
# Contract nets (task-7-brief.md, "Interfaces: Produces"). Every other sheet consumes
# these by re-emitting the same global-label text; get one of these names wrong and it's
# exactly the "plausible but wrong" failure constraint 1 warns about, just at the
# sheet-boundary level instead of the pin-resolution level -- so check_breakout_power_
# netlist.py asserts every one of these exists, not just that ERC is silent.
# ---------------------------------------------------------------------------
CONTRACT_NETS = [
    "+12V", "-12V", "+5V", "+3V3", "AGND", "DGND", "ISO_P12", "ISO_N12", "INTAN_GND",
]
assert len(CONTRACT_NETS) == 9

# ---------------------------------------------------------------------------
# Footprints -- picked from the part actually being ordered, same discipline gen_mule.py's
# own footprint-assignment comment block documents (check the REAL part's own datasheet/
# package, never a stand-in symbol's ki_fp_filters). See task-7-report.md for the sourcing
# behind each one that isn't already established elsewhere in this repo.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"  # 100nF/10nF -- decoupling, NR/SS, feed-forward
FOOTPRINT_C_BULK = "Capacitor_SMD:C_0805_2012Metric"   # 10uF bulk -- matches gen_mule.py's own choice
# SS14 -- 40V/1A Schottky, real orderable part (not a stand-in), SMA package. The
# "_Handsoldering" variant is KiCad's own extra-pad-clearance version of D_SMA, picked to
# match this board's explicit hand-solderable global constraint the same deliberate way
# gen_mule.py picked a bare (not shrouded) IDC header for its own hand-assembly reasons.
FOOTPRINT_DIODE = "Diode_SMD:D_SMA_Handsoldering"
# Ferrite bead for each pi filter's series element -- 0805, one size up from the 0603
# floor for a comfortable hand-solder joint, same reasoning gen_mule.py gave for its own
# 10uF bulk caps' 0805 choice. HandSolder pad variant, same reasoning as the diode above.
FOOTPRINT_FERRITE = "Inductor_SMD:L_0805_2012Metric_Pad1.05x1.20mm_HandSolder"
# NetTie-2, 2.0mm pad variant (not the 0.5mm one) -- generous, easily hand-inspected pads
# for the one component on this whole board whose entire job is being visibly, individually
# verifiable as either bridged or open.
FOOTPRINT_NETTIE = "NetTie:NetTie-2_SMD_Pad2.0mm"
FOOTPRINT_M12A5 = "wl-sync:M12A_5_Panel"  # hardware/README.md's own custom footprint --
# a 5-position, IEC 61076-2-101 A-coded (keyed), screw-locking M12 connector (Amphenol
# LTW M12A-05PFFP-SF8001), replacing the mini-DIN inlet at Task 7 fix round 2: the real
# Same Sky MD-40SN/MD-50SN datasheet's own diagrams show a CLUSTERED contact layout, not
# the even ring the mini-DIN footprints modelled, and that part is discontinued besides.
# See task-7-report.md's "Fix round 2" and gen_wl_sync_lib.py's M12A_5 comment block for
# the full selection rationale and sourcing.
FOOTPRINT_DCDC = "Converter_DCDC:Converter_DCDC_XP_POWER-IHxxxxD_THT"  # IH1215D's own stock Footprint property
FOOTPRINT_SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"  # LD1117S33TR -- identical to gen_mule.py's own usage
# HVSSOP-8-1EP, generic (not TI's own DGN0008[B/D/G] mechanical-suffix-specific footprint
# -- see gen_wl_sync_lib.py's TPS7A49_FOOTPRINT comment for why the generic one is used
# without asserting false certainty about which exact TI suffix applies).
FOOTPRINT_TPS7A49 = "Package_SO:HVSSOP-8-1EP_3x3mm_P0.65mm_EP1.57x1.89mm"
# Fan headers -- added after this file's original commit (spec Sec.9.5, "Fan headers --
# added 2026-08-15, and they were missing"). Both the symbol and footprint LIBRARIES
# below were already registered project-wide before this addition (Device and
# Connector_Generic in sym-lib-table; Connector_PinHeader_2.54mm in fp-lib-table --
# analog-frontend.kicad_sch already used Conn_01x03/PinHeader_1x03_P2.54mm_Vertical for
# its own, since-replaced MISC atten shunt headers), so only ONE new library entry was
# needed anywhere: "Fuse" in hardware/breakout/fp-lib-table, for the polyfuse footprint.
FOOTPRINT_FUSE = "Fuse:Fuse_1206_3216Metric_Pad1.42x1.75mm_HandSolder"  # Littelfuse
# 1206L050/15YR -- real, current, well-stocked PPTC resettable fuse (34,076 units at
# DigiKey, checked 2026-08-17): 500mA hold / 1A trip / 15V max / 100A max fault-
# interrupt rating, 1206 (3216 metric) package. HandSolder pad variant, same hand-
# assembly-margin discipline as every other footprint in this file (FOOTPRINT_DIODE/
# FOOTPRINT_FERRITE's own comments). See _place_fan_headers()'s own docstring for the
# hold-current sizing derivation and hardware/procurement-check.md for the sourcing
# record.
FOOTPRINT_HDR1X03 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
# Real pad geometry checked directly against the .kicad_mod file, not assumed from the
# symbol name (constraint 5, "check the footprint's real pad geometry... if anything you
# add depends on physical adjacency"): pads 1/2/3 sit at (0,0)/(0,2.54)/(0,5.08) -- one
# straight column in strict numeric order, so pin N *is* physical position N with no
# adjacency hazard of the kind the Conn_02x03 ganged jumper (Task 10a fix rounds 2-3)
# hit, where the symbol's logical numbering and the footprint's real pad layout
# disagreed. Matches the standard PC 3-pin fan pinout at 2.54mm this header exists to
# mate: pin 1 GND, pin 2 +12V, pin 3 tach.

# ---------------------------------------------------------------------------
# Layout grid -- every coordinate an exact multiple of 1.27mm (KiCad's schematic
# connection grid), same GRID() helper and reasoning as gen_mule.py's own.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


# Stage anchor coordinates -- one (X, Y) per placed IC/connector/net-tie; every satellite
# passive (divider resistors, filter caps, bypass caps) is placed at a fixed offset from
# its own stage's anchor by the helper functions below, so the coordinate arithmetic for
# "does this overlap the next stage" only has to be checked between anchors, not between
# every individual passive.
X_J1, Y_J1 = GRID(20), GRID(95.25)               # M12 inlet (fix round 2)
X_DIODE = GRID(66.04)
Y_D_P12, Y_D_N12 = GRID(53.34), GRID(137.16)     # +12V / -12V protection diode rows
Y_D_P5 = GRID(20.32)                             # +5V protection diode row (fix round 1)
X_CAP_BULK, X_CAP_SMALL = GRID(101.6), GRID(116.84)  # entry bulk-10uF / 100nF columns
X_TIE, Y_TIE = GRID(44.45), GRID(95.25)          # AGND/DGND star point

X_PWRFLAG0, Y_PWRFLAG = GRID(20), GRID(180.34)
PWRFLAG_DX = GRID(20.32)

# A TPS7A4901 (+12V -> +5V, logic) used to be anchored at (200.66, 53.34) here, before
# every U-prefixed reference below it. Removed at Task 7 fix round 1 -- see
# _place_logic_rails()'s own docstring; the coordinate is retired along with it rather
# than left dangling unused. Every X_U*/Y_U* constant below is renamed from the
# original report's own numbering to match the REAL reference each part gets now that
# the removal shifted everything down by one (X_U2->X_U1, X_U4->X_U3, X_U5->X_U4) --
# kept in sync deliberately, not left stale, so these names stay a reliable index into
# the real output for whoever next edits this file.
X_U1, Y_U1 = GRID(340.36), GRID(53.34)           # LD1117S33TR_SOT223: +5V -> +3V3

X_U2, Y_U2 = GRID(200.66), GRID(224.79)          # IH1215D isolated DC-DC

X_PIFILT_P, Y_PIFILT_P = GRID(260), GRID(190.5)  # positive pi filter (ISO_P15_RAW->FILT)
X_U3, Y_U3 = GRID(340.36), GRID(190.5)           # TPS7A4901 #2: ISO_P15_FILT -> ISO_P12

X_PIFILT_N, Y_PIFILT_N = GRID(260), GRID(281.94) # negative pi filter (ISO_N15_RAW->FILT)
X_U4, Y_U4 = GRID(340.36), GRID(281.94)          # TPS7A3001: ISO_N15_FILT -> ISO_N12

SAT_DX = GRID(38.1)   # divider / pi-filter satellite horizontal offset from its IC/anchor
SAT_DY = GRID(20.32)  # satellite vertical offset (row spacing for stacked satellites)

# Fan-header stage anchors (spec Sec.9.5). Checked directly against the rendered file
# before picking these, not assumed clear: every existing (at X Y) in the committed
# power.kicad_sch spans X -29.21..380.49, Y -8.89..326.39, so this whole block (X>=440)
# sits well outside that bounding box on the same A2 sheet and cannot coordinate-collide
# with anything placed above.
X_FAN_FUSE, Y_FAN_RAIL = GRID(440), GRID(30.48)      # F1: +12V -> FAN_12V
X_FAN_CAP_BULK = GRID(475.56)                         # FAN_12V/FAN_RTN bulk 10uF
X_FAN_CAP_SMALL = GRID(490.8)                         # FAN_12V/FAN_RTN small 100nF
X_FAN_TIE, Y_FAN_TIE = GRID(440), GRID(50.8)          # NT2: FAN_RTN <-> DGND
X_FAN_HDR, Y_FAN_HDR0 = GRID(520), GRID(30.48)        # first of 4 fan headers
FAN_HDR_DY = GRID(25.4)                                # row spacing, headers 2-4
X_NOTE_FAN, Y_NOTE_FAN = GRID(440), GRID(160)
FAN_NOTE_DY = GRID(5.08)


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    """Place a 2-pin part between two labeled nets, same pattern as gen_mule.py's own
    two_pin() (duplicated rather than imported -- kicad_sch.py, not gen_mule.py, is this
    project's shared machinery; see hardware/README.md)."""
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    x1, y1 = pin_pos(x, y, pins["1"])
    sch.label(net1, x1, y1)
    x2, y2 = pin_pos(x, y, pins["2"])
    sch.label(net2, x2, y2)
    return ref


def pi_filter(sch, x, y, raw_net, filt_net, gnd_net, refs):
    """10uF - ferrite bead - 10uF pi filter (brief Step 3, verbatim: "a pi filter (10uF -
    ferrite - 10uF) on each output rail"). The SECOND 10uF (at `filt_net`) doubles as the
    downstream regulator's own CIN -- TI's TPS7A49/TPS7A30 datasheets ask for a >=2.2uF
    (10uF recommended) input cap placed close to the IN pin; since `filt_net` IS the node
    that feeds that IN pin directly (see place_tps7a49_family below), one cap satisfies
    both the brief's own filter spec and the datasheet's CIN recommendation rather than
    inventing a third, redundant capacitor the netlist checker would then have to
    special-case.
    """
    c1 = two_pin(sch, "Device", "C", "C", "10uF", x, y - SAT_DY, raw_net, gnd_net, footprint=FOOTPRINT_C_BULK)
    fb = two_pin(sch, "Device", "FerriteBead", "FB", "600R@100MHz", x + SAT_DX, y, raw_net, filt_net, footprint=FOOTPRINT_FERRITE)
    c2 = two_pin(sch, "Device", "C", "C", "10uF", x + 2 * SAT_DX, y - SAT_DY, filt_net, gnd_net, footprint=FOOTPRINT_C_BULK)
    refs.setdefault("pi_filter_c", []).extend([c1, c2])
    refs.setdefault("pi_filter_fb", []).append(fb)


def fb_divider(sch, x, y, out_net, fb_net, gnd_net, r1_val, r2_val, refs):
    """R1 (OUT->FB) + R2 (FB->GND) feedback divider, plus the 10nF feed-forward cap
    (FB->OUT) TI's own "maximum AC performance" reference circuit (TPS7A49 datasheet
    Figure 29 / TPS7A30's analogous figure) adds in parallel with R1 -- included here
    given this whole isolated supply exists specifically to stay quiet next to
    microvolt-scale headstage recordings (brief's own framing), not merely to hit DC
    accuracy. r1_val/r2_val are pre-computed per instance (see build()'s own comments for
    each one's arithmetic) rather than derived in this helper, so the actual numbers used
    stay visible at the call site next to the Vout they're supposed to produce.
    """
    r1 = two_pin(sch, "Device", "R", "R", r1_val, x, y - SAT_DY, out_net, fb_net, footprint=FOOTPRINT_R)
    r2 = two_pin(sch, "Device", "R", "R", r2_val, x, y, fb_net, gnd_net, footprint=FOOTPRINT_R)
    cff = two_pin(sch, "Device", "C", "C", "10nF", x + SAT_DX, y - SAT_DY, fb_net, out_net, footprint=FOOTPRINT_C_SMALL)
    refs.setdefault("divider_r", []).extend([r1, r2])
    refs.setdefault("ff_c", []).append(cff)


def place_tps7a49_family(sch, symname, footprint, x, y, in_net, gnd_net, out_net, refs):
    """Place one TPS7A4901/TPS7A3001 instance (hardware/lib/wl-sync.kicad_sym, added this
    task -- see gen_wl_sync_lib.py): IN and EN tied together (EN "can be connected to IN,
    if not used" -- both datasheets, verbatim; EN must NOT float, so tying it somewhere
    is mandatory, not optional), GND to `gnd_net`, OUT to `out_net`. NC (pin 3) and DNC
    (pin 7) are marked no_connect -- DNC in particular must not reach any net, "not even
    GND or IN" (both datasheets, verbatim). Returns (ref, fb_net, nrss_net) so the caller
    can wire up fb_divider() and the NR/SS cap against nets scoped to this specific
    instance (each regulator's FB/NR-SS node is internal and instance-specific, unlike
    IN/GND/OUT which are shared contract or stage nets).
    """
    ref = sch.next_ref("U")
    pins = sch.place("wl-sync", symname, ref, symname, x, y, footprint=footprint)
    fb_net = f"{ref}_FB"
    nrss_net = f"{ref}_NRSS"
    for num, net in (("1", out_net), ("2", fb_net), ("4", gnd_net), ("5", in_net), ("6", nrss_net), ("8", in_net)):
        px, py = pin_pos(x, y, pins[num])
        sch.label(net, px, py)
    for num in ("3", "7"):
        px, py = pin_pos(x, y, pins[num])
        sch.no_connect(px, py)
    refs.setdefault("regulator_tps7a49_family", []).append(ref)
    # NR/SS cap right at the part, per both datasheets' Sec 9.1.4/"Do's and Don'ts" ("do
    # not resistively or inductively load the NR/SS pin" -- a plain cap to GND is exactly
    # what both recommend and nothing else).
    nrss_c = two_pin(sch, "Device", "C", "C", "10nF", x, y + SAT_DY, nrss_net, gnd_net, footprint=FOOTPRINT_C_SMALL)
    refs.setdefault("nrss_c", []).append(nrss_c)
    return ref, fb_net, nrss_net


def _place_inlet(sch, refs):
    """Step 1 (revised, Task 7 fix round 1 -- task-7-report.md's "Fix round 1", and fix
    round 2 -- "Fix round 2"): 5-position M12 connector inlet (widened from a 4-pin
    mini-DIN to 5 positions at fix round 1 -- was +12V/-12V/GND/shield; now also carries
    +5V directly from the external supply, since the on-board TPS7A4901 that used to
    derive +5V from +12V was undersized and has been removed -- see
    _place_logic_rails(). Fix round 2 replaced the mini-DIN connector ITSELF with a
    currently-stocked, keyed, screw-locking M12 part -- see FOOTPRINT_M12A5's own
    comment and hardware/README.md; this fix changed the connector, not the electrical
    design below, which is otherwise unchanged), one reverse-polarity Schottky per rail
    (three: +12V, -12V, +5V), bulk 10uF+100nF on each rail, and the single AGND/DGND
    star-point net tie.

    Pin-to-rail assignment (hardware/lib/wl-sync.kicad_sym's own M12A_5 symbol is
    deliberately generic -- "Pin-to-rail assignment... is made where this is placed",
    per its Description property, same convention every connector in this file uses --
    this is that assignment, made once, here, and unchanged in substance by fix round
    2's connector swap): pin 1 = +12V (raw), pin 2 = -12V (raw), pin 3 = +5V (raw), pin
    4 = GND, pin 5 = shield. Shield and GND both tie directly to AGND at this same inlet
    point rather than getting their own nets -- a cable shield and the external
    supply's own return terminated at the single-point star ground already established
    here, not a second or third competing reference next to it.

    Reverse-polarity protection: ONE series Schottky per rail (SS14, 40V/1A), oriented so
    each rail's own NORMAL current direction forward-biases its diode and a wiring fault
    reverse-biases it (blocks, rather than damaging downstream parts):
      +12V and +5V: conventional current flows connector -> board (out of the external
      supply's + terminal, through the board's loads, back to GND) -- anode at the raw
      connector pin, cathode at the protected net. Standard series reverse-polarity-diode
      orientation. +5V (D3, fix round 1) is electrically the SAME case as +12V (D1) here
      -- both are "positive" outputs of the same external multi-rail supply sharing one
      GND return -- so D3 is oriented exactly like D1, NOT mirrored like D2 (below).
      -12V: the mirror image, worked through explicitly because it is easy to get
      backwards. A split +-12V supply is two supplies sharing one GND: for the negative
      one, GND is effectively ITS "+" terminal and the -12V pin is its "-" terminal, so
      conventional current flows board -> connector (out of whatever on the board sinks
      current into the negative rail, back through the -12V pin, back to the external
      supply) -- i.e. the OPPOSITE direction from the +12V/+5V case. So D2's anode sits on
      the PROTECTED `-12V` net (board side) and its cathode on the raw connector pin
      (source side) -- reversed from D1/D3. Confirmed against the fault case: if the -12V
      pin is ever miswired to a positive source, that pin's now-higher potential would
      need to forward-bias cathode->anode to reach the board net, which is the diode's
      REVERSE direction -- blocked, exactly the intended fail-safe (board stays
      unpowered on that rail rather than the fault propagating downstream).

    +5V's own entry bulk (10uF) and bypass (100nF) capacitors reference DGND, NOT AGND
    -- a deliberate asymmetry against +12V/-12V's own AGND-referenced entry caps at this
    same physical connector, worth stating explicitly since "match the neighbours" would
    be the wrong call here. +5V is the LOGIC rail throughout its entire life on this
    sheet (feeds task-pc-digital/pi-interface/control-usb-i2c per the plan's sheet list
    -- same reasoning _place_logic_rails() already gives for its own GND choice), so
    DGND is its one consistent ground reference starting the moment it enters the board,
    not just downstream at the LDO. Referencing +5V's OWN entry caps to AGND instead
    would inject its entry-point ripple current onto the analog ground plane right at
    the door, then route it back to DGND through the one deliberately narrow AGND/DGND
    star tie a few millimetres away -- exactly the cross-domain noise path the whole
    AGND/DGND split exists to prevent, just relocated to the connector instead of
    avoided.

    +5V is budgeted for 400mA (fix round 1) -- see task-7-report.md: the brief's own
    28-channel worst-case draw is ~180-260mA, so this is comfortable headroom, under D3
    (SS14, 1A-rated) and this connector's own 4A/contact rating (Amphenol LTW's own
    M12A-05PFFP-SF8001 spec, fix round 2 -- more headroom than the mini-DIN's own
    2A/contact this replaced) alike, with enough margin that a downstream digital
    sheet's real load should not need to reopen this budget.
    """
    refs["inlet_diode"] = []
    refs["inlet_cap"] = []

    j1_ref = sch.next_ref("J")
    j1_pins = sch.place("wl-sync", "M12A_5", j1_ref, "M12A_5", X_J1, Y_J1, footprint=FOOTPRINT_M12A5)
    j1_map = {"1": "P12_RAW", "2": "N12_RAW", "3": "P5_RAW", "4": "AGND", "5": "AGND"}
    for num, net in j1_map.items():
        x, y = pin_pos(X_J1, Y_J1, j1_pins[num])
        sch.label(net, x, y)
    refs["j1"] = j1_ref

    # D1: +12V -- anode (raw, source side) -> cathode (protected net, load side).
    d1 = sch.next_ref("D")
    d1_pins = sch.place("Diode", "SS14", d1, "SS14", X_DIODE, Y_D_P12, footprint=FOOTPRINT_DIODE)
    ax, ay = pin_pos(X_DIODE, Y_D_P12, d1_pins["2"])  # A
    sch.label("P12_RAW", ax, ay)
    kx, ky = pin_pos(X_DIODE, Y_D_P12, d1_pins["1"])  # K
    sch.label("+12V", kx, ky)
    refs["inlet_diode"].append(d1)

    # D2: -12V -- anode (protected net, board side) -> cathode (raw, source side) --
    # REVERSED from D1; see docstring above.
    d2 = sch.next_ref("D")
    d2_pins = sch.place("Diode", "SS14", d2, "SS14", X_DIODE, Y_D_N12, footprint=FOOTPRINT_DIODE)
    ax, ay = pin_pos(X_DIODE, Y_D_N12, d2_pins["2"])  # A
    sch.label("-12V", ax, ay)
    kx, ky = pin_pos(X_DIODE, Y_D_N12, d2_pins["1"])  # K
    sch.label("N12_RAW", kx, ky)
    refs["inlet_diode"].append(d2)

    # D3: +5V (fix round 1) -- anode (raw, source side) -> cathode (protected net, load
    # side), SAME orientation as D1 -- see docstring above.
    d3 = sch.next_ref("D")
    d3_pins = sch.place("Diode", "SS14", d3, "SS14", X_DIODE, Y_D_P5, footprint=FOOTPRINT_DIODE)
    ax, ay = pin_pos(X_DIODE, Y_D_P5, d3_pins["2"])  # A
    sch.label("P5_RAW", ax, ay)
    kx, ky = pin_pos(X_DIODE, Y_D_P5, d3_pins["1"])  # K
    sch.label("+5V", kx, ky)
    refs["inlet_diode"].append(d3)

    # Bulk 10uF + 100nF at entry, per rail (brief Step 1, verbatim for +12V/-12V; +5V's
    # own entry caps are fix round 1's addition, DGND- not AGND-referenced -- see
    # docstring above for why).
    for row_y, rail, gnd in (
        (Y_D_P12, "+12V", "AGND"), (Y_D_N12, "-12V", "AGND"), (Y_D_P5, "+5V", "DGND"),
    ):
        c_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_CAP_BULK, row_y, rail, gnd, footprint=FOOTPRINT_C_BULK)
        c_small = two_pin(sch, "Device", "C", "C", "100nF", X_CAP_SMALL, row_y, rail, gnd, footprint=FOOTPRINT_C_SMALL)
        refs["inlet_cap"] += [c_bulk, c_small]

    # The star point: AGND and DGND joined at exactly one NetTie_2, here at the inlet.
    nt1 = sch.next_ref("NT")
    nt_pins = sch.place("Device", "NetTie_2", nt1, "NetTie_2", X_TIE, Y_TIE, footprint=FOOTPRINT_NETTIE)
    x1, y1 = pin_pos(X_TIE, Y_TIE, nt_pins["1"])
    sch.label("AGND", x1, y1)
    x2, y2 = pin_pos(X_TIE, Y_TIE, nt_pins["2"])
    sch.label("DGND", x2, y2)
    refs["net_tie"] = nt1


def _place_logic_rails(sch, refs):
    """Step 2 (revised, Task 7 fix round 1 -- task-7-report.md's "Fix round 1"):
    LD1117S33TR_SOT223 derives +3V3 from +5V. ONLY. Originally this stage also
    regulated +12V down to +5V via a TPS7A4901; that stage is REMOVED here, not
    simplified -- +5V now comes directly off the inlet's own third rail
    (_place_inlet()) instead of an on-board regulator.

    Why removed, not resized: the brief's own worst-case draw is ~26 simultaneous
    optocoupler LEDs (all 16 data bits plus strobe/barcode/reward-commanded/
    reward-delivered/stim-trigger plus the five Intan-bound copies) -- at the HCPL-4661
    family's ~6.3mA recommended minimum forward current that is ~180mA, ~260mA at
    10mA -- and TPS7A4901 caps at 150mA, undersized before even counting the 74HCT541
    buffers riding the same rail. Duty cycle does not rescue a regulator (it supplies
    PEAK current, not average). A bigger LINEAR regulator was considered and rejected:
    260mA across a 12V->5V drop dissipates ~1.9W inside a sealed 2U chassis that already
    carries 10-15W from the sync-box module, next to analog stages with
    temperature-dependent offset drift this heat would aggravate. A SWITCHING regulator
    was also considered and rejected, for consistency with this same design's own
    decision (elsewhere in this brief) to keep the isolated +-12V DC-DC the SOLE
    switcher in the enclosure -- this design already rejected capacitive/magnetic
    digital isolators for modulating an RF carrier near headstages, then rejected
    on-board switchers generally for the same reason; adding a second switcher here for
    +5V would contradict that. +5V is now budgeted for 400mA at the inlet (see
    _place_inlet()) -- comfortable headroom over the ~180-260mA worst case, so
    downstream digital sheets (task-pc-digital, pi-interface, control-usb-i2c) have real
    margin without reopening this budget.

    LD1117S33 sources from +5V, not +12V -- decided and stated once here, as the brief
    asks. Either rail has enough headroom for a 3.3V target (LD1117-family dropout is a
    datasheet max of roughly 1.2-1.3V at FULL rated current, well under either
    candidate's own margin, and considerably less at the light currents a digital-logic
    +3V3 rail actually draws): +12V gives enormous headroom (8.7V) but dissipates
    (12V-3.3V)*Iload as heat -- roughly 5x what +5V does at the same load current,
    fighting the exact thermal rationale that just ruled out a bigger/switching
    regulator for +5V itself, for a rail this design has no reason to run hot. +5V gives
    tighter headroom (1.7V nominal, minus D3's own Schottky forward drop -- a few
    hundred mV at the light currents +3V3 actually draws, not the ~260mA figure the
    inlet is budgeted for) while cutting dissipation roughly 5x versus +12V, and is the
    natural single-step derivation in a conventional raw->5V->3.3V logic supply chain
    besides. Sourcing from +5V is the right call on that basis, but the margin is real
    enough to flag, not hand-waved: this hasn't been checked against an actual +3V3 load
    current, since the digital sheets that draw it don't exist yet -- same "tell me if
    it doesn't fit" posture task-7-report.md's original +5V-budget concern already
    established, not silently absorbed here either.

    No dedicated input (CIN) capacitor is added at U2 itself: "+5V" is ONE net from the
    inlet's own entry bulk/bypass caps all the way to U2's IN pin (no ferrite or other
    series element narrows it into a separate downstream node the way the isolated
    supply's own pi_filter() nodes are split, which is what earns THOSE a second,
    downstream-specific capacitor) -- so the inlet's own 10uF+100nF already sit
    electrically at U2's IN pin in this topology-only model, and adding a third,
    redundant capacitor on the same net would be exactly the invented-redundancy
    pi_filter()'s own docstring already argues against. A real board's physical
    trace length from inlet to U2 is a PCB-layout concern this schematic-capture
    generator has no coordinates to speak of (same limitation
    check_mule_netlist.py's own docstring already states for bypass-cap proximity) --
    worth a local bypass at layout time regardless, flagged here rather than assumed
    away.

    GND stays DGND (unchanged by this fix): +3V3 is the LOGIC rail (consumed by the
    digital sheets -- task-pc-digital, pi-interface, control-usb-i2c, per the plan's
    sheet list), so its return path belongs on the digital ground, not analog.
    """
    refs["ldo_cap"] = []

    # Local names u1_*/X_U1/Y_U1 match the REAL reference this regulator now gets
    # (next_ref("U") hands out "U1" here since it's the first U-prefixed part placed
    # after fix round 1 removed the old TPS7A4901-for-+5V stage that used to go first)
    # -- kept in sync deliberately, not just cosmetically, so a future reader scanning
    # this file for "which part is U1 in the real output" finds the right block by name.
    u1_ref = sch.next_ref("U")
    u1_pins = sch.place(
        "Regulator_Linear", "LD1117S33TR_SOT223", u1_ref, "LD1117S33TR_SOT223",
        X_U1, Y_U1, footprint=FOOTPRINT_SOT223,
    )
    u1_map = {"3": "+5V", "1": "DGND", "2": "+3V3"}  # VI, GND, VO -- matches gen_mule.py's own LD1117 pin map
    for num, net in u1_map.items():
        x, y = pin_pos(X_U1, Y_U1, u1_pins[num])
        sch.label(net, x, y)
    u1_dc = two_pin(sch, "Device", "C", "C", "100nF", X_U1, Y_U1 + SAT_DY, "+3V3", "DGND", footprint=FOOTPRINT_C_SMALL)
    u1_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_U1 + SAT_DX, Y_U1 + SAT_DY, "+3V3", "DGND", footprint=FOOTPRINT_C_BULK)
    refs["ldo_cap"] += [u1_dc, u1_bulk]
    refs["u1"] = u1_ref


def _place_isolated_supply(sch, refs):
    """Step 3: the single isolated supply. IH1215D (XP Power, Converter_DCDC_Isolated
    stock library) is a real, stock, 2W part -- "XP Power 2W, 1000 VDC Isolated DC/DC
    Converter Module, Dual Output Voltage +-15V, +-66mA, 12V Input Voltage, DIP" (its own
    Description property, unedited). +-15V RAW, not +-12V direct: the brief also asks for
    "LDO post-regulation" after the pi filter, and a linear post-regulator can only
    regulate DOWN -- it needs headroom above the clean +-12V target, which a nominally-
    +-12V-already raw output would not leave. +-15V raw gives ~3V of headroom per rail,
    comfortably inside either regulator's own dropout spec: TPS7A4901's is a maximum of
    600mV over temperature at its full rated 150mA (TI SBVS121E's own worked design
    procedure), and TPS7A3001's is 216mV typical at 100mA (TI SBVS125D's Features list) --
    both well under 3V even before accounting for this design's own load (~46mA/~16mA
    estimated, both under each part's rated maximum, see task-7-report.md) sitting far
    below either datasheet's own worst-case current. 1W-class alternatives in the
    same stock library at the same input/output voltage combination (TMA-1212D, TMV1212D,
    NMA1212DC, IA1212D -- all "1W... 42mA each output") were checked and rejected: half
    the brief's own 2W figure, and this design's own load budget (8 diff-amp channels
    ~2mA + 6 optocoupler stages) is what the brief cites as the reason 2W was chosen, so
    halving it isn't a substitution this generator gets to make quietly.

    Primary side (+Vin/-Vin) sits on +12V/DGND, not +12V/AGND: this is the one switcher
    in the enclosure, and DGND (not the quiet analog AGND) is where its own switching
    ripple belongs, for exactly the reason the brief gives for accepting it at all --
    keeping a modulated RF carrier away from headstage-adjacent analog copper. Secondary
    side (0V, the isolated common) is INTAN_GND, the contract net -- the isolated DC-DC's
    own "0V" power_out pin is what drives it (no PWR_FLAG needed, matching gen_mule.py's
    own reasoning for skipping a flag on any net a genuine power_out pin already drives).
    """
    # Local names u2_*/X_U2/Y_U2 (IH1215D), u3_*/X_U3/Y_U3 (TPS7A4901 #2), u4_*/X_U4/Y_U4
    # (TPS7A3001) all match the REAL references next_ref("U") hands out here post-fix
    # round 1 (see X_U1's own comment above for why: removing the old
    # TPS7A4901-for-+5V stage shifted every later reference down by one) -- kept in
    # sync deliberately, same reasoning as _place_logic_rails()'s own u1_ref.
    u2_ref = sch.next_ref("U")
    u2_pins = sch.place("Converter_DCDC_Isolated", "IH1215D", u2_ref, "IH1215D", X_U2, Y_U2, footprint=FOOTPRINT_DCDC)
    u2_map = {"1": "DGND", "3": "+12V", "4": "ISO_N15_RAW", "5": "ISO_P15_RAW", "6": "INTAN_GND"}
    for num, net in u2_map.items():
        x, y = pin_pos(X_U2, Y_U2, u2_pins[num])
        sch.label(net, x, y)
    x, y = pin_pos(X_U2, Y_U2, u2_pins["2"])  # NC
    sch.no_connect(x, y)
    u2_bypass = two_pin(sch, "Device", "C", "C", "100nF", X_U2 - SAT_DX, Y_U2 - SAT_DY, "+12V", "DGND", footprint=FOOTPRINT_C_SMALL)
    refs["u2"] = u2_ref
    refs["dcdc_bypass_c"] = [u2_bypass]

    # Positive rail: pi filter then TPS7A4901 (#2) post-regulation to ISO_P12.
    pi_filter(sch, X_PIFILT_P, Y_PIFILT_P, "ISO_P15_RAW", "ISO_P15_FILT", "INTAN_GND", refs)
    u3_ref, u3_fb, u3_nrss = place_tps7a49_family(
        sch, "TPS7A4901", FOOTPRINT_TPS7A49, X_U3, Y_U3, "ISO_P15_FILT", "INTAN_GND", "ISO_P12", refs
    )
    # R1=90.9k (E96), R2=10.0k: Vout=1.185*(1+90.9/10)=11.957V, -0.36%. (R1/R2, not the
    # original report's R3/R4 -- fix round 1 renumbering, see task-7-report.md.)
    fb_divider(sch, X_U3 - 2 * SAT_DX, Y_U3, "ISO_P12", u3_fb, "INTAN_GND", "90.9k", "10.0k", refs)
    u3_cout = two_pin(sch, "Device", "C", "C", "10uF", X_U3, Y_U3 + 2 * SAT_DY, "ISO_P12", "INTAN_GND", footprint=FOOTPRINT_C_BULK)
    u3_cout2 = two_pin(sch, "Device", "C", "C", "100nF", X_U3 + SAT_DX, Y_U3 + 2 * SAT_DY, "ISO_P12", "INTAN_GND", footprint=FOOTPRINT_C_SMALL)
    refs["iso_out_cap"] = [u3_cout, u3_cout2]

    # Negative rail: pi filter then TPS7A3001 post-regulation to ISO_N12.
    pi_filter(sch, X_PIFILT_N, Y_PIFILT_N, "ISO_N15_RAW", "ISO_N15_FILT", "INTAN_GND", refs)
    u4_ref, u4_fb, u4_nrss = place_tps7a49_family(
        sch, "TPS7A3001", FOOTPRINT_TPS7A49, X_U4, Y_U4, "ISO_N15_FILT", "INTAN_GND", "ISO_N12", refs
    )
    # R3=93.1k, R4=10.0k (not the original report's R5/R6 -- fix round 1 renumbering)
    # -- TPS7A30's OWN Table 2 standard-1%-resistor pair for Vout=-12V, used as
    # published rather than re-derived (TI SBVS125D Table 2).
    fb_divider(sch, X_U4 - 2 * SAT_DX, Y_U4, "ISO_N12", u4_fb, "INTAN_GND", "93.1k", "10.0k", refs)
    u4_cout = two_pin(sch, "Device", "C", "C", "10uF", X_U4, Y_U4 + 2 * SAT_DY, "ISO_N12", "INTAN_GND", footprint=FOOTPRINT_C_BULK)
    u4_cout2 = two_pin(sch, "Device", "C", "C", "100nF", X_U4 + SAT_DX, Y_U4 + 2 * SAT_DY, "ISO_N12", "INTAN_GND", footprint=FOOTPRINT_C_SMALL)
    refs["iso_out_cap"] += [u4_cout, u4_cout2]
    refs["u3"], refs["u4"] = u3_ref, u4_ref


def _place_fan_headers(sch, refs):
    """Added after this file's original commit -- spec Sec.9.5, "Fan headers -- added
    2026-08-15, and they were missing": Sec.9.4 recorded the thermal requirement (a CM5
    plus NVMe next to 33 temperature-sensitive analog stages, sealed in a 2U chassis)
    and never specified the connectors to satisfy it. Four 3-pin chassis fan headers,
    standard PC fan pinout at 2.54mm (pin 1 GND, pin 2 +12V, pin 3 tach), on their own
    dedicated FAN_12V/FAN_RTN supply -- protected from the rails the analog section
    depends on by a single shared polyfuse, and joined to the rest of the ground system
    at exactly one point, the same star-point technique AGND/DGND already use at J1
    (_place_inlet()).

    WHY THIS IS A POWER-SHEET CHANGE, NOT A NEW SHEET: the fan headers are physically
    and electrically part of the power-distribution problem this sheet already owns (a
    load on +12V that must not contaminate AGND), not a new functional block. No other
    sheet ever consumes FAN_12V or FAN_RTN by name, so neither net is added to
    CONTRACT_NETS above -- that list is specifically the CROSS-SHEET contract, and these
    two nets never leave this sheet.

    FAN CURRENT MUST NOT RETURN THROUGH AGND. A brushless DC fan is a commutating
    switching load, and routing ~240mA of it through a board carrying 33 analog stages
    and a microphone preamp would contradict this design's own rejection of digital
    isolators for an RF carrier (spec Sec.2 decision 3) and of on-board switchers for
    consistency with that (spec Sec.8) -- the same reasoning that already put U2 (the
    isolated DC-DC, _place_isolated_supply()) and +5V's own entry caps (_place_inlet())
    on DGND rather than AGND. FAN_RTN follows that SAME precedent here, tied to DGND
    (not AGND) via NT2, a second NetTie_2 -- so ERC still treats FAN_RTN and DGND as
    distinct nets everywhere else on the board, and a future plane-split mistake between
    them is a rule violation for a checker to catch, not noise buried in a recording.
    FAN_RTN still reaches AGND, but only transitively, through NT2 then NT1 (DGND's own
    tie to AGND) -- never by a second, competing direct path.

    A POLYFUSE ON FAN_12V, not a second reverse-polarity diode: FAN_12V taps off +12V
    AFTER D1 (_place_inlet()'s own reverse-polarity Schottky), so polarity is already
    handled; a stalled or shorted fan is the fault this rail actually needs protection
    from, since a fan is the most mechanically abused part in the enclosure and the only
    one with a bearing. F1 (Littelfuse 1206L050/15YR) is sized for four fans at ~0.06A
    each (~240mA nominal, spec Sec.9.5's own figure) with ~2.1x margin at its 500mA hold
    rating -- inside the 1.5-2x band PPTC application guidance recommends, needed here
    for three concrete reasons rather than a round-number guess: (1) PPTC hold current
    is characterized at 25C and derates measurably at the elevated ambient this sealed
    chassis reaches (spec Sec.9.4's own 10-15W thermal load is the reason this design
    cares about airflow at all); (2) a fan's own starting/inrush current, before the
    rotor is up to speed, briefly exceeds its running current, on all four fans
    simultaneously at power-up; (3) ordinary part-to-part Ihold tolerance. F1's own 1A
    trip current (2x hold, its datasheet's own figure) stays comfortably under D1's
    SS14 (1A-rated) and this whole board's established headroom precedent, while a
    genuine short still trips in the datasheet's own 100ms figure -- far below anything
    that would sag the shared +12V rail the analog front end depends on.

    OUT-OF-BAND REFERENCE MINTING (constraint 3: never renumber an existing refdes).
    power.kicad_sch is the FIRST sheet this whole project ever generated -- every one of
    the other nine sheets computed its own `ref_start` by reading THIS file's committed
    reference maxima (kicad_sch.py's find_max_refs()/merge_max_refs(), the mechanism
    every gen_breakout_*.py's own build() uses) at the time each of them was generated,
    and none of them is being regenerated by this change. So an ordinary sch.next_ref()
    call for a prefix this file already used (J, C -- and, in build() below, #PWR) would
    continue from THIS file's own small local count and collide with a sibling sheet
    that seeded past that same small count months ago -- the exact hazard
    gen_breakout_comparators.py's own R190 was minted to avoid (its own module comment:
    "deliberately NOT sch.next_ref(\"R\")... becomes this sheet's own new local maximum,
    which every downstream sibling... seeds its OWN numbering past"), applied here the
    same way. Computed directly against every currently-committed sheet
    (find_max_refs()/merge_max_refs() over all ten .kicad_sch files, verified
    empirically before writing these numbers in, not guessed): J tops out at 51
    (opto-intan.kicad_sch), C at 146 (control-usb-i2c.kicad_sch -- the LAST sheet this
    project ever generated, so nothing anywhere seeds past it), and #PWR at 8
    (opto-ni.kicad_sch's own NI_5V flag). NT and F need no such bump: NT1 is the only
    net tie anywhere on the board (nothing seeds past a prefix it never used), and no
    sheet has ever placed a Device:Polyfuse at all, so ordinary next_ref() is already
    safe for both. J and C are bumped here, immediately below; #PWR gets the identical
    treatment in build() itself, right before the two new power_flag() calls. Every
    PRE-EXISTING call in this file (_place_inlet/_place_logic_rails/
    _place_isolated_supply, and the original 7 power_flag() calls) runs completely
    unmodified above this function, so J1/D1-D3/C1-C21/U1-U4/NT1/#PWR1-7 all come out
    byte-for-byte identical to before this task.
    """
    assert sch.ref_counters.get("J", 0) == 1, (
        f"expected exactly J1 (the M12 inlet) placed before this out-of-band mint, "
        f"found {sch.ref_counters.get('J', 0)} -- this file's own placement order "
        f"changed; re-check the J bump below still lands past every sibling sheet's own "
        f"real usage (currently J51, opto-intan.kicad_sch)"
    )
    sch.ref_counters["J"] = 51  # whole-board max -- see docstring. Next four next_ref("J")
    # calls mint J52-J55, not J2-J5 (which sibling sheets already claimed).
    assert sch.ref_counters.get("C", 0) == 21, (
        f"expected exactly 21 pre-existing C refs before this out-of-band mint, found "
        f"{sch.ref_counters.get('C', 0)} -- re-check the bump below still lands past "
        f"every sibling sheet's own real usage (currently C146, control-usb-i2c.kicad_sch)"
    )
    sch.ref_counters["C"] = 146  # whole-board max -- see docstring. Next two next_ref("C")
    # calls mint C147/C148, not a number some sibling already claimed.

    # F1: polyfuse between the ALREADY reverse-polarity-protected +12V (not P12_RAW --
    # D1 already guards against a miswired supply; the polyfuse's own job is fault
    # current, not polarity) and the new FAN_12V rail. Value is the real, orderable,
    # currently-stocked MPN (this file's own SS14/IH1215D/LD1117S33TR discipline, not a
    # generic rating string) -- see hardware/procurement-check.md for the sourcing
    # record and this function's own docstring for the hold-current derivation.
    refs["fan_fuse"] = two_pin(
        sch, "Device", "Polyfuse", "F", "1206L050/15YR",
        X_FAN_FUSE, Y_FAN_RAIL, "+12V", "FAN_12V", footprint=FOOTPRINT_FUSE,
    )

    # Local bulk capacitance on FAN_12V after the fuse -- the same 10uF+100nF pairing
    # this file already uses at every other rail entry point (_place_inlet()'s own
    # per-rail rows), referenced to FAN_RTN (this rail's OWN return), not AGND/DGND --
    # consistent with the whole point of giving the fans a dedicated return at all.
    c_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_FAN_CAP_BULK, Y_FAN_RAIL, "FAN_12V", "FAN_RTN", footprint=FOOTPRINT_C_BULK)
    c_small = two_pin(sch, "Device", "C", "C", "100nF", X_FAN_CAP_SMALL, Y_FAN_RAIL, "FAN_12V", "FAN_RTN", footprint=FOOTPRINT_C_SMALL)
    refs["fan_cap"] = [c_bulk, c_small]

    # NT2: the SECOND net tie on this board (NT1 is AGND<->DGND at J1's own star point,
    # _place_inlet()). FAN_RTN joins DGND, not AGND -- see this function's own docstring
    # for why (the same precedent U2's primary side and +5V's own entry caps already
    # set). Electrically this is still "the power-inlet star" the spec asks for: DGND is
    # itself joined to AGND at exactly one point (NT1), so FAN_RTN reaches AGND too,
    # transitively, through NT2 then NT1 -- never by a direct shared pin, never through a
    # second, competing path.
    nt2 = sch.next_ref("NT")
    nt2_pins = sch.place("Device", "NetTie_2", nt2, "NetTie_2", X_FAN_TIE, Y_FAN_TIE, footprint=FOOTPRINT_NETTIE)
    x1, y1 = pin_pos(X_FAN_TIE, Y_FAN_TIE, nt2_pins["1"])
    sch.label("FAN_RTN", x1, y1)
    x2, y2 = pin_pos(X_FAN_TIE, Y_FAN_TIE, nt2_pins["2"])
    sch.label("DGND", x2, y2)
    refs["fan_net_tie"] = nt2

    # Four Conn_01x03 headers, standard PC fan pinout at 2.54mm: pin 1 GND (-> FAN_RTN),
    # pin 2 +12V (-> FAN_12V), pin 3 tach -- present on the footprint, deliberately left
    # a no_connect (see the on-sheet note below for why; FOOTPRINT_HDR1X03's own comment
    # confirms the footprint's real pads are a single straight column in strict numeric
    # order, so "pin 3" unambiguously means the third, physically isolated pad).
    refs["fan_headers"] = []
    for i in range(4):
        jref = sch.next_ref("J")
        y = Y_FAN_HDR0 + i * FAN_HDR_DY
        pins = sch.place(
            "Connector_Generic", "Conn_01x03", jref, f"Chassis fan {i + 1}",
            X_FAN_HDR, y, footprint=FOOTPRINT_HDR1X03,
        )
        x1, y1 = pin_pos(X_FAN_HDR, y, pins["1"])
        sch.label("FAN_RTN", x1, y1)
        x2, y2 = pin_pos(X_FAN_HDR, y, pins["2"])
        sch.label("FAN_12V", x2, y2)
        x3, y3 = pin_pos(X_FAN_HDR, y, pins["3"])
        sch.no_connect(x3, y3)
        refs["fan_headers"].append(jref)

    for line_idx, line in enumerate([
        "Four chassis fan headers (spec Sec.9.5, added 2026-08-15): standard PC 3-pin",
        "fan pinout, 2.54mm -- pin 1 GND, pin 2 +12V, pin 3 tach.",
        "PIN 3 (TACH) IS A DELIBERATE NO-CONNECT -- DO NOT DELETE THE PIN. Power-only",
        "was chosen because the sync module's GPIO is full at 26 of 28; keeping the",
        "third pin lets a 3- or 4-pin Noctua-class plug seat with no adapter, and makes",
        "adding tach later a wire change, not a connector change.",
        "FAN_12V/FAN_RTN are dedicated nets, never AGND/DGND directly: a fan is a",
        "commutating switching load, and its ~240mA through the analog ground would",
        "contradict rejecting digital isolators for an RF carrier (spec Sec.2 decision",
        "3) and on-board switchers for consistency (spec Sec.8). FAN_12V derives from",
        "+12V through F1 (polyfuse, 1206L050/15YR: 500mA hold / 1A trip) so a stalled",
        "or shorted fan cannot pull down the rails the analog section depends on.",
        "FAN_RTN joins DGND at exactly one point (NT2), mirroring how AGND/DGND join",
        "at J1 -- so FAN_RTN reaches AGND too, but only transitively, through NT2 then",
        "NT1, never by a direct shared pin.",
    ]):
        sch.text(line, X_NOTE_FAN, Y_NOTE_FAN + line_idx * FAN_NOTE_DY)


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs) -- `refs` maps a role name to the reference designator(s)
    that play it, same convention gen_mule.py's own build() established, kept here for
    the same reason: a future PCB-layout task for the breakout board (none exists yet)
    would otherwise have to re-derive "which part is this" from bare reference numbers,
    an artifact of generation order rather than a stable contract.

    Task 7 fix round 1 (task-7-report.md's "Fix round 1"): `sch` is now constructed with
    an explicit `instance_path_prefix`, read off the REAL, already-committed
    breakout.kicad_sch (this file's own parent sheet) rather than left at Sch's own
    default -- which would make every component this file places carry a
    SELF-referential `(instances (path ...))` (this file's own root uuid, not
    breakout's), correct only for a hierarchy ROOT, which power.kicad_sch is not. See
    kicad_sch.py's Sch class docstring and find_sheet_instance_path() for the mechanism
    and the real KiCad file this was confirmed against. Reading breakout.kicad_sch's
    real, on-disk uuids here (rather than, say, hard-coding them, or trusting some
    in-memory value from a separate gen_breakout.py run) is the same discipline Task 3's
    own schematic-cross-link fix established for gen_mule_pcb.py: a separate process
    invocation mints its own fresh uuids, so the committed artifact is the only uuid
    source worth trusting.
    """
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, POWER_SHEETFILE)

    sch = Sch(project="breakout", instance_path_prefix=instance_path)
    refs: dict = {}

    _place_inlet(sch, refs)
    _place_logic_rails(sch, refs)
    _place_isolated_supply(sch, refs)
    _place_fan_headers(sch, refs)  # spec Sec.9.5, added after this file's original commit

    # PWR_FLAGs: +12V, -12V, +5V (new at fix round 1 -- see below), AGND, DGND -- the
    # nets with no genuine power_out pin of their own anywhere on this sheet (every
    # OTHER contract net -- +3V3, ISO_P12, ISO_N12, INTAN_GND -- is already driven by a
    # real regulator/converter power_out pin, so flagging those too would trip ERC's
    # pin_to_pin "two power outputs connected" rule instead of satisfying anything, same
    # reasoning gen_mule.py's own power_flag() docstring gives). +5V moved into THIS
    # category at fix round 1: it used to be driven by TPS7A4901's own power_out OUT pin
    # (so it was excluded from the flag list, like +3V3/ISO_P12/etc. still are); with
    # that regulator removed, +5V now comes straight off the inlet's own D3/entry caps
    # (all passive-typed pins), so ERC's power_pin_not_driven check needs an explicit
    # flag here exactly the way +12V/-12V already have one for the identical reason
    # (external-supply-fed, no on-sheet regulator driving it). AGND and DGND are flagged
    # INDEPENDENTLY, even though the star-point NetTie_2 physically bridges them: a net
    # tie is deliberately NOT a merge as far as ERC's per-net bookkeeping goes ("so ERC
    # treats them as distinct nets everywhere else", per the brief) -- treating that
    # bridge as if it also satisfied the OTHER net's own driven-ness check would be
    # relying on the exact ambiguity constraint 1 warns about, so each gets its own flag
    # instead of assuming.
    sch.power_flag("+12V", GRID(X_PWRFLAG0), GRID(Y_PWRFLAG))
    sch.power_flag("-12V", GRID(X_PWRFLAG0 + PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("+5V", GRID(X_PWRFLAG0 + 2 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("AGND", GRID(X_PWRFLAG0 + 3 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("DGND", GRID(X_PWRFLAG0 + 4 * PWRFLAG_DX), GRID(Y_PWRFLAG))

    # Two more, found empirically (a real `kicad-cli sch erc` run, not anticipated up
    # front): ISO_P15_FILT and ISO_N15_FILT -- the pi filter's OWN downstream node,
    # between its ferrite bead and the post-regulator's IN pin -- need flags too.
    # Device:FerriteBead's pins are typed "passive", not power_out/power_in, and a global
    # label is just a name (it does not carry a pin type) -- so ERC's power_pin_not_driven
    # check, evaluated per NET-NAME, finds nothing power_out-typed anywhere on
    # "ISO_P15_FILT"/"ISO_N15_FILT" specifically: the DC-DC's own power_out pin lives on
    # the DIFFERENT net name upstream of the ferrite ("ISO_P15_RAW"/"ISO_N15_RAW"), and a
    # pi filter's entire job is to make the downstream node NOT the same net as the
    # upstream one. Real current still reaches it (through the ferrite, which ERC's
    # per-net check does not trace across), so this is exactly the situation
    # kicad_sch.py's power_flag() docstring describes -- a genuinely-driven net that
    # still needs the flag because nothing on IT is power_out-typed -- not a sign
    # something is actually undriven.
    sch.power_flag("ISO_P15_FILT", GRID(X_PWRFLAG0 + 5 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("ISO_N15_FILT", GRID(X_PWRFLAG0 + 6 * PWRFLAG_DX), GRID(Y_PWRFLAG))

    # Two more, added with the fan headers (spec Sec.9.5): FAN_12V and FAN_RTN need
    # their own flags for the identical reason AGND/DGND already have independent ones
    # above -- nothing power_out-typed drives either net on this sheet (F1, the bulk
    # caps, NT2, and every header pin are all passive-typed), and a net tie is
    # deliberately NOT a merge for ERC's own per-net bookkeeping, so bridging FAN_RTN to
    # DGND via NT2 does not also satisfy FAN_RTN's OWN driven-ness check.
    #
    # #PWR MUST be minted out-of-band here too, for the identical reason J/C were inside
    # _place_fan_headers(): #PWR8 is opto-ni.kicad_sch's own real, already-committed
    # NI_5V flag (confirmed directly against every currently-committed sheet's own
    # find_max_refs(), not guessed), so an ordinary next_ref("#PWR") call here -- which
    # would otherwise continue this file's own local count from 7 -- would collide with
    # it.
    assert sch.ref_counters.get("#PWR", 0) == 7, (
        f"expected exactly 7 pre-existing #PWR flags before this out-of-band mint, "
        f"found {sch.ref_counters.get('#PWR', 0)} -- re-check the bump below still "
        f"lands past every sibling sheet's own real usage (currently #PWR8, "
        f"opto-ni.kicad_sch's own NI_5V flag)"
    )
    sch.ref_counters["#PWR"] = 8  # whole-board max -- see above. Next two power_flag()
    # calls mint #PWR9/#PWR10, not #PWR8 (opto-ni.kicad_sch's own NI_5V flag) again.
    sch.power_flag("FAN_12V", GRID(X_PWRFLAG0 + 7 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("FAN_RTN", GRID(X_PWRFLAG0 + 8 * PWRFLAG_DX), GRID(Y_PWRFLAG))

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "power.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written by gen_breakout.py
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
