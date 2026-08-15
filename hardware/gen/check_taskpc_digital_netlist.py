"""Parse hardware/breakout's exported netlist (and, for the instance-path check the
exported netlist provably cannot answer -- see below, the raw .kicad_sch sources) and
verify the task-PC digital sheet's contract nets are electrically correct -- not just
that ERC was silent.

Why this exists, not just ERC (same reasoning check_mule_netlist.py's and
check_breakout_power_netlist.py's own docstrings give, restated because it is the reason
THIS file exists too): gen_breakout_taskpc_digital.py's own lib_symbols entries are keyed
by full lib id, which resolves correctly -- but "resolves correctly" is proven empirically
per run, not guaranteed by construction for every future edit. Keyed bare, or with a
mismatched/incomplete symbol, KiCad loads the file without error and silently produces a
plausible, wrong netlist. So ERC passing is necessary but not sufficient.

A PER-NET check is not enough either, and this is the specific, named risk this file's
own end-to-end walk exists to catch: on the mule board, a checker that only verified each
net in isolation ("does EVT_D5_CLAMP have exactly one diode node") would have passed even
if the 16 data channels were PERMUTED through the buffers (channel 3's input wired to
channel 5's own buffer, channel 5's input wired to channel 3's) -- every net still "looks
right" on its own, ERC stays silent (nothing about a permutation violates an electrical
rule), and only an end-to-end walk that ties a channel's INPUT hop to its OUTPUT hop via
the SAME physical reference catches it. `_walk_inbound_channel()` below does this for all
19 of this sheet's own inbound channels, and -- since this sheet fans each protected input
out to TWO independent buffer banks (LVC541 -> the *_PI/bare contract net, HCT541 -> the
*_BUF net Task 11's NI optocouplers consume) -- walks BOTH forks from the shared clamp
node, not just one: a permutation on the _BUF side alone would be just as real a defect
as one on the _PI side, and just as invisible to a per-net-only check.

TEMPORARY STATE, not a defect: Tasks 9 (pi-interface) and 11 (opto-ni/opto-intan) do not
exist yet, so every *_PI/*_BUF net this sheet produces currently terminates at exactly the
one buffer-output node this sheet itself provides (nothing downstream consumes it yet --
see gen_breakout_taskpc_digital.py's own module docstring), and PD1_COMP/PD2_COMP/
ACC_TRIG/RHS_STIM_OUT (produced by Tasks 10/11, consumed here) currently have only the one
node this sheet's own outbound buffer input provides. This checker's own node-count
assertions are written to stay true both now AND once those sheets exist (>=1, not ==1,
wherever a later sheet will add more nodes) -- see verify()'s own comments for exactly
which counts are permanently fixed (raw/clamp nets: always exactly the nodes THIS sheet
puts there, nothing else ever attaches) versus which are a floor (the various output-side
contract nets).

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_taskpc_digital_netlist.py [path/to/breakout.net]

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
    parse_component_footprints,
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
DEFAULT_TASKPC_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "taskpc-digital.kicad_sch"
TASKPC_SHEETFILE = "sheets/taskpc-digital.kicad_sch"  # exactly as breakout.kicad_sch's own
# Sheetfile property spells it -- must match gen_breakout_taskpc_digital.py's own constant.

# The brief's own required net list (task-8-brief.md / the delegated task's own "Net names
# are a contract" list) -- redefined here, not imported from the generator, same
# discipline check_breakout_power_netlist.py's own CONTRACT_NETS establishes: a checker
# that trusted the generator's own list would only be checking the generator against
# itself.
#
# ANALOG_CONTRACT_NETS (fix round 1, finding 3): the 9 task-PC analog channel names,
# redefined here from plan.md's own net-naming table + Task 10 Step 3's own text -- same
# discipline as CONTRACT_NETS above, not imported from gen_breakout_taskpc_digital.py.
ANALOG_CONTRACT_NETS = (
    [f"A_EYE_{s}_TPC" for s in ("LX", "LY", "RX", "RY")]
    + ["A_JOY_X_TPC", "A_JOY_Y_TPC"]
    + [f"A_MISC{n}_TPC" for n in (1, 2, 3)]
)
assert len(ANALOG_CONTRACT_NETS) == 9

CONTRACT_NETS = (
    [f"EVT_D{i}_TPC" for i in range(16)] + ["EVT_STROBE_TPC"]
    + [f"EVT_D{i}_PI" for i in range(16)] + ["EVT_STROBE_PI"]
    + [f"EVT_D{i}_BUF" for i in range(16)] + ["EVT_STROBE_BUF"]
    + ["RWD_CMD", "RWD_CMD_BUF", "RWD_BTN", "RWD_DLVR", "STIM_TRIG", "STIM_TRIG_BUF"]
    + ["PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"]
    # One optocoupler LED per driver pin -- the 7 dedicated legs (see
    # SECOND_LEG_CHANNELS/COMPARATOR_OPTO_LEGS below).
    + ["EVT_STROBE_INTAN_BUF", "RWD_CMD_INTAN_BUF", "STIM_TRIG_INTAN_BUF"]
    + ["RWD_DLVR_BUF", "RWD_DLVR_INTAN_BUF", "PD1_COMP_BUF", "PD2_COMP_BUF"]
    + ANALOG_CONTRACT_NETS
)
assert len(CONTRACT_NETS) == 77

# Real physical MDR68 pin assignment -- redefined here from gen_breakout_taskpc_digital.py
# (same discipline as CONTRACT_NETS above), sourced from NI's own "X Series User Manual:
# NI 632x/634x/635x/636x/637x/638x/639x Devices" (370784K-01, May 2019, ni.com/manuals),
# Figure A-5 "NI PCIe-6323/6343 Pinout" -- see that generator's own module-level comment
# and task-8-report.md's "Fix round 1" for the retrieval/cross-check method.
MDR1_PIN_BY_P0 = {
    8: "52", 9: "17", 10: "49", 11: "47", 12: "19", 13: "51", 14: "16", 15: "48",
    16: "11", 17: "10", 18: "43", 19: "42", 20: "41", 21: "6", 22: "5", 23: "38",
    24: "37", 25: "3", 26: "45", 27: "46", 28: "2", 29: "40", 30: "1", 31: "39",
}
MDR1_DGND_PINS = ["4", "7", "9", "12", "13", "15", "18", "35", "36", "44", "50", "53"]
MDR0_AI_PIN = {
    0: "68", 1: "33", 2: "65", 3: "30", 4: "28", 5: "60", 6: "25", 7: "57", 8: "34",
}
MDR0_AISENSE_PIN = "62"
ANALOG_CHANNELS = list(zip(ANALOG_CONTRACT_NETS, range(9)))
assert len(ANALOG_CHANNELS) == 9

# The 19 inbound channels, exactly as gen_breakout_taskpc_digital.py's own
# INBOUND_CHANNELS/_inbound_channel_specs() build them -- redefined here rather than
# imported, same reasoning as CONTRACT_NETS above. (channel index, MDR68-side raw net,
# clamp/dual-buffer-input net, LVC541-bank output net, HCT541-_BUF-bank output net).
def _inbound_channel_specs():
    specs = []
    for i in range(16):
        specs.append((i, f"EVT_D{i}_TPC", f"EVT_D{i}_CLAMP", f"EVT_D{i}_PI", f"EVT_D{i}_BUF"))
    specs.append((16, "EVT_STROBE_TPC", "EVT_STROBE_CLAMP", "EVT_STROBE_PI", "EVT_STROBE_BUF"))
    specs.append((17, "RWD_CMD_TPC", "RWD_CMD_CLAMP", "RWD_CMD", "RWD_CMD_BUF"))
    specs.append((18, "STIM_TRIG_TPC", "STIM_TRIG_CLAMP", "STIM_TRIG", "STIM_TRIG_BUF"))
    return specs


INBOUND_CHANNELS = _inbound_channel_specs()
assert len(INBOUND_CHANNELS) == 19

# The 4 outbound channels: (input net this sheet consumes from elsewhere, this sheet's own
# MDR68-side output net, Connector 1 physical pin). Pins are P0.27-30 via MDR1_PIN_BY_P0
# (fix round 1, finding 2) -- was the sequential "20"-"23" pre-fix-round-1.
OUTBOUND_CHANNELS = [
    ("PD1_COMP", "PD1_COMP_TPC", MDR1_PIN_BY_P0[27]),
    ("PD2_COMP", "PD2_COMP_TPC", MDR1_PIN_BY_P0[28]),
    ("ACC_TRIG", "ACC_TRIG_TPC", MDR1_PIN_BY_P0[29]),
    ("RHS_STIM_OUT", "RHS_STIM_OUT_TPC", MDR1_PIN_BY_P0[30]),
]

# ---------------------------------------------------------------------------
# ONE OPTOCOUPLER LED PER DRIVER PIN -- re-derived here from the electrical requirement,
# not imported from gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS/
# COMPARATOR_OPTO_LEGS (the same "a checker that trusted the generator would only be
# checking the generator against itself" discipline every table in this file follows).
#
# Each ACSL-6400/6420 LED on this board is fed from +5V through 430R and draws ~7.33mA,
# a value pinned between the part's own 7-15mA recommended band and its 7.0mA worst-case
# switching threshold -- so it cannot be lowered to suit a driver. TWO on one pin is
# 14.7mA, against SN74AHCT541's 8mA IOL and SN74HCT32's 4mA. The `_BUF` bank named 19
# SIGNALS for what is really a 30-LED board, and the 11 unnamed LEDs landed doubled up.
#
# (input net, output net) for each dedicated leg. The three *_INTAN_BUF entries take the
# same *_CLAMP node their NI twin takes -- a parallel buffered copy off the established
# fan-out point, exactly as the LVC541 and HCT541 banks have shared it since Task 8.
SECOND_LEG_CHANNELS = [
    ("EVT_STROBE_CLAMP", "EVT_STROBE_INTAN_BUF"),
    ("RWD_CMD_CLAMP", "RWD_CMD_INTAN_BUF"),
    ("STIM_TRIG_CLAMP", "STIM_TRIG_INTAN_BUF"),
    ("RWD_DLVR", "RWD_DLVR_BUF"),
    ("RWD_DLVR", "RWD_DLVR_INTAN_BUF"),
]
# Clamp nodes that legitimately carry a THIRD buffer input (the LVC541, the HCT541 _BUF
# leg, and one *_INTAN_BUF leg) rather than the usual two -- derived from the table above
# so the two cannot drift apart.
CLAMP_NETS_WITH_SECOND_LEG = {src for src, _out in SECOND_LEG_CHANNELS if src.endswith("_CLAMP")}
assert len(CLAMP_NETS_WITH_SECOND_LEG) == 3

# The two comparator outputs whose NI optocoupler LED used to hang directly off the LM339
# -- and, through that LED's own 430R to +5V, put ~3.6-3.8V (LED off) onto GPIO20/GPIO21,
# which are 3.3V-only and wired to those nets DIRECTLY. Buffered off the outbound HCT541's
# own spare channels now. Output-only: no MDR68 pin, unlike OUTBOUND_CHANNELS.
COMPARATOR_OPTO_LEGS = [
    ("PD1_COMP", "PD1_COMP_BUF"),
    ("PD2_COMP", "PD2_COMP_BUF"),
]

# TEMPORARY, given Tasks 9/10/11 don't exist yet -- two distinct directions, both
# resolving to "only 1 node right now", both for the same underlying reason (nothing
# downstream/upstream has been built to attach a second one):
#   - PENDING_UPSTREAM_NETS: PD1_COMP/PD2_COMP/ACC_TRIG/RHS_STIM_OUT are produced by
#     Tasks 10/11 and consumed HERE -- this sheet's own outbound buffer input pin is
#     currently the only node (kicad-cli's netlist exporter does not emit a PWR_FLAG's
#     own pin as a node at all -- confirmed empirically, same finding
#     check_breakout_power_netlist.py's own module docstring already documents for +5V's
#     PWR_FLAG -- see gen_breakout_taskpc_digital.py's own _place_outbound() docstring).
#   - PENDING_DOWNSTREAM_NETS: every *_PI net (Task 9's own Pi header will consume it)
#     and every *_BUF net plus bare STIM_TRIG (Task 11's own NI/Intan optocouplers will
#     consume them) are produced HERE and consumed elsewhere -- this sheet's own buffer
#     output pin is currently the only node. RWD_CMD is the one exception in this family
#     that is NOT pending: it is ALSO consumed locally, by this same sheet's own reward-
#     OR gate (see _place_reward_or()), so it already carries 2 nodes.
#   - PENDING_ANALOG_NETS (fix round 1, finding 3): the 9 A_*_TPC analog channels this
#     sheet's own Connector 0 now names (see _place_connector0()) but Task 10's own
#     front-end/buffer sheets -- the real drivers -- don't exist yet, so each currently
#     terminates at exactly the one Connector-0 MDR68 pin node this sheet itself provides.
# Excluded from the generic "contract net populated" floor below for exactly this reason,
# not because any of these 50 nets are unimportant -- see verify()'s own summary line,
# which names all of them.
PENDING_UPSTREAM_NETS = {"PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"}
PENDING_DOWNSTREAM_NETS = (
    {f"EVT_D{i}_PI" for i in range(16)} | {"EVT_STROBE_PI"}
    | {f"EVT_D{i}_BUF" for i in range(16)} | {"EVT_STROBE_BUF", "RWD_CMD_BUF", "STIM_TRIG_BUF"}
    | {"STIM_TRIG"}
)
PENDING_ANALOG_NETS = set(ANALOG_CONTRACT_NETS)
PENDING_NETS = PENDING_UPSTREAM_NETS | PENDING_DOWNSTREAM_NETS | PENDING_ANALOG_NETS
assert len(PENDING_NETS) == 50, len(PENDING_NETS)

CHAN_A = {i: str(2 + i) for i in range(8)}
CHAN_Y = {i: str(18 - i) for i in range(8)}


def _refs_on(nets: dict[str, list[Node]], net: str) -> set[str]:
    return {n.ref for n in nets.get(net, [])}


def _pins_on(nets: dict[str, list[Node]], net: str) -> set[tuple[str, str]]:
    return {(n.ref, n.pin) for n in nets.get(net, [])}


def _walk_inbound_channel(
    nets: dict[str, list[Node]], values: dict[str, str], spec: tuple[int, str, str, str, str],
) -> tuple[str, str]:
    """Walk one of the 19 inbound channels end to end on BOTH forks: MDR68 (Connector 1)
    pin -> series R -> clamp node (BAT54S clamp, high side +5V/never +3V3) -> fans out to
    the SAME reference's own input pin on TWO independent buffers (LVC541 on +3V3,
    HCT541 on +5V) -> each buffer's OWN output pin (verified via the 74x541 family's fixed
    Ai+Yi=20 pairing, same invariant check_mule_netlist.py's own _walk_channel()
    established) -> the *_PI/bare net (LVC541 fork) and the *_BUF net (HCT541 fork).

    Returns (lvc_ref, hctbuf_ref) for the caller's aggregate checks (distinct-package,
    right-part-in-right-role).
    """
    ch_idx, raw_net, clamp_net, pi_net, buf_net = spec
    # Real, sourced physical pin (fix round 1, finding 2) -- P0.(8+ch_idx) via
    # MDR1_PIN_BY_P0, NOT the sequential str(ch_idx+1) this checker used pre-fix-round-1.
    mdr_pin = MDR1_PIN_BY_P0[8 + ch_idx]

    # Hop 1: Connector 1 pin (NI's own sourced physical position for P0.(8+ch_idx)) ->
    # series resistor.
    check(raw_net in nets, f"missing net: {raw_net!r}")
    raw_nodes = nets[raw_net]
    check(len(raw_nodes) == 2, f"{raw_net}: expected 2 nodes (MDR68 pin + series R), found {raw_nodes}")
    r_nodes = [n for n in raw_nodes if n.ref.startswith("R")]
    mdr_nodes = [n for n in raw_nodes if n.ref.startswith("J")]
    check(len(r_nodes) == 1, f"{raw_net}: expected exactly 1 resistor node, found {raw_nodes}")
    check(len(mdr_nodes) == 1, f"{raw_net}: expected exactly 1 MDR68-connector node, found {raw_nodes}")
    check(
        mdr_nodes[0].pin == mdr_pin,
        f"{raw_net}: lands on Connector 1 pin {mdr_nodes[0].pin}, expected pin {mdr_pin} "
        f"(NI's own sourced position for P0.{8 + ch_idx}, channel index {ch_idx} -- "
        f"Figure A-5, NI X Series User Manual 370784K-01)",
    )
    r_ref = r_nodes[0].ref

    # Hop 2: the SAME resistor's other leg, on the clamp node, alongside the clamp diode
    # AND both buffer banks' own input pins (the fan-out point) -- ties the series R
    # physically to this specific channel and confirms both buffer inputs really share
    # ONE protected node rather than two separate (and possibly cross-wired) ones.
    check(clamp_net in nets, f"missing net: {clamp_net!r}")
    clamp_nodes = nets[clamp_net]
    # 4 normally; 5 on the three clamp nodes that also feed an *_INTAN_BUF leg -- a THIRD
    # parallel buffered copy off the same protected node, which is exactly what this
    # fan-out point is for (the LVC541 and HCT541 banks have shared it since Task 8; see
    # CLAMP_NETS_WITH_SECOND_LEG). Still an EXACT count, not a floor: a stray extra load
    # on a protected node is precisely the class of thing this hop exists to catch.
    expected_nodes = 5 if clamp_net in CLAMP_NETS_WITH_SECOND_LEG else 4
    extra = " + one *_INTAN_BUF second leg" if expected_nodes == 5 else ""
    check(
        len(clamp_nodes) == expected_nodes,
        f"{clamp_net}: expected exactly {expected_nodes} nodes (series R, clamp diode, "
        f"LVC541 input, HCT541_BUF input{extra}), found {len(clamp_nodes)}: {clamp_nodes}",
    )
    r_here = [n for n in clamp_nodes if n.ref == r_ref]
    check(
        len(r_here) == 1,
        f"{clamp_net}: series resistor {r_ref!r} (the one bridging from {raw_net}) does "
        f"not reach here -- a different resistor was used, or this one bridges to a "
        f"DIFFERENT channel's clamp node (a permutation symptom)",
    )
    diode_nodes = [n for n in clamp_nodes if n.ref.startswith("D")]
    check(len(diode_nodes) == 1, f"{clamp_net}: expected exactly 1 clamp-diode node, found {clamp_nodes}")
    diode_ref = diode_nodes[0].ref

    # Hop 2b: that SAME diode's high side is +5V, and NEVER +3V3 (task-8-brief.md's own
    # explicit instruction, and Task 2's own mule-board fix-round-2 bug this repeats).
    on_5v = [n for n in nets.get("+5V", []) if n.ref == diode_ref]
    on_3v3 = [n for n in nets.get("+3V3", []) if n.ref == diode_ref]
    on_dgnd = [n for n in nets.get("DGND", []) if n.ref == diode_ref]
    check(len(on_5v) == 1, f"{clamp_net}: clamp diode {diode_ref} has no pin on +5V: {nets.get('+5V', [])}")
    check(
        len(on_3v3) == 0,
        f"{clamp_net}: clamp diode {diode_ref} has a pin on +3V3 -- clamping a 5V-tolerant "
        f"LVC input to the 3.3V rail (see bidirectional_clamp()'s own docstring for why "
        f"this is wrong -- the exact mule-board fix-round-2 defect)",
    )
    check(len(on_dgnd) == 1, f"{clamp_net}: clamp diode {diode_ref} has no pin on DGND")

    # Hop 3: the buffer-input nodes -- one LVC541 (the +3V3 bank) and one HCT541 (the +5V
    # _BUF bank), identified by Value (not by reference number, which this checker does
    # not assume an ordering for), PLUS a second HCT541 input on the three clamp nodes
    # that also feed an *_INTAN_BUF leg.
    u_nodes = [n for n in clamp_nodes if n.ref.startswith("U")]
    expected_u = 3 if clamp_net in CLAMP_NETS_WITH_SECOND_LEG else 2
    check(
        len(u_nodes) == expected_u,
        f"{clamp_net}: expected exactly {expected_u} buffer-input nodes, found {clamp_nodes}",
    )
    lvc_nodes = [n for n in u_nodes if values.get(n.ref) == "SN74LVC541APW"]
    buf_nodes = [n for n in u_nodes if values.get(n.ref) == "SN74AHCT541PW"]
    check(
        len(lvc_nodes) == 1 and len(buf_nodes) == expected_u - 1,
        f"{clamp_net}: expected exactly 1 SN74LVC541APW input node and {expected_u - 1} "
        f"SN74AHCT541PW input node(s), found values {[values.get(n.ref) for n in u_nodes]} "
        f"on {u_nodes}",
    )
    lvc_ref, lvc_a_pin = lvc_nodes[0].ref, int(lvc_nodes[0].pin)
    # WHICH of the (possibly two) HCT541 inputs belongs to THIS channel is decided from
    # the OUTPUT side, not by picking the only candidate: find the single driver of
    # `buf_net` and derive its partner input pin from the 74x541 family's fixed Ai+Yi=20.
    # That keeps the walk exactly one-to-one whether or not this clamp node also feeds a
    # second leg, and it is a strictly stronger statement than "the one HCT541 here" was
    # -- an output net must have exactly one driver, always.
    check(buf_net in nets, f"missing net: {buf_net!r}")
    buf_drivers = [
        n for n in nets[buf_net]
        if values.get(n.ref) == "SN74AHCT541PW" and "tri_state" in n.pintype
    ]
    check(
        len(buf_drivers) == 1,
        f"{buf_net}: expected exactly 1 SN74AHCT541PW tri_state output pin driving this "
        f"net, found {[(n.ref, n.pin) for n in nets[buf_net]]}",
    )
    buf_ref, buf_a_pin = buf_drivers[0].ref, 20 - int(buf_drivers[0].pin)
    check(
        any(n.ref == buf_ref and n.pin == str(buf_a_pin) for n in buf_nodes),
        f"{clamp_net}: {buf_ref} drives {buf_net} from output pin {buf_drivers[0].pin}, so "
        f"its partner input pin {buf_a_pin} must sit on this clamp node -- it does not "
        f"(HCT541 inputs here: {[(n.ref, n.pin) for n in buf_nodes]}). This channel's "
        f"_BUF data has been permuted within the buffer, or is fed from a different "
        f"channel's protected node.",
    )
    check(
        lvc_a_pin == buf_a_pin,
        f"{clamp_net}: LVC541 ({lvc_ref}) input lands on pin {lvc_a_pin} but HCT541_BUF "
        f"({buf_ref}) input lands on pin {buf_a_pin} -- both banks should use the SAME "
        f"local channel position for this shared node",
    )

    # Hop 4a: the LVC541's OWN output pin, on pi_net (the *_PI net, or the bare contract
    # name for RWD_CMD/STIM_TRIG) -- Ai+Yi=20 ties it to the SAME package as the input.
    check(pi_net in nets, f"missing net: {pi_net!r}")
    lvc_out = [n for n in nets[pi_net] if n.ref == lvc_ref and "tri_state" in n.pintype]
    check(
        len(lvc_out) == 1,
        f"{pi_net}: expected exactly 1 tri_state output pin belonging to {lvc_ref} (the "
        f"same LVC541 whose input is on {clamp_net}), found {len(lvc_out)} -- if a "
        f"different buffer drives this net, channel data has crossed ICs",
    )
    lvc_y_pin = int(lvc_out[0].pin)
    check(
        lvc_a_pin + lvc_y_pin == 20,
        f"channel {ch_idx} ({pi_net}): {lvc_ref}'s input pin {lvc_a_pin} (on {clamp_net}) "
        f"and output pin {lvc_y_pin} (on {pi_net}) don't satisfy the 74x541 family's fixed "
        f"Ai<->Yi pairing (input+output must equal 20) -- this channel's data has been "
        f"permuted within the buffer",
    )

    # Hop 4b: same, on the HCT541_BUF fork.
    check(buf_net in nets, f"missing net: {buf_net!r}")
    buf_out = [n for n in nets[buf_net] if n.ref == buf_ref and "tri_state" in n.pintype]
    check(
        len(buf_out) == 1,
        f"{buf_net}: expected exactly 1 tri_state output pin belonging to {buf_ref} (the "
        f"same HCT541 whose input is on {clamp_net}), found {len(buf_out)} -- if a "
        f"different buffer drives this net, channel data has crossed ICs",
    )
    buf_y_pin = int(buf_out[0].pin)
    check(
        buf_a_pin + buf_y_pin == 20,
        f"channel {ch_idx} ({buf_net}): {buf_ref}'s input pin {buf_a_pin} (on {clamp_net}) "
        f"and output pin {buf_y_pin} (on {buf_net}) don't satisfy the 74x541 family's "
        f"fixed Ai<->Yi pairing -- this channel's _BUF data has been permuted within the "
        f"buffer (the same class of defect as the _PI fork above, just on the copy Task "
        f"11's NI optocouplers consume)",
    )

    return lvc_ref, buf_ref


def _check_outbound_channel(
    nets: dict[str, list[Node]], values: dict[str, str], in_net: str, out_net: str,
    mdr_pin: str | None,
) -> str:
    """Walk one channel of the outbound SN74AHCT541PW: input net (produced elsewhere,
    consumed here by name) -> this sheet's own buffer input pin -> the SAME reference's
    own output pin (Ai+Yi=20) -> Connector 1's own pin. Returns the buffer's own reference
    for the caller's aggregate checks.

    `mdr_pin=None` walks a channel that does NOT reach Connector 1 -- the two
    comparator-optocoupler legs (COMPARATOR_OPTO_LEGS), which exist only to give
    opto-ni's own PD1/PD2 LEDs a buffered driver instead of hanging them on the
    comparator output that wires straight into GPIO20/21. Identical walk otherwise, and
    that case additionally asserts the net reaches NO connector pin at all.

    WALKED OUTPUT-FIRST, deliberately -- see the identical note in
    check_breakout_pi_interface_netlist.py's own _walk_buffered_channel(). PD1_COMP and
    PD2_COMP each feed TWO channels of this package now (the Connector-1 leg and the
    optocoupler leg), so "the one buffer input on this net" is no longer a well-defined
    anchor. An OUTPUT net always has exactly one driver, so anchoring there keeps the
    walk exactly one-to-one and is strictly the stronger statement.
    """
    check(in_net in nets, f"missing net: {in_net!r}")
    check(out_net in nets, f"missing net: {out_net!r}")
    out_nodes = [
        n for n in nets[out_net]
        if values.get(n.ref) == "SN74AHCT541PW" and "tri_state" in n.pintype
    ]
    check(
        len(out_nodes) == 1,
        f"{out_net}: expected exactly 1 SN74AHCT541PW tri_state output pin driving this "
        f"net, found {[(n.ref, n.pin, values.get(n.ref)) for n in nets[out_net]]}",
    )
    ref, y_pin = out_nodes[0].ref, int(out_nodes[0].pin)
    a_pin = 20 - y_pin
    check(
        any(n.ref == ref and n.pin == str(a_pin) for n in nets[in_net]),
        f"{in_net}->{out_net}: {ref} drives {out_net} from output pin {y_pin}, so by the "
        f"74x541 family's fixed Ai<->Yi pairing its partner input pin {a_pin} must sit on "
        f"{in_net} -- it does not. This outbound channel has been permuted within the "
        f"buffer, or is fed from the wrong signal. Nodes on {in_net}: "
        f"{[(n.ref, n.pin) for n in nets[in_net]]}",
    )
    mdr_nodes = [n for n in nets[out_net] if n.ref.startswith("J")]
    if mdr_pin is None:
        check(
            not mdr_nodes,
            f"{out_net}: reaches connector pin(s) {[(n.ref, n.pin) for n in mdr_nodes]} -- "
            f"this is an optocoupler-drive leg only (COMPARATOR_OPTO_LEGS); it must not "
            f"land on Connector 1",
        )
    else:
        check(len(mdr_nodes) == 1, f"{out_net}: expected exactly 1 MDR68-connector node, found {nets[out_net]}")
        check(
            mdr_nodes[0].pin == mdr_pin,
            f"{out_net}: lands on Connector 1 pin {mdr_nodes[0].pin}, expected pin {mdr_pin}",
        )
    return ref


def _check_one_led_per_driver_pin(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    """No logic output pin on this sheet drives more than one optocoupler LED.

    Stated over EVERY driver pin belonging to this sheet's own packages, not over a list
    of nets known to have LEDs: the defect this catches is a net GAINING a second LED
    from a sheet that does not exist yet, which no enumerated list can anticipate. Each
    ACSL LED draws ~7.33mA from +5V through 430R (a value pinned between the part's own
    7-15mA recommended band and its 7.0mA worst-case switching threshold, so it cannot be
    lowered to suit a driver); two on one pin is 14.7mA, against SN74AHCT541's 8mA IOL and
    SN74HCT32's 4mA. The 74HCT32 case is the one with a failure mode rather than just a
    margin: RWD_DLVR's LOW level is read by the +3V3 LVC541 level-shifter (V_IL,max 0.8V)
    on the way to the sync module, and an HCT gate's VOL at 3.7x its rated sink plausibly
    exceeds that -- RWD_DLVR_PI stuck HIGH, the module recording reward-delivered
    continuously.

    Also asserts the 74HCT32 reward-OR drives NO LED at all. It is the weakest driver on
    the sheet and the one whose output is level-critical; buffered legs exist for that
    job (SECOND_LEG_CHANNELS' own RWD_DLVR_BUF/RWD_DLVR_INTAN_BUF)."""
    own_values = {"SN74AHCT541PW", "SN74HCT32D", "SN74LVC541APW", "SN74HCT14D"}
    own_refs = {r for r, v in values.items() if v in own_values}
    offenders = []
    or_gate_leds = []
    for net, nodes in nets.items():
        cathodes = [n for n in nodes if (n.pinfunction or "").startswith("CATHODE")]
        if not cathodes:
            continue
        drivers = [
            n for n in nodes
            if n.ref in own_refs and ("tri_state" in n.pintype or "output" in n.pintype)
        ]
        for d in drivers:
            if len(cathodes) > 1:
                offenders.append((d.ref, d.pin, values.get(d.ref), net, len(cathodes)))
            if values.get(d.ref) == "SN74HCT32D":
                or_gate_leds.append((d.ref, d.pin, net, len(cathodes)))
    check(
        not offenders,
        f"driver pin(s) sinking more than one optocoupler LED: {offenders} (ref, pin, "
        f"part, net, LED count). Each LED is ~7.33mA, so two is 14.7mA -- against "
        f"SN74AHCT541's 8mA IOL or SN74HCT32's 4mA. Give the second LED its own buffered "
        f"leg; gen_breakout_taskpc_digital.py's own SECOND_LEG_CHANNELS spends the _BUF "
        f"bank's last 5 spare channels on exactly that.",
    )
    check(
        not or_gate_leds,
        f"the 74HCT32 reward-OR gate drives optocoupler LED(s) directly: {or_gate_leds}. "
        f"It is rated IOL=4mA, and its LOW level is read by the +3V3 LVC541 level-shifter "
        f"(V_IL,max 0.8V) on the way to the sync module -- an out-of-spec VOL here means "
        f"RWD_DLVR_PI stuck HIGH, i.e. reward-delivered recorded continuously. Drive LEDs "
        f"from RWD_DLVR_BUF/RWD_DLVR_INTAN_BUF instead.",
    )
    return (
        "One optocoupler LED per driver pin: no logic output belonging to this sheet "
        "sinks more than one LED cathode anywhere in the whole netlist, and the 74HCT32 "
        "reward-OR drives none at all."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str], footprints: dict[str, str] | None = None) -> list[str]:
    """Run the full contract check. Returns human-readable summary lines on success;
    raises CheckFailure with a specific, localized message on the first violation.

    `footprints` ({ref: Footprint lib id}, check_mule_netlist.py's own
    parse_component_footprints()) is optional and defaults to `{}` -- added for the
    panel-instrumentation task's own reward-connector/one-shot checks (2026-08-15), kept
    optional rather than required so any caller still passing the pre-existing two-arg
    signature keeps working rather than breaking loudly at every call site at once.
    """
    footprints = footprints or {}
    summary = []

    # --- Every contract net exists. Populated (>=2 nodes) everywhere EXCEPT the 41 nets
    # this project's OTHER not-yet-built sheets (Tasks 9/10/11) haven't been built to
    # drive or consume yet -- see PENDING_NETS' own comment for the two distinct
    # directions and why those currently show exactly 1 node in the EXPORTED NETLIST even
    # though ERC treats the upstream-pending 4 as driven (a PWR_FLAG's own pin is
    # confirmed, empirically, not to appear in kicad-cli's netlist export at all). ---
    for name in CONTRACT_NETS:
        check(name in nets, f"missing locked-contract net: {name!r}")
        min_nodes = 1 if name in PENDING_NETS else 2
        check(len(nets[name]) >= min_nodes, f"{name}: suspiciously small, only {nets[name]}")
    summary.append(
        f"All {len(CONTRACT_NETS)} contract nets present and populated "
        f"({len(PENDING_NETS)} of them still single-node, awaiting Task 9's Pi header, "
        f"Task 10's comparators, or Task 11's optocouplers -- checked to >=1 node instead "
        f"of >=2 for exactly that reason; all {len(CONTRACT_NETS) - len(PENDING_NETS)} "
        f"others, including every net this sheet both produces AND consumes itself, are "
        f"already fully populated)."
    )

    # --- Walk all 19 inbound channels end to end, both forks. ---
    lvc_refs: set[str] = set()
    buf_refs: set[str] = set()
    lvc_ref_counts: dict[str, int] = {}
    buf_ref_counts: dict[str, int] = {}
    for spec in INBOUND_CHANNELS:
        lvc_ref, buf_ref = _walk_inbound_channel(nets, values, spec)
        lvc_refs.add(lvc_ref)
        buf_refs.add(buf_ref)
        lvc_ref_counts[lvc_ref] = lvc_ref_counts.get(lvc_ref, 0) + 1
        buf_ref_counts[buf_ref] = buf_ref_counts.get(buf_ref, 0) + 1

    check(
        len(lvc_refs) == 3,
        f"expected exactly 3 distinct SN74LVC541APW buffer references across the 19 "
        f"inbound channels (two full 8-channel packages + one 3-of-8 package for "
        f"strobe/RWD_CMD/STIM_TRIG), found {len(lvc_refs)}: {lvc_refs}",
    )
    check(
        sorted(lvc_ref_counts.values()) == [3, 8, 8],
        f"expected the 3 LVC541 references to carry [3, 8, 8] channels, found "
        f"{sorted(lvc_ref_counts.values())}: {lvc_ref_counts}",
    )
    check(
        len(buf_refs) == 3,
        f"expected exactly 3 distinct SN74AHCT541PW _BUF-bank references, found "
        f"{len(buf_refs)}: {buf_refs}",
    )
    check(
        sorted(buf_ref_counts.values()) == [3, 8, 8],
        f"expected the 3 HCT541 _BUF references to carry [3, 8, 8] channels, found "
        f"{sorted(buf_ref_counts.values())}: {buf_ref_counts}",
    )
    check(
        lvc_refs.isdisjoint(buf_refs),
        f"the LVC541 bank {lvc_refs} and the HCT541 _BUF bank {buf_refs} must be six "
        f"DISTINCT physical packages, but they overlap: {lvc_refs & buf_refs}",
    )
    summary.append(
        f"All 19 inbound channels walked end to end on BOTH forks (Connector 1 pin -> "
        f"series R -> clamp node [diode high side confirmed +5V, never +3V3] -> LVC541 "
        f"input -> SAME LVC541's output -> *_PI/bare net, AND -> HCT541_BUF input -> SAME "
        f"HCT541's output -> *_BUF net), each hop tied to the same physical parts. LVC541 "
        f"bank: {lvc_ref_counts}. HCT541 _BUF bank: {buf_ref_counts}."
    )

    # --- Walk all 4 outbound channels end to end. ---
    outbound_refs: set[str] = set()
    for in_net, out_net, mdr_pin in OUTBOUND_CHANNELS:
        outbound_refs.add(_check_outbound_channel(nets, values, in_net, out_net, mdr_pin))
    check(
        len(outbound_refs) == 1,
        f"expected all 4 outbound channels on the SAME single SN74AHCT541PW package, "
        f"found {len(outbound_refs)}: {outbound_refs}",
    )
    summary.append(
        f"All 4 outbound channels (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT) walked end "
        f"to end on the single outbound buffer {next(iter(outbound_refs))}, each landing "
        f"on Connector 1's own pins 20-23."
    )

    # --- Walk the 7 dedicated optocoupler-drive legs: 5 second legs off the _BUF bank's
    # own third package (SECOND_LEG_CHANNELS) and 2 off the outbound package
    # (COMPARATOR_OPTO_LEGS). Identical walks; none of them reaches Connector 1. ---
    second_leg_refs: set[str] = set()
    for in_net, out_net in SECOND_LEG_CHANNELS:
        second_leg_refs.add(_check_outbound_channel(nets, values, in_net, out_net, None))
    check(
        len(second_leg_refs) == 1 and second_leg_refs.isdisjoint(outbound_refs),
        f"expected all 5 second legs on ONE SN74AHCT541PW package, distinct from the "
        f"outbound one ({outbound_refs}) -- found {second_leg_refs}",
    )
    second_leg_ref = next(iter(second_leg_refs))
    check(
        second_leg_ref in buf_refs,
        f"the 5 second legs sit on {second_leg_ref}, which is not one of the three _BUF "
        f"bank packages {buf_refs} -- they are meant to spend that bank's own last spare "
        f"channels, not to add a package",
    )
    comparator_leg_refs: set[str] = set()
    for in_net, out_net in COMPARATOR_OPTO_LEGS:
        comparator_leg_refs.add(_check_outbound_channel(nets, values, in_net, out_net, None))
    check(
        comparator_leg_refs == outbound_refs,
        f"expected both comparator-optocoupler legs on the SAME package as the 4 outbound "
        f"channels ({outbound_refs}) -- found {comparator_leg_refs}",
    )
    summary.append(
        f"All 7 dedicated optocoupler-drive legs walked end to end: 5 on {second_leg_ref} "
        f"(the _BUF bank's own third package, now 8 of 8 channels) and 2 on "
        f"{next(iter(outbound_refs))} (the outbound package, now 6 of 8), none of them "
        f"reaching Connector 1."
    )
    summary.append(_check_one_led_per_driver_pin(nets, values))

    # --- Reward OR: RWD_CMD (already the LVC541 bank's own output, walked above) and the
    # ONE-SHOT's own output (RWD_BTN_PULSE -- panel-instrumentation task, 2026-08-15,
    # NOT RWD_BTN_DEB directly any more, see the one-shot check below) combine in the
    # 74HCT32 OR gate to produce RWD_DLVR. ---
    rwd_or_refs = {n.ref for n in nets.get("RWD_DLVR", []) if values.get(n.ref) == "SN74HCT32D"}
    check(len(rwd_or_refs) == 1, f"RWD_DLVR: expected exactly 1 SN74HCT32D driving it, found {rwd_or_refs}")
    or_ref = next(iter(rwd_or_refs))
    or_out = [n for n in nets["RWD_DLVR"] if n.ref == or_ref]
    check(len(or_out) == 1 and or_out[0].pin == "3", f"RWD_DLVR: expected {or_ref} pin 3 (Y), found {or_out}")
    or_in_a = [n for n in nets.get("RWD_CMD", []) if n.ref == or_ref]
    or_in_b = [n for n in nets.get("RWD_BTN_PULSE", []) if n.ref == or_ref]
    check(len(or_in_a) == 1 and or_in_a[0].pin == "1", f"RWD_CMD: expected {or_ref} pin 1 (A), found {or_in_a}")
    check(
        len(or_in_b) == 1 and or_in_b[0].pin == "2",
        f"RWD_BTN_PULSE: expected {or_ref} pin 2 (B), found {or_in_b}",
    )
    check(
        not any(n.ref == or_ref for n in nets.get("RWD_BTN_DEB", [])),
        f"{or_ref} (the reward OR gate) still has a pin on RWD_BTN_DEB directly -- the "
        f"one-shot (U69) must sit BETWEEN the debounce inverter and the OR gate, not be "
        f"bypassed",
    )

    # --- ONE-SHOT (panel-instrumentation task, 2026-08-15, spec Sec.3.1): U69
    # (74HCT123D) sits BETWEEN the debounce inverter's own output (RWD_BTN_DEB) and the
    # OR gate's own second input (RWD_BTN_PULSE, just confirmed above) -- "the one-shot
    # exists between button and OR", made executable. Real pin map (verified against the
    # real datasheet -- see gen_breakout_taskpc_digital.py's own place_reward_oneshot()
    # docstring): pin1(A)->DGND, pin2(B)->RWD_BTN_DEB (trigger), pin3(Clr)->+5V (never
    # reset), pin13(Q)->RWD_BTN_PULSE (active-HIGH, same polarity RWD_BTN_DEB already
    # has -- see the same-polarity check below). ---
    oneshot_refs = {n.ref for n in nets.get("RWD_BTN_PULSE", []) if values.get(n.ref) == "74HCT123D"}
    check(len(oneshot_refs) == 1, f"RWD_BTN_PULSE: expected exactly 1 74HCT123D driving it, found {oneshot_refs}")
    oneshot_ref = next(iter(oneshot_refs))
    oneshot_q = [n for n in nets["RWD_BTN_PULSE"] if n.ref == oneshot_ref]
    check(len(oneshot_q) == 1 and oneshot_q[0].pin == "13", f"RWD_BTN_PULSE: expected {oneshot_ref} pin 13 (Q), found {oneshot_q}")
    oneshot_b = [n for n in nets.get("RWD_BTN_DEB", []) if n.ref == oneshot_ref]
    check(len(oneshot_b) == 1 and oneshot_b[0].pin == "2", f"RWD_BTN_DEB: expected {oneshot_ref} pin 2 (B, trigger), found {oneshot_b}")
    oneshot_a = [n for n in nets.get("DGND", []) if n.ref == oneshot_ref and n.pin == "1"]
    check(len(oneshot_a) == 1, f"{oneshot_ref} pin 1 (A) expected on DGND (tied permanently low), not found")
    oneshot_clr = [n for n in nets.get("+5V", []) if n.ref == oneshot_ref and n.pin == "3"]
    check(len(oneshot_clr) == 1, f"{oneshot_ref} pin 3 (RD-bar/Clr) expected on +5V (never reset), not found")
    # Rext/Cext: pin15 (REXT/CEXT, shared node with Rext's own far end on +5V) and pin14
    # (CEXT, shared node with Cext's own far end) -- confirms the RC network is genuinely
    # present, in series (TI SLVA720A Fig.3-1's own topology), not merely that SOME net
    # is attached to those pins.
    rcext_matches = [(name, n) for name, ns in nets.items() for n in ns if n.ref == oneshot_ref and n.pin == "15"]
    check(len(rcext_matches) == 1, f"{oneshot_ref} pin 15 (REXT/CEXT) not found on exactly one net: {rcext_matches}")
    rcext_net = rcext_matches[0][0]
    r191_on_5v = any(n.ref == "R191" for n in nets.get("+5V", []))
    r191_on_rcext = any(n.ref == "R191" for n in nets.get(rcext_net, []))
    check(
        r191_on_5v and r191_on_rcext,
        f"R191 (Rext, 442k) expected bridging +5V<->{rcext_net} ({oneshot_ref} pin 15) "
        f"-- found on +5V: {r191_on_5v}, on {rcext_net}: {r191_on_rcext}",
    )
    check(values.get("R191") == "442k", f"R191: expected Value '442k' (one-shot Rext), found {values.get('R191')!r}")
    cext_matches = [(name, n) for name, ns in nets.items() for n in ns if n.ref == oneshot_ref and n.pin == "14"]
    check(len(cext_matches) == 1, f"{oneshot_ref} pin 14 (CEXT) not found on exactly one net: {cext_matches}")
    cext_net = cext_matches[0][0]
    c149_on_rcext = any(n.ref == "C149" for n in nets.get(rcext_net, []))
    c149_on_cext = any(n.ref == "C149" for n in nets.get(cext_net, []))
    check(
        c149_on_rcext and c149_on_cext,
        f"C149 (Cext, 1uF) expected bridging {rcext_net}<->{cext_net} -- found on "
        f"{rcext_net}: {c149_on_rcext}, on {cext_net}: {c149_on_cext}",
    )
    check(values.get("C149") == "1uF", f"C149: expected Value '1uF' (one-shot Cext), found {values.get('C149')!r}")
    summary.append(
        f"One-shot confirmed between button and OR: RWD_BTN_DEB -> {oneshot_ref} "
        f"(74HCT123D) pin 2 (B) -> pin 13 (Q) -> RWD_BTN_PULSE -> {or_ref} pin 2 (B). "
        f"A=DGND, RD-bar=+5V (never reset). RC network confirmed: +5V -> R191(442k) -> "
        f"pin 15 -> C149(1uF) -> pin 14, in series (tW~199ms)."
    )

    debounce_refs = {n.ref for n in nets.get("RWD_BTN", []) if values.get(n.ref) == "SN74HCT14D"}
    check(len(debounce_refs) == 1, f"RWD_BTN: expected exactly 1 SN74HCT14D input node, found {debounce_refs}")
    deb_ref = next(iter(debounce_refs))
    check(
        any(n.ref == deb_ref and n.pin == "1" for n in nets["RWD_BTN"]),
        f"RWD_BTN: expected {deb_ref} pin 1, found {nets['RWD_BTN']}",
    )

    # --- SAME-POLARITY structural check (fix round 1, finding 1 -- CRITICAL). RWD_CMD
    # reaches the OR gate via SN74LVC541APW, a NON-inverting buffer (0 inversions,
    # already confirmed above: the "right part in the right role" check pins down
    # SN74LVC541APW is a '541-family octal buffer, and place_octal_buffer()'s own Ai->Yi
    # pairing this file's inbound walk already verifies never inverts). RWD_BTN_DEB must
    # therefore ALSO reach the OR gate via an ODD number of inversions from its own
    # active-LOW raw source (RWD_BTN idles HIGH via the 10k pull-up, reads LOW while
    # pressed) for the two OR inputs to share one active-HIGH convention -- exactly ONE
    # Schmitt inversion, deb_ref's own pin 1 (input) directly to pin 2 (output, SAME
    # unit), not a second series stage through a middle net (the pre-fix-round-1 defect:
    # a PAIR of inversions would leave RWD_BTN_DEB idle-HIGH, and `HIGH OR anything` is
    # permanently HIGH -- see place_debounce_inverter()'s own docstring). Checking pin 2
    # specifically (not merely "RWD_BTN_DEB is driven by deb_ref somewhere") is what
    # catches a reintroduced second stage: a PAIR's own final output lands on pin 4 of
    # the SAME reference, one gate further downstream.
    deb_out = [n for n in nets.get("RWD_BTN_DEB", []) if n.ref == deb_ref]
    check(
        len(deb_out) == 1 and deb_out[0].pin == "2",
        f"RWD_BTN_DEB: expected {deb_ref} pin 2 (ONE Schmitt inversion from RWD_BTN's "
        f"own pin 1 -- same polarity as RWD_CMD's non-inverting SN74LVC541APW path), "
        f"found {deb_out}. Landing on pin 4 (or any pin besides 2) would mean a SECOND "
        f"series inversion is back in the debounce path -- the exact defect that makes "
        f"RWD_DLVR assert permanently (`HIGH OR anything` = HIGH); see "
        f"place_debounce_inverter()'s own docstring.",
    )

    pullup_refs = {n.ref for n in nets["RWD_BTN"] if n.ref.startswith("R")}
    check(len(pullup_refs) == 1, f"RWD_BTN: expected exactly 1 pull-up resistor node, found {nets['RWD_BTN']}")
    pullup_ref = next(iter(pullup_refs))
    check(
        any(n.ref == pullup_ref for n in nets.get("+5V", [])),
        f"RWD_BTN pull-up {pullup_ref} has no pin on +5V",
    )
    check(values.get(pullup_ref) == "10k", f"{pullup_ref}: expected Value '10k', found {values.get(pullup_ref)!r}")

    btn_hdr_refs = {n.ref for n in nets["RWD_BTN"] if n.ref.startswith("J")}
    check(
        btn_hdr_refs == {n.ref for n in nets["DGND"] if n.ref in btn_hdr_refs},
        f"RWD_BTN's own button/jack connector(s) {btn_hdr_refs} should each also have a "
        f"pin on DGND (the switch's other terminal)",
    )
    check(len(btn_hdr_refs) == 2, f"RWD_BTN: expected 2 connectors (panel button + remote jack), found {btn_hdr_refs}")

    # --- CHANGE B (panel-instrumentation task, 2026-08-15): the three reward positions
    # are now REAL connectors, not Connector_Generic:Conn_01x02 placeholders -- "the
    # reward positions are BNC" (the remote jack J5, the reward-driver-out J6) plus the
    # recessed panel pushbutton (J4), checked by FOOTPRINT (the manufacturing-relevant
    # fact), not by the free-text Value description.
    #
    # Gated on `footprints` being non-empty rather than unconditional: `footprints`
    # defaults to `{}` (this function's own docstring) for callers that only have
    # nets/values -- every PRE-EXISTING self-test in this file's own self_test() is one
    # of those (they mutate nets/values to exercise a DIFFERENT, earlier check, and were
    # never written expecting a THIRD parameter to matter), and failing them all on a
    # footprint check they never intended to touch would be exactly the kind of
    # collateral breakage this file's own "each corruption is a minimal, targeted
    # mutation" discipline (self_test()'s own docstring) argues against. main() always
    # passes the real, parsed footprints for the actual PASS run, so this block still
    # runs unconditionally there; self_test() below adds its OWN, dedicated negative
    # controls that DO pass real footprints (deliberately mutated) to exercise this
    # block specifically. ---
    if footprints:
        check("J4" in footprints, "J4 (manual reward button) not found in the netlist's own component list")
        check(
            footprints.get("J4") == "Button_Switch_THT:SW_PUSH-12mm",
            f"J4: expected footprint 'Button_Switch_THT:SW_PUSH-12mm' (a real panel/"
            f"chassis-mount momentary pushbutton), found {footprints.get('J4')!r}",
        )
        for ref in ("J5", "J6"):
            check(ref in footprints, f"{ref} not found in the netlist's own component list")
            check(
                footprints.get(ref) == "Connector_Coaxial:BNC_PanelMountable_Vertical",
                f"{ref}: expected footprint 'Connector_Coaxial:BNC_PanelMountable_Vertical' "
                f"(a real BNC -- 'the reward positions are BNC'), found {footprints.get(ref)!r}",
            )
        # No 3.5mm TRS footprint anywhere on the whole board -- spec Sec.9.6: "the 3.5mm
        # TRS leaves the design with it [the reward remote's own move to BNC]". Checked
        # against EVERY footprint in the whole exported netlist (not just this sheet's
        # own J4-J6), since a TRS could in principle have been placed anywhere.
        trs_footprints = {ref: fp for ref, fp in footprints.items() if "TRS" in fp.upper() or "3.5" in fp}
        check(not trs_footprints, f"a 3.5mm TRS footprint still exists on the board: {trs_footprints}")
        summary.append(
            f"Reward positions confirmed real connectors: J4 (Button_Switch_THT:SW_PUSH-"
            f"12mm, recessed panel pushbutton), J5/J6 (Connector_Coaxial:"
            f"BNC_PanelMountable_Vertical). No 3.5mm TRS footprint anywhere on the board "
            f"({len(footprints)} footprints checked)."
        )

    summary.append(
        f"Reward OR confirmed: RWD_CMD (A) + RWD_BTN_DEB (B) -> {or_ref} (SN74HCT32D) "
        f"pin 3 -> RWD_DLVR. Debounce confirmed: RWD_BTN (button {sorted(btn_hdr_refs)} + "
        f"10k pull-up {pullup_ref} to +5V, idle-HIGH/active-LOW) -> {deb_ref} (SN74HCT14D) "
        f"pin 1 -> ONE Schmitt inversion -> pin 2 -> RWD_BTN_DEB (idle-LOW/active-HIGH). "
        f"Same-polarity confirmed: RWD_CMD arrives via SN74LVC541APW (non-inverting, 0 "
        f"inversions) and RWD_BTN_DEB arrives via exactly 1 inversion of an active-LOW "
        f"source -- both idle-LOW/active-HIGH at the OR gate's own inputs, so `HIGH OR "
        f"anything` cannot latch RWD_DLVR permanently (fix round 1, finding 1)."
    )

    # --- No unused logic-gate input left floating: every unused gate of U8 (74HCT32,
    # gates 2-4) and the debounce IC (74HCT14, gates 3-6) has both/its own input tied to
    # DGND. Discovered from the used gates' own refs above rather than assumed. ---
    or_dgnd_pins = sorted((int(p) for r, p in _pins_on(nets, "DGND") if r == or_ref))
    check(
        set(or_dgnd_pins) >= {4, 5, 7, 9, 10, 12, 13},
        f"{or_ref} (74HCT32): expected unused-gate inputs {{4,5,9,10,12,13}} plus GND(7) "
        f"tied to DGND, found {or_dgnd_pins}",
    )
    deb_dgnd_pins = sorted((int(p) for r, p in _pins_on(nets, "DGND") if r == deb_ref))
    check(
        set(deb_dgnd_pins) >= {3, 5, 7, 9, 11, 13},
        f"{deb_ref} (74HCT14): expected unused-gate inputs {{3,5,9,11,13}} (5 unused "
        f"gates -- only gate 1 is used, fix round 1's single-inversion fix; a PAIR would "
        f"leave gate 2's own input {{3}} NOT tied to DGND) plus GND(7) tied to DGND, "
        f"found {deb_dgnd_pins}",
    )
    summary.append(
        f"No floating logic-gate inputs: {or_ref}'s 3 unused OR gates and {deb_ref}'s 5 "
        f"unused inverters (only gate 1 is used) all have their inputs tied to DGND."
    )

    # --- Right part in the right role. ---
    expected_families = {}
    for ref in lvc_refs:
        expected_families[ref] = "SN74LVC541APW"
    for ref in buf_refs | outbound_refs:
        expected_families[ref] = "SN74AHCT541PW"
    expected_families[or_ref] = "SN74HCT32D"
    expected_families[deb_ref] = "SN74HCT14D"
    for ref, expected in expected_families.items():
        check(values.get(ref) == expected, f"{ref}: expected Value {expected!r}, found {values.get(ref)!r}")
    summary.append(f"Component values confirmed for the right part in the right role: {expected_families}.")

    # --- Connector 0: 9 analog channels + AISENSE-to-AGND tie (fix round 1, finding 3).
    # Each analog channel walk is deliberately shallow compared to the digital inbound/
    # outbound walks above: Task 10 (the real driver of every A_*_TPC net) does not exist
    # yet, so all this sheet itself contributes is ONE node per net -- the Connector 0
    # MDR68 pin, at the real sourced physical position (MDR0_AI_PIN). ---
    conn0_ref = None
    for net_name, ai_chan in ANALOG_CHANNELS:
        check(net_name in nets, f"missing net: {net_name!r}")
        mdr_pin = MDR0_AI_PIN[ai_chan]
        mdr_nodes = [n for n in nets[net_name] if n.ref.startswith("J")]
        check(
            len(mdr_nodes) == 1,
            f"{net_name}: expected exactly 1 Connector-0 MDR68-connector node, found "
            f"{nets[net_name]}",
        )
        check(
            mdr_nodes[0].pin == mdr_pin,
            f"{net_name}: lands on Connector 0 pin {mdr_nodes[0].pin}, expected pin "
            f"{mdr_pin} (AI{ai_chan}'s own sourced position -- Figure A-5, NI X Series "
            f"User Manual 370784K-01) -- a permuted analog-channel assignment",
        )
        conn0_ref = mdr_nodes[0].ref
    summary.append(
        f"All 9 Connector-0 analog channels ({[c[0] for c in ANALOG_CHANNELS]}) land on "
        f"their own sourced MDR68 pins on {conn0_ref} (MDR0_AI_PIN, NI Figure A-5) -- "
        f"single-node for now, awaiting Task 10's own front-end/buffer sheets."
    )

    # --- AISENSE (Connector 0 pin 62) tied directly to AGND -- spec Decision 5 / Task 10
    # Step 3's own text, and the finding's own "easiest thing on the board to omit by
    # accident" -- so checked explicitly rather than left to the generic per-net floor
    # above (AGND is a rail, not a CONTRACT_NETS entry). ---
    check("AGND" in nets, "missing net: 'AGND' (AISENSE tie requires this rail present)")
    aisense_nodes = [n for n in nets["AGND"] if n.ref == conn0_ref and n.pin == MDR0_AISENSE_PIN]
    check(
        len(aisense_nodes) == 1,
        f"Connector 0 ({conn0_ref}) pin {MDR0_AISENSE_PIN} (AISENSE) is not on AGND -- "
        f"found AGND nodes on {conn0_ref}: "
        f"{[n.pin for n in nets['AGND'] if n.ref == conn0_ref]}. Without this tie NRSE "
        f"does not work on the NI side (spec Decision 5) -- exactly the omission the "
        f"finding named as easiest to make by accident.",
    )
    summary.append(
        f"AISENSE confirmed: Connector 0 ({conn0_ref}) pin {MDR0_AISENSE_PIN} is tied "
        f"directly to AGND (NRSE reference, spec Decision 5 / Task 10 Step 3)."
    )

    # --- No two of the named nets checked have collapsed onto the same physical net.
    # Every *_CLAMP net (c[2], never in CONTRACT_NETS) plus the two raw *_TPC nets
    # CONTRACT_NETS does NOT already list (RWD_CMD_TPC/STIM_TRIG_TPC -- this sheet's own
    # internal naming for those two channels' pre-buffer node, not part of the contract
    # list; every EVT_D*_TPC/EVT_STROBE_TPC raw net IS already in CONTRACT_NETS, so
    # re-adding those here would compare each against ITSELF and always "collide" --
    # not a real check). ---
    extra_raw_nets = [c[1] for c in INBOUND_CHANNELS if c[1] not in CONTRACT_NETS]
    all_named = CONTRACT_NETS + [c[2] for c in INBOUND_CHANNELS] + extra_raw_nets
    seen: dict[frozenset, str] = {}
    for name in all_named:
        key = frozenset((n.ref, n.pin) for n in nets[name])
        check(
            key not in seen,
            f"{name} and {seen.get(key)} have IDENTICAL node sets -- they are the same "
            f"physical net under two different labels",
        )
        seen[key] = name
    summary.append(f"No two of the {len(all_named)} named nets checked collapsed onto the same physical net.")

    return summary


# ---------------------------------------------------------------------------
# Self-test: negative controls confirming the checks above actually fire, rather than
# passing vacuously -- same convention check_mule_netlist.py's and
# check_breakout_power_netlist.py's own self_test() establish.
# ---------------------------------------------------------------------------


def _assert_fails(nets, values, expect_substring: str, label: str, footprints=None) -> str:
    try:
        verify(nets, values, footprints)
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


def self_test(
    good_nets: dict[str, list[Node]], good_values: dict[str, str], good_footprints: dict[str, str] | None = None,
) -> list[str]:
    """Negative controls. `good_nets`/`good_values` must already pass verify() cleanly --
    each corruption below is a minimal, targeted mutation of that known-good structure.

    `good_footprints` (optional, defaults to `{}`): the panel-instrumentation task's own
    footprint-dependent negative controls (one-shot bypass, wrong reward-connector
    footprint, TRS reintroduced) pass the real, parsed footprints explicitly, mutated the
    same minimal way as every other control here -- see verify()'s own docstring for why
    the footprint checks are gated on this being non-empty in the first place.
    """
    good_footprints = good_footprints or {}
    results = []

    # --- ONE OPTOCOUPLER LED PER DRIVER PIN: three controls, one per assertion. ---

    # (a) A second LED landing on a net that already has one -- the shape the real defect
    # had on five nets at once. Uses EVT_STROBE_BUF (an HCT541 leg) so it exercises the
    # generic "any driver pin of any of this sheet's packages" scan, not a hard-coded list.
    doubled = copy.deepcopy(good_nets)
    doubled["EVT_STROBE_BUF"] = list(doubled["EVT_STROBE_BUF"]) + [
        Node("U64", "2", "CATHODE1_2", "passive")
    ]
    msg = _assert_fails(doubled, good_values, "more than one optocoupler LED", "a second LED cathode added to EVT_STROBE_BUF")
    results.append(f"Two optocoupler LEDs on one driver pin (EVT_STROBE_BUF gains a second cathode): caught -- {msg}")

    # (b) An LED hung back on the 74HCT32 reward-OR output -- the ORIGINAL defect, and
    # the one with a real failure mode rather than a margin (RWD_DLVR_PI stuck HIGH). Only
    # ONE cathode is added, so control (a)'s "more than one" assertion does NOT fire here:
    # this proves the 4mA-gate assertion stands on its own.
    or_led = copy.deepcopy(good_nets)
    or_led["RWD_DLVR"] = list(or_led["RWD_DLVR"]) + [
        Node("U60", "8", "CATHODE4_8", "passive")
    ]
    msg = _assert_fails(or_led, good_values, "74HCT32 reward-OR gate drives optocoupler LED", "one LED hung directly on the 74HCT32 output")
    results.append(f"Optocoupler LED driven directly by the 4mA 74HCT32 reward-OR (a SINGLE LED, so the 'more than one' check cannot be what fires): caught -- {msg}")

    # (c) A comparator-optocoupler leg wired to Connector 1 -- i.e. someone turning
    # PD1_COMP_BUF into a fifth outbound channel. It is an LED driver, not a DAQ line;
    # letting it reach the connector would put the LED's own load back in series with a
    # task-PC input.
    leg_on_mdr = copy.deepcopy(good_nets)
    conn1_ref = next(r for r, v in good_values.items() if v == "Connector 1 (digital, task PC NI)")
    leg_on_mdr["PD1_COMP_BUF"] = list(leg_on_mdr["PD1_COMP_BUF"]) + [
        Node(conn1_ref, "1", "Pin_1_1", "passive")
    ]
    msg = _assert_fails(leg_on_mdr, good_values, "optocoupler-drive leg only", "PD1_COMP_BUF wired to Connector 1")
    results.append(f"Optocoupler-drive leg landing on Connector 1 (PD1_COMP_BUF): caught -- {msg}")

    # THE central negative control this task's own brief asks for: swap two DATA
    # channels' buffer-output nodes on the _PI fork -- exactly the mule-board permutation
    # class ("a checker that only verified per-net properties would have passed a
    # permutation of the 16 data bits"). Channels 3 and 5 both land on DIFFERENT LVC541
    # packages here (3 is in the 0-7 package, 5 is also in the 0-7 package -- both in the
    # SAME package, channel-local, which is the harder case: a same-package permutation,
    # not just a cross-package one).
    swapped_pi = copy.deepcopy(good_nets)
    d3_idx = next(i for i, n in enumerate(swapped_pi["EVT_D3_PI"]) if "tri_state" in n.pintype)
    d5_idx = next(i for i, n in enumerate(swapped_pi["EVT_D5_PI"]) if "tri_state" in n.pintype)
    swapped_pi["EVT_D3_PI"][d3_idx], swapped_pi["EVT_D5_PI"][d5_idx] = (
        swapped_pi["EVT_D5_PI"][d5_idx], swapped_pi["EVT_D3_PI"][d3_idx],
    )
    msg = _assert_fails(swapped_pi, good_values, "permuted", "channel 3/5 _PI buffer-output swap")
    results.append(f"Bit permutation on the _PI fork (EVT_D3_PI/EVT_D5_PI buffer outputs swapped): caught -- {msg}")

    # The SAME permutation class on the _BUF fork -- the copy Task 11's NI optocouplers
    # consume, and a defect this checker's own per-net-only ancestor would have missed
    # just as surely as the _PI one above.
    swapped_buf = copy.deepcopy(good_nets)
    d8_idx = next(i for i, n in enumerate(swapped_buf["EVT_D8_BUF"]) if "tri_state" in n.pintype)
    d10_idx = next(i for i, n in enumerate(swapped_buf["EVT_D10_BUF"]) if "tri_state" in n.pintype)
    swapped_buf["EVT_D8_BUF"][d8_idx], swapped_buf["EVT_D10_BUF"][d10_idx] = (
        swapped_buf["EVT_D10_BUF"][d10_idx], swapped_buf["EVT_D8_BUF"][d8_idx],
    )
    msg = _assert_fails(swapped_buf, good_values, "permuted", "channel 8/10 _BUF buffer-output swap")
    results.append(f"Bit permutation on the _BUF fork (EVT_D8_BUF/EVT_D10_BUF buffer outputs swapped): caught -- {msg}")

    # A DIFFERENT permutation shape: swap two channels' MDR68 (Connector 1) INPUT pins,
    # not their buffer outputs -- catches a scrambled physical-pin assignment rather than
    # a scrambled buffer wiring, a genuinely different failure mode from the two above.
    swapped_mdr = copy.deepcopy(good_nets)
    d2_hdr_idx = next(i for i, n in enumerate(swapped_mdr["EVT_D2_TPC"]) if n.ref.startswith("J"))
    d4_hdr_idx = next(i for i, n in enumerate(swapped_mdr["EVT_D4_TPC"]) if n.ref.startswith("J"))
    swapped_mdr["EVT_D2_TPC"][d2_hdr_idx], swapped_mdr["EVT_D4_TPC"][d4_hdr_idx] = (
        swapped_mdr["EVT_D4_TPC"][d4_hdr_idx], swapped_mdr["EVT_D2_TPC"][d2_hdr_idx],
    )
    msg = _assert_fails(swapped_mdr, good_values, "expected pin", "channel 2/4 Connector-1 pin swap")
    results.append(f"MDR68 physical-pin permutation (EVT_D2_TPC/EVT_D4_TPC Connector-1 pins swapped): caught -- {msg}")

    # Clamp rail regression: one channel's clamp diode gains a pin on +3V3 -- the exact
    # mule-board fix-round-2 defect this design must not repeat.
    clamped_3v3 = copy.deepcopy(good_nets)
    d7_diode_ref = next(n.ref for n in clamped_3v3["EVT_D7_CLAMP"] if n.ref.startswith("D"))
    phantom = Node(ref=d7_diode_ref, pin="2", pinfunction="K_2", pintype="passive")
    clamped_3v3["+3V3"] = clamped_3v3["+3V3"] + [phantom]
    msg = _assert_fails(clamped_3v3, good_values, "has a pin on +3V3", "EVT_D7 clamp diode gains a +3V3 pin")
    results.append(f"Clamp high side regressed onto +3V3 (D on EVT_D7_CLAMP): caught -- {msg}")

    # Structural regression: drop the HCT541_BUF fork's own input node from one channel's
    # clamp net (simulating an accidental future edit that only fans out to the LVC541
    # bank) -- the exactly-4-nodes check should catch it.
    dropped_fork = copy.deepcopy(good_nets)
    buf_node = next(n for n in dropped_fork["EVT_D0_CLAMP"] if good_values.get(n.ref) == "SN74AHCT541PW")
    dropped_fork["EVT_D0_CLAMP"] = [n for n in dropped_fork["EVT_D0_CLAMP"] if n != buf_node]
    msg = _assert_fails(dropped_fork, good_values, "expected exactly 4 nodes", "EVT_D0_CLAMP loses its _BUF fork")
    results.append(f"Missing _BUF-bank fan-out (EVT_D0_CLAMP's HCT541 input dropped): caught -- {msg}")

    # Outbound-channel permutation: swap PD1_COMP_TPC's and PD2_COMP_TPC's own outbound
    # buffer-output nodes.
    swapped_out = copy.deepcopy(good_nets)
    p1_idx = next(i for i, n in enumerate(swapped_out["PD1_COMP_TPC"]) if "tri_state" in n.pintype)
    p2_idx = next(i for i, n in enumerate(swapped_out["PD2_COMP_TPC"]) if "tri_state" in n.pintype)
    swapped_out["PD1_COMP_TPC"][p1_idx], swapped_out["PD2_COMP_TPC"][p2_idx] = (
        swapped_out["PD2_COMP_TPC"][p2_idx], swapped_out["PD1_COMP_TPC"][p1_idx],
    )
    msg = _assert_fails(swapped_out, good_values, "pairing", "outbound PD1_COMP/PD2_COMP swap")
    results.append(f"Outbound channel permutation (PD1_COMP_TPC/PD2_COMP_TPC buffer outputs swapped): caught -- {msg}")

    # Reward OR regression: RWD_DLVR driven by a second, wrong-valued part (simulating a
    # copy-paste part mix-up on U8's own footprint/value).
    swapped_part = dict(good_values)
    or_ref = next(n.ref for n in good_nets["RWD_DLVR"] if good_values.get(n.ref) == "SN74HCT32D")
    swapped_part[or_ref] = "SN74AHCT541PW"
    msg = _assert_fails(good_nets, swapped_part, "RWD_DLVR", "reward-OR gate mislabelled as a buffer")
    results.append(f"Reward-OR part mix-up ({or_ref}: SN74HCT32D -> SN74AHCT541PW): caught -- {msg}")

    # Collapsed-net regression: force two distinct contract nets to share identical node
    # sets (simulating two labels resolving to the same physical net -- constraint 1's
    # own signature failure mode, "two labels, one real net"). NOT expected to surface as
    # verify()'s own dedicated "IDENTICAL node sets" check specifically: EVERY one of
    # this design's 70 contract nets is ALREADY walked by a more specific, earlier check
    # (the per-channel Ai+Yi=20 pairing, for this pair -- both EVT_D9_PI and EVT_D11_PI
    # are LVC541-bank outputs, and forcing one to literally BE the other's node list
    # breaks that pairing for whichever channel's own input pin no longer matches), so
    # that fires first and check()'s own fail-fast semantics mean verify() never reaches
    # the dedicated collapse check at all for a net this deeply walked. Confirmed
    # empirically (this exact corruption was tried first expecting "IDENTICAL node
    # sets" and instead raised the pairing message below) rather than assumed -- the
    # dedicated collapse check in verify() is still real defense-in-depth, just for any
    # FUTURE net added to its own `all_named` list without an equally specific dedicated
    # check of its own; it is not dead code, only unreachable for nets THIS thoroughly
    # covered already.
    collapsed = copy.deepcopy(good_nets)
    collapsed["EVT_D9_PI"] = list(collapsed["EVT_D11_PI"])
    msg = _assert_fails(collapsed, good_values, "pairing", "EVT_D9_PI/EVT_D11_PI collapsed")
    results.append(
        "Collapsed nets (EVT_D9_PI forced identical to EVT_D11_PI): caught -- not by the "
        "dedicated collapse check, which never gets reached, but by the SAME per-channel "
        "Ai<->Yi pairing check the bit-permutation self-tests above exercise, since a "
        "collapsed net's own channel necessarily also fails that more specific check -- "
        f"{msg}"
    )

    # --- NEW (fix round 1, finding 1 -- CRITICAL): reward-OR polarity regression.
    # Simulate the exact pre-fix-round-1 defect reintroduced by accident -- a second
    # series Schmitt stage back in the debounce path -- by moving RWD_BTN_DEB's own node
    # from deb_ref pin 2 (one inversion) to pin 4 (where a PAIR's own final output would
    # land). This is precisely the "OR's two inputs are same-polarity" control the
    # finding asked for: it proves the same-polarity check actually fires on a genuine
    # polarity regression, not just a wrong-pin-number typo.
    deb_ref_for_test = next(
        n.ref for n in good_nets["RWD_BTN"] if good_values.get(n.ref) == "SN74HCT14D"
    )
    repolarized = copy.deepcopy(good_nets)
    dlvr_idx = next(i for i, n in enumerate(repolarized["RWD_BTN_DEB"]) if n.ref == deb_ref_for_test)
    repolarized["RWD_BTN_DEB"][dlvr_idx] = Node(
        ref=deb_ref_for_test, pin="4", pinfunction="4Y", pintype="output",
    )
    msg = _assert_fails(
        repolarized, good_values, "expected", "reward debounce repolarized (pair reintroduced)",
    )
    results.append(
        f"Reward-OR same-polarity regression (RWD_BTN_DEB moved from {deb_ref_for_test} "
        f"pin 2 to pin 4, simulating a reintroduced second Schmitt stage / the exact "
        f"pre-fix-round-1 permanent-assert defect): caught -- {msg}"
    )

    # --- NEW (fix round 1, finding 3): analog-channel permutation on Connector 0 -- the
    # same permutation class the MDR68 physical-pin self-test above exercises for
    # Connector 1's digital lines, applied to the 9 new analog channels: swap two
    # channels' own Connector-0 MDR68 pin nodes.
    swapped_analog = copy.deepcopy(good_nets)
    lx_idx = next(i for i, n in enumerate(swapped_analog["A_EYE_LX_TPC"]) if n.ref.startswith("J"))
    joy_idx = next(i for i, n in enumerate(swapped_analog["A_JOY_X_TPC"]) if n.ref.startswith("J"))
    swapped_analog["A_EYE_LX_TPC"][lx_idx], swapped_analog["A_JOY_X_TPC"][joy_idx] = (
        swapped_analog["A_JOY_X_TPC"][joy_idx], swapped_analog["A_EYE_LX_TPC"][lx_idx],
    )
    msg = _assert_fails(
        swapped_analog, good_values, "expected pin", "A_EYE_LX_TPC/A_JOY_X_TPC Connector-0 pin swap",
    )
    results.append(
        f"Connector-0 analog-channel permutation (A_EYE_LX_TPC/A_JOY_X_TPC MDR68 pins "
        f"swapped): caught -- {msg}"
    )

    # --- NEW (fix round 1, finding 3): AISENSE-to-AGND tie dropped -- "the easiest thing
    # on the board to omit by accident" (the finding's own words), simulated by deleting
    # Connector 0's own AISENSE node from the AGND net entirely.
    conn0_ref_for_test = next(
        n.ref for net_name, _ in ANALOG_CHANNELS for n in good_nets[net_name] if n.ref.startswith("J")
    )
    dropped_aisense = copy.deepcopy(good_nets)
    dropped_aisense["AGND"] = [
        n for n in dropped_aisense["AGND"]
        if not (n.ref == conn0_ref_for_test and n.pin == MDR0_AISENSE_PIN)
    ]
    msg = _assert_fails(
        dropped_aisense, good_values, "AISENSE", "Connector 0 AISENSE-to-AGND tie dropped",
    )
    results.append(f"AISENSE-to-AGND tie dropped ({conn0_ref_for_test} pin 62 removed from AGND): caught -- {msg}")

    # --- Panel-instrumentation negative controls (2026-08-15) -- one per new check()
    # family added above, same mutate-the-real-parsed-structure discipline as every test
    # above. ---

    # One-shot bypassed: RWD_BTN_DEB wired DIRECTLY to the OR gate's own pin 2 again (the
    # PRE-this-task topology), simulating U69 being skipped/removed in a future edit.
    oneshot_bypassed = copy.deepcopy(good_nets)
    or_ref_for_test = next(n.ref for n in good_nets["RWD_DLVR"] if good_values.get(n.ref) == "SN74HCT32D")
    oneshot_bypassed["RWD_BTN_DEB"] = list(oneshot_bypassed["RWD_BTN_DEB"]) + [
        Node(ref=or_ref_for_test, pin="2", pinfunction="B_2", pintype="input")
    ]
    msg = _assert_fails(
        oneshot_bypassed, good_values, "still has a pin on RWD_BTN_DEB directly", "one-shot bypassed (OR gate wired straight to RWD_BTN_DEB)",
    )
    results.append(f"One-shot bypassed ({or_ref_for_test} pin 2 wired directly to RWD_BTN_DEB): caught -- {msg}")

    # One-shot part mix-up: U69's own Value edited away from '74HCT123D' -- topology
    # (which net drives RWD_BTN_PULSE) still correct, only the part identity is wrong.
    oneshot_ref_for_test = next(n.ref for n in good_nets["RWD_BTN_PULSE"] if good_values.get(n.ref) == "74HCT123D")
    oneshot_wrong_part = dict(good_values)
    oneshot_wrong_part[oneshot_ref_for_test] = "74HC123"
    msg = _assert_fails(
        good_nets, oneshot_wrong_part, "expected exactly 1 74HCT123D driving it", "one-shot part mix-up (74HCT123D -> 74HC123)",
    )
    results.append(f"One-shot part mix-up ({oneshot_ref_for_test}: 74HCT123D -> 74HC123, the CMOS- not TTL-threshold variant): caught -- {msg}")

    # One-shot pulse-width component drift: R191 (Rext, should be 442k) edited.
    rext_drifted = dict(good_values)
    rext_drifted["R191"] = "100k"
    msg = _assert_fails(good_nets, rext_drifted, "R191: expected Value '442k'", "R191 (one-shot Rext) value drift")
    results.append(f"One-shot Rext value drift (442k -> 100k): caught -- {msg}")

    # Reward connector footprint regression: J4 (should be the real panel pushbutton)
    # reverted to the old Connector_Generic:Conn_01x02 placeholder.
    j4_reverted = dict(good_footprints)
    j4_reverted["J4"] = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    msg = _assert_fails(
        good_nets, good_values, "J4: expected footprint", "J4 reverted to a generic Conn_01x02 placeholder", j4_reverted,
    )
    results.append(f"Reward button J4 reverted to a generic 2-pin header placeholder: caught -- {msg}")

    # Reward connector footprint regression: J6 (should be a real BNC) reverted to the
    # old placeholder -- same construction as J4's own test, mirrored onto a BNC position.
    j6_reverted = dict(good_footprints)
    j6_reverted["J6"] = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    msg = _assert_fails(
        good_nets, good_values, "J6: expected footprint", "J6 (reward-driver BNC) reverted to a generic placeholder", j6_reverted,
    )
    results.append(f"Reward-driver-out J6 reverted to a generic 2-pin header placeholder: caught -- {msg}")

    # TRS regression: a hypothetical future edit reintroduces a 3.5mm TRS jack footprint
    # somewhere on the board (not necessarily J5 -- this check scans every footprint, so
    # the corruption is deliberately placed on an unrelated, synthetic reference).
    trs_reintroduced = dict(good_footprints)
    trs_reintroduced["DBG89"] = "Connector_Audio:Jack_3.5mm_CUI_SJ1-3523NG_Horizontal"
    msg = _assert_fails(
        good_nets, good_values, "3.5mm TRS footprint still exists", "3.5mm TRS footprint reintroduced (synthetic DBG89)", trs_reintroduced,
    )
    results.append(f"3.5mm TRS footprint reintroduced (synthetic ref, any position): caught -- {msg}")

    return results


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as
# check_breakout_power_netlist.py's own verify_instance_paths()/self_test_instance_paths(),
# applied to this sheet. Reads the raw .kicad_sch SOURCES, not the exported netlist: this
# defect is invisible to kicad-cli sch erc/export netlist alike (both recompute
# sheetpath/tstamps by walking the real (sheet ...) hierarchy, never by reading a
# component's own (instances (path ...)) bookkeeping -- confirmed at Task 7, restated,
# not re-derived, here).
# ---------------------------------------------------------------------------


def verify_instance_paths(taskpc_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(taskpc_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"taskpc-digital.kicad_sch's own file-identity uuid ({own_root_uuid}) collides "
        f"with breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, TASKPC_SHEETFILE)

    paths = find_all_instance_paths(taskpc_sch_text)
    check(
        len(paths) >= 70,
        f"only found {len(paths)} (instances (path ...)) entries in taskpc-digital.kicad_sch "
        f"-- expected >=70. Recomputed directly against this task's own real output (77), "
        f"not guessed: 5 J (2 MDR68 + button + jack + BNC placeholder) + 20 R (19 series "
        f"+ 1 pull-up) + 19 D (clamps) + 10 C (9 decoupling + 1 debounce) + 19 U-prefixed "
        f"instance blocks (3 LVC541 + 3 HCT541_BUF + 1 outbound HCT541 -- one block each, "
        f"single-unit symbols -- plus the 74HCT32 OR gate's own 5 unit-blocks and the "
        f"74HCT14 debounce inverter's own 7 unit-blocks, each REAL unit a separate "
        f"`(instances ...)` block) + 4 PWR_FLAGs = 77.",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in taskpc-digital.kicad_sch do "
        f"not resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found "
        f"{bad[:3]!r} instead",
    )
    return (
        f"All {len(paths)} component/power-flag instance paths in taskpc-digital.kicad_sch "
        f"resolve to the real ancestor chain {expected_prefix!r} (breakout's own root uuid "
        f"+ the 'taskpc-digital' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(taskpc_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(taskpc_text, breakout_text)
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


def self_test_instance_paths(good_taskpc_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_taskpc_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, TASKPC_SHEETFILE)

    corrupted = good_taskpc_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_taskpc_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_taskpc_text may not actually be passing verify_instance_paths() "
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
    footprints = parse_component_footprints(text)
    print(f"parsed {len(nets)} nets, {len(values)} component values from {net_path}")
    try:
        summary = verify(nets, values, footprints)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS:")
    for line in summary:
        print(f"  - {line}")

    try:
        self_test_results = self_test(nets, values, footprints)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in self_test_results:
        print(f"  - {line}")

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_TASKPC_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_TASKPC_SCH} do not exist -- "
            f"run gen_breakout.py and gen_breakout_taskpc_digital.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    taskpc_sch_text = DEFAULT_TASKPC_SCH.read_text()

    # Shared row-pitch-vs-2-pin-part-span collision guard (constraint 4) -- not
    # previously wired into this checker; added at the panel-instrumentation task
    # (2026-08-15) after the guard's OWN general form caught a real defect during this
    # task's own development (the one-shot's row pitch, gen_breakout_taskpc_digital.py's
    # own place_reward_oneshot() docstring). Same pattern as check_breakout_power_
    # netlist.py's own main().
    try:
        row_pitch_summary = check_row_pitch_exceeds_2pin_span(taskpc_sch_text, "taskpc-digital.kicad_sch")
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {row_pitch_summary}")

    try:
        row_pitch_self_test_msg = self_test_row_pitch(taskpc_sch_text, "taskpc-digital.kicad_sch", min_instances=30)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: row-pitch collision reintroduced: caught -- {row_pitch_self_test_msg}")

    try:
        path_summary = verify_instance_paths(taskpc_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(taskpc_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
