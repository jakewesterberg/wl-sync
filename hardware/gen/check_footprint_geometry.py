"""Parametric footprint-geometry checks -- audit step 5's own row in
`hardware/breakout/parametric-audit.md` §5 ("Footprint axis orientation against the face
each connector is assigned to", F6), plus the MDR68 and dual-BNC rebuilds.

WHY THIS FILE EXISTS AND WHY IT IS DIFFERENT FROM EVERY OTHER CHECKER HERE.

Every other checker in this directory asks a question the netlist can answer. This one asks
questions the netlist **structurally cannot**: a footprint with the wrong drill, the wrong
pitch, or an axis pointing at the chassis lid produces a perfectly clean netlist, passes
ERC, and passes all 120 tests. That is not hypothetical on this project -- `build_minidin()`
once shipped an even contact ring where the manufacturer's diagrams show a clustered layout,
"a real topology defect that would very likely fail to mate as fabbed"
(`gen_wl_sync_footprints.py`'s own docstring). It was caught in review, by a person, because
no tool in this toolchain read footprint geometry at all.

`check_breakout_analog_frontend_netlist.py`'s own `verify_footprint_pad_adjacency()` is the
precedent -- it reads the real `.kicad_mod` from disk rather than trusting the generator that
wrote it. This file generalises that discipline to the connectors.

WHAT IT READS. The real `.kicad_mod` files on disk (via `kicad_pcb.extract_footprint()`, the
same reader KiCad's own placement path uses) and the limits in
`hardware/datasheet-params.toml` -- never a number re-typed here. A datasheet revision is a
one-line change there that propagates into these assertions, exactly as every parametric
check on this board already works.

It also reads the netlist, but only to answer "which footprint is each connector actually
assigned?" -- the geometry claims themselves are made against the library masters.

THE F6 CHECK, AND WHY IT IS EXPRESSED AS A DRILL LIMIT. A footprint cannot encode an axis
directly. What it *can* encode -- and what `BNC_PanelMountable_Vertical` encoded -- is a
9.65 mm barrel pass-through hole, which exists only because the connector's axis is
perpendicular to the board and the barrel goes *through* it. A right-angle part has contact-
sized drills and nothing else. So "no panel connector may have a signal pad drilled larger
than a contact" is the executable form of "no panel connector's axis points at the lid", and
it is the form that would have caught F6 on the day it was introduced.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_footprint_geometry.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1 with
a description of the first failure otherwise.
"""
from __future__ import annotations

import math
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mule_netlist import CheckFailure, check, parse_netlist  # noqa: E402
from kicad_pcb import (  # noqa: E402
    LOCAL_FPROOT,
    _child_tag,
    _find_balanced,
    _split_children,
    extract_footprint,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
PARAMS_PATH = Path(__file__).resolve().parent.parent / "datasheet-params.toml"

# A panel connector's contacts are contact-sized. Anything materially larger is a barrel
# pass-through, i.e. an axis perpendicular to the board -- finding F6's own signature. The
# largest legitimate NUMBERED contact drill on this board is the dual BNC's 2.01 mm ground
# terminal; 3.0 mm leaves headroom for a larger contact without admitting a 9.65 mm barrel.
MAX_PANEL_CONTACT_DRILL_MM = 3.0

# Footprints that must NOT exist on disk any more, with the reason, so a re-add is loud.
RETIRED_FOOTPRINTS = {
    "M12A_5_Panel": (
        "ruling R1: the Phoenix 1551833 is a PANEL-mount part with flying leads, not a "
        "board-mount one. The board carries a 5-way header instead and this custom "
        "footprint was deleted -- see hardware/breakout/parametric-audit.md's R1 entry."
    ),
}


# ---------------------------------------------------------------------------
# Pad geometry reader -- richer than kicad_pcb.footprint_pads(), which returns
# positions only. Drill and size are exactly what this file exists to assert on.
# ---------------------------------------------------------------------------
_PAD_HEAD_RE = re.compile(r'^\(pad\s+"([^"]*)"\s+(\S+)\s+(\S+)')
_AT_RE = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)")
_SIZE_RE = re.compile(r"\(size\s+(-?[\d.]+)\s+(-?[\d.]+)\)")
_DRILL_RE = re.compile(r"\(drill\s+(?:oval\s+(-?[\d.]+)\s+(-?[\d.]+)|(-?[\d.]+))")


class Pad:
    __slots__ = ("number", "kind", "shape", "x", "y", "w", "h", "drill")

    def __init__(self, number, kind, shape, x, y, w, h, drill):
        self.number, self.kind, self.shape = number, kind, shape
        self.x, self.y, self.w, self.h, self.drill = x, y, w, h, drill

    @property
    def numbered(self) -> bool:
        return self.number != ""

    def __repr__(self) -> str:
        return f"Pad({self.number!r},{self.kind},at=({self.x},{self.y}),drill={self.drill})"


def footprint_pad_geometry(libname: str, modname: str) -> list[Pad]:
    """Every pad in a library master, with its drill and size -- the properties this file
    asserts on and `kicad_pcb.footprint_pads()` deliberately does not carry."""
    pads: list[Pad] = []
    for child in _split_children(extract_footprint(libname, modname)):
        if _child_tag(child) != "pad":
            continue
        head = _PAD_HEAD_RE.match(child)
        check(head is not None, f"{libname}:{modname}: malformed pad block: {child[:60]!r}")
        at = _AT_RE.search(child)
        size = _SIZE_RE.search(child)
        drill = _DRILL_RE.search(child)
        check(at is not None and size is not None, f"{libname}:{modname}: pad missing at/size")
        d = None
        if drill:
            d = float(drill.group(3)) if drill.group(3) else float(drill.group(1))
        pads.append(
            Pad(
                head.group(1), head.group(2), head.group(3),
                float(at.group(1)), float(at.group(2)),
                float(size.group(1)), float(size.group(2)), d,
            )
        )
    return pads


def footprint_descr(libname: str, modname: str) -> str:
    m = re.search(r'\(descr\s+"((?:[^"\\]|\\.)*)"', extract_footprint(libname, modname))
    return m.group(1) if m else ""


def footprint_layer_extent(libname: str, modname: str, layer: str):
    """(min_x, min_y, max_x, max_y) of every graphic drawn on `layer`, or None."""
    xs: list[float] = []
    ys: list[float] = []
    coord = re.compile(r"\((?:start|end|center|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)\)")
    for child in _split_children(extract_footprint(libname, modname)):
        if _child_tag(child) not in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            continue
        if f'"{layer}"' not in child:
            continue
        for m in coord.finditer(child):
            xs.append(float(m.group(1)))
            ys.append(float(m.group(2)))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def load_params() -> dict:
    with PARAMS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def netlist_footprints(text: str) -> dict[str, str]:
    """{reference: footprint} straight out of the exported netlist."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r'\(comp\s*\(ref "([^"]+)"\)\s*\(value "(?:[^"\\]|\\.)*"\)\s*\(footprint "([^"]*)"\)',
        text, re.S,
    ):
        out[m.group(1)] = m.group(2)
    return out


def _approx(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# 1. Retired footprints must be gone from disk AND unreferenced
# ---------------------------------------------------------------------------
def verify_retired_footprints(footprints: dict[str, str]) -> str:
    for mod, reason in RETIRED_FOOTPRINTS.items():
        path = LOCAL_FPROOT / "wl-sync.pretty" / f"{mod}.kicad_mod"
        check(not path.exists(), f"{mod}.kicad_mod is back on disk at {path}. It was deleted: {reason}")
        users = sorted(r for r, fp in footprints.items() if fp.endswith(f":{mod}"))
        check(not users, f"{users} still reference the retired footprint {mod!r}. {reason}")
    return f"Retired footprints: {len(RETIRED_FOOTPRINTS)} gone from disk and unreferenced ({', '.join(RETIRED_FOOTPRINTS)})."


# ---------------------------------------------------------------------------
# 2. F6 -- no panel connector may carry a barrel pass-through drill
# ---------------------------------------------------------------------------
def verify_no_perpendicular_axis_panel_connectors(footprints: dict[str, str]) -> str:
    """A panel connector's numbered pads are contacts. A drill materially larger than a
    contact is a barrel passing THROUGH the board, which is only ever true when the
    connector's axis is perpendicular to it -- finding F6 exactly.

    Stated over every connector on the board rather than over the 31 BNCs by name, which is
    the lesson M3 taught: it named six bare panel outputs and there were eleven. A check
    stated over the population finds the members its author did not know about.
    """
    offenders: list[tuple[str, str, str, float]] = []
    inspected = 0
    contacts_seen = 0
    for ref, fp in sorted(footprints.items()):
        if not ref.startswith("J") or ":" not in fp:
            continue
        lib, mod = fp.split(":", 1)
        if lib == "MountingHole":
            continue  # a mounting hole IS a big hole; it carries no connector axis
        try:
            pads = footprint_pad_geometry(lib, mod)
        except (AssertionError, FileNotFoundError):
            continue
        inspected += 1
        for p in pads:
            # Only a NUMERIC pad number is a signal contact. A D-sub's "SH" shell pads and
            # any unnumbered mechanical pad are legitimately M3-sized and say nothing about
            # which way the connector points -- excluding them is what keeps this check
            # specific to the F6 signature rather than to "large hole".
            if not p.number.isdigit() or p.drill is None:
                continue
            contacts_seen += 1
            if p.drill > MAX_PANEL_CONTACT_DRILL_MM:
                offenders.append((ref, mod, p.number, p.drill))
    check(inspected >= 20, f"self-integrity: only {inspected} connector footprints resolved -- expected the board's full J population")
    check(contacts_seen >= 200, f"self-integrity: only {contacts_seen} numbered contacts inspected -- this check would be near-vacuous")
    check(
        not offenders,
        f"{len(offenders)} connector pad(s) are drilled larger than "
        f"{MAX_PANEL_CONTACT_DRILL_MM} mm, the signature of a connector whose axis is "
        f"PERPENDICULAR to the board (finding F6 -- a barrel passing through it, pointing "
        f"at the chassis lid rather than at the panel): {offenders[:6]}",
    )
    return (
        f"F6 axis check: {inspected} connector footprints inspected, no numbered pad drilled "
        f"over {MAX_PANEL_CONTACT_DRILL_MM} mm (no barrel pass-through, so no connector axis "
        f"perpendicular to the board)."
    )


# ---------------------------------------------------------------------------
# 3. MDR68 -- rebuilt from MH drawing 3700-0121-01 rev 3.0
# ---------------------------------------------------------------------------
MDR68_LIB, MDR68_MOD = "wl-sync", "MDR68_Male_RightAngle"


def verify_mdr68(params: dict) -> str:
    p = params["mdr68"]
    pads = footprint_pad_geometry(MDR68_LIB, MDR68_MOD)
    contacts = [q for q in pads if q.numbered]
    mounts = [q for q in pads if not q.numbered]

    check(len(contacts) == 68, f"MDR68: {len(contacts)} numbered contacts, expected 68")
    check(
        sorted(int(q.number) for q in contacts) == list(range(1, 69)),
        "MDR68: numbered contacts are not exactly 1..68",
    )

    # (a) THE FAB-STOPPER: contact drill.
    want_drill = p["contact_hole_dia_mm"]
    bad = [(q.number, q.drill) for q in contacts if not _approx(q.drill or 0, want_drill, 0.001)]
    check(
        not bad,
        f"MDR68: {len(bad)} contact(s) not drilled {want_drill} mm per "
        f"mdr68.contact_hole_dia_mm -- the pins do not fit: {bad[:6]}",
    )

    # (b) Four staggered rows at the drawing's own row spacing.
    row_offsets = p["row_offsets_from_edge_mm"]
    ys = sorted({round(q.y, 4) for q in contacts})
    check(len(ys) == 4, f"MDR68: contacts occupy {len(ys)} distinct rows, expected 4 (row_offsets_from_edge_mm has {len(row_offsets)} entries)")
    spacings = [round(ys[i + 1] - ys[i], 4) for i in range(3)]
    want_spacing = row_offsets[1]
    check(
        all(_approx(s, want_spacing, 0.001) for s in spacings),
        f"MDR68: row spacings {spacings} -- expected uniform {want_spacing} mm "
        f"(mdr68.row_offsets_from_edge_mm[1]; the alternative reading, four ABSOLUTE "
        f"offsets, would put two rows 0.615 mm apart with {want_drill} mm holes)",
    )
    per_row = {y: [q for q in contacts if _approx(q.y, y, 0.001)] for y in ys}
    sizes = sorted(len(v) for v in per_row.values())
    check(sizes == [17, 17, 17, 17], f"MDR68: contacts per row {sizes}, expected 17 each (68 / 4 rows)")

    # (c) Within-row X pitch is the stagger; the array spans the drawing's own width.
    stagger = p["contact_stagger_mm"]
    for y, members in per_row.items():
        xs = sorted(q.x for q in members)
        gaps = [round(xs[i + 1] - xs[i], 4) for i in range(len(xs) - 1)]
        check(
            all(_approx(g, stagger, 0.002) for g in gaps),
            f"MDR68: row at y={y} has X gaps {sorted(set(gaps))}, expected uniform "
            f"{stagger} mm (mdr68.contact_stagger_mm)",
        )
    all_x = [q.x for q in contacts]
    width = round(max(all_x) - min(all_x), 4)
    check(
        _approx(width, p["contact_array_width_mm"], p["contact_array_tol_mm"]),
        f"MDR68: contact array spans {width} mm, expected {p['contact_array_width_mm']} "
        f"+/- {p['contact_array_tol_mm']} (mdr68.contact_array_width_mm)",
    )

    # (d) Adjacent rows are offset by half the stagger -- what makes an 0.85 mm drill
    #     manufacturable at a 1.27 mm mating pitch in the first place.
    #
    #     Phase is computed as an INTEGER index on the 1.27 mm mating grid, not as a float
    #     modulo. `x % stagger` on this geometry returns 0.0 and 2.5399999999999996 for the
    #     same phase depending on the accumulated error in x, so a modulo-based version of
    #     this check reports two phases where there is one. Found by it failing on a
    #     footprint that was correct.
    pitch = p["contact_pitch_mm"]
    x0 = min(all_x)

    def _grid_index(x: float) -> int:
        k = (x - x0) / pitch
        check(
            abs(k - round(k)) < 0.02,
            f"MDR68: contact at x={x} is not on the {pitch} mm mating grid (index {k:.4f})",
        )
        return round(k)

    row_phase: dict[float, int] = {}
    for y, members in per_row.items():
        phases_here = {_grid_index(q.x) % 2 for q in members}
        check(len(phases_here) == 1, f"MDR68: row at y={y} mixes X phases {phases_here} -- every contact in a tail row must sit on one phase of the {pitch} mm grid")
        row_phase[y] = next(iter(phases_here))
    check(
        sorted(row_phase.values()) == [0, 0, 1, 1],
        f"MDR68: tail-row X phases {[row_phase[y] for y in ys]}, expected two rows on each "
        f"phase of the {pitch} mm mating grid (mdr68.contact_pitch_mm) -- the stagger that "
        f"makes {want_drill} mm drills fit at a {pitch} mm mating pitch",
    )

    # (e) Nearest-neighbour clearance actually survives the drill.
    pts = [(q.x, q.y) for q in contacts]
    nearest = min(
        math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]
    )
    max_pad = max(max(q.w, q.h) for q in contacts)
    check(
        nearest - max_pad > 0.15,
        f"MDR68: nearest contact centres are {nearest:.3f} mm apart with {max_pad} mm "
        f"pads -- only {nearest - max_pad:.3f} mm of copper between lands",
    )

    # (f) Mounting holes -- the drawing dimensions both diameter and span.
    check(len(mounts) == 2, f"MDR68: {len(mounts)} unnumbered mounting holes, expected 2")
    md = [q.drill for q in mounts]
    check(
        all(_approx(d or 0, p["mounting_hole_dia_mm"], 0.01) for d in md),
        f"MDR68: mounting hole drills {md}, expected {p['mounting_hole_dia_mm']} mm "
        f"(mdr68.mounting_hole_dia_mm)",
    )
    span = round(abs(mounts[0].x - mounts[1].x), 4)
    check(
        _approx(span, p["mounting_hole_span_mm"], 0.02),
        f"MDR68: mounting hole span {span} mm, expected {p['mounting_hole_span_mm']} mm "
        f"(mdr68.mounting_hole_span_mm)",
    )

    # (g) The tail-map assumption must travel with the artifact. See
    #     hardware/breakout/d3-panel-thickness.md for why this is an assumption and not a
    #     vendor round-trip.
    descr = footprint_descr(MDR68_LIB, MDR68_MOD)
    check(
        "PIN MAP UNVERIFIED" in descr,
        "MDR68: the footprint's own descr no longer carries the 'PIN MAP UNVERIFIED' note. "
        "The drawing gives the tail geometry but not which contact numbers land in which "
        "tail row; that assumption must stay attached to the artifact until it is confirmed "
        "and recorded in hardware/breakout/d3-panel-thickness.md.",
    )
    return (
        f"MDR68 (MH 3700-0121-01 rev 3.0): 68 contacts, {want_drill} mm drill, 4 rows at "
        f"{want_spacing} mm x 17, {stagger} mm within-row pitch on two {pitch} mm phases, "
        f"array {width} mm, 2 mounting holes {p['mounting_hole_dia_mm']} mm at {span} mm, "
        f"{nearest - max_pad:.2f} mm minimum copper gap, pin-map assumption recorded."
    )


# ---------------------------------------------------------------------------
# 4. Dual BNC -- built from Amphenol customer outline drawing 31-6575 rev A
# ---------------------------------------------------------------------------
BNC_LIB, BNC_MOD = "wl-sync", "BNC_Dual_RA_Isolated"


def verify_bnc_dual(params: dict) -> str:
    """The four signal tails must stay four INDEPENDENT pads.

    That is the assertion this whole footprint exists for. KiCad ships two footprints with
    this identical 6-hole pattern -- `BNC_Amphenol_031-6575_Horizontal` and
    `BNC_Win_364A2x95_Horizontal` -- and they differ in exactly one way: the Winchester one
    numbers four of its pads "3", commoning both shells with the ground terminals, because
    it models a FRONT-isolated part. The 031-6575 is INDEPENDENTLY isolated
    (`bnc_dual_isolated.note`), and ten channels on this board carry a separately-routed
    shield per connector (A_PD1_SHLD, A_MIC_SHLD, A_MISC1_SHLD and friends), each landing
    on its own resistor position.

    Adopting the Winchester numbering would short those shields together in pairs and
    produce a completely clean netlist while doing it. Hence this check.
    """
    p = params["bnc_dual_isolated"]
    pads = footprint_pad_geometry(BNC_LIB, BNC_MOD)
    signal = [q for q in pads if q.numbered]
    ground = [q for q in pads if not q.numbered]

    # (a) Four independent signal pads -- NOT three, and not four sharing numbers.
    check(
        len(signal) == p["pcb_signal_hole_count"],
        f"BNC dual: {len(signal)} numbered signal pads, expected {p['pcb_signal_hole_count']} "
        f"(bnc_dual_isolated.pcb_signal_hole_count)",
    )
    numbers = sorted(q.number for q in signal)
    check(
        numbers == ["1", "2", "3", "4"],
        f"BNC dual: signal pad numbers {numbers}, expected four DISTINCT pads 1-4. Repeated "
        f"numbers would common two shells -- the BNC_Win_364A2x95_Horizontal topology, which "
        f"models a FRONT-isolated part, not the independently-isolated 031-6575 this board "
        f"needs for its per-connector shields.",
    )
    bad = [(q.number, q.drill) for q in signal if not _approx(q.drill or 0, p["pcb_signal_hole_dia_mm"], 0.001)]
    check(not bad, f"BNC dual: signal pads not drilled {p['pcb_signal_hole_dia_mm']} mm: {bad}")

    # (b) Two ground/retention terminals, symmetric about the centreline.
    check(
        len(ground) == p["pcb_ground_hole_count"],
        f"BNC dual: {len(ground)} ground/retention terminals, expected {p['pcb_ground_hole_count']}",
    )
    gbad = [q.drill for q in ground if not _approx(q.drill or 0, p["pcb_ground_hole_dia_mm"], 0.001)]
    check(not gbad, f"BNC dual: ground terminals not drilled {p['pcb_ground_hole_dia_mm']} mm: {gbad}")
    gy = {round(q.y, 4) for q in ground}
    check(
        len(gy) == 1,
        f"BNC dual: the two ground terminals sit at different Y {sorted(gy)} -- they must be "
        f"symmetric. KiCad's own BNC_Amphenol_031-6575_Horizontal has them at -8.89 and "
        f"-8.79, a 0.1 mm asymmetry this footprint deliberately does not reproduce.",
    )
    gx = sorted(round(q.x, 4) for q in ground)
    check(
        _approx(gx[0], -gx[1], 0.001),
        f"BNC dual: ground terminals not symmetric in X: {gx}",
    )

    # (c) Every spacing the drawing dimensions must appear in the real geometry.
    want = sorted(p["pcb_spacings_mm"])
    sig_by = {q.number: q for q in signal}
    present = {
        round(abs(gx[1] - gx[0]), 4),                                   # ground to ground
        round(abs(sig_by["4"].x - sig_by["2"].x), 4),                   # shell to shell
        round(abs(sig_by["1"].x - sig_by["2"].x), 4),                   # centre to shell
        round(abs(sig_by["3"].y - ground[0].y), 4),                     # signal row to ground row
    }
    missing = [s for s in want if not any(_approx(s, q, 0.002) for q in present)]
    check(
        not missing,
        f"BNC dual: drawing spacings {missing} absent from the footprint (found {sorted(present)}) "
        f"-- bnc_dual_isolated.pcb_spacings_mm",
    )

    # (d) Body envelope -- the Amphenol drawing's, NOT the Winchester part's 15.0 x 39.1.
    fab = footprint_layer_extent(BNC_LIB, BNC_MOD, "F.Fab")
    check(fab is not None, "BNC dual: no F.Fab body outline drawn")
    w, d = round(fab[2] - fab[0], 3), round(fab[3] - fab[1], 3)
    check(
        _approx(w, p["body_width_mm"], 0.05) and _approx(d, p["body_depth_mm"], 0.05),
        f"BNC dual: body outline {w} x {d} mm, expected {p['body_width_mm']} x "
        f"{p['body_depth_mm']} (bnc_dual_isolated.body_width_mm/body_depth_mm). "
        f"BNC_Win_364A2x95_Horizontal draws 15.00 x 39.10 -- a different manufacturer's "
        f"body, which is why its outline was not copied.",
    )

    # (e) F6 itself: nothing here may be a barrel pass-through.
    biggest = max(q.drill or 0 for q in pads)
    check(
        biggest <= MAX_PANEL_CONTACT_DRILL_MM,
        f"BNC dual: largest drill {biggest} mm exceeds {MAX_PANEL_CONTACT_DRILL_MM} mm -- "
        f"this is supposed to be the RIGHT-ANGLE part that fixes F6",
    )
    return (
        f"BNC dual (Amphenol 031-6575 rev A): 4 independent signal pads 1-4 at "
        f"{p['pcb_signal_hole_dia_mm']} mm, 2 symmetric ground terminals at "
        f"{p['pcb_ground_hole_dia_mm']} mm, all {len(want)} drawing spacings present, body "
        f"{w} x {d} mm, largest drill {biggest} mm (right-angle, no barrel pass-through)."
    )


# ---------------------------------------------------------------------------
# Self-tests -- every assertion above needs one proving it fires.
# ---------------------------------------------------------------------------
def _corrupt_pad(text: str, pad_number: str, field: str, new: str) -> str:
    """Rewrite one field of one numbered pad in a footprint's raw text."""
    start = text.find(f'(pad "{pad_number}"')
    assert start != -1, f"self-test setup: pad {pad_number!r} not found"
    block = _find_balanced(text, start)
    patched = re.sub(rf"\({field}\s+[^)]*\)", f"({field} {new})", block, count=1)
    assert patched != block, f"self-test setup: {field} rewrite changed nothing"
    return text[:start] + patched + text[start + len(block):]


def self_test_mdr68_drill(params: dict) -> str:
    """The fab-stopper's own negative control: restore the as-built 0.5 mm drill and
    confirm verify_mdr68() rejects it. Asserts on the INVARIANT part of the message, not on
    the number -- this project has had two controls rot by quoting a value the change was
    about to move (connector-handoff.md §4)."""
    import kicad_pcb

    key = f"{MDR68_LIB}:{MDR68_MOD}"
    good = extract_footprint(MDR68_LIB, MDR68_MOD)
    kicad_pcb._FOOTPRINT_CACHE[key] = _corrupt_pad(good, "1", "drill", "0.5")
    try:
        verify_mdr68(params)
    except CheckFailure as e:
        check("the pins do not fit" in str(e), f"self-test 'mdr68 drill': wrong failure: {e}")
        return str(e)
    finally:
        kicad_pcb._FOOTPRINT_CACHE[key] = good
    raise CheckFailure("self-test 'mdr68 drill' did NOT raise -- the drill assertion is vacuous")


def self_test_mdr68_row_count(params: dict) -> str:
    """Collapse row 4 onto row 3 and confirm the 4-row assertion fires -- the other half of
    the as-built defect (2 rows at 2.84 mm against a 4-row staggered arrangement)."""
    import kicad_pcb

    key = f"{MDR68_LIB}:{MDR68_MOD}"
    good = extract_footprint(MDR68_LIB, MDR68_MOD)
    pads = footprint_pad_geometry(MDR68_LIB, MDR68_MOD)
    ys = sorted({round(q.y, 4) for q in pads if q.numbered})
    victim = next(q for q in pads if q.numbered and _approx(q.y, ys[3], 0.001))
    corrupted = _corrupt_pad(good, victim.number, "at", f"{victim.x} {ys[2]}")
    kicad_pcb._FOOTPRINT_CACHE[key] = corrupted
    try:
        verify_mdr68(params)
    except CheckFailure as e:
        check(
            "contacts per row" in str(e) or "distinct rows" in str(e),
            f"self-test 'mdr68 rows': wrong failure: {e}",
        )
        return str(e)
    finally:
        kicad_pcb._FOOTPRINT_CACHE[key] = good
    raise CheckFailure("self-test 'mdr68 rows' did NOT raise -- the row assertion is vacuous")


def self_test_f6_axis(footprints: dict[str, str]) -> str:
    """Reintroduce the as-built vertical BNC on one connector and confirm the F6 axis check
    fires. Uses a REAL footprint that really carries a 9.65 mm barrel drill, not a synthetic
    one, so the control exercises the same path production does."""
    victim = next(r for r in sorted(footprints) if r.startswith("J"))
    corrupted = dict(footprints)
    corrupted[victim] = "Connector_Coaxial:BNC_PanelMountable_Vertical"
    try:
        verify_no_perpendicular_axis_panel_connectors(corrupted)
    except CheckFailure as e:
        check("PERPENDICULAR to the board" in str(e), f"self-test 'f6 axis': wrong failure: {e}")
        return str(e)
    raise CheckFailure("self-test 'f6 axis' did NOT raise -- the axis assertion is vacuous")


def self_test_bnc_shells_commoned(params: dict) -> str:
    """THE control that matters for the dual BNC: renumber one shell pad to match the other
    -- the exact BNC_Win_364A2x95_Horizontal topology the audit's own handoff recommended
    -- and confirm verify_bnc_dual() rejects it. Without this, the four-independent-pads
    assertion could pass vacuously and ten per-connector shields would be silently paired."""
    import kicad_pcb

    key = f"{BNC_LIB}:{BNC_MOD}"
    good = extract_footprint(BNC_LIB, BNC_MOD)
    start = good.find('(pad "4"')
    check(start != -1, "self-test setup: pad 4 not found in the dual BNC footprint")
    kicad_pcb._FOOTPRINT_CACHE[key] = good[:start] + good[start:].replace('(pad "4"', '(pad "2"', 1)
    try:
        verify_bnc_dual(params)
    except CheckFailure as e:
        check("four DISTINCT pads" in str(e), f"self-test 'bnc shells commoned': wrong failure: {e}")
        return str(e)
    finally:
        kicad_pcb._FOOTPRINT_CACHE[key] = good
    raise CheckFailure(
        "self-test 'bnc shells commoned' did NOT raise -- the independent-shell assertion is vacuous"
    )


def self_test_bnc_body_envelope(params: dict) -> str:
    """Widen the body to the Winchester part's 15.0 mm and confirm the envelope check fires
    -- the other half of the same wrong prescription, and the half that would have put a
    2.9 mm-deeper connector on a 16-position panel."""
    import kicad_pcb

    key = f"{BNC_LIB}:{BNC_MOD}"
    good = extract_footprint(BNC_LIB, BNC_MOD)
    kicad_pcb._FOOTPRINT_CACHE[key] = good.replace("-7.2 1.05", "-7.5 1.05", 1).replace("-7.2 -35.15", "-7.5 -35.15", 1)
    try:
        verify_bnc_dual(params)
    except CheckFailure as e:
        check("body outline" in str(e), f"self-test 'bnc body': wrong failure: {e}")
        return str(e)
    finally:
        kicad_pcb._FOOTPRINT_CACHE[key] = good
    raise CheckFailure("self-test 'bnc body' did NOT raise -- the body envelope assertion is vacuous")


def self_test_retired_footprint(footprints: dict[str, str]) -> str:
    """Put a reference back on the deleted M12 footprint and confirm the retirement check
    fires -- so deleting the file and forgetting a generator cannot both pass."""
    corrupted = dict(footprints)
    corrupted["J1"] = "wl-sync:M12A_5_Panel"
    try:
        verify_retired_footprints(corrupted)
    except CheckFailure as e:
        check("retired footprint" in str(e), f"self-test 'retired footprint': wrong failure: {e}")
        return str(e)
    raise CheckFailure("self-test 'retired footprint' did NOT raise -- the retirement check is vacuous")


def main() -> int:
    net_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NET_PATH
    if not net_path.exists():
        print(
            f"netlist not found: {net_path}\n"
            "  kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net "
            "hardware/breakout/breakout.kicad_sch",
            file=sys.stderr,
        )
        return 1
    text = net_path.read_text()
    parse_netlist(text)  # structural sanity on the file before anything reads it
    footprints = netlist_footprints(text)
    params = load_params()

    try:
        results = [
            verify_retired_footprints(footprints),
            verify_no_perpendicular_axis_panel_connectors(footprints),
            verify_mdr68(params),
            verify_bnc_dual(params),
        ]
        controls = [
            self_test_retired_footprint(footprints),
            self_test_f6_axis(footprints),
            self_test_mdr68_drill(params),
            self_test_mdr68_row_count(params),
            self_test_bnc_shells_commoned(params),
            self_test_bnc_body_envelope(params),
        ]
    except CheckFailure as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    for r in results:
        print(f"  OK  {r}")
    print(f"  OK  {len(controls)} negative controls all fired.")
    print(f"check_footprint_geometry: {len(results)} checks + {len(controls)} controls passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
