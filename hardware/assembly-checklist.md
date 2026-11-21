# Assembly Checklist

These items cannot be detected by a schematic, netlist checker, or BOM; they exist only as build steps on the physical board.

## Fit a CR2032 to the CM5 IO Board

The barcode counter is derived from the wall clock, and the RTC is what carries that clock across power-off. Without it the software still records, but marks every segment `clock_trusted: false` and gap arithmetic across restarts becomes unreliable. Nothing on the board can detect its absence.

## Machine all 34 BNC panel holes

Include the one remaining spare on the front face (J5B). See `hardware/breakout/panel-elevations.md` for the per-port label table. Two ports that were spare are now the camera frame-time inputs and must NOT be labelled "spare".

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

## Try one NI cable on one MDR68 before a panel is cut

Ruling R12 (same file, 2026-11-21). The panel's counterbores and jacksockets at the four
MDR68s come from NI's drawing of the `SHC68-68-EPM`, which draws the jackscrew tips 2.5 mm
beyond the plug's backshell and gives no travel. With one cable and one MH `3700-0121-01`
on the bench: mate them, and measure how far a jackscrew reaches beyond the backshell's
face and where the face stops against the connector's nose. If the jackscrews reach the
connector's own #2-56 threads, the counterbores and jacksockets are not needed. Record
the two figures beside R12 either way.

## Colour-code the reward group

Per spec §9.6, to distinguish the reward remote from fourteen identical Intan BNCs.

## Configure NTP

`timedatectl show -p NTPSynchronized` must report `yes`. The RTC covers power-off; NTP is what stops it drifting.

## Measure Intan-input noise with `C162` fitted and removed

Ruling R4 (`hardware/breakout/parametric-audit.md`, 2026-11-20). `C162` (220 pF / 2 kV, beside `U63` on the opto-Intan sheet) is fitted on every board as built. It is part of Traco's published EN 55032 class A filter, and it bridges the Intan isolation barrier: with it the `DGND`-to-Intan-reference coupling is about 330 pF, without it about 110 pF.

On board 1, with the Intan system connected and the board running normally, record the noise at an Intan analog input twice: once with `C162` fitted, once with it removed. Keep whichever is quieter, and build the remaining boards the same way. Write the two measurements and the choice next to ruling R4, and if the choice is "removed", mark `C162` DNP in `hardware/gen/gen_breakout_opto_intan.py` so the drawing matches the boards.

Nothing in the netlist, ERC or BOM can make this decision. It is the same kind of bring-up choice as `R224` on `J19`'s shell (ruling R2), which is also left to the bench: fit 0 Ω, 10 Ω, or nothing.
