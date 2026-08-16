# Review brief — the F6 connector change

For whoever reviews commit `693c919` before layout. Written by its author, so read it as a
list of places to attack rather than as reassurance.

**Why this one wants a person.** The change touched five generators, and it introduced two
defects that ERC, all 13 checkers and all 121 tests passed straight through — they surfaced
only as a warning line on `kicad-cli`'s stdout. Both are fixed, but their existence is the
argument: **this change is in a class the automated suite demonstrably does not cover.**
Footprint geometry and pad-role assignment are invisible to a netlist by construction.

**Time budget.** Questions 1 and 2 are the ones worth real time. Everything from Q3 down is
ordinary code review. §4 lists what is already verified so you can skip it.

---

## 1. Is pad 1 the centre conductor, or the shell?

**This is the highest-stakes assumption in the change, and it is silent.**

The dual BNC footprint assigns:

| Pad | Role I assigned | Position |
|---|---|---|
| 1 | port A **centre conductor** | (0, 0) |
| 2 | port A **shell** | (−2.54, 0) |
| 3 | port B **centre conductor** | (0, −2.54) |
| 4 | port B **shell** | (+2.54, 0) |

**My reasoning chain, stated so you can break it.** The drawing gives 4 × Ø0.89 mm signal
holes and 2 × Ø2.01 mm ground holes, and their positions — but *not* which hole is which
role. So the roles are inferred, from two stock KiCad footprints drawn independently from two
manufacturers' drawings:

- `BNC_Win_364A2x95_Horizontal` numbers **only the two centreline holes** — (0,0) and
  (0,−2.54) — as independent pins 1 and 2, and lumps *everything else* (both ±2.54 side holes
  and both Ø2.01 posts) into a single pad "3". For a dual coax, "two independent things plus
  one common thing" reads as **two centre conductors plus a common ground**.
- `BNC_Amphenol_031-6575_Horizontal` numbers all four 1–4 independently but states no roles.

So the centreline→centre-conductor inference rests **entirely on the Winchester footprint's
grouping**. That is one piece of evidence, from a third party, about a different
manufacturer's part in the same family.

**If it is wrong**, all 31 channels drive their coax *shield* and ground their *centre* —
clean netlist, clean ERC, and a board that does nothing correct on the bench. Cost: respin.

**How to falsify, cheapest first:**
1. The 3D model or drawing — the centre contact is the one **collinear with the barrel
   axis**. The barrel is on the centreline, so its contact should be too. This is the check
   that also confirms my inference rather than merely restating it.
2. Continuity on a sample: centre pin of the mating face to each of the four tails.

---

## 2. Which shell belongs to which port?

Lower stakes than Q1, same silence.

I paired **pad 2 (left, −2.54) with the port whose centre is (0,0)**, and **pad 4 (right,
+2.54) with the port at (0,−2.54)**. Nothing I checked establishes that pairing — it is the
symmetric-looking choice, and the alternative (crossed) is equally consistent with every
document I have.

**If it is crossed**, the two shields swap *within* each body. That matters only on the five
bodies carrying individually-routed shields:

| Body | Pad 2 shield | Pad 4 shield |
|---|---|---|
| J20 | `A_PD1_SHLD` | `A_PD2_SHLD` |
| J22 | `A_MIC_SHLD` | `A_AMB_SHLD` |
| J24 | `A_ACC_SHLD` | `A_JOY_X_SHLD` |
| J26 | `A_JOY_Y_SHLD` | `A_MISC1_SHLD` |
| J29 | `A_MISC2_SHLD` | `A_MISC3_SHLD` |

It is **not a short** — each shield still reaches its own 10 Ω to AGND, and the two are
electrically identical networks. What it costs is the *per-channel granularity* the deferred
bulkhead-bonding scheme exists for: channel A's coax shield would carry channel B's shield
reference. On the other 21 BNCs (`INTAN_GND` ×14, `DGND` ×7) a swap is a no-op.

**Same falsification as Q1** — continuity, or the drawing.

**Worth deciding explicitly:** is this worth holding fab for, or is it acceptable to bond all
ten at bring-up and lose the granularity? That is a design call, not a review finding.

---

## 3. The 18 mm BNC column pitch is mine, and unsourced

`panel-elevations.md` §2 sets the panel column pitch at **18.0 mm**, and derives it from a
1/2-28 UNEF hex nut being "roughly 16.5 mm across corners". **I did not source that nut
dimension from any drawing.** It is the one number in the panel work that is not traceable to
`datasheet-params.toml`, and this project's whole discipline is that such numbers are how you
get a 0.5 mm drill.

It drives the front and back face widths (140.4 mm and 158.4 mm), and both faces have only
~25–28 mm of total slack across 450 mm — so a 2 mm error in pitch consumes most of the
margin on the back face.

**Check:** measure the supplied nut, or take the across-corners dimension off the 3D model.
If it is larger than 16.5 mm, the pitch must grow and the back face gets tight.

---

## 4. Ordinary code review — `hardware/gen/bnc_dual.py`

- **`_set_value()` rewrites `sch.body[index]` by position.** It records `len(sch.body) - 1`
  after `sch.place()` and edits that entry later. Safe only while `place()` appends **exactly
  one** entry per call. Is that invariant worth asserting rather than assuming?
- **`take()` deliberately burns a refdes** (calls `next_ref("J")` and discards it) to stop
  every later J reference shifting. Consequence: holes at J6*, J14, J16, J21… Is a hole
  preferable to a renumber here? (The project's own rule says never renumber; this respects
  it at the cost of a confusing sequence.) *\*J6 exists again — see Q6.*
- **`take_spare()` consumes nothing**, and this distinction is exactly what the second bug
  was. Does the asymmetry between `take()` and `take_spare()` read clearly enough that the
  next person won't reintroduce it?
- **`for_sheet()` stashes the allocator on the `Sch` object** via `setattr`. Fine while one
  generator builds one sheet per process. Does anything in this codebase build two?

## 5. The new checker — `check_footprint_geometry.py`

- `MAX_PANEL_CONTACT_DRILL_MM = 3.0` is a judgement, not a datasheet number. The largest
  legitimate numbered drill on the board is 2.01 mm (the BNC ground terminals).
- It **excludes** non-numeric pad numbers (the DB37's `SH` shell pads) and the whole
  `MountingHole` library. Both exclusions are defensible — a shell pad and a mounting hole are
  legitimately M3-sized and say nothing about which way a connector points. **But could that
  exclusion hide a real perpendicular-axis part?** The floor assertions (`inspected >= 20`,
  `contacts_seen >= 200`) are what stop it going vacuous; are they high enough?

## 6. The reward split — does it match the spec's intent?

`J5` and `J6` are the only two BNCs that do **not** share a body with a sibling. Spec §9.6
puts the reward remote on the **front** and the driver output on the **back**, and a dual body
is one piece of plastic through one panel. So each got its own body with a wired spare port —
that is the 17th body, against the 16 a pure per-sheet pairing gives.

**Check the reading of §9.6, not the implementation.** If the split is not actually required,
this is one connector and two panel holes of waste.

---

## 7. Already verified — you should not need to re-check these

Evidence given so you can spot-check rather than repeat:

| Claim | Evidence |
|---|---|
| No BNC signal net was lost or double-driven | All **31** pre-F6 BNC signal nets reach exactly one centre pad; **34** centre pads in use (31 signals + 3 spare no-connects), none double-driven |
| Net count change is fully accounted | 734 → 737, added: `unconnected-(J5B/J6B/J17B-B_In-Pad3)` — the three spare ports' no-connects. **Zero nets removed.** |
| All ten individual shields stayed distinct | J20/J22/J24/J26/J29 each carry two differently-named `*_SHLD` nets on pads 2 and 4 |
| No refdes was renumbered | Netlist contract diff vs `e5cc46f` shows no reference moved |
| Footprint matches the drawing | `check_footprint_geometry.py` — 4 checks, 6 negative controls, reading the real `.kicad_mod` off disk |
| Board is idempotent | `netlist-contract.json` byte-identical across two full regenerations |
| Committed state builds clean | Verified from a fresh `git clone`, not the working tree |

**Reproduce:**

```bash
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"
for g in power taskpc_digital pi_interface analog_frontend analog_ni \
         mux_intan comparators opto_ni opto_intan control_usb_i2c; do
    python3 hardware/gen/gen_breakout_$g.py >/dev/null || echo "GEN FAIL $g"
done
kicad-cli sch export netlist --format kicadsexpr \
    -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
cp hardware/breakout/breakout.net /tmp/breakout.net          # two paths, still
python3 hardware/gen/gen_breakout_netlist_contract.py
for c in hardware/gen/check_*.py; do python3 "$c" >/dev/null || echo "FAIL $c"; done
python3 -m pytest tests/ -q
kicad-cli sch erc -o hardware/breakout/erc.rpt hardware/breakout/breakout.kicad_sch
```

Expected: **13/13 checkers, 121 tests, ERC 0 errors / 3 pre-existing warnings.** Watch
`kicad-cli sch export netlist`'s stdout — **"schematic has annotation errors" is a failure**,
not noise. It was the only signal for both bugs this change introduced.

---

## 8. What is explicitly out of scope

- **The MDR68 tail-row map.** Different change, tracked in `d3-panel-thickness.md`, and its
  consequence is a fit failure rather than silent corruption — all four candidates produce
  different hole patterns, so a wrong choice will not seat.
- **Whether the 031-6575 is the right part.** Settled: the drawing title is verbatim
  *"50 OHM DUAL PORT BNC R/A JACK, ISOLATED BLACK"*, corroborated by Newark and element14
  both listing it as a right-angle **dual** jack.
- **Panel thickness.** Decided at 3.0 mm in `d3-panel-thickness.md`.

## 9. If you find something

Same rule as `design-review.md`: if it is a real defect rather than a residual risk, **stop
and scope a fix as its own task** rather than patching it inside a review. Questions 1 and 2
are the two that could carry that weight.
