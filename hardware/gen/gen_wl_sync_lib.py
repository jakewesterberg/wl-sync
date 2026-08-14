"""Generator for hardware/lib/wl-sync.kicad_sym -- the five custom symbols Task 6 of
docs/superpowers/plans/2026-08-13-breakout-pcb.md needs that aren't in any KiCad stock
library: the 68-pin MDR male (NI connector), mini-XLR TA4M/TA5M, the 4-pin mini-DIN
(+-12V inlet), the ACCES I/O USB-AO16-8A's mating DB37 connector, and the Raspberry Pi 5
40-pin GPIO header.

These are hand-designed from scratch (no stock symbol to extract from), so this module
writes raw s-expression text directly rather than using kicad_sch.py's extract_symbol()
-- but it follows the exact same low-level format kicad_sch.py's extract_symbol()/
unit_pins() expect to read back (see hardware/README.md's gotchas 1-2, restated in
kicad_sch.py's own module docstring): every symbol here is written BARE (`(symbol "Name"
...)`, no library prefix -- extract_symbol() adds "wl-sync:" at read time, exactly as it
does for a stock library's own bare-named entries), with child unit sub-symbols named
"<Name>_0_1" (graphics) and "<Name>_1_1" (pins), also bare. This file is a normal,
standalone .kicad_sym library, openable in KiCad's own Symbol Editor independent of any
generator -- the generator exists so the 5 symbols' pin geometry is computed from a
single per-symbol pin table (see PIN GEOMETRY below) instead of hand-typed 68 times.

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
or plan -- Task 7 (mini-DIN rail assignment), Task 8/10 (MDR68 per-pin channel
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
# 4. 4-pin mini-DIN, panel mount -- +-12V inlet. Generic pin-to-rail assignment (a
#    Task 7 decision; see module docstring). Real part: Same Sky (CUI) MD-40SN.
# ---------------------------------------------------------------------------
SYM_MINIDIN4 = build_symbol(
    "MiniDIN_4",
    "J",
    "MiniDIN_4",
    "4-pin mini-DIN, panel mount (Same Sky/CUI MD-40SN) -- analog +-12V supply inlet. "
    "Pin-to-rail assignment (+12V/-12V/GND/shield) is made where this is placed.",
    "connector mini-DIN power inlet panel",
    "https://www.sameskydevices.com/product/resource/md-sn.pdf",
    [(str(n), str(n)) for n in range(1, 5)],
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

SYMBOLS = [
    SYM_MDR68, SYM_TA4M, SYM_TA5M, SYM_MINIDIN4, SYM_ACCESIO, SYM_PI5_HEADER,
]
_SYMBOL_NAMES = [
    "MDR68_Male", "MiniXLR_TA4M", "MiniXLR_TA5M", "MiniDIN_4",
    "ACCESIO_AO16_DB37M", "RaspberryPi5_GPIO_Header",
]
_EXPECTED_PIN_COUNTS = {
    "MDR68_Male": 68, "MiniXLR_TA4M": 4, "MiniXLR_TA5M": 5, "MiniDIN_4": 4,
    "ACCESIO_AO16_DB37M": 37, "RaspberryPi5_GPIO_Header": 40,
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
