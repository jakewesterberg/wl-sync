# NI-island router bake-off: report

Task R1c of `docs/superpowers/plans/2026-11-22-breakout-routing.md`, 2026-11-24. Our routing
engine (task R1a) and Quilter routed the NI island of the breakout board, and the judge
(`hardware/gen/judge_layout.py`) graded them. **The user's decision: our engine routes every
stage, with Quilter's numbers as its targets.** The rest of this report is the evidence.

Sources, cited below by short name:

| Short name | Where |
|---|---|
| grading-run2 | `.superpowers/sdd/2026-11-22-breakout-routing/bakeoff-log/grading-run2/` in the main checkout: `summary.md`, `A/`, `B/`, `C/` (`judge.txt`, `judge.json`), `grading-run2-key/` (`blind-key.json`, `costs.md`). The judge is at c735422 |
| grading-run1 | the same `bakeoff-log/grading-run1/`, judge at 6368b86 (run 1 and the engine only) |
| run1 log, run2 log | `bakeoff-log/quilter-run1.md`, `quilter-run2.md`: the controller's steps in Quilter's UI, with Quilter's messages quoted verbatim |
| ledger | `.superpowers/sdd/2026-11-22-breakout-routing/progress.md`, the lines from "BAKE-OFF" on |
| R1c report | `.superpowers/sdd/2026-11-22-breakout-routing/task-R1c-report.md` |
| candidates | `results/engine/`, `results/quilter-1/`, `results/quilter-2/` here, each a `breakout.kicad_pcb` and a `meta.json` |

## 1. Who took part, and what each got

**DeepPCB was dropped before it routed anything.** The user, 2026-11-24 (ledger): "we will
only do quilter, and not deeppcb. deeppcb doesnt have any academic discount". Its board was
uploaded but never submitted, so it spent nothing.

The three candidates did not get the same help. Read every comparison below with this
table (grading-run2 `costs.md`; each candidate's `meta.json`):

| | engine | quilter-1 | quilter-2 |
|---|---|---|---|
| package | `ni-island/` | `ni-island/` | `ni-island-region/`: `ni-island/` plus one placement rule area |
| attempts | 1 run here, of a plan tuned on this island in R1a over at least 5 earlier routing runs | 1 job, 1 candidate | the user's one retry ("Retry Quilter once", 2026-11-24); 1 candidate |
| help beyond the package | a hand-written stage, `engine/stage_ni.py` (344 lines). It fixes the pin tables, the J45 power stubs, In2.Cu signal only under J45, the J2 via bands, the order, the corridors, the B-13 fences and the plane rectangles | the settings entered in Quilter's UI from `ni-island/RULES.md` (run1 log): stackup, fabricator minimums 0.2/0.2/0.6/0.3/0.5 mm, NI_5V power pour at 500 mA, NI_PWR widths 0.6 mm | run 1's settings, plus every island part assigned to the region and NI_5V's computed widths edited to 0.6 mm (run2 log) |
| wall-clock | 62 s of routing only; writing the plan is not counted | 2886 s, Quilter's cloud time | 2272 s |
| cost | 0 | 0 (free noncommercial plan; its inputs may be used for training) | 0 |

**What Quilter could not take** (run1 log, step 6, verbatim): "Constraints from ECAD files
were not parsed. This is currently only supported in Altium and Xpedition binary." So
Quilter never saw the `.kicad_dru`: not N-13, not N-16, not N-12, and not the net classes.

## 2. The comparison (grading-run2, de-blinded)

Key (grading-run2 `blind-key.json`): A = quilter-2, B = engine, C = quilter-1.

| | quilter-2 (A) | engine (B) | quilter-1 (C) |
|---|---|---|---|
| **judge exit** (0: every gate and check PASS or N/A) | 1 | **0** | 1 |
| not passing | DRC, PLACEMENT, REGION, K-4, S-7 | none | DRC, PLACEMENT, REGION, K-4, S-7, B-13-ALL |
| DRC-RULES | PASS | PASS | PASS |
| DRC | FAIL | PASS | FAIL |
| CONNECTIVITY | PASS (0 unrouted) | PASS | PASS |
| PLACEMENT | FAIL | PASS | FAIL |
| REGION | FAIL | PASS | FAIL |
| DRC classes | clearance 204, silk_edge_clearance 24 | silk_edge_clearance 24 | clearance 179, silk_edge_clearance 24, track_width 57 |
| K-2 | PASS | PASS | PASS |
| K-4 | FAIL | PASS | FAIL |
| S-7 | FAIL | PASS | FAIL |
| N-16 | PASS | PASS | PASS |
| K-35 | PASS | PASS | PASS |
| N-4 | PASS | PASS | PASS |
| B-13-ALL | PASS | PASS | FAIL |
| FIXED | PASS (against `ni-island-region/`, its `judge_reference`) | PASS | PASS |
| every other check (26) | N/A | N/A | N/A |
| vias | 102 | 138 | **99** |
| connections routed | 144 / 144 | 144 / 144 | 144 / 144 |
| signal mm on In1.Cu | 0 | 0 | 0 |
| signal mm on In2.Cu | **0** | 60.5 | **0** |
| NI_GND plane: islands / narrowest neck | **1 / 10 mm** | 3 / 5.5 mm | 1 / 2.5 mm |
| NI_5V plane: islands / narrowest neck | **1 / 10 mm** | 1 / 3 mm | 1 / 1.9 mm |
| routed length / MST | **1.153** (3172.4 / 2752.0 mm) | 1.232 (3391.3 / 2752.0) | 1.211 (3332.7 / 2752.0) |
| vias in SMD pads; within 0.2 mm of one (N-16) | 0; 0 | 0; 0 | 0; 0 |
| vias per routed net, mean / max | 3.64 / 31 | 4.93 / 31 | 3.54 / 29 |

The 26 N/A checks are rules whose nets are not on this island. Quilter's exports rename the
inner layers "Ground Layer 1" and "Power Layer 2", which is why the judge lists their pours
under those names. Its "0 mm on In2.Cu" is real: counted by layer id with KiCad's Python,
quilter-1 has 202 top and 151 bottom tracks and quilter-2 has 197 and 136, none on an inner
layer. Ours has 39 pieces on In2.Cu (`scratchpad/r1c/writeup/layers.py`).

## 3. Why Quilter fails

**N-13 is never given to it.** It does not parse KiCad's rules (section 1). Every clearance
violation is N-13's: in a re-run of KiCad's DRC staged as the judge stages it,
`scratchpad/r1c/writeup/quilter-*/hardware/breakout/drc.json`, every `clearance` entry
names "N-13 panel-side nets, outer layers". There are 174 for quilter-1 and 190 for
quilter-2. The judge's own runs counted 179 and 204; the count moves with KiCad's zone
refill. They sit where N-13 bites:
- for quilter-2, 85 at J45 (y ≥ 226) and 77 in the optocoupler field (x ≥ 55);
- for quilter-1, 86 and 80.

Quilter's 0 mm on In2.Cu and its lower via count are bought partly here. With 0.2 mm
between J45's pads on the outer layers it escapes the connector's inner rows on F.Cu and
B.Cu, which N-13 forbids. Our engine has to take those rows to In2.Cu (R1c report,
"Why there is no PANEL net class").

**One primary ground blocks the region pour.** Run 2's one structural change was a
placement region over the island, so that NI_GND could be set to "Use Region Pour".
Quilter refused, verbatim (run2 log): "One ground net is the primary: make another one
primary to replace it." NI_GND is the island's only ground net, so it must be primary, and
its documentation says a net cannot be both primary and a region pour. Both runs therefore
poured NI_GND and NI_5V over the whole board:
- REGION, grading-run2 `A/judge.txt` and `C/judge.txt`: "pour 'NI_GND.LAYER_1' vertex
  (0.01,0.01) outside" and "pour 'NI_5V.LAYER_2' vertex (0.01,0.01) outside";
- S-7's `S-7/poured` clause reports "NI_5V: 0 mm2 of fill on In2.Cu inside the NI region"
  and the same for NI_GND on In1.Cu. Yet quilter-2's `judge.txt` metrics show the two pours
  as one island each, of 9128.7 and 9119.0 mm², on the renamed layers. So that clause may be reading the layer rename,
  not missing copper. That is a question for the judge, not settled here, and REGION fails
  either way.

**Its export rewrites our keep-outs.** PLACEMENT, from both `judge.txt`, verbatim in part:

```
6: rule area ni_band_side is not on all four copper layers
6: rule area ni_band_back is not on all four copper layers
6: rule area intan_band_dgnd is not on all four copper layers
...
6: slot 0 has no keep-out reaching 1.0 mm beyond it on all four layers (B-8)
6: slot 1 has no keep-out reaching 1.0 mm beyond it on all four layers (B-8)
6: no rule area agnd_dgnd_gap on L2 and L3 covering the split (G-9)
6: rule area mh_H3 is not on all four copper layers
```

In the package, 16 of the 17 rule areas cover all four copper layers, and `agnd_dgnd_gap`
covers In1.Cu and In2.Cu (the obstacle extract, `engine/extract.py`). Quilter's returned
boards carry them on fewer. It did not move a part: FIXED passes.

**K-4: BARCODE_NI is not the shortest path.** The judge's reading is at most 1.5 x MST:
- quilter-2: "BARCODE_NI: 130.24 mm routed against 80.86 mm MST (over 1.5x)" (1.61 x);
- quilter-1: 134.83 mm (1.67 x);
- the engine: K-4 PASS, with 78.72 mm of copper against an extent of 78.69 mm between its
  pads (R1c report, section 4).

No Quilter setting expresses it (RULES.md: "not expressible").

## 4. What run 2's two fixes changed

| Fix (run2 log) | quilter-1 | quilter-2 |
|---|---|---|
| NI_5V's computed widths edited from 0.21/0.454/0.21 mm to 0.6 mm (Top, Power, Bottom). Run 1's narrow segments were Quilter's own computed width, all 57 "NI_5V on Top Layer" at 0.2102 mm | DRC `track_width` 57 ("N-4 NI_PWR track width") | **0** |
| every island part assigned to the placement region | B-13-ALL FAIL: "track NI_5V_FB_RAW ... leaves U62.6 (62.38,182.00) from its inner end" | **PASS** (49 straddler pads) |

Whether the B-13 fix came from the region assignment or from the run's other differences
cannot be told from one run each. The region pour, the change run 2 was for, did not
happen (section 3).

## 5. Where Quilter was better

On every layout metric the stage protocol reports, Quilter's run 2 beat our engine:
- **vias:** 102 against 138, 0.708 per connection against 0.958. Run 1 had 99;
- **signal on In2.Cu:** 0 mm against 60.5 mm;
- **routed length / MST:** 1.153 against 1.232;
- **plane shape:** both planes one island with a 10 mm narrowest neck, against our NI_GND
  in 3 islands (neck 5.5 mm) and NI_5V at 3 mm.

Some of this is the N-13 freedom it was never denied (section 3). The plane numbers are
not: they come from pouring the whole island rather than our rectangles cut by stubs and
vias.

## 6. The decision

The user, 2026-11-24 (ledger): **"Our router, Quilter's numbers as targets
(Recommended)"**. Our engine routes every stage, R3 to R13. Quilter's run-2 numbers become
targets for tuning it on the NI island (the plan's new Task R1d). No importer for Quilter
boards is needed (R1c Step 4).

**The targets, on the NI island:**

| Metric | Target | Quilter run 2 | Engine now |
|---|---|---|---|
| vias | ≤ 102 (≤ 0.708 per connection) | 102 | 138 (0.958) |
| signal copper on In2.Cu | 0 mm | 0 | 60.5 mm |
| routed length / MST | ≤ 1.153 | 1.153 | 1.232 |
| plane narrowest neck, each plane | ≥ 10 mm | 10 / 10 | 5.5 / 3 |

**The judge stays the gate.** A target is a goal. A miss is reported with its cause and
never fails a stage. A judge miss always does. The In2.Cu target may not be fully reachable
under N-13: Quilter reached 0 only by escaping J45's inner rows on the outer layers at
0.2 mm, which N-13 forbids (section 3). If R1d finds that so, it reports the residue and
its cause.

## 7. Files

- `ni-island/`, `ni-island-region/`: the packages; `make_package.py` builds both.
- `results/engine/`, `results/quilter-1/`, `results/quilter-2/`: the candidates.
- `deeppcb_client.py`: the DeepPCB client, unused after the user dropped DeepPCB.
- `grade_all.py`: the blind grading. Its run-2 output is grading-run2.
- `engine/`: our candidate's extract, stage, apply and `run.sh`.
