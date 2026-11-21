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
their thread engagement is not load-bearing.

---

## 4. Panel machining summary

| Feature | Size | Count | Source |
|---|---|---|---|
| BNC port hole — **not round**, see below | Ø **12.83** mm with one flat | 36 (35 used, 1 spare) — 34 until J57, 2026-10-18 | `bnc_dual_isolated.panel_hole_dia_mm` |
| BNC vertical pitch within a body | **16.00** mm | — | `.panel_hole_pitch_mm` |
| BNC column pitch | **18.0** mm | — | this document §2 |
| M12 inlet hole | Ø **13.5** mm + Ø1.2 mm anti-rotation pin at 45° | 1 | `m12_inlet.panel_hole_dia_mm` |
| MDR68 panel fixing | **#2-56**, 2 places | 4 connectors | `mdr68.panel_thread` |
| 60 mm fan cutout | ~**65** mm square incl. fixings | 2 front, 2 side | spec §9.7 |
| Reward button | Ø ~12 mm, recessed bezel | 1 | spec §3.1 / §9.8 |
| Panel material | **3.0 mm** aluminium | both faces | `d3-panel-thickness.md` |

**The BNC holes are single-D (added 2026-11-20, R46).** Drawing 31-6575 rev A,
"RECOMMENDED MOUNTING HOLE": 2 × Ø12.83 mm at 16 mm, each with one anti-rotation flat
5.56 mm from the hole's centre, the upper hole's flat at the top and the lower hole's at
the bottom. The 2024 sibling drawing 31-6576 rev B gives 11.7 mm across the same cutout,
which is a flat at 5.29 mm. Which applies today is open (`constraints.md` Open question
24): test-fit or ask Amphenol before machining. **The nuts** (31-5652) are 14.2 mm across
flats, about 16.4 mm across corners, and the two ports of a body are 16.0 mm apart, so the
two nuts must be turned flat to flat. **The bushing is the shell, and every BNC is insulated from the panel (rulings R7 and R8,
`parametric-audit.md`). The D-holes are NOT machined in the aluminium: they are in an
insulating sub-plate, and the aluminium gets a window. See §6.**

**Machine all 34 BNC holes.** They are real ports on real connectors that will be fitted;
leaving one undrilled would mean a body pressed against blank panel.

**Updated 2026-08-16 — only ONE is a spare now, and it is on the FRONT.** The two BACK-face
spares were taken by the camera frame-time inputs (`frame-time-inputs.md`): J17B is the
eye/ohDPI strobe and J6B the behaviour strobe. Labelling those two "spare" would put a wrong
label on a live input, so:

| Port | Face | Label |
|---|---|---|
| J5B | front | **spare** |
| J17B | back | **CAM FRAME EYE** (or equivalent — one exposure strobe per camera group) |
| J6B | back | **CAM FRAME BEH** |

Colour-code the reward group while doing it, which spec §9.6 already asks for and which
matters more now that the reward remote sits among fourteen identical Intan BNCs.

---

## 5. What is still open

- **Board set-back** (§3 item 3) — needs the protrusion depth off a sample or the 3D model.
- **Standoff height** (§3 item 1) — a layout decision, now coupled to panel appearance.
- **MDR68 tail-row-to-pin map** — the one genuine unknown, tracked in `d3-panel-thickness.md`
  and asserted by `check_footprint_geometry.py`. Nothing on this page depends on it.
- **The BNC sub-plates** (§6) are a proposal to be drawn; and which BNC port is the upper
  one (`constraints.md` Open question 26) and the hole's flat (Open question 24) are open.

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

**The holes.** In the plate, per body: 2 × Ø12.83 mm at 16.0 mm, each with its flat,
5.56 mm from centre by 31-6575 rev A or 5.29 mm by the 2024 sibling (`constraints.md` Open
question 24, still open: test-fit before cutting plates). The flat now does its job
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
- **Port labels** go on the plate, and which port of a body is the upper one needs
  settling first (`constraints.md` Open question 26).

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
| `J2` | X 194.07 to 257.93, body 9.10 mm tall | 31 mm above the body. **The height of `J2`'s mating plug and its jackscrews is not in the sources**; if a backshell is taller than about 30 mm above the board the inlet goes higher. |
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
