# Sync-box breakout PCB — design

**Written 2026-08-13.** The board that sits between the behavioural task PC, the sync box
(a Raspberry Pi Compute Module 5 on its official IO Board — §4.1), and everything that records. It is a hub: every rig signal terminates here,
is conditioned once, and is fanned to each destination that needs it.

**This repository is public.** The routing requirements originate in a private design spec in
`wl-preproc` (§4.1–§4.4, §4.7, §12). Constraints are restated here as electrical facts so the
board can be built and reviewed from this document alone; no private text is reproduced.

---

## 1. What the board is for

Two rigs, identically equipped. Without this board each rig is hand-wired, which makes the two
rigs different in ways nobody has written down. The board exists so that a rig is reproducible
by construction rather than by documentation discipline.

It performs no logic beyond one OR gate and three comparators. It conditions, isolates, buffers
and fans out.

**Scope decision:** the board is the rig's complete patch panel, not only the event-code
fan-out. Signals that never reach the Pi (analog sensors bound for the recorders, the stim
trigger's ephys copies) still terminate here, because a signal routed outside the box is a
signal nobody recorded a decision about.

---

## 2. Decisions register

| # | Decision | Rationale |
|---|---|---|
| 1 | One board, two rigs, fully populated | Rigs are identical; a depopulation strategy would be insurance against variability that does not exist |
| 2 | Sync-box board inside the same enclosure | Frees GPIO0/1 — no HAT is fitted, so nothing supplies an ID EEPROM — which is what makes the contiguous capture range and two hardware-PWM triggers coexist |
| 2a | **CM5 Lite 4 GB, no wireless (`CM5004000`) + official CM5 IO Board**, not a Pi 5 | Onboard M.2 removes the PCIe flex cable, which is a mechanical failure mode in a rack chassis that gets slid in and out while carrying the boot device. Production committed to ≥ Jan 2036. **Conditional on floorplan — see §9.4** |
| 3 | **Optocouplers** on digital, **not** capacitive/magnetic digital isolators | Digital isolators transmit by modulating an RF carrier — a deliberate RF source beside headstages. Optocouplers have no carrier. Speed is irrelevant at this timing budget |
| 4 | **Difference amplifiers** on analog, not isolation amplifiers | 33 analog stages cannot be galvanically isolated affordably, and isolation amplifiers degrade the signals most needing fidelity |
| 5 | NI analog driven **NRSE**, not per-channel differential | All sources share one reference by construction, so AISENSE tied to AGND gives the same rejection while keeping 32 channels instead of 16 |
| 6 | Two buffer families, one per direction | A single part cannot safely do both 5 V→3.3 V and 3.3 V→5 V. See §7 |
| 7 | Intan's 8 analog channels selected by **Pi-controlled mux**, not jumpers | Routing becomes software state the Pi records, rather than a physical fact somebody must document correctly for a decade |
| 8 | Comparator thresholds set by **I²C DAC**, not trimpots | Same argument, and it matters more: the accelerometer threshold is a behavioural parameter that gates task progression |
| 9 | Mux and DAC control over **internal USB**, not GPIO | Preserves two spare GPIO on a header that is otherwise full |
| 10 | ~~Comparator threshold range capped at ~4.1 V~~ **Resolved 2026-08-15.** With the accelerometer's range now known (0–5.000 V, 0.250 V pedestal), a threshold at the DAC's 4.096 V ceiling sits at **81 % of full-scale motion** and the floor must stay above the pedestal — a real motion gate lives near 5–30 %, so the ceiling does not bind. The photodiode TIA's output range is ours to set at design time | ~~Before fab~~ |
| 11 | **Nothing writes the DAC thresholds or the mux addresses yet.** The board supports both over the internal USB → I²C path, and `hardware/README.md` records the addresses and the mux truth table — but the control layer is `wl-sync`'s work and is **outside this plan entirely.** As built, the board powers up with whatever the DAC's power-on default happens to be, and the mux routing is undefined until something sets it. Needs a setup workflow too: thresholds are found by watching the analog copy of the same signal on NI or Intan while sweeping the DAC, since there is no panel affordance by design | Before first use |
| 12 | ~~Display sync reserved position~~ **Closed 2026-08-15 — dropped entirely.** The photodiode flip patch measures at the display surface and catches post-GPU drops a vsync tap structurally cannot. No component ever existed behind the reservation; the panel position is freed | ~~—~~ |
| 13 | **What connector does the reward driver take?** Specified BNC to match the panel and because TTL over coax is standard, but the driver is unchosen. This is the only reward position whose connector is set by equipment not yet selected — confirm before the panel is machined | Before fab |
| 14 | ~~`RWD_CMD` polarity~~ **Closed 2026-08-15.** MonkeyLogic exposes **`RewardPolarity`** as a Main Menu setting, TTL HIGH or LOW, provided so people can match relays that trigger on sinking. Never an unknown to discover — a configuration to set. **Set it HIGH** to match this board's active-high OR input, and record it in the rig's MonkeyLogic config beside the strobe timing | ~~Before first use~~ |
| 10 | **No on-board switching regulators** except one isolated DC-DC | Consistency with decision 3: rejecting an RF carrier and then adding a switcher would be incoherent |
| 11 | 2U rack chassis, board-mount connectors through machined panels | ~40 panel positions do not fit a smaller case; board-mount eliminates internal hand wiring |
| 12 | One board, not two | Splitting would put 30+ analog signals through an inter-board connector |

---

## 3. Signal contract

### 3.1 Digital

| Signal | Source | Task PC | Pi | NI (rec) | Intan | Other |
|---|---|---|---|---|---|---|
| Event code data ×16 | Task PC | — | ✓ | ✓ | — | |
| Event strobe | Task PC | — | ✓ | ✓ | ✓ | |
| Barcode | Pi | — | — | ✓ | ✓ | |
| ohDPI camera trigger | Pi | — | — | — | — | eye cameras |
| Behavior camera trigger | Pi | — | — | — | — | behavior cameras ×≤4 |
| Photodiode 1 comparator | Board | ✓ | ✓ | ✓ | — | decouples NI onset timing from scan rate |
| Photodiode 2 comparator | Board | ✓ | ✓ | ✓ | — | same |
| Accelerometer motion trigger | Board | ✓ | ✓ | — | — | gates task progression |
| Reward commanded | Task PC | — | ✓ | ✓ | ✓ | → OR |
| Manual reward button | Panel | — | — | — | — | → OR |
| Reward delivered | Board (OR out) | — | ✓ | ✓ | ✓ | → reward driver |
| Stim trigger | Task PC | — | ✓ | ✓ | ✓ | |
| RHS stim output | Intan | ✓ | — | ✓ | — | |
| Display sync | — | — | — | — | — | reserved, unpopulated |

Digital line counts: **24** into the recording NI, **23** at the task PC (19 out, 4 in),
**5** into Intan (of 8 available), **1** out of Intan.

**The two photodiode comparators reach NI as well as the Pi and task PC, and it is what lets the
analog scan rate be slow.** Without them, NI's only stimulus-onset information is the analog
photodiode waveform, which would force a fast scan across all 16 channels to time an edge. With
them, NI has a clean digital edge and the analog scan rate becomes a question about waveform
fidelity alone. They cost the last two spare lines on connector 1 (§9.2) and buy a cheaper card
(§9.3).

**Manual reward is a one-shot.** A monostable gives every hand-delivered reward an identical
duration regardless of how long the button is held, so manual rewards are countable rather than a
per-press variable nothing measures — which is the point of recording commanded and delivered
separately at all. Debounce is **10 ms** (10 kΩ × 1 µF), covering both switch bounce and any
connector transient. Panel button is **recessed** so a sleeve or cable cannot dispense fluid.

**Reward is recorded twice on purpose.** The task PC's commanded TTL and the debounced panel
button feed an OR gate; the OR output drives the reward driver and is separately recorded as
"delivered". A manual reward is therefore *delivered without commanded*, derivable with no
extra logic — and it is recorded on training days, which is exactly when an unlogged hand-
delivered reward would otherwise become a silent confound.

### 3.2 Analog — 16 sources

| Signal | Ch | Source | Task PC | NI (rec) | Intan |
|---|---|---|---|---|---|
| Eye X/Y, both eyes | 4 | ACCESIO USB-AO16-8A | ✓ | ✓ | mux |
| Eye pupil, both eyes | 2 | ACCESIO USB-AO16-8A | — | ✓ | mux |
| Photodiode ×2 | 2 | **passive diode**, coax | — | ✓ | mux |
| Ambient light | 1 | battery-powered, in booth | — | ✓ | mux |
| Accelerometer motion energy | 1 | battery-powered custom device | — | ✓ | mux |
| Joystick X/Y | 2 | battery-powered, in booth | ✓ | ✓ | mux |
| Microphone | 1 | battery-powered, in booth | — | ✓ | mux |
| Misc analog | 3 | panel BNC, ÷1/÷2 selectable | ✓ | ✓ | mux |
| **Totals** | **16** | | **9** | **16 of 32** | **8 of 8** |

**33 buffered analog output stages.**

**Intan's ceiling is 8 analog inputs and it is hard.** Two on the base controller plus six on
the I/O Expander. Sixteen sources want to reach it; eight can, selected by mux (§6.3).

This is a convenience loss rather than a data loss: every device is aligned into session time
by barcode, so a channel recorded on NI is available in Intan's timebase after preprocessing.
What Intan's eight buy is a native copy at the amplifier sample rate with no alignment step.

**The two photodiodes have distinct roles, now pinned.** `A_PD1` watches the **task patch** — the
stimulus-onset signal. `A_PD2` watches the **flip patch**, a corner region alternating every
refresh, making it a frame clock. Their timing reaches the recorders through the comparator lines;
the analog copies carry waveform and intensity.

**Default eight for Intan**, settable at any time since the mux makes this software state:

| # | Channel | Why it is here |
|---|---|---|
| 1 | `A_PD1` task patch | stimulus onset — the frame clock's timing is better served by its comparator line |
| 2–4 | `A_EYE_LX`, `A_EYE_LY`, `A_EYE_LP` | **one complete eye** — position and pupil together beats partial data from both |
| 5–6 | `A_JOY_X`, `A_JOY_Y` | no record anywhere else |
| 7 | `A_MIC` | no record anywhere else |
| 8 | `A_ACC` | no record anywhere else |

Ambient light and the misc inputs are not in the default set. Left eye is the default; either eye
is one mux write away.

**Recommended de-prioritisation for the eight:** the six eye channels have an authoritative
record on the eye-tracker PC and should be the first dropped. The photodiodes, joystick,
accelerometer, microphone and ambient sensor have no record anywhere except what this board
delivers.

---

## 4. Raspberry Pi GPIO map and sync-box hardware

| GPIO | Direction | Signal |
|---|---|---|
| 0–15 | in | Event code data ×16 |
| 16 | in | Event strobe |
| 17 | out | Barcode |
| 18 | out | ohDPI camera trigger (hardware PWM) |
| 19 | out | Behavior camera trigger (hardware PWM) |
| 20, 21 | in | Photodiode comparators |
| 22 | in | Reward commanded |
| 23 | in | Reward delivered |
| 24 | in | Stim trigger |
| 25 | in | Accelerometer motion trigger |
| 26, 27 | — | **spare** |

**26 of 28 used.** The header is effectively full: any future signal needing a Pi input is a
design change, not a populate option.

**Why GPIO0–16 for the capture range.** PIO parallel capture reads a contiguous pin range, and
camera triggers must land on a hardware PWM pin (12, 13, 18, 19). Every 17-wide contiguous
window inside GPIO2–27 contains 12 and 13, and only the window starting at 2 leaves even one
PWM pin free — insufficient for two triggers. Starting at 0 consumes 12 and 13 while leaving 18
and 19 free. This is available only because the Pi is a separate board inside the enclosure
rather than a HAT, so GPIO0/1 are not holding an ID EEPROM.

**Boot contention, and it must be designed for.** The Pi probes GPIO0/1 as I²C at boot looking
for a HAT ID. A buffer driving those pins contends with that probe. Mitigation: series
resistors on GPIO0/1 and `force_eeprom_read=0` in `config.txt`.

**Mux and DAC control does not use GPIO.** An internal USB cable from a Pi USB-A port to an
on-board USB-I²C bridge (MCP2221A-class) drives the mux address expanders and the threshold
DACs. All of it is configuration-time state with no timing requirement. This is what preserves
GPIO26/27 as spare.

### 4.1 Storage — NVMe, and the adapter must not touch the header

The Pi **boots and logs to an NVMe SSD on a PCIe-FPC-only adapter.** Not an SD card, and not a
GPIO-header-mounted HAT.

**The constraint is not negotiable, and it follows directly from the GPIO map above.** A
conforming HAT or HAT+ carries its ID EEPROM on ID_SD/ID_SC — which *are* GPIO0 and GPIO1, and
which carry event-code bits 0 and 1. Fitting one collides with the event bus. Mechanically it is
worse: the 40-pin header carries the ribbon to this board, so a header-mounted adapter has
nowhere to sit.

**The CM5 IO Board's onboard M.2 M-key socket (PCIe Gen 2 x1) is the answer**, and it is the main
reason the module choice landed on CM5 rather than a Pi 5: it removes the PCIe flex cable
entirely. A flex ribbon carrying the boot device, inside a chassis that gets slid in and out of
a rack for a decade, is a mechanical failure mode worth designing out.

> **Host and HAT are not the same thing, and conflating them cost a wrong conclusion once.** The
> ID EEPROM lives on the **HAT**, not on the host — ID_SD/ID_SC are how a host reads a HAT that
> brings its own. That is why an M.2 HAT+ is disqualified: it *is* a HAT, so it supplies the
> EEPROM and occupies the header. A CM5 IO Board is the host, so GPIO0/1 are as free there as on
> a Pi 5, and the same mitigation applies to both.

**Boot from the M.2 drive, with a CM5 Lite** (no eMMC). Reflashing is then identical to booting a
Pi 5 from NVMe; an eMMC variant would need `rpiboot` over USB for every reimage, which is a
worse morning for whoever maintains the rig.

**Throughput is not why.** The Pi logs its own camera-trigger edges at 500 Hz, a photodiode flip
patch at the display refresh rate, a barcode frame per second and a handful of behavioural
lines — roughly 2,000 edges/s, about **40 KB/s, or ~1 GB for an eight-hour session.** An SD card
would keep up without noticing, and capacity is a non-issue since sessions transfer to the NAS
at the end. 256 GB is months of margin.

**Reliability is why.** SD cards fail abruptly and on power loss, and a rig switched off at the
wall for a decade is close to the worst case for them. The consequence is asymmetric: the Pi is
the **sole recorder on training days**, so its storage failing does not degrade a session, it
loses one entirely. **Boot from the NVMe rather than merely mounting it for data** — leaving the
SD card in the boot path preserves exactly the failure mode being designed out.

**Active cooler fitted.** The module wants it, and it sits in a closed chassis beside analog
circuitry that would rather not be warmed. See §9.4.

### 4.2 Module variant: CM5 Lite, 4 GB, no wireless — `CM5004000`

**No wireless, and this is the part with a technical argument behind it.** A live 2.4/5 GHz
transceiver and Bluetooth radio, powered inside a sealed enclosure alongside 33 analog stages, a
microphone preamp, and a few feet of headstage cable recording microvolts, contradicts the
reasoning used everywhere else in this design: digital isolators were rejected for modulating an
RF carrier (§2 decision 3), and on-board switching regulators were rejected for consistency with
that (§8). Buying a module with a radio in it and disabling it in software would be the same
mistake with more steps. Nothing is traded away — Ethernet is already required for NAS transfer
at session end, and the sync box has no reason to talk to anything else. The non-wireless SKU is
also cheaper.

> **Ordering trap.** The digit after `CM5` is the wireless flag. `CM5104000` is the *wireless*
> 4 GB Lite part, and resellers stock the wireless variants by default because for general use
> they are the obvious choice. The part wanted here is **`CM5004000`**.

**4 GB, not 8 GB.** The service holds well under 1 GB — Pi OS Lite headless at 200–400 MB plus a
Python process — and the log streams to disk rather than accumulating, so a session's gigabyte
never sits in RAM. 8 GB serves no identifiable workload on this machine.

**The upgrade path is real, which is what makes 4 GB safe rather than merely cheap.** CM5 is a
socketed module and the OS lives on the IO Board's M.2 drive, so raising RAM later means
swapping the module and booting from the same disk — no reimaging, no re-provisioning. A Pi 5
has soldered RAM, so the equivalent upgrade would be a new board plus relocating the NVMe
adapter. This is an advantage of the CM5 choice that was not enumerated when it was made.

---

## 5. Grounding and isolation

### 5.1 The premise

Every device here is earthed through its own power cord, so **ground loops exist before this
board is installed.** Eliminating them is not achievable and is the wrong target.

The achievable goal: **no loop current in a path that shares impedance with a signal return.**

### 5.2 Three mechanisms

| Mechanism | What it buys | Where |
|---|---|---|
| Optocoupler | Breaks the conductive path for one digital line | Digital crossing into an ephys chassis |
| Differential/NRSE drive | Receiver rejects the ground difference; no galvanic break | Destinations with a differential or non-referenced input |
| Split planes, single star | Keeps unavoidable return currents out of the analog reference | Internal, everywhere |

### 5.3 Domains

- **AGND** — analog island: all 16 sources, their input buffers, comparator front ends, the mux
  bank. Every sensor here is *structurally* loop-free: none has a ground of its own, so the
  board defines their reference and no second path exists.
- **DGND** — digital island: Pi, buffers, opto input sides, task PC returns.
- Joined at a **single star point at power entry.**
- **NI_GND** — exists only on the far side of optocouplers. Contains no analog stage, because
  NRSE lets NI's copies be driven from AGND (§6.2).
- **INTAN_GND** — optocoupler output sides plus the eight difference-amplifier output stages.

### 5.4 Why the task PC is not isolated

Isolating it costs 19 additional optocouplers and buys little: the task PC is not an ephys
chassis, and its return current is confined to DGND by the plane split, where the analog
section does not see it.

**Stated as an accepted risk rather than an oversight.** If bench measurement shows task-PC
earth noise reaching the analog channels, isolating those lines is the fix — and it would be a
respin, not a populate option, because 19 optocouplers is real board area.

### 5.6 The Faraday cage, and why rig-facing inputs are received differentially

**The rig sits inside a Faraday cage sound booth.** Sensors inside it are battery powered so that
no mains conductor crosses the cage wall; only signals pass, through BNC bulkhead feedthroughs.
Runs are **2–3 m sensor to bulkhead, 1–2 m bulkhead to this board.**

Battery power makes each sensor genuinely floating: its only ground path is its own coax shield,
so no loop can form at the sensor. That is stronger than the claim §5.3 makes for the analog
island, not weaker.

**But the shield cannot simply be lifted at this end, because it is the signal return.** Open it
and the signal has no path. So flexibility about the bulkhead cannot be a ground-lift jumper.

**Whether the bulkhead bonds each shield to the cage shell is undecided, and this board is
designed not to care.** The tension is real and has no free answer:

- **EMC says bond at the penetration.** An unbonded conductor crossing a shield wall is an
  aperture, degrading the thing the cage exists to do.
- **Loop reasoning says do not**, so each shield references only AGND.

**Resolution: receive every rig-facing sensor differentially.** The shield ties to AGND through
~10 Ω, providing the return and a DC reference, and the input stage senses **centre against
shield at the connector** rather than assuming the shield sits at AGND. If the cage also bonds
the shield, circulating current develops a voltage across that 10 Ω which the difference
amplifier rejects as common mode instead of adding to signal.

This uses the part family already specified for the Intan side, so it costs stages rather than
new parts — and it means the bulkhead decision can be made, or reversed, without touching the
board.

**Rig-level, above this board:** if shields do bond at the bulkhead, the cage and the rack should
be bonded to each other at **one deliberate point**, so there is a single ground system rather
than two competing ones with the signal shields arbitrating between them.

### 5.5 Why NI and Intan are treated differently

Not symmetry for its own sake; it falls out of the two receivers' input topologies.

- **NI has non-referenced single-ended and differential input modes.** Drive from AGND with one
  wire carrying AGND to **AISENSE**. NI performs the rejection. No NI-side analog supply exists.
- **Intan's analog inputs are single-ended BNC**, so the shield is the return and there is no
  differential receiver to exploit. Difference amplifiers referenced to INTAN_GND are required,
  and they need power in that domain — which is why exactly one isolated supply exists (§8).

---

## 6. Analog signal chain

### 6.1 Range convention

**±5 V board-wide.** Inside every destination's range (NI ±10 V, Intan ±10.24 V), leaves
headroom on ±12 V rails, and the ACCESIO DAC is jumper-selectable to ±5 V so the largest source
group matches natively. Sources outside the convention are scaled at their own front end. The
three misc inputs carry switchable ÷1/÷2 attenuation so they accept ±10 V.

### 6.2 Per-source chain

```
panel connector
  → series resistance + clamp diodes to rails
  → scaling / buffer stage (AGND)
  ├─→ buffer → series R → NI panel connector        (all 16; AGND → AISENSE)
  ├─→ mux bank → difference amp (INTAN_GND) → BNC   (8 selected)
  ├─→ buffer → task PC panel connector              (9 of 16)
  └─→ comparator with hysteresis                    (photodiodes ×2, accelerometer)
```

### 6.3 The Intan mux bank

Eight 16:1 analog multiplexers (ADG1206-class), each selecting one of the 16 sources onto one
Intan output. Address lines driven by two I²C expanders behind the USB-I²C bridge.

The mux sits **in AGND, ahead of the difference amplifier**, so its on-resistance is harmless
into the amplifier's high-impedance input and charge injection appears only at switch time,
never during a recording.

The selected routing is software state the Pi holds and can write into the session record.

### 6.3.1 Photodiode transimpedance stage

The photodiodes are **passive**, so the transimpedance amplifier is on this board and the coax
capacitance sits at its summing junction: 3–5 m of coax is roughly **300–500 pF**.

With a 1 MΩ feedback resistor and a 10 MHz op-amp, compensation lands bandwidth near **57 kHz** —
comfortably above the ~10 kHz these channels need. The cost is noise-gain peaking, mitigated by
the post-TIA low-pass wanted for anti-aliasing regardless.

**Design for 6 m; tested range 3–5 m.** Cable length is an electrical parameter of the stimulus-
onset timing reference, so it is specified rather than assumed, and a substantially longer cable
is a change to be verified rather than a swap.

**Photovoltaic (zero-bias) mode**, forced by the constraint that there is no power in the booth.
Lower dark current and lower noise; slightly slower, which is irrelevant against millisecond
display transitions. Reverse bias is achievable by offsetting the TIA's non-inverting input and
would need no supply at the sensor, but it buys speed this application does not need and costs
dark current.

### 6.4 Microphone anti-alias filter

**NI X-series multifunction cards have no anti-alias filter** — the front end is wideband, so
content above Nyquist folds into the band. Fifteen of the sixteen channels are inherently
band-limited by their sources: the eye channels come from a DAC updating at 4 kHz, and the
sensors, joystick and misc inputs are all slow. The microphone is the only broadband source, and
a mic with its own preamp typically responds well past 20 kHz.

So the microphone front end carries a **4th-order low-pass at ~12 kHz**, implemented as two
Sallen-Key sections on the buffer already present — passives only, no new part types.

**The cutoff is set by Intan, not by NI.** The mic reaches both; Intan samples at 30 kHz, so its
Nyquist is 15 kHz, below NI's 20 kHz at a 40 kHz scan. Filtering for NI alone would alias in the
Intan record. Second-order would be only ~4 dB down at 15 kHz, which is why the order is four.
12 kHz preserves macaque call energy, which mostly sits below 10 kHz.

### 6.5 Comparators

Three channels used — photodiode 1, photodiode 2, accelerometer — from one quad package. The
fourth is brought out to a misc input, unpopulated.

**Hysteresis is mandatory, for two different reasons.** A photodiode crossing a bare threshold
on a slow display transition emits a burst of edges. Motion energy is a noisy, slowly varying
signal that chatters across a bare threshold continuously.

> **`wl-shook` validates this choice for a reason we did not have when we made it.** That device
> accepts a resting baseline that moves with temperature, and notes that *a fixed comparator
> threshold sits directly on top of whatever voltage it emits when the chair is still* — so
> baseline drift is drift of a behavioural criterion. Because this threshold is software-set and
> recorded, re-setting it after drift is a **logged event** rather than an untracked knob twiddle,
> and the NI analog copy of the same channel gives you the real baseline to set it against.

**Thresholds are I²C-DAC-set with fixed hysteresis.** The accelerometer threshold defines how
much movement counts as movement and gates task progression, which makes it a behavioural
parameter; a task-gating parameter that is not recorded is a reproducibility hazard. If
asymmetric make/break points are wanted later, that is a second DAC channel per comparator.

---

## 7. Digital signal chain

### 7.1 Level shifting — the correction

A single `74HCT541` cannot serve both directions, and following that assumption literally is
how the Pi gets destroyed:

- The task PC's DAQ outputs **0–5 V**. Into an HCT541 powered at 3.3 V this violates the
  absolute-maximum input rating.
- Powering that HCT541 at 5 V protects the buffer but produces 5 V outputs, which is precisely
  what destroys a Pi that is not 5 V tolerant.

| Path | Part | Rail | Why |
|---|---|---|---|
| Task PC (5 V) → Pi | `74LVC541A` | 3.3 V | LVC inputs tolerate 5.5 V regardless of supply |
| Pi (3.3 V) → 5 V equipment | `74HCT541` | 5 V | 3.3 V is a valid high at HCT's 2.0 V threshold |
| → NI or Intan | buffer, then optocoupler | — | galvanic break into the ephys chassis |
| Intan → task PC, NI | optocoupler out of INTAN_GND, then buffer | — | the one inbound ephys-domain signal |

Intan's digital inputs accept low 0–0.8 V and high 2.0–5.0 V, so the 5 V buffered outputs drive
them directly.

### 7.2 Reward OR

Task PC commanded TTL and the debounced panel button (RC + Schmitt) feed an OR gate whose
output drives the reward driver and is buffered out as "reward delivered" (§3.1).

### 7.3 Optocoupler count

| Crossing | Channels |
|---|---|
| Into NI_GND | **24** |
| Into INTAN_GND | 5 |
| Out of INTAN_GND (RHS stim output) | 1 |
| **Total** | **30** |

> **Corrected 2026-08-15.** This table read 22/5/1 = 28. Routing the two photodiode comparators
> to NI (§3.1) took the NI-bound digital count from 22 to 24, and that change was propagated to
> §3.1's line counts but not to here — so the optocoupler total was stale by two channels and one
> package. Found while dispatching the sheet that builds them.

**Eight** quad packages in SOIC-16, all hand-solderable. Drive current is set conservatively: the timing budget is hundreds of
microseconds, so there is large margin against current-transfer-ratio degradation over the
board's intended decade of service.

---

## 8. Power

**No on-board switching regulators**, with one unavoidable exception. Rejecting digital
isolators for their RF carrier and then adding a switcher to the same board would be incoherent.

| Rail | Source |
|---|---|
| Sync box 5 V / 5 A | Its own official USB-C PD supply, panel cutout. Separate from the board's +5 V rail. Substituting is a false economy on a CM5-class board |
| ±12 V analog, **+5 V** | External linear supply, panel inlet — **5-position M12 A-coded**: +12 V, −12 V, +5 V, GND, shield. **+5 V output: ≥0.8 A at ±2 %** — the tolerance is load-bearing, see §8.2 |
| +3.3 V | LDO. **+5 V is NOT derived on board** — see §8.2 |
| NI domain | +5 V from NI's 68-pin connector, **250 mA per connector** — switcher-free and already referenced to NI's ground. See §8.1 |
| **Intan domain** | **One isolated ±12 V DC-DC**, pi-filtered with LDO post-regulation |

The Intan domain is the exception because its single-ended inputs force difference amplifiers
there (§5.5). As the only switcher in the enclosure it receives the whole filtering budget.

### 8.2 +5 V comes from the external supply, not from +12 V on board

The +5 V rail feeds the optocoupler LEDs, and there are **30** on this rail. Worst case is
**29 lit simultaneously** — all 16 data bits, strobe, barcode, reward commanded, reward
delivered, stim trigger, the five Intan-bound copies, both comparator legs and the buffered
`RHS_STIM_OUT` leg. (The 30th, `OPTO_INTAN_SPARE1_IN`, is a populated spare on an unused net
and never conducts.)

**Revised 2026-08-16, finding F1.** The two figures this section originally budgeted from —
6.3 mA and 10 mA per LED — are both wrong for this board, in different ways. 6.3 mA came from
the **HCPL-4661** family; this design uses **ACSL-6xx0**, whose recommended minimum is 7 mA
with an 8 mA guardband (Broadcom AV02-0235EN, footnote b), so 6.3 mA is below the threshold at
which the part is specified to switch. 10 mA was the mule's value on a different rail with no
series drops in front of it. The real drive, set by clearing that 8 mA floor once `F4` and
`D3` are accounted for, is:

| Drive per LED | Worst-case rail current (29 lit) |
|---|---|
| 12.65 mA nominal (249 Ω on `+5V`) | ~367 mA |
| **13.90 mA at the max-current corner** | **~403 mA** |

**Budget: 600 mA.** Raised from the 400 mA this section previously carried, which the LEDs
alone now exceed. With the logic and regulator branch on top, the rail carries **392 mA
typical / 495 mA maximum** (762 mA if the NI-fallback `TMA-0505S` is ever populated) — see
finding M7. Two consequences follow, both tracked there rather than here: `F4` must rise from
0.5 A to **≥1.1 A**, and the external supply's own +5 V output must be rated **≥0.8 A** (the
audit's 0.5 A figure sits exactly at the computed maximum, which is not a margin).

**The +5 V output must be specified at ±2 %.** This is a requirement, not an observation. The
optocoupler window is 8–15 mA — a 1.88:1 ratio — and every tolerance in the chain (supply,
series drop, V_F, V_OL, resistor) has to fit inside it. At ±5 % the spread is 1.95:1 against
that window and does not fit; at ±2 % it does, with margin at both ends. **This tolerance
protects 29 optocoupler channels and is load-bearing** — it is the reason the resistor value
works, so it belongs beside the current rating rather than being left to whichever brick is
on the shelf.

Duty cycle does not rescue a smaller supply — a regulator supplies peak, not average, and riding
260 mA through a 750 µs code on bulk capacitance would need ~2000 µF for 100 mV of droop, which
is a workaround rather than a design.

**Why not a larger on-board LDO:** linear 12→5 V at 260 mA dissipates **~1.9 W** inside a sealed
2U chassis already carrying 10–15 W from the sync-box module, beside analog stages whose offset
drift is temperature-dependent (§9.4). Two more watts to save one connector pin is the wrong
trade.

**Why not a switching regulator:** unavailable by construction. This design rejected digital
isolators for modulating an RF carrier beside headstages (§2 decision 3) and rejected on-board
switchers for consistency with that (§8). The isolated ±12 V DC-DC remains the sole switcher.

So the external supply becomes a **three-output linear brick** — +12 V, −12 V and
**+5 V at ≥0.8 A, ±2 %** — and the inlet gains a pin.

### 8.1 The NI +5 V budget is 250 mA, and it constrains the pull-ups

NI's device specifications give **250 mA per connector** on the +5 V pins. Twenty-four
optocoupler output stages at a typical 5–7 mA each is already 120–170 mA, so the pull-up choice
is not free:

| Pull-up | Pull-up current, 24 ch | Edge into ~50 pF | Domain total | Verdict |
|---|---|---|---|---|
| 1 kΩ | 120 mA | 0.05 µs | 240–288 mA | Over budget |
| **3.9 kΩ** | **31 mA** | **0.2 µs** | **151–199 mA** | **Within budget and within the part's spec** |
| 10 kΩ | 12 mA | 0.5 µs | 132–180 mA | Saves 19 mA by exceeding the datasheet |

**3.9 kΩ on the NI side.**

> **Corrected 2026-08-15, revising an earlier ruling of 10 kΩ.** That figure came from comparing
> against the mule's 1 kΩ and never checked the optocoupler's own limit: the `ACSL-6400`
> specifies a **maximum** load resistance of 4 kΩ. 10 kΩ exceeds it to save 19 mA on a rail that
> already fits at 3.9 kΩ — a bad trade. The edge cost is 0.2 µs into ~50 pF against a 500 µs
> strobe, which is irrelevant either way.

Note the mule board deliberately uses **1 kΩ** to sharpen edges *for measurement*; different
board, different objective, and it has no 250 mA ceiling to respect. This deliberately differs from the mule board, where 1 kΩ was chosen
to sharpen edges for measurement — different board, different objective.

That lands the domain near 150 mA against 250 mA. If bench measurement disagrees, the fallbacks
in order are: draw from both connectors, then a filtered isolated DC-DC.

Every panel input carries series resistance and clamp diodes.

---

## 9. Connectors and mechanical

### 9.1 Panel inventory

| Interface | Connector | Qty |
|---|---|---|
| Intan RHS | BNC | 14 (8 analog out, 5 digital out, 1 digital in) |
| Recording NI | 68-pin MDR, male | 2 (analog+AISENSE / digital) |
| Task PC NI | 68-pin MDR, male | 2 (analog+AISENSE / digital) |
| Misc analog in | BNC | 3 |
| Camera triggers | BNC | 5 (1 eye, 4 behavior) |
| Reward driver out | BNC | 1 |
| Display sync in | BNC | 1, unpopulated |
| Photodiodes ×2, ambient, accelerometer, joystick X/Y, microphone | **BNC** | 7 |
| Manual reward | panel momentary button + remote jack | 1 + 1 |
| Supply in (±12 V, +5 V) | **M12 A-coded, 5-pos**, screw-locking | 1 | `M12A-05PFFP-SF8001`; see §8.2 |
| Pi ports | cutouts: USB-C, Ethernet, USB-A | — |

> **All rig-facing sensors are BNC — mini-XLR removed entirely, 2026-08-13.** This section
> previously specified powered sensor heads on TB4M/TB5M. **The rig sits inside a Faraday cage
> sound booth**: every sensor inside it is battery powered precisely so that no mains conductor
> crosses the cage wall, and only signals pass through the bulkhead. Nothing needs power from
> this board, so nothing needs more than a coax. This deleted the two custom footprints whose
> contact arrangement was the largest known fab risk.

**31 BNC positions, 30 populated** — the display-sync footprint and its panel cutout exist, the
connector is not fitted. Every input is clamped, so a mis-plug costs wrong data rather than
hardware; with a uniform connector type, clear panel labelling is what prevents it instead of
mechanical keying. **Isolated BNCs throughout**, so each shell lands on its own pad rather than
being bonded to the shield plane by the connector body — that is what makes §5.6 possible.

### 9.2 NI connector choice

NI's `SHC68-68-EPM` cable is VHDCI-male to 68-pin-SCSI-female, and NI's own breakouts present
the SCSI end. **The board carries 68-pin male MDR**, so NI's standard cable plugs straight in
with nothing between. MDR is also the hand-solderable choice at 1.27 mm pitch against VHDCI's
0.8 mm.

**Two connectors per device**, and the split is confirmed from NI's device specifications:

| | Connector 0 | Connector 1 |
|---|---|---|
| Analog in | **AI 0–15** | AI 16–31 |
| Digital | P0.0–P0.7, P1.0–P1.7 | **P0.8–P0.31**, P2.0–P2.7 |

This permits the clean split the design wants: **all 16 analog channels on connector 0** — AI 0–15
is exactly the channel count — and **all 24 digital lines on connector 1**, which is P0.8–P0.31
exactly, so analog and digital ride physically separate shielded cables with no interleaving.
The task PC's 9 analog and 23 digital fit the same way.

**Connector 1 has zero spare hardware-timed lines at 24.** Only P0's 32 lines are hardware-timed
(P1 and P2 are static), and P0.0–P0.7 live on connector 0. A twenty-fifth NI signal therefore
either mixes digital onto the analog cable or is not hardware-timed. That is the cost of routing
the photodiode comparators to NI (§3.1), and it is worth paying.

**Consequence for task configuration:** the 16 event-code bits land on **P0.8–P0.23**, not
P0.0–P0.15. MonkeyLogic should accept a non-zero-based line range; confirm rather than assume it.

Two connectors are needed at all because all 32 analog inputs sit on Connector 0 while only
P0.0–P0.7 do; the remaining port-0 lines are on Connector 1, and 22–23 digital lines cannot fit
on eight. This is a benefit: analog and digital ride physically separate shielded cables.

### 9.3 NI device selection and the analog scan rate

**Recording: PXIe-6353. Task PC: PCIe-6343.** Both Active, both quoted at 12–13 weeks.

| | Card | Each | AI rate | Load |
|---|---|---|---|---|
| Recording | **PXIe-6353** | $2,999 | 1.25 MS/s | 640 kS/s = 51% |
| Task PC | **PCIe-6343** | $1,880 | 500 kS/s | ~18 kS/s = 4% |

Both carry 48 DIO with **32 hardware-timed lines on P0.<0..31>** and two 68-pin connectors —
identical to the 6363 on every axis this design uses. The 6363's extra AI rate is unused.

**The scan rate is set by the microphone and nothing else.** An X-series card multiplexes one
ADC across the scanlist and has a single AI timing engine, so every channel in the task runs at
the same rate and the fastest channel sets it. Per-channel requirements:

| Channel(s) | Real bandwidth | Adequate rate |
|---|---|---|
| Eye X/Y, pupil (6) | 500 Hz — camera frame rate, via a DAC updating at 4 kHz | ~2 kHz |
| Ambient light (1) | near-DC | ~100 Hz |
| Accelerometer (1) | motion-energy envelope | ~1 kHz |
| Joystick X/Y (2) | behavioural | ~1 kHz |
| Misc (3) | confirmed slow | ~1 kHz |
| Photodiode ×2 | waveform only — precise onset is the comparator, §3.1 | ~10 kHz |
| **Microphone (1)** | **vocalisations, energy to ~10 kHz** | **~25 kHz** |

**40 kHz scan**, giving 640 kS/s across 16 channels. Sampling the eye channels there records the
DAC's staircase many times per genuine update, which is harmless; the alternative is losing
audio bandwidth on the only channel that has any.

**Residual, stated rather than buried:** SpikeGLX's documentation supports X-series generically
and names the 6341, 6363 and 6366 as tested. **The 6353 is not named.** It presents identically
through DAQmx — same AI count, same waveform-DI count, same STC3 timing — so the risk is low,
but that is where the evidence stops.

**Cables:** `SHC68-68-EPM` × 2 per card, so eight for two rigs. Same lead time, easily forgotten.

### 9.4 Enclosure

**2U 19" rack chassis.** Rig-facing connectors front, equipment-facing rear, so the board spans
the chassis depth: approximately **430 × 240 mm, 4 layers.** Panels are machined to match the
layout; the enclosure is designed alongside the board rather than bought after it.

Rack mounting also places the box beside the Intan controller, which is itself 1U rack-mount,
keeping the barcode line short — it carries a high edge density and must be routed away from
headstage cables.

**Thermal, which is a mechanical requirement nobody had written down.** The enclosure contains a
CM5 with its active cooler and an M.2 drive (§4.1) — call it 10–15 W of deliberate heat —
sharing a sealed chassis with 33 analog stages whose offset drift is temperature-dependent, and
with comparator thresholds that gate task progression. Three consequences for the panel and
floorplan, all of which must be settled **before panels are machined**:

- **Airflow is designed, not assumed.** Intake and exhaust positions are panel cutouts and
  therefore part of the mechanical drawing, not something added afterwards.
- **The Pi and NVMe sit downstream of the analog section in the airflow**, not upstream, so their
  exhaust does not wash over the analog front ends and the difference amplifiers.
- **Fans are a noise source in both senses.** If a fan is fitted it wants to be a quiet one on
  the rack-facing panel, and its motor is an electrical noise source that should not sit beside
  the microphone preamp or the photodiode front ends.

### 9.6 Panel arrangement — corrected 2026-08-15

**The front/rear assignment was backwards.** This spec assumed equipment-facing meant rear.
**The Intan controller and the recording NI live on the front of the rack**, so the sync box's
connections to them belong on the same face; the task PC and the Faraday-cage booth are both
reached from the rack's back.

| Face | Carries | Rows |
|---|---|---|
| **Front** (rack-facing) | 14 Intan BNC · 2× MDR68 recording NI · **recessed reward button + reward remote BNC** · 2× 60 mm intake | 280 / 154 mm, fans right |
| **Back** | 15 rig BNC · **reward driver out BNC** · 2× MDR68 task PC · CM5 port cutouts | 204 / 300 mm |

**The reward group splits deliberately across both faces.** The *hardware* connection follows the
animal — the driver output goes to the back, toward the booth. The *human controls* face the
person operating the rig — button and remote on the front, beside the recording equipment they
are working with. Splitting them costs nothing electrically and puts each end where its user is.

Two consequences. The back is **less** crowded than before (504 mm across two rows, from 544).
And the **colour-coding matters more**, not less: a lone reward remote sitting among fourteen
identical Intan BNCs is exactly the mis-plug that coloured insulators exist to prevent. The
recessed button also earns its keep on the front, which is where people stand and lean.

The front's spare width is what makes the fans possible: splitting its connectors into two rows on
the left frees a **170 × 82 mm full-height strip** on the right.

**31 BNC positions**, all populated. Display sync is **dropped entirely** (§12 item 12), and the
3.5 mm TRS leaves the design with it — the reward remote became a BNC, which was its last use.

> **Colour-code the reward group.** With every position now an identical BNC, isolated connectors
> with coloured insulators cost nothing and make a mis-plug visible rather than something found in
> the data. Worth doing for the camera triggers too.

### 9.7 Airflow — diagonal, filtered, slightly positive

**60 mm fans, not 80 mm.** The binding constraint is height, not width: a 2U face has ~82 mm
usable and an 80 mm fan needs ~85 mm of cutout including its mounting holes. It misses on *any*
face in *any* arrangement. 60 mm needs ~65 mm and fits with margin.

**Two 60 mm intake on the front-right strip; two 60 mm exhaust on the left side panel**, so air
crosses the board **diagonally**. That matters because the board is 430 mm wide and only 240 mm
deep — straight front-to-back takes the short path and leaves the width stagnant.

**Placement follows from the flow**, and resolves a constraint that was previously delicate:

- **CM5 back-left, at the exhaust.** Its ports need the back panel anyway, so the board's only
  real heat source sits at the outlet and its 10–15 W leaves without crossing anything.
- **Analog back-right, in the quietest corner** — furthest from both inlet and outlet, and where
  its own connectors are. **The analog section does not want cooling, it wants thermal
  stability**: offset drift responds to changes and gradients, not to absolute temperature, so a
  still uniformly-warm corner beats a fluctuating airstream.

**Fixed-speed fans with in-line resistors.** There is no PWM source on the board — the GPIO
header is full — so fans on a dumb 12 V rail run flat out unless resistively slowed. Noctua ships
low-noise adapters in the box. Given 10–15 W of load and a side gap that starts whistling above
~8 m/s, slowing them is likely better on both counts; tune at bring-up with a thermometer rather
than assuming.

**Filtered intake, biased slightly positive.** All incoming air passes the two front fans, so one
filter there means everything entering is filtered; keeping intake ≥ exhaust pushes air out
through every seam rather than drawing dust in. Over a decade in an animal lab that is the
difference between a clean box and one with fur settled on the analog section.

**A ceiling the geometry sets:** the board is 430 mm in a ~470 mm internal chassis, so **20 mm of
clearance per side** — and that gap is the path to the side exhaust. 1,640 mm² per side is ample,
but two fans at a realistic 12 CFM give ~7 m/s through it, and pushing past ~25 CFM each reaches
~14 m/s where a slot that size begins to whistle. **Low-speed or PWM-limited fans**, not
high-static-pressure parts — there is a microphone preamp in this box.

### 9.8 Five things a rack instrument needs that this one lacked

None were specified; all are additions.

| # | Item | Why |
|---|---|---|
| 1 | **Chassis earth stud** | §5.6 says the cage and rack bond at one deliberate point — there was no point to bond *to*, so somebody would improvise one |
| 2 | **Power-good LED per rail** | Three rails (+12, −12, +5) and a failed one is invisible until the data is wrong |
| 3 | **Barcode heartbeat LED** | The barcode is a 1 Hz pulse; an LED on it says the sync box is alive from across the room |
| 4 | **Rail test points** | Bring-up says to measure each rail before seating ICs, and there is nowhere to put a probe |
| 5 | **Main input fusing** | The fans got a polyfuse; ±12 V and +5 V did not |

### 9.5 Fan headers — added 2026-08-15, and they were missing

§9.4 recorded the thermal *requirement* and never specified the connectors to satisfy it. The
board carries **four 3-pin fan headers** at 2.54 mm on the standard PC fan pinout — pin 1 GND,
pin 2 **+12 V**, pin 3 tach.

**12 V, not 5 V.** An NF-A8-class 12 V fan draws ~0.06 A against ~0.1 A for the 5 V variant, so
four is **~240 mA on the ±12 V rail that already exists** for the analog section rather than
~400 mA on a +5 V rail already carrying 367–403 mA of optocoupler load (§8.2, revised at
finding F1 — this comparison originally cited 150–199 mA, which was the NI-side domain's
figure rather than this rail's, and the correction only strengthens the argument). The 5 V Noctuas are
really the USB-powered variants and buy nothing here. **The external supply's +12 V budget grows
by 240 mA.**

**Fan current must not return through `AGND`.** A brushless DC fan is a *commutating switching
load*, and routing 240 mA of it through a board carrying 33 analog stages and a microphone
preamp would contradict every other noise decision in this design — digital isolators were
rejected for an RF carrier (§2 decision 3), on-board switchers for consistency with that (§8).
The fan feed therefore gets **its own power and return traces, star-connected at the power inlet
only**, never sharing the analog ground or its return path.

**A polyfuse on the fan feed**, so a stalled or shorted fan cannot pull down the rails the analog
section depends on. A fan is the most mechanically abused part in the enclosure and the only one
with a bearing.

**Tach pins are present but unconnected.** Power-only was chosen deliberately — the sync module's
GPIO is full at 26 of 28, so tach would have to reach a spare I²C expander input, which is real
work for a warning nobody asked for. Keeping the third pin on the header means a 3- or 4-pin
Noctua plug seats directly with no adapter, **and adding tach later is a wire change rather than
a connector change.**

**The CM5 decision is conditional on this floorplan, and the condition is footprint.** The CM5 IO
Board is **160 × 90 mm** against a Pi 5's 85 × 56 — roughly **three times the shadow** cast over
a 430 × 240 mm main board. It must sit over the **rear digital region**, never over the analog
front ends or across the airflow path serving them.

If placement cannot achieve that, **the decision reverts to a Pi 5 with a PCIe-FPC NVMe adapter**,
accepting the flex cable to recover the smaller footprint. That is a Task 14 determination and it
must be settled **before panels are machined**, since both options change the panel cutouts for
Ethernet, USB and power.

**Nothing in the software depends on which is chosen.** CM5 and Pi 5 carry the same BCM2712 and
the same RP1 southbridge, so PIO, the GPIO numbering and `wl_sync` are identical either way. The
bench acceptance test is valid on whichever board runs it.

---

## 10. Fabrication, assembly, schedule

### 10.1 Quantity

Two rigs. **Fab run of 5**: two production units, one to shake out, two spares.

**Assembly, decided 2026-08-15: turnkey the first board, hand-build the rest.** A house assembles
board one including the two accepted exceptions — the `MCP4728` at 0.5 mm pitch and the two
exposed-pad regulators — and the remaining boards are hand-built using it as a reference. The
reasoning is about *diagnosis*, not difficulty: many of this board's failure modes are invisible
to inspection, and an assembly defect that masquerades as a design defect at bring-up would be
expensive to chase. A known-good reference removes that ambiguity from the one board that has to
work first.

**This makes the order-code CSV load-bearing rather than tidy.** An assembler orders from the
file it is given, so `breakout-bom-order.csv` carrying real orderable part numbers — not the raw
export's obsolete `INA105KU` and `SN74HCT14D` — is what stands between the BOM and a wrong build.

**No spare NI cards, decided 2026-08-15.** With two identical rigs a card failure means running
one rig while a replacement ships. Worth asking NI their RMA turnaround when ordering — it is
usually far shorter than the 12–13 week new-order lead, which would make this decision
comfortable rather than merely accepted.

The BOM is constrained so either route works — hand-solderable packages (nothing finer
than SOIC/TSSOP, no BGA or QFN, nothing below 0603) that also exist in a turnkey assembler's
parts library.

> **One documented exception, found 2026-08-15.** The `MCP4728` quad I²C DAC that sets the
> comparator thresholds is sold **only** in MSOP-10 at **0.5 mm pitch** — finer than anything
> else on this board, where the previous worst case was 0.65 mm TSSOP. Microchip offers no other
> package, so it is unavoidable rather than chosen. It is a real step up in hand-assembly
> difficulty and is recorded here so it can be planned for — flux, a fine tip and magnification —
> rather than met by surprise at the bench. Every other part on the board remains within the
> stated constraint.

### 10.2 Lead time

| Stage | Estimate |
|---|---|
| Schematic capture | 2–3 wk |
| Layout | 3–4 wk |
| Panel and enclosure drawings | 1 wk, parallel |
| Design review before fab | 1 wk |
| Parts procurement | 1–8 wk |
| PCB fabrication | ~2 wk |
| Panel machining | 2–3 wk, parallel |
| Hand assembly, first unit | 8–16 hr |
| Bring-up and bench test | 1–2 wk |

**10–14 weeks from this spec's approval to a bench-tested prototype**, dominated by design
(5–8 weeks) rather than fabrication (2–3). The schedule risk is in the drawing, not the fab
house.

**Procurement is the widest error bar.** Actives and passives are commodities; board-mount
MDR68 and 24 BNCs can run 4–8 weeks if no distributor holds stock. This is the first thing to
confirm, being cheap to check and the only line that could quietly add a month.

### 10.3 Against January

Prototype lands late October to late November; a production run of four more finishes
mid-November to mid-December. **This works with almost no slack for a respin**, which would
cost 4–6 weeks and land in January or past it.

Two consequences:

1. **Order the NI cards now.** Their 12–13 week lead time is fixed and independent of this
   board's pace.
2. **Fab a minimal event-path mule immediately.** A board carrying only the 17-line event path —
   the LVC541A down-shift, the HCT541 up-shift, a few optocouplers, IDC in and out — is a
   one-day schematic, a two-day layout, roughly $30, and two weeks of fab. It validates the
   level-shifting scheme, the strobe path, the contiguous-range PIO capture and the event
   protocol end to end **while the main board is still in layout**, de-risking exactly the
   failure a January respin cannot absorb.

---

## 11. Reversals and amendments

Changes this design makes to decisions recorded in the `wl-preproc` spec. Each is a reversal
rather than a gap, stated rather than made silently.

| # | Was | Now | Why |
|---|---|---|---|
| 1 | Camera exposure-active returns are Pi inputs | Dropped | The spec names this as its own escape hatch when Pi inputs run short. Safe: the Pi triggers the cameras so frame times are known by construction, and the trigger-count-versus-frames check still runs off the camera sidecar |
| 2 | Stim triggers go to NI and RHS only, never the Pi | Stim trigger also reaches the Pi | The Pi defines session time, so stim lands in the master timebase directly instead of being aligned into it. Reversal 1 freed the pins |
| 3 | A `74HCT541` handles both directions in one part | Two families, one per direction | True going up, unsafe going down. See §7.1 |
| 4 | Optoisolators on ephys-bound lines (board is digital fan-out) | Optocouplers on digital, difference amplifiers on analog | The board became mixed-signal. Isolating 17 digital lines while 30-plus analog lines tie the same grounds is ceremony, not protection |
| 5 | Photodiode is analog to Intan/NI and comparator-digital to the Pi | Also comparator-digital to the task PC; two photodiodes, one as a per-frame flip patch | A flip patch is a frame clock measured at the display surface, catching post-GPU drops a vsync tap structurally cannot |

**One note for the `wl-preproc` side, not actioned here.** The eye analog path is an ACCESIO
USB DAC driven by the eye-tracking software, so its *content* carries software and USB latency
even though its *samples* land on the recorder's clock. The authoritative eye record is the
eye-tracker PC's own file. Recording the analog copy alongside it makes that lag measurable by
cross-correlation per session, which is a better reason to keep the channels than redundancy.

---

## 12. Open items

| # | Item | Blocking |
|---|---|---|
| 1 | ~~+5 V current budget on the NI 68-pin connector~~ **Closed 2026-08-13** — **250 mA per connector**. Feasible at ~151–199 mA with **3.9 kΩ** pull-ups (24 optocoupler output stages at 5–7 mA each, plus ~31 mA of pull-up current); 1 kΩ would not be (~240–288 mA, at or over budget). 10 kΩ, the original figure here, also fit the budget but exceeded the ACSL-6400's own R\_L maximum — see §8.1 | ~~Schematic~~ |
| 2 | **Whether SpikeGLX exposes NRSE** as an NI terminal configuration. Decision 5 depends on it | Schematic |
| 3 | ~~Connector 0 / Connector 1 pin split~~ **Closed 2026-08-13** — Connector 0 carries AI 0–15 + P0.0–7 + P1; Connector 1 carries AI 16–31 + P0.8–31 + P2. Analog fits entirely on 0, digital entirely on 1. See §9.2 | ~~Layout~~ |
| 4 | **MDR68 and BNC stock and lead time** — the widest schedule error bar (§10.2) | Immediately |
| 5 | ~~Accelerometer output range~~ **Closed 2026-08-15 by `wl-shook` §3's output contract.** 0–5.000 V single-ended on BNC, non-inverted, with a **0.250 V resting pedestal** so 0 V means the device is absent rather than still — the NI record proves liveness sample by sample. Floating, battery-only, no rig supply, which is exactly what the differential receive assumes. Inside this board's ±5 V convention, so **the input stage is a unity buffer with nothing to derive and the board is correct as built.** No scaling network, no respin | ~~Schematic~~ |
| 6 | Which 8 of 16 analog sources are the default mux selection. Deferred safely — the mux makes it software, not copper | Post-bring-up |
| 7 | ~~Whether the misc analog ports need to be outputs as well as inputs~~ **Closed by construction — they are inputs only.** Each misc BNC runs one way: clamp → /1÷/2 divider → INA105 `+` input → `A_MISCn` → the NI and task-PC buffers. There is no drive-back path from the board to the connector anywhere on the analog front end, so an output misc port would be a respin | ~~Schematic~~ |
| 8 | Behavior camera count (≤4 budgeted); all share one trigger rate, since only two hardware PWM pins survive the contiguous capture range | Layout |
| 9 | Whether asymmetric comparator make/break thresholds are wanted. **Cost restated (2026-08-15): a second MCP4728 package, not a second channel each.** All four channels of the one DAC are allocated — VOUTA/B/C/D to PD1/PD2/ACC/MISC1 — so there is no spare channel to take the second threshold of any comparator. This is a new I²C device (the part has no address pins; its 3 address bits are EEPROM-programmed, factory default 0x60, so a second one must be reprogrammed before it can share the bus), plus its decoupling and board area | Schematic |
