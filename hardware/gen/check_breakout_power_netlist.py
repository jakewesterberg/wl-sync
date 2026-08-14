"""Parse hardware/breakout's exported netlist and verify the power sheet's contract nets
are electrically correct -- not just that ERC was silent.

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

DEFAULT_NET_PATH = Path("/tmp/breakout.net")

# The brief's own required net list -- task-7-brief.md, "Net names you must produce,
# exactly". Every other breakout sheet consumes these by name.
CONTRACT_NETS = [
    "+12V", "-12V", "+5V", "+3V3", "AGND", "DGND", "ISO_P12", "ISO_N12", "INTAN_GND",
]

# Isolated-domain nets: the three contract nets plus this sheet's own internal isolated
# nodes (the pi filters' raw/filtered nodes and the two isolated-side regulators'
# FB/NR-SS nodes) -- everything electrically inside the DC-DC's secondary side. Checked
# at PIN level against NON_ISO_NETS below, same reasoning check_mule_netlist.py's own
# ISO_NETS/NON_ISO_NETS split gives: U3 (the isolated DC-DC) legitimately straddles the
# barrier BY REFERENCE (primary pins on the non-isolated side, secondary pins on the
# isolated side), so only a PIN-level check -- not a reference-level one -- can tell a
# real short from the converter's own by-design straddling.
ISO_NETS = [
    "ISO_P12", "ISO_N12", "INTAN_GND",
    "ISO_P15_RAW", "ISO_P15_FILT", "ISO_N15_RAW", "ISO_N15_FILT",
    "U4_FB", "U4_NRSS", "U5_FB", "U5_NRSS",
]
NON_ISO_NETS = [
    "+12V", "-12V", "+5V", "+3V3", "AGND", "DGND",
    "P12_RAW", "N12_RAW", "U1_FB", "U1_NRSS",
]

# Rail-pair bypass/bulk capacitor counts -- every capacitor gen_breakout_power.py places
# is wired straight across some (rail, gnd) pair via two_pin(), so this can only regress
# by a future edit accidentally dropping, adding, or moving one -- same
# regression-guard purpose as check_mule_netlist.py's own RAIL_BYPASS_EXPECTED, computed
# here directly against the currently-generated netlist (see task-7-report.md) rather than
# assumed from the generator's own source.
RAIL_BYPASS_EXPECTED = {
    ("+12V", "AGND"): 2,          # C1 (10uF), C2 (100nF) -- entry bulk+small, brief Step 1
    ("-12V", "AGND"): 2,          # C3, C4 -- ditto, -12V rail
    ("+12V", "DGND"): 2,          # C7 (U1 CIN), C12 (U3/IH1215D primary bypass)
    ("+5V", "DGND"): 2,           # C8 (U1 COUT), C9 (U1 COUT extra HF bypass)
    ("+3V3", "DGND"): 2,          # C10, C11 -- U2/LD1117S33TR output decouple+bulk
    ("ISO_P12", "INTAN_GND"): 2,  # C17, C18 -- U4 COUT + extra HF bypass
    ("ISO_N12", "INTAN_GND"): 2,  # C23, C24 -- U5 COUT + extra HF bypass
    ("ISO_P15_RAW", "INTAN_GND"): 1,   # C13 -- positive pi filter's first 10uF
    ("ISO_P15_FILT", "INTAN_GND"): 1,  # C14 -- positive pi filter's second 10uF / U4 CIN
    ("ISO_N15_RAW", "INTAN_GND"): 1,   # C19 -- negative pi filter's first 10uF
    ("ISO_N15_FILT", "INTAN_GND"): 1,  # C20 -- negative pi filter's second 10uF / U5 CIN
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

    # --- U3 (IH1215D) pin map: confirms the isolated DC-DC's primary side really is on
    # +12V/DGND and its secondary side really is on the raw isolated nodes that lead to
    # ISO_P12/ISO_N12/INTAN_GND -- the specific wiring the isolation-barrier check above
    # depends on being right, not just present. ---
    u3_map = {"1": "DGND", "3": "+12V", "4": "ISO_N15_RAW", "5": "ISO_P15_RAW", "6": "INTAN_GND"}
    for pin, net in u3_map.items():
        nodes = [n for n in nets[net] if n.ref == "U3" and n.pin == pin]
        check(len(nodes) == 1, f"U3 pin {pin} expected on {net!r}, not found there: {nets[net]}")
    summary.append("U3 (isolated DC-DC) pin map confirmed: primary +12V/DGND, secondary raw ISO nodes.")

    # --- Reverse-polarity diode orientation, D1 (+12V) and D2 (-12V) -- worked through
    # explicitly in gen_breakout_power.py's own _place_inlet() docstring because the two
    # rails need OPPOSITE diode orientations and it is easy to get backwards; this is the
    # executable form of that derivation. A silently-reversed diode would still produce a
    # net literally named "+12V" or "-12V" (ERC-clean, netlist-plausible) while providing
    # NO reverse-polarity protection at all -- exactly the "plausible but wrong" failure
    # class this whole checker exists to catch, just at the component-orientation level
    # instead of the pin-resolution level. ---
    d1_k = [n for n in nets["+12V"] if n.ref == "D1" and n.pin == "1"]
    d1_a = [n for n in nets["P12_RAW"] if n.ref == "D1" and n.pin == "2"]
    check(len(d1_k) == 1, f"D1 cathode (pin 1) expected on +12V, not found: {nets['+12V']}")
    check(len(d1_a) == 1, f"D1 anode (pin 2) expected on P12_RAW, not found: {nets['P12_RAW']}")
    d2_a = [n for n in nets["-12V"] if n.ref == "D2" and n.pin == "2"]
    d2_k = [n for n in nets["N12_RAW"] if n.ref == "D2" and n.pin == "1"]
    check(len(d2_a) == 1, f"D2 anode (pin 2) expected on -12V, not found: {nets['-12V']}")
    check(len(d2_k) == 1, f"D2 cathode (pin 1) expected on N12_RAW, not found: {nets['N12_RAW']}")
    summary.append(
        "Reverse-polarity diodes correctly oriented: D1 anode->P12_RAW/cathode->+12V "
        "(source->load), D2 anode->-12V/cathode->N12_RAW (load->source, mirrored -- see "
        "_place_inlet()'s docstring for the derivation)."
    )

    # --- FB-divider topology + values for the three adjustable regulators (U1, U4, U5):
    # R_top between OUT and FB, R_bottom between FB and the regulator's own GND net, with
    # the specific 1%-standard values task-7-report.md derives/cites. Catches a swapped
    # R1/R2 (which would still "look like" a divider topologically but set the wrong
    # ratio) as well as a wrong or drifted value. ---
    divider_specs = [
        ("U1", "+5V", "DGND", "R1", "32.4k", "R2", "10.0k"),
        ("U4", "ISO_P12", "INTAN_GND", "R3", "90.9k", "R4", "10.0k"),
        ("U5", "ISO_N12", "INTAN_GND", "R5", "93.1k", "R6", "10.0k"),
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
        "FB-divider topology AND values confirmed: U1 R1=32.4k/R2=10.0k (Vout~5.02V), "
        "U4 R3=90.9k/R4=10.0k (Vout~11.96V), U5 R5=93.1k/R6=10.0k (TI's own published "
        "-12V pair) -- each R_top bridges OUT<->FB, each R_bottom bridges FB<->GND."
    )

    # --- Right part in the right role: the wl-sync custom symbols (TPS7A4901/TPS7A3001)
    # and the two real, specific, brief-named/report-justified parts (IH1215D, SS14) are
    # each the value gen_breakout_power.py actually intends for that reference -- catches
    # a copy-paste value mistake (e.g. U5 accidentally left as "TPS7A4901" instead of
    # "TPS7A3001", which would silently turn the -12V post-regulator into a POSITIVE
    # regulator wired backwards) that no net-topology check above would notice, since the
    # pin NUMBERS/NAMES are identical between the two parts by design (see
    # gen_wl_sync_lib.py's own comment on the shared TPS7A49_PINS table). ---
    expected_values = {
        "U1": "TPS7A4901", "U2": "LD1117S33TR_SOT223", "U3": "IH1215D",
        "U4": "TPS7A4901", "U5": "TPS7A3001", "D1": "SS14", "D2": "SS14",
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
    all_named = CONTRACT_NETS + ["P12_RAW", "N12_RAW"]
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

    # Divider resistor value drift: R1 (should be 32.4k) accidentally left/edited to a
    # different value -- topology still correct, only the VALUE is wrong.
    drifted_values = dict(good_values)
    drifted_values["R1"] = "10k"
    msg = _assert_fails(good_nets, drifted_values, "should be '32.4k'", "R1 value drift")
    results.append(f"R1 value drift (32.4k -> 10k, topology unchanged): caught -- {msg}")

    # Copy-paste part mix-up: U5 (should be TPS7A3001, the negative regulator) left as
    # TPS7A4901 -- exactly the failure class the "right part in the right role" check
    # exists for, since the two parts' pin numbers/names are identical.
    swapped_part = dict(good_values)
    swapped_part["U5"] = "TPS7A4901"
    msg = _assert_fails(good_nets, swapped_part, "U5: expected Value", "U5 part mix-up")
    results.append(f"U5 part mix-up (TPS7A3001 -> TPS7A4901, a copy-paste-shaped bug): caught -- {msg}")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
