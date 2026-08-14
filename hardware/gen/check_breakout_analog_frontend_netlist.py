"""Parse hardware/breakout's exported netlist (and, for the instance-path check the
exported netlist provably cannot answer -- see below, the raw .kicad_sch sources) and
verify the analog front-end sheet's contract nets and topology are electrically correct --
not just that ERC was silent.

Why this exists, not just ERC (same reasoning check_mule_netlist.py's,
check_breakout_power_netlist.py's, check_taskpc_digital_netlist.py's, and
check_breakout_pi_interface_netlist.py's own docstrings all give, restated because it is the
reason THIS file exists too): gen_breakout_analog_frontend.py's own lib_symbols entries are
keyed by full lib id, which resolves correctly -- but "resolves correctly" is proven
empirically per run, not guaranteed by construction for every future edit. Keyed bare, or
with a channel accidentally wired to the wrong pin, KiCad loads the file without error and
silently produces a plausible, wrong netlist. So ERC passing is necessary but not sufficient.

THE central risk this file exists to catch, named explicitly by this task's own brief: this
sheet's entire reason for existing is that every one of the 16 sources is received
DIFFERENTIALLY (sensing centre against shield/ACCESIO_AGND, not against board AGND) so that a
Faraday-cage bulkhead-bonding decision can be made or reversed later without touching the
board (spec Sec.5.6). A front end that got silently simplified into a plain AGND-referenced
buffer -- one wrong net label, invisible to ERC (still a fully-connected, 0-error netlist) --
would defeat the entire point of this sheet while looking correct at a glance. `verify()`
below re-derives, independently of gen_breakout_analog_frontend.py's own choices, exactly
which pin of which part is allowed to touch AGND directly (INA105's REF pin, the shell-bond
resistor's far end, the MIC discrete diff-amp's R4) and which pin must NOT (every difference/
transimpedance amplifier's own reference input) -- same "a checker that trusted the generator
would only be checking the generator against itself" discipline every prior checker in this
project already follows for its own contract nets.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_analog_frontend_netlist.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1 with
a description of the first failure otherwise.
"""
from __future__ import annotations

import copy
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
from kicad_sch import (  # noqa: E402
    find_all_instance_paths,
    find_root_uuid,
    find_sheet_instance_path,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_AF_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "analog-frontend.kicad_sch"
ANALOG_FRONTEND_SHEETFILE = "sheets/analog-frontend.kicad_sch"  # exactly as breakout.kicad_sch's
# own Sheetfile property spells it -- must match gen_breakout_analog_frontend.py's own constant.

# ---------------------------------------------------------------------------
# Channel classification -- spec Sec.3.2's "16 sources" table, redefined here from the spec
# rather than imported from the generator's own CONTRACT_NETS_PRODUCED/ACCESIO_MAP/etc, same
# independence discipline every other checker in this project already follows for GPIO maps,
# physical pin tables, and the like.
# ---------------------------------------------------------------------------
ALL_16_NETS = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_NETS) == 16

ACCESIO_CHANNELS = ["A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP"]
BNC_CHANNELS = [n for n in ALL_16_NETS if n not in ACCESIO_CHANNELS]
assert len(ACCESIO_CHANNELS) == 6 and len(BNC_CHANNELS) == 10

PHOTODIODE_CHANNELS = ["A_PD1", "A_PD2"]
MIC_CHANNEL = "A_MIC"
# The 13 channels whose input stage is a plain INA105 unity-gain difference receiver --
# everything except the 2 photodiodes (transimpedance) and the mic (discrete diff-amp +
# filter, topologically different even though also a difference receiver).
PLAIN_DIFFAMP_CHANNELS = [n for n in ALL_16_NETS if n not in PHOTODIODE_CHANNELS + [MIC_CHANNEL]]
assert len(PLAIN_DIFFAMP_CHANNELS) == 13

# Real TI INA105 datasheet pinout (SOIC-8/DIP-8, standard unity-gain application circuit) --
# re-derived from the part's own datasheet-standard pin assignment, not imported from the
# generator's own placement code. REF is the ONLY pin of this part legitimately tied to AGND.
INA105 = {"ref_pin": "1", "minus": "2", "plus": "3", "vneg": "4", "sense": "5", "out": "6", "vpos": "7"}

# OPA2197xD (dual op-amp) unit/pin roles -- fixed by the STOCK KICAD SYMBOL's own pin NAMES
# ("-"/"+"/output, confirmed directly against the library via kicad_sch.py's extract_symbol()/
# unit_pins() while this checker was written), not a generator layout choice: unit 1 uses
# pins (out=1, minus=2, plus=3); unit 2 uses pins (plus=5, minus=6, out=7).
OPA2197_UNIT = {
    "A_PD1": {"minus": "2", "plus": "3", "out": "1"},
    "A_PD2": {"minus": "6", "plus": "5", "out": "7"},
}


def _refs_on(nets: dict[str, list[Node]], net: str) -> set[str]:
    return {n.ref for n in nets.get(net, [])}


def _nodes_on(nets: dict[str, list[Node]], net: str, ref: str) -> list[Node]:
    return [n for n in nets.get(net, []) if n.ref == ref]


def _find_bridging_resistor(nets: dict[str, list[Node]], net_a: str, net_b: str) -> str:
    """The single R-prefixed reference with one pin on net_a and the other on net_b --
    same "identify by walking two known nets" technique every checker in this project uses
    (e.g. check_breakout_pi_interface_netlist.py's own GPIO0/1 series-resistor check).
    """
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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_16_contract_nets(nets: dict[str, list[Node]]) -> str:
    for name in ALL_16_NETS:
        check(name in nets, f"missing contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    return f"All 16 A_* contract nets present and populated (>=2 nodes each)."


def _check_bnc_shell_bonds(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """Every one of the 10 BNC channels' own shield node reaches AGND THROUGH A RESISTOR --
    never a direct short to AGND (spec Sec.5.6: "The shield ties to AGND through ~10 Ohm,
    providing the return and a DC reference") -- plus the ACCESIO connector's own 19-pin
    AGND bundle, bonded to board AGND the identical way (module docstring: "the same class
    of ground offset"). A shell net that collapsed onto bare AGND (no resistor, no distinct
    net) would fail this by construction: `net_a` below would equal `net_b`, and
    `_find_bridging_resistor` would find no resistor with one pin on each because there is
    only one net, not two.
    """
    for name in BNC_CHANNELS:
        shield_net = f"{name}_SHLD"
        check(
            shield_net in nets,
            f"{name}: shield net {shield_net!r} does not exist as its own distinct net -- "
            f"if it collapsed onto bare AGND (KiCad's own netlist exporter merges same-net "
            f"labels under one chosen name, same as the +12V/AGND short this generator hit "
            f"and fixed during development), this is exactly what that merge looks like",
        )
        r_ref = _find_bridging_resistor(nets, shield_net, "AGND")
        check(
            values.get(r_ref) == "10",
            f"{name}: shell-bond resistor {r_ref} should be '10' (ohms, spec Sec.5.6's "
            f"'~10 Ohm'), found {values.get(r_ref)!r}",
        )
        j_refs = {n.ref for n in nets[shield_net] if n.ref.startswith("J")}
        check(len(j_refs) == 1, f"{name}: expected exactly 1 connector (J-ref) on {shield_net!r}, found {j_refs}")

    r_ref = _find_bridging_resistor(nets, "ACCESIO_AGND", "AGND")
    check(
        values.get(r_ref) == "10",
        f"ACCESIO_AGND: bond resistor {r_ref} should be '10' (ohms), found {values.get(r_ref)!r}",
    )
    accesio_agnd_pins = {n.pin for n in nets["ACCESIO_AGND"] if n.ref.startswith("J")}
    check(
        len(accesio_agnd_pins) == 19,
        f"ACCESIO_AGND: expected all 19 of the connector's own AGND pins present, found {len(accesio_agnd_pins)}",
    )
    return (
        f"All 10 BNC channels' own shield nodes reach AGND through a dedicated 10R "
        f"resistor (never directly); the ACCESIO connector's 19-pin AGND bundle is bonded "
        f"to AGND the same way."
    )


def _check_no_direct_agnd_reference(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """No front end's OWN differential-reference input sits directly on AGND where it
    should sit on its own shield/ACCESIO_AGND node -- the central risk this checker exists
    to catch (module docstring). Checked three different ways, matching the three different
    circuit topologies this sheet actually uses (a checker asserting only one shape would
    miss defects in the other two):

    (1) The 13 plain INA105 channels: pin 2 ("-") must be the channel's own reference node
        (its BNC shield, or ACCESIO_AGND for the six eye channels) and must NOT be AGND
        itself. Pin 1 (REF) IS allowed on AGND -- that is what lands the OUTPUT in the AGND
        domain, and is the one pin per instance this check does not flag.
    (2) The 2 photodiode TIAs: the non-inverting ("+") input must be the channel's own BNC
        shield, not AGND -- spec Sec.6.3.1 / module docstring topology 2's whole point.
    (3) The mic channel's own discrete difference amplifier: the non-inverting node
        (A_MIC_DAP) must NOT itself be AGND (R4 alone reaches AGND, one hop downstream of
        the actual op-amp input), and A_MIC_SHLD must genuinely feed it via R3.
    """
    for name in PLAIN_DIFFAMP_CHANNELS:
        out_refs = {n.ref for n in nets[name] if values.get(n.ref) == "INA105KU"}
        check(len(out_refs) == 1, f"{name}: expected exactly 1 INA105KU driving this net, found {out_refs}")
        ref = next(iter(out_refs))
        minus_nodes = _nodes_on(nets, "AGND", ref)
        check(
            not any(n.pin == INA105["minus"] for n in minus_nodes),
            f"{name}: {ref}'s own '-' pin (reference input) is wired directly to AGND -- "
            f"it must reference its own shield/ACCESIO_AGND node instead (spec Sec.5.6); "
            f"this would defeat the whole point of receiving this channel differentially",
        )
        ref_nodes = _nodes_on(nets, "AGND", ref)
        check(
            any(n.pin == INA105["ref_pin"] for n in ref_nodes),
            f"{name}: {ref}'s own REF pin (1) is not on AGND -- its output would not land "
            f"in the AGND domain",
        )
        expected_shield = "ACCESIO_AGND" if name in ACCESIO_CHANNELS else f"{name}_SHLD"
        minus_net_nodes = [n for n in nets.get(expected_shield, []) if n.ref == ref and n.pin == INA105["minus"]]
        check(
            len(minus_net_nodes) == 1,
            f"{name}: {ref}'s own '-' pin is not on its expected reference node "
            f"{expected_shield!r}",
        )

    for name in PHOTODIODE_CHANNELS:
        clamp_net = f"{name}_CLAMP"
        shield_net = f"{name}_SHLD"
        unit = OPA2197_UNIT[name]
        minus_refs = {n.ref for n in nets[clamp_net] if n.pin == unit["minus"] and values.get(n.ref, "").startswith("OPA2197")}
        check(len(minus_refs) == 1, f"{name}: expected exactly 1 OPA2197xD summing-junction node on {clamp_net!r}, found {minus_refs}")
        ref = next(iter(minus_refs))
        check(
            not any(n.ref == ref and n.pin == unit["plus"] for n in nets.get("AGND", [])),
            f"{name}: {ref}'s own '+' pin (non-inverting reference) is wired directly to "
            f"AGND -- it must reference the shield node {shield_net!r} instead (spec "
            f"Sec.6.3.1 / module docstring topology 2), or shield-borne disturbance "
            f"immunity is lost for this channel",
        )
        check(
            any(n.ref == ref and n.pin == unit["plus"] for n in nets.get(shield_net, [])),
            f"{name}: {ref}'s own '+' pin is not on its expected shield node {shield_net!r}",
        )

    check("A_MIC_DAP" in nets, "missing net: 'A_MIC_DAP' (mic discrete diff-amp's own non-inverting node)")
    check("A_MIC_DAP" != "AGND", "A_MIC_DAP node incorrectly identical to AGND")
    dap_opamp_pins = [n for n in nets["A_MIC_DAP"] if values.get(n.ref, "").startswith("OPA4197")]
    check(
        len(dap_opamp_pins) == 1 and dap_opamp_pins[0].pintype == "input",
        f"A_MIC_DAP: expected exactly 1 OPA4197xD 'input'-typed pin (the diff-amp's own "
        f"non-inverting input), found {dap_opamp_pins}",
    )
    # A resistor bridges the shield node to A_MIC_DAP (R3, spec Sec.5.6's shield-reference
    # path) -- _find_bridging_resistor() itself raises with a specific message if none
    # exists, so a bare call is the check (no separate boolean assertion needed).
    _find_bridging_resistor(nets, "A_MIC_SHLD", "A_MIC_DAP")
    return (
        "No front end's own differential-reference input ('-' on the 13 INA105 channels, "
        "'+' on the 2 photodiode TIAs, the mic's own discrete non-inverting node) is wired "
        "directly to AGND; each references its own shield/ACCESIO_AGND node instead, with "
        "only the one designed exception per channel (INA105's REF pin) actually touching AGND."
    )


def _check_photodiode_tia_topology(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """Confirms the photodiode stages are genuine TRANSIMPEDANCE amplifiers, not just
    op-amps with no feedback: a real Rf/Cf pair bridging the summing junction (CLAMP) to
    the raw output (RAWOUT), plus a single-pole anti-alias RC low-pass (RAWOUT -> R -> the
    final A_PDn net -> C -> AGND) downstream of it.
    """
    for name in PHOTODIODE_CHANNELS:
        clamp_net = f"{name}_CLAMP"
        raw_net = f"{name}_RAWOUT"
        rf_ref = _find_bridging_resistor(nets, clamp_net, raw_net)
        check(
            values.get(rf_ref) == "1M",
            f"{name}: TIA feedback resistor {rf_ref} should be '1M' (spec Sec.6.3.1's own "
            f"worked example), found {values.get(rf_ref)!r}",
        )
        cf_refs = ({n.ref for n in nets[clamp_net] if n.ref.startswith("C")}
                   & {n.ref for n in nets[raw_net] if n.ref.startswith("C")})
        check(len(cf_refs) == 1, f"{name}: expected exactly 1 feedback capacitor bridging {clamp_net!r}<->{raw_net!r}, found {cf_refs}")

        check(name in nets, f"missing net: {name!r}")
        aa_r = ({n.ref for n in nets[raw_net] if n.ref.startswith("R")}
                & {n.ref for n in nets[name] if n.ref.startswith("R")})
        check(len(aa_r) == 1, f"{name}: expected exactly 1 anti-alias resistor bridging {raw_net!r}<->{name!r}, found {aa_r}")
        c_on_out = {n.ref for n in nets[name] if n.ref.startswith("C")}
        c_on_agnd = {n.ref for n in nets["AGND"] if n.ref.startswith("C")}
        check(
            bool(c_on_out & c_on_agnd),
            f"{name}: expected an anti-alias shunt capacitor from the final net to AGND, none found",
        )
    return (
        "Both photodiode channels confirmed as genuine transimpedance amplifiers: Rf=1M "
        "feedback resistor + feedback capacitor bridging the summing junction to the raw "
        "output, followed by a single-pole R/C anti-alias low-pass to the final A_PDn net."
    )


def _check_mic_4th_order(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """The mic channel's own low-pass is 4th order: two CASCADED Sallen-Key sections (4
    frequency-setting capacitors total -- a Sallen-Key low-pass's order is exactly its own
    capacitor count), not one (2nd order) or zero. Also confirms the two sections are
    genuinely IN SERIES (section 1's own output feeds section 2's own input), not two
    parallel copies of the same stage, and that the discrete difference-receive stage
    (unit 1) is distinct from both filter stages (units 2, 3) -- three different op-amp
    UNITS of the same physical package, not one unit doing double duty.
    """
    da_out, s1_out, final_out = "A_MIC_DA", "A_MIC_S1", "A_MIC"
    for net in (da_out, s1_out, final_out):
        check(net in nets, f"missing net: {net!r}")

    quad_refs = {n.ref for n in nets[final_out] if values.get(n.ref, "").startswith("OPA4197")}
    check(len(quad_refs) == 1, f"A_MIC: expected exactly 1 OPA4197xD driving the final net, found {quad_refs}")
    quad_ref = next(iter(quad_refs))

    da_pins = {n.pin for n in nets[da_out] if n.ref == quad_ref}
    s1_pins = {n.pin for n in nets[s1_out] if n.ref == quad_ref}
    final_pins = {n.pin for n in nets[final_out] if n.ref == quad_ref}
    check(len(da_pins) == 1, f"A_MIC_DA: expected exactly 1 {quad_ref} pin (the discrete diff-amp's own output), found {da_pins}")
    check(len(s1_pins) == 2, f"A_MIC_S1: expected exactly 2 {quad_ref} pins (Sallen-Key section 1's own out+minus follower), found {s1_pins}")
    check(len(final_pins) == 2, f"A_MIC: expected exactly 2 {quad_ref} pins (Sallen-Key section 2's own out+minus follower), found {final_pins}")
    check(
        da_pins.isdisjoint(s1_pins) and s1_pins.isdisjoint(final_pins) and da_pins.isdisjoint(final_pins),
        f"A_MIC: the discrete diff-amp and the two Sallen-Key sections must use three "
        f"DISTINCT op-amp units of {quad_ref}, found overlapping pins: DA={da_pins} "
        f"S1={s1_pins} FINAL={final_pins}",
    )

    # Section 1's own internal RC nodes (n1 = "_SKN", plus-side = "_SKP") and section 2's
    # own (bare "A_MIC" prefix) -- 4 distinct capacitor references total = 4 poles.
    sk_nodes = ["A_MIC_S1_SKN", "A_MIC_S1_SKP", "A_MIC_SKN", "A_MIC_SKP"]
    cap_refs = set()
    for node in sk_nodes:
        check(node in nets, f"missing net: {node!r} (mic Sallen-Key internal node)")
        caps = {n.ref for n in nets[node] if n.ref.startswith("C")}
        check(len(caps) == 1, f"{node}: expected exactly 1 capacitor, found {caps}")
        cap_refs |= caps
    check(
        len(cap_refs) == 4,
        f"A_MIC low-pass: expected 4 DISTINCT frequency-setting capacitors (2 Sallen-Key "
        f"sections x 2 poles each = 4th order), found {len(cap_refs)}: {sorted(cap_refs)}",
    )

    # Section 1's own output (A_MIC_S1) must feed section 2's own input resistor -- the
    # cascade, not two independent copies of the same stage.
    r_into_s2 = {n.ref for n in nets[s1_out] if n.ref.startswith("R")} & {n.ref for n in nets["A_MIC_SKN"] if n.ref.startswith("R")}
    check(len(r_into_s2) == 1, f"A_MIC: section 1's own output does not feed section 2's own input resistor -- not a real cascade, found {r_into_s2}")

    return (
        f"A_MIC low-pass confirmed as genuine 4th order: 2 cascaded Sallen-Key sections on "
        f"{quad_ref} (3 distinct op-amp units total, including the discrete diff-amp), 4 "
        f"distinct frequency-setting capacitors, section 1's output feeding section 2's input."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    summary.append(_check_16_contract_nets(nets))
    summary.append(_check_bnc_shell_bonds(nets, values))
    summary.append(_check_no_direct_agnd_reference(nets, values))
    summary.append(_check_photodiode_tia_topology(nets, values))
    summary.append(_check_mic_4th_order(nets, values))

    # +12V/-12V/AGND consumed, not produced fresh on this sheet (spec's own "Consumes"
    # list) -- present with a real population, same sanity floor every prior checker
    # applies to its own consumed contract nets.
    for rail in ("+12V", "-12V", "AGND"):
        check(rail in nets, f"missing consumed rail: {rail!r}")
        check(len(nets[rail]) >= 10, f"{rail}: suspiciously small population ({len(nets[rail])} nodes) for a rail this sheet places ~15 ICs on")
    summary.append("Consumed rails (+12V/-12V/AGND) present with substantial populations.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire, rather than
# passing vacuously -- same convention every other checker in this project establishes.
# ---------------------------------------------------------------------------


def _assert_fails(nets, values, expect_substring: str, label: str) -> str:
    try:
        verify(nets, values)
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


def self_test(good_nets: dict[str, list[Node]], good_values: dict[str, str]) -> list[str]:
    """Negative controls. `good_nets`/`good_values` must already pass verify() cleanly --
    each corruption below is a minimal, targeted mutation of that known-good structure.
    """
    results = []

    # (1) THE central defect this checker exists to catch: an INA105 channel's own '-'
    # reference pin moved from its shield node directly onto AGND -- the exact
    # "differential receive silently simplified to a plain AGND-referenced buffer" failure
    # spec Sec.5.6 exists to prevent, still a fully-connected 0-error netlist.
    moved = copy.deepcopy(good_nets)
    # NOT just "pin == INA105['minus']" -- the BNC connector's OWN shell pin is ALSO
    # numbered "2" (Connector:Conn_Coaxial's "Ext" pin), so filtering on pin number alone
    # picks whichever of the two happens to sort first. Filter on the INA105 instance
    # itself (Value == "INA105KU") to target the real '-' pin unambiguously.
    idx = next(
        i for i, n in enumerate(moved["A_AMB_SHLD"])
        if n.pin == INA105["minus"] and good_values.get(n.ref) == "INA105KU"
    )
    victim = moved["A_AMB_SHLD"].pop(idx)
    moved["AGND"] = moved["AGND"] + [victim]
    msg = _assert_fails(moved, good_values, "wired directly to AGND", "A_AMB's INA105 '-' pin moved from its shield node onto bare AGND")
    results.append(f"Plain-channel diff-amp '-' pin silently AGND-referenced instead of shield-referenced: caught -- {msg}")

    # (2) The photodiode-specific flavour of the same defect class: the TIA's own '+'
    # (non-inverting reference) moved from shield onto AGND.
    moved2 = copy.deepcopy(good_nets)
    unit = OPA2197_UNIT["A_PD1"]
    idx2 = next(i for i, n in enumerate(moved2["A_PD1_SHLD"]) if n.pin == unit["plus"])
    victim2 = moved2["A_PD1_SHLD"].pop(idx2)
    moved2["AGND"] = moved2["AGND"] + [victim2]
    msg = _assert_fails(moved2, good_values, "wired directly to AGND", "A_PD1's TIA '+' pin moved from shield onto bare AGND")
    results.append(f"Photodiode TIA '+' pin silently AGND-referenced instead of shield-referenced: caught -- {msg}")

    # (3) A BNC shield bonded DIRECTLY to AGND (no resistor at all) -- simulated by
    # collapsing A_JOY_X_SHLD's own identity into AGND (deleting the distinct net, moving
    # every one of its nodes onto AGND directly, and removing the bond resistor's now-
    # redundant AGND-side pin so it doesn't look like a 2-pin bridge that happens to have
    # the same name on both ends).
    collapsed = copy.deepcopy(good_nets)
    shield_nodes = collapsed.pop("A_JOY_X_SHLD")
    bond_ref = next(n.ref for n in shield_nodes if n.ref.startswith("R"))
    collapsed["AGND"] = [n for n in collapsed["AGND"] if not (n.ref == bond_ref)] + shield_nodes
    msg = _assert_fails(collapsed, good_values, "does not exist as its own distinct net", "A_JOY_X shield bonded directly to AGND (no resistor)")
    results.append(f"BNC shell bonded directly to AGND, bypassing the 10R return resistor entirely: caught -- {msg}")

    # (4) Shell-bond resistor value drift (10R -> 100R) -- spec Sec.5.6's own "~10 Ohm" is
    # a real design constant (sets the DC reference / return-path impedance), not a
    # cosmetic choice.
    drifted = dict(good_values)
    r_ref = next(n.ref for n in good_nets["A_MISC1_SHLD"] if n.ref.startswith("R"))
    drifted[r_ref] = "100"
    msg = _assert_fails(good_nets, drifted, "should be '10'", f"{r_ref} (A_MISC1 shell-bond resistor) value drift 10R->100R")
    results.append(f"Shell-bond resistor value drift (10R -> 100R): caught -- {msg}")

    # (5) Photodiode TIA feedback resistor removed entirely (simulating an edit that
    # deletes the feedback path -- an open-loop comparator-like mess, not a TIA).
    no_fb = copy.deepcopy(good_nets)
    rf_ref = next(n.ref for n in no_fb["A_PD2_CLAMP"] if values_ref_is_1M(good_values, n.ref))
    no_fb["A_PD2_CLAMP"] = [n for n in no_fb["A_PD2_CLAMP"] if n.ref != rf_ref]
    no_fb["A_PD2_RAWOUT"] = [n for n in no_fb["A_PD2_RAWOUT"] if n.ref != rf_ref]
    msg = _assert_fails(no_fb, good_values, "expected exactly 1 resistor bridging", "A_PD2 TIA feedback resistor deleted entirely")
    results.append(f"Photodiode TIA feedback resistor deleted (no longer a real transimpedance stage): caught -- {msg}")

    # (6) Mic filter quietly de-featured to 2nd order: section 2 short-circuited so its own
    # output net becomes identical to section 1's (same node set) -- simulating someone
    # "simplifying" the cascade down to one Sallen-Key section while leaving the final net
    # name alone.
    flattened = copy.deepcopy(good_nets)
    flattened["A_MIC"] = list(flattened["A_MIC_S1"])
    msg = _assert_fails(flattened, good_values, "DISTINCT op-amp units", "mic filter's 2nd Sallen-Key section collapsed onto the 1st (2nd order, not 4th)")
    results.append(f"Mic filter silently de-featured from 4th order to 2nd order: caught -- {msg}")

    return results


def values_ref_is_1M(values: dict[str, str], ref: str) -> bool:
    return ref.startswith("R") and values.get(ref) == "1M"


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker in
# this project's own verify_instance_paths()/self_test_instance_paths().
# ---------------------------------------------------------------------------


def verify_instance_paths(af_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(af_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"analog-frontend.kicad_sch's own file-identity uuid ({own_root_uuid}) collides "
        f"with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, ANALOG_FRONTEND_SHEETFILE)

    paths = find_all_instance_paths(af_sch_text)
    check(
        len(paths) >= 128,
        f"only found {len(paths)} (instances (path ...)) entries in analog-frontend.kicad_sch "
        f"-- expected >=128 (recomputed directly against this task's own real output: 134 "
        f"placed instances as committed, not guessed)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in analog-frontend.kicad_sch do "
        f"not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found "
        f"{bad[:3]!r} instead",
    )
    return (
        f"All {len(paths)} component instance paths in analog-frontend.kicad_sch resolve to "
        f"the real ancestor chain {expected_prefix!r} (breakout's own root uuid + the "
        f"'analog-frontend' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(af_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(af_text, breakout_text)
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


def self_test_instance_paths(good_af_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_af_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, ANALOG_FRONTEND_SHEETFILE)

    corrupted = good_af_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_af_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_af_text may not actually be passing verify_instance_paths() "
        "cleanly to begin with",
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
    print(f"parsed {len(nets)} nets, {len(values)} component values from {net_path}")
    try:
        summary = verify(nets, values)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS:")
    for line in summary:
        print(f"  - {line}")

    try:
        self_test_results = self_test(nets, values)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in self_test_results:
        print(f"  - {line}")

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_AF_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_AF_SCH} do not exist -- run "
            f"gen_breakout.py and gen_breakout_analog_frontend.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    af_sch_text = DEFAULT_AF_SCH.read_text()
    try:
        path_summary = verify_instance_paths(af_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(af_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
