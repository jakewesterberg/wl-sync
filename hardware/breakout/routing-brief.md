# Breakout board: routing brief

Written 2026-11-21 for the person who routes `breakout.kicad_pcb` in pcbnew. The board is
placed and unrouted. This is plan Task 14 from Step 3's note onward
(`docs/superpowers/plans/2026-08-13-breakout-pcb.md`).

**This brief changes no rule.** Every rule is in `constraints.md`, cited here by its ID
(G-1, B-7, K-11 and so on). Where the two disagree, `constraints.md` is right and this
file is stale. Positions are in `placement.md`, regions in `floorplan.md`, panel matters
in `panel-elevations.md`, rulings in `parametric-audit.md`.

Coordinates are the board file's: x to the right, y down, the back edge at y = 0 and the
front edge at y = 240 (`placement.md` §1). Layers: L1 is F.Cu, L2 In1.Cu, L3 In2.Cu,
L4 B.Cu.

Nothing on this board has been built or measured.

## 1. Starting point

**Open** `hardware/breakout/breakout.kicad_pro` in KiCad 10.0.5 and then the board. The
project file carries the net classes and `breakout.kicad_dru` the custom rules; pcbnew
loads both with the board.

**Read before the first track:** `placement.md` (all of it, 390 lines), then
`constraints.md` §2 to §6. `floorplan.md` §7 is a plan view of the regions.

**What the board file already has:** the 430 × 240 mm outline, four copper layers, the two
isolation slots on Edge.Cuts, 569 footprints (567 from the netlist, and the IO Board's
two standoff holes `H1` and `H2`), every pad on its net, nine named keep-out rule areas,
and the IO Board's outline on User.Comments. **It has no track, no via and no pour.**

**How it was made**, in case it must be remade before routing starts:

```bash
kicad-cli sch export netlist --format kicadsexpr \
    -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
python3 hardware/gen/gen_breakout_pcb.py
kicad-cli pcb upgrade hardware/breakout/breakout.kicad_pcb
python3 hardware/gen/check_breakout_placement.py
```

`hardware/README.md`, "Regenerating fab outputs", has the full sequence with the netlist
checkers. `breakout.net` is not committed.

**Once the board has a track, never run `gen_breakout_pcb.py` again.** It writes the file
whole. It refuses to overwrite a board with routed items unless given `--force`, and
`--force` discards the routing. To move a part before routing, move it in the generator
(`ANCHORS`, `placement.md` §7) and regenerate. To move a part after routing has begun,
move it in pcbnew and say so in `placement.md`.

**What must not move.**

- **The 23 panel connectors.** They are at their panel positions (`placement.md` §2) and
  the panel will be drawn from them (M-2). `check_breakout_placement.py` fails if one
  moves.
- **The twelve parts that straddle a barrier**, and the slots under them: `U56` to `U61`
  and `U62` on the NI line, `U64`, `U65`, `U63`, `C162` and `U2` on the Intan line. Their
  pad-to-slot margins are 0.50 to 0.55 mm (`placement.md` §3.3).
- **`NT1`**, on the `AGND`/`DGND` split at (170, 94.5), and `J1` beside it.
- **The slots, the rule areas and the outline.**
- No part may cross into another domain's region; the check fails on it.

Everything else may be nudged to make a route work. `placement.md` §7 says which 413
parts were placed by rule and are proposals.

**The checks, before and after any session:**

```bash
python3 hardware/gen/check_breakout_placement.py
kicad-cli pcb drc --severity-all -o hardware/breakout/drc.rpt hardware/breakout/breakout.kicad_pcb
```

**What DRC shows today, before routing** (run 2026-11-21, KiCad 10.0.5; `drc.rpt`):

| Class | Count | |
|---|---|---|
| `unconnected_items` | 499 | the board is unrouted; routing takes this to 0 |
| `silk_edge_clearance` | 24 | two on each of the twelve straddlers, their own outlines over their slots. B-21 accepts these and they stay. |
| every other class | 0 | and must stay 0 |

The placement check prints `PASS` and `routed items (tracks, vias): 0`.

## 2. Stack-up and net classes

**Stack-up** (S-1, S-3, S-4): four layers, signal / ground / power / signal; 1.6 mm;
35 µm copper on all four; every part on L1. The two inner layers are typed as planes.
**The board file has no stack-up block**: the dielectric thicknesses are not in the
sources (`constraints.md` Open question 8), so no impedance figure can be computed, and
none is asked for except N-9's pair.

**Classes.** Membership is in `breakout.kicad_pro` (`net_settings`), one class per net,
written by the generator from `constraints.md` §4. The widths are enforced as minimums by
`breakout.kicad_dru`, because KiCad does not enforce a class's width (V-2).

| Class | Rule | Min width | Nets |
|---|---|---|---|
| `INLET` | N-1 | 1.0 mm | `P12_RAW`, `P12_FUSED`, `P5_RAW`, `P5_FUSED` |
| `INLET_N12` | N-2 | 0.8 mm | `N12_RAW`, `N12_FUSED` |
| `FAN` | N-3 | 0.8 mm | `FAN_12V_RAW`, `FAN_12V`, `FAN_RTN` |
| `NI_PWR` | N-4 | 0.6 mm | `NI_5V`, `NI_GND` |
| `ISO_PWR` | N-5 | 0.5 mm | `ISO_P15_RAW`, `ISO_N15_RAW`, `ISO_P15_FILT`, `ISO_N15_FILT`, `ISO_P12`, `ISO_N12`, `ISO_5V`, `ISO_5V_RTN`, `U63_VIN` |
| `RAIL_BRANCH` | N-6 | 0.4 mm | `+12V`, `-12V`, `+5V`, `+3V3` |
| `ANALOG` | N-7 | 0.25 mm | 165 nets: every `A_*`, `MUX_OUT1` to `8`, `INTAN_AO*`, the `*_THR` nets, `AO_L_*`, `AO_R_*`. The list is in the project file. |
| `USB` | N-9 | 0.2 mm | `USB_DP_PI`, `USB_DM_PI` |
| `Default` | N-8, N-11, N-14 | 0.2 mm | everything else |

Every class has 0.2 mm clearance and a 0.6 mm via on a 0.3 mm drill (N-11, N-14).

**Rules in `breakout.kicad_dru` beyond the widths:**

| Rule | What it enforces |
|---|---|
| N-12 | 0.6 mm on outer layers between the raw or filtered +15 V nets and the −15 V ones |
| N-13 | 0.6 mm on outer layers from each of the 129 nets that reach a panel connector to any other net. One exemption: between `U11`'s own pads. |
| S-5 | 0.15 mm track and gap allowed only inside the courtyards, and between the own pads, of `U55`, `U3` and `U4` |

**Three widths DRC does not enforce.** The class system holds one class per net, and
these rules are about part of a net:

- N-1: **`+5V` from `D3` to its plane is 1.0 mm.** `+5V` is in `RAIL_BRANCH`, so DRC
  accepts 0.4 mm there.
- N-2: **`-12V` from `D2` to its plane is 0.8 mm**, for the same reason. By N-1's
  reasoning `+12V` from `D1` to its plane is copper behind a 0.5 A fuse too; N-1 does not
  name it, so its width is yours.
- N-4: 0.6 mm from *each* `J45` pin, using both `NI_5V` pins (8 and 14) symmetrically and
  all twelve `NI_GND` pins.

Two more that only a person can hold: N-10 (`+5V` copper drops no more than 25 mV from
`D3` to the farthest optocoupler LED resistor, about 83 squares) and G-8 (nothing
narrower than `NT1`'s own 2 mm bridge joins it, and both sides carry 1 A).

## 3. The order to route in

Derived from `constraints.md` and the plan's Task 14. The reasoning is that a pour or a
keep-out drawn late moves tracks, and that the nets with the least freedom should choose
their paths first. **[engineering judgment]** for the order itself; no source gives one.

1. **Confirm the fences.** Run both checks. Look at the nine rule areas and the two
   slots in pcbnew so that you know where copper cannot go (`placement.md` §3.4).
2. **Draw the planes: L2, then L3** (§5 below; S-6, S-7, G-9). Everything else is routed
   against them, and K-2, K-3, K-18 and N-9 are all statements about which plane lies
   under a track. Fill, run DRC, and confirm each plane is one piece.
3. **The star and the inlet** (G-1 to G-8, G-14, G-15, P-1, P-2, N-1 to N-3). It is one
   small area at (170, 80 to 95) where two ground domains, four ties and the fuse chains
   meet, and the two forced split crossings `P5_RAW` and `FAN_12V_RAW` pass through it.
4. **The parts on the barriers** (B-12, B-13, P-3, P-7 to P-10, K-34, K-35, G-6). Each
   straddler's pads leave from their outer ends, away from the slot. `U63`'s input loop,
   `C162` and `NT4` are tight and fixed in shape.
5. **The Intan supply chain** (P-4 to P-6) and the `ISO_PWR` nets, N-12 between the two
   raw rails.
6. **The barcode family** (K-1 to K-10), inside its strip in `DGND` and its two islands.
   Route it before the rest of the digital nets so that it takes the short path.
7. **The split crossings** (G-10, G-11): 46 nets, in the corridor on x = 170, then the
   16 mux and `AGND`-sense traces into the Intan `INA105`s (B-17, B-18).
8. **The analog front ends** (K-11 to K-23), starting with the two photodiode summing
   junctions and their guard rings, then each BNC's centre-and-shield pair.
9. **The analog distribution** (K-18 to K-22, G-16, G-17): the sixteen `A_*` nets to the
   mux bank and both NI connectors, AISENSE, and the audio pair with its own return.
10. **Comparators, thresholds, I²C and USB** (K-24 to K-31, N-9).
11. **The remaining digital nets** (K-32 to K-37, G-23), and the escapes from `J3` and
    `J45`.
12. **Rail branches** (N-6) and the fan pair (G-24, N-3, P-14).
13. **Silkscreen** (M-20) and the checks of §7.

## 4. The rules, by subject

Each line names the rule and what it means for a track. Read the rule itself before
relying on the line.

### 4.1 Barriers, slots and creepage

- **Nothing crosses a barrier line on any layer: no track, no via, no pour (B-6).** The
  only things that span the NI barrier (x = 70, and y = 100 from x = 0 to 70) or the
  `DGND`/Intan barrier (y = 165 from x = 112 to 170, and x = 112 from y = 165 to the
  front edge) are the twelve straddling parts. The island's right-hand side, x = 238,
  faces `AGND` and has the same band (`intan_band_right`): nothing crosses it either.
- **The slots are real cutouts** (B-5): 2.0 mm under the eight SOIC-16 optocouplers
  (B-11, R6), 1.5 mm under `C162` for 3.4 mm (B-14, B-23, R5), 2.5 mm under `U62`, `U63`
  and `U2` (B-3, B-4).
- **Keep-outs are drawn as rule areas** and DRC enforces them: each slot plus 1 mm
  (B-8), and a 2.5 mm copper-free band along the whole of each barrier line to the board
  edge (B-7, B-9). A pad may sit inside a slot's keep-out; a track may not, so **every
  straddler pad is left from its outer end** (B-13).
- **The `AGND`/Intan boundary is not galvanic and has no slot** (B-17). Its band,
  `intan_band_agnd` on y = 165 from x = 170 to 239.25, blocks vias and pours and allows
  tracks: exactly sixteen cross it, `MUX_OUTn` and an `AGND` sense trace for each of the
  eight `INA105`s, side by side and equal in length (B-18). Nothing else crosses.
- **No Intan copper under the primary pins of `U2` and `U63`, and no `DGND` copper under
  their secondary pins** (B-16).
- **Creepage.** No standard's figure applies: the sources give no working voltage across
  either barrier (`constraints.md` §3, Open question 7). The slot margins are fabrication
  margins. Do not narrow one.
- `U62` and its four companions are DNP and are routed as if fitted (B-22).

### 4.2 Ground domains and their single bonds

- **`AGND` and `DGND` meet at `NT1` and nowhere else** (G-1): no stitching via between
  the two pours, no shared pad, no overlap. The gap between them on L2 and L3 is 1.0 mm,
  on x = 170 from the back edge to y = 165 (G-9; rule area `agnd_dgnd_gap`).
- **Three more ties, each the only join of its net** (G-3 to G-7): `NT2` for `FAN_RTN`,
  `NT3` for `CHASSIS_GND`, both to `DGND` at the star; `NT4` for `ISO_5V_RTN` to
  `INTAN_GND` at `U63`. Route each of those three nets as a trace or island. **Never
  flood one into the plane it ties to**, and keep the four net-tie footprints as drawn.
- `J56`, the earth stud, reaches `NT3` by its own trace (G-5).
- `J1` pins 4 and 5 are both `AGND`, brought into the star with full-width copper (G-15).
- **Nets that are grounds in name only and must stay out of every pour:**
  `AO_GND_TPC` (trace only, G-17); `ACCESIO_AGND` (to `AGND` only through `R27`, G-19);
  `ACCESIO_SHELL` (to `AGND` only through `R224`, G-20); the ten `*_SHLD` nets (§4.5).
- On `J2` and `J36` only pin 62 meets a board ground; every other ground pin of those
  two connectors gets no copper (G-16). AISENSE goes to the plane at the buffer bank by
  its own trace (K-20).
- The fourteen Intan BNC shells land on the `INTAN_GND` plane directly (G-21). The shells
  of `J5`, `J6`, `J13`, `J15` and `J17` are `DGND` (G-22).
- Fan current never returns through `AGND`, and the fan pair is not routed across the
  `AGND` region (G-24). `placement.md` §4 notes that the pair to `J54` and `J55` is about
  160 mm long and crosses the barcode strip; `floorplan.md` Open question 6 is open.

### 4.3 Crossing the `AGND`/`DGND` split

- **Only the nets of G-11 cross, once each, in one corridor near `NT1`** (G-10). The
  placement check derives the crossing nets from the netlist and declares 54. Eight of
  them, `MUX_OUT1` to `8`, cross from `AGND` into the Intan island (§4.1). The other 46
  cross this split: the 32 `MUXn_A*` lines, `I2C_SCL`, `I2C_SDA`, `PD1_COMP`,
  `PD2_COMP`, `ACC_TRIG`, `A_MISC1_COMP`, `CAM_FRAME_EYE`, `CAM_FRAME_BEH`, their two
  `_CLAMP` nets, `CAM_FRAME_THR`, `PD1_COMP_PFI_BUF`, `P5_RAW` and `FAN_12V_RAW`.
  `+12V` and `+3V3` cross too, as rails (P-3, G-12).
- **No rule area can express G-10**, so nothing stops a track crossing x = 170 on L1 or
  L4 somewhere else. This is yours to hold. `floorplan.md` §3.3 expects the crossings
  between y = 50 and 165 on x = 170.
- A signal that crosses returns through `NT1`: keep each crossing short and near the tie.

### 4.4 The barcode corridor

- The family: `BARCODE_RAW`, `BARCODE_BUF`, `BARCODE_INTAN_BUF`, `BARCODE_PI`,
  `BARCODE_HB`, `BARCODE_NI`, `BARCODE_INTAN`, `BARCODE_INTAN_BNC` and the five
  `CAM_SYNC_*` nets (`constraints.md` §5.1).
- **K-1 has two readings and the sources do not choose** (Open question 2). **K-2, the
  region reading:** no barcode net enters `AGND`, or runs over or under it, on any layer.
  **K-3, the layer reading:** barcode on L4 under a `DGND`-domain rail, analog on L1 over
  `AGND`. `floorplan.md` §4 draws a strip, x = 75 to 110 and floorplan Y = 80 to 150
  (board y = 90 to 160), in which both hold. Meet K-2 always. K-3 costs a via at every
  barcode pin; whether to take it is the designer's decision, and is not made.
- **Vias on barcode nets are allowed.** No rule forbids them.
- K-4: `BARCODE_NI` and `BARCODE_INTAN`/`BARCODE_INTAN_BNC` take the shortest path inside
  their own islands, the Intan one down the island's left edge, clear of `INTAN_AO*` and
  the `INA105`s. K-9: the two `_BUF` nets reach `U60` and `U64` across `DGND` only.
- K-6 to K-8, K-10: `BARCODE_RAW` short; the five 47 Ω at the buffers; the five headers
  on short stubs; the heartbeat LED on its own buffer leg and no other.
- No rule area expresses K-2 either. V-4's render is the check.

### 4.5 Panel-facing nets and the shield resistors

- **N-13:** 0.6 mm on outer layers from any of the 129 panel-connector nets to any other
  net. DRC enforces it. It makes the escape from each MDR68 and each BNC wider than the
  default; plan the fan-out for it.
- **K-14, K-28, K-36:** each input's series resistor and clamp sit between the connector
  and the silicon, at the connector. Do not let a track reach the IC round them.
- **G-18:** each of the ten rig-facing shields (`A_PD1_SHLD` and the rest, on `J20` to
  `J29`) is its own net and reaches `AGND` only through its own 10 Ω. Do not merge two,
  and do not let the `AGND` pour touch a shell pad. **K-15:** route each centre and its
  shield as an adjacent pair from the BNC to the receiver, with the 10 Ω at the BNC end.
- **K-12:** `U22` pins 3 and 5 are fed from the shield pads, and each photodiode summing
  junction is ringed by a guard trace on that shield net.
- **Which pad is which port (R11, 2026-11-21).** On every dual BNC, pads 1 and 2 are the
  upper port's centre and shell, pads 3 and 4 the lower's. Pads 1 and 3 are centres; that
  much is from Amphenol's labels. That pad 2, not pad 4, is the upper port's shell is
  read from how 31-6575's rear view is drawn. It matters only on `J20` to `J29`, where
  the two shells are different nets. GT-4 no longer holds routing; a continuity check on
  the first box remains (`../assembly-checklist.md`).
- **X-6:** the two large unnumbered pads of each BNC are post-mount holes with no net.
  Keep every net and every pour clear of them.
- **X-7:** the MDR68 boardlock holes are non-plated. Keep copper on all layers clear.

### 4.6 Planes and pours

**None is drawn. The board has rule areas only.** To draw:

| Layer | Pours | Rule |
|---|---|---|
| L2 | `NI_GND`, `INTAN_GND`, `DGND`, `AGND`, one per region | S-6 |
| L3 | `NI_5V` in the NI region; `ISO_P12`, `ISO_N12`, `ISO_5V` in the Intan region; `+5V`, `+3V3` in the `DGND` region; `+12V`, `-12V` in the `AGND` region. How each region is divided between its rails is left to you. | S-7 |
| L1 | an `INTAN_GND` area under each of `U3` and `U4`, joined to pad 9 and stitched to L2 by vias under the pad | P-6 |
| L1 | a `+3V3` area for `U1`'s tab (pin 2), clear of `DGND` | P-12 |

- **The L3 splits are in the same places as the L2 ones** (S-6), so no domain's plane lies
  over another's.
- The regions' rectangles are `REGIONS` in `hardware/gen/check_breakout_placement.py` and
  `floorplan.md` §3.1. The rule areas hold each pour back from the barrier lines and the
  split, so a pour drawn to the region's outline fills correctly.
- **K-3, if taken:** over the barcode strip L3 must be `+5V` or `+3V3`.
- No outer-layer ground flood is asked for anywhere. If you add one, G-1, G-3, G-16 to
  G-20, X-6 and B-16 all bear on it.
- No mounting hole other than `J56` is on the board, and none is in the sources (M-16,
  Open question 8). If holes are added they are isolated from every plane.

### 4.7 Supplies, thermal and the PowerPAD

- **P-2:** the fuse and diode chains in netlist order at `J1`; nothing else joins a
  `*_RAW` or `*_FUSED` net.
- **P-4:** each Intan pi filter in line, no branch before the LDO.
- **P-5:** `U3` and `U4` pin 7 gets no copper at all, not a stub and not a pour contact.
  FB and NR/SS parts on short traces at the pins.
- **P-6:** pad 9 of `U3` and `U4` is `INTAN_GND`, soldered to an L1 area and tied to L2 by
  vias under the pad. No other net's copper passes under either part. The via count and
  size are not in the sources.
- **P-7 to P-10:** `U63`'s input filter. `U63_VIN` is one short L1 trace: no via, no pour,
  no test pad, no contact with the `+5V` plane. `C161`'s `DGND` pad returns to `U63` pin 1
  on L1 before either reaches the plane. `U63`'s `+5V` feed is its own branch from the
  plane entry, separate from the LED resistors' branch.
- **P-12:** `U1`'s tab is `+3V3`, not ground.
- Thermal relief on plane connections is not specified by any rule. G-15 asks for
  full-width copper at `J1` pins 4 and 5, so no relief there.
- T-4, K-17, K-27, X-9, X-10 are placement rules and are met as placed. Do not route the
  fan pair beside the microphone or photodiode stages (X-10).

### 4.8 Length, skew and impedance

**There is no length-matching or skew rule on this board except these four:**

- N-9: `USB_DP_PI` and `USB_DM_PI` as one coupled pair, matched, over unbroken `DGND`.
  No width or gap is given, because the dielectric is not in the sources.
- B-18: each `MUX_OUTn` and its `AGND` sense trace equal in length across the gap.
- K-33: the two paralleled outputs of each LED-driver pair join by short, equal traces.
- K-23: the two legs of each ÷1/÷2 divider laid out symmetrically.

Short-as-possible rules: `BARCODE_RAW` (K-6), the summing junctions on L1 with no via
(K-11), the comparator outputs without detours (K-24), I²C (K-29), the hysteresis and FB
nodes (K-26, P-5). K-32 asks you to keep `U8` to `U11` and `U15` outputs short and to
judge series termination from the length you end up with; the schematic has no footprint
for it (Open question 9).

## 5. What a router must not do

- Put copper, a via or a pour in a slot, in a slot's keep-out, or across a barrier line
  (B-5 to B-9).
- Join `AGND` to `DGND` anywhere but `NT1`, or flood `FAN_RTN`, `CHASSIS_GND` or
  `ISO_5V_RTN` into a plane (G-1, G-3).
- Cross x = 170 with any net not in G-11, or with a G-11 net twice (G-10).
- Let a barcode-family net enter, overlie or underlie the `AGND` region (K-2).
- Let any pour touch `AO_GND_TPC`, `ACCESIO_AGND`, `ACCESIO_SHELL`, a `*_SHLD` net, an
  unused ground pin of `J2` or `J36`, a BNC post-mount pad, or pin 7 of `U3` or `U4`.
- Put a via on `A_PD1_CLAMP`, `A_PD2_CLAMP` or `U63_VIN` (K-11, P-8).
- Go under 0.2 mm track or gap except at `U55`, `U3` and `U4` (S-5).
- Move a panel connector, a straddler, a slot or `NT1`.
- Change a net, a footprint's pads or the schematic. If a route cannot be made, stop and
  say which rule blocks it.
- Run `gen_breakout_pcb.py`.
- Accept a new DRC class as "expected". The only accepted violations are the 24
  `silk_edge_clearance`.

## 6. Open items the router should know

**Closed on 2026-11-21, none of which moves a footprint:**

- *The BNC panel flat* is 5.56 mm (R10; `constraints.md` Open question 24).
- *Which BNC port is upper:* port A, pads 1 and 2 (R11; Open question 26). Only the
  footprint's description changed.
- *The NI cable* (R12; Open question 23, narrowed). It is held by jackscrews into
  jacksockets, in panel counterbores at the eight MDR68 fixing positions. A panel
  matter. What is left open is the jacksocket part and a bench trial.

**Still open, and bearing on routing:**

| | Where | What it means for you |
|---|---|---|
| K-1's two readings | Open question 2 | §4.4: meet K-2; K-3 is undecided |
| Series termination on the MDR68-bound buffers | Open question 9, K-32 | no footprints exist; note the lengths you route |
| A local bypass for `U1` | Open question 20, P-13 | no part exists; `U1` is at the entry capacitors |
| Fan headers `J54`, `J55` and the pair to them | `floorplan.md` Open question 6 | a 160 mm `FAN` pair across the barcode strip, or move the headers before routing |
| `J4`, the reward button | Open question 14 | board-mounted or wired; its pads are placed either way |
| Board mounting holes, corner radius, stack-up, fab house | Open question 8 | none is on the board; mounting holes will take routing room when they come |
| The 413 parts placed by rule | `placement.md` §7 and §10 | proposals; read the board before routing it |
| The regions are deeper than the connectors now need | `placement.md` §10 item 8 | there is spare depth behind each connector row |
| DRC's exit code | Task 14 Step 4 | "exit code 0" cannot be met while the 24 accepted warnings stand; say how you settle it |

**Bring-up provisions that are board parts, to be kept reachable:** `R224` at `J19`
(D-3), `C162` (B-15, D-4), `R196` and `R197` (K-13), `TP1` to `TP7` (P-15).

**Panel matters that do not touch the board:** the BNC sub-plates and their one unfitted
strap point each, of which only the rig-signal plate may ever be strapped (R8, R9;
`panel-elevations.md` §6); the M12 inlet at X = 210, 50 mm above the board, whose leads
run over `AGND` to `J1` (§7 there; M-13); the MDR68 counterbores (§8 there).

## 7. Checks after routing, and what Task 15 needs

1. `python3 hardware/gen/check_breakout_placement.py` prints `PASS`. It still checks
   every pad's net, every connector's position, every part's region and the slots.
2. DRC with `--severity-all`: **0 `unconnected_items`, 24 `silk_edge_clearance`, 0 of
   every other class.** Run it with `--schematic-parity` as well: 30 warnings and no
   error is what the unrouted board gives (`placement.md` §9).
3. `python3 hardware/gen/check_footprint_geometry.py hardware/breakout/breakout.net`.
4. The Python suite: `python -m pytest -q`.
   `tests/hardware/test_breakout_placement.py` runs the placement check on the committed
   board.
5. **By eye, because no tool checks them:** G-10 (only G-11 nets cross x = 170, in the
   corridor); K-2 (no barcode net over, under or in `AGND`); G-1 and G-3 (one tie each,
   nothing flooded); G-16 to G-20 (no pour on the nets of §4.2); P-5 and P-8; the three
   widths of §2; N-10's 83 squares.
6. V-4, the plan's Task 15 Step 4: render top and bottom, and confirm the slots are
   unbroken, no copper crosses them, and the barcode is away from the analog region.
7. Record in `placement.md` anything you moved, and commit `drc.rpt`.

**Task 15 then needs from the board:**

- the final position of every panel connector, unchanged, for the panel drawings (M-2,
  V-3). The plan's Step 1 exports `User.Drawings`, which is empty today: the panel
  outlines, the sub-plate windows of `panel-elevations.md` §6 and the counterbores of §8
  have still to be drawn;
- Gerbers, drill and position files from the routed board (Step 3), which want a
  stack-up and a fab house first (Open question 8);
- the renders of Step 4;
- the BNC holes at 5.56 mm (R10) and the labels by hole (R11, `panel-elevations.md` §4).
