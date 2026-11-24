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

**Read before the first track:** `placement.md` (all of it), then
`constraints.md` §2 to §6. `floorplan.md` §7 is a plan view of the regions.

**What the board file already has:** the 430 × 240 mm outline, four copper layers, the two
isolation slots on Edge.Cuts, 577 footprints (567 from the netlist, the IO Board's
two standoff holes `H1` and `H2`, and the board's eight mounting holes `H3` to `H10`, added
by task R0 on the user's decision of 2026-11-22), every pad on its net, 17 named keep-out
rule areas (the nine barrier and split areas, and `mh_H3` to `mh_H10` round the mounting
holes, task R0), and the IO Board's outline on User.Comments. **It has no track, no via and no pour.**

**How it is made** (task R1b): the driver places it and replays the route files.

```bash
kicad-cli sch export netlist --format kicadsexpr \
    -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
python hardware/gen/route_breakout.py build        # a Python with numpy; writes the board,
                                                   # routes/built.json and the stage contract
                                                   # (complete.nets, touched.nets, due.txt)
python3 hardware/gen/check_breakout_placement.py
```

`hardware/README.md`, "Regenerating fab outputs", has the full sequence with the netlist
checkers. `breakout.net` is not committed.

**The generator writes only to the build directory; copper comes only from `routes/`;
rebuild with the driver** (task R1b). `python hardware/gen/route_breakout.py build` runs
`gen_breakout_pcb.py --out build/route/placed`, replays the committed route files
`hardware/breakout/routes/stage-NN.json` onto that placement with no search, and writes
this board, `routes/complete.nets`, `routes/touched.nets` and `routes/due.txt` (the stage
contract, §7's partial mode); `route_breakout.py route N` routes stage N on top of
the stages before it, writes its route file, then builds. The commands above are how the
placement was first made: do not run the generator into `hardware/breakout/` now. Nobody
edits copper in pcbnew; a hand fix goes into its stage module's `fixed()`, and `route N`
carries it into the route file.
Before writing anything, the driver refuses a route file recorded against another
placement or other rules (re-route that stage), a route file whose `fixed` is not its
stage module's `fixed()` (a hand fix goes into the module; route files are generated), and
a committed board that is not what the driver last wrote (`routes/built.json`: any copper,
footprint or other edit made in pcbnew). To move a part, move it in the generator
(`ANCHORS`, `placement.md` §7) and rebuild; each stage routed before the move is then
refused until it is re-routed.

**What must not move.**

- **The 23 panel connectors.** They are at their panel positions (`placement.md` §2) and
  the panel will be drawn from them (M-2). `check_breakout_placement.py` fails if one
  moves.
- **The twelve parts that straddle a barrier**, and the slots under them: `U56` to `U61`
  and `U62` on the NI line, `U64`, `U65`, `U63`, `C162` and `U2` on the Intan line. Their
  pad-to-slot margins are 0.50 to 0.55 mm, and 1.635 mm at `U2` (`placement.md` §3.3).
- **`NT1`**, on the `AGND`/`DGND` split at (170, 94.5), and `J1` beside it.
- **The slots, the rule areas and the outline.**
- No part may cross into another domain's region; the check fails on it.

Everything else may be nudged to make a route work. `placement.md` §7 says which 386
parts were placed by rule and are proposals.

**The checks, before and after any session:**

```bash
python3 hardware/gen/check_breakout_placement.py
kicad-cli pcb drc --severity-all -o hardware/breakout/drc.rpt hardware/breakout/breakout.kicad_pcb
```

**What DRC shows today, before routing** (run 2026-11-21, KiCad 10.0.5; `drc.rpt`):

| Class | Count | |
|---|---|---|
| `unconnected_items` | 499 reported | **a cap, not a count.** The board has 1791 unrouted connections; see below. Routing takes it to 0. |
| `silk_edge_clearance` | 24 | two on each of the twelve straddlers, their own outlines over their slots. B-21 accepts these and they stay. |
| every other class | 0 | and must stay 0 |

The placement check prints `PASS` and `routed items (tracks, vias): 0`.

**DRC will not show you progress.** KiCad's report lists at most 499 unconnected items.
This board has **1791** unrouted connections (KiCad's own connectivity count, which
equals the sum over the 747 nets of pads less one), so DRC will go on saying 499 until
fewer than 499 are left anywhere on the board, and only then start to fall. To see where
you are, ask KiCad's connectivity directly, with the Python that ships inside KiCad
(the path is macOS's; measured with 10.0.5):

```bash
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 -c "
import pcbnew; b = pcbnew.LoadBoard('hardware/breakout/breakout.kicad_pcb')
b.BuildConnectivity(); print(b.GetConnectivity().GetUnconnectedCount(False))"
```

It prints 1791 today, after a wxApp assertion message that can be ignored. pcbnew's status
bar has an "Unrouted" field as well; it was not compared with this count. To check one region on its own, do it on
a copy whose other pads have had their nets removed: a copy holding only the 28 nets of
the NI island reports 144 by both this count and DRC.

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
| N-16 | no via in an SMD pad, and 0.2 mm from a via's copper to every SMD pad, its own net's included (a `physical_clearance`, so it holds within a net). Exempt: pad 9 of `U3` and `U4`, where P-6 asks for vias |

**Two widths DRC does not enforce, and one pin rule.** The class system holds one class
per net, and the first two rules are about part of a net:

- N-1: **`+5V` from `D3` to its plane is 1.0 mm.** `+5V` is in `RAIL_BRANCH`, so DRC
  accepts 0.4 mm there.
- N-2: **`-12V` from `D2` to its plane is 0.8 mm**, for the same reason. By N-1's
  reasoning `+12V` from `D1` to its plane is copper behind a 0.5 A fuse too; N-1 does not
  name it, so its width is yours.
- N-4's 0.6 mm *is* enforced, on every `NI_5V` and `NI_GND` track. What DRC cannot see
  is its pin rule: both `NI_5V` pins of `J45` (8 and 14) used, with symmetric copper, and
  all twelve `NI_GND` pins.

Two more that only a person can hold: N-10 (`+5V` copper drops no more than 25 mV from
`D3` to the farthest optocoupler LED resistor, about 83 squares) and G-8 (nothing
narrower than `NT1`'s own 2 mm bridge joins it, and both sides carry 1 A).

## 3. The order to route in

Derived from `constraints.md` and the plan's Task 14. The reasoning is that a pour or a
keep-out drawn late moves tracks, and that the nets with the least freedom should choose
their paths first. **[engineering judgment]** for the order itself; no source gives one.

1. **Confirm the fences.** Run both checks. Look at the 17 rule areas (the nine of
   `placement.md` §3.4, and the eight mounting-hole keep-outs of task R0) and the two
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
- The only holes for hardware on the board are `J56`, the earth stud; `H1` and `H2`,
  the IO Board's two standoff holes; and `H3` to `H10`, the board's eight mounting holes,
  placed by task R0 on the user's decision of 2026-11-22. All ten are non-plated and carry
  no net (M-16), and each of `H3` to `H10` is isolated from every plane by its 3.6 mm
  keep-out `mh_<ref>` (§6).

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
- Put a via in an SMD pad, or within 0.2 mm of one, on any net including the pad's own
  (N-16). DRC and the judge both flag it. The one exception is P-6's: the vias under pad 9
  of `U3` and `U4`.
- Go under 0.2 mm track or gap except at `U55`, `U3` and `U4` (S-5).
- Move a panel connector, a straddler, a slot or `NT1`.
- Change a net, a footprint's pads or the schematic. If a route cannot be made, stop and
  say which rule blocks it.
- Run `gen_breakout_pcb.py` into `hardware/breakout/`, or edit copper in pcbnew. The
  generator writes only to the build directory, copper comes only from `routes/`, and the
  board is rebuilt with `hardware/gen/route_breakout.py` (§1).
- Accept a new DRC class as "expected". The only accepted violations are the 24
  `silk_edge_clearance`.

## 6. Open items the router should know

**Closed on 2026-11-21, none of which moves a footprint:**

- *The BNC panel flat* is 5.56 mm (R10; `constraints.md` Open question 24).
- *Which BNC port is upper:* port A, pads 1 and 2 (R11; Open question 26). Only the
  footprint's description changed.
- *The NI cable* (R12; Open question 23, narrowed). It is held by jackscrews into
  jacksockets at the eight MDR68 fixing positions, and each panel opening has to admit
  the cable's own mating shell. A panel matter. What is left open is the jacksocket
  part and a coupon test-fit that gates the panel.

**Closed on 2026-11-21, by moving parts:** *the optocoupler escapes* (task J2). The pin-14
pull-ups `R123`, `R131`, `R139`, `R147`, `R163`, `R155` are 1.0 mm west. Each of pins 11 to
14 of `U56` to `U61` can leave its outer end by an F.Cu stub to a via **on the pin's own line
anywhere from x = 65.80 to 66.05**, with no via in a pad. The window is sized for N-16's
0.2 mm via-to-pad web, which DRC now enforces: a via at 66.10 fails N-16, and one at 65.70
fails N-13 (`placement.md` §7, "Pull-ups anchored by task J2").

**Closed on 2026-11-22, by moving five parts:** *the pin-16 bypass loops* (task J3). The
pin-13 pull-ups `R125`, `R133`, `R141`, `R149`, `R157` are 0.5 mm south, so pin 16 of every
NI optocoupler reaches its capacitor's `NI_GND` pad by a 0.6 mm (N-4) F.Cu track west
between the capacitor's `NI_5V` pad and the pull-up's, alongside pin 15's own track and
J2's escapes (`placement.md` §7, "Pin-13 pull-ups moved by task J3"). In the same task,
`U64`'s pin-15/16 bypass `C139`, which pass 3 had put 25 mm away, is at the pair, and
`R176` moved to make room (`placement.md` §7, "`U64`'s second bypass").

**Closed on 2026-11-22, by adding parts:** *the board's mounting holes* (task R0, the user's
decision; M-16). Eight unplated 3.2 mm holes with no net, `H3` to `H10`, at (165.5, 12),
(212.5, 16.5), (425, 41.5), (83, 125.5), (287, 120), (100, 220.5), (300, 223.5) and
(425, 225). Each sits in a 3.6 mm circular rule area, `mh_H3` to `mh_H10`, on all four
copper layers, that no track, via or pour may enter: route round it, and leave the planes
cut back from it. DRC enforces the rule areas, and the placement check fails if a hole or
its keep-out moves. No part moved to make room (`placement.md` §7, "The board's mounting
holes").

**Still open, and bearing on routing:**

| | Where | What it means for you |
|---|---|---|
| K-1's two readings | Open question 2 | §4.4: meet K-2; K-3 is undecided |
| Series termination on the MDR68-bound buffers | Open question 9, K-32 | no footprints exist; note the lengths you route |
| A local bypass for `U1` | Open question 20, P-13 | no part exists; `U1` is at the entry capacitors |
| Fan headers `J54`, `J55` and the pair to them | `floorplan.md` Open question 6 | a 160 mm `FAN` pair across the barcode strip, or move the headers before routing |
| `J4`, the reward button | Open question 14 | board-mounted or wired; its pads are placed either way |
| Corner radius, stack-up, fab house | Open question 8 | none is on the board. The mounting holes are (above) |
| The 386 parts placed by rule | `placement.md` §7 and §10 | proposals; read the board before routing it |
| The regions are deeper than the connectors now need | `placement.md` §10 item 8 | there is spare depth behind each connector row |
| DRC's exit code | Task 14 Step 4 | "exit code 0" cannot be met while the 24 accepted warnings stand; say how you settle it |

**Bring-up provisions that are board parts, to be kept reachable:** `R224` at `J19`
(D-3), `C162` (B-15, D-4), `R196` and `R197` (K-13), `TP1` to `TP7` (P-15).

**Panel matters that do not touch the board:** the BNC sub-plates and their one unfitted
strap point each, of which only the rig-signal plate may ever be strapped (R8, R9;
`panel-elevations.md` §6); the M12 inlet at X = 210, 50 mm above the board, whose leads
run over `AGND` to `J1` (§7 there; M-13); the MDR68 openings and jacksockets (§8 there).

## 7. Checks after routing, and what Task 15 needs

1. `python3 hardware/gen/check_breakout_placement.py` prints `PASS`. It still checks
   every pad's net, every connector's position, every part's region and the slots.
2. DRC with `--severity-all`: **0 `unconnected_items`, 24 `silk_edge_clearance`, 0 of
   every other class.** Confirm the 0 with the connectivity count of §1, which must
   print 0 as well. Run it with `--schematic-parity` as well: 30 warnings and no
   error is what the unrouted board gives (`placement.md` §9).
3. `python3 hardware/gen/check_footprint_geometry.py hardware/breakout/breakout.net`.
4. The Python suite: `python -m pytest -q`.
   `tests/hardware/test_breakout_placement.py` runs the placement check on the committed
   board.
5. **By eye, because no tool checks them:** G-10 (only G-11 nets cross x = 170, in the
   corridor); K-2 (no barcode net over, under or in `AGND`); G-1 and G-3 (one tie each,
   nothing flooded); G-16 to G-20 (no pour on the nets of §4.2); P-5 and P-8; the two
   widths and the pin rule of §2; N-10's 83 squares.
6. V-4, the plan's Task 15 Step 4: render top and bottom, and confirm the slots are
   unbroken, no copper crosses them, and the barcode is away from the analog region.
7. Record in `placement.md` anything you moved, and commit `drc.rpt`.

**Task 15 then needs from the board:**

- the final position of every panel connector, unchanged, for the panel drawings (M-2,
  V-3). The plan's Step 1 exports `User.Drawings`, which is empty today: the panel
  outlines, the sub-plate windows of `panel-elevations.md` §6 and the MDR68 openings and
  counterbores of §8 have still to be drawn, the last after the coupon test-fit;
- Gerbers, drill and position files from the routed board (Step 3), which want a
  stack-up and a fab house first (Open question 8);
- the renders of Step 4;
- the BNC holes at 5.56 mm (R10) and the labels by hole (R11, `panel-elevations.md` §4).

### The automatic judge

`hardware/gen/judge_layout.py` (task J1, 2026-11-21) grades a routed board, or a region-only
copy of it, against this board's rules, so that two candidate layouts can be ranked without
a person. **It does not sign a board off**: the checks of this section still end with a
person, once, before fab. Run it with KiCad's own Python, from the repository root:

```bash
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
    hardware/gen/judge_layout.py hardware/breakout/breakout.kicad_pcb \
    [--baseline UNROUTED.kicad_pcb] [--reference REF.kicad_pcb] \
    [--nets NETS.txt --region x0,y0,x1,y1] [--json OUT.json] [--kicad-happy] \
    [--allow-not-checked ID,ID,...] [--complete-nets routes/complete.nets]
```

- **Layer 1, gates:** DRC with `--severity-all`, on a copy staged as `breakout.kicad_pcb`
  beside this directory's project, rules and library tables (a board under any other name
  is checked without them), with a control track that only `N-1 INLET track width` can
  flag, to prove the rules loaded; the classes against the accepted 24
  `silk_edge_clearance`; the connectivity count of §1 after filling every zone; the
  placement check; and, with `--nets`/`--region`, every track, via and pour inside the
  region and on a listed net.
- **Layer 2:** item 5's checks and the prohibitions of §5 that DRC cannot see, each by rule
  ID, split into the clauses its rule asks for, with coordinates. Where a rule's words leave
  a number open the judge states one and marks it `[judge's reading]`; the readings are in
  the JSON. The user accepted all 27 readings as written on 2026-11-22
  (`judge-readings-2026-11-22.md`, in the one-clock workspace); the summary prints
  `[reading accepted 2026-11-22]` beside the mark, which stays, so a reading can still be
  told from a sourced value. G-1's and FIXED's readings were extended after that sheet
  (task R0, mounting holes), and say so. `N/A` means the rule's nets are absent or not yet
  routed, never a pass.
  `NOT CHECKED` means a clause the board cannot settle (G-1's mounting hardware, K-23's hand
  access), or a figure the judge has only as a bound that does not decide it (N-10 through a
  pour); it is printed with its reason. A list of the rules no check holds is printed too.
- **FIXED** compares the panel connectors, straddlers, `NT1`, `J1`, Edge.Cuts and rule
  areas with `--reference`, else `--baseline`, else this board as committed on `main`; when
  the reference is the board itself it says so and reports `NOT CHECKED`.
- **Layer 3:** comparison numbers (vias, signal copper on L2 and L3, pour islands and
  necks, bypass path lengths, routed length against a minimum spanning tree, layer changes),
  against `--baseline` if given.
- **Layer 4, `--kicad-happy`:** kicad-happy's findings new in the board, advisory only.
  Its return-path figures are re-measured on the adjacent plane: with no stack-up in the
  board file it samples the opposite outer layer instead.

**Partial mode, the stage contract** (task R2, amended in its fix round 1). A board routed
through stage N is judged against three lists `route_breakout.py` writes, each the union over
the stages built: `routes/complete.nets` (the nets the stages complete, every pad joined: by
default those a stage routes with tracks or vias, never by a zone alone), `routes/touched.nets`
(every net with copper) and `routes/due.txt` (each stage module's `DUE`: check IDs, or
check/clause keys such as `G-1/overlap`). Run the judge with
`--complete-nets routes/complete.nets`; it reads the other two beside it (`--touched-nets`,
`--due` to name others; `--routed-nets` is the old name). Each layer-2 check, and each of its
clauses, is printed with its key and its class:

- a **prohibition**, which any copper present can break (G-10's foreign crossings, K-2,
  P-8's and K-11's no-via, N-16, B-16), is judged on all the copper, on any net;
- a **requirement**, which needs copper to exist (N-4's pins, B-18's pairs, K-35's joined
  bypass, N-1/N-2's widths along a path, N-10's squares, every SHORT ratio, G-10's corridor
  and every G-11 net crossing, S-7's every plane poured), is `PENDING` until every net it
  needs is complete, unless it is due. A `PENDING` clause prints what it found so far and
  the nets it waits on, and does not count against the exit code;
- a **due** check or clause is judged on the copper present, and anything but `PASS`, `N/A`
  included, fails the run. A requirement that is judged (due, or its nets complete) holds
  for every net it names: a net it needs with no copper FAILs it, so it cannot pass on the
  subset of its nets that has copper.

CONNECTIVITY counts the complete nets' unrouted connections (the whole board's count is
printed beside it); DRC sets aside an `unconnected_items` entry only when every item in it
is on a net that is not complete, and every other class keeps the accepted 24
`silk_edge_clearance` (DRC lists at most 499 `unconnected_items`, so the CONNECTIVITY count
is the complete one); the NET-LISTS gate fails copper on a net in neither list. A SHORT
ratio on a net whose pads are not all joined is a lower bound: over 1.5 it FAILs, under it
it is NOT CHECKED, never PASS, in either mode; B-18's, N-9's, K-23's and K-33's length
comparisons are the same (out of tolerance FAILs, else NOT CHECKED until the net is joined). Without `--complete-nets` everything is
judged and nothing is ever `PENDING`. G-1's mounting-hardware clause is `NOT CHECKED` on
every board, so a stage run waives G-1 (`--allow-not-checked G-1`) and makes `G-1/overlap`
due; from R9, once K-23's nets are complete, its `K-23/block` clause is NOT CHECKED too, and
the stage run waives `G-1,K-23`. On the committed, unrouted board, empty lists exit 1 on G-1 alone: DRC, connectivity
and NET-LISTS pass, and every requirement is `PENDING`.

Exit code 0 only when every gate and every layer-2 check is `PASS` or `N/A` (or `PENDING`, in
partial mode); 2 on a bad argument, such as a name in the lists that is not a net of the
board, a `due.txt` entry that is neither a check ID nor a clause key, or `--complete-nets`
with `--nets`/`--region`. A rule check's `NOT CHECKED` counts
against it unless that check's ID is named in `--allow-not-checked`; a
`FAIL` is never waived, and no gate (DRC, connectivity, placement, region) can be named.
`--skip-drc` is for development only: a run that uses it never exits 0. The summary lists
every waived ID with its reason. On the committed, unrouted board it exits 1 on DRC
(`unconnected_items`), connectivity (1791), and G-1 and FIXED `NOT CHECKED`.

**On a fully routed board**, run it against the unrouted placement it was routed from, and
waive the four checks that the board can never settle:

```bash
git show 96a7d50:hardware/breakout/breakout.kicad_pcb > /tmp/breakout-placement.kicad_pcb
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
    hardware/gen/judge_layout.py hardware/breakout/breakout.kicad_pcb \
    --reference /tmp/breakout-placement.kicad_pcb \
    --allow-not-checked G-1,K-23,K-35,N-10 --json /tmp/judge.json
```

- `G-1`: whether a screw or standoff joins two pours is a property of hardware that is not
  on the board.
- `K-23`: a 2-gang block fitted and moved by hand is a matter of access, not of copper.
- `K-35`: `U65` is an ACSL-6420, whose supply pins K-35 does not name.
- `N-10`: with `+5V` an L3 plane, every path from `D3` crosses a pour, whose resistance the
  judge bounds only from below, so it can fail the 83 squares but never pass them.

`--reference` matters once the routed board is on `main`: without it FIXED compares the
board with itself and reports `NOT CHECKED`. Waive nothing else without reading why it is
`NOT CHECKED`. Its tests are
`tests/hardware/test_judge_layout.py`: for each clause under test a base board on which it
passes and a control that makes one change and fails it; skipped where KiCad is not
installed.
