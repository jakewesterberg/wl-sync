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
