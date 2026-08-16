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
`kicad-cli` to produce it, so the assertions below (26 at the time; 34 now) SKIPPED on every single CI run. A skip
reports green and reads as coverage while testing nothing -- worse than no test at all for a
contract whose entire reason to exist is that every one of these properties was actually
violated at least once, by omission, during this board's own build (see each check's own
docstring below for which task, which sheet). Skips do not catch regressions.

The fix: `hardware/gen/gen_breakout_netlist_contract.py` renders the exported netlist into
`hardware/breakout/netlist-contract.json` -- a sorted, UUID-free `{net: [(ref, pin,
pinfunction, pintype), ...]}` + `{ref: value}` snapshot, committed to the repository (NOT
gitignored, unlike the raw `.net` it is derived from). This file loads THAT committed
snapshot below (`_load_snapshot()`), so all 34 assertions run unconditionally in every
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

THEN TWO MORE, ADDED AFTER A WHOLE-BRANCH REVIEW FOUND THAT CHECKS 1-12 ALL SHARE ONE
BLIND SPOT: they ask ONE-HOP questions about NETS ("is this node on that net"), and the
hazards that got through are TWO-HOP questions about PATHS. Check 6 matches only a literal
"+5V" node on a GPIO pin; check 7 sees only resistor bridges; neither can see
`+5V -> 430R -> optocoupler LED -> GPIO`. Nothing anywhere examined a driver's own
NEGATIVE rail, or summed a net's total sink load across sheets.

  13. No pin on a sync-module GPIO net can impose a voltage outside 0V..+3V3 on it --
      evaluated PER PINTYPE, because that is the whole difficulty. An open-collector
      output cannot source, so an LM339 on +12V is correct by design while the same part
      on -12V puts ~-11.9V straight onto GPIO20/21/25; a passive pin's reach runs through
      one two-terminal passive, which is what sees an optocoupler LED whose anode is one
      430R resistor from +5V. Validated against the real pre-fix netlist: exactly 5
      hazard paths, zero false positives.
  14. No driver pin is asked to sink more than this board allows that part -- LED cathodes
      plus pull-ups, SUMMED ACROSS SHEETS. The defect was five nets each carrying two
      optocoupler LEDs, which is structurally invisible to any per-sheet checker: opto-ni
      sees one LED on the net and opto-intan sees one LED on the same net.

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
import tomllib
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parents[2] / "hardware" / "breakout" / "netlist-contract.json"
PARAMS = Path(__file__).parents[2] / "hardware" / "datasheet-params.toml"

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
    "any reference present on both nets" check false-positived on U11 (SN74AHCT541PW, the
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

ISOLATED_RAILS = {"NI_5V", "NI_GND", "ISO_P12", "ISO_N12", "INTAN_GND", "ISO_5V",
                  "ISO_5V_RTN"}  # ISO_5V_RTN added at finding F5 -- see RAIL_VOLTS.
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


# ===========================================================================
# 13-14: TWO-HOP questions about PATHS, which no one-hop question about a NET can
# answer.
#
# Every verification blind spot found on this branch has had the same shape. Checks 6
# and 7 above are the two clearest examples, and they sit right next to each other:
# `_check_no_5v_on_sync_module_gpio` matches only a LITERAL "+5V" node on a GPIO pin, and
# `_check_comparator_pullups_on_3v3_never_5v` sees only resistor BRIDGES. Neither can see
# `+5V -> 430R -> optocoupler LED -> GPIO`, which is the same hazard two hops out; nothing
# anywhere examined a DRIVER'S OWN NEGATIVE RAIL (an open-collector output's LOW level is
# its part's V-, so an LM339 on -12V put -11.9V onto GPIO20/21/25 through no protection at
# all); and nothing summed a net's total sink load ACROSS SHEETS, so five nets each carried
# two optocoupler LEDs -- invisible to any checker reasoning one sheet at a time, because
# opto-ni sees one LED on the net and opto-intan sees one LED on the net.
#
# Both checks below are deliberately written over the WHOLE snapshot rather than over an
# enumerated list of known-interesting nets: the defects they exist to catch all arrived
# as a LATER sheet adding a node to an EARLIER sheet's net, which no enumerated list
# written before that sheet existed could have anticipated.
# ===========================================================================

# Rails, and what each one actually is in volts. Hardcoded on purpose: this board has a
# small, fixed rail set, and the whole point of check 13 is to reason about VOLTAGE, which
# a netlist does not carry. `_rail_nets()` below cross-checks this table against the
# snapshot so a new rail cannot be added to the board without being given a voltage here.
RAIL_VOLTS = {
    "+3V3": 3.3, "+5V": 5.0, "+12V": 12.0, "-12V": -12.0,
    "AGND": 0.0, "DGND": 0.0,
    "NI_5V": 5.0, "NI_5V_FB_RAW": 5.0, "NI_GND": 0.0,
    "ISO_5V": 5.0, "INTAN_GND": 0.0,
    # ISO_5V_RTN: the F5 converter's own secondary return, joined to INTAN_GND at NT4
    # rather than merged into it, so the switcher's return current reaches the Intan
    # reference through one controlled tie. Same net at 0 V for every purpose here.
    "ISO_5V_RTN": 0.0,
    "ISO_P12": 12.0, "ISO_N12": -12.0,
    "ISO_P15_RAW": 15.0, "ISO_P15_FILT": 15.0,
    "ISO_N15_RAW": -15.0, "ISO_N15_FILT": -15.0,
}

# What a sync-module GPIO pad tolerates: 0V to +3V3. The module is 3.3V and NOT 5V
# tolerant, and its pads' absolute minimum is -0.5V (spec Sec.4; pi-interface.kicad_sch's
# own GPIO_PIN_SPEC records that several of these nets reach the header with kind="direct"
# -- no series resistance, no clamp, nothing).
GPIO_SAFE_MIN_V, GPIO_SAFE_MAX_V = 0.0, 3.3


def _rail_nets(nodes: dict[str, list[Node]]) -> set[str]:
    """Every net carrying at least one power pin -- derived from the snapshot, not listed
    by hand, so a rail that appears on a future sheet is picked up automatically. Asserts
    each one has a voltage in RAIL_VOLTS: a rail nobody has assigned a voltage to would
    otherwise be silently treated as harmless by check 13."""
    rails = {
        name for name, nl in nodes.items()
        if any("power" in pt for _ref, _pin, _pf, pt in nl)
    }
    unpriced = rails - set(RAIL_VOLTS)
    assert not unpriced, (
        f"rail(s) {sorted(unpriced)} carry power pins but have no entry in RAIL_VOLTS -- "
        f"add one. Check 13 reasons about VOLTAGE, and an unpriced rail would be silently "
        f"treated as safe to put on a 3.3V-only GPIO pad."
    )
    return rails


def _two_terminal_passive_refs(nodes: dict[str, list[Node]]) -> dict[str, set[str]]:
    """{reference: {net, net}} for every component with EXACTLY two pins in the whole
    snapshot -- resistors, capacitors, ferrites, 2-pin headers. These are the parts a
    voltage can propagate straight through (a resistor to a rail IS that rail, softened),
    which is what makes them the right unit for "one hop"."""
    pins: dict[str, set[tuple[str, str]]] = {}
    nets_of: dict[str, set[str]] = {}
    for name, nl in nodes.items():
        for ref, pin, _pf, _pt in nl:
            pins.setdefault(ref, set()).add((ref, pin))
            nets_of.setdefault(ref, set()).add(name)
    return {ref: nets_of[ref] for ref, p in pins.items() if len(p) == 2}


def _part_nets(nodes: dict[str, list[Node]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for name, nl in nodes.items():
        for ref, _pin, _pf, _pt in nl:
            out.setdefault(ref, set()).add(name)
    return out


def _reachable_rails(node: Node, rails, part_nets, two_pin) -> set[str]:
    """Which rails the given pin can actually impose on the net it sits on.

    PINTYPE-AWARE, and that is the entire point. The naive version of this check -- "does
    this pin's part touch a rail outside {+3V3, GND}" -- flags the LM339 comparator for
    its +12V supply, which is correct BY DESIGN: an open-collector output cannot source at
    all, so it can never pull its net above the +3V3 its own external pull-up provides. The
    same naive version therefore has to be relaxed until it stops seeing the real -12V
    defect too. Distinguishing the two is exactly the "examine the driver's NEGATIVE rail"
    question nothing on this board was asking:

      - open_collector: pulls DOWN only, to its part's most-negative supply. That rail,
        and only that rail, is what this pin can impose.
      - output / tri_state / bidirectional (push-pull): can drive to EITHER of its part's
        supplies.
      - power_in / power_out: the rail itself.
      - passive: no supply of its own, so the reach is structural -- any rail its part
        touches, plus any rail ONE two-terminal passive away from a net its part touches.
        That second clause is what sees `+5V -> 430R -> LED anode ... LED cathode -> GPIO`:
        the optocoupler is not a two-terminal part, so no bridge-shaped check can see
        through it, but its LED's anode net is one resistor from +5V.
      - input: nothing. A receiver imposes no voltage, which is why the HCT541/HCT32 input
        pins legitimately sitting on these nets are not false positives.
    """
    ref, _pin, _pf, pintype = node
    own = {n for n in part_nets.get(ref, set()) if n in rails}
    if "no_connect" in pintype:
        return set()
    if "power" in pintype:
        return own
    if "open_collector" in pintype:
        return {min(own, key=lambda n: RAIL_VOLTS[n])} if own else set()
    if any(k in pintype for k in ("output", "tri_state", "bidirectional")):
        return own
    # passive
    reach = set(own)
    for net in part_nets.get(ref, set()):
        for other_ref, other_nets in two_pin.items():
            if other_ref == ref or net not in other_nets:
                continue
            reach |= {n for n in other_nets if n in rails}
    return reach


def _check_gpio_nets_reach_no_unsafe_rail(nodes: dict[str, list[Node]]) -> None:
    rails = _rail_nets(nodes)
    part_nets = _part_nets(nodes)
    two_pin = _two_terminal_passive_refs(nodes)
    header_refs = {
        ref for nl in nodes.values() for ref, _p, pf, _pt in nl if pf and _GPIO_PINFUNCTION_RE.match(pf)
    }
    hazards = []
    for name, nl in nodes.items():
        if not any(pf and _GPIO_PINFUNCTION_RE.match(pf) for _r, _p, pf, _pt in nl):
            continue
        for node in nl:
            ref, pin, _pf, pintype = node
            if ref in header_refs or "input" in pintype:
                continue
            unsafe = sorted(
                r for r in _reachable_rails(node, rails, part_nets, two_pin)
                if not (GPIO_SAFE_MIN_V <= RAIL_VOLTS[r] <= GPIO_SAFE_MAX_V)
            )
            if unsafe:
                hazards.append((name, ref, pin, pintype, unsafe))
    assert not hazards, (
        f"{len(hazards)} path(s) can impose an out-of-range voltage on a sync-module GPIO "
        f"net (safe range {GPIO_SAFE_MIN_V}V..{GPIO_SAFE_MAX_V}V; the module is 3.3V, is "
        f"NOT 5V tolerant, and its pads' absolute minimum is -0.5V):\n  "
        + "\n  ".join(
            f"{net}: {ref} pin {pin} ({pintype}) reaches {rs}" for net, ref, pin, pintype, rs in hazards
        )
        + "\n(net, pin, pintype, reachable rails). Two real defects had exactly this "
        "shape and neither was visible to any existing check: an LM339 whose V- was -12V, "
        "making every open-collector output LOW ~-11.9V straight into GPIO20/21/25; and "
        "ACSL-6400 LEDs on PD1_COMP/PD2_COMP whose anodes sat on +5V through 430R, so "
        "GPIO20/21 idled ~3.6-3.8V and took ~7mA into an unpowered pad."
    )


def test_gpio_nets_reach_no_unsafe_rail(nodes):
    _check_gpio_nets_reach_no_unsafe_rail(nodes)


def test_gpio_nets_reach_no_unsafe_rail_fires_on_negative_driver_rail(nodes):
    """The LM339-on--12V defect, reproduced exactly: move the comparator's V- pin from
    AGND to -12V and nothing else. Every output stays open-collector, every pull-up stays
    on +3V3, every net keeps the same node count -- the ONLY difference is one power pin's
    net, which is all the real defect ever was."""
    lm_vneg = next(n for n in nodes["AGND"] if n[0] == "U54" and n[1] == "12")
    corrupted = dict(nodes)
    corrupted["AGND"] = [n for n in nodes["AGND"] if n != lm_vneg]
    corrupted["-12V"] = list(nodes.get("-12V", [])) + [lm_vneg]
    with pytest.raises(AssertionError, match=r"open_collector.*-12V"):
        _check_gpio_nets_reach_no_unsafe_rail(corrupted)


def test_gpio_nets_reach_no_unsafe_rail_fires_on_led_two_hops_from_5v(nodes):
    """The other half: an optocoupler LED cathode put back onto PD1_COMP. +5V is TWO hops
    away (rail -> 430R -> LED anode net, then through the LED to the cathode), so neither
    check 6 (literal +5V node on a GPIO pin) nor check 7 (resistor bridge) sees it.

    Matches on '+5V' specifically, not just 'U61 pin 6' -- U61 (the whole ACSL-6400
    package) already has OTHER pins on NI_5V (its own VDD, unrelated to this test's
    fabricated cathode), and NI_5V is unsafe too, so `own` alone (the zero-hop "does this
    REF touch a rail anywhere" half of _reachable_rails, not the two-hop bridge-through-a-
    passive half this test exists to exercise) already makes ANY U61 pin report a hazard.
    A match on 'U61 pin 6' alone is satisfied by that alone and stays green even with the
    whole two-hop clause deleted from _reachable_rails (confirmed by deleting it: all 34
    tests in this file, including this one, still pass). '+5V' is reachable only via the
    LED-anode-resistor bridge (U61's own package never has a pin directly on '+5V'), so
    requiring it actually depends on the two-hop logic."""
    corrupted = dict(nodes)
    corrupted["PD1_COMP"] = list(nodes["PD1_COMP"]) + [("U61", "6", "CATHODE3_6", "passive")]
    with pytest.raises(AssertionError, match=r"U61 pin 6.*\+5V"):
        _check_gpio_nets_reach_no_unsafe_rail(corrupted)


def test_gpio_nets_reach_no_unsafe_rail_does_not_fire_on_12v_comparator_supply(nodes):
    """The false positive this check is specifically shaped to avoid, asserted rather
    than assumed. The LM339 runs from +12V and its outputs wire straight to GPIO20/21/25;
    that is correct, because an open-collector output cannot source, so its net is pulled
    up only by the +3V3 resistor. A check that flagged it would have to be loosened until
    it stopped seeing the -12V defect as well."""
    lm_out = next(n for n in nodes["ACC_TRIG"] if n[0] == "U54")
    assert "open_collector" in lm_out[3]
    assert "+12V" in _part_nets(nodes)["U54"]
    _check_gpio_nets_reach_no_unsafe_rail(nodes)


# --- 14: per-driver-pin sink load, summed across sheets. --------------------------------

# Maximum sink current this board may ASK OF a given part's output pin, in mA -- datasheet
# IOL figures, one per part actually placed:
#
#   SN74AHCT541PW is rated IOL = 8mA, and every one of its LED-driving pins is asked for
#   7.33mA -- fully compliant, with margin. The LED current itself is not a free choice
#   (the ACSL-6400's own requirements: 7-15mA recommended, 7.0mA WORST-CASE switching
#   threshold, so anything much lower risks a marginal part not switching at all --
#   gen_breakout_opto_ni.py's own derivation), so the part is what had to give: the
#   original SN74HCT541PW (6mA rated IOL) put 7.33mA 22% over its own rated sink, still
#   far inside its 25mA per-pin absolute maximum but past the datasheet's own guaranteed-
#   VOL condition -- and self-consistently against the part's own ~55 ohm output
#   impedance (not the single 6mA test point), the real current is closer to ~7.18mA,
#   uncomfortably close to the LED's own 7.0mA floor. SN74AHCT541PW -- identical pinout
#   and TSSOP-20 footprint, a Value/MPN swap with zero refdes churn -- is what is placed
#   on U8-U11 (taskpc-digital.kicad_sch) and U15 (pi-interface.kicad_sch) now. The budget
#   below is kept at 7.5mA (comfortably under the new 8mA rating, not loosened to it) so
#   a SECOND LED on the same pin (14.7mA) still fails hard either way.
MAX_SINK_MA = {
    "SN74AHCT541PW": 7.5,
    "SN74HCT32D": 4.0,
    "SN74HCT14D": 4.0,
    "SN74LVC541APW": 24.0,
    "ACSL-6400-00TE": 13.0,
    "ACSL-6420-00TE": 13.0,
    "LM339": 6.0,
}

LED_VF_V = 1.52   # ACSL-6400 typ at I_F=10mA, the closest datasheet test point
DRIVER_VOL_V = 0.33  # SN74HCT541's own max at IOL=6mA -- the same approximation
# gen_breakout_opto_ni.py used to pick 430R in the first place.


def _parse_ohms(value: str) -> float | None:
    v = value.strip().replace("R", "").replace("Ω", "")
    mult = 1.0
    if v.endswith("k"):
        v, mult = v[:-1], 1e3
    elif v.endswith("M"):
        v, mult = v[:-1], 1e6
    try:
        return float(v) * mult
    except ValueError:
        return None


def _net_sink_ma(name, nl, nodes, values, rails, two_pin) -> tuple[float, list[str]]:
    """Total current a driver pulling this net LOW has to sink, and how it breaks down."""
    total, why = 0.0, []
    for ref, pin, pf, _pt in nl:
        # Optocoupler LED: current is set by the series resistor on the MATCHING channel's
        # anode net, found via the pinfunction's own channel index ("CATHODE3_6" pairs with
        # "ANODE3_*" on the same package) -- derived, not assumed to be 7.33mA, so a
        # resistor-value edit moves this number instead of silently invalidating it.
        if not (pf or "").startswith("CATHODE"):
            continue
        chan = pf[len("CATHODE"):].split("_")[0]
        anode = next(
            (
                (n2, r2, p2) for n2, nl2 in nodes.items() for r2, p2, pf2, _t2 in nl2
                if r2 == ref and (pf2 or "").startswith(f"ANODE{chan}_")
            ),
            None,
        )
        assert anode, f"{name}: LED cathode {ref}.{pin} ({pf}) has no matching ANODE{chan} pin on {ref}"
        anode_net = anode[0]
        for r_ref in {r for r, _p, _pf, _t in nodes[anode_net]} & set(two_pin):
            rail = next((n for n in two_pin[r_ref] if n in rails), None)
            ohms = _parse_ohms(values.get(r_ref, ""))
            if rail is None or not ohms:
                continue
            ma = (RAIL_VOLTS[rail] - DRIVER_VOL_V - LED_VF_V) / ohms * 1000
            total += ma
            why.append(f"LED {ref}.{pin} via {r_ref} from {rail}: {ma:.2f}mA")
    for r_ref in {r for r, _p, _pf, _t in nl} & set(two_pin):
        rail = next((n for n in two_pin[r_ref] if n in rails and RAIL_VOLTS[n] > 0), None)
        ohms = _parse_ohms(values.get(r_ref, ""))
        if rail is None or not ohms:
            continue
        ma = (RAIL_VOLTS[rail] - DRIVER_VOL_V) / ohms * 1000
        total += ma
        why.append(f"pull-up {r_ref} to {rail}: {ma:.2f}mA")
    return total, why


def _check_driver_pin_sink_load(nodes: dict[str, list[Node]], values: dict[str, str]) -> None:
    """Per-PIN, which after finding F1 is not the same number as per-NET.

    When two buffer outputs are tied together and both pull low, each sinks roughly half
    the branch current -- so the load a PIN carries is the net total divided by the number
    of outputs actually driving that net. Charging the whole branch to each pin (which is
    what this check did before F1) rejects a design that is genuinely inside spec: 249R
    delivers ~12.7mA, which is over one AHCT541 pin's 7.5mA budget and comfortably inside
    two.

    The division is deliberately by the count of DRIVING OUTPUTS ON THE NET, not by a
    per-part assumption, and check 18b asserts separately that those outputs have tied
    inputs -- without which they are not a parallel pair at all and the division would be
    unearned. Splitting the two makes this check safe to divide: nothing can lower its
    reported load except a second output that check 18b has already proved is wired to
    drive in lockstep with the first.
    """
    rails = _rail_nets(nodes)
    two_pin = _two_terminal_passive_refs(nodes)
    overloaded = []
    for name, nl in nodes.items():
        drivers = [
            (ref, pin) for ref, pin, _pf, pt in nl
            if values.get(ref) in MAX_SINK_MA
            and any(k in pt for k in ("output", "tri_state", "open_collector"))
            and "no_connect" not in pt
        ]
        if not drivers:
            continue
        total, why = _net_sink_ma(name, nl, nodes, values, rails, two_pin)
        share = total / len(drivers)
        for ref, pin in drivers:
            budget = MAX_SINK_MA[values[ref]]
            if share > budget + 1e-6:
                overloaded.append((
                    name, ref, pin, values[ref], round(share, 2), budget, len(drivers),
                    round(total, 2), why,
                ))
    assert not overloaded, (
        f"{len(overloaded)} driver pin(s) are asked to sink more than this board allows "
        f"that part:\n  "
        + "\n  ".join(
            f"{ref} pin {pin} ({part}) on {net}: {sh}mA/pin > {budget}mA "
            f"({tot}mA across {n} paralleled output(s)) -- " + "; ".join(why)
            for net, ref, pin, part, sh, budget, n, tot, why in overloaded
        )
        + "\nThis is summed ACROSS SHEETS on purpose. The real defect was five nets each "
        "carrying two optocoupler LEDs (~14.7mA against a 6mA or 4mA IOL), and no checker "
        "reasoning one sheet at a time could see it: opto-ni saw one LED on the net and "
        "opto-intan saw one LED on the same net. The worst case, RWD_DLVR off a 4mA "
        "74HCT32, would have left RWD_DLVR_PI stuck HIGH -- the sync module recording "
        "reward-delivered continuously."
    )


def test_driver_pin_sink_load(nodes, values):
    _check_driver_pin_sink_load(nodes, values)


def test_driver_pin_sink_load_fires_on_doubled_led(nodes, values):
    """The defect verbatim: opto-intan's own LED put back onto the net opto-ni's LED is
    already on, so one HCT541 pin drives both."""
    corrupted = dict(nodes)
    corrupted["EVT_STROBE_BUF"] = list(nodes["EVT_STROBE_BUF"]) + [("U64", "2", "CATHODE1_2", "passive")]
    with pytest.raises(AssertionError, match=r"U10 pin 18"):
        _check_driver_pin_sink_load(corrupted, values)


def test_driver_pin_sink_load_fires_on_led_on_the_4ma_or_gate(nodes, values):
    """A SINGLE LED back on the 74HCT32 reward-OR output. One LED is inside the HCT541
    budget, so this can only fail on the per-part table actually being per-part."""
    corrupted = dict(nodes)
    corrupted["RWD_DLVR"] = list(nodes["RWD_DLVR"]) + [("U60", "8", "CATHODE4_8", "passive")]
    with pytest.raises(AssertionError, match=r"U12 pin 3 \(SN74HCT32D\)"):
        _check_driver_pin_sink_load(corrupted, values)


def test_driver_pin_sink_load_fires_on_dropping_one_of_a_parallel_pair(nodes, values):
    """FINDING F1's OWN TRAP, made executable: keep the 249R resistor and ship only one
    output per LED. That combination -- which is exactly what an earlier attempt at F1
    did, and why it was reverted -- puts ~12.7mA on a pin budgeted at 7.5mA.

    This is the control that keeps the per-pin division honest. The division is the one
    thing F1 loosened in this check, so it needs a test proving it does not simply make
    the check unfailable."""
    corrupted = dict(nodes)
    drivers = [n for n in nodes["EVT_D0_BUF"] if values.get(n[0]) == "SN74AHCT541PW"]
    assert len(drivers) == 2, f"expected a paralleled pair on EVT_D0_BUF, found {drivers}"
    corrupted["EVT_D0_BUF"] = [n for n in nodes["EVT_D0_BUF"] if n != drivers[-1]]
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF.*1 paralleled output"):
        _check_driver_pin_sink_load(corrupted, values)


def test_driver_pin_sink_load_fires_on_a_third_led_across_a_parallel_pair(nodes, values):
    """The other way to abuse the division: keep the pair, add a third LED to the net.
    Two outputs sharing three LEDs is ~19mA/pin. Proves the division scales the load
    rather than excusing it."""
    corrupted = dict(nodes)
    corrupted["EVT_D0_BUF"] = list(nodes["EVT_D0_BUF"]) + [("U64", "2", "CATHODE1_2", "passive")]
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF"):
        _check_driver_pin_sink_load(corrupted, values)


def test_driver_pin_sink_load_fires_on_pullup_value_drift(nodes, values):
    """Not only LEDs: an NI-side pull-up edited from 3.9k to 100R, which alone puts the
    optocoupler's own output stage over its 13mA."""
    r_ref = next(iter(_bridging_resistor_refs(nodes, "NI_5V", "EVT_D5_NI")))
    corrupted_values = dict(values)
    corrupted_values[r_ref] = "100"
    with pytest.raises(AssertionError, match=r"EVT_D5_NI"):
        _check_driver_pin_sink_load(nodes, corrupted_values)


# ===========================================================================
# 15-18: THE OTHER HALF OF EVERY TWO-SIDED CONSTRAINT (finding F1,
# hardware/breakout/parametric-audit.md).
#
# Check 14 above verifies that LED current does not EXCEED what the driver can sink.
# Nobody checked that it REACHES what the optocoupler needs to switch. Half a two-sided
# constraint was tested, and the untested half was wrong on 30 channels for seventy-three
# commits: 430R against a 7.0mA I_FH minimum delivers 4.4mA worst case, so those channels
# were never guaranteed to switch at all. Check 14 could not have caught it -- an
# under-driven LED sinks LESS than the budget, which is exactly what check 14 calls
# "pass".
#
# Every assertion below reads a VALUE and compares it against a DATASHEET LIMIT, rather
# than reading a net and comparing it against an expected name. The limits come from
# hardware/datasheet-params.toml -- a committed, static data file whose whole stated
# purpose is that "parametric checkers import limits from here rather than re-typing
# them, so a datasheet revision is a one-line change that propagates to every assertion".
# Reading it keeps this file's own hard-won independence intact (no KiCad, no import of
# hardware/gen/, runs unconditionally in CI): tomllib is stdlib, and the .toml is
# committed exactly like the snapshot beside it.
#
#   15. Every LED branch's WORST-CASE forward current clears the ACSL-6xx0's own
#       guardbanded switching floor -- the assertion whose absence let F1 survive.
#   16. Every LED branch's BEST-CASE forward current stays under the part's ABSOLUTE
#       MAXIMUM I_F. 15mA is an absolute maximum, not a recommendation; both ends of the
#       7-15mA window are hard, so shrinking the resistor to fix check 15 has to be
#       stopped from overshooting the other way.
#   17. Package GROUND-pin current, summed across every channel of a package. The
#       AHCT541's GND absolute maximum is 75mA, and the event bus idles at 0x0000 --
#       active-low drive means sixteen LEDs are lit CONTINUOUSLY, a steady state rather
#       than a transient. This is a per-PACKAGE limit that no per-PIN check can see.
#   18. Outputs wired in parallel onto one net must have their INPUTS tied to one net
#       too. Paralleling is what makes check 14 pass at 249R (the current divides), and
#       it is only safe while both outputs are guaranteed to drive the same direction --
#       two push-pull outputs disagreeing is a supply-to-ground short through the die,
#       not a marginal load.
# ===========================================================================


def _load_params() -> dict:
    """Datasheet limits, from the committed hardware/datasheet-params.toml. Missing is a
    broken checkout, not a skippable condition -- same reasoning as `_load_snapshot()`."""
    assert PARAMS.exists(), (
        f"{PARAMS} is missing. It is committed to the repository -- its absence means the "
        f"checkout is broken. Every limit below is a datasheet fact, not a preference."
    )
    return tomllib.loads(PARAMS.read_text())


@pytest.fixture(scope="module")
def params() -> dict:
    return _load_params()


# What sits in series BETWEEN a rail's nominal voltage and an LED anode fed from it, as
# (typical, maximum) volts. This is the "and the series drops in front of the rail" clause
# of the audit's own proposed assertion, and it is the entire reason F1 needs TWO resistor
# values rather than one:
#
#   +5V   arrives at the LEDs through F4 (1206L050 polyfuse) and D3 (SS14 reverse-polarity
#         diode), so the anode rail is NOT 5.00V. SS14's V_F is specified at 1.0A, not at
#         the ~0.45-0.50A this rail carries (datasheet-params.toml's own `note` on [ss14]
#         warns that using the 0.50V headline directly over-estimates the drop by enough
#         to change the resistor), so the estimated-at-450mA figures are the right ones.
#         The fuse contributes I x R_min.
#   ISO_5V is regulated locally, on the isolated side of the barrier, with nothing in
#         series -- so an LED fed from it sees the full 5.00V and the SAME resistor would
#         push it past the absolute maximum. Hence 249R on +5V and 301R on ISO_5V.
#   NI_5V  feeds no LED anode on this board (it feeds the NI-side pull-ups only), but is
#         priced here so the guard below cannot be satisfied vacuously if that changes.
#
# Derived from datasheet-params.toml at call time rather than written as literals here --
# see `_rail_series_drop_v()`.
LED_RAIL_RATED_CURRENT_A = 0.495  # +5V worst-case rail load, parametric-audit.md M7's own
# "392 / 495 mA" figure -- the current the series drop is evaluated AT.

# The external +5V supply's tolerance. F1 makes this LOAD-BEARING rather than incidental:
# at a +-5% supply the LED tolerance spread is 1.95:1 against a 2.14:1 window, which fits
# with nothing to spare, so the fix shrinks the SPREAD rather than chasing the resistor.
# This number is therefore a specification the rig must meet, not an observation -- it
# belongs beside the current rating in the spec, and this assertion is what holds the
# design to it.
SUPPLY_TOL = 0.02
# E96 values (249, 301) are 1% parts; E24 (430) is nominally 5% but is held to the same
# 1% here deliberately, so a regression to a loose value cannot hide behind a wider
# tolerance band than the part it replaced.
R_LED_TOL = 0.01


GROUND_NETS = {"AGND", "DGND", "NI_GND", "INTAN_GND", "FAN_RTN", "ISO_5V_RTN"}


def _series_graph(nodes, values):
    """(series edges, supply-inlet nets) for the board's power tree.

    A SERIES element is a two-terminal part with both pins on non-ground nets -- a fuse or
    a reverse-polarity diode. A decoupling capacitor has one pin on ground and is a SHUNT;
    including it would bridge every rail to every other through the ground net and make
    any walk over this graph meaningless.

    The inlet is derived as the connector feeding two or more fuse input nets, rather than
    named: `+12V` (every op-amp's V+) and `FAN_12V` (four 3-pin fan headers) both look like
    "a net with a multi-pin part on it", and treating either as a supply source silently
    makes the cut tests below unfailable.
    """
    two_pin = _two_terminal_passive_refs(nodes)
    series = {
        ref: nets for ref, nets in two_pin.items()
        if len(nets) == 2 and not (nets & GROUND_NETS)
    }
    fuse_nets = {
        name for name, nl in nodes.items()
        for ref, _p, _pf, _t in nl
        if ref.startswith("F") and not ref.startswith("FB") and values.get(ref) in POLYFUSE_PARAMS
    }
    conn_hits: dict[str, set[str]] = {}
    for name in fuse_nets:
        for ref, _p, _pf, _t in nodes[name]:
            if ref.startswith("J"):
                conn_hits.setdefault(ref, set()).add(name)
    source_nets = {n for ref, hits in conn_hits.items() if len(hits) >= 2 for n in hits}
    return series, source_nets


def _reachable_from_source(series, source_nets, without=None) -> set[str]:
    seen, frontier = set(source_nets), set(source_nets)
    while frontier:
        nxt = set()
        for ref, nets in series.items():
            if ref == without:
                continue
            if nets & frontier:
                nxt |= nets - seen
        seen |= nxt
        frontier = nxt
    return seen


def _supply_series_parts(nodes, values, rail: str) -> set[str]:
    """Every series part whose removal disconnects `rail` from the supply inlet -- i.e.
    the parts genuinely carrying that rail's current, found by a cut test rather than by
    following net names. Empty for a rail the inlet cannot reach at all (the isolated
    domains), which is the correct answer for them."""
    series, source_nets = _series_graph(nodes, values)
    if not source_nets or rail not in _reachable_from_source(series, source_nets):
        return set()
    return {
        ref for ref in series
        if rail not in _reachable_from_source(series, source_nets, without=ref)
    }


def _rail_series_drop_v(params: dict, nodes=None, values=None) -> dict[str, tuple[float, float]]:
    """{rail: (typical, maximum) volts dropped between the rail's nominal voltage and an
    LED anode fed from it} -- computed from the pinned datasheet parameters of the parts
    ACTUALLY IN THE PATH, found from the netlist, never written down as a literal and
    never named.

    Deriving the fuse rather than naming it is not fussiness. This function used to read
    `polyfuse_1206l050` by name, and finding M7 then changed `F4` to a `1206L110-C` whose
    R_min is 0.040 Ω instead of 0.150 Ω. A named lookup keeps returning the old part's
    drop forever: silently pessimistic here, but the identical pattern pointed the other
    way is how finding F1 happened in the first place -- a number that stayed true to a
    part the board no longer had.

    `nodes`/`values` are optional only so the pre-existing callers that pass just
    `params` keep working; when they are supplied the path is derived, and when they are
    not the conservative +5 V figures are used.
    """
    diode = params["ss14"]

    def drops_for(rail: str) -> tuple[float, float] | None:
        if nodes is None or values is None:
            return None
        parts = _supply_series_parts(nodes, values, rail)
        if not parts:
            return None
        fuse_r = sum(
            params[POLYFUSE_PARAMS[values[ref]]]["r_min_ohm"]
            for ref in parts if values.get(ref) in POLYFUSE_PARAMS
        )
        n_diodes = sum(1 for ref in parts if values.get(ref) == "SS14")
        fuse_drop = LED_RAIL_RATED_CURRENT_A * fuse_r
        return (n_diodes * diode["v_f_estimated_at_450ma_typ_v"] + fuse_drop,
                n_diodes * diode["v_f_estimated_at_450ma_max_v"] + fuse_drop)

    # Fallback figures, used only when the caller supplies no netlist: today's F4 plus D3.
    fallback_fuse = LED_RAIL_RATED_CURRENT_A * params["polyfuse_1206l110"]["r_min_ohm"]
    out = {
        "+5V": (diode["v_f_estimated_at_450ma_typ_v"] + fallback_fuse,
                diode["v_f_estimated_at_450ma_max_v"] + fallback_fuse),
        # Both regulated locally, on the isolated side of the barrier, with nothing in
        # series -- and structurally unreachable from the supply inlet, so the derivation
        # below returns nothing for them and these zeros stand.
        "ISO_5V": (0.0, 0.0),
        "NI_5V": (0.0, 0.0),
    }
    for rail in list(out):
        derived = drops_for(rail)
        if derived is not None:
            out[rail] = derived
    return out


def _led_branches(nodes: dict[str, list[Node]], values: dict[str, str], two_pin) -> list[dict]:
    """Every optocoupler LED on the board, with the rail and resistor that set its current.

    Found structurally, from the ACSL symbols' own ANODEn/CATHODEn pinfunctions, rather
    than from any enumerated list of channels -- the same decision checks 13 and 14 above
    already made and for the same reason: every defect these exist to catch arrived as a
    LATER sheet adding a channel an earlier list could not have anticipated. All 32 LED
    positions on this board (30 on +5V, 2 on ISO_5V) are picked up automatically.
    """
    out = []
    for name, nl in nodes.items():
        for ref, pin, pf, _pt in nl:
            if not (pf or "").startswith("CATHODE"):
                continue
            chan = pf[len("CATHODE"):].split("_")[0]
            anode_net = next(
                (n2 for n2, nl2 in nodes.items() for r2, _p2, pf2, _t2 in nl2
                 if r2 == ref and (pf2 or "").startswith(f"ANODE{chan}_")),
                None,
            )
            assert anode_net, f"{name}: LED cathode {ref}.{pin} ({pf}) has no matching ANODE{chan} pin"
            for r_ref in {r for r, _p, _pf, _t in nodes[anode_net]} & set(two_pin):
                rail = next((n for n in two_pin[r_ref] if n in ("+5V", "ISO_5V", "NI_5V")), None)
                ohms = _parse_ohms(values.get(r_ref, ""))
                if rail is None or not ohms:
                    continue
                # SERIES RESISTANCE ON THE CATHODE SIDE COUNTS TOO -- finding F2.
                # The LED's current is set by everything in the loop, not just the anode
                # resistor, and this board has one channel (the inbound stim input) whose
                # 100R panel-protection resistor sits INSIDE the current path rather than
                # beside it. Counting only the anode resistor reported 8.75 mA on that
                # branch where the truth is 6.57 mA -- i.e. it declared a starved channel
                # healthy, which is precisely the failure mode check 15 exists to prevent.
                #
                # Derived, not special-cased: a resistor on the cathode net whose OTHER
                # terminal is not a rail is in the loop. A pull-up returns to a rail and is
                # not; a driver output is not a two-terminal part at all. Every ordinary
                # channel yields 0 here, which is the correct answer for them.
                cathode_r = sum(
                    o for r2 in {rr for rr, _p2, _pf2, _t2 in nl} & set(two_pin)
                    if r2.startswith("R") and not (two_pin[r2] & set(RAIL_VOLTS))
                    and (o := _parse_ohms(values.get(r2, "")))
                )
                out.append({
                    "cathode_net": name, "opto": ref, "pin": pin,
                    "rail": rail, "r_ref": r_ref, "ohms": ohms,
                    "cathode_ohms": cathode_r, "total_ohms": ohms + cathode_r,
                })
    return out


def _led_current_bounds_ma(branch: dict, params: dict, drops: dict) -> tuple[float, float]:
    """(worst-case minimum, best-case maximum) forward current for one LED branch, in mA.

    The minimum stacks every tolerance the wrong way at once -- low supply, maximum
    series drop in front of the rail, maximum LED V_F, maximum driver V_OL, high
    resistor -- because that is the corner in which a marginal part fails to switch, and
    "it works on the bench" is exactly how F1 survived. The maximum stacks them the other
    way against an ABSOLUTE maximum, where a single overshooting unit is a dead part.
    """
    acsl, drv = params["acsl_6xx0"], params["sn74ahct541"]
    nominal = RAIL_VOLTS[branch["rail"]]
    drop_typ, drop_max = drops[branch["rail"]]
    r_total = branch.get("total_ohms", branch["ohms"])
    i_min = (
        (nominal * (1 - SUPPLY_TOL) - drop_max - acsl["v_f_max_v"] - drv["v_ol_max_v"])
        / (r_total * (1 + R_LED_TOL))
    ) * 1000
    # Best case: the driver is barely conducting, so V_OL falls toward zero rather than
    # its rated value at full I_OL -- not a tolerance that can be stacked favourably here.
    i_max = (
        (nominal * (1 + SUPPLY_TOL) - drop_typ - acsl["v_f_min_v"])
        / (r_total * (1 - R_LED_TOL))
    ) * 1000
    return i_min, i_max


def _check_led_current_clears_switching_floor(nodes, values, params) -> None:
    two_pin = _two_terminal_passive_refs(nodes)
    drops = _rail_series_drop_v(params, nodes, values)
    floor = params["acsl_6xx0"]["i_fh_recommended_min_ma"]
    branches = _led_branches(nodes, values, two_pin)
    assert branches, "no optocoupler LED branches found at all -- this check would pass vacuously"
    unpriced = {b["rail"] for b in branches} - set(drops)
    assert not unpriced, (
        f"LED anode rail(s) {sorted(unpriced)} have no series-drop entry -- add one. A rail "
        f"with drops in front of it that nobody priced is precisely how F1 happened: the "
        f"resistor was sized against 5.00V at the anode, and the anode never saw 5.00V."
    )
    starved = []
    for b in branches:
        i_min, _ = _led_current_bounds_ma(b, params, drops)
        if i_min < floor - 1e-9:
            starved.append((b, round(i_min, 2)))
    assert not starved, (
        f"{len(starved)} optocoupler LED branch(es) are not guaranteed to switch -- "
        f"worst-case forward current below the ACSL-6xx0's own {floor}mA recommended "
        f"minimum (Broadcom AV02-0235EN Recommended Operating Conditions, footnote b: "
        f"'It is recommended that minimum 8 mA be used for best performance and to permit "
        f"guardband for LED degradation'; the hard I_FH minimum below which the part is "
        f"not specified to switch at all is "
        f"{params['acsl_6xx0']['i_fh_min_ma']}mA):\n  "
        + "\n  ".join(
            f"{b['cathode_net']}: LED {b['opto']}.{b['pin']} via {b['r_ref']}={b['ohms']:.0f}R "
            f"from {b['rail']}"
            + (f" (+{b['cathode_ohms']:.0f}R in the cathode path, {b['total_ohms']:.0f}R total)"
               if b.get("cathode_ohms") else "")
            + f": {ma}mA worst case"
            for b, ma in starved
        )
        + "\nThis is the OTHER HALF of check 14 above. That check verifies current does "
        "not EXCEED what the driver can sink; this one verifies it REACHES what the "
        "optocoupler needs to switch. Half of a two-sided constraint was tested and the "
        "untested half was wrong on 30 channels for seventy-three commits."
    )


def test_led_current_clears_switching_floor(nodes, values, params):
    _check_led_current_clears_switching_floor(nodes, values, params)


def test_led_current_clears_switching_floor_fires_on_resistor_drift(nodes, values, params):
    """The defect verbatim: one LED resistor back at the original 430R, which delivers
    ~5mA worst case against an 8mA floor."""
    b = next(b for b in _led_branches(nodes, values, _two_terminal_passive_refs(nodes))
             if b["cathode_net"] == "EVT_D0_BUF")
    corrupted = dict(values)
    corrupted[b["r_ref"]] = "430"
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF.*430R"):
        _check_led_current_clears_switching_floor(nodes, corrupted, params)


def test_led_current_clears_switching_floor_fires_on_iso_rail_using_the_main_value(nodes, values, params):
    """The reason F1 needs TWO resistor values and not one. `+5V` reaches its LEDs through
    F4 and D3; `ISO_5V` is regulated locally with nothing in series. This control proves
    the check is genuinely rail-aware -- it moves an ISO_5V branch's resistor to the +5V
    value, which is a real over-drive on that rail, and must be caught by check 16 rather
    than passing because 'it is the same part'."""
    # A branch with NOTHING extra in the cathode path, so this control isolates the rail
    # difference it exists to test. The inbound stim channel carries 100R of panel
    # protection inside its own loop (finding F2), which would confound the comparison.
    iso = next(b for b in _led_branches(nodes, values, _two_terminal_passive_refs(nodes))
               if b["rail"] == "ISO_5V" and not b["cathode_ohms"])
    _, i_max_iso = _led_current_bounds_ma({**iso, "ohms": 249.0, "total_ohms": 249.0}, params,
                                          _rail_series_drop_v(params, nodes, values))
    assert i_max_iso > params["acsl_6xx0"]["i_f_abs_max_ma"], (
        f"a 249R resistor on ISO_5V gives {i_max_iso:.1f}mA best case, which must exceed "
        f"the part's absolute maximum for the two-value split to be load-bearing"
    )


# --- 16: LED current stays under the part's ABSOLUTE MAXIMUM I_F. ----------------------


def _check_led_current_under_absolute_maximum(nodes, values, params) -> None:
    two_pin = _two_terminal_passive_refs(nodes)
    drops = _rail_series_drop_v(params, nodes, values)
    ceiling = params["acsl_6xx0"]["i_f_abs_max_ma"]
    branches = _led_branches(nodes, values, two_pin)
    assert branches, "no optocoupler LED branches found at all -- this check would pass vacuously"
    overdriven = []
    for b in branches:
        _, i_max = _led_current_bounds_ma(b, params, drops)
        if i_max > ceiling + 1e-9:
            overdriven.append((b, round(i_max, 2)))
    assert not overdriven, (
        f"{len(overdriven)} optocoupler LED branch(es) can exceed the ACSL-6xx0's "
        f"ABSOLUTE MAXIMUM forward current of {ceiling}mA (Broadcom AV02-0235EN Absolute "
        f"Maximum Ratings, 'Average Forward Input Current (per channel) I_F'):\n  "
        + "\n  ".join(
            f"{b['cathode_net']}: LED {b['opto']}.{b['pin']} via {b['r_ref']}={b['ohms']:.0f}R "
            f"from {b['rail']}: {ma}mA best case"
            for b, ma in overdriven
        )
        + "\n15mA is an absolute maximum, not a recommendation, so BOTH ends of the "
        "7-15mA window are hard. This check exists so that shrinking the resistor to "
        "clear the switching floor (check 15) cannot overshoot the other way -- 240R was "
        "rejected during the audit for landing at 99% of this limit."
    )


def test_led_current_under_absolute_maximum(nodes, values, params):
    _check_led_current_under_absolute_maximum(nodes, values, params)


def test_led_current_under_absolute_maximum_fires_on_undersized_resistor(nodes, values, params):
    """Overshooting the other way: 150R, comfortably clear of the switching floor and
    straight past the absolute maximum."""
    b = next(b for b in _led_branches(nodes, values, _two_terminal_passive_refs(nodes))
             if b["cathode_net"] == "EVT_D0_BUF")
    corrupted = dict(values)
    corrupted[b["r_ref"]] = "150"
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF.*150R"):
        _check_led_current_under_absolute_maximum(nodes, corrupted, params)


# --- 17/18: paralleled outputs -- the second half of F1. -------------------------------

_BUFFER_OUT_RE = re.compile(r"^Y(\d+)_")
_BUFFER_IN_RE = re.compile(r"^A(\d+)_")

# Parts whose outputs this board is allowed to wire in parallel, and their per-package
# ground-pin absolute maximum in mA. Restricted to the octal buffers on purpose: an
# open-collector optocoupler output and a push-pull gate output have completely different
# paralleling rules, and neither is paralleled anywhere on this board.
PARALLELABLE_PARTS = {"SN74AHCT541PW", "SN74LVC541APW"}


def _buffer_channels(nodes, values) -> dict[str, dict[int, dict[str, str]]]:
    """{package ref: {channel index: {"in": net, "out": net}}} for every octal buffer,
    read from the symbols' own A{n}_/Y{n}_ pinfunctions rather than from pin arithmetic --
    so this does not silently rot if a future package has a different pinout."""
    out: dict[str, dict[int, dict[str, str]]] = {}
    for name, nl in nodes.items():
        for ref, _pin, pf, _pt in nl:
            if values.get(ref) not in PARALLELABLE_PARTS:
                continue
            m_out, m_in = _BUFFER_OUT_RE.match(pf or ""), _BUFFER_IN_RE.match(pf or "")
            if m_out:
                out.setdefault(ref, {}).setdefault(int(m_out.group(1)), {})["out"] = name
            elif m_in:
                out.setdefault(ref, {}).setdefault(int(m_in.group(1)), {})["in"] = name
    return out


def _parallel_driver_count(nodes, values) -> dict[str, int]:
    """{net: how many buffer outputs drive it}. 1 for an ordinary net; 2 for an LED
    branch after F1. This is what makes check 14 correct rather than merely strict: when
    two outputs are tied together and both pull low, each sinks about half the current,
    and a checker that charges the whole branch to one pin would reject a design that is
    actually inside spec."""
    counts: dict[str, int] = {}
    for ref, chans in _buffer_channels(nodes, values).items():
        for _idx, c in chans.items():
            net = c.get("out")
            if net and not net.startswith("unconnected-"):
                counts[net] = counts.get(net, 0) + 1
    return counts


def _check_led_drivers_are_paralleled(nodes, values, params) -> None:
    """Finding F1(b). Every optocoupler LED driven by an on-board buffer must be driven by
    at least TWO paralleled outputs, and those outputs must take their inputs from ONE
    net.

    The two halves of F1 are not independently shippable and this assertion is why: 430R
    starves the LED below its switching threshold, and 249R on a SINGLE output asks
    ~12.7mA of a pin budgeted at 7.5mA. Only both together land inside every bound.

    The tied-input requirement is not bookkeeping. Two tri-state outputs wired together
    are safe only while they are guaranteed to drive the same direction; if their inputs
    ever differ, one sources and the other sinks and the current is limited by nothing but
    the two output stages, which is a short across the die rather than a marginal load.
    """
    two_pin = _two_terminal_passive_refs(nodes)
    chans = _buffer_channels(nodes, values)
    counts = _parallel_driver_count(nodes, values)
    branches = _led_branches(nodes, values, two_pin)
    assert branches, "no optocoupler LED branches found at all -- this check would pass vacuously"

    single = []
    for b in branches:
        net = b["cathode_net"]
        n = counts.get(net, 0)
        if n == 0:
            continue  # not buffer-driven (a spare position, or an external/opto source)
        if n < 2:
            single.append((net, b))

    assert not single, (
        f"{len(single)} optocoupler LED(s) are driven by a SINGLE buffer output:\n  "
        + "\n  ".join(
            f"{net}: LED {b['opto']}.{b['pin']} via {b['r_ref']}={b['ohms']:.0f}R from {b['rail']}"
            for net, b in single
        )
        + f"\nFinding F1(b): at the resistor value that actually switches the "
        f"optocoupler, one output is asked for more than its budget, and the package's "
        f"{params['sn74ahct541']['i_gnd_abs_max_ma']}mA ground-pin absolute maximum is "
        f"exceeded on the packages driving the event bus, which idles at 0x0000 with "
        f"sixteen LEDs lit CONTINUOUSLY. Parallel two outputs per LED, from tied inputs."
    )


def test_led_drivers_are_paralleled(nodes, values, params):
    _check_led_drivers_are_paralleled(nodes, values, params)


def test_led_drivers_are_paralleled_fires_on_a_dropped_second_leg(nodes, values, params):
    """Half of F1 shipped alone -- the second leg removed from one LED, leaving the
    resistor value that only a paralleled pair can carry."""
    net = "EVT_D0_BUF"
    chans = _buffer_channels(nodes, values)
    victims = [r for r, c in chans.items() if any(v.get("out") == net for v in c.values())]
    corrupted = dict(nodes)
    corrupted[net] = [n for n in nodes[net] if n[0] != victims[-1]]
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF"):
        _check_led_drivers_are_paralleled(corrupted, values, params)


# --- 18b: paralleled outputs must take their inputs from ONE net. ----------------------


def _check_paralleled_outputs_have_tied_inputs(nodes, values) -> None:
    """Two tri-state outputs wired together are safe only while both are guaranteed to
    drive the same direction. If their inputs ever differ, one sources while the other
    sinks and the current is limited by nothing but the two output stages -- a short
    across the die, not a marginal load. Separate from 18a on purpose: 18a asks whether
    the paralleling EXISTS, this asks whether it is WIRED SAFELY, and a checker that
    bundled them would report one when it meant the other."""
    chans = _buffer_channels(nodes, values)
    by_net: dict[str, set[str]] = {}
    for _ref, cs in chans.items():
        for c in cs.values():
            net, src = c.get("out"), c.get("in")
            if net and src and not net.startswith("unconnected-"):
                by_net.setdefault(net, set()).add(src)
    mismatched = {net: sorted(ins) for net, ins in by_net.items() if len(ins) > 1}
    assert not mismatched, (
        f"{len(mismatched)} net(s) have paralleled buffer outputs whose INPUTS are not "
        f"tied together: "
        + "; ".join(f"{net} <- inputs {ins}" for net, ins in sorted(mismatched.items()))
        + "\nTwo tri-state outputs on one net are safe only while both are guaranteed to "
        "drive the same direction. Different inputs means one sources while the other "
        "sinks, limited by nothing but the two output stages."
    )


def test_paralleled_outputs_have_tied_inputs(nodes, values):
    _check_paralleled_outputs_have_tied_inputs(nodes, values)


def test_paralleled_outputs_have_tied_inputs_fires_on_untied_input(nodes, values):
    """The hazard paralleling introduces, asserted rather than assumed: a second output
    on the same net taking its input from somewhere else.

    Fabricated as a WHOLLY SYNTHETIC package rather than on a spare channel of a real
    one -- found necessary the hard way: borrowing a real package's channel index
    silently overwrites that channel's genuine output in `_buffer_channels`, so the
    control passed or failed depending on dict ordering rather than on the property under
    test, and would have rotted the moment F1 filled the spare channels it borrowed."""
    corrupted_values = dict(values) | {"UDBG97": "SN74AHCT541PW"}
    corrupted = dict(nodes)
    corrupted["EVT_D0_BUF"] = list(nodes["EVT_D0_BUF"]) + [("UDBG97", "18", "Y0_18", "tri_state")]
    corrupted["RWD_DLVR"] = list(nodes["RWD_DLVR"]) + [("UDBG97", "2", "A0_2", "input")]
    with pytest.raises(AssertionError, match=r"EVT_D0_BUF <- inputs"):
        _check_paralleled_outputs_have_tied_inputs(corrupted, corrupted_values)


# --- 17: per-PACKAGE ground-pin current, which no per-PIN check can see. ---------------


def _check_package_ground_current(nodes, values, params) -> None:
    """The AHCT541's absolute maximum through its GND pin is 75mA (SCLS269Q Sec.4.1), and
    U8/U9 each drive EIGHT LEDs. Because the drive is active-low and the event bus idles
    at 0x0000, sixteen LEDs are lit continuously -- a steady state, not a transient.

    This is a per-PACKAGE limit, so check 14 (per-PIN) cannot see it: eight pins each
    comfortably inside their own budget still add up to a package that is not.
    """
    two_pin = _two_terminal_passive_refs(nodes)
    drops = _rail_series_drop_v(params, nodes, values)
    counts = _parallel_driver_count(nodes, values)
    limit = params["sn74ahct541"]["i_gnd_abs_max_ma"]

    per_pkg: dict[str, list[tuple[str, float]]] = {}
    for b in _led_branches(nodes, values, two_pin):
        net = b["cathode_net"]
        n = counts.get(net, 0)
        if not n:
            continue
        _, i_max = _led_current_bounds_ma(b, params, drops)
        share = i_max / n
        for ref, chans in _buffer_channels(nodes, values).items():
            for c in chans.values():
                if c.get("out") == net:
                    per_pkg.setdefault(ref, []).append((net, share))

    over = [
        (ref, round(sum(ma for _n, ma in legs), 2), len(legs))
        for ref, legs in per_pkg.items()
        if sum(ma for _n, ma in legs) > limit + 1e-9
    ]
    assert not over, (
        f"{len(over)} buffer package(s) exceed the {limit}mA ground-pin absolute maximum "
        f"with every LED they drive lit simultaneously:\n  "
        + "\n  ".join(f"{ref}: {ma}mA across {n} LED-driving channel(s)" for ref, ma, n in over)
        + "\nThe event bus idles at 0x0000 and the drive is active-low, so this is a "
        "STEADY STATE, not a transient. A per-pin check cannot see it: eight pins each "
        "inside their own budget still add up to a package that is not."
    )


def test_package_ground_current(nodes, values, params):
    _check_package_ground_current(nodes, values, params)


def test_package_ground_current_fires_when_paralleling_is_removed(nodes, values, params):
    """F1's two halves are not independently shippable, asserted rather than asserted-in-
    prose: keep the resistor value and drop the paralleling, and the packages driving the
    event bus go over their ground-pin absolute maximum. This is the failure that made an
    earlier resistor-only attempt get reverted."""
    chans = _buffer_channels(nodes, values)
    evt_nets = {f"EVT_D{i}_BUF" for i in range(8)}
    # Strip every second driver from the D0-D7 LED nets, leaving one output per LED.
    seen, corrupted = set(), dict(nodes)
    for ref, cs in chans.items():
        for c in cs.values():
            net = c.get("out")
            if net in evt_nets:
                if net in seen:
                    corrupted[net] = [n for n in corrupted[net] if n[0] != ref]
                seen.add(net)
    with pytest.raises(AssertionError, match=r"ground-pin absolute maximum"):
        _check_package_ground_current(corrupted, values, params)


# ===========================================================================
# 19-20: FUSES (findings F7 and M7, hardware/breakout/parametric-audit.md).
#
# Two questions no structural checker asks, and they fail in opposite directions:
#
#   19. SELECTIVITY. Two protective devices in series only "coordinate" if the upstream
#       one holds more current than the downstream one trips at. Two IDENTICAL parts in
#       series have NO selectivity, and the board shipped exactly that: `F1` (the fan
#       feed) sat downstream of `F2` (main +12 V) at the same 0.5 A hold / 1.0 A trip, so
#       a fan fault at 0.9 A is below `F1`'s trip and well over `F2`'s hold -- `F2` can
#       trip first and kill the analog rails to protect the fans, inverting the entire
#       purpose of giving the fans their own fuse.
#
#   20. CAPACITY. A fuse must hold the rail's real maximum load at the real ambient, not
#       at the datasheet's 23 C. `F4` protects +5 V, which carries 495 mA maximum after
#       finding F1 raised the optocoupler drive; a 1206L050 holds ~0.44 A derated. It
#       trips in normal operation.
#
# Both read a VALUE and compare it against a DATASHEET LIMIT, like checks 15-18 above.
# ===========================================================================

# Which datasheet-params.toml section describes each polyfuse part number actually placed.
# Keyed by the Value string so the netlist itself selects the limits -- a fuse whose value
# is edited without a matching entry here fails loudly rather than being scored against
# whatever part the previous value happened to be.
POLYFUSE_PARAMS = {
    "1206L050/15YR": "polyfuse_1206l050",
    "1206L110-C": "polyfuse_1206l110",
}

# Ambient inside the sealed 2U chassis, in C. Spec §9.4 budgets 10-15 W of thermal load
# and the audit's own thermal check measures under 3 C rise at 12 CFM, so 35 C is a
# deliberately pessimistic operating ambient for a room-temperature rig room -- and PPTC
# hold current derates steeply, so this is the number the capacity check turns on.
FUSE_AMBIENT_C = 35.0

# Per-rail load, mA (typical, maximum). NOT derivable from a netlist -- it is the sum of
# every consumer's operating current, which no connectivity export carries -- so it is
# declared here, in one reviewable place, rather than left scattered through prose.
# Figures are finding M7's, recomputed there with every audit fix applied.
# `_check_fuse_capacity` guards that every fused rail has an entry, and separately
# cross-checks the +5 V figure against the LED current DERIVED from the netlist, so the
# half of it that moves when someone edits a resistor cannot silently go stale.
RAIL_LOAD_MA = {
    # +5V: 392/495 mA of on-board load (M7), PLUS the input current of finding F5's
    # TMR 1-0511, which moved the ISO_5V branch onto this rail from ISO_P12. At 76%
    # efficiency its 63/91 mA output costs 83/120 mA in -- so the rail total is 475/615 mA.
    # That is the whole cost side of F5's trade, and it lands on the rail M7 had just
    # fused: F4's 1206L110 holds ~973 mA derated, so 615 mA still clears by 1.58x.
    "+5V": (475.0, 615.0),
    "+12V": (97.0, 121.0),
    "-12V": (54.0, 66.0),
    "FAN_12V": (240.0, 240.0),
}


def _fuse_hold_at(params: dict, section: str, ambient_c: float) -> float:
    """Hold current at `ambient_c`, linearly interpolated between the two bracketing
    columns of the part's own Temperature Rerating table. Interpolated rather than
    rounded to the nearest column because the derating is steep: 1206L050/15 falls from
    0.50 A at 23 C to 0.42 A at 40 C, so picking the wrong column moves the answer by
    nearly 20%."""
    table = sorted((float(k), v) for k, v in params[section]["i_hold_vs_ambient_a"].items())
    assert table, f"{section}: no i_hold_vs_ambient_a table"
    if ambient_c <= table[0][0]:
        return table[0][1]
    for (t0, a0), (t1, a1) in zip(table, table[1:]):
        if t0 <= ambient_c <= t1:
            return a0 + (a1 - a0) * (ambient_c - t0) / (t1 - t0)
    return table[-1][1]


def _fuses(nodes, values) -> dict[str, dict]:
    """{fuse ref: {"value", "section", "nets"}} for every polyfuse on the board."""
    out = {}
    for name, nl in nodes.items():
        for ref, _pin, _pf, _pt in nl:
            v = values.get(ref, "")
            if not ref.startswith("F") or ref.startswith("FB") or v not in POLYFUSE_PARAMS:
                continue
            out.setdefault(ref, {"value": v, "section": POLYFUSE_PARAMS[v], "nets": set()})
            out[ref]["nets"].add(name)
    return out


def _check_every_polyfuse_is_priced(nodes, values) -> None:
    """A fuse whose Value has no entry in POLYFUSE_PARAMS would be silently skipped by
    both checks below -- the vacuous-pass failure mode this project has hit three times."""
    placed = {
        ref: values.get(ref, "")
        for nl in nodes.values() for ref, _p, _pf, _t in nl
        if ref.startswith("F") and not ref.startswith("FB")
    }
    unpriced = {r: v for r, v in placed.items() if v not in POLYFUSE_PARAMS}
    assert not unpriced, (
        f"polyfuse(s) {unpriced} have no POLYFUSE_PARAMS entry -- add one pointing at "
        f"their datasheet-params.toml section. Without it both fuse checks below skip "
        f"them entirely and pass vacuously."
    )


def test_every_polyfuse_is_priced(nodes, values):
    _check_every_polyfuse_is_priced(nodes, values)


def test_every_polyfuse_is_priced_fires_on_unknown_part(nodes, values):
    corrupted = dict(values) | {"F4": "SOME-OTHER-PPTC"}
    with pytest.raises(AssertionError, match=r"F4"):
        _check_every_polyfuse_is_priced(nodes, corrupted)


# --- 19: series fuse coordination. -----------------------------------------------------


def _check_series_fuse_coordination(nodes, values, params) -> None:
    """Finding F7. For any two fuses in series, the UPSTREAM one's hold current must
    exceed the DOWNSTREAM one's TRIP current -- otherwise a downstream fault can open the
    upstream device first, and the fuse that was supposed to isolate one branch takes out
    everything the upstream device protects.

    "In series" is derived structurally, by a CUT TEST rather than by reachability: fuse A
    is upstream of fuse B when removing A disconnects B from the supply inlet. That is the
    exact meaning of "all of B's current flows through A", and it gets the DIRECTION right
    for free -- removing the fan fuse does not disconnect the main rail, so the pair is
    reported once, the right way round.

    Direction matters more than it sounds. Plain reachability calls every fuse upstream of
    every other, because the graph is undirected and each rail reaches every other rail
    through the ground net via any two decoupling capacitors. Two guards handle that: only
    SERIES elements are edges (a two-terminal part with BOTH pins on non-ground nets -- a
    decoupling cap has one pin on ground and is a shunt, not a series element), and the cut
    test supplies the direction the graph itself does not carry.

    This is also where finding F7's own published fix went wrong. It names `P12_FUSED` as
    "upstream of `F2`", but `P12_FUSED` is `F2`'s OUTPUT -- so tapping the fan there would
    leave fan current still flowing through `F2` while removing the fan branch's
    reverse-polarity protection. Reasoning from net NAMES rather than from topology is
    exactly the error this check exists to make impossible.
    """
    fuses = _fuses(nodes, values)
    assert fuses, "no polyfuses found at all -- this check would pass vacuously"
    series, source_nets = _series_graph(nodes, values)
    assert source_nets, (
        "could not identify the supply inlet's own rails (a connector feeding two or more "
        "fuse input nets) -- this check would pass vacuously"
    )

    bad = []
    for a_ref, a in fuses.items():
        without_a = _reachable_from_source(series, source_nets, without=a_ref)
        for b_ref, b in fuses.items():
            if b_ref == a_ref:
                continue
            # All of B's current flows through A iff removing A strands B entirely.
            if b["nets"] & without_a:
                continue
            a_p, b_p = params[a["section"]], params[b["section"]]
            if a_p["i_hold_a"] <= b_p["i_trip_a"]:
                bad.append((a_ref, a["value"], a_p["i_hold_a"], b_ref, b["value"], b_p["i_trip_a"]))
    bad = sorted(set(bad))
    assert not bad, (
        f"{len(bad)} fuse pair(s) in series have no selectivity -- the upstream device's "
        f"hold current does not exceed the downstream device's trip current:\n  "
        + "\n  ".join(
            f"{au} ({av}, hold {ah} A) upstream of {bu} ({bv}, trip {bt} A)"
            for au, av, ah, bu, bv, bt in bad
        )
        + "\nFinding F7: a fault below the DOWNSTREAM device's trip point but above the "
        "UPSTREAM device's hold point opens the upstream one first, so the fuse that "
        "exists to isolate one branch instead takes out everything upstream protects. On "
        "this board that meant a fan fault killing the analog rails."
    )


def test_series_fuse_coordination(nodes, values, params):
    _check_series_fuse_coordination(nodes, values, params)


def test_series_fuse_coordination_fires_on_identical_parts_in_series(nodes, values, params):
    """The defect verbatim: put the fan feed back downstream of the main +12 V fuse, two
    identical 0.5 A/1.0 A parts in series. Fabricated by moving F1's input from its own
    branch onto the main rail's output net."""
    f1_in = next(n for n in nodes["P12_RAW"] if n[0] == "F1") if any(
        n[0] == "F1" for n in nodes.get("P12_RAW", [])
    ) else None
    corrupted = dict(nodes)
    if f1_in:  # post-fix board: move F1 back downstream of F2
        corrupted["P12_RAW"] = [n for n in nodes["P12_RAW"] if n[0] != "F1"]
        corrupted["+12V"] = list(nodes["+12V"]) + [f1_in]
    with pytest.raises(AssertionError, match=r"no selectivity"):
        _check_series_fuse_coordination(corrupted, values, params)


# --- 20: fuse capacity against the real rail load at the real ambient. -----------------


def _check_fuse_capacity(nodes, values, params) -> None:
    """Finding M7. Each fuse's hold current, DERATED to the operating ambient, must cover
    the maximum load of the rail it protects.

    PPTC hold current is characterised at 23 C and falls steeply -- a 1206L050 holds
    0.50 A at 23 C and 0.42 A at 40 C -- so checking against the headline number is
    checking against a condition the board never operates in.
    """
    fuses = _fuses(nodes, values)
    assert fuses, "no polyfuses found at all -- this check would pass vacuously"

    # Which rail each fuse protects: the one of its two nets that a declared load names,
    # directly or through the reverse-polarity diode that follows it.
    two_pin = _two_terminal_passive_refs(nodes)
    undersized, unpriced = [], []
    for ref, f in fuses.items():
        rails = set()
        for net in f["nets"]:
            if net in RAIL_LOAD_MA:
                rails.add(net)
            for other, othernets in two_pin.items():
                if other != ref and net in othernets:
                    rails |= {n for n in othernets if n in RAIL_LOAD_MA}
        if not rails:
            unpriced.append((ref, sorted(f["nets"])))
            continue
        hold_ma = _fuse_hold_at(params, f["section"], FUSE_AMBIENT_C) * 1000
        for rail in sorted(rails):
            _typ, mx = RAIL_LOAD_MA[rail]
            if hold_ma < mx:
                undersized.append((ref, f["value"], rail, round(hold_ma, 1), mx))

    assert not unpriced, (
        f"fuse(s) {unpriced} protect no rail named in RAIL_LOAD_MA -- add the rail and its "
        f"load, or this fuse's sizing is never checked at all."
    )
    assert not undersized, (
        f"{len(undersized)} fuse(s) cannot hold their rail's maximum load at "
        f"{FUSE_AMBIENT_C} C:\n  "
        + "\n  ".join(
            f"{ref} ({val}) on {rail}: holds {hold} mA derated, rail draws up to {mx} mA"
            for ref, val, rail, hold, mx in undersized
        )
        + "\nFinding M7. PPTC hold current is characterised at 23 C and derates steeply, "
        "so a fuse chosen against the headline number is chosen against a condition the "
        "board never operates in."
    )


def test_fuse_capacity(nodes, values, params):
    _check_fuse_capacity(nodes, values, params)


def test_fuse_capacity_fires_on_undersized_fuse(nodes, values, params):
    """The defect verbatim: F4 back at 1206L050, whose ~0.44 A derated hold does not cover
    the +5 V rail's 495 mA maximum."""
    corrupted = dict(values) | {"F4": "1206L050/15YR"}
    with pytest.raises(AssertionError, match=r"F4.*\+5V"):
        _check_fuse_capacity(nodes, corrupted, params)


def test_fuse_capacity_derates_rather_than_using_the_headline_rating(params):
    """The derating is the whole point, asserted rather than assumed.

    Stated as a property of the PART, not against `RAIL_LOAD_MA["+5V"]`. It was written
    the other way first -- "the headline accepts the rail's 495 mA and the derated figure
    rejects it" -- which was true and stopped being true the moment finding F5 moved the
    ISO_5V branch onto this rail and took it to 615 mA. A demonstration pinned to a number
    that legitimately moves is a demonstration that breaks for the wrong reason.

    The historical fact it was capturing is worth keeping in prose: when M7 was
    implemented the +5 V rail was 495 mA, which sat between the 1206L050's 500 mA headline
    and its ~443 mA figure at 35 C -- accepted by the first, rejected by the second. That
    gap is exactly why F4 had to change part, and the assertions below hold it open.
    """
    at_23 = _fuse_hold_at(params, "polyfuse_1206l050", 23.0) * 1000
    at_amb = _fuse_hold_at(params, "polyfuse_1206l050", FUSE_AMBIENT_C) * 1000
    assert at_amb < at_23 * 0.95, (
        f"derating at {FUSE_AMBIENT_C} C ({at_amb:.0f} mA) is within 5% of the 23 C "
        f"headline ({at_23:.0f} mA) -- if that ever became true, checking against the "
        f"headline would be harmless and this whole check would be pointless"
    )
    midpoint = (at_23 + at_amb) / 2
    assert at_amb < midpoint < at_23, (
        "there must exist a load the headline rating accepts and the derated rating "
        "rejects -- that band is the entire failure mode finding M7 caught"
    )


def test_rail_load_table_matches_the_led_current_derived_from_the_netlist(nodes, values, params):
    """Keeps the declared +5 V figure honest. The LED share of that rail IS derivable, so
    it is derived here and compared against the declaration -- if someone edits a resistor
    or adds a channel, the hand-written rail total is caught drifting instead of quietly
    under-reporting what the fuse has to hold."""
    two_pin = _two_terminal_passive_refs(nodes)
    drops = _rail_series_drop_v(params, nodes, values)
    led_max = sum(
        _led_current_bounds_ma(b, params, drops)[1]
        for b in _led_branches(nodes, values, two_pin)
        if b["rail"] == "+5V"
    )
    declared_max = RAIL_LOAD_MA["+5V"][1]
    assert led_max <= declared_max, (
        f"+5 V LEDs alone draw up to {led_max:.0f} mA, but RAIL_LOAD_MA declares the whole "
        f"rail at {declared_max} mA -- the declared total no longer covers its own LED "
        f"share, so every fuse decision resting on it is stale."
    )
    assert led_max > 0.5 * declared_max, (
        f"+5 V LEDs draw {led_max:.0f} mA of a declared {declared_max} mA. If the LED "
        f"share has fallen below half the rail, RAIL_LOAD_MA was probably not updated "
        f"alongside whatever change caused that -- re-derive it rather than loosening "
        f"this bound."
    )


# ===========================================================================
# 21: ISOLATED SUPPLY LOADING AND BALANCE (finding F5).
#
# The isolated +/-12 V module is an IH1215D: 66 mA per rail, UNREGULATED. Two things
# about it are checkable and neither was:
#
#   - Its per-rail load against that 66 mA. `ISO_P12` carried 114-162% of it, because the
#     linear regulator making `ISO_5V` drew that whole digital branch THROUGH the isolated
#     module. A linear regulator passes its output current straight to its input, so the
#     load simply appears one rail upstream -- which is exactly the kind of hop a
#     per-sheet, per-net checker cannot see.
#
#   - The BALANCE between the two rails. Cross-regulation on an unregulated dual module is
#     only characterised when the lighter rail carries 25-100% of rating
#     (`ih_series.cross_regulation_valid_range_pct`); at 162% on one rail and 24% on the
#     other, the datasheet's +/-5% cross-regulation figure does not describe this board at
#     all. Every rail can be individually "within rating" and the pair still be outside
#     the conditions its specs were measured under.
#
# WHERE ISO_5V'S LOAD LANDS IS DERIVED, NOT DECLARED. The check finds what feeds `ISO_5V`
# and asks which side of the barrier it draws from: a linear regulator bridging
# `ISO_P12 -> ISO_5V` puts the load on the isolated module, an isolated converter with its
# primary on `+5V` does not. That single structural question is the whole of finding F5,
# so the check turns on it rather than on a hand-maintained number.
# ===========================================================================

ISO_SUPPLY_RATED_MA = 66.0  # IH1215D per rail -- cross-checked against params below.


def _iso_5v_load_ma(nodes, values, params) -> tuple[float, float]:
    """(typical, maximum) mA drawn from ISO_5V, derived from the netlist.

    Three consumers, each counted structurally: the optocoupler detector stages whose VDD
    sits on ISO_5V (I_DDL, the LED-on case, which is the worst one), the pull-ups bridging
    ISO_5V to a channel's output net, and any LED anode fed from ISO_5V.
    """
    acsl = params["acsl_6xx0"]
    two_pin = _two_terminal_passive_refs(nodes)
    iso_refs = {r for r, _p, _pf, _t in nodes.get("ISO_5V", [])}

    # Detector channels on ISO_5V: a VO pin whose own package has VDD on ISO_5V. Counted
    # per CHANNEL, not per package -- the ACSL-6420 straddles the barrier by design, with
    # only two of its four channels' detectors on this rail.
    # `VO\d+_` -- a NUMBERED channel output, not a bare "VO". Found the hard way: the
    # LD1117S50 regulator's own output pin is `VO_2`, so a plain startswith("VO") counted
    # the supply itself as a seventh detector channel drawing 10.5 mA from the rail it
    # produces.
    vo_re = re.compile(r"^VO\d+_")
    chans = 0
    for _name, nl in nodes.items():
        for ref, _pin, pf, _pt in nl:
            if not vo_re.match(pf or "") or ref not in iso_refs:
                continue
            # This VO belongs to ISO_5V's side only if its own pull-up returns to ISO_5V.
            # That is what separates the ACSL-6420's two ISO_5V-side channels from its two
            # +5V-side ones -- the package straddles the barrier by design.
            pu = {r for r, _p, _pf2, _t2 in nl} & set(two_pin)
            if any("ISO_5V" in two_pin[r] for r in pu):
                chans += 1

    pullup_ma = sum(
        (RAIL_VOLTS["ISO_5V"] - DRIVER_VOL_V) / ohms * 1000
        for r in iso_refs & set(two_pin)
        if (ohms := _parse_ohms(values.get(r, ""))) and ohms > 1000
    )
    drops = _rail_series_drop_v(params, nodes, values)
    led_ma = sum(
        _led_current_bounds_ma(b, params, drops)[1]
        for b in _led_branches(nodes, values, two_pin) if b["rail"] == "ISO_5V"
    )
    return (chans * acsl["i_ddl_typ_ma"] + pullup_ma + led_ma,
            chans * acsl["i_ddl_max_ma"] + pullup_ma + led_ma)


def _iso_5v_drawn_from(nodes, values) -> str | None:
    """Which rail ISO_5V's supply draws its input current from.

    A LINEAR regulator passes output current to its input, so its input rail carries the
    ISO_5V load. An ISOLATED converter's primary is a different net on the other side of
    the barrier, and the isolated module never sees that current. Distinguished by whether
    the part feeding ISO_5V also has a pin on an isolated 12 V rail.
    """
    # The SUPPLY is the part with a power_out pin on ISO_5V -- not merely any part
    # connected to it. Found the hard way: both ACSL optocouplers have pins on ISO_5V AND
    # on +5V (they straddle the barrier by design), so "first U-ref touching a candidate
    # rail" returned +5V from an optocoupler and silently declared the load already moved.
    sources = [
        r for r, _p, _pf, pt in nodes.get("ISO_5V", [])
        if r.startswith("U") and "power_out" in pt
    ]
    if len(sources) != 1:
        return None
    nets_of = _part_nets(nodes).get(sources[0], set())
    for candidate in ("ISO_P12", "ISO_N12", "+5V", "+12V"):
        if candidate in nets_of:
            return candidate
    return None


def _check_isolated_supply_loading(nodes, values, params) -> None:
    ih = params["ih_series"]
    assert ih["i_out_per_rail_ma"] == ISO_SUPPLY_RATED_MA, (
        f"IH1215D per-rail rating moved to {ih['i_out_per_rail_ma']} mA in "
        f"datasheet-params.toml but this check still uses {ISO_SUPPLY_RATED_MA}"
    )
    ina = params["ina105"]
    n_ina = len({
        r for r, _p, _pf, _t in nodes.get("ISO_P12", [])
        if values.get(r, "").startswith("INA105")
    })
    assert n_ina, "no INA105 found on ISO_P12 -- this check would pass vacuously"

    iso5v_typ, iso5v_max = _iso_5v_load_ma(nodes, values, params)
    source = _iso_5v_drawn_from(nodes, values)
    assert source, "could not determine what supplies ISO_5V -- this check would pass vacuously"
    passes_through = source in ("ISO_P12", "ISO_N12")

    p12 = (n_ina * ina["i_q_typ_ma"] + (iso5v_typ if passes_through else 0.0),
           n_ina * ina["i_q_max_ma"] + (iso5v_max if passes_through else 0.0))
    n12 = (n_ina * ina["i_q_typ_ma"], n_ina * ina["i_q_max_ma"])

    over = [
        (rail, round(mx, 1))
        for rail, (_t, mx) in (("ISO_P12", p12), ("ISO_N12", n12))
        if mx > ISO_SUPPLY_RATED_MA
    ]
    assert not over, (
        f"isolated rail(s) over the IH1215D's {ISO_SUPPLY_RATED_MA} mA per-rail rating: "
        + "; ".join(f"{r} at {ma} mA ({ma / ISO_SUPPLY_RATED_MA:.0%})" for r, ma in over)
        + f"\nISO_5V draws {iso5v_max:.0f} mA max and is supplied from {source!r}"
        + (", a LINEAR regulator -- so that entire digital branch is drawn THROUGH the "
           "isolated module. Finding F5: supply ISO_5V across the barrier from +5V "
           "instead, and the isolated module carries only the INA105 analog load."
           if passes_through else ".")
    )

    # Balance: cross-regulation is only characterised over this range on the LIGHTER rail.
    lo, hi = min(p12[1], n12[1]), max(p12[1], n12[1])
    assert hi <= 2.0 * lo, (
        f"the isolated module's two rails are unbalanced {hi:.0f} mA vs {lo:.0f} mA "
        f"({hi / max(lo, 1e-9):.1f}:1). It is UNREGULATED, and its cross-regulation figure "
        f"({ih['cross_regulation_pct']}%) is characterised only with the lighter rail at "
        f"{ih['cross_regulation_valid_range_pct'][0]}-"
        f"{ih['cross_regulation_valid_range_pct'][1]}% of rating -- so at this imbalance "
        f"the datasheet does not describe this board. This is a PAIRWISE property: both "
        f"rails can be individually within rating and still be outside the conditions "
        f"their specs were measured under."
    )


def test_isolated_supply_loading(nodes, values, params):
    _check_isolated_supply_loading(nodes, values, params)


def test_isolated_supply_loading_fires_when_iso_5v_is_drawn_through_the_module(nodes, values, params):
    """The defect verbatim: ISO_5V supplied by a linear regulator off ISO_P12, so the
    whole digital branch is drawn through the isolated module's positive rail."""
    ldo = "UDBG96"
    corrupted_values = dict(values) | {ldo: "LD1117S50TR_SOT223"}
    corrupted = dict(nodes)
    # Swap whatever currently sources ISO_5V for a linear regulator fed from ISO_P12.
    # Identified by pintype rather than by refdes so this control keeps testing the
    # property rather than a particular part number.
    corrupted["ISO_5V"] = [
        n for n in nodes["ISO_5V"] if not (n[0].startswith("U") and "power_out" in n[3])
    ] + [(ldo, "2", "VO_2", "power_out")]
    corrupted["ISO_P12"] = list(nodes.get("ISO_P12", [])) + [(ldo, "3", "VI_3", "power_in")]
    with pytest.raises(AssertionError, match=r"LINEAR regulator"):
        _check_isolated_supply_loading(corrupted, corrupted_values, params)


# ===========================================================================
# 22-25: THE ANALOG FRONT END (findings F3, M1, M2, M4).
#
# Four more rows of the audit's own proposed-checker table, and they share a shape with
# F1: each asks whether a VALUE that was chosen once, correctly, against one set of
# assumptions still holds against the real ones.
#
#   22. F3 -- the photodiode transimpedance stage's output at full-scale light, against
#       the system's own +/-5 V convention. A 1 Mohm feedback resistor demanded 11 V out
#       of an amplifier that cannot produce it and a system that could not accept it.
#   23. M1 -- that stage's compensation, against the input capacitance it actually sees.
#       The photodiode is run at ZERO bias, where its capacitance is at maximum and
#       Thorlabs publish nothing; the fitted 3.3 pF was optimal for a number the design
#       never operates at.
#   24. M2 -- where the mux taps the photodiode channels. Tapping behind the anti-alias
#       resistor unbalances the difference amplifier that receives it.
#   25. M4 -- pull-up RC against the timing requirement of the signal it carries. A
#       comparator whose entire purpose is precise stimulus-onset timing had a 2.2 us
#       rise on its output.
# ===========================================================================

# Irradiance at the photodiode from a display flip patch, W/cm^2. NOT a datasheet number
# -- it is this design's own stated operating assumption (parametric-audit.md, F3), and it
# is declared here so the assertion below is honest about what it rests on. A brighter
# panel scales the answer linearly; the 180k value has ~2.5x headroom to the +/-5 V
# convention, which is the real margin against this estimate being wrong.
DISPLAY_IRRADIANCE_W_PER_CM2 = 0.29e-3

# The system-wide analog convention (spec Sec.5): every analog signal on this board is
# carried as +/-5 V, which is what both NI and Intan are configured to accept.
ANALOG_CONVENTION_V = 5.0

# Worst-case total input capacitance at the TIA summing junction, F. Photodiode junction
# capacitance at ZERO bias (unpublished, estimated 130-380 pF -- see fds100's own note)
# plus 3-5 m of coax at ~100 pF/m. The compensation has to be robust across this range
# rather than tuned to a point in it.
TIA_C_IN_WORST_F = 900e-12

# The photodiode channels carry a display flip edge; ~10 kHz is what the timing analysis
# needs from them (parametric-audit.md, M1).
TIA_SIGNAL_BW_HZ = 10e3


def _tia_stages(nodes, values):
    """[(feedback resistor ref, feedback capacitor ref, output net)] for every
    transimpedance stage -- found structurally, as an R and a C sharing BOTH nodes (the
    summing junction and the amplifier output). That parallel RC across an op-amp is what
    a TIA feedback network IS, so this needs no list of which channels are photodiodes."""
    two_pin = _two_terminal_passive_refs(nodes)
    out = []
    for r_ref, r_nets in two_pin.items():
        if not r_ref.startswith("R") or not _parse_ohms(values.get(r_ref, "")):
            continue
        for c_ref, c_nets in two_pin.items():
            if not c_ref.startswith("C") or c_nets != r_nets:
                continue
            # The output side is whichever of the two nets an op-amp output pin sits on.
            for net in sorted(r_nets):
                if any("output" in pt for ref, _p, _pf, pt in nodes[net] if ref.startswith("U")):
                    out.append((r_ref, c_ref, net))
                    break
    return sorted(set(out))


def _parse_farads(value: str) -> float | None:
    v = value.strip().replace("F", "")
    mult = {"p": 1e-12, "n": 1e-9, "u": 1e-6}.get(v[-1:] if v[-1:].isalpha() else "", None)
    if mult is None:
        return None
    try:
        return float(v[:-1]) * mult
    except ValueError:
        return None


def _check_tia_output_within_convention(nodes, values, params) -> None:
    """Finding F3. Full-scale photocurrent through the feedback resistor must not demand
    an output voltage the system cannot carry."""
    fds = params["fds100"]
    i_photo = (DISPLAY_IRRADIANCE_W_PER_CM2 * fds["active_area_mm2"] / 100.0
               * fds["responsivity_at_550nm_a_per_w"])
    stages = _tia_stages(nodes, values)
    assert stages, "no transimpedance stage found at all -- this check would pass vacuously"
    saturated = []
    for r_ref, _c_ref, out_net in stages:
        r_f = _parse_ohms(values[r_ref])
        v_out = i_photo * r_f
        if v_out > ANALOG_CONVENTION_V:
            saturated.append((out_net, r_ref, r_f, v_out))
    assert not saturated, (
        f"{len(saturated)} transimpedance stage(s) demand more output swing than this "
        f"board's +/-{ANALOG_CONVENTION_V} V analog convention allows at full-scale light "
        f"({i_photo * 1e6:.1f} uA from a {fds['active_area_mm2']} mm2 FDS100 at "
        f"{fds['responsivity_at_550nm_a_per_w']} A/W):\n  "
        + "\n  ".join(
            f"{net}: {r}={ohms / 1e3:.0f}k demands {v:.1f} V"
            for net, r, ohms, v in saturated
        )
        + "\nFinding F3. The stage clips long before full scale, so the brightest part of "
        "every flip is flat -- and the edge it exists to time is the part that survives, "
        "which is why this is not merely a scaling error."
    )


def test_tia_output_within_convention(nodes, values, params):
    _check_tia_output_within_convention(nodes, values, params)


def test_tia_output_within_convention_fires_on_oversized_feedback(nodes, values, params):
    """The defect verbatim: a 1 Mohm feedback resistor, demanding ~11 V."""
    stages = _tia_stages(nodes, values)
    corrupted = dict(values) | {stages[0][0]: "1M"}
    with pytest.raises(AssertionError, match=r"demands 1[01]\.\d V"):
        _check_tia_output_within_convention(nodes, corrupted, params)


def _check_tia_compensation(nodes, values, params) -> None:
    """Finding M1. The feedback capacitor must be large enough to keep the loop stable at
    the input capacitance this stage actually sees.

    The stability criterion for a transimpedance amplifier is
    C_f >= sqrt(C_in / (2*pi*R_f*GBW)); below it the loop peaks and rings, which lands
    directly on the edge these channels exist to time. Evaluated at the WORST-CASE C_in
    rather than a typical one, because the photodiode runs at zero bias where its own
    capacitance is unpublished and at maximum.
    """
    gbw = params["opax197"]["gbw_mhz"] * 1e6
    stages = _tia_stages(nodes, values)
    assert stages, "no transimpedance stage found at all -- this check would pass vacuously"
    bad = []
    for r_ref, c_ref, out_net in stages:
        r_f, c_f = _parse_ohms(values[r_ref]), _parse_farads(values[c_ref])
        assert c_f, f"{c_ref}: unparseable capacitance {values[c_ref]!r}"
        c_min = (TIA_C_IN_WORST_F / (2 * 3.141592653589793 * r_f * gbw)) ** 0.5
        f_3db = 1.0 / (2 * 3.141592653589793 * r_f * c_f)
        if c_f < c_min:
            bad.append(("under-compensated", out_net, c_ref, c_f, c_min, f_3db))
        elif f_3db < TIA_SIGNAL_BW_HZ:
            bad.append(("over-compensated", out_net, c_ref, c_f, c_min, f_3db))
    assert not bad, (
        "transimpedance compensation is outside the usable window:\n  "
        + "\n  ".join(
            f"{net}: {c}={cf * 1e12:.1f}pF {kind} -- needs >= {cmin * 1e12:.1f}pF for "
            f"stability at {TIA_C_IN_WORST_F * 1e12:.0f}pF of input capacitance, and must "
            f"keep -3 dB above {TIA_SIGNAL_BW_HZ / 1e3:.0f}kHz (this gives {f3 / 1e3:.1f}kHz)"
            for kind, net, c, cf, cmin, f3 in bad
        )
        + "\nFinding M1. The photodiode is run at ZERO bias -- the no-power-in-the-booth "
        "constraint -- where its junction capacitance is at maximum and Thorlabs publish "
        "nothing at all. Compensation has to be robust across the estimated range, not "
        "fitted to a point in it."
    )


def test_tia_compensation(nodes, values, params):
    _check_tia_compensation(nodes, values, params)


def test_tia_compensation_fires_on_under_compensation(nodes, values, params):
    """The defect verbatim: 3.3 pF, optimal for a 1 Mohm stage at a typical input
    capacitance and too small for the real one."""
    stages = _tia_stages(nodes, values)
    corrupted = dict(values) | {stages[0][1]: "3.3pF"}
    with pytest.raises(AssertionError, match=r"under-compensated"):
        _check_tia_compensation(nodes, corrupted, params)


def test_tia_compensation_fires_on_over_compensation(nodes, values, params):
    """The other bound: enough capacitance to kill the bandwidth the channel needs."""
    stages = _tia_stages(nodes, values)
    corrupted = dict(values) | {stages[0][1]: "10nF"}
    with pytest.raises(AssertionError, match=r"over-compensated"):
        _check_tia_compensation(nodes, corrupted, params)


# --- 24: the mux must tap a BUFFERED node (M2). ----------------------------------------


def _check_mux_taps_are_buffered(nodes, values) -> None:
    """Finding M2. Every analog mux input must sit on a node driven by an amplifier
    output, never on a passive node behind a series resistor.

    Why it matters is one hop further on than the mux. The Intan-side `INA105` receives
    the mux output on IN+ with AGND on IN-, so ANY series resistance in the mux leg
    unbalances that bridge and drops its CMRR by 1/(R_series/50k). The mux's own ~180 ohm
    R_on is unavoidable and costs ~49 dB, which is adequate. Tapping BEHIND a 1.6k
    anti-alias resistor adds that to the leg as well and takes it to ~30 dB -- so the two
    photodiode channels alone were an order of magnitude worse at rejecting the
    inter-rack ground offset every other channel handles fine.

    Stated over every mux input, not over the two channels known to be wrong: the defect
    is a tap point, and a future channel added the same way would be equally wrong.
    """
    part_nets = _part_nets(nodes)
    two_pin = _two_terminal_passive_refs(nodes)
    mux_refs = {r for r, v in values.items() if v.startswith("ADG1206")}
    assert mux_refs, "no ADG1206 mux found at all -- this check would pass vacuously"

    unbuffered = []
    for name, nl in nodes.items():
        muxes_here = {ref for ref, _p, _pf, _t in nl if ref in mux_refs}
        if not muxes_here:
            continue
        if name in RAIL_VOLTS or name.startswith("MUX_"):
            continue  # supply and address lines, not signal inputs
        driven = any(
            "output" in pt for ref, _p, _pf, pt in nl
            if ref.startswith("U") and ref not in mux_refs
        )
        if driven:
            continue
        # Not amplifier-driven: is it passive behind a series resistor?
        series = sorted(
            r for r in {rr for rr, _p, _pf, _t in nl} & set(two_pin)
            if r.startswith("R") and not ({n for n in two_pin[r]} & set(RAIL_VOLTS))
        )
        if series:
            unbuffered.append((name, sorted(muxes_here), series))
    assert not unbuffered, (
        f"{len(unbuffered)} mux input net(s) tap a passive node behind series resistance "
        f"rather than an amplifier output:\n  "
        + "\n  ".join(
            f"{net}: {len(m)} mux input(s) behind series resistor(s) {s}"
            for net, m, s in unbuffered
        )
        + "\nFinding M2. Series resistance in the mux leg unbalances the INA105 that "
        "receives it -- CMRR falls to 1/(R_series/50k). Retap to the unity-gain buffer "
        "that already exists on these nodes; it is a net reassignment, not a new part."
    )


def test_mux_taps_are_buffered(nodes, values):
    _check_mux_taps_are_buffered(nodes, values)


# --- 25: pull-up RC against the timing the signal actually needs (M4). -----------------

# Capacitance a comparator output drives: a sync-module GPIO pad, two AHCT541 inputs, the
# 1 Mohm hysteresis leg and trace. ~100 pF (parametric-audit.md, M4) -- an estimate, and
# declared as one.
COMPARATOR_LOAD_PF = 100.0

# DESIGN RULE, not a datasheet limit: a comparator edge used to timestamp stimulus onset
# must rise in under 1 us. These channels exist precisely so NI gets a clean digital edge
# instead of having to scan the analog waveform fast enough to find one (spec Sec.3.1), so
# an edge slower than the analog path's own ~10 kHz (100 us) resolution by less than two
# decades is not buying what it was added to buy.
COMPARATOR_RISE_MAX_US = 1.0


def _check_comparator_pullup_rise_time(nodes, values, params) -> None:
    lm = params["lm339"]
    outputs = [n for n in COMPARATOR_OUTPUTS]
    slow, sinks = [], []
    for out_net in outputs:
        for r_ref in _bridging_resistor_refs(nodes, "+3V3", out_net):
            ohms = _parse_ohms(values.get(r_ref, ""))
            if not ohms:
                continue
            # 10-90% rise through an RC is 2.2*R*C.
            rise_us = 2.2 * ohms * COMPARATOR_LOAD_PF * 1e-12 * 1e6
            if rise_us > COMPARATOR_RISE_MAX_US:
                slow.append((out_net, r_ref, ohms, rise_us))
            sink_ma = RAIL_VOLTS["+3V3"] / ohms * 1000
            if sink_ma > lm["i_o_abs_max_ma"]:
                sinks.append((out_net, r_ref, ohms, sink_ma))
    assert not slow, (
        f"{len(slow)} comparator output(s) rise too slowly for the timing they carry "
        f"(limit {COMPARATOR_RISE_MAX_US} us into an estimated {COMPARATOR_LOAD_PF:.0f} pF):\n  "
        + "\n  ".join(
            f"{net}: {r}={ohms / 1e3:.1f}k gives {us:.2f} us" for net, r, ohms, us in slow
        )
        + "\nFinding M4. These outputs exist to give a clean, precisely-timed digital edge "
        "for stimulus onset; a pull-up chosen for low current instead of for edge rate "
        "gives away the thing they were added for."
    )
    assert not sinks, (
        f"lowering the pull-up must not exceed the LM339's own "
        f"{lm['i_o_abs_max_ma']} mA output absolute maximum: "
        + "; ".join(f"{net}: {r}={ohms:.0f} draws {ma:.1f} mA" for net, r, ohms, ma in sinks)
    )


def test_comparator_pullup_rise_time(nodes, values, params):
    _check_comparator_pullup_rise_time(nodes, values, params)


def test_comparator_pullup_rise_time_fires_on_slow_pullup(nodes, values, params):
    """The defect verbatim: 10k pull-ups, 2.2 us into ~100 pF."""
    corrupted = dict(values)
    for out_net in COMPARATOR_OUTPUTS:
        for r_ref in _bridging_resistor_refs(nodes, "+3V3", out_net):
            corrupted[r_ref] = "10k"
    with pytest.raises(AssertionError, match=r"2\.20 us"):
        _check_comparator_pullup_rise_time(nodes, corrupted, params)


def test_comparator_pullup_rise_time_fires_on_overloading_the_comparator(nodes, values, params):
    """The other bound: a pull-up small enough to be fast and too small for the LM339 to
    sink. Both ends matter -- the same two-sided-constraint lesson as finding F1."""
    corrupted = dict(values)
    for out_net in COMPARATOR_OUTPUTS:
        for r_ref in _bridging_resistor_refs(nodes, "+3V3", out_net):
            corrupted[r_ref] = "100"
    # Matches on "output absolute maximum", the durable part of the complaint -- the
    # numeric limit is read from datasheet-params.toml and moves with a datasheet revision.
    with pytest.raises(AssertionError, match=r"output absolute maximum"):
        _check_comparator_pullup_rise_time(nodes, corrupted, params)


# ===========================================================================
# 26: PANEL OUTPUTS -- fan-out and series termination (findings F4 and M3).
#
# Two properties of the same wire, and the board violated both on the same six pins:
#
#   - HOW MANY panel connectors one logic output drives. `CAM_TRIG_BEH` drove FOUR BNCs
#     from a single SN74AHCT541 output: four parallel coax runs are ~12.5 ohm of
#     transmission line, and the initial edge into that draws roughly 118 mA against the
#     part's 25 mA per-output ABSOLUTE MAXIMUM. Short, but on every edge for the life of
#     the board.
#
#   - WHETHER there is series resistance between the driver and the connector. Without it
#     the cameras see reflections that can double-trigger -- and a double-triggered
#     behaviour camera is corrupted frame timing, not a cosmetic fault. The same omission
#     left `J6` (reward driver out) with a 74HCT32 gate wired straight onto a panel BNC
#     that runs to a solenoid driver, where an inductive kick or a mis-plug feeds directly
#     into the gate.
#
# Every OTHER panel connection on this board already has series resistance -- every input
# has it plus a BAT54S, and every Intan output has it. These six were the exception, which
# is why stating the rule over ALL panel outputs is the point rather than fixing six nets.
# ===========================================================================

# Series resistance expected between a logic driver and a panel BNC, in ohms.
#   Camera triggers: ~47 ohm, to bring source impedance near the 50 ohm coax. This ERRS
#   HIGH on purpose. An AHCT541's own output impedance is ~25-35 ohm, so the textbook
#   series-termination value would be nearer 20 ohm; 47 ohm over-damps, which costs a
#   little edge rate and buys margin against the failure that actually matters here
#   (a reflection re-crossing the camera's input threshold and triggering a second frame).
#   The first step at a high-impedance camera input is ~77% of the swing before settling,
#   still far above any TTL V_IH.
#   Reward out: 100 ohm, this board's own established panel-series value -- that pin's
#   problem is fault protection, not transmission-line matching.
PANEL_SERIES_MIN_OHM = 20.0
PANEL_SERIES_MAX_OHM = 220.0


# A dual BNC body's Value always begins with this marker -- written by
# hardware/gen/bnc_dual.py's own place_port(), which composes both ports' descriptions into
# one body-level value. See _panel_bnc_refs() for why the marker is what these checks key on.
BNC_BODY_VALUE_MARKER = "Dual panel BNC:"

# Centre-conductor pads of the dual BNC. Pads 2 and 4 are the two ports' own shells.
BNC_CENTRE_PADS = ("1", "3")


def _panel_bnc_refs(values) -> set[str]:
    """Every panel BNC BODY on the board.

    Keys on the `Dual panel BNC:` value marker rather than on `"(BNC)" in v`, which is what
    this did before finding F6 and which was wrong in both directions once the connectors
    were repackaged. It matched J28/J30/J32 -- the /1//2 shunt HEADERS, whose descriptions
    quote the BNC they belong to -- and it would have gone on matching a body whose value
    happened to mention a BNC for any other reason. Harmless here only because those headers
    never sit on a driver net.
    """
    return {
        r for r, v in values.items()
        if r.startswith("J") and v.startswith(BNC_BODY_VALUE_MARKER)
    }


def _panel_bnc_ports_on(nl, bncs) -> list[tuple[str, str]]:
    """The panel BNC PORTS present on one net, as (reference, centre pad).

    Ports, not bodies. Finding F6 put two ports in each connector, and every argument these
    checks make is about cable runs: two coax runs leaving one body are still two loads on
    the driver, and the F4 transmission-line arithmetic counts them as two. Counting distinct
    references instead -- which is what this did before F6 -- would let a driver feed both
    ports of one body and report a fan-out of 1.
    """
    return sorted({
        (ref, pin) for ref, pin, _pf, _t in nl
        if ref in bncs and pin in BNC_CENTRE_PADS
    })


def _check_panel_outputs_have_one_driver_and_series_r(nodes, values) -> None:
    two_pin = _two_terminal_passive_refs(nodes)
    bncs = _panel_bnc_refs(values)
    assert bncs, "no panel BNCs found at all -- this check would pass vacuously"

    fanout, unterminated = [], []
    for name, nl in nodes.items():
        bnc_here = _panel_bnc_ports_on(nl, bncs)
        if not bnc_here:
            continue
        drivers = sorted({
            (ref, pin) for ref, pin, _pf, pt in nl
            if values.get(ref) in MAX_SINK_MA
            and any(k in pt for k in ("output", "tri_state", "open_collector"))
            and "no_connect" not in pt
        })
        if not drivers:
            continue  # an input net, or driven from off-board
        # F4: one logic output must not drive several panel connectors.
        if len(bnc_here) > 1:
            fanout.append((name, drivers, bnc_here))
        # M3: a driver and a connector on the SAME net means no series resistance between
        # them. With a series resistor the two sit on different nets by construction.
        unterminated.append((name, drivers, bnc_here))

    assert not fanout, (
        f"{len(fanout)} logic output(s) drive more than one panel connector:\n  "
        + "\n  ".join(
            f"{net}: {[f'{r}.{p}' for r, p in d]} drives {len(b)} BNC ports "
            f"{[f'{r}.{p}' for r, p in b]}"
            for net, d, b in fanout
        )
        + "\nFinding F4. N parallel coax runs are Z0/N of transmission line; four is "
        "~12.5 ohm, and the initial edge into that draws ~118 mA against the AHCT541's "
        "25 mA per-output ABSOLUTE maximum -- on every edge, for the life of the board. "
        "Give each connector its own buffer channel."
    )
    assert not unterminated, (
        f"{len(unterminated)} panel connector(s) sit on the SAME net as their logic "
        f"driver, so there is no series resistance between them:\n  "
        + "\n  ".join(
            f"{net}: {[f'{r}.{p}' for r, p in d]} -> {[f'{r}.{p}' for r, p in b]} directly"
            for net, d, b in unterminated
        )
        + "\nFinding M3. Every other panel connection on this board carries series "
        "resistance -- every input has it plus a BAT54S clamp, every Intan output has it. "
        "Without it a trigger's reflections can double-trigger a camera, and a mis-plug or "
        "an inductive kick on an output feeds straight back into the driving gate."
    )


def test_panel_outputs_have_one_driver_and_series_r(nodes, values):
    _check_panel_outputs_have_one_driver_and_series_r(nodes, values)


def _check_panel_series_resistor_values(nodes, values) -> None:
    """The series resistors that check 26 requires must actually be resistors of a
    sensible value -- a 0 ohm link would satisfy the topology and terminate nothing."""
    two_pin = _two_terminal_passive_refs(nodes)
    bncs = _panel_bnc_refs(values)
    bad = []
    for name, nl in nodes.items():
        bnc_here = {ref for ref, _p, _pf, _t in nl if ref in bncs}
        if not bnc_here:
            continue
        for r_ref in {r for r, _p, _pf, _t in nl} & set(two_pin):
            if not r_ref.startswith("R"):
                continue
            other = next((n for n in two_pin[r_ref] if n != name), None)
            if other is None:
                continue
            driven = any(
                values.get(ref) in MAX_SINK_MA
                and any(k in pt for k in ("output", "tri_state", "open_collector"))
                for ref, _p, _pf, pt in nodes[other]
            )
            if not driven:
                continue
            ohms = _parse_ohms(values.get(r_ref, ""))
            if ohms is None or not (PANEL_SERIES_MIN_OHM <= ohms <= PANEL_SERIES_MAX_OHM):
                bad.append((name, r_ref, values.get(r_ref)))
    assert not bad, (
        "panel-output series resistor(s) outside the usable "
        f"{PANEL_SERIES_MIN_OHM:.0f}-{PANEL_SERIES_MAX_OHM:.0f} ohm window: "
        + "; ".join(f"{net}: {r}={v!r}" for net, r, v in bad)
        + "\nToo low terminates nothing (a 0 ohm link satisfies the topology and does no "
        "work); too high droops the edge into the receiver's own input current."
    )


def test_panel_series_resistor_values(nodes, values):
    _check_panel_series_resistor_values(nodes, values)


def test_panel_outputs_fires_on_shared_driver(nodes, values):
    """The F4 defect verbatim: the four behaviour-trigger BNCs put back onto the single
    buffer output that used to drive them all.

    Corrupts the net carrying the DRIVER (`CAM_TRIG_BEH1_BUF`), not the post-resistor net
    the connector now sits on -- a net with no driver on it is skipped by the check, so
    corrupting that one would have proved nothing."""
    corrupted = dict(nodes)
    bncs = _panel_bnc_refs(values)
    # Found through the four CAM_TRIG_BEH* nets rather than by matching "Behavior camera
    # trigger" in a Value. Finding F6 merged the four triggers onto three dual bodies and
    # composed both ports' text into one body-level Value, so the old value-matching version
    # returned 3 references for 4 triggers and this control stopped being able to build its
    # corruption at all. A port is (reference, centre pad); the nets know which is which.
    beh_ports = []
    for i in (1, 2, 3, 4):
        beh_ports += _panel_bnc_ports_on(nodes[f"CAM_TRIG_BEH{i}"], bncs)
    beh_ports = sorted(set(beh_ports))
    assert len(beh_ports) == 4, f"expected 4 behaviour-trigger BNC ports, found {beh_ports}"
    corrupted["CAM_TRIG_BEH1_BUF"] = (
        list(nodes["CAM_TRIG_BEH1_BUF"]) + [(r, p, "", "passive") for r, p in beh_ports]
    )
    with pytest.raises(AssertionError, match=r"more than one panel connector"):
        _check_panel_outputs_have_one_driver_and_series_r(corrupted, values)


def test_panel_outputs_fanout_counts_ports_not_bodies(nodes, values):
    """Both ports of ONE dual body on a single driver is still a fan-out of two.

    New with finding F6, and the reason `_panel_bnc_ports_on()` exists. Two coax runs
    leaving one connector load the driver exactly as two runs leaving two connectors do --
    the F4 transmission-line arithmetic does not care that they share a shell. A version of
    this check that counted references would report a fan-out of 1 here and stay silent.
    """
    corrupted = dict(nodes)
    bncs = _panel_bnc_refs(values)
    body = sorted(bncs)[0]
    corrupted["CAM_TRIG_BEH1_BUF"] = (
        list(nodes["CAM_TRIG_BEH1_BUF"])
        + [(body, pad, "", "passive") for pad in BNC_CENTRE_PADS]
    )
    with pytest.raises(AssertionError, match=r"more than one panel connector"):
        _check_panel_outputs_have_one_driver_and_series_r(corrupted, values)


def test_panel_outputs_fires_on_missing_series_resistance(nodes, values):
    """The M3 half, on its own: ONE connector moved onto its driver's net, so the fan-out
    assertion stays silent and only the missing-series-resistance one can fire."""
    corrupted = dict(nodes)
    # Found through RWD_DLVR_BNC, the net the reward-driver connector actually sits on.
    # This used to match the Value string "Reward driver out (BNC)" exactly; finding F6
    # composed that description into a two-port body value, so the exact match stopped
    # matching and `next()` raised StopIteration -- the lucky failure mode. The unlucky one
    # is a control that silently stops corrupting anything, which has happened five times on
    # this project.
    bncs = _panel_bnc_refs(values)
    ports = _panel_bnc_ports_on(nodes["RWD_DLVR_BNC"], bncs)
    assert len(ports) == 1, f"expected exactly 1 BNC port on RWD_DLVR_BNC, found {ports}"
    victim_ref, victim_pad = ports[0]
    corrupted["RWD_DLVR"] = list(nodes["RWD_DLVR"]) + [(victim_ref, victim_pad, "", "passive")]
    with pytest.raises(AssertionError, match=r"no series resistance between them"):
        _check_panel_outputs_have_one_driver_and_series_r(corrupted, values)
