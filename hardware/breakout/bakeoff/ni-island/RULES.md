# NI island: the routing rules in plain words

This package is the NI island of the wl-sync breakout board, for a router bake-off (task
R1c, 2026-11-24). Every router, ours and the two cloud services, gets the same files and these
same rules, entered as far as its own rule entry allows. What a tool could not take is
recorded in the bake-off report.

The rules are `hardware/breakout/constraints.md` in the wl-sync repository, cited by ID.
Where this page and that file disagree, that file is right. The candidates are graded
against the repository's own `breakout.kicad_pro` and `breakout.kicad_dru` by its judge
(`hardware/gen/judge_layout.py`), not against what a tool understood.

## The job

- **Route all 144 connections** of the 28 nets that have any, and **draw two planes**:
  `NI_GND` on In1.Cu and `NI_5V` on In2.Cu, each held to the island (S-6, S-7).
- The other 30 nets, `unconnected-(J45-PadNN)`, are single pads. They need nothing.
- **Everything is placed. Move nothing**: no footprint, pad, net, slot, rule area or outline.
- **The island** is the region x 0 to 70 mm, y 100 to 240 mm, in the board file's
  coordinates (x to the right, y down, the back edge at y = 0 and the front edge at
  y = 240). Every track, via and pour, pour outline included, must lie inside x 0 to 68.75,
  y 101.25 to 240, and be on one of the nets in `island.nets.txt`. The judge fails any copper
  outside x 0 to 70, y 100 to 240.
- The rest of the board is in the file only as obstacles. Its pads have no net.

## The files

| File | What it holds |
|---|---|
| `breakout.kicad_pcb` | the board: outline, slots, 17 rule areas, 577 footprints, the island's nets; no track, via or pour |
| `breakout.kicad_pro` | net classes (widths, clearances, via) and the board-level clearances |
| `breakout.kicad_dru` | KiCad custom rules (N-13, N-16 and the width minimums), loaded by KiCad with the board |
| `island.nets.txt` | the 58 island nets |
| `fp-lib-table`, `footprints/` | every footprint the board uses, so the project opens on its own |
| `sym-lib-table`, `symbols/` | the project's own symbol library (there is no schematic) |

KiCad 10.0.5 opens it in place. `kicad-cli pcb drc --severity-all breakout.kicad_pcb`, run in
this directory, loads the rules and shows 144 `unconnected_items` and 24
`silk_edge_clearance` (the straddlers' own outlines over their slots, accepted by B-21) and
nothing else.

**What each tool reads.** KiCad reads all three rule sources. DeepPCB's upload is the
`.kicad_pcb` and the `.kicad_pro` only (its plugin sends no `.kicad_dru`), so it sees the net
classes, the board-level clearances and the rule areas, and nothing in the `.kicad_dru`.
Quilter reads keep-outs and, by its documentation, "net-class, layer-specific, and
pair-specific clearance rules"; that is shown only for Altium, so whether it reads KiCad's
`.kicad_dru` is not known.

## The rules, and how this package expresses each

"Net class" means the rule is in `breakout.kicad_pro` and any tool that reads KiCad net
classes gets it. "Keep-out" means a rule area on the board. "`.kicad_dru` only" means only a
tool that reads KiCad's custom rules gets it. "Not expressible" means no file can carry it,
so it has to be entered by hand if the tool allows.

### Stack-up

| Rule | In plain words | Expressed by |
|---|---|---|
| S-1 | Four copper layers: F.Cu signal, In1.Cu ground plane, In2.Cu power plane, B.Cu signal. | the board's layers (In1.Cu and In2.Cu are typed `power`, KiCad's plane type). The names are KiCad's, not "gnd"/"pwr": **enter by hand** where a tool asks each layer's purpose (In1.Cu ground, In2.Cu power) |
| S-2 | The outline is 430 x 240 mm. | the board outline (Edge.Cuts) |
| S-3 | 1.6 mm board, 35 um (1 oz) copper on all four layers. | thickness 1.6 mm in the board; **copper weight not expressible**: the board has no stack-up block (no dielectric is in the sources). Enter by hand if asked |
| S-4 | Every part is on F.Cu. | the placement |
| S-5 | 0.15 mm track and gap only at `U55`, `U3` and `U4`. **None of them is in the island, so the island's floor is 0.2 mm track and 0.2 mm gap.** | net classes give 0.2 mm; but the board's own minimums in the `.kicad_pro` are 0.15 mm (for those three parts), so **enter 0.2 mm by hand** as the minimum trace width and clearance if the tool takes board minimums from the file |
| S-6 | `NI_GND` is the island's plane on In1.Cu, and only inside the island. | **not expressible** (the package has no pours, as our engine had none). Enter by hand: an `NI_GND` pour on In1.Cu filling the island, held inside x 0 to 68.75, y 101.25 to 240 |
| S-7 | `NI_5V` is the island's plane on In2.Cu, and only inside the island. | **not expressible**, as S-6: an `NI_5V` pour on In2.Cu inside the same rectangle |

The rule areas already stop a pour at the island's edge (B-7 to B-9 below); a pour drawn
to the rectangle fills correctly. A pour whose outline reaches past the rectangle fails the
judge even when its fill does not.

### Barriers, slots and keep-outs

The island is galvanically isolated. Its boundary is the line x = 70 (from y = 100 to the
front edge) and y = 100 (from x = 0 to 70).

| Rule | In plain words | Expressed by |
|---|---|---|
| B-5 | The slots are real cutouts in the board. | the board outline (Edge.Cuts): the NI slot runs under the seven straddling parts |
| B-6 | Nothing crosses the barrier line on any layer: no track, via or pour. Only the straddling parts span it: `U56` to `U61` (optocouplers) and `U62` (DC/DC). | keep-outs `ni_band_side`, `ni_band_back`, `ni_slot_keepout` |
| B-7 | A copper-free band at least 2.5 mm wide along the barrier, on all four layers. | keep-outs `ni_band_side` (x 68.75 to 71.25, y 98.75 to 240) and `ni_band_back` (x 0 to 71.25, y 98.75 to 101.25), no tracks, vias or pours, all layers |
| B-8 | A keep-out over each slot and 1 mm beyond it, no tracks, vias or pours, all layers. Pads may sit inside it; tracks may not. | keep-out `ni_slot_keepout` (x 67.75 to 72.25, y 101.3 to 185.93) |
| B-9 | The keep-out runs from slot to board edge, so nothing routes round a slot's end. | the two bands reach the board edge at x = 0 and y = 240 |
| B-12 | `U62`: the slot passes between its pins 2 and 4, about 0.54 mm each side. Its island-side pins are the isolated ones. | the placement and the slot |
| B-13 (as applied to every straddler, routing-brief §4.1) | **Every straddler pad is left from its outer end, away from the slot.** No track may end inside a straddler pad on the half nearer the barrier line (x = 70) than the pad's centre. (B-13 itself names `C162` and `U63`, which are not in this island; the brief applies its rule to all twelve straddlers.) | partly: `ni_slot_keepout` (from x = 67.75) covers the innermost 0.75 mm of each optocoupler pad (pads x 66.55 to 68.5, centres 67.525) and 0.46 mm of `U62`'s pin 2 pad; the rest **not expressible**: enter by hand if the tool allows |
| B-22 | `U62` and its companions `C135`, `C136`, `FB3`, `R170` are routed as if fitted (they are DNP). | the netlist on the board |
| X-7 | `J45`'s two boardlock holes are non-plated: keep copper on all layers clear of them. | the holes are on the board; hole clearance 0.25 mm in the `.kicad_pro` |

### Widths, clearances and vias

| Rule | In plain words | Expressed by |
|---|---|---|
| N-4 | `NI_5V` and `NI_GND` tracks are at least 0.6 mm wide. | net class `NI_PWR` (track width 0.6 mm) and the `.kicad_dru` rule "N-4 NI_PWR track width" (a minimum DRC enforces) |
| N-4, its pin rule | **Use both `NI_5V` pins of `J45` (8 and 14), with symmetric copper, and all twelve `NI_GND` pins of `J45`.** The judge reads "symmetric" as: both pins joined by `NI_5V` pours on the same layers, the tracks hanging off each within 1.0 mm in total length, with the same narrowest width and the same via count. | **not expressible**: enter by hand if the tool allows |
| N-8 | Every other track is at least 0.2 mm. | net class `Default` (0.2 mm) and the `.kicad_dru` rule "N-8 default track width" |
| N-11 | 0.2 mm copper to copper, as the default. | net class `Default` (clearance 0.2 mm) |
| N-13 | **0.6 mm on the outer layers (F.Cu and B.Cu) between any of the 24 panel-connector nets and any other net.** On the inner layers the default 0.2 mm holds. The 24, all pins of `J45`: `BARCODE_NI`, `EVT_D0_NI` to `EVT_D15_NI`, `EVT_STROBE_NI`, `PD1_COMP_NI`, `PD2_COMP_NI`, `RHS_STIM_OUT_NI`, `RWD_CMD_NI`, `RWD_DLVR_NI`, `STIM_TRIG_NI`. | **`.kicad_dru` only** (rule "N-13 panel-side nets, outer layers"). **Not expressible as a net class**: a class's clearance holds on every layer, and at 0.6 mm on every layer the island cannot be routed. `J45`'s pads are 1.4 mm on a 2.54 mm pitch, 1.14 mm apart, and a 0.2 mm track between two of them needs 0.2 + 2 x 0.6 = 1.4 mm. So no track would pass between two `J45` pads on any layer, and 11 of the 12 panel nets on `J45`'s two inner rows (y = 231.09 and 232.995), boxed in by `J45`'s own pads, could not be reached at all; under N-13 as written they escape on an inner layer. (The twelfth, pin 2, is at its row's end.) Enter by hand as an outer-layer-only clearance if the tool allows; do not enter it as a clearance on every layer |
| N-14 | Every via: 0.6 mm pad, 0.3 mm drill, through. | every net class (via 0.6 mm / 0.3 mm) |
| N-16 | **No via in an SMD pad, and every via's copper at least 0.2 mm from every SMD pad, of any net, the pad's own included.** | **`.kicad_dru` only** (rule "N-16 via clear of SMD pads", a `physical_clearance`). Not expressible as a net class (a net class has no clearance within its own net). Enter by hand if the tool allows |
| board | Copper at least 0.5 mm from the board edge and from the slots; 0.25 mm from any hole; 0.25 mm hole to hole. | the `.kicad_pro`'s board rules (`min_copper_edge_clearance`, `min_hole_clearance`, `min_hole_to_hole`), written out at KiCad's defaults |

### The nets that must take a particular path

| Rule | In plain words | Expressed by |
|---|---|---|
| K-4 | `BARCODE_NI` takes the shortest path from `U60` to `J45` pin 3, inside the island. The judge reads "shortest" as at most 1.5 x the octilinear minimum spanning tree of its pads. | **not expressible**: enter by hand if the tool allows (as a length limit or a priority net) |
| K-34 | Each optocoupler's 3.9 kOhm pull-ups (`R123` to `R169`, on `NI_5V`) are on the island side, one per output. | the placement; the netlist joins each output to its own pull-up |
| K-35 | A 100 nF bypass at each optocoupler supply pin pair, on the island side: for each of `U56` to `U61`, pins 10 and 9 to one capacitor and pins 15 and 16 to another (`C121` to `C132`). The judge reads "at" as each capacitor pad within 5 mm of its pin and joined to it. | the placement and the netlist. **Enter by hand** where a tool takes bypass capacitors (pairs: `U56` `C121`/`C127`, `U57` `C122`/`C128`, `U58` `C123`/`C129`, `U59` `C124`/`C130`, `U60` `C125`/`C131`, `U61` `C126`/`C132`) |

### What the placement leaves, from the routing brief (consequences, not further rules)

- **Optocoupler outputs** (routing-brief §6, task J2): each of pins 11 to 14 of `U56` to
  `U61` can leave its outer end by an F.Cu stub to a via on the pin's own line anywhere from
  x = 65.80 to 66.05, with no via in a pad. A via at 66.10 fails N-16; one at 65.70 fails
  N-13.
- **Pin 16** of every optocoupler reaches its capacitor's `NI_GND` pad by a 0.6 mm F.Cu
  track (routing-brief §6, task J3).
- **N-13 makes the escape from `J45` wider than the default**: on the outer layers no track
  passes between two of its pads, so its inner rows escape on an inner layer
  (routing-brief §4.5).

## What a router must not do here (routing-brief §5)

- Put copper, a via or a pour in a slot, in a slot's keep-out, or across the barrier line.
- Put a via in an SMD pad, or within 0.2 mm of one, on any net including the pad's own.
- Go under 0.2 mm track or gap.
- Move a part, or change a net or a footprint's pads.
- Accept a new DRC class as expected. The only accepted violations are the 24
  `silk_edge_clearance`.
