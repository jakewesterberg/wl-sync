"""Parse hardware/breakout's exported netlist (and, for the instance-path check the
exported netlist provably cannot answer -- see below, the raw .kicad_sch sources) and
verify the sync-module interface sheet's contract nets are electrically correct -- not
just that ERC was silent.

Why this exists, not just ERC (same reasoning check_mule_netlist.py's,
check_breakout_power_netlist.py's, and check_taskpc_digital_netlist.py's own docstrings
give, restated because it is the reason THIS file exists too): gen_breakout_pi_interface.py's
own lib_symbols entries are keyed by full lib id, which resolves correctly -- but "resolves
correctly" is proven empirically per run, not guaranteed by construction for every future
edit. Keyed bare, or with a mismatched/incomplete symbol, KiCad loads the file without error
and silently produces a plausible, wrong netlist. So ERC passing is necessary but not
sufficient.

THE central risk this file exists to catch, named explicitly by this task's own brief: a
WRONG GPIO assignment. gen_breakout_pi_interface.py places wl-sync:RaspberryPi5_GPIO_Header,
a symbol whose pins are NAMED by GPIO number (gen_wl_sync_lib.py's own SYM_PI5_HEADER
docstring), so the GENERATOR itself cannot transpose two GPIOs' physical positions by a
hand-transcription slip the way gen_mule.py's generic, position-only-named header could --
but `kicad-cli sch export netlist`'s own `pinfunction` field is always "{name}_{number}",
never the bare name (hardware/README.md's own gotcha), and the EXPORTED NETLIST this
checker reads only carries the bare NUMBER on each `(node (ref ...) (pin ...))` entry. So a
future edit to this sheet -- or to GPIO_PHYSICAL_PIN in EITHER this file or the generator --
that silently transposes which physical pin a GPIO's own signal lands on would be
INVISIBLE to ERC (a swapped pin number is still a valid, fully-connected net -- "0 errors")
and would only surface on a bench, wrong, during PIO capture bring-up. `_walk_gpio_pin()`
below is the check that exists specifically to catch this, independent of whatever
gen_breakout_pi_interface.py's own generation-time bookkeeping believes -- it re-derives the
GPIO->physical-pin table from spec Sec.4 / the standard Raspberry Pi 40-pin pinout, from
scratch, rather than importing gen_breakout_pi_interface.py's own GPIO_PIN_SPEC (a checker
that trusted the generator's own list would only be checking the generator against itself,
same discipline every other checker in this project already follows for its own CONTRACT_NETS).

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_pi_interface_netlist.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1
with a description of the first failure otherwise.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mule_netlist import (  # noqa: E402
    CheckFailure,
    Node,
    check,
    parse_component_values,
    parse_netlist,
)
from check_row_pitch_guard import (  # noqa: E402
    check_row_pitch_exceeds_2pin_span,
    self_test_row_pitch,
)
from kicad_sch import (  # noqa: E402
    find_all_instance_paths,
    find_root_uuid,
    find_sheet_instance_path,
)

DEFAULT_NET_PATH = Path("/tmp/breakout.net")
DEFAULT_BREAKOUT_SCH = Path(__file__).resolve().parent.parent / "breakout" / "breakout.kicad_sch"
DEFAULT_PI_INTERFACE_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "pi-interface.kicad_sch"
PI_INTERFACE_SHEETFILE = "sheets/pi-interface.kicad_sch"  # exactly as breakout.kicad_sch's
# own Sheetfile property spells it -- must match gen_breakout_pi_interface.py's own constant.

# ---------------------------------------------------------------------------
# GPIO -> physical header pin, redefined here from spec Sec.4 / the standard Raspberry Pi
# 40-pin pinout (unchanged since Model B+, 2014, through the Pi 5/CM5 IO Board -- spec
# Sec.9.4's own "identical BCM2712 and RP1... PIO, the GPIO numbering... are identical
# either way") -- NOT imported from gen_breakout_pi_interface.py's own GPIO_PIN_SPEC, same
# "a checker that trusted the generator would only check the generator against itself"
# discipline every other checker in this project already follows. Cross-checked three
# independent ways before use, none of them "trust the generator": (1) against this
# project's own gen_mule.py GPIO_PHYSICAL_PIN/STROBE_GPIO_PHYSICAL_PIN/
# BARCODE_GPIO_PHYSICAL_PIN for every position that table covers (GPIO0-17); (2) against
# gen_wl_sync_lib.py's own _PI_PHYSICAL_TO_NAME (the symbol library generator, a DIFFERENT
# file from gen_breakout_pi_interface.py, inverted from physical-position-keyed to
# GPIO-keyed) for the full GPIO0-27 range; (3) against the real, exported netlist produced
# from the actually-committed pi-interface.kicad_sch (every value below reproduces what
# that export showed on first generation, transcribed from the checked artifact, not
# copied from either source file blind).
GPIO_PHYSICAL_PIN = {
    0: 27, 1: 28, 2: 3, 3: 5, 4: 7, 5: 29, 6: 31, 7: 26,
    8: 24, 9: 21, 10: 19, 11: 23, 12: 32, 13: 33, 14: 8, 15: 10,
    16: 36, 17: 11, 18: 12, 19: 35, 20: 38, 21: 40, 22: 15, 23: 16,
    24: 18, 25: 22, 26: 37, 27: 13,
}
assert set(GPIO_PHYSICAL_PIN) == set(range(28))
assert len(set(GPIO_PHYSICAL_PIN.values())) == 28  # every physical pin used at most once

GND_PHYSICAL_PINS = (6, 9, 14, 20, 25, 30, 34, 39)  # matches gen_mule.py's own
# PI_GND_PHYSICAL_PINS -- the standard Pi 40-pin header's 8 real GND positions.
V3V3_PHYSICAL_PINS = (1, 17)
V5_PHYSICAL_PINS = (2, 4)
assert (
    set(GPIO_PHYSICAL_PIN.values()) | set(GND_PHYSICAL_PINS)
    | set(V3V3_PHYSICAL_PINS) | set(V5_PHYSICAL_PINS)
) == set(range(1, 41))  # every one of the header's 40 physical pins accounted for exactly once

# The net landing AT the physical header pin for each GPIO -- redefined here from this
# task's own net contract + spec Sec.4's GPIO map, same independence discipline as
# GPIO_PHYSICAL_PIN above. For GPIO0/1 and GPIO23 this is a LOCAL name (the resistor/
# level-shifter's own downstream node), not the contract net itself -- see
# gen_breakout_pi_interface.py's own GPIO_PIN_SPEC docstring for why.
GPIO_NET_AT_PIN = {
    0: "GPIO0_HDR", 1: "GPIO1_HDR",
    **{i: f"EVT_D{i}_PI" for i in range(2, 16)},
    16: "EVT_STROBE_PI",
    17: "BARCODE_RAW", 18: "CAM_TRIG_EYE_RAW", 19: "CAM_TRIG_BEH_RAW",
    20: "PD1_COMP", 21: "PD2_COMP", 22: "RWD_CMD", 23: "RWD_DLVR_PI",
    24: "STIM_TRIG", 25: "ACC_TRIG",
    # 26, 27: spare, no net -- deliberately absent from this dict; SPARE_GPIOS below.
}
SPARE_GPIOS = (26, 27)
assert set(GPIO_NET_AT_PIN) | set(SPARE_GPIOS) == set(range(28))

# This task's own net contract ("consumes ... from Task 8" / "Produces").
CONSUMED_CONTRACT_NETS = [f"EVT_D{i}_PI" for i in range(16)] + [
    "EVT_STROBE_PI", "RWD_CMD", "RWD_DLVR", "STIM_TRIG", "PD1_COMP", "PD2_COMP", "ACC_TRIG",
]
assert len(CONSUMED_CONTRACT_NETS) == 23
PRODUCED_CONTRACT_NETS = [
    "BARCODE_PI", "CAM_TRIG_EYE", "CAM_TRIG_BEH",
    # The two dedicated optocoupler legs, added with the "one LED per driver pin"
    # fix -- see gen_breakout_pi_interface.py's own BARCODE_OPTO_LEGS.
    "BARCODE_BUF", "BARCODE_INTAN_BUF",
]


def _pins_on(nets: dict[str, list[Node]], net: str) -> set[tuple[str, str]]:
    return {(n.ref, n.pin) for n in nets.get(net, [])}


def _header_pin_net_map(nets: dict[str, list[Node]], header_ref: str) -> dict[str, str]:
    """{physical pin number -> net name} for every pin belonging to `header_ref`, built
    by scanning every net in the parsed netlist (the header's own pins are scattered
    across dozens of distinct nets, one label each -- there is no single "(nets ...)"
    section for one component the way parse_netlist()'s own per-net grouping works). A
    genuinely no-connected pin (the 4 power pins -- see module docstring, "3V3/5V") still
    appears here: kicad-cli's own netlist exporter synthesizes an
    "unconnected-(<ref>-<pin name>-Pad<n>)" net name for it (confirmed directly against
    the real exported netlist, not assumed), rather than omitting the pin entirely.
    """
    out: dict[str, list[str]] = {}
    for net_name, node_list in nets.items():
        for n in node_list:
            if n.ref == header_ref:
                out.setdefault(n.pin, []).append(net_name)
    for pin, net_names in out.items():
        check(
            len(net_names) == 1,
            f"{header_ref} pin {pin} appears on {len(net_names)} DIFFERENT nets "
            f"{net_names} -- a single physical pin cannot legitimately be on more than "
            f"one net (a short, or a parsing bug)",
        )
    return {pin: net_names[0] for pin, net_names in out.items()}


def _find_header_ref(nets: dict[str, list[Node]]) -> str:
    """The RaspberryPi5_GPIO_Header instance's own reference, found by walking a net that
    can ONLY have a header pin on it besides Task 8's own driver (EVT_D2_PI: Task 8's
    SN74LVC541APW tri-state output + this sheet's own header pin, nothing else) and
    taking the "J"-prefixed one -- NOT by filtering on Value text (this symbol is placed
    with a human-readable description as its Value, e.g. "Sync-module 40-pin GPIO
    header...", same convention every connector in this project's own generators uses,
    not the bare symbol name "RaspberryPi5_GPIO_Header" -- confirmed empirically against
    the real exported netlist, not assumed). Same "identify by walking a known net +
    reference prefix" technique check_taskpc_digital_netlist.py's own conn0_ref/conn1_ref
    discovery already uses for the identical reason (MDR68 is placed with a descriptive
    Value too).
    """
    check("EVT_D2_PI" in nets, "missing net: 'EVT_D2_PI' (needed to locate the header reference)")
    j_refs = {n.ref for n in nets["EVT_D2_PI"] if n.ref.startswith("J")}
    check(len(j_refs) == 1, f"EVT_D2_PI: expected exactly 1 J-prefixed (header) node, found {j_refs}")
    return next(iter(j_refs))


def _walk_gpio_pin(
    nets: dict[str, list[Node]], header_ref: str, gpio: int, net_name: str,
) -> None:
    """THE check this task's own brief asks for: confirm GPIO{gpio}'s own contract/local
    net lands on the header's REAL physical pin (GPIO_PHYSICAL_PIN[gpio]) and nowhere
    else -- see module docstring for why this is invisible to ERC and specifically named
    as this task's own central risk.
    """
    expected_pin = str(GPIO_PHYSICAL_PIN[gpio])
    check(net_name in nets, f"GPIO{gpio}: missing net {net_name!r}")
    hdr_nodes = [n for n in nets[net_name] if n.ref == header_ref]
    check(
        len(hdr_nodes) == 1,
        f"GPIO{gpio} ({net_name}): expected exactly 1 node belonging to the header "
        f"{header_ref}, found {len(hdr_nodes)}: {hdr_nodes}",
    )
    check(
        hdr_nodes[0].pin == expected_pin,
        f"GPIO{gpio}: net {net_name!r} lands on header pin {hdr_nodes[0].pin}, expected "
        f"pin {expected_pin} (GPIO{gpio}'s real physical position per spec Sec.4 / the "
        f"standard Raspberry Pi 40-pin pinout) -- a wrong GPIO assignment: this would "
        f"pass ERC and export a fully-connected netlist, and only fail on a bench during "
        f"PIO capture bring-up",
    )


def _walk_buffered_channel(
    nets: dict[str, list[Node]], values: dict[str, str], raw_net: str, out_net: str, buf_value: str,
) -> str:
    """Walk one 74x541-family buffered channel end to end: `raw_net` (this sheet's own
    local pre-buffer node) -> the SAME reference's own input pin -> that reference's own
    OUTPUT pin (Ai+Yi=20, the 74x541 family's fixed pairing every other checker in this
    project already relies on) -> `out_net`. Returns the buffer's own reference for the
    caller's aggregate checks (same-package confirmation, right-part-in-right-role).

    WALKED OUTPUT-FIRST, deliberately. The obvious direction (find the ONE buffer input
    pin on `raw_net`, then look for its partner output) stopped working when BARCODE_RAW
    gained two more channels off the same package -- BARCODE_BUF and BARCODE_INTAN_BUF,
    the dedicated optocoupler legs that keep one LED per driver pin (see
    gen_breakout_pi_interface.py's own BARCODE_OPTO_LEGS). A source feeding several
    parallel buffered copies is legitimate and is the established idiom on this board
    (taskpc-digital's own *_CLAMP nodes have fed two independent buffer banks since Task
    8). An OUTPUT net, by contrast, must have exactly one driver -- so anchoring the walk
    there keeps the check exact and one-to-one without caring how many channels share the
    input. Every guarantee is unchanged: same physical package at both ends, Ai+Yi=20.
    """
    check(raw_net in nets, f"missing net: {raw_net!r}")
    check(out_net in nets, f"missing net: {out_net!r}")
    out_nodes = [
        n for n in nets[out_net]
        if values.get(n.ref) == buf_value and "tri_state" in n.pintype
    ]
    check(
        len(out_nodes) == 1,
        f"{out_net}: expected exactly 1 {buf_value} tri_state output pin driving this "
        f"net, found {[(n.ref, n.pin, values.get(n.ref)) for n in nets[out_net]]}",
    )
    ref, y_pin = out_nodes[0].ref, int(out_nodes[0].pin)
    a_pin = 20 - y_pin

    on_raw = [n for n in nets[raw_net] if n.ref == ref and n.pin == str(a_pin)]
    check(
        len(on_raw) == 1,
        f"{raw_net}->{out_net}: {ref} drives {out_net} from output pin {y_pin}, so by the "
        f"74x541 family's fixed Ai<->Yi pairing (input+output must equal 20) its partner "
        f"input pin {a_pin} must sit on {raw_net} -- it does not. "
        f"This channel has been permuted within the buffer, or is fed from a "
        f"different signal than it should be. Nodes on {raw_net}: "
        f"{[(n.ref, n.pin) for n in nets[raw_net]]}",
    )
    return ref


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    """Run the full contract check. Returns human-readable summary lines on success;
    raises CheckFailure with a specific, localized message on the first violation.
    """
    summary = []
    header_ref = _find_header_ref(nets)
    check(values.get(header_ref, "").startswith("Sync-module 40-pin GPIO header"), f"{header_ref}: unexpected Value {values.get(header_ref)!r}, doesn't look like the sync-module header")

    # --- Every one of the 40 header pins accounted for: 28 GPIO (walked to their real
    # physical position -- the central check this task's own brief asks for), 8 GND (to
    # DGND, at their own real physical positions), 4 power (genuinely no-connect, not
    # bridged to this board's own +3V3/+5V -- see gen_breakout_pi_interface.py's own
    # module docstring for why: the sync module has its own independent supply). ---
    for gpio, net_name in GPIO_NET_AT_PIN.items():
        _walk_gpio_pin(nets, header_ref, gpio, net_name)
    summary.append(
        f"All {len(GPIO_NET_AT_PIN)} wired GPIO pins land on their real physical header "
        f"position (spec Sec.4's own GPIO map, independently re-derived in this checker, "
        f"not imported from the generator)."
    )

    pin_map = _header_pin_net_map(nets, header_ref)
    for gpio in SPARE_GPIOS:
        pin = str(GPIO_PHYSICAL_PIN[gpio])
        check(
            pin_map.get(pin, "").startswith("unconnected-"),
            f"GPIO{gpio} (spare, physical pin {pin}): expected no-connect, found "
            f"{pin_map.get(pin)!r} -- a spare pin has been wired to something",
        )
    for pin in GND_PHYSICAL_PINS:
        check(
            pin_map.get(str(pin)) == "DGND",
            f"header physical pin {pin}: expected DGND (one of the 8 real GND "
            f"positions), found {pin_map.get(str(pin))!r}",
        )
    for pin in V3V3_PHYSICAL_PINS + V5_PHYSICAL_PINS:
        check(
            pin_map.get(str(pin), "").startswith("unconnected-"),
            f"header physical pin {pin} (3V3/5V): expected no-connect (the sync module "
            f"has its own independent supply, spec Sec.8 -- bridging this would parallel "
            f"two regulated supplies), found {pin_map.get(str(pin))!r}",
        )
    summary.append(
        f"All 8 GND pins land on DGND at their real physical positions {GND_PHYSICAL_PINS}; "
        f"all 4 power pins ({V3V3_PHYSICAL_PINS + V5_PHYSICAL_PINS}) and both spare GPIOs "
        f"{SPARE_GPIOS} are genuinely no-connect."
    )

    # --- GPIO0/GPIO1 series resistors: 330R each, bridging the received EVT_D{0,1}_PI
    # contract net to the LOCAL header-pin net -- boot-contention mitigation (spec Sec.4). ---
    for gpio, contract_net in ((0, "EVT_D0_PI"), (1, "EVT_D1_PI")):
        hdr_net = GPIO_NET_AT_PIN[gpio]
        check(contract_net in nets, f"missing net: {contract_net!r}")
        r_on_contract = [n for n in nets[contract_net] if n.ref.startswith("R")]
        r_on_hdr = [n for n in nets[hdr_net] if n.ref.startswith("R")]
        check(
            len(r_on_contract) == 1 and len(r_on_hdr) == 1 and r_on_contract[0].ref == r_on_hdr[0].ref,
            f"GPIO{gpio}: expected exactly 1 resistor bridging {contract_net!r}<->{hdr_net!r}, "
            f"found on {contract_net}: {r_on_contract}, on {hdr_net}: {r_on_hdr}",
        )
        r_ref = r_on_contract[0].ref
        check(
            values.get(r_ref) == "330",
            f"GPIO{gpio}: series resistor {r_ref} should be '330' (ohms), found {values.get(r_ref)!r}",
        )
    summary.append("GPIO0/GPIO1 each carry a dedicated 330R series resistor between the received EVT_D{0,1}_PI net and the physical header pin.")

    # --- RWD_DLVR level shift: one SN74LVC541APW channel, RWD_DLVR (+5V, received from
    # Task 8) -> RWD_DLVR_PI (lands on GPIO23) -- see gen_breakout_pi_interface.py's own
    # module docstring for the full derivation (plan.md's global constraint: "Pi GPIO is
    # 3.3V and not 5V tolerant... never one part for both"). ---
    lvl_ref = _walk_buffered_channel(nets, values, "RWD_DLVR", "RWD_DLVR_PI", "SN74LVC541APW")
    check(
        values.get(lvl_ref) == "SN74LVC541APW",
        f"{lvl_ref}: expected Value 'SN74LVC541APW' (the RWD_DLVR level shifter), found {values.get(lvl_ref)!r}",
    )
    on_3v3 = [n for n in nets.get("+3V3", []) if n.ref == lvl_ref]
    on_5v = [n for n in nets.get("+5V", []) if n.ref == lvl_ref]
    check(len(on_3v3) == 1, f"{lvl_ref} (RWD_DLVR level shifter): expected a pin on +3V3, found {on_3v3}")
    check(
        not on_5v,
        f"{lvl_ref} (RWD_DLVR level shifter): has a pin on +5V -- it must be powered "
        f"from +3V3 ONLY, or it cannot level-shift the 5V-domain RWD_DLVR down to a "
        f"sync-module-safe level at all",
    )
    summary.append(
        f"RWD_DLVR level shift confirmed: {lvl_ref} (SN74LVC541APW, powered from +3V3 "
        f"only) bridges RWD_DLVR (received at +5V from Task 8) to RWD_DLVR_PI, which "
        f"lands on GPIO23's real physical pin."
    )

    # --- Trigger buffer: BARCODE_RAW/CAM_TRIG_EYE_RAW/CAM_TRIG_BEH_RAW -> the SAME
    # SN74AHCT541PW package's own 3 channels -> BARCODE_PI/CAM_TRIG_EYE/CAM_TRIG_BEH. ---
    barcode_buf = _walk_buffered_channel(nets, values, "BARCODE_RAW", "BARCODE_PI", "SN74AHCT541PW")
    eye_buf = _walk_buffered_channel(nets, values, "CAM_TRIG_EYE_RAW", "CAM_TRIG_EYE", "SN74AHCT541PW")
    beh_buf = _walk_buffered_channel(nets, values, "CAM_TRIG_BEH_RAW", "CAM_TRIG_BEH", "SN74AHCT541PW")
    # The two dedicated optocoupler legs -- BARCODE_RAW's own second and third buffered
    # copies (gen_breakout_pi_interface.py's own BARCODE_OPTO_LEGS). Walked exactly like
    # the three above, from the SAME input net and the SAME package, because that is the
    # whole point: a parallel buffered copy of one signal, not a re-derived one.
    barcode_ni_buf = _walk_buffered_channel(nets, values, "BARCODE_RAW", "BARCODE_BUF", "SN74AHCT541PW")
    barcode_intan_buf = _walk_buffered_channel(nets, values, "BARCODE_RAW", "BARCODE_INTAN_BUF", "SN74AHCT541PW")
    check(
        barcode_buf == eye_buf == beh_buf == barcode_ni_buf == barcode_intan_buf,
        f"BARCODE_PI/CAM_TRIG_EYE/CAM_TRIG_BEH/BARCODE_BUF/BARCODE_INTAN_BUF should share "
        f"the SAME physical SN74AHCT541PW package, found {barcode_buf}/{eye_buf}/{beh_buf}/"
        f"{barcode_ni_buf}/{barcode_intan_buf}",
    )
    trig_buf = barcode_buf
    # Five DISTINCT channels of that one package -- not, say, two names accidentally
    # labelled onto one output pin (which would put both LEDs back on one driver pin,
    # the exact defect BARCODE_OPTO_LEGS exists to fix).
    barcode_out_pins = {
        out_net: next(int(n.pin) for n in nets[out_net] if n.ref == trig_buf and "tri_state" in n.pintype)
        for out_net in ("BARCODE_PI", "CAM_TRIG_EYE", "CAM_TRIG_BEH", "BARCODE_BUF", "BARCODE_INTAN_BUF")
    }
    check(
        len(set(barcode_out_pins.values())) == 5,
        f"{trig_buf}: expected 5 DISTINCT output pins across "
        f"BARCODE_PI/CAM_TRIG_EYE/CAM_TRIG_BEH/BARCODE_BUF/BARCODE_INTAN_BUF, found "
        f"{barcode_out_pins} -- two contract nets sharing one output pin would put both "
        f"optocoupler LEDs back on a single driver pin",
    )
    on_5v = [n for n in nets.get("+5V", []) if n.ref == trig_buf]
    check(len(on_5v) == 1, f"{trig_buf} (trigger buffer): expected a pin on +5V, found {on_5v}")
    summary.append(
        f"Trigger buffer confirmed: {trig_buf} (SN74AHCT541PW, +5V) drives all five of "
        f"BARCODE_RAW->BARCODE_PI, CAM_TRIG_EYE_RAW->CAM_TRIG_EYE, "
        f"CAM_TRIG_BEH_RAW->CAM_TRIG_BEH, BARCODE_RAW->BARCODE_BUF and "
        f"BARCODE_RAW->BARCODE_INTAN_BUF from the SAME package on 5 DISTINCT channels "
        f"{barcode_out_pins}, each pairing confirmed via the 74x541 family's fixed "
        f"Ai<->Yi=20."
    )

    # --- BARCODE HEARTBEAT LED (panel-instrumentation task, 2026-08-15, spec Sec.9.8
    # item 3): a SIXTH dedicated buffered leg off the SAME trigger buffer package and the
    # SAME BARCODE_RAW input -- "drive it from a spare buffered leg, not by loading the
    # barcode net", made executable the same way BARCODE_OPTO_LEGS already is above. ---
    barcode_hb_buf = _walk_buffered_channel(nets, values, "BARCODE_RAW", "BARCODE_HB", "SN74AHCT541PW")
    check(
        barcode_hb_buf == trig_buf,
        f"BARCODE_HB should share the SAME physical SN74AHCT541PW package as BARCODE_PI "
        f"et al. ({trig_buf}), found {barcode_hb_buf}",
    )
    hb_out_pin = next(int(n.pin) for n in nets["BARCODE_HB"] if n.ref == trig_buf and "tri_state" in n.pintype)
    check(
        hb_out_pin not in barcode_out_pins.values(),
        f"{trig_buf}: BARCODE_HB's own output pin ({hb_out_pin}) collides with one of "
        f"BARCODE_PI/CAM_TRIG_EYE/CAM_TRIG_BEH/BARCODE_BUF/BARCODE_INTAN_BUF's own pins "
        f"({barcode_out_pins}) -- this would load the barcode net's own driver pin, "
        f"exactly what a dedicated leg exists to avoid",
    )
    r195_refs = {n.ref for n in nets["BARCODE_HB"] if n.ref.startswith("R")}
    check(len(r195_refs) == 1, f"BARCODE_HB: expected exactly 1 series resistor, found {r195_refs}")
    r195_ref = next(iter(r195_refs))
    check(values.get(r195_ref) == "390", f"{r195_ref}: expected Value '390' (barcode heartbeat LED series R), found {values.get(r195_ref)!r}")
    hb_led_node = next(name for name, ns in nets.items() if any(n.ref == r195_ref and name != "BARCODE_HB" for n in ns))
    d43_refs = {n.ref for n in nets.get(hb_led_node, []) if values.get(n.ref) == "LED"}
    check(len(d43_refs) == 1, f"{hb_led_node}: expected exactly 1 LED, found {d43_refs}")
    d43_ref = next(iter(d43_refs))
    d43_cathode_on_dgnd = [n for n in nets.get("DGND", []) if n.ref == d43_ref and n.pin == "1"]
    check(len(d43_cathode_on_dgnd) == 1, f"{d43_ref}: cathode (pin 1) expected on DGND, not found there")
    d43_anode_on_node = [n for n in nets.get(hb_led_node, []) if n.ref == d43_ref and n.pin == "2"]
    check(len(d43_anode_on_node) == 1, f"{d43_ref}: anode (pin 2) expected on {hb_led_node!r} (in series with {r195_ref}), not found there")
    summary.append(
        f"Barcode heartbeat LED confirmed: BARCODE_RAW -> {trig_buf} (channel producing "
        f"BARCODE_HB, pin {hb_out_pin} -- distinct from every other channel's own pin) "
        f"-> {r195_ref}(390R) -> {d43_ref}(LED) -> DGND. Never loads BARCODE_PI or "
        f"either _BUF net."
    )

    # --- Barcode fan-out reaches exactly 5 loads (this task's own brief: "5 loads -- NI
    # opto, Intan opto, and three spare positions" -- this sheet's own 5 placeholder
    # headers, Task 9's own commit), each a DISTINCT reference, AND NOT ONE OF THEM AN
    # OPTOCOUPLER LED.
    #
    # This count was briefly 7. When Task 11 built the real optocoupler sheets, it hung
    # opto-ni's own ACSL-6400 LED cathode (5->6) and opto-intan's own (6->7) directly on
    # BARCODE_PI, on top of the 5 headers -- the two placeholder POSITIONS this sheet's
    # own on-sheet text named "NI opto"/"Intan opto" acquiring real loads. That looked
    # like the net simply gaining real consumers, and this check was widened to 7 to
    # match. It was actually a defect: each LED draws ~7.33mA from +5V through 430R, so
    # the trigger buffer's own Y0 pin was sinking 14.7mA against SN74HCT541's 6mA IOL.
    # Both LEDs now have their own dedicated channel off this same package (BARCODE_BUF,
    # BARCODE_INTAN_BUF -- gen_breakout_pi_interface.py's own BARCODE_OPTO_LEGS), so
    # BARCODE_PI is back to exactly the 5 headers and drives no LED at all. The cathode
    # assertion below is what keeps the widening from silently happening again: a future
    # sheet that hangs another LED here fails, instead of prompting the count to be
    # bumped to 6. ---
    barcode_loads = [n for n in nets["BARCODE_PI"] if n.ref != trig_buf]
    check(
        len(barcode_loads) == 5,
        f"BARCODE_PI: expected exactly 5 loads besides the driving buffer {trig_buf} "
        f"(this sheet's own 5 placeholder headers, and nothing else), found "
        f"{len(barcode_loads)}: {barcode_loads}",
    )
    barcode_load_refs = {n.ref for n in barcode_loads}
    check(
        len(barcode_load_refs) == 5,
        f"BARCODE_PI: expected 5 DISTINCT load references, found {len(barcode_load_refs)}: "
        f"{barcode_load_refs} (a repeated reference would mean one 2-pin placeholder's "
        f"OWN two pins both landed on BARCODE_PI, not two independent loads)",
    )
    barcode_cathodes = [n for n in barcode_loads if (n.pinfunction or "").startswith("CATHODE")]
    check(
        not barcode_cathodes,
        f"BARCODE_PI: carries optocoupler LED cathode pin(s) "
        f"{[(n.ref, n.pin) for n in barcode_cathodes]}. This net drives 5 placeholder "
        f"headers already; an LED on top of them costs ~7.33mA on the trigger buffer's "
        f"own Y0 pin, and two of them costs 14.7mA against SN74HCT541's 6mA IOL. Each "
        f"optocoupler LED gets its OWN buffered leg -- BARCODE_BUF and BARCODE_INTAN_BUF "
        f"(BARCODE_OPTO_LEGS)",
    )
    summary.append(
        f"BARCODE_PI fans out to exactly 5 distinct loads ({sorted(barcode_load_refs)}) "
        f"and carries no optocoupler LED cathode -- both real LEDs are on their own "
        f"dedicated buffered legs (BARCODE_BUF, BARCODE_INTAN_BUF)."
    )

    # --- Camera triggers fan out to 5 real BNC positions -- 1 eye, 4 behavior. ---
    eye_loads = {n.ref for n in nets["CAM_TRIG_EYE"] if n.ref != trig_buf}
    check(len(eye_loads) == 1, f"CAM_TRIG_EYE: expected exactly 1 BNC load, found {eye_loads}")
    beh_loads = {n.ref for n in nets["CAM_TRIG_BEH"] if n.ref != trig_buf}
    check(len(beh_loads) == 4, f"CAM_TRIG_BEH: expected exactly 4 BNC loads, found {beh_loads}")
    summary.append(
        f"Camera-trigger fan-out confirmed: CAM_TRIG_EYE -> 1 BNC ({sorted(eye_loads)}), "
        f"CAM_TRIG_BEH -> 4 BNC ({sorted(beh_loads)})."
    )

    # --- Internal USB header: 1x4, standard USB-A pin order (1=VBUS 2=D- 3=D+ 4=GND). ---
    for name in ("USB_VBUS_PI", "USB_DP_PI", "USB_DM_PI"):
        check(name in nets, f"missing net: {name!r}")
        check(len(nets[name]) >= 1, f"{name}: no nodes at all")
    # The physical header is the ONE reference present on ALL THREE nets -- found via
    # INTERSECTION, not union. Before Task 12, the header was the only consumer of any of
    # these three nets, so a union-based "exactly one reference across all three" held
    # trivially. Task 12's own control-usb-i2c.kicad_sch legitimately adds a SECOND
    # reference (its MCP2221A bridge) on USB_DP_PI/USB_DM_PI only -- deliberately NOT on
    # USB_VBUS_PI, which that sheet's own module docstring documents leaving unconnected
    # (3.3V self-powered mode needs no VBUS connection) -- so the union now has two members
    # by design, not by defect. The header's own reference is still the one common to all
    # three (the bridge touches only two of them), which is what actually identifies it.
    usb_ref_sets = [{n.ref for n in nets[name]} for name in ("USB_VBUS_PI", "USB_DP_PI", "USB_DM_PI")]
    common_refs = usb_ref_sets[0] & usb_ref_sets[1] & usb_ref_sets[2]
    check(
        len(common_refs) == 1,
        f"USB_VBUS_PI/USB_DP_PI/USB_DM_PI should share exactly ONE common connector "
        f"reference (present on all three -- the header itself), found {common_refs} "
        f"(per-net reference sets: {usb_ref_sets})",
    )
    usb_ref = next(iter(common_refs))
    usb_pin_map = {"1": "USB_VBUS_PI", "2": "USB_DM_PI", "3": "USB_DP_PI"}
    for pin, net in usb_pin_map.items():
        nodes = [n for n in nets[net] if n.ref == usb_ref and n.pin == pin]
        check(len(nodes) == 1, f"{usb_ref} pin {pin} expected on {net!r} (standard USB-A pin order), not found there: {nets[net]}")
    gnd_on_usb = [n for n in nets["DGND"] if n.ref == usb_ref and n.pin == "4"]
    check(len(gnd_on_usb) == 1, f"{usb_ref} pin 4 expected on DGND (USB-A pin 4 = GND), not found")
    summary.append(f"Internal USB header ({usb_ref}) wired in standard USB-A pin order: 1=VBUS, 2=D-, 3=D+, 4=GND.")

    # --- Every contract net this task's own net contract names (both directions) is
    # present and non-trivially populated. ---
    for name in CONSUMED_CONTRACT_NETS + PRODUCED_CONTRACT_NETS:
        check(name in nets, f"missing locked-contract net: {name!r}")
        check(len(nets[name]) >= 2, f"{name}: suspiciously small, only {nets[name]}")
    summary.append(
        f"All {len(CONSUMED_CONTRACT_NETS)} consumed + {len(PRODUCED_CONTRACT_NETS)} "
        f"produced contract nets present and populated."
    )

    # --- No two of the named nets checked have collapsed onto the same physical net. ---
    all_named = (
        CONSUMED_CONTRACT_NETS + PRODUCED_CONTRACT_NETS
        + list(GPIO_NET_AT_PIN.values())
        + ["USB_VBUS_PI", "USB_DP_PI", "USB_DM_PI"]
    )
    seen: dict[frozenset, str] = {}
    for name in sorted(set(all_named)):
        key = frozenset((n.ref, n.pin) for n in nets[name])
        check(
            key not in seen,
            f"{name} and {seen.get(key)} have IDENTICAL node sets -- they are the same "
            f"physical net under two different labels",
        )
        seen[key] = name
    summary.append(f"No two of the {len(set(all_named))} named nets checked collapsed onto the same physical net.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire, rather than
# passing vacuously -- same convention every other checker in this project establishes.
# ---------------------------------------------------------------------------


def _assert_fails(nets, values, expect_substring: str, label: str) -> str:
    try:
        verify(nets, values)
    except CheckFailure as e:
        check(
            expect_substring in str(e),
            f"self-test {label!r}: verify() failed, but not with the expected complaint "
            f"(expected a message containing {expect_substring!r}, got: {e})",
        )
        return str(e)
    raise CheckFailure(
        f"self-test {label!r}: verify() did NOT raise on a corrupted netlist -- the check "
        f"this self-test exists to validate is passing vacuously"
    )


def self_test(good_nets: dict[str, list[Node]], good_values: dict[str, str]) -> list[str]:
    """Negative controls. `good_nets`/`good_values` must already pass verify() cleanly --
    each corruption below is a minimal, targeted mutation of that known-good structure.
    """
    results = []
    header_ref = _find_header_ref(good_nets)

    # THE central negative control this task's own brief asks for: move ONE GPIO signal
    # to a DIFFERENT (wrong) physical pin -- exactly "a wrong GPIO assignment [that]
    # would pass ERC and break PIO capture on a bench" (this file's own module
    # docstring). EVT_D5_PI (GPIO5, real position 29) moved to GPIO6's own real position
    # (31) -- both are ordinary event-data bits, so nothing else about the netlist looks
    # obviously wrong; only the physical-pin walk catches it.
    moved = copy.deepcopy(good_nets)
    idx = next(i for i, n in enumerate(moved["EVT_D5_PI"]) if n.ref == header_ref)
    old = moved["EVT_D5_PI"][idx]
    moved["EVT_D5_PI"][idx] = Node(ref=old.ref, pin=str(GPIO_PHYSICAL_PIN[6]), pinfunction=old.pinfunction, pintype=old.pintype)
    msg = _assert_fails(
        moved, good_values, "expected pin",
        "EVT_D5_PI (GPIO5) moved to GPIO6's own physical pin",
    )
    results.append(
        f"Wrong GPIO assignment (EVT_D5_PI moved from GPIO5's own physical pin 29 to "
        f"GPIO6's physical pin 31 -- ERC-invisible, bench-breaking): caught -- {msg}"
    )

    # A second, independent flavour of the same defect class: RWD_DLVR_PI (GPIO23, a
    # LOCAL post-level-shift net, not a raw EVT_D* one) moved to a wrong pin too --
    # confirms the walk fires for every GPIO_NET_AT_PIN entry, not only the ones sharing
    # EVT_D*_PI's own naming pattern.
    moved23 = copy.deepcopy(good_nets)
    idx23 = next(i for i, n in enumerate(moved23["RWD_DLVR_PI"]) if n.ref == header_ref)
    old23 = moved23["RWD_DLVR_PI"][idx23]
    moved23["RWD_DLVR_PI"][idx23] = Node(ref=old23.ref, pin=str(GPIO_PHYSICAL_PIN[24]), pinfunction=old23.pinfunction, pintype=old23.pintype)
    msg = _assert_fails(
        moved23, good_values, "expected pin",
        "RWD_DLVR_PI (GPIO23) moved to GPIO24's own physical pin",
    )
    results.append(f"Wrong GPIO assignment (RWD_DLVR_PI moved from GPIO23's pin 16 to GPIO24's pin 18): caught -- {msg}")

    # GND miswired: one of the 8 real GND positions (physical pin 6) reassigned to a
    # different net -- simulating a future edit that drops or mislabels a ground pin.
    # Moved to a synthetic net name no other check in verify() tracks (not a real net
    # like EVT_D2_PI, which already has its OWN header pin -- adding a second header
    # node there would trip the earlier GPIO2 walk first instead of exercising the GND
    # check this self-test targets).
    gnd_moved = copy.deepcopy(good_nets)
    gidx = next(i for i, n in enumerate(gnd_moved["DGND"]) if n.ref == header_ref and n.pin == "6")
    victim = gnd_moved["DGND"][gidx]
    gnd_moved["DGND"] = gnd_moved["DGND"][:gidx] + gnd_moved["DGND"][gidx + 1:]
    gnd_moved["STRAY_NET"] = [victim]
    msg = _assert_fails(gnd_moved, good_values, "expected DGND", "header GND pin 6 reassigned off DGND")
    results.append(f"Header GND pin (physical 6) moved off DGND: caught -- {msg}")

    # Power-pin regression: a 3V3 header pin (physical 1) accidentally bridged to this
    # board's own +3V3 rail instead of left no-connect -- exactly the "two independent
    # supplies paralleled" hazard the module docstring names. Must REMOVE the pin from
    # its own synthetic "unconnected-(...)" net first (not just add a second net), or
    # _header_pin_net_map()'s own "a pin can only be on one net" assertion fires instead
    # of the no-connect check this self-test targets -- a real bridged pin would only
    # ever show up on ONE net (the one it's actually wired to), never both.
    bridged = copy.deepcopy(good_nets)
    old_pin_map = _header_pin_net_map(bridged, header_ref)
    stray_net = old_pin_map["1"]
    bridged[stray_net] = [n for n in bridged[stray_net] if not (n.ref == header_ref and n.pin == "1")]
    bridged["+3V3"] = bridged["+3V3"] + [Node(ref=header_ref, pin="1", pinfunction="3V3_1", pintype="passive")]
    msg = _assert_fails(bridged, good_values, "expected no-connect", "header 3V3 pin bridged to the board's own +3V3 rail")
    results.append(f"Header 3V3 pin (physical 1) bridged to the board's own +3V3 rail: caught -- {msg}")

    # GPIO0 series resistor value drift: 330R silently edited to some other value.
    drifted = dict(good_values)
    r0_ref = next(n.ref for n in good_nets["EVT_D0_PI"] if n.ref.startswith("R"))
    drifted[r0_ref] = "1k"
    msg = _assert_fails(good_nets, drifted, "should be '330'", f"{r0_ref} (GPIO0 series resistor) value drift")
    results.append(f"GPIO0 series resistor value drift (330 -> 1k): caught -- {msg}")

    # RWD_DLVR level shifter accidentally powered from +5V (defeats the entire purpose --
    # it would then pass a 5V logic high straight through instead of shifting it down).
    lvl_ref = next(n.ref for n in good_nets["RWD_DLVR_PI"] if good_values.get(n.ref) == "SN74LVC541APW")
    mispowered = copy.deepcopy(good_nets)
    mispowered["+5V"] = mispowered["+5V"] + [Node(ref=lvl_ref, pin="20", pinfunction="VCC_20", pintype="power_in")]
    msg = _assert_fails(mispowered, good_values, "has a pin on +5V", "RWD_DLVR level shifter also powered from +5V")
    results.append(f"RWD_DLVR level shifter ({lvl_ref}) mis-powered from +5V as well as +3V3: caught -- {msg}")

    # Trigger buffer channel permutation: swap CAM_TRIG_EYE's and CAM_TRIG_BEH's own
    # buffer-output nodes (the SAME permutation class check_taskpc_digital_netlist.py's
    # own self-tests exercise for its buffers).
    swapped = copy.deepcopy(good_nets)
    eye_idx = next(i for i, n in enumerate(swapped["CAM_TRIG_EYE"]) if "tri_state" in n.pintype)
    beh_idx = next(i for i, n in enumerate(swapped["CAM_TRIG_BEH"]) if "tri_state" in n.pintype)
    swapped["CAM_TRIG_EYE"][eye_idx], swapped["CAM_TRIG_BEH"][beh_idx] = (
        swapped["CAM_TRIG_BEH"][beh_idx], swapped["CAM_TRIG_EYE"][eye_idx],
    )
    msg = _assert_fails(swapped, good_values, "pairing", "CAM_TRIG_EYE/CAM_TRIG_BEH buffer-output swap")
    results.append(f"Trigger-buffer channel permutation (CAM_TRIG_EYE/CAM_TRIG_BEH outputs swapped): caught -- {msg}")

    # Barcode fan-out regression: drop one of the 7 loads (simulating an accidental
    # deletion of a placeholder position in a future edit).
    dropped = copy.deepcopy(good_nets)
    victim_ref = sorted({n.ref for n in dropped["BARCODE_PI"] if n.ref.startswith("J")})[0]
    dropped["BARCODE_PI"] = [n for n in dropped["BARCODE_PI"] if n.ref != victim_ref]
    msg = _assert_fails(dropped, good_values, "expected exactly 5 loads", f"BARCODE_PI load {victim_ref} dropped")
    results.append(f"Barcode fan-out regression ({victim_ref} dropped, 7 loads -> 6): caught -- {msg}")

    # An optocoupler LED hung back onto BARCODE_PI -- exactly what Task 11 did, and what
    # made the trigger buffer's own Y0 pin sink 14.7mA against a 6mA IOL. The load-count
    # check above would also fire on this (6 != 5), but only by accident of arithmetic: a
    # future edit that legitimately retires one placeholder header and adds an LED would
    # keep the count at 5 and slip straight through. This control targets the cathode
    # assertion specifically, so the "5" and the "no LEDs" halves are each proven to fire
    # on their own.
    with_led = copy.deepcopy(good_nets)
    a_header = sorted({n.ref for n in with_led["BARCODE_PI"] if n.ref.startswith("J")})[0]
    with_led["BARCODE_PI"] = [n for n in with_led["BARCODE_PI"] if n.ref != a_header] + [
        Node(ref="U60", pin="4", pinfunction="CATHODE2_4", pintype="passive")
    ]
    msg = _assert_fails(with_led, good_values, "carries optocoupler LED cathode pin", "an optocoupler LED cathode hung back onto BARCODE_PI")
    results.append(f"Optocoupler LED hung directly on BARCODE_PI (opto-ni's own U60 cathode, replacing a header so the load COUNT still reads 5): caught -- {msg}")

    # USB header pin-order regression: D+/D- swapped (a real, easy-to-make placement
    # mistake -- USB D+/D- polarity matters).
    usb_ref = next(iter({n.ref for n in good_nets["USB_VBUS_PI"]}))
    usb_swapped = copy.deepcopy(good_nets)
    dp_idx = next(i for i, n in enumerate(usb_swapped["USB_DP_PI"]) if n.ref == usb_ref)
    dm_idx = next(i for i, n in enumerate(usb_swapped["USB_DM_PI"]) if n.ref == usb_ref)
    usb_swapped["USB_DP_PI"][dp_idx], usb_swapped["USB_DM_PI"][dm_idx] = (
        usb_swapped["USB_DM_PI"][dm_idx], usb_swapped["USB_DP_PI"][dp_idx],
    )
    msg = _assert_fails(usb_swapped, good_values, "standard USB-A pin order", "USB D+/D- swapped")
    results.append(f"Internal USB header D+/D- swapped ({usb_ref}): caught -- {msg}")

    # Barcode heartbeat LED collision (panel-instrumentation task, 2026-08-15): BARCODE_HB
    # accidentally shares its own trigger-buffer output pin with BARCODE_PI (simulating a
    # future edit that reuses an already-spoken-for channel instead of a genuinely spare
    # one), loading the real barcode net exactly as the dedicated leg exists to avoid.
    hb_buf_ref = next(n.ref for n in good_nets["BARCODE_HB"] if good_values.get(n.ref) == "SN74AHCT541PW")
    hb_collision = copy.deepcopy(good_nets)
    barcode_pi_out_pin = next(n.pin for n in hb_collision["BARCODE_PI"] if n.ref == hb_buf_ref and "tri_state" in n.pintype)
    hb_collision["BARCODE_HB"] = [n for n in hb_collision["BARCODE_HB"] if not (n.ref == hb_buf_ref and "tri_state" in n.pintype)] + [
        Node(ref=hb_buf_ref, pin=barcode_pi_out_pin, pinfunction=f"Y{barcode_pi_out_pin}", pintype="tri_state")
    ]
    msg = _assert_fails(hb_collision, good_values, "collides with one of", "BARCODE_HB reusing BARCODE_PI's own output pin")
    results.append(f"Barcode heartbeat LED channel collision (BARCODE_HB reusing BARCODE_PI's own {hb_buf_ref} output pin): caught -- {msg}")

    return results


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker
# in this project's own verify_instance_paths()/self_test_instance_paths().
# ---------------------------------------------------------------------------


def verify_instance_paths(pi_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(pi_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"pi-interface.kicad_sch's own file-identity uuid ({own_root_uuid}) collides "
        f"with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, PI_INTERFACE_SHEETFILE)

    paths = find_all_instance_paths(pi_sch_text)
    check(
        len(paths) >= 18,
        f"only found {len(paths)} (instances (path ...)) entries in pi-interface.kicad_sch "
        f"-- expected >=18 (1 J-prefixed header + 2 series R + 1 level-shift U + its own "
        f"1 decoupling C + 1 trigger-buffer U + its own 1 decoupling C + 5 barcode-load J "
        f"+ 5 BNC J + 1 USB J = 18; recomputed directly against this task's own real "
        f"output, not guessed)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in pi-interface.kicad_sch do "
        f"not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found "
        f"{bad[:3]!r} instead",
    )
    return (
        f"All {len(paths)} component instance paths in pi-interface.kicad_sch resolve to "
        f"the real ancestor chain {expected_prefix!r} (breakout's own root uuid + the "
        f"'pi-interface' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(pi_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(pi_text, breakout_text)
    except CheckFailure as e:
        check(
            expect_substring in str(e),
            f"self-test {label!r}: verify_instance_paths() failed, but not with the "
            f"expected complaint (expected a message containing {expect_substring!r}, "
            f"got: {e})",
        )
        return str(e)
    raise CheckFailure(
        f"self-test {label!r}: verify_instance_paths() did NOT raise on a corrupted "
        f"schematic -- the check this self-test exists to validate is passing vacuously"
    )


def self_test_instance_paths(good_pi_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_pi_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, PI_INTERFACE_SHEETFILE)

    corrupted = good_pi_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_pi_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_pi_text may not actually be passing verify_instance_paths() "
        "cleanly to begin with",
    )
    msg = _assert_instance_paths_fail(
        corrupted, good_breakout_text, "do not resolve to the real ancestor chain",
        "one component's instance path reverted to self-referential",
    )
    return [
        f"Self-referential instance path (this file's own root uuid in place of the real "
        f"breakout ancestor chain, on one component) reintroduced: caught -- {msg}"
    ]


def main() -> int:
    net_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NET_PATH
    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print(
            "  kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net "
            "hardware/breakout/breakout.kicad_sch"
        )
        return 1
    text = net_path.read_text()
    nets = parse_netlist(text)
    values = parse_component_values(text)
    print(f"parsed {len(nets)} nets, {len(values)} component values from {net_path}")
    try:
        summary = verify(nets, values)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS:")
    for line in summary:
        print(f"  - {line}")

    try:
        self_test_results = self_test(nets, values)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in self_test_results:
        print(f"  - {line}")

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_PI_INTERFACE_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_PI_INTERFACE_SCH} do not "
            f"exist -- run gen_breakout.py and gen_breakout_pi_interface.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    pi_sch_text = DEFAULT_PI_INTERFACE_SCH.read_text()

    # Shared row-pitch-vs-2-pin-part-span collision guard (constraint 4) -- not
    # previously wired into this checker; added at the panel-instrumentation task
    # (2026-08-15) alongside the barcode heartbeat LED's own two new 2-pin parts
    # (R195/D43), same pattern as check_breakout_power_netlist.py/
    # check_taskpc_digital_netlist.py's own main().
    try:
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(pi_sch_text, "pi-interface.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(pi_sch_text, "pi-interface.kicad_sch", min_instances=15)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

    try:
        path_summary = verify_instance_paths(pi_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(pi_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
