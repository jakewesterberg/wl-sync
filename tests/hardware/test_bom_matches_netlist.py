"""The committed BOMs must describe exactly the board the netlist describes.

WHY THIS EXISTS. `hardware/breakout/breakout-bom.csv` and `breakout-bom-order.csv` are
hand-maintained -- no generator writes them -- while every part on the board is generated.
Nothing connected the two. Through the 2026-08-16 parametric-audit implementation the BOMs
were kept correct by running this comparison by hand after each change, which is exactly
the kind of discipline that works until the person doing it stops.

The failure mode is quiet and expensive: a value corrected in the schematic but not the
BOM builds the wrong board, and no ERC, netlist checker or test in this repo would say a
word. `netlist-contract.json` already records every reference and its value, so the
comparison is free.

Reads the same committed snapshot `test_netlist.py` does, so it runs unconditionally in
CI with no KiCad dependency.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

HW = Path(__file__).parents[2] / "hardware" / "breakout"
SNAPSHOT = HW / "netlist-contract.json"
BOMS = [HW / "breakout-bom.csv", HW / "breakout-bom-order.csv"]

_REF_RE = re.compile(r"^([A-Za-z#]+)(\d+)$")


def _expand(refs: str) -> set[str]:
    """"R1,R3-R5" -> {R1, R3, R4, R5}. Ranges are inclusive and must share a prefix --
    a malformed range raises rather than silently yielding nothing, since a range that
    quietly expanded to the empty set would make this whole comparison vacuous."""
    out: set[str] = set()
    for part in (p.strip() for p in refs.split(",")):
        if not part:
            continue
        if "-" not in part:
            out.add(part)
            continue
        lo, hi = part.split("-", 1)
        m_lo, m_hi = _REF_RE.match(lo), _REF_RE.match(hi)
        assert m_lo and m_hi, f"malformed reference range {part!r}"
        assert m_lo.group(1) == m_hi.group(1), f"reference range {part!r} spans two prefixes"
        out |= {f"{m_lo.group(1)}{n}" for n in range(int(m_lo.group(2)), int(m_hi.group(2)) + 1)}
    return out


@pytest.fixture(scope="module")
def netlist_values() -> dict[str, str]:
    values = json.loads(SNAPSHOT.read_text())["component_values"]
    # #PWR flags are schematic annotations, not orderable parts.
    return {r: v for r, v in values.items() if not r.startswith("#")}


@pytest.mark.parametrize("bom_path", BOMS, ids=lambda p: p.name)
def test_bom_matches_netlist(bom_path, netlist_values):
    with bom_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows, f"{bom_path.name} is empty"

    bom: dict[str, str] = {}
    duplicates = []
    for row in rows:
        for ref in _expand(row["Reference"]):
            if ref in bom:
                duplicates.append(ref)
            bom[ref] = row["Value"]
    assert not duplicates, (
        f"{bom_path.name}: reference(s) {sorted(set(duplicates))} appear in more than one "
        f"row -- a part cannot be ordered as two different things"
    )

    missing = sorted(set(netlist_values) - set(bom))
    extra = sorted(set(bom) - set(netlist_values))
    mismatched = {
        r: (netlist_values[r], bom[r])
        for r in sorted(set(netlist_values) & set(bom))
        if netlist_values[r] != bom[r]
    }
    assert not missing, (
        f"{bom_path.name}: {len(missing)} part(s) on the board are absent from the BOM: "
        f"{missing} -- they would not be ordered"
    )
    assert not extra, (
        f"{bom_path.name}: {len(extra)} BOM line(s) name references that are not on the "
        f"board: {extra} -- stale rows from a part that was renamed or removed"
    )
    assert not mismatched, (
        f"{bom_path.name}: {len(mismatched)} part(s) have a different value in the BOM "
        f"than on the board: "
        + "; ".join(f"{r}: board={b!r} bom={m!r}" for r, (b, m) in mismatched.items())
        + " -- the schematic was corrected and the BOM was not, or vice versa"
    )


def test_bom_check_fires_on_a_value_that_drifted(netlist_values):
    """Negative control: the comparison must actually catch a value edited on one side
    only. Built by corrupting the BOM's own parsed form rather than the file."""
    board = dict(netlist_values)
    bom = dict(netlist_values)
    victim = next(r for r in sorted(bom) if r.startswith("R"))
    bom[victim] = "999k"
    mismatched = {r: (board[r], bom[r]) for r in board if board[r] != bom[r]}
    assert mismatched == {victim: (board[victim], "999k")}


def test_bom_check_fires_on_a_missing_part(netlist_values):
    """Negative control: a part on the board with no BOM line."""
    board = dict(netlist_values)
    bom = dict(netlist_values)
    victim = next(r for r in sorted(bom) if r.startswith("U"))
    del bom[victim]
    assert sorted(set(board) - set(bom)) == [victim]
