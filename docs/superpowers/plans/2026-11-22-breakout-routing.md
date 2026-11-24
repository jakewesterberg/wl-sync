# Breakout Board Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `hardware/breakout/breakout.kicad_pcb` completely, by script, in the order of
`routing-brief.md` §3, with every stage gated by the automatic judge.

**Architecture:** A grid maze router grown from the Opus NI-island trial router
(`git show trial-ni-opus:hardware/breakout/trial_route_ni.py`), with its rasterisation and
search moved to numpy. KiCad's Python (which has no numpy) only reads the placed board and
writes the routed one. Each stage of §3 is a module that produces a committed route file.
The routed board is a build product: `gen_breakout_pcb.py` places the board into a build
directory, and the driver replays every stage's route file onto it. A placement nudge
therefore costs a rebuild, not a re-route by hand. The judge gains a partial mode, so that
a board routed through stage N can be judged on the nets routed so far.

**Tech Stack:** KiCad 10.0.5 (`pcbnew` Python, `kicad-cli`); Python ≥ 3.11 with numpy for the
engine (`/opt/anaconda3/bin/python`, 3.13); pytest.

**Spec:** `hardware/breakout/routing-brief.md` (all of it) and the rules it cites in
`hardware/breakout/constraints.md`, which wins where the two differ. The parent plan is
`docs/superpowers/plans/2026-08-13-breakout-pcb.md` Task 14, which marked the routing
`[human-led]` because `kicad-cli` has no Specctra export. These decisions replace that
marking:
- the user's choice of scripted routing, after the NI trial (2026-11-21, "Routing trial
  (NI island)"; Opus routed the better board);
- "Our router, sped up" (2026-11-22);
- the judge's 27 readings, accepted as written (2026-11-22,
  `.superpowers/sdd/2026-11-07-one-clock/judge-readings-2026-11-22.md`).

**Runs after:** task J3 (pin-16 bypass loops; branch `pin16-escape`) is merged. Task J4,
which records the accepted readings in the judge, is folded into Task R2 here.

## Global Constraints

- Every rule is `constraints.md`'s, cited by ID. This plan changes no rule.
- **Widths** (minimums, `breakout.kicad_dru`):
  - `INLET` 1.0 mm;
  - `INLET_N12` 0.8;
  - `FAN` 0.8;
  - `NI_PWR` 0.6;
  - `ISO_PWR` 0.5;
  - `RAIL_BRANCH` 0.4;
  - `ANALOG` 0.25;
  - `USB` 0.2;
  - `Default` 0.2.
- **Partial-net widths that DRC can't see:** `+5V` from `D3` to its plane 1.0 mm (N-1);
  `-12V` from `D2` to its plane 0.8 mm (N-2).
- **Clearances and vias:** every class has 0.2 mm clearance and a 0.6 mm via on a 0.3 mm
  drill (N-11, N-14).
  - N-12: 0.6 mm on the outer layers between the ±15 V nets.
  - N-13: 0.6 mm on the outer layers from each of the 129 panel-connector nets to any
    other net (except between `U11`'s own pads).
  - S-5: 0.15 mm only inside `U55`, `U3` and `U4`.
  - N-16: no via in, or within 0.2 mm of, an SMD pad on any net. The exceptions are `U3`
    and `U4` pad 9.
- **Stack-up** (S-1, S-6, S-7): L1 signal, L2 (In1.Cu) the four grounds, L3 (In2.Cu) the
  rails, L4 signal.
  - Signal copper on In1.Cu is a cost, and the trial's better board had none.
  - Signal copper on In2.Cu is allowed only where a stage names it.
- **Prohibitions** (`routing-brief.md` §5, verbatim in substance):
  - no copper, via or pour in a slot, a slot keep-out or across a barrier line (B-5 to B-9);
  - `AGND`–`DGND` joined only at `NT1`; no flooding `FAN_RTN`, `CHASSIS_GND` or
    `ISO_5V_RTN` (G-1, G-3);
  - x = 170 crossed only by G-11 nets, once each (G-10);
  - no barcode-family net in, over or under `AGND` (K-2);
  - no pour on the §5 list of nets;
  - no via on `A_PD1_CLAMP`, `A_PD2_CLAMP` or `U63_VIN` (K-11, P-8);
  - no new DRC class: only the 24 `silk_edge_clearance` are accepted.
- **Must not:**
  - move a panel connector, a straddler (`U56`–`U62`, `U64`, `U65`, `U63`, `C162`,
    `U2`), a slot, `NT1` or `J1`;
  - change a net, a footprint's pads or the schematic.
- **Judge readings, accepted 2026-11-22:**
  - SHORT ≤ 1.5 × the octilinear MST;
  - matching within 1.0 mm (N-4, B-18, N-9, K-33, K-23);
  - G-10 corridor y 50 to 165, with no gap over 5 mm;
  - N-9 coupled for 80 % of its length within a 0.4 mm gap;
  - the rest as in `READINGS`.
- **Copper comes only from the route files.** Nobody edits copper in pcbnew. A hand fix is
  written into the stage module's `fixed()`, and the route file carries it (`route N`
  writes it there; route files are generated and never edited by hand, and `build`
  refuses one whose `fixed` differs from its module's).
- **Placement nudges** follow tasks J2 and J3:
  - move only a by-rule part (`placement.md` §7);
  - add it to `ANCHORS` in `gen_breakout_pcb.py`, with the task and the rule each new
    position meets;
  - placement check PASS, judge `FIXED` PASS against the previous main;
  - record it in `placement.md` §7.
- **Commits** end with the session's two attribution lines. Dates are the user's local
  date (`date`, America/Indianapolis). No push without the user.

## Review Focus

1. **Stale replay.** A route file recorded against an older placement is replayed after a
   nudge, and its copper lands on moved pads. The driver must refuse a route file whose
   `placement_sha256` differs from the build's (Task R1b test
   `test_replay_refuses_stale_placement`).
2. **Rules drift.** The engine's clearances come from `breakout.kicad_dru` and
   `breakout.kicad_pro`. If they are copied by hand, they drift when a rule changes (N-16
   was added yesterday). Task R1a's `test_rules_match_dru` parses both files, and the
   engine reads them at run time.
3. **Vacuous partial PASS.** On a partly routed board, a requirement check such as N-4's
   "both pins used" passes trivially when its nets carry no copper. The partial judge must
   say PENDING for it (Task R2 test `test_partial_requirement_pending`).
4. **Lost hand edits.** Someone fixes a track in pcbnew, and the next rebuild silently
   discards it. The driver must refuse to overwrite a committed board whose copper differs
   from the replay of its route files (Task R1b test `test_refuses_unreplayed_copper`).
5. **Non-deterministic builds.** Random uuids or zone-fill order make two rebuilds differ,
   so every rebuild shows a diff and reviews can't see what changed. Task R1b's
   `test_rebuild_is_byte_identical` builds twice and compares.

---

## File structure

```
hardware/gen/route/__init__.py
hardware/gen/route/rules.py       Rules.load(breakout_dir) -> Rules  (no pcbnew, no numpy)
hardware/gen/route/board_io.py    KiCad Python only: extract(), apply()
hardware/gen/route/grid.py        numpy: Grid, rasterisation, occupancy per clearance case
hardware/gen/route/search.py      numpy: route_connection()
hardware/gen/route/stage.py       Stage, RouteResult, the route-file schema
hardware/gen/route/stages/sNN_<name>.py   one per stage of routing-brief §3
hardware/gen/route_breakout.py    the driver (python with numpy; calls KiCad Python, kicad-cli)
hardware/breakout/routes/stage-NN.json    committed route files
hardware/breakout/routes/complete.nets    the stage contract (R2 fix round 1), each the union over
hardware/breakout/routes/touched.nets       the stages built: the nets completed, the nets with
hardware/breakout/routes/due.txt            copper, and the judge's checks/clauses that must PASS
tests/hardware/test_route_engine.py       numpy only; runs in CI
tests/hardware/test_route_breakout.py     needs KiCad; skipped in CI
```

Route-file schema (`stage.py`, written by the driver, sorted, 4 decimal places):

```json
{"stage": 3, "name": "star-inlet", "placement_sha256": "...", "rules_sha256": "...",
 "nets": ["P12_RAW", "..."],
 "tracks": [{"net": "P12_RAW", "layer": "F.Cu", "width": 1.0, "pts": [[170.0, 81.5], [172.0, 81.5]]}],
 "vias":   [{"net": "P12_RAW", "at": [172.0, 81.5], "size": 0.6, "drill": 0.3}],
 "zones":  [{"net": "DGND", "layer": "In1.Cu", "outline": [[x, y], "..."], "priority": 0,
             "connect": "thermal", "min_width": 0.25, "name": "DGND_L2"}],
 "fixed":  []}
```

`fixed` holds hand-specified tracks and vias, in the same shapes, which the stage replays
verbatim before it searches.

---

### Task R0: the board's mounting holes

The user's decision, 2026-11-22: "Use these 8"; then (5, 20), which lies under the CM5 IO Board (x 0 to 160,
y 0 to 90), moved to (165.5, 12) ("Move to (165.5, 12)"). The positions are proposals
measured on the placed board. At each, a 3.6 mm radius clears every courtyard by at least 0.3 mm, and none
is in the NI or Intan island.

**Files:**
- Modify: `hardware/gen/gen_breakout_pcb.py` (beside `CM5_HOLES`),
  `hardware/gen/check_breakout_placement.py`, `tests/hardware/test_breakout_placement.py`,
  `hardware/breakout/{breakout.kicad_pcb,breakout.kicad_pro,breakout.kicad_dru,drc.rpt}`,
  `constraints.md` (M-16's row: the positions and the user's decision),
  `floorplan.md` (the "Corner radius, mounting holes" row), `placement.md` §7,
  `routing-brief.md` §6

**Interfaces:**
- Produces: footprints `H3` to `H10`, unplated 3.2 mm holes with no net, at (165.5, 12),
  (212.5, 16.5), (425, 41.5), (83, 125.5), (287, 120), (100, 220.5), (300, 223.5) and
  (425, 225) mm. Each has a circular keep-out rule area `mh_H<n>` of radius 3.6 mm on all
  four copper layers, with no tracks, vias or pours; the radius is [engineering judgment],
  an M3 washer of 7 mm plus margin. R1b's `extract()` reads them like any other rule
  area.

- [ ] **Step 1: Write the failing test** `test_mounting_holes` in
  `tests/hardware/test_breakout_placement.py`. It asserts:
  - the eight footprints are at those positions (±0.01 mm), with no net and an unplated
    3.2 mm drill;
  - each has its keep-out;
  - no courtyard comes within 3.6 mm of a hole centre;
  - no hole lies in the NI or INTAN region, or under the IO Board outline
    (User.Comments, x 0 to 160, y 0 to 90).
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Add the holes and keep-outs to the generator**, and teach the placement
  check the same assertions. Then regenerate (`routing-brief.md` §1) and keep the J2 uuids
  (the transplant method). Expected:
  - placement PASS;
  - DRC 499 `unconnected_items` + 24 `silk_edge_clearance`, nothing else;
  - connectivity 1791;
  - the board diff shows only the holes and the keep-outs.
- [ ] **Step 4: Run the judge** with `--reference` set to the main version: `FIXED` PASS.
  Then run `python -m pytest -q`.
- [ ] **Step 5: Update the docs** listed above, and commit:
  `hw: eight board mounting holes with keep-outs (task R0)`.

### Task R1a: the routing engine

**Files:**
- Create: `hardware/gen/route/{__init__,rules,grid,search,stage}.py`,
  `tests/hardware/test_route_engine.py`
- Modify: `pyproject.toml` (a new extra `hw = ["numpy>=1.26"]`); `.github/workflows/ci.yml`
  (install `.[dev,hw]`); `wl.yaml` (`third_party` numpy, with a `why`; check with
  `/opt/anaconda3/bin/wl-check`)

**Interfaces:**
- Produces:
  - `Rules.load(breakout_dir: Path) -> Rules`
  - `Rules.width(net: str) -> float`
  - `Rules.clearance(a: str, b: str, layer: str) -> float`, outer-layer N-12/N-13 included
  - `Rules.via -> (0.6, 0.3)`
  - `Rules.panel_nets: frozenset[str]`
  - `Rules.sha256: str`
  - `Grid(window: tuple[float, float, float, float], pitch: float = 0.05)`
  - `Grid.add_obstacles(obstacles: dict)`, where the dict is `extract()`'s JSON (R1b)
  - `Grid.add_copper(result: RouteResult)`
  - `route_connection(grid, net: str, sources: list[Pad], goals: list[Pad], layers: tuple[str, ...], via_cost: float = 3.0, bend_cost: float = 0.2) -> Path | None`
  - `RouteResult` with `tracks`, `vias` and `zones`, and `to_json` / `from_json` in the
    schema above

- [ ] **Step 1: Write the failing tests** in `tests/hardware/test_route_engine.py`
  (`pytest.importorskip("numpy")`):
  - `test_rules_match_dru`: the N-13 net list equals the one in `breakout.kicad_dru` (129
    nets). `clearance(n, "DGND", "F.Cu") == 0.6` for `n` the first net of the N-13 list, and `0.2`
    on `In1.Cu`. `width("P12_RAW") == 1.0`.
  - `test_route_two_pads_straight`: two pads 10 mm apart on one layer, with no obstacle,
    give one path within 1.05× their distance and no via.
  - `test_route_respects_clearance`: a foreign pad across the line forces a detour, and
    every path cell lies at least clearance + width/2 from it.
  - `test_route_takes_via_when_blocked`: a wall across F.Cu forces exactly one layer change
    through a 0.6/0.3 via.
  - `test_n16_via_web`: no via is placed within 0.2 mm of an SMD pad's copper, including
    its own net's.
  - `test_deterministic`: the same inputs, routed twice, give equal `to_json()`.
- [ ] **Step 2: Run them and see them fail.**
  `/opt/anaconda3/bin/python -m pytest tests/hardware/test_route_engine.py -q`. Expected:
  ImportError on `hardware.gen.route`.
- [ ] **Step 3: Port the trial router into these modules.**
  - Read first, for ideas only: wl-shook's router (`~/GitHub/wl-shook/hardware/gen/shook_router.py`,
    read-only) and its `docs/lessons-for-future-projects.md`. Borrow only what fits:
    - its route-then-measure assertions (`assert_switching_nets_stayed_local`);
    - its power-first, shortest-first order.
    Do not import from it or copy its board-specific constants.
  - Rasterisation becomes numpy: a distance field per pad shape, thresholded per
    clearance case. Net ids are `uint16`, since there are 747 nets.
  - The A* stays a heap search. Its window is the pads' bounding box plus 5 mm, doubled on
    failure up to the whole board.
  - Clearances come only from `Rules`.
  - Copper is held 0.5 mm from the board edge and 0.25 mm from holes, as in the trial.
- [ ] **Step 4: Run the tests until they pass.** Same command. Expected: 6 passed.
- [ ] **Step 5: The NI island regression.** Do this in the scratchpad, not the repo.
  1. Build the NI island copy of the post-J3 board (as J2 did, staged as
     `breakout.kicad_pcb`).
  2. Write a scratch stage that routes its 144 connections and the two planes (NI_GND on
     In1.Cu, NI_5V on In2.Cu).
  3. Run the judge in island mode (`--nets`, `--region 0,100,70,240`).
  Expected:
  - judge exit 0;
  - N-16 0, signal copper on In1.Cu 0 mm;
  - wall-clock ≤ 5 min (the trial took about 15).
  Paste the judge's summary in the report.
- [ ] **Step 6: Commit.** `hw: routing engine (numpy) from the NI trial router (task R1a)`.

### Task R1b: the driver, route files and replay

**Files:**
- Create: `hardware/gen/route/board_io.py`, `hardware/gen/route_breakout.py`,
  `tests/hardware/test_route_breakout.py`
- Modify:
  - `hardware/gen/gen_breakout_pcb.py`: add `--out DIR`, which writes the three files there
    instead of `hardware/breakout/`.
  - `hardware/gen/kicad_pcb.py`: add `seed_uuids(seed: str)`, so that `uid()` gives
    `uuid5` values from a counter. Only `gen_breakout_pcb.py` calls it, with
    `seed="breakout"`. `gen_mule_pcb.py` is unchanged.
  - `hardware/breakout/routing-brief.md` §1 and §5: the "never run `gen_breakout_pcb.py`
    again" rule becomes "the generator writes only to the build directory; copper comes
    only from `routes/`; rebuild with the driver". Every other line stays.

**Interfaces:**
- Consumes: R1a's `Rules`, `Grid`, `RouteResult`.
- Produces:
  - `board_io.extract(board: Path, out_json: Path) -> None`, run in KiCad Python. It
    writes pads (shape polygon, net, layers, SMD flag), holes, the outline, keep-out rule
    areas and slots.
  - `board_io.apply(placed: Path, route_files: list[Path], out: Path) -> None`, run in
    KiCad Python. It adds tracks, vias and zones, fills every zone and saves.
  - CLI: `route_breakout.py build [--through N]`, which rebuilds from the route files with
    no search.
  - CLI: `route_breakout.py route N`, which searches stage N on top of stages before N,
    writes `routes/stage-NN.json`, then builds.
  - Both commands write `hardware/breakout/breakout.kicad_pcb`, `routes/routed.nets` and
    print the connectivity count over the routed nets and over the whole board.
    *(Amended in R2 fix round 1, 2026-11-24: `routed.nets` listed every touched net, zones
    included, which made a plane net look complete. It is retired for `routes/complete.nets`,
    `routes/touched.nets` and `routes/due.txt`; see the stage interface below.)*
  - Build directory: `build/route/` (git-ignored).

- [ ] **Step 1: Write the failing tests** in `tests/hardware/test_route_breakout.py`
  (skipped without KiCad):
  - `test_build_with_no_stages_is_placement`: `build --through 0` gives a board whose
    connectivity count is 1791, and whose footprints equal the generator's.
  - `test_rebuild_is_byte_identical`: two `build` runs produce identical bytes.
  - `test_replay_refuses_stale_placement`: change one anchor, and `build` exits 1 naming
    the stale stage file.
  - `test_refuses_unreplayed_copper`: add a track to the committed board by hand, and
    `build` exits 1 without writing.
  - `test_route_file_roundtrip`: a hand-written one-track stage file is replayed, and the
    track is in the board with its net, layer and width.
- [ ] **Step 2: Run them and see them fail.**
  `python -m pytest tests/hardware/test_route_breakout.py -q`.
- [ ] **Step 3: Implement** `--out`, `seed_uuids`, `board_io` and the driver.
  - Regenerate once with seeded uuids. This changes every uuid on the board, and only
    uuids.
  - Prove it: placement check PASS; DRC 499 + 24 `silk_edge_clearance`; connectivity
    1791; `--schematic-parity` 30 warnings and no error, as before.
- [ ] **Step 4: Run the tests until they pass**, then the whole suite with
  `python -m pytest -q`.
- [ ] **Step 5: Commit.**
  `hw: route driver, route files, replayable breakout build (task R1b)`.

### Task R1c: router bake-off on the NI island

The user's decision, 2026-11-22: "Yes, all three". Our engine (R1a), Quilter and DeepPCB
each route the NI island of the post-J3, post-R0 board, and the judge grades them. The
result decides which router carries which stages. Possibly a mix: an external router for
the ordinary nets of R12, with ours for the constrained stages.

**Files:**
- Create: `hardware/breakout/bakeoff/` holding:
  - `ni-island/`, the package: `breakout.kicad_pcb` (the island copy), `breakout.kicad_pro`,
    `breakout.kicad_dru`, `fp-lib-table`, `sym-lib-table`, the island's footprints, and
    `RULES.md`, the island's rules in plain words for a tool's own rule entry;
  - `results/<tool>/`, each tool's returned board;
  - `bakeoff-report.md`.

**Steps:**
- [ ] **Step 1: Build the package.** Check that it opens with `kicad-cli pcb drc` in place,
  with the rules loaded, and that connectivity is 144.
- [ ] **Step 2: The user creates the Quilter and DeepPCB accounts.** The controller uploads
  the package to each and downloads every candidate it returns.
  - API keys are never printed or committed.
  - Each tool gets the same package and the same plain-words rules, entered as far as that
    tool's own rule entry allows. What it could not take is recorded.
- [ ] **Step 3: Grade every candidate**, and R1a's own island result, with the judge in
  island mode (`--nets`, `--region 0,100,70,240`). The report compares:
  - every gate and check;
  - the metrics table of the stage protocol;
  - wall-clock and cost per candidate.

  The tool names stay hidden until the scoring is done, as in the model trial.
- [ ] **Step 4: Recommend which router carries which stages**, with the evidence, for the
  user's decision. If an external router is chosen, the plan gains a task wiring its
  returned board into route files (an importer to `stage-NN.json`), so that replay and
  determinism still hold.

### Task R2: the judge's partial mode and the accepted readings (folds J4)

**Files:**
- Modify: `hardware/gen/judge_layout.py`, `tests/hardware/test_judge_layout.py`,
  `hardware/breakout/routing-brief.md` §7 ("The automatic judge")

**Interfaces:**
- Consumes: `routes/routed.nets` (R1b).
- Produces: `judge_layout.py BOARD --routed-nets FILE`, and a status `PENDING`. These
  keep their meanings:
  - exit 0: gates and checks PASS, N/A or PENDING, or named waivers;
  - exit 2: a bad argument.
- *Amended in fix round 1 (2026-11-24):* the judge takes the stage contract,
  `--complete-nets routes/complete.nets` (`--routed-nets` is kept as its old name) with
  `touched.nets` and `due.txt` beside it; see the stage interface under "The stage tasks".

- [ ] **Step 1: Write the failing tests:**
  - `test_partial_connectivity_counts_listed_nets`: on the placed board, with one net
    routed and listed, CONNECTIVITY passes, and the whole-board count is still printed.
  - `test_partial_drc_accepts_only_unlisted_unconnected`: an `unconnected_items` entry
    between two pads of a listed net FAILs DRC; between unlisted nets it is accepted.
  - `test_partial_requirement_pending`: with N-4's nets unlisted, N-4 is PENDING, not
    PASS.
  - `test_partial_prohibition_still_fails`: a track across x = 170 on a listed non-G-11
    net FAILs G-10.
  - `test_pending_not_allowed_without_routed_nets`: without the flag, no check may print
    PENDING.
  - `test_readings_accepted`: every key of `READINGS` is in `ACCEPTED_READINGS`, dated
    2026-11-22. The summary prints `[reading accepted 2026-11-22]`, and the JSON carries
    `"reading_accepted": "2026-11-22"`.
- [ ] **Step 2: Run them and see them fail.**
  `<kicad python> -m pytest tests/hardware/test_judge_layout.py -q -k "partial or readings"`.
- [ ] **Step 3: Implement.**
  - Classify every layer-2 check as a *prohibition*, which is judged on the copper present,
    or a *requirement*, which is PENDING until all its nets are listed.
  - Record the class beside each check, and print it.
- [ ] **Step 4: Run the judge tests and the suite until they pass.**
- [ ] **Step 5: Document** `--routed-nets` and PENDING in `routing-brief.md` §7, with one
  line on the readings' acceptance.
- [ ] **Step 6: Commit.**
  `hw: judge partial mode (--routed-nets, PENDING); readings accepted 2026-11-22 (task R2)`.

---

## The stage tasks (R3 to R13)

Every stage task follows the same protocol. Only the table row below differs.

**Files:**
- Create: `hardware/gen/route/stages/sNN_<name>.py`, `hardware/breakout/routes/stage-NN.json`
- Modify: `hardware/breakout/breakout.kicad_pcb`, `hardware/breakout/drc.rpt`,
  `routes/complete.nets`, `routes/touched.nets`, `routes/due.txt`; on a nudge,
  `gen_breakout_pcb.py` `ANCHORS` and `placement.md` §7

**Interfaces:** a stage module defines:
- `STAGE: int`, `NAME: str`;
- `nets(board_nets: list[str]) -> list[str]`;
- `zones() -> list[dict]`;
- `layers(net: str) -> tuple[str, ...]`;
- `order(nets) -> list[str]`;
- `fixed() -> list[dict]`;
- optionally `route(ctx)`, to route its nets itself;
- `COMPLETE`, a list or a function `complete(nets)` of the nets the stage touched: the nets
  this stage completes, every pad joined. The default is the nets the stage routes with
  tracks or vias of its own (the default loop, `route(ctx)` or `fixed()`); a zone never
  completes a net. A stage that only extends a net (R4 joining `D3` to the `+5V` plane)
  declares it, leaving that net out;
- `DUE`: exactly the IDs of the row's "The judge must PASS" column, as check IDs (`S-7`)
  or check/clause keys (`G-1/overlap`; the judge prints each clause's key).

*(The contract amended in R2 fix round 1, 2026-11-24.)* The driver records `COMPLETE` and
`DUE` in the route file and writes `routes/complete.nets`, `routes/touched.nets` (every net
with copper) and `routes/due.txt`, each the union over the stages built. The judge, run as
`judge_layout.py BOARD --complete-nets routes/complete.nets` (touched.nets and due.txt read
beside it): CONNECTIVITY and DRC's `unconnected_items` count the complete nets only; a due
check or clause is judged on the copper present and anything but PASS fails the run; a
requirement that is not due waits, PENDING, until its nets are complete; prohibitions are
always judged on all the copper; copper on a net in neither list fails. A due item stays due
at every later stage. R13 runs the judge with no flags, so nothing can stay PENDING.

The driver does the rest. Later stages see earlier copper only as obstacles. They never rip
it up.

- [ ] **Step 1: Write the stage's acceptance test** in
  `tests/hardware/test_route_breakout.py`, named `test_stage_NN_<name>`. It asserts, on the
  committed board:
  - the stage module's `DUE` is exactly the row's "The judge must PASS" column;
  - the connectivity over `routes/complete.nets` is 0;
  - the judge with `--complete-nets routes/complete.nets --allow-not-checked W` exits 0,
    which holds every entry of `routes/due.txt` at PASS (a due item that is anything else
    fails the run). W is `G-1` for R3 to R8, and `G-1,K-23` for R9 to R12 (fix round 2):
    - G-1, because its mounting-hardware clause is NOT CHECKED on any board (its plane
      clause, `G-1/overlap`, is due from R3);
    - K-23, from R9 on, because R9 completes K-23's nets, and its `K-23/block` (a 2-gang
      block moved by hand) is then judged, and is NOT CHECKED on any board.

    R13 keeps its own list, what routing-brief §7 names;
  - the row's metric bounds hold.
- [ ] **Step 2: Run it and see it fail** (no stage file yet).
- [ ] **Step 3: Write the stage module**, then run `route_breakout.py route NN`. Iterate
  until the test passes.
  - If a net can't be routed, nudge by-rule parts under the nudge protocol in Global
    Constraints.
  - If only a fixed part or a rule change would free it, stop with `BLOCKED`, naming the
    net, the part and the rule.
- [ ] **Step 4: Run the full judge in partial mode and the suite.** Paste into the report:
  - the gates;
  - every check's status;
  - the metrics table: vias; signal mm on In1.Cu and In2.Cu; plane islands and narrowest
    neck; routed length / MST; vias in pads.
- [ ] **Step 5: Run `route_breakout.py build` twice and confirm the bytes are identical.**
  Then commit the stage module, the route file, the board, `drc.rpt` and the three contract
  files:
  `hw: route stage NN <name> (task RK)`.

The "The judge must PASS" column is the stage module's `DUE`, verbatim (amended to clause
level in R2 fix round 1, so that every row can pass).

| Task | Stage (§3) | Nets and rules | The judge must PASS | Bounds |
|---|---|---|---|---|
| R3 | 1–2, fences and planes | every plane net, on In1.Cu (the four grounds, S-6) and In2.Cu (rails, S-7); G-9 | `S-7` (all three clauses: layer, region, and every plane poured), `G-1/overlap`, `G-3` | each plane fill one piece; narrowest neck ≥ 2.0 mm [engineering judgment; the trial's better board had 2.5] |
| R4 | 3, star and inlet | G-1 to G-8, G-14, G-15, P-1, P-2, N-1 to N-3; `P5_RAW`, `FAN_12V_RAW` across the split | `G-1/overlap`, `G-3`, `G-4`, `G-5`, `G-6`, `N-1`, `N-2`, `G-10/foreign`, `G-10/once`, `G-10/stretch` | nothing narrower than 2 mm joins `NT1` (G-8, by measurement in the report) |
| R5 | 4, the barrier parts | B-12, B-13, P-3, P-7 to P-10, K-34, K-35, G-6; J2's and J3's escapes | `B-13`, `B-13-ALL`, `K-35/joined`, `P-8` | vias in pads 0; every opto output by its J2 window |
| R6 | 5, the Intan supply chain | P-4 to P-6, `ISO_PWR` nets, N-12 | P-5, P-8 | P-6's vias under `U3`/`U4` pad 9 present |
| R7 | 6, the barcode family | K-1 to K-10 | K-2, K-4 | each barcode net ≤ 1.5× MST |
| R8 | 7, the split crossings | G-10, G-11 (46 nets), B-17, B-18 (16 mux/sense traces) | `G-10` (whole: G-11's list is its `G-10/all-cross` clause, every G-11 net crossing), `B-16`, `B-18` | one corridor; every G-11 net crosses once |
| R9 | 8, the analog front ends | K-11 to K-23; guard rings; the BNC pairs | `K-23/length`, `K-23/vias`, `K-33` | no via on `A_PD*_CLAMP` |
| R10 | 9, the analog distribution | K-18 to K-22, G-16, G-17; the 16 `A_*` nets, AISENSE, audio | `G-16`, `G-17` | |
| R11 | 10, comparators, thresholds, I²C, USB | K-24 to K-31, N-9 | `N-9` | USB pair matched within 1.0 mm, coupled 80 % |
| R12 | 11, the remaining digital nets | K-32 to K-37, G-23; the `J3` and `J45` escapes | `N-4` (`K-2` is due from R7 and stays due) | record the K-32 buffer lengths (Open question 9) |
| R13 | 12–13, rails, fan, silkscreen, final | N-6, G-24, N-3, P-14, M-20; then `routing-brief.md` §7 items 1 to 6 | the judge **without** `--complete-nets`: exit 0 with `--allow-not-checked` limited to what §7 names | connectivity 0; DRC 0 + 24 `silk_edge_clearance`; parity 30 warnings and no error; renders for V-4 |

Notes on the column (R2 fix round 1):

- G-1 is never due whole: its mounting-hardware clause is NOT CHECKED on any board, so
  every stage run waives G-1 (`--allow-not-checked G-1`), and its plane clause
  `G-1/overlap` is due from R3.
- R5's K-35 is `K-35/joined` only. Its other clause, `K-35/other-part` (U65, an ACSL-6420,
  whose isolated supply pins K-35 does not name and no source pairs), is NOT CHECKED once
  judged. It is not due, and its nets (the opto supplies, plane nets) are complete only on
  the final board, so it stays PENDING through R12 and needs no waiver there. R13 waives
  K-35 by name (`--allow-not-checked G-1,K-23,K-35,N-10`, routing-brief §7).
- R9's K-23 leaves out `K-23/block` (a 2-gang block moved by hand), NOT CHECKED on any
  board. Once R9 completes K-23's nets the clause is judged, so R9 to R12 waive K-23 with
  G-1 (Step 1), and R13 waives it in its own list.
- R9's K-33 is whole: its joined clause is written when it holds as well as when it fails
  (fix round 2), so `K-33` due can PASS.
- A due requirement holds for every net it names: one with no copper FAILs it (fix round 2),
  so a row's DUE is met only when the stage has routed all of that check's nets.
- G-10 at R4 is its three prohibitions; at R8 it is whole, the corridor and every G-11
  crossing included, on the copper present.

R13 also updates `placement.md` (every nudge), the `hardware/README.md` counts, and
`routing-brief.md` §6 (closed items). It records which "by eye" items of §7 item 5 the
judge now covers and which still need the user's one look before fab. It does not sign the
board off.

**Before R12, the user decides** the fan headers `J54`/`J55`, `floorplan.md` Open question 6:
route a 160 mm `FAN` pair across the barcode strip, or move the headers. The controller asks
when R11 completes.
