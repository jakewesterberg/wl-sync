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


# ---------------------------------------------------------------------------
# Sourcing columns (added 2026-08-16)
#
# WHY THIS EXISTS, SEPARATELY FROM THE Reference/Value COMPARISON ABOVE. That comparison
# reads two columns and ignores the other three. `breakout-bom.csv` is documented as the
# literal, unedited `kicad-cli sch export bom` output -- and it is not: 18 rows carry
# hand-added MPN/Manufacturer/Qty that the raw export emits BLANK.
#
# So the file looks regenerable and is not. Re-running the documented command wipes all 18
# rows silently, and every check in this repo still passes: the References and Values are
# untouched, so the comparison above says nothing, and no ERC or netlist checker reads a BOM
# at all. That happened on 2026-08-16 while regenerating for the frame-time inputs, and was
# caught by eye during diff review rather than by anything automatic -- which is precisely
# the "discipline that works until the person doing it stops" this module's own docstring
# opens by naming. The cost of missing it is ordering 17 panel connectors with no part
# number.
#
# The BNC rule is DERIVED, not a pinned list of references, so a future dual-BNC body
# inherits the requirement instead of quietly becoming an 18th exception.
# ---------------------------------------------------------------------------
RAW_BOM = HW / "breakout-bom.csv"
ORDER_BOM = HW / "breakout-bom-order.csv"

BNC_DUAL_FOOTPRINT = "wl-sync:BNC_Dual_RA_Isolated"
BNC_DUAL_SOURCING = ("031-6575", "Amphenol RF", "1")

# The one row whose sourcing data no footprint rule implies -- the supply inlet header,
# which is a generic part deliberately not locked to an MPN. Pinned by reference because it
# genuinely is a one-off, not because the rule was too hard to write.
ONE_OFF_SOURCING = {
    "J1": ("GENERIC - not a locked MPN", "Sullins/TE/Molex/Amphenol/Wurth", "1"),
}

# Backstop: the total number of rows carrying ANY sourcing data. Catches a row losing its
# data even if some future rule change stops covering it. If you ADD sourcing data to a new
# row, bump this deliberately -- same pinned-baseline idiom as check_breakout_power_netlist
# .py's own RAIL_BYPASS_EXPECTED and this board's own ERC warning count.
SOURCED_ROW_COUNT = 18


def _sourcing(row: dict[str, str]) -> tuple[str, str, str]:
    return (row["MPN"].strip(), row["Manufacturer"].strip(), row["Qty"].strip())


@pytest.fixture(scope="module")
def raw_bom_rows() -> list[dict[str, str]]:
    with RAW_BOM.open() as fh:
        return list(csv.DictReader(fh))


def test_bnc_bodies_keep_their_sourcing_columns(raw_bom_rows):
    bnc_rows = [r for r in raw_bom_rows if r["Footprint"] == BNC_DUAL_FOOTPRINT]
    assert bnc_rows, (
        f"{RAW_BOM.name}: no rows with footprint {BNC_DUAL_FOOTPRINT!r} -- either the panel "
        f"connectors were removed from the board or this test's own rule has gone stale, "
        f"and either way it is no longer checking anything"
    )
    blanked = {r["Reference"]: _sourcing(r) for r in bnc_rows if _sourcing(r) != BNC_DUAL_SOURCING}
    assert not blanked, (
        f"{RAW_BOM.name}: {len(blanked)} dual-BNC row(s) do not carry the expected "
        f"MPN/Manufacturer/Qty {BNC_DUAL_SOURCING}: {blanked}. `kicad-cli sch export bom` "
        f"emits these three columns BLANK, so this is what a wholesale re-export looks "
        f"like. Restore them, or edit the Reference/Value cells in place instead of "
        f"re-exporting -- see hardware/README.md, 'BOM and procurement'."
    )


def test_one_off_sourcing_rows_survive(raw_bom_rows):
    by_ref = {r["Reference"]: r for r in raw_bom_rows}
    wrong = {
        ref: _sourcing(by_ref[ref])
        for ref, expected in ONE_OFF_SOURCING.items()
        if ref in by_ref and _sourcing(by_ref[ref]) != expected
    }
    missing = sorted(set(ONE_OFF_SOURCING) - set(by_ref))
    assert not missing, f"{RAW_BOM.name}: expected sourcing row(s) {missing} are absent entirely"
    assert not wrong, (
        f"{RAW_BOM.name}: {len(wrong)} hand-sourced row(s) lost or changed their "
        f"MPN/Manufacturer/Qty: {wrong} (expected {ONE_OFF_SOURCING}) -- the signature of a "
        f"wholesale re-export"
    )


def test_sourced_row_count_is_pinned(raw_bom_rows):
    sourced = [r["Reference"] for r in raw_bom_rows if any(_sourcing(r))]
    assert len(sourced) == SOURCED_ROW_COUNT, (
        f"{RAW_BOM.name}: {len(sourced)} row(s) carry sourcing data, expected "
        f"{SOURCED_ROW_COUNT}: {sorted(sourced)}. FEWER means a re-export or an edit blanked "
        f"them -- restore before committing. MORE means sourcing data was added, which is "
        f"fine: bump SOURCED_ROW_COUNT deliberately, in the same commit."
    )


def test_order_bom_rows_all_carry_an_order_code():
    """`breakout-bom-order.csv` is the file you actually purchase from, and unlike the raw
    BOM no part of it is machine-generated. A row with no Order Code is a part nobody can
    buy -- the same class of defect, one file over."""
    with ORDER_BOM.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows, f"{ORDER_BOM.name} is empty"
    blank = sorted(r["Reference"][:40] for r in rows if not r["Order Code"].strip())
    assert not blank, (
        f"{ORDER_BOM.name}: {len(blank)} row(s) have no Order Code: {blank} -- this is the "
        f"file procurement buys from, so a blank cell is an unorderable part"
    )


def test_sourcing_check_fires_on_a_wholesale_re_export(raw_bom_rows):
    """Negative control, and it reproduces the exact 2026-08-16 near-miss rather than an
    invented one: `kicad-cli sch export bom` blanks MPN/Manufacturer/Qty on every row.
    Built by corrupting the parsed form, not the committed file."""
    reexported = [{**r, "MPN": "", "Manufacturer": "", "Qty": ""} for r in raw_bom_rows]

    # Every check above must fire on it, or it is guarding nothing.
    bnc_blanked = [
        r for r in reexported
        if r["Footprint"] == BNC_DUAL_FOOTPRINT and _sourcing(r) != BNC_DUAL_SOURCING
    ]
    assert len(bnc_blanked) == 17, f"expected all 17 dual-BNC rows blanked, got {len(bnc_blanked)}"

    by_ref = {r["Reference"]: r for r in reexported}
    assert all(_sourcing(by_ref[ref]) != exp for ref, exp in ONE_OFF_SOURCING.items())
    assert len([r for r in reexported if any(_sourcing(r))]) == 0 != SOURCED_ROW_COUNT

    # And the Reference/Value comparison must NOT fire -- that is the whole problem: the
    # existing test stays green through a re-export, which is why these exist.
    assert [r["Reference"] for r in reexported] == [r["Reference"] for r in raw_bom_rows]
    assert [r["Value"] for r in reexported] == [r["Value"] for r in raw_bom_rows]
