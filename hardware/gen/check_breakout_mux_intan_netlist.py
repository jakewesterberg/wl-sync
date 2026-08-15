"""Parse hardware/breakout's exported netlist (and, for two checks the exported netlist
provably cannot answer -- see below, the raw .kicad_sch sources) and verify the mux-intan
sheet's contract nets and topology are electrically correct -- not just that ERC was silent.
Same reasoning check_mule_netlist.py's, check_breakout_power_netlist.py's,
check_taskpc_digital_netlist.py's, check_breakout_pi_interface_netlist.py's,
check_breakout_analog_frontend_netlist.py's, and check_breakout_analog_ni_netlist.py's own
docstrings all give, restated because it is the reason THIS file exists too:
gen_breakout_mux_intan.py's own lib_symbols entries are keyed by full lib id, which resolves
correctly -- but "resolves correctly" is proven empirically per run, not guaranteed by
construction for every future edit. Keyed bare, or with a channel accidentally wired to the
wrong pin, KiCad loads the file without error and silently produces a plausible, wrong
netlist. So ERC passing is necessary but not sufficient evidence.

THE central risks this file exists to catch, named explicitly by this task's own brief:

  1. All 16 A_* sources must reach EVERY ONE of the 8 muxes (not just some) -- a source
     missing from one mux silently shrinks that channel's own routing options without
     touching connectivity anywhere else (still a fully-connected, 0-error netlist).
  2. The 32 address nets (MUX1_A0..MUX8_A3) must exist and be UNIQUELY named -- a name
     collision between two muxes' own address lines would tie their selection together
     (both muxes always pick the same relative channel), invisible to ERC (still a valid,
     if wrong, netlist) and only found once Task 12 tries to address them independently.
  3. EVERY difference amplifier's own REF pin must be INTAN_GND, and NONE may be AGND --
     this is the entire reason this sheet's isolation topology works at all (module
     docstring of gen_breakout_mux_intan.py): a REF pin silently wired to AGND instead of
     INTAN_GND would still produce a fully-connected netlist (AGND is a perfectly real,
     populated net) while defeating the ground-offset rejection this whole sheet exists
     to provide -- and, worse, would put an AGND reference INSIDE the isolated domain's own
     output stage, exactly the "one designed exception" this sheet's own docstring says must
     never happen.
  4. The isolated domain (ISO_P12/ISO_N12/INTAN_GND) must be PIN-DISJOINT from the
     non-isolated rails (+12V/-12V/AGND) except where a part straddles BY DESIGN -- the 8
     INA105KU difference amplifiers, and ONLY those 8 (not the muxes, not anything else).
     A part that straddles BY ACCIDENT (a wrong pin landing an isolated-domain net on a
     non-isolated one, or vice versa) is a real short across an isolation barrier this
     board's entire Sec.8 rationale for carrying exactly one isolated DC-DC depends on.
  5. All 8 INTAN_AO* nets must reach a real BNC connector pin -- a mislabeled/dropped final
     net would leave a difference amplifier's output driving nothing external.
  6. Coordinate collisions -- this project has hit a REAL rail short from exactly this
     defect class before (analog-frontend.kicad_sch's own task-10a-report.md). Checked by
     parsing this sheet's own raw rendered text for every global-label (x, y),
     independently of the exported netlist (which only reports CONNECTIVITY, not the
     geometry that could have accidentally created it).
  7. No two of a mux's own 21 signal pins (S1-S16/A0-A3/D) may ever share a net, and its 3
     NC pins (2, 3, 13) must carry no net at all -- added at fix round 1 (task-10c-
     report.md) after the originally committed version of this sheet wired candidate pins
     13 AND 14 to the same A3 net as a hedge against unconfirmed TSSOP-28 pin numbering.
     That was not a safe hedge: two different physical pins tied to one net is a short
     between whatever those two pins actually are, and would have been a real fault, not a
     redundancy, had pin 13 turned out to be a supply or a source pin instead of NC. Now
     that Table 4 of the real datasheet is definitive (no reconstruction, no hedge), this
     risk is checked generically -- for A3, for every other signal pin, and for every NC
     pin -- so a hedge of this kind cannot pass silently again, on this part or the next.

`verify()` below re-derives, independently of gen_breakout_mux_intan.py's own choices, the
full channel/pin contract -- same "a checker that trusted the generator would only be
checking the generator against itself" discipline every prior checker in this project
already follows.

Regenerate the netlist this reads via:
    kicad-cli sch export netlist --format kicadsexpr -o /tmp/breakout.net \\
        hardware/breakout/breakout.kicad_sch

Usage:
    python3 hardware/gen/check_breakout_mux_intan_netlist.py [path/to/breakout.net]

Exits 0 and prints a summary if every check (including the self-tests) passes; exits 1 with
a description of the first failure otherwise.
"""
from __future__ import annotations

import copy
import re
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
DEFAULT_MUX_INTAN_SCH = DEFAULT_BREAKOUT_SCH.parent / "sheets" / "mux-intan.kicad_sch"
MUX_INTAN_SHEETFILE = "sheets/mux-intan.kicad_sch"  # exactly as breakout.kicad_sch's own
# Sheetfile property spells it -- must match gen_breakout_mux_intan.py's own constant.

# ---------------------------------------------------------------------------
# Channel/contract constants -- redefined here from spec Sec.3.2/6.3 / this task's own
# brief, NEVER imported from the generator (same independence discipline every checker in
# this project already follows for its own contract nets/pin tables).
# ---------------------------------------------------------------------------
ALL_16_NETS = [
    "A_EYE_LX", "A_EYE_LY", "A_EYE_RX", "A_EYE_RY", "A_EYE_LP", "A_EYE_RP",
    "A_PD1", "A_PD2", "A_AMB", "A_ACC", "A_JOY_X", "A_JOY_Y", "A_MIC",
    "A_MISC1", "A_MISC2", "A_MISC3",
]
assert len(ALL_16_NETS) == 16
assert len(set(ALL_16_NETS)) == 16

N_MUX = 8
MUX_VALUE = "ADG1206YRUZ"
DIFFAMP_VALUE = "INA105KU"
SERIES_R_VALUE = "100"

# ADG1206YRUZ pin map (gen_wl_sync_lib.py's own ADG1206_PINS) -- redefined independently.
# Table 4 of Analog Devices' own ADG1206/ADG1207 Rev.0 datasheet, 28-Lead TSSOP column,
# transcribed verbatim (fix round 1, task-10c-report.md, replaced a two-pin A3 hedge --
# see _check_32_address_nets()'s own history in git blame / the report -- with this real,
# sourced table; no reconstruction remains).
S_PIN_NUMBERS = ["19", "20", "21", "22", "23", "24", "25", "26", "11", "10", "9", "8", "7", "6", "5", "4"]
assert len(S_PIN_NUMBERS) == 16
MUX_PIN_D = "28"  # single common output -- pin 2 is NC on the real part, not a second
# drain; there is no D1/D2 tie to check.
MUX_PIN_VDD = "1"
MUX_PIN_VMINUS = "27"
MUX_PIN_GND = "12"
MUX_PIN_EN = "18"
MUX_PIN_A0 = "17"
MUX_PIN_A1 = "16"
MUX_PIN_A2 = "15"
MUX_PIN_A3 = "14"
MUX_ADDR_PINS = {0: MUX_PIN_A0, 1: MUX_PIN_A1, 2: MUX_PIN_A2, 3: MUX_PIN_A3}
MUX_NC_PINS = ("2", "3", "13")  # this part's only NC pins (Table 4) -- must carry no net.
# The 21 "signal" pins whose whole purpose is to carry ONE logical, per-channel role
# (unlike VDD/GND/VSS/EN, which legitimately share a rail net with other pins on the SAME
# instance -- e.g. EN and VDD both land on +12V by design). No two of these 21 physical
# pins may EVER share a net on the SAME mux instance: that is exactly the shape of the
# original A3 hedge defect (two different physical pins, at most one of which is really
# the signal it was labelled as, tied to one net -- a short, not a redundancy, if the
# other pin ever turns out to be live). See _check_no_signal_pin_collisions().
MUX_SIGNAL_PINS = tuple(S_PIN_NUMBERS) + (MUX_PIN_A0, MUX_PIN_A1, MUX_PIN_A2, MUX_PIN_A3, MUX_PIN_D)
assert len(MUX_SIGNAL_PINS) == 21
assert len(set(MUX_SIGNAL_PINS)) == 21

# INA105 pin roles -- real TI datasheet pinout (SOIC-8), redefined independently (matches
# check_breakout_analog_frontend_netlist.py's own INA105 dict).
INA105 = {"ref_pin": "1", "minus": "2", "plus": "3", "vneg": "4", "sense": "5", "out": "6", "vpos": "7"}

# The 32 canonical address net names.
ALL_32_ADDR_NETS = sorted(f"MUX{n}_A{b}" for n in range(1, N_MUX + 1) for b in range(4))
assert len(ALL_32_ADDR_NETS) == 32
assert len(set(ALL_32_ADDR_NETS)) == 32

# Rail-pair domain lists, redefined independently of check_breakout_power_netlist.py's own
# ISO_NETS/NON_ISO_NETS (this checker's own version, scoped to exactly the two pairs this
# sheet's own parts actually touch -- not a full copy of that file's own broader list,
# which also tracks nets this sheet has no pins on at all).
ISO_RAILS = ["ISO_P12", "ISO_N12", "INTAN_GND"]
NON_ISO_RAILS = ["+12V", "-12V", "AGND"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _find_bridging_resistor(nets: dict[str, list[Node]], net_a: str, net_b: str) -> str:
    """The single R-prefixed reference with one pin on net_a and the other on net_b -- same
    technique every checker in this project uses (duplicated, not imported)."""
    check(net_a in nets, f"missing net: {net_a!r}")
    check(net_b in nets, f"missing net: {net_b!r}")
    ra = {n.ref for n in nets[net_a] if n.ref.startswith("R")}
    rb = {n.ref for n in nets[net_b] if n.ref.startswith("R")}
    both = ra & rb
    check(
        len(both) == 1,
        f"expected exactly 1 resistor bridging {net_a!r}<->{net_b!r}, found on {net_a}: "
        f"{sorted(ra)}, on {net_b}: {sorted(rb)}",
    )
    return next(iter(both))


def _mux_refs(values: dict[str, str]) -> list[str]:
    refs = sorted((r for r, v in values.items() if v == MUX_VALUE), key=lambda r: int(r[1:]))
    check(len(refs) == N_MUX, f"expected exactly {N_MUX} {MUX_VALUE!r} instances, found {len(refs)}: {refs}")
    return refs


def _diffamp_refs(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    """The 8 INA105KU instances THIS sheet placed -- identified by a pin on ISO_P12, not
    merely Value=='INA105KU' (analog-frontend.kicad_sch's own 13 AGND-domain INA105KU
    instances share that Value; ISO_P12 is what's unique to this sheet's own 8)."""
    refs = sorted(
        {n.ref for n in nets.get("ISO_P12", []) if values.get(n.ref) == DIFFAMP_VALUE},
        key=lambda r: int(r[1:]),
    )
    check(len(refs) == N_MUX, f"expected exactly {N_MUX} {DIFFAMP_VALUE!r} instances on ISO_P12, found {len(refs)}: {refs}")
    return refs


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_all_sources_reach_all_muxes(nets: dict[str, list[Node]], mux_refs: list[str]) -> str:
    """Every one of the 16 A_* sources has a pin on EVERY ONE of the 8 mux instances, at
    the expected physical S-pin (S_PIN_NUMBERS, this checker's own independently-redefined
    table) -- not just present somewhere on that instance."""
    for ref in mux_refs:
        for source_net, pin_num in zip(ALL_16_NETS, S_PIN_NUMBERS):
            check(source_net in nets, f"missing source net: {source_net!r}")
            nodes = [n for n in nets[source_net] if n.ref == ref]
            check(
                len(nodes) == 1,
                f"{ref}: expected exactly 1 pin on source net {source_net!r}, found {nodes}",
            )
            check(
                nodes[0].pin == pin_num,
                f"{ref}: {source_net!r} lands on pin {nodes[0].pin}, expected pin {pin_num} "
                f"(a permuted source-to-pin assignment)",
            )
    return (
        f"All 16 A_* sources reach every one of the {len(mux_refs)} mux instances "
        f"({mux_refs}), each on its own expected physical S-pin."
    )


def _check_32_address_nets(nets: dict[str, list[Node]], mux_refs: list[str]) -> str:
    """All 32 MUX{n}_A{b} nets exist, are uniquely named (the canonical set, no collision,
    no omission), and each lands on the RIGHT mux instance's RIGHT address pin -- including
    MUX{n}_A3, on its own single, datasheet-confirmed physical pin (14), with no second
    candidate pin to also check (fix round 1, task-10c-report.md, retired the two-pin A3
    hedge this check used to also verify -- see _check_no_signal_pin_collisions() and
    _check_nc_pins_carry_no_net() below for the general replacement: no hedge of this kind,
    for A3 or any other signal pin, can pass silently again).
    """
    present = {n for n in nets if re.fullmatch(r"MUX[1-8]_A[0-3]", n)}
    check(
        present == set(ALL_32_ADDR_NETS),
        f"address net set mismatch: expected exactly {sorted(ALL_32_ADDR_NETS)}, found "
        f"{sorted(present)} (missing: {sorted(set(ALL_32_ADDR_NETS) - present)}, extra: "
        f"{sorted(present - set(ALL_32_ADDR_NETS))})",
    )
    for i, ref in enumerate(mux_refs, start=1):
        for bit, addr_pin in MUX_ADDR_PINS.items():
            net = f"MUX{i}_A{bit}"
            nodes = [n for n in nets[net] if n.ref == ref]
            check(
                any(n.pin == addr_pin for n in nodes),
                f"{net}: expected a pin on {ref} at physical pin {addr_pin}, found {nodes} "
                f"-- either a wrong mux instance or a permuted address-bit assignment",
            )
    return (
        f"All 32 address nets (MUX1_A0..MUX8_A3) present, uniquely named, each landing on "
        f"its own expected mux instance and physical pin."
    )


def _check_no_signal_pin_collisions(nets: dict[str, list[Node]], mux_refs: list[str]) -> str:
    """No two of a mux's own 21 signal pins (S1-S16, A0-A3, D -- MUX_SIGNAL_PINS) ever
    share a net on the SAME instance, and every one of the 21 carries exactly one net.
    This is the GENERAL form of the defect the original A3 hedge WAS (fix round 1,
    task-10c-report.md): two different physical pins, at most one of which is really the
    signal it was wired as, tied to the same net -- a short between whatever those two
    pins actually are, not a redundancy (and would have been a real fault, not a harmless
    hedge, had the second candidate pin turned out to be a supply or another source
    terminal instead of NC). Checked per instance, independently of which specific
    pins/nets are involved, so ANY future two-pins-one-net hedge on ANY signal pin of this
    part -- not just A3 -- fails loudly here instead of passing silently the way the
    original hedge did.
    """
    for ref in mux_refs:
        net_of_pin: dict[str, str] = {}
        for net_name, nodes in nets.items():
            for node in nodes:
                if node.ref != ref or node.pin not in MUX_SIGNAL_PINS:
                    continue
                prior = net_of_pin.get(node.pin)
                check(
                    prior is None or prior == net_name,
                    f"{ref}: signal pin {node.pin} is itself on more than one net "
                    f"({prior!r} and {net_name!r}) -- a single physical pin must belong "
                    f"to exactly one net",
                )
                net_of_pin[node.pin] = net_name
        check(
            set(net_of_pin) == set(MUX_SIGNAL_PINS),
            f"{ref}: expected all 21 signal pins ({sorted(MUX_SIGNAL_PINS, key=int)}) to "
            f"carry a net, found {sorted(net_of_pin, key=int)} -- missing: "
            f"{sorted(set(MUX_SIGNAL_PINS) - set(net_of_pin), key=int)}",
        )
        nets_used = list(net_of_pin.values())
        dupes = {n for n in nets_used if nets_used.count(n) > 1}
        check(
            not dupes,
            f"{ref}: two or more of its own 21 signal pins (S1-S16/A0-A3/D) share a net "
            f"-- a two-pins-one-net hedge of exactly the kind fix round 1 "
            f"(task-10c-report.md) removed for A3: pins "
            f"{sorted((p for p, n in net_of_pin.items() if n in dupes), key=int)} all "
            f"land on {sorted(dupes)}",
        )
    return (
        f"No two of any mux's own 21 signal pins (S1-S16/A0-A3/D) share a net on the same "
        f"instance, across all {len(mux_refs)} muxes -- each of the 21 carries its own "
        f"distinct net."
    )


def _check_nc_pins_carry_no_net(nets: dict[str, list[Node]], mux_refs: list[str]) -> str:
    """This part's only three NC pins (2, 3, 13 -- Table 4, MUX_NC_PINS) carry no REAL net
    on any mux instance. A genuinely no_connect-typed pin that nothing labels still shows
    up in kicad-cli's own exported netlist -- confirmed empirically against this sheet's
    own real export, not assumed -- but only as kicad-cli's own synthetic, always-
    single-node "unconnected-(REF-...)" net (same shape 10a's own INA105KU pin 8 already
    produces, e.g. "unconnected-(U39-Pad8)"). This check accepts exactly that shape and
    rejects anything else: an NC pin appearing on any OTHER (real) net, or sharing even
    its own synthetic net with a second node, means something now drives a pin this
    part's datasheet says has no internal connection. This is the other half of what the
    original A3 hedge got wrong: pin 13 is genuinely NC, and a correct design must leave
    it that way, not merely avoid re-hedging it onto some OTHER signal.
    """
    for ref in mux_refs:
        for pin in MUX_NC_PINS:
            hits = [name for name, nodes in nets.items() for n in nodes if n.ref == ref and n.pin == pin]
            for name in hits:
                check(
                    name.startswith("unconnected-"),
                    f"{ref}: NC pin {pin} unexpectedly carries a real net ({name!r}) -- "
                    f"pins {MUX_NC_PINS} are this part's only NC pins (Table 4) and must "
                    f"remain genuinely unconnected",
                )
            check(
                len(hits) <= 1,
                f"{ref}: NC pin {pin} appears on more than one net ({hits}) -- expected at "
                f"most kicad-cli's own single synthetic 'unconnected-*' net",
            )
    return (
        f"NC pins {MUX_NC_PINS} carry no real net on any of the {len(mux_refs)} mux "
        f"instances -- genuinely unconnected (kicad-cli's own synthetic 'unconnected-*' "
        f"net only), as Table 4 requires."
    )


def _check_diffamp_reference(nets: dict[str, list[Node]], values: dict[str, str], diffamp_refs: list[str]) -> str:
    """Every difference amplifier's own REF pin is INTAN_GND, and its '-' pin is AGND (the
    one designed exception -- see module docstring) -- and, the central risk this whole
    sheet exists to guard: NONE of the 8 has REF on AGND instead."""
    check("INTAN_GND" in nets, "missing net: 'INTAN_GND'")
    check("AGND" in nets, "missing net: 'AGND'")
    for i, ref in enumerate(diffamp_refs, start=1):
        ref_nodes = [n for n in nets["INTAN_GND"] if n.ref == ref and n.pin == INA105["ref_pin"]]
        check(
            len(ref_nodes) == 1,
            f"{ref} (Intan channel {i}): REF pin ({INA105['ref_pin']}) is not on INTAN_GND "
            f"-- found INTAN_GND nodes on {ref}: "
            f"{[n.pin for n in nets['INTAN_GND'] if n.ref == ref]}. Every difference "
            f"amplifier must reference INTAN_GND -- this is the entire mechanism by which "
            f"this sheet rejects the AGND-to-INTAN_GND ground offset.",
        )
        check(
            not any(n.ref == ref and n.pin == INA105["ref_pin"] for n in nets["AGND"]),
            f"{ref} (Intan channel {i}): REF pin is (also) on AGND -- a difference "
            f"amplifier's REF pin must NEVER reference AGND; this would put a non-isolated "
            f"reference inside the isolated domain's own output stage.",
        )
        minus_nodes = [n for n in nets["AGND"] if n.ref == ref and n.pin == INA105["minus"]]
        check(
            len(minus_nodes) == 1,
            f"{ref} (Intan channel {i}): '-' pin ({INA105['minus']}) is not on AGND -- "
            f"found AGND nodes on {ref}: {[n.pin for n in nets['AGND'] if n.ref == ref]}. "
            f"This is the one designed AGND reference (the offset being rejected), "
            f"distinct from the REF pin.",
        )
        plus_net = f"MUX_OUT{i}"
        check(
            any(n.ref == ref and n.pin == INA105["plus"] for n in nets.get(plus_net, [])),
            f"{ref} (Intan channel {i}): '+' pin is not on {plus_net!r} (the mux's own "
            f"single D output)",
        )
        buf_net = f"INTAN_AO{i}_BUF"
        check(buf_net in nets, f"missing internal net: {buf_net!r}")
        se_out = {n.pin for n in nets[buf_net] if n.ref == ref}
        check(
            se_out == {INA105["sense"], INA105["out"]},
            f"{ref} (Intan channel {i}): expected SENSE ({INA105['sense']}) and OUT "
            f"({INA105['out']}) shorted together on {buf_net!r} (unity gain), found pins "
            f"{se_out}",
        )
        check(
            any(n.ref == ref and n.pin == INA105["vpos"] for n in nets.get("ISO_P12", [])),
            f"{ref} (Intan channel {i}): V+ pin ({INA105['vpos']}) is not on ISO_P12",
        )
        check(
            any(n.ref == ref and n.pin == INA105["vneg"] for n in nets.get("ISO_N12", [])),
            f"{ref} (Intan channel {i}): V- pin ({INA105['vneg']}) is not on ISO_N12",
        )
    return (
        f"All {len(diffamp_refs)} difference amplifiers ({diffamp_refs}) reference "
        f"INTAN_GND on their own REF pin and AGND only on their own '-' pin (the one "
        f"designed exception); none references AGND on REF."
    )


PRE_EXISTING_STRADDLER_VALUE = "IH1215D"  # the isolated DC-DC converter (power.kicad_sch,
# Task 7) -- ITS OWN primary side legitimately sits on +12V/DGND while its secondary side
# legitimately sits on the raw isolated nodes leading to ISO_P12/ISO_N12/INTAN_GND
# (check_breakout_power_netlist.py's own u2_map, already reviewed and accepted at Task 7).
# This checker's own netlist read is PROJECT-WIDE (nets like +12V/AGND/ISO_P12 are shared
# across every sheet, not scoped to mux-intan.kicad_sch alone), so a straddle check against
# them necessarily also sees this ALREADY-LEGITIMATE straddle -- identified here by Value,
# independently of check_breakout_power_netlist.py's own reference-number-based u2_map, not
# hand-waved away as "someone else's problem".


def _check_isolation_pin_disjoint(
    nets: dict[str, list[Node]], values: dict[str, str], mux_refs: list[str], diffamp_refs: list[str],
) -> str:
    """The isolated domain (ISO_P12/ISO_N12/INTAN_GND) is pin-disjoint from the
    non-isolated rails (+12V/-12V/AGND) -- checked at PIN level (no single physical pin on
    both, a genuine short) AND at REFERENCE level (which components have SOME pins in each
    domain, i.e. legitimately straddle) -- confirming that set is EXACTLY the 8 difference
    amplifiers PLUS the one already-reviewed pre-existing straddler (the isolated DC-DC,
    Task 7), never the 8 muxes or anything else new. A reference-level-only check would
    miss a genuine pin-level short; a pin-level-only check would not by itself confirm
    WHICH parts are doing the (by-design) straddling -- both together are this task's own
    literal ask: 'pin-disjoint... except where a part straddles by design.'
    """
    for name in ISO_RAILS + NON_ISO_RAILS:
        check(name in nets, f"missing net: {name!r}")
    iso_pins = {(n.ref, n.pin) for name in ISO_RAILS for n in nets[name]}
    non_iso_pins = {(n.ref, n.pin) for name in NON_ISO_RAILS for n in nets[name]}
    pin_overlap = iso_pins & non_iso_pins
    check(
        not pin_overlap,
        f"isolation barrier violated at the PIN level: {pin_overlap} -- a single physical "
        f"pin cannot legitimately sit on both an isolated-domain net and a non-isolated one",
    )

    iso_refs = {ref for ref, _ in iso_pins}
    non_iso_refs = {ref for ref, _ in non_iso_pins}
    straddling_refs = iso_refs & non_iso_refs
    pre_existing = {r for r, v in values.items() if v == PRE_EXISTING_STRADDLER_VALUE}
    expected_straddlers = set(diffamp_refs) | pre_existing
    check(
        straddling_refs == expected_straddlers,
        f"reference-level straddle set mismatch: expected EXACTLY the {len(diffamp_refs)} "
        f"difference amplifiers ({sorted(diffamp_refs)}) plus the pre-existing isolated "
        f"DC-DC ({sorted(pre_existing)}) to have pins in both domains (by design), found "
        f"{sorted(straddling_refs)} -- "
        f"{'an unexpected part straddles the isolation boundary' if straddling_refs - expected_straddlers else 'an expected by-design straddle is missing'}",
    )
    return (
        f"Isolated domain (ISO_P12/ISO_N12/INTAN_GND) is pin-disjoint from the "
        f"non-isolated rails (+12V/-12V/AGND): zero shared pins, and the only references "
        f"with pins in both domains are the {len(diffamp_refs)} difference amplifiers plus "
        f"the one already-reviewed pre-existing straddler ({sorted(pre_existing)}) -- "
        f"never the {len(mux_refs)} muxes, which this check also confirms stay entirely "
        f"within the AGND domain on every pin this sheet wires."
    )


def _check_intan_ao_reach_bnc(nets: dict[str, list[Node]]) -> str:
    """All 8 INTAN_AO* nets reach a real BNC connector pin, through the 100R series
    resistor bridging each amplifier's own internal *_BUF node to the final net."""
    bnc_refs = []
    for i in range(1, N_MUX + 1):
        buf_net = f"INTAN_AO{i}_BUF"
        final_net = f"INTAN_AO{i}"
        r_ref = _find_bridging_resistor(nets, buf_net, final_net)
        # SERIES_R_VALUE is confirmed by check_component_values() below, not here --
        # duplicating a values-lookup in this net-topology-only helper would need
        # `values` threaded through where it isn't otherwise needed.
        conn_refs = {n.ref for n in nets[final_net] if n.ref.startswith("J")}
        check(
            len(conn_refs) == 1,
            f"{final_net}: expected exactly 1 BNC connector reference, found {conn_refs}",
        )
        bnc_ref = next(iter(conn_refs))
        bnc_refs.append(bnc_ref)
        check(
            any(n.ref == bnc_ref and n.pin == "2" for n in nets.get("INTAN_GND", [])),
            f"{bnc_ref} (Intan channel {i}'s own BNC): shell (pin 2) is not on INTAN_GND "
            f"-- must never be AGND (spec Sec.5.5: Intan's shield IS its own return)",
        )
        check(
            not any(n.ref == bnc_ref and n.pin == "2" for n in nets.get("AGND", [])),
            f"{bnc_ref} (Intan channel {i}'s own BNC): shell (pin 2) is (also) on AGND",
        )
    check(len(set(bnc_refs)) == N_MUX, f"expected {N_MUX} DISTINCT BNC references, found {sorted(set(bnc_refs))}")
    return f"All {N_MUX} INTAN_AO* nets reach their own distinct BNC ({bnc_refs}), shell to INTAN_GND, never AGND."


def _check_component_values(values: dict[str, str], mux_refs: list[str], diffamp_refs: list[str]) -> str:
    for ref in mux_refs:
        check(values.get(ref) == MUX_VALUE, f"{ref}: expected Value {MUX_VALUE!r}, found {values.get(ref)!r}")
    for ref in diffamp_refs:
        check(values.get(ref) == DIFFAMP_VALUE, f"{ref}: expected Value {DIFFAMP_VALUE!r}, found {values.get(ref)!r}")
    return f"Component values confirmed: {len(mux_refs)} x {MUX_VALUE}, {len(diffamp_refs)} x {DIFFAMP_VALUE}."


def _check_series_resistor_values(nets: dict[str, list[Node]], values: dict[str, str]) -> str:
    for i in range(1, N_MUX + 1):
        r_ref = _find_bridging_resistor(nets, f"INTAN_AO{i}_BUF", f"INTAN_AO{i}")
        check(
            values.get(r_ref) == SERIES_R_VALUE,
            f"{r_ref} (Intan channel {i}'s own series resistor): expected "
            f"{SERIES_R_VALUE!r}, found {values.get(r_ref)!r}",
        )
    return f"All {N_MUX} output series resistors confirmed {SERIES_R_VALUE!r} ohm."


def _check_en_tied_high(nets: dict[str, list[Node]], mux_refs: list[str]) -> str:
    """EN tied high on every mux (this task's own explicit instruction) -- to +12V
    specifically (already-consumed, already-driven; see module docstring for why)."""
    check("+12V" in nets, "missing net: '+12V'")
    for ref in mux_refs:
        check(
            any(n.ref == ref and n.pin == MUX_PIN_EN for n in nets["+12V"]),
            f"{ref}: EN pin ({MUX_PIN_EN}) is not tied to +12V",
        )
    return f"EN tied to +12V (always enabled) on all {len(mux_refs)} muxes."


_GLOBAL_LABEL_RE = re.compile(r'\(global_label "([^"]+)"\s*\(shape \w+\)\s*\(at ([\-0-9.]+) ([\-0-9.]+)')


def _check_no_coordinate_collisions(sch_text: str) -> str:
    """Scans this sheet's OWN raw rendered text for every global_label's real (x, y) and
    asserts no two DIFFERENT net names ever share one -- a real short, indistinguishable
    from a correct design by the exported netlist alone. This project has hit exactly this
    defect before (analog-frontend.kicad_sch's own task-10a-report.md)."""
    coords: dict[tuple[float, float], set[str]] = {}
    for m in _GLOBAL_LABEL_RE.finditer(sch_text):
        name, x, y = m.group(1), round(float(m.group(2)), 3), round(float(m.group(3)), 3)
        coords.setdefault((x, y), set()).add(name)
    check(len(coords) > 100, f"suspiciously few distinct label coordinates found ({len(coords)}) -- is this really mux-intan.kicad_sch's own rendered text?")
    collisions = {k: v for k, v in coords.items() if len(v) > 1}
    check(
        not collisions,
        f"{len(collisions)} coordinate(s) in mux-intan.kicad_sch carry more than one "
        f"DISTINCT global-label net name -- a real electrical short between those nets: "
        f"{list(collisions.items())[:5]}",
    )
    return (
        f"No coordinate collisions: all {len(coords)} distinct global-label positions in "
        f"mux-intan.kicad_sch carry exactly one net name each."
    )


def verify(nets: dict[str, list[Node]], values: dict[str, str]) -> list[str]:
    summary = []
    mux_refs = _mux_refs(values)
    diffamp_refs = _diffamp_refs(nets, values)

    summary.append(_check_all_sources_reach_all_muxes(nets, mux_refs))
    summary.append(_check_32_address_nets(nets, mux_refs))
    summary.append(_check_no_signal_pin_collisions(nets, mux_refs))
    summary.append(_check_nc_pins_carry_no_net(nets, mux_refs))
    summary.append(_check_diffamp_reference(nets, values, diffamp_refs))
    # BNC-shell-specific check BEFORE the general isolation-barrier check: the BNC shell
    # (INTAN_GND) and AGND are both tracked by the isolation check too, so ordering this
    # one first gives its OWN "shell must never be AGND" assertion genuine, distinct
    # negative-control coverage instead of always being pre-empted by the more general
    # (and, for that specific defect, equally correct) isolation-barrier message.
    summary.append(_check_intan_ao_reach_bnc(nets))
    summary.append(_check_isolation_pin_disjoint(nets, values, mux_refs, diffamp_refs))
    summary.append(_check_component_values(values, mux_refs, diffamp_refs))
    summary.append(_check_series_resistor_values(nets, values))
    summary.append(_check_en_tied_high(nets, mux_refs))

    for rail in ("+12V", "-12V", "AGND", "ISO_P12", "ISO_N12", "INTAN_GND"):
        check(rail in nets, f"missing consumed rail: {rail!r}")
        check(len(nets[rail]) >= 10, f"{rail}: suspiciously small population ({len(nets[rail])} nodes)")
    summary.append("Consumed rails (+12V/-12V/AGND/ISO_P12/ISO_N12/INTAN_GND) present with substantial populations.")

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
    each corruption below is a minimal, targeted mutation of that known-good structure."""
    results = []

    # (1) A source silently missing from one mux: A_PD1's own node on the 3rd mux deleted.
    mux_refs = _mux_refs(good_values)
    missing_source = copy.deepcopy(good_nets)
    victim_ref = mux_refs[2]
    missing_source["A_PD1"] = [n for n in missing_source["A_PD1"] if n.ref != victim_ref]
    msg = _assert_fails(missing_source, good_values, "expected exactly 1 pin on source net", f"A_PD1 missing from {victim_ref} (3rd mux)")
    results.append(f"Source silently missing from one mux ({victim_ref}): caught -- {msg}")

    # (2) THE central negative control this task's own brief explicitly asks for: an
    # amplifier's own REF pin re-referenced to AGND. Simulated by ADDING an AGND node
    # for REF while LEAVING its own genuine INTAN_GND node in place (rather than moving
    # it), so this specifically exercises the "must NOT reference AGND" assertion (a
    # dedicated check, not merely the reverse side of "must be on INTAN_GND" -- realistic
    # if a generator edit ever added a second, stray label call for the same pin, e.g.
    # via a coordinate collision merging two nets, this project's own recurring defect
    # class per every prior sheet's own task-10a-report.md precedent).
    diffamp_refs = _diffamp_refs(good_nets, good_values)
    re_referenced = copy.deepcopy(good_nets)
    victim_da = diffamp_refs[4]
    stray_node = next(n for n in good_nets["INTAN_GND"] if n.ref == victim_da and n.pin == INA105["ref_pin"])
    re_referenced.setdefault("AGND", []).append(stray_node)
    msg = _assert_fails(re_referenced, good_values, "must NEVER reference AGND", f"{victim_da}'s own REF pin also wired to AGND")
    results.append(f"Difference amplifier REF pin re-referenced to AGND (Intan channel 5, {victim_da}): caught -- {msg}")

    # (3) Isolation barrier violated: one mux's own GND pin moved from AGND to INTAN_GND --
    # an unexpected reference straddling the isolation boundary.
    straddle = copy.deepcopy(good_nets)
    victim_mux = mux_refs[0]
    moved = next(n for n in straddle["AGND"] if n.ref == victim_mux and n.pin == MUX_PIN_GND)
    straddle["AGND"] = [n for n in straddle["AGND"] if not (n.ref == victim_mux and n.pin == MUX_PIN_GND)]
    straddle.setdefault("INTAN_GND", []).append(moved)
    msg = _assert_fails(straddle, good_values, "an unexpected part straddles the isolation boundary", f"{victim_mux}'s own GND pin moved from AGND onto INTAN_GND")
    results.append(f"Mux GND pin moved onto the isolated domain (unexpected straddle, {victim_mux}): caught -- {msg}")

    # (4) Address net collision: MUX2_A0's own mux-side node deleted (simulating a dropped/
    # misrouted address line -- the net still exists as a label but no longer reaches its
    # own mux instance's real pin).
    addr_gap = copy.deepcopy(good_nets)
    addr_gap["MUX2_A0"] = [n for n in addr_gap["MUX2_A0"] if not (n.ref == mux_refs[1] and n.pin == MUX_PIN_A0)]
    msg = _assert_fails(addr_gap, good_values, "expected a pin on", "MUX2_A0's own node on U40 (2nd mux) dropped")
    results.append("Address line dropped from its own mux instance (MUX2_A0): caught -- " + msg)

    # (5) INTAN_AO not reaching a BNC: channel 6's own BNC node removed.
    no_bnc = copy.deepcopy(good_nets)
    bnc_ref = next(n.ref for n in good_nets["INTAN_AO6"] if n.ref.startswith("J"))
    no_bnc["INTAN_AO6"] = [n for n in no_bnc["INTAN_AO6"] if n.ref != bnc_ref]
    msg = _assert_fails(no_bnc, good_values, "expected exactly 1 BNC connector reference", f"INTAN_AO6's own BNC node ({bnc_ref}) removed")
    results.append(f"Intan output channel 6 no longer reaching its own BNC ({bnc_ref}): caught -- {msg}")

    # (6) BNC shell bonded to AGND instead of/in addition to INTAN_GND.
    bad_shell = copy.deepcopy(good_nets)
    bnc7_ref = next(n.ref for n in good_nets["INTAN_AO7"] if n.ref.startswith("J"))
    bad_shell.setdefault("AGND", []).append(Node(bnc7_ref, "2", None, "passive"))
    msg = _assert_fails(bad_shell, good_values, "is (also) on AGND", f"{bnc7_ref} (channel 7's own BNC) shell bonded to AGND")
    results.append(f"Intan output BNC shell bonded to AGND (channel 7, {bnc7_ref}): caught -- {msg}")

    # (7) Series resistor value drift (100 -> 1k) on one channel.
    drifted = dict(good_values)
    r_ref = _find_bridging_resistor(good_nets, "INTAN_AO3_BUF", "INTAN_AO3")
    drifted[r_ref] = "1k"
    msg = _assert_fails(good_nets, drifted, f"expected", f"{r_ref} (Intan channel 3's own series resistor) value drift 100->1k")
    results.append(f"Series resistor value drift (100 ohm -> 1k, channel 3): caught -- {msg}")

    # (8) EN no longer tied high: one mux's own EN node on +12V removed.
    no_en = copy.deepcopy(good_nets)
    en_victim = mux_refs[6]
    no_en["+12V"] = [n for n in no_en["+12V"] if not (n.ref == en_victim and n.pin == MUX_PIN_EN)]
    msg = _assert_fails(no_en, good_values, "EN pin", f"{en_victim}'s own EN tie to +12V removed")
    results.append(f"Mux EN no longer tied to +12V ({en_victim}): caught -- {msg}")

    # (9) THE central negative control fix round 1 (task-10c-report.md) added: a
    # two-pins-one-net hedge of the SAME general shape as the original A3 defect, but not
    # A3-specific this time (confirming _check_no_signal_pin_collisions() catches the
    # defect CLASS, not just the one historical instance it was written to explain).
    # Simulated by MOVING the 4th mux's own D pin (28) off its real net (MUX_OUT4) and
    # onto its own A3 net (MUX4_A3) instead -- two different signal pins (14 and 28) now
    # both land on MUX4_A3, exactly the shape "two candidate pins wired to the same net"
    # takes when the pins involved are NOT both harmless NC.
    collided = copy.deepcopy(good_nets)
    victim_mux4 = mux_refs[3]
    d_node = next(n for n in good_nets["MUX_OUT4"] if n.ref == victim_mux4 and n.pin == MUX_PIN_D)
    collided["MUX_OUT4"] = [n for n in collided["MUX_OUT4"] if not (n.ref == victim_mux4 and n.pin == MUX_PIN_D)]
    collided.setdefault("MUX4_A3", []).append(d_node)
    msg = _assert_fails(collided, good_values, "share a net", f"{victim_mux4}'s own D pin (28) moved onto its own A3 net (MUX4_A3)")
    results.append(f"Signal pin collision reintroduced (D pin also wired onto A3's net, 4th mux, {victim_mux4}): caught -- {msg}")

    # (10) THE OTHER half of the original A3 hedge: a genuinely NC pin (13, the hedge's
    # own other candidate) stray-wired onto a real net instead of being left unconnected --
    # simulating a future edit that starts (mis)using this pin for something.
    nc_live = copy.deepcopy(good_nets)
    victim_mux8 = mux_refs[7]
    nc_live.setdefault("+12V", []).append(Node(victim_mux8, "13", None, "passive"))
    msg = _assert_fails(nc_live, good_values, "unexpectedly carries a real net", f"{victim_mux8}'s own NC pin 13 stray-wired onto +12V")
    results.append(f"NC pin carrying a net reintroduced (pin 13 wired onto +12V, 8th mux, {victim_mux8}): caught -- {msg}")

    return results


def self_test_collision(good_sch_text: str) -> str:
    """Negative control for _check_no_coordinate_collisions(): splices a SECOND global
    label's own (x, y) onto a FIRST label's coordinate (by exact character offset from the
    original regex match, not a substring search that could accidentally hit an unrelated
    "(at X Y" elsewhere in the file) and confirms the check fires on the resulting
    synthetic short.
    """
    matches = list(_GLOBAL_LABEL_RE.finditer(good_sch_text))
    check(len(matches) > 10, "self-test setup failed: too few global labels found to corrupt")
    first = matches[0]
    victim = next(m for m in matches if m.group(1) != first.group(1))
    corrupted = (
        good_sch_text[: victim.start(2)]
        + first.group(2) + " " + first.group(3)
        + good_sch_text[victim.end(3):]
    )
    check(corrupted != good_sch_text, "self-test setup failed: splice produced no change")
    try:
        _check_no_coordinate_collisions(corrupted)
    except CheckFailure as e:
        check(
            "DISTINCT global-label net name" in str(e),
            f"self-test 'coordinate collision reintroduced': wrong failure message: {e}",
        )
        return str(e)
    raise CheckFailure(
        "self-test 'coordinate collision reintroduced' did NOT raise -- "
        "_check_no_coordinate_collisions() is passing vacuously"
    )


# ---------------------------------------------------------------------------
# Instance-path ancestor-chain check -- same mechanism/rationale as every other checker in
# this project's own verify_instance_paths()/self_test_instance_paths().
# ---------------------------------------------------------------------------


def verify_instance_paths(mi_sch_text: str, breakout_sch_text: str) -> str:
    breakout_root_uuid = find_root_uuid(breakout_sch_text)
    own_root_uuid = find_root_uuid(mi_sch_text)
    check(
        own_root_uuid != breakout_root_uuid,
        f"mux-intan.kicad_sch's own file-identity uuid ({own_root_uuid}) collides with "
        f"breakout.kicad_sch's root uuid ({breakout_root_uuid}) -- regenerate",
    )
    expected_prefix = find_sheet_instance_path(breakout_sch_text, breakout_root_uuid, MUX_INTAN_SHEETFILE)

    paths = find_all_instance_paths(mi_sch_text)
    check(
        len(paths) >= 60,
        f"only found {len(paths)} (instances (path ...)) entries in mux-intan.kicad_sch -- "
        f"expected >=60 (recomputed directly against this task's own real output: 64 "
        f"placed instances -- 8 muxes + 8 difference amplifiers + 8 series resistors + 8 "
        f"BNCs + 32 decoupling capacitors (8 muxes x2 + 8 diffamps x2) -- the >=60 floor is "
        f"intentionally left below that real count so a future small edit doesn't need "
        f"this floor bumped in lockstep)",
    )
    bad = [p for p in paths if p != expected_prefix]
    check(
        not bad,
        f"{len(bad)}/{len(paths)} component instance paths in mux-intan.kicad_sch do not "
        f"resolve to the real ancestor chain ({expected_prefix!r}) -- e.g. found {bad[:3]!r}",
    )
    return (
        f"All {len(paths)} component instance paths in mux-intan.kicad_sch resolve to the "
        f"real ancestor chain {expected_prefix!r} (breakout's own root uuid + the "
        f"'mux-intan' sheet symbol's own uuid), not a self-referential one."
    )


def _assert_instance_paths_fail(mi_text: str, breakout_text: str, expect_substring: str, label: str) -> str:
    try:
        verify_instance_paths(mi_text, breakout_text)
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


def self_test_instance_paths(good_mi_text: str, good_breakout_text: str) -> list[str]:
    own_root_uuid = find_root_uuid(good_mi_text)
    breakout_root_uuid = find_root_uuid(good_breakout_text)
    real_prefix = find_sheet_instance_path(good_breakout_text, breakout_root_uuid, MUX_INTAN_SHEETFILE)

    corrupted = good_mi_text.replace(f'(path "{real_prefix}"', f'(path "/{own_root_uuid}"', 1)
    check(
        corrupted != good_mi_text,
        "self-test setup failed: no occurrence of the expected ancestor path found to "
        "corrupt -- good_mi_text may not actually be passing verify_instance_paths() cleanly",
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

    if not DEFAULT_BREAKOUT_SCH.exists() or not DEFAULT_MUX_INTAN_SCH.exists():
        print(
            f"error: {DEFAULT_BREAKOUT_SCH} and/or {DEFAULT_MUX_INTAN_SCH} do not exist -- "
            f"run gen_breakout.py and gen_breakout_mux_intan.py first"
        )
        return 1
    breakout_sch_text = DEFAULT_BREAKOUT_SCH.read_text()
    mi_sch_text = DEFAULT_MUX_INTAN_SCH.read_text()

    try:
        collision_summary = _check_no_coordinate_collisions(mi_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {collision_summary}")

    try:
        collision_self_test_msg = self_test_collision(mi_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print(f"SELF-TEST PASS: coordinate collision reintroduced: caught -- {collision_self_test_msg}")

    try:
        path_summary = verify_instance_paths(mi_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"FAIL: {e}")
        return 1
    print(f"PASS: {path_summary}")

    try:
        path_self_test_results = self_test_instance_paths(mi_sch_text, breakout_sch_text)
    except CheckFailure as e:
        print(f"SELF-TEST FAIL: {e}")
        return 1
    print("SELF-TEST PASS (negative controls fired as expected):")
    for line in path_self_test_results:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
