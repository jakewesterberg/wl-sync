"""Reusable helpers for generating KiCad 10 .kicad_sch files programmatically.

Scaled up from the throwaway feasibility probe at
.superpowers/sdd/2026-08-13-breakout-pcb/f3-probe-reference.py, which established the core
technique: place symbol instances on a grid, and attach a global label at each pin's
connection point so connectivity is by label name rather than by wire geometry. Computing
wire routes between components is the part that does not scale; labels avoid it entirely
and still produce a real, GUI-editable schematic.

Three KiCad format constraints, each found the hard way (see hardware/README.md):

1. ``lib_symbols`` entries must be keyed by the full lib id (``Device:R``, not ``R``).
   Keyed bare, KiCad loads the file but silently fails to resolve pins: they collapse to
   the symbol origin as ``[???, ?]`` and both pins of a two-pin part merge into one net.
   It does not error -- it produces a plausible, wrong netlist.
2. Child unit symbols inside that block must keep bare names (``R_0_1``, not
   ``Device:R_0_1``). Prefixing them makes KiCad refuse the file ("Failed to load
   schematic").
3. A ``sym-lib-table`` must sit directly beside the ``.kicad_pro`` -- KiCad does not search
   parent directories.

A fourth constraint found while building this generator, not in the original probe: some
stock symbols (e.g. ``74xx:74HCT541``, ``Regulator_Linear:LD1117S33TR_SOT223``,
``Converter_DCDC_Isolated:TMA-0512D``) use ``(extends "ParentName")`` and carry no pins of
their own -- their graphics/pins live entirely on a *different* symbol. Tasks 2 and 3
sidestepped this entirely by always extracting the extends-free root symbol (the one that
actually carries pin geometry) and overriding its Value property to the real ordered part
number (see gen_mule.py for which root symbol backs which real part) and left resolving it
properly as an open question: "it is not yet known whether KiCad accepts a lib_symbols entry
embedding the extends chain the way it does when the GUI places such a part."

Resolved at Task 6, empirically, and it is NOT simply "embed both the parent and the child
verbatim": that was tried first, keying both by full lib id exactly like every other symbol
here (constraint 1) and leaving the child's own bare ``(extends "74LS541")`` untouched.
Result, confirmed with a real ``kicad-cli sch erc`` + netlist export round-trip: every pin
of the placed symbol reports ``label_dangling`` and the exported netlist carries zero nodes
for it -- the *lib_symbols* entry has zero resolvable pins, independent of whether the
project's own sym-lib-table also has a real, resolvable entry for the parent library (tried
both ways; identical failure). KiCad's GUI does not merely copy the child's text when it
places a derived symbol -- it flattens parent and child into one self-contained block at
placement time, and only a schematic file already containing that flattened form resolves.
``extract_symbol()`` below does the same flattening this generator's way: the parent's full
unit geometry (pins and graphics, its child sub-symbols renamed from the parent's bare name
to the child's) merged with the child's own top-level property overrides (Reference
default position aside, every derived symbol checked overrides exactly and only Value,
Footprint, Datasheet, Description, ki_keywords, ki_fp_filters -- nothing else varies, which
is what makes flattening this generically safe rather than special-cased per part). A
symbol using ``extends`` can now be placed directly, by its own real name and library, with
no stand-in Value substitution needed.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

SYMDIR = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
# This repo's own custom symbols (hardware/lib/wl-sync.kicad_sym) -- searched after the
# stock KiCad directory, so a local library name can never accidentally shadow a stock
# one. Added at Task 6 (the first task to introduce a non-stock library); every stock-only
# lookup Tasks 2/3 relied on is unaffected since none of those library names exist here.
LOCAL_SYMDIR = Path(__file__).resolve().parent.parent / "lib"


def uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Symbol extraction and pin parsing
# ---------------------------------------------------------------------------


def _find_balanced(text: str, start: int) -> str:
    """Return the balanced-paren s-expression block starting at `start`."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unbalanced parens")


_SYMLIB_CACHE: dict[str, str] = {}
_EXTENDS_RE = re.compile(r'\(extends\s+"([^"]+)"\)')
_PROPERTY_NAME_RE = re.compile(r'\(property\s+"([^"]+)"')


def _symlib_path(libname: str) -> Path:
    for root in (SYMDIR, LOCAL_SYMDIR):
        candidate = root / f"{libname}.kicad_sym"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"{libname}.kicad_sym not found in {SYMDIR} or {LOCAL_SYMDIR}"
    )


def _header(block: str) -> str:
    """The portion of a `(symbol "Name" ...)` block before its first nested child-unit
    `(symbol "Name_U_S" ...)` -- exactly where top-level attributes like `(extends ...)`
    and top-level `(property ...)` blocks live, as opposed to unit graphics/pins."""
    child_start = block.find('\n\t\t(symbol "')
    return block if child_start == -1 else block[:child_start]


def _top_properties(block: str) -> dict[str, str]:
    """{property_name: full "(property ...)" block text} for every TOP-LEVEL property
    (i.e. within `_header()`, not reaching into nested unit sub-symbols)."""
    header = _header(block)
    out: dict[str, str] = {}
    for m in re.finditer(r'\(property\s+"', header):
        pblock = _find_balanced(header, m.start())
        name = _PROPERTY_NAME_RE.match(pblock).group(1)
        out[name] = pblock
    return out


def _flatten_extends(libname: str, symname: str, child_block: str, parent_name: str) -> str:
    """Resolve `(extends "parent_name")` by merging the parent's full unit geometry with
    the child's own top-level property overrides -- see module docstring, constraint 4,
    for why this is necessary (a live extends reference embedded in a generated file does
    not resolve) and what "merging" means precisely. Returns a block keyed
    "libname:symname" with no `extends` remaining, safe to treat exactly like any other
    `ensure_lib_symbol()` result.
    """
    parent_full = extract_symbol(libname, parent_name)  # keyed "libname:parent_name"
    parent_bare = parent_full.replace(
        f'(symbol "{libname}:{parent_name}"', f'(symbol "{parent_name}"', 1
    )
    parent_props = _top_properties(parent_bare)
    child_props = _top_properties(child_block)

    flat = parent_bare.replace(f'(symbol "{parent_name}"', f'(symbol "{libname}:{symname}"', 1)
    for pname, ptext in child_props.items():
        assert pname in parent_props, (
            f"{libname}:{symname}: child overrides property {pname!r} that its parent "
            f"{parent_name!r} does not itself define -- every derived symbol checked "
            f"while writing this overrides a strict subset of the parent's own "
            f"properties (Reference/Value/Footprint/Datasheet/Description/ki_keywords/"
            f"ki_fp_filters); this one doesn't fit that pattern and needs a real "
            f"insertion point worked out, not a silent drop."
        )
        flat = flat.replace(parent_props[pname], ptext, 1)

    # The parent's own child unit sub-symbols (e.g. "74LS541_0_1", "74LS541_1_1") keep
    # the PARENT's bare name -- rename them to the symbol actually being placed so
    # unit_pins()'s f"{symname}_{unit}_" lookup finds them (constraint 2: still bare,
    # just re-based).
    flat = re.sub(
        rf'\(symbol "{re.escape(parent_name)}_(\d+_\d+)"',
        lambda m: f'(symbol "{symname}_{m.group(1)}"',
        flat,
    )
    return flat


def extract_symbol(libname: str, symname: str) -> str:
    """Pull one symbol's full definition out of a .kicad_sym library (stock KiCad first,
    then this repo's own hardware/lib/ -- see LOCAL_SYMDIR).

    Renames only the PARENT entry to "libname:symname" (lib_symbols must be keyed by
    full lib id -- constraint 1). Nested child-unit symbols keep their bare names
    (constraint 2) because this function only rewrites the opening tag, not the body.

    If the symbol uses `(extends ...)`, returns a flattened, self-contained, extends-free
    block instead (see module docstring, constraint 4, and `_flatten_extends()`) rather
    than raising -- callers no longer need to pick the root symbol it extends by hand.
    """
    if libname not in _SYMLIB_CACHE:
        _SYMLIB_CACHE[libname] = _symlib_path(libname).read_text()
    text = _SYMLIB_CACHE[libname]
    start = text.find(f'(symbol "{symname}"')
    assert start != -1, f"{libname}:{symname} not found"
    block = _find_balanced(text, start)
    m = _EXTENDS_RE.search(_header(block))
    if m:
        return _flatten_extends(libname, symname, block, m.group(1))
    return block.replace(f'(symbol "{symname}"', f'(symbol "{libname}:{symname}"', 1)


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    x: float
    y: float
    rot: float
    etype: str


_PIN_RE = re.compile(
    r'\(pin\s+(?P<etype>\w+)\s+(?P<gstyle>\w+)\s*'
    r'\(at\s+(?P<x>-?[\d.]+)\s+(?P<y>-?[\d.]+)\s+(?P<rot>-?[\d.]+)\)'
    r'(?:\s*\(length\s+(?P<length>-?[\d.]+)\))?'
    r'.*?\(name\s+"(?P<name>[^"]*)"'
    r'.*?\(number\s+"(?P<number>[^"]*)"',
    re.DOTALL,
)
_SUBSYMBOL_RE = re.compile(r'\(symbol\s+"([^"]+)"')


def _parse_all_units(block: str) -> dict[str, list[Pin]]:
    """Split a lib_symbols block into child `(symbol "Name_u_s" ...)` units -> pins."""
    units: dict[str, list[Pin]] = {}
    for m in _SUBSYMBOL_RE.finditer(block):
        uname = m.group(1)
        ublock = _find_balanced(block, m.start())
        pins = [
            Pin(
                number=pm.group("number"),
                name=pm.group("name"),
                x=float(pm.group("x")),
                y=float(pm.group("y")),
                rot=float(pm.group("rot")),
                etype=pm.group("etype"),
            )
            for pm in _PIN_RE.finditer(ublock)
        ]
        if pins:
            units[uname] = pins
    return units


def unit_pins(block: str, symname: str, unit: int) -> dict[str, Pin]:
    """Return {pin_number: Pin} for one unit of a symbol, keyed off pin NUMBER.

    Looks for a child block named "<bare_symname>_<unit>_<style>" (style varies: some
    stock symbols only define style 0, others only style 1 -- both carry identical pin
    geometry, so either is fine). `symname` is the bare name (no lib prefix), matching
    what extract_symbol leaves on child blocks (constraint 2).
    """
    units = _parse_all_units(block)
    prefix = f"{symname}_{unit}_"
    for uname, pins in units.items():
        if uname.startswith(prefix):
            return {p.number: p for p in pins}
    raise AssertionError(f"no unit {unit} found for {symname} (have: {list(units)})")


# ---------------------------------------------------------------------------
# Placement / coordinate transform
# ---------------------------------------------------------------------------
# Confirmed by the probe: symbol-library-internal Y is up-positive (standard cartesian),
# schematic-sheet Y is down-positive. A symbol placed at (x, y) rotation 0 puts a pin at
# local-offset (lx, ly) at schematic position (x + lx, y - ly). This generator places
# every instance at rotation 0 -- never rotated -- so this single transform is all that's
# needed anywhere in the file.


def pin_pos(place_x: float, place_y: float, pin: Pin) -> tuple[float, float]:
    return (place_x + pin.x, place_y - pin.y)


# ---------------------------------------------------------------------------
# Schematic element renderers
# ---------------------------------------------------------------------------


@dataclass
class Sch:
    """Accumulates the pieces of a .kicad_sch file as it's built."""

    project: str
    root_uuid: str = None  # type: ignore[assignment]
    lib_symbol_blocks: dict[str, str] = None  # type: ignore[assignment]
    body: list[str] = None  # type: ignore[assignment]
    ref_counters: dict[str, int] = None  # type: ignore[assignment]
    footprints: dict[str, str] = None  # type: ignore[assignment]
    instance_uuid: dict[str, str] = None  # type: ignore[assignment]
    values: dict[str, str] = None  # type: ignore[assignment]
    _sheet_page: int = 1  # page "1" is always the root sheet itself; sheet() below hands
    # out 2, 3, 4, ... to each hierarchical sheet symbol placed, matching the page numbers
    # a real KiCad-authored project assigns as sheets are added.

    def __post_init__(self):
        self.root_uuid = uid()
        self.lib_symbol_blocks = {}
        self.body = []
        self.ref_counters = {}
        # ref -> footprint lib id, ref -> this symbol instance's own uuid, and ref -> Value
        # text, recorded as a side effect of place() so a companion PCB generator
        # (hardware/gen/gen_mule_pcb.py, using hardware/gen/kicad_pcb.py) can consume them
        # as the single source of truth for "which real footprint does this reference use",
        # "which schematic symbol instance does this PCB footprint correspond to" (the
        # `path` field pcbnew's own "Update PCB from Schematic" reads), and "what value
        # should the PCB footprint display" -- instead of hand-maintaining a second,
        # driftable copy of any of the three.
        self.footprints = {}
        self.instance_uuid = {}
        self.values = {}

    def next_ref(self, prefix: str) -> str:
        n = self.ref_counters.get(prefix, 0) + 1
        self.ref_counters[prefix] = n
        return f"{prefix}{n}"

    def ensure_lib_symbol(self, libname: str, symname: str) -> str:
        """Register libname:symname in lib_symbols (once). Returns the full lib id."""
        lib_id = f"{libname}:{symname}"
        if lib_id not in self.lib_symbol_blocks:
            self.lib_symbol_blocks[lib_id] = extract_symbol(libname, symname)
        return lib_id

    # -- placement -----------------------------------------------------

    def place(
        self,
        libname: str,
        symname: str,
        ref: str,
        value: str,
        x: float,
        y: float,
        unit: int = 1,
        footprint: str = "",
        extra_props: dict[str, str] | None = None,
    ) -> dict[str, Pin]:
        """Place one symbol instance (one unit) at (x, y), rotation 0.

        Returns {pin_number: Pin} in SYMBOL-LOCAL coordinates (not yet transformed) so
        callers can compute schematic-space label positions via pin_pos().
        """
        lib_id = self.ensure_lib_symbol(libname, symname)
        block = self.lib_symbol_blocks[lib_id]
        pins = unit_pins(block, symname, unit)

        pin_lines = []
        for num in pins:
            pin_lines.append(f'\t\t(pin "{num}" (uuid "{uid()}"))')

        extra = ""
        if extra_props:
            for pname, pval in extra_props.items():
                extra += (
                    f'\n\t\t(property "{pname}" "{pval}"\n'
                    f"\t\t\t(at {x} {y} 0)\n"
                    f"\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n"
                    f"\t\t)"
                )

        instance_uuid = uid()
        self.footprints[ref] = footprint
        self.instance_uuid[ref] = instance_uuid
        self.values[ref] = value
        self.body.append(
            f"""\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} 0)
\t\t(unit {unit})
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{instance_uuid}")
\t\t(property "Reference" "{ref}"
\t\t\t(at {x + 2.032} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "{value}"
\t\t\t(at {x + 2.032} {y + 2.54} 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Footprint" "{footprint}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t){extra}
{chr(10).join(pin_lines)}
\t\t(instances
\t\t\t(project "{self.project}"
\t\t\t\t(path "/{self.root_uuid}"
\t\t\t\t\t(reference "{ref}") (unit {unit})
\t\t\t\t)
\t\t\t)
\t\t)
\t)"""
        )
        return pins

    # -- labels / flags / no-connects -----------------------------------

    def label(self, name: str, x: float, y: float, shape: str = "bidirectional") -> None:
        self.body.append(
            f"""\t(global_label "{name}"
\t\t(shape {shape})
\t\t(at {x} {y} 0)
\t\t(effects (font (size 1.27 1.27)) (justify left))
\t\t(uuid "{uid()}")
\t\t(property "Intersheetrefs" "${{INTERSHEET_REFS}}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t)"""
        )

    def power_flag(self, net: str, x: float, y: float) -> None:
        """Assert that `net` is driven, via a PWR_FLAG (power_out pin) + label.

        Needed because stock KiCad power symbols (+5V, +3V3, GND, ...) are themselves
        typed power_in, not power_out -- ERC's "power pin not driven" rule requires an
        actual power_out (or PWR_FLAG) pin somewhere on a power-input net. Only call this
        for a net that doesn't already have a genuine power_out pin from some other part
        (a regulator or converter output, say): ERC's `pin_to_pin` rule separately flags
        two power_out pins connected together as an error, so a PWR_FLAG on a net that's
        already driven isn't safety margin, it trips a different check. Callers decide
        per-net which nets qualify (see gen_mule.py).
        """
        ref = self.next_ref("#PWR")
        lib_id = self.ensure_lib_symbol("power", "PWR_FLAG")
        block = self.lib_symbol_blocks[lib_id]
        # PWR_FLAG's only defined body is unit 0 (style 0), not unit 1 -- unlike every
        # other symbol used in this generator. Confirmed via inspection at generation
        # time, not guessed: unit_pins() raises loudly if this ever stops being true.
        pins = unit_pins(block, "PWR_FLAG", 0)
        assert list(pins.keys()) == ["1"]
        self.body.append(
            f"""\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom no)
\t\t(on_board no)
\t\t(dnp no)
\t\t(uuid "{uid()}")
\t\t(property "Reference" "{ref}"
\t\t\t(at {x} {y - 3} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Value" "PWR_FLAG"
\t\t\t(at {x} {y - 5} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(pin "1" (uuid "{uid()}"))
\t\t(instances
\t\t\t(project "{self.project}"
\t\t\t\t(path "/{self.root_uuid}"
\t\t\t\t\t(reference "{ref}") (unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)"""
        )
        self.label(net, x, y, shape="input")

    def no_connect(self, x: float, y: float) -> None:
        self.body.append(f'\t(no_connect\n\t\t(at {x} {y})\n\t\t(uuid "{uid()}")\n\t)')

    def text(self, contents: str, x: float, y: float, size: float = 1.27) -> None:
        """An on-sheet documentation note (not a label -- carries no electrical meaning).

        `contents` must be a single line: a raw newline byte inside the quoted string
        makes KiCad refuse to load the file outright ("Failed to load schematic") -- a
        fifth format constraint found building this generator, isolated by testing a
        multi-line call against one with literal parentheses but no newline (which loads
        fine), so it's specifically the embedded newline, not just "unusual characters in
        general". Call this once per line for a multi-line note (see hardware/README.md).
        """
        assert "\n" not in contents, (
            "Sch.text() contents must be single-line -- a raw newline inside the quoted "
            "string makes KiCad refuse to load the file. Call text() once per line instead."
        )
        escaped = contents.replace("\\", "\\\\").replace('"', '\\"')
        self.body.append(
            f"""\t(text "{escaped}"
\t\t(at {x} {y} 0)
\t\t(effects (font (size {size} {size})))
\t\t(uuid "{uid()}")
\t)"""
        )

    def sheet(
        self, name: str, filename: str, x: float, y: float,
        w: float = 76.2, h: float = 38.1,
    ) -> str:
        """Place one hierarchical sheet symbol -- a reference to a CHILD .kicad_sch file
        (`filename`, resolved relative to this file's own directory by KiCad), not a
        component instance. `(x, y)` is the box's TOP-LEFT corner, matching KiCad's own
        `(at X Y)` semantics for a sheet (confirmed against a real pcbnew-authored file:
        demos/complex_hierarchy's ampli_ht_vertical sheet sits at `(at 71.12 111.76)`
        `(size 50.8 36.83)` with its "Sheetname" property text baseline at Y=110.9975,
        i.e. 0.7625mm *above* the box's own top edge, and "Sheetfile" at Y=149.2001, i.e.
        0.6101mm *below* the bottom edge at 111.76+36.83=148.59 -- both reproduced here at
        a clean, round 1.27mm gap instead of KiCad's own auto-placement fractions, which
        carry no meaning beyond "GUI text auto-placed at this specific font size").

        Emits zero `(pin ...)` entries: a childless/pin-less sheet symbol is valid KiCad
        -- the same demo's ampli_ht_vertical/_horizontal sheets carry none either, since
        their child file (ampli_ht.kicad_sch) exposes no hierarchical labels for the
        parent to expose pins for. Exactly what an empty placeholder sheet needs; a later
        task that adds hierarchical labels to the child file adds matching `(pin ...)`
        entries here too, at that point.

        The child file itself does NOT need to exist on disk for `kicad-cli sch erc` to
        pass: confirmed empirically against this exact generator's output (a `(sheet
        ...)` referencing a missing file reports zero violations; kicad-cli silently
        treats it as an empty sheet). So this method does not create the child file --
        matching the plan's own division of labour, where each later task's brief lists
        its own child sheet file as something IT creates, not Task 6.

        Returns the new sheet symbol's own uuid.
        """
        self._sheet_page += 1
        page = str(self._sheet_page)
        sheet_uuid = uid()
        name_y = y - 1.27
        file_y = y + h + 1.27
        self.body.append(
            f"""\t(sheet
\t\t(at {x} {y})
\t\t(size {w} {h})
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(stroke
\t\t\t(width 0.1524)
\t\t\t(type solid)
\t\t)
\t\t(fill
\t\t\t(color 0 0 0 0.0000)
\t\t)
\t\t(uuid "{sheet_uuid}")
\t\t(property "Sheetname" "{name}"
\t\t\t(at {x} {name_y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left bottom))
\t\t)
\t\t(property "Sheetfile" "{filename}"
\t\t\t(at {x} {file_y} 0)
\t\t\t(effects (font (size 1.27 1.27)) (justify left top))
\t\t)
\t\t(instances
\t\t\t(project "{self.project}"
\t\t\t\t(path "/{self.root_uuid}"
\t\t\t\t\t(page "{page}")
\t\t\t\t)
\t\t\t)
\t\t)
\t)"""
        )
        return sheet_uuid

    # -- assembly --------------------------------------------------------

    def render(self, paper: str = "A2") -> str:
        lib_symbols_text = "\n".join(self.lib_symbol_blocks.values())
        return f"""(kicad_sch
\t(version 20231120)
\t(generator "wl-sync-gen")
\t(generator_version "8.0")
\t(uuid "{self.root_uuid}")
\t(paper "{paper}")
\t(lib_symbols
{lib_symbols_text}
\t)
{chr(10).join(self.body)}
\t(sheet_instances
\t\t(path "/" (page "1"))
\t)
\t(embedded_fonts no)
)
"""


def write_project_stub(path: Path) -> None:
    path.write_text('{"board":{},"schematic":{}}\n')
