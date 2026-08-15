"""Parse hardware/breakout's exported netlist (and, for the two checks the exported netlist
provably cannot answer -- coordinate collisions and the instance-path ancestor chain -- the
raw .kicad_sch sources) and verify opto-intan.kicad_sch's contract nets and topology are
electrically correct -- not just that ERC was silent. Same reasoning every prior checker in
this project gives, restated because it is why THIS file exists too.

THE CENTRAL RISKS this file exists to catch, named explicitly by this task's own brief:

  1. All 6 channels must exist and cross the barrier they should -- 5 outbound
     (DGND -> INTAN_GND) and 1 inbound (INTAN_GND -> DGND, RHS_STIM_OUT). Walked end to
     end per channel, not assumed from net names alone.
  2. The Intan-isolated domain (INTAN_GND, ISO_5V, every *_INTAN net, RHS_STIM_RAW) must
     be PIN-DISJOINT from every non-isolated rail and from every *_BUF/source net, except
     the ACSL-6400/ACSL-6420 packages themselves (the barrier components, straddling by
     design). RHS_STIM_OUT itself is the DGND-side name for the inbound channel's own
     output -- it is NOT part of the isolated set (it lives on the non-isolated side, by
     definition, once past the barrier).
  3. Every pull-up on this sheet is 3.9k -- FIX ROUND 2 (see .superpowers/sdd/
     2026-08-13-breakout-pcb/task-11-report.md's own "Fix round 2" section): the
     original 10k exceeded ACSL-6400/ACSL-6420's own shared datasheet RL-max (4k,
     Broadcom AV02-0235EN's ONE "Recommended Operating Conditions" table for the
     whole ACSL-6xx0 family) -- the same violation fix round 1 already corrected on
     opto-ni.kicad_sch, independently RE-VERIFIED against the datasheet here rather
     than inferred from that sibling sheet. 3.9k is the largest E24 value at or under
     that limit, on whichever rail (ISO_5V or +5V) each channel's own output side
     actually sits -- never 10k (this sheet's own former, now-stale value) and never
     the mule's own 1k.
  4. LED current-setting resistors are 430R, matching opto-ni.kicad_sch's own value (the
     ACSL-6400/6420 family shares one set of electrical specs).
  5. ACSL-6420's own bi-directional (2/2) pin map is correct, per channel -- the SPECIFIC
     risk this task's own pin-level verification found: an all-in-one part cannot serve
     this sheet's own mixed-direction second package, and a bidirectional part's own
     "which two channels go which way" is exactly the kind of fact a permutation could
     silently get wrong while still looking fully connected.
  6. No same-column real 2-pin part may have its own pin-to-pin reach overlap another's --
     the GENERAL form of opto-ni.kicad_sch's own real coordinate-collision defect
     (CH_ROW_DY's own account in gen_breakout_opto_ni.py), checked structurally (from real,
     rendered pin geometry) rather than only after an actual duplicate coordinate appears
     -- see _check_row_pitch_exceeds_2pin_span() below, added fix round 1.

`verify()` below re-derives the full channel/pin contract independently of
gen_breakout_opto_intan.py's own choices -- same "a checker that trusted the generator
would only be checking the generator against itself" discipline every prior checker in this
project already follows.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_opto_intan_netlist.py [path/to/breakout.net]

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
    extract_symbol,
    find_all_instance_paths,
    find_root_uuid,
    find_sheet_instance_path,
    pin_pos,
    unit_pins,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_OPTO_INTAN_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "opto-intan.kicad_sch"
OPTO_INTAN_SHEETFILE = "sheets/opto-intan.kicad_sch"

# ---------------------------------------------------------------------------
# Channel/contract constants -- redefined here from this task's own brief and Broadcom's
# own AV02-0235EN, NEVER imported from the generator.
# ---------------------------------------------------------------------------
ACSL6400_VALUE = "ACSL-6400-00TE"
ACSL6420_VALUE = "ACSL-6420-00TE"
LDO_VALUE = "LD1117S50TR_SOT223"
BAT54S_VALUE = "BAT54S"

ACSL6400_CH_PINS = {
    0: ("1", "2", "14"),
    1: ("3", "4", "13"),
    2: ("5", "6", "12"),
    3: ("7", "8", "11"),
}
ACSL6400_PIN_GND = ["9", "16"]
ACSL6400_PIN_VDD = ["10", "15"]

# ACSL-6420, Broadcom AV02-0235EN Figure 6/12 (both agree exactly) -- see
# gen_wl_sync_lib.py's own "ACSL-6420" comment. channel -> (anode, cathode, vo).
ACSL6420_DIR_1TO2 = {1: ("5", "6", "11"), 2: ("7", "8", "10")}
ACSL6420_DIR_2TO1 = {3: ("14", "13", "3"), 4: ("16", "15", "2")}
ACSL6420_PIN_GND1 = "1"
ACSL6420_PIN_VDD1 = "4"
ACSL6420_PIN_GND2 = "9"
ACSL6420_PIN_VDD2 = "12"

LED_R_OHMS = "430"
PULLUP_OHMS = "3.9k"  # fix round 2 -- corrected from 10k, which exceeded ACSL-6400/
# ACSL-6420's own shared datasheet RL-max (4k). See module docstring, risk 3.

OUTBOUND_CHANNELS = [
    ("EVT_STROBE_BUF", "EVT_STROBE_INTAN"),
    ("BARCODE_PI", "BARCODE_INTAN"),
    ("RWD_CMD_BUF", "RWD_CMD_INTAN"),
    ("RWD_DLVR", "RWD_DLVR_INTAN"),
]
STIM_TRIG_CHANNEL = ("STIM_TRIG_BUF", "STIM_TRIG_INTAN")
RHS_STIM_CHANNEL = ("RHS_STIM_RAW", "RHS_STIM_OUT")

ALL_SOURCE_NETS = {c[0] for c in OUTBOUND_CHANNELS} | {STIM_TRIG_CHANNEL[0]}  # DGND-side sources
ALL_OUTBOUND_FINAL = {c[1] for c in OUTBOUND_CHANNELS} | {STIM_TRIG_CHANNEL[1]}  # INTAN-side finals

NON_ISOLATED_RAILS = {"+5V", "+3V3", "+12V", "-12V", "AGND", "DGND"}
ISOLATED_RAILS = {"ISO_5V", "INTAN_GND"}
# RHS_STIM_OUT is the DGND-side (non-isolated) name for the inbound channel's own output
# -- NOT part of the isolated net set. RHS_STIM_RAW is the Intan-side (isolated) name for
# its own source, upstream of the LED.
ISOLATED_NETS = ISOLATED_RAILS | ALL_OUTBOUND_FINAL | {"RHS_STIM_RAW"}
NON_ISOLATED_NETS = NON_ISOLATED_RAILS | ALL_SOURCE_NETS | {"RHS_STIM_OUT"}


# ---------------------------------------------------------------------------
# Shared helpers -- duplicated from prior checkers' own technique, not imported.
# ---------------------------------------------------------------------------


def _find_bridging_resistor(nets: dict[str, list[Node]], net_a: str, net_b: str) -> str:
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


def _node_net(nets: dict[str, list[Node]], ref: str, pin: str) -> str:
    hits = [name for name, nodelist in nets.items() if any(n.ref == ref and n.pin == pin for n in nodelist)]
    check(len(hits) == 1, f"{ref}.{pin}: expected to be on exactly 1 net, found {hits}")
    return hits[0]


def _single_ref(values: dict[str, str], value: str) -> str:
    refs = [r for r, v in values.items() if v == value]
    check(len(refs) == 1, f"expected exactly 1 {value!r} instance, found {len(refs)}: {refs}")
    return refs[0]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _walk_outbound(nets, values, pkg_ref, ch_pins, led_hi, source_net, pu_hi, final_net, expect_pintype="open_collector") -> tuple[str, str]:
    """One outbound-style channel's own LED->pull-up walk. Returns (r_led, r_pu); raises
    on any mismatch. `pkg_ref` is asserted (not assumed) to actually drive `final_net`."""
    anode_pin, cathode_pin, vo_pin = ch_pins
    check(final_net in nets, f"missing contract net: {final_net!r}")
    oc_nodes = [n for n in nets[final_net] if n.pintype == expect_pintype]
    check(len(oc_nodes) == 1, f"{final_net}: expected exactly 1 {expect_pintype} VOx pin, found {oc_nodes} (all nodes: {nets[final_net]})")
    check(oc_nodes[0].ref == pkg_ref and oc_nodes[0].pin == vo_pin, f"{final_net}: expected {pkg_ref}.{vo_pin}, found {oc_nodes[0]}")

    r_pu = _find_bridging_resistor(nets, pu_hi, final_net)
    check(values.get(r_pu) == PULLUP_OHMS, f"{r_pu} ({final_net}'s own pull-up): expected {PULLUP_OHMS!r}, found {values.get(r_pu)!r}")
    other_hi_rails = (NON_ISOLATED_RAILS | ISOLATED_RAILS) - {pu_hi}
    on_other_rail = [rail for rail in other_hi_rails if any(n.ref == r_pu for n in nets.get(rail, []))]
    check(not on_other_rail, f"{r_pu} ({final_net}'s own pull-up) also has a pin on {on_other_rail} -- must be {pu_hi} only")

    check(source_net in nets, f"missing contract net: {source_net!r}")
    check(
        any(n.ref == pkg_ref and n.pin == cathode_pin for n in nets[source_net]),
        f"{source_net}: {pkg_ref} pin {cathode_pin} (CATHODE) not found -- expected the "
        f"LED's cathode driven directly from the source net",
    )
    anode_net = _node_net(nets, pkg_ref, anode_pin)
    check(anode_net != source_net, f"{pkg_ref} pin {anode_pin} (ANODE) is on the same net as CATHODE/source -- LED shorted")
    r_led = _find_bridging_resistor(nets, led_hi, anode_net)
    check(values.get(r_led) == LED_R_OHMS, f"{r_led} ({final_net}'s own LED resistor): expected {LED_R_OHMS!r}, found {values.get(r_led)!r}")
    return r_led, r_pu


def _check_package_a(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, set[str]]:
    """ACSL-6400, 4 outbound channels: EVT_STROBE_BUF/BARCODE_PI/RWD_CMD_BUF/RWD_DLVR ->
    their own *_INTAN net, package power on ISO_5V/INTAN_GND. Returns (summary,
    own_refs) -- own_refs is every reference THIS FUNCTION discovered as belonging to
    opto-intan.kicad_sch (the package plus its own 4 channels' LED/pull-up resistors),
    for the domain-disjointness check to scope itself against (see that check's own
    docstring for why a project-wide rail scan cannot be used instead)."""
    # Discover package A from ANY one of the 4 outbound channels' own driver (module
    # docstring: ACSL-6400 is shared with opto-ni.kicad_sch, so a project-wide Value
    # scan cannot be used to identify "this sheet's own package" -- same reasoning
    # check_breakout_opto_ni_netlist.py's own fix already established).
    first_final = OUTBOUND_CHANNELS[0][1]
    check(first_final in nets, f"missing contract net: {first_final!r}")
    oc_nodes = [n for n in nets[first_final] if n.pintype == "open_collector"]
    check(len(oc_nodes) == 1, f"{first_final}: expected exactly 1 open_collector VOx pin, found {oc_nodes}")
    pkg_ref = oc_nodes[0].ref
    check(values.get(pkg_ref) == ACSL6400_VALUE, f"{pkg_ref}: expected Value {ACSL6400_VALUE!r}, found {values.get(pkg_ref)!r}")

    own_refs = {pkg_ref}
    for local_idx, (source_net, final_net) in enumerate(OUTBOUND_CHANNELS):
        r_led, r_pu = _walk_outbound(nets, values, pkg_ref, ACSL6400_CH_PINS[local_idx], "+5V", source_net, "ISO_5V", final_net)
        own_refs.update((r_led, r_pu))

    for gpin in ACSL6400_PIN_GND:
        check(any(n.ref == pkg_ref and n.pin == gpin for n in nets.get("INTAN_GND", [])), f"{pkg_ref} pin {gpin} (GND) not found on INTAN_GND")
    for vpin in ACSL6400_PIN_VDD:
        check(any(n.ref == pkg_ref and n.pin == vpin for n in nets.get("ISO_5V", [])), f"{pkg_ref} pin {vpin} (VDD) not found on ISO_5V")

    return f"Package A ({pkg_ref}, {ACSL6400_VALUE!r}): all 4 outbound channels walked end to end, GND/VDD on INTAN_GND/ISO_5V.", own_refs


def _check_package_b(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, str, set[str]]:
    """ACSL-6420, bi-directional 2/2: channel 1 (STIM_TRIG_BUF, direction 1->2) and
    channel 3 (RHS_STIM_OUT, direction 2->1). Returns (summary, pkg_ref, own_refs)."""
    stim_final = STIM_TRIG_CHANNEL[1]
    check(stim_final in nets, f"missing contract net: {stim_final!r}")
    oc_nodes = [n for n in nets[stim_final] if n.pintype == "open_collector"]
    check(len(oc_nodes) == 1, f"{stim_final}: expected exactly 1 open_collector VOx pin, found {oc_nodes}")
    pkg_ref = oc_nodes[0].ref
    check(values.get(pkg_ref) == ACSL6420_VALUE, f"{pkg_ref}: expected Value {ACSL6420_VALUE!r}, found {values.get(pkg_ref)!r}")
    own_refs = {pkg_ref}

    # Channel 1: direction 1->2 -- LED on +5V/DGND, output (pull-up) on ISO_5V.
    source1, final1 = STIM_TRIG_CHANNEL
    r_led1, r_pu1 = _walk_outbound(nets, values, pkg_ref, ACSL6420_DIR_1TO2[1], "+5V", source1, "ISO_5V", final1)
    own_refs.update((r_led1, r_pu1))

    # Channel 3: direction 2->1 -- LED on ISO_5V/INTAN_GND, output (pull-up) on +5V.
    # RHS_STIM_RAW is the ISO-side protected signal (upstream of this channel's own
    # LED); RHS_STIM_OUT is the DGND-side output -- the ALREADY-ESTABLISHED bare
    # contract net (Task 8).
    source3, final3 = RHS_STIM_CHANNEL
    r_led3, r_pu3 = _walk_outbound(nets, values, pkg_ref, ACSL6420_DIR_2TO1[3], "ISO_5V", source3, "+5V", final3)
    own_refs.update((r_led3, r_pu3))

    check(any(n.ref == pkg_ref and n.pin == ACSL6420_PIN_GND1 for n in nets.get("DGND", [])), f"{pkg_ref} pin {ACSL6420_PIN_GND1} (GND1) not found on DGND")
    check(any(n.ref == pkg_ref and n.pin == ACSL6420_PIN_VDD1 for n in nets.get("+5V", [])), f"{pkg_ref} pin {ACSL6420_PIN_VDD1} (VDD1) not found on +5V")
    check(any(n.ref == pkg_ref and n.pin == ACSL6420_PIN_GND2 for n in nets.get("INTAN_GND", [])), f"{pkg_ref} pin {ACSL6420_PIN_GND2} (GND2) not found on INTAN_GND")
    check(any(n.ref == pkg_ref and n.pin == ACSL6420_PIN_VDD2 for n in nets.get("ISO_5V", [])), f"{pkg_ref} pin {ACSL6420_PIN_VDD2} (VDD2) not found on ISO_5V")

    return (
        f"Package B ({pkg_ref}, {ACSL6420_VALUE!r}): channel 1 (STIM_TRIG_BUF, "
        f"direction 1->2, LED on +5V/DGND) and channel 3 (RHS_STIM_OUT, direction "
        f"2->1, LED on ISO_5V/INTAN_GND) both walked end to end; VDD1/GND1 on +5V/DGND, "
        f"VDD2/GND2 on ISO_5V/INTAN_GND.",
        pkg_ref,
        own_refs,
    )


def _rhs_stim_clamp_ref(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """The BAT54S instance whose own pin 3 (COM) sits on RHS_STIM_RAW specifically --
    BAT54S is NOT unique project-wide (taskpc-digital.kicad_sch alone places 19 more,
    one per inbound task-PC channel), so this must be found by NET, not by a global
    Value scan (same reasoning check_breakout_opto_ni_netlist.py's own fix already
    established for ACSL-6400)."""
    check("RHS_STIM_RAW" in nets, "missing net: 'RHS_STIM_RAW'")
    candidates = [n.ref for n in nets["RHS_STIM_RAW"] if n.pin == "3" and values.get(n.ref) == BAT54S_VALUE]
    check(len(candidates) == 1, f"RHS_STIM_RAW: expected exactly 1 BAT54S pin 3 (COM), found {candidates}")
    return candidates[0]


def _check_rhs_stim_input_protection(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, set[str]]:
    """RHS_STIM_OUT's own inbound BNC -> 100R series -> BAT54S clamp (ISO_5V/INTAN_GND)
    -> RHS_STIM_RAW (module docstring, risk 1 / the global 'every panel input' constraint).
    Returns (summary, own_refs) -- the series resistor and clamp diode refs."""
    check("RHS_STIM_BNC" in nets, "missing net: 'RHS_STIM_BNC'")
    r_ref = _find_bridging_resistor(nets, "RHS_STIM_BNC", "RHS_STIM_RAW")
    check(values.get(r_ref) == "100", f"{r_ref} (RHS_STIM_OUT's own series resistor): expected '100', found {values.get(r_ref)!r}")

    d_ref = _rhs_stim_clamp_ref(nets, values)
    check(any(n.ref == d_ref and n.pin == "2" for n in nets.get("ISO_5V", [])), f"{d_ref} pin 2 (K, high clamp) not found on ISO_5V")
    check(any(n.ref == d_ref and n.pin == "1" for n in nets.get("INTAN_GND", [])), f"{d_ref} pin 1 (A, low clamp) not found on INTAN_GND")

    bnc_nodes = [n for n in nets["RHS_STIM_BNC"] if n.ref.startswith("J")]
    check(len(bnc_nodes) == 1, f"RHS_STIM_BNC: expected exactly 1 BNC connector pin, found {bnc_nodes}")
    return f"RHS_STIM_OUT's own inbound protection: BNC -[100R {r_ref}]-> RHS_STIM_RAW -[BAT54S {d_ref} clamp to ISO_5V/INTAN_GND].", {r_ref, d_ref}


def _check_iso5v_ldo(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, set[str]]:
    """ISO_5V's own source: LD1117S50TR_SOT223, VI=ISO_P12, GND=INTAN_GND, VO=ISO_5V
    (module docstring, POWER) -- this value string IS unique project-wide (Task 7's own
    +3V3 stage uses the 33-fixed sibling, LD1117S33TR_SOT223, a different Value).
    Returns (summary, own_refs)."""
    ref = _single_ref(values, LDO_VALUE)
    check(any(n.ref == ref and n.pin == "3" for n in nets.get("ISO_P12", [])), f"{ref} pin 3 (VI) not found on ISO_P12")
    check(any(n.ref == ref and n.pin == "1" for n in nets.get("INTAN_GND", [])), f"{ref} pin 1 (GND) not found on INTAN_GND")
    check(any(n.ref == ref and n.pin == "2" for n in nets.get("ISO_5V", [])), f"{ref} pin 2 (VO) not found on ISO_5V")
    return f"ISO_5V's own source ({ref}, {LDO_VALUE!r}): VI on ISO_P12, GND on INTAN_GND, VO on ISO_5V.", {ref}


def _check_all_bncs(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, set[str]]:
    """6 BNCs total (5 outbound + 1 inbound), each shell on INTAN_GND, no two channels
    sharing one connector. Returns (summary, own_refs)."""
    expected_center_nets = {c[1] for c in OUTBOUND_CHANNELS} | {STIM_TRIG_CHANNEL[1], "RHS_STIM_BNC"}
    check(len(expected_center_nets) == 6, f"internal inconsistency: expected 6 BNC center nets, computed {len(expected_center_nets)}")
    all_bnc_refs = set()
    for net in expected_center_nets:
        check(net in nets, f"missing net: {net!r}")
        j_nodes = [n for n in nets[net] if n.ref.startswith("J")]
        check(len(j_nodes) == 1, f"{net}: expected exactly 1 BNC connector pin, found {j_nodes}")
        check(any(n.ref == j_nodes[0].ref and n.pin == "2" for n in nets.get("INTAN_GND", [])), f"{j_nodes[0].ref} (on {net}): shell (pin 2) not found on INTAN_GND")
        all_bnc_refs.add(j_nodes[0].ref)
    check(len(all_bnc_refs) == 6, f"expected 6 DISTINCT BNC references, found {len(all_bnc_refs)}: {all_bnc_refs}")
    return f"All 6 BNCs ({sorted(all_bnc_refs)}) confirmed: own distinct connector, shell on INTAN_GND, center on the right channel net.", all_bnc_refs


def _check_domain_pin_disjoint(nets: dict[str, list[Node]], values: dict[str, str], own_refs: set[str], barrier_refs: set[str]) -> str:
    """The Intan-isolated domain must be PIN-DISJOINT from every non-isolated rail and
    source/RHS_STIM_OUT net, except the ACSL-6400/6420 packages themselves, straddling
    by design (module docstring, risk 2).

    SCOPED TO `own_refs` -- every reference the other checks in this file discovered as
    genuinely part of opto-intan.kicad_sch (both packages, every channel's own LED/
    pull-up resistor, the LDO, the clamp diode, the series resistor, all 6 BNCs) --
    DELIBERATELY NOT a blind scan of "every node on ISO_5V/INTAN_GND/+5V/DGND/etc.
    project-wide". Confirmed empirically why the blind version is wrong, not assumed:
    mux-intan.kicad_sch's own 8 INA105KU difference amplifiers EACH carry a real pin on
    BOTH INTAN_GND (isolated) and AGND (non-isolated) -- Task 10c's own already-reviewed
    analog design, which senses relative to AGND at one leg while running its own supply
    from the isolated rail, a DELIBERATE partial-isolation choice for the analog fan-out
    that is explicitly NOT the same guarantee this task's own DIGITAL optocoupler barrier
    makes (spec's own architecture note: analog fan-out uses differential receive and
    NRSE, not galvanic isolation; only the digital crossings are optocoupled). Task 7's
    own isolated DC-DC (U2) straddles +12V/DGND vs INTAN_GND identically, by design, as
    the isolated supply itself. Scanning by rail name alone pulled BOTH sets of
    already-reviewed, out-of-this-task's-scope components in as false positives before
    this fix -- restricting to own_refs excludes them correctly without re-litigating
    Task 7's or Task 10c's own, separately-reviewed designs.
    """
    isolated_nodes = {(ref, pin) for name in ISOLATED_NETS for n in nets.get(name, []) if (ref := n.ref) in own_refs for pin in (n.pin,)}
    non_isolated_nodes = {(ref, pin) for name in NON_ISOLATED_NETS for n in nets.get(name, []) if (ref := n.ref) in own_refs for pin in (n.pin,)}
    isolated_refs = {ref for ref, _pin in isolated_nodes}
    non_isolated_refs = {ref for ref, _pin in non_isolated_nodes}
    straddling = isolated_refs & non_isolated_refs

    check(
        straddling == barrier_refs,
        f"unexpected straddling references, among opto-intan.kicad_sch's own {len(own_refs)} "
        f"components, between the Intan-isolated domain and non-isolated rails: "
        f"{straddling - barrier_refs or 'none'} (expected only the 2 barrier packages "
        f"{sorted(barrier_refs)}); missing expected straddlers: {barrier_refs - straddling or 'none'}",
    )
    check(
        isolated_refs | non_isolated_refs <= own_refs,
        "internal check error: a ref outside own_refs leaked into the domain scan",
    )
    return (
        f"Intan-isolated domain (ISO_5V/INTAN_GND/all *_INTAN nets/RHS_STIM_RAW) is "
        f"pin-disjoint from every non-isolated rail/*_BUF/source net/RHS_STIM_OUT, among "
        f"opto-intan.kicad_sch's own {len(own_refs)} components, except the 2 barrier "
        f"packages {sorted(barrier_refs)}, straddling by design."
    )


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 40, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really opto-intan.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in opto-intan.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short: {list(collisions.items())[:5]}",
    )
    return f"No coordinate collisions: all {len(coords)} distinct global-label positions in opto-intan.kicad_sch carry exactly one net name each."


# ---------------------------------------------------------------------------
# GENERAL row-pitch-vs-2-pin-part-span guard -- fix round 1 (added on opto-ni.kicad_sch's
# own checker first; see check_breakout_opto_ni_netlist.py's own module comment for the
# full "third distinct instance of this defect class" account and rationale, restated only
# briefly here). `_check_no_coordinate_collisions()` above catches an ACTUAL duplicate
# label coordinate, which needs the real bad schematic to exist first; this instead checks
# the STRUCTURAL property (no same-column 2-pin part's own real pin-reach overlaps
# another's) that prevents one from ever being possible, independently of whether THIS
# sheet's own current layout happens to trip an exact coincidence.
# ---------------------------------------------------------------------------

_PLACED_INSTANCE_RE = re.compile(
    r'\(symbol\s*\n\s*\(lib_id "([^"]+)"\)\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)\s*\n\s*\(unit (\d+)\)'
    r'.*?\(property "Reference" "([^"]+)"',
    re.DOTALL,
)


def _two_pin_reach_intervals(sch_text: str) -> dict[float, list[tuple[str, float, float]]]:
    """Same technique as check_breakout_opto_ni_netlist.py's own function of this name --
    duplicated, not imported, per this project's own established per-checker independence
    discipline. For every PLACED symbol instance in `sch_text` that resolves (via its own
    real library definition) to exactly 2 pins, compute the REAL rendered [lo_y, hi_y]
    reach both of its own pins actually land on, grouped by the REAL X column both pins
    share -- VERTICAL 2-pin parts only (both pins at the same real X), true for every
    discrete device placed in a shared, repeated per-row column (Device:R/Device:C/
    Device:FerriteBead) and the actual SHAPE this project's own row-pitch defect class
    takes. A 2-pin part whose own two pins sit at DIFFERENT real X -- found empirically on
    THIS sheet's own BNC jacks, `Connector:Conn_Coaxial`'s center/shield pins -- is a
    genuinely different geometry (individually positioned per instance, not stacked with
    siblings in a shared column) and is excluded, not force-fit."""
    by_column: dict[float, list[tuple[str, float, float]]] = {}
    for m in _PLACED_INSTANCE_RE.finditer(sch_text):
        lib_id, ax, ay, _rot, unit, ref = m.groups()
        ax, ay = float(ax), float(ay)
        libname, _sep, symname = lib_id.partition(":")
        try:
            block = extract_symbol(libname, symname)
            pins = unit_pins(block, symname, int(unit))
        except (AssertionError, FileNotFoundError):
            continue
        if len(pins) != 2:
            continue
        real = [pin_pos(ax, ay, p) for p in pins.values()]
        xs = sorted(round(x, 3) for x, _y in real)
        if xs[0] != xs[1]:
            continue  # non-vertical 2-pin part (e.g. Conn_Coaxial's center+shield) --
            # not subject to the shared-column row-pitch hazard this function models
        ys = sorted(y for _x, y in real)
        by_column.setdefault(xs[0], []).append((ref, ys[0], ys[1]))
    return by_column


def _check_row_pitch_exceeds_2pin_span(sch_text: str) -> str:
    """THE GENERAL guard -- see check_breakout_opto_ni_netlist.py's own function of this
    name for the full rationale. Every same-column pair of real, placed 2-pin parts on
    opto-intan.kicad_sch must have strictly disjoint pin-to-pin reach intervals."""
    by_column = _two_pin_reach_intervals(sch_text)
    checked_columns = 0
    checked_parts = 0
    violations = []
    for col, members in by_column.items():
        if len(members) < 2:
            continue
        checked_columns += 1
        checked_parts += len(members)
        for i in range(len(members)):
            ref_i, lo_i, hi_i = members[i]
            for j in range(i + 1, len(members)):
                ref_j, lo_j, hi_j = members[j]
                if lo_i <= hi_j and lo_j <= hi_i:
                    violations.append((col, ref_i, (lo_i, hi_i), ref_j, (lo_j, hi_j)))
    check(
        checked_columns > 0,
        "no column hosted 2+ real 2-pin parts -- suspiciously unable to exercise this "
        "check at all; is this really opto-intan.kicad_sch's own rendered text, with its "
        "own real R/C placements?",
    )
    check(
        not violations,
        f"{len(violations)} same-column 2-pin-part pair(s) have OVERLAPPING real pin-"
        f"reach intervals in opto-intan.kicad_sch -- the general form of the NI_5V/"
        f"channel-data-net short opto-ni.kicad_sch already hit once (CH_ROW_DY's own "
        f"account in gen_breakout_opto_ni.py), independent of whether it currently lands "
        f"an exact duplicate coordinate: {violations[:5]}",
    )
    return (
        f"Row-pitch-vs-2-pin-part-span guard: {checked_parts} real 2-pin parts across "
        f"{checked_columns} shared columns in opto-intan.kicad_sch, every same-column "
        f"pair's own pin-reach interval strictly disjoint from every other."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    a_summary, a_refs = _check_package_a(nets, values)
    summary.append(a_summary)
    b_summary, pkg_b_ref, b_refs = _check_package_b(nets, values)
    summary.append(b_summary)
    ldo_summary, ldo_refs = _check_iso5v_ldo(nets, values)
    summary.append(ldo_summary)
    rhs_summary, rhs_refs = _check_rhs_stim_input_protection(nets, values)
    summary.append(rhs_summary)
    bnc_summary, bnc_refs = _check_all_bncs(nets, values)
    summary.append(bnc_summary)

    pkg_a_ref = next(r for r in a_refs if values.get(r) == ACSL6400_VALUE)
    own_refs = a_refs | b_refs | ldo_refs | rhs_refs | bnc_refs
    summary.append(_check_domain_pin_disjoint(nets, values, own_refs, {pkg_a_ref, pkg_b_ref}))

    for rail in ("+5V", "DGND", "ISO_5V", "INTAN_GND", "ISO_P12"):
        check(rail in nets, f"missing consumed/produced rail: {rail!r}")
    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire.
# ---------------------------------------------------------------------------


def _assert_fails(nets, values, expect_substring: str, label: str) -> str:
    try:
        verify(nets, values)
    except CheckFailure as e:
        check(expect_substring in str(e), f"self-test {label!r}: wrong complaint (expected {expect_substring!r}, got: {e})")
        return str(e)
    raise CheckFailure(f"self-test {label!r}: verify() did NOT raise on a corrupted netlist")


def self_test(good_nets: dict[str, list[Node]], good_values: dict[str, str]) -> list[str]:
    results = []

    # (1) Pull-up value drift on an outbound channel -- specifically BACK to this
    # sheet's own former, now-stale, datasheet-RL-max-violating 10k (fix round 2's own
    # most meaningful regression to guard against: someone "fixing" this back to what
    # looks like the more conservative value).
    drifted = dict(good_values)
    r_pu = _find_bridging_resistor(good_nets, "ISO_5V", "BARCODE_INTAN")
    drifted[r_pu] = "10k"
    msg = _assert_fails(good_nets, drifted, "expected '3.9k'", f"{r_pu} (BARCODE_INTAN's own pull-up) drifted 3.9k->10k (the stale, RL-max-violating value)")
    results.append(f"Pull-up value drift (3.9k -> the stale 10k, BARCODE_INTAN, {r_pu}): caught -- {msg}")

    # (2) Pull-up value drift on the inbound channel (RHS_STIM_OUT's own, +5V-referenced)
    # -- the mule's own 1k, the same drift target opto-ni.kicad_sch's own analogous
    # self-test uses.
    drifted2 = dict(good_values)
    r_pu2 = _find_bridging_resistor(good_nets, "+5V", "RHS_STIM_OUT")
    drifted2[r_pu2] = "1k"
    msg = _assert_fails(good_nets, drifted2, "expected '3.9k'", f"{r_pu2} (RHS_STIM_OUT's own pull-up) drifted 3.9k->1k (the mule's own value)")
    results.append(f"Pull-up value drift (3.9k -> the mule's own 1k, RHS_STIM_OUT, {r_pu2}): caught -- {msg}")

    # (3) THE domain-defeating hazard: an outbound channel's own pull-up ALSO wired to
    # +5V (shorting the isolated rail onto the non-isolated one) -- ADD a stray node,
    # don't move the real one (same reasoning as every other checker's identical control).
    also_5v = copy.deepcopy(good_nets)
    r_pu3 = _find_bridging_resistor(good_nets, "ISO_5V", "RWD_CMD_INTAN")
    stray = next(n for n in good_nets["ISO_5V"] if n.ref == r_pu3)
    also_5v.setdefault("+5V", []).append(stray)
    msg = _assert_fails(also_5v, good_values, "also has a pin on", f"{r_pu3} (RWD_CMD_INTAN's own pull-up) also wired to +5V")
    results.append(f"Pull-up also wired to +5V (defeats the isolation barrier, RWD_CMD_INTAN, {r_pu3}): caught -- {msg}")

    # (4) LED resistor value drift.
    drifted4 = dict(good_values)
    pkg_a_ref = next(n.ref for n in good_nets["EVT_STROBE_INTAN"] if n.pintype == "open_collector")
    anode_net = _node_net(good_nets, pkg_a_ref, "1")
    r_led = _find_bridging_resistor(good_nets, "+5V", anode_net)
    drifted4[r_led] = "330"
    msg = _assert_fails(good_nets, drifted4, "expected '430'", f"{r_led} (EVT_STROBE_INTAN's own LED resistor) drifted 430->330")
    results.append(f"LED resistor value drift (430 -> 330, EVT_STROBE_INTAN, {r_led}): caught -- {msg}")

    # (5) ACSL-6420 channel-direction permutation: swap STIM_TRIG_INTAN's own VOx node
    # with RHS_STIM_OUT's own VOx node (simulating a generator edit that assigns the
    # wrong physical channel/direction to a signal -- the exact "plausible but wrong"
    # class this task's own brief warns about).
    swapped = copy.deepcopy(good_nets)
    a_idx = next(i for i, n in enumerate(swapped["STIM_TRIG_INTAN"]) if n.pintype == "open_collector")
    b_idx = next(i for i, n in enumerate(swapped["RHS_STIM_OUT"]) if n.pintype == "open_collector")
    swapped["STIM_TRIG_INTAN"][a_idx], swapped["RHS_STIM_OUT"][b_idx] = swapped["RHS_STIM_OUT"][b_idx], swapped["STIM_TRIG_INTAN"][a_idx]
    msg = _assert_fails(swapped, good_values, "expected", "STIM_TRIG_INTAN/RHS_STIM_OUT own VOx nodes swapped (wrong-direction channel assignment)")
    results.append(f"ACSL-6420 channel/direction permutation (STIM_TRIG_INTAN <-> RHS_STIM_OUT own VOx pins): caught -- {msg}")

    # (6) RHS_STIM_OUT's own clamp diode moved off INTAN_GND (a real, silent hazard --
    # the low-side clamp reference lost).
    moved = copy.deepcopy(good_nets)
    d_ref = _rhs_stim_clamp_ref(good_nets, good_values)
    d_low_node = next(n for n in good_nets["INTAN_GND"] if n.ref == d_ref)
    moved["INTAN_GND"] = [n for n in moved["INTAN_GND"] if n != d_low_node]
    msg = _assert_fails(moved, good_values, "not found on INTAN_GND", f"{d_ref} pin 1 (A, low clamp) dropped from INTAN_GND")
    results.append(f"RHS_STIM_OUT clamp diode's own low-side reference dropped ({d_ref}): caught -- {msg}")

    # (7) A BNC shell disconnected from INTAN_GND (simulating a dropped/misrouted shell tie).
    no_shell = copy.deepcopy(good_nets)
    bnc_ref_on_barcode = next(n.ref for n in good_nets["BARCODE_INTAN"] if n.ref.startswith("J"))
    shell_node = next(n for n in good_nets["INTAN_GND"] if n.ref == bnc_ref_on_barcode)
    no_shell["INTAN_GND"] = [n for n in no_shell["INTAN_GND"] if n != shell_node]
    msg = _assert_fails(no_shell, good_values, "shell (pin 2) not found on INTAN_GND", f"{bnc_ref_on_barcode}'s own shell dropped from INTAN_GND")
    results.append(f"BNC shell disconnected from INTAN_GND ({bnc_ref_on_barcode}, BARCODE_INTAN): caught -- {msg}")

    return results


def self_test_collision(good_sch_text: str) -> str:
    matches = list(_GLOBAL_LABEL_RE.finditer(good_sch_text))
    check(len(matches) > 20, "self-test setup failed: too few global labels found to corrupt")
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
        check("DISTINCT global-label net name" in str(e), f"self-test 'coordinate collision reintroduced': wrong failure message: {e}")
        return str(e)
    raise CheckFailure("self-test 'coordinate collision reintroduced' did NOT raise -- _check_no_coordinate_collisions() is passing vacuously")


def self_test_row_pitch(good_sch_text: str) -> str:
    """Negative control for _check_row_pitch_exceeds_2pin_span() -- same technique as
    check_breakout_opto_ni_netlist.py's own function of this name: forces one real 2-pin
    part's own placement Y onto a DIFFERENT, same-column part's own placement Y and
    confirms the general guard fires."""
    matches = list(_PLACED_INSTANCE_RE.finditer(good_sch_text))
    check(len(matches) > 10, "self-test setup failed: too few placed instances found to corrupt")
    by_column = _two_pin_reach_intervals(good_sch_text)
    col, members = next(((c, ms) for c, ms in by_column.items() if len(ms) >= 2), (None, None))
    check(col is not None, "self-test setup failed: no column hosts 2+ real 2-pin parts")
    ref_a, ref_b = members[0][0], members[1][0]
    m_a = next(m for m in matches if m.group(6) == ref_a)
    m_b = next(m for m in matches if m.group(6) == ref_b)
    corrupted = good_sch_text[: m_b.start(3)] + m_a.group(3) + good_sch_text[m_b.end(3):]
    check(corrupted != good_sch_text, "self-test setup failed: splice produced no change")
    try:
        _check_row_pitch_exceeds_2pin_span(corrupted)
    except CheckFailure as e:
        check("OVERLAPPING real pin-reach intervals" in str(e), f"self-test 'row-pitch collision reintroduced': wrong failure message: {e}")
        return str(e)
    raise CheckFailure("self-test 'row-pitch collision reintroduced' did NOT raise -- _check_row_pitch_exceeds_2pin_span() is passing vacuously")


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check.
# ---------------------------------------------------------------------------


def verify_instance_paths(opto_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(opto_sch_text)
    check(own_root_uuid != breakout_root_uuid, f"opto-intan.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate")
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, OPTO_INTAN_SHEETFILE)

    paths = find_all_instance_paths(opto_sch_text)
    check(
        len(paths) >= 28,
        f"only found {len(paths)} (instances (path ...)) entries in opto-intan.kicad_sch -- "
        f"expected >=28 (recomputed directly against this task's own real output: 33 "
        f"placed instances -- 2 ACSL packages + 6 BNCs + 16 channel resistors (6 real + "
        f"2 spare channels, LED+pullup each) + 4 bypass caps + 1 series R + 1 BAT54S + 1 "
        f"LDO + 2 LDO caps -- the >=28 floor is intentionally left below that real count "
        f"so a future small edit doesn't need this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(not bad, f"{len(bad)}/{len(paths)} component instance paths in opto-intan.kicad_sch do not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}")
    return f"All {len(paths)} component instance paths in opto-intan.kicad_sch resolve to the real ancestor chain {expected_prefix!r}."


def _assert_instance_paths_fail(opto_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(opto_text, breakout_text)
    except CheckFailure as e:
        check(expect_substring in str(e), f"self-test {label!r}: wrong complaint: {e}")
        return str(e)
    raise CheckFailure(f"self-test {label!r}: verify_instance_paths() did NOT raise")


def self_test_instance_paths(good_opto_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_opto_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, OPTO_INTAN_SHEETFILE)
    corrupted = good_opto_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(corrupted != good_opto_text, "self-test setup failed: no occurrence of the expected ancestor path found to corrupt")
    msg = _assert_instance_paths_fail(corrupted, good_breakout_text, "do not resolve to the real ancestor chain", "one component's instance path reverted to self-referential")
    return [f"Self-referential instance path reintroduced: caught -- {msg}"]


def main() -> int:
    net_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NET_PATH
    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print("  kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net hardware/breakout/breakout.kicad_sch")
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_OPTO_INTAN_SCH.exists():
        print(f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_OPTO_INTAN_SCH} do not exist")
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    opto_sch_text = DEFAULT_OPTO_INTAN_SCH.read_text()

    try:
        collision_summary = _check_no_coordinate_collisions(opto_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {collision_summary}")

    try:
        collision_self_test_msg = self_test_collision(opto_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: coordinate collision reintroduced: caught -- {collision_self_test_msg}")

    try:
        row_pitch_summary = _check_row_pitch_exceeds_2pin_span(opto_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(opto_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

    try:
        path_summary = verify_instance_paths(opto_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(opto_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
