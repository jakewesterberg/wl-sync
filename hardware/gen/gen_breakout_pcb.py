"""Generator for hardware/breakout/breakout.kicad_pcb -- the breakout's generated placement
(layout task L4; plan Task 14 Step 3, "Place connectors first, everything else after").

What it writes: the board outline; four copper layers; the two isolation slots as Edge.Cuts
cutouts (2.0 mm under the SOIC-16 optocouplers, 1.5 mm under C162, 2.5 mm elsewhere);
every footprint in the netlist, placed; each pad's net; the barrier keep-outs as rule
areas; the CM5 IO Board's outline and its two inboard standoff holes. Beside the board it
writes the net classes into breakout.kicad_pro, and their minimum track widths and the
clearance rules into breakout.kicad_dru.

What it does not write: a track, a via or a pour. `kicad-cli` has no netlist-import and no
Specctra-DSN export (the mule plan's Task 3 note), so routing is a person's, in pcbnew.
ONCE A PERSON HAS ROUTED THE BOARD, DO NOT RE-RUN THIS: it writes the file whole, and a
re-run discards the routing. `main()` refuses to overwrite a board that has routed items.

Prerequisite, as for the mule (`main()` checks it):

    kicad-cli sch export netlist --format kicadsexpr \\
        -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch

and afterwards, as the mule's recipe does (hardware/README.md, "Format upgrade"):

    kicad-cli pcb upgrade hardware/breakout/breakout.kicad_pcb
    python3 hardware/gen/check_breakout_placement.py

HOW IT PLACES. Three passes, in the order the plan gives.

1. Panel connectors, at their panel positions. floorplan.md §2 gives the order on each face
   and panel-elevations.md the pitch; no source gives an X. The centres are PANEL_CENTRES
   below and are recorded in hardware/breakout/placement.md §2.
2. The parts that straddle a barrier, and every IC and board connector, at coordinates
   written out below (ANCHORS). These are the placement decisions; each cites its
   constraint or is marked [engineering judgment] in placement.md.
3. Every other part -- 430-odd resistors, capacitors, diodes and test points -- next to the
   part it serves. Which part that is comes from the netlist by the rules in `owner_of()`,
   with OWNER_OVERRIDE where a constraint says otherwise (K-7, K-19). Where it goes is the
   nearest free position to the pads it connects to, INSIDE ITS OWN DOMAIN'S REGION: the
   search never offers a position on the wrong side of a barrier.

THE BARRIER CHECK. The regions, the declared straddlers and the domain derivation are
check_breakout_placement.py's, imported here, exactly as gen_mule_pcb.py imports
check_mule_netlist.py. Pass 3 searches inside them, and before anything is written the
whole board text is put through `run_checks()` -- the same function the committed file is
tested with -- and a failure stops the write. Placement cannot drift from the check, and
the check does not take the generator's word for where anything is: it re-reads the text.

Coordinates are the board file's: x to the right, y DOWN, so board y = 240 - floorplan Y
and the back edge is at the top, as floorplan.md §7 draws it. See the check's docstring.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_breakout_placement as chk  # noqa: E402
from check_breakout_placement import (  # noqa: E402
    BACK_Y, BAND_MM, BOARD_H, BOARD_W, COPPER_LAYERS, FRONT_Y, INTAN_LINE_Y, INTAN_X0,
    INTAN_X1, KEEPOUT_MARGIN_MM, NI_BACK_Y, NI_X, SLOT_C162_MM, SLOT_MM, SLOT_OPTO_MM,
    SPLIT_GAP_MM,
    SPLIT_X, fy,
)
from gen_breakout_netlist_contract import (  # noqa: E402
    _natural_key, parse_component_values, parse_net_nodes,
)
from kicad_pcb import (  # noqa: E402
    Board, _child_at, _child_tag, _split_children, extract_footprint,
    footprint_courtyard_bbox, place_point,
)

OUT = Path(__file__).resolve().parent.parent / "breakout"
PCB_PATH = OUT / "breakout.kicad_pcb"
NET_PATH = OUT / "breakout.net"
PRO_PATH = OUT / "breakout.kicad_pro"
DRU_PATH = OUT / "breakout.kicad_dru"
CONTRACT_PATH = OUT / "netlist-contract.json"

# ===========================================================================
# 1. Panel connectors. Centre X along each face, mm. floorplan.md §2.1 and §2.2 for the
#    order; panel-elevations.md §2 for the 18.0 mm BNC pitch and each item's width.
#    The centres themselves are [engineering judgment]: see placement.md §2.
# ===========================================================================
BNC_PITCH = chk.BNC_PITCH_MM


def _row(first_centre: float, refs: list[str]) -> dict[str, float]:
    return {r: first_centre + i * BNC_PITCH for i, r in enumerate(refs)}


FRONT_CENTRES: dict[str, float] = {
    "J45": 34.5,
    "J5": 81.0,
    **_row(121.0, ["J46", "J48", "J50", "J37", "J39", "J41", "J43"]),
    "J36": 275.0,
}
BACK_CENTRES: dict[str, float] = {
    **_row(9.5, ["J13", "J15", "J17", "J6"]),
    "J3": 107.0,
    "J57": 182.0,
    "J2": 226.0,
    "J19": 298.0,
    **_row(346.0, ["J29", "J26", "J24", "J22", "J20"]),
}

# Where each connector family's footprint origin is, relative to its body. Read from the
# footprints themselves (the two custom ones are generated from datasheet-params.toml).
BNC_ORIGIN_TO_FACE = 35.15    # pad 1 to the body's front face; BNC_Dual_RA_Isolated, F.Fab
MDR_ORIGIN_TO_CENTRE = 20.955  # contact 1 to the middle of the 34-wide mating row: 16.5 x 1.27
DSUB_ORIGIN_TO_CENTRE = 24.93  # pin 1 to the midpoint of the shell holes: (-6.82 + 56.68) / 2
DSUB_ORIGIN_TO_EDGE = 12.74    # pin 1's row to the PCB edge: 1.42 + 11.32 (the stock footprint)
# How far each family's face stands beyond the board edge: the check's constants, where
# the reasoning is. [engineering judgment] until a part is measured.
BNC_BEYOND = chk.BNC_FACE_BEYOND_EDGE_MM
MDR_BEYOND = chk.MDR_FACE_BEYOND_EDGE_MM


def panel_anchor(ref: str, lib_id: str) -> tuple[float, float, int]:
    """(x, y, rot) of a panel connector's footprint origin."""
    front = ref in FRONT_CENTRES
    cx = FRONT_CENTRES[ref] if front else BACK_CENTRES[ref]
    if lib_id.endswith("BNC_Dual_RA_Isolated"):
        d = round(BNC_ORIGIN_TO_FACE - BNC_BEYOND, 3)
        return (cx, FRONT_Y - d, 180) if front else (cx, BACK_Y + d, 0)
    if lib_id.endswith("MDR68_Male_RightAngle"):
        if front:
            return (round(cx + MDR_ORIGIN_TO_CENTRE, 3), round(FRONT_Y + MDR_BEYOND, 3), 180)
        return (round(cx - MDR_ORIGIN_TO_CENTRE, 3), round(BACK_Y - MDR_BEYOND, 3), 0)
    if "DSUB-37" in lib_id:
        assert not front
        return (round(cx + DSUB_ORIGIN_TO_CENTRE, 3), BACK_Y + DSUB_ORIGIN_TO_EDGE, 180)
    raise AssertionError(f"{ref}: no panel rule for {lib_id}")


# ===========================================================================
# 2. Anchors: straddlers, ICs, board connectors. ref -> (x, y, rot), board frame.
#    Each group's reason is in placement.md §3 to §6, by the heading given here.
# ===========================================================================
ANCHORS: dict[str, tuple[float, float, int]] = {}

# -- NI barrier (placement.md §3.1). B-10: long axis along the line, pins 1-8 on the DGND
#    side, which at this line is +x, so rot 180. One slot under all seven (B-2c, B-5).
for _ref, _y in (("U56", 109.0), ("U57", 121.0), ("U58", 133.0), ("U59", 145.0),
                 ("U61", 157.0), ("U60", 169.0)):
    ANCHORS[_ref] = (NI_X, _y, 180)
# U62 (DNP, B-22): the slot passes between pin 2 and pin 4, 5.08 mm apart, so its origin
# (pin 1) is 5.08 mm on the DGND side of the line; rot 270 turns its pin row across it.
ANCHORS["U62"] = (NI_X + 5.08, 182.0, 270)

# -- Intan barrier, DGND part (placement.md §3.2). Order along the line is floorplan
#    §3.2's: U64, U65, U63 with C162, U2, then the T. rot 270 puts pins 1-8 (and C162's
#    pad 1, and U63's pins 1-2) on the DGND side, which at this line is -y.
ANCHORS["U64"] = (119.5, INTAN_LINE_Y, 270)
ANCHORS["U65"] = (131.0, INTAN_LINE_Y, 270)
ANCHORS["U63"] = (143.5, INTAN_LINE_Y - 5.08, 270)  # the line midway between pins 2 and 4
ANCHORS["C162"] = (148.0, INTAN_LINE_Y, 270)        # B-13: beside U63
ANCHORS["U2"] = (152.5, INTAN_LINE_Y - 3.81, 0)     # midway between the y=0 and y=7.62 rows

# -- the star (placement.md §4). G-2, P-1: J1 at the split; NT1 on it.
ANCHORS["J1"] = (173.5, 80.0, 0)
ANCHORS["NT1"] = (SPLIT_X, 94.5, 180)   # pad 1 (AGND) at +x, pad 2 (DGND) at -x
ANCHORS["NT2"] = (165.5, 98.5, 0)       # G-4: beside NT1
ANCHORS["NT3"] = (165.5, 101.5, 0)      # G-5: at the inlet star cluster
ANCHORS["J56"] = (165.0, 55.0, 0)       # M-15; clear of the IO Board's shadow (x > 160)
# P-2: the protection chain in netlist order at J1. The +/-12 V parts on the AGND side,
# the +5 V and fan parts on the DGND side (G-14, G-24).
ANCHORS["F1"] = (179.0, 75.0, 180)
ANCHORS["F2"] = (179.0, 79.0, 0)
ANCHORS["F3"] = (179.0, 83.0, 0)
ANCHORS["D1"] = (188.0, 79.0, 180)
ANCHORS["D2"] = (188.0, 83.0, 0)
ANCHORS["F4"] = (165.5, 85.08, 180)     # in line with J1 pin 3 (P5_RAW)
ANCHORS["D3"] = (157.0, 85.08, 0)
ANCHORS["D44"] = (157.0, 79.5, 0)
ANCHORS["U1"] = (159.5, 92.5, 0)        # P-13: beside the +5V entry capacitors
ANCHORS["J52"] = (157.0, 65.0, 0)       # P-14, G-24; floorplan Open question 6
ANCHORS["J53"] = (163.0, 65.0, 0)
ANCHORS["J54"] = (4.0, 93.0, 90)        # P-14: toward the left side panel
ANCHORS["J55"] = (15.0, 93.0, 90)

# -- sync module interface (placement.md §5.1). M-17, floorplan §6.6: J7 just inboard of
#    the IO Board's shadow, about X 100-155, floorplan Y 130-148.
ANCHORS["J7"] = (103.0, 97.0, 90)
ANCHORS["J18"] = (158.0, 103.5, 0)
ANCHORS["U66"] = (164.5, 109.0, 0)      # K-30: beside J18
ANCHORS["U15"] = (113.0, 84.0, 0)       # K-6: beside J7
ANCHORS["U74"] = (124.0, 84.0, 0)
ANCHORS["U14"] = (140.0, 84.0, 0)
for _i, _ref in enumerate(("J8", "J9", "J10", "J11", "J12")):   # K-8: grouped at U15
    ANCHORS[_ref] = (82.0 + 5.0 * _i, 82.5, 0)

# -- task-PC digital (placement.md §5.2). G-23: U5-U7 and their clamps at J3.
#    The series resistors and clamps fill the strip between J3's body and these three.
ANCHORS["U5"] = (88.0, 72.0, 270)
ANCHORS["U6"] = (107.0, 72.0, 270)
ANCHORS["U7"] = (126.0, 72.0, 270)
# K-33: each LED-driver pair side by side, between J7 and the NI line, level with the
# optocouplers it drives. rot 180 turns the outputs toward the line.
for _a, _b, _y in (("U8", "U70", 115.0), ("U9", "U71", 139.0),
                   ("U11", "U73", 157.0), ("U10", "U72", 171.5)):
    ANCHORS[_a] = (84.0, _y, 180)
    ANCHORS[_b] = (93.0, _y, 180)
# reward logic, in the DGND tongue behind J5 and J4
ANCHORS["U13"] = (78.0, 193.0, 0)
ANCHORS["U69"] = (88.0, 193.0, 0)
ANCHORS["U12"] = (98.0, 193.0, 0)
ANCHORS["J4"] = (94.25, 229.25, 0)      # M-19; constraints Open question 14

# -- control bus (placement.md §5.3). K-31: U67, U68 on the DGND side, beside the mux bank.
ANCHORS["U67"] = (162.0, 126.0, 0)
ANCHORS["U68"] = (162.0, 146.0, 0)

# -- mux bank and Intan-side INA105s (placement.md §6.1). B-17, B-19: each INA105 wholly on
#    the island with pins 1-4 facing the line; each mux directly behind it in AGND.
MUX_X0, MUX_PITCH = 175.0, 8.2
for _i in range(8):
    _x = round(MUX_X0 + MUX_PITCH * _i, 3)
    ANCHORS[f"U{38 + 2 * _i}"] = (_x, 157.5, 180)   # ADG1206: pin 28 (MUX_OUT) toward the line
    # the mux's two bypass capacitors, side by side behind it (the bank is too tight a
    # pitch for the search to keep each pair at its own mux)
    ANCHORS[f"C{86 + 4 * _i}"] = (round(_x - 2.6, 3), 150.2, 90)
    ANCHORS[f"C{87 + 4 * _i}"] = (round(_x + 2.6, 3), 150.2, 90)
    ANCHORS[f"U{39 + 2 * _i}"] = (_x, 171.0, 270)   # INA105

# -- Intan island supply (placement.md §6.2). P-4: each pi filter in line and in order.
for _y, _c_raw, _fb, _c_filt, _ldo in ((183.5, "C10", "FB1", "C11", "U3"),
                                       (192.0, "C16", "FB2", "C17", "U4")):
    ANCHORS[_c_raw] = (160.0, _y, 0)
    ANCHORS[_fb] = (155.3, _y, 0)
    ANCHORS[_c_filt] = (150.6, _y, 0)
    ANCHORS[_ldo] = (144.0, _y, 0)
ANCHORS["NT4"] = (146.0, 177.5, 0)      # G-6: at U63's secondary pins
ANCHORS["U75"] = (168.0, 188.0, 0)
for _ref, _x in (("TP5", 118.0), ("TP7", 123.0), ("TP6", 128.0)):   # P-15: together
    ANCHORS[_ref] = (_x, 188.0, 0)

# -- comparators and DAC (placement.md §6.3). K-25, G-12, G-13: AGND side of the split.
ANCHORS["U54"] = (178.0, 52.0, 0)
ANCHORS["U76"] = (178.0, 66.0, 0)
ANCHORS["U55"] = (187.5, 48.0, 0)

# -- buffer banks (placement.md §6.4). K-18, K-19.
for _i, _ref in enumerate(("U31", "U32", "U33", "U34")):
    ANCHORS[_ref] = (251.0 + 14.0 * _i, 177.0, 90)
for _i, _ref in enumerate(("U35", "U36", "U37")):
    ANCHORS[_ref] = (206.0 + 16.0 * _i, 60.0, 90)

# -- analog front ends (placement.md §6.5). K-11, K-14, K-16: each behind its own connector.
for _i, _ref in enumerate(("U16", "U17", "U18", "U19", "U20", "U21")):
    ANCHORS[_ref] = (275.5 + 9.0 * _i, 31.0, 0)
FE_Y = 54.0
for _j, _a, _b in (("J29", "U29", "U30"), ("J26", "U27", "U28"),
                   ("J24", "U25", "U26"), ("J22", "U23", "U24")):
    ANCHORS[_a] = (BACK_CENTRES[_j] - 4.5, FE_Y + (3.0 if _a == "U23" else 0.0), 0)
    ANCHORS[_b] = (BACK_CENTRES[_j] + 4.5, FE_Y, 0)
ANCHORS["U22"] = (BACK_CENTRES["J20"], FE_Y, 0)       # K-11: directly behind J20
# K-23: the three divide-by-1/2 headers, each beside its own difference amplifier.
ANCHORS["J28"] = (BACK_CENTRES["J26"] + 4.5 - 1.27, 67.0, 0)
ANCHORS["J30"] = (BACK_CENTRES["J29"] - 4.5 - 1.27, 67.0, 0)
ANCHORS["J32"] = (BACK_CENTRES["J29"] + 4.5 - 1.27, 67.0, 0)

# Reference text for an anchor that has no free space above, below or beside it.
# ref -> (x, y) of the text's centre.
ANCHOR_LABEL: dict[str, tuple[float, float]] = {
    "C162": (148.4, 161.0),   # between U63 and U2, on the DGND side, clear of the slot
}
for _i in range(8):
    # the mux bank: each mux's text between its two capacitors, theirs staggered above
    _x = round(MUX_X0 + MUX_PITCH * _i, 3)
    ANCHOR_LABEL[f"U{38 + 2 * _i}"] = (_x, 150.2)
    ANCHOR_LABEL[f"C{86 + 4 * _i}"] = (round(_x - 2.6, 3), 147.9)
    ANCHOR_LABEL[f"C{87 + 4 * _i}"] = (round(_x + 2.6, 3), 146.6)

# Silkscreen notes beside each slot, as the mule has: (text, centre, height).
BARRIER_TEXT = [
    ("NI ISOLATION BARRIER", (40.0, 104.0), 1.0),
    ("INTAN ISOLATION BARRIER", (205.0, 198.0), 1.0),
]

# The CM5 IO Board (floorplan §6.3, §6.6): outline, and its two inboard standoff holes.
CM5_SHADOW = (0.0, fy(240), 160.0, fy(150))           # X 0-160, floorplan Y 150-240
CM5_HOLES = {"H1": (27.5, fy(163)), "H2": (149.0, fy(163))}
CM5_HOLE_FOOTPRINT = "MountingHole:MountingHole_2.7mm_M2.5"

# ===========================================================================
# 3. Satellites. Which part each one serves, where the netlist's own rule is not the one
#    a constraint asks for. ref -> list of owner refs.
# ===========================================================================
OWNER_OVERRIDE: dict[str, list[str]] = {
    # K-19: the 100 ohm at the op-amp output, not at the connector.
    **{f"R{n}": [o] for n, o in zip(range(78, 94), ["U31"] * 4 + ["U32"] * 4 + ["U33"] * 4 + ["U34"] * 4)},
    **{f"R{n}": [o] for n, o in zip(range(94, 103), ["U35"] * 4 + ["U36"] * 4 + ["U37"])},
    **{f"R{103 + i}": [f"U{39 + 2 * i}"] for i in range(8)},
    # K-7: the 47 ohm source terminations at the buffer outputs, not at the BNCs.
    "R198": ["U15"], "R199": ["U15"], "R200": ["U74"], "R201": ["U74"], "R202": ["U74"],
    # K-21, K-22: beside J2, between it and J57.
    "R221": ["J2", "J57"], "R222": ["J2", "J57"],
    # G-14, P-2: entry capacitors, each group on its own side of NT1, after its diode.
    "C1": ["D1"], "C2": ["D1"], "C3": ["D2"], "C4": ["D2"], "C5": ["D3"], "C6": ["D3"],
    "C7": ["U1"], "C8": ["U1"],
    "TP1": ["D1"], "TP2": ["D2"], "TP3": ["D3"], "TP4": ["U1"],
    "C9": ["U2"],                                   # P-3
    "C147": ["D44"], "C148": ["D44"],
    # P-7, P-9: C161 on the source side of L1; C155, C156 beside C161, not beside pin 2.
    "L1": ["U63"], "C161": ["L1"], "C155": ["C161"], "C156": ["C161"],
    "C137": ["U63"], "C138": ["U63"],               # G-6: the loop closes at the part
    # P-5: output capacitors and the FB / NR parts at the LDO pins.
    "C14": ["U3"], "C15": ["U3"], "C20": ["U4"], "C21": ["U4"],
    # opto-ni: the bulk pair at the connector's NI_5V pins; B-22: U62's companions at U62.
    "C133": ["J45"], "C134": ["J45"],
    "C135": ["U62"], "C136": ["FB3"], "FB3": ["C135"], "R170": ["FB3"],
    # K-10: the heartbeat LED from its own buffer leg.
    "R195": ["U15"], "D43": ["R195"],
    # K-37: at J7.
    "R25": ["J7"], "R26": ["J7"],
    # K-26: each hysteresis network at its comparator.
    "R111": ["U54"], "R114": ["U54"], "R117": ["U54"], "R190": ["U54"],
    "R120": ["U54"], "R121": ["R120"],
    # the pull-ups are DGND parts (+3V3); each at the first DGND load of its output.
    "R113": ["U11"], "R116": ["U11"], "R119": ["U11"], "R215": ["J7"], "R220": ["J7"],
    # G-22, FT: the CAM_FRAME_THR divider is DGND-referenced.
    "R209": ["R210"], "R210": ["C160"], "C160": ["J7"],
    # K-28: the frame-time pull-ups with their clamps, at the connector.
    "R212": ["R211"], "R217": ["R216"],
    # power-good LEDs beside the entry they report.
    "R192": ["D1"], "D40": ["R192"], "R193": ["D2"], "D41": ["R193"],
    "R194": ["D3"], "D42": ["R194"],
    # K-29: the bus pull-ups at the bridge.
    "R188": ["U66"], "R189": ["U66"],
    # the reward button's debounce parts at the logic, not at the panel.
    "C29": ["U13"], "R24": ["U13"], "C149": ["U69"], "R191": ["U69"],
    # the photodiode filter after the TIA goes with the TIA (K-11 keeps the junction short).
    "R39": ["U22"], "C49": ["R39"], "R41": ["U22"], "C51": ["R41"],
    # the Intan-side buffer's protection at J50
    "R187": ["J50"],
}

GROUND_NETS = {"AGND", "DGND", "NI_GND", "INTAN_GND"}
# Panel connectors, for "a part on a net that reaches a panel connector goes at that
# connector" (K-14, K-15, K-28, K-36).
PANEL_REFS = set(FRONT_CENTRES) | set(BACK_CENTRES) | {"J4"}

# ---------------------------------------------------------------------------
# Search parameters. None is a dimension of the design: they shape where the generator
# looks, and the result is judged by DRC and by the check, not by these.
# ---------------------------------------------------------------------------
GRID_MM = 0.25           # candidate positions are on this grid
COURTYARD_GAP_MM = 0.25  # kept between any two courtyards, beyond DRC's "no overlap"
EDGE_MARGIN_MM = 1.0     # courtyard to board edge, for parts that are not panel connectors
LABEL_H_MM = 0.8         # Board.place()'s reference text height
SEARCH_RADII_MM = (6.0, 12.0, 24.0, 48.0, 96.0)

_BARRIER_CLEAR = BAND_MM / 2 + KEEPOUT_MARGIN_MM   # 2.25: a courtyard stays out of every
_SPLIT_CLEAR = SPLIT_GAP_MM / 2 + 0.5              # barrier's keep-out, and off the gap
# Where a part of each domain may be searched for: the region, shrunk by the margins
# above. A courtyard and its label must lie wholly inside ONE of a domain's rectangles;
# the rectangles overlap so that this loses nothing of the L-shaped regions.
ALLOWED: dict[str, list[tuple[float, float, float, float]]] = {
    "NI": [(EDGE_MARGIN_MM, NI_BACK_Y + _BARRIER_CLEAR, NI_X - _BARRIER_CLEAR,
            BOARD_H - EDGE_MARGIN_MM)],
    "INTAN": [(INTAN_X0 + _BARRIER_CLEAR, INTAN_LINE_Y + _BARRIER_CLEAR,
               INTAN_X1 - _BARRIER_CLEAR, BOARD_H - EDGE_MARGIN_MM)],
    "DGND": [
        (EDGE_MARGIN_MM, EDGE_MARGIN_MM, SPLIT_X - _SPLIT_CLEAR, NI_BACK_Y - _BARRIER_CLEAR),
        (NI_X + _BARRIER_CLEAR, EDGE_MARGIN_MM, SPLIT_X - _SPLIT_CLEAR,
         INTAN_LINE_Y - _BARRIER_CLEAR),
        (NI_X + _BARRIER_CLEAR, EDGE_MARGIN_MM, INTAN_X0 - _BARRIER_CLEAR,
         BOARD_H - EDGE_MARGIN_MM),
    ],
    "AGND": [
        (SPLIT_X + _SPLIT_CLEAR, EDGE_MARGIN_MM, BOARD_W - EDGE_MARGIN_MM,
         INTAN_LINE_Y - _BARRIER_CLEAR),
        (INTAN_X1 + _BARRIER_CLEAR, EDGE_MARGIN_MM, BOARD_W - EDGE_MARGIN_MM,
         BOARD_H - EDGE_MARGIN_MM),
    ],
}

# The slots (placement.md §3). Each is one straight run under every straddler of its line
# (B-2c, B-5), ending SLOT_END_MM beyond the outermost straddler's courtyard.
SLOT_END_MM = 1.5   # [engineering judgment]: past the last part, short of the island's corner
# B-23: how far the 1.5 mm section runs past C162's land at each end. Equal to the
# laminate the narrow section leaves beside the pad (0.55 mm), so the corner where the
# slot widens is no nearer the pad than the slot's side is. [engineering judgment]
C162_NARROW_PAST_PAD_MM = 0.55


# ===========================================================================
# Footprint geometry, read from the libraries.
# ===========================================================================
_PADHEAD_RE = re.compile(r'^\(pad\s+"([^"]*)"\s+(\S+)\s+(\S+)')
_SIZE_RE = re.compile(r'\(size\s+(-?[\d.]+)\s+(-?[\d.]+)\)')
_GEOM: dict[str, dict] = {}


def geom(lib_id: str) -> dict:
    """{'pads': [(num, lx, ly, w, h)], 'box': local keep-clear box} for a library footprint.
    The box is the courtyard; a footprint that draws none (the net tie) gets its pads'
    extent plus the courtyard gap, so nothing is placed on its copper."""
    if lib_id not in _GEOM:
        lib, mod = lib_id.split(":", 1)
        pads = []
        for child in _split_children(extract_footprint(lib, mod)):
            if _child_tag(child) != "pad":
                continue
            m = _PADHEAD_RE.match(child)
            lx, ly, ang = _child_at(child)
            assert ang % 90 == 0, f"{lib_id}: pad at a non-right angle in the library"
            sm = _SIZE_RE.search(child)
            w, h = float(sm.group(1)), float(sm.group(2))
            if ang % 180 == 90:
                w, h = h, w
            pads.append((m.group(1), lx, ly, w, h))
        box = footprint_courtyard_bbox(lib, mod)
        if box is None:
            box = (min(p[1] - p[3] / 2 for p in pads) - COURTYARD_GAP_MM,
                   min(p[2] - p[4] / 2 for p in pads) - COURTYARD_GAP_MM,
                   max(p[1] + p[3] / 2 for p in pads) + COURTYARD_GAP_MM,
                   max(p[2] + p[4] / 2 for p in pads) + COURTYARD_GAP_MM)
        _GEOM[lib_id] = {"pads": pads, "box": box}
    return _GEOM[lib_id]


def placed_box(lib_id: str, x: float, y: float, rot: int) -> tuple[float, float, float, float]:
    b = geom(lib_id)["box"]
    pts = [place_point(x, y, rot, cx, cy) for cx in (b[0], b[2]) for cy in (b[1], b[3])]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def placed_pads(lib_id: str, x: float, y: float, rot: int) -> dict[str, list[tuple[float, float, float, float]]]:
    """{pad number: [(x, y, half_w, half_h), ...]} in board coordinates."""
    out: dict[str, list] = {}
    for num, lx, ly, w, h in geom(lib_id)["pads"]:
        px, py = place_point(x, y, rot, lx, ly)
        if rot % 180 == 90:
            w, h = h, w
        out.setdefault(num, []).append((px, py, w / 2, h / 2))
    return out


def label_size(ref: str) -> tuple[float, float]:
    """A box that holds a reference's text at Board.place()'s 0.8 mm height. One text
    height per character is wider than KiCad's stroke font draws; the surplus is margin."""
    return (len(ref) * LABEL_H_MM + 0.4, LABEL_H_MM + 0.4)


# ===========================================================================
# The netlist.
# ===========================================================================
_COMP_RE = re.compile(
    r'\(comp\s*\(ref\s+"([^"]+)"\)\s*\(value\s+"([^"]*)"\)\s*\(footprint\s+"([^"]*)"\)'
)
_SHEET_RE = re.compile(r'\(sheetpath\s*\(names\s+"([^"]+)"\)\s*\(tstamps\s+"([^"]+)"\)')
_TSTAMP_RE = re.compile(r'\(tstamps\s+"([0-9a-fA-F-]{36})"')
_SHEETFILE_RE = re.compile(r'\(name\s+"Sheetfile"\)\s*\(value\s+"([^"]*)"\)')


def load_netlist(path: Path) -> tuple[dict[str, dict], dict[str, dict[str, str]]]:
    """(components, pin nets). components[ref] = footprint, value, sheet name, sheet file,
    schematic path and DNP flag, each from the component's own block of the exported
    netlist; pin nets[ref][pin] = net name."""
    text = path.read_text()
    comps: dict[str, dict] = {}
    ms = list(_COMP_RE.finditer(text))
    end_all = text.find("(libparts")
    for i, m in enumerate(ms):
        blk = text[m.end(): ms[i + 1].start() if i + 1 < len(ms) else end_all]
        sheet = _SHEET_RE.search(blk)
        stamps = _TSTAMP_RE.findall(blk)
        sheetfile = _SHEETFILE_RE.search(blk)
        assert sheet and stamps and sheetfile, f"{m.group(1)}: incomplete component block"
        assert m.group(1) not in comps, f"duplicate reference {m.group(1)}"
        comps[m.group(1)] = {
            "value": m.group(2), "lib_id": m.group(3),
            "sheet": sheet.group(1).strip("/"), "sheetfile": sheetfile.group(1),
            # a multi-unit symbol lists one uuid per unit; pcbnew links the first
            "path": sheet.group(2) + stamps[0],
            "dnp": '(name "dnp")' in blk,
        }
    pin_nets: dict[str, dict[str, str]] = {r: {} for r in comps}
    for net, nodes in parse_net_nodes(text).items():
        for ref, pin, _fn, _ty in nodes:
            assert pin not in pin_nets[ref], f"{ref}.{pin} is on two nets"
            pin_nets[ref][pin] = net
    return comps, pin_nets


def assert_netlist_is_the_contracts(path: Path, contract: dict) -> None:
    """The board is generated from breakout.net, which is not committed, and judged against
    netlist-contract.json, which is. Refuse to run if they are not the same netlist."""
    text = path.read_text()
    assert parse_component_values(text) == contract["component_values"], (
        f"{path} and netlist-contract.json disagree on components: re-export the netlist"
    )
    mine = {n: sorted(tuple(x) for x in nodes) for n, nodes in parse_net_nodes(text).items()}
    theirs = {n: sorted(tuple(x) for x in nodes) for n, nodes in contract["nets"].items()}
    assert mine == theirs, (
        f"{path} and netlist-contract.json disagree on nets: re-export the netlist, or "
        f"regenerate the contract (hardware/README.md, 'Whole-board netlist contract')"
    )


# ===========================================================================
# The placer.
# ===========================================================================
class Placer:
    def __init__(self, comps, pin_nets, domains):
        self.comps, self.pin_nets, self.dom = comps, pin_nets, domains
        self.at: dict[str, tuple[float, float, int]] = {}
        self.label_at: dict[str, tuple[float, float]] = {}
        self.pads: dict[str, dict[str, list]] = {}
        self.boxes: dict[str, tuple[float, float, float, float]] = {}
        self._cells: dict[tuple[int, int], list[tuple[tuple, str]]] = {}
        self.no_label: list[tuple[float, float, float, float]] = []
        self.owner_log: dict[str, list[str]] = {}
        self.target_use: dict[tuple[str, str], int] = {}
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for ref, pins in pin_nets.items():
            for pin, net in pins.items():
                self.nets.setdefault(net, []).append((ref, pin))

    # -- occupancy ---------------------------------------------------------
    _CELL = 4.0

    def _cells_of(self, box):
        for i in range(int(box[0] // self._CELL), int(box[2] // self._CELL) + 1):
            for j in range(int(box[1] // self._CELL), int(box[3] // self._CELL) + 1):
                yield (i, j)

    def _reserve(self, box, tag: str) -> None:
        for c in self._cells_of(box):
            self._cells.setdefault(c, []).append((box, tag))

    def _hit(self, box, gap: float) -> str | None:
        g = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
        for c in self._cells_of(g):
            for other, tag in self._cells.get(c, ()):
                if chk.boxes_overlap(g, other):
                    return tag
        return None

    # -- labels ------------------------------------------------------------
    def _label_options(self, ref, box):
        w, h = label_size(ref)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        off = LABEL_H_MM / 2 + 0.4
        return [
            (cx, box[1] - off), (cx, box[3] + off),
            (box[2] + w / 2 + 0.3, cy), (box[0] - w / 2 - 0.3, cy),
        ]

    def _label_ok(self, ref, pos) -> bool:
        w, h = label_size(ref)
        lb = (pos[0] - w / 2, pos[1] - h / 2, pos[0] + w / 2, pos[1] + h / 2)
        if not chk.box_in_box(lb, (0.5, 0.5, BOARD_W - 0.5, BOARD_H - 0.5)):
            return False
        if any(chk.boxes_overlap(lb, nb) for nb in self.no_label):
            return False
        return self._hit(lb, 0.0) is None

    def _commit(self, ref, x, y, rot, box, label_pos) -> None:
        lib_id = self.comps[ref]["lib_id"] if ref in self.comps else CM5_HOLE_FOOTPRINT
        self.at[ref] = (x, y, rot)
        self.boxes[ref] = box
        self.pads[ref] = placed_pads(lib_id, x, y, rot)
        self._reserve(box, ref)
        self.label_at[ref] = label_pos
        w, h = label_size(ref)
        self._reserve((label_pos[0] - w / 2, label_pos[1] - h / 2,
                       label_pos[0] + w / 2, label_pos[1] + h / 2), ref + " label")

    # -- pass 1 and 2: a part at stated coordinates -------------------------
    def fixed(self, ref, x, y, rot, lib_id=None, label=None) -> None:
        lib_id = lib_id or self.comps[ref]["lib_id"]
        box = placed_box(lib_id, x, y, rot)
        hit = self._hit(box, COURTYARD_GAP_MM)
        assert hit is None, f"{ref} at ({x}, {y}, {rot}) lands on {hit}"
        self.at[ref] = (x, y, rot)
        self.boxes[ref] = box
        self.pads[ref] = placed_pads(lib_id, x, y, rot)
        self._reserve(box, ref)
        self._pending_labels.append((ref, label))

    _pending_labels: list = []

    def label_anchors(self) -> None:
        """Anchors get their labels once ALL of them are down, so that no label is put
        where a later anchor then lands."""
        for ref, label in self._pending_labels:
            options = [label] if label else self._label_options(ref, self.boxes[ref])
            for pos in options:
                if self._label_ok(ref, pos):
                    break
            else:
                raise AssertionError(f"{ref}: no free place for its reference text")
            self.label_at[ref] = pos
            w, h = label_size(ref)
            self._reserve((pos[0] - w / 2, pos[1] - h / 2, pos[0] + w / 2, pos[1] + h / 2),
                          ref + " label")
        self._pending_labels.clear()

    # -- pass 3: a part beside the part it serves ----------------------------
    def owner_of(self, ref) -> list[str]:
        """The part(s) `ref` is placed beside. In order:

        1. OWNER_OVERRIDE.
        2. A clamp diode goes with the series resistor on its clamped node (K-14, K-28).
        3. A part on a net that reaches a panel connector goes at that connector (K-14,
           K-15, K-28, K-36) -- unless it also has a pin on a supply rail: a pull-up
           belongs with the part whose output it pulls up (K-34), and rule 4 finds that.
        4. A part on a signal net goes with the IC on its least-shared signal net: the
           most local connection it has.
        5. A part on rails only (a bypass capacitor) goes to an IC on its own sheet that
           has a pin on that rail; the capacitors of one value on one rail are dealt out
           one per IC in reference order, and round again if there are more (K-35).
        """
        if ref in OWNER_OVERRIDE:
            return OWNER_OVERRIDE[ref]
        c = self.comps[ref]
        pins = self.pin_nets[ref]
        signal = [n for n in pins.values() if n not in chk.SEED_DOMAIN]
        if c["value"] == "BAT54S":
            for other, _pin in self.nets[pins["3"]]:
                if other.startswith("R") and other != ref:
                    onets = set(self.pin_nets[other].values()) - {pins["3"]}
                    if any(r in PANEL_REFS for n in onets for r, _ in self.nets[n]):
                        return [other]
        on_supply = any(n in chk.SEED_DOMAIN and n not in GROUND_NETS for n in pins.values())
        for net in ([] if on_supply else signal):
            conns = sorted({r for r, _ in self.nets[net] if r in PANEL_REFS}, key=_natural_key)
            if conns:
                return [conns[0]]
        best = None
        for net in sorted(set(signal), key=lambda n: (len(self.nets[n]), n)):
            ics = sorted({r for r, _ in self.nets[net]
                          if r != ref and (r.startswith("U") or r in ANCHORS)},
                         key=lambda r: (not r.startswith("U"), _natural_key(r)))
            if ics:
                best = ics[0]
                break
        if best:
            return [best]
        if not signal:
            rail = [n for n in pins.values() if n not in GROUND_NETS]
            assert rail, f"{ref}: a part between two grounds"
            key = (c["sheet"], c["value"], tuple(sorted(pins.values())))
            peers = sorted((r for r, cc in self.comps.items()
                            if r not in OWNER_OVERRIDE and r not in ANCHORS
                            and (cc["sheet"], cc["value"],
                                 tuple(sorted(self.pin_nets[r].values()))) == key),
                           key=_natural_key)
            ics = sorted((r for r, cc in self.comps.items()
                          if r.startswith("U") and cc["sheet"] == c["sheet"]
                          and rail[0] in self.pin_nets[r].values()), key=_natural_key)
            assert ics, f"{ref}: no IC on sheet {c['sheet']} uses {rail[0]}"
            return [ics[peers.index(ref) % len(ics)]]
        raise AssertionError(f"{ref}: no rule finds the part it serves; add an override")

    def _targets(self, ref, owners) -> list[tuple[str, tuple[float, float]]]:
        """(own pad number, board point) pairs: for each of `ref`'s pads that shares a net
        with an owner, the owner pad to sit beside. Where an owner has several pads on the
        net (an IC's two supply pins), the one fewest satellites have taken so far."""
        out = []
        for pin, net in sorted(self.pin_nets[ref].items()):
            cands = []
            for o in owners:
                for opin, onet in self.pin_nets.get(o, {}).items():
                    if onet == net and opin in self.pads[o]:
                        for (px, py, _hw, _hh) in self.pads[o][opin]:
                            if self.comps[o]["lib_id"].endswith("MDR68_Male_RightAngle"):
                                # An MDR68's contacts are under 51 mm of body (X-4). Aim
                                # at the contact's own X at the body's inboard end, so a
                                # connector's parts line up behind it in contact order
                                # and are not squeezed down its sides.
                                ob = self.boxes[o]
                                py = ob[3] + 2.0 if ob[1] < BOARD_H / 2 else ob[1] - 2.0
                            cands.append((self.target_use.get((o, opin), 0), o, opin, px, py))
            if cands and net in GROUND_NETS and len(self.pin_nets[ref]) > 1 \
                    and any(n not in GROUND_NETS for n in self.pin_nets[ref].values()):
                # ground is a plane: a part with another pin to aim at is not pulled
                # toward one particular ground pin
                continue
            if cands:
                out.append((pin, cands))
        if not out:
            return []
        # choose one candidate per pad: least used, then nearest to the others' centroid
        chosen = []
        for pin, cands in out:
            cands.sort(key=lambda c: (c[0], _natural_key(c[1]), _natural_key(c[2])))
            use, o, opin, px, py = cands[0]
            chosen.append((pin, (px, py), (o, opin)))
        for _pin, _pt, key in chosen:
            self.target_use[key] = self.target_use.get(key, 0) + 1
        return [(pin, pt) for pin, pt, _k in chosen]

    def auto(self, ref) -> None:
        owners = self.owner_of(ref)
        assert all(o in self.at for o in owners), f"{ref}: owner {owners} not placed yet"
        self.owner_log[ref] = owners
        lib_id = self.comps[ref]["lib_id"]
        domain = self.dom["home"][ref]
        targets = self._targets(ref, owners)
        local = {num: (lx, ly) for num, lx, ly, _w, _h in geom(lib_id)["pads"]}
        if targets:
            cx = sum(t[1][0] for t in targets) / len(targets)
            cy = sum(t[1][1] for t in targets) / len(targets)
        else:
            ob = self.boxes[owners[0]]
            cx, cy = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2
        gx, gy = round(cx / GRID_MM) * GRID_MM, round(cy / GRID_MM) * GRID_MM
        b = geom(lib_id)["box"]
        symmetric = len(local) <= 1
        rots = (0,) if symmetric else (0, 90, 180, 270)
        tried = 0.0
        for radius in SEARCH_RADII_MM:
            n = int(radius / GRID_MM)
            cands = []
            for i in range(-n, n + 1):
                for j in range(-n, n + 1):
                    if max(abs(i), abs(j)) * GRID_MM <= tried:
                        continue
                    x, y = gx + i * GRID_MM, gy + j * GRID_MM
                    for rot in rots:
                        if targets:
                            cost = 0.0
                            for pin, (tx, ty) in targets:
                                px, py = place_point(x, y, rot, *local[pin])
                                cost += math.hypot(px - tx, py - ty)
                        else:
                            cost = math.hypot(x - cx, y - cy)
                        cands.append((round(cost, 4), y, x, rot))
            cands.sort()
            for _cost, y, x, rot in cands:
                pts = [place_point(x, y, rot, px, py) for px in (b[0], b[2]) for py in (b[1], b[3])]
                box = (min(p[0] for p in pts), min(p[1] for p in pts),
                       max(p[0] for p in pts), max(p[1] for p in pts))
                if not any(chk.box_in_box(box, r) for r in ALLOWED[domain]):
                    continue
                if self._hit(box, COURTYARD_GAP_MM) is not None:
                    continue
                for pos in self._label_options(ref, box):
                    w, h = label_size(ref)
                    lb = (pos[0] - w / 2, pos[1] - h / 2, pos[0] + w / 2, pos[1] + h / 2)
                    if any(chk.box_in_box(lb, r) for r in ALLOWED[domain]) \
                            and self._label_ok(ref, pos):
                        self._commit(ref, round(x, 4), round(y, 4), rot, box, pos)
                        return
            tried = radius
        raise AssertionError(f"{ref}: no free position within {SEARCH_RADII_MM[-1]} mm of "
                             f"{owners} inside the {domain} region")


# ===========================================================================
# Slots and keep-outs.
# ===========================================================================
def slot_profile(sections, line: float, axis: str, grow: float = 0.0):
    """The outline of a slot that changes width along its barrier line.

    `sections` is [(from, to, width), ...] along the line, end to end, no two neighbours the
    same width. `axis` is "y" for a line y = `line` (sections run in x) and "x" for a line
    x = `line` (sections run in y). `grow` offsets the outline outward, for the keep-out
    that follows the steps (B-8, B-23): each section widens by 2 x grow, the slot's two
    ends move out by grow, and at a step the wider section's grown outline advances by
    grow into the narrower one.
    """
    secs = []
    for i, (a, b, w) in enumerate(sections):
        left = sections[i - 1][2] if i else None
        right = sections[i + 1][2] if i + 1 < len(sections) else None
        assert left != w and right != w, "merge neighbouring sections of one width"
        a2 = a - grow if left is None or left < w else a + grow
        b2 = b + grow if right is None or right < w else b - grow
        assert b2 > a2, "a section is too short for the keep-out to follow"
        secs.append((a2, b2, w + 2 * grow))
    near = [pt for a, b, w in secs for pt in ((a, line - w / 2), (b, line - w / 2))]
    far = [pt for a, b, w in secs for pt in ((a, line + w / 2), (b, line + w / 2))]
    pts = near + far[::-1]
    out = []
    for along, across in pts:
        pt = (round(along, 4), round(across, 4)) if axis == "y" else (round(across, 4), round(along, 4))
        if not out or out[-1] != pt:
            out.append(pt)
    return out


def slot_sections(pl: Placer) -> dict[str, list[tuple[float, float, float]]]:
    """Each slot as sections along its line, from where the straddlers were placed.

    One run per line under every straddler (B-2c, B-5), at 2.5 mm (B-3) except: 2.0 mm
    under the SOIC-16 optocouplers (B-11, ruling R6) and 1.5 mm under C162 (B-14, R5).
    A step between two parts is midway between their courtyards.
    """
    def mid(a: str, b: str, k: int) -> float:
        return (pl.boxes[a][k + 2] + pl.boxes[b][k]) / 2

    ni = sorted((r for r, (axis, _l) in chk.STRADDLER_LINE.items() if axis == "x"),
                key=lambda r: pl.at[r][1])
    assert ni[-1] == "U62" and all(r in chk.RULED_SLOT_MM for r in ni[:-1]), ni
    y0 = max(pl.boxes[ni[0]][1] - SLOT_END_MM, NI_BACK_Y + BAND_MM / 2)
    y1 = pl.boxes[ni[-1]][3] + SLOT_END_MM
    ni_sections = [(y0, mid(ni[-2], ni[-1], 1), SLOT_OPTO_MM),
                   (mid(ni[-2], ni[-1], 1), y1, SLOT_MM)]

    it = sorted((r for r, (axis, _l) in chk.STRADDLER_LINE.items() if axis == "y"),
                key=lambda r: pl.at[r][0])
    assert it == ["U64", "U65", "U63", "C162", "U2"], it
    # ...never into the band of the island's own corner, or past the T (B-2c)
    x0 = max(pl.boxes[it[0]][0] - SLOT_END_MM, INTAN_X0 + BAND_MM / 2)
    x1 = min(pl.boxes[it[-1]][2] + SLOT_END_MM, SPLIT_X - BAND_MM / 2)
    # B-14, B-23: 1.5 mm under C162, for its land and C162_NARROW_PAST_PAD_MM beyond.
    c_pads = [p for ps in pl.pads["C162"].values() for p in ps]
    nx0 = min(p[0] - p[2] for p in c_pads) - C162_NARROW_PAST_PAD_MM
    nx1 = max(p[0] + p[2] for p in c_pads) + C162_NARROW_PAST_PAD_MM
    step = mid("U65", "U63", 0)
    intan_sections = [(x0, step, SLOT_OPTO_MM), (step, nx0, SLOT_MM),
                      (nx0, nx1, SLOT_C162_MM), (nx1, x1, SLOT_MM)]
    return {"ni": ni_sections, "intan": intan_sections}


# ===========================================================================
# Net classes and rules (constraints.md §4; V-2: entered before routing starts).
# ===========================================================================
NET_CLASS_WIDTH_MM = {   # N-1 .. N-8
    "INLET": 1.0, "INLET_N12": 0.8, "FAN": 0.8, "NI_PWR": 0.6, "ISO_PWR": 0.5,
    "RAIL_BRANCH": 0.4, "ANALOG": 0.25,
}
NET_CLASS_RULE = {"INLET": "N-1", "INLET_N12": "N-2", "FAN": "N-3", "NI_PWR": "N-4",
                  "ISO_PWR": "N-5", "RAIL_BRANCH": "N-6", "ANALOG": "N-7"}
DEFAULT_WIDTH_MM = 0.2        # N-8: DIGITAL is the default class
DEFAULT_CLEARANCE_MM = 0.2    # N-11
MIN_TRACK_GAP_MM = 0.15       # S-5
VIA_DRILL_MM, VIA_PAD_MM = 0.3, 0.6   # N-14


# S-5's "where a package forces it": the parts whose stock footprints have pads 0.15 mm
# apart. constraints.md names U55; U3 and U4 were found by DRC on this placement.
FINE_PITCH_PARTS = ("U55", "U3", "U4")


def net_class_of(net: str) -> str | None:
    """constraints.md §4's membership, by name. None is the default class (N-8)."""
    if net in ("P12_RAW", "P5_RAW", "P12_FUSED", "P5_FUSED"):
        return "INLET"                                         # N-1
    if net in ("N12_RAW", "N12_FUSED"):
        return "INLET_N12"                                     # N-2
    if net in ("FAN_12V_RAW", "FAN_12V", "FAN_RTN"):
        return "FAN"                                           # N-3
    if net in ("NI_5V", "NI_GND"):
        return "NI_PWR"                                        # N-4
    if net in ("ISO_P15_RAW", "ISO_N15_RAW", "ISO_P15_FILT", "ISO_N15_FILT",
               "ISO_P12", "ISO_N12", "ISO_5V", "ISO_5V_RTN", "U63_VIN"):
        return "ISO_PWR"                                       # N-5
    if net in ("+12V", "-12V", "+5V", "+3V3"):
        return "RAIL_BRANCH"                                   # N-6
    if (net.startswith(("A_", "MUX_OUT", "INTAN_AO", "AO_L_", "AO_R_"))
            or net.endswith("_THR")) and net != "CAM_FRAME_THR":
        return "ANALOG"                                        # N-7
    if net in ("USB_DP_PI", "USB_DM_PI"):
        return "USB"                                           # N-9
    return None


def project_json(existing: dict, nets: list[str]) -> str:
    def cls(name, width, clearance=DEFAULT_CLEARANCE_MM):
        return {"name": name, "clearance": clearance, "track_width": width,
                "via_diameter": VIA_PAD_MM, "via_drill": VIA_DRILL_MM,
                "diff_pair_width": width, "diff_pair_gap": clearance,
                "diff_pair_via_gap": 0.25, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "bus_width": 12, "wire_width": 6, "line_style": 0,
                "pcb_color": "rgba(0, 0, 0, 0.000)",
                "schematic_color": "rgba(0, 0, 0, 0.000)"}
    classes = [cls("Default", DEFAULT_WIDTH_MM)]
    classes += [cls(n, w) for n, w in NET_CLASS_WIDTH_MM.items()]
    # N-9 gives the pair no width or gap (the dielectric is not in the sources); the class
    # exists so the two nets are named as a pair, at the default width until that is known.
    classes.append(cls("USB", DEFAULT_WIDTH_MM))
    patterns = [{"netclass": c, "pattern": n}
                for n in sorted(nets, key=_natural_key) if (c := net_class_of(n))]
    pro = dict(existing)
    board = dict(pro.get("board") or {})
    ds = dict(board.get("design_settings") or {})
    rules = dict(ds.get("rules") or {})
    rules.update({"min_clearance": MIN_TRACK_GAP_MM, "min_track_width": MIN_TRACK_GAP_MM})
    ds["rules"] = rules
    board["design_settings"] = ds
    pro["board"] = board
    pro["net_settings"] = {"classes": classes, "meta": {"version": 4},
                           "net_colors": None, "netclass_assignments": None,
                           "netclass_patterns": patterns}
    pro.setdefault("schematic", {})
    return json.dumps(pro, indent=2) + "\n"


def panel_side_nets(pin_nets, nets: dict[str, list]) -> tuple[list[str], list[str]]:
    """N-13's nets, as amended 2026-11-21 (ruling R45): every net with a pin on a panel
    connector, a ground or a rail excepted. These are the nets that see a mis-plug voltage
    directly.

    Returns (all of them, those among them with no series or bond resistor). The second
    list is informational: the amendment brought them under the rule, and they are the
    ones K-32 and Open question 9 are about."""
    every, direct = [], []
    for net, nodes in nets.items():
        if net in chk.SEED_DOMAIN or net.startswith("unconnected-"):
            continue
        if not any(r in PANEL_REFS for r, _ in nodes):
            continue
        every.append(net)
        series = False
        for r, _pin in nodes:
            if not r.startswith("R"):
                continue
            other = [n for n in pin_nets[r].values() if n != net]
            if other and (other[0] in GROUND_NETS or other[0] not in chk.SEED_DOMAIN):
                series = True
        if not series:
            direct.append(net)
    return sorted(every, key=_natural_key), sorted(direct, key=_natural_key)


# N-13's one exemption (R45): U11 drives four nets straight to J3 from a TSSOP-20, whose
# pads are 0.2 mm apart at 0.65 mm pitch. Between U11's own pads the default holds.
N13_EXEMPT_PART = "U11"


def rules_text(panel_nets: list[str]) -> str:
    """breakout.kicad_dru. In a KiCad rules file a later rule outranks an earlier one, so
    each group below runs from the general rule to its exceptions."""
    def any_of(side: str, names: list[str]) -> str:
        return "(" + " || ".join(f"{side}.NetName == '{n}'" for n in names) + ")"

    def own_pads(ref: str) -> str:
        return (f"A.Type == 'Pad' && B.Type == 'Pad' && A.memberOfFootprint('{ref}') "
                f"&& B.memberOfFootprint('{ref}')")

    lines = [
        "(version 1)",
        "# Generated by hardware/gen/gen_breakout_pcb.py from hardware/breakout/constraints.md §4.",
        "# Net-class membership, default widths and the default clearance are in",
        "# breakout.kicad_pro. KiCad treats a net class's width as what the router draws,",
        "# not as a minimum DRC enforces, so the minimums are stated here (V-2).",
        "",
        "# ---- track widths --------------------------------------------------------------",
        "# N-8: 0.2 mm is the floor for every net.",
        '(rule "N-8 default track width"',
        "  (condition \"A.Type == 'Track'\")",
        f"  (constraint track_width (min {DEFAULT_WIDTH_MM}mm)))",
    ]
    for name, width in sorted(NET_CLASS_WIDTH_MM.items(), key=lambda kv: kv[1]):
        lines += [
            f'(rule "{NET_CLASS_RULE[name]} {name} track width"',
            f"  (condition \"A.Type == 'Track' && A.NetClass == '{name}'\")",
            f"  (constraint track_width (min {width}mm)))",
        ]
    lines += [
        "# S-5: 0.15 mm only where a package forces it -- inside the courtyards of the three",
        "# fine-pitch parts, where a track leaving a 0.35 or 0.5 mm pad cannot be wider.",
    ]
    for u in FINE_PITCH_PARTS:
        lines += [
            f'(rule "S-5 {u} escape track width"',
            f"  (condition \"A.Type == 'Track' && A.intersectsCourtyard('{u}')\")",
            f"  (constraint track_width (min {MIN_TRACK_GAP_MM}mm)))",
        ]
    lines += [
        "",
        "# ---- clearances ----------------------------------------------------------------",
        "# N-12: about 33 V between the raw +15 V and -15 V nets; IPC-2221 Table 6-1 B2.",
        '(rule "N-12 raw 15 V pair, outer layers"',
        "  (layer outer)",
        "  (condition \"" + any_of("A", ["ISO_P15_RAW", "ISO_P15_FILT"]) + " && "
        + any_of("B", ["ISO_N15_RAW", "ISO_N15_FILT"]) + "\")",
        "  (constraint clearance (min 0.6mm)))",
        "",
        "# N-13 (as amended 2026-11-21): 0.6 mm on outer layers from any net that reaches a",
        "# panel connector to any other net. The list is every such net in the netlist,",
        "# grounds and rails excepted.",
        '(rule "N-13 panel-side nets, outer layers"',
        "  (layer outer)",
        "  (condition \"A.Net != B.Net && " + any_of("A", panel_nets) + "\")",
        "  (constraint clearance (min 0.6mm)))",
        f"# N-13's one exemption: {N13_EXEMPT_PART}'s own pads, 0.2 mm apart at 0.65 mm pitch.",
        f'(rule "N-13 exemption, {N13_EXEMPT_PART} own pads"',
        f"  (condition \"{own_pads(N13_EXEMPT_PART)}\")",
        f"  (constraint clearance (min {DEFAULT_CLEARANCE_MM}mm)))",
        "",
        "# S-5: 0.15 mm, used only where a package forces it. Three packages do: U55's",
        "# MSOP-10 (0.35 mm pads at 0.5 mm pitch) and the HVSSOP-8 of U3 and U4 (0.5 mm",
        "# pads at 0.65 mm pitch). Each rule covers that part's own pads and nothing else.",
    ]
    for u in FINE_PITCH_PARTS:
        lines += [
            f'(rule "S-5 {u} own pad gap"',
            f"  (condition \"{own_pads(u)}\")",
            f"  (constraint clearance (min {MIN_TRACK_GAP_MM}mm)))",
        ]
    lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Build.
# ===========================================================================
def build(net_path: Path = NET_PATH, contract_path: Path = CONTRACT_PATH):
    contract = json.loads(contract_path.read_text())
    assert_netlist_is_the_contracts(net_path, contract)
    comps, pin_nets = load_netlist(net_path)
    domains = chk.derive_domains(contract)
    assert not domains["undeclared_multi"] and not domains["undecided"], domains
    pl = Placer(comps, pin_nets, domains)

    # the barrier bands and the split gap take no reference text
    for _name, rect, _t in chk.BANDS:
        pl.no_label.append((rect[0] - KEEPOUT_MARGIN_MM, rect[1] - KEEPOUT_MARGIN_MM,
                            rect[2] + KEEPOUT_MARGIN_MM, rect[3] + KEEPOUT_MARGIN_MM))
    pl.no_label.append(chk.SPLIT_STRIP)

    # --- pass 1: panel connectors, at their panel positions -----------------
    for ref in list(FRONT_CENTRES) + list(BACK_CENTRES):
        pl.fixed(ref, *panel_anchor(ref, comps[ref]["lib_id"]))
    # --- pass 2: straddlers, ICs and board connectors -----------------------
    for ref, (x, y, rot) in ANCHORS.items():
        assert ref in comps, f"{ref} is anchored and is not in the netlist"
        pl.fixed(ref, x, y, rot, label=ANCHOR_LABEL.get(ref))
    for ref, (x, y) in CM5_HOLES.items():
        pl.fixed(ref, x, y, 0, lib_id=CM5_HOLE_FOOTPRINT)
    # every anchor that is not a straddler, a tie or a panel connector is in its region
    for ref in ANCHORS:
        if ref in chk.STRADDLERS or ref == chk.TIE_ON_SPLIT:
            continue
        home = domains["home"][ref]
        assert any(chk.box_in_box(pl.boxes[ref], r) for r in ALLOWED[home]), (
            f"anchor {ref} at {pl.at[ref]} is not inside the {home} region's allowed area "
            f"{ALLOWED[home]} (courtyard {pl.boxes[ref]})"
        )
    pl.label_anchors()
    for _text, (tx, ty), size in BARRIER_TEXT:
        w = len(_text) * size + 1.0
        box = (tx - w / 2, ty - size, tx + w / 2, ty + size)
        hit = pl._hit(box, 0.0)
        assert hit is None, f"silk text {_text!r} lands on {hit}"
        pl._reserve(box, "silk text")

    # --- pass 3: everything else, beside what it serves ---------------------
    todo = sorted((r for r in comps if r not in pl.at), key=_natural_key)
    while todo:
        later = []
        for ref in todo:
            if all(o in pl.at for o in pl.owner_of(ref)):
                pl.auto(ref)
            else:
                later.append(ref)
        assert len(later) < len(todo), f"owner cycle among {later}"
        todo = later

    # --- the board ----------------------------------------------------------
    b = Board(project="breakout", width=BOARD_W, height=BOARD_H,
              sch_filename="breakout.kicad_sch", copper_layers=4)
    b.edge_rect(0, 0, BOARD_W, BOARD_H)
    sections = slot_sections(pl)
    m = KEEPOUT_MARGIN_MM
    for name, axis, line in (("ni", "x", NI_X), ("intan", "y", INTAN_LINE_Y)):
        b.edge_poly(slot_profile(sections[name], line, axis))
        b.keepout_poly(slot_profile(sections[name], line, axis, grow=m),
                       f"{name}_slot_keepout", layers=COPPER_LAYERS)
    for name, rect, tracks_ok in chk.BANDS:
        b.keepout_poly([(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]),
                        (rect[0], rect[3])], name, layers=COPPER_LAYERS, tracks=tracks_ok)
    s = chk.SPLIT_STRIP
    b.keepout_poly([(s[0], s[1]), (s[2], s[1]), (s[2], s[3]), (s[0], s[3])],
                   "agnd_dgnd_gap", layers=("In1.Cu", "In2.Cu"))

    # The IO Board is over the board, not on it: its outline is a drawing (floorplan §6.3).
    b.drawing_rect(*CM5_SHADOW, layer="Cmts.User")
    b.silk_text("CM5 IO Board above (160 x 90), port edge at the back panel; "
                "floorplan.md 6.3", 80.0, 86.0, size=1.5, layer="Cmts.User")
    for _text, (tx, ty), size in BARRIER_TEXT:
        b.silk_text(_text, tx, ty, size=size)

    for ref in sorted(pl.at, key=_natural_key):
        x, y, rot = pl.at[ref]
        if ref in CM5_HOLES:
            b.place(*CM5_HOLE_FOOTPRINT.split(":", 1), ref, "M2.5 standoff, CM5 IO Board",
                    x, y, rot=rot, ref_at=pl.label_at[ref], ref_angle=0, board_only=True)
            continue
        c = comps[ref]
        lib, mod = c["lib_id"].split(":", 1)
        b.place(lib, mod, ref, c["value"], x, y, rot=rot, pad_nets=pin_nets[ref],
                ref_at=pl.label_at[ref], ref_angle=0, sch_path=c["path"],
                sheetname=f"/{c['sheet']}/", sheetfile=c["sheetfile"], dnp=c["dnp"])
    return b, pl, comps, pin_nets, contract


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not NET_PATH.exists():
        print(f"error: {NET_PATH} does not exist. Export it with:")
        print("  kicad-cli sch export netlist --format kicadsexpr "
              "-o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch")
        return 1
    if PCB_PATH.exists() and "--force" not in argv:
        routed = chk.read_board(PCB_PATH.read_text())["routed"]
        if routed:
            print(f"error: {PCB_PATH} has {routed} routed items. Regenerating would "
                  f"discard them. Pass --force only if that is what is wanted.")
            return 1
    b, pl, comps, pin_nets, contract = build()
    text = b.render()
    pro_existing = json.loads(PRO_PATH.read_text()) if PRO_PATH.exists() else {}
    pro_text = project_json(pro_existing, list(contract["nets"]))
    panel_nets, direct_nets = panel_side_nets(pin_nets, pl.nets)
    dru_text = rules_text(panel_nets)

    fails, report = chk.run_checks(text, contract, pro_text, dru_text)
    if fails:
        print(f"NOT WRITTEN: the placement fails its own check ({len(fails)} problems)")
        for f in fails[:60]:
            print("  " + f)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    PCB_PATH.write_text(text)
    PRO_PATH.write_text(pro_text)
    DRU_PATH.write_text(dru_text)
    print(f"wrote {PCB_PATH} ({PCB_PATH.stat().st_size} bytes)")
    print(f"{len(b.net_codes) - 1} nets, {len(b.footprints_text)} footprints placed "
          f"({len(FRONT_CENTRES) + len(BACK_CENTRES)} panel connectors, "
          f"{len(ANCHORS)} anchors, {len(pl.owner_log)} beside the part they serve, "
          f"{len(CM5_HOLES)} mechanical holes)")
    print(f"parts per region: {report['parts_per_region']}; "
          f"straddlers: {report['straddlers']}; pads on the wrong side: "
          f"{report['pads_on_wrong_side']}")
    print(f"wrote {PRO_PATH.name} (net classes) and {DRU_PATH.name} (minimum widths, N-12, N-13, S-5)")
    print(f"N-13 covers {len(panel_nets)} nets; {len(direct_nets)} of them reach a panel "
          f"connector with no series resistor")
    if "--plan" in argv:
        print("  no series resistor: " + ", ".join(direct_nets))
        for ref in sorted(pl.owner_log, key=_natural_key):
            x, y, rot = pl.at[ref]
            o = pl.owner_log[ref][0]
            ob = pl.boxes[o]
            d = math.hypot(x - (ob[0] + ob[2]) / 2, y - (ob[1] + ob[3]) / 2)
            print(f"  {ref:6} {comps[ref]['value'][:12]:12} -> {','.join(pl.owner_log[ref]):10} "
                  f"at ({x:7.2f},{y:7.2f}) rot {rot:3}  {d:5.1f} mm from its centre")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
