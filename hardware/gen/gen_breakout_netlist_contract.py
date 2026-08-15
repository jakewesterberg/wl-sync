"""Generator for `hardware/breakout/netlist-contract.json` -- Task 12 fix round 1
(.superpowers/sdd/2026-08-13-breakout-pcb/task-12-report.md's own "Concerns" section named
this gap; this file is the fix).

THE PROBLEM THIS FIXES: `tests/hardware/test_netlist.py`'s 26 assertions (this board's
hard-won invariants -- no +5V reaching a 3.3V GPIO, comparator/I2C pull-ups on +3V3, AISENSE
tied to AGND, isolated domains pin-disjoint, Intan capped at exactly 8 analog outputs, and
seven more) used to read `hardware/breakout/breakout.net` directly. That file is
`*.net`-gitignored BY DESIGN (mechanical, fully re-derivable from the checked-in schematic --
see "Byte-reproducibility" below), so it does not exist on a fresh checkout, and this
project's CI has no `kicad-cli` to produce it. The test file's own `pytestmark` therefore
SKIPPED all 26 assertions on every CI run -- not failed, skipped, which reports green and
reads as coverage while testing nothing. That is worse than no test at all: a skip that
looks like a pass trains reviewers to stop checking, exactly backwards from what a contract
guarding "this board's invariants were each violated for real, at least once, during this
build" is supposed to do.

THE FIX: commit a canonical, UUID-free, deterministically-formatted JSON rendering of the
exported netlist -- `hardware/breakout/netlist-contract.json`, built by this script --
instead of the raw `.net`. `tests/hardware/test_netlist.py` now loads THIS file (committed,
always present, zero KiCad dependency) and its 26 assertions run unconditionally, in every
environment, CI included. `tests/hardware/test_netlist_contract_freshness.py` is the
separate, locally-gated check that this snapshot still matches what `kicad-cli` produces
from the CURRENTLY checked-in schematic right now -- it may skip when `kicad-cli` is
unavailable (verifying the snapshot is fresh is not the same claim as verifying the board,
and only the second one has to hold everywhere, always). See that file's own module
docstring, and hardware/README.md's "Whole-board netlist contract" section, for the full
split.

WHY NOT COMMIT THE RAW `.net`: it embeds a fresh `uuid.uuid4()` on every symbol, footprint,
and hierarchical-sheet instance on every single regeneration (`hardware/gen/kicad_sch.py`'s
own `uid()`; see "Byte-reproducibility" below) -- committing it would put ~2200 lines of
UUID churn in front of every reviewer on every schematic edit, burying the handful of lines
that actually changed (a net gaining or losing a connection) in noise indistinguishable from
"nothing electrically changed, just regenerated." This file strips every UUID: it keeps only
each net's sorted `(reference, pin, pinfunction, pintype)` tuples and each component's
`Value` string -- exactly the fields `tests/hardware/test_netlist.py`'s 12 checks (26 with
negative controls) read, nothing that changes without an electrical or component-value
change also changing.

FORMAT: valid, standard JSON (`json.loads()` reads it with zero custom parsing) -- but
hand-serialized rather than `json.dump(..., indent=2)`, which would explode each 4-element
node array across 4 lines and scatter one connection's ref/pin/pinfunction/pintype across
lines a reviewer has to scroll between (confirmed by trying it first). `render_contract()`
below keeps every node on exactly one line instead. Net names, component references, and
each net's own node list are all naturally sorted (`R2` before `R10`, not after) so the
diff for a single added/removed connection is exactly one line, and the file's own byte
layout is fully deterministic given the same input netlist -- no dependency on `kicad-cli`'s
own internal iteration order.

Usage:
    python3 hardware/gen/gen_breakout_netlist_contract.py [net_path] [out_path]

    net_path defaults to hardware/breakout/breakout.net (regenerate first with the
    `kicad-cli sch export netlist` command hardware/README.md's own "Regenerating fab
    outputs" documents); out_path defaults to hardware/breakout/netlist-contract.json.

Run this LAST in the regeneration sequence, after every `check_breakout_*_netlist.py`
checker has passed against the same netlist -- this script canonicalizes whatever netlist
it is given with no electrical judgment of its own; it should only ever canonicalize a
netlist already independently confirmed correct, never launder a broken one into looking
like ground truth.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Netlist parsing -- the same whitespace-tolerant technique
# `hardware/gen/check_mule_netlist.py`'s own `parse_netlist()`/`parse_component_values()`
# and (until this fix) `tests/hardware/test_netlist.py`'s own module-level parsing used:
# kicad-cli 10.0.5's real `sch export netlist` output pretty-prints one field per line
# (`(net\n\t\t\t(code "1")\n\t\t\t(name "+3V3")...`), not the single-space-joined form a
# naive regex would assume -- `\s*`/`\s+` throughout is what actually matches it.
# ---------------------------------------------------------------------------

_NET_BLOCK_RE = re.compile(r'\(net\s*\(code\s+"(\d+)"\)\s*\(name\s+"([^"]*)"\)')
_NODE_RE = re.compile(
    r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)'
    r'(?:\s+\(pinfunction\s+"([^"]*)"\))?\s+\(pintype\s+"([^"]+)"\)\s*\)'
)
_COMP_REF_VALUE_RE = re.compile(r'\(ref\s+"([^"]+)"\)\s*\(value\s+"([^"]*)"\)')

Node = tuple[str, str, str, str]  # (ref, pin, pinfunction, pintype)


def parse_net_nodes(text: str) -> dict[str, list[Node]]:
    """{net_name: [(ref, pin, pinfunction, pintype), ...]} straight from the exported
    netlist's own `(nets ...)` section -- see module docstring for the regex rationale."""
    start = text.find("(nets")
    assert start != -1, "no (nets ...) section in the exported netlist"
    body = text[start:]
    matches = list(_NET_BLOCK_RE.finditer(body))
    out: dict[str, list[Node]] = {}
    for idx, m in enumerate(matches):
        name = m.group(2)
        block_start = m.end()
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        block = body[block_start:block_end]
        out[name] = [(g[0], g[1], g[2], g[3]) for g in _NODE_RE.findall(block)]
    return out


def parse_component_values(text: str) -> dict[str, str]:
    """{reference: Value property text} -- same technique
    check_mule_netlist.py's own parse_component_values() uses."""
    return {m.group(1): m.group(2) for m in _COMP_REF_VALUE_RE.finditer(text)}


# ---------------------------------------------------------------------------
# Canonical, sorted, UUID-free contract structure + deterministic serialization.
# ---------------------------------------------------------------------------


def _natural_key(s: str) -> list[tuple[int, object]]:
    """Sort key so 'R2' sorts before 'R10' (plain string sort would put 'R10' first) --
    purely a diff-readability nicety, not a correctness requirement. Each chunk is tagged
    (0, str) or (1, int) rather than left as a bare mixed-type value so two keys can never
    attempt an int-vs-str comparison, regardless of what a future net or reference name
    looks like (defensive: every real net/ref in this project starts with a letter or
    '+'/'-', so today chunk 0 is always a str, but this makes that an observation, not a
    silent assumption the sort would crash on on if it stopped being true)."""
    return [(1, int(chunk)) if chunk.isdigit() else (0, chunk) for chunk in re.findall(r"\d+|\D+", s)]


def build_contract(net_path: Path) -> dict:
    text = net_path.read_text()
    return {
        "generated_by": "hardware/gen/gen_breakout_netlist_contract.py",
        "source_schematic": "hardware/breakout/breakout.kicad_sch",
        "component_values": parse_component_values(text),
        "nets": parse_net_nodes(text),
    }


def render_contract(contract: dict) -> str:
    """Serialize `contract` to deterministic, diff-friendly JSON text -- see module
    docstring's "FORMAT" section for why this is hand-rolled rather than
    `json.dumps(..., indent=2)`. Still 100% standard JSON: `json.loads()` reads the result
    back with no custom parsing at all; only the WRITER is hand-rolled."""
    lines = ["{"]
    lines.append(f'  "generated_by": {json.dumps(contract["generated_by"])},')
    lines.append(f'  "source_schematic": {json.dumps(contract["source_schematic"])},')

    lines.append('  "component_values": {')
    comp_items = sorted(contract["component_values"].items(), key=lambda kv: _natural_key(kv[0]))
    for i, (ref, value) in enumerate(comp_items):
        comma = "," if i < len(comp_items) - 1 else ""
        lines.append(f"    {json.dumps(ref)}: {json.dumps(value)}{comma}")
    lines.append("  },")

    lines.append('  "nets": {')
    net_items = sorted(contract["nets"].items(), key=lambda kv: _natural_key(kv[0]))
    for ni, (net_name, nodes) in enumerate(net_items):
        net_comma = "," if ni < len(net_items) - 1 else ""
        sorted_nodes = sorted(nodes, key=lambda n: (_natural_key(n[0]), _natural_key(n[1])))
        if not sorted_nodes:
            lines.append(f"    {json.dumps(net_name)}: []{net_comma}")
            continue
        lines.append(f"    {json.dumps(net_name)}: [")
        for nj, node in enumerate(sorted_nodes):
            node_comma = "," if nj < len(sorted_nodes) - 1 else ""
            lines.append(f"      {json.dumps(list(node))}{node_comma}")
        lines.append(f"    ]{net_comma}")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo_root = Path(__file__).resolve().parents[2]
    net_path = Path(argv[0]) if len(argv) > 0 else repo_root / "hardware" / "breakout" / "breakout.net"
    out_path = Path(argv[1]) if len(argv) > 1 else repo_root / "hardware" / "breakout" / "netlist-contract.json"

    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print(
            f"  kicad-cli sch export netlist --format kicadsexpr -o {net_path} "
            f"hardware/breakout/breakout.kicad_sch"
        )
        return 1

    contract = build_contract(net_path)
    rendered = render_contract(contract)

    # Self-check: what we are about to write must parse back to exactly the data we built
    # it from -- a cheap round-trip guard against a serializer bug silently committing a
    # snapshot that quietly drops or corrupts an entry (json.loads is the same parser
    # every reader of this file, including the pytest contract, will use).
    reparsed = json.loads(rendered)
    assert reparsed["component_values"] == contract["component_values"], (
        "serializer bug: component_values did not round-trip through render_contract()"
    )
    # Compare as sorted lists, not raw order: render_contract() deliberately reorders each
    # net's nodes (natural sort, for diff readability) without mutating `contract` itself,
    # so a plain == would flag that harmless reordering as if it were data loss. Sorting
    # both sides here still catches the bug this guards against -- a node silently dropped,
    # duplicated, or altered by the serializer -- while not caring about order.
    reparsed_nets = {name: sorted(tuple(n) for n in nodes) for name, nodes in reparsed["nets"].items()}
    original_nets = {name: sorted(nodes) for name, nodes in contract["nets"].items()}
    assert reparsed_nets == original_nets, "serializer bug: nets did not round-trip through render_contract()"

    out_path.write_text(rendered)
    n_nodes = sum(len(v) for v in contract["nets"].values())
    print(
        f"wrote {out_path} ({len(rendered)} bytes): {len(contract['nets'])} nets, "
        f"{n_nodes} nodes, {len(contract['component_values'])} component values"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
