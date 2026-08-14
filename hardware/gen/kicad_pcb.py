"""Reusable helpers for generating KiCad 10 .kicad_pcb files programmatically.

Companion to kicad_sch.py, same philosophy applied to layout: pull real footprint
definitions out of KiCad's stock .pretty libraries, place instances on the board, and
assign each pad's net directly from the schematic's own exported netlist -- so the
ratsnest pcbnew computes at load time is guaranteed to match the schematic exactly, the
same "read the real exported data back, don't hand-maintain a second copy of it" discipline
hardware/gen/check_mule_netlist.py established for the schematic side.

Why a generator at all, not a hand-drawn board: `kicad-cli` has no netlist-import or
Specctra-DSN subcommand (confirmed against `kicad-cli pcb --help` while scoping this task),
so there is no way to place footprints or import a netlist from the command line short of
writing one. Placement is also the design-critical half of this task; a generator makes the
floorplan's constraints (the isolation clearance slot, the two IDC headers on opposite
edges, decoupling-cap proximity) checkable code rather than only a diagram.

Format constraints found building this, parallel to kicad_sch.py's four for schematics:

1. A .kicad_pcb file has no shared footprint-library section analogous to a schematic's
   `lib_symbols` -- every placed footprint instance carries its FULL graphic definition
   (fp_line/fp_rect/fp_circle/fp_poly/fp_text/pad geometry) inline, verbatim from the
   library .kicad_mod file (confirmed against a real KiCad-saved board, this repo's own
   installed RaspberryPi-HAT.kicad_pcb template). extract_footprint() pulls that text
   wholesale; Board.place() instantiates it by adding only the instance-only fields (a
   fresh uuid on the footprint and on every graphic/pad child, plus per-pad net/at-rotation)
   rather than reconstructing geometry by hand.
2. Everything inside a `(footprint ...)` block stays in the footprint's OWN local
   coordinate frame -- pads, silkscreen, courtyard and fab-layer graphics are never
   translated by this generator; only the single top-level `(at X Y [ROT])` on the
   footprint itself moves and rotates it as a rigid body. This is the opposite of
   kicad_sch.py's pin_pos(), which has to add the placement offset to every pin by hand
   because schematic global_labels need absolute coordinates -- a PCB footprint's own pads
   need no equivalent transform here, KiCad applies `at` to the whole block at load time.
3. A pad with no net simply omits `(net ...)` entirely -- confirmed against the same real
   board (its MountingHole footprints' pads carry no net field at all). There is no need to
   emit a `(net 0 "")` placeholder on an intentionally-unconnected pad.
4. Net codes are assigned by this generator; net 0 is always the reserved "no net" entry
   (`(net 0 "")`, required to exist even though nothing references it by number). Every
   pad's `(net N "name")` must use the SAME code the board-level `(net N "name")` table
   declares for that name, or KiCad treats them as silently different nets -- Board.place()
   never lets a caller pick a net code directly, only a net NAME, specifically so this
   can't drift (net_code() is the only path to a code, and it's idempotent per name).
5. A quoted string inside a library .kicad_mod file can contain literal, individually
   balanced parentheses (e.g. R_0603_1608Metric's own `descr` field: "...square
   (rectangular) end terminal... (Body size source: ...)"). A naive paren-depth scan that
   doesn't treat `"..."` as opaque still happens to end up balanced in every case actually
   present in this design's footprints (each such string's parens are paired within
   themselves), but that is a property of today's specific strings, not something to rely
   on -- _find_balanced() and _split_children() below are quote-aware (parens inside a
   double-quoted string, tracking `\\"` escapes, never change nesting depth) so a future
   footprint whose description happens to end mid-parenthetical doesn't silently truncate.
6. Constraint 2 above is true of POSITIONS but not of ANGLES. In a placed footprint, every
   pad's and every text element's own `(at X Y ANGLE)` carries an ANGLE that is ABSOLUTE
   (board frame), not local -- so instantiating a rotated footprint means writing
   `library angle + footprint rotation`, normalised into [0, 360), on each pad and each
   text child, even though their X/Y stay in the local frame untouched. Established from
   real pcbnew-authored boards, not from the format docs: in KiCad's own
   `demos/pic_programmer`, `Package_DIP:DIP-14_W7.62mm_LongPads` is placed at
   `(at 115.57 119.38 90)` and every one of its 14 pads reads `(at LX LY 90)` while the
   library master has no pad angle at all; the same board's `Connector_Dsub:DSUB-9...` at
   `rot=-90` writes pad angle `270` (hence the normalisation); and that footprint's
   `fp_text user "${REFERENCE}"`, whose library angle is 90, is written as 180 = 90 + 90.
   Omitting this offset is silent for a pad whose shape is invariant under the rotation in
   question (a circle at any angle, a square roundrect at multiples of 90 deg) and is a
   real geometry error for any other pad -- an oval, a rectangle, a keyed or chamfered pad
   would be laid down in the wrong orientation while sitting at the right centre. It is
   also what `kicad-cli pcb drc` reports as `lib_footprint_mismatch` on rotated instances:
   the check re-orients the library master to the instance's rotation and compares, so a
   pad left at its library angle differs from the master by exactly the rotation.
7. `point` -- the pin-1 anchor marker newer footprint generators emit (e.g.
   `Package_DIP:DIP-8_W7.62mm`) -- is the single exception to constraint 2 on the position
   side: in a placed instance its `(at X Y)` is in ABSOLUTE BOARD coordinates, at rot=0 as
   much as under rotation. Same evidence base (KiCad's demos/pic_programmer, the only demo
   board carrying any): every one of its seven point-bearing instances writes exactly
   `place_point(footprint at, library-local point)`. Copied verbatim from the library the
   marker lands at its local offset from the BOARD origin -- for this design, both DIP-8s'
   markers stacked at (3.81, 3.81) in the board's top-left corner.

Constraints 6 and 7 are both applied inside Board.place()'s per-child loop.
"""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

FPROOT = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")


def uid() -> str:
    return str(uuid.uuid4())


def _fmt(v: float) -> str:
    """Format a coordinate/angle the way KiCad does: no trailing `.0` on whole numbers."""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _cos_sin(rot: float) -> tuple[float, float]:
    """cos/sin of `rot` degrees, exact at the quarter turns (every rotation this generator
    actually uses), so placed coordinates come out as clean decimals instead of carrying a
    1e-16 residue from math.cos(math.radians(270))."""
    r = rot % 360
    if r == 0:
        return 1.0, 0.0
    if r == 90:
        return 0.0, 1.0
    if r == 180:
        return -1.0, 0.0
    if r == 270:
        return 0.0, -1.0
    a = math.radians(r)
    return math.cos(a), math.sin(a)


def place_point(
    fp_x: float, fp_y: float, rot: float, lx: float, ly: float
) -> tuple[float, float]:
    """Absolute board position of the footprint-local point (lx, ly), for a footprint
    placed at `(at fp_x fp_y rot)`.

    KiCad's board frame has +Y downward and a positive footprint rotation turns the part
    counter-clockwise on screen, which makes the transform

        x = fp_x + lx*cos(rot) + ly*sin(rot)
        y = fp_y - lx*sin(rot) + ly*cos(rot)

    Verified against KiCad's own output rather than derived from the sign convention:
    `kicad-cli pcb drc` reports U5's pad 2 (library-local 0, 2.54) at (17.46, 62.5) and its
    pad 8 (library-local 7.62, 0) at (20.0, 70.12) for a DIP-8 placed at (20, 62.5, 270) --
    both exactly what this returns.
    """
    c, s = _cos_sin(rot)
    return (fp_x + lx * c + ly * s, fp_y - lx * s + ly * c)


def local_point(
    fp_x: float, fp_y: float, rot: float, ax: float, ay: float
) -> tuple[float, float]:
    """Inverse of place_point(): the footprint-local coordinates that land on the absolute
    board point (ax, ay). Lets placement code aim at a spot on the BOARD -- "just above this
    part's placed courtyard" -- and write the local number a footprint child needs."""
    c, s = _cos_sin(rot)
    dx, dy = ax - fp_x, ay - fp_y
    return (c * dx - s * dy, s * dx + c * dy)


# ---------------------------------------------------------------------------
# Quote-aware balanced-paren parsing (see module docstring, constraint 5).
# ---------------------------------------------------------------------------


def _find_balanced(text: str, start: int) -> str:
    """Return the balanced-paren s-expression block starting at `start`, treating the
    contents of any `"..."` string (with `\\"` escapes) as opaque -- parens inside a
    quoted string never change nesting depth."""
    depth = 0
    in_str = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1  # skip the escaped character too
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise AssertionError("unbalanced parens")


def _split_children(block: str) -> list[str]:
    """Split a balanced `(tag ["name"] child...)` block into its direct child
    s-expressions, each itself a balanced, self-contained string. Skips the head token and
    its optional immediately-following quoted name (e.g. `footprint "R_0603..."`)."""
    assert block[0] == "(" and block[-1] == ")"
    inner = block[1:-1]
    n = len(inner)
    i = 0
    while i < n and not inner[i].isspace():
        i += 1
    while i < n and inner[i].isspace():
        i += 1
    if i < n and inner[i] == '"':
        j = i + 1
        while j < n and inner[j] != '"':
            if inner[j] == "\\":
                j += 1
            j += 1
        i = j + 1
    children = []
    while i < n:
        while i < n and inner[i].isspace():
            i += 1
        if i >= n:
            break
        assert inner[i] == "(", f"expected '(' at offset {i}, context: {inner[max(0,i-20):i+20]!r}"
        child = _find_balanced(inner, i)
        children.append(child)
        i += len(child)
    return children


def _child_tag(child: str) -> str:
    """The head token of a `(tag ...)` s-expression, e.g. "pad" or "fp_line"."""
    assert child[0] == "("
    i = 1
    while i < len(child) and not child[i].isspace() and child[i] != ")":
        i += 1
    return child[1:i]


def _inject_before_close(block: str, fields: str) -> str:
    """Splice `fields` (already-formatted, newline-led s-expr fragment(s)) in just before
    a block's closing paren."""
    assert block.endswith(")")
    return block[:-1].rstrip() + fields + "\n\t)"


_PAD_NUM_RE = re.compile(r'^\(pad\s+"([^"]*)"')
_COORD_RE = re.compile(r'\((?:start|end|center|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)\)')
# A footprint child's OWN placement: always its first `(at ...)`, in the footprint's local
# frame, with an optional third field that is an angle (see module docstring, constraint 6).
_AT_RE = re.compile(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)')
# Children whose `(at ...)` carries an absolute angle that must be offset by the footprint's
# rotation when the instance is placed. Geometry children (fp_line/fp_rect/fp_circle/fp_poly/
# fp_arc) carry no angle at all -- their vertices stay in the local frame and KiCad rotates
# them as a rigid body -- and `point` (the pin-1 anchor marker) has an `(at ...)` with no
# angle field in the library and none in a placed instance either.
_ANGLE_TAGS = frozenset({"pad", "fp_text"})
# `fp_text_box` is a text element too, so it has an orientation that a rotated instance has
# to account for -- but it does not express it as an `(at ...)`: it uses `(start ...)`,
# `(end ...)` (or `(pts ...)`) plus an optional `(angle ...)`, so the rule above does not
# apply to it unchanged. No footprint this design places has one (they appear on a handful
# of stock modules, e.g. RF_Module:Raytac_MDBT42Q), so rather than guess at the right
# transform untested, Board.place() refuses to rotate a footprint containing one.
_UNROTATABLE_TAGS = frozenset({"fp_text_box"})

# Element tags that get a fresh instance uuid when placed (graphics + pads) -- confirmed
# against a real KiCad-saved board (RaspberryPi-HAT.kicad_pcb): every fp_line/fp_rect/
# fp_circle/fp_poly/fp_arc/fp_text/pad child carries its own uuid once instantiated, even
# though none of them do in the library .kicad_mod source file. `point` (a pin-1 anchor
# marker some newer footprint-generator output uses, e.g. Package_DIP:DIP-8_W7.62mm) is
# the same story -- omitting its uuid is what a real `kicad-cli pcb drc` run flagged as
# `lib_footprint_mismatch` on every DIP-8 instance while building this generator, which is
# how this tag was found missing here in the first place.
_UUID_TAGS = frozenset({
    "fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc", "fp_text", "pad", "point",
})
# Library-file-only metadata: present in the .kicad_mod source, absent from a placed
# instance (confirmed against the same real board -- no version/generator info survives
# placement, only the geometry and pads do).
_DROP_TAGS = frozenset({"version", "generator", "generator_version", "property"})


_FOOTPRINT_CACHE: dict[str, str] = {}


def extract_footprint(libname: str, modname: str) -> str:
    """Read one footprint's full body text out of `libname.pretty/modname.kicad_mod`."""
    key = f"{libname}:{modname}"
    if key not in _FOOTPRINT_CACHE:
        path = FPROOT / f"{libname}.pretty" / f"{modname}.kicad_mod"
        text = path.read_text()
        start = text.find(f'(footprint "{modname}"')
        assert start != -1, f"{key}: opening tag not found in {path}"
        _FOOTPRINT_CACHE[key] = _find_balanced(text, start)
    return _FOOTPRINT_CACHE[key]


def _child_at(child: str) -> tuple[float, float, float]:
    """A child's own `(at X Y [ANGLE])` -- always its first `at`, and always the element's
    own placement (a pad's `size`/`drill`/`primitives` never precede it)."""
    m = _AT_RE.search(child)
    assert m, f"child has no (at ...): {child[:60]!r}"
    return float(m.group(1)), float(m.group(2)), float(m.group(3) or 0.0)


def _rewrite_at(child: str, x: float, y: float, ang: float | None) -> str:
    """Replace a child's own first `(at ...)`. `ang=None` (or 0) omits the angle field
    entirely, which is exactly what KiCad writes for an unrotated element."""
    body = f"{_fmt(x)} {_fmt(y)}"
    if ang:
        body += f" {_fmt(ang)}"
    return _AT_RE.sub(f"(at {body})", child, count=1)


def footprint_pads(libname: str, modname: str) -> dict[str, list[tuple[float, float, float]]]:
    """{pad_number: [(local_x, local_y, local_angle), ...]} straight out of the library
    master. A list per number because a pad number is not unique in general (thermal pads,
    jumper pads, multi-pad connector positions) -- Board.pad_pos() asserts on that rather
    than silently taking the first."""
    out: dict[str, list[tuple[float, float, float]]] = {}
    for child in _split_children(extract_footprint(libname, modname)):
        if _child_tag(child) != "pad":
            continue
        m = _PAD_NUM_RE.match(child)
        assert m, f"{libname}:{modname}: malformed pad block: {child[:60]!r}"
        out.setdefault(m.group(1), []).append(_child_at(child))
    return out


def footprint_courtyard(libname: str, modname: str) -> tuple[float, float, float, float] | None:
    """The library master's courtyard bounding box in its own local frame,
    `(min_x, min_y, max_x, max_y)`, or None if the footprint draws no courtyard."""
    xs: list[float] = []
    ys: list[float] = []
    for child in _split_children(extract_footprint(libname, modname)):
        if _child_tag(child) not in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            continue
        if '"F.CrtYd"' not in child and '"B.CrtYd"' not in child:
            continue
        for m in _COORD_RE.finditer(child):
            xs.append(float(m.group(1)))
            ys.append(float(m.group(2)))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


_REF_PROP_RE = re.compile(r'\(property\s+"Reference"\s+"([^"]*)"')


def read_placed_pads(pcb_text: str) -> dict[tuple[str, str], tuple[float, float]]:
    """Absolute pad centres `{(reference, pad_number): (x, y)}` read back out of a written
    .kicad_pcb.

    Deliberately independent of Board's own bookkeeping: it re-reads each footprint's
    `(at ...)` and each pad's local `(at ...)` from the file text and applies the same
    transform KiCad does. That makes it a real check on the artifact -- a distance measured
    with this is measured on the board file, not on the placement arithmetic that produced
    it -- and it works on a file this generator did not write (e.g. after
    `kicad-cli pcb upgrade` rewrites it, or on a board a person has since edited).
    """
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for m in re.finditer(r'\(footprint "', pcb_text):
        block = _find_balanced(pcb_text, m.start())
        children = _split_children(block)
        ref = None
        for child in children:
            rm = _REF_PROP_RE.match(child)
            if rm:
                ref = rm.group(1)
                break
        assert ref, f"placed footprint with no Reference property: {block[:80]!r}"
        at_children = [c for c in children if _child_tag(c) == "at"]
        assert at_children, f"{ref}: placed footprint with no (at ...)"
        fx, fy, rot = _child_at(at_children[0])
        for child in children:
            if _child_tag(child) != "pad":
                continue
            num = _PAD_NUM_RE.match(child).group(1)
            lx, ly, _ = _child_at(child)
            out[(ref, num)] = place_point(fx, fy, rot, lx, ly)
    return out


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class Board:
    """Accumulates the pieces of a .kicad_pcb file as it's built."""

    project: str
    width: float
    height: float
    sch_filename: str = ""

    def __post_init__(self):
        self.footprints_text: list[str] = []
        self.graphics_text: list[str] = []
        self.zones_text: list[str] = []
        self.net_codes: dict[str, int] = {"": 0}  # "" is the reserved no-net entry (net 0)
        self.ref_positions: dict[str, tuple[float, float, float]] = {}  # for floorplan checks
        # Absolute board geometry of what was actually placed, derived from the library
        # footprint's own pads/courtyard after rotation -- so floorplan code can ask where a
        # pin physically IS rather than restate it in a comment that the board file is then
        # free to disagree with.
        self.pad_positions: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self.courtyards: dict[str, tuple[float, float, float, float]] = {}

    # -- nets ------------------------------------------------------------

    def net_code(self, name: str) -> int:
        """Return `name`'s net code, assigning the next free one on first use. The ONLY
        path to a net code (see module docstring, constraint 4) -- callers never pick one
        directly, so a pad and the board-level net table can never disagree about what
        code a given name maps to."""
        if name not in self.net_codes:
            self.net_codes[name] = len(self.net_codes)
        return self.net_codes[name]

    # -- placement ---------------------------------------------------------

    def place(
        self,
        libname: str,
        modname: str,
        ref: str,
        value: str,
        x: float,
        y: float,
        rot: float = 0,
        pad_nets: dict[str, str] | None = None,
        sch_uuid: str | None = None,
        layer: str = "F.Cu",
        ref_at: tuple[float, float] | None = None,
    ) -> None:
        """Place one footprint instance at (x, y), rotated `rot` degrees.

        pad_nets: {pad_number: net_name}. A pad number absent from this dict gets no net
        at all -- a real, intentionally-unconnected pad (an unused connector position, a
        barrel jack's power-detect switch terminal), not an omission (see module
        docstring, constraint 3).

        ref_at: where to put this instance's Reference silkscreen text, in BOARD
        coordinates. The default (just above the placed courtyard) is right for a part with
        clear space above it; a part deliberately tucked hard under another one has no such
        space and needs to say where its label goes instead.
        """
        pad_nets = pad_nets or {}
        lib_id = f"{libname}:{modname}"
        block = extract_footprint(libname, modname)
        children = _split_children(block)

        # Put the Reference silkscreen text just above the part's own courtyard instead of
        # at a fixed (0,0), which for most two- and three-pad parts sits on top of pad 1 --
        # confirmed against a real `kicad-cli pcb drc` run: (0,0,0) for every part produced
        # ~230 silk_over_copper/silk_overlap warnings, almost all "Reference field of X
        # overlaps pad N of X" on X's own pads.
        #
        # "Above" means above on the BOARD, not above in the footprint's own frame: the two
        # are the same thing only at rot=0. Aiming in the local frame put U5's and U6's
        # reference text 90 degrees round, straight into the isolation slot -- silkscreen
        # printed into a milled void, i.e. the two parts this board exists to measure would
        # have arrived unlabelled. So the target is computed in board coordinates from the
        # PLACED courtyard and converted back with local_point().
        crtyd = footprint_courtyard(libname, modname)
        if crtyd:
            corners = [
                place_point(x, y, rot, cx, cy)
                for cx in (crtyd[0], crtyd[2])
                for cy in (crtyd[1], crtyd[3])
            ]
            box = (
                min(c[0] for c in corners), min(c[1] for c in corners),
                max(c[0] for c in corners), max(c[1] for c in corners),
            )
            self.courtyards[ref] = box
            ref_x, ref_y = local_point(
                x, y, rot, *(ref_at or ((box[0] + box[2]) / 2, box[1] - 0.8))
            )
        elif ref_at:
            ref_x, ref_y = local_point(x, y, rot, *ref_at)
        else:
            ref_x, ref_y = 0.0, 0.0  # no courtyard found (shouldn't happen for real parts)

        kept: list[str] = []
        seen_pads: set[str] = set()
        for child in children:
            tag = _child_tag(child)
            if tag in _DROP_TAGS:
                continue
            if tag == "pad":
                m = _PAD_NUM_RE.match(child)
                assert m, f"{lib_id}: malformed pad block: {child[:60]!r}"
                num = m.group(1)
                seen_pads.add(num)
                lx, ly, _ = _child_at(child)
                self.pad_positions.setdefault((ref, num), []).append(
                    place_point(x, y, rot, lx, ly)
                )
                fields = f'\n\t\t(uuid "{uid()}")'
                if num in pad_nets:
                    net_name = pad_nets[num]
                    code = self.net_code(net_name)
                    fields = f'\n\t\t(net {code} "{_escape(net_name)}")' + fields
                child = _inject_before_close(child, fields)
            elif tag in _UUID_TAGS:
                child = _inject_before_close(child, f'\n\t\t(uuid "{uid()}")')
            assert not (rot and tag in _UNROTATABLE_TAGS), (
                f"{lib_id}: contains a `{tag}`, whose orientation this generator does not "
                f"know how to transform, and {ref} is placed at rot={rot}. Place it "
                f"unrotated, or work out the transform against a pcbnew-saved board first "
                f"(see module docstring, constraint 6)"
            )
            if tag in _ANGLE_TAGS and rot:
                # Absolute-angle rule, module docstring constraint 6.
                lx, ly, ang = _child_at(child)
                child = _rewrite_at(child, lx, ly, (ang + rot) % 360)
            elif tag == "point":
                # `point` (the pin-1 anchor marker) is the one child whose POSITION is
                # written in absolute board coordinates in a placed instance, not in the
                # footprint's local frame -- verified against every point-carrying footprint
                # instance in KiCad's own demo boards (all seven are in demos/pic_programmer:
                # five DIPs at rot=0, a DIP-14 at rot=90 and a DSUB-9 at rot=-90; each
                # board value equals place_point(footprint at, library-local point) to the
                # last digit). Copied verbatim
                # it would sit at the footprint's local offset from the BOARD origin
                # instead of on the part.
                lx, ly, ang = _child_at(child)
                px, py = place_point(x, y, rot, lx, ly)
                child = _rewrite_at(child, px, py, ang or None)
            kept.append(child)

        missing = set(pad_nets) - seen_pads
        assert not missing, (
            f"{ref} ({lib_id}): pad_nets references pad(s) {sorted(missing)} that don't "
            f"exist on this footprint (have: {sorted(seen_pads)}) -- a net/pin mismatch "
            f"between the schematic and the chosen footprint"
        )

        self.ref_positions[ref] = (x, y, rot)
        fp_uuid = uid()
        at = f"{x} {y}" if rot == 0 else f"{x} {y} {rot}"
        # Every property is a text element, so its `at` angle is absolute too (module
        # docstring, constraint 6): pcbnew writes the footprint's own rotation on all four,
        # which is what makes a rotated part's silkscreen reference read along the part.
        prot = _fmt(rot % 360)
        props = (
            f'\t\t(property "Reference" "{ref}"\n'
            f'\t\t\t(at {ref_x} {ref_y} {prot})\n'
            f'\t\t\t(layer "F.SilkS")\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 0.8 0.8) (thickness 0.13)))\n'
            f'\t\t)\n'
            f'\t\t(property "Value" "{_escape(value)}"\n'
            f'\t\t\t(at 0 0 {prot})\n'
            f'\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 0.9 0.9) (thickness 0.15)))\n'
            f'\t\t)\n'
            f'\t\t(property "Datasheet" ""\n'
            f'\t\t\t(at 0 0 {prot})\n'
            f'\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
            f'\t\t)\n'
            f'\t\t(property "Description" ""\n'
            f'\t\t\t(at 0 0 {prot})\n'
            f'\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
            f'\t\t)'
        )
        link = ""
        if sch_uuid:
            # Best-effort cross-link back to the schematic symbol instance, for pcbnew's
            # "Update PCB from Schematic" / cross-probing -- not required for DRC, the
            # ratsnest, or routing (net assignment on the pads above is what those actually
            # need), so a wrong path here costs GUI convenience, not correctness.
            link = (
                f'\n\t\t(path "/{sch_uuid}")'
                f'\n\t\t(sheetname "/")'
                f'\n\t\t(sheetfile "{self.sch_filename}")'
            )
        body = "\n".join(kept)
        self.footprints_text.append(
            f'\t(footprint "{lib_id}"\n'
            f'\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{fp_uuid}")\n'
            f'\t\t(at {at})\n'
            f'{props}{link}\n'
            f'{body}\n'
            f'\t)'
        )

    # -- placed geometry (for floorplan arithmetic) ------------------------

    def pad_pos(self, ref: str, pad: str) -> tuple[float, float]:
        """Absolute board position of `ref`'s pad `pad`, as actually placed.

        Derived in place() from the library footprint's own pad coordinates put through
        place_point() with the instance's rotation -- never from a number a caller typed in
        a comment. Floorplan code that needs to sit a part next to a specific pin should ask
        this rather than restate the pin's coordinates, so the placement and the board file
        cannot disagree.
        """
        hits = self.pad_positions.get((ref, pad))
        assert hits, (
            f"{ref} has no pad {pad!r} placed (placed pads for {ref}: "
            f"{sorted(p for (r, p) in self.pad_positions if r == ref)})"
        )
        assert len(hits) == 1, (
            f"{ref} has {len(hits)} pads numbered {pad!r} -- ambiguous, so there is no "
            f"single position to return; use self.pad_positions[(ref, pad)] and say which"
        )
        return hits[0]

    def courtyard(self, ref: str) -> tuple[float, float, float, float]:
        """`ref`'s courtyard bounding box in absolute board coordinates, after rotation:
        `(min_x, min_y, max_x, max_y)`. The real keep-clear envelope of the placed part,
        for deciding how far away the next part has to sit."""
        assert ref in self.courtyards, f"{ref} was not placed, or draws no courtyard"
        return self.courtyards[ref]

    # -- board-outline / isolation-slot graphics --------------------------

    def edge_rect(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """A closed rectangle on Edge.Cuts. The OUTER board outline if it isn't fully
        enclosed by another Edge.Cuts shape; an interior CUTOUT (a real milled slot) if it
        is -- KiCad determines this from containment, not from any special tag, so the same
        method serves both the board outline and the isolation slot below."""
        self.graphics_text.append(
            f'\t(gr_rect\n'
            f'\t\t(start {x1} {y1})\n'
            f'\t\t(end {x2} {y2})\n'
            f'\t\t(stroke (width 0.15) (type default))\n'
            f'\t\t(fill none)\n'
            f'\t\t(layer "Edge.Cuts")\n'
            f'\t\t(uuid "{uid()}")\n'
            f'\t)'
        )

    def keepout_rect(
        self, x1: float, y1: float, x2: float, y2: float, name: str,
        layers: tuple[str, ...] = ("F.Cu", "B.Cu"),
    ) -> None:
        """A copper/via/pour keepout rule area -- belt-and-suspenders on top of a physical
        Edge.Cuts slot (which already makes routing across it physically impossible): a
        keepout also (a) enforces a clearance margin approaching the slot, not just at its
        exact edge, and (b) is a strong, colored, hard-to-miss visual cue in pcbnew for the
        person about to route this board, independent of whether they happen to notice a
        thin cutout in the outline."""
        layers_txt = " ".join(f'"{l}"' for l in layers)
        self.zones_text.append(
            f'\t(zone\n'
            f'\t\t(net 0)\n'
            f'\t\t(net_name "")\n'
            f'\t\t(layers {layers_txt})\n'
            f'\t\t(uuid "{uid()}")\n'
            f'\t\t(name "{_escape(name)}")\n'
            f'\t\t(hatch edge 0.5)\n'
            f'\t\t(connect_pads (clearance 0))\n'
            f'\t\t(min_thickness 0.254)\n'
            f'\t\t(filled_areas_thickness no)\n'
            f'\t\t(keepout\n'
            f'\t\t\t(tracks not_allowed)\n'
            f'\t\t\t(vias not_allowed)\n'
            f'\t\t\t(pads allowed)\n'
            f'\t\t\t(copperpour not_allowed)\n'
            f'\t\t\t(footprints allowed)\n'
            f'\t\t)\n'
            f'\t\t(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
            f'\t\t(polygon\n'
            f'\t\t\t(pts\n'
            f'\t\t\t\t(xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2})\n'
            f'\t\t\t)\n'
            f'\t\t)\n'
            f'\t)'
        )

    def silk_text(self, contents: str, x: float, y: float, size: float = 1.5, layer: str = "F.SilkS") -> None:
        """An on-board documentation note (e.g. the isolation-barrier warning silkscreened
        right next to the slot it describes)."""
        self.graphics_text.append(
            f'\t(gr_text "{_escape(contents)}"\n'
            f'\t\t(at {x} {y} 0)\n'
            f'\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{uid()}")\n'
            f'\t\t(effects (font (size {size} {size}) (thickness {size * 0.15:.3f})))\n'
            f'\t)'
        )

    # -- assembly ----------------------------------------------------------

    def render(self) -> str:
        net_lines = "\n".join(
            f'\t(net {code} "{_escape(name)}")'
            for name, code in sorted(self.net_codes.items(), key=lambda kv: kv[1])
        )
        return f"""(kicad_pcb
\t(version 20241229)
\t(generator "wl-sync-gen")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A3")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "None")
\t\t\t(dielectric_constraints no)
\t\t)
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
{net_lines}
{chr(10).join(self.footprints_text)}
{chr(10).join(self.graphics_text)}
{chr(10).join(self.zones_text)}
\t(embedded_fonts no)
)
"""
