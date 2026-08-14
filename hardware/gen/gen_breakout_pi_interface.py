"""Generator for hardware/breakout/sheets/pi-interface.kicad_sch -- Task 9 of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-9-brief.md): the breakout board's sync-module interface sheet.

WORDING NOTE (module-neutral, per this task's own instruction): this file's own text and
comments say "sync module", not "Pi", except when quoting spec/plan text that itself says
"Pi". The 40-pin header this sheet places is currently a Compute Module 5 on its official
IO Board (spec Sec.2 Decision 2a) -- a decision spec Sec.9.4 states is CONDITIONAL on
floorplan placement and may revert to a Raspberry Pi 5. Both carry the identical BCM2712
SoC and RP1 I/O controller, so the GPIO map and 40-pin physical pinout this sheet wires
are IDENTICAL either way (hardware/README.md's own opening paragraph makes the same
point; spec Sec.9.4: "Nothing in the software depends on which is chosen... PIO, the GPIO
numbering and wl_sync are identical either way"). The symbol itself keeps its existing
name, wl-sync:RaspberryPi5_GPIO_Header -- renaming it was considered and rejected as this
task's own instruction says: optional, low-value churn (pin names/numbers are unaffected
by the module decision; only a silkscreen legend would change, and this project has no
silkscreen-legend step yet).

Five things, matching the brief's own step numbering:

  1. Place the 40-pin header (wl-sync:RaspberryPi5_GPIO_Header) and assign every one of
     its 40 pins: 28 GPIO-named pins per GPIO_PIN_SPEC below (spec Sec.4's own table,
     transcribed verbatim and restated as an on-sheet text block per the brief's own
     "self-checking against the spec during review" instruction), 8 GND pins to DGND, and
     4 power pins (two 3V3, two 5V) left NO-CONNECT -- the sync module has its own
     official USB-C PD supply, panel-cutout, EXPLICITLY separate from this board's own
     +5V rail (spec Sec.8: "Sync box 5V/5A. Its own official USB-C PD supply... Separate
     from the board's +5V rail. Substituting is a false economy on a CM5-class board"),
     so bridging the header's own power pins to this board's own +5V/+3V3 would parallel
     two independently-regulated supplies against each other -- the same no-connect
     precedent gen_mule.py's own header_nc_remaining() already established for its Pi-side
     header's 3V3/5V pins (mule board, PI_GND_PHYSICAL_PIN's sibling positions), reused
     here rather than re-derived. Because this symbol names its pins by GPIO number
     (gen_wl_sync_lib.py's own SYM_PI5_HEADER docstring: "so the GPIO map ... is checkable
     by eye against a placed instance rather than against header position numbers"), this
     generator looks pins up BY NAME (_gpio_named_pins() below) rather than transcribing a
     second physical-pin table by hand the way gen_mule.py's own GPIO_PHYSICAL_PIN has to
     for its generic, position-only-named Conn_02x20_Odd_Even header -- one less place for
     a transcription error to hide. check_breakout_pi_interface_netlist.py, reading the
     EXPORTED NETLIST (which reports only pin NUMBER, never name -- hardware/README.md's
     own "pinfunction" gotcha), does still need an independent physical-pin table; see
     that file's own module docstring.

  2. GPIO0/GPIO1 -- physical pins 27/28 -- get a 330R series resistor each, between the
     already-buffered EVT_D0_PI/EVT_D1_PI nets Task 8 hands off and the physical header
     pin (GPIO{0,1}_HDR, a LOCAL name this sheet mints -- EVT_D{0,1}_PI, per the net
     contract, is what THIS sheet consumes from Task 8, not necessarily what lands
     literally AT the header pin, exactly the same naming split gen_mule.py's own
     buffer_output_net() already uses for its mirror-image reason: the sync module probes
     GPIO0/1 as I2C at boot looking for a HAT ID EEPROM, and a buffer driving those two
     pins contends with that probe (spec Sec.4). Mitigation: series resistors here, and
     `force_eeprom_read=0` in `config.txt` (a software-side setting this sheet can only
     document, not enforce).

  3. Buffer the three signals this sheet PRODUCES (BARCODE_PI, CAM_TRIG_EYE,
     CAM_TRIG_BEH) through one more SN74HCT541PW on +5V (3 of 8 channels), and fan out:
     BARCODE_PI to 5 loads (2 placeholder positions for Task 11's not-yet-built NI/Intan
     optocouplers + 3 genuinely spare positions -- brief Step 3's own "three spare
     positions"), CAM_TRIG_EYE/CAM_TRIG_BEH to 5 real panel BNC positions (spec Sec.9.1:
     "Camera triggers | BNC | 5 (1 eye, 4 behavior)"). See _place_trigger_buffer_and_
     fanout()'s own docstring for the placeholder-connector precedent this reuses from
     gen_breakout_taskpc_digital.py rather than inventing fresh.

     ALSO, NOT in the brief's own literal step list (see "RWD_DLVR level shift" below):
     one more SN74LVC541APW channel on +3V3, level-shifting the one 5V-domain signal this
     sheet's own net contract consumes (RWD_DLVR) down to a sync-module-safe level before
     it reaches GPIO23. Required by this project's OWN global, non-negotiable constraint
     (plan.md, "Global Constraints": "Pi GPIO is 3.3V and not 5V tolerant... `74LVC541A`
     on 3.3V for inbound, `74HCT541` on 5V for outbound. Never one part for both."), not
     an invented addition -- see that section's own docstring for the full derivation.

  4. Route the internal USB header: a 2.54mm 1x4 pin header for the internal cable from
     the sync module's own USB-A port (a panel CUTOUT on the module itself, spec Sec.9.1's
     own "Pi ports | cutouts: USB-C, Ethernet, USB-A" -- NOT a pin on THIS 40-pin header)
     to Task 12's own not-yet-built MCP2221A-class USB-I2C bridge (spec Sec.4: "Mux and
     DAC control does not use GPIO... This is what preserves GPIO26/27 as spare").

RWD_DLVR LEVEL SHIFT -- why this sheet adds a fifth thing beyond the brief's own four
literal steps, stated once here rather than only inline: RWD_DLVR is produced on
hardware/breakout/sheets/taskpc-digital.kicad_sch by a 74HCT32 OR gate powered from +5V
(gen_breakout_taskpc_digital.py's place_reward_or_gate(), called with rail="+5V") -- a
genuine 0-5V logic signal, not a 3.3V one. This project's OWN global constraint (plan.md,
quoted above) states plainly that the sync module's GPIO is 3.3V and NOT 5V tolerant, and
spec Sec.7.1 ("Level shifting -- the correction") is explicit about the failure mode this
constraint exists to prevent: "A single `74HCT541` cannot serve both directions, and
following that assumption literally is how the [sync module] gets destroyed." Task 9's own
net contract (as handed down for this task) lists RWD_DLVR -- bare, no "_PI" suffix, unlike
every EVT_* signal, which already arrives from Task 8 pre-shifted to 3.3V -- among the nets
this sheet consumes, and spec Sec.4's own GPIO map fixes GPIO23 as "Reward delivered". Wiring
RWD_DLVR to GPIO23 DIRECTLY would satisfy both of those literally while violating the global
constraint that supersedes them: a ~5V logic high landing on a 3.3V-only input. So this sheet
inserts exactly the recipe spec Sec.7.1's own table already prescribes for the identical
class of problem ("Task PC (5V) -> Pi | `74LVC541A` | 3.3V | LVC inputs tolerate 5.5V
regardless of supply"): one more SN74LVC541APW channel on +3V3, RWD_DLVR -> U<n> ->
RWD_DLVR_PI (a local name -- not a further contract net; nothing downstream of GPIO23 needs
to know this hop happened) -> GPIO23. This is the same class of call
gen_breakout_taskpc_digital.py's own report already made at Task 8 fix round 1 for a
different, but similarly critical, defect (the reward-OR debounce polarity) -- catching and
fixing a genuine electrical hazard the literal brief text didn't spell out, rather than
implementing it literally and filing a concern. PD1_COMP/PD2_COMP/ACC_TRIG are NOT given the
same treatment here: their real driver (Task 10's comparator sheet) does not exist yet, so
their eventual voltage domain is not yet a confirmed fact the way RWD_DLVR's +5V origin
already is (grep-confirmed against gen_breakout_taskpc_digital.py's own committed source,
not inferred) -- inventing a level-shift for an unknown future voltage would be premature,
and likely wrong in the other direction if Task 10 turns out to choose a 3.3V-safe pull-up
(a real possibility flagged on-sheet: the SAME signal must also satisfy Task 8's own +5V
HCT541 outbound-buffer input, and a 3.3V pull-up satisfies both constraints simultaneously
where a 5V one would not). Flagged on-sheet and in this task's own report instead.

REF_START -- seeding past TWO siblings, not one: this is this project's THIRD real child
sheet, after power.kicad_sch (Task 7) and taskpc-digital.kicad_sch (Task 8) -- both already
committed, both real (non-empty), so BOTH must be seeded past, or this sheet's own
independently-started "J1"/"R1"/"U1"/"C1" collide with whichever of the two priors used the
higher number for that prefix (hardware/README.md's own "duplicate reference designators"
gotcha names this exact extension as needed "once Tasks 9-12" arrived). kicad_sch.py's own
find_max_refs() only ever reads ONE sheet's text, so this generator computes the per-prefix
MAXIMUM across both siblings via kicad_sch.py's new merge_max_refs() (added this task,
generic machinery, not sheet-specific -- see that function's own docstring) rather than
picking only one sibling or summing the two.

Run directly: `python3 hardware/gen/gen_breakout_pi_interface.py` (writes
hardware/breakout/sheets/pi-interface.kicad_sch). hardware/breakout/fp-lib-table gained one
new entry for this task -- Connector_Coaxial (BNC_PanelMountable_Vertical, for the 5 camera-
trigger panel positions) -- see hardware/README.md.

Requires hardware/breakout/breakout.kicad_sch, hardware/breakout/sheets/power.kicad_sch, and
hardware/breakout/sheets/taskpc-digital.kicad_sch to already exist on disk (Tasks 6-8's own
outputs) -- build() reads all three: the first to compute this file's real root+sheet-symbol
ancestor path (kicad_sch.py's find_root_uuid()/find_sheet_instance_path()), the latter two to
seed this file's own reference counters past both already-committed siblings' real usage
(merge_max_refs(), above).
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_max_refs,
    find_root_uuid,
    find_sheet_instance_path,
    merge_max_refs,
    pin_pos,
    write_project_stub,
)

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
POWER_SCH = OUT / "power.kicad_sch"              # already-committed sibling (Task 7)
TASKPC_SCH = OUT / "taskpc-digital.kicad_sch"     # already-committed sibling (Task 8)
PI_INTERFACE_SHEETFILE = "sheets/pi-interface.kicad_sch"  # exactly as breakout.kicad_sch's
# own (sheet ...) block spells its Sheetfile property (gen_breakout.py's SHEET_NAMES/
# f"sheets/{name}.kicad_sch") -- find_sheet_instance_path() matches on this literal string.

# ---------------------------------------------------------------------------
# GPIO map (spec Sec.4, transcribed verbatim -- restated as an on-sheet text block by
# _place_header()'s own text-block loop, per the brief's own "self-checking against the
# spec during review" instruction).
#
# Each row: (gpio number, kind, net landing AT the physical header pin, the CONTRACT net
# this sheet produces/consumes for this signal, human description).
#
#   kind="direct"         -- net_at_pin == contract_net: the contract net is consumed (or,
#                             for GPIO17-19 this would be wrong -- see "buffered_out"
#                             below) or produced AT the header pin directly, no local hop.
#   kind="series_gpio01"  -- net_at_pin is a LOCAL "GPIO{n}_HDR" name; a 330R resistor
#                             bridges it to contract_net (EVT_D{n}_PI, received from Task
#                             8) -- see module docstring, item 2.
#   kind="buffered_out"   -- net_at_pin is a LOCAL "..._RAW" name (this sheet's own raw
#                             module-sourced signal); the SN74HCT541PW trigger buffer
#                             bridges it to contract_net (the net this sheet PRODUCES).
#   kind="level_shift_in" -- net_at_pin is a LOCAL "..._PI" name; one SN74LVC541APW
#                             channel on +3V3 bridges it to contract_net (RWD_DLVR,
#                             received from Task 8 at +5V) -- see module docstring,
#                             "RWD_DLVR level shift".
#   kind="spare"          -- no_connect; net_at_pin/contract_net are both None.
# ---------------------------------------------------------------------------
GPIO_PIN_SPEC = tuple(
    [
        (0, "series_gpio01", "GPIO0_HDR", "EVT_D0_PI", "Event code data bit 0"),
        (1, "series_gpio01", "GPIO1_HDR", "EVT_D1_PI", "Event code data bit 1"),
    ]
    + [
        (i, "direct", f"EVT_D{i}_PI", f"EVT_D{i}_PI", f"Event code data bit {i}")
        for i in range(2, 16)
    ]
    + [
        (16, "direct", "EVT_STROBE_PI", "EVT_STROBE_PI", "Event strobe"),
        (17, "buffered_out", "BARCODE_RAW", "BARCODE_PI", "Barcode"),
        (18, "buffered_out", "CAM_TRIG_EYE_RAW", "CAM_TRIG_EYE", "ohDPI camera trigger (hardware PWM)"),
        (19, "buffered_out", "CAM_TRIG_BEH_RAW", "CAM_TRIG_BEH", "Behavior camera trigger (hardware PWM)"),
        (20, "direct", "PD1_COMP", "PD1_COMP", "Photodiode 1 comparator"),
        (21, "direct", "PD2_COMP", "PD2_COMP", "Photodiode 2 comparator"),
        (22, "direct", "RWD_CMD", "RWD_CMD", "Reward commanded"),
        (23, "level_shift_in", "RWD_DLVR_PI", "RWD_DLVR", "Reward delivered"),
        (24, "direct", "STIM_TRIG", "STIM_TRIG", "Stim trigger"),
        (25, "direct", "ACC_TRIG", "ACC_TRIG", "Accelerometer motion trigger"),
        (26, "spare", None, None, "spare"),
        (27, "spare", None, None, "spare"),
    ]
)
assert [t[0] for t in GPIO_PIN_SPEC] == list(range(28))

# CONTRACT_NETS_CONSUMED (task's own net contract, "consumes ... from Task 8") -- every
# GPIO_PIN_SPEC row whose contract_net this sheet does NOT itself produce. Listed here,
# once, so build()/main() output and this task's own report can state the contract
# without re-deriving it from GPIO_PIN_SPEC by hand.
CONTRACT_NETS_CONSUMED = [
    f"EVT_D{i}_PI" for i in range(16)
] + ["EVT_STROBE_PI", "RWD_CMD", "RWD_DLVR", "STIM_TRIG", "PD1_COMP", "PD2_COMP", "ACC_TRIG"]
assert len(CONTRACT_NETS_CONSUMED) == 23

# CONTRACT_NETS_PRODUCED -- task's own net contract, "Produces".
CONTRACT_NETS_PRODUCED = ["BARCODE_PI", "CAM_TRIG_EYE", "CAM_TRIG_BEH"]

# ---------------------------------------------------------------------------
# Footprints -- picked from the part actually being ordered / the stock footprint the
# symbol's own real-world equivalent uses, same discipline every other generator in this
# project's own footprint-assignment comment block documents.
# ---------------------------------------------------------------------------
FOOTPRINT_TSSOP20 = "Package_SO:Texas_PW0020A_TSSOP-20_4.4x6.5mm_P0.65mm"  # SN74LVC541APW /
# SN74HCT541PW -- TI's own "PW" package code is literally TSSOP-20 (gen_mule.py's own choice,
# reused at every prior '541 placement in this project).
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"  # 100nF decoupling
FOOTPRINT_HDR1X02 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
FOOTPRINT_HDR1X04 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
# Bare 2x20 2.54mm THT header -- NOT a shrouded IDC box header, matching a real Pi/CM5 IO
# Board 40-pin header's own physical form (a standard ribbon connects directly, same
# reasoning gen_mule.py's own FOOTPRINT_IDC40 comment gives for its Pi-side header) and
# hardware/README.md's own documented choice for this exact symbol ("The Pi header ...
# reuse[s] stock footprints -- a bare 2x20 2.54mm THT header").
FOOTPRINT_PI_HDR = "Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical"
# Generic, panel-mountable, 2-pad (centre + shield -- no separate chassis/mounting pad,
# consistent with spec Sec.9.1's own "Isolated BNCs throughout, so each shell lands on its
# own pad rather than being bonded to the shield plane by the connector body") BNC
# footprint -- a real stock KiCad footprint, not a placeholder part number, though (like
# every connector in this project not yet locked to a specific ordered MPN -- see
# hardware/README.md's own "Custom connector footprints" section) the exact manufacturer
# part is a layout-stage/procurement decision this schematic-capture task does not make.
FOOTPRINT_BNC = "Connector_Coaxial:BNC_PanelMountable_Vertical"

# ---------------------------------------------------------------------------
# Layout grid -- every coordinate an exact multiple of 1.27mm (KiCad's schematic
# connection grid), same GRID() helper as every other generator in this project.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


X_NOTE1, Y_NOTE1 = GRID(15), GRID(15)      # GPIO map restatement
NOTE_DY = GRID(5.08)

X_HDR, Y_HDR = GRID(200), GRID(160)        # 40-pin sync-module GPIO header

X_R01 = GRID(60)                            # GPIO0/GPIO1 series resistors (Y from pin)

X_NOTE2, Y_NOTE2 = GRID(15), GRID(130)     # GPIO0/1 boot-contention note

X_LVLSHIFT, Y_LVLSHIFT = GRID(15), GRID(180)   # RWD_DLVR level-shift SN74LVC541APW
X_NOTE3, Y_NOTE3 = GRID(15), GRID(220)     # RWD_DLVR level-shift note

X_TRIGBUF, Y_TRIGBUF = GRID(300), GRID(160)    # BARCODE/CAM_TRIG output SN74HCT541PW
X_BARCODE_LOADS, Y_BARCODE_LOADS0 = GRID(360), GRID(120)  # 5 barcode placeholder loads
LOAD_DY = GRID(12.7)
X_BNC, Y_BNC0 = GRID(360), GRID(210)       # 5 camera-trigger BNC positions
X_NOTE4, Y_NOTE4 = GRID(300), GRID(280)    # barcode/camera fan-out note

X_USB, Y_USB = GRID(15), GRID(320)         # internal USB header
X_NOTE5, Y_NOTE5 = GRID(15), GRID(350)     # USB header note

DECOUPLE_DX = GRID(15.24)

CHAN_A = {i: str(2 + i) for i in range(8)}   # 74x541 unit-1 pin numbers: A0..A7
CHAN_Y = {i: str(18 - i) for i in range(8)}  # ...and Y0..Y7, paired by channel: Ai+Yi=20


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    """Place a 2-pin part between two labeled nets -- same pattern as every other
    generator in this project's own two_pin() (duplicated rather than imported:
    kicad_sch.py, not any one generator, is this project's shared machinery)."""
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    x1, y1 = pin_pos(x, y, pins["1"])
    sch.label(net1, x1, y1)
    x2, y2 = pin_pos(x, y, pins["2"])
    sch.label(net2, x2, y2)
    return ref


def place_octal_buffer(sch, libname, symname, value, x, y, rail, channels, refs, role_key, footprint):
    """Place one 74x541-family octal buffer -- IDENTICAL logic to
    gen_breakout_taskpc_digital.py's own place_octal_buffer() (duplicated, not imported,
    same "kicad_sch.py is the shared machinery" discipline as two_pin() above): input Ai
    at pin 2+i, tri-state output Yi at pin 18-i, so Ai+Yi=20 for every channel of every
    instance -- the invariant check_breakout_pi_interface_netlist.py's own walk depends
    on. `channels` maps local channel index (0-7) -> (input_net, output_net) for USED
    channels; channels not present are tied off safely (input -> DGND, output ->
    no-connect), never left floating.
    """
    ref = sch.next_ref("U")
    pins = sch.place(
        libname, symname, ref, value, x, y,
        footprint=footprint,
        extra_props={"Description": "8-bit buffer/line driver, 3-state outputs"},
    )
    for i in range(8):
        a_pin, y_pin = pins[CHAN_A[i]], pins[CHAN_Y[i]]
        if i in channels:
            a_net, y_net = channels[i]
            ax, ay = pin_pos(x, y, a_pin)
            sch.label(a_net, ax, ay)
            yx, yy = pin_pos(x, y, y_pin)
            sch.label(y_net, yx, yy)
        else:
            ax, ay = pin_pos(x, y, a_pin)
            sch.label("DGND", ax, ay)
            yx, yy = pin_pos(x, y, y_pin)
            sch.no_connect(yx, yy)
    for oe_pin in ("1", "19"):
        px, py = pin_pos(x, y, pins[oe_pin])
        sch.label("DGND", px, py)  # active-low output enable, tied permanently enabled
    gx, gy = pin_pos(x, y, pins["10"])
    sch.label("DGND", gx, gy)
    vx, vy = pin_pos(x, y, pins["20"])
    sch.label(rail, vx, vy)
    cap_ref = two_pin(sch, "Device", "C", "C", "100nF", x - DECOUPLE_DX, y, rail, "DGND", footprint=FOOTPRINT_C_SMALL)
    refs.setdefault(role_key, []).append(ref)
    refs.setdefault(role_key + "_decouple_c", []).append(cap_ref)
    return ref


def _gpio_named_pins(pins: dict) -> dict:
    """{GPIO name -> Pin} for the 28 GPIO-named pins of a placed RaspberryPi5_GPIO_Header
    instance, looked up BY NAME rather than by a hand-transcribed physical-pin table --
    see module docstring, item 1, for why this symbol makes that possible and gen_mule.py's
    own generic header cannot."""
    out = {p.name: p for p in pins.values() if p.name.startswith("GPIO")}
    assert len(out) == 28, f"expected 28 GPIOn-named pins, found {len(out)}: {sorted(out)}"
    return out


def _place_header(sch, refs):
    """Step 1: place the 40-pin header, wire its 8 GND pins to DGND, leave its 4 power
    pins (2x 3V3, 2x 5V) no-connect (module has its own independent supply -- see module
    docstring), and wire all 28 GPIO pins per GPIO_PIN_SPEC. GPIO0/1's own 330R series
    resistors are placed here too (their own placement is tightly coupled to the header
    pin they bridge to), not in a separate function.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "RaspberryPi5_GPIO_Header", ref,
        "Sync-module 40-pin GPIO header (Raspberry Pi 5 / CM5 IO Board -- see module docstring)",
        X_HDR, Y_HDR, footprint=FOOTPRINT_PI_HDR,
    )
    refs["pi_header"] = ref

    gnd_n = v3v3_n = v5_n = 0
    for p in pins.values():
        if p.name == "GND":
            x, y = pin_pos(X_HDR, Y_HDR, p)
            sch.label("DGND", x, y)
            gnd_n += 1
        elif p.name in ("3V3", "5V"):
            x, y = pin_pos(X_HDR, Y_HDR, p)
            sch.no_connect(x, y)
            v3v3_n += p.name == "3V3"
            v5_n += p.name == "5V"
    assert (gnd_n, v3v3_n, v5_n) == (8, 2, 2), (gnd_n, v3v3_n, v5_n)

    gpio_pins = _gpio_named_pins(pins)
    refs["gpio01_series_r"] = []
    series_gpio01_seen = 0  # GPIO0 (physical pin 27, LEFT column) and GPIO1 (physical
    # pin 28, RIGHT column) share the SAME row -- build_symbol()'s left/right columns
    # both index the same `ys[row]` per row, and 27/28 are consecutive odd/even numbers
    # in the same row. Placing both resistors at a single fixed (X_R01, y) therefore
    # stacks them on the exact same coordinate (found empirically: kicad-cli sch erc's
    # own multiple_net_names -- "Both GPIO0_HDR and GPIO1_HDR are attached to the same
    # items" -- on the first generation attempt). A per-instance Y offset keeps them
    # distinct -- but Device:R's own two pins are EXACTLY 7.62mm apart (pin1 at local Y
    # +3.81, pin2 at -3.81, confirmed via unit_pins()), so a 7.62mm offset between
    # instances (tried second) makes R<n>'s own pin2 land exactly on R<n+1>'s own pin1
    # instead -- same failure, one component over. GRID(12.7), comfortably more than
    # 7.62mm, clears both pins of one resistor from both pins of the next.
    for gpio, kind, hdr_net, contract_net, _desc in GPIO_PIN_SPEC:
        p = gpio_pins[f"GPIO{gpio}"]
        x, y = pin_pos(X_HDR, Y_HDR, p)
        if kind == "spare":
            sch.no_connect(x, y)
            continue
        sch.label(hdr_net, x, y)
        if kind == "series_gpio01":
            ry = y + series_gpio01_seen * GRID(12.7)
            series_gpio01_seen += 1
            r_ref = two_pin(
                sch, "Device", "R", "R", "330", X_R01, ry, contract_net, hdr_net,
                footprint=FOOTPRINT_R,
            )
            refs["gpio01_series_r"].append(r_ref)

    for line_idx, line in enumerate([
        "Sync-module GPIO map (spec Sec.4 -- transcribed verbatim; restated here so this",
        "sheet is self-checking against the spec during review):",
        "  GPIO      Dir   Signal",
        "  0-15      in    Event code data x16",
        "  16        in    Event strobe",
        "  17        out   Barcode",
        "  18        out   ohDPI camera trigger (hardware PWM)",
        "  19        out   Behavior camera trigger (hardware PWM)",
        "  20, 21    in    Photodiode comparators",
        "  22        in    Reward commanded",
        "  23        in    Reward delivered",
        "  24        in    Stim trigger",
        "  25        in    Accelerometer motion trigger",
        "  26, 27    --    spare",
        "Why GPIO0-16 (not 2-18): PIO parallel capture reads a CONTIGUOUS pin range, and",
        "camera triggers must land on a hardware PWM pin (12, 13, 18, 19). Every 17-wide",
        "contiguous window inside GPIO2-27 contains 12 and 13, and only the window",
        "starting at 2 leaves even one PWM pin free -- insufficient for two triggers.",
        "Starting at 0 consumes 12 and 13 (ordinary data bits here) while leaving 18 and",
        "19 free for the two camera triggers.",
        "Module: a Compute Module 5 on its official IO Board as of this sheet",
        "(conditional on floorplan, spec Sec.9.4) or a Raspberry Pi 5 -- identical",
        "BCM2712/RP1, identical GPIO map/pinout either way; see hardware/README.md.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "GPIO0/GPIO1 (physical header pins 27/28): the sync module probes these two pins",
        "as I2C at boot, looking for a HAT ID EEPROM. The 330R series resistors above",
        "limit contention between that probe and this board's own buffered drive.",
        "Requires force_eeprom_read=0 in config.txt (spec Sec.4) -- a config.txt setting,",
        "not something this schematic can enforce.",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    return pins


def _place_rwd_dlvr_level_shift(sch, refs):
    """RWD_DLVR level shift -- see module docstring's own dedicated section for the full
    derivation. One SN74LVC541APW channel (1 of 8) on +3V3: RWD_DLVR (received from Task
    8 at +5V) -> RWD_DLVR_PI (local name, lands on GPIO23). Same sparse-usage precedent as
    taskpc-digital.kicad_sch's own U3 (3/8 channels) and outbound U7 (4/8): a full package
    is placed because it is the part already qualified for this exact role on this board
    (spec Sec.7.1's own prescribed "Task PC (5V) -> Pi" recipe), not because 8 channels of
    level-shifting are needed here.
    """
    channels = {0: ("RWD_DLVR", "RWD_DLVR_PI")}
    ref = place_octal_buffer(
        sch, "74xx", "74LS541", "SN74LVC541APW", X_LVLSHIFT, Y_LVLSHIFT, "+3V3",
        channels, refs, "rwd_dlvr_lvl_shift", FOOTPRINT_TSSOP20,
    )
    for line_idx, line in enumerate([
        "RWD_DLVR level shift (added on this sheet, beyond the brief's own literal step",
        "list -- see this generator's own module docstring for the full derivation):",
        "RWD_DLVR is produced on taskpc-digital.kicad_sch by a 74HCT32 OR gate powered",
        "from +5V (place_reward_or_gate(), rail=\"+5V\") -- a genuine 0-5V logic signal.",
        "The sync module's GPIO is 3.3V and NOT 5V tolerant (plan.md's own global",
        "constraint: '74LVC541A on 3.3V for inbound, 74HCT541 on 5V for outbound --",
        "never one part for both'). So RWD_DLVR is wired through one more",
        "SN74LVC541APW channel on +3V3 here -- the SAME level-shift-down recipe spec",
        "Sec.7.1's own table already prescribes for 'Task PC (5V) -> Pi' -- rather than",
        "straight to GPIO23, which would put ~5V on a 3.3V-only input.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)
    return ref


def _place_trigger_buffer_and_fanout(sch, refs):
    """Step 3: buffer the three signals this sheet PRODUCES (BARCODE_PI, CAM_TRIG_EYE,
    CAM_TRIG_BEH) through one more SN74HCT541PW on +5V (3 of 8 channels), then fan out.

    BARCODE_PI's own "5 loads" (brief Step 3: "NI opto, Intan opto, and three spare
    positions") are placeholder Connector_Generic:Conn_01x02 positions, not real opto/
    front-end circuitry -- Task 11 (NI/Intan optocouplers) does not exist yet, and KiCad
    only lets the file that PLACES a symbol instance attach labels to its own pins, so
    this sheet cannot wire Task 11's own real optocoupler LED inputs any more than
    gen_breakout_taskpc_digital.py's own reward-button/jack/reward-driver-BNC placeholders
    could wire real hardware for THEIR own not-yet-built destinations -- same "placeholder
    connector for a circuit that doesn't exist yet" precedent, reused rather than invented
    fresh (see that generator's own _place_reward_or() docstring).

    CAM_TRIG_EYE/CAM_TRIG_BEH fan out to 5 REAL panel BNC positions (Connector:Conn_Coaxial
    -- a real, permanent part of this board's own panel inventory, not a stand-in for
    later circuitry): spec Sec.9.1's own connector budget, "Camera triggers | BNC | 5 (1
    eye, 4 behavior)". All 4 behavior positions share the SAME CAM_TRIG_BEH net (spec
    Sec.9.4: "only two hardware PWM pins survive the contiguous capture range", so every
    behavior camera shares one trigger rate -- this is not 4 independently-triggerable
    channels).
    """
    channels = {
        0: ("BARCODE_RAW", "BARCODE_PI"),
        1: ("CAM_TRIG_EYE_RAW", "CAM_TRIG_EYE"),
        2: ("CAM_TRIG_BEH_RAW", "CAM_TRIG_BEH"),
    }
    place_octal_buffer(
        sch, "74xx", "74HCT541", "SN74HCT541PW", X_TRIGBUF, Y_TRIGBUF, "+5V",
        channels, refs, "trigger_buf", FOOTPRINT_TSSOP20,
    )

    barcode_loads = [
        "Barcode -> NI optocoupler (Task 11 placeholder)",
        "Barcode -> Intan optocoupler (Task 11 placeholder)",
        "Barcode spare 1",
        "Barcode spare 2",
        "Barcode spare 3",
    ]
    refs["barcode_load_hdr"] = []
    for i, desc in enumerate(barcode_loads):
        y = Y_BARCODE_LOADS0 + i * LOAD_DY
        r = two_pin(
            sch, "Connector_Generic", "Conn_01x02", "J", desc, X_BARCODE_LOADS, y,
            "BARCODE_PI", "DGND", footprint=FOOTPRINT_HDR1X02,
        )
        refs["barcode_load_hdr"].append(r)

    refs["cam_trig_bnc"] = []
    eye_ref = two_pin(
        sch, "Connector", "Conn_Coaxial", "J", "Eye camera trigger out (BNC)",
        X_BNC, Y_BNC0, "CAM_TRIG_EYE", "DGND", footprint=FOOTPRINT_BNC,
    )
    refs["cam_trig_bnc"].append(eye_ref)
    for i in range(4):
        y = Y_BNC0 + (i + 1) * LOAD_DY
        r = two_pin(
            sch, "Connector", "Conn_Coaxial", "J", f"Behavior camera trigger out {i + 1} (BNC)",
            X_BNC, y, "CAM_TRIG_BEH", "DGND", footprint=FOOTPRINT_BNC,
        )
        refs["cam_trig_bnc"].append(r)

    for line_idx, line in enumerate([
        "BARCODE_PI fan-out (5 loads, brief Step 3): 2 placeholder positions for Task",
        "11's own NI/Intan optocouplers (not built yet -- same placeholder-connector",
        "precedent gen_breakout_taskpc_digital.py's own reward-button/jack/BNC",
        "placeholders established) plus 3 genuinely spare positions.",
        "CAM_TRIG_EYE/CAM_TRIG_BEH fan out to 5 REAL panel BNC positions (spec Sec.9.1:",
        "'Camera triggers | BNC | 5 (1 eye, 4 behavior)') -- 1 eye camera, 4 behavior",
        "positions sharing ONE trigger rate (spec Sec.9.4: only GPIO18/19 survive the",
        "contiguous PIO capture range as hardware-PWM pins, so a third independently-",
        "triggerable channel is not available).",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)


def _place_usb_header(sch, refs):
    """Step 4: 2.54mm 1x4 internal USB header -- the sync module's own USB-A port (panel
    cutout, spec Sec.9.1) to Task 12's own not-yet-built USB-I2C bridge. Standard USB-A
    pin order (1=VBUS 2=D- 3=D+ 4=GND -- USB's own defining physical convention, not this
    board's choice). Net names (USB_VBUS_PI/USB_DP_PI/USB_DM_PI) are this sheet's own
    choice: no contract fixes them yet (this task's own brief lists only BARCODE_PI/
    CAM_TRIG_EYE/CAM_TRIG_BEH as "Produces"), and plan.md's own Task 12 interfaces line
    ("Consumes: ... the USB header from Task 9") names the HEADER, not specific net names
    -- so Task 12 discovers these three by reading this sheet, same as any other
    cross-sheet net in this design. GND does not get a suffixed name: every other GND-role
    net in this design just uses DGND directly, and a USB cable's own ground return is no
    different.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "Connector_Generic", "Conn_01x04", ref,
        "Internal USB-A (sync-module port -> Task 12 USB-I2C bridge)",
        X_USB, Y_USB, footprint=FOOTPRINT_HDR1X04,
    )
    usb_map = {"1": "USB_VBUS_PI", "2": "USB_DM_PI", "3": "USB_DP_PI", "4": "DGND"}
    for num, net in usb_map.items():
        x, y = pin_pos(X_USB, Y_USB, pins[num])
        sch.label(net, x, y)
    refs["usb_hdr"] = ref

    for line_idx, line in enumerate([
        "Internal USB header (2.54mm 1x4): from the sync module's own USB-A port (a",
        "panel cutout on the module itself, spec Sec.9.1 -- NOT a pin on the 40-pin GPIO",
        "header above) to Task 12's own MCP2221A-class USB-I2C bridge, driving the mux",
        "address expanders and threshold DACs (spec Sec.4: mux/DAC control does not use",
        "GPIO -- this is what preserves GPIO26/27 as spare).",
        "Standard USB-A pin order: 1=VBUS 2=D- 3=D+ 4=GND.",
        "Net names (USB_VBUS_PI/USB_DP_PI/USB_DM_PI) are this sheet's own choice, no",
        "contract fixes them -- Task 12 consumes them by these names (plan.md Task 12:",
        "'Consumes: ... the USB header from Task 9').",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)


def build() -> tuple[Sch, dict]:
    """Returns (sch, refs) -- `refs` maps a role name to the reference designator(s) that
    play it, same convention every generator in this project follows.

    `ref_start`: seeded past BOTH already-committed prior siblings (power.kicad_sch,
    taskpc-digital.kicad_sch), via kicad_sch.py's new merge_max_refs() -- see module
    docstring's own "REF_START" section.
    """
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, PI_INTERFACE_SHEETFILE)
    ref_start = merge_max_refs(
        find_max_refs(POWER_SCH.read_text()),
        find_max_refs(TASKPC_SCH.read_text()),
    )

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    _place_header(sch, refs)
    _place_rwd_dlvr_level_shift(sch, refs)
    _place_trigger_buffer_and_fanout(sch, refs)
    _place_usb_header(sch, refs)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "pi-interface.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
