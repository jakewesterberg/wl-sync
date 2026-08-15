"""Shared structural guard: no same-column pair of real, placed 2-pin parts on a rendered
.kicad_sch may have overlapping pin-to-pin reach intervals -- the GENERAL form of the
row-pitch-vs-2-pin-part-span defect class first found and fixed on opto-ni.kicad_sch
(gen_breakout_opto_ni.py's own CH_ROW_DY account: a Device:R's own 7.62mm pin-to-pin span
landing exactly on a foreign row because it was an integer multiple of the ACSL package's
2.54mm row pitch, silently merging NI_5V into a channel's own data net -- caught by ERC only
by luck of the specific values involved, not by anything structural). Originally added as
`_check_row_pitch_exceeds_2pin_span()` (built on `_two_pin_reach_intervals()`) directly
inside check_breakout_opto_ni_netlist.py and check_breakout_opto_intan_netlist.py at Task 11
fix round 1 -- see .superpowers/sdd/2026-08-13-breakout-pcb/task-11-report.md, "Fix round 1".

FACTORED OUT HERE at Task 11 fix round 2, not duplicated a fifth and sixth time, for a
reason specific to THIS check and not a reversal of this project's own general "duplicate a
checker's own helpers rather than import them" discipline (see below): unlike every
sheet-specific check in this project's own checkers -- which genuinely NEEDS that sheet's
own channel/contract data (net names, pin tables, direction splits) and so is correctly
kept independent, one copy per checker, so a change to one sheet's own contract can never
silently alter what another sheet's checker verifies -- this ONE check has ZERO sheet-
specific parameters. It operates purely on a sheet's raw rendered .kicad_sch text plus
kicad_sch.py's own already-shared extract_symbol()/unit_pins()/pin_pos() primitives, with no
per-sheet data of any kind threaded through it. Task 11 fix round 2 found this exact guard
missing from three checkers it had never been back-ported to
(check_breakout_analog_ni_netlist.py, check_breakout_comparators_netlist.py,
check_breakout_mux_intan_netlist.py) -- not because those sheets are structurally immune
(mux-intan.kicad_sch places the identical Device:R/Device:C row-pitch hazard analog-ni and
comparators share, plus its own 8 BNCs), but purely because hand-copying ~130 lines a third,
fourth, and fifth time was never done. That is exactly the failure mode a shared import
forecloses structurally rather than relying on someone remembering to keep copying it onto
every future checker too -- "this has now recurred three times on this project in different
forms" (task-11-report.md's own fix-round-2 dispatch). See that report's own "Fix round 2"
section for the full account of why this one function graduated from "duplicated, per this
project's own established per-checker independence discipline" (still the right call for
every sheet-specific verify()/self_test() in every checker that imports this module) to
"shared" -- a recognition that this particular check was never actually sheet-specific to
begin with, not a change of policy for the checks that are.

Every calling checker still defines its own DEFAULT_*_SCH path, still runs this guard
against its OWN sheet's real rendered text, and still gets its own genuinely-firing
self-test -- nothing about per-checker independence for WHAT gets checked changes; only
WHERE the shared, parameter-free mechanics of HOW live.

`two_pin_reach_intervals()` treats a 2-pin part whose own two pins sit at DIFFERENT real X
(found empirically on opto-intan.kicad_sch's own BNC jacks, Connector:Conn_Coaxial's
center/shield pins, and present again on mux-intan.kicad_sch's own 8 analog-output BNCs) as
a genuinely different geometry -- individually positioned per instance, not stacked with
siblings in a shared column -- and EXCLUDES it, not force-fits it (fix round 1's own finding,
inherited here unchanged so every caller, including the three newly back-ported ones, gets
the identical correct treatment for its own irregular parts automatically, rather than each
having to remember to reapply the exclusion by hand).
"""
from __future__ import annotations

import re

from check_mule_netlist import CheckFailure, check
from kicad_sch import extract_symbol, pin_pos, unit_pins

PLACED_INSTANCE_RE = re.compile(
    r'\(symbol\s*\n\s*\(lib_id "([^"]+)"\)\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)\s*\n\s*\(unit (\d+)\)'
    r'.*?\(property "Reference" "([^"]+)"',
    re.DOTALL,
)


def two_pin_reach_intervals(sch_text: str) -> dict[float, list[tuple[str, float, float]]]:
    """For every PLACED symbol instance in `sch_text` that resolves -- via its own real
    library definition (extract_symbol()/unit_pins(), the SAME source KiCad itself reads
    pin geometry from, not any assumption this checker invents) -- to exactly 2 pins,
    compute the REAL rendered Y each of its own two pins actually lands on, and group the
    resulting [lo_y, hi_y] "reach interval" by the REAL X column both of that part's own
    pins share.

    This project places every instance at rotation 0 (kicad_sch.py's own Sch class
    docstring). Every 2-pin part this check can usefully reason about is a VERTICAL one
    (both pins at the same real X, one above the other) -- true for every discrete 2-pin
    device placed in a shared, repeated per-row column (Device:R/Device:C/
    Device:FerriteBead, confirmed directly: local (0, +3.81)/(0, -3.81)), which is the
    actual SHAPE this project's own row-pitch defect class takes (CH_ROW_DY's own
    account). A 2-pin part whose own two pins sit at DIFFERENT real X -- found empirically
    on opto-intan.kicad_sch's own BNC jacks and mux-intan.kicad_sch's own BNC jacks,
    `Connector:Conn_Coaxial`'s center/shield pins -- is a genuinely different geometry
    (individually positioned per instance, not stacked with siblings in a shared column)
    and is excluded, not force-fit: it does not participate in the "many same-shaped parts
    stacked in one column" hazard this function models, so grouping it by either pin's own
    X would misrepresent it, not protect it.

    Returns {column_x: [(ref, lo_y, hi_y), ...]} -- the raw material
    `check_row_pitch_exceeds_2pin_span()` uses to find any two SAME-COLUMN parts whose own
    real pin-reach intervals overlap, independent of whether that overlap happens to land
    two DIFFERENT net names on the identical coordinate in THIS specific design (a
    caller's own `_check_no_coordinate_collisions()` may separately catch that symptom) --
    this is the general, structural version, catching the defect class even when the
    CURRENT channel count/offset happens not to trip an exact collision.
    """
    by_column: dict[float, list[tuple[str, float, float]]] = {}
    for m in PLACED_INSTANCE_RE.finditer(sch_text):
        lib_id, ax, ay, _rot, unit, ref = m.groups()
        ax, ay = float(ax), float(ay)
        libname, _sep, symname = lib_id.partition(":")
        try:
            block = extract_symbol(libname, symname)
            pins = unit_pins(block, symname, int(unit))
        except (AssertionError, FileNotFoundError):
            continue  # not a resolvable part this check can reason about -- skip, don't fail
        if len(pins) != 2:
            continue  # only a genuine 2-pin (2-terminal) part poses this specific hazard
        real = [pin_pos(ax, ay, p) for p in pins.values()]
        xs = sorted(round(x, 3) for x, _y in real)
        if xs[0] != xs[1]:
            continue  # non-vertical 2-pin part (e.g. Conn_Coaxial's center+shield) --
            # not subject to the shared-column row-pitch hazard this function models
        ys = sorted(y for _x, y in real)
        by_column.setdefault(xs[0], []).append((ref, ys[0], ys[1]))
    return by_column


def check_row_pitch_exceeds_2pin_span(sch_text: str, sheet_label: str) -> str:
    """THE GENERAL guard: every same-column pair of real, placed 2-pin parts' own
    pin-to-pin reach intervals must be strictly disjoint. A caller's own
    `_check_no_coordinate_collisions()` catches an ACTUAL resulting short; this instead
    checks the structural property that prevents one from ever being possible in the first
    place -- so it would catch this defect class even in a hypothetical layout that
    doesn't (yet) happen to land an exact duplicate coordinate (e.g. one channel short of
    tripping it), not only the one that already does. Directly generalizes CH_ROW_DY's own
    invariant (gen_breakout_opto_ni.py: "> Device:R's own 7.62mm pin-to-pin span") into an
    executable check instead of a comment every future generator has to remember to
    imitate.

    `sheet_label` (e.g. "opto-ni.kicad_sch") is used only for diagnostic text -- every
    caller passes its own sheet's name so a failure message still reads as sheet-specific
    even though the mechanics are shared.
    """
    by_column = two_pin_reach_intervals(sch_text)
    checked_columns = 0
    checked_parts = 0
    violations = []
    for col, members in by_column.items():
        if len(members) < 2:
            continue
        checked_columns += 1
        checked_parts += len(members)
        for i in range(len(members)):
            ref_i, lo_i, hi_i = members[i]
            for j in range(i + 1, len(members)):
                ref_j, lo_j, hi_j = members[j]
                if lo_i <= hi_j and lo_j <= hi_i:  # intervals overlap (or touch)
                    violations.append((col, ref_i, (lo_i, hi_i), ref_j, (lo_j, hi_j)))
    check(
        checked_columns > 0,
        f"no column hosted 2+ real 2-pin parts -- suspiciously unable to exercise this "
        f"check at all; is this really {sheet_label}'s own rendered text, with its own "
        f"real R/C placements?",
    )
    check(
        not violations,
        f"{len(violations)} same-column 2-pin-part pair(s) have OVERLAPPING real pin-"
        f"reach intervals in {sheet_label} -- the general form of the NI_5V/channel-"
        f"data-net short this project already hit once (CH_ROW_DY's own account in "
        f"gen_breakout_opto_ni.py), independent of whether it currently lands an exact "
        f"duplicate coordinate: {violations[:5]}",
    )
    return (
        f"Row-pitch-vs-2-pin-part-span guard: {checked_parts} real 2-pin parts across "
        f"{checked_columns} shared columns in {sheet_label}, every same-column pair's "
        f"own pin-reach interval strictly disjoint from every other."
    )


def self_test_row_pitch(good_sch_text: str, sheet_label: str, min_instances: int = 10) -> str:
    """Negative control for check_row_pitch_exceeds_2pin_span(): forces one real 2-pin
    part's own placement Y onto a DIFFERENT, same-column part's own placement Y (the same
    mechanism CH_ROW_DY's own comment in gen_breakout_opto_ni.py describes -- a resistor's
    row picked to coincide with a foreign row) and confirms the general guard fires, even
    though this splice alone does not necessarily also create a same-coordinate GLOBAL
    LABEL collision (it only forces the two components' own reach intervals to coincide
    exactly) -- proving this is a genuinely different, structural check, not a restatement
    of a caller's own `_check_no_coordinate_collisions()`.

    `min_instances` is a self-test-setup-integrity floor ("did this really find enough
    placed instances to corrupt"), not an electrical assertion -- each caller passes a
    value appropriate to its OWN sheet's real component count (same reasoning every
    checker's own verify_instance_paths() already uses for its own per-sheet floor).
    """
    matches = list(PLACED_INSTANCE_RE.finditer(good_sch_text))
    check(len(matches) > min_instances, "self-test setup failed: too few placed instances found to corrupt")
    by_column = two_pin_reach_intervals(good_sch_text)
    col, members = next(((c, ms) for c, ms in by_column.items() if len(ms) >= 2), (None, None))
    check(col is not None, "self-test setup failed: no column hosts 2+ real 2-pin parts")
    ref_a, ref_b = members[0][0], members[1][0]
    m_a = next(m for m in matches if m.group(6) == ref_a)
    m_b = next(m for m in matches if m.group(6) == ref_b)
    corrupted = good_sch_text[: m_b.start(3)] + m_a.group(3) + good_sch_text[m_b.end(3):]
    check(corrupted != good_sch_text, "self-test setup failed: splice produced no change")
    try:
        check_row_pitch_exceeds_2pin_span(corrupted, sheet_label)
    except CheckFailure as e:
        check("OVERLAPPING real pin-reach intervals" in str(e), f"self-test 'row-pitch collision reintroduced': wrong failure message: {e}")
        return str(e)
    raise CheckFailure("self-test 'row-pitch collision reintroduced' did NOT raise -- check_row_pitch_exceeds_2pin_span() is passing vacuously")
