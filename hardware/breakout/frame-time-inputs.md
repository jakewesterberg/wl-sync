# Frame-time inputs: camera strobes back into the box

Decided 2026-08-16, in design phase, before layout. Implements the gap
`camera-sync-change.md` §3 opened: with the cameras free-running, **the box no longer knows
frame times**, and spec §12 item 1's justification for dropping exposure-active returns
("the Pi triggers the cameras so frame times are known by construction") is void.

Two panel inputs bring one exposure strobe per camera group back to the Pi, timestamped in
the same PIO/event stream as everything else. Frame times still also reach analysis as data
via the camera logger and the barcode — this is the hardware half, not a replacement for it.

---

## 1. One front end serves both groups

Both camera groups are FLIR Blackfly S. The eye tracker is OpenIrisDPI, and OpenIris drives
FLIR cameras through Spinnaker — its reference binocular build is two Blackfly S USB3 at up
to 500 fps, which is exactly the 500 Hz spec §6 already assumes for the eye channels. So the
ohDPI strobe and the behaviour-camera strobe are the **same electrical interface**. See
`[flir_bfs_gpio]` in `datasheet-params.toml`, which records this and the reason one strobe
per group suffices (FLIR's primary strobe *drives* the secondaries' triggers, so it defines
the group's exposure rather than merely representing it).

Per input, ×2, identical:

```
BNC ──[100R]──┬─────────────────────────── clamp node
              │                    │
         BAT54S (+5V/DGND)    [1k] to +5V      ← FLIR's own published row:
              │                    │             0.87 V low / 5.0 V high / 4.2 mA
              └──────────────[10k]─┴──▶ LM339 IN+ ──┐
                                                    │
   +2.50 V ────────────────────────────▶ LM339 IN−  │  V+ = +12V
                                                  [1M]  V− = AGND
                                    open collector ─┴──[2.2k] to +3V3 ──▶ Pi GPIO
```

- **Connectors:** spare ports **J6B** (`taskpc-digital`) and **J17B** (`pi-interface`), both
  back face. Both holes are already machined — `panel-elevations.md` counts 34 BNC holes,
  31 used, 3 spare, and already instructs that all three be machined. **No new panel holes,
  no new connector bodies.**
- **GPIO:** **26** (eye/ohDPI), **27** (behaviour) — the last two non-PWM spares.
- **Threshold:** 2.50 V, one 10k/10k divider off +5V shared by both channels.

## 2. Why +12 V, which the first sketch of this omitted

**The comparator cannot run from +5 V.** The LM339's input common-mode ceiling is V+ − 1.5 V
(this sheet's own docstring) to V+ − 2.0 V (`[lm339]`). On a +5 V supply that ceiling is
3.0–3.5 V, and the strobe's HIGH is **5.0 V** — outside it. An LM339 driven above its
common-mode ceiling can invert its output; this is the part's best-known failure mode.

With V+ = +12 V the ceiling is 10.0 V and a 5.0 V input has 5 V of headroom. That is also
exactly U54's existing arrangement on this sheet, so nothing new is being proven.

**Why not divide the input down and stay on +5 V.** A 2:1 divider would fit the +5 V window,
but it perturbs the FLIR operating point off the published 5 V / 1 kΩ row unless the divider
legs are ≥100 kΩ, and it halves every margin below for no gain once +12 V is already present
on this sheet.

## 3. Ground domains — where the current actually goes

This was got **backwards** in the first pass and is the reason it is written out here.

The strobe is an open-collector opto pulled up to **+5 V, whose return is DGND**. So the
4.2 mA return path is decided by the BNC shell:

| Shell net | Where 4.2 mA of switching current flows |
|---|---|
| AGND | node → camera → shell → AGND → **through NT1, the single star tie** → DGND |
| **DGND** (chosen) | node → camera → shell → DGND → supply. **Never crosses the tie.** |

So the shells, the BAT54S clamp and the threshold divider are all **DGND**-referenced. That
also matches the other port on each of the same two connector bodies, which is already DGND.

**The comparator's V− is still AGND**, matching U54 and the three proven channels, and this
is not a mismatch:

- **The comparison is unaffected.** IN+ and IN− are both DGND-referenced, so any AGND–DGND
  offset is common-mode and cancels differentially. V− does not enter the comparison beyond
  setting the common-mode window, which §2 shows is satisfied with 5 V to spare.
- **V− sets the output LOW level**, per this sheet's SECOND DESTROY-HARDWARE CONSTRAINT — the
  pin is the common emitter of all four open-collector outputs. Output LOW is
  AGND + V_CEsat ≈ 0.2 V, read by the Pi against DGND. Against a 3.3 V GPIO's ~0.99 V V_IL
  that is ample, and it is the identical arrangement PD1_COMP/PD2_COMP/ACC_TRIG already ship.
- The clamp holds the node to −0.7…+5.7 V; the 10 kΩ series into IN+ bounds the resulting
  input current to ~40 µA against the 50 mA the datasheet explicitly permits below −0.3 V.
  Same reasoning as R190 on channel 4.

## 4. Margins

| | Value | Against |
|---|---|---|
| Low | **1.63 V** | 0.87 V strobe low vs 2.50 V threshold |
| High | **2.50 V** | 5.00 V strobe high vs 2.50 V threshold |
| Hysteresis | ~33 mV | 10k series / 1M feedback against a 3.3 V swing — this sheet's own k = 0.01 recipe |

Compare the alternative this replaces: driving an `SN74LVC541A` input directly **misses by
70 mV** — its V_IL max is 0.8 V and the strobe's best published low is 0.87 V. That miss is
silent, and it is why `[sn74lvc541a]` was pinned.

Hysteresis is not strictly required here — the falling edge is actively driven and fast —
but 33 mV costs two resistors and guards against reflections on an unterminated coax run.

## 5. Parts

**1 × LM339A + 12 resistors + 2 caps + 2 BAT54S.** No new part *number*: `U54` is already an
LM339A and `[lm339]`/`[bat54s]` are already pinned. 2 of the new package's 4 sections are
used; the spare two have their inputs tied to defined levels (never floating) with outputs
left open — they have no pull-up, so they sink nothing.

Refdes are minted **out of band**, above the board-wide maxima (C158, D44, R208, U75), for
the same reason `R190` and `R198`–`R202` were: this sheet's counters are pinned, and siblings
seed their own counters past its committed maxima. An auto-minted ref here would renumber
parts on other sheets.

## 6. Two things the software must do

- **Timestamp the FALLING edge.** ExposureActive asserts by pulling low, and the falling edge
  is actively driven by the opto transistor. The rising edge is RC through the pull-up and
  varies with cable length, so measuring pulse *width* from this signal inherits a systematic
  error.
- **The threshold does not need trimming.** The swing is 0.87 ↔ 5.0 V; anything from ~1.5 to
  ~3.5 V behaves identically. Its only effect is a constant sub-µs offset on a 2 ms frame
  period, and it calibrates out. Do not spend a DAC channel on it — all four MCP4728 channels
  are allocated (spec §12 item 9), and a second package would be a new I²C device needing its
  EEPROM address reprogrammed.

## 7. The alternative considered and rejected

A **2:1 divider into a spare `SN74LVC541A` channel** skips the comparator entirely: divided,
low = 0.435 V against the 0.8 V limit, high = 2.5 V against the 2.0 V minimum. Four resistors
instead of an IC.

**Spare channels do exist** — `pi-interface`'s level-shift package uses 1 of 8, so 7 are free.
(This was carried as UNVERIFIED in the design handoff; it is now checked. The alternative was
live, and is rejected on margin, not on availability.)

Rejected because FLIR label the whole table **"values are for reference only"**, and one row
of it (3.3 V / 200 Ω) reads 1.46 V. Divided, that row lands at 0.73 V — essentially on the
0.8 V limit. The comparator's margin is protection against a number the manufacturer declines
to guarantee, and it is the only option here whose margin is a **design choice** rather than a
datasheet coincidence.

## 8. What this changes elsewhere

| Document | Change |
|---|---|
| Spec §4 GPIO map | 26, 27 → frame-time inputs (were spare). Spares drop to 2 (18, 19, both PWM-capable) |
| Spec §12 item 1 | Restate: frame times return in hardware on GPIO26/27, *and* reach analysis as data via the camera logger aligned by barcode |
| Spec §9.1 panel inventory | Two of the three spare BNC ports are now used |
| `camera-sync-change.md` §3 | Its "if frame times are ever wanted in hardware, GPIO26/27 are free" is now taken |

## 9. Layout note

The 100 Ω series resistor and BAT54S clamp for each input are **drawn on the comparators
sheet** but must be **placed physically adjacent to their connector** (J6B on the back face,
J17B likewise), not next to the LM339. Sheet assignment is a schematic-organisation choice;
the protection only works if it is between the panel and the silicon in copper, too.
