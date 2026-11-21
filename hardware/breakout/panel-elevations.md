# Panel elevations — redrawn for finding F6

Redrawn 2026-08-16, replacing the figures in `audit-handoff.md` §4. Every dimension is from
`../datasheet-params.toml` (read from manufacturer drawings) or from the spec section named
beside it. Nothing here is re-sourced or estimated except where it says so.

**Why they needed redrawing.** F6 replaces 31 panel-mounted vertical BNCs with 17 PCB-mounted
right-angle **dual** bodies. That changes the panel arithmetic in two directions at once: it
halves the column count, and it moves the connectors from the panel onto the board, so their
height above the panel's centreline is now set by the board's standoff rather than by where a
machinist drills.

---

## 1. Face allocation

Follows spec §9.6, which assigns faces deliberately: *"The Intan controller and the recording
NI live on the front of the rack, so the sync box's connections to them belong on the same
face; the task PC and the Faraday-cage booth are both reached from the rack's back."*

| Face | BNC ports | BNC bodies | Other |
|---|---|---|---|
| **Front** (rack-facing) | 14 Intan + 1 reward remote = **15** | **8** (1 spare port) | 2× MDR68 recording NI · recessed reward button · 2× 60 mm intake |
| **Back** | 15 rig + 1 reward driver out + 2 camera frame-time in + 2 line-out = **20** | **10** (0 spare ports) | 2× MDR68 task PC · DB37 eye tracker · M12 supply inlet · CM5 cutouts |
| | **35** | **18** | |

> **2026-10-18: J57, the task-PC line-out, joins the back face** (`line-out-and-pfi-copy.md`).
> It is one more dual body, both ports used, and its shells are on AO GND only. It takes
> the back face's last slack, 25.2 mm down to 7.2 mm, so **the back face has no room for
> any further connector.**

Bodies per sheet, and which face each lands on:

| Sheet | Refs | Ports | Bodies | Face |
|---|---|---|---|---|
| `mux-intan` | J37, J39, J41, J43 | 8 | 4 | front |
| `opto-intan` | J46, J48, J50 | 6 | 3 | front |
| `taskpc-digital` | J5 | 1 (+1 spare) | 1 | front — reward remote |
| `taskpc-digital` | J6 | 1 (+1 spare) | 1 | back — reward driver out |
| `taskpc-digital` | J57 | 2 | 1 | back — task-PC line-out, L and R (2026-10-18) |
| `analog-frontend` | J20, J22, J24, J26, J29 | 10 | 5 | back |
| `pi-interface` | J13, J15, J17 | 5 (+1 spare) | 3 | back |

**The reward group is the only sheet whose two ports do not share a body**, because spec §9.6
splits it across both faces on purpose and a dual body is one piece of plastic through one
panel. See `hardware/gen/bnc_dual.py`.

---

## 2. Horizontal budget

Usable width between rack ears ≈ **450 mm**. The board itself is 430 mm wide, and only the
PCB-mounted items (BNC bodies, MDR68, DB37) are constrained by it; the fans, button, M12 and
CM5 cutouts are chassis furniture and constrain the panel only.

**Dual BNC column pitch: 18.0 mm.** The body is 14.40 mm wide (`body_width_mm`) and the
footprint's courtyard 15.40 mm, but the binding dimension is the 1/2-28 UNEF nut, roughly
16.5 mm across corners. 18.0 mm clears the nut by ~1.5 mm and the courtyard by 2.6 mm. A row
of *N* bodies is therefore `(N−1) × 18.0 + 14.40` mm.

### Front

| Item | Qty | Each | Width | Source |
|---|---|---|---|---|
| MDR68, recording NI | 2 | 66.0 | 132.0 | `mdr68.overall_width_mm` 63.86 + 2.1 clearance |
| Dual BNC row | 8 | — | **140.4** | 7 × 18.0 + 14.40 |
| Recessed reward button | 1 | 20.0 | 20.0 | 12 mm pushbutton + bezel (J4, spec §9.8) |
| 60 mm intake fan | 2 | 65.0 | 130.0 | spec §9.7 — "60 mm needs ~65 mm" |
| | | | **422.4** | |
| **Clearance remaining** | | | **27.6** | across 5 inter-group gaps and 2 edge margins |

### Back

| Item | Qty | Each | Width | Source |
|---|---|---|---|---|
| MDR68, task PC NI | 2 | 66.0 | 132.0 | as above |
| Dual BNC row | 10 | — | **176.4** | 9 × 18.0 + 14.40 (J57 added 2026-10-18) |
| DB37, ACCES I/O eye tracker | 1 | 69.4 | 69.4 | D-sub 37 flange, mechanical standard (J19) |
| M12 supply inlet | 1 | 20.0 | 20.0 | `m12_inlet.panel_front_flange_dia_mm` 16.5 + clearance |
| CM5 port cutouts | 1 | 45.0 | 45.0 | USB-C + Ethernet + USB-A (spec §9.1) |
| | | | **442.8** | |
| **Clearance remaining** | | | **7.2** | across 4 inter-group gaps and 2 edge margins — was 25.2 before J57 |

**Both faces fit, and both are tight** — about 25–28 mm of slack across a 450 mm face. That is
worth knowing before anyone adds a connector: there is roughly one more BNC body of room on
each face and no more.

**The dual connector is what makes a single row work at all.** 31 ports as single connectors
at 18 mm pitch would need 31 × 18 = 558 mm — over the rack width before counting a single
MDR68. This is the same conclusion `audit-handoff.md` §4 reached, by a different route: it
assumed the ports could be stacked into two panel rows, which was true of a panel-mounted part
and is not true of a PCB-mounted one (see §3).

---

## 3. Vertical placement, and the one thing F6 genuinely changed

A 2U face is 88.9 mm with ~82 mm usable (spec §9.7).

The dual body's two ports are **16.00 mm apart, stacked vertically** (`port_pitch_mm`), in a
body 29.25 mm tall (`body_height_mm`). The pair spans 16.00 + 12.83 = **28.83 mm** including
the panel holes — comfortably inside 82 mm.

**But the row height is now set by the board, not by the panel.** The connector sits on the
PCB, so its port axes are fixed relative to the board's top surface:

| | Height above board top surface |
|---|---|
| Lower port axis | **7 mm** |
| Upper port axis | **23 mm** |

(**Corrected 2026-11-20, ruling R46**, from drawing 31-6575 rev A: "7 REF" from the seating
plane to the lower port's axis, and the 16 mm pitch. These read 6.63 and 22.63 mm, from
assuming the ports were centred in a 29.25 mm body; the drawing dimensions the lower one.
The figures below that were built on 6.63 mm move by 0.37 mm.)

Three consequences, none of which existed when the BNCs were panel-mounted:

1. **The standoff height chooses where the BNC row lands on the panel.** Centring the port
   pair on the panel's vertical centreline puts the board's top surface 14.63 mm below it,
   which is a ~26 mm standoff — tall for 2U. Any standoff from about 5 mm up keeps both ports
   inside the usable band, so this is a real choice to make at layout, not a constraint that
   makes itself.
2. **The MDR68 and BNC rows cannot both be centred.** The MDR68's body is 9.10 mm tall
   (`body_height_mm`) with its tail rows 5.10–10.82 mm inside the **PCB edge** (corrected
   2026-11-20: this said "behind the mating face"; the drawing's datum is the board edge,
   and the mating nose stands 4.70 mm beyond it), so its face
   centre sits far lower above the board than the BNC pair's does. Expect the MDR68s to sit
   lower relative to the BNC field, or to be shimmed.
3. **Board set-back from the panel** is set by the BNC body depth, 36.20 mm
   (`body_depth_mm`), less however much of it protrudes through the panel. That protrusion is
   not dimensioned on the drawing; measure it on a sample, or take it from the 3D model,
   before fixing the board outline.

   > **Superseded 2026-11-20 (ruling R46: manufacturer drawings in place of samples).**
   > Drawing 31-6575 rev A does dimension it. The body proper is 15.5 mm deep and the jacks
   > stand 20,7 REF beyond its front face, 8,6 REF of that thread. The part is a rear-mount
   > bulkhead part: the panel goes over the bushings and seats on the body's front face.
   > That face is put on the board edge, so **the panel's inner face is at the board edge
   > and there is no set-back**. Behind a 3.0 mm panel 5.6 mm of thread shows, and the
   > 2.8 mm nut (31-5652) leaves 2.8 mm. The thickest panel the thread takes is about
   > 5.8 mm, which is inferred (8.6 less the nut), not stated. The board-edge datum is
   > inferred too: the drawing shows no board edge, only a front face that runs down to the
   > seating plane.

**Panel thickness: 3.0 mm aluminium**, decided in `d3-panel-thickness.md`. Under the M12's
3.5 mm maximum, which is the only stated limit on the board, and the BNCs are PCB-anchored so
their thread engagement is not load-bearing. **2026-11-21 (R12):** it stays 3.0 mm, with
one local exception proposed: a counterbore leaving a 0.8 mm land at each MDR68
jacksocket (§8), to be confirmed by a coupon test-fit.

---

## 4. Panel machining summary

| Feature | Size | Count | Source |
|---|---|---|---|
| BNC port hole — **not round**, see below | Ø **12.83** mm with one flat **5.56 mm** from centre (R10) | 36 (35 used, 1 spare) — 34 until J57, 2026-10-18 | `bnc_dual_isolated.panel_hole_dia_mm`, `.panel_hole_flat_ruled_mm` |
| BNC vertical pitch within a body | **16.00** mm | — | `.panel_hole_pitch_mm` |
| BNC column pitch | **18.0** mm | — | this document §2 |
| M12 inlet hole | Ø **13.5** mm + Ø1.2 mm anti-rotation pin at 45° | 1 | `m12_inlet.panel_hole_dia_mm` |
| MDR68 opening | **53.3 × 14.0** mm, to admit the cable's mating shell (R12, §8); gated on the coupon test-fit | 4 | `mdr68.hook_span_mm`; `ni_shc68_68_epm.backshell_height_mm` |
| MDR68 panel fixing | **#2-56**, 2 places, 57.93 mm apart: a jacksocket through a counterbore, Ø 7.0 mm at most, that leaves a 0.8 mm land (R12, §8); gated on the same test-fit | 4 connectors, 8 counterbores | `mdr68.panel_thread`; `ni_shc68_68_epm` |
| 60 mm fan cutout | ~**65** mm square incl. fixings | 2 front, 2 side | spec §9.7 |
| Reward button | Ø ~12 mm, recessed bezel | 1 | spec §3.1 / §9.8 |
| Panel material | **3.0 mm** aluminium | both faces | `d3-panel-thickness.md` |

**The BNC holes are single-D (added 2026-11-20, R46).** Drawing 31-6575 rev A,
"RECOMMENDED MOUNTING HOLE": 2 × Ø12.83 mm at 16 mm, each with one anti-rotation flat
5.56 mm from the hole's centre, the upper hole's flat at the top and the lower hole's at
the bottom. The 2024 sibling drawing 31-6576 rev B gives 11.7 mm across the same cutout,
which is a flat at 5.29 mm. **Ruled 2026-11-21 (R10, `parametric-audit.md`): machine the
flat at 5.56 mm.** It accepts a bushing made to either drawing, where a 5.29 mm flat
would refuse one made to 5.56; with a 5.29 mm bushing it leaves up to 0.27 mm of play,
which the nut's clamp takes up **[engineering judgment]**. `constraints.md` Open question
24 is closed. **The nuts** (31-5652) are 14.2 mm across
flats, about 16.4 mm across corners, and the two ports of a body are 16.0 mm apart, so the
two nuts must be turned flat to flat. **The bushing is the shell, and every BNC is insulated from the panel (rulings R7 and R8,
`parametric-audit.md`). The D-holes are NOT machined in the aluminium: they are in an
insulating sub-plate, and the aluminium gets a window. See §6.**

**Machine all 36 BNC holes** (34 until `J57` was added on 2026-10-18; corrected here
2026-11-21). They are real ports on real connectors that will be fitted;
leaving one undrilled would mean a body pressed against blank panel.

**Updated 2026-08-16 — only ONE is a spare now, and it is on the FRONT.** The two BACK-face
spares were taken by the camera frame-time inputs (`frame-time-inputs.md`): J17B is the
eye/ohDPI strobe and J6B the behaviour strobe. Labelling those two "spare" would put a wrong
label on a live input, so:

| Port | Face | Hole | Label |
|---|---|---|---|
| J5B | front | lower | **spare** |
| J17B | back | lower | **CAM FRAME EYE** (or equivalent — one exposure strobe per camera group) |
| J6B | back | lower | **CAM FRAME BEH** |

**Which hole is which port (ruling R11, 2026-11-21, `parametric-audit.md`).** Port A of
every body (unit 1, pads 1 and 2) is the **upper** hole, 23 mm above the board. Port B
(unit 2, pads 3 and 4) is the **lower** hole, 7 mm above it. That is Amphenol's drawing
31-6576 rev B, Detail A ("HIGH PORT SIGNAL CONTACT HOLE" is pad 1's), and 31-6575 rev A's
rear and side views draw the same. The footprint's note had it the other way round until
this date; only the note was wrong. So the three ports above are the lower holes of their
bodies. Every port, from `netlist-contract.json` (pad 1's net above pad 3's):

| Body | Face | Upper hole (port A, pad 1) | Lower hole (port B, pad 3) | Shells |
|---|---|---|---|---|
| `J5` | front | `RWD_BTN`, the reward remote | spare (no net) | `DGND` |
| `J46` | front | `EVT_STROBE_INTAN_BNC` | `BARCODE_INTAN_BNC` | `INTAN_GND` |
| `J48` | front | `RWD_CMD_INTAN_BNC` | `RWD_DLVR_INTAN_BNC` | `INTAN_GND` |
| `J50` | front | `STIM_TRIG_INTAN_BNC` | `RHS_STIM_BNC` | `INTAN_GND` |
| `J37` | front | `INTAN_AO1` | `INTAN_AO2` | `INTAN_GND` |
| `J39` | front | `INTAN_AO3` | `INTAN_AO4` | `INTAN_GND` |
| `J41` | front | `INTAN_AO5` | `INTAN_AO6` | `INTAN_GND` |
| `J43` | front | `INTAN_AO7` | `INTAN_AO8` | `INTAN_GND` |
| `J13` | back | `CAM_SYNC_EYE` | `CAM_SYNC_BEH1` | `DGND` |
| `J15` | back | `CAM_SYNC_BEH2` | `CAM_SYNC_BEH3` | `DGND` |
| `J17` | back | `CAM_SYNC_BEH4` | `CAM_FRAME_EYE_BNC` | `DGND` |
| `J6` | back | `RWD_DLVR_BNC`, the reward driver out | `CAM_FRAME_BEH_BNC` | `DGND` |
| `J57` | back | `AO_L_BNC` | `AO_R_BNC` | `AO_GND_TPC` |
| `J29` | back | `A_MISC2_RAW` | `A_MISC3_RAW` | each its own `*_SHLD` |
| `J26` | back | `A_JOY_Y_RAW` | `A_MISC1_RAW` | each its own `*_SHLD` |
| `J24` | back | `A_ACC_RAW` | `A_JOY_X_RAW` | each its own `*_SHLD` |
| `J22` | back | `A_MIC_RAW` | `A_AMB_RAW` | each its own `*_SHLD` |
| `J20` | back | `A_PD1_RAW` | `A_PD2_RAW` | each its own `*_SHLD` |

Not checked on a part. Before the plates are labelled, check continuity from each port's
centre to its pad on the first built box (`../assembly-checklist.md`).

Colour-code the reward group while doing it, which spec §9.6 already asks for and which
matters more now that the reward remote sits among fourteen identical Intan BNCs.

---

## 5. What is still open

- ~~**Board set-back** (§3 item 3)~~ — closed 2026-11-20 by the note under §3 item 3:
  there is no set-back, the BNC body's front face is on the board edge. (Struck
  2026-11-21; this line had been left standing.)
- **Standoff height** (§3 item 1) — a layout decision, now coupled to panel appearance.
- **MDR68 tail-row-to-pin map** — the one genuine unknown, tracked in `d3-panel-thickness.md`
  and asserted by `check_footprint_geometry.py`. Nothing on this page depends on it.
- **The BNC sub-plates** (§6) are a proposal to be drawn. The hole's flat is 5.56 mm
  (R10) and port A is the upper port (R11), both ruled 2026-11-21; the second waits on a
  continuity check on the first built box.
- **The MDR68s' openings and jacksockets** (§8, R12): the jacksocket is not chosen, the
  cable's shell outline and seated depth are not in the sources, and a coupon test-fit
  gates the panel.

## 6. The BNC sub-plates (ruling R8, 2026-11-20)

The user ruled "Insulating sub-plate" on 2026-11-20, after no stock shoulder washer was
found to fit the 16.0 mm port pitch (`constraints.md` Open question 25). Every dual BNC
passes through an insulating plate, not through the aluminium. **Everything in this
section but the Amphenol dimensions is [engineering judgment]**: it is a proposal for the
panel drawing (Task 15) to draw, with its arithmetic shown, and nothing here was built.

**Windows.** One window in the aluminium per group of BNC bodies, and one plate per
window: one on the front and three on the back, four plates. A single window is not
possible on the back, where two MDR68s and the DB37 sit between the groups and fix to the
metal.

| Face | Bodies | Window in X (board frame) | Width |
|---|---|---|---|
| front | `J5`, the button `J4`, `J46`, `J48`, `J50`, `J37`, `J39`, `J41`, `J43` | 70.3 to 239.7 | 169.4 |
| back | `J13`, `J15`, `J17`, `J6` | −1.2 to 74.2 | 75.4 |
| back | `J57` | 171.3 to 192.7 | 21.4 |
| back | `J29`, `J26`, `J24`, `J22`, `J20` | 335.3 to 428.7 | 93.4 |

**The front is one window because of the reward button.** With `J5` in a window of its
own (70.3 to 91.7) and the Intan row in another (from 110.3), the aluminium between them
was 18.6 mm wide, and §2 allows the button and its bezel 20.0 mm. The 10.7 mm margins
cannot be narrowed without going under B-7's 2.5 mm. So the two windows are one, and the
button's hole is in the plate, at the 3.0 mm thickness, where the panel would have had
it. The button is a `DGND` part behind an insulator, which costs nothing.

Each window runs from 10.7 mm outside the first body's centre to 10.7 mm outside the
last's, and in height from 3.7 mm below the board's top surface to 33.7 mm above it (the
port axes at 7 and 23 mm, ±10.7). The back-left window starts 1.2 mm outside the board's
left edge; the panel overhangs the board there (`floorplan.md` §1).

**Why 10.7 mm.** The nut is 14.2 mm across flats, so 8.2 mm from the port's axis to a
corner. The board keeps 2.5 mm free of copper between ground domains (`constraints.md`
B-7), which is the widest spacing it holds anywhere, and the same 2.5 mm is held here
between any BNC metal and any chassis metal. 8.2 + 2.5 = 10.7. That leaves:

| From | To the window's metal edge |
|---|---|
| a nut's corner, on the outer face | 2.5 mm, over the plate's surface |
| a bushing in its Ø12.83 hole, through the plate | 10.7 − 6.4 = 4.3 mm |

The sources state no working voltage for any of these domains against the chassis
(`constraints.md` §3), so no standard's creepage figure applies; the board's own rules are
0.6 mm for a mis-plugged input (N-13) and the 2.5 mm above. The laminate itself is not the
limit: the distances over its surface are. Its dielectric strength is quoted at 350 V/mil
and up (`datasheet-params.toml` `[bnc_sub_plate]`), a short-time figure for thin sheet
that does not scale to a withstand voltage for this plate, and none is claimed here.

**The plate, and why it is stepped.** G-10/FR-4 glass-epoxy laminate. The BNC's body
face is on the board edge, and so is the aluminium's inner face (§3). For that to stay
true the plate's inner face has to lie in the same plane, so the plate cannot simply be
bolted to the back of the panel (it would stand where the board is) or to the front (its
inner face would be 3.0 mm ahead of the body). It is stepped:

- **3.0 mm thick inside the window**, the panel's own thickness, with its inner face
  flush with the aluminium's. The BNC bodies seat on it exactly as they would on the panel.
- **A 1.6 mm lip** beyond the window, lying on the aluminium's outer face.
- So **4.6 mm at each BNC.**

Thread: 8.6 mm stands beyond the body's face (31-6575 rev A).

| | mm |
|---|---|
| plate at the BNC | 4.6 |
| nut, Amphenol 31-5652 | 2.8 |
| **used** | **7.4** |
| **left of 8.6** | **1.2** |

**The jacks show 1.6 mm less than they would through bare panel.** Each stands 20.7 mm
beyond the board edge wherever the plate is. Beyond a 3.0 mm panel that is 17.7 mm;
beyond the plate's 4.6 mm it is 16.1 mm, with 4.0 mm of thread showing for the nut and
its 1.2 mm to spare. A mating plug's body has to clear the plate's face 1.6 mm sooner.

No washer is fitted: there is 1.2 mm for one, and none is needed on an insulator. A lip
thicker than 2.8 mm would leave the nut no thread. The part can be machined from one
sheet of 1/4 in stock, or made as a 3.0 mm piece bonded to a 1.6 mm one; 4.6 mm is not a
stock thickness.

**The holes.** In the plate, per body: 2 × Ø12.83 mm at 16.0 mm, each with its flat
**5.56 mm from the hole's centre** (ruling R10, 2026-11-21: 31-6575 rev A's own figure,
which also accepts the 2024 sibling's 5.29 mm bushing), the upper hole's flat at the top
and the lower hole's at the bottom. The flat now does its job
against an insulator, and it is the only thing that stops a bushing turning in a
glass-epoxy hole, so it should not be left round.

**Fixing.** Screws through the lip into the aluminium, along the **top** edge of each
plate and at whichever **ends** have room. Three things limit where:

- every screw is chassis metal, so each is at least 2.5 mm from any nut's corner. A screw
  in the lip is outside the window, so it is farther than the window's edge already;
- the windows' bottom edges are about 2.9 mm above the chassis floor with the board on its
  5 mm standoff (5 + 1.6 − 3.7), so there is no room for a lip and screws below. The
  bottom edge is held by the step alone;
- three ends have a neighbour's fixing close by: `J3`'s jack socket is about 3.8 mm from
  the back-left window, `J2`'s 4.3 mm from `J57`'s, and `J19`'s jackpost 5.5 mm from the
  back-right one. The lip is narrow there and carries no screw.

Screw size, count and the lip's width are for the panel drawing.

**Hardware on the plate: nuts only, as built.** One Amphenol 31-5652 hex nut per port,
2.8 mm thick, 14.2 mm across flats, turned flat to flat on each body (§4). 36 per box. No
metal washer, no solder lug, no bracket: anything metal that spans from a bushing toward
the plate's edge shortens the 2.5 mm.

**A strap point on each plate, unfitted (ruling R9).** One of each plate's fixing screws,
on its top edge, is the strap point: it is chassis metal already, and it is drawn with
room under its head for a solder tag. Nothing is fitted there as built. To bond one port
to the chassis at bring-up, a 1/2 in ring lug goes under that port's nut and a short wire
from it to the tag. The lug has to be 1.2 mm thick or less, which is the thread to spare.
Neither the lug nor the tag is a chosen part.

- **Only the rig-signal plate is ever strapped**: the back-right one, `J29`, `J26`, `J24`,
  `J22`, `J20`, whose ten shields each reach `AGND` through 10 Ω. If those inputs pick up
  interference, strap a shield, measure, and keep whichever is quieter. That is the
  bulkhead choice SPEC:330–342 left open, made testable, in the way R2 and R4 leave
  `R224` and `C162` to bring-up.
- **The other three plates have the point for uniformity and must not be strapped.** A
  strap on the front plate bonds `INTAN_GND` to the chassis and bridges the Intan barrier;
  on the camera plate or `J5` it adds a second `DGND`-to-chassis bond (G-3, G-5); on
  `J57` it bonds the task PC's AO ground (G-17).
- **What a strap trades.** It gains shielding at radio frequencies, in both directions:
  the shield meets the chassis where the cable enters. It gives up the single low-frequency
  return: the strapped shield joins `AGND` to the chassis through 10 Ω, and the chassis
  reaches `DGND` at `NT3`, a second path beside `NT1` (G-1). Mains-frequency loop current
  then flows in the shield. The default is not bonded because low-frequency loops are the
  failure this board is built against (SPEC:284–287).

**What it changes elsewhere.**
- The **M12 inlet moves** (the user, 2026-11-20: "Elsewhere on the back"). See §7.
- The **IO Board's port cutouts** are above the back-left plate, whose top is 33.7 mm
  above the board plus its lip.
- The panel's **stiffness**: four windows, the front one 169.4 mm long, in 3.0 mm
  aluminium. Not assessed.
- **Port labels** go on the plate. Port A is the upper hole and port B the lower
  (ruling R11; the table in §4), to be confirmed by continuity on the first built box
  before the plates are labelled.

## 7. The M12 inlet's position (2026-11-20)

**The inlet's hole is centred at X = 210 mm in the board's frame, 50 mm above the board's
top surface, in the aluminium of the back panel.** That is above the left-hand end of
`J2`, and 56.6 mm above the chassis floor with the board on its 5 mm standoff.
**[engineering judgment]**; no source gives the inlet a position, and the user's ruling
was "Elsewhere on the back".

It was to sit in the connector row at about X = 162 to 174 (`placement.md` §2, until
this date). `J57`'s sub-plate now takes X = 171.3 to 192.7 up to 33.7 mm above the board,
and the IO Board with its bracket reaches X = 160 above the row, so nothing 20 mm wide is
left there.

Why this place:

| It must clear | Where that is | Clearance at X 210, 50 mm up |
|---|---|---|
| its own allocation | 20 mm: the 16.5 mm flange and clearance (§2) | X 200 to 220, 40 to 60 mm above the board |
| `J57`'s plate | X ≤ 192.7 and its lip; top at 33.7 mm and its lip | 7 mm to the side of the allocation, and above the plate's height |
| `J2` | X 194.07 to 257.93, body 9.10 mm tall | 31 mm above the body. **Settled 2026-11-21 (R12, §8):** `J2`'s plug is NI's SHC68-68-EPM, whose backshell is 14.0 mm tall, 7.0 mm each side of an axis that lies inside the 9.10 mm body, so its top is at most 16.1 mm above the board and its sides are at X 193.55 to 258.45. The inlet's allocation starts at 40 mm. The inlet stays where it is. This row said the plug's height was not in the sources. |
| the IO Board and its bracket | X ≤ 160 | 40 mm |
| `J19` and the rig-signal plate | X ≥ 262.87 | 43 mm |
| the fans | front panel, X ≥ 310; left side panel | a different panel |
| the airflow | front-right to the left side (`floorplan.md` §5.1) | the inlet is a hole filled by a connector in the back panel, not in the path |
| the panel's usable band | about 82 mm (§3) | the flange's top is 64.9 mm above the floor |

**The leads to `J1`.** `J1` is at (173.5, 80 to 90) on the board, on the `AGND` side of
the split, and both of its ground pins are `AGND` (G-15). From X = 210 the leads run about
93 mm across the board and drop 50 mm, entirely over the `AGND` region: they do not cross
the split, the `DGND` region or the barcode strip, and they reach `J1` from its own side.
`floorplan.md` §3.3 allowed about 90 mm of lead from the old place (P-1). They pass over
`U35` and near the comparators `U54`, `U55` and `U76`; dress them clear of those.

**What it frees.** The 20.0 mm the inlet took in the back row's width (§2's table) is no
longer in the row: the row's slack is 27.2 mm, not 7.2 mm. The table is left as drawn.

## 8. The MDR68s and NI's cable (rulings R12 and R13, 2026-11-21)

The cable for `J2`, `J3`, `J36` and `J45` is NI's `SHC68-68-EPM` (SPEC §9.3). Its drawing
is NI customer drawing 192061-XX rev E; the figures are in `datasheet-params.toml`
`[ni_shc68_68_epm]` with the document's sha256, and the reasoning is in
`parametric-audit.md` R12. **Nothing here was tried on a cable, and no panel is cut until
the coupon test-fit below is done.**

**What the drawing says of the end that meets this box.** Two jackscrews, 2-56 UNC-2A,
57.9 mm apart; no latch. The mating shell stands 4.1 mm beyond the backshell's front
face and the jackscrew tips 2.5 mm. The backshell is 64.9 mm across the jackscrews and
14.0 mm tall, and the plug is 67.3 mm long behind its face. **The mating shell's own
width and height are not dimensioned**, and neither is the depth to which the plug
seats.

**Against the 3.0 mm panel.** With x measured outward from the board edge, the MH flange
and the mouth of its #2-56 threads are at −0.30, the panel's faces at 0 and +3.00, the
hooks' tips at +3.20 and the nose tip at +4.70. The drawings do not say which shell goes
inside the other. MH's nose has grounding dimples on its outer face, which points to the
nose going inside the cable's shell **[engineering judgment]**:

| | Nose inside the cable's shell (likelier) | Cable's shell inside the nose |
|---|---|---|
| Backshell face, at the least | +3.80 | +4.70 |
| The cable's shell | passes through the panel's thickness, to −0.30 at the least | stays inside the nose |
| Jackscrew tips, at the least | +1.30 | +2.20 |
| Short of the MH threads by, at the least | 1.60 mm | 2.50 mm |

- **The jackscrews do not reach the MH threads either way**, whatever the panel's
  thickness. NI's cables are made to screw into jacksockets, whose heads are 3.76 mm
  tall (.148 in, NI's connector guide). **Each MDR68 gets two jacksockets.**
- **The opening has to admit the cable's shell**, not only the connector's nose. Cut to
  the nose alone, it would stop the shell on the panel's outer face, with 1.7 mm of the
  nose engaged and the jackscrews out of reach of anything.

**The opening and the fixings, per MDR68.**

| | | From |
|---|---|---|
| Through opening | **53.3 × 14.0 mm**, centred on the line through the two #2-56 axes | Width: the hooks' 51.32 mm (MH) plus 1.0 mm a side; a plug's shell passes between the hooks. Height: the backshell's own 14.0 mm (NI), which the shell cannot exceed. Margins **[engineering judgment]**: the shell's outline is not in the sources. |
| Height of that line above the board | **not in the sources**: MH's drawing does not dimension it | take it from the part |
| Jacksockets | 2, one on each #2-56 axis, 57.93 mm apart: 2-56 male stud into the MH bracket, 2-56 female head about 3.76 mm tall | **not a chosen part; none found off the shelf.** NI's own has an M2.5 stud. |
| Proposed seat | a counterbore in the panel's outer face at each axis, leaving a **0.8 mm land** | **[engineering judgment]**; see below |
| Counterbore diameter | from the chosen jacksocket's hex and its driver; **7.0 mm at the most** | 64.9 − 57.9: the plug's own envelope round a jackscrew, so the plug hides it |
| Through the land | a clearance hole for the 2-56 stud | size for the panel drawing |
| Behind the land | a **0.3 mm washer** on each stud, filling the 0.30 mm to the MH flange | **[engineering judgment]**, so that tightening neither bows the land nor loads the solder joints; not a chosen part |
| Count | 4 openings, 8 jacksockets, 8 counterbores, 8 washers per box | |

**What the 0.8 mm land does in each case.** The jacksocket's head ends at
0.8 + 3.76 = +4.56.

| | Nose inside the shell | Shell inside the nose |
|---|---|---|
| Plug | held 0.76 mm short of the flange; the shell still overlaps the nose for its whole 4.1 mm | not held off; 0.14 mm to spare |
| Jackscrew thread engaged | 2.50 mm, 5.5 turns | 2.36 mm, 5.2 turns |

Whether the contacts are fully engaged 0.76 mm short of the flange is not in the
sources. With the opening above, the land between the opening's end and the stud's hole
is only about 1.1 mm wide. **If the coupon shows the land is wrong, the alternative is no
land**: widen the opening to pass the jacksockets and seat them on the MH bracket. Their
heads then end at +3.46, under the backshell in either case, with 2.16 mm (4.8 turns) or
1.26 mm (2.8 turns) of thread, and the connector is held by its solder joints and
boardlocks alone.

**Where the plug crowds its neighbours.** The backshell is 64.9 mm wide, 1.04 mm wider
than the connector's body, and its face may be as little as 0.80 mm off the panel.

| Plug | Its end | Nearest thing | Gap |
|---|---|---|---|
| `J3` | X = 74.55 | back-left sub-plate window, to X = 74.2 | 0.35 mm |
| `J2` | X = 193.55 | `J57`'s window, to X = 192.7 | 0.85 mm |
| `J2` | X = 258.45 | `J19`'s courtyard, from X = 262.87 | 4.4 mm |
| `J45` | X = 66.95 | front window, from X = 70.3 | 3.35 mm |
| `J36` | X = 242.55 | front window, to X = 239.7 | 2.85 mm |

At the first two, **a sub-plate's 1.6 mm lip must stop at the window**: under the
backshell it would foul it by up to 0.8 mm. §6 already has those two lips narrow and
without a screw. A 7.0 mm counterbore there would leave 0.3 and 0.8 mm of aluminium to
the window, which is one reason to make it smaller.

**Depth outside the panel.** Each plug stands 0.8 to 1.7 mm off the panel and is 67.3 mm
long, and its cable then wants a 76.2 mm bend radius. That is on both faces of the box.

**R13 (2026-11-21, the user: "TE first, RAF fallback") chooses the jacksocket, and changes
the stack above.** Each of the 8 fixings takes **TE 786585-2** (drawing C-786585 rev B2:
2-56 both ends, head 2.54 mm, flange Ø4.75 mm, female thread 2.29 mm minimum), **seated by
its collar on the MH bracket** at x = −0.30. The collar, 1.27 mm long, is **derived from
TE's web listing (stud 5.21 mm below it) and not dimensioned on TE's drawing**. Its head
top is at **+3.51**, below the backshell face in both cases: clear by 0.29 mm in case A
(jackscrew engaged 2.21 mm, 4.9 turns, with 0.08 mm to spare against the 2.29 mm minimum
depth) and by 1.19 mm in case B (1.31 mm, 2.9 turns). The fallback is **RAF
4500A-256-SS-20** (catalog p. 111; DigiKey 1772-3524-ND; 3/16 in hex, 5/32 in body, 2-56
both ends), **on the bracket and fitted before the panel**: top at +3.67, clear by 0.13 mm
in case A (2.37 mm, 5.2 turns) and 1.03 mm in case B (1.47 mm, 3.2 turns). On a land it
would foul case B by 0.07 mm at R12's 0.8 mm land plus washer. In the table above:

- **The 0.3 mm washer can be dropped with TE**: its collar spans the gap to the flange
  **[engineering judgment]**. MH's ±0.2 on both dimensions of that gap means collar and
  flange may not both bear; settle it at the coupon.
- **The land-or-bracket choice is the bracket.** The "proposed seat" row (a 0.8 mm land)
  and the 0.8 mm land's figures give way to a hole at each axis, about Ø5.3 to 5.5 mm for
  TE (it only has to pass the Ø4.75 flange; the slot is turned with a screwdriver) and
  about Ø6.0 mm for RAF. The 7.0 mm counterbore ceiling then no longer binds. At `J3` a
  Ø5.5 hole leaves about 1.05 mm to the window instead of 0.3 mm, and at `J2` about 1.55
  mm instead of 0.8 mm **[derived]**; a Ø5.3 hole centred on the axis reaches the 53.3 mm
  opening (26.32 mm from centre, against the opening's end at 26.65), so the opening is
  widened locally there. Panel features stay gated on the coupon.
- Both parts are **[derived]** figures from the drawings and R12's datums, not measured. The
  record, with every source and sha256, is `parametric-audit.md` R13.

**The gate: a coupon test-fit.** Before any panel is cut, fit one `SHC68-68-EPM` to one MH
`3700-0121-01` through a 3.0 mm aluminium coupon cut with this opening and these
counterbores (`../assembly-checklist.md`). It decides which case is real, the seat, the
opening and the hole, and it adds (R13): where MH's 2-56 thread starts, how deep it runs and
whether it is blind or through; and a fit of TE 786585-2 and of RAF 4500A-256-SS-20, keeping
the one that seats and engages. `constraints.md` Open question 23 has what is left.
