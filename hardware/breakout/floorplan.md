# Breakout board: floorplan

This is the floorplan for plan Task 14, Steps 1 and 1b. It says where the board's edges,
connectors, ground domains and barriers go, where the barcode runs, and whether the CM5 IO
Board fits. It is a document, not a board file: no `breakout.kicad_pcb` exists yet, nothing
here was measured on hardware, and placement is a later task.

It satisfies `constraints.md`, and cites that file by constraint ID.

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
| B-1, K-4, … | constraint IDs in `hardware/breakout/constraints.md` |

**The frame.** Plan view from above, components up. X runs left to right as seen by someone
facing the front panel, 0 to 430 mm. Y runs from the front edge (0) to the back edge (240).
**[engineering judgment]** The sources never name a viewpoint for "left" and "right". SPEC
puts the fans on the front face's right (SPEC:798, "fans right"), the exhaust on the left
side panel, the CM5 back-left and the analog section back-right (SPEC:828, 834, 836), and
those four statements agree with each other only in this frame. The back panel, seen from
behind, is mirrored.

---

## 1. Board outline

| Item | Value | Source |
|---|---|---|
| Outline | **430 × 240 mm, nominal** | SPEC:767–769; S-2 |
| Layers | four | S-1 |
| Chassis | 2U, 19 in rack | SPEC:767 |
| Chassis interior width | about 470 mm, so 20 mm clear on each side of the board | SPEC:852–853 |
| Panel width between rack ears | about 450 mm | PE:52 |
| Panel height | 88.9 mm, about 82 mm usable | PE:98; SPEC:824 |
| Panels | 3.0 mm aluminium, front and back | D3:3; PE:130 |
| Board set-back from each panel | 36.2 mm less the BNC's protrusion through the panel. The protrusion is **not in the sources**. | PE:125–128; TOML `[bnc_dual_isolated].body_depth_mm`; M-9 |
| Chassis interior depth | **not in the sources**. It is the board depth plus two set-backs plus two panel offsets, so it waits on the protrusion as well. | M-9 |
| Chassis interior height | **not in the sources**. SPEC uses 82 mm once as a height: 20 mm × 82 mm is its "1,640 mm² per side". | SPEC:853 |
| Standoff under the board | **not in the sources**. "Any standoff from about 5 mm up" keeps both BNC ports on the panel. | PE:116–120; M-7 |
| Board thickness | 1.6 mm | S-3, itself engineering judgment |
| Corner radius, mounting holes | **not in the sources** | M-16; constraints Open question 8 |

**Do not freeze the depth.** 240 mm is the spec's "approximately". Both the front and the
back edge carry BNC bodies, so the depth and the chassis are tied by twice the set-back
(M-9, S-2).

**Where the board sits against the panel.** The board is centred in the chassis
(20 mm each side, SPEC:852–853). Taking the 450 mm of panel as centred too, the panel
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
  (K-16, K-18), and the CM5 and its digital region are at the back-left (SPEC:834), so
  `AGND` is the right-hand domain and `NI_GND` the left-hand one;
- no front connector sits behind the fan strip on the right (M-6).

Inside the Intan group, `J46` carries `BARCODE_INTAN` and goes at the end farthest from
`J37` to `J43` (K-4). That is the left end, beside `DGND`, which also keeps the barcode's
DGND side away from `J36` (K-4).

**Budget.** Board-mounted items total 66.0 + 14.4 + 20.0 + 122.4 + 66.0 = **288.8 mm** when
the seven Intan bodies are a group of their own. PE's own figure is 292.4 mm, with `J5`
on the same 18.0 mm pitch as the Intan row (PE:65–67). The fan strip takes the right-hand
130 mm of the panel, which is board X ≥ 310 under the centring of §1. That leaves
310 − 288.8 = **21.2 mm** for one edge margin and four gaps, and three of those gaps are
domain boundaries that each need B-7's 2.5 mm copper-free band.

**SPEC's 170 mm strip does not fit this row.** M-6 reserves "a 170 × 82 mm strip" from
SPEC:811–812. That figure belongs to the two-row arrangement SPEC had then ("280 / 154 mm",
SPEC:798). PE replaced it with one row, because a PCB-mounted dual body cannot be stacked
in two rows (PE:88–92), and budgets the fans at 130 mm (PE:68). With 170 mm reserved the
board-mounted items would have 450 − 170 − 10 = 270 mm and need 288.8. This floorplan uses
PE's 130 mm. See Open question 6.

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

**Budget.** Board-mounted items total 132.0 + 176.4 + 69.4 = **377.8 mm** (M-5). The board
edge is 430 mm, so **52.2 mm of it is free of board-mounted connectors**, in total and not
in one piece. That number decides §6.

PE's 45 mm for "CM5 port cutouts" (PE:80) is panel width beside the connector row. §6 shows
the IO Board cannot be beside the row, so that 45 mm is not spent where PE spent it.

### 2.3 Not on a panel

| Item | Where | Source |
|---|---|---|
| `J1`, supply header, with `NT1`, `NT2`, `NT3` | on the `AGND`/`DGND` boundary, §3.3 | G-2, G-4, G-5, P-1 |
| `J7`, 40-pin header to the sync module | in `DGND`, just inboard of the sync module's carrier, §6.6 | M-17 |
| `J18`, internal USB | in `DGND` beside `J7`, with `U66` | M-17, K-30 |
| `J52` to `J55`, fan headers | in `DGND`; see Open question 7 | P-14, G-24 |
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
T, then the `INA105`s for outputs 1 to 8. **[engineering judgment]** `U64` carries the
barcode and goes farthest from the `INA105`s (K-4), directly behind `J46`. `U2` and `U63`
are the board's switchers, and this puts them more than 190 mm from the photodiode and
microphone stages (K-17), but next to the T; see Open question 9.

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
| `U54`, `U76`, `U55` | `AGND` side of the split, Y = 160 to 190 | K-25, G-12, G-13; position is **[engineering judgment]**, see Open question 9 |
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
| `BARCODE_INTAN_BUF` | `U15` | `U64`, at the left end of the Intan line | across `DGND` only (K-9), same strip, to about X = 115, Y = 78 |
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
air crosses the board diagonally (T-1; SPEC:828–830). Both pairs are panel cutouts of about
65 mm square (PE:145). The 20 mm between the board's left edge and the side panel is the
way out (X-8; SPEC:852–856).

- Intake: the front panel's right-hand 130 mm, board X ≥ 310 (§2.1).
- Exhaust: the left side panel. Where along its depth is **not in the sources**. SPEC puts
  the CM5 "back-left, at the exhaust" (SPEC:834), so the rear half is proposed,
  Y = 110 to 240. **[engineering judgment]**
- Nothing overhangs the left or right board edge (X-8).

The CM5 and its NVMe are the last thing the air meets (T-2). The analog front ends at the
back-right are off the diagonal (T-3; SPEC:836–839). See Open question 8 for what that
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
| Any height, any connector width or overhang, PCB thickness, a standoff | **not in the document** | Figure 2 is a plan view |

Printed page numbers are the ones in the page footers. The PDF's own index is one higher.

**CM5 module**, from CM5 §4.1.1, printed p.16: 55 mm × 40 mm, 4.6 mm deep bare, and 4.94 mm
or 7.44 mm high once mounted, depending on the carrier's connector. Which one the IO Board
fits is not stated. The active cooler's height is in neither document.

**Raspberry Pi 5**, from PI5, recorded at TOML `[raspberry_pi_5]`: 85 mm × 56 mm; connectors
on one long edge at 11.2, 25.8 and 39.2 mm from a corner, and on the short edge at the
other end at 10.2, 29.1 and 47 mm, overhanging it by 3 mm. The sheet says its dimensions
are "approximate and for reference purposes only". It does not label the connectors. On
the board's known arrangement the short edge carries Ethernet and the USB ports and the
long edge carries the USB-C power input, so **the Pi 5's power input does not face the same
panel as its Ethernet and USB.** No height is on the sheet.

**PCIe-FPC NVMe adapter**: a third-party part nobody has chosen. Its dimensions are **not in
the sources**.

### 6.2 The condition

"It must sit over the **rear digital region**, never over the analog front ends or across
the airflow path serving them" (SPEC:901–904; X-2). Its ports "need the back panel"
(SPEC:834). It sits downstream of the analog section in the airflow (T-2).

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
160 mm board edge, and that whole edge has to be at the panel. The back edge has
**52.2 mm** free of board-mounted connectors (§2.2). 160 > 52.2, by 107.8 mm.

**So it goes above the connector row, and whether it clears is not in the sources.** Under
its shadow at the back edge are `J13`, `J15`, `J17`, `J6` and `J3`. The tallest is the BNC
body, 29.25 mm above the board's top surface (X-5).

| | mm | Source |
|---|---|---|
| Standoff under the main board, least | 5 | PE:118–119 |
| Main board | 1.6 | S-3, judgment |
| BNC body | 29.25 | TOML `[bnc_dual_isolated].body_height_mm` |
| **Top of the BNC bodies, at least** | **35.85** | sum |
| Usable height | about 82 | PE:98; SPEC:853 |
| **Left for the IO Board, its underside, the module, the cooler and the air above** | **about 46** | 82 − 35.85 |

Four numbers would close this, and none is in the repository or in CM5IO: the IO Board's
height above its own PCB at its tallest connector; its underside protrusion and PCB
thickness; the cooler's height on the module; and the chassis interior height, of which
"about 82" is the panel's usable band and not a measurement.

**The standoff decides it as much as the IO Board does.** Constraints Open question 17
offers a standoff of about 26 mm to centre the BNC pairs on the panel (PE:116–118). With
that, the BNC bodies top out at 26 + 1.6 + 29.25 = 56.85 mm and about 25 mm is left. A
carrier above the row needs the short standoff.

**Verdict.** The CM5 IO Board **fits in plan** over the rear digital region and meets
X-2, with 10 mm margins. It **does not fit at the panel beside the connector row**
(160 mm against 52.2 mm). It can only ride above the back-edge connectors, and **that fit
in height is unproven**: at least 35.85 mm of about 82 mm is used below it, and the rest
of the stack is not in the sources.

### 6.5 The two options

The choice is the user's. It is Open question 1.

| | **A. CM5 Lite on the CM5 IO Board** | **B. Pi 5 with a PCIe-FPC NVMe adapter** |
|---|---|---|
| Outline | 160 × 90 mm | 85 × 56 mm, plus the adapter (not in the sources) |
| Shadow | 14,400 mm², 14 % of the board | 4,760 mm², 4.6 %, before the adapter |
| Edge that must reach the back panel | 160 mm | 56 mm, if Ethernet and USB are to reach it |
| Against 52.2 mm of free back edge | over by 107.8 mm | over by 3.8 mm, and the 52.2 mm is not in one piece |
| So it sits | above `J13`, `J15`, `J17`, `J6` and `J3` | above about three of those bodies |
| Height it must clear | 29.25 mm BNC bodies | the same, unless it is moved over `J3` alone (9.10 mm, TOML `[mdr68].body_height_mm`), which K-5 does not forbid |
| Its own height | not in the sources | not in the sources |
| Back-panel cutouts | USB-C power, USB3, Ethernet, centres spanning 34.00 mm | Ethernet and two USB stacks, centres spanning 36.8 mm (47 − 10.2). **Power is on the adjacent edge** and needs its own arrangement. |
| NVMe | on the board, inside the outline, no cable | on a flex cable, which SPEC:222–225 calls "a mechanical failure mode worth designing out" |
| Over `DGND` only | yes, 10 mm margins | yes, with more than 100 mm to spare along the back edge |
| RAM upgrade | swap the module | new board (SPEC:272–276) |
| Software | identical (SPEC:911–913) | identical |

Neither option sits beside the connector row, so both ride above it, and the unproven
height stack is common to both. What separates them is how many connector bodies each
covers, the flex cable, and where the power cutout goes.

**[engineering judgment]** A variant of A the sources do not mention: mount the IO Board
set back from the panel, inboard of the connector bodies, and bring Ethernet, USB and
power to the panel on short panel-mount leads. It removes the height stack and the
set-back coupling. It adds three cables to the option that was chosen to have none.

### 6.6 What waits on the choice

- X-3, the component height limit under the shadow: the carrier's standoff is unknown, so
  nothing under X = 0 to 160, Y = 150 to 240 is assumed usable for a tall part.
- `J7` and `J18` (M-17). Proposed for option A: in `DGND` just inboard of the shadow,
  about X = 100 to 155, Y = 130 to 148, so the ribbon drops from the IO Board's header
  over its inboard edge. **[engineering judgment]**
- The back panel's cutouts, and whether the 45 mm PE allowed them is returned to the row.
- The left side panel's exhaust cutouts, which should face the carrier (§5.1).

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
| `o` | `DGND` under the CM5 IO Board's shadow, option A (§6.3) |
| `b` | `DGND` carrying the barcode corridor (§4); under the shadow it is drawn in place of `o` |

The NI slot runs up the right-hand side of the `N` block from about Y = 55. The Intan slot
runs along the top of the `I` block from X = 112 to 170, and the `INA105`s along the rest
of that top edge. `J1` and `NT1` are on the `.`/`a` line at about Y = 150. The islands'
bands and the 1 mm split are narrower than a cell and are not drawn.

---

## Open questions for the designer

Numbered for this document. Where one continues a question in `constraints.md`, that
question is named.

1. **CM5 IO Board, or Pi 5?** The IO Board fits in plan over the rear digital region with
   10 mm margins, and meets X-2. It does not fit at the back panel beside the connector
   row, 160 mm against 52.2 mm free, so it has to ride above `J13`, `J15`, `J17`, `J6`
   and `J3`, and that height stack is unproven: at least 35.85 mm of about 82 mm is used
   below it and the IO Board's own height is not in Raspberry Pi's datasheet (§6.4). A
   Pi 5 has the same problem on a 56 mm edge and adds a flex cable and a power input that
   faces sideways (§6.5). Which is it, A or B? The plan says the answer precedes panel
   machining (PLAN:912–913). Four measurements would turn "unproven" into yes or no: the
   IO Board's tallest connector, its underside, the cooler's height, and the chassis
   interior height. (Continues constraints Open question 10.)

2. **Is a carrier above the connector row acceptable at all?** Nobody decided it; the
   arithmetic of §6.4 forces it for both options. It ties the standoff to the short end
   of its range (§6.4; constraints Open question 17), puts the carrier's port cutouts
   above the BNC nuts, and covers the camera-sync and `J3` bodies. The alternative is the
   set-back variant of §6.5, with three panel leads.

3. **PE's 45 mm for the CM5 cutouts.** The three ports' centres span 34.00 mm (CM5IO
   Figure 2), which leaves 11 mm for two half-widths and clearance, and the connector
   widths are not in the datasheet. If the carrier rides above the row, is that 45 mm
   returned to the back row, whose slack is 7.2 mm (PE:82)?

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

6. **130 mm or 170 mm for the fan strip.** SPEC:811–812 reserves 170 mm and M-6 repeats
   it. PE budgets 130 mm, and the single connector row needs it: with 170 mm the front
   row is 18.8 mm too long (§2.1). Confirm 130 mm, and that two 65 mm cutouts need no
   more.

7. **Fan headers.** `FAN_RTN` ties to `DGND` at the inlet and G-24 keeps the fan pair out
   of the `AGND` region. The intake fans are at the front-right, and the nearest `DGND`
   is at X ≤ 170, at least 140 mm away, so P-14's "two toward the front-right intake
   strip" cannot be met by a trace. Put all four headers in `DGND` and let the intake
   fans' own leads cross above `AGND`, or allow a routed pair along the front edge?

8. **The intake jet and the quiet corner.** SPEC calls the back-right the quietest corner
   because the flow is diagonal (SPEC:836–839). The intakes are at the front-right and
   blow along Y, straight at the back-right, before the air turns left. Is a deflector
   wanted, and where along the left side panel are the exhaust cutouts (§5.1)?

9. **Comparators against the heat sources.** K-25 and G-10 want `U54`, `U76` and `U55` at
   the split near `NT1`. K-27 wants `U54` and `U76` away from `U1`, `U2`, `U63` and the
   CM5. On this floorplan they are about 30 to 50 mm from `U1` and about 90 mm or more
   from `U2`, `U63` and the module. The sources give the margin (about 3 °C of
   self-heating in a 0 to 70 °C part, TOML `[lm339]`) and no distance. Enough?

10. **Board X against the panel.** §1 takes the board and the 450 mm of panel as
    concentric, which costs the front row 10 mm (§2.1). Is the panel's usable width
    centred on the chassis?

Still open from `constraints.md` and unchanged by this document: Open questions 2 and 3
(the barcode readings), 4 to 7 (slots and barriers), 8 (stack-up and mounting),
11 to 13 (BNC terminals, pads, nut and protrusion), 15, 18 to 22.
