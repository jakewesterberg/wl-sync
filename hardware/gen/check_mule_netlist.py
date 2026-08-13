"""Parse hardware/mule/mule.net and verify the contract nets are electrically correct.

Why this exists, not just ERC: this generator embeds `lib_symbols` entries keyed by full
lib id (e.g. "Device:R") -- required, because keying them bare makes KiCad silently fail
to resolve pins, collapsing them to the symbol origin and merging both pins of a two-pin
part into one net. That failure mode does NOT raise an ERC error; it produces a plausible,
wrong netlist. So ERC passing is necessary but not sufficient evidence this schematic is
correct -- this script is the check that actually reads the exported netlist and confirms
the nets it claims to have are the nets that are really there.

Also asserts the Pi-side header's real GPIO physical-pin mapping (coordinator fix round 1,
finding 3) against the single source of truth in gen_mule.py (GPIO_PHYSICAL_PIN etc.),
imported directly rather than duplicated here, so a later edit to the mapping in one file
cannot silently drift out of sync with what the other expects.

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


def _expected_gpio(i: int) -> int:
    return GPIO_PHYSICAL_PIN[i] if i < 16 else STROBE_GPIO_PHYSICAL_PIN


def verify(nets: dict[str, list[Node]]) -> list[str]:
    """Run the full contract check. Returns a list of human-readable summary lines on
    success; raises CheckFailure with a specific, localized message on the first
    violation found.
    """
    summary = []

    # --- Core contract: all 17 EVT_*_PI nets exist, each connecting to the Pi header at
    # its REAL physical GPIO pin position, ultimately driven by exactly one buffer
    # output (directly for 15 of 17 lines; through a 330R series resistor for GPIO0/1). ---
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

    for i, name in enumerate(pi_signal_names):
        check(name in nets, f"missing net: {name!r} does not exist in the netlist at all")
        nodes = nets[name]
        # The header pin is identified by reference prefix, not electrical type, because
        # a resistor tap (also pintype "passive") can otherwise be indistinguishable from
        # the header pin (also "passive") by type alone.
        headers = [n for n in nodes if not n.ref.startswith(("U", "R"))]
        check(
            len(headers) == 1,
            f"{name}: expected exactly 1 header-pin node, found {len(headers)}: {nodes}",
        )
        header_refs_seen.add(headers[0].ref)

        expected_pin = str(_expected_gpio(i))
        check(
            headers[0].pin == expected_pin,
            f"{name}: lands on header pin {headers[0].ref}.{headers[0].pin}, but the real "
            f"Raspberry Pi GPIO{i if i < 16 else 16} physical position is pin "
            f"{expected_pin} -- the Pi-side header no longer matches the real Pi 5 GPIO "
            f"map, and the bring-up PIO capture test (check 7) cannot run against this",
        )

        if i in INDIRECT_LINES:
            # GPIO0/GPIO1: a 330R resistor sits between the buffer output and this header
            # pin (boot-time I2C HAT-ID probe contention). The only non-header node on
            # EVT_Dx_PI itself must be that resistor's Pi-side leg; the actual tri_state
            # driver lives one hop upstream, on EVT_Dx_BUF, bridged by the SAME resistor.
            check(
                len(nodes) == 2,
                f"{name}: GPIO{i} is an indirect (resistor-bridged) line, expected exactly "
                f"2 nodes (resistor + header), found {len(nodes)}: {nodes}",
            )
            resistors = [n for n in nodes if n.ref.startswith("R")]
            check(
                len(resistors) == 1,
                f"{name}: expected exactly 1 series-resistor node, found {nodes}",
            )
            buf_name = f"EVT_D{i}_BUF"
            check(
                buf_name in nets,
                f"missing net: {buf_name!r} -- the buffer-side leg of GPIO{i}'s series "
                f"resistor should land here, not directly on {name}",
            )
            buf_nodes = nets[buf_name]
            check(
                len(buf_nodes) == 2,
                f"{buf_name}: expected exactly 2 nodes (buffer output + resistor), found "
                f"{len(buf_nodes)}: {buf_nodes}",
            )
            buf_drivers = [n for n in buf_nodes if "tri_state" in n.pintype]
            buf_resistors = [n for n in buf_nodes if n.ref.startswith("R")]
            check(
                len(buf_drivers) == 1,
                f"{buf_name}: expected exactly 1 tri_state (buffer output) node, found "
                f"{buf_nodes}",
            )
            check(
                len(buf_resistors) == 1,
                f"{buf_name}: expected exactly 1 resistor node, found {buf_nodes}",
            )
            check(
                buf_resistors[0].ref == resistors[0].ref,
                f"{name}/{buf_name}: resistor pins are on two DIFFERENT references "
                f"({resistors[0].ref} vs {buf_resistors[0].ref}) -- should be the same "
                f"330R part bridging buffer output to header pin, not two unrelated parts",
            )
            check(
                buf_drivers[0].ref.startswith("U"),
                f"{buf_name}: driver {buf_drivers[0]} is not on a 'U'-prefixed (IC) "
                f"reference -- unexpected part driving this net",
            )
            driver_ref_counts[buf_drivers[0].ref] = (
                driver_ref_counts.get(buf_drivers[0].ref, 0) + 1
            )
        else:
            drivers = [n for n in nodes if "tri_state" in n.pintype]
            extras = [n for n in nodes if n.ref.startswith("R")]
            expected_extra = expected_extra_fanout.get(name, 0)
            check(
                len(drivers) == 1,
                f"{name}: expected exactly 1 tri_state (buffer output) node, found "
                f"{len(drivers)}: {nodes}",
            )
            check(
                len(extras) == expected_extra,
                f"{name}: expected {expected_extra} extra (non-driver/non-header) node(s), "
                f"found {len(extras)}: {nodes} -- this is exactly the 'merged net' failure "
                f"mode constraint 1 warns about if it's an unexpected resistor/other part, "
                f"or a broken connection if a driver/header is missing",
            )
            check(
                len(nodes) == 2 + expected_extra,
                f"{name}: expected {2 + expected_extra} total nodes, found {len(nodes)}: "
                f"{nodes}",
            )
            check(
                drivers[0].ref.startswith("U"),
                f"{name}: the tri_state driver {drivers[0]} is not on a 'U'-prefixed "
                f"(IC) reference -- unexpected part driving this net",
            )
            driver_ref_counts[drivers[0].ref] = driver_ref_counts.get(drivers[0].ref, 0) + 1

    check(
        len(header_refs_seen) == 1,
        f"all 17 EVT_*_PI nets should land on the SAME physical Pi-header connector, "
        f"but found {len(header_refs_seen)} different refs: {header_refs_seen}",
    )
    summary.append(
        f"All 17 EVT_*_PI nets land on header {next(iter(header_refs_seen))} at their real "
        f"Pi 5 GPIO physical pin positions (GPIO0-15 -> data, GPIO16 -> strobe), each "
        f"ultimately driven by exactly one buffer output. GPIO0/GPIO1 verified indirect "
        f"via their dedicated 330R series resistor each. Driver distribution: "
        f"{driver_ref_counts} (expect 3 distinct buffer refs: two carrying 8 lines each, "
        f"one carrying 1 -- see task-2-report.md, 'three packages not two')."
    )
    check(
        len(driver_ref_counts) == 3,
        f"expected exactly 3 distinct SN74LVC541APW buffer references driving the 17 "
        f"EVT_*_PI lines (two full 8-channel packages + one single-channel package for "
        f"strobe), found {len(driver_ref_counts)}: {driver_ref_counts}",
    )
    counts = sorted(driver_ref_counts.values())
    check(
        counts == [1, 8, 8],
        f"expected the 3 buffer references to carry [1, 8, 8] lines (one strobe-only "
        f"package, two full 8-line packages); found {counts}",
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

    # --- Bidirectional clamp: every CLAMP net's BAT54S diode must reference +5V, not
    # +3V3, on its high side (coordinator fix round 2). A clamp referenced to +3V3 sinks
    # continuous current into that rail on every normal 5V logic-high, defeating the
    # reason SN74LVC541A was chosen at all -- see task-2-report.md, "Fix round 2". Checked
    # per-diode against both +5V and +3V3 (not just "does +5V mention it somewhere") so a
    # future edit that reintroduces a +3V3 connection on any one of the 17 diodes is
    # caught even if the other 16 are still correct. ---
    clamp_signal_names = [f"EVT_D{i}_CLAMP" for i in range(16)] + ["EVT_STROBE_CLAMP"]
    clamp_diode_refs: set[str] = set()
    for name in clamp_signal_names:
        check(name in nets, f"missing net: {name!r}")
        diode_nodes = [n for n in nets[name] if n.ref.startswith("D")]
        check(
            len(diode_nodes) == 1,
            f"{name}: expected exactly 1 diode (BAT54S COM pin) node, found {nets[name]}",
        )
        clamp_diode_refs.add(diode_nodes[0].ref)
    check(
        len(clamp_diode_refs) == 17,
        f"expected 17 distinct clamp-diode references (one BAT54S per line), found "
        f"{len(clamp_diode_refs)}: {clamp_diode_refs}",
    )
    for ref in clamp_diode_refs:
        on_5v = [n for n in nets.get("+5V", []) if n.ref == ref]
        on_3v3 = [n for n in nets.get("+3V3", []) if n.ref == ref]
        on_dgnd = [n for n in nets.get("DGND", []) if n.ref == ref]
        check(
            len(on_5v) == 1,
            f"clamp diode {ref}: expected exactly 1 pin on +5V (the high-side clamp "
            f"target), found {len(on_5v)}",
        )
        check(
            len(on_3v3) == 0,
            f"clamp diode {ref}: has a pin on +3V3 -- the high-side clamp target has "
            f"regressed back to +3V3, which sinks continuous current into that rail on "
            f"every normal 5V logic-high (see task-2-report.md, 'Fix round 2')",
        )
        check(
            len(on_dgnd) == 1,
            f"clamp diode {ref}: expected exactly 1 pin on DGND (the low-side clamp "
            f"target), found {len(on_dgnd)}",
        )
    summary.append(
        f"All 17 clamp diodes (BAT54S) reference +5V/DGND on their outer pins, with zero "
        f"pins on +3V3 -- checked per-diode, not just net-level."
    )

    # --- TPC-side inbound nets: header pin -> 100R series resistor, one each. Stays
    # sequential (coordinator fix round 1: accepted as-is, not a real Pi pinout). ---
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
        f"each, distinct from the PI-side header (sequential pin assignment, unchanged)."
    )

    # --- Locked Pi-sourced / power net names exist at all (existence + non-triviality). ---
    for name in ("BARCODE_PI", "BARCODE_OUT", "+5V", "+3V3", "DGND", "ISO_5V", "ISO_GND"):
        check(name in nets, f"missing locked-contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    summary.append("BARCODE_PI, BARCODE_OUT, and all 5 power nets are present and populated.")

    # --- No two of the locked event-code nets (including the two new GPIO0/1 buffer-side
    # nets) have accidentally merged into one (the specific "plausible but wrong" failure
    # this whole check exists to catch). ---
    all_locked = (
        pi_signal_names
        + tpc_signal_names
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
