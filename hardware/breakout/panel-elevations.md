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
| **Back** | 15 rig + 1 reward driver out + 2 camera frame-time in = **18** | **9** (0 spare ports) | 2× MDR68 task PC · DB37 eye tracker · M12 supply inlet · CM5 cutouts |
| | **33** | **17** | |

Bodies per sheet, and which face each lands on:

| Sheet | Refs | Ports | Bodies | Face |
|---|---|---|---|---|
| `mux-intan` | J37, J39, J41, J43 | 8 | 4 | front |
| `opto-intan` | J46, J48, J50 | 6 | 3 | front |
| `taskpc-digital` | J5 | 1 (+1 spare) | 1 | front — reward remote |
| `taskpc-digital` | J6 | 1 (+1 spare) | 1 | back — reward driver out |
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
| Dual BNC row | 9 | — | **158.4** | 8 × 18.0 + 14.40 |
| DB37, ACCES I/O eye tracker | 1 | 69.4 | 69.4 | D-sub 37 flange, mechanical standard (J19) |
| M12 supply inlet | 1 | 20.0 | 20.0 | `m12_inlet.panel_front_flange_dia_mm` 16.5 + clearance |
| CM5 port cutouts | 1 | 45.0 | 45.0 | USB-C + Ethernet + USB-A (spec §9.1) |
| | | | **424.8** | |
| **Clearance remaining** | | | **25.2** | across 4 inter-group gaps and 2 edge margins |

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
| Lower port axis | **6.63 mm** |
| Upper port axis | **22.63 mm** |

(From `body_height_mm` 29.25 and `port_pitch_mm` 16.00, ports centred in the body.)

Three consequences, none of which existed when the BNCs were panel-mounted:

1. **The standoff height chooses where the BNC row lands on the panel.** Centring the port
   pair on the panel's vertical centreline puts the board's top surface 14.63 mm below it,
   which is a ~26 mm standoff — tall for 2U. Any standoff from about 5 mm up keeps both ports
   inside the usable band, so this is a real choice to make at layout, not a constraint that
   makes itself.
2. **The MDR68 and BNC rows cannot both be centred.** The MDR68's body is 9.10 mm tall
   (`body_height_mm`) with its contacts 5.10–10.82 mm behind the mating face, so its face
   centre sits far lower above the board than the BNC pair's does. Expect the MDR68s to sit
   lower relative to the BNC field, or to be shimmed.
3. **Board set-back from the panel** is set by the BNC body depth, 36.20 mm
   (`body_depth_mm`), less however much of it protrudes through the panel. That protrusion is
   not dimensioned on the drawing; measure it on a sample, or take it from the 3D model,
   before fixing the board outline.

**Panel thickness: 3.0 mm aluminium**, decided in `d3-panel-thickness.md`. Under the M12's
3.5 mm maximum, which is the only stated limit on the board, and the BNCs are PCB-anchored so
their thread engagement is not load-bearing.

---

## 4. Panel machining summary

| Feature | Size | Count | Source |
|---|---|---|---|
| BNC port hole | Ø **12.83** mm | 34 (33 used, 1 spare) | `bnc_dual_isolated.panel_hole_dia_mm` |
| BNC vertical pitch within a body | **16.00** mm | — | `.panel_hole_pitch_mm` |
| BNC column pitch | **18.0** mm | — | this document §2 |
| M12 inlet hole | Ø **13.5** mm + Ø1.2 mm anti-rotation pin at 45° | 1 | `m12_inlet.panel_hole_dia_mm` |
| MDR68 panel fixing | **#2-56**, 2 places | 4 connectors | `mdr68.panel_thread` |
| 60 mm fan cutout | ~**65** mm square incl. fixings | 2 front, 2 side | spec §9.7 |
| Reward button | Ø ~12 mm, recessed bezel | 1 | spec §3.1 / §9.8 |
| Panel material | **3.0 mm** aluminium | both faces | `d3-panel-thickness.md` |

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
