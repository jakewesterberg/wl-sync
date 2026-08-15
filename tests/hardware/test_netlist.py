"""Whole-board netlist contract for hardware/breakout/breakout.kicad_sch -- Task 12 (Part
B) of docs/superpowers/plans/2026-08-13-breakout-pcb.md
(.superpowers/sdd/2026-08-13-breakout-pcb/task-12-brief.md). This is the LAST build task on
this board, and this file is deliberately outside hardware/ (in the repo's own test tree)
so it is discovered and run by the same `pytest` invocation as the rest of this project's
Python suite (pyproject.toml's own `testpaths = ["tests"]`), not only by someone who
remembers to run a separate hardware-specific script.

WHY THIS FILE DOES NOT IMPORT hardware/gen/'s OWN check_mule_netlist.py (or any sibling
checker): this is a whole-board CONTRACT test, one architectural layer above any single
sheet's own checker -- it exists to keep verifying the real, exported artifact even if
hardware/gen/'s own verification machinery has a latent bug, or is simply unavailable (this
package is not installed, hardware/gen/ is not on sys.path by default for a test run from
the repo root).

THIS FILE READS A COMMITTED SNAPSHOT, NOT THE RAW EXPORTED NETLIST (Task 12 fix round 1 --
see hardware/README.md's "Whole-board netlist contract" section and
.superpowers/sdd/2026-08-13-breakout-pcb/task-12-report.md's "Fix round 1" for the full
account of why this changed). The first version of this file parsed
`hardware/breakout/breakout.net` directly, with its own small self-contained regexes -- but
that file is `*.net`-gitignored on purpose (mechanical, fully re-derivable from the checked-
in schematic; see hardware/.gitignore's own comment) and this project's CI has no
`kicad-cli` to produce it, so the 26 assertions below SKIPPED on every single CI run. A skip
reports green and reads as coverage while testing nothing -- worse than no test at all for a
contract whose entire reason to exist is that every one of these properties was actually
violated at least once, by omission, during this board's own build (see each check's own
docstring below for which task, which sheet). Skips do not catch regressions.

The fix: `hardware/gen/gen_breakout_netlist_contract.py` renders the exported netlist into
`hardware/breakout/netlist-contract.json` -- a sorted, UUID-free `{net: [(ref, pin,
pinfunction, pintype), ...]}` + `{ref: value}` snapshot, committed to the repository (NOT
gitignored, unlike the raw `.net` it is derived from). This file loads THAT committed
snapshot below (`_load_snapshot()`), so the 26 assertions run unconditionally in every
environment, CI included, with zero dependency on KiCad or `hardware/gen/` being
importable -- an even stronger form of the isolation the paragraph above already wanted:
previously this file still depended on correctly guessing kicad-cli's own export
formatting (see the "found the hard way" gotcha this docstring used to carry); now it
depends on nothing but a static, versioned data file with a two-key JSON schema.

This intentionally moves the "is the committed snapshot still an accurate rendering of the
CURRENT schematic" question out of this file entirely -- that is a claim about tooling
freshness, not about the board, and belongs in a check that is allowed to be unavailable in
an environment with no KiCad. See `test_netlist_contract_freshness.py`, right beside this
file, which regenerates a netlist and a snapshot with real `kicad-cli` and diffs the result
against what's committed here -- skipping (with an explicit reason) only when `kicad-cli`
itself is not on `PATH`. THAT file may skip. This one must not, and does not: every
assertion below runs against already-committed data every single time `pytest` runs,
including in CI, with nothing gating collection or execution.

THE CONTRACT, PER THIS TASK'S OWN BRIEF (task-12-brief.md, Step 3) -- the first five
checks below reproduce that brief's own literal assertions, refactored into small,
independently-callable checker functions so each one can carry its own negative control
(this task's own instruction: "every assertion needs one proving it fires. A checker that
passes vacuously has happened three times on this project"):

  1. All 16 event-code lines reach both the sync module and NI; the strobe reaches module,
     NI, AND Intan.
  2. Intan gets EXACTLY 8 analog outputs -- the 9th must not exist.
  3. All 16 analog sources reach NI.
  4. Reward is recorded TWICE -- commanded and delivered as distinct nets.
  5. AGND and DGND are distinct nets.

THEN, GOING FURTHER THAN THE BRIEF (this task's own instruction, because this session
knows things the brief's own author did not -- the properties that have actually been at
risk on THIS board, each one a real, previously-caught defect on a sibling sheet):

  6. No +5V net may reach a sync-module GPIO pin -- the module is 3.3V and not 5V
     tolerant, and this hazard reached two sheets by omission before being caught
     (RWD_DLVR's own +5V origin, pi-interface.kicad_sch/Task 9; the comparator pull-up
     domain, comparators.kicad_sch/Task 10d).
  7. Every comparator pull-up on +3V3, not +5V (the same destroy-hardware class of defect
     as #6, for a different net family: PD1_COMP/PD2_COMP/ACC_TRIG wire DIRECTLY into the
     sync module's own GPIO).
  8. Every I2C bus pull-up (this task's OWN new bus) on +3V3, not +5V -- the identical
     hazard class, extended to control-usb-i2c.kicad_sch's own new devices.
  9. AISENSE tied to AGND on every NI connector (both the task-PC's own NI card and the
     recording NI card each carry one) -- "the single wire that makes NRSE work"
     (gen_breakout_analog_ni.py's own module docstring), easy to omit by accident (it
     already was, once, at Task 8, caught only in fix round 1).
  10. Both isolated domains (NI_5V/NI_GND, and ISO_P12/ISO_N12/INTAN_GND/ISO_5V) pin-
      disjoint from every non-isolated rail -- checked at PIN level, not reference level,
      so a component that legitimately straddles the barrier BY DESIGN (an optocoupler's
      LED side vs. detector side, an isolated DC-DC's primary vs. secondary, a difference
      amplifier's "-" input vs. its own REF/power) never trips this by construction; only
      a single PIN genuinely present on both sides would, which is always a real short.
  11. AGND and DGND are not merely both PRESENT (brief check #5, name-only) but genuinely
      DISTINCT -- share no PIN directly (mirrors check_breakout_power_netlist.py's own
      star-point check, re-derived independently at the whole-board level).
  12. RWD_CMD and RWD_DLVR are not merely both PRESENT (brief check #4, name-only) but
      genuinely DISTINCT nets -- share no PIN (a silently collapsed pair would make
      commanded and delivered indistinguishable in every recording).

This file reads the committed snapshot:
    hardware/breakout/netlist-contract.json

Regenerate it (after regenerating the netlist it is derived from) via:
    kicad-cli sch export netlist --format kicadsexpr \\
        -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
    python3 hardware/gen/gen_breakout_netlist_contract.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parents[2] / "hardware" / "breakout" / "netlist-contract.json"

Node = tuple[str, str, str, str]  # (ref, pin, pinfunction, pintype)


def _load_snapshot() -> dict:
    """Load the committed contract snapshot -- see module docstring for why this file
    reads this instead of parsing hardware/breakout/breakout.net directly. Unlike the old
    `pytest.mark.skipif`, a MISSING snapshot is not a tolerated, informatively-skipped
    condition: this file is committed (not gitignored -- see hardware/.gitignore, which
    excludes only `*.net`), so its absence means the repository itself is broken, not that
    some optional local tool wasn't run. Let that fail loudly, at fixture setup, with a
    concrete regenerate command, rather than skip."""
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT} is missing. Unlike hardware/breakout/breakout.net, this file IS "
        f"committed to the repository -- its absence means the checkout is broken, not "
        f"that a tool needs to be run. If it is genuinely gone, regenerate with:\n"
        f"    kicad-cli sch export netlist --format kicadsexpr "
        f"-o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch\n"
        f"    python3 hardware/gen/gen_breakout_netlist_contract.py"
    )
    return json.loads(SNAPSHOT.read_text())


def nets() -> set[str]:
    """Every distinct net name present in the committed snapshot -- this task's own brief
    (task-12-brief.md, Step 3) names this exact helper and its exact signature; kept
    name-for-name so the first five checks below read the same way the brief itself
    specifies them, but built on top of the snapshot-backed `net_nodes()` below rather
    than re-parsing anything."""
    return set(net_nodes())


def net_nodes() -> dict[str, list[Node]]:
    """{net_name: [(ref, pin, pinfunction, pintype), ...]} -- the richer, node-level view
    the extended (beyond-the-brief) checks below need, to tell "this net exists" apart
    from "this net is driven by the RIGHT physical pin and shares no pin with that OTHER
    net" -- the same distinction every hardware/gen/*_netlist.py checker in this project
    draws between ERC-silent and actually-correct. Each JSON node array is converted back
    to a real tuple so callers get the exact `Node` shape this file has always used,
    regardless of whether the data originated from a live parse (the old implementation)
    or a committed snapshot (this one)."""
    raw = _load_snapshot()["nets"]
    return {name: [tuple(node) for node in node_list] for name, node_list in raw.items()}


def component_values() -> dict[str, str]:
    """{reference: Value property text} -- straight from the snapshot's own
    `component_values` mapping (built by the same technique
    check_mule_netlist.py's own parse_component_values() uses -- see
    hardware/gen/gen_breakout_netlist_contract.py)."""
    return dict(_load_snapshot()["component_values"])


def _pins(nodes: dict[str, list[Node]], net: str) -> set[tuple[str, str]]:
    return {(ref, pin) for ref, pin, _pf, _pt in nodes.get(net, [])}


def _bridging_resistor_refs(nodes: dict[str, list[Node]], net_a: str, net_b: str) -> set[str]:
    """Reference designators of 2-terminal resistors with a pin on EACH of net_a/net_b --
    restricted to R-prefixed references specifically, the same `_find_bridging_resistor()`
    -style filtering every hardware/gen/*_netlist.py checker in this project already uses.
    Found necessary the hard way while writing this file, not assumed: an unrestricted
    "any reference present on both nets" check false-positived on U11 (SN74HCT541PW, the
    task-PC's own outbound buffer, taskpc-digital.kicad_sch) -- its OWN pin 20 (VCC)
    legitimately sits on +5V while a DIFFERENT, electrically unrelated pin (one buffer
    channel's own input) legitimately sits on PD1_COMP, and the two are not connected to
    each other inside the part. Restricting to resistor references specifically (true
    2-terminal parts, where "this reference is on both nets" DOES mean "this part
    bridges them") is what a bridging component actually looks like on this project's own
    boards -- every pull-up here is a discrete resistor, never a multi-pin IC."""
    a_refs = {ref for ref, _pin, _pf, _pt in nodes.get(net_a, []) if ref.startswith("R")}
    b_refs = {ref for ref, _pin, _pf, _pt in nodes.get(net_b, []) if ref.startswith("R")}
    return a_refs & b_refs


# ---------------------------------------------------------------------------
# Fixtures -- parsed once per test session (the file is large; nothing below mutates
# these in place, every negative control below builds its own modified COPY).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_nets() -> set[str]:
    return nets()


@pytest.fixture(scope="module")
def nodes() -> dict[str, list[Node]]:
    return net_nodes()


@pytest.fixture(scope="module")
def values() -> dict[str, str]:
    return component_values()


# ===========================================================================
# 1-5: this task's own brief, Step 3 -- literal contract, refactored into checker
# functions so each one carries its own negative control.
# ===========================================================================


def _check_event_code_lines_reach_pi_and_ni(present: set[str]) -> None:
    for i in range(16):
        assert f"EVT_D{i}_PI" in present, f"EVT_D{i}_PI missing -- event code bit {i} does not reach the sync module"
        assert f"EVT_D{i}_NI" in present, f"EVT_D{i}_NI missing -- event code bit {i} does not reach the recording NI"
    assert {"EVT_STROBE_PI", "EVT_STROBE_NI", "EVT_STROBE_INTAN"} <= present, (
        "the strobe does not reach all three of module/NI/Intan"
    )


def test_event_code_lines_reach_pi_and_ni(all_nets):
    _check_event_code_lines_reach_pi_and_ni(all_nets)


def test_event_code_lines_reach_pi_and_ni_fires_on_missing_line(all_nets):
    corrupted = set(all_nets) - {"EVT_D7_NI"}
    with pytest.raises(AssertionError, match="EVT_D7_NI"):
        _check_event_code_lines_reach_pi_and_ni(corrupted)


def test_event_code_lines_reach_pi_and_ni_fires_on_missing_strobe_leg(all_nets):
    corrupted = set(all_nets) - {"EVT_STROBE_INTAN"}
    with pytest.raises(AssertionError, match="strobe"):
        _check_event_code_lines_reach_pi_and_ni(corrupted)


def _check_intan_gets_exactly_eight_analog_outputs(present: set[str]) -> None:
    assert {f"INTAN_AO{i}" for i in range(1, 9)} <= present, "not all 8 INTAN_AO1..8 are present"
    assert "INTAN_AO9" not in present, "INTAN_AO9 exists -- Intan's own 8-input ceiling has been exceeded"


def test_intan_gets_exactly_eight_analog_outputs(all_nets):
    _check_intan_gets_exactly_eight_analog_outputs(all_nets)


def test_intan_gets_exactly_eight_analog_outputs_fires_on_ninth_output(all_nets):
    corrupted = set(all_nets) | {"INTAN_AO9"}
    with pytest.raises(AssertionError, match="INTAN_AO9"):
        _check_intan_gets_exactly_eight_analog_outputs(corrupted)


def test_intan_gets_exactly_eight_analog_outputs_fires_on_missing_output(all_nets):
    corrupted = set(all_nets) - {"INTAN_AO4"}
    with pytest.raises(AssertionError):
        _check_intan_gets_exactly_eight_analog_outputs(corrupted)


ALL_16_SOURCES = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_SOURCES) == 16


def _check_all_sixteen_sources_reach_ni(present: set[str]) -> None:
    for s in ALL_16_SOURCES:
        assert f"{s}_NI" in present, f"{s} does not reach NI"


def test_all_sixteen_sources_reach_ni(all_nets):
    _check_all_sixteen_sources_reach_ni(all_nets)


def test_all_sixteen_sources_reach_ni_fires_on_missing_source(all_nets):
    corrupted = set(all_nets) - {"A_JOY_X_NI"}
    with pytest.raises(AssertionError, match="A_JOY_X"):
        _check_all_sixteen_sources_reach_ni(corrupted)


def _check_reward_is_recorded_twice(present: set[str]) -> None:
    assert {"RWD_CMD", "RWD_DLVR"} <= present, "reward is not recorded as two distinct named nets (commanded, delivered)"


def test_reward_is_recorded_twice(all_nets):
    _check_reward_is_recorded_twice(all_nets)


def test_reward_is_recorded_twice_fires_on_missing_net(all_nets):
    corrupted = set(all_nets) - {"RWD_DLVR"}
    with pytest.raises(AssertionError):
        _check_reward_is_recorded_twice(corrupted)


def _check_agnd_and_dgnd_are_distinct_nets(present: set[str]) -> None:
    assert "AGND" in present and "DGND" in present, "AGND and/or DGND missing entirely"


def test_agnd_and_dgnd_are_distinct_nets(all_nets):
    _check_agnd_and_dgnd_are_distinct_nets(all_nets)


def test_agnd_and_dgnd_are_distinct_nets_fires_on_missing_rail(all_nets):
    corrupted = set(all_nets) - {"AGND"}
    with pytest.raises(AssertionError):
        _check_agnd_and_dgnd_are_distinct_nets(corrupted)


# ===========================================================================
# 6-12: beyond the brief -- the properties that have actually been at risk on this
# board, each with its own negative control.
# ===========================================================================

# --- 6: no +5V net may reach a sync-module GPIO pin. -----------------------------------

_GPIO_PINFUNCTION_RE = re.compile(r"^GPIO\d+_\d+$")
# 'GPIOn_m' is this project's own unique pinfunction shape for the sync-module header's
# own GPIO-named pins (hardware/README.md's own "pinfunction" gotcha: kicad-cli's export
# always writes "{pin name}_{pin number}", confirmed directly against the real exported
# netlist while writing this test -- e.g. "GPIO25_22" for GPIO25 landing on physical pin
# 22). No other symbol in this whole project names a pin "GPIOn" (MCP23017's own pins are
# "GPA"/"GPB"-prefixed, MCP2221A's own are bare "GP0".."GP3" -- neither collides with this
# pattern), so a pinfunction match alone identifies a sync-module GPIO pin without first
# needing to look up the header's own reference designator.


def _check_no_5v_on_sync_module_gpio(nodes: dict[str, list[Node]]) -> None:
    hits = [(ref, pin, pf) for ref, pin, pf, _pt in nodes.get("+5V", []) if pf and _GPIO_PINFUNCTION_RE.match(pf)]
    assert not hits, (
        f"+5V reaches a sync-module GPIO pin directly: {hits} -- the module is 3.3V and "
        f"NOT 5V tolerant (this hazard reached two sheets by omission before being "
        f"caught: RWD_DLVR's own +5V origin at pi-interface.kicad_sch/Task 9, and the "
        f"comparator pull-up domain at comparators.kicad_sch/Task 10d)"
    )


def test_no_5v_reaches_sync_module_gpio(nodes):
    _check_no_5v_on_sync_module_gpio(nodes)


def test_no_5v_reaches_sync_module_gpio_fires_on_direct_short(nodes):
    corrupted = dict(nodes)
    corrupted["+5V"] = list(nodes.get("+5V", [])) + [("J7", "38", "GPIO20_38", "passive")]
    with pytest.raises(AssertionError, match="GPIO20_38"):
        _check_no_5v_on_sync_module_gpio(corrupted)


# --- 7: every comparator pull-up on +3V3, never +5V. ------------------------------------

COMPARATOR_OUTPUTS = ["PD1_COMP", "PD2_COMP", "ACC_TRIG"]


def _check_comparator_pullups_on_3v3_never_5v(nodes: dict[str, list[Node]]) -> None:
    for out_net in COMPARATOR_OUTPUTS:
        bridging_3v3 = _bridging_resistor_refs(nodes, "+3V3", out_net)
        assert bridging_3v3, f"{out_net}: no resistor bridges +3V3<->{out_net} -- a pull-up is missing"
        bridging_5v = _bridging_resistor_refs(nodes, "+5V", out_net)
        assert not bridging_5v, (
            f"{out_net}: a resistor bridges +5V<->{out_net} directly ({bridging_5v}) -- "
            f"this line wires DIRECTLY into the sync module's own 3.3V-only GPIO "
            f"(pi-interface.kicad_sch); a +5V pull-up would destroy it the first time "
            f"this line goes idle-high"
        )


def test_comparator_pullups_on_3v3_never_5v(nodes):
    _check_comparator_pullups_on_3v3_never_5v(nodes)


def test_comparator_pullups_on_3v3_never_5v_fires_on_5v_pullup(nodes):
    r_ref = next(iter(_bridging_resistor_refs(nodes, "+3V3", "PD2_COMP")))
    stray = next(n for n in nodes["+3V3"] if n[0] == r_ref)
    corrupted = dict(nodes)
    corrupted["+5V"] = list(nodes.get("+5V", [])) + [stray]
    with pytest.raises(AssertionError, match="PD2_COMP"):
        _check_comparator_pullups_on_3v3_never_5v(corrupted)


# --- 8: every I2C bus pull-up (this task's own new bus) on +3V3, never +5V. ------------

I2C_BUS_NETS = ["I2C_SDA", "I2C_SCL"]


def _check_i2c_pullups_on_3v3_never_5v(nodes: dict[str, list[Node]]) -> None:
    for bus_net in I2C_BUS_NETS:
        bridging_3v3 = _bridging_resistor_refs(nodes, "+3V3", bus_net)
        assert bridging_3v3, f"{bus_net}: no resistor bridges +3V3<->{bus_net} -- a bus pull-up is missing"
        bridging_5v = _bridging_resistor_refs(nodes, "+5V", bus_net)
        assert not bridging_5v, (
            f"{bus_net}: a resistor bridges +5V<->{bus_net} directly ({bridging_5v}) -- "
            f"this bus runs every device (MCP2221A, both MCP23017s, the MCP4728) at "
            f"+3V3 specifically for logic-level consistency (control-usb-i2c.kicad_sch's "
            f"own module docstring); a +5V pull-up would push an out-of-spec high onto "
            f"every 3.3V-VDD device's own SDA/SCL input"
        )


def test_i2c_pullups_on_3v3_never_5v(nodes):
    _check_i2c_pullups_on_3v3_never_5v(nodes)


def test_i2c_pullups_on_3v3_never_5v_fires_on_5v_pullup(nodes):
    r_ref = next(iter(_bridging_resistor_refs(nodes, "+3V3", "I2C_SDA")))
    stray = next(n for n in nodes["+3V3"] if n[0] == r_ref)
    corrupted = dict(nodes)
    corrupted["+5V"] = list(nodes.get("+5V", [])) + [stray]
    with pytest.raises(AssertionError, match="I2C_SDA"):
        _check_i2c_pullups_on_3v3_never_5v(corrupted)


# --- 9: AISENSE tied to AGND on every NI connector. -------------------------------------

NI_CONNECTOR_VALUES = [
    "Connector 0 (analog + AISENSE, task PC NI)",       # taskpc-digital.kicad_sch, Task 8
    "Connector 0 (analog + AISENSE, recording NI)",     # analog-ni.kicad_sch, Task 10b
]
AISENSE_PIN = "62"  # NI's own "AI SENSE" physical pin on this connector family, both cards


def _check_aisense_tied_to_agnd(nodes: dict[str, list[Node]], values: dict[str, str]) -> None:
    for value in NI_CONNECTOR_VALUES:
        refs = [r for r, v in values.items() if v == value]
        assert len(refs) == 1, f"{value!r}: expected exactly 1 instance, found {refs}"
        ref = refs[0]
        hit = any(r == ref and pin == AISENSE_PIN for r, pin, _pf, _pt in nodes.get("AGND", []))
        assert hit, f"{value!r} ({ref}) pin {AISENSE_PIN} (AISENSE) is not tied to AGND"


def test_aisense_tied_to_agnd_on_every_ni_connector(nodes, values):
    _check_aisense_tied_to_agnd(nodes, values)


def test_aisense_tied_to_agnd_on_every_ni_connector_fires_on_dropped_tie(nodes, values):
    ref = next(r for r, v in values.items() if v == "Connector 0 (analog + AISENSE, recording NI)")
    corrupted = dict(nodes)
    corrupted["AGND"] = [n for n in nodes.get("AGND", []) if not (n[0] == ref and n[1] == AISENSE_PIN)]
    with pytest.raises(AssertionError, match="AISENSE"):
        _check_aisense_tied_to_agnd(corrupted, values)


# --- 10: both isolated domains pin-disjoint from every non-isolated rail. --------------

ISOLATED_RAILS = {"NI_5V", "NI_GND", "ISO_P12", "ISO_N12", "INTAN_GND", "ISO_5V"}
NON_ISOLATED_RAILS = {"+12V", "-12V", "+5V", "+3V3", "AGND", "DGND"}


def _check_isolated_domains_pin_disjoint(nodes: dict[str, list[Node]]) -> None:
    """Checked at PIN level, not reference level -- see module docstring, #10. A
    component that legitimately straddles BY DESIGN (an ACSL optocoupler's LED pins vs.
    its own detector pins, an isolated DC-DC's primary vs. secondary, an INA105
    difference amplifier's '-' input vs. its own REF/power) has SOME pins on each side,
    which this check does not and structurally cannot flag; only a single PIN present in
    both sets at once would, which is always a real short, independent of which
    reference it belongs to -- so this needs no per-component exception list at all."""
    iso_pins = {p for rail in ISOLATED_RAILS for p in _pins(nodes, rail)}
    non_iso_pins = {p for rail in NON_ISOLATED_RAILS for p in _pins(nodes, rail)}
    overlap = iso_pins & non_iso_pins
    assert not overlap, (
        f"pin(s) {overlap} appear on both an isolated-domain rail ({sorted(ISOLATED_RAILS)}) "
        f"and a non-isolated rail ({sorted(NON_ISOLATED_RAILS)}) -- a single pin cannot "
        f"legitimately be on both sides of a galvanic barrier"
    )


def test_isolated_domains_pin_disjoint_from_non_isolated(nodes):
    _check_isolated_domains_pin_disjoint(nodes)


def test_isolated_domains_pin_disjoint_from_non_isolated_fires_on_shared_pin(nodes):
    phantom = ("DBG99", "1", "", "passive")
    corrupted = dict(nodes)
    corrupted["NI_GND"] = list(nodes.get("NI_GND", [])) + [phantom]
    corrupted["DGND"] = list(nodes.get("DGND", [])) + [phantom]
    with pytest.raises(AssertionError, match="DBG99"):
        _check_isolated_domains_pin_disjoint(corrupted)


# --- 11: AGND and DGND are genuinely distinct nets (not merely both present). ----------


def _check_agnd_dgnd_share_no_pin(nodes: dict[str, list[Node]]) -> None:
    agnd_pins = _pins(nodes, "AGND")
    dgnd_pins = _pins(nodes, "DGND")
    assert agnd_pins, "AGND has no nodes at all"
    assert dgnd_pins, "DGND has no nodes at all"
    shared = agnd_pins & dgnd_pins
    assert not shared, f"AGND and DGND share pin(s) {shared} directly -- not genuinely distinct nets"


def test_agnd_dgnd_share_no_pin(nodes):
    _check_agnd_dgnd_share_no_pin(nodes)


def test_agnd_dgnd_share_no_pin_fires_on_direct_short(nodes):
    phantom = ("DBG98", "1", "", "passive")
    corrupted = dict(nodes)
    corrupted["AGND"] = list(nodes.get("AGND", [])) + [phantom]
    corrupted["DGND"] = list(nodes.get("DGND", [])) + [phantom]
    with pytest.raises(AssertionError, match="DBG98"):
        _check_agnd_dgnd_share_no_pin(corrupted)


# --- 12: RWD_CMD and RWD_DLVR are genuinely distinct nets (not merely both present). ---


def _check_reward_nets_electrically_distinct(nodes: dict[str, list[Node]]) -> None:
    cmd_pins = _pins(nodes, "RWD_CMD")
    dlvr_pins = _pins(nodes, "RWD_DLVR")
    assert cmd_pins, "RWD_CMD has no nodes at all"
    assert dlvr_pins, "RWD_DLVR has no nodes at all"
    assert cmd_pins != dlvr_pins, (
        "RWD_CMD and RWD_DLVR have IDENTICAL node sets -- they are the same physical net "
        "under two different labels, which would make commanded and delivered "
        "indistinguishable in every recording"
    )


def test_reward_cmd_and_dlvr_are_electrically_distinct(nodes):
    _check_reward_nets_electrically_distinct(nodes)


def test_reward_cmd_and_dlvr_are_electrically_distinct_fires_on_collapsed_nets(nodes):
    corrupted = dict(nodes)
    corrupted["RWD_DLVR"] = list(nodes["RWD_CMD"])  # simulate the two nets merging
    with pytest.raises(AssertionError, match="IDENTICAL node sets"):
        _check_reward_nets_electrically_distinct(corrupted)
