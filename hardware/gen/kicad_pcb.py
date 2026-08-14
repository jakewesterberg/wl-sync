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
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

FPROOT = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")


def uid() -> str:
    return str(uuid.uuid4())


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
    ) -> None:
        """Place one footprint instance at (x, y), rotated `rot` degrees.

        pad_nets: {pad_number: net_name}. A pad number absent from this dict gets no net
        at all -- a real, intentionally-unconnected pad (an unused connector position, a
        barrel jack's power-detect switch terminal), not an omission (see module
        docstring, constraint 3).
        """
        pad_nets = pad_nets or {}
        lib_id = f"{libname}:{modname}"
        block = extract_footprint(libname, modname)
        children = _split_children(block)

        # Find the courtyard's local bounding box so the Reference silkscreen text can be
        # placed just outside the part's own body/pads instead of at a fixed (0,0) that
        # sits on top of pad 1 for most two- and three-pad parts -- confirmed against a
        # real `kicad-cli pcb drc` run: (0,0,0) for every part produced ~230
        # silk_over_copper/silk_overlap warnings, almost all "Reference field of X
        # overlaps pad N of X" on X's own pads.
        crtyd_xs: list[float] = []
        crtyd_ys: list[float] = []
        for child in children:
            if _child_tag(child) not in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
                continue
            if '"F.CrtYd"' not in child and '"B.CrtYd"' not in child:
                continue
            for m in _COORD_RE.finditer(child):
                crtyd_xs.append(float(m.group(1)))
                crtyd_ys.append(float(m.group(2)))
        if crtyd_xs:
            ref_x = (min(crtyd_xs) + max(crtyd_xs)) / 2
            ref_y = min(crtyd_ys) - 0.8
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
                fields = f'\n\t\t(uuid "{uid()}")'
                if num in pad_nets:
                    net_name = pad_nets[num]
                    code = self.net_code(net_name)
                    fields = f'\n\t\t(net {code} "{_escape(net_name)}")' + fields
                child = _inject_before_close(child, fields)
            elif tag in _UUID_TAGS:
                child = _inject_before_close(child, f'\n\t\t(uuid "{uid()}")')
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
        props = (
            f'\t\t(property "Reference" "{ref}"\n'
            f'\t\t\t(at {ref_x} {ref_y} 0)\n'
            f'\t\t\t(layer "F.SilkS")\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 0.8 0.8) (thickness 0.13)))\n'
            f'\t\t)\n'
            f'\t\t(property "Value" "{_escape(value)}"\n'
            f'\t\t\t(at 0 0 0)\n'
            f'\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 0.9 0.9) (thickness 0.15)))\n'
            f'\t\t)\n'
            f'\t\t(property "Datasheet" ""\n'
            f'\t\t\t(at 0 0 0)\n'
            f'\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(hide yes)\n'
            f'\t\t\t(uuid "{uid()}")\n'
            f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
            f'\t\t)\n'
            f'\t\t(property "Description" ""\n'
            f'\t\t\t(at 0 0 0)\n'
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
