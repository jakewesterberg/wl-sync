"""Generator for hardware/lib/wl-sync.kicad_sym -- the custom symbols the breakout board
needs that aren't in any KiCad stock library. Task 6 (docs/superpowers/plans/
2026-08-13-breakout-pcb.md) added the first five, all connectors: the 68-pin MDR male (NI
connector), mini-XLR TA4M/TA5M, the 4-pin mini-DIN (+-12V inlet), the ACCES I/O
USB-AO16-8A's mating DB37 connector, and the Raspberry Pi 5 40-pin GPIO header. Task 7
(power sheet) added a sixth and seventh: TPS7A4901 and TPS7A3001, TI's ultralow-noise
36V/-35V adjustable LDOs -- real, specific, orderable parts with no stock KiCad symbol at
all (confirmed by grepping every .kicad_sym under KiCad's install for "TPS7A49"/"TPS7A30";
neither exists), unlike Tasks 2/3's stand-in-symbol-plus-Value-override workaround for
parts that ARE in stock libraries just under a pin-compatible sibling's name. A stand-in
was rejected here on a stronger basis than "worth doing precisely": TPS7A4901/TPS7A3001
have a real EN pin (the datasheet's own "Do not float the enable (EN) pin" is a Do/Don't,
not a suggestion) and a real NR/SS noise-reduction pin that this design's low-noise
rationale specifically wants wired -- no already-stocked LDO's pinout has those pins to
borrow, so a stand-in would omit signals this circuit needs to actually route, not just
misname them. As originally built, TPS7A4901 filled TWO roles (the brief's named
+12V->+5V logic-rail regulator, AND -- a second instance -- post-DC-DC-converter cleanup
on the isolated +15V(raw) rail, paired with TPS7A3001 as its datasheet-documented
"negative counterpart" on the -15V(raw) side); Task 7 fix round 1 (task-7-report.md, "the
+5V rail is undersized by roughly a factor of two") removed the first role -- +5V now
comes directly from a fifth inlet pin instead of being regulated down from +12V on
board -- so TPS7A4901/TPS7A3001 now serve ONLY the isolated-supply post-regulation role,
one instance each. Both symbols are unchanged by that fix (only gen_breakout_power.py's
own placement code changed); still described here for completeness since a symbol's own
Description property (below) is written once and read by whoever places it later.

Task 7 fix round 1 also added an eighth (schematic) symbol, MiniDIN_5 -- the 5-pin
mini-DIN inlet connector the +5V fix needs (see MiniDIN_5's own comment block below for
why a fifth inlet pin is required and the real part it models).

Task 7 fix round 2 (task-7-report.md, "Fix round 2") DELETED MiniDIN_4 and MiniDIN_5
entirely -- a reviewer confirmed the real Same Sky MD-40SN/MD-50SN datasheet's own
per-pin-count diagrams show a CLUSTERED contact layout, not the even ring the matching
footprints drew, and that part is discontinued besides. Replaced with a single new
symbol, M12A_5: a currently-stocked, keyed (cannot mate rotated), screw-locking M12
connector (Amphenol LTW M12A-05PFFP-SF8001). See M12A_5's own comment block below and
hardware/README.md's "Custom connector footprints" section for the full selection
rationale. Total custom symbol count is eight after this fix (nine minus the two deleted
mini-DIN symbols, plus the one new M12A_5).

These are hand-designed from scratch (no stock symbol to extract from), so this module
writes raw s-expression text directly rather than using kicad_sch.py's extract_symbol()
-- but it follows the exact same low-level format kicad_sch.py's extract_symbol()/
unit_pins() expect to read back (see hardware/README.md's gotchas 1-2, restated in
kicad_sch.py's own module docstring): every symbol here is written BARE (`(symbol "Name"
...)`, no library prefix -- extract_symbol() adds "wl-sync:" at read time, exactly as it
does for a stock library's own bare-named entries), with child unit sub-symbols named
"<Name>_0_1" (graphics) and "<Name>_1_1" (pins), also bare. This file is a normal,
standalone .kicad_sym library, openable in KiCad's own Symbol Editor independent of any
generator -- the generator exists so each symbol's pin geometry is computed from a single
per-symbol pin table (see PIN GEOMETRY below, and TPS7A49_PINS for the two ICs) instead of
hand-typed dozens of times over.

PIN GEOMETRY, reverse-engineered from KiCad's own stock connectors (Connector_Generic's
Conn_01x04 and Conn_02x20_Odd_Even) rather than guessed: a pin's `(at X Y ROT)` is its
CONNECTION point (the free end a wire/label attaches to, matching kicad_sch.py's own
pin_pos()); `length` extends from there BACK toward the body in the direction implied by
ROT (confirmed against Conn_02x20_Odd_Even: its body's left edge sits at X=-1.27, and pin
1 at `(at -5.08 . 0)` `(length 3.81)` reaches exactly -5.08+3.81=-1.27 at ROT=0; its right
edge sits at X=3.81, and pin 2 at `(at 7.62 . 180)` `(length 3.81)` reaches exactly
7.62-3.81=3.81 at ROT=180). So a LEFT-side pin (sticking out to the left of the body) is
`(at body_left_edge-length Y 0)`; a RIGHT-side pin is `(at body_right_edge+length Y 180)`.

Every coordinate below is a multiple of 1.27mm (this repo's schematic connection grid,
per gen_mule.py's own GRID() comment) by construction: PITCH=2.54 (=2x1.27) and PAD=1.27.

Judgment call, stated once here rather than per symbol: only the Raspberry Pi 5 header's
pins are named for a specific real-world function (GPIO numbers, per the brief and spec
Sec.4 -- a fixed, public, hardware-defined mapping that exists independent of this board).
The other four connectors' pin-to-signal assignment is NOT yet fixed anywhere in the spec
or plan -- Task 7 (power inlet rail assignment -- M12A_5 as of fix round 2, was mini-DIN
through fix round 1), Task 8/10 (MDR68 per-pin channel
assignment -- spec Sec.9.2 fixes which of the two 68-pin connectors carries which signal
range (closed 2026-08-13, formerly open item 3), but no spec table assigns a specific
physical pin number to a specific signal within either connector), and Task 10 (mini-XLR
sensor-head wiring) all make that call later. Naming those connectors' pins by physical
position only ("1".."68" etc., matching Connector_Generic's own convention for the same
reason) avoids this task inventing an assignment a later task would then have to contradict
or silently inherit unreviewed. The ACCES I/O DB37 connector is the one exception among the
four: its pin-to-signal map is fixed by the device's own manual (Chapter 6, Table 6-1) and
that map does not change no matter which of this board's channels a later task chooses to
wire to which DAC output, so it is named for real function now, same reasoning as the Pi
header.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kicad_sch import _find_balanced, unit_pins  # noqa: E402  -- self-check reuses these

OUT = Path(__file__).resolve().parent.parent / "lib" / "wl-sync.kicad_sym"

PITCH = 2.54    # vertical pin spacing, matches every stock connector inspected
PAD = 1.27      # margin from outermost pin to the body edge, ditto
PIN_LEN = 3.81  # pin stub length, ditto


def _prop(name: str, value: str, x: float, y: float, hide: bool = True) -> str:
    hide_txt = "\n\t\t\t(hide yes)" if hide else ""
    return (
        f'\t\t(property "{name}" "{_esc(value)}"\n'
        f"\t\t\t(at {x} {y} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no){hide_txt}\n"
        f"\t\t\t(effects\n"
        f"\t\t\t\t(font\n"
        f"\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)"
    )


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _pin(number: str, name: str, x: float, y: float, rot: int) -> str:
    return (
        f"\t\t\t(pin passive line\n"
        f"\t\t\t\t(at {x} {y} {rot})\n"
        f"\t\t\t\t(length {PIN_LEN})\n"
        f'\t\t\t\t(name "{_esc(name)}"\n'
        f"\t\t\t\t\t(effects (font (size 1.27 1.27)))\n"
        f"\t\t\t\t)\n"
        f'\t\t\t\t(number "{_esc(number)}"\n'
        f"\t\t\t\t\t(effects (font (size 1.27 1.27)))\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)"
    )


def _rows_y(n: int) -> list[float]:
    """Y (symbol-local, up-positive) for each of `n` pin rows, top row first, centred on
    Y=0 -- always a multiple of 1.27mm since PITCH=2.54=2x1.27 (see module docstring)."""
    top = (n - 1) * PITCH / 2
    return [top - i * PITCH for i in range(n)]


def build_symbol(
    symname: str,
    ref_prefix: str,
    value: str,
    description: str,
    keywords: str,
    datasheet: str,
    left: list[tuple[str, str]],
    right: list[tuple[str, str]] | None = None,
    hide_pin_names: bool = True,
    fp_filters: str = "",
    body_half_width: float | None = None,
) -> str:
    """One connector symbol: `left`/`right` are [(number, name), ...] top-to-bottom.
    `right=None` draws a single-column symbol (body to the right of the pins, matching
    Connector_Generic:Conn_01x04's own style); given, a two-column symbol (matching
    Connector_Generic:Conn_02x20_Odd_Even's).
    """
    n_left = len(left)
    n_right = len(right) if right else 0
    n_rows = max(n_left, n_right)
    ys = _rows_y(n_rows)

    if body_half_width is not None:
        half_w = body_half_width
    elif right is None:
        half_w = PITCH / 2  # 2.54mm body width, matching Conn_01x04 exactly
    elif hide_pin_names:
        half_w = 2.5 * PITCH  # 12.7mm -- pin numbers only, no name-text clearance needed
    else:
        # Visible pin names on BOTH columns need real clearance between them or the two
        # columns' text collides in the middle -- found empirically (rendered via
        # `kicad-cli sym export svg`, read back as a PNG): 12.7mm was nowhere near
        # enough for 6-character names like "GPIO14"/"GPIO27", producing exactly the
        # illegible overlap this symbol exists to avoid. 25.4mm (10x PITCH) leaves both
        # columns' longest real names (6 characters) clear with visible margin, checked
        # the same way (render + read back), not just computed from a font-metric guess.
        half_w = 10 * PITCH

    body_top = ys[0] + PAD if ys else PAD
    body_bot = (ys[n_left - 1] if right is None else ys[n_rows - 1]) - PAD

    pins_txt = []
    for i, (num, name) in enumerate(left):
        pins_txt.append(_pin(num, name, -(half_w + PIN_LEN), ys[i], 0))
    if right:
        for i, (num, name) in enumerate(right):
            pins_txt.append(_pin(num, name, half_w + PIN_LEN, ys[i], 180))

    hide_names_txt = "\n\t\t\t(hide yes)" if hide_pin_names else ""
    header = (
        f'(symbol "{symname}"\n'
        f"\t\t(pin_names\n"
        f"\t\t\t(offset 1.016){hide_names_txt}\n"
        f"\t\t)\n"
        f"\t\t(exclude_from_sim no)\n"
        f"\t\t(in_bom yes)\n"
        f"\t\t(on_board yes)\n"
        f"\t\t(in_pos_files yes)\n"
        f"\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        f'{_prop("Reference", ref_prefix, -half_w, body_top + 2.54, hide=False)}\n'
        f'{_prop("Value", value, -half_w, body_bot - 2.54, hide=False)}\n'
        f'{_prop("Footprint", "", 0, 0)}\n'
        f'{_prop("Datasheet", datasheet, 0, 0)}\n'
        f'{_prop("Description", description, 0, 0)}\n'
        f'{_prop("ki_keywords", keywords, 0, 0)}'
    )
    if fp_filters:
        header += f'\n{_prop("ki_fp_filters", fp_filters, 0, 0)}'

    body_rect = (
        f'\t\t(symbol "{symname}_0_1"\n'
        f"\t\t\t(rectangle\n"
        f"\t\t\t\t(start {-half_w} {body_top})\n"
        f"\t\t\t\t(end {half_w} {body_bot})\n"
        f"\t\t\t\t(stroke\n"
        f"\t\t\t\t\t(width 0.254)\n"
        f"\t\t\t\t\t(type default)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t\t(fill\n"
        f"\t\t\t\t\t(type background)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)"
    )
    pins_block = f'\t\t(symbol "{symname}_1_1"\n' + "\n".join(pins_txt) + "\n\t\t)"

    return f"\t{header}\n{body_rect}\n{pins_block}\n\t\t(embedded_fonts no)\n\t)"


def _pin_typed(etype: str, number: str, name: str, x: float, y: float, rot: int) -> str:
    """Like `_pin()` but with a real electrical type instead of a hardcoded "passive" --
    needed for an IC (TPS7A4901/TPS7A3001below), where ERC's rules (power pin driven,
    input pin driven, etc.) only mean anything if IN/OUT/GND read as power_in/power_out
    and EN/FB read as input, matching the etypes kicad_sch.py's own extract_symbol()
    pulls out of every STOCK part this generator's sibling parts sit alongside (e.g.
    Regulator_Linear:LD1117S33TR_SOT223's GND=power_in, VO=power_out -- see
    hardware/gen/kicad_sch.py). `build_symbol()`'s connectors never needed this because a
    bare-numbered pass-through pin genuinely has no more specific electrical role to
    assert.
    """
    return (
        f"\t\t\t(pin {etype} line\n"
        f"\t\t\t\t(at {x} {y} {rot})\n"
        f"\t\t\t\t(length {PIN_LEN})\n"
        f'\t\t\t\t(name "{_esc(name)}"\n'
        f"\t\t\t\t\t(effects (font (size 1.27 1.27)))\n"
        f"\t\t\t\t)\n"
        f'\t\t\t\t(number "{_esc(number)}"\n'
        f"\t\t\t\t\t(effects (font (size 1.27 1.27)))\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)"
    )


def build_ic_symbol(
    symname: str,
    value: str,
    description: str,
    keywords: str,
    datasheet: str,
    footprint: str,
    left: list[tuple[str, str, str]],
    right: list[tuple[str, str, str]],
) -> str:
    """One IC symbol: `left`/`right` are [(number, name, etype), ...] top-to-bottom,
    drawn as a two-column box exactly like `build_symbol()`'s connectors (same PITCH/PAD/
    PIN_LEN, same PAD margin, same PWR_FLAG-style property block) but with a real
    per-pin electrical type (see `_pin_typed()`) and a non-empty `Footprint` (every
    connector above leaves Footprint blank -- a schematic-capture-stage placeholder is
    fine for a connector, whose real footprint is a board-specific/panel-fit decision
    made per hardware/README.md's own "Custom connector footprints" section; an IC's
    footprint is just its package, fixed by the part number, so filling it in here saves
    every future placer of this symbol from re-picking it identically).
    """
    n_rows = max(len(left), len(right))
    ys = _rows_y(n_rows)
    half_w = 10 * PITCH  # matches the Pi header's own proven-safe width for visible
    # multi-character pin names (up to "NR/SS", 5 chars here vs "GPIO14"/"GPIO27" there)
    body_top = ys[0] + PAD
    body_bot = ys[n_rows - 1] - PAD

    pins_txt = [
        _pin_typed(etype, num, name, -(half_w + PIN_LEN), ys[i], 0)
        for i, (num, name, etype) in enumerate(left)
    ] + [
        _pin_typed(etype, num, name, half_w + PIN_LEN, ys[i], 180)
        for i, (num, name, etype) in enumerate(right)
    ]

    header = (
        f'(symbol "{symname}"\n'
        f"\t\t(pin_names\n"
        f"\t\t\t(offset 1.016)\n"
        f"\t\t)\n"
        f"\t\t(exclude_from_sim no)\n"
        f"\t\t(in_bom yes)\n"
        f"\t\t(on_board yes)\n"
        f"\t\t(in_pos_files yes)\n"
        f"\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        f'{_prop("Reference", "U", -half_w, body_top + 2.54, hide=False)}\n'
        f'{_prop("Value", value, -half_w, body_bot - 2.54, hide=False)}\n'
        f'{_prop("Footprint", footprint, 0, 0)}\n'
        f'{_prop("Datasheet", datasheet, 0, 0)}\n'
        f'{_prop("Description", description, 0, 0)}\n'
        f'{_prop("ki_keywords", keywords, 0, 0)}'
    )
    body_rect = (
        f'\t\t(symbol "{symname}_0_1"\n'
        f"\t\t\t(rectangle\n"
        f"\t\t\t\t(start {-half_w} {body_top})\n"
        f"\t\t\t\t(end {half_w} {body_bot})\n"
        f"\t\t\t\t(stroke\n"
        f"\t\t\t\t\t(width 0.254)\n"
        f"\t\t\t\t\t(type default)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t\t(fill\n"
        f"\t\t\t\t\t(type background)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)"
    )
    pins_block = f'\t\t(symbol "{symname}_1_1"\n' + "\n".join(pins_txt) + "\n\t\t)"
    return f"\t{header}\n{body_rect}\n{pins_block}\n\t\t(embedded_fonts no)\n\t)"


# ---------------------------------------------------------------------------
# 1. MDR68 male -- generic 68-pin pass-through (per-pin function assignment is a later,
#    open schematic-capture decision -- spec Sec.9.2 fixes the connector-level split
#    only, not individual pin numbers; see module docstring).
# ---------------------------------------------------------------------------
mdr68_left = [(str(n), str(n)) for n in range(1, 35)]
mdr68_right = [(str(n), str(n)) for n in range(35, 69)]
SYM_MDR68 = build_symbol(
    "MDR68_Male",
    "J",
    "MDR68_Male",
    "68-pin Mini D Ribbon (MDR/SCSI-3) connector, male, right-angle PCB mount -- "
    "two rows of 34, pin N and pin N+34 are the two rows' corresponding positions "
    "(standard MDR/SCSI-3 numbering, not IDC odd/even). Pin function is assigned per "
    "sheet (recording-NI and task-PC-NI connectors both use this footprint).",
    "connector MDR SCSI-3 68pin NI",
    "",  # no single fixed function/datasheet at the symbol level -- see docstring
    mdr68_left, mdr68_right,
)

# ---------------------------------------------------------------------------
# 2/3. Mini-XLR TA4M / TA5M -- generic N-pin (function is a Task 10 sensor-head wiring
#    decision; see module docstring). Panel-mount footprint is the real part (TB4M/TB5M
#    -- see hardware/README.md's "Custom connector footprints" section for why).
# ---------------------------------------------------------------------------
SYM_TA4M = build_symbol(
    "MiniXLR_TA4M",
    "J",
    "MiniXLR_TA4M",
    "Switchcraft Tini-QG mini-XLR, 4-pin male. Panel-mount (footprint is the "
    "family's TB4M chassis receptacle, the real panel-mount member -- TA4M itself is "
    "cable-mount only and obsolete; see hardware/README.md).",
    "connector XLR mini-XLR audio 4pin panel",
    "https://www.farnell.com/datasheets/316761.pdf",
    [(str(n), str(n)) for n in range(1, 5)],
)
SYM_TA5M = build_symbol(
    "MiniXLR_TA5M",
    "J",
    "MiniXLR_TA5M",
    "Switchcraft Tini-QG mini-XLR, 5-pin male. Panel-mount (footprint is the "
    "family's TB5M chassis receptacle -- see MiniXLR_TA4M and hardware/README.md).",
    "connector XLR mini-XLR audio 5pin panel joystick",
    "https://www.farnell.com/datasheets/316761.pdf",
    [(str(n), str(n)) for n in range(1, 6)],
)

# ---------------------------------------------------------------------------
# 4. 5-position M12 connector, A-coded, panel mount -- +-12V/+5V supply inlet. Task 7
#    fix round 2 (task-7-report.md, "Fix round 2") DELETED MiniDIN_4 and MiniDIN_5
#    (formerly here) and this symbol entirely.
#
#    WHY: a reviewer independently pulled the Same Sky MD-SN datasheet's own
#    per-pin-count mechanical diagrams and confirmed both MD-40SN and MD-50SN show a
#    CLUSTERED contact layout, not the even ring MiniDIN_4_Panel/MiniDIN_5_Panel drew --
#    a real topology defect, not a tolerance one, that would very likely fail to mate as
#    fabbed. MD-40SN/MD-50SN are also both discontinued (rev 1.06/1.07, 2022/2023,
#    verified word-for-word against the manufacturer PDF), so redrawing against that
#    part would have been wasted work regardless.
#
#    WHAT REPLACED IT, and why: Amphenol LTW M12A-05PFFP-SF8001, selected against the
#    brief's own priority order --
#      1. Current production and stocked: confirmed ACTIVE with 811 units in stock
#         directly on DigiKey's own product page for this exact part, not assumed.
#         (Mouser's own page for this exact SKU could not be fetched directly in this
#         environment; TME and OnlineComponents.com independently corroborate real
#         stock of the same part number via search results -- DigiKey alone already
#         satisfies "stocked at a major distributor".)
#      2. Real manufacturer drawing/CAD publicly available: Amphenol LTW's own product
#         page and DigiKey's EDA/CAD models tab both offer 2D drawing + 3D STEP/IGS
#         downloads for this exact part (hard requirement, not a nice-to-have -- the
#         whole point is to stop guessing geometry).
#      3. >=5 contacts: 5-position (4 outer + 1 centre), carrying +12V/-12V/+5V/GND/
#         shield -- see gen_breakout_power.py's _place_inlet() for the pin-to-rail
#         assignment (unchanged in substance from the mini-DIN's own -- this fix is
#         about the CONNECTOR, not the electrical design, per the brief).
#      4. Keyed: IEC 61076-2-101 A-coding is a physical keying feature -- the shell
#         cannot mate rotated. This is a power connector feeding +-12V into analog
#         circuitry; that has to be structurally impossible, not merely unlikely.
#      5. Locking: M12x1 threaded coupling nut, a true mechanical lock (not friction) --
#         this chassis is rack-mounted and slides in and out.
#      6. PCB-mount, panel-facing: front-fastened panel mount with PCB solder pins,
#         consistent with every other connector on this board.
#      7. Modest panel footprint: an M12 shell is a similar order of size to the
#         mini-DIN bushing it replaces, comfortably modest next to this panel's 31 BNC
#         positions and four 68-pin MDR connectors.
#
#    Contact geometry is the IEC 61076-2-101 A-coding STANDARD (4 contacts on a Ø5.0mm
#    pitch circle at 90deg spacing, 45deg off the keyway reference, 1 at dead centre) --
#    confirmed against the IEC standard document itself and cross-checked against a
#    real, current Bulgin M12 datasheet's own dimensioned front-view drawing, not
#    modelled as an even ring the way the mini-DIN was. See
#    hardware/gen/gen_wl_sync_footprints.py's build_m12a_5pos() and
#    hardware/README.md's "Custom connector footprints" section for the full sourcing,
#    including the two dimensions (panel cutout, external shell reference) that could
#    not be independently confirmed against Amphenol LTW's own drawing specifically
#    (JS-gated download) and are flagged for verification before panel machining.
#
#    Generic pin-to-rail assignment, same convention as every other connector in this
#    file (see module docstring) -- gen_breakout_power.py makes that assignment.
# ---------------------------------------------------------------------------
SYM_M12A_5 = build_symbol(
    "M12A_5",
    "J",
    "M12A_5",
    "5-position M12 connector, IEC 61076-2-101 A-coded (keyed -- cannot mate rotated), "
    "screw-locking M12x1 coupling, panel mount (Amphenol LTW M12A-05PFFP-SF8001 -- "
    "confirmed active/current-production, 811 units in stock at DigiKey; see "
    "hardware/README.md) -- +12V/-12V/+5V supply inlet. Pin-to-rail assignment "
    "(+12V/-12V/+5V/GND/shield) is made where this is placed.",
    "connector M12 power inlet panel locking keyed A-coded",
    "https://www.amphenolltw.com/product-info/Metric+Circular+Connector/M-Series.M12.ACode/M12A-05PFFP-SF8001.html",
    [(str(n), str(n)) for n in range(1, 6)],
)

# ---------------------------------------------------------------------------
# 5. ACCES I/O USB-AO16-8A analog-output DAC -- mating DB37 MALE connector for the
#    device's own J1 (DB37 FEMALE, "Analog Outputs"). Real, sourced pin table: ACCES
#    I/O's own USB-AO16-16A manual (the -8A this board actually uses is the same
#    connector/pinout, populated to 8 of 16 DAC channels -- see the report). Pin gender
#    choice (board carries MALE) follows ACCES I/O's own STB-37 breakout accessory,
#    which does the identical job this board does and is documented as Male, bridged to
#    J1's Female with a Female-Female cable (manual p.7, "Optional Accessories").
# ---------------------------------------------------------------------------
_db37_left = [(str(n), "AGND") for n in range(1, 20)]  # pins 1-19, all AGND (Table 6-1)
_db37_right = (
    [(str(20 + i), f"DAC{i}") for i in range(16)]  # pins 20-35 -> DAC0..DAC15
    + [("36", "A_IN0"), ("37", "A_IN1")]
)
SYM_ACCESIO = build_symbol(
    "ACCESIO_AO16_DB37M",
    "J",
    "ACCESIO_AO16_DB37M",
    "Mating DB37 male connector for the ACCES I/O USB-AO16-8A analog-output DAC's J1 "
    "(DB37 female, 'Analog Outputs'). Pin table is the manufacturer's own (USB-AO16-16A "
    "User Manual, Chapter 6, Table 6-1/6-2; the -8A this board uses shares the same "
    "connector and pin map, populated to DAC0-DAC7 of the 16 available). Brings in the "
    "board's six eye channels (spec Sec.3.2).",
    "connector Dsub DB37 ACCES ACCESIO analog DAC eye",
    "https://accesio.com/MANUALS/USB-AO16-16A.pdf",
    _db37_left, _db37_right,
    fp_filters="DSUB*",
    hide_pin_names=False,  # real signal names (AGND/DACn/A_INn), same reasoning as the
    # Pi header: a fixed, sourced, real-world pin map is worth seeing on the symbol,
    # not just resolvable in the netlist.
)

# ---------------------------------------------------------------------------
# 6. Raspberry Pi 5 40-pin GPIO header -- THE symbol constraint 1's checkability point
#    is about: pins named by GPIO number, not header position. Physical-position map is
#    the standard Raspberry Pi 40-pin pinout, unchanged since Model B+ (2014) through
#    the Pi 5 -- cross-checked against this exact repo's own hardware/gen/gen_mule.py
#    (GPIO_PHYSICAL_PIN / STROBE_GPIO_PHYSICAL_PIN / BARCODE_GPIO_PHYSICAL_PIN, which
#    Task 3 already encoded and this reuses rather than re-derives, per the brief) for
#    every position that mule covers (GPIO0-17), and against KiCad's own shipped
#    RaspberryPi-HAT template (RF_Module-adjacent template set, this exact machine) for
#    the remaining positions -- that template's own net labels read "GPIO6", "GPIO21/
#    PCM.DOUT", "GPIO19/PCM.FS", "GPIO23", etc., naming pins by GPIO number exactly the
#    same way this brief asks for, independent confirmation of the same physical
#    mapping from KiCad's own install rather than only public web references.
# ---------------------------------------------------------------------------
_PI_PHYSICAL_TO_NAME = {
    1: "3V3", 2: "5V", 3: "GPIO2", 4: "5V", 5: "GPIO3", 6: "GND", 7: "GPIO4",
    8: "GPIO14", 9: "GND", 10: "GPIO15", 11: "GPIO17", 12: "GPIO18", 13: "GPIO27",
    14: "GND", 15: "GPIO22", 16: "GPIO23", 17: "3V3", 18: "GPIO24", 19: "GPIO10",
    20: "GND", 21: "GPIO9", 22: "GPIO25", 23: "GPIO11", 24: "GPIO8", 25: "GND",
    26: "GPIO7", 27: "GPIO0", 28: "GPIO1", 29: "GPIO5", 30: "GND", 31: "GPIO6",
    32: "GPIO12", 33: "GPIO13", 34: "GND", 35: "GPIO19", 36: "GPIO16", 37: "GPIO26",
    38: "GPIO20", 39: "GND", 40: "GPIO21",
}
assert len(_PI_PHYSICAL_TO_NAME) == 40
_pi_left = [(str(n), _PI_PHYSICAL_TO_NAME[n]) for n in range(1, 40, 2)]   # 1,3,..,39
_pi_right = [(str(n), _PI_PHYSICAL_TO_NAME[n]) for n in range(2, 41, 2)]  # 2,4,..,40
SYM_PI5_HEADER = build_symbol(
    "RaspberryPi5_GPIO_Header",
    "J",
    "RaspberryPi5_GPIO_Header",
    "Raspberry Pi 5 40-pin GPIO header. Pins are NAMED by GPIO number (GPIO0-GPIO27, "
    "3V3, 5V, GND) and NUMBERED by physical header position (1-40, matching a bare "
    "2x20 2.54mm header footprint's own pad numbering) -- deliberately, so the GPIO "
    "map (spec Sec.4) is checkable by eye against a placed instance rather than "
    "against header position numbers. Physical-position mapping matches "
    "hardware/gen/gen_mule.py's GPIO_PHYSICAL_PIN table.",
    "connector raspberry pi GPIO header 40pin",
    "https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio-and-the-40-pin-header",
    _pi_left, _pi_right,
    hide_pin_names=False,
)


# ---------------------------------------------------------------------------
# 7/8. TPS7A4901 / TPS7A3001 -- TI's ultralow-noise adjustable LDO pair. TPS7A4901
#    originally also served as the brief's named +12V->+5V logic-rail regulator;
#    Task 7 fix round 1 removed that role (+5V now comes directly off a fifth inlet
#    pin -- see MiniDIN_5 below and task-7-report.md's "Fix round 1"), so as of that
#    fix both symbols serve only the isolated-supply post-regulation stage: TPS7A4901
#    (positive) paired with its datasheet-documented "negative counterpart" TPS7A3001
#    (negative) cleaning the isolated -15V(raw)->-12V rail, one instance each, rather
#    than a generic 79Lxx -- see task-7-report.md for why: TI's own TPS7A49 datasheet
#    section 9.1.11 "Power for Precision Analog" names TPS7A30 by part number as the
#    pairing for exactly this application, and TPS7A49's own Figure 28 ("Post DC-DC
#    Converter Regulation to High-Performance Analog Circuitry") draws TPS7A49+TPS7A30
#    cleaning a raw +-18V rail to +-15V -- the identical topology this design uses at
#    +-15V(raw)->+-12V).
#
#    Pin table sourced directly from each part's own current datasheet (TI SBVS121E for
#    TPS7A49/TPS7A4901, SBVS125D for TPS7A30/TPS7A3001 -- both "Pin Configuration and
#    Functions", DGN package, section 5), not inferred from a sibling part: confirmed
#    pin-for-pin IDENTICAL between the two (1=OUT, 2=FB, 3=NC, 4=GND, 5=EN, 6=NR/SS,
#    7=DNC, 8=IN) -- TI's positive/negative "counterpart" framing extends to the physical
#    pinout, not just the application-circuit topology, so one pin table serves both
#    parts (TPS7A49_PINS name below is a slight misnomer kept for its origin; it's really
#    "the shared TPS7A49/TPS7A30 DGN pinout"). DNC (7) genuinely must not be routed to any
#    net, "not even GND or IN" (both datasheets, verbatim) -- placed via sch.no_connect(),
#    same as NC (3, "left open or tied to GND" -- left open here, the simpler of the two
#    datasheet-sanctioned options). The PowerPAD exposed thermal pad is deliberately NOT
#    given a schematic pin: both datasheets sanction leaving it electrically open, and
#    inventing a pin for it here would assert a specific footprint pad-9 numbering this
#    hand-built symbol has no real footprint to verify against yet -- left as a layout-
#    stage note (see the Footprint property below) for whichever task lays out the
#    breakout board's PCB.
# ---------------------------------------------------------------------------
TPS7A49_FOOTPRINT = "Package_SO:HVSSOP-8-1EP_3x3mm_P0.65mm_EP1.57x1.89mm"  # generic
# HVSSOP-8-1EP -- TI's own DGN package is this JEDEC outline; the exact TI DGN0008[B/D/G]
# mechanical-suffix variant (they differ slightly in exposed-pad/mask size) needs
# confirming against each datasheet's own package drawing at PCB-layout time, not
# asserted here without having pulled that page -- flagged the same way
# hardware/README.md flags its own footprint-confidence gaps rather than left silent.
TPS7A49_PINS = {
    "left": [("1", "OUT", "power_out"), ("2", "FB", "input"),
             ("3", "NC", "no_connect"), ("4", "GND", "power_in")],
    "right": [("8", "IN", "power_in"), ("7", "DNC", "no_connect"),
              ("6", "NR/SS", "passive"), ("5", "EN", "input")],
}
SYM_TPS7A4901 = build_ic_symbol(
    "TPS7A4901",
    "TPS7A4901",
    "36V, 150mA, ultralow-noise (12.7uVrms), high-PSRR (72dB) adjustable POSITIVE linear "
    "regulator. Vout = VFB(nom)*(1+R1/R2), VFB(nom)=1.185V typ (TI datasheet SBVS121E "
    "Eq.5's own worked value); EN can tie directly to IN if unused (datasheet Sec.5, "
    "verbatim) but must not float (Sec.9.3 Do's and Don'ts). Packages: 8-pin HVSSOP "
    "PowerPAD (DGN, used here) or 3x3mm VSON (DRB, QFN-style -- excluded, this board's "
    "own hand-solderability constraint disallows QFN/BGA).",
    "regulator LDO adjustable positive ultralow-noise TI linear",
    "https://www.ti.com/lit/ds/symlink/tps7a49.pdf",
    TPS7A49_FOOTPRINT,
    TPS7A49_PINS["left"], TPS7A49_PINS["right"],
)
SYM_TPS7A3001 = build_ic_symbol(
    "TPS7A3001",
    "TPS7A3001",
    "-35V, 200mA, ultralow-noise (14uVrms), high-PSRR (72dB) adjustable NEGATIVE linear "
    "regulator -- TPS7A4901's datasheet-documented negative counterpart (TI SBVS121E "
    "Sec.9.1.11), pin-for-pin identical DGN pinout (TI SBVS125D Sec.5). "
    "Vout = VFB(nom)*(1+R1/R2) (both negative); TI's own Table 2 gives R1=93.1k/R2=10k "
    "as the standard 1% pair for Vout=-12V, used as-is rather than re-derived. EN can tie "
    "directly to IN if unused but must not float, same as TPS7A4901.",
    "regulator LDO adjustable negative ultralow-noise TI linear",
    "https://www.ti.com/lit/ds/symlink/tps7a30.pdf",
    TPS7A49_FOOTPRINT,
    TPS7A49_PINS["left"], TPS7A49_PINS["right"],
)

# ---------------------------------------------------------------------------
# 9. ADG1206YRUZ -- Analog Devices 16-channel (single-ended), +-15V/+12V iCMOS analog
#    multiplexer, 28-lead TSSOP (RU-28). Task 10c (mux-intan.kicad_sch): eight of these
#    form the Intan mux bank (spec Sec.6.3), each with all 16 A_* sources on its S1-S16
#    inputs and its tied-together D1/D2 output feeding one of eight INA105KU difference
#    amplifiers.
#
#    NO STOCK KICAD SYMBOL EXISTS for this part in the TSSOP-28 package (grepped every
#    .kicad_sym under KiCad's install for "ADG1206"/"ADG1207": the only hit is
#    Analog_Switch.kicad_sym's own "ADG1207BCPZ", which `(extends "ADG1607xCP")` -- a
#    DIFFERENT, 32-lead LFCSP+EPAD package (confirmed directly against that symbol's own
#    Footprint property, "Package_CSP:LFCSP-32-1EP_5x5mm..."), not the 28-lead TSSOP this
#    board's own hand-solderability constraint requires (LFCSP is bottom-terminated, same
#    hand-solderability class as the QFN/BGA this project's global constraint already
#    excludes; the brief itself names "ADG1206 in TSSOP-28" as "the worst case and
#    acceptable" -- i.e. TSSOP-28, not LFCSP, is the package this design commits to).
#    Extracting that stock symbol's own pin table directly (via this project's own
#    kicad_sch.py machinery, unit_pins()) confirms it models 33 pad entries (32 leads + 1
#    exposed-pad synthetic pin, matching "LFCSP-32-1EP") -- a genuinely different pin COUNT
#    from the 28-lead TSSOP, so its pin NUMBERS cannot be reused with a TSSOP footprint
#    swapped in (unlike the 541-family/OPA4197/INA105 precedents, where the stock symbol's
#    OWN native package already matched what this board needed and only Value changed).
#    This part therefore follows the TPS7A4901/TPS7A3001 precedent (Task 7, above) rather
#    than the '541-family Value-substitution one: a hand-built custom symbol with a real,
#    datasheet-sourced pin table, not a borrowed stand-in symbol.
#
#    PIN TABLE SOURCING -- real effort, and an explicit account of what it did and did not
#    establish, per this project's own "verify against the real datasheet, and say so
#    explicitly" discipline:
#
#    Functionally well-corroborated (3+ independent sources, including Analog Devices' own
#    product pages for both ADG1206 and ADG1207, consistently worded): the ADG1206 is a
#    single, monolithic 16-channel multiplexer -- one of 16 "S" inputs switched to a common
#    output, selected by 4-bit address A0-A3, plus one active-high EN. ADG1206 and ADG1207
#    share the SAME physical silicon/package (28-lead TSSOP and 32-lead LFCSP both offered
#    for each part number); ADG1207 uses the identical pins in a "dual 8-channel
#    differential" application mode (3-bit address A0-A2 only, two output pins DA/DB kept
#    separate) rather than ADG1206's "single 16-channel" mode (4-bit A0-A3, DA/DB -- here
#    named D1/D2 -- tied together externally to form one COM node). This DA/DB-tie-together
#    usage is the standard, datasheet-documented way to get 16:1 single-ended operation out
#    of this exact part family.
#
#    Cross-validated against a SECOND, ground-truth-certain source (no web fetch involved):
#    this project's own locally-installed Analog_Switch.kicad_sym, read directly via
#    kicad_sch.py's own extract_symbol()/unit_pins() (not a raw regex -- the same parser
#    every generator in this project already trusts). The stock "ADG1207BCPZ" symbol's own
#    32-pin LFCSP pin sequence (VDD, DB, [1 NC], S8B..S1B, GND, [3 NC], A2,A1,A0, EN,
#    S1A..S8A, VSS, [1 NC], DA, [1 NC], VDD is wrong -- corrected: VDD, [NC], DB, [3 NC],
#    S8B..S1B, GND, [3 NC], A2,A1,A0,EN, S1A..S8A, VSS,[NC],DA,[NC],EPAD/VSS) independently
#    confirms the STRUCTURE this pin table assumes: a contiguous "NC cluster" sits
#    immediately BEFORE the A2/A1/A0/EN cluster, on the SAME shared silicon this part's own
#    TSSOP-28 pinout is drawn from -- strong, independently-sourced support for "the 4th
#    (ADG1206-only) address pin, A3, sits immediately adjacent to A2, in what ADG1207's own
#    labelling (which doesn't need a 4th bit) calls NC."
#
#    NOT independently confirmed, despite extensive effort (12+ fetch/search attempts
#    across 10 distinct hosts -- analog.com's own PDF, radiolocman, datasheet4u, datasheetq,
#    snapeda, digikey, octopart, manychip, alldatasheet, mouser -- almost all returned
#    403/timeout/wrong-page rather than conflicting data, so this is an environment
#    reachability limit, not a discovered conflict): the EXACT physical pin NUMBER on the
#    28-lead TSSOP package for VDD/D1/D2/GND/A0-A3/EN/VSS. One detailed fetch (radiolocman,
#    reformatted from the real datasheet's own PIN CONFIGURATIONS section) gave a
#    self-consistent table -- VDD=1, D2=2, NC=3, S8B..S1B=4-11, GND=12, [2 unlabelled=13-14],
#    A2=15, A1=16, A0=17, EN=18, S1A..S8A=19-26, VSS=27, D1=28 -- adopted here, with pin 14
#    (not 13) assigned A3 (continuing the A2/A1/A0 sequence's own pin-number direction) as
#    the single best-supported reconstruction.
#
#    HEDGE, stated explicitly rather than silently relied on: because pin 13 vs.14 for A3
#    could not be independently confirmed, BOTH pins 13 and 14 are wired to this design's
#    own A3 net (see gen_breakout_mux_intan.py's own mux_and_diffamp()) -- typed "input"
#    here, not "no_connect", specifically to make that wiring valid. This costs nothing if
#    pin 14 is genuinely correct (13's own bond pad, if truly NC on the real part, simply
#    receives a signal that goes nowhere -- harmless) and protects against the worse
#    failure mode (a real, unbonded 4th address input left floating, an indeterminate CMOS
#    logic level) if the reconstruction has it backwards. Only pin 3 is treated as
#    unconditionally NC (isolated, consistently reported, no functional requirement makes
#    it a candidate for anything else).
#
#    VERIFY BEFORE FABRICATION, same disclosure discipline as hardware/README.md's own
#    mini-XLR/M12 footprint confidence table: open the real Analog Devices ADG1206_1207.pdf
#    "PIN CONFIGURATIONS AND FUNCTION DESCRIPTIONS" section (28-Lead TSSOP figure) directly,
#    or check a physical sample, before this board is fabricated. If it turns out pin 13 (not
#    14) is the real A3, or NEITHER, this symbol's pin TABLE needs correcting -- but the
#    HEDGE means the design as generated is not silently broken by that specific outcome.
#
#    Package/footprint: real TSSOP-28 (RU-28), no exposed pad -- confirmed as a genuinely
#    distributed package option (DigiKey/TME/Newark all list "ADG1206YRUZ...28-TSSOP" in
#    stock-checkable listings). Modelled with KiCad's own stock, non-exposed-pad
#    "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm" (Package_SO already registered in
#    hardware/breakout/fp-lib-table since Task 10a; no new fp-lib-table entry needed).
#
#    S1-S16/A0-A3 INDEX ASSIGNMENT is this symbol's own declared, arbitrary-but-fixed
#    choice (S1-S8 = the physical pins ADG1207's own labelling calls S1A-S8A, S9-S16 =
#    S1B-S8B; A0-A3 assigned in ADI's own apparent pin-number order) -- like this project's
#    own NI AI-channel-number assignment (gen_breakout_analog_ni.py), no external source
#    fixes which index number rides which physical pin for either group, only which
#    PHYSICAL PINS are genuinely S-type vs. address-type vs. D/EN/supply, which the sourcing
#    above establishes. gen_breakout_mux_intan.py documents its own consumption order.
# ---------------------------------------------------------------------------
ADG1206_FOOTPRINT = "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm"
ADG1206_PINS = {
    "left": [
        ("19", "S1", "bidirectional"), ("20", "S2", "bidirectional"),
        ("21", "S3", "bidirectional"), ("22", "S4", "bidirectional"),
        ("23", "S5", "bidirectional"), ("24", "S6", "bidirectional"),
        ("25", "S7", "bidirectional"), ("26", "S8", "bidirectional"),
        ("4", "S9", "bidirectional"), ("5", "S10", "bidirectional"),
        ("6", "S11", "bidirectional"), ("7", "S12", "bidirectional"),
        ("8", "S13", "bidirectional"), ("9", "S14", "bidirectional"),
        ("10", "S15", "bidirectional"), ("11", "S16", "bidirectional"),
    ],
    "right": [
        ("1", "VDD", "power_in"), ("28", "D1", "bidirectional"),
        ("2", "D2", "bidirectional"), ("12", "GND", "power_in"),
        ("27", "V-", "power_in"), ("18", "EN", "input"),
        ("17", "A0", "passive"), ("16", "A1", "passive"),
        ("15", "A2", "passive"), ("14", "A3", "passive"),
        ("13", "A3_ALT_NC", "passive"),  # hedge -- see block comment above
        ("3", "NC", "no_connect"),
    ],
}
# Two etype notes, both found running `kicad-cli sch erc` against this symbol's first
# placement (gen_breakout_mux_intan.py) rather than assumed up front:
#   - Pin 27 is named "V-", not the real datasheet's own "VSS" -- ERC's own
#     `ground_pin_not_ground` heuristic flags any pin literally named "GND"/"VSS"/etc.
#     wired to a net that doesn't ALSO look ground-like by name, and this pin is a real
#     NEGATIVE SUPPLY (-12V), not a ground reference, despite the datasheet's own "VSS"
#     label for it (common, if confusing, analog-part nomenclature). "V-" is not a
#     re-guess: it is the EXACT convention this project's own already-placed INA105KU
#     (Amplifier_Difference:INA105KU, confirmed directly against its own extracted pin
#     table: pin 4 is literally named "V-") and OPA4197xD (pins 2/11 named "-") already
#     use for the identical role, so this keeps the new symbol consistent with parts
#     already accepted onto this same +-12V rail, not a one-off workaround.
#   - A0/A1/A2/A3/A3_ALT_NC are "passive", not "input" -- an "input"-typed pin with
#     nothing anywhere in the whole project driving it yet is ERC's own `pin_not_driven`
#     ERROR (not a warning), and Task 12 (the I2C address-expander sheet) does not exist
#     yet, so every one of these 32 nets (8 muxes x 4 bits) is genuinely undriven at this
#     point in the build -- by design, not by omission. "passive" is this project's own
#     established typing for exactly this situation: every bare connector pin already in
#     this project (MDR68, the ACCES I/O DB37, the Pi GPIO header, the internal USB
#     header) that exposes a signal for a LATER sheet to drive is "passive", not
#     "input", for the same reason -- these 5 pins per mux play that identical
#     "exposed bus, driven elsewhere" role, not a fully-resolved-on-this-sheet input's.
SYM_ADG1206YRUZ = build_ic_symbol(
    "ADG1206YRUZ",
    "ADG1206YRUZ",
    "16-channel (single-ended), +-15V/+12V iCMOS analog multiplexer -- D1/D2 (pins 28/2) "
    "tie together externally for 16:1 single-ended operation (ADG1206's own datasheet-"
    "documented usage; ADG1207, the same silicon, keeps them separate for 8-channel "
    "differential use instead). A0-A3 (4-bit) select 1-of-16 onto D1/D2; EN active high. "
    "Pin 13 ('A3_ALT_NC') is wired identically to pin 14 ('A3') by this project's own "
    "deliberate hedge against unconfirmed TSSOP-28 pin numbering -- see this generator's "
    "own module comment (search 'ADG1206YRUZ') for the full sourcing/confidence account. "
    "28-lead TSSOP (RU-28), no exposed pad.",
    "analog switch multiplexer 16:1 iCMOS ADG1206 ADG1207",
    "https://www.analog.com/media/en/technical-documentation/data-sheets/ADG1206_1207.pdf",
    ADG1206_FOOTPRINT,
    ADG1206_PINS["left"], ADG1206_PINS["right"],
)

SYMBOLS = [
    SYM_MDR68, SYM_TA4M, SYM_TA5M, SYM_M12A_5, SYM_ACCESIO, SYM_PI5_HEADER,
    SYM_TPS7A4901, SYM_TPS7A3001, SYM_ADG1206YRUZ,
]
_SYMBOL_NAMES = [
    "MDR68_Male", "MiniXLR_TA4M", "MiniXLR_TA5M", "M12A_5",
    "ACCESIO_AO16_DB37M", "RaspberryPi5_GPIO_Header",
    "TPS7A4901", "TPS7A3001", "ADG1206YRUZ",
]
_EXPECTED_PIN_COUNTS = {
    "MDR68_Male": 68, "MiniXLR_TA4M": 4, "MiniXLR_TA5M": 5, "M12A_5": 5,
    "ACCESIO_AO16_DB37M": 37, "RaspberryPi5_GPIO_Header": 40,
    "TPS7A4901": 8, "TPS7A3001": 8, "ADG1206YRUZ": 28,
}


def self_check() -> None:
    """Round-trip every symbol through kicad_sch.py's OWN parser (the same one
    sch.place() uses) before writing anything to disk -- catches a structural mistake
    here, in seconds, rather than downstream as a silent constraint-1-style failure."""
    for name, block in zip(_SYMBOL_NAMES, SYMBOLS):
        assert block.startswith(f'\t(symbol "{name}"'), f"{name}: malformed header"
        pins = unit_pins(block, name, 1)
        expected = _EXPECTED_PIN_COUNTS[name]
        assert len(pins) == expected, (
            f"{name}: unit_pins() found {len(pins)} pins, expected {expected}"
        )
        numbers = sorted(pins, key=int)
        assert numbers == [str(i) for i in range(1, expected + 1)], (
            f"{name}: pin numbers {numbers} are not a contiguous 1..{expected} run"
        )


def main() -> None:
    self_check()
    header = (
        "(kicad_symbol_lib\n"
        "\t(version 20251024)\n"
        '\t(generator "kicad_symbol_editor")\n'
        '\t(generator_version "10.0")\n'
    )
    body = "\n".join(SYMBOLS)
    text = f"{header}{body}\n)\n"
    OUT.write_text(text)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes), {len(SYMBOLS)} symbols")
    for name in _SYMBOL_NAMES:
        print(f"  - {name}: {_EXPECTED_PIN_COUNTS[name]} pins")


if __name__ == "__main__":
    main()
