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

FAN HEADERS (spec Sec.9.5, added after Task 7's original commit) added a third check
family, same "topology, not just ERC" motivation as the first two: verify() now also
asserts the four Conn_01x03 fan headers exist with the right pin map, that no fan pin
touches AGND/DGND directly, that FAN_RTN reaches the rest of the ground system ONLY
through its own net tie (NT2 -- mirroring the AGND/DGND star-point check, not a copy of
it: FAN_RTN is checked disjoint from BOTH AGND and DGND, since it must never gain a
second, direct path to either), and that FAN_12V reaches +12V ONLY through the polyfuse
(F1) -- each with its own genuinely-firing negative control in self_test(). FAN_12V and
FAN_RTN are deliberately NOT added to CONTRACT_NETS above: nothing outside this sheet
ever consumes them by name (the fan headers are physically and electrically local to
this sheet), so they are not part of the cross-sheet contract that list exists to guard.
This addition also back-ports check_row_pitch_guard.py's shared row-pitch-vs-2-pin-span
collision guard to this checker (constraint 4) -- one of five checkers that had never
had it wired in; main() now runs it against power.kicad_sch's own raw source directly,
same pattern as the checkers it was already back-ported to at Task 11 fix round 2.

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
    "FAN_12V", "FAN_RTN",  # added with the fan headers -- unambiguously non-isolated
    # (DGND-referenced, never touching INTAN_GND/ISO_P12/ISO_N12), so adding them here
    # strengthens the isolation-barrier check to also catch a future edit that
    # accidentally bridges either fan net into the isolated domain.
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
#
# RECOMPUTED AGAIN AT TASK 10d: comparators.kicad_sch adds ONE more +-12V-powered
# package -- its own single LM339 quad comparator (the MCP4728 DAC is the sheet's other
# IC, but it runs on +3V3/AGND, a rail pair this dict has never tracked -- see that
# sheet's own module docstring for why that pair is a new, untracked one rather than an
# extension of an existing key) -- +1 on EACH of +12V/AGND and -12V/AGND (32->33 both).
# Confirmed directly against the regenerated whole-project netlist via this same
# `_rail_bypass_cap_count()`, not guessed. WILL need the same treatment again once Tasks
# 11-12 place their own +12V/-12V-powered parts.
#
# RECOMPUTED AGAIN AT TASK 12: control-usb-i2c.kicad_sch adds FOUR more +3V3-powered
# decouplers -- the MCP2221A's own VDD (100nF) and VUSB (220nF) caps (both topologically
# +3V3<->DGND pairs in this sheet's own 3.3V-self-powered configuration, see that
# generator's own module docstring), plus one 100nF decoupler per MCP23017 instance (x2) --
# +4 on +3V3/DGND (6->10). +12V/AGND, -12V/AGND, +5V/DGND, +12V/DGND, and every ISO_*
# pair are UNCHANGED: this sheet places nothing on any of them. Confirmed directly against
# the regenerated whole-project netlist via this same `_rail_bypass_cap_count()`, not
# guessed. This is the LAST schematic-capture sheet on this board, so this dict needs no
# further "will need updating again" note.
RAIL_BYPASS_EXPECTED = {
    ("+12V", "AGND"): 34,          # C1 (10uF), C2 (100nF) -- entry bulk+small, brief Step
                                   # 1; +15 from analog-frontend.kicad_sch's own 15 op-amp
                                   # packages (Task 10a); +7 from analog-ni.kicad_sch's own
                                   # 7 quad-buffer packages (Task 10b); +8 from
                                   # mux-intan.kicad_sch's own 8 ADG1206YRUZ muxes (Task
                                   # 10c); +2 from comparators.kicad_sch's own LM339 (Task
                                   # 10d, corrected: BOTH of that sheet's own LM339
                                   # decouplers now sit here -- see the -12V/AGND entry)
    ("-12V", "AGND"): 32,          # C3, C4 -- ditto, -12V rail; +15 (Task 10a) +7 (Task
                                   # 10b) +8 (Task 10c), same reasoning as +12V/AGND above.
                                   # NOTHING from comparators.kicad_sch (Task 10d): that
                                   # sheet's own LM339 originally ran on +-12V, which was a
                                   # destroy-hardware defect -- LM339 pin 12 is the common
                                   # emitter of all four open-collector outputs, so a -12V
                                   # V- made every output LOW ~-11.9V straight into
                                   # GPIO20/21/25. It now runs +12V/AGND single-supply and
                                   # consumes -12V not at all, so its second decoupler
                                   # moved from this pair to +12V/AGND (33/33 -> 34/32).
                                   # See gen_breakout_comparators.py's own "THE SECOND
                                   # DESTROY-HARDWARE CONSTRAINT".
    ("+5V", "DGND"): 18,          # C5, C6 -- entry bulk+small (power.kicad_sch); + 6 from
                                   # taskpc-digital.kicad_sch's own +5V-powered ICs (Task 8);
                                   # + 1 from pi-interface.kicad_sch's own trigger buffer
                                   # (Task 9); + 1 from opto-intan.kicad_sch's own
                                   # ACSL-6420 VDD1/GND1 decoupling cap (Task 11 -- the
                                   # ONLY one of its own 6 channels whose own package
                                   # power pins sit on +5V/DGND rather than ISO_5V/
                                   # INTAN_GND; opto-ni.kicad_sch adds none here, its own
                                   # decoupling is entirely on NI_5V/NI_GND); + 1 (C150)
                                   # from the panel-instrumentation task's own reward
                                   # one-shot (U69, taskpc-digital.kicad_sch) -- its own
                                   # +5V/DGND decoupling cap, same one-decoupler-per-IC
                                   # discipline as every other package on this board;
                                   # + 4 (C151-C154) from finding F1's own four new
                                   # SN74AHCT541PW packages (U70-U73, taskpc-digital.
                                   # kicad_sch -- the second buffer output of every
                                   # optocoupler LED pair), by that same one-decoupler-
                                   # per-IC discipline. 11 -> 15;
                                   # + 2 (C155-C156) from finding F5's own
                                   # TMR 1-0511 INPUT decoupling on
                                   # opto-intan.kicad_sch. Unlike the LDO it
                                   # replaced, that part draws its input
                                   # current in 220 kHz pulses and reflects
                                   # 80 mAp-p back into +5V, so local bulk +
                                   # HF bypass on its primary is required,
                                   # not stylistic. 15 -> 17; + 1 (C157) from finding
                                   # F4's own second trigger buffer (U74,
                                   # pi-interface.kicad_sch), one decoupler per IC
                                   # as always. 17 -> 18.
    ("+12V", "DGND"): 1,          # C9 -- U2/IH1215D primary-side bypass (was 2 before fix
                                   # round 1: U1's own CIN, now gone with U1, was the other)
    ("+3V3", "DGND"): 10,         # C7, C8 -- U1/LD1117S33TR output decouple+bulk
                                   # (power.kicad_sch); + 3 from taskpc-digital.kicad_sch's
                                   # own 3 LVC541 packages (Task 8); + 1 from
                                   # pi-interface.kicad_sch's own RWD_DLVR level-shift
                                   # (Task 9); + 4 from control-usb-i2c.kicad_sch's own
                                   # MCP2221A (VDD+VUSB) and 2x MCP23017 decouplers (Task 12)
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
    ("FAN_12V", "FAN_RTN"): 2,  # C147 (10uF bulk), C148 (100nF small) -- local bulk
    # capacitance on FAN_12V after F1 (the polyfuse), spec Sec.9.5's own requirement,
    # referenced to FAN_RTN (this rail's own dedicated return) rather than AGND/DGND --
    # see gen_breakout_power.py's own _place_fan_headers() docstring.
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
    check(
        bridge_ref.startswith("NT"),
        f"the AGND/DGND bridging component {bridge_ref!r} is not a NetTie_2 reference "
        f"-- either the star point isn't a net tie, or the bridge is some other part",
    )
    # NOTE: this used to also assert bridge_ref was the design's ONLY "NT"-prefixed
    # reference anywhere on the board. That was true before the fan headers (spec
    # Sec.9.5) added a SECOND, legitimate net tie (NT2, FAN_RTN<->DGND) -- the
    # equivalent "no THIRD, stray, unaccounted-for net tie exists anywhere" property is
    # now checked once, holistically, after FAN_RTN's own bridge is identified below
    # (see "Exactly two net ties on the whole board, nothing else").
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
    # PANEL-INSTRUMENTATION TASK (2026-08-15): each diode's own RAW-side net is now
    # "*_FUSED", not "*_RAW" -- main-input fusing (F2/F3/F4, checked in its own block
    # below) sits between the M12 connector's own raw pin and each diode's anode/cathode,
    # so the diode's own protection now starts one hop later than it used to. D1/D2/D3
    # themselves keep their EXISTING references (constraint 3) -- only the label on their
    # already-existing raw-side pin moved.
    d1_k = [n for n in nets["+12V"] if n.ref == "D1" and n.pin == "1"]
    d1_a = [n for n in nets["P12_FUSED"] if n.ref == "D1" and n.pin == "2"]
    check(len(d1_k) == 1, f"D1 cathode (pin 1) expected on +12V, not found: {nets['+12V']}")
    check(len(d1_a) == 1, f"D1 anode (pin 2) expected on P12_FUSED, not found: {nets['P12_FUSED']}")
    d2_a = [n for n in nets["-12V"] if n.ref == "D2" and n.pin == "2"]
    d2_k = [n for n in nets["N12_FUSED"] if n.ref == "D2" and n.pin == "1"]
    check(len(d2_a) == 1, f"D2 anode (pin 2) expected on -12V, not found: {nets['-12V']}")
    check(len(d2_k) == 1, f"D2 cathode (pin 1) expected on N12_FUSED, not found: {nets['N12_FUSED']}")
    d3_k = [n for n in nets["+5V"] if n.ref == "D3" and n.pin == "1"]
    d3_a = [n for n in nets["P5_FUSED"] if n.ref == "D3" and n.pin == "2"]
    check(len(d3_k) == 1, f"D3 cathode (pin 1) expected on +5V, not found: {nets['+5V']}")
    check(len(d3_a) == 1, f"D3 anode (pin 2) expected on P5_FUSED, not found: {nets['P5_FUSED']}")
    summary.append(
        "Reverse-polarity diodes correctly oriented: D1 anode->P12_FUSED/cathode->+12V "
        "(source->load), D2 anode->-12V/cathode->N12_FUSED (load->source, mirrored -- see "
        "_place_inlet()'s docstring for the derivation), D3 anode->P5_FUSED/cathode->+5V "
        "(source->load, same orientation as D1 -- fix round 1). Raw-side nets renamed "
        "*_FUSED (panel-instrumentation task) -- see the main-input-fusing check below."
    )

    # --- MAIN INPUT FUSING (panel-instrumentation task, 2026-08-15, spec Sec.9.8 item 5):
    # "the fans already have a polyfuse; these did not". One Littelfuse 1206L050/15YR per
    # rail, bridging the M12 connector's own raw pin to that rail's own diode-protected
    # node -- same "joined by reference at EXACTLY one component, and that component
    # contributes exactly one pin to each side" construction as every other bridging
    # check in this file (F1/FAN_12V above, the two net-tie checks). ---
    # F4 IS DELIBERATELY A DIFFERENT PART -- finding M7, 2026-08-16. The +5 V rail
    # carries 392/495 mA once finding F1 raised the optocoupler drive, and a 1206L050
    # holds only ~0.44 A at this chassis's 35 C ambient: it would trip in NORMAL
    # operation. 1206L110-C holds 1.10 A / ~0.97 A derated. Its V_max is 6 Vdc, not 15,
    # which is exactly why it suits +5 V and CANNOT be reused on F2/F3 -- the expected
    # value is therefore per-fuse here rather than one shared constant, so a future
    # "tidy-up" back to a single value fails this check instead of passing it.
    main_fuse_specs = [
        ("P12_RAW", "P12_FUSED", "F2", "1206L050/15YR"),
        ("N12_RAW", "N12_FUSED", "F3", "1206L050/15YR"),
        ("P5_RAW", "P5_FUSED", "F4", "1206L110-C"),
    ]
    for raw_net, fused_net, expected_ref, expected_value in main_fuse_specs:
        check(
            _pins_on(nets, raw_net).isdisjoint(_pins_on(nets, fused_net)),
            f"{raw_net} and {fused_net} share a PIN directly -- they are not distinct nets (the fuse is bypassed)",
        )
        bridging = _refs_on(nets, raw_net) & _refs_on(nets, fused_net)
        check(
            len(bridging) == 1,
            f"{raw_net}/{fused_net} should be joined by reference at EXACTLY one "
            f"component (the main-input fuse), found {len(bridging)}: {bridging}",
        )
        fref = next(iter(bridging))
        check(fref == expected_ref, f"{raw_net}/{fused_net}'s own bridging fuse is {fref!r}, expected {expected_ref!r}")
        check(fref.startswith("F"), f"the {raw_net}/{fused_net} bridging component {fref!r} is not an F-prefixed fuse reference")
        check(
            values.get(fref) == expected_value,
            f"{fref}: expected Value {expected_value!r} (main-input fuse), found {values.get(fref)!r}",
        )
        raw_pins = [n.pin for n in nets[raw_net] if n.ref == fref]
        fused_pins = [n.pin for n in nets[fused_net] if n.ref == fref]
        check(
            len(raw_pins) == 1 and len(fused_pins) == 1,
            f"{fref} should contribute exactly 1 pin to each of {raw_net}/{fused_net}, "
            f"found {len(raw_pins)}/{len(fused_pins)}",
        )
    summary.append(
        "Main input fusing confirmed: F2 (P12_RAW<->P12_FUSED, 1206L050/15YR), F3 "
        "(N12_RAW<->N12_FUSED, 1206L050/15YR), F4 (P5_RAW<->P5_FUSED, 1206L110-C per "
        "finding M7), each the sole bridge."
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

    # --- Fan headers (spec Sec.9.5, added after Task 7's original commit). Four
    # Conn_01x03 headers, each pin 1 -> FAN_RTN, pin 2 -> FAN_12V, pin 3 (tach) a
    # deliberate no-connect. Identified as "refs present on BOTH FAN_12V and FAN_RTN"
    # rather than hard-coded J-numbers, so this check does not need editing if a future
    # regeneration ever shifts the out-of-band J52-J55 minting to different numbers. ---
    fan12v_refs = _refs_on(nets, "FAN_12V")
    fanrtn_refs = _refs_on(nets, "FAN_RTN")
    # J-prefixed AND present on both nets -- NOT just "present on both nets": C147/C148
    # (the local bulk capacitance, RAIL_BYPASS_EXPECTED's own new entry below) are ALSO
    # wired straight across FAN_12V<->FAN_RTN, same "filter by reference prefix among a
    # shared rail pair" discipline _rail_bypass_cap_count() already uses (its own
    # `n.ref.startswith("C")`) to tell a bypass cap apart from anything else that
    # legitimately touches the same two nets.
    fan_header_refs = sorted(
        {r for r in (fan12v_refs & fanrtn_refs) if r.startswith("J")},
        key=lambda r: int(r[1:]),
    )
    check(
        len(fan_header_refs) == 4,
        f"expected exactly 4 fan headers (a J-prefixed reference present on BOTH "
        f"FAN_12V and FAN_RTN), found {len(fan_header_refs)}: {fan_header_refs}",
    )
    for ref in fan_header_refs:
        pin2_on_12v = [n for n in nets["FAN_12V"] if n.ref == ref and n.pin == "2"]
        pin1_on_rtn = [n for n in nets["FAN_RTN"] if n.ref == ref and n.pin == "1"]
        check(len(pin2_on_12v) == 1, f"{ref}: pin 2 expected on FAN_12V, not found there: {nets['FAN_12V']}")
        check(len(pin1_on_rtn) == 1, f"{ref}: pin 1 expected on FAN_RTN, not found there: {nets['FAN_RTN']}")
        tach_on_12v = [n for n in nets["FAN_12V"] if n.ref == ref and n.pin == "3"]
        tach_on_rtn = [n for n in nets["FAN_RTN"] if n.ref == ref and n.pin == "3"]
        check(
            not tach_on_12v and not tach_on_rtn,
            f"{ref}: pin 3 (tach) is wired to FAN_12V or FAN_RTN -- it must stay a bare "
            f"no-connect (present on the footprint, deliberately unwired; spec Sec.9.5: "
            f"GPIO is full at 26 of 28, so power-only was chosen deliberately)",
        )
    summary.append(
        f"4 fan headers confirmed ({fan_header_refs}), each pin 2->FAN_12V / pin "
        f"1->FAN_RTN, pin 3 (tach) genuinely unwired to either."
    )

    # --- No fan-header pin (any of the 3, on any of the 4 headers) is wired directly to
    # AGND or DGND -- the literal failure mode of a stray or "helpfully corrected" net
    # name, checked independently of the star-point check below (which only proves
    # FAN_RTN's own AGGREGATE connectivity is right, not that no INDIVIDUAL header pin
    # was mislabelled onto AGND/DGND directly). ---
    fan_pins_on_agnd = [n for n in nets["AGND"] if n.ref in fan_header_refs]
    fan_pins_on_dgnd = [n for n in nets["DGND"] if n.ref in fan_header_refs]
    check(
        not fan_pins_on_agnd and not fan_pins_on_dgnd,
        f"fan header pin(s) wired DIRECTLY to AGND/DGND instead of FAN_RTN -- AGND: "
        f"{fan_pins_on_agnd}, DGND: {fan_pins_on_dgnd}",
    )
    summary.append("No fan header pin touches AGND or DGND directly.")

    # --- FAN_RTN reaches the rest of the ground system ONLY through NT2, exactly one
    # net tie -- same disjoint-pins + exactly-one-bridging-reference construction as the
    # AGND/DGND star-point check above, applied to the new pair. FAN_RTN is tied to
    # DGND specifically, not AGND directly (gen_breakout_power.py's own
    # _place_fan_headers() docstring has the reasoning: it mirrors where U2's primary
    # side and +5V's own entry caps already put their own switching/entry-point noise),
    # so this checks FAN_RTN against DGND the way AGND was checked against DGND above,
    # PLUS an explicit disjointness check against AGND too -- FAN_RTN must never gain a
    # second, redundant, DIRECT path to AGND; it may only reach AGND transitively,
    # through NT2 then NT1. ---
    check(
        _pins_on(nets, "FAN_RTN").isdisjoint(_pins_on(nets, "DGND")),
        "FAN_RTN and DGND share a PIN directly -- they are not distinct nets",
    )
    check(
        _pins_on(nets, "FAN_RTN").isdisjoint(_pins_on(nets, "AGND")),
        "FAN_RTN and AGND share a PIN directly -- FAN_RTN must reach AGND only "
        "transitively, through NT2 then NT1, never by a direct pin",
    )
    fan_rtn_refs = _refs_on(nets, "FAN_RTN")
    fan_bridging_refs = fan_rtn_refs & dgnd_refs
    check(
        len(fan_bridging_refs) == 1,
        f"FAN_RTN and DGND should be joined by reference at EXACTLY one component "
        f"(NT2, the fan return's own net tie), found {len(fan_bridging_refs)}: "
        f"{fan_bridging_refs}",
    )
    fan_bridge_ref = next(iter(fan_bridging_refs))
    check(
        fan_bridge_ref.startswith("NT") and fan_bridge_ref != bridge_ref,
        f"the FAN_RTN/DGND bridging component {fan_bridge_ref!r} is not a SECOND, "
        f"distinct net tie from the AGND/DGND star point's own {bridge_ref!r}",
    )
    fan_bridge_rtn_pins = [n.pin for n in nets["FAN_RTN"] if n.ref == fan_bridge_ref]
    fan_bridge_dgnd_pins = [n.pin for n in nets["DGND"] if n.ref == fan_bridge_ref]
    check(
        len(fan_bridge_rtn_pins) == 1 and len(fan_bridge_dgnd_pins) == 1,
        f"fan-return net tie {fan_bridge_ref} should contribute exactly 1 pin to each "
        f"of FAN_RTN/DGND, found {len(fan_bridge_rtn_pins)}/{len(fan_bridge_dgnd_pins)}",
    )
    summary.append(
        f"FAN_RTN is distinct from both AGND and DGND (zero shared pins with either), "
        f"joined to DGND at exactly one net tie ({fan_bridge_ref}), reaching AGND only "
        f"transitively through that tie and NT1 -- never a direct pin."
    )

    # --- CHASSIS EARTH STUD (panel-instrumentation task, 2026-08-15, spec Sec.9.8 item
    # 1): "a mounting point the cage-to-rack bond can land on" (spec Sec.5.6). J56
    # carries a new net, CHASSIS_GND, joined to DGND at exactly one net tie (NT3) --
    # IDENTICAL construction to the FAN_RTN/DGND check above, applied to the third tie. ---
    check(
        _pins_on(nets, "CHASSIS_GND").isdisjoint(_pins_on(nets, "DGND")),
        "CHASSIS_GND and DGND share a PIN directly -- they are not distinct nets",
    )
    check(
        _pins_on(nets, "CHASSIS_GND").isdisjoint(_pins_on(nets, "AGND")),
        "CHASSIS_GND and AGND share a PIN directly -- CHASSIS_GND must reach AGND only "
        "transitively, through NT3 then NT1, never by a direct pin",
    )
    chassis_refs = _refs_on(nets, "CHASSIS_GND")
    check(
        "J56" in chassis_refs,
        f"J56 (the chassis earth stud) has no pin on CHASSIS_GND: {nets['CHASSIS_GND']}",
    )
    chassis_bridging_refs = chassis_refs & dgnd_refs
    check(
        len(chassis_bridging_refs) == 1,
        f"CHASSIS_GND and DGND should be joined by reference at EXACTLY one component "
        f"(NT3, the earth stud's own net tie), found {len(chassis_bridging_refs)}: "
        f"{chassis_bridging_refs}",
    )
    chassis_bridge_ref = next(iter(chassis_bridging_refs))
    check(
        chassis_bridge_ref.startswith("NT") and chassis_bridge_ref not in (bridge_ref, fan_bridge_ref),
        f"the CHASSIS_GND/DGND bridging component {chassis_bridge_ref!r} is not a THIRD, "
        f"distinct net tie from the AGND/DGND star point ({bridge_ref!r}) and the fan "
        f"return ({fan_bridge_ref!r})",
    )
    summary.append(
        f"Chassis earth stud confirmed: J56 on CHASSIS_GND, joined to DGND at exactly "
        f"one net tie ({chassis_bridge_ref}), reaching AGND only transitively."
    )

    # --- Exactly three net ties on the whole board, nothing else: the generalised form
    # of the single-net-tie uniqueness check the AGND/DGND star point used to make alone
    # (see the NOTE left at that check, above). Now that FAN_RTN's own bridge
    # (fan_bridge_ref) AND CHASSIS_GND's own bridge (chassis_bridge_ref) are both known
    # too, this confirms none of the three known ties has a stray FOURTH sibling
    # anywhere on the board -- a future edit that adds an unaccounted-for net tie
    # (accidentally or otherwise) is caught here even though each individual "exactly one
    # bridging reference" check above only looks at its own net pair in isolation. ---
    # FOUR as of finding F5 (2026-08-16), not three. NT4 lives on opto-intan.kicad_sch and
    # joins ISO_5V_RTN to INTAN_GND -- the F5 isolated converter's own secondary return,
    # tied at one point rather than merged, for the same reason NT2 keeps FAN_RTN distinct
    # from DGND. It is named here rather than pattern-matched so this stays an EXACT set
    # comparison: a genuinely stray FIFTH tie is still caught, and a missing known one too.
    ISO5V_RTN_TIE = "NT4"
    nettie_refs = {n.ref for name in nets for n in nets[name] if n.ref.startswith("NT")}
    expected_ties = {bridge_ref, fan_bridge_ref, chassis_bridge_ref, ISO5V_RTN_TIE}
    check(
        nettie_refs == expected_ties,
        f"unexpected NetTie_2 reference(s) somewhere on the board -- expected exactly "
        f"the four known net ties {sorted(expected_ties)} (AGND/DGND star point, "
        f"FAN_RTN/DGND, CHASSIS_GND/DGND, ISO_5V_RTN/INTAN_GND), found "
        f"{sorted(nettie_refs)} -- either a stray/unaccounted net tie exists, or one of "
        f"the four known ones is missing",
    )
    check(
        _refs_on(nets, "ISO_5V_RTN") & _refs_on(nets, "INTAN_GND") == {ISO5V_RTN_TIE},
        f"{ISO5V_RTN_TIE} should be the sole join between ISO_5V_RTN and INTAN_GND",
    )
    summary.append(f"Exactly four net ties on the whole board, nothing else: {sorted(nettie_refs)}.")

    # --- POWER-GOOD LEDS (panel-instrumentation task, 2026-08-15, spec Sec.9.8 item 2):
    # one per rail (+12V, -12V, +5V) -- "a failed one is otherwise invisible until the
    # data is wrong". Each is a series resistor + LED between the rail and its own local
    # ground reference; -12V's own pair is checked with the MIRRORED orientation
    # (anode/resistor on AGND, cathode on -12V) -- see gen_breakout_power.py's own
    # _place_power_good_leds() docstring for why. ---
    pgood_specs = [
        ("D40", "R192", "+12V", "AGND", False),
        ("D41", "R193", "AGND", "-12V", True),   # mirrored -- see docstring above
        ("D42", "R194", "+5V", "DGND", False),
    ]
    for led_ref, r_ref, plus_net, minus_net, _mirrored in pgood_specs:
        check(led_ref in values, f"{led_ref} (power-good LED) not found in the netlist at all")
        check(values.get(led_ref) == "LED", f"{led_ref}: expected Value 'LED', found {values.get(led_ref)!r}")
        cathode_on_minus = [n for n in nets[minus_net] if n.ref == led_ref and n.pin == "1"]
        check(
            len(cathode_on_minus) == 1,
            f"{led_ref}: cathode (pin 1) expected on {minus_net!r}, not found there: {nets[minus_net]}",
        )
        r_on_plus = [n for n in nets[plus_net] if n.ref == r_ref]
        check(len(r_on_plus) == 1, f"{r_ref}: expected exactly 1 pin on {plus_net!r}, found {r_on_plus}")
        # The resistor and LED must actually be in series with EACH OTHER (share a node
        # that is neither plus_net nor minus_net), not just each independently touch the
        # rail/ground pair -- otherwise a "resistor straight across the rail, LED
        # straight across the rail" (two independent, non-series branches) would pass
        # the two checks above just as well as the intended series pair.
        r_other_pin = next(p for p in ("1", "2") if not any(n.ref == r_ref and n.pin == p for n in nets[plus_net]))
        r_node = next(name for name, ns in nets.items() if any(n.ref == r_ref and n.pin == r_other_pin for n in ns))
        anode_on_r_node = [n for n in nets[r_node] if n.ref == led_ref and n.pin == "2"]
        check(
            len(anode_on_r_node) == 1,
            f"{led_ref}/{r_ref}: not genuinely in series -- {r_ref}'s own non-{plus_net} "
            f"pin lands on {r_node!r}, but {led_ref}'s own anode (pin 2) is not there: {nets[r_node]}",
        )
    summary.append(
        "Power-good LEDs confirmed: D40/R192 (+12V->AGND), D41/R193 (AGND->-12V, "
        "mirrored), D42/R194 (+5V->DGND) -- each a genuine series resistor+LED pair."
    )

    # --- RAIL TEST POINTS (panel-instrumentation task, 2026-08-15, spec Sec.9.8 item 4):
    # one per voltage rail (+12V, -12V, +5V, +3V3, ISO_P12, ISO_N12) plus one on
    # INTAN_GND -- the isolated rails' own reference, without which ISO_P12/ISO_N12
    # cannot be measured at all (see gen_breakout_power.py's own
    # _place_rail_test_points() docstring). ---
    tp_rails = ["+12V", "-12V", "+5V", "+3V3", "ISO_P12", "ISO_N12", "INTAN_GND"]
    tp_refs_found = {}
    for rail in tp_rails:
        tp_on_rail = sorted({n.ref for n in nets[rail] if n.ref.startswith("TP")})
        check(len(tp_on_rail) == 1, f"{rail}: expected exactly 1 test point, found {tp_on_rail}")
        tp_refs_found[rail] = tp_on_rail[0]
    check(
        len(set(tp_refs_found.values())) == len(tp_rails),
        f"the same test point reference is shared across more than one rail: {tp_refs_found}",
    )
    summary.append(f"Rail test points confirmed, one per rail (including INTAN_GND, the isolated reference): {tp_refs_found}.")

    # --- FAN_12V reaches +12V ONLY through F1 (the polyfuse) -- same construction again,
    # applied to the fan supply side. A silently-omitted or silently-bridged fuse would
    # still produce a net literally named "FAN_12V" (ERC-clean, netlist-plausible) while
    # providing NO overcurrent protection at all, or none at all separating it from
    # +12V -- the same "plausible but wrong" failure class the reverse-polarity-diode
    # checks above exist to catch, just for the fan feed's own protective element. ---
    # FINDING F7, 2026-08-16 -- THE FAN BRANCH NOW TAPS P12_RAW, NOT +12V, so what this
    # block asserts changed shape. The chain is:
    #
    #     J1.1 -> P12_RAW -+-> [F2] -> P12_FUSED -> [D1] -> +12V      (main)
    #                      `-> [F1] -> FAN_12V_RAW -> [D44] -> FAN_12V  (fans)
    #
    # It used to be +12V -> [F1] -> FAN_12V, which put F1 in SERIES with F2 at identical
    # ratings: a fan fault at 0.9 A is below F1's trip but over F2's hold, so F2 could
    # open first and kill the analog rails to protect the fans. The assertion that FAN_12V
    # reaches +12V through exactly one component is therefore now the OPPOSITE of correct
    # -- the two must share no component at all.
    check(
        _pins_on(nets, "FAN_12V").isdisjoint(_pins_on(nets, "+12V")),
        "FAN_12V and +12V share a PIN directly -- they are not distinct nets",
    )
    stray = fan12v_refs & _refs_on(nets, "+12V")
    check(
        not stray,
        f"FAN_12V and +12V are joined by component(s) {stray} -- finding F7 requires them "
        f"to be PARALLEL branches off P12_RAW, sharing no series element, so that a fan "
        f"fault cannot open the main +12 V fuse. Any shared component puts the two fuses "
        f"back in series at identical ratings, which is no selectivity at all.",
    )
    # Both branch fuses hang off the SAME inlet net -- the positive half of the same claim.
    p12_raw_fuses = sorted({r for r in _refs_on(nets, "P12_RAW") if r.startswith("F") and not r.startswith("FB")})
    check(
        p12_raw_fuses == ["F1", "F2"],
        f"P12_RAW should carry exactly the two parallel branch fuses F1 (fans) and F2 "
        f"(main +12 V), found {p12_raw_fuses}",
    )
    # F1: P12_RAW <-> FAN_12V_RAW, sole bridge, correct part.
    fan_fuse_bridging = _refs_on(nets, "P12_RAW") & _refs_on(nets, "FAN_12V_RAW")
    check(
        len(fan_fuse_bridging) == 1,
        f"P12_RAW/FAN_12V_RAW should be joined at EXACTLY one component (F1, the fan-feed "
        f"polyfuse), found {len(fan_fuse_bridging)}: {fan_fuse_bridging}",
    )
    fuse_ref = next(iter(fan_fuse_bridging))
    check(fuse_ref == "F1", f"the P12_RAW/FAN_12V_RAW bridging component is {fuse_ref!r}, expected 'F1'")
    check(
        values.get(fuse_ref) == "1206L050/15YR",
        f"{fuse_ref}: expected Value '1206L050/15YR' (the fan-feed polyfuse), found "
        f"{values.get(fuse_ref)!r}. 0.50 A is the LARGEST 1206L hold current available to "
        f"a 12 V rail -- every part at 0.75 A and above is rated 6 Vdc -- so finding M7's "
        f"'0.75 A recommended' cannot be satisfied in this series and this value stands.",
    )
    # D44: FAN_12V_RAW <-> FAN_12V, the fan branch's OWN reverse-polarity Schottky, which
    # tapping upstream of D1 makes necessary. Orientation checked, not assumed: anode on
    # the raw side, cathode on the protected side, exactly like D1/D2/D3.
    fan_diode_bridging = _refs_on(nets, "FAN_12V_RAW") & _refs_on(nets, "FAN_12V")
    check(
        len(fan_diode_bridging) == 1,
        f"FAN_12V_RAW/FAN_12V should be joined at EXACTLY one component (D44, the fan "
        f"branch's reverse-polarity Schottky), found {len(fan_diode_bridging)}: "
        f"{fan_diode_bridging}",
    )
    fan_diode_ref = next(iter(fan_diode_bridging))
    check(fan_diode_ref == "D44", f"the FAN_12V_RAW/FAN_12V bridging component is {fan_diode_ref!r}, expected 'D44'")
    check(
        values.get(fan_diode_ref) == "SS14",
        f"{fan_diode_ref}: expected Value 'SS14' (same part as D1/D2/D3), found {values.get(fan_diode_ref)!r}",
    )
    anode_pins = [n.pin for n in nets["FAN_12V_RAW"] if n.ref == fan_diode_ref]
    cathode_pins = [n.pin for n in nets["FAN_12V"] if n.ref == fan_diode_ref]
    check(
        anode_pins == ["2"] and cathode_pins == ["1"],
        f"{fan_diode_ref} is oriented backwards: expected anode (pin 2) on FAN_12V_RAW and "
        f"cathode (pin 1) on FAN_12V, found {anode_pins} / {cathode_pins}. Reversed, it "
        f"blocks the fans entirely rather than protecting them.",
    )
    summary.append(
        f"Fan branch confirmed PARALLEL to the main +12 V branch (finding F7): P12_RAW "
        f"-[{fuse_ref}, 1206L050/15YR]-> FAN_12V_RAW -[{fan_diode_ref}, SS14, anode on the "
        f"raw side]-> FAN_12V, sharing no component with +12V, and both branch fuses "
        f"({p12_raw_fuses}) hanging off P12_RAW."
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
        "F1": "1206L050/15YR",  # the fan-feed polyfuse (spec Sec.9.5) -- Littelfuse
        # 1206L050/15YR, 500mA hold / 1A trip / 15V max; see gen_breakout_power.py's
        # own _place_fan_headers() docstring for the hold-current sizing derivation.
        "F2": "1206L050/15YR", "F3": "1206L050/15YR", "F4": "1206L110-C",
        # ^ main-input fuses (panel-instrumentation task, 2026-08-15, spec Sec.9.8 item
        # 5). F2/F3 are the SAME real part as F1. F4 is NOT, as of finding M7
        # (2026-08-16): the +5 V rail carries 392/495 mA once finding F1 raised the
        # optocoupler drive, and a 1206L050 holds only ~0.44 A at 35 C -- it would trip
        # in normal operation. 1206L110-C holds 1.10 A / ~0.97 A derated. Its V_max is
        # 6 Vdc rather than 15, which is why it fits +5 V and cannot be reused on the
        # 12 V rails; see hardware/datasheet-params.toml. The values are listed per-fuse
        # rather than shared so a future "tidy-up" back to one part fails this check.
        "D40": "LED", "D41": "LED", "D42": "LED",  # power-good LEDs (spec Sec.9.8 item 2)
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
    all_named = CONTRACT_NETS + [
        "P12_RAW", "N12_RAW", "P5_RAW", "FAN_12V", "FAN_RTN",
        "P12_FUSED", "N12_FUSED", "P5_FUSED", "CHASSIS_GND",  # panel-instrumentation task
    ]
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
    # unintended net tie or jumper added somewhere else on the sheet). Phantom ref is
    # "DBG96" (this file's own DBG9x synthetic-self-test-node convention, see DBG97/98/99
    # below), NOT "NT2" -- the fan headers (spec Sec.9.5) added a REAL NT2 to this design,
    # so a same-named phantom here would silently collide with it instead of testing a
    # clean, unambiguous double-bridge scenario.
    double_bridged = copy.deepcopy(good_nets)
    extra = Node(ref="DBG96", pin="1", pinfunction="", pintype="passive")
    extra2 = Node(ref="DBG96", pin="2", pinfunction="", pintype="passive")
    double_bridged["AGND"] = double_bridged["AGND"] + [extra]
    double_bridged["DGND"] = double_bridged["DGND"] + [extra2]
    msg = _assert_fails(double_bridged, good_values, "EXACTLY one component", "second AGND/DGND bridge")
    results.append(f"Second AGND/DGND bridge (extra DBG96, not the real star point): caught -- {msg}")

    # Isolation barrier: short ISO_P12 to +12V via one shared pin (simulating a routing
    # mistake that ties the isolated and non-isolated domains together).
    iso_shorted = copy.deepcopy(good_nets)
    phantom2 = Node(ref="DBG98", pin="1", pinfunction="", pintype="passive")
    iso_shorted["ISO_P12"] = iso_shorted["ISO_P12"] + [phantom2]
    iso_shorted["+12V"] = iso_shorted["+12V"] + [phantom2]
    msg = _assert_fails(iso_shorted, good_values, "isolation barrier violated", "ISO_P12/+12V short")
    results.append(f"Isolation barrier short (ISO_P12 tied to +12V via one shared pin): caught -- {msg}")

    # Reverse-polarity diode installed backwards: swap D1's two pins between +12V and
    # P12_FUSED (panel-instrumentation task renamed the raw-side net from P12_RAW --
    # same net MEMBERSHIP as before, just each pin now on the OTHER net -- exactly what
    # a physically-reversed diode looks like in the netlist).
    d1_reversed = copy.deepcopy(good_nets)
    d1_in_12v = next(n for n in d1_reversed["+12V"] if n.ref == "D1")
    d1_in_raw = next(n for n in d1_reversed["P12_FUSED"] if n.ref == "D1")
    d1_reversed["+12V"] = [n for n in d1_reversed["+12V"] if n.ref != "D1"] + [
        Node(ref="D1", pin=d1_in_raw.pin, pinfunction=d1_in_raw.pinfunction, pintype=d1_in_raw.pintype)
    ]
    d1_reversed["P12_FUSED"] = [n for n in d1_reversed["P12_FUSED"] if n.ref != "D1"] + [
        Node(ref="D1", pin=d1_in_12v.pin, pinfunction=d1_in_12v.pinfunction, pintype=d1_in_12v.pintype)
    ]
    msg = _assert_fails(d1_reversed, good_values, "D1 cathode", "D1 installed backwards")
    results.append(f"D1 (reverse-polarity diode) installed backwards: caught -- {msg}")

    # D3 (+5V, fix round 1) installed backwards -- same construction as D1's own test
    # above, mirrored onto the new diode. P5_FUSED, not P5_RAW -- see D1's own comment.
    d3_reversed = copy.deepcopy(good_nets)
    d3_in_5v = next(n for n in d3_reversed["+5V"] if n.ref == "D3")
    d3_in_raw = next(n for n in d3_reversed["P5_FUSED"] if n.ref == "D3")
    d3_reversed["+5V"] = [n for n in d3_reversed["+5V"] if n.ref != "D3"] + [
        Node(ref="D3", pin=d3_in_raw.pin, pinfunction=d3_in_raw.pinfunction, pintype=d3_in_raw.pintype)
    ]
    d3_reversed["P5_FUSED"] = [n for n in d3_reversed["P5_FUSED"] if n.ref != "D3"] + [
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

    # --- Fan-header negative controls (spec Sec.9.5) -- one per new check() family
    # added above, same mutate-the-real-parsed-netlist discipline as every test above. ---
    fan_header_refs_good = sorted(
        {r for r in ({n.ref for n in good_nets["FAN_12V"]} & {n.ref for n in good_nets["FAN_RTN"]}) if r.startswith("J")},
        key=lambda r: int(r[1:]),
    )
    check(len(fan_header_refs_good) == 4, "self-test setup failed: expected 4 real fan headers in good_nets")
    victim_hdr = fan_header_refs_good[0]

    # Fan pin wired directly to AGND -- the literal "someone relabels a stray pin"
    # defect the dedicated no-direct-AGND/DGND check exists to catch.
    fan_to_agnd = copy.deepcopy(good_nets)
    phantom_fan_pin = Node(ref=victim_hdr, pin="1", pinfunction="", pintype="passive")
    fan_to_agnd["AGND"] = fan_to_agnd["AGND"] + [phantom_fan_pin]
    msg = _assert_fails(fan_to_agnd, good_values, "wired DIRECTLY to AGND/DGND", f"{victim_hdr} pin 1 wired to AGND")
    results.append(f"Fan header pin wired directly to AGND ({victim_hdr} pin 1): caught -- {msg}")

    # FAN_RTN/DGND direct short (phantom shared pin) -- same construction as the AGND/
    # DGND direct-short test above, mirrored onto the new pair.
    fan_rtn_shorted = copy.deepcopy(good_nets)
    phantom_fan1 = Node(ref="DBG95", pin="1", pinfunction="", pintype="passive")
    fan_rtn_shorted["FAN_RTN"] = fan_rtn_shorted["FAN_RTN"] + [phantom_fan1]
    fan_rtn_shorted["DGND"] = fan_rtn_shorted["DGND"] + [phantom_fan1]
    msg = _assert_fails(fan_rtn_shorted, good_values, "FAN_RTN and DGND share a PIN directly", "FAN_RTN/DGND direct short")
    results.append(f"FAN_RTN/DGND direct short (phantom shared pin): caught -- {msg}")

    # Second FAN_RTN/DGND bridge (extra net tie or jumper) -- same construction as the
    # AGND/DGND double-bridge test above, mirrored.
    fan_rtn_double = copy.deepcopy(good_nets)
    extra3 = Node(ref="DBG94", pin="1", pinfunction="", pintype="passive")
    extra4 = Node(ref="DBG94", pin="2", pinfunction="", pintype="passive")
    fan_rtn_double["FAN_RTN"] = fan_rtn_double["FAN_RTN"] + [extra3]
    fan_rtn_double["DGND"] = fan_rtn_double["DGND"] + [extra4]
    msg = _assert_fails(fan_rtn_double, good_values, "EXACTLY one component", "second FAN_RTN/DGND bridge")
    results.append(f"Second FAN_RTN/DGND bridge (extra DBG94, not the real NT2): caught -- {msg}")

    # FAN_12V/+12V direct short (phantom shared pin -- bypassing F1 entirely).
    fan12v_shorted = copy.deepcopy(good_nets)
    phantom_fan2 = Node(ref="DBG93", pin="1", pinfunction="", pintype="passive")
    fan12v_shorted["FAN_12V"] = fan12v_shorted["FAN_12V"] + [phantom_fan2]
    fan12v_shorted["+12V"] = fan12v_shorted["+12V"] + [phantom_fan2]
    msg = _assert_fails(fan12v_shorted, good_values, "FAN_12V and +12V share a PIN directly", "FAN_12V/+12V direct short")
    results.append(f"FAN_12V/+12V direct short (phantom shared pin): caught -- {msg}")

    # ANY FAN_12V/+12V bridge -- finding F7 (2026-08-16). This control used to add a
    # SECOND bridge around F1 and expect an "EXACTLY one component" complaint, because
    # the fan branch legitimately hung off +12V through F1 alone. Since F7 the two are
    # parallel branches off P12_RAW and must share NOTHING, so a single bridge is now
    # the defect -- and it is the exact regression F7 exists to prevent: re-joining the
    # fan feed to +12V puts F1 back in series with F2 at identical ratings.
    fan12v_double = copy.deepcopy(good_nets)
    extra5 = Node(ref="DBG92", pin="1", pinfunction="", pintype="passive")
    extra6 = Node(ref="DBG92", pin="2", pinfunction="", pintype="passive")
    fan12v_double["FAN_12V"] = fan12v_double["FAN_12V"] + [extra5]
    fan12v_double["+12V"] = fan12v_double["+12V"] + [extra6]
    msg = _assert_fails(fan12v_double, good_values, "PARALLEL branches off P12_RAW", "FAN_12V/+12V rejoined")
    results.append(f"Fan branch rejoined to +12V (extra DBG92 -- F1 back in series with F2, finding F7): caught -- {msg}")

    # The fan branch's own reverse-polarity diode reversed. Tapping P12_RAW puts the fan
    # feed upstream of D1, so D44 is the only thing protecting it -- and a backwards
    # Schottky blocks the fans entirely rather than protecting them.
    d44_reversed = copy.deepcopy(good_nets)
    d44_raw = [n for n in d44_reversed["FAN_12V_RAW"] if n.ref == "D44"]
    d44_out = [n for n in d44_reversed["FAN_12V"] if n.ref == "D44"]
    d44_reversed["FAN_12V_RAW"] = [n for n in d44_reversed["FAN_12V_RAW"] if n.ref != "D44"] + d44_out
    d44_reversed["FAN_12V"] = [n for n in d44_reversed["FAN_12V"] if n.ref != "D44"] + d44_raw
    msg = _assert_fails(d44_reversed, good_values, "oriented backwards", "D44 reversed")
    results.append(f"Fan reverse-polarity diode D44 reversed (blocks the fans entirely): caught -- {msg}")

    # Polyfuse value drift: F1's own Value edited/lost, topology unchanged -- exactly the
    # failure class the "right part in the right role" style check exists for.
    fuse_drifted = dict(good_values)
    fuse_drifted["F1"] = "Polyfuse"
    msg = _assert_fails(good_nets, fuse_drifted, "expected Value '1206L050/15YR'", "F1 value drift")
    results.append(f"F1 value drift (1206L050/15YR -> generic 'Polyfuse'): caught -- {msg}")

    # Wrong fan header count: strip one real header from BOTH FAN_12V and FAN_RTN
    # (simulating only 3 of 4 headers actually got placed/wired in a future edit).
    missing_hdr = copy.deepcopy(good_nets)
    missing_hdr["FAN_12V"] = [n for n in missing_hdr["FAN_12V"] if n.ref != victim_hdr]
    missing_hdr["FAN_RTN"] = [n for n in missing_hdr["FAN_RTN"] if n.ref != victim_hdr]
    msg = _assert_fails(missing_hdr, good_values, "expected exactly 4 fan headers", f"{victim_hdr} dropped entirely")
    results.append(f"Fan header count regression ({victim_hdr} dropped from both FAN_12V/FAN_RTN): caught -- {msg}")

    # Stray FOURTH net tie (panel-instrumentation task added a third, real one -- NT3,
    # CHASSIS_GND<->DGND -- so this corruption now has to be a fourth to genuinely be
    # "stray"): an NT-prefixed reference appears somewhere none of the three per-pair
    # "exactly one bridging component" checks above would catch on its own. Landed on
    # +3V3 specifically: not a member of any bridging-reference computation above, and
    # not power_out-typed, so this corruption is isolated to the ONE check it exists to
    # exercise rather than tripping an earlier, unrelated one first.
    stray_tie = copy.deepcopy(good_nets)
    phantom_nt = Node(ref="NT99", pin="1", pinfunction="", pintype="passive")
    stray_tie["+3V3"] = stray_tie["+3V3"] + [phantom_nt]
    msg = _assert_fails(stray_tie, good_values, "unexpected NetTie_2 reference", "stray fourth net tie (NT99)")
    results.append(f"Stray fourth net tie (NT99, bridging nothing in particular): caught -- {msg}")

    # --- Panel-instrumentation negative controls (2026-08-15) -- one per new check()
    # family added above, same mutate-the-real-parsed-netlist discipline as every test
    # above. ---

    # Main-input fuse bypassed: P12_RAW shorted straight to P12_FUSED (simulating F2
    # missing or solder-bridged).
    fuse_bypassed = copy.deepcopy(good_nets)
    phantom_f2 = Node(ref="DBG91", pin="1", pinfunction="", pintype="passive")
    fuse_bypassed["P12_RAW"] = fuse_bypassed["P12_RAW"] + [phantom_f2]
    fuse_bypassed["P12_FUSED"] = fuse_bypassed["P12_FUSED"] + [phantom_f2]
    msg = _assert_fails(fuse_bypassed, good_values, "share a PIN directly", "P12_RAW/P12_FUSED direct short (fuse bypassed)")
    results.append(f"Main fuse F2 bypassed (P12_RAW shorted to P12_FUSED): caught -- {msg}")

    # Main-input fuse value drift: F4's own Value edited/lost.
    f4_drifted = dict(good_values)
    f4_drifted["F4"] = "Polyfuse"
    msg = _assert_fails(good_nets, f4_drifted, "expected Value '1206L110-C'", "F4 value drift")
    results.append(f"F4 value drift (1206L110-C -> generic 'Polyfuse'): caught -- {msg}")

    # F4 "tidied" back to F2/F3's part -- the specific regression finding M7 exists to
    # prevent, and the one a single shared expected-value constant would have allowed.
    f4_tidied = dict(good_values)
    f4_tidied["F4"] = "1206L050/15YR"
    msg = _assert_fails(good_nets, f4_tidied, "expected Value '1206L110-C'", "F4 reverted to the 12 V rails' part")
    results.append(f"F4 reverted to F2/F3's 1206L050/15YR (undersized for +5 V, finding M7): caught -- {msg}")

    # Chassis earth stud: CHASSIS_GND shorted directly to DGND (simulating NT3 missing
    # and the stud wired straight to ground instead).
    chassis_shorted = copy.deepcopy(good_nets)
    phantom_ch = Node(ref="DBG90", pin="1", pinfunction="", pintype="passive")
    chassis_shorted["CHASSIS_GND"] = chassis_shorted["CHASSIS_GND"] + [phantom_ch]
    chassis_shorted["DGND"] = chassis_shorted["DGND"] + [phantom_ch]
    msg = _assert_fails(chassis_shorted, good_values, "share a PIN directly", "CHASSIS_GND/DGND direct short")
    results.append(f"Chassis earth CHASSIS_GND/DGND direct short (NT3 bypassed): caught -- {msg}")

    # Power-good LED reversed: D40's own pins swapped between +12V's own resistor node
    # and AGND -- exactly what a physically-reversed LED looks like in the netlist (same
    # construction as the D1-reversed reverse-polarity-diode test above).
    led_reversed = copy.deepcopy(good_nets)
    r192_node = next(name for name, ns in led_reversed.items() if any(n.ref == "R192" and n.pin == "2" for n in ns))
    d40_on_node = next(n for n in led_reversed[r192_node] if n.ref == "D40")
    d40_on_agnd = next(n for n in led_reversed["AGND"] if n.ref == "D40")
    led_reversed[r192_node] = [n for n in led_reversed[r192_node] if n.ref != "D40"] + [
        Node(ref="D40", pin=d40_on_agnd.pin, pinfunction=d40_on_agnd.pinfunction, pintype=d40_on_agnd.pintype)
    ]
    led_reversed["AGND"] = [n for n in led_reversed["AGND"] if n.ref != "D40"] + [
        Node(ref="D40", pin=d40_on_node.pin, pinfunction=d40_on_node.pinfunction, pintype=d40_on_node.pintype)
    ]
    msg = _assert_fails(led_reversed, good_values, "D40", "power-good LED D40 installed backwards")
    results.append(f"Power-good LED D40 installed backwards (anode/cathode swapped): caught -- {msg}")

    # Power-good LED value drift/wrong part: D42's own Value edited.
    led_drifted = dict(good_values)
    led_drifted["D42"] = "LED_RED"
    msg = _assert_fails(good_nets, led_drifted, "D42: expected Value 'LED'", "D42 value drift")
    results.append(f"Power-good LED D42 value drift ('LED' -> 'LED_RED'): caught -- {msg}")

    # Rail test point missing: drop TP3 (+5V's own test point) entirely.
    tp_missing = copy.deepcopy(good_nets)
    tp_missing["+5V"] = [n for n in tp_missing["+5V"] if n.ref != "TP3"]
    msg = _assert_fails(tp_missing, good_values, "expected exactly 1 test point", "TP3 (+5V test point) dropped")
    results.append(f"Rail test point TP3 dropped from +5V: caught -- {msg}")

    # Rail test point shared across two rails: -12V's own TP2 is REPLACED by a copy of
    # TP1 (still exactly one TP-prefixed ref per rail individually, so the per-rail-count
    # check above stays satisfied -- this corruption is isolated to the cross-rail
    # uniqueness check specifically). Both nets are non-isolated, so this does not also
    # trip the unrelated, already-tested isolation-barrier check.
    tp_wrong_rail = copy.deepcopy(good_nets)
    tp1_node = next(n for n in tp_wrong_rail["+12V"] if n.ref == "TP1")
    tp_wrong_rail["-12V"] = [n for n in tp_wrong_rail["-12V"] if n.ref != "TP2"] + [
        Node(ref="TP1", pin=tp1_node.pin, pinfunction=tp1_node.pinfunction, pintype=tp1_node.pintype)
    ]
    msg = _assert_fails(tp_wrong_rail, good_values, "shared across more than one rail", "TP1 reused on -12V instead of its own TP2")
    results.append(f"Rail test point TP1 reused on -12V in place of TP2 (same ref, two rails): caught -- {msg}")

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

    # Shared row-pitch-vs-2-pin-part-span collision guard (constraint 4) -- back-ported
    # here the same way it was back-ported to three other checkers at Task 11 fix round
    # 2 (check_row_pitch_guard.py's own module docstring). power.kicad_sch was one of
    # five checkers that had never had this wired in; it operates on the sheet's raw
    # rendered text, independent of the exported-netlist checks above.
    try:
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(power_sch_text, "power.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(power_sch_text, "power.kicad_sch", min_instances=30)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

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
