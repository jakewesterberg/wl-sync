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
# RETIRED 2026-08-16 (ruling R1, audit step 5): SYM_M12A_5 is deleted, not merely unused.
# The Phoenix 1551833 the design actually procures terminates in AWG 22-20 WIRE and mounts
# through the panel -- it has no PCB tails, so neither a board-mount footprint nor a
# board-mount symbol could ever have been right for it. The inlet is now a stock
# Connector:Conn_01x05_Pin on a 2.54 mm header where those flying leads land; see
# gen_breakout_power.py's own FOOTPRINT_INLET_HEADER comment and
# gen_wl_sync_footprints.py's retired section 4. The implementation is in git history.

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
#    inputs and its own single D output feeding one of eight INA105KU difference
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
#    FIX ROUND 1 (task-10c-report.md, this round) REPLACED A HEDGE WITH THE REAL
#    DATASHEET. The original committed version of this symbol could not independently
#    confirm one fact -- the physical pin for A3 -- despite 12+ fetch/search attempts
#    across 10 hosts (almost all 403/timeout/wrong-page, not conflicting data, so an
#    environment reachability limit, not a discovered discrepancy). Rather than guess
#    silently, that version wired BOTH candidate pins (13 and 14) to the same A3 net. That
#    was not a safe hedge: two different physical pins tied to one net is a short between
#    whatever those two pins actually are, and would have been a real fault, not a
#    redundancy, had pin 13 turned out to be a supply or a source pin instead of NC. This
#    fix round replaces the hedge with the real datasheet, transcribed directly rather
#    than reconstructed.
#
#    PIN TABLE, DEFINITIVE: Analog Devices ADG1206/ADG1207 Rev.0 datasheet
#    (https://www.analog.com/media/en/technical-documentation/data-sheets/
#    ADG1206_1207.pdf), Table 4 "ADG1206 Pin Function Descriptions", 28-Lead TSSOP column
#    -- every one of the 28 physical pins, transcribed verbatim, not reconstructed:
#
#      Pin  Name        Pin  Name
#      1    VDD         15   A2
#      2    NC          16   A1
#      3    NC          17   A0
#      4    S16         18   EN
#      5    S15         19   S1
#      6    S14         20   S2
#      7    S13         21   S3
#      8    S12         22   S4
#      9    S11         23   S5
#      10   S10         24   S6
#      11   S9          25   S7
#      12   GND         26   S8
#      13   NC          27   VSS
#      14   A3          28   D (drain / common output)
#
#    A3 is pin 14; pin 13 (the hedge's other candidate) is genuinely NC per this same
#    table -- confirmed, not reasoned to under uncertainty the way the pre-fix version had
#    to. Pins 2, 3, and 13 are this part's only three NC pins and carry no net anywhere in
#    this design (all three typed "no_connect" in ADG1206_PINS below;
#    check_breakout_mux_intan_netlist.py separately asserts none of the three is ever
#    wired to a net, and that no two of the 21 per-channel signal pins -- S1-S16/A0-A3/D
#    -- ever share a net on one mux instance, so a two-pins-one-net hedge of this kind
#    cannot pass silently again).
#
#    This table also corrects a second, less consequential error the hedge-era version
#    carried alongside the A3 guess: it modelled TWO drain pins (D1=28, D2=2, "tied
#    together externally for 16:1 single-ended operation", reasoning by analogy from
#    ADG1207's own separate DA/DB output pins on the SAME shared silicon). The real
#    ADG1206 -- not the ADG1207 mode it was reasoned from -- brings out only ONE drain pin
#    (D, pin 28); pin 2 is NC on this part, not a second output. There is nothing to tie
#    together; gen_breakout_mux_intan.py now labels only pin 28.
#
#    EN (pin 18), confirmed by this same table: active HIGH. Table 4's own EN description
#    reads "When this pin is low, the device is disabled and all switches are turned off.
#    When this pin is high, the Ax logic inputs determine which switch is turned on" --
#    matches this design's own EN-tied-to-+12V (always enabled, selection by address
#    only); no change needed to how this pin is used.
#
#    ADDRESS TRUTH TABLE (Table 4, A3-A2-A1-A0 read as a 4-bit binary count): 0000 selects
#    S1, 0001 selects S2, ... 1111 selects S16 -- straight binary weighting, EN high
#    throughout. Recorded here and in hardware/README.md ("ADG1206 mux address truth
#    table") for Task 12, which drives MUX{n}_A0..A3, so the software side does not have
#    to re-derive it. Which of THIS design's own 16 A_* sources rides physical S1 vs. S16
#    on every mux is a separate, board-specific fact this sheet chooses on its own
#    (gen_breakout_mux_intan.py's own ALL_16_NETS/S_PIN_NUMBERS list order) -- not part of
#    the datasheet's own truth table above.
#
#    Package/footprint: real TSSOP-28 (RU-28), no exposed pad -- confirmed as a genuinely
#    distributed package option (DigiKey/TME/Newark all list "ADG1206YRUZ...28-TSSOP" in
#    stock-checkable listings). Modelled with KiCad's own stock, non-exposed-pad
#    "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm" (Package_SO already registered in
#    hardware/breakout/fp-lib-table since Task 10a; no new fp-lib-table entry needed).
#
#    S1-S16/A0-A3/D ARE THE DATASHEET'S OWN PIN NAMES (Table 4's own "Pin Name" column)
#    for these physical pins, not this project's invention -- unlike this same paragraph's
#    pre-fix "arbitrary-but-fixed" framing, written when the physical pin behind each name
#    was not yet independently confirmed. What genuinely remains this project's OWN
#    choice, at the SHEET level rather than this symbol's: which of the 16 A_* source nets
#    rides physical S1 vs. S9 on every mux (gen_breakout_mux_intan.py's own ALL_16_NETS/
#    S_PIN_NUMBERS list-order pairing -- no datasheet fixes a board's own source
#    assignment), and, later, which of Task 12's own I2C expander output bits drives which
#    now-fixed physical address pin.
# ---------------------------------------------------------------------------
ADG1206_FOOTPRINT = "Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm"
ADG1206_PINS = {
    "left": [
        ("19", "S1", "bidirectional"), ("20", "S2", "bidirectional"),
        ("21", "S3", "bidirectional"), ("22", "S4", "bidirectional"),
        ("23", "S5", "bidirectional"), ("24", "S6", "bidirectional"),
        ("25", "S7", "bidirectional"), ("26", "S8", "bidirectional"),
        ("11", "S9", "bidirectional"), ("10", "S10", "bidirectional"),
        ("9", "S11", "bidirectional"), ("8", "S12", "bidirectional"),
        ("7", "S13", "bidirectional"), ("6", "S14", "bidirectional"),
        ("5", "S15", "bidirectional"), ("4", "S16", "bidirectional"),
    ],
    "right": [
        ("1", "VDD", "power_in"), ("2", "NC", "no_connect"),
        ("3", "NC", "no_connect"), ("12", "GND", "power_in"),
        ("27", "V-", "power_in"), ("18", "EN", "input"),
        ("17", "A0", "passive"), ("16", "A1", "passive"),
        ("15", "A2", "passive"), ("14", "A3", "passive"),
        ("13", "NC", "no_connect"), ("28", "D", "bidirectional"),
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
#   - A0/A1/A2/A3 are "passive", not "input" -- an "input"-typed pin with
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
    "16-channel (single-ended), +-15V/+12V iCMOS analog multiplexer -- D (pin 28) is the "
    "single common output selected by A0-A3 (4-bit address, pins 17/16/15/14); EN (pin "
    "18) active high enables the device, active low disables it (all switches off). "
    "Pin table is Table 4 of Analog Devices' own ADG1206/ADG1207 Rev.0 datasheet, 28-Lead "
    "TSSOP column, transcribed verbatim -- no hedge, every pin independently confirmed. "
    "Pins 2, 3, and 13 are NC (no internal connection) and carry no net on this design. "
    "See this generator's own module comment (search 'ADG1206YRUZ') for the full sourcing "
    "account and the address truth table (also in hardware/README.md). 28-lead TSSOP "
    "(RU-28), no exposed pad.",
    "analog switch multiplexer 16:1 iCMOS ADG1206 ADG1207",
    "https://www.analog.com/media/en/technical-documentation/data-sheets/ADG1206_1207.pdf",
    ADG1206_FOOTPRINT,
    ADG1206_PINS["left"], ADG1206_PINS["right"],
)

# ---------------------------------------------------------------------------
# 10/11. ACSL-6400 / ACSL-6420 -- Broadcom quad-channel, 15 MBd digital logic gate
#    optocouplers, 16-pin narrow-body SOIC. Task 11 (`opto-ni.kicad_sch`,
#    `opto-intan.kicad_sch`): the galvanic barrier between the rig-side (DGND) domain and
#    the two ephys chassis (NI_GND, INTAN_GND). This is the part class plan.md's own Task
#    11 text names as "the reference candidate" (spec's own logic-output-not-phototransistor
#    requirement, matching the mule's own validated HCPL-4661 part class -- Task 2).
#
#    NO STOCK KICAD SYMBOL EXISTS for either part (grepped the full installed
#    Isolator.kicad_sym -- 429 top-level symbols -- for "ACSL", zero matches; every
#    "quad"/"-4"-suffixed entry that DOES exist there, CNY17-4/SFH617A-4/TLP290-4/
#    TLP291-4/TLP292-4/TLP293-4/TLP627-4/VO615A-4, is a phototransistor-output family,
#    which the "not phototransistor" requirement rules out directly -- so this follows the
#    ADG1206YRUZ/TPS7A4901 precedent above: a hand-built custom symbol with a real,
#    datasheet-sourced pin table, not a borrowed stand-in.
#
#    PIN TABLES, DEFINITIVE -- Broadcom AV02-0235EN ("ACSL-6xx0 Data Sheet: Multi-Channel
#    and Bi-Directional, 15 MBd Digital Logic Gate Optocoupler", docs.broadcom.com/doc/
#    AV02-0235EN, dated August 21, 2018), fetched directly and read with `pdftotext
#    -layout`. Every physical pin below is cross-confirmed against TWO independent figures
#    within the SAME datasheet for each part -- Figure 4 (pin/functional diagram) AND
#    Figure 10 (schematic diagram) for ACSL-6400; Figure 6 (pin/functional diagram) AND
#    Figure 12 (schematic diagram) for ACSL-6420 -- and both figures agree exactly, on
#    every pin, for both parts. Truth table (both parts, per the datasheet's own "Pin
#    Description / Truth Table" page): LED ON -> output LOW; LED OFF -> output HIGH
#    (output detector IC's own two-stage amplifier feeds an open-collector Schottky-
#    clamped transistor -- VO pins below are typed "open_collector", matching this
#    project's own LM339 convention). A 0.1uF bypass capacitor is the datasheet's own
#    explicit instruction "as close as possible between the power supply pins" -- both
#    VDD/GND pairs on a bidirectional part.
#
#    ACSL-6400 ("Quad, All-in-One", device selection guide table) -- ALL FOUR CHANNELS GO
#    ONE DIRECTION, sharing ONE VDD/GND domain on the OUTPUT (detector) side:
#
#      Pin  Name       Pin  Name
#      1    ANODE1     9    GND
#      2    CATHODE1   10   VDD
#      3    ANODE2     11   VO4
#      4    CATHODE2   12   VO3
#      5    ANODE3     13   VO2
#      6    CATHODE3   14   VO1
#      7    ANODE4     15   VDD
#      8    CATHODE4   16   GND
#
#    Used on opto-ni.kicad_sch (six instances, all-in-one direction: DGND -> NI_GND, 24
#    channels) and once on opto-intan.kicad_sch (the 4 same-direction outbound channels:
#    DGND -> INTAN_GND -- strobe/barcode/RWD_CMD/RWD_DLVR).
#
#    ACSL-6420 ("Quad, Bi-Dir 2/2") -- TWO channels go ONE direction (VDD1/GND1 on the LED
#    side, VDD2/GND2 on the output side), the OTHER two go the OPPOSITE direction (VDD2/
#    GND2 on the LED side, VDD1/GND1 on the output side):
#
#      Pin  Name       Pin  Name
#      1    GND1       9    GND2
#      2    VO4        10   VO2
#      3    VO3        11   VO1
#      4    VDD1       12   VDD2
#      5    ANODE1     13   CATHODE3
#      6    CATHODE1   14   ANODE3
#      7    ANODE2     15   CATHODE4
#      8    CATHODE2   16   ANODE4
#
#    Channels 1/2 (LED on VDD1/GND1, output VO1/VO2 on VDD2/GND2) go "direction 1->2";
#    channels 3/4 (LED on VDD2/GND2, output VO3/VO4 on VDD1/GND1) go "direction 2->1".
#
#    WHY OPTO-INTAN'S SECOND PACKAGE IS ACSL-6420, NOT A SECOND ACSL-6400 -- a correction
#    found by this pin-level verification, not assumed going in. plan.md's own Task 11 text
#    (and this task's own dispatch) puts 5 outbound channels (strobe/barcode/RWD_CMD/
#    RWD_DLVR/STIM_TRIG) and 1 inbound channel (RHS_STIM_OUT, the one signal originating
#    INSIDE the Intan domain) on opto-intan -- a genuinely mixed-direction 6-channel set.
#    Four outbound channels fill one ACSL-6400 cleanly (all-in-one, single direction, exact
#    fit). The remaining 1 outbound (STIM_TRIG) + 1 inbound (RHS_STIM_OUT) channel CANNOT
#    share a second ACSL-6400: an all-in-one part's four channels all share ONE VDD/GND on
#    the OUTPUT side, so forcing one channel's isolated-side output and another channel's
#    non-isolated-side output onto the SAME physical VDD/GND pins would put a channel's
#    output on the wrong side of the isolation barrier entirely -- not a tolerance problem,
#    a topology one, the exact "plausible but wrong" failure class this project's own
#    constraint-1 gotcha warns about, just one level up (part-class choice, not pin
#    mislabeling). ACSL-6420's OWN 2/2 bidirectional split is built for precisely this:
#    channel 1 (direction 1->2) carries STIM_TRIG_BUF (LED on VDD1/GND1 = +5V/DGND, VO1 on
#    VDD2/GND2 = ISO_5V/INTAN_GND); channel 3 (direction 2->1) carries RHS_STIM_OUT (LED on
#    VDD2/GND2 = ISO_5V/INTAN_GND, VO3 on VDD1/GND1 = +5V/DGND). Channels 2 and 4 are the
#    genuine spares (one per direction) -- see gen_breakout_opto_intan.py.
#
#    Package/footprint: real, current, 16-pin narrow-body SOIC (datasheet's own "Package
#    Outline Drawings", 16-pin narrow-body, confirmed against the stock KiCad
#    "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm" footprint -- same body width/length/pitch
#    class as every other narrow SOIC on this board, 16 pads confirmed directly against
#    the .kicad_mod). No new fp-lib-table entry needed (Package_SO already registered,
#    Task 10a).
# ---------------------------------------------------------------------------
ACSL_FOOTPRINT = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
ACSL6400_DATASHEET = "https://docs.broadcom.com/doc/AV02-0235EN"
ACSL6400_PINS = {
    "left": [
        ("1", "ANODE1", "passive"), ("2", "CATHODE1", "passive"),
        ("3", "ANODE2", "passive"), ("4", "CATHODE2", "passive"),
        ("5", "ANODE3", "passive"), ("6", "CATHODE3", "passive"),
        ("7", "ANODE4", "passive"), ("8", "CATHODE4", "passive"),
    ],
    "right": [
        ("9", "GND", "power_in"), ("10", "VDD", "power_in"),
        ("11", "VO4", "open_collector"), ("12", "VO3", "open_collector"),
        ("13", "VO2", "open_collector"), ("14", "VO1", "open_collector"),
        ("15", "VDD", "power_in"), ("16", "GND", "power_in"),
    ],
}
SYM_ACSL6400 = build_ic_symbol(
    "ACSL6400",
    "ACSL-6400-00TE",
    "Broadcom quad-channel, all-in-one (single direction), 15 MBd digital logic gate "
    "optocoupler -- 16-pin narrow-body SOIC. ANODE/CATHODE 1-4 (LED, input side); VO1-4 "
    "open-collector (output side, needs an external pull-up); VDD/GND appear twice (pins "
    "9/16, 10/15), both pairs on the SAME (output-side) supply domain -- datasheet's own "
    "instruction is a 0.1uF bypass as close as possible to each pair. Truth table: LED ON "
    "-> VOx LOW; LED OFF -> VOx HIGH (via pull-up). Pin table is Broadcom AV02-0235EN's own "
    "Figure 4/Figure 10 (both agree exactly), transcribed verbatim -- see this generator's "
    "own module comment (search 'ACSL-6400') for the full sourcing account.",
    "optocoupler isolator quad logic output ACSL-6400 ACSL-6xx0",
    ACSL6400_DATASHEET,
    ACSL_FOOTPRINT,
    ACSL6400_PINS["left"], ACSL6400_PINS["right"],
)

ACSL6420_PINS = {
    "left": [
        ("1", "GND1", "power_in"), ("2", "VO4", "open_collector"),
        ("3", "VO3", "open_collector"), ("4", "VDD1", "power_in"),
        ("5", "ANODE1", "passive"), ("6", "CATHODE1", "passive"),
        ("7", "ANODE2", "passive"), ("8", "CATHODE2", "passive"),
    ],
    "right": [
        ("9", "GND2", "power_in"), ("10", "VO2", "open_collector"),
        ("11", "VO1", "open_collector"), ("12", "VDD2", "power_in"),
        ("13", "CATHODE3", "passive"), ("14", "ANODE3", "passive"),
        ("15", "CATHODE4", "passive"), ("16", "ANODE4", "passive"),
    ],
}
SYM_ACSL6420 = build_ic_symbol(
    "ACSL6420",
    "ACSL-6420-00TE",
    "Broadcom quad-channel, bi-directional 2/2, 15 MBd digital logic gate optocoupler -- "
    "16-pin narrow-body SOIC. Channels 1/2: LED (ANODE1/CATHODE1, ANODE2/CATHODE2) on "
    "VDD1/GND1, open-collector output (VO1/VO2) on VDD2/GND2 -- 'direction 1->2'. Channels "
    "3/4: LED (ANODE3/CATHODE3, ANODE4/CATHODE4) on VDD2/GND2, open-collector output "
    "(VO3/VO4) on VDD1/GND1 -- 'direction 2->1'. Same truth table and bypass-cap "
    "instruction as ACSL6400 (see that symbol's own Description), applied per VDD/GND "
    "pair. Pin table is Broadcom AV02-0235EN's own Figure 6/Figure 12 (both agree "
    "exactly), transcribed verbatim -- see this generator's own module comment (search "
    "'ACSL-6420') for the full sourcing account, including why opto-intan.kicad_sch's own "
    "mixed-direction second package needs this part and not a second ACSL6400.",
    "optocoupler isolator quad logic output bidirectional ACSL-6420 ACSL-6xx0",
    ACSL6400_DATASHEET,
    ACSL_FOOTPRINT,
    ACSL6420_PINS["left"], ACSL6420_PINS["right"],
)

def build_dual_coax_symbol(
    symname: str,
    value: str,
    description: str,
    keywords: str,
    datasheet: str,
    fp_filters: str = "",
) -> str:
    """A TWO-UNIT coaxial connector: one physical body, two independent ports.

    The first multi-unit symbol in this library, and it is multi-unit for a structural
    reason rather than a drafting one. Finding F6 replaces 31 single panel-mount BNCs with
    16 dual right-angle bodies (Amphenol 031-6575). A dual is one purchased part, one
    reference designator and one footprint, but two ports that are wired in two different
    places on a sheet -- which is exactly what KiCad units are for.

    EACH UNIT IS GEOMETRICALLY IDENTICAL TO STOCK Connector:Conn_Coaxial -- pin at
    (-5.08, 0) rot 0 for the centre conductor, (0, -5.08) rot 90 for the shell, read
    directly out of the stock symbol before writing this. That is deliberate: every
    generator that places a BNC computes its label positions from `pin_pos()`, so keeping
    the per-unit geometry identical means the 31 existing placements keep landing their
    labels exactly where they already do, and this becomes a symbol swap rather than a
    re-layout of five sheets.

    Pin map, matching wl-sync.pretty/BNC_Dual_RA_Isolated.kicad_mod pad for pad:

        unit 1 (port A)   pin 1 = centre conductor   pin 2 = shell
        unit 2 (port B)   pin 3 = centre conductor   pin 4 = shell

    All four are independent, because the 031-6575's shells are isolated from the panel AND
    from each other. Ten channels on this board route a shield per connector; commoning the
    shells would defeat that silently. See check_footprint_geometry.py's verify_bnc_dual().
    """
    units = [(1, "1", "2", "A"), (2, "3", "4", "B")]
    header = (
        f'(symbol "{symname}"\n'
        f"\t\t(pin_names\n"
        f"\t\t\t(offset 1.016)\n"
        f"\t\t\t(hide yes)\n"
        f"\t\t)\n"
        f"\t\t(exclude_from_sim no)\n"
        f"\t\t(in_bom yes)\n"
        f"\t\t(on_board yes)\n"
        f"\t\t(in_pos_files yes)\n"
        f"\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        f'{_prop("Reference", "J", -2.54, 5.08, hide=False)}\n'
        f'{_prop("Value", value, -2.54, -7.62, hide=False)}\n'
        f'{_prop("Footprint", "", 0, 0)}\n'
        f'{_prop("Datasheet", datasheet, 0, 0)}\n'
        f'{_prop("Description", description, 0, 0)}\n'
        f'{_prop("ki_keywords", keywords, 0, 0)}'
    )
    if fp_filters:
        header += f'\n{_prop("ki_fp_filters", fp_filters, 0, 0)}'

    blocks = []
    for unit, centre_num, shell_num, port in units:
        body = (
            f"\t\t\t(circle\n"
            f"\t\t\t\t(center 0 0)\n"
            f"\t\t\t\t(radius 2.54)\n"
            f"\t\t\t\t(stroke (width 0.254) (type default))\n"
            f"\t\t\t\t(fill (type none))\n"
            f"\t\t\t)\n"
            f"\t\t\t(circle\n"
            f"\t\t\t\t(center 0 0)\n"
            f"\t\t\t\t(radius 0.508)\n"
            f"\t\t\t\t(stroke (width 0.254) (type default))\n"
            f"\t\t\t\t(fill (type outline))\n"
            f"\t\t\t)\n"
            f"\t\t\t(polyline\n"
            f"\t\t\t\t(pts (xy -2.54 0) (xy -0.508 0))\n"
            f"\t\t\t\t(stroke (width 0.254) (type default))\n"
            f"\t\t\t\t(fill (type none))\n"
            f"\t\t\t)\n"
            f"\t\t\t(polyline\n"
            f"\t\t\t\t(pts (xy 0 -2.54) (xy 0 -1.27))\n"
            f"\t\t\t\t(stroke (width 0.254) (type default))\n"
            f"\t\t\t\t(fill (type none))\n"
            f"\t\t\t)"
        )
        pins = "\n".join([
            _pin(centre_num, f"{port}_In", -5.08, 0, 0),
            _pin(shell_num, f"{port}_Ext", 0, -5.08, 90),
        ])
        blocks.append(f'\t\t(symbol "{symname}_{unit}_1"\n{body}\n{pins}\n\t\t)')

    return f"\t{header}\n" + "\n".join(blocks) + "\n\t\t(embedded_fonts no)\n\t)"


SYM_BNC_DUAL = build_dual_coax_symbol(
    "BNC_Dual_RA_Isolated",
    "BNC_Dual_RA_Isolated",
    "Dual-port right-angle BNC jack, each shell independently isolated from the panel and "
    "from its neighbour (Amphenol RF 031-6575). Two units, one body: unit 1 = port A "
    "(pin 1 centre, pin 2 shell), unit 2 = port B (pin 3 centre, pin 4 shell). "
    "Signal-to-net assignment is made where this is placed, the same convention every "
    "other connector in this library uses. Replaces the panel-mount vertical BNC whose "
    "axis pointed at the chassis lid -- see parametric-audit.md finding F6.",
    "BNC coaxial dual right-angle isolated panel connector",
    "https://www.amphenolrf.com/en-us/part/031-6575/2397/",
    fp_filters="BNC_Dual_RA_Isolated",
)


SYMBOLS = [
    SYM_MDR68, SYM_TA4M, SYM_TA5M, SYM_ACCESIO, SYM_PI5_HEADER,
    SYM_TPS7A4901, SYM_TPS7A3001, SYM_ADG1206YRUZ, SYM_ACSL6400, SYM_ACSL6420,
    SYM_BNC_DUAL,
]
_SYMBOL_NAMES = [
    "MDR68_Male", "MiniXLR_TA4M", "MiniXLR_TA5M",
    "ACCESIO_AO16_DB37M", "RaspberryPi5_GPIO_Header",
    "TPS7A4901", "TPS7A3001", "ADG1206YRUZ", "ACSL6400", "ACSL6420",
    "BNC_Dual_RA_Isolated",
]
# {name: pins per unit, in unit order}. Single-unit parts carry a one-entry list, so the
# self-check below has one shape to reason about rather than two.
#
# M12A_5 IS GONE (ruling R1, audit step 5): the Phoenix 1551833 the design procures is a
# panel-mount part with flying leads, so the board carries a plain 5-way header and neither
# the custom symbol nor the custom footprint has anything left to describe. See
# gen_wl_sync_footprints.py's retired section 4.
_EXPECTED_PIN_COUNTS = {
    "MDR68_Male": [68], "MiniXLR_TA4M": [4], "MiniXLR_TA5M": [5],
    "ACCESIO_AO16_DB37M": [37], "RaspberryPi5_GPIO_Header": [40],
    "TPS7A4901": [8], "TPS7A3001": [8], "ADG1206YRUZ": [28],
    "ACSL6400": [16], "ACSL6420": [16],
    "BNC_Dual_RA_Isolated": [2, 2],
}


def self_check() -> None:
    """Round-trip every symbol through kicad_sch.py's OWN parser (the same one
    sch.place() uses) before writing anything to disk -- catches a structural mistake
    here, in seconds, rather than downstream as a silent constraint-1-style failure.

    Multi-unit aware as of audit step 5: pin numbers must form a contiguous 1..N run across
    ALL units taken together, with no number appearing in two units. A dual connector whose
    second unit repeated pins 1-2 would place two ports on one pair of pads -- exactly the
    silent shell-commoning failure the dual BNC exists to avoid, one layer up."""
    for name, block in zip(_SYMBOL_NAMES, SYMBOLS):
        assert block.startswith(f'\t(symbol "{name}"'), f"{name}: malformed header"
        expected_per_unit = _EXPECTED_PIN_COUNTS[name]
        seen: dict[str, int] = {}
        for unit_index, expected in enumerate(expected_per_unit, start=1):
            pins = unit_pins(block, name, unit_index)
            assert len(pins) == expected, (
                f"{name}: unit {unit_index} has {len(pins)} pins, expected {expected}"
            )
            for num in pins:
                assert num not in seen, (
                    f"{name}: pin {num!r} appears in unit {seen[num]} AND unit "
                    f"{unit_index} -- two units sharing a pad"
                )
                seen[num] = unit_index
        total = sum(expected_per_unit)
        numbers = sorted(seen, key=int)
        assert numbers == [str(i) for i in range(1, total + 1)], (
            f"{name}: pin numbers {numbers} are not a contiguous 1..{total} run across "
            f"{len(expected_per_unit)} unit(s)"
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
