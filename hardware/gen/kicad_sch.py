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

    Looks for child blocks named "<bare_symname>_<unit>_<style>" (style varies: some
    stock symbols only define style 0, others only style 1 -- both carry identical pin
    geometry, so either is fine) AND "<bare_symname>_0_<style>" -- KiCad's own "common to
    all units" pseudo-unit, whose content is shown/present no matter which real unit
    (1, 2, 3, ...) is selected, merged in unconditionally the same way KiCad's GUI
    renders it. For every part used through Task 6 this merge was a no-op (unit 0 either
    doesn't exist in the library entry, or exists as pin-less body graphics only), so it
    went unnoticed; found necessary at Task 7 by Converter_DCDC_Isolated's XP Power
    IH-series (e.g. IH1215D): confirmed directly against the raw library text, its 6 pins
    split with 5 (-Vin/+Vin/-Vout/+Vout/0V) living in "IH0503D_0_0" (unit 0) and only the
    6th (NC) in "IH0503D_1_1" (unit 1) -- calling with unit=1 alone silently returned just
    the NC pin, a 5-pin-short result that would have gone undetected as "a plausible but
    wrong netlist" (this file's own constraint-1 failure mode) had it not raised KeyError
    the moment a caller tried to label a pin number this dropped. `symname` is the bare
    name (no lib prefix), matching what extract_symbol leaves on child blocks
    (constraint 2).
    """
    units = _parse_all_units(block)

    def _collect(n: int) -> dict[str, Pin]:
        prefix = f"{symname}_{n}_"
        out: dict[str, Pin] = {}
        for uname, pins in units.items():
            if uname.startswith(prefix):
                out.update({p.number: p for p in pins})
        return out

    merged = _collect(0)
    if unit != 0:
        merged.update(_collect(unit))
    assert merged, f"no pins found for {symname} unit {unit} (have: {list(units)})"
    return merged


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
    instance_path_prefix: str | None = None
    # The ancestor-path PREFIX every `(instances (project ... (path "<prefix>" ...)))`
    # block this file emits (place(), power_flag(), sheet()) carries. Left None (the
    # default), this file is treated as a hierarchy ROOT: __post_init__ below sets it to
    # `/{root_uuid}`, this file's OWN identity uuid -- correct for a root .kicad_sch,
    # confirmed against a real pcbnew-authored one (KiCad's own demos/complex_hierarchy/
    # complex_hierarchy.kicad_sch: every component placed directly on its own canvas
    # carries exactly `(path "/<that file's own uuid>" ...)`).
    #
    # A CHILD sheet (one instantiated via some OTHER file's sheet() call, e.g. this
    # project's power.kicad_sch under breakout.kicad_sch) must NOT default this way --
    # its own file-identity uuid plays no role at all in a real KiCad project's own
    # ancestor-path bookkeeping. Confirmed empirically against the same demo project's
    # ampli_ht.kicad_sch, a genuine child sheet placed TWICE (as "ampli_ht_vertical" and
    # "ampli_ht_horizontal") from complex_hierarchy.kicad_sch: every one of its own
    # components carries TWO `(path ...)` entries, one per placement --
    # `/<complex_hierarchy's root uuid>/<vertical sheet SYMBOL's own uuid>` and
    # `/<same root uuid>/<horizontal sheet SYMBOL's own uuid>` -- and NEITHER contains
    # ampli_ht.kicad_sch's own file-identity uuid anywhere. So a child sheet's builder
    # must pass this explicitly: `/{parent's real root uuid}/{the sheet SYMBOL's own
    # uuid, as placed in the parent}` -- see find_sheet_instance_path() below, which
    # computes exactly this string by reading the parent .kicad_sch's own on-disk text
    # (not trusting any in-process value from a separate generator run -- the same
    # defect class Task 3 hit with gen_mule_pcb.py's schematic cross-link uuids, fixed
    # there by reading the committed artifact rather than re-deriving in-process).
    lib_symbol_blocks: dict[str, str] = None  # type: ignore[assignment]
    body: list[str] = None  # type: ignore[assignment]
    ref_counters: dict[str, int] = None  # type: ignore[assignment]
    ref_start: dict[str, int] | None = None
    # Per-prefix SEED for ref_counters, so next_ref("J") hands out "J2" (not "J1") when a
    # SIBLING sheet already committed to disk has already used "J1" for something else
    # entirely. Left None (the default) for a hierarchy ROOT or a child sheet that is the
    # first to use a given prefix -- ref_counters then starts every prefix at 0, exactly
    # the pre-existing behaviour every generator through Task 7 relied on (gen_mule.py,
    # gen_breakout.py, gen_breakout_power.py -- none of them pass this, and none of their
    # own committed output changes because of this parameter's mere existence).
    #
    # WHY THIS EXISTS (found at Task 8, not anticipated at Task 7): every child-sheet
    # generator through Task 7 built the FIRST non-trivial sheet to coexist with another
    # one, so no two sheets' OWN reference counters had ever actually collided yet. Task
    # 8 is this project's second one (alongside power.kicad_sch), and its own next_ref()
    # calls independently mint "J1", "R1", "C1", "D1", "U1", ... starting from 1 again --
    # colliding with power.kicad_sch's OWN "J1" (a completely different physical part,
    # the M12A_5 inlet, versus task-PC-digital's own Connector 0). Confirmed empirically,
    # not theoretically: `kicad-cli sch export netlist` on the combined project prints
    # "Warning: schematic has annotation errors", and hardware/gen/check_mule_netlist.py's
    # own parse_component_values() -- shared infrastructure check_breakout_power_
    # netlist.py already depends on, and every future child-sheet checker will too --
    # hard-asserts globally unique references and raises immediately on the very first
    # duplicate. A REAL KiCad user hits the identical situation building a multi-sheet
    # project by hand and resolves it with Eeschema's own "Annotate Schematic" tool,
    # which this generator has no equivalent of; ref_start is the substitute, computed by
    # a child-sheet generator reading its already-committed SIBLING sheets' own real,
    # on-disk reference usage (find_max_refs() below) -- same "read the real committed
    # artifact, don't invent identity data" discipline instance_path_prefix already
    # established, extended from uuids to reference numbers. See
    # gen_breakout_taskpc_digital.py's own build() for the concrete usage.
    footprints: dict[str, str] = None  # type: ignore[assignment]
    instance_uuid: dict[str, str] = None  # type: ignore[assignment]
    values: dict[str, str] = None  # type: ignore[assignment]
    _sheet_page: int = 1  # page "1" is always the root sheet itself; sheet() below hands
    # out 2, 3, 4, ... to each hierarchical sheet symbol placed, matching the page numbers
    # a real KiCad-authored project assigns as sheets are added.

    def __post_init__(self):
        self.root_uuid = uid()
        self._is_hierarchy_root = self.instance_path_prefix is None
        if self.instance_path_prefix is None:
            self.instance_path_prefix = f"/{self.root_uuid}"
        self.lib_symbol_blocks = {}
        self.body = []
        self.ref_counters = dict(self.ref_start) if self.ref_start else {}
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
        dnp: bool = False,
    ) -> dict[str, Pin]:
        """Place one symbol instance (one unit) at (x, y), rotation 0.

        `dnp` (default False, unchanged behaviour for every generator that doesn't pass
        it) marks the instance "Do Not Populate" -- KiCad's own `(dnp yes)` field, read
        back by both `kicad-cli` (BOM/position-file generation excludes it) and a human
        opening the file in Eeschema (drawn with the standard DNP cross-out). Added at
        Task 10d for comparators.kicad_sch's own 4th ("populate option") channel: a
        component that is fully PRESENT in the schematic/footprint sense (real land
        pattern on the PCB, so stuffing it later is an assembly step, not a respin) but
        not stuffed by default. This is a generation-time property only -- it does not
        change unit_pins()/pin_pos() or ANY connectivity this file computes; a DNP part's
        pins are still wired exactly as instructed; only the assembly/BOM-visible flag
        differs. Whether `kicad-cli sch export netlist`/ERC treat a DNP pin any
        differently from a populated one is verified empirically per-caller (see
        check_breakout_comparators_netlist.py), not assumed here.
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
\t\t(dnp {"yes" if dnp else "no"})
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
\t\t\t\t(path "{self.instance_path_prefix}"
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
\t\t\t\t(path "{self.instance_path_prefix}"
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
\t\t\t\t(path "{self.instance_path_prefix}"
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
        # `sheet_instances` records where THIS SCREEN ITSELF sits in the hierarchy --
        # meaningful only for the project ROOT (trivially "/", page "1"). A genuine
        # nested child .kicad_sch carries NO such block at all: confirmed against
        # demos/complex_hierarchy/ampli_ht.kicad_sch (a real child sheet, used twice),
        # which has zero `(sheet_instances ...)` -- unlike its own parent
        # complex_hierarchy.kicad_sch, which has exactly one, `(path "/" (page "1"))`,
        # describing only ITSELF (not either of ampli_ht's two placements, which live
        # entirely in the PARENT's own per-sheet-symbol `(instances ...)` blocks
        # instead -- see sheet()). A child screen can be instantiated more than once at
        # different ancestor paths (this project's own power.kicad_sch is not, but the
        # demo's ampli_ht.kicad_sch is), so there is no single path that could correctly
        # describe it at the file level even in principle. Gated on the same
        # `_is_hierarchy_root` flag instance_path_prefix's default derives from -- see
        # the Sch class docstring.
        sheet_instances_block = (
            '\t(sheet_instances\n\t\t(path "/" (page "1"))\n\t)\n' if self._is_hierarchy_root else ""
        )
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
{sheet_instances_block}\t(embedded_fonts no)
)
"""


def write_project_stub(path: Path) -> None:
    path.write_text('{"board":{},"schematic":{}}\n')


# ---------------------------------------------------------------------------
# Reading hierarchy/ancestor-path information back out of an already-rendered
# .kicad_sch file -- what a CHILD sheet's own generator needs to build a correctly-
# ancestored Sch(instance_path_prefix=...) (see the class docstring above), and what
# a netlist-contract checker needs to confirm the fix actually took, independent of
# whatever the generator itself computed in-process. Reads real on-disk text only --
# no trust placed in any in-memory Sch object from a separate process run, the same
# discipline gen_mule_pcb.py's schematic-cross-link fix established (Task 3): a
# separate process invocation mints its own fresh uuids, so the only value worth
# trusting is the one actually committed to the artifact being built against.
# ---------------------------------------------------------------------------

_FILE_UUID_RE = re.compile(r'\(uuid\s+"([0-9a-fA-F-]{36})"\)')


def find_root_uuid(sch_text: str) -> str:
    """The FILE's own identity uuid -- the first `(uuid "...")` in a rendered
    .kicad_sch, emitted before lib_symbols or any placed element (see Sch.render()).
    For a project ROOT this is also the value every one of its own directly-placed
    components' `(instances (path "/<this>" ...))` should carry (confirmed against
    demos/complex_hierarchy/complex_hierarchy.kicad_sch). It is NOT what a CHILD
    sheet's own components should carry -- see find_sheet_instance_path().
    """
    m = _FILE_UUID_RE.search(sch_text)
    assert m, "no top-level (uuid \"...\") found -- not a rendered .kicad_sch file"
    return m.group(1)


_SHEET_BLOCK_RE = re.compile(r"\(sheet\n")
_SHEETFILE_PROP_RE = re.compile(r'\(property "Sheetfile" "([^"]+)"')


def find_sheet_instance_path(parent_sch_text: str, parent_root_uuid: str, child_filename: str) -> str:
    """The correct `(instances (path "..." ...))` PREFIX for every component a CHILD
    sheet places, given the PARENT (or root) .kicad_sch's own already-rendered text,
    that parent's own root uuid (find_root_uuid(parent_sch_text), passed in rather
    than re-derived here so a caller walking a >2-level hierarchy can chain calls with
    the true project root's uuid throughout, not each intermediate parent's own), and
    the child's `Sheetfile` property text exactly as the parent's own `(sheet ...)`
    block spells it (e.g. "sheets/power.kicad_sch").

    This is the read side of the fix for the self-referential-`(instances (path...))`
    defect flagged in task-7-report.md's Concerns (Sch previously always used its OWN
    root_uuid for this, correct only when a file IS the root). Confirmed against a
    real KiCad-authored file, not assumed: demos/complex_hierarchy/ampli_ht.kicad_sch
    is placed TWICE from complex_hierarchy.kicad_sch (as "ampli_ht_vertical" and
    "ampli_ht_horizontal"), and every one of its own components carries two `(path
    ...)` entries, `/<root>/<vertical sheet symbol's own uuid>` and `/<root>/<horizontal
    sheet symbol's own uuid>` -- ampli_ht.kicad_sch's OWN file-identity uuid appears in
    neither, proving only the PLACING sheet symbol's own uuid (not the child file's
    own identity) belongs in the chain.
    """
    for m in _SHEET_BLOCK_RE.finditer(parent_sch_text):
        block = _find_balanced(parent_sch_text, m.start())
        fm = _SHEETFILE_PROP_RE.search(block)
        if fm and fm.group(1) == child_filename:
            um = _FILE_UUID_RE.search(block)
            assert um, f"(sheet ...) block for Sheetfile {child_filename!r} has no (uuid ...)"
            return f"/{parent_root_uuid}/{um.group(1)}"
    raise AssertionError(
        f"no (sheet ...) block with Sheetfile {child_filename!r} found in the given parent "
        f"schematic text -- wrong parent file, or the sheet symbol hasn't been placed yet"
    )


_INSTANCES_BLOCK_RE = re.compile(r"\(instances\n")
_INSTANCE_PATH_RE = re.compile(r'\(path\s+"([^"]+)"')


def find_all_instance_paths(sch_text: str) -> list[str]:
    """Every `(instances (project ... (path "..." ...)))` PATH STRING actually present
    in a rendered .kicad_sch file -- one per placed symbol/power-flag/sheet-symbol
    instance (more than one only for a child sheet instantiated more than once by its
    own parent, e.g. the ampli_ht.kicad_sch case documented in
    find_sheet_instance_path() -- not this project's power.kicad_sch, placed once).
    Used by check_breakout_power_netlist.py to confirm, independently and from the
    actual on-disk artifact, that every path resolves to the real ancestor chain
    find_sheet_instance_path() computes -- not merely that Sch was CONSTRUCTED with
    the right instance_path_prefix (which the exported netlist itself cannot show:
    confirmed empirically that kicad-cli sch export netlist recomputes each
    component's own `sheetpath`/`tstamps` fields by walking the real `(sheet ...)`
    file-path hierarchy, never by reading this `(instances (path ...))` bookkeeping at
    all, so a self-referential path here does not show up as any difference in the
    exported netlist -- only in the raw .kicad_sch source itself, which is why this
    function reads that directly rather than extending parse_netlist()).
    """
    paths = []
    for m in _INSTANCES_BLOCK_RE.finditer(sch_text):
        block = _find_balanced(sch_text, m.start())
        paths.extend(_INSTANCE_PATH_RE.findall(block))
    return paths


_INSTANCE_REFERENCE_RE = re.compile(r'\(property "Reference" "([A-Za-z#]+?)(\d+)"')


def find_max_refs(sch_text: str) -> dict[str, int]:
    """{reference prefix: highest number already used} for every PLACED INSTANCE's own
    `(property "Reference" "...")` in a rendered .kicad_sch file -- e.g. {"J": 1, "R": 4,
    "C": 21, "D": 3, "U": 4, "NT": 1, "FB": 2, "#PWR": 7} for the real, committed
    power.kicad_sch. Used by a child-sheet generator to compute `Sch`'s own `ref_start`
    (see that class's own docstring for why) so its OWN next_ref() calls do not silently
    re-mint a reference an already-committed SIBLING sheet already used for a different
    physical part.

    The regex requires at least one trailing digit (`\\d+`), which is what excludes a
    `lib_symbols` entry's own bare-prefix default Reference (`(property "Reference" "U"
    ...)`, no digit -- every stock/custom symbol's own library-default Reference is
    exactly this bare-letter form, confirmed against every symbol used through Task 7) --
    so this only counts genuinely PLACED instances, never a library definition copied
    into the same file by ensure_lib_symbol(). Confirmed directly against
    power.kicad_sch: this returns exactly the 43 real placed instances' own maxima, not
    the handful of extra bare-prefix entries the file's own lib_symbols block also
    contains.
    """
    maxes: dict[str, int] = {}
    for prefix, num in _INSTANCE_REFERENCE_RE.findall(sch_text):
        n = int(num)
        if n > maxes.get(prefix, 0):
            maxes[prefix] = n
    return maxes


def merge_max_refs(*ref_maps: dict[str, int]) -> dict[str, int]:
    """Per-prefix maximum across MULTIPLE already-committed sibling sheets' own
    find_max_refs() results -- extends that function's "seed past one sibling" fix
    (see its own docstring, and hardware/README.md's "duplicate reference designators"
    gotcha) to "seed past however many siblings already exist". find_max_refs() itself
    only ever reads ONE sheet's text, which was exactly sufficient at Task 8 (this
    project's first two-sibling case, seeding only past power.kicad_sch) but stops being
    enough the moment a THIRD real sheet exists alongside two prior ones -- Task 9 is
    that sheet (power.kicad_sch AND taskpc-digital.kicad_sch both already committed), and
    the README's own gotcha entry already flagged this exact extension as needed "once
    Tasks 9-12" arrived, not a hypothetical.

    A prefix absent from every map passed in is simply absent from the result -- Sch's
    own ref_start already treats a missing prefix as "start at 1" (unchanged pre-existing
    behaviour), and a prefix used by only SOME siblings behaves as if the others used it
    zero times, which is exactly correct: the seed only needs to clear the highest number
    any sibling actually used, not accumulate a sum across them.
    """
    merged: dict[str, int] = {}
    for ref_map in ref_maps:
        for prefix, n in ref_map.items():
            if n > merged.get(prefix, 0):
                merged[prefix] = n
    return merged
