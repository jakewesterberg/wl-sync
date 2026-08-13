"""Parse hardware/mule/mule.net and verify the contract nets are electrically correct.

Why this exists, not just ERC: this generator embeds `lib_symbols` entries keyed by full
lib id (e.g. "Device:R") -- required, because keying them bare makes KiCad silently fail
to resolve pins, collapsing them to the symbol origin and merging both pins of a two-pin
part into one net. That failure mode does NOT raise an ERC error; it produces a plausible,
wrong netlist. So ERC passing is necessary but not sufficient evidence this schematic is
correct -- this script is the check that actually reads the exported netlist and confirms
the nets it claims to have are the nets that are really there.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net \\
        hardware/mule/mule.kicad_sch

Usage:
    python3 hardware/gen/check_mule_netlist.py [path/to/mule.net]

Exits 0 and prints a summary if every check passes; exits 1 with a description of the
first failure otherwise. Intended to be inherited by later tasks (e.g. a Task 12 netlist
contract test) rather than re-derived -- see task-2-report.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_NET_PATH = Path(__file__).resolve().parent.parent / "mule" / "mule.net"

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


class CheckFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def verify(nets: dict[str, list[Node]]) -> list[str]:
    """Run the full contract check. Returns a list of human-readable summary lines on
    success; raises CheckFailure with a specific, localized message on the first
    violation found.
    """
    summary = []

    # --- Core contract (explicitly required by task-2 verification): all 17 EVT_*_PI
    # nets exist, each connecting exactly one buffer output pin to one Pi-header pin. ---
    pi_signal_names = [f"EVT_D{i}_PI" for i in range(16)] + ["EVT_STROBE_PI"]
    check(len(pi_signal_names) == 17, "internal error: expected 17 PI-side signal names")

    # EVT_STROBE_PI carries one deliberate extra connection beyond buffer+header: it
    # also feeds the isolated path's strobe optocoupler channel (see gen_mule.py, Step
    # 3) -- reusing the already-buffered signal rather than adding an undocumented
    # second buffer stage. That is a real 3rd node (a resistor pin) on a real net, not a
    # merge bug, so it needs to be an explicit, named exception rather than silently
    # loosening the check for all 17 nets.
    expected_extra_fanout = {"EVT_STROBE_PI": 1}  # net -> extra non-driver/non-header nodes

    header_refs_seen: set[str] = set()
    driver_ref_counts: dict[str, int] = {}
    for name in pi_signal_names:
        check(name in nets, f"missing net: {name!r} does not exist in the netlist at all")
        nodes = nets[name]
        drivers = [n for n in nodes if "tri_state" in n.pintype]
        # The header pin and buffer are identified by reference prefix, not electrical
        # type, because a resistor tap (also pintype "passive") can otherwise be
        # indistinguishable from the header pin (also "passive") by type alone.
        headers = [n for n in nodes if not n.ref.startswith(("U", "R"))]
        extras = [n for n in nodes if n.ref.startswith("R")]
        check(
            len(drivers) == 1,
            f"{name}: expected exactly 1 tri_state (buffer output) node, found "
            f"{len(drivers)}: {nodes}",
        )
        check(
            len(headers) == 1,
            f"{name}: expected exactly 1 header-pin node, found {len(headers)}: {nodes}",
        )
        expected_extra = expected_extra_fanout.get(name, 0)
        check(
            len(extras) == expected_extra,
            f"{name}: expected {expected_extra} extra (non-driver/non-header) node(s), "
            f"found {len(extras)}: {nodes} -- this is exactly the 'merged net' failure "
            f"mode constraint 1 warns about if it's an unexpected resistor/other part, "
            f"or a broken connection if a driver/header is missing",
        )
        check(
            len(nodes) == 2 + expected_extra,
            f"{name}: expected {2 + expected_extra} total nodes, found {len(nodes)}: {nodes}",
        )
        check(
            drivers[0].ref.startswith("U"),
            f"{name}: the tri_state driver {drivers[0]} is not on a 'U'-prefixed "
            f"(IC) reference -- unexpected part driving this net",
        )
        header_refs_seen.add(headers[0].ref)
        driver_ref_counts[drivers[0].ref] = driver_ref_counts.get(drivers[0].ref, 0) + 1

    check(
        len(header_refs_seen) == 1,
        f"all 17 EVT_*_PI nets should land on the SAME physical Pi-header connector, "
        f"but found {len(header_refs_seen)} different refs: {header_refs_seen}",
    )
    summary.append(
        f"All 17 EVT_*_PI nets: exactly 1 buffer output + 1 header pin each, all on "
        f"header {next(iter(header_refs_seen))}. Driver distribution: {driver_ref_counts} "
        f"(expect 3 distinct buffer refs: two carrying 8 lines each, one carrying 1 -- "
        f"see task-2-report.md, 'three packages not two')."
    )
    check(
        len(driver_ref_counts) == 3,
        f"expected exactly 3 distinct SN74LVC541APW buffer references driving the 17 "
        f"EVT_*_PI nets (two full 8-channel packages + one single-channel package for "
        f"strobe), found {len(driver_ref_counts)}: {driver_ref_counts}",
    )
    counts = sorted(driver_ref_counts.values())
    check(
        counts == [1, 8, 8],
        f"expected the 3 buffer references to carry [1, 8, 8] lines (one strobe-only "
        f"package, two full 8-line packages); found {counts}",
    )

    # --- TPC-side inbound nets: header pin -> 100R series resistor, one each. ---
    tpc_signal_names = [f"EVT_D{i}_TPC" for i in range(16)] + ["EVT_STROBE_TPC"]
    tpc_header_refs: set[str] = set()
    for name in tpc_signal_names:
        check(name in nets, f"missing net: {name!r}")
        nodes = nets[name]
        check(len(nodes) == 2, f"{name}: expected 2 nodes (header pin + resistor), found {nodes}")
        r_nodes = [n for n in nodes if n.ref.startswith("R")]
        other_nodes = [n for n in nodes if not n.ref.startswith("R")]
        check(len(r_nodes) == 1, f"{name}: expected exactly 1 resistor node, found {nodes}")
        check(len(other_nodes) == 1, f"{name}: expected exactly 1 non-resistor node, found {nodes}")
        tpc_header_refs.add(other_nodes[0].ref)
    check(
        len(tpc_header_refs) == 1,
        f"all 17 EVT_*_TPC nets should land on the same task-PC header connector, found "
        f"{tpc_header_refs}",
    )
    check(
        tpc_header_refs.isdisjoint(header_refs_seen),
        f"the TPC-side header {tpc_header_refs} and the PI-side header {header_refs_seen} "
        f"must be two DIFFERENT physical connectors, but they're the same -- inbound and "
        f"outbound sides of the level shift have collapsed onto one connector",
    )
    summary.append(
        f"All 17 EVT_*_TPC nets: header {next(iter(tpc_header_refs))} + series resistor "
        f"each, distinct from the PI-side header."
    )

    # --- Locked Pi-sourced / power net names exist at all (existence + non-triviality). ---
    for name in ("BARCODE_PI", "BARCODE_OUT", "+5V", "+3V3", "DGND", "ISO_5V", "ISO_GND"):
        check(name in nets, f"missing locked-contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    summary.append("BARCODE_PI, BARCODE_OUT, and all 5 power nets are present and populated.")

    # --- No two of the 17+17 locked event-code nets have accidentally merged into one
    # (the specific "plausible but wrong" failure this whole check exists to catch). ---
    all_locked = pi_signal_names + tpc_signal_names
    seen_node_sets = {}
    for name in all_locked:
        key = frozenset((n.ref, n.pin) for n in nets[name])
        check(
            key not in seen_node_sets,
            f"{name} and {seen_node_sets.get(key)} have IDENTICAL node sets -- they are "
            f"the same physical net under two different labels, i.e. merged",
        )
        seen_node_sets[key] = name
    summary.append("No two of the 34 EVT_* nets collapsed onto the same physical net.")

    return summary


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
