"""Parse hardware/mule/mule.net and verify the contract nets are electrically correct.

Why this exists, not just ERC: this generator embeds `lib_symbols` entries keyed by full
lib id (e.g. "Device:R") -- required, because keying them bare makes KiCad silently fail
to resolve pins, collapsing them to the symbol origin and merging both pins of a two-pin
part into one net. That failure mode does NOT raise an ERC error; it produces a plausible,
wrong netlist. So ERC passing is necessary but not sufficient evidence this schematic is
correct -- this script is the check that actually reads the exported netlist and confirms
the nets it claims to have are the nets that are really there.

FIX ROUND 3 (coordinator review, IMPORTANT 3): earlier versions of this checker verified
each net in isolation -- "does EVT_D5_CLAMP have exactly one diode node" -- without ever
tying channel 5's input path to channel 5's output path. That passes even if the 16 data
channels are permuted through the buffers (e.g. channel 3's buffer input wired to channel
5's buffer output), which is exactly the failure class a netlist contract check exists to
catch. `_walk_channel()` below walks each of the 17 lines end to end -- TPC header pin ->
series R -> clamp node -> buffer input pin -> the SAME buffer reference's output pin
(verified via the 74x541 family's fixed Ai<->Yi pin-pairing: input_pin + output_pin == 20
for every channel of every instance of this symbol) -> PI net -> PI header pin at the real
GPIO physical position -- asserting every hop belongs to the same physical parts, not just
that each net individually looks plausible.

Also asserts (fix round 3, IMPORTANT 4) that the isolation barrier is intact at pin level:
no (ref, pin) appears on both an isolated-domain net and a non-isolated net. Pin level, not
reference level, because U5/U6 (optocouplers) and U7 (isolated DC-DC) straddle the barrier
by design -- their primary-side pins are legitimately on DGND, their secondary-side pins on
ISO_GND, and a reference-level check would incorrectly flag every one of them.

Both of the above are self-tested (see `self_test()`) against synthetic corruptions of the
real, currently-passing netlist -- a negative control confirming the checks actually fire
rather than passing vacuously -- every time this script runs, not just when someone
remembers to.

Also asserts the Pi-side header's real GPIO physical-pin mapping (coordinator fix round 1,
finding 3) against the single source of truth in gen_mule.py (GPIO_PHYSICAL_PIN etc.),
imported directly rather than duplicated here, so a later edit to the mapping in one file
cannot silently drift out of sync with what the other expects.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net \\
        hardware/mule/mule.kicad_sch

Usage:
    python3 hardware/gen/check_mule_netlist.py [path/to/mule.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1
with a description of the first failure otherwise. Intended to be inherited by later
tasks (e.g. a Task 12 netlist contract test) rather than re-derived -- see
task-2-report.md.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_mule import (  # noqa: E402
    BARCODE_GPIO_PHYSICAL_PIN,
    GPIO_PHYSICAL_PIN,
    STROBE_GPIO_PHYSICAL_PIN,
)

DEFAULT_NET_PATH = Path(__file__).resolve().parent.parent / "mule" / "mule.net"

# GPIO0/GPIO1 get an extra 330R series resistor between buffer and header pin (Pi boot-time
# I2C HAT-ID probe contention) -- see gen_mule.py's buffer_output_net(). Every other channel
# connects its buffer output directly to the header pin.
INDIRECT_LINES = (0, 1)

ISO_NETS = [
    "ISO_5V", "ISO_GND", "EVT_STROBE_ISO", "BARCODE_ISO",
    "OPTO_SPARE1_ISO", "OPTO_SPARE2_ISO",
]
NON_ISO_NETS = ["+5V", "+3V3", "DGND"]

# Bypass/decoupling capacitors -- carried forward from Task 2's review: nothing previously
# caught a future accidental removal of a decoupling cap from gen_mule.py. This can only
# check TOPOLOGY (a capacitor really is wired straight across the rail pair, the two_pin()
# pattern every decoupling/bulk cap in this design uses) -- not physical placement or the
# <=7mm datasheet proximity the HCPL-4661s need, which is a PCB-layout property this
# generator's netlist has no coordinates to speak of (see hardware/mule/floorplan.md for
# the physical placement requirement instead). A capacitor's specific identity can't be
# pinned to "the one decoupling U1" this way either -- every cap tied straight across a
# given rail pair is topologically identical to every other one on that same pair -- so
# this asserts a per-rail-pair COUNT, which is exactly the granularity a deleted or
# accidentally-moved cap would change.
RAIL_BYPASS_EXPECTED = {
    # rail, ground -> expected capacitor count wired directly across the pair
    ("+3V3", "DGND"): 5,       # U1, U2, U3 (541s) + U8 (LDO output) local decouplers, + 1 bulk 10uF
    ("+5V", "DGND"): 3,        # U4 (541) local decoupler + U7 (DC-DC) input-side bypass, + 1 bulk 10uF
    ("ISO_5V", "ISO_GND"): 4,  # U5, U6 (optos) local bypass + U7 (DC-DC) output-side bypass, + 1 bulk 10uF
}


def _rail_bypass_cap_count(nets: dict[str, list[Node]], rail_net: str, gnd_net: str) -> int:
    """Count capacitor references with one pin on `rail_net` and the other on `gnd_net` --
    the two_pin() decoupling/bulk-cap pattern gen_mule.py uses throughout."""
    rail_caps = {n.ref for n in nets.get(rail_net, []) if n.ref.startswith("C")}
    gnd_caps = {n.ref for n in nets.get(gnd_net, []) if n.ref.startswith("C")}
    return len(rail_caps & gnd_caps)


_NET_RE = re.compile(r'\(net\s*\(code\s+"(\d+)"\)\s*\(name\s+"([^"]*)"\)')
_NODE_RE = re.compile(
    r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)'
    r'(?:\s+\(pinfunction\s+"([^"]*)"\))?\s+\(pintype\s+"([^"]+)"\)\s*\)'
)


class Node:
    __slots__ = ("ref", "pin", "pinfunction", "pintype")

    def __init__(self, ref, pin, pinfunction, pintype):
        self.ref = ref
        self.pin = pin
        self.pinfunction = pinfunction
        self.pintype = pintype

    def __repr__(self):
        return f"{self.ref}.{self.pin}({self.pintype})"

    def __eq__(self, other):
        return isinstance(other, Node) and (self.ref, self.pin) == (other.ref, other.pin)

    def __hash__(self):
        return hash((self.ref, self.pin))


def parse_netlist(text: str) -> dict[str, list[Node]]:
    """Return {net_name: [Node, ...]}. Raises if a net name is duplicated (that would
    itself indicate something went wrong -- kicad-cli emits one `(net ...)` block per
    distinct net name, ordinarily)."""
    nets_section_start = text.find("(nets")
    assert nets_section_start != -1, "no (nets ...) section in netlist"
    body = text[nets_section_start:]

    nets: dict[str, list[Node]] = {}
    matches = list(_NET_RE.finditer(body))
    for idx, m in enumerate(matches):
        name = m.group(2)
        block_start = m.end()
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        block = body[block_start:block_end]
        nodes = [Node(*g) for g in _NODE_RE.findall(block)]
        assert name not in nets, f"duplicate net name in netlist: {name!r}"
        nets[name] = nodes
    return nets


_COMP_RE = re.compile(r'\(comp\s+\(ref\s+"([^"]+)"\)', re.S)
_COMP_TSTAMP_RE = re.compile(r'\(tstamps\s+"([0-9a-fA-F-]{36})"\)')


def parse_component_uuids(text: str) -> dict[str, str]:
    """Return {reference: schematic symbol instance uuid}, from each component's own
    `(tstamps ...)` in the exported netlist.

    This is the schematic file's real, on-disk uuid for that symbol -- exported by
    `kicad-cli` out of the .kicad_sch itself -- as opposed to whatever uuid a fresh
    in-process `gen_mule.build()` happens to mint, which exists only in that process.
    A .kicad_pcb footprint's `(path "/UUID")` cross-link has to use the former or it points
    at nothing (see gen_mule_pcb.py, where it did).
    """
    out: dict[str, str] = {}
    matches = list(_COMP_RE.finditer(text))
    for idx, m in enumerate(matches):
        ref = m.group(1)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        # The component's own tstamps is the one 36-char uuid in its block; the
        # `(sheetpath ... (tstamps "/"))` above it is a sheet path, not a uuid, and does
        # not match.
        found = _COMP_TSTAMP_RE.findall(text[m.end():end])
        assert len(found) == 1, (
            f"{ref}: expected exactly one component (tstamps ...) uuid in its netlist "
            f"block, found {found}"
        )
        assert ref not in out, f"duplicate component reference in netlist: {ref!r}"
        out[ref] = found[0]
    return out


class CheckFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def _expected_gpio(i: int) -> int:
    return GPIO_PHYSICAL_PIN[i] if i < 16 else STROBE_GPIO_PHYSICAL_PIN


def _data_name(i: int) -> str:
    return f"EVT_D{i}" if i < 16 else "EVT_STROBE"


def _walk_channel(nets: dict[str, list[Node]], i: int) -> tuple[str, str, str]:
    """Walk one of the 17 event-code channels end to end, asserting every hop belongs to
    the SAME physical parts (not just that each net individually looks plausible).
    Returns (buffer_ref, tpc_header_ref, pi_header_ref) for the caller's aggregate checks.
    """
    data_name = _data_name(i)
    tpc_net, clamp_net, pi_net = f"{data_name}_TPC", f"{data_name}_CLAMP", f"{data_name}_PI"
    is_indirect = i in INDIRECT_LINES

    # Hop 1: TPC header pin (sequential position i+1) -> series resistor R_in.
    check(tpc_net in nets, f"missing net: {tpc_net!r}")
    tpc_nodes = nets[tpc_net]
    check(
        len(tpc_nodes) == 2,
        f"{tpc_net}: expected 2 nodes (header pin + series R), found {tpc_nodes}",
    )
    r_in_nodes = [n for n in tpc_nodes if n.ref.startswith("R")]
    tpc_hdr_nodes = [n for n in tpc_nodes if not n.ref.startswith("R")]
    check(len(r_in_nodes) == 1, f"{tpc_net}: expected exactly 1 resistor node, found {tpc_nodes}")
    check(len(tpc_hdr_nodes) == 1, f"{tpc_net}: expected exactly 1 header-pin node, found {tpc_nodes}")
    expected_tpc_pin = str(i + 1)
    check(
        tpc_hdr_nodes[0].pin == expected_tpc_pin,
        f"{tpc_net}: lands on header pin {tpc_hdr_nodes[0]}, expected sequential task-PC "
        f"pin {expected_tpc_pin}",
    )
    r_in_ref = r_in_nodes[0].ref

    # Hop 2: THE SAME resistor's other leg, on the clamp node, alongside the clamp diode
    # and the buffer's INPUT pin -- ties the series R physically to this specific channel.
    check(clamp_net in nets, f"missing net: {clamp_net!r}")
    clamp_nodes = nets[clamp_net]
    r_in_here = [n for n in clamp_nodes if n.ref == r_in_ref]
    check(
        len(r_in_here) == 1,
        f"{clamp_net}: series resistor {r_in_ref} (the one bridging from {tpc_net}) does "
        f"not reach here -- a different resistor was used, or this one bridges to a "
        f"DIFFERENT channel's clamp node (a permutation symptom)",
    )
    diode_nodes = [n for n in clamp_nodes if n.ref.startswith("D")]
    buf_in_nodes = [n for n in clamp_nodes if n.ref.startswith("U")]
    check(len(diode_nodes) == 1, f"{clamp_net}: expected exactly 1 diode node, found {clamp_nodes}")
    check(len(buf_in_nodes) == 1, f"{clamp_net}: expected exactly 1 buffer-input node, found {clamp_nodes}")
    check(
        len(clamp_nodes) == 3,
        f"{clamp_net}: expected exactly 3 nodes (series R, diode, buffer input), found "
        f"{len(clamp_nodes)}: {clamp_nodes}",
    )
    diode_ref = diode_nodes[0].ref
    buf_ref = buf_in_nodes[0].ref
    buf_a_pin = int(buf_in_nodes[0].pin)

    # Hop 2b: that SAME diode clamps to +5V (not +3V3, fix round 2's bug) and DGND.
    on_5v = [n for n in nets.get("+5V", []) if n.ref == diode_ref]
    on_3v3 = [n for n in nets.get("+3V3", []) if n.ref == diode_ref]
    on_dgnd = [n for n in nets.get("DGND", []) if n.ref == diode_ref]
    check(len(on_5v) == 1, f"{clamp_net}: clamp diode {diode_ref} has no pin on +5V")
    check(
        len(on_3v3) == 0,
        f"{clamp_net}: clamp diode {diode_ref} has a pin on +3V3 -- regressed to the "
        f"fix-round-2 bug (see task-2-report.md, 'Fix round 2')",
    )
    check(len(on_dgnd) == 1, f"{clamp_net}: clamp diode {diode_ref} has no pin on DGND")

    # Hop 3: the SAME buffer reference's OUTPUT pin -- not just "a" tri_state driver
    # somewhere, but the one channel-paired with buf_a_pin via the 74x541 family's fixed
    # Ai<->Yi numbering (gen_mule.py's CHAN_A/CHAN_Y: Ai=2+i, Yi=18-i, so
    # input_pin + output_pin == 20 for every channel of every instance of this symbol).
    # A permutation that wires one channel's input to a DIFFERENT channel's output either
    # lands on a different buffer reference (caught by the ref match below) or the same
    # reference at the wrong pin (caught by the pin-sum invariant).
    buf_net = f"{data_name}_BUF" if is_indirect else pi_net
    check(buf_net in nets, f"missing net: {buf_net!r}")
    buf_side_nodes = nets[buf_net]
    buf_out_nodes = [n for n in buf_side_nodes if n.ref == buf_ref and "tri_state" in n.pintype]
    check(
        len(buf_out_nodes) == 1,
        f"{buf_net}: expected exactly 1 tri_state output pin belonging to buffer {buf_ref} "
        f"(the same reference whose input pin is on {clamp_net}), found {len(buf_out_nodes)} "
        f"-- if a different buffer drives this net, channel data has crossed ICs",
    )
    buf_y_pin = int(buf_out_nodes[0].pin)
    check(
        buf_a_pin + buf_y_pin == 20,
        f"channel {i}: buffer {buf_ref}'s input pin {buf_a_pin} (on {clamp_net}) and "
        f"output pin {buf_y_pin} (on {buf_net}) don't satisfy the 74x541 family's fixed "
        f"Ai<->Yi pairing (input_pin + output_pin must equal 20) -- this channel's input "
        f"and output are not the same physical buffer channel: the data has been permuted",
    )

    # Hop 4: reach the PI header at the real GPIO physical position -- directly for 15 of
    # 17 lines, or via the SAME GPIO0/1 series resistor bridging buf_net to pi_net.
    if is_indirect:
        check(
            len(buf_side_nodes) == 2,
            f"{buf_net}: expected 2 nodes (buffer output + series R), found {buf_side_nodes}",
        )
        r_out_nodes = [n for n in buf_side_nodes if n.ref.startswith("R")]
        check(len(r_out_nodes) == 1, f"{buf_net}: expected exactly 1 resistor node, found {buf_side_nodes}")
        r_out_ref = r_out_nodes[0].ref
        check(pi_net in nets, f"missing net: {pi_net!r}")
        pi_nodes = nets[pi_net]
        r_out_here = [n for n in pi_nodes if n.ref == r_out_ref]
        check(
            len(r_out_here) == 1,
            f"{pi_net}: the GPIO{i} series resistor {r_out_ref} (bridging from {buf_net}) "
            f"does not reach here -- a different resistor, or a broken bridge",
        )
        check(len(pi_nodes) == 2, f"{pi_net}: expected 2 nodes (resistor + header), found {pi_nodes}")
        pi_hdr_nodes = [n for n in pi_nodes if not n.ref.startswith("R")]
    else:
        pi_nodes = buf_side_nodes  # buf_net == pi_net for every direct line
        # EVT_STROBE_PI carries one deliberate extra connection beyond driver + header:
        # it also feeds the isolated path's strobe optocoupler channel. Every other
        # direct line has exactly the driver + header, 2 nodes.
        expected_extra = 1 if data_name == "EVT_STROBE" else 0
        extra_r = [n for n in pi_nodes if n.ref.startswith("R")]
        check(
            len(extra_r) == expected_extra,
            f"{pi_net}: expected {expected_extra} extra resistor node(s) (opto tap, "
            f"strobe only), found {len(extra_r)}: {pi_nodes}",
        )
        check(
            len(pi_nodes) == 2 + expected_extra,
            f"{pi_net}: expected {2 + expected_extra} total nodes, found {len(pi_nodes)}: {pi_nodes}",
        )
        pi_hdr_nodes = [n for n in pi_nodes if not n.ref.startswith(("U", "R"))]

    check(
        len(pi_hdr_nodes) == 1,
        f"{pi_net}: expected exactly 1 header-pin node, found {len(pi_hdr_nodes)}: {pi_nodes}",
    )
    expected_pi_pin = str(_expected_gpio(i))
    check(
        pi_hdr_nodes[0].pin == expected_pi_pin,
        f"{pi_net}: lands on header pin {pi_hdr_nodes[0]}, but the real Raspberry Pi "
        f"GPIO{i if i < 16 else 16} physical position is pin {expected_pi_pin} -- the "
        f"bring-up PIO capture test (check 7) cannot run against this",
    )

    return buf_ref, tpc_hdr_nodes[0].ref, pi_hdr_nodes[0].ref


def verify(nets: dict[str, list[Node]]) -> list[str]:
    """Run the full contract check. Returns a list of human-readable summary lines on
    success; raises CheckFailure with a specific, localized message on the first
    violation found.
    """
    summary = []

    # --- Walk all 17 event-code channels end to end (fix round 3, IMPORTANT 3). ---
    driver_ref_counts: dict[str, int] = {}
    tpc_header_refs: set[str] = set()
    pi_header_refs: set[str] = set()
    for i in range(17):
        buf_ref, tpc_hdr_ref, pi_hdr_ref = _walk_channel(nets, i)
        driver_ref_counts[buf_ref] = driver_ref_counts.get(buf_ref, 0) + 1
        tpc_header_refs.add(tpc_hdr_ref)
        pi_header_refs.add(pi_hdr_ref)

    check(
        len(pi_header_refs) == 1,
        f"all 17 EVT_*_PI nets should land on the SAME physical Pi-header connector, "
        f"found {len(pi_header_refs)}: {pi_header_refs}",
    )
    check(
        len(tpc_header_refs) == 1,
        f"all 17 EVT_*_TPC nets should land on the SAME physical task-PC header connector, "
        f"found {len(tpc_header_refs)}: {tpc_header_refs}",
    )
    check(
        tpc_header_refs.isdisjoint(pi_header_refs),
        f"the TPC-side header {tpc_header_refs} and the PI-side header {pi_header_refs} "
        f"must be two DIFFERENT physical connectors, but they're the same",
    )
    check(
        len(driver_ref_counts) == 3,
        f"expected exactly 3 distinct SN74LVC541APW buffer references driving the 17 "
        f"channels (two full 8-channel packages + one single-channel package for strobe), "
        f"found {len(driver_ref_counts)}: {driver_ref_counts}",
    )
    counts = sorted(driver_ref_counts.values())
    check(
        counts == [1, 8, 8],
        f"expected the 3 buffer references to carry [1, 8, 8] channels (one strobe-only "
        f"package, two full 8-channel packages); found {counts}",
    )
    summary.append(
        f"All 17 event-code channels walked end to end (TPC header -> series R -> clamp "
        f"node -> buffer input -> SAME buffer's output, verified via the 74x541 "
        f"Ai<->Yi=20 pairing -> PI net -> PI header at its real GPIO physical pin), each "
        f"hop tied to the same physical parts. TPC header {next(iter(tpc_header_refs))}, "
        f"PI header {next(iter(pi_header_refs))}. Driver distribution: {driver_ref_counts}."
    )

    # --- BARCODE_PI: also a real physical GPIO position (GPIO17), named explicitly in
    # the brief's own Step 2 text, not just an arbitrary header pin. ---
    check("BARCODE_PI" in nets, "missing locked-contract net: 'BARCODE_PI'")
    barcode_headers = [n for n in nets["BARCODE_PI"] if not n.ref.startswith("U")]
    check(
        len(barcode_headers) == 1,
        f"BARCODE_PI: expected exactly 1 header-pin node, found {nets['BARCODE_PI']}",
    )
    check(
        barcode_headers[0].pin == str(BARCODE_GPIO_PHYSICAL_PIN),
        f"BARCODE_PI: lands on header pin {barcode_headers[0]}, expected physical pin "
        f"{BARCODE_GPIO_PHYSICAL_PIN} (the real Pi GPIO17 position)",
    )
    summary.append(
        f"BARCODE_PI lands on {barcode_headers[0].ref}.{barcode_headers[0].pin}, the real "
        f"Pi GPIO17 physical position."
    )

    # --- Isolation barrier: no (ref, pin) may appear on both an isolated-domain net and a
    # non-isolated net (fix round 3, IMPORTANT 4). Pin level, not reference level -- U5,
    # U6 (optocouplers) and U7 (isolated DC-DC) straddle the barrier BY DESIGN (LED/
    # primary-side pins legitimately on DGND, output/secondary-side pins legitimately on
    # ISO_GND); a reference-level check would incorrectly flag every one of them. ---
    for name in ISO_NETS + NON_ISO_NETS:
        check(name in nets, f"missing net: {name!r}")
    iso_pins = {(n.ref, n.pin) for name in ISO_NETS for n in nets[name]}
    non_iso_pins = {(n.ref, n.pin) for name in NON_ISO_NETS for n in nets[name]}
    overlap = iso_pins & non_iso_pins
    check(
        not overlap,
        f"isolation barrier violated: pin(s) {overlap} appear on both an isolated-domain "
        f"net ({ISO_NETS}) and a non-isolated net ({NON_ISO_NETS}) -- a single PIN cannot "
        f"legitimately be on both sides of the barrier (unlike a REFERENCE, which can "
        f"straddle it by design)",
    )
    summary.append(
        f"Isolation barrier intact: zero pins shared between {ISO_NETS} and "
        f"{NON_ISO_NETS} (checked at pin level, not reference level)."
    )

    # --- Bypass/decoupling caps: every rail pair carries exactly the expected number of
    # capacitors wired straight across it (fix round 3 required these caps to exist for
    # U5/U6/U7; this is the regression guard a later review flagged as still missing -- a
    # future edit that silently drops one now fails loudly here instead of only showing up
    # as a bench measurement anomaly on hardware that's already been fabbed). ---
    for (rail, gnd), expected in RAIL_BYPASS_EXPECTED.items():
        found = _rail_bypass_cap_count(nets, rail, gnd)
        check(
            found == expected,
            f"{rail}/{gnd}: expected {expected} bypass/bulk capacitor(s) wired directly "
            f"across this rail pair, found {found} -- a decoupling or bulk cap was added, "
            f"removed, or moved off this rail pair",
        )
    summary.append(
        "Bypass/decoupling capacitor counts intact on all three rail pairs: "
        + ", ".join(f"{r}/{g}={n}" for (r, g), n in RAIL_BYPASS_EXPECTED.items())
    )

    # --- Locked Pi-sourced / power net names exist at all (existence + non-triviality). ---
    for name in ("BARCODE_PI", "BARCODE_OUT", "+5V", "+3V3", "DGND", "ISO_5V", "ISO_GND"):
        check(name in nets, f"missing locked-contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    summary.append("BARCODE_PI, BARCODE_OUT, and all 5 power nets are present and populated.")

    # --- No two of the locked event-code nets have accidentally merged into one (the
    # specific "plausible but wrong" failure this whole check exists to catch). ---
    all_locked = (
        [f"EVT_D{i}_PI" for i in range(16)]
        + ["EVT_STROBE_PI"]
        + [f"EVT_D{i}_TPC" for i in range(16)]
        + ["EVT_STROBE_TPC"]
        + [f"EVT_D{i}_BUF" for i in INDIRECT_LINES]
    )
    seen_node_sets = {}
    for name in all_locked:
        key = frozenset((n.ref, n.pin) for n in nets[name])
        check(
            key not in seen_node_sets,
            f"{name} and {seen_node_sets.get(key)} have IDENTICAL node sets -- they are "
            f"the same physical net under two different labels, i.e. merged",
        )
        seen_node_sets[key] = name
    summary.append(f"No two of the {len(all_locked)} EVT_* nets collapsed onto the same physical net.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire,
# rather than passing vacuously. Runs against synthetic corruptions of the
# real, currently-passing netlist every time this script runs.
# ---------------------------------------------------------------------------


def _assert_fails(nets: dict[str, list[Node]], expect_substring: str, label: str) -> str:
    try:
        verify(nets)
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


def self_test(good_nets: dict[str, list[Node]]) -> list[str]:
    """Negative controls for fix round 3's two new checks. `good_nets` must already pass
    `verify()` cleanly -- each corruption below is a minimal, targeted mutation of that
    known-good structure, not a hand-built fixture, so the test is exercising the real
    parsed shape of the real schematic.
    """
    results = []

    # IMPORTANT 3 negative control: swap two channels' buffer OUTPUT pins (a permutation)
    # and confirm the end-to-end walk catches it. Channels 3 and 5 are both "direct"
    # lines (not GPIO0/1's indirect resistor-bridged pair), so EVT_D3_PI and EVT_D5_PI
    # each hold exactly one tri_state driver node -- swap those two Node objects between
    # the two nets.
    swapped = copy.deepcopy(good_nets)
    d3_driver_idx = next(i for i, n in enumerate(swapped["EVT_D3_PI"]) if "tri_state" in n.pintype)
    d5_driver_idx = next(i for i, n in enumerate(swapped["EVT_D5_PI"]) if "tri_state" in n.pintype)
    swapped["EVT_D3_PI"][d3_driver_idx], swapped["EVT_D5_PI"][d5_driver_idx] = (
        swapped["EVT_D5_PI"][d5_driver_idx],
        swapped["EVT_D3_PI"][d3_driver_idx],
    )
    msg = _assert_fails(swapped, "permuted", "channel 3/5 buffer-output swap")
    results.append(f"Channel permutation (swapped EVT_D3_PI/EVT_D5_PI buffer drivers): caught -- {msg}")

    # IMPORTANT 4 negative control: short ISO_GND to DGND (a single shared pin) and
    # confirm the isolation-barrier check catches it.
    shorted = copy.deepcopy(good_nets)
    phantom = Node(ref="DBG99", pin="1", pinfunction="", pintype="passive")
    shorted["ISO_GND"] = shorted["ISO_GND"] + [phantom]
    shorted["DGND"] = shorted["DGND"] + [phantom]
    msg = _assert_fails(shorted, "isolation barrier violated", "ISO_GND/DGND short")
    results.append(f"Isolation barrier short (ISO_GND tied to DGND via one shared pin): caught -- {msg}")

    # Bypass-cap negative control: drop one capacitor's two nodes from ISO_5V/ISO_GND
    # (simulating an accidental deletion of a decoupling cap in a future edit) and confirm
    # the new rail-bypass-count check catches it.
    dropped = copy.deepcopy(good_nets)
    iso_5v_caps = {n.ref for n in dropped["ISO_5V"] if n.ref.startswith("C")}
    iso_gnd_caps = {n.ref for n in dropped["ISO_GND"] if n.ref.startswith("C")}
    victim = sorted(iso_5v_caps & iso_gnd_caps)[0]
    dropped["ISO_5V"] = [n for n in dropped["ISO_5V"] if n.ref != victim]
    dropped["ISO_GND"] = [n for n in dropped["ISO_GND"] if n.ref != victim]
    msg = _assert_fails(dropped, "bypass/bulk capacitor", f"{victim} dropped from ISO_5V/ISO_GND")
    results.append(f"Bypass cap removal ({victim} dropped from ISO_5V/ISO_GND): caught -- {msg}")

    return results


def main() -> int:
    net_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NET_PATH
    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print(
            "  kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net "
            "hardware/mule/mule.kicad_sch"
        )
        return 1
    nets = parse_netlist(net_path.read_text())
    print(f"parsed {len(nets)} nets from {net_path}")
    try:
        summary = verify(nets)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS:")
    for line in summary:
        print(f"  - {line}")

    try:
        self_test_results = self_test(nets)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
