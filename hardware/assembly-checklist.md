# Assembly Checklist

These items cannot be detected by a schematic, netlist checker, or BOM; they exist only as build steps on the physical board.

## Fit a CR2032 to the CM5 IO Board

The barcode counter is derived from the wall clock, and the RTC is what carries that clock across power-off. Without it the software still records, but marks every segment `clock_trusted: false` and gap arithmetic across restarts becomes unreliable. Nothing on the board can detect its absence.

## Machine all 36 BNC panel holes

Include the one remaining spare on the front face (J5B). See `hardware/breakout/panel-elevations.md` for the per-port label table. Two ports that were spare are now the camera frame-time inputs and must NOT be labelled "spare".

The count is 36 since `J57` was added (2026-10-18); this heading said 34 until
2026-11-21.

## Check each BNC port against its pad before the plates are labelled

Ruling R11 (`hardware/breakout/parametric-audit.md`, 2026-11-21). The panel labels take
port A of every dual BNC (pads 1 and 2) as the **upper** hole and port B (pads 3 and 4) as
the **lower**, from Amphenol's drawings; `hardware/breakout/panel-elevations.md` §4 lists
every port by hole. No part was checked. On the first built box, with the board unpowered
and nothing plugged in, on one body of each kind (`J5`, an Intan body, a camera body,
`J57` and an analog body):

- continuity from the **upper** jack's centre contact to pad 1, and from the **lower**
  jack's to pad 3;
- continuity from the upper jack's bushing to pad 2, and from the lower's to pad 4. On
  `J20` to `J29` the two shells are different nets, so this one matters there.

If a part disagrees with the drawing, change the labels, not the board. Nothing in the
netlist, ERC or DRC can see this.

## Coupon test-fit of the NI cable, before any panel is cut

Ruling R12 (same file, 2026-11-21). **This gates the panel drawing's MDR68 features.**
The opening, counterbores and jacksockets at the four MDR68s come from NI's drawing of
the `SHC68-68-EPM` and MH's of the `3700-0121-01`. Neither says which connector's shell
goes inside the other's, how deep the plug seats, or how wide the plug's shell is.

Make a coupon of 3.0 mm aluminium with one opening 53.3 × 14.0 mm and, on the #2-56
axes 57.93 mm apart, two counterbores from the outer face leaving a 0.8 mm land with a
clearance hole for a 2-56 stud (`hardware/breakout/panel-elevations.md` §8). Solder one
MH `3700-0121-01` at a board edge with its footprint's datum on the edge, and set the
coupon's inner face on that edge, as the panel will be.

1. **Without the coupon**, mate the cable and the connector. Record which shell goes
   inside the other, and the distance from the connector's flange to the plug's
   backshell face. Record how far a jackscrew can be made to reach beyond the backshell
   face (NI draws 2.5 mm), and whether it reaches the connector's own #2-56 threads.
2. **Through the coupon, without jacksockets.** Does the plug's shell enter the opening
   and clear it on all four sides? Does the plug seat to the distance of step 1?
3. **With jacksockets on the lands**, and a 0.3 mm washer behind each land. Does the plug
   still seat to the distance of step 1, or do the jacksockets hold it off, and by how
   much? How many turns do the jackscrews take?
4. If step 3 holds the plug off, repeat with the jacksockets seated on the connector's
   bracket (open the coupon out to pass them).

**Added by ruling R13 (2026-11-21, "TE first, RAF fallback").** The jacksocket is TE
786585-2 (fallback RAF 4500A-256-SS-20); buy a few of each for the coupon. Both seat on the
MH bracket, so steps 3 and 4 above use that seat first, with holes of about Ø5.5 mm (TE) and
Ø6.0 mm (RAF) in place of the counterbores. Also:

5. **Measure MH's thread.** With a 2-56 screw and a depth gauge: where the thread starts
   behind the flange face, how deep it runs, and whether the hole is blind or through.
   (MH's drawing gives "#2-56 (2 Places)" and none of these.) Both parts have an unthreaded
   length under the shoulder (TE about 1.0 mm, RAF up to 0.68 mm), so the depth behind the
   thread matters: 4.0 mm or more for RAF, 5.2 mm or more for TE.
6. **Fit TE 786585-2 and RAF 4500A-256-SS-20, each**, on the bracket. Record whether TE's
   collar bears on the bracket and is about 1.27 mm long, and whether the 0.3 mm washer is
   needed; the turns each takes in the jackscrew; and whether the plug seats. RAF goes on
   before the coupon.
7. **Keep the one that seats and engages.** TE first; RAF if TE does not fit or cannot be
   bought.

Write the figures beside R12 and R13 and fix the seat, the opening and the hole from
them.

## Colour-code the reward group

Per spec §9.6, to distinguish the reward remote from fourteen identical Intan BNCs.

## Configure NTP

`timedatectl show -p NTPSynchronized` must report `yes`. The RTC covers power-off; NTP is what stops it drifting.

## Measure Intan-input noise with `C162` fitted and removed

Ruling R4 (`hardware/breakout/parametric-audit.md`, 2026-11-20). `C162` (220 pF / 2 kV, beside `U63` on the opto-Intan sheet) is fitted on every board as built. It is part of Traco's published EN 55032 class A filter, and it bridges the Intan isolation barrier: with it the `DGND`-to-Intan-reference coupling is about 330 pF, without it about 110 pF.

On board 1, with the Intan system connected and the board running normally, record the noise at an Intan analog input twice: once with `C162` fitted, once with it removed. Keep whichever is quieter, and build the remaining boards the same way. Write the two measurements and the choice next to ruling R4, and if the choice is "removed", mark `C162` DNP in `hardware/gen/gen_breakout_opto_intan.py` so the drawing matches the boards.

Nothing in the netlist, ERC or BOM can make this decision. It is the same kind of bring-up choice as `R224` on `J19`'s shell (ruling R2), which is also left to the bench: fit 0 Ω, 10 Ω, or nothing.
