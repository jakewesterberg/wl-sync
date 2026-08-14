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
    parse_component_values,
    parse_netlist,
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
CONTRACT_NETS = (
    [f"EVT_D{i}_TPC" for i in range(16)] + ["EVT_STROBE_TPC"]
    + [f"EVT_D{i}_PI" for i in range(16)] + ["EVT_STROBE_PI"]
    + [f"EVT_D{i}_BUF" for i in range(16)] + ["EVT_STROBE_BUF"]
    + ["RWD_CMD", "RWD_CMD_BUF", "RWD_BTN", "RWD_DLVR", "STIM_TRIG", "STIM_TRIG_BUF"]
    + ["PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"]
)
assert len(CONTRACT_NETS) == 61

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
# MDR68-side output net, Connector 1 physical pin).
OUTBOUND_CHANNELS = [
    ("PD1_COMP", "PD1_COMP_TPC", "20"),
    ("PD2_COMP", "PD2_COMP_TPC", "21"),
    ("ACC_TRIG", "ACC_TRIG_TPC", "22"),
    ("RHS_STIM_OUT", "RHS_STIM_OUT_TPC", "23"),
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
# Excluded from the generic "contract net populated" floor below for exactly this reason,
# not because any of these 41 nets are unimportant -- see verify()'s own summary line,
# which names all of them.
PENDING_UPSTREAM_NETS = {"PD1_COMP", "PD2_COMP", "ACC_TRIG", "RHS_STIM_OUT"}
PENDING_DOWNSTREAM_NETS = (
    {f"EVT_D{i}_PI" for i in range(16)} | {"EVT_STROBE_PI"}
    | {f"EVT_D{i}_BUF" for i in range(16)} | {"EVT_STROBE_BUF", "RWD_CMD_BUF", "STIM_TRIG_BUF"}
    | {"STIM_TRIG"}
)
PENDING_NETS = PENDING_UPSTREAM_NETS | PENDING_DOWNSTREAM_NETS
assert len(PENDING_NETS) == 41, len(PENDING_NETS)

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
    mdr_pin = str(ch_idx + 1)

    # Hop 1: Connector 1 pin (sequential position ch_idx+1) -> series resistor.
    check(raw_net in nets, f"missing net: {raw_net!r}")
    raw_nodes = nets[raw_net]
    check(len(raw_nodes) == 2, f"{raw_net}: expected 2 nodes (MDR68 pin + series R), found {raw_nodes}")
    r_nodes = [n for n in raw_nodes if n.ref.startswith("R")]
    mdr_nodes = [n for n in raw_nodes if n.ref.startswith("J")]
    check(len(r_nodes) == 1, f"{raw_net}: expected exactly 1 resistor node, found {raw_nodes}")
    check(len(mdr_nodes) == 1, f"{raw_net}: expected exactly 1 MDR68-connector node, found {raw_nodes}")
    check(
        mdr_nodes[0].pin == mdr_pin,
        f"{raw_net}: lands on Connector 1 pin {mdr_nodes[0].pin}, expected sequential "
        f"pin {mdr_pin} (channel index {ch_idx})",
    )
    r_ref = r_nodes[0].ref

    # Hop 2: the SAME resistor's other leg, on the clamp node, alongside the clamp diode
    # AND both buffer banks' own input pins (the fan-out point) -- ties the series R
    # physically to this specific channel and confirms both buffer inputs really share
    # ONE protected node rather than two separate (and possibly cross-wired) ones.
    check(clamp_net in nets, f"missing net: {clamp_net!r}")
    clamp_nodes = nets[clamp_net]
    check(
        len(clamp_nodes) == 4,
        f"{clamp_net}: expected exactly 4 nodes (series R, clamp diode, LVC541 input, "
        f"HCT541_BUF input), found {len(clamp_nodes)}: {clamp_nodes}",
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

    # Hop 3: the two buffer-input nodes -- one LVC541 (the +3V3 bank), one HCT541 (the
    # +5V _BUF bank), identified by Value (not by reference number, which this checker
    # does not assume an ordering for).
    u_nodes = [n for n in clamp_nodes if n.ref.startswith("U")]
    check(len(u_nodes) == 2, f"{clamp_net}: expected exactly 2 buffer-input nodes, found {clamp_nodes}")
    lvc_nodes = [n for n in u_nodes if values.get(n.ref) == "SN74LVC541APW"]
    buf_nodes = [n for n in u_nodes if values.get(n.ref) == "SN74HCT541PW"]
    check(
        len(lvc_nodes) == 1 and len(buf_nodes) == 1,
        f"{clamp_net}: expected exactly 1 SN74LVC541APW input node and 1 SN74HCT541PW "
        f"input node, found values {[values.get(n.ref) for n in u_nodes]} on {u_nodes}",
    )
    lvc_ref, lvc_a_pin = lvc_nodes[0].ref, int(lvc_nodes[0].pin)
    buf_ref, buf_a_pin = buf_nodes[0].ref, int(buf_nodes[0].pin)
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
    nets: dict[str, list[Node]], values: dict[str, str], in_net: str, out_net: str, mdr_pin: str,
) -> str:
    """Walk one of the 4 outbound channels: input net (produced elsewhere, consumed here
    by name) -> this sheet's own SN74HCT541PW's input pin -> the SAME reference's own
    output pin (Ai+Yi=20) -> Connector 1's own pin. Returns the outbound buffer's own
    reference for the caller's aggregate checks.
    """
    check(in_net in nets, f"missing net: {in_net!r}")
    in_nodes = [n for n in nets[in_net] if values.get(n.ref) == "SN74HCT541PW"]
    check(
        len(in_nodes) == 1,
        f"{in_net}: expected exactly 1 SN74HCT541PW input node, found "
        f"{[(n.ref, n.pin, values.get(n.ref)) for n in nets[in_net]]}",
    )
    ref, a_pin = in_nodes[0].ref, int(in_nodes[0].pin)

    check(out_net in nets, f"missing net: {out_net!r}")
    out_nodes = [n for n in nets[out_net] if n.ref == ref and "tri_state" in n.pintype]
    check(
        len(out_nodes) == 1,
        f"{out_net}: expected exactly 1 tri_state output pin belonging to {ref} (the "
        f"same buffer whose input is on {in_net}), found {len(out_nodes)}",
    )
    y_pin = int(out_nodes[0].pin)
    check(
        a_pin + y_pin == 20,
        f"{out_net}: {ref}'s input pin {a_pin} (on {in_net}) and output pin {y_pin} don't "
        f"satisfy the 74x541 family's fixed Ai<->Yi pairing -- this outbound channel has "
        f"been permuted within the buffer",
    )
    mdr_nodes = [n for n in nets[out_net] if n.ref.startswith("J")]
    check(len(mdr_nodes) == 1, f"{out_net}: expected exactly 1 MDR68-connector node, found {nets[out_net]}")
    check(
        mdr_nodes[0].pin == mdr_pin,
        f"{out_net}: lands on Connector 1 pin {mdr_nodes[0].pin}, expected pin {mdr_pin}",
    )
    return ref


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    """Run the full contract check. Returns human-readable summary lines on success;
    raises CheckFailure with a specific, localized message on the first violation.
    """
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
        f"expected exactly 3 distinct SN74HCT541PW _BUF-bank references, found "
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
        f"expected all 4 outbound channels on the SAME single SN74HCT541PW package, "
        f"found {len(outbound_refs)}: {outbound_refs}",
    )
    summary.append(
        f"All 4 outbound channels (PD1_COMP, PD2_COMP, ACC_TRIG, RHS_STIM_OUT) walked end "
        f"to end on the single outbound buffer {next(iter(outbound_refs))}, each landing "
        f"on Connector 1's own pins 20-23."
    )

    # --- Reward OR: RWD_CMD (already the LVC541 bank's own output, walked above) and the
    # debounced RWD_BTN combine in the 74HCT32 OR gate to produce RWD_DLVR. ---
    rwd_or_refs = {n.ref for n in nets.get("RWD_DLVR", []) if values.get(n.ref) == "SN74HCT32D"}
    check(len(rwd_or_refs) == 1, f"RWD_DLVR: expected exactly 1 SN74HCT32D driving it, found {rwd_or_refs}")
    or_ref = next(iter(rwd_or_refs))
    or_out = [n for n in nets["RWD_DLVR"] if n.ref == or_ref]
    check(len(or_out) == 1 and or_out[0].pin == "3", f"RWD_DLVR: expected {or_ref} pin 3 (Y), found {or_out}")
    or_in_a = [n for n in nets.get("RWD_CMD", []) if n.ref == or_ref]
    or_in_b = [n for n in nets.get("RWD_BTN_DEB", []) if n.ref == or_ref]
    check(len(or_in_a) == 1 and or_in_a[0].pin == "1", f"RWD_CMD: expected {or_ref} pin 1 (A), found {or_in_a}")
    check(
        len(or_in_b) == 1 and or_in_b[0].pin == "2",
        f"RWD_BTN_DEB: expected {or_ref} pin 2 (B), found {or_in_b}",
    )

    debounce_refs = {n.ref for n in nets.get("RWD_BTN", []) if values.get(n.ref) == "SN74HCT14D"}
    check(len(debounce_refs) == 1, f"RWD_BTN: expected exactly 1 SN74HCT14D input node, found {debounce_refs}")
    deb_ref = next(iter(debounce_refs))
    check(
        any(n.ref == deb_ref and n.pin == "1" for n in nets["RWD_BTN"]),
        f"RWD_BTN: expected {deb_ref} pin 1, found {nets['RWD_BTN']}",
    )
    check("RWD_BTN_INV1" in nets, "missing net: 'RWD_BTN_INV1'")
    inv1_pins = {n.pin for n in nets["RWD_BTN_INV1"] if n.ref == deb_ref}
    check(
        inv1_pins == {"2", "3"},
        f"RWD_BTN_INV1: expected {deb_ref} pins {{2, 3}} (gate 1 output -> gate 2 input), "
        f"found {inv1_pins}",
    )
    deb_out = [n for n in nets.get("RWD_BTN_DEB", []) if n.ref == deb_ref]
    check(len(deb_out) == 1 and deb_out[0].pin == "4", f"RWD_BTN_DEB: expected {deb_ref} pin 4, found {deb_out}")

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

    summary.append(
        f"Reward OR confirmed: RWD_CMD (A) + RWD_BTN_DEB (B) -> {or_ref} (SN74HCT32D) "
        f"pin 3 -> RWD_DLVR. Debounce confirmed: RWD_BTN (button {sorted(btn_hdr_refs)} + "
        f"10k pull-up {pullup_ref} to +5V) -> {deb_ref} (SN74HCT14D) gate 1 -> "
        f"RWD_BTN_INV1 -> gate 2 -> RWD_BTN_DEB (2 series Schmitt stages, non-inverting)."
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
        set(deb_dgnd_pins) >= {5, 7, 9, 11, 13},
        f"{deb_ref} (74HCT14): expected unused-gate inputs {{5,9,11,13}} plus GND(7) tied "
        f"to DGND, found {deb_dgnd_pins}",
    )
    summary.append(
        f"No floating logic-gate inputs: {or_ref}'s 3 unused OR gates and {deb_ref}'s 4 "
        f"unused inverters all have their inputs tied to DGND."
    )

    # --- Right part in the right role. ---
    expected_families = {}
    for ref in lvc_refs:
        expected_families[ref] = "SN74LVC541APW"
    for ref in buf_refs | outbound_refs:
        expected_families[ref] = "SN74HCT541PW"
    expected_families[or_ref] = "SN74HCT32D"
    expected_families[deb_ref] = "SN74HCT14D"
    for ref, expected in expected_families.items():
        check(values.get(ref) == expected, f"{ref}: expected Value {expected!r}, found {values.get(ref)!r}")
    summary.append(f"Component values confirmed for the right part in the right role: {expected_families}.")

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
    msg = _assert_fails(swapped_mdr, good_values, "expected sequential", "channel 2/4 Connector-1 pin swap")
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
    buf_node = next(n for n in dropped_fork["EVT_D0_CLAMP"] if good_values.get(n.ref) == "SN74HCT541PW")
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
    swapped_part[or_ref] = "SN74HCT541PW"
    msg = _assert_fails(good_nets, swapped_part, "RWD_DLVR", "reward-OR gate mislabelled as a buffer")
    results.append(f"Reward-OR part mix-up ({or_ref}: SN74HCT32D -> SN74HCT541PW): caught -- {msg}")

    # Collapsed-net regression: force two distinct contract nets to share identical node
    # sets (simulating two labels resolving to the same physical net -- constraint 1's
    # own signature failure mode, "two labels, one real net"). NOT expected to surface as
    # verify()'s own dedicated "IDENTICAL node sets" check specifically: EVERY one of
    # this design's 61 contract nets is ALREADY walked by a more specific, earlier check
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_TASKPC_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_TASKPC_SCH} do not exist -- "
            f"run gen_breakout.py and gen_breakout_taskpc_digital.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    taskpc_sch_text = DEFAULT_TASKPC_SCH.read_text()
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
