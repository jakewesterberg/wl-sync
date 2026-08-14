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

Four stages, matching the brief's own step numbering:
  1. Analog inlet: 4-pin mini-DIN, one reverse-polarity Schottky diode per +-12V rail
     (oriented per rail -- see _place_inlet()'s docstring for the derivation), bulk
     10uF+100nF at entry, and the AGND/DGND star point (exactly one NetTie_2).
  2. Logic rails, linear only: TPS7A4901 (+12V->+5V) then LD1117S33TR_SOT223 (+5V->+3V3).
  3. The single isolated supply: IH1215D (2W, 12V-in, +-15V-out isolated DC/DC -- the
     real, stock XP Power part that actually meets the brief's 2W figure; see
     task-7-report.md for why +-15V raw, not +-12V direct, and for the 1W-class
     alternatives in the same stock library that were rejected as under-spec), a pi
     filter (10uF-ferrite-10uF) on each output rail, then TPS7A4901 again (positive) and
     TPS7A3001 (negative, TPS7A4901's datasheet-documented "negative counterpart" --
     hardware/lib's own new symbols, see gen_wl_sync_lib.py) post-regulating the raw
     +-15V down to the clean ISO_P12/ISO_N12 contract nets referenced to INTAN_GND.
  4. Six PWR_FLAGs (+12V, -12V, AGND, DGND, plus ISO_P15_FILT/ISO_N15_FILT -- the pi
     filters' own downstream nodes, found empirically to need one too; see build()'s own
     comment for why exactly these six and none of the others, mirroring gen_mule.py's
     power_flag() reasoning).

Run directly: `python3 hardware/gen/gen_breakout_power.py` (writes
hardware/breakout/sheets/power.kicad_sch). hardware/breakout/sym-lib-table and
fp-lib-table both needed new entries for this task -- see task-7-report.md for exactly
which, and why each was a stock KiCad library already installed alongside the ones Task 6
registered, not a new custom one.
"""
from pathlib import Path

from kicad_sch import Sch, pin_pos, write_project_stub

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"

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
FOOTPRINT_MINIDIN = "wl-sync:MiniDIN_4_Panel"  # hardware/README.md's own custom footprint
FOOTPRINT_DCDC = "Converter_DCDC:Converter_DCDC_XP_POWER-IHxxxxD_THT"  # IH1215D's own stock Footprint property
FOOTPRINT_SOT223 = "Package_TO_SOT_SMD:SOT-223-3_TabPin2"  # LD1117S33TR -- identical to gen_mule.py's own usage
# HVSSOP-8-1EP, generic (not TI's own DGN0008[B/D/G] mechanical-suffix-specific footprint
# -- see gen_wl_sync_lib.py's TPS7A49_FOOTPRINT comment for why the generic one is used
# without asserting false certainty about which exact TI suffix applies).
FOOTPRINT_TPS7A49 = "Package_SO:HVSSOP-8-1EP_3x3mm_P0.65mm_EP1.57x1.89mm"

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
X_J1, Y_J1 = GRID(20), GRID(95.25)               # mini-DIN inlet
X_DIODE = GRID(66.04)
Y_D_P12, Y_D_N12 = GRID(53.34), GRID(137.16)     # +12V / -12V protection diode rows
X_CAP_BULK, X_CAP_SMALL = GRID(101.6), GRID(116.84)  # entry bulk-10uF / 100nF columns
X_TIE, Y_TIE = GRID(44.45), GRID(95.25)          # AGND/DGND star point

X_PWRFLAG0, Y_PWRFLAG = GRID(20), GRID(180.34)
PWRFLAG_DX = GRID(20.32)

X_U1, Y_U1 = GRID(200.66), GRID(53.34)           # TPS7A4901 #1: +12V -> +5V (logic)
X_U2, Y_U2 = GRID(340.36), GRID(53.34)           # LD1117S33TR_SOT223: +5V -> +3V3

X_U3, Y_U3 = GRID(200.66), GRID(224.79)          # IH1215D isolated DC-DC

X_PIFILT_P, Y_PIFILT_P = GRID(260), GRID(190.5)  # positive pi filter (ISO_P15_RAW->FILT)
X_U4, Y_U4 = GRID(340.36), GRID(190.5)           # TPS7A4901 #2: ISO_P15_FILT -> ISO_P12

X_PIFILT_N, Y_PIFILT_N = GRID(260), GRID(281.94) # negative pi filter (ISO_N15_RAW->FILT)
X_U5, Y_U5 = GRID(340.36), GRID(281.94)          # TPS7A3001: ISO_N15_FILT -> ISO_N12

SAT_DX = GRID(38.1)   # divider / pi-filter satellite horizontal offset from its IC/anchor
SAT_DY = GRID(20.32)  # satellite vertical offset (row spacing for stacked satellites)


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
    """Step 1: 4-pin mini-DIN inlet, one reverse-polarity Schottky per +-12V rail, bulk
    10uF+100nF on each rail, and the single AGND/DGND star-point net tie.

    Mini-DIN pin-to-rail assignment (hardware/lib/wl-sync.kicad_sym's own MiniDIN_4
    symbol is deliberately generic -- "Pin-to-rail assignment... is made where this is
    placed", per its Description property -- this is that assignment, made once, here):
    pin 1 = +12V (raw), pin 2 = -12V (raw), pin 3 = GND, pin 4 = shield. Shield ties
    directly to AGND at this same inlet point rather than getting its own net -- a cable
    shield terminated at the single-point star ground already established here avoids
    adding a second, competing ground reference right next to the one this whole section
    exists to make unambiguous.

    Reverse-polarity protection: ONE series Schottky per rail (SS14, 40V/1A), oriented so
    each rail's own NORMAL current direction forward-biases its diode and a wiring fault
    reverse-biases it (blocks, rather than damaging downstream parts):
      +12V: conventional current flows connector -> board (out of the external supply's +
      terminal, through the board's loads, back to GND) -- anode at the raw connector
      pin, cathode at the protected `+12V` net. Standard series reverse-polarity-diode
      orientation.
      -12V: the mirror image, worked through explicitly because it is easy to get
      backwards. A split +-12V supply is two supplies sharing one GND: for the negative
      one, GND is effectively ITS "+" terminal and the -12V pin is its "-" terminal, so
      conventional current flows board -> connector (out of whatever on the board sinks
      current into the negative rail, back through the -12V pin, back to the external
      supply) -- i.e. the OPPOSITE direction from the +12V case. So D2's anode sits on
      the PROTECTED `-12V` net (board side) and its cathode on the raw connector pin
      (source side) -- reversed from D1. Confirmed against the fault case: if the -12V
      pin is ever miswired to a positive source, that pin's now-higher potential would
      need to forward-bias cathode->anode to reach the board net, which is the diode's
      REVERSE direction -- blocked, exactly the intended fail-safe (board stays
      unpowered on that rail rather than the fault propagating downstream).
    """
    refs["inlet_diode"] = []
    refs["inlet_cap"] = []

    j1_ref = sch.next_ref("J")
    j1_pins = sch.place("wl-sync", "MiniDIN_4", j1_ref, "MiniDIN_4", X_J1, Y_J1, footprint=FOOTPRINT_MINIDIN)
    j1_map = {"1": "P12_RAW", "2": "N12_RAW", "3": "AGND", "4": "AGND"}
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

    # Bulk 10uF + 100nF at entry, per rail (brief Step 1, verbatim).
    for row_y, rail in ((Y_D_P12, "+12V"), (Y_D_N12, "-12V")):
        c_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_CAP_BULK, row_y, rail, "AGND", footprint=FOOTPRINT_C_BULK)
        c_small = two_pin(sch, "Device", "C", "C", "100nF", X_CAP_SMALL, row_y, rail, "AGND", footprint=FOOTPRINT_C_SMALL)
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
    """Step 2: TPS7A4901 (+12V->+5V) then LD1117S33TR_SOT223 (+5V->+3V3). Linear
    throughout, per the global "no switchers except the one isolated DC-DC" constraint.
    Both regulators' GND lands on DGND: +5V/+3V3 are the LOGIC rails (consumed by the
    digital sheets -- task-pc-digital, pi-interface, control-usb-i2c, per the plan's
    sheet list), so their return path belongs on the digital ground, not analog.

    TPS7A4901 divider: Vout = VFB(nom)*(1+R1/R2), VFB(nom)=1.185V (TI SBVS121E's own
    Eq.5 worked value). R2=10.0k (TI's own "set R2 to a standard 1% value" convention --
    see TPS7A3001's Table 2, which uses 10k for every listed output voltage). R1 solved
    for 5.0V: 10k*(5.0/1.185-1)=32.19k -> nearest E96 standard value 32.4k (same pair
    TPS7A30's own Table 2 lists for its -5V row, since the two families' VFB(nom)
    magnitudes are close enough that the same ratio lands on the same standard pair).
    Actual Vout = 1.185*(1+32.4/10) = 5.024V, +0.5%.
    """
    refs["ldo_cap"] = []
    u1_ref, u1_fb, u1_nrss = place_tps7a49_family(
        sch, "TPS7A4901", FOOTPRINT_TPS7A49, X_U1, Y_U1, "+12V", "DGND", "+5V", refs
    )
    fb_divider(sch, X_U1 - 2 * SAT_DX, Y_U1, "+5V", u1_fb, "DGND", "32.4k", "10.0k", refs)
    cin = two_pin(sch, "Device", "C", "C", "10uF", X_U1 + SAT_DX, Y_U1 - SAT_DY, "+12V", "DGND", footprint=FOOTPRINT_C_BULK)
    cout = two_pin(sch, "Device", "C", "C", "10uF", X_U1, Y_U1 + 2 * SAT_DY, "+5V", "DGND", footprint=FOOTPRINT_C_BULK)
    cout2 = two_pin(sch, "Device", "C", "C", "100nF", X_U1 + SAT_DX, Y_U1 + 2 * SAT_DY, "+5V", "DGND", footprint=FOOTPRINT_C_SMALL)
    refs["ldo_cap"] += [cin, cout, cout2]

    u2_ref = sch.next_ref("U")
    u2_pins = sch.place(
        "Regulator_Linear", "LD1117S33TR_SOT223", u2_ref, "LD1117S33TR_SOT223",
        X_U2, Y_U2, footprint=FOOTPRINT_SOT223,
    )
    u2_map = {"3": "+5V", "1": "DGND", "2": "+3V3"}  # VI, GND, VO -- matches gen_mule.py's own LD1117 pin map
    for num, net in u2_map.items():
        x, y = pin_pos(X_U2, Y_U2, u2_pins[num])
        sch.label(net, x, y)
    u2_dc = two_pin(sch, "Device", "C", "C", "100nF", X_U2, Y_U2 + SAT_DY, "+3V3", "DGND", footprint=FOOTPRINT_C_SMALL)
    u2_bulk = two_pin(sch, "Device", "C", "C", "10uF", X_U2 + SAT_DX, Y_U2 + SAT_DY, "+3V3", "DGND", footprint=FOOTPRINT_C_BULK)
    refs["ldo_cap"] += [u2_dc, u2_bulk]
    refs["u1"], refs["u2"] = u1_ref, u2_ref


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
    u3_ref = sch.next_ref("U")
    u3_pins = sch.place("Converter_DCDC_Isolated", "IH1215D", u3_ref, "IH1215D", X_U3, Y_U3, footprint=FOOTPRINT_DCDC)
    u3_map = {"1": "DGND", "3": "+12V", "4": "ISO_N15_RAW", "5": "ISO_P15_RAW", "6": "INTAN_GND"}
    for num, net in u3_map.items():
        x, y = pin_pos(X_U3, Y_U3, u3_pins[num])
        sch.label(net, x, y)
    x, y = pin_pos(X_U3, Y_U3, u3_pins["2"])  # NC
    sch.no_connect(x, y)
    u3_bypass = two_pin(sch, "Device", "C", "C", "100nF", X_U3 - SAT_DX, Y_U3 - SAT_DY, "+12V", "DGND", footprint=FOOTPRINT_C_SMALL)
    refs["u3"] = u3_ref
    refs["dcdc_bypass_c"] = [u3_bypass]

    # Positive rail: pi filter then TPS7A4901 (#2) post-regulation to ISO_P12.
    pi_filter(sch, X_PIFILT_P, Y_PIFILT_P, "ISO_P15_RAW", "ISO_P15_FILT", "INTAN_GND", refs)
    u4_ref, u4_fb, u4_nrss = place_tps7a49_family(
        sch, "TPS7A4901", FOOTPRINT_TPS7A49, X_U4, Y_U4, "ISO_P15_FILT", "INTAN_GND", "ISO_P12", refs
    )
    # R3=90.9k (E96), R4=10.0k: Vout=1.185*(1+90.9/10)=11.957V, -0.36%.
    fb_divider(sch, X_U4 - 2 * SAT_DX, Y_U4, "ISO_P12", u4_fb, "INTAN_GND", "90.9k", "10.0k", refs)
    u4_cout = two_pin(sch, "Device", "C", "C", "10uF", X_U4, Y_U4 + 2 * SAT_DY, "ISO_P12", "INTAN_GND", footprint=FOOTPRINT_C_BULK)
    u4_cout2 = two_pin(sch, "Device", "C", "C", "100nF", X_U4 + SAT_DX, Y_U4 + 2 * SAT_DY, "ISO_P12", "INTAN_GND", footprint=FOOTPRINT_C_SMALL)
    refs["iso_out_cap"] = [u4_cout, u4_cout2]

    # Negative rail: pi filter then TPS7A3001 post-regulation to ISO_N12.
    pi_filter(sch, X_PIFILT_N, Y_PIFILT_N, "ISO_N15_RAW", "ISO_N15_FILT", "INTAN_GND", refs)
    u5_ref, u5_fb, u5_nrss = place_tps7a49_family(
        sch, "TPS7A3001", FOOTPRINT_TPS7A49, X_U5, Y_U5, "ISO_N15_FILT", "INTAN_GND", "ISO_N12", refs
    )
    # R5=93.1k, R6=10.0k -- TPS7A30's OWN Table 2 standard-1%-resistor pair for Vout=-12V,
    # used as published rather than re-derived (TI SBVS125D Table 2).
    fb_divider(sch, X_U5 - 2 * SAT_DX, Y_U5, "ISO_N12", u5_fb, "INTAN_GND", "93.1k", "10.0k", refs)
    u5_cout = two_pin(sch, "Device", "C", "C", "10uF", X_U5, Y_U5 + 2 * SAT_DY, "ISO_N12", "INTAN_GND", footprint=FOOTPRINT_C_BULK)
    u5_cout2 = two_pin(sch, "Device", "C", "C", "100nF", X_U5 + SAT_DX, Y_U5 + 2 * SAT_DY, "ISO_N12", "INTAN_GND", footprint=FOOTPRINT_C_SMALL)
    refs["iso_out_cap"] += [u5_cout, u5_cout2]
    refs["u4"], refs["u5"] = u4_ref, u5_ref


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs) -- `refs` maps a role name to the reference designator(s)
    that play it, same convention gen_mule.py's own build() established, kept here for
    the same reason: a future PCB-layout task for the breakout board (none exists yet)
    would otherwise have to re-derive "which part is this" from bare reference numbers,
    an artifact of generation order rather than a stable contract.
    """
    sch = Sch(project="breakout")
    refs: dict = {}

    _place_inlet(sch, refs)
    _place_logic_rails(sch, refs)
    _place_isolated_supply(sch, refs)

    # PWR_FLAGs: +12V, -12V, AGND, DGND -- the four nets with no genuine power_out pin of
    # their own anywhere on this sheet (every OTHER contract net -- +5V, +3V3, ISO_P12,
    # ISO_N12, INTAN_GND -- is already driven by a real regulator/converter power_out
    # pin, so flagging those too would trip ERC's pin_to_pin "two power outputs
    # connected" rule instead of satisfying anything, same reasoning gen_mule.py's own
    # power_flag() docstring gives). AGND and DGND are flagged INDEPENDENTLY, even though
    # the star-point NetTie_2 physically bridges them: a net tie is deliberately NOT a
    # merge as far as ERC's per-net bookkeeping goes ("so ERC treats them as distinct
    # nets everywhere else", per the brief) -- treating that bridge as if it also
    # satisfied the OTHER net's own driven-ness check would be relying on the exact
    # ambiguity constraint 1 warns about, so each gets its own flag instead of assuming.
    sch.power_flag("+12V", GRID(X_PWRFLAG0), GRID(Y_PWRFLAG))
    sch.power_flag("-12V", GRID(X_PWRFLAG0 + PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("AGND", GRID(X_PWRFLAG0 + 2 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("DGND", GRID(X_PWRFLAG0 + 3 * PWRFLAG_DX), GRID(Y_PWRFLAG))

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
    sch.power_flag("ISO_P15_FILT", GRID(X_PWRFLAG0 + 4 * PWRFLAG_DX), GRID(Y_PWRFLAG))
    sch.power_flag("ISO_N15_FILT", GRID(X_PWRFLAG0 + 5 * PWRFLAG_DX), GRID(Y_PWRFLAG))

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
