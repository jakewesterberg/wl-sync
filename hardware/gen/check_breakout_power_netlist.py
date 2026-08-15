"""Parse hardware/breakout's exported netlist (and, for two checks the exported netlist
provably cannot answer -- see below, the raw .kicad_sch sources) and verify the power
sheet's contract nets are electrically correct -- not just that ERC was silent.

Why this exists, not just ERC (same reasoning as check_mule_netlist.py's own docstring,
restated because it is the reason THIS file exists too, not inherited automatically): this
generator's own lib_symbols entries are keyed by full lib id, which resolves correctly --
but "resolves correctly" was proven once, empirically (task-7-report.md), not guaranteed by
construction for every future edit to gen_breakout_power.py or gen_wl_sync_lib.py. Keyed
bare, or with a mismatched/incomplete custom symbol, KiCad loads the file without error and
silently produces a plausible, wrong netlist. So ERC passing is necessary but not sufficient
evidence this sheet is correct -- this script reads the exported netlist and confirms the
nets it claims to have are the nets that are really there, and (this sheet's own specific,
highest-stakes property) that AGND/DGND are genuinely distinct nets joined at exactly one
point, and that the isolated rails share no PIN with the non-isolated ones.

Reuses check_mule_netlist.py's parser (parse_netlist, Node, CheckFailure, check) rather
than re-implementing it -- same netlist s-expression shape, same repo, same job.

TASK 7 FIX ROUND 1 (task-7-report.md, "Fix round 1") added two check families on top of
the original nine, one per finding:

  - verify() gained a "+5V originates at the inlet, not a regulator" check (Finding 1: the
    +5V rail was undersized regulating it down from +12V on board; the fix moved it to a
    direct inlet rail instead, and this is the netlist-topology proof that fix actually
    landed -- D3 bridges P5_RAW<->+5V with the same reverse-polarity orientation as D1,
    J1 has a pin on P5_RAW, and critically, NO power_out-typed pin sits on +5V anywhere
    -- ruling out ANY regulator driving it, not just the specific TPS7A4901 that used to).
  - A new, separate verify_instance_paths() (Finding 2: the child sheet's own component
    `(instances (path ...))` ancestor chain was self-referential -- this file's own root
    uuid, correct only for a hierarchy ROOT -- rather than the true breakout-root +
    power-sheet-symbol chain a real KiCad-authored nested sheet carries). This check
    CANNOT be folded into verify() or run against the exported netlist at all: confirmed
    empirically against the ORIGINAL (pre-fix) committed power.kicad_sch -- its every
    component's own `(instances (path "/cac9e729-..." ...))` carried that file's own
    self-referential root uuid (grepped directly: 47 raw occurrences in the .kicad_sch
    source), yet that exact uuid string had ZERO occurrences anywhere in the exported
    netlist kicad-cli produced from it, and the netlist's own per-component `sheetpath`/
    `tstamps` fields already matched the REAL breakout-root+sheet-symbol chain even
    then. I.e. kicad-cli sch export netlist recomputes those fields by walking the REAL
    `(sheet ...)` file hierarchy from the invoked root, never by reading a component's
    own `(instances (path ...))` bookkeeping at all -- the defect was already invisible
    in the netlist BEFORE this fix, not merely unchanged by it. So this check reads the raw
    .kicad_sch sources directly (kicad_sch.py's find_root_uuid()/find_sheet_instance_path()/
    find_all_instance_paths(), the same helpers gen_breakout_power.py's own build() now
    uses to compute the correct prefix in the first place) -- the only place this defect is
    actually visible on disk.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_power_netlist.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1
with a description of the first failure otherwise.
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
# The two raw .kicad_sch sources verify_instance_paths() reads -- see module docstring
# for why this check can't use the exported netlist at all. Not overridable from argv
# (unlike DEFAULT_NET_PATH): the task's own verification command runs this script with
# no arguments, so these two must resolve correctly on their own.
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_POWER_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "power.kicad_sch"
POWER_SHEETFILE = "sheets/power.kicad_sch"  # exactly as breakout.kicad_sch's own Sheetfile
# property spells it -- must match gen_breakout_power.py's own POWER_SHEETFILE constant.

# The brief's own required net list -- task-7-brief.md, "Net names you must produce,
# exactly". Every other breakout sheet consumes these by name.
CONTRACT_NETS = [
    "+12V", "-12V", "+5V", "+3V3", "AGND", "DGND", "ISO_P12", "ISO_N12", "INTAN_GND",
]

# Isolated-domain nets: the three contract nets plus this sheet's own internal isolated
# nodes (the pi filters' raw/filtered nodes and the two isolated-side regulators'
# FB/NR-SS nodes) -- everything electrically inside the DC-DC's secondary side. Checked
# at PIN level against NON_ISO_NETS below, same reasoning check_mule_netlist.py's own
# ISO_NETS/NON_ISO_NETS split gives: U2 (the isolated DC-DC) legitimately straddles the
# barrier BY REFERENCE (primary pins on the non-isolated side, secondary pins on the
# isolated side), so only a PIN-level check -- not a reference-level one -- can tell a
# real short from the converter's own by-design straddling.
#
# Reference numbers below are Task 7 fix round 1's, not the original report's: removing
# the old U1 (TPS7A4901, the undersized +12V->+5V logic regulator -- Finding 1) shifted
# every later reference down by one (U2->U1, U3->U2, U4->U3, U5->U4; R3-R6->R1-R4;
# recomputed directly against the regenerated netlist, not hand-derived -- see
# task-7-report.md's "Fix round 1"). U1_FB/U1_NRSS (the old TPS7A4901-for-+5V's own FB/
# NR-SS nodes) no longer exist at all -- LD1117S33 (now U1) is a fixed regulator with
# neither pin -- so they are dropped here, not renumbered.
ISO_NETS = [
    "ISO_P12", "ISO_N12", "INTAN_GND",
    "ISO_P15_RAW", "ISO_P15_FILT", "ISO_N15_RAW", "ISO_N15_FILT",
    "U3_FB", "U3_NRSS", "U4_FB", "U4_NRSS",
]
NON_ISO_NETS = [
    "+12V", "-12V", "+5V", "+3V3", "AGND", "DGND",
    "P12_RAW", "N12_RAW", "P5_RAW",
]

# Rail-pair bypass/bulk capacitor counts. `_rail_bypass_cap_count()` below counts every
# C-prefixed reference wired straight across a (rail, gnd) pair PROJECT-WIDE (it reads
# the whole exported breakout.kicad_sch netlist, not just power.kicad_sch's own portion
# of it) -- so this can only regress by a future edit accidentally dropping, adding, or
# moving one, same regression-guard purpose as check_mule_netlist.py's own
# RAIL_BYPASS_EXPECTED, computed here directly against the currently-generated netlist
# (see task-7-report.md) rather than assumed from the generator's own source. Recomputed
# for fix round 1 (reference numbers shifted -- see ISO_NETS's own comment above; only
# +12V/DGND's COUNT actually changed, 2->1, since U1's own CIN cap is gone along with U1
# itself).
#
# RECOMPUTED AGAIN AT TASK 8: +5V/DGND and +3V3/DGND are SHARED rails -- Task 7's own
# +5V/DGND count (2: C5, C6, the inlet's own entry bulk+small) and +3V3/DGND count (2:
# C7, C8, the LDO's own output decouple+bulk) are UNCHANGED on power.kicad_sch's own
# side, but taskpc-digital.kicad_sch (Task 8) is the first OTHER sheet to also draw
# these two rails, and it adds its own local 100nF decoupling next to each of its own
# ICs (place_octal_buffer()/place_reward_or_gate()/place_debounce_inverters()'s own
# established per-IC-decoupling convention, same discipline as every IC on this whole
# project) -- 6 more on +5V/DGND (the _BUF bank's 3 HCT541 packages + the outbound
# HCT541 + the 74HCT32 OR gate + the 74HCT14 debounce inverter, all +5V-powered) and 3
# more on +3V3/DGND (the 3 LVC541 packages). Confirmed directly against the regenerated
# whole-project netlist (not guessed), same discipline the rest of this dict already
# follows.
#
# RECOMPUTED AGAIN AT TASK 9: pi-interface.kicad_sch adds two more ICs on these same
# shared rails, each with its own place_octal_buffer()-style 100nF decoupler -- the
# RWD_DLVR level-shift SN74LVC541APW (+3V3, +1 -> 6) and the BARCODE_PI/CAM_TRIG_EYE/
# CAM_TRIG_BEH output SN74HCT541PW (+5V, +1 -> 9). Confirmed directly against the
# regenerated whole-project netlist. WILL need updating again once Tasks 10-12 add their
# own decoupling to these same two shared rails -- not a one-time fix, an expected
# consequence of `_rail_bypass_cap_count()`'s own project-wide scope every later child
# sheet inherits.
#
# RECOMPUTED AGAIN AT TASK 10a: analog-frontend.kicad_sch is the first child sheet to draw
# +12V/-12V (every prior sheet's own ICs ran on +5V/+3V3 only), and it decouples each of
# its 15 op-amp packages (13 INA105KU difference receivers + 1 OPA2197xD photodiode TIA
# dual + 1 OPA4197xD mic quad) with its own 100nF-per-rail pair, same discipline as every
# IC on this whole project -- +15 on EACH of +12V/AGND and -12V/AGND (2->17 both), not
# just one of the two, since every one of those 15 packages is genuinely dual-supply.
# Confirmed directly against the regenerated whole-project netlist via this same
# `_rail_bypass_cap_count()`, not guessed. Exactly the update this comment's own prior
# entry predicted would be needed "once Tasks 10-12 add their own" -- WILL need the same
# treatment again once Tasks 10c-10d (and 11-12) place their own +12V/-12V-powered parts.
#
# RECOMPUTED AGAIN AT TASK 10b: analog-ni.kicad_sch decouples each of its 7 OPA4192-value
# quad-buffer packages (4 for the 16-channel NI bank, 3 for the 9-channel task-PC bank) on
# BOTH +12V/AGND and -12V/AGND, same one-decoupling-pair-per-package discipline every dual-
# supply IC on this project already follows -- +7 on EACH rail pair (17->24 both), not just
# one, since every one of those 7 packages is genuinely dual-supply. Confirmed directly
# against the regenerated whole-project netlist via this same `_rail_bypass_cap_count()`,
# not guessed. WILL need the same treatment again once Tasks 10c-10d (and 11-12) place
# their own +12V/-12V-powered parts.
#
# RECOMPUTED AGAIN AT TASK 10c: mux-intan.kicad_sch adds two more part POPULATIONS on TWO
# DIFFERENT rail-pair groups, not just one -- the first time a single child sheet touches
# both an already-tracked non-isolated pair AND an already-tracked isolated pair in one
# commit. Its 8 ADG1206YRUZ muxes (the AGND-domain half of the sheet, spec Sec.6.3's own
# "the mux sits in AGND") decouple +12V/AGND and -12V/AGND same as every other dual-supply
# IC on this project -- +8 on EACH (24->32 both). Its 8 INA105KU difference amplifiers (the
# INTAN_GND-domain half -- see this sheet's own module docstring for why THESE, not the
# muxes, are the parts that legitimately straddle the isolation boundary) decouple ISO_P12/
# INTAN_GND and ISO_N12/INTAN_GND -- +8 on EACH (2->10 both). Confirmed directly against the
# regenerated whole-project netlist via this same `_rail_bypass_cap_count()`, not guessed
# (32/32/10/10, checked before writing these numbers in). WILL need the same treatment
# again once Task 10d (and 11-12) place their own rail-powered parts.
RAIL_BYPASS_EXPECTED = {
    ("+12V", "AGND"): 32,         # C1 (10uF), C2 (100nF) -- entry bulk+small, brief Step 1;
                                   # +15 from analog-frontend.kicad_sch's own 15 op-amp
                                   # packages (Task 10a); +7 from analog-ni.kicad_sch's own
                                   # 7 quad-buffer packages (Task 10b); +8 from
                                   # mux-intan.kicad_sch's own 8 ADG1206YRUZ muxes (Task 10c)
    ("-12V", "AGND"): 32,          # C3, C4 -- ditto, -12V rail; +15 (Task 10a) +7 (Task
                                   # 10b) +8 (Task 10c), same reasoning as +12V/AGND above
    ("+5V", "DGND"): 9,           # C5, C6 -- entry bulk+small (power.kicad_sch); + 6 from
                                   # taskpc-digital.kicad_sch's own +5V-powered ICs (Task 8);
                                   # + 1 from pi-interface.kicad_sch's own trigger buffer
                                   # (Task 9)
    ("+12V", "DGND"): 1,          # C9 -- U2/IH1215D primary-side bypass (was 2 before fix
                                   # round 1: U1's own CIN, now gone with U1, was the other)
    ("+3V3", "DGND"): 6,          # C7, C8 -- U1/LD1117S33TR output decouple+bulk
                                   # (power.kicad_sch); + 3 from taskpc-digital.kicad_sch's
                                   # own 3 LVC541 packages (Task 8); + 1 from
                                   # pi-interface.kicad_sch's own RWD_DLVR level-shift
                                   # (Task 9)
    ("ISO_P12", "INTAN_GND"): 10,  # C14, C15 -- U3 COUT + extra HF bypass; +8 from
                                   # mux-intan.kicad_sch's own 8 INA105KU difference
                                   # amplifiers (Task 10c)
    ("ISO_N12", "INTAN_GND"): 10,  # C20, C21 -- U4 COUT + extra HF bypass; +8 from
                                   # mux-intan.kicad_sch's own 8 INA105KU (Task 10c), same
                                   # reasoning as ISO_P12/INTAN_GND above
    ("ISO_P15_RAW", "INTAN_GND"): 1,   # C10 -- positive pi filter's first 10uF
    ("ISO_P15_FILT", "INTAN_GND"): 1,  # C11 -- positive pi filter's second 10uF / U3 CIN
    ("ISO_N15_RAW", "INTAN_GND"): 1,   # C16 -- negative pi filter's first 10uF
    ("ISO_N15_FILT", "INTAN_GND"): 1,  # C17 -- negative pi filter's second 10uF / U4 CIN
}


def _rail_bypass_cap_count(nets: dict[str, list[Node]], rail_net: str, gnd_net: str) -> int:
    rail_caps = {n.ref for n in nets.get(rail_net, []) if n.ref.startswith("C")}
    gnd_caps = {n.ref for n in nets.get(gnd_net, []) if n.ref.startswith("C")}
    return len(rail_caps & gnd_caps)


def _refs_on(nets: dict[str, list[Node]], net: str) -> set[str]:
    return {n.ref for n in nets.get(net, [])}


def _pins_on(nets: dict[str, list[Node]], net: str) -> set[tuple[str, str]]:
    return {(n.ref, n.pin) for n in nets.get(net, [])}


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    """Run the full contract check. `values` is parse_component_values()'s own {ref:
    Value} map -- needed alongside `nets` because a component's Value (a resistor's
    ohms, an IC's part number) lives on its own (comp ...) block in the netlist, not on
    the (nets ...) section's Node records parse_netlist() returns. Returns human-readable
    summary lines on success; raises CheckFailure with a specific, localized message on
    the first violation.
    """
    summary = []

    # --- Every contract net exists and is non-trivially populated. ---
    for name in CONTRACT_NETS:
        check(name in nets, f"missing locked-contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    summary.append(f"All {len(CONTRACT_NETS)} contract nets present and populated: {CONTRACT_NETS}.")

    # --- The star point: AGND and DGND are genuinely DISTINCT nets (no shared node),
    # joined at EXACTLY one component reference (the NetTie_2), and that reference
    # contributes exactly one pin to each side (not, say, both its pins landing on the
    # same net by some other mistake). ---
    agnd_refs = _refs_on(nets, "AGND")
    dgnd_refs = _refs_on(nets, "DGND")
    check(
        _pins_on(nets, "AGND").isdisjoint(_pins_on(nets, "DGND")),
        "AGND and DGND share a PIN directly -- they are not distinct nets",
    )
    bridging_refs = agnd_refs & dgnd_refs
    check(
        len(bridging_refs) == 1,
        f"AGND and DGND should be joined by reference at EXACTLY one component (the star "
        f"point's NetTie_2), found {len(bridging_refs)}: {bridging_refs}",
    )
    bridge_ref = next(iter(bridging_refs))
    nettie_refs = {n.ref for name in nets for n in nets[name] if n.ref.startswith("NT")}
    check(
        nettie_refs == {bridge_ref},
        f"the AGND/DGND bridging component {bridge_ref!r} is not the design's only "
        f"NetTie_2 reference ({nettie_refs}) -- either the star point isn't a net tie, or "
        f"there's more than one net tie somewhere on the sheet",
    )
    bridge_agnd_pins = [n.pin for n in nets["AGND"] if n.ref == bridge_ref]
    bridge_dgnd_pins = [n.pin for n in nets["DGND"] if n.ref == bridge_ref]
    check(
        len(bridge_agnd_pins) == 1 and len(bridge_dgnd_pins) == 1,
        f"star-point net tie {bridge_ref} should contribute exactly 1 pin to each of "
        f"AGND/DGND, found {len(bridge_agnd_pins)}/{len(bridge_dgnd_pins)}",
    )
    summary.append(
        f"AGND and DGND are distinct nets ({len(nets['AGND'])}/{len(nets['DGND'])} nodes, "
        f"zero shared pins), joined at exactly one net tie ({bridge_ref})."
    )

    # --- Isolation barrier: no (ref, pin) may appear on both an isolated-domain net and a
    # non-isolated net. Pin level, not reference level -- U3 (the isolated DC-DC)
    # straddles BY DESIGN (primary pins legitimately on +12V/DGND, secondary pins
    # legitimately on the isolated nets); a reference-level check would incorrectly flag
    # it. ---
    for name in ISO_NETS + NON_ISO_NETS:
        check(name in nets, f"missing net: {name!r}")
    iso_pins = {p for name in ISO_NETS for p in _pins_on(nets, name)}
    non_iso_pins = {p for name in NON_ISO_NETS for p in _pins_on(nets, name)}
    overlap = iso_pins & non_iso_pins
    check(
        not overlap,
        f"isolation barrier violated: pin(s) {overlap} appear on both an isolated-domain "
        f"net ({ISO_NETS}) and a non-isolated net ({NON_ISO_NETS})",
    )
    summary.append(
        f"Isolation barrier intact: zero pins shared between the {len(ISO_NETS)} "
        f"isolated-domain nets and the {len(NON_ISO_NETS)} non-isolated nets."
    )

    # --- U2 (IH1215D) pin map: confirms the isolated DC-DC's primary side really is on
    # +12V/DGND and its secondary side really is on the raw isolated nodes that lead to
    # ISO_P12/ISO_N12/INTAN_GND -- the specific wiring the isolation-barrier check above
    # depends on being right, not just present. (Ref "U2", not the original report's
    # "U3": fix round 1 removed the old U1/TPS7A4901-for-+5V, shifting every later
    # reference down by one -- see ISO_NETS's own comment above.) ---
    u2_map = {"1": "DGND", "3": "+12V", "4": "ISO_N15_RAW", "5": "ISO_P15_RAW", "6": "INTAN_GND"}
    for pin, net in u2_map.items():
        nodes = [n for n in nets[net] if n.ref == "U2" and n.pin == pin]
        check(len(nodes) == 1, f"U2 pin {pin} expected on {net!r}, not found there: {nets[net]}")
    summary.append("U2 (isolated DC-DC) pin map confirmed: primary +12V/DGND, secondary raw ISO nodes.")

    # --- Reverse-polarity diode orientation, D1 (+12V), D2 (-12V), and D3 (+5V, fix
    # round 1) -- worked through explicitly in gen_breakout_power.py's own
    # _place_inlet() docstring because -12V needs the OPPOSITE orientation from +12V/+5V
    # and it is easy to get backwards; this is the executable form of that derivation. A
    # silently-reversed diode would still produce a net literally named "+12V"/"-12V"/
    # "+5V" (ERC-clean, netlist-plausible) while providing NO reverse-polarity
    # protection at all -- exactly the "plausible but wrong" failure class this whole
    # checker exists to catch, just at the component-orientation level instead of the
    # pin-resolution level. ---
    d1_k = [n for n in nets["+12V"] if n.ref == "D1" and n.pin == "1"]
    d1_a = [n for n in nets["P12_RAW"] if n.ref == "D1" and n.pin == "2"]
    check(len(d1_k) == 1, f"D1 cathode (pin 1) expected on +12V, not found: {nets['+12V']}")
    check(len(d1_a) == 1, f"D1 anode (pin 2) expected on P12_RAW, not found: {nets['P12_RAW']}")
    d2_a = [n for n in nets["-12V"] if n.ref == "D2" and n.pin == "2"]
    d2_k = [n for n in nets["N12_RAW"] if n.ref == "D2" and n.pin == "1"]
    check(len(d2_a) == 1, f"D2 anode (pin 2) expected on -12V, not found: {nets['-12V']}")
    check(len(d2_k) == 1, f"D2 cathode (pin 1) expected on N12_RAW, not found: {nets['N12_RAW']}")
    d3_k = [n for n in nets["+5V"] if n.ref == "D3" and n.pin == "1"]
    d3_a = [n for n in nets["P5_RAW"] if n.ref == "D3" and n.pin == "2"]
    check(len(d3_k) == 1, f"D3 cathode (pin 1) expected on +5V, not found: {nets['+5V']}")
    check(len(d3_a) == 1, f"D3 anode (pin 2) expected on P5_RAW, not found: {nets['P5_RAW']}")
    summary.append(
        "Reverse-polarity diodes correctly oriented: D1 anode->P12_RAW/cathode->+12V "
        "(source->load), D2 anode->-12V/cathode->N12_RAW (load->source, mirrored -- see "
        "_place_inlet()'s docstring for the derivation), D3 anode->P5_RAW/cathode->+5V "
        "(source->load, same orientation as D1 -- fix round 1)."
    )

    # --- Finding 1's own core claim, made executable: +5V ORIGINATES AT THE INLET, not
    # from a regulator off +12V. Two halves, both needed -- either alone is not the full
    # claim. (1) POSITIVE: the D3/J1 wiring just confirmed above already proves +5V
    # traces back to J1 (the physical connector) through exactly one protection diode --
    # restated here as the conclusion rather than re-checked. (2) NEGATIVE, the half a
    # topology check alone would miss: no node on +5V is power_out-typed, other than a
    # PWR_FLAG (which never appears in an exported netlist's own node list at all --
    # confirmed empirically, checked directly against this net and against +12V's own
    # long-standing flag -- so no explicit exclusion is even needed here). A regulator's
    # OUT pin is ALWAYS power_out-typed (confirmed directly: U3's own OUT pin on ISO_P12
    # is "power_out" in this exact netlist) -- ruling out ANY power_out pin on +5V rules
    # out ANY regulator driving it, not merely the specific TPS7A4901 fix round 1
    # removed, catching a future regression that re-adds a DIFFERENT regulator here just
    # as surely. ---
    check(len(nets["P5_RAW"]) >= 1, f"P5_RAW: suspiciously small, only {nets['P5_RAW']}")
    j1_on_p5raw = [n for n in nets["P5_RAW"] if n.ref == "J1"]
    check(len(j1_on_p5raw) == 1, f"J1 (the inlet connector) has no pin on P5_RAW: {nets['P5_RAW']}")
    power_out_on_5v = [n for n in nets["+5V"] if n.pintype == "power_out"]
    check(
        not power_out_on_5v,
        f"+5V has a power_out-typed pin ({power_out_on_5v}) -- it is being DRIVEN BY A "
        f"REGULATOR again, regressing exactly the Finding 1 defect fix round 1 removed "
        f"(the +5V rail must originate at the inlet via D3, not from an on-board "
        f"regulator off +12V; see task-7-report.md's 'Fix round 1')",
    )
    summary.append(
        "+5V originates at the inlet (J1 -> D3 -> +5V, same reverse-polarity orientation "
        "as +12V) and NOT from a regulator: zero power_out-typed pins on +5V anywhere on "
        "this sheet (fix round 1 -- see task-7-report.md)."
    )

    # --- FB-divider topology + values for the two adjustable isolated-supply
    # post-regulators (U3, U4 -- fix round 1 renumbered these from U4/U5; the +5V
    # regulator's own divider, formerly U1's, no longer exists at all, see ISO_NETS's
    # own comment above): R_top between OUT and FB, R_bottom between FB and the
    # regulator's own GND net, with the specific 1%-standard values task-7-report.md
    # derives/cites. Catches a swapped R1/R2 (which would still "look like" a divider
    # topologically but set the wrong ratio) as well as a wrong or drifted value. ---
    divider_specs = [
        ("U3", "ISO_P12", "INTAN_GND", "R1", "90.9k", "R2", "10.0k"),
        ("U4", "ISO_N12", "INTAN_GND", "R3", "93.1k", "R4", "10.0k"),
    ]
    for u_ref, out_net, gnd_net, r_top, r_top_val, r_bot, r_bot_val in divider_specs:
        fb_net = f"{u_ref}_FB"
        check(fb_net in nets, f"missing net: {fb_net!r}")
        top_on_out = [n for n in nets[out_net] if n.ref == r_top]
        top_on_fb = [n for n in nets[fb_net] if n.ref == r_top]
        bot_on_fb = [n for n in nets[fb_net] if n.ref == r_bot]
        bot_on_gnd = [n for n in nets[gnd_net] if n.ref == r_bot]
        check(
            len(top_on_out) == 1 and len(top_on_fb) == 1,
            f"{u_ref}: divider top resistor {r_top} should bridge {out_net}<->{fb_net}, "
            f"found on {out_net}: {top_on_out}, on {fb_net}: {top_on_fb}",
        )
        check(
            len(bot_on_fb) == 1 and len(bot_on_gnd) == 1,
            f"{u_ref}: divider bottom resistor {r_bot} should bridge {fb_net}<->{gnd_net}, "
            f"found on {fb_net}: {bot_on_fb}, on {gnd_net}: {bot_on_gnd}",
        )
        fb_pin = [n for n in nets[fb_net] if n.ref == u_ref]
        check(len(fb_pin) == 1, f"{u_ref}: expected exactly 1 own pin on {fb_net}, found {fb_pin}")
        check(
            values.get(r_top) == r_top_val,
            f"{u_ref}: divider top resistor {r_top} should be {r_top_val!r}, found "
            f"{values.get(r_top)!r}",
        )
        check(
            values.get(r_bot) == r_bot_val,
            f"{u_ref}: divider bottom resistor {r_bot} should be {r_bot_val!r}, found "
            f"{values.get(r_bot)!r}",
        )
    summary.append(
        "FB-divider topology AND values confirmed: U3 R1=90.9k/R2=10.0k (Vout~11.96V), "
        "U4 R3=93.1k/R4=10.0k (TI's own published -12V pair) -- each R_top bridges "
        "OUT<->FB, each R_bottom bridges FB<->GND. (U1/LD1117S33 is a FIXED regulator, "
        "no divider to check; the old TPS7A4901-for-+5V divider this used to also check "
        "no longer exists at all -- fix round 1.)"
    )

    # --- Right part in the right role: the wl-sync custom symbols (TPS7A4901/TPS7A3001)
    # and the real, specific, brief-named/report-justified parts (IH1215D, SS14,
    # LD1117S33TR_SOT223) are each the value gen_breakout_power.py actually intends for
    # that reference -- catches a copy-paste value mistake (e.g. U4 accidentally left as
    # "TPS7A4901" instead of "TPS7A3001", which would silently turn the -12V
    # post-regulator into a POSITIVE regulator wired backwards) that no net-topology
    # check above would notice, since the pin NUMBERS/NAMES are identical between the
    # two parts by design (see gen_wl_sync_lib.py's own comment on the shared
    # TPS7A49_PINS table). References renumbered for fix round 1 -- see ISO_NETS's own
    # comment above; D3 (the new +5V protection diode) added. ---
    expected_values = {
        "U1": "LD1117S33TR_SOT223", "U2": "IH1215D",
        "U3": "TPS7A4901", "U4": "TPS7A3001",
        "D1": "SS14", "D2": "SS14", "D3": "SS14",
    }
    for ref, expected in expected_values.items():
        check(
            values.get(ref) == expected,
            f"{ref}: expected Value {expected!r}, found {values.get(ref)!r}",
        )
    summary.append(f"Component values confirmed for the right part in the right role: {expected_values}.")

    # --- Bypass/bulk capacitor counts, every rail pair. ---
    for (rail, gnd), expected in RAIL_BYPASS_EXPECTED.items():
        found = _rail_bypass_cap_count(nets, rail, gnd)
        check(
            found == expected,
            f"{rail}/{gnd}: expected {expected} bypass/bulk capacitor(s) wired directly "
            f"across this rail pair, found {found}",
        )
    summary.append(
        "Bypass/bulk capacitor counts intact on all "
        f"{len(RAIL_BYPASS_EXPECTED)} rail pairs: "
        + ", ".join(f"{r}/{g}={n}" for (r, g), n in RAIL_BYPASS_EXPECTED.items())
    )

    # --- No two named nets collapsed onto the same physical net (constraint 1's own
    # signature failure: two labels, one real net). ---
    all_named = CONTRACT_NETS + ["P12_RAW", "N12_RAW", "P5_RAW"]
    seen: dict[frozenset, str] = {}
    for name in all_named:
        key = frozenset((n.ref, n.pin) for n in nets[name])
        check(
            key not in seen,
            f"{name} and {seen.get(key)} have IDENTICAL node sets -- they are the same "
            f"physical net under two different labels",
        )
        seen[key] = name
    summary.append(f"No two of the {len(all_named)} named nets checked collapsed onto the same physical net.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire, rather than
# passing vacuously -- same convention check_mule_netlist.py's own self_test()
# establishes, run against synthetic corruptions of the real, currently-passing netlist
# every time this script runs, not just when someone remembers to.
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
    """Negative controls, one per check() family above that isn't already exercised by
    the others firing first. `good_nets`/`good_values` must already pass verify()
    cleanly -- each corruption below is a minimal, targeted mutation of that known-good
    structure, not a hand-built fixture, so the test exercises the real parsed shape of
    the real schematic.
    """
    results = []

    # AGND/DGND short: merge them into a single shared pin (simulating a missing/failed
    # net tie, or a stray wire bridging them somewhere else on the sheet).
    shorted = copy.deepcopy(good_nets)
    phantom = Node(ref="DBG99", pin="1", pinfunction="", pintype="passive")
    shorted["AGND"] = shorted["AGND"] + [phantom]
    shorted["DGND"] = shorted["DGND"] + [phantom]
    msg = _assert_fails(shorted, good_values, "share a PIN directly", "AGND/DGND direct short")
    results.append(f"AGND/DGND direct short (phantom shared pin): caught -- {msg}")

    # A second bridging component: AGND and DGND still disjoint at pin level, but now
    # joined by reference at TWO components instead of exactly one (simulating an extra,
    # unintended net tie or jumper added somewhere else on the sheet).
    double_bridged = copy.deepcopy(good_nets)
    extra = Node(ref="NT2", pin="1", pinfunction="", pintype="passive")
    extra2 = Node(ref="NT2", pin="2", pinfunction="", pintype="passive")
    double_bridged["AGND"] = double_bridged["AGND"] + [extra]
    double_bridged["DGND"] = double_bridged["DGND"] + [extra2]
    msg = _assert_fails(double_bridged, good_values, "EXACTLY one component", "second AGND/DGND bridge")
    results.append(f"Second AGND/DGND bridge (extra NT2, not the real star point): caught -- {msg}")

    # Isolation barrier: short ISO_P12 to +12V via one shared pin (simulating a routing
    # mistake that ties the isolated and non-isolated domains together).
    iso_shorted = copy.deepcopy(good_nets)
    phantom2 = Node(ref="DBG98", pin="1", pinfunction="", pintype="passive")
    iso_shorted["ISO_P12"] = iso_shorted["ISO_P12"] + [phantom2]
    iso_shorted["+12V"] = iso_shorted["+12V"] + [phantom2]
    msg = _assert_fails(iso_shorted, good_values, "isolation barrier violated", "ISO_P12/+12V short")
    results.append(f"Isolation barrier short (ISO_P12 tied to +12V via one shared pin): caught -- {msg}")

    # Reverse-polarity diode installed backwards: swap D1's two pins between +12V and
    # P12_RAW (same net MEMBERSHIP as before, just each pin now on the OTHER net --
    # exactly what a physically-reversed diode looks like in the netlist).
    d1_reversed = copy.deepcopy(good_nets)
    d1_in_12v = next(n for n in d1_reversed["+12V"] if n.ref == "D1")
    d1_in_raw = next(n for n in d1_reversed["P12_RAW"] if n.ref == "D1")
    d1_reversed["+12V"] = [n for n in d1_reversed["+12V"] if n.ref != "D1"] + [
        Node(ref="D1", pin=d1_in_raw.pin, pinfunction=d1_in_raw.pinfunction, pintype=d1_in_raw.pintype)
    ]
    d1_reversed["P12_RAW"] = [n for n in d1_reversed["P12_RAW"] if n.ref != "D1"] + [
        Node(ref="D1", pin=d1_in_12v.pin, pinfunction=d1_in_12v.pinfunction, pintype=d1_in_12v.pintype)
    ]
    msg = _assert_fails(d1_reversed, good_values, "D1 cathode", "D1 installed backwards")
    results.append(f"D1 (reverse-polarity diode) installed backwards: caught -- {msg}")

    # D3 (+5V, fix round 1) installed backwards -- same construction as D1's own test
    # above, mirrored onto the new diode.
    d3_reversed = copy.deepcopy(good_nets)
    d3_in_5v = next(n for n in d3_reversed["+5V"] if n.ref == "D3")
    d3_in_raw = next(n for n in d3_reversed["P5_RAW"] if n.ref == "D3")
    d3_reversed["+5V"] = [n for n in d3_reversed["+5V"] if n.ref != "D3"] + [
        Node(ref="D3", pin=d3_in_raw.pin, pinfunction=d3_in_raw.pinfunction, pintype=d3_in_raw.pintype)
    ]
    d3_reversed["P5_RAW"] = [n for n in d3_reversed["P5_RAW"] if n.ref != "D3"] + [
        Node(ref="D3", pin=d3_in_5v.pin, pinfunction=d3_in_5v.pinfunction, pintype=d3_in_5v.pintype)
    ]
    msg = _assert_fails(d3_reversed, good_values, "D3 cathode", "D3 installed backwards")
    results.append(f"D3 (+5V reverse-polarity diode, fix round 1) installed backwards: caught -- {msg}")

    # Finding 1 regression control: +5V driven by a regulator again -- inject a
    # synthetic power_out-typed node onto "+5V" (exactly what re-adding ANY regulator's
    # OUT pin there, not just the specific removed TPS7A4901, would look like in the
    # netlist) and confirm the "not from a regulator" half of the +5V-origin check fires.
    regulator_regressed = copy.deepcopy(good_nets)
    phantom_reg = Node(ref="DBG97", pin="1", pinfunction="OUT_1", pintype="power_out")
    regulator_regressed["+5V"] = regulator_regressed["+5V"] + [phantom_reg]
    msg = _assert_fails(
        regulator_regressed, good_values, "power_out-typed pin", "+5V driven by a regulator again",
    )
    results.append(
        f"+5V regulator regression (synthetic power_out pin injected, simulating a "
        f"regulator re-added off +12V): caught -- {msg}"
    )

    # Divider resistor value drift: R1 (should be 90.9k, fix round 1's renumbering)
    # accidentally left/edited to a different value -- topology still correct, only the
    # VALUE is wrong.
    drifted_values = dict(good_values)
    drifted_values["R1"] = "10k"
    msg = _assert_fails(good_nets, drifted_values, "should be '90.9k'", "R1 value drift")
    results.append(f"R1 value drift (90.9k -> 10k, topology unchanged): caught -- {msg}")

    # Copy-paste part mix-up: U4 (should be TPS7A3001, the negative regulator -- fix
    # round 1 renumbered this from U5) left as TPS7A4901 -- exactly the failure class
    # the "right part in the right role" check exists for, since the two parts' pin
    # numbers/names are identical.
    swapped_part = dict(good_values)
    swapped_part["U4"] = "TPS7A4901"
    msg = _assert_fails(good_nets, swapped_part, "U4: expected Value", "U4 part mix-up")
    results.append(f"U4 part mix-up (TPS7A3001 -> TPS7A4901, a copy-paste-shaped bug): caught -- {msg}")

    # Bypass-cap negative control: drop one capacitor's two nodes from a rail pair
    # (simulating an accidental deletion in a future edit).
    dropped = copy.deepcopy(good_nets)
    p12_caps = {n.ref for n in dropped["+12V"] if n.ref.startswith("C")}
    agnd_caps = {n.ref for n in dropped["AGND"] if n.ref.startswith("C")}
    victim = sorted(p12_caps & agnd_caps)[0]
    dropped["+12V"] = [n for n in dropped["+12V"] if n.ref != victim]
    dropped["AGND"] = [n for n in dropped["AGND"] if n.ref != victim]
    msg = _assert_fails(dropped, good_values, "bypass/bulk capacitor", f"{victim} dropped from +12V/AGND")
    results.append(f"Bypass cap removal ({victim} dropped from +12V/AGND): caught -- {msg}")

    return results


# ---------------------------------------------------------------------------
# Finding 2's check: instance-path ancestor-chain resolution. Reads the raw .kicad_sch
# SOURCES directly, not the exported netlist -- see module docstring for why the netlist
# cannot show this defect at all (confirmed empirically, not assumed).
# ---------------------------------------------------------------------------


def verify_instance_paths(power_sch_text: str, breakout_sch_text: str) -> str:
    """Confirm every component/power-flag instance power.kicad_sch places carries the
    REAL root+sheet-symbol ancestor path (`/<breakout's own root uuid>/<the "power"
    sheet symbol's own uuid>`, both read fresh off breakout_sch_text -- not trusted from
    any in-process value) rather than a self-referential one (power.kicad_sch's own
    file-identity uuid, which is what kicad_sch.py's Sch used to default to for every
    file regardless of whether it was a hierarchy root -- see task-7-report.md's
    Concerns and "Fix round 1", and kicad_sch.py's Sch class docstring for the real
    KiCad file this was confirmed against).

    Returns a summary line on success; raises CheckFailure with a specific message on
    the first violation. Structured as its own function (not folded into verify()) and
    given its own self-test (self_test_instance_paths(), below) because its INPUTS are
    different in kind -- raw schematic source text, not a parsed netlist -- not because
    the property it checks matters any less.
    """
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(power_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"power.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with "
        f"breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate "
        f"power.kicad_sch (kicad_sch.py's uid() mints a fresh uuid4 per file; an actual "
        f"collision here would itself be the failure worth investigating)",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, POWER_SHEETFILE)

    paths = find_all_instance_paths(power_sch_text)
    check(
        len(paths) >= 40,
        f"only found {len(paths)} (instances (path ...)) entries in power.kicad_sch -- "
        f"expected >=40 (one per placed component/power-flag; task-7-report.md's "
        f"original count was 5 ICs + 24 caps + 6 resistors + 2 diodes + 2 ferrite beads "
        f"+ 1 net tie + 1 connector + 6 flags = 47, and fix round 1 only shifted counts "
        f"around, not down past 40)",
    )
    bad = [p for p in paths if p != expected_prefix]
    # `bad[:3]`, not `bad[0]`: check()'s message argument is a plain Python expression,
    # evaluated eagerly before check() decides whether to raise -- indexing an element
    # that only exists on the FAILURE path would raise IndexError even when `bad` is
    # empty and the check legitimately passes. A slice is safe empty-or-not.
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in power.kicad_sch do not "
        f"resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found "
        f"{bad[:3]!r} instead. A self-referential path (this file's own root uuid rather "
        f"than breakout's root + the 'power' sheet symbol's own uuid) breaks "
        f"cross-probing and any future PCB generator's schematic cross-link, even "
        f"though it does not change kicad-cli sch erc/export netlist's own output "
        f"(confirmed empirically -- see task-7-report.md's 'Fix round 1')",
    )
    return (
        f"All {len(paths)} component/power-flag instance paths in power.kicad_sch "
        f"resolve to the real ancestor chain {expected_prefix!r} (breakout's own root "
        f"uuid + the 'power' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(power_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(power_text, breakout_text)
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


def self_test_instance_paths(good_power_text: str, good_breakout_text: str) -> list[str]:
    """One negative control: reintroduce the ORIGINAL self-referential-path defect
    (task-7-report.md's Concerns, before fix round 1) into a single component's own
    `(instances (path ...))` entry -- power.kicad_sch's own file-identity uuid in place
    of the real breakout-root+sheet-symbol chain, exactly what kicad_sch.py's Sch always
    used to emit -- and confirm verify_instance_paths() catches it. `good_power_text`/
    `good_breakout_text` must already pass verify_instance_paths() cleanly.
    """
    own_root_uuid = find_root_uuid(good_power_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, POWER_SHEETFILE)

    # Corrupt exactly ONE occurrence (not every one) -- a single mis-generated
    # component's path is a more realistic regression than the whole file reverting,
    # and proves the check catches even one bad entry among many good ones, not just a
    # wholesale reversion.
    corrupted = good_power_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_power_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_power_text may not actually be passing verify_instance_paths() "
        "cleanly to begin with",
    )
    msg = _assert_instance_paths_fail(
        corrupted, good_breakout_text, "do not resolve to the real ancestor chain",
        "one component's instance path reverted to self-referential",
    )
    return [
        f"Self-referential instance path (power.kicad_sch's own root uuid in place of "
        f"the real breakout ancestor chain, on one component -- the exact original "
        f"defect) reintroduced: caught -- {msg}"
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

    # Finding 2's check: instance-path ancestor-chain resolution -- reads the raw
    # .kicad_sch SOURCES, not the netlist (see module docstring for why). Not
    # overridable from argv, unlike net_path above -- see DEFAULT_BREAKOUT_SCH/
    # DEFAULT_POWER_SCH's own comment.
    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_POWER_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_POWER_SCH} do not exist -- "
            f"run gen_breakout.py and gen_breakout_power.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    power_sch_text = DEFAULT_POWER_SCH.read_text()
    try:
        path_summary = verify_instance_paths(power_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(power_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
