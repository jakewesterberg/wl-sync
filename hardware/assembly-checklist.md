# Assembly Checklist

These items cannot be detected by a schematic, netlist checker, or BOM; they exist only as build steps on the physical board.

## Fit a CR2032 to the CM5 IO Board

The barcode counter is derived from the wall clock, and the RTC is what carries that clock across power-off. Without it the software still records, but marks every segment `clock_trusted: false` and gap arithmetic across restarts becomes unreliable. Nothing on the board can detect its absence.

## Machine all 34 BNC panel holes

Include the one remaining spare on the front face (J5B). See `hardware/breakout/panel-elevations.md` for the per-port label table. Two ports that were spare are now the camera frame-time inputs and must NOT be labelled "spare".

## Colour-code the reward group

Per spec §9.6, to distinguish the reward remote from fourteen identical Intan BNCs.

## Configure NTP

`timedatectl show -p NTPSynchronized` must report `yes`. The RTC covers power-off; NTP is what stops it drifting.

## Measure Intan-input noise with `C162` fitted and removed

Ruling R4 (`hardware/breakout/parametric-audit.md`, 2026-11-20). `C162` (220 pF / 2 kV, beside `U63` on the opto-Intan sheet) is fitted on every board as built. It is part of Traco's published EN 55032 class A filter, and it bridges the Intan isolation barrier: with it the `DGND`-to-Intan-reference coupling is about 330 pF, without it about 110 pF.

On board 1, with the Intan system connected and the board running normally, record the noise at an Intan analog input twice: once with `C162` fitted, once with it removed. Keep whichever is quieter, and build the remaining boards the same way. Write the two measurements and the choice next to ruling R4, and if the choice is "removed", mark `C162` DNP in `hardware/gen/gen_breakout_opto_intan.py` so the drawing matches the boards.

Nothing in the netlist, ERC or BOM can make this decision. It is the same kind of bring-up choice as `R224` on `J19`'s shell (ruling R2), which is also left to the bench: fit 0 Ω, 10 Ω, or nothing.
