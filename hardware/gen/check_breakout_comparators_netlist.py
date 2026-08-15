"""Parse hardware/breakout's exported netlist (and, for the two checks the exported netlist
provably cannot answer -- coordinate collisions and the instance-path ancestor chain -- the
raw .kicad_sch sources) and verify the comparators sheet's contract nets and topology are
electrically correct -- not just that ERC was silent. Same reasoning check_mule_netlist.py's,
check_breakout_power_netlist.py's, check_taskpc_digital_netlist.py's,
check_breakout_pi_interface_netlist.py's, check_breakout_analog_frontend_netlist.py's,
check_breakout_analog_ni_netlist.py's, and check_breakout_mux_intan_netlist.py's own
docstrings all give, restated because it is the reason THIS file exists too:
gen_breakout_comparators.py's own lib_symbols entries are keyed by full lib id, which
resolves correctly -- but "resolves correctly" is proven empirically per run, not
guaranteed by construction for every future edit. Keyed bare, or with a channel wired to
the wrong physical pin, KiCad loads the file without error and silently produces a
plausible, wrong netlist. So ERC passing is necessary but not sufficient evidence.

THE central risks this file exists to catch, named explicitly by this task's own brief:

  1. PD1_COMP/PD2_COMP/ACC_TRIG must exist and originate at a REAL comparator output pin
     (the LM339's own open_collector pin for that channel), not merely be present as a
     label with no real driver -- the exact gap this sheet's own job is to close (Tasks
     8/9 already named these nets; this sheet is the first to actually drive them).
  2. EVERY pull-up (all three populated ones, AND the fourth, DNP one) must land on +3V3
     and NEVER +5V -- task-10-brief.md's own "destroy-the-module constraint": these three
     outputs wire DIRECTLY into the sync module's own 3.3V-only GPIO (pi-interface.kicad_
     sch's own GPIO_PIN_SPEC, kind="direct", confirmed by reading that file), so a pull-up
     silently wired to +5V would be a fully-connected, ERC-clean netlist that destroys the
     module the first time that GPIO goes idle-high. Checked at the PIN level (which net a
     specific reference's own pin lands on), not merely "a net named +3V3 exists somewhere
     on this sheet".
  3. Hysteresis feedback (a 1M resistor from the comparator's own output back to its own
     '+' input, or to the '+'-node network on a populated channel) must exist on EVERY one
     of the 4 channels, including the unpopulated 4th -- "hysteresis is mandatory" applies
     to the DESIGN, not just to whichever channels happen to be stuffed.
  4. The MCP4728 must drive each comparator's own '-' (inverting) input from the RIGHT
     VOUT channel -- a silently permuted VOUTA<->VOUTB (say) would still produce a fully-
     connected, plausible netlist (every node present, every net named) while setting
     PD1_COMP's own threshold from the channel meant for PD2_COMP and vice versa.
  5. The 4th (A_MISC1) channel must be PRESENT (a real LM339 section, wired, ready to
     drive a real output net) but its own pull-up and hysteresis-feedback resistors must
     be DNP (Do Not Populate) -- "a populate option, not a respin": if a future edit
     silently un-marks the DNP flag (stuffing invisible, uncosted parts) or, the opposite
     failure, silently marks one of the three REAL channels' own resistors DNP (breaking a
     channel nobody asked to disable), neither should pass silently.
  6. Coordinate collisions -- this project has hit a real rail short from exactly this
     defect class before (analog-frontend.kicad_sch's own task-10a-report.md). Checked by
     parsing this sheet's own raw rendered text for every global-label (x, y),
     independently of the exported netlist (which only reports CONNECTIVITY, not the
     geometry that could have accidentally created it).
  7. No same-column real 2-pin part may have its own pin-to-pin reach overlap another's --
     the GENERAL form of risk 6 above (a two-pin part's own real pin-to-pin span landing on
     a row that is some integer multiple of a shared column's own row pitch away, which
     silently merged NI_5V into a channel's own data net on opto-ni.kicad_sch -- CH_ROW_DY's
     own account in gen_breakout_opto_ni.py), checked structurally (from real, rendered pin
     geometry) rather than only after an actual duplicate coordinate appears -- BACK-PORTED
     here at Task 11 fix round 2 from check_row_pitch_guard.py's own shared
     check_row_pitch_exceeds_2pin_span(), imported below (this sheet previously carried
     only risk 6's exact-duplicate-only scan, the same latent exposure task-11-report.md's
     own "Fix round 1" section flagged as a well-scoped follow-up).

`verify()` below re-derives, independently of gen_breakout_comparators.py's own choices,
the full channel/pin contract -- same "a checker that trusted the generator would only be
checking the generator against itself" discipline every prior checker in this project
already follows. The LM339 unit->pin table and MCP4728 pin table are redefined here from
the real datasheets (TI SLCS006Z Table 5-1; Microchip DS22187E page 2) independently of
gen_breakout_comparators.py's own module-docstring account of the same sourcing.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_comparators_netlist.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1 with
a description of the first failure otherwise.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mule_netlist import (  # noqa: E402
    CheckFailure,
    Node,
    check,
    parse_component_values,
    parse_netlist,
)
from check_row_pitch_guard import (  # noqa: E402
    check_row_pitch_exceeds_2pin_span,
    self_test_row_pitch,
)
from kicad_sch import (  # noqa: E402
    find_all_instance_paths,
    find_root_uuid,
    find_sheet_instance_path,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_COMPARATORS_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "comparators.kicad_sch"
COMPARATORS_SHEETFILE = "sheets/comparators.kicad_sch"  # exactly as breakout.kicad_sch's own
# Sheetfile property spells it -- must match gen_breakout_comparators.py's own constant.

# ---------------------------------------------------------------------------
# Channel/contract constants -- redefined here from this task's own brief and spec
# Sec.6.5, NEVER imported from the generator (same independence discipline every checker
# in this project already follows for its own contract nets/pin tables).
# ---------------------------------------------------------------------------
LM339_VALUE = "LM339"
MCP4728_VALUE = "MCP4728"

# LM339 unit -> (minus_pin, plus_pin, out_pin) -- TI SLCS006Z, Table 5-1 "Pin Functions",
# D/DB/N/NS/PW/DYY/J (14-pin) column, grouped into 4 real comparators by DESCRIPTION text
# ("comparator 1".."comparator 4"), cross-checked directly against KiCad's own
# Comparator:LM339 library entry (its 5 sub-units' own pin groupings match Table 5-1's own
# 4 real comparators, plus VCC=3/GND=12, exactly -- see gen_breakout_comparators.py's own
# module docstring for the full account, including why TI's own "comparator N" numbering
# does not match KiCad's own unit index 1:1 and why that mismatch is immaterial
# electrically). Keyed here, independently, by KiCad's own unit index (1-4) -- the number
# this checker uses to independently identify which physical pins a given `sch.place(...,
# unit=N)` call actually wired, the same number the generator's own placement calls use.
LM339_UNIT_PINS = {
    1: ("4", "5", "2"),     # TI's own "comparator 1": IN2-, IN2+, OUT2
    2: ("6", "7", "1"),     # TI's own "comparator 2": IN1-, IN1+, OUT1
    3: ("10", "11", "13"),  # TI's own "comparator 4": IN4-, IN4+, OUT4
    4: ("8", "9", "14"),    # TI's own "comparator 3": IN3-, IN3+, OUT3
}
LM339_PIN_VPOS = "3"
LM339_PIN_VNEG = "12"
# All 14 physical pins -- used by the completeness check below to confirm every one of
# them carries a real net (no accidentally-dropped pin), independent of which ROLE each
# plays.
LM339_ALL_PINS = {p for triplet in LM339_UNIT_PINS.values() for p in triplet} | {LM339_PIN_VPOS, LM339_PIN_VNEG}
assert LM339_ALL_PINS == {str(n) for n in range(1, 15)}, sorted(LM339_ALL_PINS, key=int)

# MCP4728 pin map -- Microchip DS22187E, page 2, "Package Type" (10-lead MSOP), transcribed
# verbatim.
MCP4728_PIN_VDD = "1"
MCP4728_PIN_SCL = "2"
MCP4728_PIN_SDA = "3"
MCP4728_PIN_LDAC = "4"
MCP4728_PIN_RDYBSY = "5"
MCP4728_PIN_VOUTA = "6"
MCP4728_PIN_VOUTB = "7"
MCP4728_PIN_VOUTC = "8"
MCP4728_PIN_VOUTD = "9"
MCP4728_PIN_VSS = "10"

# (LM339 unit, analog source net, produced/local output net, this channel's own MCP4728
# VOUT pin, populated?) -- this task's own literal net-name contract for the first three
# (PD1_COMP/PD2_COMP/ACC_TRIG, already referenced by pi-interface.kicad_sch and
# taskpc-digital.kicad_sch); A_MISC1_COMP is this sheet's own choice for the unpopulated
# 4th, since the brief names no contract net for it.
CHANNELS = [
    (1, "A_PD1", "PD1_COMP", MCP4728_PIN_VOUTA, True),
    (2, "A_PD2", "PD2_COMP", MCP4728_PIN_VOUTB, True),
    (3, "A_ACC", "ACC_TRIG", MCP4728_PIN_VOUTC, True),
    (4, "A_MISC1", "A_MISC1_COMP", MCP4728_PIN_VOUTD, False),
]
assert len(CHANNELS) == 4
assert len({c[0] for c in CHANNELS}) == 4
assert len({c[2] for c in CHANNELS}) == 4
CONTRACT_OUTPUT_NETS = ["PD1_COMP", "PD2_COMP", "ACC_TRIG"]  # this task's own literal
# "Produces" list -- A_MISC1_COMP is NOT in this list (see module docstring, risk 5).

PULLUP_VALUE = "10k"
FEEDBACK_VALUE = "1M"
SERIES_VALUE = "10k"


# ---------------------------------------------------------------------------
# Shared helpers -- duplicated from prior checkers' own technique, not imported (this
# project's own "never import from a sibling generator/checker" discipline, beyond the
# explicitly shared check_mule_netlist.py infra).
# ---------------------------------------------------------------------------


def _find_bridging_resistor(nets: dict[str, list[Node]], net_a: str, net_b: str) -> str:
    """The single R-prefixed reference with one pin on net_a and the other on net_b."""
    check(net_a in nets, f"missing net: {net_a!r}")
    check(net_b in nets, f"missing net: {net_b!r}")
    ra = {n.ref for n in nets[net_a] if n.ref.startswith("R")}
    rb = {n.ref for n in nets[net_b] if n.ref.startswith("R")}
    both = ra & rb
    check(
        len(both) == 1,
        f"expected exactly 1 resistor bridging {net_a!r}<->{net_b!r}, found on {net_a}: "
        f"{sorted(ra)}, on {net_b}: {sorted(rb)}",
    )
    return next(iter(both))


def _lm339_ref(values: dict[str, str]) -> str:
    refs = [r for r, v in values.items() if v == LM339_VALUE]
    check(len(refs) == 1, f"expected exactly 1 {LM339_VALUE!r} instance, found {len(refs)}: {refs}")
    return refs[0]


def _mcp4728_ref(values: dict[str, str]) -> str:
    refs = [r for r, v in values.items() if v == MCP4728_VALUE]
    check(len(refs) == 1, f"expected exactly 1 {MCP4728_VALUE!r} instance, found {len(refs)}: {refs}")
    return refs[0]


_COMP_BLOCK_RE = re.compile(r'\(comp\s+\(ref\s+"([^"]+)"\)', re.S)
_DNP_PROPERTY_RE = re.compile(r'\(property\s*\(name\s+"dnp"\)\s*\)')


def parse_dnp_refs(text: str) -> set[str]:
    """{reference} for every component whose own `(comp ...)` block in the EXPORTED
    NETLIST carries a `(property (name "dnp"))` marker -- kicad-cli's own real export
    shape for a DNP="yes" symbol, confirmed directly against this sheet's own real
    export (not assumed): the property is emitted, value-less, ONLY when dnp=yes, and
    omitted entirely (not "(value \"no\")") otherwise -- checked against every one of
    this project's other 376+ components, all DNP="no", none of which carry this
    property at all. This is what lets a checker confirm the 4th channel's own pull-up/
    feedback resistors are genuinely marked "populate option" rather than merely
    "unconnected" or "value zero" -- kicad_sch.py's own `dnp` field on `Sch.place()`
    (added this task) is a generation-time-only property with no effect on
    connectivity (see that function's own docstring) but a real, distinctly-shaped
    effect on the exported netlist's own component metadata, which is what this
    function reads.
    """
    matches = list(_COMP_BLOCK_RE.finditer(text))
    out: set[str] = set()
    for idx, m in enumerate(matches):
        ref = m.group(1)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end():end]
        if _DNP_PROPERTY_RE.search(block):
            out.add(ref)
    return out


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_outputs_originate_at_comparator(nets: dict[str, list[Node]], lm_ref: str) -> str:
    """Every one of PD1_COMP/PD2_COMP/ACC_TRIG exists and has exactly one open_collector
    pin belonging to the LM339, at that channel's own real physical OUT pin (not merely
    present as a label with only a downstream LOAD pin, the state these nets were in
    before this sheet existed -- see module docstring, risk 1)."""
    for unit, _src, out_net, _dac_pin, _pop in CHANNELS:
        if out_net not in CONTRACT_OUTPUT_NETS:
            continue
        _minus, _plus, out_pin = LM339_UNIT_PINS[unit]
        check(out_net in nets, f"missing contract net: {out_net!r}")
        oc_nodes = [n for n in nets[out_net] if n.ref == lm_ref and n.pintype == "open_collector"]
        check(
            len(oc_nodes) == 1,
            f"{out_net}: expected exactly 1 open_collector pin belonging to {lm_ref}, "
            f"found {oc_nodes} (all nodes on this net: {nets[out_net]})",
        )
        check(
            oc_nodes[0].pin == out_pin,
            f"{out_net}: {lm_ref}'s open_collector pin is {oc_nodes[0].pin}, expected "
            f"pin {out_pin} (channel {unit}'s own real physical OUT pin, TI SLCS006Z "
            f"Table 5-1) -- a permuted channel-to-pin assignment",
        )
    return (
        f"All 3 contract nets ({CONTRACT_OUTPUT_NETS}) originate at {lm_ref}'s own "
        f"open_collector output pin, each at its own real physical OUT pin."
    )


def _check_pullups_on_3v3_never_5v(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """THE destroy-hardware constraint (module docstring, risk 2): every one of the 4
    pull-ups (all 3 populated ones AND the 4th, DNP one) bridges +3V3<->out_net, and
    NONE has any pin on +5V. Checked at the pin/reference level, not merely "a net named
    +3V3 exists somewhere on this sheet"."""
    check("+3V3" in nets, "missing net: '+3V3'")
    for unit, _src, out_net, _dac_pin, _pop in CHANNELS:
        r_ref = _find_bridging_resistor(nets, "+3V3", out_net)
        check(
            values.get(r_ref) == PULLUP_VALUE,
            f"{r_ref} (channel {unit} pull-up): expected Value {PULLUP_VALUE!r}, found "
            f"{values.get(r_ref)!r}",
        )
        on_5v = [n for n in nets.get("+5V", []) if n.ref == r_ref]
        check(
            not on_5v,
            f"{r_ref} (channel {unit} pull-up, out_net={out_net!r}): also has a pin on "
            f"+5V ({on_5v}) -- pull-ups MUST go to +3V3, NEVER +5V. All three comparator "
            f"outputs wire directly into the sync module's own 3.3V-only GPIO; a +5V "
            f"pull-up would destroy it the first time this line goes idle-high.",
        )
    return (
        f"All 4 pull-ups (channels {[c[0] for c in CHANNELS]}) bridge +3V3<->their own "
        f"output net, value {PULLUP_VALUE!r}, and none has any pin on +5V."
    )


def _check_hysteresis_feedback(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """Hysteresis feedback (1M) exists on EVERY one of the 4 channels (module docstring,
    risk 3) -- bridging out_net to the '+'-node network: a real, separate f"{out_net}_FB"
    node on the 3 populated channels (reached from the analog source through its own 10k
    series resistor), or the source net directly on the 4th, unpopulated channel (see
    gen_breakout_comparators.py's own module docstring for why that channel has no
    series resistor of its own)."""
    for unit, source_net, out_net, _dac_pin, populated in CHANNELS:
        fb_net = f"{out_net}_FB" if populated else source_net
        r_ref = _find_bridging_resistor(nets, out_net, fb_net)
        check(
            values.get(r_ref) == FEEDBACK_VALUE,
            f"{r_ref} (channel {unit} hysteresis feedback): expected Value "
            f"{FEEDBACK_VALUE!r}, found {values.get(r_ref)!r}",
        )
        if populated:
            r_ser = _find_bridging_resistor(nets, source_net, fb_net)
            check(
                values.get(r_ser) == SERIES_VALUE,
                f"{r_ser} (channel {unit} hysteresis series resistor): expected Value "
                f"{SERIES_VALUE!r}, found {values.get(r_ser)!r}",
            )
    return (
        f"Hysteresis feedback ({FEEDBACK_VALUE!r}) present on all 4 channels "
        f"({[c[0] for c in CHANNELS]}); the 3 populated channels also confirmed on their "
        f"own {SERIES_VALUE!r} series resistor."
    )


def _check_dac_drives_inverting_inputs(nets: dict[str, list[Node]], values: dict[str, str], lm_ref: str, dac_ref: str) -> str:
    """Every comparator's own '-' (inverting) input is driven by THIS channel's own
    MCP4728 VOUT pin -- not a permuted one (module docstring, risk 4). Checked per
    channel: the thr_net has exactly 2 real nodes, the LM339's own minus pin AND the
    MCP4728's own expected VOUT pin, nothing else."""
    for unit, _src, out_net, dac_pin, _pop in CHANNELS:
        minus_pin, _plus, _out = LM339_UNIT_PINS[unit]
        thr_net = f"{out_net}_THR"
        check(thr_net in nets, f"missing net: {thr_net!r}")
        lm_nodes = [n for n in nets[thr_net] if n.ref == lm_ref]
        dac_nodes = [n for n in nets[thr_net] if n.ref == dac_ref]
        check(
            len(lm_nodes) == 1 and lm_nodes[0].pin == minus_pin,
            f"{thr_net}: expected exactly 1 {lm_ref} node at pin {minus_pin} (channel "
            f"{unit}'s own '-' input), found {lm_nodes}",
        )
        check(
            len(dac_nodes) == 1 and dac_nodes[0].pin == dac_pin,
            f"{thr_net}: expected exactly 1 {dac_ref} node at pin {dac_pin} (channel "
            f"{unit}'s own VOUT), found {dac_nodes} -- a permuted DAC-channel-to-"
            f"comparator assignment",
        )
        check(
            len(nets[thr_net]) == 2,
            f"{thr_net}: expected exactly 2 nodes (the DAC's own VOUT pin, the "
            f"comparator's own '-' pin), found {len(nets[thr_net])}: {nets[thr_net]}",
        )
    return (
        f"All 4 channels' own '-' inputs are driven by {dac_ref}'s own expected VOUT pin "
        f"(no permutation), each thr_net carrying exactly the 2 expected nodes."
    )


def _check_fourth_channel_dnp(nets: dict[str, list[Node]], values: dict[str, str], dnp_refs: set[str], lm_ref: str) -> str:
    """The 4th (A_MISC1) channel is PRESENT (a real, wired LM339 section) but its own
    pull-up and hysteresis-feedback resistors are DNP -- 'a populate option, not a
    respin' (module docstring, risk 5). '+' and '-' are real, permanent, never-floating
    connections (checked here too): only the two OUTPUT-side passives are DNP."""
    unit, source_net, out_net, dac_pin, populated = CHANNELS[3]
    check(not populated, "internal inconsistency: CHANNELS[3] must be the unpopulated channel")
    minus_pin, plus_pin, _out_pin = LM339_UNIT_PINS[unit]

    check(
        any(n.ref == lm_ref and n.pin == plus_pin for n in nets.get(source_net, [])),
        f"4th channel: {lm_ref} pin {plus_pin} ('+') not found directly on {source_net!r} "
        f"-- the 4th channel's own '+' input must be a real, permanent wire (never DNP, "
        f"never floating), regardless of the pull-up/feedback resistors' own DNP state",
    )
    thr_net = f"{out_net}_THR"
    check(
        any(n.ref == lm_ref and n.pin == minus_pin for n in nets.get(thr_net, [])),
        f"4th channel: {lm_ref} pin {minus_pin} ('-') not found on {thr_net!r}",
    )

    r_pu = _find_bridging_resistor(nets, "+3V3", out_net)
    r_fb = _find_bridging_resistor(nets, out_net, source_net)
    check(
        r_pu in dnp_refs,
        f"4th channel pull-up {r_pu}: expected DNP (unpopulated -- a populate option, "
        f"not a respin), but the exported netlist does not mark it DNP",
    )
    check(
        r_fb in dnp_refs,
        f"4th channel hysteresis feedback {r_fb}: expected DNP (unpopulated), but the "
        f"exported netlist does not mark it DNP",
    )

    for unit2, _src2, out_net2, _dac2, populated2 in CHANNELS:
        if not populated2:
            continue
        r_pu2 = _find_bridging_resistor(nets, "+3V3", out_net2)
        r_fb2 = _find_bridging_resistor(nets, out_net2, f"{out_net2}_FB")
        check(
            r_pu2 not in dnp_refs,
            f"channel {unit2}'s own pull-up {r_pu2}: unexpectedly marked DNP -- this is "
            f"one of the 3 REAL, populated channels, not the 4th 'populate option' one",
        )
        check(
            r_fb2 not in dnp_refs,
            f"channel {unit2}'s own hysteresis feedback {r_fb2}: unexpectedly marked "
            f"DNP -- this is one of the 3 REAL, populated channels",
        )
    return (
        f"4th channel (A_MISC1 -> {out_net}) present -- {lm_ref}'s own '+'/'-' pins "
        f"real and permanently wired -- with its own pull-up ({r_pu}) and hysteresis "
        f"feedback ({r_fb}) correctly marked DNP, and none of the 3 populated channels' "
        f"own resistors incorrectly marked DNP."
    )


def _check_lm339_pin_completeness(nets: dict[str, list[Node]], lm_ref: str) -> str:
    """All 14 physical LM339 pins carry a real net -- confirms no pin was silently
    dropped (a channel missing its own '-' or '+' input, say) even though this sheet's
    own generator wires every one of them by construction.

    ALSO asserts the supply pair itself: V+ on +12V and V- on AGND, and V- on NOTHING
    ELSE -- specifically never -12V. That is not a stylistic preference. LM339 pin 12 is
    the common emitter of all four open-collector output transistors, so it IS this
    sheet's own output LOW level; on -12V an output LOW is ~-11.9V, which reaches
    J7/GPIO20/21/25 and U11's HCT541 inputs with no series resistance and no clamp
    anywhere on the path (both rated -0.5V absolute minimum). This checker's own reason
    to exist is defects ERC cannot see, and a power_in pin on a valid, correctly-typed
    power net is never an ERC violation regardless of WHICH power net -- exactly the
    shape of the pull-up-to-+5V defect one function above. See
    gen_breakout_comparators.py's own "THE SECOND DESTROY-HARDWARE CONSTRAINT"."""
    check("+12V" in nets, "missing net: '+12V'")
    check("AGND" in nets, "missing net: 'AGND'")
    found_pins = {
        n.pin for name, nodes in nets.items() for n in nodes if n.ref == lm_ref
    }
    check(
        found_pins == LM339_ALL_PINS,
        f"{lm_ref}: expected all 14 physical pins ({sorted(LM339_ALL_PINS, key=int)}) "
        f"to carry a net, found {sorted(found_pins, key=int)} -- missing: "
        f"{sorted(LM339_ALL_PINS - found_pins, key=int)}",
    )
    check(
        any(n.ref == lm_ref and n.pin == LM339_PIN_VPOS for n in nets["+12V"]),
        f"{lm_ref}: VCC (pin {LM339_PIN_VPOS}) is not on +12V",
    )
    check(
        any(n.ref == lm_ref and n.pin == LM339_PIN_VNEG for n in nets["AGND"]),
        f"{lm_ref}: negative supply (pin {LM339_PIN_VNEG}) is not on AGND -- this pin is "
        f"the common emitter of all four open-collector outputs, so it IS this sheet's "
        f"own output LOW level; anything but AGND puts that level onto GPIO20/21/25 and "
        f"U11's HCT541 inputs directly",
    )
    vneg_nets = sorted(
        name for name, nodes in nets.items()
        if any(n.ref == lm_ref and n.pin == LM339_PIN_VNEG for n in nodes)
    )
    check(
        vneg_nets == ["AGND"],
        f"{lm_ref}: negative supply (pin {LM339_PIN_VNEG}) is on {vneg_nets} -- expected "
        f"AGND and nothing else. A NEGATIVE rail here (-12V was the original, "
        f"hardware-destroying choice) makes every open-collector output LOW ~-11.9V, "
        f"which reaches J7's own GPIO20/21/25 (pad absolute minimum -0.5V) and U11's "
        f"SN74HCT541 inputs (likewise) with no series resistance and no clamp anywhere",
    )
    return (
        f"{lm_ref}: all 14 physical pins carry a real net; V+ (pin {LM339_PIN_VPOS}) on "
        f"+12V, V- (pin {LM339_PIN_VNEG}) on AGND and on nothing else -- never a "
        f"negative rail, which would BE the output LOW level."
    )


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    """Scans this sheet's OWN raw rendered text for every global_label's real (x, y) and
    asserts no two DIFFERENT net names ever share one -- a real short, indistinguishable
    from a correct design by the exported netlist alone. This project has hit exactly this
    defect before (analog-frontend.kicad_sch's own task-10a-report.md)."""
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 20, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really comparators.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in comparators.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short between those nets: "
        f"{list(collisions.items())[:5]}",
    )
    return (
        f"No coordinate collisions: all {len(coords)} distinct global-label positions in "
        f"comparators.kicad_sch carry exactly one net name each."
    )


def _check_component_values(values: dict[str, str], lm_ref: str, dac_ref: str) -> str:
    check(values.get(lm_ref) == LM339_VALUE, f"{lm_ref}: expected Value {LM339_VALUE!r}, found {values.get(lm_ref)!r}")
    check(values.get(dac_ref) == MCP4728_VALUE, f"{dac_ref}: expected Value {MCP4728_VALUE!r}, found {values.get(dac_ref)!r}")
    return f"Component values confirmed: {lm_ref}={LM339_VALUE!r}, {dac_ref}={MCP4728_VALUE!r}."


def verify(nets: dict[str, list[Node]], values: dict[str, str], dnp_refs: set[str]) -> list[str]:
    summary = []
    lm_ref = _lm339_ref(values)
    dac_ref = _mcp4728_ref(values)

    summary.append(_check_outputs_originate_at_comparator(nets, lm_ref))
    summary.append(_check_pullups_on_3v3_never_5v(nets, values))
    summary.append(_check_hysteresis_feedback(nets, values))
    summary.append(_check_dac_drives_inverting_inputs(nets, values, lm_ref, dac_ref))
    summary.append(_check_fourth_channel_dnp(nets, values, dnp_refs, lm_ref))
    summary.append(_check_lm339_pin_completeness(nets, lm_ref))
    summary.append(_check_component_values(values, lm_ref, dac_ref))

    # -12V is deliberately NOT in this list any more: this sheet does not consume it at
    # all (see _check_lm339_pin_completeness above and gen_breakout_comparators.py's own
    # "THE SECOND DESTROY-HARDWARE CONSTRAINT"). It still exists board-wide -- the analog
    # front end's own op-amps run on it -- so its presence in `nets` says nothing about
    # THIS sheet either way, which is exactly why the V- assertion above is pin-level.
    for rail in ("+12V", "+3V3", "AGND"):
        check(rail in nets, f"missing consumed rail: {rail!r}")
        check(len(nets[rail]) >= 10, f"{rail}: suspiciously small population ({len(nets[rail])} nodes)")
    summary.append("Consumed rails (+12V/+3V3/AGND -- NOT -12V) present with substantial populations.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire, rather than
# passing vacuously -- same convention every other checker in this project establishes.
# ---------------------------------------------------------------------------


def _assert_fails(nets, values, dnp_refs, expect_substring: str, label: str) -> str:
    try:
        verify(nets, values, dnp_refs)
    except CheckFailure as e:
        check(
            expect_substring in str(e),
            f"self-test {label!r}: verify() failed, but not with the expected complaint "
            f"(expected a message containing {expect_substring!r}, got: {e})",
        )
        return str(e)
    raise CheckFailure(
        f"self-test {label!r}: verify() did NOT raise on a corrupted netlist -- the check "
        f"this self-test exists to validate is passing vacuously"
    )


def self_test(good_nets: dict[str, list[Node]], good_values: dict[str, str], good_dnp: set[str]) -> list[str]:
    """Negative controls. `good_nets`/`good_values`/`good_dnp` must already pass verify()
    cleanly -- each corruption below is a minimal, targeted mutation of that known-good
    structure."""
    results = []
    lm_ref = _lm339_ref(good_values)
    dac_ref = _mcp4728_ref(good_values)

    # (1) THE central negative control this task's own brief explicitly asks for: a
    # pull-up ALSO wired to +5V -- the destroy-hardware defect. Simulated by ADDING a
    # stray +5V node for the SAME reference while LEAVING its own genuine +3V3 node in
    # place (rather than moving it), so this specifically exercises the "must NOT also
    # be on +5V" assertion -- same technique check_breakout_mux_intan_netlist.py's own
    # "REF pin re-referenced to AGND" negative control uses, and for the same reason: a
    # coordinate collision or a stray second label call (this project's own recurring
    # defect class, every prior sheet's own task-10a-report.md precedent) would ADD a
    # node, not silently move one.
    also_5v = copy.deepcopy(good_nets)
    r_pu2 = _find_bridging_resistor(good_nets, "+3V3", "PD2_COMP")
    stray_node = next(n for n in good_nets["+3V3"] if n.ref == r_pu2)
    also_5v.setdefault("+5V", []).append(stray_node)
    msg = _assert_fails(also_5v, good_values, good_dnp, "also has a pin on +5V", f"{r_pu2} (PD2_COMP's own pull-up) also wired to +5V")
    results.append(f"Pull-up also wired to +5V (the destroy-hardware defect, PD2_COMP's own {r_pu2}): caught -- {msg}")

    # (2) A comparator output silently missing its own real driver (simulating a channel
    # whose OWN open_collector pin got dropped/mislabeled): ACC_TRIG's own LM339 node
    # removed.
    no_driver = copy.deepcopy(good_nets)
    victim_node = next(n for n in good_nets["ACC_TRIG"] if n.ref == lm_ref)
    no_driver["ACC_TRIG"] = [n for n in no_driver["ACC_TRIG"] if n != victim_node]
    msg = _assert_fails(no_driver, good_values, good_dnp, "expected exactly 1 open_collector pin", f"ACC_TRIG's own {lm_ref} driver pin removed")
    results.append(f"Comparator output with no real driver (ACC_TRIG, {lm_ref}'s own pin removed): caught -- {msg}")

    # (3) Hysteresis feedback removed from one channel (PD1_COMP's own 1M resistor's
    # node on PD1_COMP_FB deleted, simulating a dropped/misrouted feedback resistor).
    no_hyst = copy.deepcopy(good_nets)
    r_fb1 = _find_bridging_resistor(good_nets, "PD1_COMP", "PD1_COMP_FB")
    no_hyst["PD1_COMP_FB"] = [n for n in no_hyst["PD1_COMP_FB"] if n.ref != r_fb1]
    msg = _assert_fails(no_hyst, good_values, good_dnp, "expected exactly 1 resistor bridging", f"PD1_COMP's own hysteresis feedback ({r_fb1}) dropped")
    results.append(f"Hysteresis feedback resistor dropped (PD1_COMP, {r_fb1}): caught -- {msg}")

    # (4) DAC channel permutation: VOUTA and VOUTB swapped between PD1_COMP_THR and
    # PD2_COMP_THR (the DAC's own nodes only -- the comparators' own '-' pins stay put),
    # simulating a generator edit that assigns the wrong VOUT pin to a channel.
    swapped_dac = copy.deepcopy(good_nets)
    a_idx = next(i for i, n in enumerate(swapped_dac["PD1_COMP_THR"]) if n.ref == dac_ref)
    b_idx = next(i for i, n in enumerate(swapped_dac["PD2_COMP_THR"]) if n.ref == dac_ref)
    swapped_dac["PD1_COMP_THR"][a_idx], swapped_dac["PD2_COMP_THR"][b_idx] = (
        swapped_dac["PD2_COMP_THR"][b_idx], swapped_dac["PD1_COMP_THR"][a_idx],
    )
    msg = _assert_fails(swapped_dac, good_values, good_dnp, "a permuted DAC-channel-to-comparator assignment", f"{dac_ref}'s own VOUTA/VOUTB swapped between PD1_COMP_THR/PD2_COMP_THR")
    results.append(f"DAC channel permutation (VOUTA/VOUTB swapped, PD1/PD2 thresholds): caught -- {msg}")

    # (5) 4th channel's own pull-up silently un-marked DNP (simulating an edit that
    # accidentally stuffs an "unpopulated" part without updating its own generator call).
    unmarked = set(good_dnp)
    r_pu4 = _find_bridging_resistor(good_nets, "+3V3", "A_MISC1_COMP")
    unmarked.discard(r_pu4)
    msg = _assert_fails(good_nets, good_values, unmarked, "expected DNP (unpopulated", f"4th channel's own pull-up ({r_pu4}) silently un-marked DNP")
    results.append(f"4th channel pull-up silently un-marked DNP ({r_pu4}): caught -- {msg}")

    # (6) THE OTHER half of risk 5: a REAL channel's own resistor accidentally marked
    # DNP (simulating a copy-paste/generator-edit mistake that disables a channel
    # nobody asked to disable).
    over_marked = set(good_dnp)
    r_pu1 = _find_bridging_resistor(good_nets, "+3V3", "PD1_COMP")
    over_marked.add(r_pu1)
    msg = _assert_fails(good_nets, good_values, over_marked, "unexpectedly marked DNP", f"PD1_COMP's own real pull-up ({r_pu1}) accidentally marked DNP")
    results.append(f"Real channel's own pull-up accidentally marked DNP (PD1_COMP, {r_pu1}): caught -- {msg}")

    # (7) 4th channel's own '+' input floating (its own direct wire to A_MISC1 dropped).
    floating_plus = copy.deepcopy(good_nets)
    _minus4, plus4, _out4 = LM339_UNIT_PINS[4]
    floating_plus["A_MISC1"] = [n for n in floating_plus["A_MISC1"] if not (n.ref == lm_ref and n.pin == plus4)]
    msg = _assert_fails(floating_plus, good_values, good_dnp, "not found directly on", f"4th channel's own '+' input ({lm_ref} pin {plus4}) dropped from A_MISC1")
    results.append(f"4th channel '+' input disconnected from A_MISC1 (a real floating-input hazard, {lm_ref} pin {plus4}): caught -- {msg}")

    # (8) Series/pull-up/feedback resistor value drift (10k -> 1k on PD2's own series R).
    drifted = dict(good_values)
    r_ser2 = _find_bridging_resistor(good_nets, "A_PD2", "PD2_COMP_FB")
    drifted[r_ser2] = "1k"
    msg = _assert_fails(good_nets, drifted, good_dnp, "expected Value", f"{r_ser2} (PD2's own hysteresis series resistor) value drift 10k->1k")
    results.append(f"Hysteresis series resistor value drift (10k -> 1k, PD2, {r_ser2}): caught -- {msg}")

    # (9) THE OTHER destroy-hardware defect on this sheet, and the one that was actually
    # committed: the LM339's own V- MOVED from AGND back to -12V. Simulated as a real
    # move (delete the AGND node, add the same pin to -12V) rather than an addition,
    # because that is exactly the shape of the defect -- one net label on one pin -- and
    # it must be caught by the "is it on AGND" half of the assertion.
    neg_rail = copy.deepcopy(good_nets)
    vneg_node = next(n for n in good_nets["AGND"] if n.ref == lm_ref and n.pin == LM339_PIN_VNEG)
    neg_rail["AGND"] = [n for n in neg_rail["AGND"] if n != vneg_node]
    neg_rail.setdefault("-12V", []).append(vneg_node)
    msg = _assert_fails(neg_rail, good_values, good_dnp, f"pin {LM339_PIN_VNEG}) is not on AGND", f"{lm_ref}'s own V- moved from AGND to -12V")
    results.append(f"LM339 V- on a NEGATIVE rail (the destroy-hardware defect: V- IS the output LOW level, {lm_ref} pin {LM339_PIN_VNEG}): caught -- {msg}")

    # (10) The subtler half of (9): V- correctly on AGND but ALSO shorted to -12V (a
    # stray second label, this project's own recurring defect class -- the same reason
    # negative control (1) above ADDS a +5V node rather than moving one). "On AGND" alone
    # would pass this; only the "and on nothing else" half catches it.
    also_neg = copy.deepcopy(good_nets)
    also_neg.setdefault("-12V", []).append(vneg_node)
    msg = _assert_fails(also_neg, good_values, good_dnp, "expected AGND and nothing else", f"{lm_ref}'s own V- on AGND but ALSO on -12V")
    results.append(f"LM339 V- also shorted to a negative rail ({lm_ref} pin {LM339_PIN_VNEG} on AGND AND -12V): caught -- {msg}")

    return results


def self_test_collision(good_sch_text: str) -> str:
    """Negative control for _check_no_coordinate_collisions(): splices a SECOND global
    label's own (x, y) onto a FIRST label's coordinate and confirms the check fires on
    the resulting synthetic short."""
    matches = list(_GLOBAL_LABEL_RE.finditer(good_sch_text))
    check(len(matches) > 10, "self-test setup failed: too few global labels found to corrupt")
    first = matches[0]
    victim = next(m for m in matches if m.group(1) != first.group(1))
    corrupted = (
        good_sch_text[: victim.start(2)]
        + first.group(2) + " " + first.group(3)
        + good_sch_text[victim.end(3):]
    )
    check(corrupted != good_sch_text, "self-test setup failed: splice produced no change")
    try:
        _check_no_coordinate_collisions(corrupted)
    except CheckFailure as e:
        check(
            "DISTINCT global-label net name" in str(e),
            f"self-test 'coordinate collision reintroduced': wrong failure message: {e}",
        )
        return str(e)
    raise CheckFailure(
        "self-test 'coordinate collision reintroduced' did NOT raise -- "
        "_check_no_coordinate_collisions() is passing vacuously"
    )


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker in
# this project's own verify_instance_paths()/self_test_instance_paths().
# ---------------------------------------------------------------------------


def verify_instance_paths(comp_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(comp_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"comparators.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with "
        f"breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, COMPARATORS_SHEETFILE)

    paths = find_all_instance_paths(comp_sch_text)
    check(
        len(paths) >= 18,
        f"only found {len(paths)} (instances (path ...)) entries in comparators.kicad_sch "
        f"-- expected >=18 (recomputed directly against this task's own real output: 21 "
        f"placed instances -- 5 LM339 unit placements (each `sch.place(unit=N)` call is "
        f"its own instance, same as mux-intan.kicad_sch's own 8 ADG1206YRUZ) + 1 MCP4728 "
        f"+ 1 PWR_FLAG (I2C_SCL) + 11 resistors (3 channels x 3 -- series/feedback/pullup "
        f"-- + the 4th channel's own 2 -- feedback/pullup, no series) + 3 decoupling caps "
        f"-- the >=18 floor is intentionally left below that real count so a future small "
        f"edit doesn't need this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in comparators.kicad_sch do not "
        f"resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}",
    )
    return (
        f"All {len(paths)} component instance paths in comparators.kicad_sch resolve to "
        f"the real ancestor chain {expected_prefix!r} (breakout's own root uuid + the "
        f"'comparators' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(comp_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(comp_text, breakout_text)
    except CheckFailure as e:
        check(
            expect_substring in str(e),
            f"self-test {label!r}: verify_instance_paths() failed, but not with the "
            f"expected complaint (expected a message containing {expect_substring!r}, "
            f"got: {e})",
        )
        return str(e)
    raise CheckFailure(
        f"self-test {label!r}: verify_instance_paths() did NOT raise on a corrupted "
        f"schematic -- the check this self-test exists to validate is passing vacuously"
    )


def self_test_instance_paths(good_comp_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_comp_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, COMPARATORS_SHEETFILE)

    corrupted = good_comp_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_comp_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_comp_text may not actually be passing verify_instance_paths() cleanly",
    )
    msg = _assert_instance_paths_fail(
        corrupted, good_breakout_text, "do not resolve to the real ancestor chain",
        "one component's instance path reverted to self-referential",
    )
    return [
        f"Self-referential instance path (this file's own root uuid in place of the real "
        f"breakout ancestor chain, on one component) reintroduced: caught -- {msg}"
    ]


def main() -> int:
    net_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NET_PATH
    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print(
            "  kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net "
            "hardware/breakout/breakout.kicad_sch"
        )
        return 1
    text = net_path.read_text()
    nets = parse_netlist(text)
    values = parse_component_values(text)
    dnp_refs = parse_dnp_refs(text)
    print(f"parsed {len(nets)} nets, {len(values)} component values, {len(dnp_refs)} DNP refs from {net_path}")
    try:
        summary = verify(nets, values, dnp_refs)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS:")
    for line in summary:
        print(f"  - {line}")

    try:
        self_test_results = self_test(nets, values, dnp_refs)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in self_test_results:
        print(f"  - {line}")

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_COMPARATORS_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_COMPARATORS_SCH} do not exist -- "
            f"run gen_breakout.py and gen_breakout_comparators.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    comp_sch_text = DEFAULT_COMPARATORS_SCH.read_text()

    try:
        collision_summary = _check_no_coordinate_collisions(comp_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {collision_summary}")

    try:
        collision_self_test_msg = self_test_collision(comp_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: coordinate collision reintroduced: caught -- {collision_self_test_msg}")

    try:
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(comp_sch_text, "comparators.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(comp_sch_text, "comparators.kicad_sch", min_instances=10)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

    try:
        path_summary = verify_instance_paths(comp_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(comp_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
