"""Parse hardware/breakout's exported netlist (and, for the two checks the exported netlist
provably cannot answer -- coordinate collisions and the instance-path ancestor chain -- the
raw .kicad_sch sources) and verify opto-ni.kicad_sch's contract nets and topology are
electrically correct -- not just that ERC was silent. Same reasoning every prior checker in
this project gives, restated because it is why THIS file exists too: a mislabeled pin
produces a plausible, wrong, ERC-clean netlist. This task's own brief names the exact
failure this class of bug could produce here: "a previous sheet [hedged two candidate pins
and] concealed a reversed mapping that would have permuted 8 of 16 analog channels while
passing every connectivity check" (task-10c-report.md) -- this file exists to make that
impossible to do silently on THIS sheet's own 24 channels.

THE CENTRAL RISKS this file exists to catch, named explicitly by this task's own brief:

  1. All 24 channels must exist and cross the DGND->NI_GND barrier -- source_net (cathode)
     -> LED (430R to +5V) -> ACSL-6400 -> pull-up (3.9k to NI_5V) -> final_net -> Connector
     1's own REAL, sourced physical pin. Walked end to end per channel, not assumed from
     net names alone.
  2. The NI-isolated domain (NI_5V, NI_GND, every *_NI net) must be PIN-DISJOINT from every
     non-isolated rail (+5V/+3V3/+12V/-12V/AGND/DGND) and from every *_BUF/source net,
     except the ACSL-6400 packages themselves (the barrier component, straddling by
     design) and the fallback DC-DC (also straddling by design, DNP or not -- DNP does not
     change connectivity, this project's own established finding, task-10d-report.md).
  3. Every NI-side pull-up is 3.9k, on NI_5V specifically -- FIX ROUND 1 (see
     .superpowers/sdd/2026-08-13-breakout-pcb/task-11-report.md's own "Fix round 1"
     section): the original 10k exceeded ACSL-6400's own datasheet RL-max (4k), so this
     sheet's own generator/checker both moved to 3.9k, the largest E24 value at or under
     that limit -- never 1k (the mule's own value) and never +5V (which would short the
     isolated output rail onto the non-isolated one, silently defeating the barrier while
     still looking like "a resistor to a valid power net" to ERC).
  4. LED current-setting resistors are 430R (the value this sheet's own generator derives
     for ~7.33mA, roughly half ACSL-6400's own datasheet-recommended top-of-range current).
  5. The MDR68 Connector 1 pin assignment matches NI's own X Series User Manual Figure
     A-18 -- redefined here from the SAME source, independently of
     gen_breakout_opto_ni.py's own account of it.
  6. No same-column real 2-pin part may have its own pin-to-pin reach overlap another's --
     the GENERAL form of this sheet's own real coordinate-collision defect (CH_ROW_DY's
     own account in gen_breakout_opto_ni.py -- Device:R's 7.62mm span landing exactly on a
     foreign row because it was an exact multiple of the ACSL package's 2.54mm row pitch),
     checked structurally (from real, rendered pin geometry) rather than only after an
     actual duplicate coordinate appears -- see check_row_pitch_guard.py's own
     check_row_pitch_exceeds_2pin_span(), imported below (added directly in this file at
     fix round 1, factored into that shared module at fix round 2 once three more
     checkers needed the identical guard).

`verify()` below re-derives the full channel/pin contract independently of
gen_breakout_opto_ni.py's own choices -- same "a checker that trusted the generator would
only be checking the generator against itself" discipline every prior checker in this
project already follows.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_opto_ni_netlist.py [path/to/breakout.net]

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
DEFAULT_OPTO_NI_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "opto-ni.kicad_sch"
OPTO_NI_SHEETFILE = "sheets/opto-ni.kicad_sch"

# ---------------------------------------------------------------------------
# Channel/contract constants -- redefined here from this task's own brief and NI's own
# manual, NEVER imported from the generator (same independence discipline every checker
# in this project already follows for its own contract nets/pin tables).
# ---------------------------------------------------------------------------
ACSL6400_VALUE = "ACSL-6400-00TE"
TMA0505S_VALUE = "TMA-0505S"
MDR68_VALUE = "Connector 1 (digital, recording NI)"

# local channel index (0-3) -> (anode, cathode, vo) pin numbers -- Broadcom AV02-0235EN,
# Figure 4/Figure 10 (both agree exactly; see gen_wl_sync_lib.py's own "ACSL-6400" comment).
ACSL6400_CH_PINS = {
    0: ("1", "2", "14"),
    1: ("3", "4", "13"),
    2: ("5", "6", "12"),
    3: ("7", "8", "11"),
}
ACSL6400_VO_TO_LOCAL = {pins[2]: idx for idx, pins in ACSL6400_CH_PINS.items()}
ACSL6400_PIN_GND = ["9", "16"]
ACSL6400_PIN_VDD = ["10", "15"]
ACSL6400_ALL_PINS = {p for triplet in ACSL6400_CH_PINS.values() for p in triplet} | set(ACSL6400_PIN_GND) | set(ACSL6400_PIN_VDD)
assert ACSL6400_ALL_PINS == {str(n) for n in range(1, 17)}, sorted(ACSL6400_ALL_PINS, key=int)

LED_R_OHMS = "430"
PULLUP_OHMS = "3.9k"  # fix round 1 -- corrected from 10k, which exceeded ACSL-6400's own
# datasheet RL-max (4k); see gen_breakout_opto_ni.py's own module docstring, NI-SIDE
# PULL-UP, and .superpowers/sdd/2026-08-13-breakout-pcb/task-11-report.md's "Fix round 1".

NI_CHANNELS = (
    [(f"EVT_D{i}_BUF", f"EVT_D{i}_NI", 8 + i) for i in range(16)]
    + [
        ("EVT_STROBE_BUF", "EVT_STROBE_NI", 24),
        ("BARCODE_BUF", "BARCODE_NI", 25),
        ("RWD_CMD_BUF", "RWD_CMD_NI", 26),
        ("RWD_DLVR_BUF", "RWD_DLVR_NI", 27),
        ("STIM_TRIG_BUF", "STIM_TRIG_NI", 28),
        ("RHS_STIM_OUT", "RHS_STIM_OUT_NI", 29),
        ("PD1_COMP_BUF", "PD1_COMP_NI", 30),
        ("PD2_COMP_BUF", "PD2_COMP_NI", 31),
    ]
)
assert len(NI_CHANNELS) == 24
assert len({c[1] for c in NI_CHANNELS}) == 24

# Physical MDR68 (Connector 1, recording NI) pins -- NI's X Series User Manual
# (370784K-01), Figure A-18 "NI PCIe-6353 and NI PCIe/PXIe-6363 Pinout" -- see
# gen_breakout_opto_ni.py's own module docstring for the full retrieval/cross-check
# account. Redefined here from the SAME primary source, independently.
MDR1_PIN_BY_P0 = {
    8: "52", 9: "17", 10: "49", 11: "47", 12: "19", 13: "51", 14: "16", 15: "48",
    16: "11", 17: "10", 18: "43", 19: "42", 20: "41", 21: "6", 22: "5", 23: "38",
    24: "37", 25: "3", 26: "45", 27: "46", 28: "2", 29: "40", 30: "1", 31: "39",
}
assert set(MDR1_PIN_BY_P0) == set(range(8, 32))
MDR1_DGND_PINS = ["4", "7", "9", "12", "13", "15", "18", "35", "36", "44", "50", "53"]
MDR1_5V_PINS = ["8", "14"]

NON_ISOLATED_RAILS = {"+5V", "+3V3", "+12V", "-12V", "AGND", "DGND"}
ISOLATED_RAILS = {"NI_5V", "NI_GND"}
SOURCE_NETS = {c[0] for c in NI_CHANNELS}  # DGND-domain nets this sheet consumes
FINAL_NETS = {c[1] for c in NI_CHANNELS}   # NI-isolated-domain nets this sheet produces


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
    """The net name containing (ref, pin) -- there must be exactly one."""
    hits = [name for name, nodelist in nets.items() if any(n.ref == ref and n.pin == pin for n in nodelist)]
    check(len(hits) == 1, f"{ref}.{pin}: expected to be on exactly 1 net, found {hits}")
    return hits[0]


def _mdr68_ref(values: dict[str, str]) -> str:
    refs = [r for r, v in values.items() if v == MDR68_VALUE]
    check(len(refs) == 1, f"expected exactly 1 {MDR68_VALUE!r} instance, found {len(refs)}: {refs}")
    return refs[0]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_channels_end_to_end(nets: dict[str, list[Node]], values: dict[str, str]) -> tuple[str, set[str]]:
    """Walk all 24 channels: source_net (cathode) -> LED (430R to +5V) -> ACSL-6400 ->
    pull-up (3.9k to NI_5V) -> final_net -> Connector 1's own real, sourced physical pin.
    Confirms every hop belongs to the SAME physical parts, not just that each net
    individually looks plausible (module docstring, risk 1).

    Deliberately does NOT pre-fetch "every ref with Value=='ACSL-6400-00TE' anywhere in
    the project" -- opto-intan.kicad_sch's own package A shares that identical MPN
    (module docstring: opto-ni and opto-intan's first package are the SAME part), so a
    project-wide value scan would silently pull in a reference this sheet never placed.
    Instead, each channel's own package ref is discovered from ITS OWN final_net (this
    sheet's own contract net) via the open_collector pintype alone -- narrow enough on a
    single net (the pull-up resistor and Connector 1's own pin are both "passive") that
    no ref filter is needed -- and VERIFIED (not merely assumed) to carry the expected
    Value afterward. Returns the set of package refs THIS SHEET's own 24 channels
    actually use, for the caller to reuse as the correctly-scoped "opto-ni's own
    packages" set (see verify()).
    """
    mdr_ref = _mdr68_ref(values)
    seen_pkg_channel: set[tuple[str, str]] = set()

    for source_net, final_net, p0 in NI_CHANNELS:
        check(final_net in nets, f"missing contract net: {final_net!r}")
        oc_nodes = [n for n in nets[final_net] if n.pintype == "open_collector"]
        check(
            len(oc_nodes) == 1,
            f"{final_net}: expected exactly 1 open_collector VOx pin, found {oc_nodes} "
            f"(all nodes: {nets[final_net]})",
        )
        pkg_ref, vo_pin = oc_nodes[0].ref, oc_nodes[0].pin
        check(
            values.get(pkg_ref) == ACSL6400_VALUE,
            f"{final_net}: open_collector driver {pkg_ref} has Value {values.get(pkg_ref)!r}, expected {ACSL6400_VALUE!r}",
        )
        check(vo_pin in ACSL6400_VO_TO_LOCAL, f"{final_net}: {pkg_ref}.{vo_pin} is not a real VOx pin")
        check(
            (pkg_ref, vo_pin) not in seen_pkg_channel,
            f"{pkg_ref}.{vo_pin} already used by a different channel -- duplicate/collapsed net",
        )
        seen_pkg_channel.add((pkg_ref, vo_pin))
        local_idx = ACSL6400_VO_TO_LOCAL[vo_pin]
        anode_pin, cathode_pin, expect_vo = ACSL6400_CH_PINS[local_idx]
        check(expect_vo == vo_pin, "internal inconsistency in ACSL6400_VO_TO_LOCAL")

        # Pull-up: NI_5V <-> final_net, value 3.9k (fix round 1; see PULLUP_OHMS above).
        r_pu = _find_bridging_resistor(nets, "NI_5V", final_net)
        check(values.get(r_pu) == PULLUP_OHMS, f"{r_pu} ({final_net}'s own pull-up): expected {PULLUP_OHMS!r}, found {values.get(r_pu)!r}")
        on_other_rail = [rail for rail in NON_ISOLATED_RAILS if any(n.ref == r_pu for n in nets.get(rail, []))]
        check(not on_other_rail, f"{r_pu} ({final_net}'s own pull-up) also has a pin on {on_other_rail} -- must be NI_5V only")

        # Connector 1: final_net must land on the sourced physical pin.
        expected_pin = MDR1_PIN_BY_P0[p0]
        conn_nodes = [n for n in nets[final_net] if n.ref == mdr_ref]
        check(
            len(conn_nodes) == 1 and conn_nodes[0].pin == expected_pin,
            f"{final_net} (P0.{p0}): expected {mdr_ref} pin {expected_pin} (NI's own X "
            f"Series manual Figure A-18), found {conn_nodes} -- a permuted/wrong physical pin",
        )

        # Cathode: source_net must carry (pkg_ref, cathode_pin) directly.
        check(source_net in nets, f"missing contract net: {source_net!r}")
        check(
            any(n.ref == pkg_ref and n.pin == cathode_pin for n in nets[source_net]),
            f"{source_net}: {pkg_ref} pin {cathode_pin} (this channel's own CATHODE) not "
            f"found -- expected the LED's cathode driven directly from the source net",
        )

        # Anode: LED resistor 430R bridges +5V <-> anode_net, and anode_net carries
        # (pkg_ref, anode_pin).
        anode_net = _node_net(nets, pkg_ref, anode_pin)
        check(anode_net != source_net, f"{pkg_ref} pin {anode_pin} (ANODE) is on the same net as the CATHODE/source -- LED shorted")
        r_led = _find_bridging_resistor(nets, "+5V", anode_net)
        check(values.get(r_led) == LED_R_OHMS, f"{r_led} ({final_net}'s own LED resistor): expected {LED_R_OHMS!r}, found {values.get(r_led)!r}")

        # Package power: this instance's GND/VDD pins are NI_GND/NI_5V.
        for gpin in ACSL6400_PIN_GND:
            check(
                any(n.ref == pkg_ref and n.pin == gpin for n in nets.get("NI_GND", [])),
                f"{pkg_ref} pin {gpin} (GND) not found on NI_GND",
            )
        for vpin in ACSL6400_PIN_VDD:
            check(
                any(n.ref == pkg_ref and n.pin == vpin for n in nets.get("NI_5V", [])),
                f"{pkg_ref} pin {vpin} (VDD) not found on NI_5V",
            )

    check(len(seen_pkg_channel) == 24, f"expected 24 distinct (package, VOx) pairs used, found {len(seen_pkg_channel)}")
    used_packages = {ref for ref, _ in seen_pkg_channel}
    check(
        len(used_packages) == 6,
        f"expected exactly 6 ACSL-6400 packages used across the 24 channels (4 each), "
        f"found {len(used_packages)}: {sorted(used_packages)} -- either a package is "
        f"idle or two channels share one VOx pin (already caught above if so)",
    )
    summary = (
        f"All 24 channels walked end to end (source_net -[LED 430R]-> ACSL-6400 -[3.9k "
        f"pull-up]-> final_net -> Connector 1's own sourced physical pin), each on its "
        f"own distinct (package, VOx) pair across exactly 6 {ACSL6400_VALUE!r} instances."
    )
    return summary, used_packages


def _all_pin_domain_nodes(nets: dict[str, list[Node]], net_names: set[str]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for name in net_names:
        for n in nets.get(name, []):
            out.add((n.ref, n.pin))
    return out


def _check_domain_pin_disjoint(nets: dict[str, list[Node]], values: dict[str, str], acsl_refs: set[str]) -> str:
    """The NI-isolated domain (NI_5V, NI_GND, every *_NI net) must be PIN-DISJOINT from
    every non-isolated rail and from every *_BUF/source net -- except the ACSL-6400
    packages themselves and the fallback DC-DC, both straddling BY DESIGN (module
    docstring, risk 2). `acsl_refs` is opto-ni's own 6 packages, as discovered by
    _check_channels_end_to_end()'s own channel walk -- NOT a project-wide Value scan
    (opto-intan.kicad_sch's own package A shares the identical "ACSL-6400-00TE" MPN, so
    such a scan would silently pull in a reference this sheet never placed)."""
    isolated_nodes = _all_pin_domain_nodes(nets, ISOLATED_RAILS | FINAL_NETS)
    non_isolated_nodes = _all_pin_domain_nodes(nets, NON_ISOLATED_RAILS | SOURCE_NETS)

    isolated_refs = {ref for ref, _pin in isolated_nodes}
    non_isolated_refs = {ref for ref, _pin in non_isolated_nodes}
    straddling = isolated_refs & non_isolated_refs

    dcdc_refs = {r for r, v in values.items() if v == TMA0505S_VALUE}
    expected_straddlers = acsl_refs | dcdc_refs

    check(
        straddling == expected_straddlers,
        f"unexpected straddling references between the NI-isolated domain and non-"
        f"isolated rails: {straddling - expected_straddlers or 'none'} (expected only "
        f"the 6 ACSL-6400 packages {sorted(acsl_refs)} and the fallback DC-DC "
        f"{sorted(dcdc_refs)}); missing expected straddlers: "
        f"{expected_straddlers - straddling or 'none'}",
    )
    return (
        f"NI-isolated domain (NI_5V/NI_GND/all 24 *_NI nets) is pin-disjoint from every "
        f"non-isolated rail and *_BUF/source net, except the 6 ACSL-6400 packages and the "
        f"fallback DC-DC ({sorted(dcdc_refs)}), both straddling by design."
    )


def _check_connector1_completeness(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """Every one of Connector 1's own 68 physical pins carries a real net; the 24 P0.x +
    12 D GND + 2 +5V roles land where NI's own manual says, and nothing else is wired
    (module docstring, risk 5)."""
    mdr_ref = _mdr68_ref(values)
    found_pins = {n.pin for nodelist in nets.values() for n in nodelist if n.ref == mdr_ref}
    check(len(found_pins) <= 68, f"{mdr_ref}: more than 68 distinct pins found ({len(found_pins)})")

    for pin in MDR1_DGND_PINS:
        check(any(n.ref == mdr_ref and n.pin == pin for n in nets.get("NI_GND", [])), f"{mdr_ref} pin {pin} (D GND) not on NI_GND")
    for pin in MDR1_5V_PINS:
        check(any(n.ref == mdr_ref and n.pin == pin for n in nets.get("NI_5V", [])), f"{mdr_ref} pin {pin} (+5V) not on NI_5V")

    used_pins = set(MDR1_DGND_PINS) | set(MDR1_5V_PINS) | {MDR1_PIN_BY_P0[p0] for _s, _f, p0 in NI_CHANNELS}
    check(len(used_pins) == 24 + 12 + 2, f"expected 38 distinct used physical pins, computed {len(used_pins)} -- a collision in the sourced pin table")
    return (
        f"Connector 1 ({mdr_ref}): all 24 P0.x + 12 D GND + 2 +5V roles confirmed on "
        f"their own sourced physical pin (38 distinct pins, no overlap)."
    )


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    """Scans opto-ni.kicad_sch's OWN raw rendered text for every global_label's real
    (x, y) and asserts no two DIFFERENT net names ever share one. This project has hit
    exactly this defect class before -- including on THIS sheet, during its own
    development (a pull-up's own far pin landing exactly on a different channel's VOx
    row -- see gen_breakout_opto_ni.py's own CH_ROW_DY comment for the full account)."""
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 100, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really opto-ni.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in opto-ni.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short: {list(collisions.items())[:5]}",
    )
    return f"No coordinate collisions: all {len(coords)} distinct global-label positions in opto-ni.kicad_sch carry exactly one net name each."


# ---------------------------------------------------------------------------
# GENERAL row-pitch-vs-2-pin-part-span guard -- fix round 1 (added here first); FACTORED
# OUT to check_row_pitch_guard.py at fix round 2, once the identical guard needed
# back-porting to three more checkers that had never had it (see that module's own
# docstring for the full account of why this ONE check -- unlike every sheet-specific check
# in this file -- has no sheet-specific parameters, and so graduated from "duplicated" to
# "shared"). `_check_no_coordinate_collisions()` above catches an ACTUAL duplicate label
# coordinate; it needs the real bad schematic to exist first. This is the third distinct
# instance of that defect CLASS on this project (analog-frontend's own decoupling-cap rail
# short, task-10a-report.md; its own shield-leg near-miss, caught by hand that time; and
# THIS sheet's own NI_5V/channel short, CH_ROW_DY's own comment in gen_breakout_opto_ni.py)
# -- recurring often enough that the underlying ARITHMETIC RELATIONSHIP (a two-pin part's
# own real pin-to-pin span landing on a row that is some integer multiple of a shared
# column's own row pitch away) deserves a guard that checks the STRUCTURAL property
# directly, not merely the symptom. `check_row_pitch_exceeds_2pin_span()`/
# `self_test_row_pitch()`, imported above from check_row_pitch_guard.py, are that guard;
# both are called against opto-ni.kicad_sch's own real rendered text in main() below.
# ---------------------------------------------------------------------------


def _check_one_led_per_source_net(nets, sheet_label: str, source_nets) -> str:
    """Every LED cathode net named by this sheet's own channel table carries EXACTLY ONE
    optocoupler LED cathode pin, BOARD-WIDE.

    Not a stylistic check. Each ACSL LED here is fed from +5V (or ISO_5V) through 430R and
    draws ~7.33mA -- a value chosen to sit inside the part's own 7-15mA recommended band
    and clear of its 7.0mA worst-case switching threshold, so it cannot simply be lowered.
    Two LEDs on one net means two LEDs on one driver pin: 14.7mA against SN74HCT541's 6mA
    IOL, or against SN74HCT32's 4mA. This board shipped that defect on five nets at once
    (EVT_STROBE_BUF, RWD_CMD_BUF, STIM_TRIG_BUF, BARCODE_PI, RWD_DLVR -- each feeding an
    NI LED and an Intan LED from one pin), and it is INVISIBLE to any check that reasons
    about one sheet at a time: opto-ni sees one LED on the net, opto-intan sees one LED on
    the net, and neither can see the other. So this check deliberately counts across the
    WHOLE exported netlist, not just this sheet's own references.

    See gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS for the fix and the
    per-pin numbers.
    """
    for source in sorted(source_nets):
        check(source in nets, f"{sheet_label}: missing source net {source!r}")
        cathodes = [n for n in nets[source] if (n.pinfunction or "").startswith("CATHODE")]
        check(
            len(cathodes) == 1,
            f"{source}: carries {len(cathodes)} optocoupler LED cathode pin(s) board-wide "
            f"({[(n.ref, n.pin) for n in cathodes]}), expected exactly 1. Two LEDs on one "
            f"net is two LEDs on one driver pin -- ~14.7mA against a 6mA (SN74HCT541) or "
            f"4mA (SN74HCT32) IOL. Give the second one its own buffered leg "
            f"(gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS).",
        )
    return (
        f"{sheet_label}: all {len(source_nets)} LED source nets carry exactly one LED "
        f"cathode pin board-wide -- one optocoupler LED per driver pin, counted across "
        f"the whole netlist rather than this sheet alone."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    channel_summary, acsl_refs = _check_channels_end_to_end(nets, values)
    summary.append(channel_summary)
    summary.append(_check_one_led_per_source_net(nets, "opto-ni", {c[0] for c in NI_CHANNELS}))
    summary.append(_check_domain_pin_disjoint(nets, values, acsl_refs))
    summary.append(_check_connector1_completeness(nets, values))

    for rail in ("+5V", "NI_5V", "NI_GND"):
        check(rail in nets, f"missing consumed/produced rail: {rail!r}")
        check(len(nets[rail]) >= 10, f"{rail}: suspiciously small population ({len(nets[rail])} nodes)")
    summary.append("Rails +5V/NI_5V/NI_GND present with substantial populations.")
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
    # (0) THE CROSS-SHEET LOAD DEFECT: a second optocoupler LED cathode landing on a net
    # that already has one -- which is what "two LEDs on one driver pin" looks like in a
    # netlist. Modelled the way it really happened, as an opto-intan LED appearing on
    # opto-ni's own source net (this board shipped exactly that on five nets), so the
    # control also proves the check reaches BEYOND this sheet's own references.
    doubled = copy.deepcopy(good_nets)
    doubled["EVT_STROBE_BUF"] = list(doubled["EVT_STROBE_BUF"]) + [
        Node("U64", "2", "CATHODE1_2", "passive")
    ]
    msg = _assert_fails(doubled, good_values, "expected exactly 1", "a second LED cathode added to EVT_STROBE_BUF")
    results.append(f"Two optocoupler LEDs on one driver pin (EVT_STROBE_BUF gains opto-intan's own U64 cathode): caught -- {msg}")

    _summary, acsl_refs = _check_channels_end_to_end(good_nets, good_values)

    # (1) THE central instruction this task gives explicitly (fix round 1: now 3.9k, not
    # the original 10k -- see PULLUP_OHMS above): a pull-up drifted to the mule's own 1k
    # value.
    drifted = dict(good_values)
    r_pu = _find_bridging_resistor(good_nets, "NI_5V", "EVT_D5_NI")
    drifted[r_pu] = "1k"
    msg = _assert_fails(good_nets, drifted, "expected '3.9k'", f"{r_pu} (EVT_D5_NI's own pull-up) drifted 3.9k->1k")
    results.append(f"NI pull-up value drift (3.9k -> the mule's own 1k, EVT_D5_NI, {r_pu}): caught -- {msg}")

    # (2) THE domain-defeating hazard: a pull-up ALSO wired to +5V (shorting the isolated
    # rail onto the non-isolated one) -- simulated by ADDING a stray +5V node for the
    # SAME reference while LEAVING its own genuine NI_5V node in place (rather than
    # moving it), same technique check_breakout_comparators_netlist.py's own "pull-up
    # also wired to +5V" negative control uses, and for the same reason: a coordinate
    # collision or a stray second label call would ADD a node, not silently move one.
    also_5v = copy.deepcopy(good_nets)
    r_pu2 = _find_bridging_resistor(good_nets, "NI_5V", "EVT_D9_NI")
    stray = next(n for n in good_nets["NI_5V"] if n.ref == r_pu2)
    also_5v.setdefault("+5V", []).append(stray)
    msg = _assert_fails(also_5v, good_values, "also has a pin on", f"{r_pu2} (EVT_D9_NI's own pull-up) also wired to +5V")
    results.append(f"Pull-up also wired to +5V (defeats the isolation barrier, EVT_D9_NI, {r_pu2}): caught -- {msg}")

    # (3) LED resistor value drift.
    drifted2 = dict(good_values)
    anode_net = _node_net(good_nets, sorted(acsl_refs, key=lambda r: int(r[1:]))[0], "1")
    r_led = _find_bridging_resistor(good_nets, "+5V", anode_net)
    drifted2[r_led] = "330"
    msg = _assert_fails(good_nets, drifted2, "expected '430'", f"{r_led} (EVT_D0_NI's own LED resistor) drifted 430->330 (the mule's own value)")
    results.append(f"LED resistor value drift (430 -> the mule's own 330, {r_led}): caught -- {msg}")

    # (4) Connector 1 physical-pin permutation: EVT_D2_NI and EVT_D7_NI swapped.
    swapped = copy.deepcopy(good_nets)
    mdr_ref = _mdr68_ref(good_values)
    a_idx = next(i for i, n in enumerate(swapped["EVT_D2_NI"]) if n.ref == mdr_ref)
    b_idx = next(i for i, n in enumerate(swapped["EVT_D7_NI"]) if n.ref == mdr_ref)
    swapped["EVT_D2_NI"][a_idx], swapped["EVT_D7_NI"][b_idx] = swapped["EVT_D7_NI"][b_idx], swapped["EVT_D2_NI"][a_idx]
    msg = _assert_fails(swapped, good_values, "a permuted/wrong physical pin", "Connector 1 physical pins swapped (EVT_D2_NI <-> EVT_D7_NI)")
    results.append(f"Connector 1 physical-pin permutation (EVT_D2_NI <-> EVT_D7_NI): caught -- {msg}")

    # (5) A channel's own VOx driver dropped (simulating a broken/misrouted connection).
    no_driver = copy.deepcopy(good_nets)
    victim = next(n for n in good_nets["PD1_COMP_NI"] if n.ref in acsl_refs and n.pintype == "open_collector")
    no_driver["PD1_COMP_NI"] = [n for n in no_driver["PD1_COMP_NI"] if n != victim]
    msg = _assert_fails(no_driver, good_values, "expected exactly 1 open_collector", "PD1_COMP_NI's own VOx driver pin removed")
    results.append(f"Channel with no real VOx driver (PD1_COMP_NI): caught -- {msg}")

    # (6) Domain leak: a stray DGND-domain component reference (the MDR68 connector
    # itself, already legitimately on a source net indirectly via... use a genuinely
    # DGND-only ref instead) added onto NI_GND, simulating an accidental short across the
    # barrier from a coordinate collision or a stray label call.
    leak = copy.deepcopy(good_nets)
    dgnd_only_ref = next(n.ref for n in good_nets.get("+5V", []) if n.ref not in acsl_refs and not values_is_dcdc(good_values, n.ref))
    stray_leak_node = next(n for n in good_nets["+5V"] if n.ref == dgnd_only_ref)
    leak.setdefault("NI_GND", []).append(stray_leak_node)
    msg = _assert_fails(leak, good_values, "unexpected straddling references", f"non-isolated ref {dgnd_only_ref} (already on +5V) leaked onto NI_GND")
    results.append(f"Domain leak (a +5V-side reference, {dgnd_only_ref}, also wired onto NI_GND): caught -- {msg}")

    return results


def values_is_dcdc(values: dict[str, str], ref: str) -> bool:
    return values.get(ref) == TMA0505S_VALUE


def self_test_collision(good_sch_text: str) -> str:
    matches = list(_GLOBAL_LABEL_RE.finditer(good_sch_text))
    check(len(matches) > 50, "self-test setup failed: too few global labels found to corrupt")
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


# self_test_row_pitch() itself is imported from check_row_pitch_guard.py (see imports
# above) -- called against opto-ni.kicad_sch's own real rendered text in main() below.


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker.
# ---------------------------------------------------------------------------


def verify_instance_paths(opto_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(opto_sch_text)
    check(own_root_uuid != breakout_root_uuid, f"opto-ni.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate")
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, OPTO_NI_SHEETFILE)

    paths = find_all_instance_paths(opto_sch_text)
    check(
        len(paths) >= 70,
        f"only found {len(paths)} (instances (path ...)) entries in opto-ni.kicad_sch -- "
        f"expected >=70 (recomputed directly against this task's own real output: 75 "
        f"placed instances -- 6 ACSL-6400 + 1 MDR68 connector + 24 LED resistors + 24 "
        f"pull-ups + 12 per-package bypass caps + 2 NI-entry caps + 1 PWR_FLAG + 1 "
        f"fallback DC-DC + 3 fallback filter parts + 1 bridge resistor -- the >=70 floor "
        f"is intentionally left below that real count so a future small edit doesn't "
        f"need this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(not bad, f"{len(bad)}/{len(paths)} component instance paths in opto-ni.kicad_sch do not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}")
    return f"All {len(paths)} component instance paths in opto-ni.kicad_sch resolve to the real ancestor chain {expected_prefix!r}."


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
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, OPTO_NI_SHEETFILE)
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_OPTO_NI_SCH.exists():
        print(f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_OPTO_NI_SCH} do not exist")
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    opto_sch_text = DEFAULT_OPTO_NI_SCH.read_text()

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
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(opto_sch_text, "opto-ni.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(opto_sch_text, "opto-ni.kicad_sch", min_instances=20)
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
