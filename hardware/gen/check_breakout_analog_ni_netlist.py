"""Parse hardware/breakout's exported netlist (and, for two checks the exported netlist
provably cannot answer -- see below, the raw .kicad_sch sources) and verify the analog-NI
fan-out sheet's contract nets and topology are electrically correct -- not just that ERC was
silent. Same reasoning check_mule_netlist.py's, check_breakout_power_netlist.py's,
check_taskpc_digital_netlist.py's, check_breakout_pi_interface_netlist.py's, and
check_breakout_analog_frontend_netlist.py's own docstrings all give, restated because it is
the reason THIS file exists too: gen_breakout_analog_ni.py's own lib_symbols entries are keyed
by full lib id, which resolves correctly -- but "resolves correctly" is proven empirically per
run, not guaranteed by construction for every future edit. Keyed bare, or with a channel
accidentally wired to the wrong pin, KiCad loads the file without error and silently produces
a plausible, wrong netlist. So ERC passing is necessary but not sufficient evidence.

THE central risks this file exists to catch, named explicitly by this task's own brief:

  1. All 16 A_* channels (Task 10a) must reach the recording NI's own Connector 0 THROUGH a
     genuine OPA4192-value unity-gain buffer + 100R series -- not a bare wire, and not
     silently missing a channel -- landing on the physical MDR68 pin NI's own X Series User
     Manual assigns that AI channel number, not a permuted or sequential guess.
  2. AISENSE (recording NI Connector 0, physical pin 62) is tied DIRECTLY to AGND -- "this
     single wire is what makes NRSE work... the easiest thing on the board to omit by
     accident" (this task's own brief). Without it NI measures every channel against its own
     ground instead of ours, and analog-frontend.kicad_sch's entire differential-receive
     design silently does nothing on the NI side -- a defect invisible to ERC (still a fully
     connected, 0-error netlist) and invisible to a schematic read that only checks "is
     AISENSE labelled something".
  3. The 9 task-PC channels drive the CORRECT PRE-EXISTING labels Task 8 already placed on
     taskpc-digital.kicad_sch (`A_EYE_LX_TPC` etc.) -- a misspelled/mistyped label here would
     leave Task 8's own connector pin isolated (this sheet's own resistor driving a stray,
     disconnected net instead) while still passing a shallow "does this net exist" check.
  4. Coordinate collisions -- this project has hit a REAL rail short from exactly this defect
     class before (analog-frontend.kicad_sch's own task-10a-report.md: "a real rail short
     from consecutive rows' decoupling pins landing on identical coordinates"). Checked by
     parsing this sheet's own raw rendered text for every global-label (x, y), independently
     of the exported netlist (which only reports CONNECTIVITY, not the geometry that could
     have accidentally created it).

`verify()` below re-derives, independently of gen_breakout_analog_ni.py's own choices, the
full AI0-AI15+AISENSE physical-pin table (from the same NI manual, cross-checked against BOTH
Figure A-5 and Figure A-18 -- see the generator's own module docstring) and the channel/net
contract -- same "a checker that trusted the generator would only be checking the generator
against itself" discipline every prior checker in this project already follows.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_analog_ni_netlist.py [path/to/breakout.net]

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
from kicad_sch import (  # noqa: E402
    find_all_instance_paths,
    find_root_uuid,
    find_sheet_instance_path,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_NI_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "analog-ni.kicad_sch"
ANALOG_NI_SHEETFILE = "sheets/analog-ni.kicad_sch"  # exactly as breakout.kicad_sch's own
# Sheetfile property spells it -- must match gen_breakout_analog_ni.py's own constant.

# ---------------------------------------------------------------------------
# Channel/contract constants -- redefined here from spec Sec.3.2 / this task's own brief,
# NEVER imported from the generator (same independence discipline every checker in this
# project already follows for its own contract nets/physical-pin tables).
# ---------------------------------------------------------------------------
ALL_16_NETS = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_NETS) == 16
NI_CHANNELS = list(zip(ALL_16_NETS, range(16)))

TPC_CHANNEL_NAMES = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY",
    "A_JOY_X", "A_JOY_Y", "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(TPC_CHANNEL_NAMES) == 9
NOT_TO_TPC = [n for n in ALL_16_NETS if n not in TPC_CHANNEL_NAMES]
assert sorted(NOT_TO_TPC) == sorted(
    ["A_EYE_LP", "A_EYE_RP", "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_MIC"]
), "the task-PC bank must NOT gain pupil/photodiode/ambient/accelerometer/mic channels"

# Recording NI Connector 0's own AI0-AI15 physical pins -- NI's X Series User Manual
# (370784K-01), Figure A-5 (same figure Task 8 already used), cross-checked directly against
# Figure A-18 (the recording card's own PCIe-6353/PCIe-PXIe-6363 family): both give the
# IDENTICAL table on these 17 pins. NOT sequential.
MDR0_AI_PIN = {
    0: "68", 1: "33", 2: "65", 3: "30", 4: "28", 5: "60", 6: "25", 7: "57", 8: "34",
    9: "66", 10: "31", 11: "63", 12: "61", 13: "26", 14: "58", 15: "23",
}
assert set(MDR0_AI_PIN) == set(range(16))
MDR0_AISENSE_PIN = "62"

BUFFER_VALUE = "OPA4192IDR"
SERIES_R_VALUE = "100"

# Standard quad-op-amp pinout (Amplifier_Operational:OPA4197xD, the real symbol placed with
# Value overridden -- see generator's own module docstring) -- redefined here independently.
QUAD_UNIT = {
    1: {"plus": "3", "minus": "2", "out": "1"},
    2: {"plus": "5", "minus": "6", "out": "7"},
    3: {"plus": "10", "minus": "9", "out": "8"},
    4: {"plus": "12", "minus": "13", "out": "14"},
}


def _unit_for_pin(pin: str) -> int:
    for unit, roles in QUAD_UNIT.items():
        if pin in (roles["minus"], roles["out"]):
            return unit
    raise CheckFailure(f"pin {pin!r} is not a '-'/OUT pin of any known OPA4192 quad unit")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _find_bridging_resistor(nets: dict[str, list[Node]], net_a: str, net_b: str) -> str:
    """The single R-prefixed reference with one pin on net_a and the other on net_b -- same
    technique every checker in this project uses (duplicated, not imported, per this
    project's own "never import from a sibling checker" discipline)."""
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


def _check_one_buffered_channel(
    nets: dict[str, list[Node]], values: dict[str, str], source_net: str, final_net: str, label: str,
) -> tuple[str, str]:
    """Confirms `source_net` reaches `final_net` through a genuine OPA4192-value unity-gain
    buffer ('+' fed by source_net, '-' shorted to OUT on the internal `{final_net}_BUF` node)
    followed by a 100R series resistor -- the shared shape both the NI bank and the task-PC
    bank use, checked once here rather than duplicated per caller. Returns
    (buffer_ref, connector_ref_or_None) -- the connector ref is whatever single J-prefixed
    reference sits on final_net (callers with more specific expectations check its identity
    further themselves).
    """
    buf_net = f"{final_net}_BUF"
    check(source_net in nets, f"{label}: missing source net {source_net!r}")
    check(buf_net in nets, f"{label}: missing internal buffer net {buf_net!r}")
    check(final_net in nets, f"{label}: missing final net {final_net!r}")

    buf_refs = {n.ref for n in nets[buf_net] if values.get(n.ref) == BUFFER_VALUE}
    check(
        len(buf_refs) == 1,
        f"{label}: expected exactly 1 {BUFFER_VALUE} buffer driving {buf_net!r}, found {buf_refs}",
    )
    bref = next(iter(buf_refs))

    check(
        any(n.ref == bref for n in nets[source_net]),
        f"{label}: buffer {bref}'s own non-inverting input is not fed by the source net "
        f"{source_net!r} -- this channel is not genuinely buffered from its own source",
    )
    minus_out_nodes = [n for n in nets[buf_net] if n.ref == bref]
    check(
        len(minus_out_nodes) == 2,
        f"{label}: expected exactly 2 of {bref}'s own pins ('-' and OUT) on {buf_net!r} -- "
        f"a genuine unity-gain follower shorts them together; found {minus_out_nodes}",
    )

    r_ref = _find_bridging_resistor(nets, buf_net, final_net)
    check(
        values.get(r_ref) == SERIES_R_VALUE,
        f"{label}: series resistor {r_ref} should be {SERIES_R_VALUE!r} (100 ohm, this "
        f"task's own brief), found {values.get(r_ref)!r}",
    )

    conn_refs = {n.ref for n in nets[final_net] if n.ref.startswith("J")}
    check(
        len(conn_refs) <= 1,
        f"{label}: {final_net!r} reaches more than one connector reference: {conn_refs}",
    )
    return bref, (next(iter(conn_refs)) if conn_refs else None)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_16_ni_channels(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, str]:
    """All 16 A_* channels reach the recording NI's own Connector 0 through a genuine
    OPA4192 buffer + 100R series, landing on the sourced physical AI pin -- not permuted,
    not sequential. Returns (connector_ref, summary)."""
    conn_ref = None
    for name, ai_chan in NI_CHANNELS:
        final_net = f"{name}_NI"
        _bref, cref = _check_one_buffered_channel(nets, values, name, final_net, f"{name} (NI)")
        check(cref is not None, f"{name}_NI: does not reach any connector pin at all")
        if conn_ref is None:
            conn_ref = cref
        check(
            cref == conn_ref,
            f"{name}_NI: lands on connector {cref}, but other NI channels land on {conn_ref} "
            f"-- all 16 must reach the SAME physical recording-NI Connector 0",
        )
        expected_pin = MDR0_AI_PIN[ai_chan]
        actual_pin = next(n.pin for n in nets[final_net] if n.ref == cref)
        check(
            actual_pin == expected_pin,
            f"{name}_NI: lands on Connector 0 pin {actual_pin}, expected pin {expected_pin} "
            f"(AI{ai_chan}'s own sourced position -- NI X Series User Manual 370784K-01, "
            f"Figure A-5/A-18) -- a permuted AI-channel assignment",
        )
    check(conn_ref is not None, "no recording-NI connector reference found at all")
    return conn_ref, (
        f"All 16 A_* channels reach the recording NI's own Connector 0 ({conn_ref}) through "
        f"a genuine {BUFFER_VALUE} unity-gain buffer + {SERIES_R_VALUE} ohm series resistor, "
        f"each landing on its own sourced AI-channel physical pin (Figure A-5/A-18)."
    )


def _check_aisense(nets: dict[str, list[Node]], conn_ref: str) -> str:
    check("AGND" in nets, "missing net: 'AGND' (AISENSE tie requires this rail present)")
    aisense_nodes = [n for n in nets["AGND"] if n.ref == conn_ref and n.pin == MDR0_AISENSE_PIN]
    check(
        len(aisense_nodes) == 1,
        f"recording-NI Connector 0 ({conn_ref}) pin {MDR0_AISENSE_PIN} (AISENSE) is not tied "
        f"to AGND -- found AGND nodes on {conn_ref}: "
        f"{[n.pin for n in nets['AGND'] if n.ref == conn_ref]}. This single wire is what "
        f"makes NRSE work (spec Decision 5); without it NI measures every channel against "
        f"its own ground instead of ours, and analog-frontend.kicad_sch's own differential "
        f"receive design silently does nothing on the NI side.",
    )
    return (
        f"AISENSE confirmed: recording-NI Connector 0 ({conn_ref}) pin {MDR0_AISENSE_PIN} is "
        f"tied directly to AGND (NRSE reference, spec Decision 5)."
    )


def _check_9_tpc_channels(nets: dict[str, list[Node]], values: dict[str, str], ni_conn_ref: str) -> str:
    """The 9 task-PC channels reach the CORRECT PRE-EXISTING labels Task 8 already placed
    on taskpc-digital.kicad_sch, through this sheet's own genuine buffer + 100R series --
    and land on Task 8's own single, shared connector reference (proving they are all
    driving the SAME real connector, not 9 different stray nets)."""
    tpc_conn_ref = None
    for name in TPC_CHANNEL_NAMES:
        final_net = f"{name}_TPC"
        _bref, cref = _check_one_buffered_channel(nets, values, name, final_net, f"{name} (TPC)")
        check(
            cref is not None,
            f"{final_net}: does not reach any connector pin -- Task 8's own pre-existing "
            f"label may have been misspelled/mistyped here, leaving its real connector pin "
            f"isolated and this sheet's own resistor driving a stray, disconnected net",
        )
        if tpc_conn_ref is None:
            tpc_conn_ref = cref
        check(
            cref == tpc_conn_ref,
            f"{final_net}: lands on connector {cref}, but other TPC channels land on "
            f"{tpc_conn_ref} -- all 9 must reach the SAME physical task-PC Connector 0",
        )
    check(
        tpc_conn_ref != ni_conn_ref,
        f"the task-PC channels' own connector ({tpc_conn_ref}) must not be the SAME "
        f"reference as the recording NI's own connector ({ni_conn_ref}) -- they are two "
        f"physically distinct MDR68 connectors on two different sheets",
    )
    return (
        f"All 9 task-PC channels ({TPC_CHANNEL_NAMES}) drive Task 8's own pre-existing "
        f"labels through a genuine {BUFFER_VALUE} buffer + {SERIES_R_VALUE} ohm series "
        f"resistor, all landing on the SAME real task-PC connector ({tpc_conn_ref}), "
        f"distinct from the recording-NI connector ({ni_conn_ref})."
    )


def _check_asymmetry_preserved(nets: dict[str, list[Node]]) -> str:
    """The 7 channels that must NOT reach the task PC (pupil x2, photodiode x2, ambient,
    accelerometer, mic -- spec's own deliberate asymmetry, this task's own explicit
    instruction not to 'fix') genuinely do not have a _TPC net driven by this sheet at all."""
    for name in NOT_TO_TPC:
        final_net = f"{name}_TPC"
        check(
            final_net not in nets or not any(n.ref.startswith("R") for n in nets[final_net]),
            f"{name}: found a _TPC net ({final_net!r}) with a resistor on it -- this channel "
            f"must NOT reach the task PC (spec's own deliberate asymmetry: pupil/photodiode/"
            f"ambient/accelerometer/mic reach NI and Intan only)",
        )
    return (
        f"Confirmed the deliberate asymmetry survives: none of {NOT_TO_TPC} drive a _TPC net."
    )


def _check_buffer_ic_count(values: dict[str, str]) -> str:
    ic_refs = {ref for ref, v in values.items() if v == BUFFER_VALUE}
    check(
        len(ic_refs) == 7,
        f"expected exactly 7 {BUFFER_VALUE} packages (4 for the 16-channel NI bank + 3 for "
        f"the 9-channel task-PC bank, 4 real channels per quad package), found "
        f"{len(ic_refs)}: {sorted(ic_refs)}",
    )
    return f"Exactly 7 {BUFFER_VALUE} quad-buffer packages placed (4 NI-bank + 3 task-PC-bank)."


def _check_spare_tieoff(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """The task-PC bank's 3rd (partial) quad package has 3 unused units (9 channels = 2 full
    packages + 1 with only 1 of 4 used) -- each must be safely tied off: '+' to AGND (never
    floating), '-'/OUT shorted together on a PRIVATE, uniquely-named net (never shared across
    spares, which would short two independent op-amp outputs together)."""
    spare_nets = sorted(n for n in nets if re.fullmatch(r"TPC_SPARE\d+_FB", n))
    check(
        len(spare_nets) == 3,
        f"expected exactly 3 TPC spare tie-off nets (9 channels = 2 full quad packages + 1 "
        f"partial, 3 unused units), found {len(spare_nets)}: {spare_nets}",
    )
    for net in spare_nets:
        nodes = nets[net]
        check(len(nodes) == 2, f"{net}: expected exactly 2 nodes ('-' and OUT of one spare unit), found {nodes}")
        refs_on_net = {n.ref for n in nodes}
        check(
            len(refs_on_net) == 1,
            f"{net}: both nodes must belong to the SAME op-amp instance (a genuine unity-gain "
            f"follower), found refs {refs_on_net} -- if these are two DIFFERENT refs, two "
            f"independent op-amp outputs have been shorted together",
        )
        ref = next(iter(refs_on_net))
        check(values.get(ref) == BUFFER_VALUE, f"{net}: {ref} expected Value {BUFFER_VALUE!r}, found {values.get(ref)!r}")
        units = {_unit_for_pin(n.pin) for n in nodes}
        check(len(units) == 1, f"{net}: nodes do not share one quad unit: {nodes}")
        plus_pin = QUAD_UNIT[next(iter(units))]["plus"]
        check(
            any(n.ref == ref and n.pin == plus_pin for n in nets.get("AGND", [])),
            f"{net}: {ref}'s own '+' input (pin {plus_pin}) is not tied to AGND -- a spare "
            f"buffer unit left with a floating, undefined input",
        )
    return "All 3 spare task-PC buffer units safely tied off ('+' to AGND, '-'/OUT shorted on their own private net)."


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    """Scans this sheet's OWN raw rendered text for every global_label's real (x, y) and
    asserts no two DIFFERENT net names ever share one -- a real short, indistinguishable
    from a correct design by the exported netlist alone (which only reports which nets
    exist, not the placement arithmetic that could have accidentally merged two of them).
    This project has hit exactly this defect before (analog-frontend.kicad_sch's own
    task-10a-report.md, decoupling pins from consecutive rows landing on identical
    coordinates) -- checked directly here rather than trusted from layout arithmetic alone.
    """
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 100, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really analog-ni.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in analog-ni.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short between those nets: "
        f"{list(collisions.items())[:5]}",
    )
    return (
        f"No coordinate collisions: all {len(coords)} distinct global-label positions in "
        f"analog-ni.kicad_sch carry exactly one net name each."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    ni_conn_ref, msg = _check_16_ni_channels(nets, values)
    summary.append(msg)
    summary.append(_check_aisense(nets, ni_conn_ref))
    summary.append(_check_9_tpc_channels(nets, values, ni_conn_ref))
    summary.append(_check_asymmetry_preserved(nets))
    summary.append(_check_buffer_ic_count(values))
    summary.append(_check_spare_tieoff(nets, values))

    for rail in ("+12V", "-12V", "AGND"):
        check(rail in nets, f"missing consumed rail: {rail!r}")
        check(len(nets[rail]) >= 10, f"{rail}: suspiciously small population ({len(nets[rail])} nodes)")
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
    each corruption below is a minimal, targeted mutation of that known-good structure."""
    results = []

    # (1) AI-channel permutation: A_PD1_NI's and A_PD2_NI's own recording-NI connector nodes
    # swapped -- still a fully-connected, 0-error netlist, exactly the class of defect ERC
    # cannot see (a transposed physical pin), matching Task 8's own precedent self-test.
    swapped = copy.deepcopy(good_nets)
    idx1 = next(i for i, n in enumerate(swapped["A_PD1_NI"]) if n.ref.startswith("J"))
    idx2 = next(i for i, n in enumerate(swapped["A_PD2_NI"]) if n.ref.startswith("J"))
    swapped["A_PD1_NI"][idx1], swapped["A_PD2_NI"][idx2] = swapped["A_PD2_NI"][idx2], swapped["A_PD1_NI"][idx1]
    msg = _assert_fails(swapped, good_values, "permuted AI-channel assignment", "A_PD1/A_PD2 recording-NI connector pins swapped")
    results.append(f"AI-channel permutation (A_PD1/A_PD2 connector pins swapped): caught -- {msg}")

    # (2) Channel silently un-buffered: the series resistor bridging A_MIC_NI_BUF->A_MIC_NI
    # deleted entirely (simulating an edit that wires the buffer output straight to the
    # connector pin, or drops the resistor by accident) -- topology no longer a genuine
    # buffer+series-R fan-out.
    no_r = copy.deepcopy(good_nets)
    r_ref = next(n.ref for n in no_r["A_MIC_NI_BUF"] if n.ref.startswith("R"))
    no_r["A_MIC_NI_BUF"] = [n for n in no_r["A_MIC_NI_BUF"] if n.ref != r_ref]
    no_r["A_MIC_NI"] = [n for n in no_r["A_MIC_NI"] if n.ref != r_ref]
    msg = _assert_fails(no_r, good_values, "expected exactly 1 resistor bridging", "A_MIC's own 100R series resistor deleted (buffer no longer reaches the connector through it)")
    results.append(f"Series resistor deleted, channel no longer genuinely buffered to the connector: caught -- {msg}")

    # (3) AISENSE dropped from AGND -- "the easiest thing on the board to omit by accident".
    ni_conn_ref = next(n.ref for n in good_nets["A_EYE_LX_NI"] if n.ref.startswith("J"))
    dropped_aisense = dict(good_nets)
    dropped_aisense["AGND"] = [
        n for n in good_nets["AGND"] if not (n.ref == ni_conn_ref and n.pin == MDR0_AISENSE_PIN)
    ]
    msg = _assert_fails(dropped_aisense, good_values, "AISENSE", "recording-NI Connector 0's own AISENSE tie to AGND dropped")
    results.append(f"AISENSE-to-AGND tie dropped: caught -- {msg}")

    # (4) Series resistor value drift (100 -> 1k) on a task-PC channel.
    drifted = dict(good_values)
    r_ref2 = _find_bridging_resistor(good_nets, "A_JOY_X_TPC_BUF", "A_JOY_X_TPC")
    drifted[r_ref2] = "1k"
    msg = _assert_fails(good_nets, drifted, f"should be {SERIES_R_VALUE!r}", f"{r_ref2} (A_JOY_X task-PC series resistor) value drift 100->1k")
    results.append(f"Series resistor value drift (100 ohm -> 1k): caught -- {msg}")

    # (5) Task-PC channel mislabeled: A_MISC2_TPC's own connector node (Task 8's pre-existing
    # label) removed while this sheet's own resistor node stays -- simulates a label-text
    # mismatch between the two sheets (e.g. a typo on either side) leaving this net driven by
    # nothing but our own resistor, never actually reaching Task 8's real connector pin.
    mislabeled = copy.deepcopy(good_nets)
    mislabeled["A_MISC2_TPC"] = [n for n in mislabeled["A_MISC2_TPC"] if not n.ref.startswith("J")]
    msg = _assert_fails(mislabeled, good_values, "does not reach any connector pin", "A_MISC2_TPC's own connector node removed (Task 8's own pre-existing label no longer reached)")
    results.append(f"Task-PC channel driving a mistyped/wrong label (Task 8's own connector pin no longer reached): caught -- {msg}")

    # (6) The forbidden asymmetry re-introduced: A_PD1 (must NOT reach the task PC) wired to
    # a _TPC net -- exactly the "fix" this task's own brief explicitly says not to make.
    leaked = copy.deepcopy(good_nets)
    leaked["A_PD1_TPC"] = [Node("R999", "2", None, "passive"), Node("J997", "1", None, "passive")]
    msg = _assert_fails(leaked, good_values, "must NOT reach the task PC", "A_PD1 (photodiode) given a _TPC net -- the deliberate asymmetry silently 'fixed'")
    results.append(f"Forbidden channel (photodiode) leaking to the task PC: caught -- {msg}")

    # (7) A spare task-PC buffer unit's own '+' input left floating (AGND tie removed).
    no_tieoff = copy.deepcopy(good_nets)
    spare_net = next(n for n in good_nets if re.fullmatch(r"TPC_SPARE\d+_FB", n))
    spare_ref = next(n.ref for n in good_nets[spare_net])
    spare_unit = _unit_for_pin(next(n.pin for n in good_nets[spare_net] if n.ref == spare_ref))
    plus_pin = QUAD_UNIT[spare_unit]["plus"]
    no_tieoff["AGND"] = [n for n in good_nets["AGND"] if not (n.ref == spare_ref and n.pin == plus_pin)]
    msg = _assert_fails(no_tieoff, good_values, "floating, undefined input", f"{spare_ref}'s own spare unit '+' input tie to AGND removed")
    results.append(f"Spare buffer unit left with a floating input (AGND tie removed): caught -- {msg}")

    # (8) Buffer package COUNT drifted from the expected 7 -- simulated by relabelling an
    # UNRELATED resistor's own Value to OPA4192IDR (a stray/miscounted package), rather than
    # corrupting one of the 7 real buffer refs directly: every one of those 7 is already
    # covered by a more specific per-channel check above (which would catch a same-ref
    # corruption first, with a different and equally valid message) -- this self-test
    # exists specifically to confirm the GLOBAL COUNT floor itself also fires, independent
    # of any one channel's own structural check.
    value_drift = dict(good_values)
    extra_ref = next(r for r, v in good_values.items() if r.startswith("R") and v != BUFFER_VALUE)
    value_drift[extra_ref] = BUFFER_VALUE
    msg = _assert_fails(good_nets, value_drift, "expected exactly 7", f"{extra_ref} (an unrelated resistor) Value relabelled to {BUFFER_VALUE}, simulating a miscounted package")
    results.append(f"Buffer package count drifted from 7 (stray relabelled component): caught -- {msg}")

    return results


def self_test_collision(good_sch_text: str) -> str:
    """Negative control for _check_no_coordinate_collisions(): splices a SECOND global
    label's own (x, y) onto a FIRST label's coordinate (by exact character offset from the
    original regex match, not a substring search that could accidentally hit an unrelated
    "(at X Y" elsewhere in the file, e.g. a component placement) and confirms the check
    fires on the resulting synthetic short.
    """
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


def verify_instance_paths(ni_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(ni_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"analog-ni.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with "
        f"breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, ANALOG_NI_SHEETFILE)

    paths = find_all_instance_paths(ni_sch_text)
    check(
        len(paths) >= 60,
        f"only found {len(paths)} (instances (path ...)) entries in analog-ni.kicad_sch -- "
        f"expected >=60 (recomputed directly against this task's own real output: 75 placed "
        f"instances -- 1 MDR68 connector + 7 quad-buffer packages x5 placements each (4 real "
        f"units + 1 power unit) + 25 series resistors + 14 decoupling capacitors -- the >=60 "
        f"floor is intentionally left below that real count so a future small edit doesn't "
        f"need this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in analog-ni.kicad_sch do not "
        f"resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}",
    )
    return (
        f"All {len(paths)} component instance paths in analog-ni.kicad_sch resolve to the "
        f"real ancestor chain {expected_prefix!r} (breakout's own root uuid + the "
        f"'analog-ni' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(ni_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(ni_text, breakout_text)
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


def self_test_instance_paths(good_ni_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_ni_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, ANALOG_NI_SHEETFILE)

    corrupted = good_ni_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_ni_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_ni_text may not actually be passing verify_instance_paths() cleanly",
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_NI_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_NI_SCH} do not exist -- run "
            f"gen_breakout.py and gen_breakout_analog_ni.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    ni_sch_text = DEFAULT_NI_SCH.read_text()

    try:
        collision_summary = _check_no_coordinate_collisions(ni_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {collision_summary}")

    try:
        collision_self_test_msg = self_test_collision(ni_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: coordinate collision reintroduced: caught -- {collision_self_test_msg}")

    try:
        path_summary = verify_instance_paths(ni_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(ni_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
