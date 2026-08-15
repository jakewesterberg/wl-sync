"""Parse hardware/breakout's exported netlist (and, for the two checks the exported netlist
provably cannot answer -- coordinate collisions and the instance-path ancestor chain -- the
raw .kicad_sch source) and verify control-usb-i2c.kicad_sch's contract nets and topology are
electrically correct -- not just that ERC was silent. Same reasoning every prior checker in
this project gives, restated because it is why THIS file exists too: this is the LAST
schematic-capture sheet, and task-12-brief.md's own non-negotiable #5 ("verify every pin map
against the real datasheet... never hedge") applies here exactly as it did to every sheet
before it.

THE CENTRAL RISKS this file exists to catch, named explicitly by this task's own brief and
by this project's own recurring defect history:

  1. All 32 MUX{n}_A{bit} lines must be driven by the RIGHT MCP23017 GPIO pin -- a silently
     permuted GPIO-to-address-line assignment would still produce a fully-connected,
     ERC-clean netlist while scrambling which physical mux input a given address value
     actually reaches (the exact "plausible but wrong" class task-10c-report.md's own
     reversed-S9-S16 near-miss already demonstrated on a sibling sheet). Walked end to end
     per line, not assumed from net names alone.
  2. The two MCP23017 instances must carry DISTINCT I2C addresses, independently DECODED
     from their own real A2/A1/A0 pin-to-rail wiring (not merely read off a Value-string
     claim) -- and neither may collide with the MCP4728's own already-recorded 0x60.
  3. Neither MCP23017's own address pins may be ambiguous (a pin on both +3V3 and DGND at
     once, or on neither) -- the same "two candidates wired to one net is a short, not a
     redundancy" principle this project's own ADG1206 A3 near-miss (task-10c-report.md)
     already established, applied here to a 1-bit address field instead of a pin identity.
  4. Every I2C bus pull-up (2 of them: SDA, SCL) must land on +3V3 and NEVER +5V -- this
     sheet's own bus runs every device at 3.3V specifically to keep logic levels consistent
     (see gen_breakout_control_usb_i2c.py's own module docstring, MCP2221A POWER MODE); a
     +5V pull-up here would push an out-of-spec high onto every 3.3V-VDD device's own SDA/
     SCL input, the same class of hazard comparators.kicad_sch's own +3V3-not-+5V pull-up
     rule already guards against for a different net family.
  5. The MCP2221A's own VDD and VUSB must BOTH land on +3V3 and NEVER +5V (module docstring,
     MCP2221A POWER MODE) -- a silent drift to USB_VBUS_PI or +5V would push this bus's own
     master onto a mismatched logic-level domain from every other device on it.
  6. USB_DP_PI/USB_DM_PI must reach the MCP2221A's own real D+/D- pins (not merely be
     labelled) -- confirming this sheet actually "drives those nets" (task-12-brief.md's
     own Part A instruction), for the two USB signal pins this sheet DOES consume.
  7. No same-column real 2-pin part may have its own pin-to-pin reach overlap another's --
     the GENERAL form of this project's own recurring row-pitch-vs-2-pin-span defect class
     (CH_ROW_DY's own account in gen_breakout_opto_ni.py), checked structurally via the
     shared check_row_pitch_guard.py guard.

`verify()` below re-derives the full channel/address/pin contract independently of
gen_breakout_control_usb_i2c.py's own choices -- same "a checker that trusted the generator
would only be checking the generator against itself" discipline every prior checker in this
project already follows.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_control_usb_i2c_netlist.py [path/to/breakout.net]

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
DEFAULT_CONTROL_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "control-usb-i2c.kicad_sch"
CONTROL_SHEETFILE = "sheets/control-usb-i2c.kicad_sch"  # exactly as breakout.kicad_sch's own
# Sheetfile property spells it -- must match gen_breakout_control_usb_i2c.py's own constant.

# ---------------------------------------------------------------------------
# Contract constants -- redefined here from this task's own brief and the real Microchip
# datasheets, NEVER imported from the generator (same independence discipline every checker
# in this project already follows for its own contract nets/pin tables).
# ---------------------------------------------------------------------------
MCP2221A_VALUE = "MCP2221A"

MCP2221A_PIN_VDD = "1"
MCP2221A_PIN_VSS = "14"
MCP2221A_PIN_SDA = "9"
MCP2221A_PIN_SCL = "10"
MCP2221A_PIN_VUSB = "11"
MCP2221A_PIN_DM = "12"
MCP2221A_PIN_DP = "13"

MCP23017_PIN_A0 = "15"
MCP23017_PIN_A1 = "16"
MCP23017_PIN_A2 = "17"
MCP23017_PIN_SCL = "12"
MCP23017_PIN_SDA = "13"
MCP23017_PIN_VDD = "9"
MCP23017_PIN_VSS = "10"

GPA_PIN_NUMS = ["21", "22", "23", "24", "25", "26", "27", "28"]  # GPA0..GPA7
GPB_PIN_NUMS = ["1", "2", "3", "4", "5", "6", "7", "8"]          # GPB0..GPB7

EXPECTED_ADDRS = {0x20, 0x21}
MCP4728_ADDR = 0x60  # comparators.kicad_sch's own already-recorded factory default
MUX_BASE_BY_ADDR = {0x20: 1, 0x21: 5}  # this sheet's own declared GPIO-to-mux convention

PULLUP_OHMS = "2.2k"


def expander_channel_map(mux_base: int) -> list[tuple[str, str]]:
    """Independently re-derives the same 16 (pin_number, MUX{n}_A{bit} net) pairs
    gen_breakout_control_usb_i2c.py's own expander_channel_map() computes -- redefined
    here, not imported, so a change to the generator's own convention cannot silently
    escape this checker's notice."""
    pins = GPA_PIN_NUMS + GPB_PIN_NUMS
    nets = [f"MUX{mux_base + local_mux}_A{bit}" for local_mux in range(4) for bit in range(4)]
    return list(zip(pins, nets))


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


def _bridge_ref(values: dict[str, str]) -> str:
    refs = [r for r, v in values.items() if v == MCP2221A_VALUE]
    check(len(refs) == 1, f"expected exactly 1 {MCP2221A_VALUE!r} instance, found {len(refs)}: {refs}")
    return refs[0]


def _expander_refs(values: dict[str, str]) -> list[str]:
    refs = [r for r, v in values.items() if v.startswith("MCP23017")]
    check(len(refs) == 2, f"expected exactly 2 MCP23017 instances, found {len(refs)}: {refs}")
    return refs


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _decode_expander_addr(nets: dict[str, list[Node]], ref: str) -> int:
    """Independently DECODE a MCP23017 instance's own real I2C address from which rail
    (+3V3=1, DGND=0) each of its own A2/A1/A0 physical pins is actually tied to -- not
    from any Value-string claim (module docstring, risk 2/3)."""
    bits: dict[str, int] = {}
    for name, pin in (("a2", MCP23017_PIN_A2), ("a1", MCP23017_PIN_A1), ("a0", MCP23017_PIN_A0)):
        on_3v3 = any(n.ref == ref and n.pin == pin for n in nets.get("+3V3", []))
        on_dgnd = any(n.ref == ref and n.pin == pin for n in nets.get("DGND", []))
        check(on_3v3 or on_dgnd, f"{ref} pin {pin} ({name}) is on neither +3V3 nor DGND -- an ambiguous/floating address bit")
        check(not (on_3v3 and on_dgnd), f"{ref} pin {pin} ({name}) is on BOTH +3V3 and DGND -- shorted address bit, not a valid tie")
        bits[name] = 1 if on_3v3 else 0
    return 0x20 | (bits["a2"] << 2) | (bits["a1"] << 1) | bits["a0"]


def _check_addresses_distinct_and_expected(nets: dict[str, list[Node]], exp_refs: list[str]) -> tuple[str, dict[str, int]]:
    addr_by_ref = {ref: _decode_expander_addr(nets, ref) for ref in exp_refs}
    addrs = list(addr_by_ref.values())
    check(len(set(addrs)) == len(addrs), f"MCP23017 instances do not carry distinct I2C addresses: {addr_by_ref}")
    check(
        set(addrs) == EXPECTED_ADDRS,
        f"MCP23017 addresses {sorted(hex(a) for a in addrs)} do not match the expected set "
        f"{sorted(hex(a) for a in EXPECTED_ADDRS)}",
    )
    check(
        MCP4728_ADDR not in addrs,
        f"a MCP23017 instance decodes to 0x{MCP4728_ADDR:02X}, the same address "
        f"comparators.kicad_sch's own MCP4728 already uses at its factory default -- a "
        f"real I2C bus collision",
    )
    return (
        f"Both MCP23017 addresses independently decoded from real A2/A1/A0 pin wiring: "
        f"{ {r: hex(a) for r, a in addr_by_ref.items()} }, distinct from each other and "
        f"from the MCP4728's own 0x{MCP4728_ADDR:02X}.",
        addr_by_ref,
    )


def _check_mux_channels_end_to_end(nets: dict[str, list[Node]], addr_by_ref: dict[str, int]) -> str:
    """Walk all 32 MUX{n}_A{bit} lines: each must be driven by the exact GPIO pin this
    sheet's own declared convention assigns it, on the SAME reference whose own address was
    independently decoded above -- not merely present as a label (module docstring, risk 1).

    Each net also carries the RECEIVING ADG1206 mux's own address pin (mux-intan.kicad_sch,
    Task 10c -- that IS the point of the wire), so "no other driver" is checked against the
    other EXPANDER references specifically (a real cross-expander collision), not against
    every other IC on the net -- the mux itself is an expected, passive-typed receiver here.
    """
    seen_nets: set[str] = set()
    all_exp_refs = set(addr_by_ref)
    for ref, addr in addr_by_ref.items():
        mux_base = MUX_BASE_BY_ADDR[addr]
        for pin_num, net in expander_channel_map(mux_base):
            check(net in nets, f"missing contract net: {net!r}")
            hits = [n for n in nets[net] if n.ref == ref and n.pin == pin_num]
            check(
                len(hits) == 1,
                f"{net}: expected {ref} pin {pin_num} on this net (expander at 0x{addr:02X}, "
                f"this sheet's own GPIO-to-mux convention), found {[n for n in nets[net] if n.ref == ref]} "
                f"-- a permuted GPIO-to-address-line assignment",
            )
            other_expander_nodes = [n for n in nets[net] if n.ref in all_exp_refs and n.ref != ref]
            check(
                not other_expander_nodes,
                f"{net}: also carries a node from a DIFFERENT expander ({other_expander_nodes}) "
                f"-- two expanders driving the same address line",
            )
            seen_nets.add(net)
    all_expected = {f"MUX{n}_A{b}" for n in range(1, 9) for b in range(4)}
    check(len(all_expected) == 32, "internal inconsistency: expected 32 distinct MUX{n}_A{b} nets")
    check(
        seen_nets == all_expected,
        f"expected exactly the 32 contract nets {sorted(all_expected)} to be walked, got "
        f"{len(seen_nets)}: missing {sorted(all_expected - seen_nets)}, extra {sorted(seen_nets - all_expected)}",
    )
    return f"All 32 MUX{{n}}_A{{bit}} lines walked end to end onto their own expander's real, address-consistent GPIO pin -- no permutation, no cross-expander collision."


def _check_bridge_pins(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """MCP2221A: SDA/SCL on the I2C bus, D+/D- on the USB header's own contract nets,
    VDD/VUSB on +3V3 and NEVER +5V, VSS on DGND (module docstring, risks 4/5/6)."""
    ref = _bridge_ref(values)
    checks = [
        ("I2C_SDA", MCP2221A_PIN_SDA), ("I2C_SCL", MCP2221A_PIN_SCL),
        ("USB_DP_PI", MCP2221A_PIN_DP), ("USB_DM_PI", MCP2221A_PIN_DM),
        ("+3V3", MCP2221A_PIN_VDD), ("+3V3", MCP2221A_PIN_VUSB),
        ("DGND", MCP2221A_PIN_VSS),
    ]
    for net, pin in checks:
        check(net in nets, f"missing net: {net!r}")
        check(
            any(n.ref == ref and n.pin == pin for n in nets[net]),
            f"{ref} pin {pin} not found on {net!r} -- expected connection missing",
        )
    on_5v = [n for n in nets.get("+5V", []) if n.ref == ref]
    check(
        not on_5v,
        f"{ref} (MCP2221A) has a pin on +5V ({on_5v}) -- this bus is deliberately run at "
        f"+3V3 for logic-level consistency across every device (module docstring, MCP2221A "
        f"POWER MODE); a +5V-referenced bridge would violate that by construction",
    )
    on_vbus = [n for n in nets.get("USB_VBUS_PI", []) if n.ref == ref]
    check(
        not on_vbus,
        f"{ref} (MCP2221A) unexpectedly has a pin on USB_VBUS_PI ({on_vbus}) -- this "
        f"design deliberately runs 3.3V self-powered and does not consume VBUS (module "
        f"docstring, MCP2221A POWER MODE)",
    )
    return (
        f"{ref} (MCP2221A): SDA/SCL on the I2C bus, D+/D- on USB_DP_PI/USB_DM_PI, VDD "
        f"and VUSB both on +3V3 (never +5V), VSS on DGND, and no pin on USB_VBUS_PI "
        f"(deliberately unconnected)."
    )


def _check_pullups(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """Both I2C bus pull-ups (SDA, SCL) bridge +3V3<->their own net at 2.2k, and NEITHER
    has any pin on +5V (module docstring, risk 4 -- the destroy-hardware-adjacent class of
    hazard for a 3.3V-VDD bus)."""
    check("+3V3" in nets, "missing net: '+3V3'")
    for net in ("I2C_SDA", "I2C_SCL"):
        r_ref = _find_bridging_resistor(nets, "+3V3", net)
        check(values.get(r_ref) == PULLUP_OHMS, f"{r_ref} ({net}'s own pull-up): expected Value {PULLUP_OHMS!r}, found {values.get(r_ref)!r}")
        on_5v = [n for n in nets.get("+5V", []) if n.ref == r_ref]
        check(not on_5v, f"{r_ref} ({net}'s own pull-up) also has a pin on +5V ({on_5v}) -- I2C bus pull-ups MUST go to +3V3, NEVER +5V")
    return f"Both I2C bus pull-ups (I2C_SDA, I2C_SCL) confirmed at {PULLUP_OHMS!r}, bridging +3V3, neither on +5V."


def _check_expander_bus_and_power(nets: dict[str, list[Node]], exp_refs: list[str]) -> str:
    """Every MCP23017's own SDA/SCL/VDD/VSS lands correctly, and none has a pin on +5V."""
    for ref in exp_refs:
        for net, pin in (
            ("I2C_SDA", MCP23017_PIN_SDA), ("I2C_SCL", MCP23017_PIN_SCL),
            ("+3V3", MCP23017_PIN_VDD), ("DGND", MCP23017_PIN_VSS),
        ):
            check(net in nets, f"missing net: {net!r}")
            check(any(n.ref == ref and n.pin == pin for n in nets[net]), f"{ref} pin {pin} not found on {net!r}")
        on_5v = [n for n in nets.get("+5V", []) if n.ref == ref]
        check(not on_5v, f"{ref} (MCP23017) has a pin on +5V ({on_5v}) -- this bus runs at +3V3 only")
    return f"Both MCP23017 instances ({exp_refs}) confirmed on I2C_SDA/I2C_SCL/+3V3/DGND, neither on +5V."


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    """Scans control-usb-i2c.kicad_sch's OWN raw rendered text for every global_label's
    real (x, y) and asserts no two DIFFERENT net names ever share one -- this project has
    hit exactly this defect class before (analog-frontend.kicad_sch's own
    task-10a-report.md; gen_breakout_opto_ni.py's own CH_ROW_DY account)."""
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 30, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really control-usb-i2c.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in control-usb-i2c.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short: {list(collisions.items())[:5]}",
    )
    return f"No coordinate collisions: all {len(coords)} distinct global-label positions in control-usb-i2c.kicad_sch carry exactly one net name each."


def self_test_collision(good_sch_text: str) -> str:
    matches = list(_GLOBAL_LABEL_RE.finditer(good_sch_text))
    check(len(matches) > 30, "self-test setup failed: too few global labels found to corrupt")
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


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    exp_refs = _expander_refs(values)
    addr_summary, addr_by_ref = _check_addresses_distinct_and_expected(nets, exp_refs)
    summary.append(addr_summary)
    summary.append(_check_mux_channels_end_to_end(nets, addr_by_ref))
    summary.append(_check_bridge_pins(nets, values))
    summary.append(_check_pullups(nets, values))
    summary.append(_check_expander_bus_and_power(nets, exp_refs))

    for rail in ("+3V3", "DGND", "I2C_SDA", "I2C_SCL"):
        check(rail in nets, f"missing rail/bus net: {rail!r}")
        check(len(nets[rail]) >= 4, f"{rail}: suspiciously small population ({len(nets[rail])} nodes)")
    summary.append("Rails/bus nets (+3V3/DGND/I2C_SDA/I2C_SCL) present with substantial populations.")
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
    exp_refs = _expander_refs(good_values)
    _addr_summary, addr_by_ref = _check_addresses_distinct_and_expected(good_nets, exp_refs)
    ref_20 = next(r for r, a in addr_by_ref.items() if a == 0x20)
    ref_21 = next(r for r, a in addr_by_ref.items() if a == 0x21)

    # (1) GPIO-to-mux permutation: MUX1_A0 and MUX1_A1's own driver nodes swapped
    # (simulating a generator edit that scrambles the address-bit-to-GPIO-pin order).
    swapped = copy.deepcopy(good_nets)
    a0_idx = next(i for i, n in enumerate(swapped["MUX1_A0"]) if n.ref == ref_20)
    a1_idx = next(i for i, n in enumerate(swapped["MUX1_A1"]) if n.ref == ref_20)
    swapped["MUX1_A0"][a0_idx], swapped["MUX1_A1"][a1_idx] = (
        swapped["MUX1_A1"][a1_idx], swapped["MUX1_A0"][a0_idx],
    )
    msg = _assert_fails(swapped, good_values, "a permuted GPIO-to-address-line assignment", "MUX1_A0/MUX1_A1 GPIO pins swapped")
    results.append(f"GPIO-to-mux-address-line permutation (MUX1_A0 <-> MUX1_A1 on {ref_20}): caught -- {msg}")

    # (2) Address collision: the expander at 0x21 has its own A0 pin moved from +3V3 to
    # DGND, so it now decodes to 0x20 too -- a real I2C bus address collision.
    collided = copy.deepcopy(good_nets)
    a0_node = next(n for n in good_nets["+3V3"] if n.ref == ref_21 and n.pin == MCP23017_PIN_A0)
    collided["+3V3"] = [n for n in collided["+3V3"] if n != a0_node]
    collided.setdefault("DGND", []).append(a0_node)
    msg = _assert_fails(collided, good_values, "do not carry distinct I2C addresses", f"{ref_21}'s own A0 moved DGND-ward, colliding with {ref_20}'s own 0x20")
    results.append(f"I2C address collision (both expanders decode to 0x20, {ref_21}'s own A0 forced low): caught -- {msg}")

    # (3) Ambiguous address bit: a THIRD, stray node added to an address pin's net on the
    # OTHER rail too, so that bit reads on both +3V3 and DGND simultaneously (a short, not
    # a valid tie -- module docstring, risk 3, the ADG1206-A3-hedge class of defect).
    ambiguous = copy.deepcopy(good_nets)
    a2_node = next(n for n in good_nets["DGND"] if n.ref == ref_20 and n.pin == MCP23017_PIN_A2)
    ambiguous.setdefault("+3V3", []).append(a2_node)
    msg = _assert_fails(ambiguous, good_values, "is on BOTH +3V3 and DGND", f"{ref_20}'s own A2 shorted onto both +3V3 and DGND")
    results.append(f"Ambiguous/shorted address bit ({ref_20}'s own A2 on both rails): caught -- {msg}")

    # (4) I2C pull-up value drift.
    drifted = dict(good_values)
    r_scl = _find_bridging_resistor(good_nets, "+3V3", "I2C_SCL")
    drifted[r_scl] = "4.7k"
    msg = _assert_fails(good_nets, drifted, "expected Value", f"{r_scl} (I2C_SCL's own pull-up) value drift 2.2k->4.7k")
    results.append(f"I2C pull-up value drift (2.2k -> 4.7k, I2C_SCL, {r_scl}): caught -- {msg}")

    # (5) THE destroy-hazard-adjacent negative control this sheet's own brief-derived
    # discipline explicitly asks for: an I2C pull-up ALSO wired to +5V (would push an
    # out-of-spec high onto every 3.3V-VDD device's own SDA/SCL input).
    also_5v = copy.deepcopy(good_nets)
    r_sda = _find_bridging_resistor(good_nets, "+3V3", "I2C_SDA")
    stray = next(n for n in good_nets["+3V3"] if n.ref == r_sda)
    also_5v.setdefault("+5V", []).append(stray)
    msg = _assert_fails(also_5v, good_values, "also has a pin on +5V", f"{r_sda} (I2C_SDA's own pull-up) also wired to +5V")
    results.append(f"I2C pull-up also wired to +5V (I2C_SDA, {r_sda}): caught -- {msg}")

    # (6) MCP2221A itself drifted onto +5V (simulating a bus-powered-mode regression).
    bridge_ref = _bridge_ref(good_values)
    bridge_5v = copy.deepcopy(good_nets)
    bridge_vdd_node = next(n for n in good_nets["+3V3"] if n.ref == bridge_ref and n.pin == MCP2221A_PIN_VDD)
    bridge_5v.setdefault("+5V", []).append(bridge_vdd_node)
    msg = _assert_fails(bridge_5v, good_values, "has a pin on +5V", f"{bridge_ref} (MCP2221A) VDD also wired to +5V")
    results.append(f"MCP2221A drifted onto +5V (bus-powered-mode regression, {bridge_ref}): caught -- {msg}")

    return results


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker.
# ---------------------------------------------------------------------------


def verify_instance_paths(control_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(control_sch_text)
    check(own_root_uuid != breakout_root_uuid, f"control-usb-i2c.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate")
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, CONTROL_SHEETFILE)

    paths = find_all_instance_paths(control_sch_text)
    check(
        len(paths) >= 7,
        f"only found {len(paths)} (instances (path ...)) entries in control-usb-i2c.kicad_sch "
        f"-- expected >=7 (recomputed directly against this task's own real output: 9 placed "
        f"instances -- 1 MCP2221A + 2 MCP23017 + 2 I2C pull-up resistors + 4 decoupling caps "
        f"-- the >=7 floor is intentionally left below that real count so a future small "
        f"edit doesn't need this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(not bad, f"{len(bad)}/{len(paths)} component instance paths in control-usb-i2c.kicad_sch do not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}")
    return f"All {len(paths)} component instance paths in control-usb-i2c.kicad_sch resolve to the real ancestor chain {expected_prefix!r}."


def _assert_instance_paths_fail(control_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(control_text, breakout_text)
    except CheckFailure as e:
        check(expect_substring in str(e), f"self-test {label!r}: wrong complaint: {e}")
        return str(e)
    raise CheckFailure(f"self-test {label!r}: verify_instance_paths() did NOT raise")


def self_test_instance_paths(good_control_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_control_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, CONTROL_SHEETFILE)
    corrupted = good_control_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(corrupted != good_control_text, "self-test setup failed: no occurrence of the expected ancestor path found to corrupt")
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_CONTROL_SCH.exists():
        print(f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_CONTROL_SCH} do not exist")
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    control_sch_text = DEFAULT_CONTROL_SCH.read_text()

    try:
        collision_summary = _check_no_coordinate_collisions(control_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {collision_summary}")

    try:
        collision_self_test_msg = self_test_collision(control_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: coordinate collision reintroduced: caught -- {collision_self_test_msg}")

    try:
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(control_sch_text, "control-usb-i2c.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(control_sch_text, "control-usb-i2c.kicad_sch", min_instances=5)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

    try:
        path_summary = verify_instance_paths(control_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(control_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
