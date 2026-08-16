# Handoff — the connector and panel work (audit step 5)

Written 2026-08-16 at the end of the session that implemented every **electrical** finding
in `parametric-audit.md`. For whoever does the mechanical ones.

**Read first:** `parametric-audit.md` (each finding now carries an IMPLEMENTED block saying
what actually shipped and how it differed from the finding), `audit-handoff.md` (the audit
session's own handoff, now annotated with what came true), and `../datasheet-params.toml` —
which already holds **every dimension you need**, read from the real drawings.

This file records only what is *not* written down there: current state, traps found the
hard way, and why the remaining work is verified differently from everything before it.

---

## 1. Where things stand

Closed: **F1, F2, F3, F4, F5, F7, M1, M2, M3, M4, M7, D1**, plus a board-wide generator fix
and three spec updates. What remains is **F6, D3 and the footprint/panel work** — all
mechanical.

Baseline to match before you start (run it; if it does not reproduce, something drifted):

```bash
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"
for g in power taskpc_digital pi_interface analog_frontend analog_ni \
         mux_intan comparators opto_ni opto_intan control_usb_i2c; do
    python3 hardware/gen/gen_breakout_$g.py >/dev/null || echo "GEN FAIL $g"
done
kicad-cli sch export netlist --format kicadsexpr \
    -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
cp hardware/breakout/breakout.net /tmp/breakout.net      # SEE §2
python3 hardware/gen/gen_breakout_netlist_contract.py
for c in hardware/gen/check_*.py; do python3 "$c" >/dev/null || echo "FAIL $c"; done
python3 -m pytest tests/ -q
kicad-cli sch erc -o hardware/breakout/erc.rpt hardware/breakout/breakout.kicad_sch
```

Expected: **12/12 checkers, 120 tests, ERC 0 errors / 3 warnings.** The three warnings are
pre-existing `isolated_pin_label` notes on `USB_VBUS_PI` and the two unpopulated
opto-intan spare nets. They have been there throughout; do not "fix" them without deciding
the spares' fate first.

**Board-wide refdes maxima**, which you need before minting anything:

| C | D | F | FB | J | NT | R | TP | U |
|---|---|---|---|---|---|---|---|---|
| 158 | 44 | 4 | 3 | 56 | 4 | 208 | 7 | 75 |

---

## 2. What changed about the workflow, and what did not

**Fixed: the refdes bomb.** `audit-handoff.md` §1 warned about a stale-`ref_start` bug. It
was worse than described — seven of ten generators rewrote their entire refdes space on a
plain re-run, because `ref_start` was seeded by reading sibling sheets live and
`taskpc-digital` had gained out-of-band refs. **All ten generators are now pinned.** The
board is idempotent: regenerate every sheet twice and `netlist-contract.json` comes out
byte-identical. That is now a property worth protecting — if you add a sheet, pin it.

**Still broken: the two paths.** The checkers default to `/tmp/breakout.net`; the contract
generator defaults to `hardware/breakout/breakout.net`. Hence the `cp` above. This is still
worth fixing properly and nobody has.

**Do not verify by diffing `.kicad_sch`.** Several sheets were last written by KiCad rather
than by their generator, so regenerating reformats the whole file even when nothing
electrical changes. **`netlist-contract.json` is the only signal that means anything.** A
change that leaves it byte-identical changed nothing.

**The BOM is now checked.** `tests/hardware/test_bom_matches_netlist.py` compares both CSVs
against the netlist snapshot — every reference present, no strays, every value matching. It
was added at the end of this session because the BOMs are hand-maintained and had been kept
correct by running that comparison manually. **They will need updating for every connector
you change**, and now the suite tells you.

---

## 3. What step 5 actually is

**Every dimension is already pinned** in `../datasheet-params.toml`, read from the
manufacturer drawings during the audit. Do not re-source them; do not take them from
distributor pages. `[mdr68]`, `[m12_inlet]`, `[bnc_dual_isolated]`.

### F6 — 31 BNCs on a footprint pointing at the chassis lid

`BNC_PanelMountable_Vertical` puts the connector axis **perpendicular to the PCB**, with a
9.65 mm drill and 15.24 mm annular pad per position. On a 430 × 240 mm board lying flat in
a 2U chassis those point at the lid, not at the panels where all 31 are supposed to emerge.
It also costs ~5,650 mm² of pad and ~2,270 mm² of removed copper *on every layer*, punched
through a mixed-signal board carrying five ground domains.

Needs a right-angle isolated part. `[bnc_dual_isolated]` pins the Amphenol 031-6575 (dual,
right-angle, isolated) with its full PCB and panel geometry.

> **Footprint trap, confirmed:** KiCad's own `BNC_Amphenol_031-6575_Horizontal` is
> **self-contradictory** — described as dual, it draws a single body with one bayonet circle
> and four clustered pads. Do not use it. `BNC_Win_364A2x95_Horizontal`'s geometry matches
> the Amphenol drawing's real pattern (4 × Ø0.89 signal, 2 × Ø2.01 ground).

### MDR68 — the as-built footprint cannot be fabricated

`wl-sync:MDR68_Male_RightAngle` has a **0.5 mm drill against a required 0.85 mm** — the pins
do not fit — plus 2 rows at 2.84 mm against a 4-row staggered arrangement at 1.905 mm, and
no mounting holes at all. Rebuild from MH drawing 3700-0121-01 rev 3.0; every dimension is
in `[mdr68]`.

### M12 — delete the footprint

R1 is ruled: the Phoenix 1551833 is a **panel-mount part with flying leads**, not a
board-mount one, so `M12A_5_Panel.kicad_mod` should be **deleted** and the board given a
5-way header. That also retires one of the three custom footprints. As built, every cable
insertion and every 2 N·m tightening loads PCB pads directly — a mechanical defect
independent of the footprint's dimensions.

### D3 — panel thickness still unconfirmed

| Connector | Panel thickness | Source |
|---|---|---|
| M12 inlet | max 3.5 mm | Phoenix drawing 00662206 Index 2 |
| MDR68 | **not stated** — panel mounting is #2-56, 2 places | MH drawing rev 3.0 |
| BNC | **unknown** | pending |

A 2U rack panel is commonly 2–3 mm aluminium. This needs settling **by asking MH and
Amphenol**, not by assuming — the original 2.00 mm figure came from a 3M part that was
evaluated and not selected.

### Panel elevations

Numbers are worked out in `audit-handoff.md` §4 and hold: the 031-6575's ports are 16.00 mm
apart **stacked vertically**, so two rows of duals fit a 2U face (58 mm of 82 mm usable),
halving the horizontal span. Front ≈ 358 mm, back ≈ 273 mm, against ~450 mm usable between
rack ears. At 16 mm centres adjacent mating plugs nearly touch — normal for stacked duals,
but plugs mate one at a time.

---

## 4. Traps

**This project has already shipped a wrong custom footprint once.** `build_minidin()` drew
an even contact ring where the manufacturer's diagrams show a **clustered** layout — a
topology defect that "would very likely fail to mate as fabbed" (see
`gen_wl_sync_footprints.py`'s own docstring). It was caught in review, not by a tool. That
is the precedent for how carefully the MDR68 and BNC footprints need drawing: **the netlist
cannot see any of this.** A footprint with the wrong drill, the wrong pitch or the wrong
axis produces a perfectly clean netlist, passes ERC, and passes all 120 tests.

**Negative controls go vacuous when nets move — this happened five times this session.**
Twice they raised `StopIteration` (the lucky version) and three times they silently stopped
testing anything, caught only because a sibling assertion complained. **When a change
relocates or renames a net, grep the self-tests for its old name before assuming they still
bite.** Two of them were pinned to complaint *text* (`"expected '430'"`,
`"expected exactly 4 nodes"`) and broke when the number they quoted changed; assert on the
invariant part of a message, never on a value the change is about to move.

**The audit is not always right, and it is worth checking.** Implementing it corrected it
five times: F7's fix named the wrong node (`P12_FUSED` is `F2`'s *output*, so the published
fix would have fixed nothing and removed protection); M7's "0.75 A recommended" fan fuse
**does not exist** in a 12 V-rated 1206L part; F5's named `TMA-0505S` is **unregulated** and
would have broken F1; M3 counted six bare panel outputs where there were **eleven**; and
D1 had no schematic component at all. The findings' *diagnoses* were sound every time. Their
*prescriptions* were not. Verify against the primary document before implementing.

**`datasheet-params.toml` is primary-sources-only, and that rule earns its keep.** F5 was
gated on pinning a part, and pinning it is exactly what revealed the recommendation was
wrong. If a datasheet will not come out of the vendor's own site (Traco and Littelfuse both
403), a distributor-hosted copy of the *manufacturer's* document is acceptable; a
distributor's own spec table is not.

---

## 5. Suggested order, and why

1. **M12 first.** Deleting `M12A_5_Panel` and dropping in a 5-way header is the smallest
   change, it retires a custom footprint rather than creating one, and it is fully ruled.
2. **MDR68 next.** It is the only fab-stopper (a 0.5 mm drill for a 0.85 mm pin), the
   geometry is completely pinned, and it does not interact with the panel layout.
3. **BNC last**, because it is the one with an open question (panel thickness) and the
   largest knock-on: 31 positions, a footprint to draw from scratch, and the panel
   elevations depend on it. Get the vendor answer moving early even though the work is last.
4. **Then the remaining checkers** — the audit's §5 rows for F6 and the connectors:
   footprint axis against the face each connector is assigned to, and pad coverage.

`check_breakout_analog_frontend_netlist.py`'s own `verify_footprint_pad_adjacency()` is the
precedent for a footprint-geometry check that reads the real `.kicad_mod` from disk rather
than trusting the generator. Follow it.

---

## 6. The one-line lesson from the electrical half

Every finding it corrected had the same shape: **a number that was right about the model and
wrong about the board.** 430 Ω solved for a rail that never reached 5.00 V. 3.3 pF was
optimal for a photodiode capacitance the design never operates at. A checker reported a
starved channel healthy because it counted the anode resistor and not the 100 Ω on the other
side of the LED. An exception was correct when written and invalidated by a change made two
sheets away for an unrelated reason.

The mechanical half is the same problem with different units. A footprint is a number that
is right about a part and wrong about the one you are buying. **Check the drawing.**
