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

FIX ROUND 2 (task-10b-report.md): MISC1-3's own /1//2 attenuator jumpers, previously two
INDEPENDENT Conn_01x03 headers per channel (one per leg) that fix round 1's own leg-symmetry
checks below could confirm were WIRED symmetrically but never confirm were physically
GANGED (nothing stopped the two shunts from being left in disagreeing positions -- a real
field-reconfiguration risk, not just an initial-assembly one), are now ONE Conn_02x03 header
per channel populated with a single 2-gang shorting block. `_check_misc_leg_symmetry()`'s own
checks (2), (3) below are unchanged in substance; check (1) and a NEW check (4) close the gap
fix round 1 left open by confirming both legs' own jumper headers resolve to the SAME
component reference, not merely the same topology.

FIX ROUND 3 (task-10b-report.md, this round): fix round 2's own Conn_02x03_Top_Bottom pin
assignment (row 1 = signal leg pins 1-3, row 2 = shield leg pins 4-6) assumed a physical pad
adjacency the REAL footprint (PinHeader_2x03_P2.54mm_Vertical) does not have -- confirmed by
rendering the real .kicad_mod (`kicad-cli fp export svg`) and reading its pad coordinates
directly (`hardware/gen/kicad_pcb.py`'s own `footprint_pads()`): the real part is
COLUMN-paired (pins (1,2)/(3,4)/(5,6) adjacent at the header's own 2.54mm pitch), not
row-paired the way Top_Bottom's own SCHEMATIC SYMBOL drawing suggested -- Conn_02x03_Top_
Bottom's own pin N/pin N+3 pairing sits 3.59mm apart on the diagonal, not adjacent at all.
Under fix round 2's assignment, NO shunt position on the real part achieved "both legs at
the same tap" -- worse than the leg-asymmetry defect fix round 1 closed out.
`gen_breakout_analog_frontend.py` now uses Conn_02x03_Odd_Even with pins INTERLEAVED
(column A = pins 1,3,5 = signal leg's own raw/div/mid, column B = pins 2,4,6 = shield leg's),
matching the real footprint's actual geometry; `_check_header_pin_roles()`'s own calls below
are updated to the new pin numbers, and a NEW check, `verify_footprint_pad_adjacency()`
(bottom of this file, parallel to `verify_instance_paths()`), reads the real footprint's pad
geometry directly and asserts the pin pairs this generator relies on being physically
bridgeable actually are -- closing the process gap (nothing in this toolchain previously
read footprint geometry at all; ERC and every netlist-based check, including every other one
in this file, can only ever see electrical connectivity, which is orthogonal to a jumper
header's physical pad layout by construction), not just this one instance.

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
import math
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
from kicad_pcb import footprint_pads  # noqa: E402
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
MISC_CHANNELS = ["A_MISC1", "A_MISC2", "A_MISC3"]  # the only 3 of 16 with a jumper-
# selectable divider on EITHER leg -- see _check_misc_leg_symmetry() (fix round 1).
assert all(n in ALL_16_NETS for n in MISC_CHANNELS)
DIVIDER_R_VALUE = "10.0k 0.1%"  # both legs' /1//2 divider resistors (fix round 1) -- must
# match EXACTLY between legs, or a shield disturbance leaks through in /2 mode again.
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
        # MISC channels' own shield leg carries a SECOND J-ref: the /1//2 shunt header's
        # own pin 2 (fix round 3 -- ONE Conn_02x03_Odd_Even header now carries both legs,
        # column B = shield leg raw/div/mid; pin 2 lands directly on this net, the same
        # relationship the signal leg's own pin 1 already has to its own CLAMP node) --
        # _check_misc_leg_symmetry() checks that header's own full topology, including
        # that both legs' own headers are genuinely the SAME physical part; this just
        # widens the connector-count expectation for these 3 channels specifically.
        expected_j_count = 2 if name in MISC_CHANNELS else 1
        check(
            len(j_refs) == expected_j_count,
            f"{name}: expected exactly {expected_j_count} connector(s) (J-ref) on "
            f"{shield_net!r}, found {j_refs}",
        )

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


def _check_header_pin_roles(
    nets: dict[str, list[Node]], raw_net: str, mid_net: str, div_net: str,
    raw_pin: str, div_pin: str, mid_pin: str, label: str,
) -> str:
    """Confirm ONE shunt-jumper header genuinely implements "raw_pin = raw node direct,
    div_pin = COMMON (to the amplifier), mid_pin = /2 tap" across raw_net/div_net/mid_net --
    not just three nets that happen to share a component reference. Used by
    _check_misc_leg_symmetry() for BOTH legs of every MISC channel, with DIFFERENT pin
    numbers per leg since fix round 2 (task-10b-report.md) merged what were two separate
    Conn_01x03 headers (each its own pins 1/2/3) into ONE Conn_02x03 header. Fix round 3
    (task-10b-report.md, this round) corrected the pin map itself: the header is
    Conn_02x03_Odd_Even, column A (pins 1,3,5) carries the signal leg and column B (pins
    2,4,6) the shield leg -- see gen_breakout_analog_frontend.py's own _atten_leg_pair()
    docstring for the full rationale (fix round 2's original row-based
    Conn_02x03_Top_Bottom assignment assumed a pin adjacency the real footprint does not
    have -- see verify_footprint_pad_adjacency() below). Written generically (raw/mid/div,
    not "clamp/shield") so it applies identically to either leg, whatever its own pin
    numbers are.
    """
    j_raw = {n.ref for n in nets.get(raw_net, []) if n.ref.startswith("J")}
    j_mid = {n.ref for n in nets.get(mid_net, []) if n.ref.startswith("J")}
    j_div = {n.ref for n in nets.get(div_net, []) if n.ref.startswith("J")}
    common = j_raw & j_mid & j_div
    check(
        len(common) == 1,
        f"{label}: expected exactly 1 shunt-jumper header spanning {raw_net!r}/{div_net!r}/"
        f"{mid_net!r}, found {common} (raw={j_raw}, div={j_div}, mid={j_mid})",
    )
    jref = next(iter(common))
    check(any(n.ref == jref and n.pin == raw_pin for n in nets[raw_net]), f"{label}: header {jref}'s pin {raw_pin} is not on {raw_net!r}")
    check(any(n.ref == jref and n.pin == div_pin for n in nets[div_net]), f"{label}: header {jref}'s pin {div_pin} is not on {div_net!r}")
    check(any(n.ref == jref and n.pin == mid_pin for n in nets[mid_net]), f"{label}: header {jref}'s pin {mid_pin} is not on {mid_net!r}")
    return jref


def _check_misc_leg_symmetry(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """FIX ROUND 1's own checker gap, closed: the previous version of this checker
    verified topology and connectivity but never leg-to-leg GAIN symmetry, so it approved
    the original defect (MISC1-3's /2 tap dividing the signal leg only) vacuously --
    every other check here passed cleanly on that broken netlist, because none of them
    compared the two legs' own attenuation against each other. This is the assertion that
    closes that gap: for every one of the 3 channels with a jumper-selectable divider
    (MISC1-3 -- the only channels on this sheet where either leg is anything but a direct
    wire), confirm BOTH legs divide by the SAME ratio, checked four ways so a future
    regression cannot slip through any one of them alone:

    (1) The INA105's own "+"/"-" pins are fed by EACH leg's own divider COMMON pin (not
        the raw clamp/shield node directly, and not the other leg's own common pin by a
        copy-paste mistake).
    (2) Both legs' divider resistors (4 total: raw->mid and mid->AGND, per leg) carry the
        IDENTICAL matched value (DIVIDER_R_VALUE) -- a value drift on just one leg breaks
        the match even if the topology still looks right.
    (3) Each leg's own header genuinely implements the "raw/common/tap" jumper-selector
        topology (via _check_header_pin_roles()), so both legs really do move together in
        EITHER jumper position, not just in whichever position happened to be exercised by
        some other check.
    (4) FIX ROUND 2 (task-10b-report.md): both legs' own headers are the SAME PHYSICAL
        COMPONENT -- one Conn_02x03 (Odd_Even variant, pin map corrected at fix round 3;
        column A = signal leg pins 1,3,5, column B = shield leg pins 2,4,6) populated with
        a single 2-gang shorting block, not two independent Conn_01x03 headers that could
        disagree. This is the fact that makes "both jumpers MUST be set to the same
        position" a physical impossibility to violate rather than merely a documented
        instruction -- checked here by confirming BOTH legs' own
        `_check_header_pin_roles()` calls return the identical reference.
    """
    for name in MISC_CHANNELS:
        clamp_net, mid_net, div_net = f"{name}_CLAMP", f"{name}_MID", f"{name}_DIV"
        shld_net, shld_mid_net, shld_div_net = f"{name}_SHLD", f"{name}_SHLD_MID", f"{name}_SHLD_DIV"
        for n in (clamp_net, mid_net, div_net, shld_net, shld_mid_net, shld_div_net):
            check(n in nets, f"{name}: missing net {n!r} (expected a leg-symmetric /1//2 divider structure, fix round 1)")

        ina_refs = {n.ref for n in nets[name] if values.get(n.ref) == "INA105KU"}
        check(len(ina_refs) == 1, f"{name}: expected exactly 1 INA105KU driving this net, found {ina_refs}")
        ina_ref = next(iter(ina_refs))

        plus_here = [n for n in nets[div_net] if n.ref == ina_ref and n.pin == INA105["plus"]]
        check(
            len(plus_here) == 1,
            f"{name}: {ina_ref}'s own '+' pin is not fed by the SIGNAL leg's own divider "
            f"common ({div_net!r})",
        )
        minus_here = [n for n in nets[shld_div_net] if n.ref == ina_ref and n.pin == INA105["minus"]]
        check(
            len(minus_here) == 1,
            f"{name}: {ina_ref}'s own '-' pin is not fed by the SHIELD leg's own divider "
            f"common ({shld_div_net!r}) -- if it is wired to {shld_net!r} directly instead, "
            f"this is the ORIGINAL leg-asymmetry defect (fix round 1): the shield leg "
            f"bypasses its own attenuation while the signal leg still divides, so a shield "
            f"disturbance is no longer fully cancelled in /2 mode",
        )

        leg_resistors = {
            "signal-hi (clamp->mid)": _find_bridging_resistor(nets, clamp_net, mid_net),
            "signal-lo (mid->AGND)": _find_bridging_resistor(nets, mid_net, "AGND"),
            "shield-hi (shield->mid)": _find_bridging_resistor(nets, shld_net, shld_mid_net),
            "shield-lo (mid->AGND)": _find_bridging_resistor(nets, shld_mid_net, "AGND"),
        }
        for leg, rref in leg_resistors.items():
            check(
                values.get(rref) == DIVIDER_R_VALUE,
                f"{name}: {leg} divider resistor {rref} should be {DIVIDER_R_VALUE!r} "
                f"(matched across both legs so a shield disturbance still cancels in /2 "
                f"mode), found {values.get(rref)!r}",
            )

        # Fix round 3's own /1//2 header pin map (corrected from fix round 2's row-based
        # Conn_02x03_Top_Bottom assignment, which assumed a pin adjacency the real
        # PinHeader_2x03_P2.54mm_Vertical footprint does not have -- see
        # verify_footprint_pad_adjacency() below and gen_breakout_analog_frontend.py's own
        # _atten_leg_pair() docstring): Conn_02x03_Odd_Even, column A (pins 1,3,5) = signal
        # leg (raw/common/tap), column B (pins 2,4,6) = shield leg.
        sig_jref = _check_header_pin_roles(nets, clamp_net, mid_net, div_net, "1", "3", "5", f"{name} signal leg")
        shld_jref = _check_header_pin_roles(nets, shld_net, shld_mid_net, shld_div_net, "2", "4", "6", f"{name} shield leg")
        check(
            sig_jref == shld_jref,
            f"{name}: signal-leg jumper header ({sig_jref}) and shield-leg jumper header "
            f"({shld_jref}) are NOT the same component. Fix round 2's entire point is that "
            f"ONE physical Conn_02x03 header carries both legs, populated with a single "
            f"2-gang shorting block, so the two legs' own jumper positions CANNOT disagree "
            f"-- two different refs here means fix round 1's own original defect (two "
            f"independently-settable jumpers) is back.",
        )

    return (
        f"All {len(MISC_CHANNELS)} /1//2-switchable channels (MISC1-3) confirmed leg-"
        f"symmetric: both signal and shield legs feed the INA105 through their own "
        f"matched ({DIVIDER_R_VALUE}) divider, selected by ONE mechanically-ganged "
        f"Conn_02x03_Odd_Even jumper header per channel (signal leg on pins 1,3,5, shield "
        f"leg on pins 2,4,6, confirmed to be the SAME physical component) -- so /2 mode "
        f"divides both legs equally and a shield disturbance still cancels at the output, "
        f"in either jumper position, and the two legs' own jumper positions cannot disagree."
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
        if name in ACCESIO_CHANNELS:
            expected_shield = "ACCESIO_AGND"
        elif name in MISC_CHANNELS:
            # Fix round 1: MISC's own "-" leg is ALSO divided now (matched to the signal
            # leg's own divider, see _check_misc_leg_symmetry()), so it no longer lands on
            # the raw shield node directly -- it lands on that leg's own divider COMMON
            # pin instead. Landing on the raw node here would mean the shield leg is
            # bypassing its own divider while the signal leg still divides -- the original
            # defect this fix exists to close.
            expected_shield = f"{name}_SHLD_DIV"
        else:
            expected_shield = f"{name}_SHLD"
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
    summary.append(_check_misc_leg_symmetry(nets, values))
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
    # NOT "next(... if n.ref.startswith('R'))" -- since fix round 1, A_MISC1_SHLD carries
    # a SECOND R-ref (the shield leg's own divider-hi resistor, added by that fix), so a
    # bare "first R-ref found" is ambiguous/order-dependent and could silently drift the
    # WRONG resistor. _find_bridging_resistor() targets the one that bridges specifically
    # to AGND (the shell bond) -- the same technique _check_bnc_shell_bonds() itself uses.
    r_ref = _find_bridging_resistor(good_nets, "A_MISC1_SHLD", "AGND")
    drifted[r_ref] = "100"
    msg = _assert_fails(good_nets, drifted, "should be '10'", f"{r_ref} (A_MISC1 shell-bond resistor) value drift 10R->100R")
    results.append(f"Shell-bond resistor value drift (10R -> 100R): caught -- {msg}")

    # (4b) THE finding this checker exists to catch after fix round 1, reintroduced
    # synthetically: an INA105 MISC channel's own '-' pin moved from its shield leg's OWN
    # divider common back onto the raw, undivided shield net directly -- exactly the
    # original defect (signal leg divided, shield leg not), still a fully-connected
    # 0-error netlist, and still a netlist _check_no_direct_agnd_reference() alone would
    # NOT have caught before fix round 1 added leg-symmetry checking (that check only ever
    # confirmed the '-' pin was on SOME shield-derived node, never that it was attenuated
    # by the SAME ratio as the '+' leg).
    leg_asym = copy.deepcopy(good_nets)
    idx = next(
        i for i, n in enumerate(leg_asym["A_MISC1_SHLD_DIV"])
        if n.pin == INA105["minus"] and good_values.get(n.ref) == "INA105KU"
    )
    victim = leg_asym["A_MISC1_SHLD_DIV"].pop(idx)
    leg_asym["A_MISC1_SHLD"] = leg_asym["A_MISC1_SHLD"] + [victim]
    msg = _assert_fails(
        leg_asym, good_values, "SHIELD leg's own divider common",
        "A_MISC1's INA105 '-' pin moved from the shield leg's own divider common back onto "
        "the raw, undivided shield net (the original leg-asymmetry defect, fix round 1)",
    )
    results.append(f"MISC leg-gain asymmetry (shield leg's own attenuator bypassed, signal leg still divided): caught -- {msg}")

    # (4c) A subtler version of the same defect class: both legs still go through A
    # divider (topology intact), but the SHIELD leg's own divider resistor value has
    # drifted away from the SIGNAL leg's -- so /2 mode no longer divides both legs by the
    # same ratio, and a shield disturbance leaks through partially instead of fully (a
    # quieter, harder-to-notice version of the original bug, not caught by the full-
    # bypass control above since the topology here is superficially fine).
    leg_drift = dict(good_values)
    r_shld_hi = _find_bridging_resistor(good_nets, "A_MISC2_SHLD", "A_MISC2_SHLD_MID")
    leg_drift[r_shld_hi] = "20.0k 0.1%"
    msg = _assert_fails(
        good_nets, leg_drift, f"should be {DIVIDER_R_VALUE!r}",
        f"{r_shld_hi} (A_MISC2 shield-leg divider-hi resistor) value drift "
        f"{DIVIDER_R_VALUE}->20.0k 0.1% (breaks leg-to-leg matching without breaking topology)",
    )
    results.append(f"MISC shield-leg divider resistor value drift (matched topology, mismatched ratio): caught -- {msg}")

    # (4d) FIX ROUND 2's own central defect, reintroduced synthetically: A_MISC3's own
    # shield-leg header pins (4/5/6) relabelled onto a FAKE second reference ("J9001"),
    # simulating a future edit that reverts to two independent headers (fix round 1's own
    # original risk) instead of the one mechanically-ganged Conn_02x03 -- still a fully-
    # connected, topologically-correct, 0-error netlist (every OTHER check above passes
    # cleanly on it), and still nothing _check_header_pin_roles() alone would catch (each
    # leg's own header, in isolation, still correctly implements raw/common/tap) -- only
    # the cross-leg "same ref" assertion added this round catches it.
    degang = copy.deepcopy(good_nets)
    real_jref = next(n.ref for n in good_nets["A_MISC3_SHLD_DIV"] if n.ref.startswith("J"))
    for net in ("A_MISC3_SHLD", "A_MISC3_SHLD_DIV", "A_MISC3_SHLD_MID"):
        degang[net] = [
            Node("J9001", n.pin, n.pinfunction, n.pintype) if (n.ref == real_jref) else n
            for n in degang[net]
        ]
    msg = _assert_fails(
        degang, good_values, "NOT the same component",
        "A_MISC3's shield-leg header pins relabelled onto a fake second ref (simulating a "
        "reverted-to-two-independent-headers defect, fix round 1's own original risk)",
    )
    results.append(f"Shield-leg jumper header split back onto a second (fake) physical part, un-ganging the two legs: caught -- {msg}")

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
# Footprint pad-adjacency check (FIX ROUND 3, task-10b-report.md) -- closes the process gap
# the pin-numbering defect above exposed, not just this one instance: nothing in this
# toolchain previously checked a symbol's assumed pin ADJACENCY against its paired
# footprint's real physical pad geometry. Same second-track-check pattern as
# verify_instance_paths()/self_test_instance_paths() below -- this doesn't fit the
# nets/values shape verify()/self_test() above take (it reads a .kicad_mod directly, not
# the exported netlist), so it gets its own top-level verify/self_test pair, wired into
# main() alongside that other pair.
# ---------------------------------------------------------------------------

JUMPER_FOOTPRINT_LIB = "Connector_PinHeader_2.54mm"
JUMPER_FOOTPRINT_MOD = "PinHeader_2x03_P2.54mm_Vertical"  # must match
# gen_breakout_analog_frontend.py's own FOOTPRINT_HDR2X03 -- re-stated here rather than
# imported from the generator, same independence discipline every fact in this file already
# follows (module docstring: "re-derives... independently of gen_breakout_analog_frontend.
# py's own choices").

# The pin pairs _atten_leg_pair() relies on a SINGLE shorting-block position bridging
# together -- signal leg's own raw-div and div-mid steps (column A, pins 1,3,5), shield
# leg's own (column B, pins 2,4,6). Restated here, independent of the generator's own
# choices, same as every other fact this file checks.
REQUIRED_ADJACENT_PIN_PAIRS = [
    ("1", "3", "signal leg raw(1)-div(3)"),
    ("3", "5", "signal leg div(3)-mid(5)"),
    ("2", "4", "shield leg raw(2)-div(4)"),
    ("4", "6", "shield leg div(4)-mid(6)"),
]
# The DISCARDED fix-round-2 assumption (Conn_02x03_Top_Bottom's own row pairing: pin N
# adjacent to pin N+3) -- must NOT hold on the real footprint, or this check would not
# actually have caught fix round 2's own defect.
DISCARDED_ADJACENCY_ASSUMPTION = [("1", "4"), ("2", "5"), ("3", "6")]


def _pad_center(pads: dict[str, list[tuple[float, float, float]]], pin: str) -> tuple[float, float]:
    entries = pads.get(pin)
    check(entries is not None and len(entries) == 1, f"footprint pad {pin!r} missing or ambiguous: {entries}")
    x, y, _angle = entries[0]
    return (x, y)


def verify_footprint_pad_adjacency(
    pads: dict[str, list[tuple[float, float, float]]] | None = None,
) -> str:
    """THE close-the-blind-spot check. gen_breakout_analog_frontend.py's own
    _atten_leg_pair() assigns symbol pin NUMBERS to nets on the assumption that certain
    pairs of them are physically adjacent on the real board, so ONE mechanical 2-gang
    shorting block can bridge each pair simultaneously. kicad_sch.py's own
    extract_symbol()/unit_pins() machinery (used by every generator in this project) reads
    ONLY the .kicad_sym symbol library -- never the .kicad_mod footprint a symbol happens
    to be paired with via `footprint=` -- so nothing in this toolchain otherwise confirms a
    symbol's assumed pin adjacency matches the REAL footprint's physical pad layout. This
    is invisible to ERC and to every OTHER check in this file by construction: they all
    reason about electrical CONNECTIVITY (which pins share a net), and physical pad
    adjacency is not a connectivity fact -- two pads sitting right next to each other on
    the real part deliberately carry NO shared net until a shunt is added; that is the
    entire point of a jumper header.

    This is exactly the shape of defect fix round 2 (task-10b-report.md) shipped with: it
    picked Conn_02x03_Top_Bottom and assigned row 1 (pins 1-3) = signal leg, row 2 (pins
    4-6) = shield leg, reasoning -- confirmed, per that version's own docstring, "directly
    against the raw Connector_Generic library text via kicad_sch.py's own
    extract_symbol()/unit_pins()" -- that pin N and pin N+3 share a physical column. That
    confirmation checked the SCHEMATIC SYMBOL's own drawn geometry, not the footprint
    actually assigned to the part. A reviewer rendered the real
    PinHeader_2x03_P2.54mm_Vertical.kicad_mod (`kicad-cli fp export svg`) and found its
    true pad layout is COLUMN-paired: pads (1,2)/(3,4)/(5,6) sit at the header's own
    2.54mm minimum pitch, while (1,4)/(2,5)/(3,6) -- the pairing Top_Bottom's row
    assignment needed -- sit 3.59mm apart on the diagonal, not adjacent at all. Fix round
    2's own netlist was fully connected and ERC-clean (0 errors) with this defect present:
    every check in this file above passed cleanly on it, because none of them reads
    footprint geometry.

    FIX ROUND 3 (task-10b-report.md, this round) corrected the pin assignment (see
    gen_breakout_analog_frontend.py's own _atten_leg_pair() docstring) to
    Conn_02x03_Odd_Even, interleaved: column A (pins 1,3,5) = signal leg's own
    raw/div/mid, column B (pins 2,4,6) = shield leg's. This function is the fix for the
    PROCESS gap, not just this one instance: it reads the REAL footprint's pad geometry
    directly, via `kicad_pcb.footprint_pads()` -- already-existing, general machinery this
    project's own PCB-layout generator (gen_mule_pcb.py) uses for placement, imported here
    rather than reimplemented, and usable against ANY footprint in either .pretty root by
    libname/modname, not hardcoded to this one part -- and asserts, in Euclidean distance
    terms, that every pin pair _atten_leg_pair() relies on being physically bridgeable
    really does sit at the header's own minimum pad pitch, AND that the discarded
    Top_Bottom-style pairing does NOT (proving this check would actually have caught fix
    round 2's own defect, not merely that it passes on whatever assignment happens to be
    current).

    NOT generalized to audit every connector this project places -- the general form of
    the gap this closes. Most connectors here (the panel BNCs, the ACCESIO DB37, the
    digital IDC headers) carry no electrical requirement that any two of their own pins be
    physically adjacent; only a jumper/shorting-block application like this one does, so a
    blanket "diff every placed connector's footprint against its symbol" pass would have
    nothing meaningful to assert for them -- there is no generic notion of "the symbol's
    pin-adjacency assumption" to compare against for a part with no adjacency assumption
    at all. Deliberately restricted to the one part in this project where physical pad
    adjacency IS an electrical-correctness precondition. A later task adding another
    shorting-block-style jumper anywhere in this project should add its own call here (or
    promote this into a shared kicad_sch.py/kicad_pcb.py helper if a second instance ever
    makes the duplication worth generalizing) -- tracked explicitly, not silently assumed
    closed by this one check existing.

    `pads` is injectable (defaults to the real footprint_pads() read) purely so
    self_test_footprint_pad_adjacency() below can exercise a corrupted layout without
    touching the real .kicad_mod on disk.
    """
    if pads is None:
        pads = footprint_pads(JUMPER_FOOTPRINT_LIB, JUMPER_FOOTPRINT_MOD)
    check(
        set(pads) == {"1", "2", "3", "4", "5", "6"},
        f"{JUMPER_FOOTPRINT_LIB}:{JUMPER_FOOTPRINT_MOD}: expected exactly pads 1-6, found {sorted(pads)}",
    )

    def dist(pin_a: str, pin_b: str) -> float:
        (xa, ya), (xb, yb) = _pad_center(pads, pin_a), _pad_center(pads, pin_b)
        return math.hypot(xa - xb, ya - yb)

    pitch = dist("1", "2")  # the header's own minimum pad spacing, re-derived from the
    # real footprint rather than hardcoded as a literal 2.54 -- stays meaningful even if a
    # future footprint swap changes the pitch, as long as it's internally consistent.
    check(0 < pitch < 10, f"{JUMPER_FOOTPRINT_MOD}: pin 1-2 spacing ({pitch}mm) is not a sane header pitch")

    for pin_a, pin_b, label in REQUIRED_ADJACENT_PIN_PAIRS:
        d = dist(pin_a, pin_b)
        check(
            abs(d - pitch) < 1e-6,
            f"{JUMPER_FOOTPRINT_MOD}: {label} (pins {pin_a}/{pin_b}) are {d:.4f}mm apart "
            f"on the REAL footprint, not the header's own {pitch:.4f}mm minimum pitch -- "
            f"_atten_leg_pair()'s own pin assignment assumes a single mechanical shorting "
            f"block can bridge these two pins directly; if this fires, that assumption no "
            f"longer holds for whatever footprint is actually assigned",
        )

    for pin_a, pin_b in DISCARDED_ADJACENCY_ASSUMPTION:
        d = dist(pin_a, pin_b)
        check(
            abs(d - pitch) > 1e-6,
            f"{JUMPER_FOOTPRINT_MOD}: pins {pin_a}/{pin_b} unexpectedly sit at the "
            f"header's own minimum pitch ({pitch:.4f}mm) -- this footprint's real "
            f"geometry has changed such that the discarded fix-round-2 Top_Bottom-style "
            f"adjacency assumption (pin N adjacent to pin N+3) would ALSO hold; "
            f"re-examine whether that changes which symbol/pin-assignment combination is "
            f"actually correct",
        )

    return (
        f"MISC1-3's own /1//2 shunt header footprint "
        f"({JUMPER_FOOTPRINT_LIB}:{JUMPER_FOOTPRINT_MOD}) pad geometry read directly from "
        f"its real .kicad_mod (not the schematic symbol) and confirmed: signal leg (pins "
        f"1-3-5) and shield leg (pins 2-4-6) each form a straight-line {pitch:.2f}mm-pitch "
        f"ladder a single shorting block can walk, and the discarded Top_Bottom-style "
        f"adjacency (pin N / pin N+3) does NOT hold on the real part."
    )


def self_test_footprint_pad_adjacency(
    good_pads: dict[str, list[tuple[float, float, float]]],
) -> list[str]:
    """Negative control: confirm verify_footprint_pad_adjacency() actually fires on a
    corrupted footprint layout, rather than passing vacuously -- same self-test discipline
    every other check in this project already establishes.
    """
    corrupted = copy.deepcopy(good_pads)
    # Move pin 3 (signal leg's own DIV/common pin) off its real adjacent position onto
    # pin 6's -- simulating a footprint swap (or a future pin-assignment edit) that breaks
    # the "signal leg raw-div-mid forms one straight adjacent ladder" assumption
    # _atten_leg_pair() relies on. Still a syntactically well-formed pads dict.
    corrupted["3"] = corrupted["6"]
    try:
        verify_footprint_pad_adjacency(corrupted)
    except CheckFailure as e:
        check(
            "signal leg raw(1)-div(3)" in str(e),
            f"self-test 'pin 3 moved off its real adjacent position': "
            f"verify_footprint_pad_adjacency() failed, but not with the expected "
            f"complaint, got: {e}",
        )
        return [f"Jumper header pin moved off its real physically-adjacent footprint position: caught -- {e}"]
    raise CheckFailure(
        "self-test 'pin 3 moved off its real adjacent position': "
        "verify_footprint_pad_adjacency() did NOT raise on a corrupted pad layout -- the "
        "check this self-test exists to validate is passing vacuously"
    )


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
        len(paths) >= 120,
        f"only found {len(paths)} (instances (path ...)) entries in analog-frontend.kicad_sch "
        f"-- expected >=120 (recomputed directly against this task's own real output: 134 "
        f"placed instances as originally committed at 4ecae2f, 143 after fix round 1 added 9 "
        f"more (2 divider resistors + 1 header per MISC channel's new shield leg, x3), 140 "
        f"after fix round 2 removed 3 (two Conn_01x03 headers merged into one Conn_02x03 per "
        f"MISC channel, x3) -- not guessed any of the three times; the >=120 floor itself is "
        f"intentionally left below all three real counts so a future edit that adds or "
        f"removes a handful of components doesn't need this floor bumped in lockstep)",
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

    try:
        pad_summary = verify_footprint_pad_adjacency()
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {pad_summary}")

    good_pads = footprint_pads(JUMPER_FOOTPRINT_LIB, JUMPER_FOOTPRINT_MOD)
    try:
        pad_self_test_results = self_test_footprint_pad_adjacency(good_pads)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in pad_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
