# Breakout board: floorplan

This is the floorplan for plan Task 14, Steps 1 and 1b. It says where the board's edges,
connectors, ground domains and barriers go, where the barcode runs, and whether the CM5 IO
Board fits. It is a document, not a board file. Nothing here was measured on hardware.

> **2026-11-20.** `breakout.kicad_pcb` now exists: it is generated, with every part placed
> and nothing routed, and `placement.md` records it. Two figures this document builds on
> were corrected that day from the manufacturers' drawings (ruling R46; `constraints.md`
> X-4, X-5, M-9):
>
> - an **MDR68 takes 12.0 mm** of board from the edge, not 51.32 mm (that is a width);
> - a **dual BNC takes 15.5 mm**, not 36.2 mm (the other 20.7 mm is the jacks, beyond the
>   edge), and its tallest point is still 29.25 mm;
> - there is **no board set-back** from either panel: the panel's inner face is at the
>   board edge.
>
> The text below is left as written. Where it reserves 51.32 mm or 36.2 mm of depth (§1,
> §3.1, §5.3, §6.4 and §6.5's comparison table) the regions are deeper than the connectors
> need, which costs nothing and has not been re-drawn. §6.4's finding that nothing reaches
> the IO Board's port-edge holes should be looked at again: the connector bodies under
> them are now 12 to 15.5 mm deep, and those holes are 8 mm in.

It satisfies `constraints.md`, and cites that file by constraint ID.

Two rulings by the user on 2026-11-20 are recorded here: the CM5 IO Board rides above the
back connector row (§6.5), and the fan strip is 130 mm (§2.1). A review of the first
version asked for one fix round, and this text includes it.

## How to read this

**Every placement cites its source**, as a constraint ID in `constraints.md`, a `file:line`,
a key in `hardware/datasheet-params.toml`, or a Raspberry Pi document and page. A placement
the sources do not decide is marked **[engineering judgment]** with its reason. Treat those as
proposals. Where the sources lack a dimension the text says "not in the sources" and gives
none.

**Coordinates are nominal.** Every X and Y below is a region boundary proposed here, built by
adding up widths the sources give. None is a placement coordinate. Panel connector positions
are fixed at placement, to ±0.1 mm, and the panel is drawn from the placed footprints
(constraints M-2).

| Short | File |
|---|---|
| PLAN | `docs/superpowers/plans/2026-08-13-breakout-pcb.md` |
| SPEC | `docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md` |
| PE | `hardware/breakout/panel-elevations.md` |
| D3 | `hardware/breakout/d3-panel-thickness.md` |
| TOML | `hardware/datasheet-params.toml`, cited as `[section].key` |
| NET | `hardware/breakout/netlist-contract.json` |
| CM5IO | *Raspberry Pi Compute Module 5 IO Board* datasheet, Raspberry Pi Ltd, documentation history dated 11 September 2026; recorded at TOML `[cm5_io_board]` |
| CM5 | *Raspberry Pi Compute Module 5* datasheet, Raspberry Pi Ltd |
| PI5 | *Raspberry Pi 5* mechanical drawing, Raspberry Pi Ltd, one sheet; recorded at TOML `[raspberry_pi_5]` |
| CASE | *Raspberry Pi IO Case for Raspberry Pi Compute Module 5* product brief, Raspberry Pi Ltd, published November 2024; recorded at TOML `[cm5_io_case]` |
| B-1, K-4, … | constraint IDs in `hardware/breakout/constraints.md` |

**The frame.** Plan view from above, components up. X runs left to right as seen by someone
facing the front panel, 0 to 430 mm. Y runs from the front edge (0) to the back edge (240).
**[engineering judgment]** The sources never name a viewpoint for "left" and "right". SPEC
puts the fans on the front face's right (SPEC:798, "fans right"), the exhaust on the left
side panel, the CM5 back-left and the analog section back-right (SPEC:836, 842, 844), and
those four statements agree with each other only in this frame. The back panel, seen from
behind, is mirrored.

---

## 1. Board outline

| Item | Value | Source |
|---|---|---|
| Outline | **430 × 240 mm, nominal** | SPEC:767–769; S-2 |
| Layers | four | S-1 |
| Chassis | 2U, 19 in rack | SPEC:767 |
| Chassis interior width | about 470 mm, so 20 mm clear on each side of the board | SPEC:860–861 |
| Panel width between rack ears | about 450 mm | PE:52 |
| Panel height | 88.9 mm, about 82 mm usable | PE:98; SPEC:832 |
| Panels | 3.0 mm aluminium, front and back | D3:3; PE:130 |
| Board set-back from each panel | 36.2 mm less the BNC's protrusion through the panel. The protrusion is **not in the sources**. | PE:125–128; TOML `[bnc_dual_isolated].body_depth_mm`; M-9 |
| Chassis interior depth | **not in the sources**. It is the board depth plus two set-backs plus two panel offsets, so it waits on the protrusion as well. | M-9 |
| Chassis interior height | **not in the sources**. SPEC uses 82 mm once as a height: 20 mm × 82 mm is its "1,640 mm² per side". | SPEC:861 |
| Standoff under the board | **not in the sources**. "Any standoff from about 5 mm up" keeps both BNC ports on the panel. | PE:116–120; M-7 |
| Board thickness | 1.6 mm | S-3, itself engineering judgment |
| Corner radius, mounting holes | **not in the sources** | M-16; constraints Open question 8 |

**Do not freeze the depth.** 240 mm is the spec's "approximately". Both the front and the
back edge carry BNC bodies, so the depth and the chassis are tied by twice the set-back
(M-9, S-2).

**Where the board sits against the panel.** The board is centred in the chassis
(20 mm each side, SPEC:860–861). Taking the 450 mm of panel as centred too, the panel
overhangs the board by 10 mm at each end. **[engineering judgment]** PE gives the two
widths and not their alignment; a symmetric rack chassis is the plain reading. It matters
in §2, because a board-mounted connector cannot use those 10 mm.

---

## 2. Connectors on each face

Faces and widths are PE's (PE:17–46, 61–82), which follows SPEC §9.6 (GT-2, M-3). They are
not re-derived here. PE gives each item's width and each face's total. It gives no X
position for anything, and neither does any other source, so what follows is an **order**
and a **budget**.

### 2.1 Front edge (Y = 0), left to right

| Order | Item | Ground | Width | Source for width |
|---|---|---|---|---|
| 1 | `J45`, recording NI Connector 1 | `NI_GND` | 66.0 | PE:65 |
| 2 | `J5`, reward remote and spare port | `DGND` | 14.4 | TOML `[bnc_dual_isolated].body_width_mm` |
| 3 | `J4`, reward button | `DGND` | 20.0 | PE:67 |
| 4 | `J46`, `J48`, `J50`: Intan digital | `INTAN_GND` | 122.4 for all seven, at 18.0 mm pitch | PE:56–59 |
| 5 | `J37`, `J39`, `J41`, `J43`: Intan analog out | `INTAN_GND` | (in the 122.4 above) | |
| 6 | `J36`, recording NI Connector 0 | `AGND` | 66.0 | PE:65 |
| 7 | two 60 mm intake fans, panel only | none | 130.0 | PE:68 |

Grounds are B-2(a)'s, from NET:250, 245, 224, 223.

**The order is forced, not chosen.** **[engineering judgment]** Four rules leave one order:

- the NI island borders `DGND` only, and not the Intan island (B-2(b), B-20), so `DGND`
  lies between them;
- the Intan island borders both `DGND` and `AGND` (B-2(b)), so it lies between those two;
- `AGND` must run unbroken from `J36` to the analog front ends at the back-right
  (K-16, K-18), and the CM5 and its digital region are at the back-left (SPEC:842), so
  `AGND` is the right-hand domain and `NI_GND` the left-hand one;
- no front connector sits behind the fan strip on the right (M-6).

Inside the Intan group, `J46` carries `BARCODE_INTAN` and goes at the end farthest from
`J37` to `J43` (K-4). That is the left end, beside `DGND`, which also keeps the barcode's
DGND side away from `J36` (K-4).

**Budget.** Board-mounted items total 66.0 + 14.4 + 20.0 + 122.4 + 66.0 = **288.8 mm** when
the seven Intan bodies are a group of their own. PE's own figure is 292.4 mm, with `J5`
on the same 18.0 mm pitch as the Intan row (PE:65–67). Both count `J4`'s 20.0 mm as
board-mounted, which follows its footprint; whether the button is on the board or on the
panel is still open (constraints Open question 14). The fan strip takes the right-hand
130 mm of the panel, which is board X ≥ 310 under the centring of §1. That leaves
310 − 288.8 = **21.2 mm** for one edge margin and four gaps, and three of those gaps are
domain boundaries that each need B-7's 2.5 mm copper-free band.

**The fan strip is 130 mm. Ruled by the user on 2026-11-20: "the fan strip is 130 mm
(2 × 65 mm fans)".** M-6 is amended to say so. SPEC:811–812 says the two-row front "frees
a 170 × 82 mm full-height strip": that was the space the two-row arrangement left over
(SPEC:798, "280 / 154 mm"), not what the fans need, and SPEC now carries a dated note
there. What the fans need is two cutouts of about 65 mm (SPEC:832–834; PE:68). PE replaced
the two rows with one, because a PCB-mounted dual body cannot be stacked (PE:88–92), and
one row does not leave 170 mm: on PE's accounting 450 − 170 − 292.4 is 12.4 mm short, and
on this document's 450 − 170 − 10 − 288.8 is 18.8 mm short.

### 2.2 Back edge (Y = 240), left to right

| Order | Item | Ground | Width | Source for width |
|---|---|---|---|---|
| 1 | `J13`, `J15`, `J17`: camera sync, with frame-time in on `J17B` | `DGND` | 68.4 for these three and `J6`, at 18.0 mm pitch | PE:56–59 |
| 2 | `J6`: reward driver out, frame-time in on `J6B` | `DGND` | (in the 68.4 above) | |
| 3 | `J3`, task-PC NI Connector 1 | `DGND` | 66.0 | PE:76 |
| 4 | M12 supply inlet, panel only, flying leads to `J1` | none | 20.0 | PE:79; M-13 |
| 5 | `J57`, task-PC line-out | `AO_GND_TPC` shells | 14.4 | TOML `.body_width_mm`; G-17 |
| 6 | `J2`, task-PC NI Connector 0 | `AGND` | 66.0 | PE:76 |
| 7 | `J19`, DB37 eye tracker | `AGND` | 69.4 | PE:78; M-14 |
| 8 | `J29`, `J26`, `J24`: rig analog | `AGND` | 86.4 for these three and the two below | PE:56–59 |
| 9 | `J22` (microphone, ambient light), `J20` (photodiodes) | `AGND` | (in the 86.4 above) | NET:238, 237 |

The order is K-5's: camera sync at the CM5 end, the five analog bodies at the far end,
`J6`, `J57` and the non-BNC connectors between. Three choices inside it are
**[engineering judgment]**:

- `J57` beside `J2`, because its two signals and its shell return all come from `J2`
  (G-17, K-22);
- the M12 at the `AGND`/`DGND` boundary, because `J1` is on that boundary (P-1);
- `J20` and `J22` at the right-hand end, because the photodiode stages and the microphone
  chain are the parts K-17 keeps farthest from the converters and the fan pair.

**Budget.** M-5 totals the board-mounted items at 132.0 + 176.4 + 69.4 = **377.8 mm**,
which leaves **52.2 mm** of the 430 mm edge free. That counts the ten BNC bodies as one
row at 18.0 mm pitch. In the order above they are three groups, 68.4 + 14.4 + 86.4, so
the items total 370.6 mm and **59.4 mm** is free. Either way the free length is in
pieces: it has to supply five gaps between groups, and the M12's 20 mm of panel where the
inlet sits over the board. No single free stretch approaches 56 mm, let alone 160. That
decides §6.

PE's 45 mm for "CM5 port cutouts" (PE:80) is panel width beside the connector row. §6 shows
the IO Board cannot be beside the row, so that 45 mm is not spent where PE spent it.

### 2.3 Not on a panel

| Item | Where | Source |
|---|---|---|
| `J1`, supply header, with `NT1`, `NT2`, `NT3` | on the `AGND`/`DGND` boundary, §3.3 | G-2, G-4, G-5, P-1 |
| `J7`, 40-pin header to the sync module | in `DGND`, just inboard of the sync module's carrier, §6.6 | M-17 |
| `J18`, internal USB | in `DGND` beside `J7`, with `U66` | M-17, K-30 |
| `J52` to `J55`, fan headers | in `DGND`; see Open question 6 | P-14, G-24 |
| `J56`, chassis earth stud | at the inlet star | G-5, M-15 |

---

## 3. Domain regions

### 3.1 The regions

| Region | Nominal extent (X × Y, mm) | Reaches | What sets its size |
|---|---|---|---|
| `NI_GND` island | 0–70 × 0–140 | front edge at `J45` | Width: `J45`'s 66.0 mm. Depth: `J45`'s body is 51.32 mm deep (X-4), and the barrier's seven straddlers line up behind it (§3.2). |
| `INTAN_GND` island | 112–238 × 0–75 | front edge at its seven bodies | Width: seven bodies at 18.0 mm pitch with the 15.4 mm courtyard, 123.4 mm (X-5). Depth: the 36.2 mm body (X-5) plus the supply chain of P-4. |
| `DGND` | 0–170 × 140–240, and 70–170 × 75–140, and 70–112 × 0–75 | back edge at `J13`, `J15`, `J17`, `J6`, `J3`; front edge at `J5`, `J4` | §3.3 |
| `AGND` | 170–430 × 75–240, and 238–430 × 0–75 | back edge at `J57`, `J2`, `J19` and the five analog bodies; front edge at `J36` | everything else |

Every extent is **[engineering judgment]**: the sources fix which edge each domain reaches
(B-2(a)) and nothing about its size. The depths 140 and 75 are allowances, not sums of
sourced dimensions, and placement will move them.

Rails follow the regions on L3 (S-7), and L2 and L3 are split in the same places (S-6).

### 3.2 The barriers

**B-1 asks for one straight line per barrier. Neither isolated island can have that.** An
island that reaches a board edge, and is not the whole width or depth of the board, has
two interior sides if it is in a corner and three if it is not. B-2 handles this by
counting straight segments, each facing one neighbour, with no straddling part at a corner.
The floorplan adds one rule to that: **every straddling part of a barrier is on one
segment**, so each barrier has one slotted line and its other sides are plain copper-free
bands.

| Barrier | Slotted segment | Unslotted segments | Faces |
|---|---|---|---|
| NI | X = 70, from behind `J45`'s body to the island's back: about Y = 55 to 140 | Y = 140, from X = 0 to 70 | `DGND` only, both (B-2(b)) |
| Intan, `DGND` part | Y = 75, X = 112 to 170 | X = 112, Y = 0 to 75 | `DGND` |
| Intan, `AGND` part | none: Y = 75, X = 170 to 238, crossed by the `INA105` traces (B-17) | X = 238, Y = 0 to 75 | `AGND` |

**NI barrier.** `U56` to `U61` and the DNP `U62` straddle it (B-3, B-22). Six SOIC-16
packages lie with their long axis along the line (B-10), 9.9 mm each from the footprint
name, so 59.4 mm of package before any gap. That does not fit across the island's back,
which is 70 mm wide with `U62` still to place, so the slotted segment is the island's
right-hand side. **[engineering judgment]** The widths of `U62`'s and the other converters'
footprints across the line are not in the sources; read them from the footprints at
placement. The slot is one straight run under all seven parts (B-2(c), B-5), and B-9's
keep-out carries on from its ends to the front edge and round the back to the left edge.

**Intan barrier.** Its `DGND`-facing and `AGND`-facing parts are **one straight line**,
Y = 75, because the `AGND`/`DGND` split meets it at a T at X = 170. This is the
arrangement B-2(b) names as the way to avoid a corner: "unless both regions meet it along
one line". Left of the T are the galvanic straddlers `U64`, `U65`, `U2`, `U63` and `C162`
(B-3, B-4, B-13), under one slot that narrows to 1.5 mm under `C162` (B-14, B-23). Right
of the T are the eight `INA105`s, wholly on the island, each with its two traces crossing
the band (B-17, B-18), and the eight muxes directly behind them in `AGND` (B-19). No part
sits at the T (B-2(c)). The island's two short sides carry no parts.

**[engineering judgment]** The eight `INA105`s have 68 mm of line, 8.5 mm each. The five
`DGND` straddlers have 58 mm, of which the two SOIC-16s take 19.8 mm; whether `U2` and
`U63` fit in the rest waits on their footprints. If they do not, the split can move right,
to X = 183 at most (§3.3).

**Order along the Intan line**, left to right: `U64`, `U65`, `C162` with `U63`, `U2`, the
T, then the `INA105`s for outputs 1 to 8. No straddler sits at the island's corner
(B-2(c)), so `U64`'s centre is at X ≥ about 119: from X = 112, half of B-7's 2.5 mm band,
B-8's 1 mm beyond it, and half of the SOIC-16's 9.9 mm. **[engineering judgment]** `U64` carries the
barcode and goes farthest from the `INA105`s (K-4), directly behind `J46`. `U2` and `U63`
are the board's switchers, and this puts them more than 190 mm from the photodiode and
microphone stages (K-17), but next to the T; see Open question 8.

**Islands do not touch.** The `DGND` tongue, X = 70 to 112, separates them for the whole
depth of the Intan island (B-20).

### 3.3 The AGND/DGND split, and the star

The split is one straight line, **X = 170**, from the back edge to the T at Y = 75. It is
a gap of at least 1.0 mm on L2 and L3 (G-9), not an isolation barrier, and has no slot.

**[engineering judgment]** Two things bound where it can be at the back edge:

- the `AGND` connectors need 14.4 + 66.0 + 69.4 + 86.4 = 236.2 mm of that edge, and three
  gaps, so the line is at X ≤ about 183;
- the IO Board is 160 mm long and must lie over `DGND` only (X-2), so the line is at
  X ≥ 160.

170 leaves 13 mm on the first count and 10 mm on the second.

**`J1` and `NT1` sit on that line at about Y = 150.** **[engineering judgment]** G-2 fixes
the star at `J1` and P-1 puts `J1` on the boundary. Mid-depth, not at the back edge,
because the crossings of G-11 are spread along the line: the 32 mux address lines cross at
the mux bank near Y = 80 to 110 (K-31), the comparator outputs and the I²C bus at the
comparators (K-25), and `+12V` to `U2` near Y = 75 (P-3). G-10 wants all of them in one
corridor near `NT1`, and the middle of the line is the one place within about 75 mm of
each. It costs about 90 mm of flying lead from the M12, which P-1 allows. The crossings
then lie along X = 170 between Y = 75 and 190, which is a long reading of G-10's "one
corridor". See Open question 5.

`NT2`, `NT3` and `J56` are at `J1` (G-4, G-5). The `+5V` entry capacitors go on the `DGND`
side of `NT1` and the ±12 V ones on the `AGND` side (G-14).

### 3.4 What goes where inside the regions

Placement is a later task. These are the anchors the regions were drawn round.

| Group | Where | Source |
|---|---|---|
| Analog front ends: `U22`, `U23` and the difference amplifiers | back-right, behind their own connectors | K-11, K-14, K-16 |
| Mux bank `U38` to `U52` | `AGND`, directly behind the Intan line, X = 170 to 238 | B-19 |
| `U67`, `U68` | `DGND` side of the split, beside the mux bank | K-31 |
| `U54`, `U76`, `U55` | `AGND` side of the split, Y = 160 to 190 | K-25, G-12, G-13; position is **[engineering judgment]**, see Open question 8 |
| NI and task-PC buffer banks | `AGND`, between the mux bank, `J36` and `J2` | K-18, K-19 |
| `U3`, `U4`, the two pi filters, `TP5` to `TP7` | Intan island, between the BNC bodies and the line | P-4, P-5, P-6, P-15 |
| `U1` | `DGND`, beside the `+5V` entry capacitors at `J1` | P-13 |
| `U8` to `U11`, `U70` to `U73` | `DGND`, between `J7` and the NI line | K-33 |
| `U5`, `U6`, `U7` and their clamps | `DGND`, at `J3` | G-23 |
| `U15`, `U74`, `J8` to `J12` | `DGND`, beside `J7` | K-6, K-8 |

---

## 4. The barcode corridor

Both readings of K-1 stay open (constraints Open question 2). The corridor below is drawn
so that either can be applied to it without moving anything.

**In plan.** The barcode family stays in the left 170 mm of the board, inside `DGND` or
inside the island it is delivered to:

| Net | From | To | Path |
|---|---|---|---|
| `BARCODE_RAW` | `J7` pin 11 | `U15`, `U74` | a few millimetres, beside `J7` (K-6) |
| `CAM_SYNC_*`, five nets | `U15`/`U74`, through `R198` to `R202` at the buffers (K-7) | `J13`, `J15`, `J17` | toward the back-left, under the IO Board's shadow |
| `BARCODE_BUF` | `U15` | `U60`, on the NI line at X = 70 | across `DGND` only (K-9), X = 75 to 110 |
| `BARCODE_INTAN_BUF` | `U15` | `U64`, at the left end of the Intan line | across `DGND` only (K-9), same strip, to about X = 120, Y = 78 |
| `BARCODE_NI` | `U60` | `J45` pin 3 | inside the NI island (K-4) |
| `BARCODE_INTAN`, `BARCODE_INTAN_BNC` | `U64`, through `R205` | `J46` pin 3 | down the island's left edge, clear of the `INA105`s and `INTAN_AO*` (K-4) |

The strip is X = 75 to 110, Y = 80 to 150, with a branch under the IO Board to the camera
bodies. Its nearest approach to `AGND` is the split at X = 170, 60 mm away, and to `J36`
about 130 mm. **[engineering judgment]** for the strip's position: it is the shortest
`DGND` path that touches both barriers' barcode optocouplers.

**Region reading (K-2).** Met: no barcode net enters, overlies or underlies `AGND`.

**Layer reading (K-3).** Also available: in this strip L3 is `+5V` or `+3V3` (S-7), so a
barcode net taken to L4 has a `DGND`-domain rail above it for its whole length. Whether
to put it on L4 is the designer's decision.

**Front edge (K-4).** The barcode reaches the front edge twice, at `J45` and `J46`, and
both are at the left: `J46` is 3 × 18.0 = 54 mm from `J37`, the nearest Intan analog
output, when the row is ordered as in §2.1.

**Back edge (K-5).** Camera sync is at the left end of the row. Between `J17` and the
first analog body lie `J6`, `J3`, the M12, `J57`, `J2` and `J19`, which is 66.0 + 14.4 +
66.0 + 69.4 mm of connector and more.

---

## 5. Airflow, heat sources and keep-outs

### 5.1 The path

Two 60 mm fans draw in at the front-right and two exhaust through the left side panel, so
air crosses the board diagonally (T-1; SPEC:836–838). Both pairs are panel cutouts of about
65 mm square (PE:145). The 20 mm between the board's left edge and the side panel is the
way out (X-8; SPEC:860–864).

- Intake: the front panel's right-hand 130 mm, board X ≥ 310 (§2.1).
- Exhaust: the left side panel. Where along its depth is **not in the sources**. SPEC puts
  the CM5 "back-left, at the exhaust" (SPEC:842), so the rear half is proposed,
  Y = 110 to 240. **[engineering judgment]**
- Nothing overhangs the left or right board edge (X-8).

The CM5 and its NVMe are the last thing the air meets (T-2). The analog front ends at the
back-right are off the diagonal (T-3; SPEC:844–847). See Open question 7 for what that
leaves undecided.

### 5.2 Heat sources

| Source | Dissipation | Where | Source |
|---|---|---|---|
| CM5 with active cooler and NVMe | 10–15 W | back-left, over `DGND`, §6 | SPEC:775–777; SPEC:249–250 |
| `U3`, `U4` | about 0.16 W and 0.07 W | Intan island | T-4 |
| `U63` | about 0.10 to 0.15 W: its input is 83 to 120 mA at 5 V for 63 to 91 mA out | Intan line, `DGND` part | SPEC:588–589; TOML `[tmr_1_0511].efficiency_pct`. The subtraction is arithmetic on those. |
| `U2` | **not in the sources**. It is a 2 W part, 82 % efficient at full load, running at 12 to 16 mA a rail. | Intan line, `DGND` part | TOML `[ih_series].power_w`, `.efficiency_pct`; N-5 |
| `U1` | **not in the sources**: the `+3V3` load was never budgeted | `DGND`, at `J1` | N-6; P-13 |

T-4 keeps the five on-board parts off the analog region. `U3` and `U4` are on the Intan
island and the other three are left of X = 170, so none is in `AGND`.

### 5.3 Keep-outs

| Keep-out | Extent | Source |
|---|---|---|
| Barrier slots and bands | §3.2 | X-1; B-7, B-8, B-9, B-14, B-23 |
| Behind each MDR68 | 51.32 mm deep, 63.86 mm wide | X-4 |
| Behind each dual BNC | 36.2 mm deep, 15.4 × 37.2 mm courtyard, 29.25 mm tall | X-5 |
| Sync-module carrier shadow | §6 | X-2, X-3 |
| Left and right edges | no overhang | X-8 |
| The diagonal | no tall part between the front-right intake and the left exhaust | X-9 |
| Fans and fan copper | none beside `U23`, `U22` or their nets | X-10, K-17 |
| Intake fan bodies | pending. If a fan is deeper than the front set-back, it overhangs the board's front-right (region `f` in §7) and limits component height there. The fan's depth and the set-back are both **not in the sources**. | M-9; **[engineering judgment]** that it needs a keep-out |

**Fans against the quiet channels.** The CM5's own cooler is at about X = 40 to 80 (§6.3)
and the photodiode and microphone bodies are right of about X = 330, so they are at least
250 mm apart. The chassis intake fans are on the front panel at the same X as those
bodies and most of a board depth away, about 200 mm. The exhaust fans are on the far side panel.
No fan header is in `AGND` (G-24).

---

## 6. Step 1b: the sync module's carrier

### 6.1 What Raspberry Pi publish

**CM5 IO Board**, from CM5IO, recorded at TOML `[cm5_io_board]`:

| Fact | Value | Where in CM5IO |
|---|---|---|
| Outline | **160 mm × 90 mm** | §1.3, printed p.4; §6.1 and Figure 2, printed p.10 |
| External connectors | all on one 160 mm edge | Figure 2, p.10 |
| Along that edge, from the corner beside HDMI0, to each connector's centre line | HDMI0 23.00, HDMI1 48.00, Ethernet 74.00, USB3 93.00, USB-C power in 108.00, microSD 121.25 mm | Figure 2, p.10. The figure prints the chain 3.50, 19.50, 25.00, 26.00, 19.00, 15.00, 13.25; the sums are made here. |
| Ethernet centre to USB-C centre | 34.00 mm | 19.00 + 15.00, same chain |
| M.2 M key socket | at the far end of the port edge from HDMI0; the card lies inside the outline, pointing away from the port edge, with posts for 2230, 2242, 2260 and 2280 | Figure 2, p.10; §4.1, p.7 |
| 40-pin header | on the HDMI half, between HAT mounting holes at 3.50 and 61.50 mm from that corner, 52.50 mm from the port edge | Figure 2, p.10, as read from its extension lines |
| Power | 5 V at 5 A, USB-C PD, on the port edge | §2.1, p.5 |
| The board's own mounting holes | four: 11.00 and 132.50 mm along the port edge from the HDMI corner, in rows **8.00** and 77.00 mm from the port edge. A 121.5 × 69 mm pattern. | Figure 2, p.10; the pattern is the differences |
| Any height, any connector width or overhang, PCB thickness, a standoff | **not in the document** | Figure 2 is a plan view |

**The IO Case**, from CASE, recorded at TOML `[cm5_io_case]`. Raspberry Pi's own enclosure
for this board is **170 mm × 94 mm × 28 mm** overall (p.3, "Form factor"; the p.6 drawing
prints the same 28). It holds the module on the IO Board, with "space for accessories
connected to the IO Board, such as an M.2 SSD or a PoE+ HAT+", and a fan built into the
lid (p.2). The board is held by "four M2.5 × 4 screws" through "the four mounting holes"
(p.4, step 3). This box does not use the case. Its 28 mm is used below as a **ceiling** on
the IO Board assembly's height: everything the case holds is inside it. It does not cover
a cooler on the module, which the brief does not mention.

Printed page numbers are the ones in the page footers. The PDF's own index is one higher.

**CM5 module**, from CM5 §4.1.1, printed p.16: 55 mm × 40 mm, 4.6 mm deep bare, and 4.94 mm
or 7.44 mm high once mounted, depending on the carrier's connector. Which one the IO Board
fits is not stated. The active cooler's height is in neither document.

**Raspberry Pi 5**, from PI5, recorded at TOML `[raspberry_pi_5]`: 85 mm × 56 mm; mounting holes
on a 58 × 49 mm pattern, 3.5 mm in, so the nearer pair is 23.5 mm from the short edge
(85 − 3.5 − 58); connectors
on one long edge at 11.2, 25.8 and 39.2 mm from a corner, and on the short edge at the
other end at 10.2, 29.1 and 47 mm, overhanging it by 3 mm. The sheet says its dimensions
are "approximate and for reference purposes only". It does not label the connectors. On
the board's known arrangement the short edge carries Ethernet and the USB ports and the
long edge carries the USB-C power input, so **the Pi 5's power input does not face the same
panel as its Ethernet and USB.** No height is dimensioned. The sheet is 1:1 at A4 with a
side view, and the review of this document scaled the USB and Ethernet stack from it at
about 16 mm above the PCB.

**PCIe-FPC NVMe adapter**: a third-party part nobody has chosen. Its dimensions are **not in
the sources**.

### 6.2 The condition

"It must sit over the **rear digital region**, never over the analog front ends or across
the airflow path serving them" (SPEC:909–912; X-2). Its ports "need the back panel"
(SPEC:842). It sits downstream of the analog section in the airflow (T-2).

### 6.3 Where it goes

**Over X = 0 to 160, Y = 150 to 240**, port edge at the back panel, M.2 end to the left.
**[engineering judgment]** for the orientation: the ports are all on one long edge, so
that edge faces the back; and of the two ways round, this one puts the M.2 socket and the
module at the exhaust end and the 40-pin header at the inboard end, nearest `J7`.

Turned that way, and with its left edge at X = 0:

| Feature | Board X | From |
|---|---|---|
| USB-C power in | 52 | 160 − 108.00 |
| USB3 | 67 | 160 − 93.00 |
| Ethernet | 86 | 160 − 74.00 |
| HDMI1, HDMI0, behind blank panel | 112, 137 | 160 − 48.00, 160 − 23.00 |
| 40-pin header | about 98 to 157 | 160 − 61.50 to 160 − 3.50 |
| CM5 module | about 40 to 80, scaled from Figure 2, which does not dimension it | |
| Mounting holes | 27.5 and 149, in rows 8.00 and 77.00 mm in from the port edge | 160 − 132.50, 160 − 11.00 |

Its Y depends on how far its connectors overhang its own edge, which CM5IO does not
dimension, and on the main board's set-back (§1).

### 6.4 Does it fit

**In plan, yes.**

| Check | Need | Have | Margin |
|---|---|---|---|
| Along the back edge, over `DGND` only | 160 mm | X = 0 to 170 | 10 mm |
| Depth, clear of the NI island | 90 mm | Y = 140 to 240, so 100 mm | 10 mm |
| Off the analog front ends | not over them | they are right of about X = 330 | 170 mm |
| Off `AGND` altogether | not over it | split at X = 170 | 10 mm |
| Off the airflow serving the analog section | not across it | it is at the outlet, the last thing in the flow | §5.1 |
| Overhang at the left edge | none (X-8) | left edge at X = 0 | 0 |

Its shadow is 160 × 90 = 14,400 mm², 14 % of the 103,200 mm² board. Both 10 mm margins
are this floorplan's own nominal boundaries, and each can be bought back: the split can
move to X = 183 (§3.3) and the NI island's depth is an allowance (§3.1).

**Beside the connector row, no.** The plan and PE both picture the carrier's ports in a
45 mm slice of back panel alongside the other connectors (PE:80). The ports are on a
160 mm board edge, and that whole edge has to be at the panel. The back edge has 52.2 to
59.4 mm free of board-mounted connectors, in pieces (§2.2). 160 mm is more than 100 mm
over either figure.

**Above the connector row, yes at the 5 mm standoff and no at the 26 mm one.** Under its
shadow at the back edge are `J13`, `J15`, `J17`, `J6` and `J3`. The tallest is the BNC
body, 29.25 mm above the board's top surface (X-5).

| | 5 mm standoff | 26 mm standoff | Source |
|---|---|---|---|
| Standoff under the main board | 5 | 26 | PE:116–119 |
| Main board | 1.6 | 1.6 | S-3, judgment |
| BNC body | 29.25 | 29.25 | TOML `[bnc_dual_isolated].body_height_mm` |
| **Top of the BNC bodies** | **35.85** | **56.85** | sum |
| IO Board assembly, at most | 28 | 28 | CASE p.3; TOML `[cm5_io_case].overall_height_mm` |
| **Top of the stack, at most** | **63.85, about 64** | **84.85, about 85** | sum |
| Usable height | about 82 | about 82 | PE:98; SPEC:861 |
| **Left over** | **about 18** | **none: about 3 over** | difference |

The 28 mm is a ceiling, not a measurement of this assembly. It is the outside of a case
that holds the board, the module, an SSD and a lid fan, so the bare assembly is less by
the case's sheet metal, which CASE does not dimension. The 18 mm left over has to cover
three things the table does not: the clearance between the BNC tops and the IO Board's
underside; whatever the module's cooler adds above the case's envelope; and air.
"About 82" is the panel's usable band, used once by SPEC as a height (SPEC:861), and is
not a floor-to-lid measurement.

**Nothing yet holds it up there.** The IO Board's own mounting holes are in rows 8.00 and
77.00 mm from its port edge (§6.1). With the port edge at the back panel:

- the row 77 mm in is at about Y = 163, inboard of every connector body. The deepest,
  `J3`, reaches 51.32 mm in from the board edge (X-4). Standoffs from the main board can
  reach those two holes. They are about 30 mm long or more, to clear the BNC bodies, and
  they land in `DGND` at about X = 27.5 and 149.
- the row 8 mm in is over the connector bodies. A BNC body is 36.2 mm deep (X-5), and
  the gap between two bodies at 18.0 mm pitch is 18.0 − 14.4 = 3.6 mm. An M2.5 standoff
  does not go down a 3.6 mm gap. **[engineering judgment]** on that last step: the screw
  size is CASE's (p.4), and no standoff's width is in the sources, but a hexagonal one
  for a 2.5 mm thread is wider than 3.6 mm.

So the port edge cannot stand on the main board.

### 6.5 The answer to Step 1b

**Ruled by the user on 2026-11-20: the compute board is the CM5, above the row.** The
sync module is a CM5 Lite on the official CM5 IO Board, with its NVMe on the board's own
M.2 socket. The IO Board sits above `J13`, `J15`, `J17`, `J6` and `J3`, over the rear
digital region only, at X = 0 to 160, Y = 150 to 240 nominal (§6.3). The main board is on
the 5 mm standoff. The IO Board's port edge is carried by a bracket.

The plan's reversal to a Pi 5 is not taken. PLAN's Task 14 Step 1b carries a note of the
same date.

**Mechanical requirement: a port-edge bracket.** **[engineering judgment]** The two
mounting holes 8 mm in from the IO Board's port edge are fixed to a bracket from the back
panel or the chassis, because nothing on the main board can reach them (§6.4). Two things
the bracket must do, both judgment:

- hold the IO Board's port edge to the back panel within the tolerance its cutouts need
  (M-2 asks ±0.1 mm of board-mounted connectors; the IO Board's ports are located by the
  bracket instead);
- not become a second connection between circuit ground and chassis. The board bonds
  `CHASSIS_GND` to `DGND` at `NT3` and nowhere else (G-3, M-16). Whether the IO Board's
  mounting holes are connected to its ground is **not in the sources**; if they are, the
  bracket or its fixings must be insulating.

Its design is Open question 2.

**What the ruling costs, and what it leaves.**

- **The BNC row is off-centre on both panels.** Centring the port pair on the panel's
  centreline needs a standoff of about 26 mm (PE:116–118). At 5 mm the pair sits about
  21 mm lower. The MDR68 row is lower still (M-8). The 26 mm standoff is not available:
  the stack would need about 85 mm (§6.4). Constraints Open question 17 is answered to
  that extent.
- **The IO Board's ports open above the BNC nuts**, in the upper part of the back panel.
  PE's 45 mm of row width for them (PE:80) is not spent in the row.
- **Four figures are still to be measured**, and the panels are not machined before they
  are (PLAN:912–913):
  1. the cooler's height on the module. SPEC:249–250 fits an active cooler, and the 28 mm
     ceiling does not include one;
  2. the chassis's floor-to-lid interior height. "About 82 mm" is the panel's usable band;
  3. the BNC's protrusion through the panel, which sets the main board's set-back (M-9)
     and so where the IO Board's port edge and its bracket land;
  4. the IO Board's real component heights, above and below its PCB, from Raspberry Pi's
     KiCad design files (CM5IO §6.1 points to them) or a board in hand. They replace the
     28 mm ceiling with the real stack.

**Alternatives considered, with their costs.**

| | **Chosen: CM5 IO Board above the row** | **Pi 5 with a PCIe-FPC NVMe adapter** | **IO Board set back from the panel, on leads** |
|---|---|---|---|
| Outline | 160 × 90 mm | 85 × 56 mm, plus the adapter (not in the sources) | 160 × 90 mm |
| Shadow | 14,400 mm², 14 % of the board | 4,760 mm², 4.6 %, before the adapter | 14,400 mm² |
| Beside the back row | no: 160 mm against 52.2 to 59.4 mm free | no: 56 mm, and no free stretch that long (§2.2) | not at the panel at all |
| So it sits | above `J13`, `J15`, `J17`, `J6`, `J3` | above about three of those bodies, or above `J3` alone (9.10 mm tall, TOML `[mdr68].body_height_mm`), which K-5 does not forbid | inboard of the connector bodies |
| Over `DGND` only | yes, 10 mm margins | yes, with more than 100 mm to spare along the back edge | **not as drawn.** Inboard of the bodies `DGND` is 240 − 140 − 36.2 = 63.8 mm deep behind the BNCs and 240 − 140 − 51.32 = 48.7 mm behind `J3`, against 90 mm. It needs the NI island or the barcode strip moved, or the board raised over the bodies' rear after all. |
| Height | at most about 64 of about 82 mm at the 5 mm standoff; does not fit at 26 mm | not dimensioned by Raspberry Pi; about 16 mm above its PCB, scaled (§6.1), plus cooler and adapter | low |
| Support | inboard holes on standoffs; port-edge holes on a bracket | its nearer holes are 23.5 mm from the port edge, over `J3`'s 51.32 mm body: a bracket too | ordinary standoffs |
| Back-panel cutouts | USB-C power, USB3, Ethernet; centres span 34.00 mm; above the BNC nuts | Ethernet and two USB stacks, centres spanning 36.8 mm (47 − 10.2). **Power is on the adjacent edge** and needs its own route. | three panel-mount connectors, anywhere |
| NVMe | on the board, inside the outline, no cable | on a flex cable, which SPEC:222–225 calls "a mechanical failure mode worth designing out" | on the board |
| Cables added | none | one flex, carrying the boot device | three leads, on the option chosen to have none |
| RAM upgrade | swap the module | new board (SPEC:272–276) | swap the module |
| Software | identical (SPEC:919–921) | identical | identical |
| Standoff | forces the short one; BNC row off-centre | either | either |

### 6.6 What follows from the ruling

- **X-3**, the component height limit under the shadow. The IO Board's underside is above
  the BNC bodies, so at least 29.25 mm is clear over the main board's surface there. The
  exact figure waits on measurement 4 above.
- **`J7` and `J18`** (M-17): in `DGND` just inboard of the shadow, about X = 100 to 155,
  Y = 130 to 148, so the ribbon drops from the IO Board's header over its inboard edge.
  **[engineering judgment]**
- **The two inboard standoffs** land in `DGND` at about (27.5, 163) and (149, 163). Their
  footprints on the main board are isolated holes (M-16), and the barcode's branch to the
  camera bodies (§4) goes round the first.
- **The back panel's cutouts** are drawn from the IO Board as bracketed, after the four
  measurements.
- **The left side panel's exhaust cutouts** should face the carrier (§5.1).

---

## 7. Plan view

**To scale: one column is 5 mm of X and one row is 10 mm of Y**, so a character cell is
5 × 10 mm and boundaries are drawn to the nearest cell. Each row is labelled with the Y of
its upper edge. The view is from above, back edge at the top. Connector labels above and
below the frame show order only, not position (§2).

```
   back, left to right:  J13 J15 J17 J6 | J3 | M12 || J57 | J2 | J19 | J29 J26 J24 J22 J20

Y mm    X = 0                             170                                                 430
        +--------------------------------------------------------------------------------------+  BACK EDGE
 240 -  |oooooooooooooooooooooooooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 230 -  |oooooooooooooooooooooooooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 220 -  |oooooooooooooooooooooooooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 210 -  |oooooooooooooooooooooooooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 200 -  |obbbbbbbbbbbbbbbbbbbbboooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 190 -  |obbbbbbbbbbbbbbbbbbbbboooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 180 -  |obbbbbbbbbbbbbbbbbbbbboooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 170 -  |obbbbbbbbbbbbbbbbbbbbboooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 160 -  |obbbbbbbbbbbbbbbbbbbbboooooooooo..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 150 -  |...............bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 140 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 130 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 120 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 110 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
 100 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
  90 -  |NNNNNNNNNNNNNN.bbbbbbb............aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
  80 -  |NNNNNNNNNNNNNN....................aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|
  70 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  60 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  50 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  40 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  30 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  20 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
  10 -  |NNNNNNNNNNNNNN........IIIIIIIIIIIIIIIIIIIIIIIIIIaaaaaaaaaaaaaaffffffffffffffffffffffff|
   0 -  +--------------------------------------------------------------------------------------+  FRONT EDGE

   front, left to right: J45 | J5 J4 | J46 J48 J50 J37 J39 J41 J43 | J36 | intake fans (panel)
```

| Mark | Meaning |
|---|---|
| `N` | `NI_GND` island |
| `I` | `INTAN_GND` island |
| `.` | `DGND` |
| `a` | `AGND` |
| `f` | `AGND` behind the intake fans; in the airstream, and no front connector here (M-6) |
| `o` | `DGND` under the CM5 IO Board's shadow (§6.3) |
| `b` | `DGND` carrying the barcode corridor (§4); under the shadow it is drawn in place of `o` |

The NI slot runs up the right-hand side of the `N` block from about Y = 55. The Intan slot
runs along the top of the `I` block from X = 112 to 170, and the `INA105`s along the rest
of that top edge. `J1` and `NT1` are on the `.`/`a` line at about Y = 150. The islands'
bands and the 1 mm split are narrower than a cell and are not drawn.

---

## Open questions for the designer

Numbered for this document. Where one continues a question in `constraints.md`, that
question is named.

**Settled since the first version, both by the user on 2026-11-20:** the compute board is
the CM5 IO Board above the back connector row (§6.5), and the fan strip is 130 mm (§2.1).

1. **The four measurements behind the CM5 ruling.** The stack is at most about 64 mm of
   about 82 mm, on a 28 mm ceiling borrowed from Raspberry Pi's IO Case (§6.4). Before
   panels are machined, measure or fetch: the cooler's height on the module; the
   chassis's floor-to-lid interior height; the BNC's protrusion through the panel; and
   the IO Board's real component heights, from Raspberry Pi's KiCad design files or a
   board in hand (§6.5). Who does each, and what happens if the cooler does not fit under
   the lid: a lower cooler, or the lid fan Raspberry Pi's own case uses?

2. **The port-edge bracket.** The IO Board's two mounting holes 8 mm from its port edge
   sit over the back connector bodies, and the 3.6 mm between BNC bodies will not take a
   standoff, so a bracket from the back panel or the chassis carries that edge (§6.5).
   What is it made of, what does it fix to, and how does it locate the ports against
   their cutouts? If the IO Board's mounting holes are tied to its ground, how is the
   bracket kept from bonding circuit ground to chassis a second time (G-3)? Is one hole
   at about X = 149 better taken to the main board, in the gap beside `J3`, by shifting
   the IO Board sideways? (Continues constraints Open question 10.)

3. **The IO Board's cutouts, and PE's 45 mm.** The three ports' centres span 34.00 mm
   (CM5IO Figure 2), and their widths are not in the datasheet. They now open above the
   BNC nuts. Is PE's 45 mm of row width returned to the back row, whose slack is 7.2 mm
   (PE:82), and how much panel is left between a BNC nut and a port cutout above it?

4. **The islands' straddler lines.** §3.2 puts the NI slot on the island's side, because
   six SOIC-16s and `U62` do not fit across a 70 mm back, and the Intan slot on the
   island's back, sharing one line with the `INA105`s. Both give each island unslotted
   sides as well. Is that the reading of B-1 and B-2 that was wanted? If `J4` is
   panel-mounted and wired (constraints Open question 14), its 20 mm could widen the NI
   island instead. (Continues constraints Open question 1.)

5. **`J1` mid-board.** §3.3 puts the star at about Y = 150 on the split, 90 mm of flying
   lead from the M12, so that every G-11 crossing is within about 75 mm of `NT1`. At the
   back edge the leads are short and the mux address lines cross 130 mm from the tie.
   Which? (Continues constraints Open question 16.)

6. **Fan headers.** `FAN_RTN` ties to `DGND` at the inlet and G-24 keeps the fan pair out
   of the `AGND` region. The intake fans are at the front-right, and the nearest `DGND`
   is at X ≤ 170, at least 140 mm away, so P-14's "two toward the front-right intake
   strip" cannot be met by a trace. Put all four headers in `DGND` and let the intake
   fans' own leads cross above `AGND`, or allow a routed pair along the front edge?

7. **The intake jet and the quiet corner.** SPEC calls the back-right the quietest corner
   because the flow is diagonal (SPEC:844–847). The intakes are at the front-right and
   blow along Y, straight at the back-right, before the air turns left. Is a deflector
   wanted, and where along the left side panel are the exhaust cutouts (§5.1)? How deep
   are the intake fans against the front set-back (§5.3)?

8. **Comparators against the heat sources.** K-25 and G-10 want `U54`, `U76` and `U55` at
   the split near `NT1`. K-27 wants `U54` and `U76` away from `U1`, `U2`, `U63` and the
   CM5. On this floorplan they are about 30 to 50 mm from `U1` and about 90 mm or more
   from `U2`, `U63` and the module. The sources give the margin (about 3 °C of
   self-heating in a 0 to 70 °C part, TOML `[lm339]`) and no distance. Enough?

9. **Board X against the panel.** §1 takes the board and the 450 mm of panel as
   concentric, which costs the front row 10 mm (§2.1). Is the panel's usable width
   centred on the chassis?

Still open from `constraints.md` and unchanged by this document: Open questions 2 and 3
(the barcode readings), 4 to 7 (slots and barriers), 8 (stack-up and mounting),
11 to 13 (BNC terminals, pads, nut and protrusion), 15, 18 to 22. Its Open question 17
(the standoff) is answered as far as §6.5 says.
