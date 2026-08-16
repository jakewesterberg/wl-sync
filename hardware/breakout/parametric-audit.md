# Breakout board — parametric audit, 2026-08-15

Twenty-one findings from a pre-layout audit against primary datasheets. This document exists
separately from `design-review.md` because it records a different *kind* of check.

## Why this is separate from every check that already passed

Before this audit the board was ERC-clean, passing twelve structural checkers and an 80-test
suite. Seven of the findings below would have prevented it working.

That is not a failure of those checks. They verify **topology** — is this pin on the right
net, is this rail correct, do the isolated domains stay pin-disjoint, is the bit order
preserved through the buffers. Every finding here is **parametric** — does this current clear
a datasheet minimum, does this voltage survive the series drops in front of it, does this
converter's rating cover its load, does this footprint's axis match the mechanics of the
enclosure.

**The board was verified for connectivity and never for operating points.** Section 4 proposes
the checkers that close that gap.

Severity keys: **F** = would prevent correct operation. **M** = marginal or robustness.
**D** = documentation error. **R** = needs a human ruling.

---

## 1. Functional failures

### F1 — Optocoupler LEDs are under-driven on 30 channels

`ACSL-6xx0` (AV02-0235EN) Recommended Operating Conditions give **I_FH minimum 7 mA**, with
footnote b adding: *"It is recommended that minimum 8 mA be used for best performance and to
permit guardband for LED degradation."* V_F is 1.25 / 1.52 / **1.80 V** min/typ/max.

The LED anodes are fed from `+5V` through 430 Ω, cathodes sunk by `SN74AHCT541` outputs
(V_OL max 0.44 V at I_OL = 8 mA, SCLS269Q).

**The rail is not 5.00 V at the LED.** It arrives through `F4` and `D3`. Both pinned to
their datasheets:

- `1206L050` (Littelfuse 1206L): **R_min 0.15 Ω** un-tripped, R_1max ~0.6 Ω after a trip. With
  `F4` upgraded per M7, R_min falls to ~0.06 Ω → **0.027 V** at 0.45 A.
- `SS14` (Vishay 88746): V_F max **0.50 V, specified at 1.0 A**. At ~0.45 A, ≈0.35 V typ /
  0.42 V max.

Total series drop **0.38–0.45 V**, so the anode rail is **4.55–4.62 V**.

**15 mA is an absolute maximum, not a recommendation** — AV02-0235EN's Absolute Maximum
Ratings list *Average Forward Input Current (per channel) I_F 15 mA*. Both ends of the window
are hard.

| Series resistor | Worst case | Best case | Verdict |
|---|---|---|---|
| **430 Ω (as built)** | **4.4 mA** | — | Below the 7 mA switching floor |
| 240 Ω | 8.6 mA | **14.9 mA — 99% of absolute maximum** | Ceiling too close |
| 270 Ω | 7.6 mA — misses the 8 mA guardband | 13.2 mA | Safe, no aging margin |
| **249 Ω + ±2% supply** | **8.8 mA** | **13.7 mA** | **Both bounds clear with margin** |

At a ±5% supply the tolerance spread is **1.95:1** against a **2.14:1** window — it fits, but
nothing sits comfortably. The fix is to shrink the spread rather than chase the resistor.

**Fix:** 430 Ω → **249 Ω (E96)**, *and* specify the external +5 V supply at **±2%**. That
tolerance now protects 30 optocoupler channels, making it a load-bearing specification rather
than an assumption — it belongs in the spec beside the current rating. See also M7 (`F4`) and
F7 (the fan tap), both of which affect this rail.

**Also required:** the AHCT541's absolute maximum through its GND pin is **±75 mA**
(SCLS269Q §4.1), and `U8`/`U9`/`U10` each drive **eight** LEDs. Because the drive is
active-low and the event bus idles at `0x0000`, sixteen LEDs are lit **continuously** — a
steady state, not a transient. At 11 mA × 8 that is 88 mA, over the limit.

**Fix:** parallel two buffer outputs per LED, from tied inputs. Halving the effective
on-resistance also improves V_OL. Per-output current falls to ~5.5 mA against a
recommended 8 mA; package ground current to ~44 mA against 75 mA. Cost: 30 LEDs plus ~12
other outputs needs **9 packages against today's 5**.

**Rejected alternative:** driving the LEDs from +12 V would give a much tighter 1.25:1
tolerance spread, but with the anode at 12 V and the buffer output high at 5 V there is still
7 V across the LED and resistor — about 5.5 mA — so **the LED never turns off**. The anode
rail must sit at or below the driver's high level.

**Optional margin:** replacing `D3` with a P-channel MOSFET (≈20 mV instead of 350 mV) plus
the larger `F4` recovers ~0.45 V, letting 300 Ω give 7.9–12.8 mA. One new part type for real
headroom on both bounds.

### F2 — The inbound stim channel is under-driven, and depends on an undocumented switch

`RHS_STIM_OUT` puts its 100 Ω input resistor **inside** the LED current path:
`ISO_5V` → 430 Ω → LED → 100 Ω → Intan's output.

- Typical: **6.57 mA** — below the 7 mA minimum before any tolerance
- Worst case: **4.8 mA** — will not switch

**The Intan output level is a physical switch.** The Stim/Recording Controller user guide:
*"Switch 4 (CONFIG4) is used to select the voltage level of the digital output ports. With
CONFIG4 in the down position, 3.3 V digital signals are generated. With CONFIG4 in the up
position, 5.0 V digital signals are generated."*

At 3.3 V the LED anode sits at 5 V against a cathode resting at 3.3 V, leaking roughly
**1.3 mA against a 250 µA I_FL limit** — the channel may never turn cleanly off.

**Fix:** buffer Intan's output locally in the `INTAN_GND` domain off `ISO_5V`, and let that
buffer drive the LED. The 100 Ω and `BAT54S` then do protection only instead of sitting in
the current path, the off-state becomes a clean 0 V across the LED, and the design stops
depending on an external switch position or on an output drive capability the Intan guide
never specifies.

**Record regardless:** this rig requires **CONFIG4 UP**. It belongs beside `RewardPolarity`
HIGH as a configuration the design depends on and no checker can see.

### F3 — The photodiode transimpedance stage saturates

`R38` = 1 MΩ, `C48` = 3.3 pF, `R34` = 1 kΩ in series from the BNC to the summing junction,
`R35` = 10 Ω on the shield leg feeding IN+. Amplifier is `OPA2197` (dual; the second half is
PD2's TIA, so there is no spare channel).

With a Thorlabs **FDS100** (13.0 mm², 0.30 A/W at 550 nm, TO-5, spec sheet 0637-S01) against
a display patch at roughly 0.29 mW/cm², photocurrent is **~11 µA**. Into 1 MΩ that demands
**11 V out** — twice the ±5 V system convention and past both NI's and Intan's input range.

**Fix:** `R38` 1 MΩ → **180 kΩ**, putting peak white near 2.0 V.

**Fit a DNP parallel resistor position** across `R38` so gain can be trimmed at bring-up with
one component. A jumper at a summing junction that sensitive would be a leakage and noise
liability; a parallel pad is not.

### F4 — Four camera triggers share one unterminated buffer output

`CAM_TRIG_BEH` drives `J14`–`J17` from a single `SN74AHCT541` output — four parallel coax
runs, about 12.5 Ω of transmission line. The initial edge draws roughly **118 mA** against
the part's **25 mA** per-output absolute maximum. Short, but on every edge for the life of
the board, and with no source termination the cameras see reflections that can double-trigger.

A systematic sweep of every logic output on the board confirms this is the **only** net where
one output drives multiple BNCs. It is a single site, not a pattern.

**Fix:** one buffer channel per trigger, each with a **~47 Ω** series resistor to bring source
impedance near the 50 Ω line. This folds into F1's respreading, which already adds spare
channels.

### F5 — The isolated supply is over its per-rail rating

`IH1215D` is **±15 V at ±66 mA**, unregulated, 82% efficient (XP Power IH series datasheet).
`INA105` I_Q is **±1.5 mA typ, ±2 mA max** (SBOS145B).

| `ISO_P12` load | Typ | Max |
|---|---|---|
| 8 × INA105 V+ | 12 mA | 16 mA |
| `LD1117S50` input → `ISO_5V`: 6 opto output stages, 6 × 3.9 kΩ pull-ups, 2 × 430 Ω inbound LEDs | 57.5 mA | 93 mA |
| **Total** | **69.5 mA (105%)** | **109 mA (165%)** |

`ISO_N12` carries only 12–16 mA — about 20%. That imbalance puts the lighter rail **below the
25% floor at which cross-regulation is characterised at all**, so the ±5% figure does not
apply.

The `LD1117S50` also burns **0.37–0.58 W** dropping 12 V to 5 V for a digital load.

**Fix:** replace `LD1117S50` with a **second `TMA-0505S`** — already on the board as `U62`,
the NI-side fallback, so no new part number. It is 1 W / 200 mA, covering the 52–83 mA branch
with 2.4× margin.

| | Now | After |
|---|---|---|
| `ISO_P12` load | 69.5–109 mA (105–165%) | **12–16 mA (18–24%)** |
| `ISO_N12` load | 12–16 mA | 12–16 mA — **balanced** |
| LDO heat | 0.37–0.58 W | eliminated |

This **improves** the noise architecture rather than compromising it: the new converter powers
only optocoupler output stages, while the eight Intan-domain INA105s stay behind the ferrite
pi filter and the two low-noise LDOs. Today those analog parts share `ISO_P12` with a 57 mA
digital branch; afterwards they do not.

At ~20% load an unregulated module's output rises — ±15 V may reach ~±16.5 V. The
`TPS7A4901`/`TPS7A3001` accept 35 V and regulate to ±12 V, so they absorb it.

### F6 — 31 BNCs are on a footprint pointing at the enclosure lid

`BNC_PanelMountable_Vertical` is described in KiCad's library as *"Panel-mountable BNC
connector mounted through PCB, vertical"*, with a **9.65 mm drill and 15.24 mm annular pad**
for the barrel to pass through the board. The connector axis is **perpendicular to the PCB**.

On a 430 × 240 mm board lying flat in a 2U chassis, those BNCs point at the lid, not at the
front and back panels where all 31 are supposed to emerge. The footprint only makes sense if
the panel is parallel to the board.

It also costs roughly **5,650 mm² of pad and 2,270 mm² of removed copper on every layer**,
punched through a mixed-signal board carrying five ground domains.

**Fix:** a right-angle isolated part. See §3 for selection status.

### F7 — The fan fuse is defeated by its own placement

`F1` is fed from `+12V`, downstream of `F2`. Both are `1206L050` — 0.5 A hold, 1.0 A trip —
**in series**. A fan fault drawing 0.9 A is below `F1`'s trip but well over `F2`'s hold, so
**`F2` can trip first and kill the analog rails to protect the fans**, inverting the entire
purpose of giving the fans a separate fuse.

It also means `F2` carries main load **plus** fans: 337–361 mA, not the ~100 mA the main rail
alone draws.

**Fix:** tap `FAN_12V` from **`P12_FUSED`** instead of `+12V` — upstream of `F2`, so the two
fuses become parallel branches. `F2` then carries 97–121 mA (3.7× margin at 0.5 A, unchanged)
and `F1` protects the fans independently. One net change, better than upsizing `F2`.

---

## 2. Marginal, robustness and documentation

### M1 — TIA under-compensated at long cable

Thorlabs publishes FDS100 capacitance only at 20 V bias (24 pF); at the **zero bias** this
design is forced into it is far higher — an estimated 130–380 pF. With 3–5 m of coax, total
input capacitance reaches **440–900 pF**. Optimal C_f rises to ~3.8 pF against the fitted
3.3 pF, and `R34` = 1 kΩ adds a pole at ~265 kHz, only 5× above the ~51 kHz loop crossover,
costing a further ~11° of phase margin. Ringing on precisely the edge used to time stimulus
onset.

**Fix:** with `R38` at 180 kΩ (F3), set **C_f = 22 pF**. Deliberate over-compensation:
−3 dB at **40 kHz** — still four times the ~10 kHz these channels need — and unconditionally
stable up to **5.5 nF** of input capacitance, about 50 m of coax. The stage stops depending
on cable length and diode spread.

### M2 — Photodiode mux tap unbalances the difference amplifier

The Intan-side `INA105` has the mux output on IN+ and **AGND on IN−**, so anything in series
with the mux leg unbalances it. `ADG1206` R_on at ±12 V is ~180 Ω (the 120 Ω headline is the
±15 V figure; the +12 V single-supply table gives 300 Ω typ / 475 Ω max).

| Channel group | Gain error | CMRR |
|---|---|---|
| `INA105KU` alone | — | 72 dB min |
| 14 channels (op-amp driven) | 0.36% | ~49 dB |
| **`A_PD1` / `A_PD2`** (1.6 kΩ RC + R_on) | **3.3%** | **~30 dB** |

49 dB is adequate — tens of millivolts of inter-rack ground offset becomes sub-LSB against
Intan's 0.31 mV. The photodiode channels are the outlier because they tap `A_PD1`, the node
*behind* the 1.6 kΩ anti-alias resistor.

**Fix, free:** `U32C` already sits there as a unity-gain follower producing `A_PD1_NI_BUF`.
Retap the eight mux `S7`/`S8` inputs to the buffer outputs. A net reassignment, no new parts.

### M3 — Six panel outputs have no series resistance and no clamp

Every input on this board has series resistance plus a `BAT54S`; every Intan output has a
series resistor. `J13`–`J17` (camera triggers) and `J6` (reward driver out) have neither.
`J6` is driven straight from a `SN74HCT32` gate output onto a panel BNC that runs to a
solenoid driver — an inductive kick or a mis-plug feeds directly into the gate.

**Fix:** series resistance on all six, per F4 for the triggers.

### M4 — Comparator outputs rise in 2.2 µs

The `LM339` outputs sit behind 10 kΩ pull-ups driving five loads each — GPIO, two AHCT541
inputs, the 1 MΩ feedback, and trace. Into ~100 pF that is a **2.2 µs rise**, from a
comparator whose purpose is precise stimulus-onset timing.

**Fix:** 10 kΩ → **2.2 kΩ**, sharpening to ~0.5 µs for 1.5 mA on a +3V3 rail with headroom.
The LM339 sinks 16 mA, so this is well inside spec.

### M5 — Back-injection when unpowered

The spec's claim that "a mis-plug costs wrong data rather than hardware" holds **powered**:
+5 V carries 290 mA of LED load, comfortably more than a single fault injects. With the board
**off**, a 12 V fault charges the dead rail through the clamp toward **11.6 V**, past the 7 V
absolute maximum of the AHCT541 and the HCT gates.

**Fix:** a 5.6 V zener or 5.0 V TVS on +5 V. One part.

### M6 — Mux supply current unbudgeted

`ADG1206` I_DD is 0.002 µA with digital inputs at 0 V or VDD, but **260 µA typ / 420 µA max
at intermediate levels** — the input stage sits in its linear region and draws shoot-through.
The address lines rest at 3.3 V against a 12 V rail. Across eight muxes that is **~3.4 mA on
+12 V**: affordable, but it belongs in the budget rather than being discovered on a meter.

### M7 — Fuse sizing

With every fix applied:

| Rail | Load (typ / max) | Fuse | Verdict |
|---|---|---|---|
| +5 V | 392 / 495 mA (762 mA if the NI-fallback TMA is populated) | `F4` 0.5 A (~380 mA derated) | **Trips — needs ≥1.1 A, 1.5 A if both TMAs fit** |
| +12 V | 97 / 121 mA *after F7* | `F2` 0.5 A | 3.7× — unchanged |
| −12 V | 54 / 66 mA | `F3` 0.5 A | 5.8× — unchanged |
| FAN_12V | 240 mA | `F1` 0.5 A | 1.6× derated — **0.75 A recommended** |

The +5 V figure also sets the external supply: **0.5 A minimum, 0.8 A if the NI fallback is
ever populated.**

### D1 — The threshold DAC cannot reach 4.096 V

`MCP4728` VDD is **+3V3** and its outputs are rail-limited, so the documented **4.096 V
ceiling is unreachable** — the real ceiling is ~3.29 V. Configured for internal reference ×
gain 2 (which is what produces 4.096 V nominal), **the top fifth of the code range is dead** —
a silent nonlinearity.

Raising the DAC to +5 V does not work: its I²C inputs would need 0.7 × VDD = 3.5 V and the bus
runs at 3.3 V.

**Fix:** internal reference, **gain 1** — a clean 0–2.048 V, linear across all 4096 codes at
0.5 mV steps, absolute rather than ratiometric. The accelerometer's 5–30% gate band sits at
0.49–1.68 V, comfortably inside. Strike the "4.096 V → 81% of full-scale motion" figure.

Pairs with F3: at 180 kΩ the photodiode's peak white lands near 2.0 V, so the DAC spans the
entire signal.

### D2 — `INA105KU` CMRR is 72 dB, not 86 dB

86 dB is the **BM** grade. The board uses **KU**: 72 dB min / 90 dB typ (SBOS145B).

### D3 — Panel thickness limits are unconfirmed for the parts actually chosen

**Corrected 2026-08-16.** This finding originally cited **2.00 mm max** from 3M's MDR
drawings (TS-0620-B, TS-0621-C). **The board does not use a 3M connector** — it uses MH
Connectors `3700-0121-01`, whose drawing states no panel thickness at all. The constraint was
recorded from a part that was evaluated and not selected.

What is confirmed:

| Connector | Panel thickness | Source |
|---|---|---|
| M12 inlet (Phoenix 1551833) | **max 3.5 mm** | Installation drawing 00662206 Index 2 |
| MDR68 (MH 3700-0121-01) | **not stated** — panel mounting is #2-56, 2 places | MH drawing rev 3.0 |
| BNC | unknown | pending |

A 2U rack panel is commonly 2–3 mm aluminium, so this still needs settling before machining —
but by asking MH and the BNC vendor, not by assuming 3M's figure applies.

---

## 3. Needs a ruling

### R1 — The M12 inlet has no shield pin — **RULED 2026-08-16**

| Pin | Net |
|---|---|
| 1 | `P12_RAW` |
| 2 | `N12_RAW` |
| 3 | `P5_RAW` |
| 4 | `AGND` |
| 5 | **`AGND`** |

Spec §9.1 specifies "+12 V, −12 V, +5 V, GND, **shield**". Pin 5 is a second ground instead.

**Ruling: keep pin 5 as a second ground; the cable shield bonds through the connector shell
to the panel.** This became available only once the M12 was established as panel-mounted
rather than board-mounted (see below) — the threaded shell contacts the chassis directly, so
shield current terminates on chassis earth and never enters signal ground, while pin 5 keeps
halving the supply return resistance. The spec's five-conductor intent is satisfied; the fifth
conductor is simply the shell rather than a pin.

> **One verification this ruling now depends on.** The bond only exists if the connector's
> M12 thread is metal. The Phoenix datasheet states contact material CuZn and contact carrier
> PA 66 but does not name the body material in what has been retrieved. **Confirm the thread
> is metallic before treating the shield as terminated** — if the body is plastic, this ruling
> reverts to routing pin 5 to the chassis earth stud.

**Gender, resolved:** the board is the device and should carry pins so the live cable end has
no exposed metal. Phoenix `1551833` is a **Pin (male)** type, which is correct. The originally
specified part and the Binder candidate were both female.

**Mounting, resolved:** the pins emerge axially, the part is rated by wire gauge (AWG 22-20),
and tightening torque is 1.5–2 N·m. It is a panel-mount connector with flying leads, **not a
board-mount part**. The board needs a 5-way header, and the custom `M12A_5_Panel` footprint
can be deleted — one of three custom footprints retired.

**New finding this exposed:** as built, the M12 is a board-mounted part, so every cable
insertion and every 2 N·m tightening loads PCB pads directly. That is a mechanical defect
independent of the footprint's dimensions.

### R2 — `J19` DB37 shell floats — **RULED 2026-08-16**

The ACCES I/O eye-tracker connector's shell pad carries no net, while every BNC shell on this
board lands deliberately.

**Ruling: fit a selectable resistor position between the shell and `AGND` — 0 Ω / 10 Ω / DNP,
populated at bring-up.** This matches the design's existing philosophy rather than inventing a
new one: rig inputs are received differentially precisely so bonding decisions can be deferred
or reversed without touching the board. The eye-tracker cable runs to a separate
mains-powered PC, which is a materially different situation from the battery-powered booth
sensors, so committing that bond in copper now would be guessing. One pad, one resistor.

### R3 — LDO thermal pads carry no net — **RULED 2026-08-16**

`U3` and `U4` pad 9 (the PowerPAD) has no net. Both datasheets permit "left open", but both
also say *"Solder to the PCB plane to enhance thermal performance"* and explicitly allow
tying to GND.

**Ruling: add pin 9 to both hand-built symbols and tie it to `INTAN_GND`.** The datasheet-
preferred option. It removes the layout ambiguity of a large unnetted pad beneath a 0.65 mm-
pitch part, gives both regulators a thermal path, and prevents an accidental connection to
whatever plane passes underneath. Dissipation is modest (~0.16 W and ~0.07 W), so this is
about determinism rather than heat.

## 4. Verified correct

Recorded because a clean result is evidence too.

- **All five hand-built symbols** against primary datasheets: `TPS7A4901` and `TPS7A3001`
  (SBVS121E / SBVS125D — TI genuinely uses an identical DGN pinout for the complementary
  pair), `ACSL-6400` and `ACSL-6420` (AV02-0235EN Figures 10 and 12, pin for pin),
  `ADG1206YRUZ`.
- **LED drive polarity on all 32 channel positions** — active-low drive correctly cancels the
  optocoupler's inverting truth table. Polarity is right board-wide.
- **Sync-module 40-pin header map**, pin for pin.
- **Board-wide footprint pad coverage** — every footprint resolved on disk; only three pads
  carry no net (R2, R3 above).
- **Comparator topology**: signal on IN+, DAC threshold on IN−, V+ on +12 V, V− on `AGND`,
  hysteresis **32.7 mV** with the documented 1% gain error, both pull-ups on `+3V3`.
- **Mux digital interface**: `ADG1206` inputs are TTL (V_INH 2.0 V, V_INL 0.8 V) independent
  of the supply rails, so 3.3 V address lines correctly drive a part on ±12 V; `EN` tied to
  +12 V is within spec.
- **Microphone filter**: 4th-order Butterworth at 12.0 kHz — measured Q of 0.548 and 1.306
  against the ideal 0.5412 and 1.3065, **within 1.2%**. `U23D`, the unused channel, correctly
  wired as a grounded unity follower.
- **Level shifters**: `SN74LVC541A` on +3V3 with both OE tied low, 5 V-tolerant inputs
  receiving 5 V logic.
- **`INA105` supply and swing**: ±12 V inside the recommended ±5 to ±18 V range; output swing
  ~±9 V typ / ±7 V worst case against a ±5 V convention; NC pin correctly floating; load
  capacitance stability 1000 pF with the 100 Ω series resistors correctly isolating the coax.
- **I²C addressing**: `U67` strapped 000 → 0x20, `U68` 001 → 0x21, matching their labels and
  distinct from the MCP4728's factory 0x60; `/RESET` released on both; 2.2 kΩ pull-ups giving
  1.5 mA sink. Run the bus at **100 kHz** — into ~150 pF the ~280 ns rise is fine against the
  1 µs limit but marginal against 400 kHz's 300 ns.
- **Reverse-polarity diodes**: all three `SS14` correctly oriented, including `D2` conducting
  supply-ward on the negative rail.
- **Clamp coverage**: 19 digital clamps to DGND/+5V behind 100 Ω (survives a ±24 V mis-plug),
  16 analog to ±12 V behind 1 kΩ (survives ±48 V), one to `INTAN_GND`/`ISO_5V`. `FAN_RTN`
  star-joins through `NT2`.
- **Fan-out**: `CAM_TRIG_BEH` is the only net where one logic output drives multiple BNCs.
- **Thermal**: 19.4 W total giving **under 3 °C rise at 12 CFM**, vindicating the low-speed
  fan decision. LED resistors at 32% of an 0603's rating; ACSL input dissipation 15 mW of
  27 mW per channel, output 29 mW of 65 mW.
- **Lifecycle**: `ACSL-6400-00TE` Active and stocked; `INA105KU` Active, datasheet revised
  December 2025; `IH1215D` and `TMA-0505S` stocked with no obsolescence signal; `ADG1206YRUZ`
  Active with `ADG5206` as a pin-compatible successor.

---

## 5. Checkers this audit implies

Every finding above was invisible to twelve structural checkers because they ask topological
questions. These are the parametric assertions that would have caught them.

| Assertion | Catches |
|---|---|
| Every LED branch current computed from rail, V_F max, driver V_OL **and the series drops in front of the rail**, compared against the part's I_FH min | F1, F2 |
| Package pin current summed across all channels against the driver's GND absolute maximum | F1 |
| Every amplifier's output demand at full-scale input against its supply rails and the system convention | F3 |
| Count of panel connectors per driver output, and presence of series termination | F4, M3 |
| Every isolated supply's total load against its per-rail rating, with load balance | F5 |
| Footprint axis orientation against the face each connector is assigned to | F6 |
| Series fuse coordination: upstream hold current above downstream trip current | F7 |
| DAC output range against its own supply rail, not just its reference setting | D1 |
| Pull-up RC against the timing requirement of the signal it carries | M4 |

The pattern is that each reads a **value** and compares it to a **datasheet limit**, rather
than reading a net and comparing it to an expected name.

---

## 6. Sources

All primary, all retrieved during the audit.

| Part | Document |
|---|---|
| `ACSL-6xx0` | Broadcom AV02-0235EN |
| `SN74AHCT541` | TI SCLS269Q |
| `SN74AC541` | TI SCAS958 |
| `TPS7A4901` | TI SBVS121E |
| `TPS7A3001` | TI SBVS125D |
| `INA105` | TI SBOS145B |
| `ADG1206` | Analog Devices ADG1206/ADG1207 |
| `IH1215D` | XP Power IH series |
| FDS100 | Thorlabs 0637-S01 Rev E |
| Intan RHS | Intan Stimulation/Recording Controller User Guide |
| MDR68 | 3M TS-0620-B (plug), TS-0621-C (receptacle) |
| BNC | Bomar/Winchester BNC Products Spec |
