# D5: code words, barcodes and edges on one clock (draft for decision)

**Status:** draft, 2026-10-30. Nothing in it is built. It is written for the rig owner to decide on after bench day.
**Read for this draft:**
- wl-sync `main` at `9be4505`.
- wl-preproc `main` at `4bd10f1`, read with `git show`.
- wl-phantom `main` at `44d4e36`.
- External sources, each cited where it is used:
  - piolib (`raspberrypi/utils` master);
  - the RP1 peripherals datasheet (build 2023-11-07, `b9b6f74`);
  - Linux master;
  - systemd main;
  - libgpiod master.

Marks: **[unverified]** means I could not confirm it from a primary source. **[mine]** marks a judgement.

---

## 1. The fault

### How each record is timed today (wl-sync `9be4505`)

| Record | Where its tick comes from | Which oscillator |
|---|---|---|
| `W` | The PIO state machine's X register, decremented every 2 cycles at 2 MHz (`wl_sync/rp1/program.py`, `backend.py:49-50`, `:271`) | RP1's crystal. RP1 datasheet §2.5: *"The reference clock is a 50MHz crystal input"*. clk_sys comes from the integer Core PLL (§2.5.2). |
| `B` | `backend.now_us()` = `CLOCK_MONOTONIC − _origin_ns`, read immediately before the frame (`cli.py:769`, `backend.py:235-237`) | The CPU's system counter, plus NTP |
| `E` | The kernel's `ktime_get_ns()` in the hard IRQ (Linux `drivers/gpio/gpiolib-cdev.c`, `line_event_timestamp`), minus the same origin (`backend.py:362-366`, `gpio.py:73-91`) | The CPU's system counter, plus NTP |

**The join happens once.** `_origin_ns` is read just before `pio_sm_set_enabled` (`backend.py:276-277`; the notes cite `:227-230` and `:270`, which have moved).

**What the PIO clock is.** piolib's `rp1_clock_get_hz` returns a hard-coded `200 * MHZ` for clk_sys (`piolib/pio_rp1.c`). So "clk_sys reads 200 MHz" in `next-session.md` §0 is a constant, not a measurement.

### Two corrections to the numbers in `docs/next-session.md` ("Found on the box, 2026-10-19")

**1. The error grows from capture start, not from segment start.**
- Capture starts once per process (`cli.py:488`), and a roll does not restart it (`log.py`, `process_uuid` docstring: *"a roll does not reset"*).
- So with a relative rate *r*, a code word is off by `r × (time since the process started)` plus the origin offset (bench check 3).
- Example: a session opened 2 h after boot already starts 47 ms off at 6.5 ppm.

**2. r is not necessarily 6.5 ppm.**
- −6.5 ppm is NTP's correction to the CPU's oscillator.
- W against B drifts at that rate only if RP1 and the CPU share a reference.
- RP1 has its own 50 MHz crystal (RP1 datasheet §2.5; Linux DT patch "arm64: dts: bcm2712: Add external clock for RP1 chipset on Rpi5": *"The RP1 found on Raspberry Pi 5 board needs an external crystal at 50MHz"*).
- The CPU's counter is believed to run from a separate 54 MHz reference **[unverified on CM5; `dmesg | grep arch_timer` settles it]**.
- If they are separate, *r* is RP1's crystal error against NTP time, which is unknown and plausibly ±10–50 ppm **[unverified]**.
- Bench day measures it (B1, B2).

### What it costs, against the ±100 µs budget

| r | Budget gone after | 10 min | 1 h | 10 h process |
|---|---|---|---|---|
| 6.5 ppm | 15 s | 3.9 ms | 23 ms | 0.23 s |
| 20 ppm | 5 s | 12 ms | 72 ms | 0.72 s |

**What it would take to pass by matching crystals.** Holding ±100 µs over a 10 h process, with one constant removed, needs |r| < 0.0056 ppm. **No two free-running crystals hold that.** So W must either share B's counter or be mapped onto B's clock continuously.

**NTP adds a second effect on top.** CLOCK_MONOTONIC *"is affected by frequency adjustments"* (clock_gettime(2), man7). CLOCK_MONOTONIC_RAW *"is not subject to frequency adjustments"*.
- systemd-timesyncd slews any offset under `NTP_MAX_ADJUST` = 0.4 s through the kernel PLL, and steps larger ones (`src/timesync/timesyncd-manager.c`).
- The kernel applies `offset >> (SHIFT_PLL + time_constant)` each second, with `SHIFT_PLL` = 2 (`kernel/time/ntp.c`, `include/linux/timex.h`).
- So each correction bends MONOTONIC's rate for seconds to minutes.
- A first sync after a cold boot can slew up to 0.4 s through it. The size on the box is **[unverified]**.
- Steps do not touch MONOTONIC.

### Who it hurts

**wl-preproc** (`main` `4bd10f1`):
- `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` §4.3: *"Event strobe capture | ~100 µs or better | The only hard one. On training days this is the sole record of event times, and reaction times and saccade latencies come off it."*
- `wl_preproc/timebase/clock.py::syncbox_codes`: *"Identity rate, not `fit_rate`: session time IS the sync box's own timeline"*. So W ticks become session seconds as they stand.
- Every other device is placed through the box's `B` records (`2026-10-22-syncbox-records-and-segments-design.md` §3.2).
- **The result:** each code sits *r·t* away from the eye, neural and camera data aligned to it. Saccade latency is the eye sample minus the stimulus code, so it carries the full error.
- Reaction times between two codes do not (6.5 ppm × 300 ms ≈ 2 µs).
- **Nothing downstream catches it.** The NI cross-check compares codes *"by sequence position, not by time"* (same design, §5 table). wl-preproc `main` reads no `E` records outside `synth/`.

**wl-phantom** (`44d4e36`):
- §5.5 and `wl_phantom/compare.py` expect the box to fail and predict "+6.5 ppm".
- That prediction inherits the shared-oscillator assumption, so expect a different number.

**No real session carries this yet.** The unit still runs `--fake` until bench acceptance (`next-session.md`, Step B).

---

## 2. Options

### What every option has to work with

There are two oscillators: RP1's (W) and the CPU's (RAW, and MONO = RAW plus NTP).

**MONO ↔ RAW is exact in software.** Both come from one counter, so `clock_gettime` pairs read back to back, sampled at about 50 Hz and interpolated, convert between them to under 1 µs.

**RP1 ↔ CPU needs a physical event that both clocks timestamp.** Today there is none.

### (a) Re-anchor continuously: fit PIO→MONO and convert W

**Mechanism:**
- Collect (PIO count, CPU time) pairs continuously, fit them, and convert each W before it is written.
- Fit against RAW, then convert RAW→MONO exactly. A fit made directly in MONO lags every NTP slew.

**The pair source does not exist.** Two candidates:
- **(i) A host-triggered sample.** A second state machine in lockstep is made to push its counter by `pio_sm_exec` through the RP1 firmware ioctl, with clock reads either side of the call.
  - piolib does one ioctl per call (`pio_rp1.c`).
  - Whether an exec'd instruction costs that SM a cycle on RP1 is **[unverified]**. If it does, it skews the sampling counter.
- **(ii) The barcode lead edge as the PIO sees it.** This needs (c)'s wiring, and then (c) is the better use of it.

**Files:**
- `pio/wlsync_pio.c`: a second SM, sync enable, the exec sample.
- `wl_sync/rp1/backend.py`.
- A new clock-map module.
- `Recorder._on_word` in `service.py`.

**Residual:**
- Pair noise: about `w/√12/√N`, roughly 1–3 µs for a 120-pair fit, where *w* is the ioctl round trip.
- **Plus a bias of up to w/2, on W itself.** It cannot be calibrated without a shared physical edge.
- So (a) is viable only if *w* ≲ 20 µs **[unmeasured]**.

**Failure modes:**
- An NTP step changes nothing, and a slew change changes nothing when fitted in RAW.
- After every restart there is a warm-up while few pairs exist, when W is least certain.
- A wrap is harmless: the fit works on unwrapped counts.

**Format:** none. W keeps its shape, and the header already claims one clock. It ships without moving wl-preproc's pin.

**Size:** 3–4 days, plus bench.

### (b) Move edges and barcodes onto PIO

**Barcodes:** this is (c).

**Edges cannot all move.**
- piolib gives 4 SMs per block (`MAX_PIO_SMS 4`, `piolib.h`). RP1's block count is **[unverified, believed 1]**.
- One SM watching 8 lines needs a counter plus two registers to detect a change, and PIO's only comparison is `x!=y`. So it is one SM per line, and only 2–3 are free.
- The cameras alone produce 1000 words/s.

**Partial (b)** (for example `pd1_comp` on a free SM) is an add-on to (c) if edges get a pass line (Q6).

### (c) Put the barcode on the PIO counter — the recommended base

**Mechanism:**
- A second SM runs the **existing capture program unchanged**: same offset, same divider, no new instructions. Its jmp pin is a copy of the barcode.
- It is enabled in lockstep with the code-word SM through `pio_enable_sm_in_sync_mask` → `PIO_IOC_SM_ENABLE_SYNC` (`piolib.h`, `pio_rp1.c`).
- Its push at a frame's first rising edge **is** the `B` tick. W and B then come from one count, and B is the edge on the wire, not the software moment before it.
- The run loop drains that FIFO once after each frame. No pump thread is needed: the first pair is the lead edge, and later rising edges fill the FIFO harmlessly.

**How the SM sees the barcode.** Three ways, most certain first:
- **c-loop.** GPIO17 → GPIO19 (spare; `PIO[19]` is function a7, datasheet Table 4).
  - On the board: a series resistor, before layout (`wl.yaml`: *"layout not started"*).
  - On the bench: a jumper from header pin 11 to pin 35.
- **c-direct.** Use GPIO17 itself as the jmp pin while libgpiod drives it.
  - Datasheet §3.1.1 suggests inputs are routed by FUNCSEL (*"If the same peripheral input is connected to multiple GPIOs, the peripheral sees the logical OR"*), so probably not.
  - A 10-minute bench test settles it.
- **c-gen.** The PIO drives the barcode itself.
  - It needs a new program (about 15–19 of the 19 free instructions **[unverified]**).
  - It also removes `emit_pulses`' GIL spin, which is the hazard behind bench check 4.

**E onto the same count:**
- Kernel MONO → RAW exactly.
- RAW → PIO by a sliding fit of one pair per frame. Each pair is the RAW stamps either side of the lead edge's `set_value`, against its PIO tick, with wide brackets rejected.
- That needs `emit_pulses` to return its bracket.

**Files:**
- `pio/wlsync_pio.c`: second SM, sync enable.
- `wl_sync/rp1/backend.py`: second SM, per-frame drain, bracket.
- `wl_sync/backend.py`: protocol and `FakeBackend`, so the fake has two clocks and today's drift fails a test.
- A new clock-map module.
- `wl_sync/service.py` (`Recorder`: B from PIO, E converted).
- `wl_sync/cli.py` (run loop).
- `wl_sync/log.py` (docstrings, and optionally a field).
- `wl_sync/pins.py` (`barcode_loop`: 19).
- `hardware/gen/gen_breakout_pi_interface.py` and breakout spec §4.
- `wl_sync/bench.py`.

**Residual, W against B:**
- **Zero drift, by construction.**
- Constant: the same program on both SMs gives the same pin-to-push latency, so 0. The start skew is 0 if the sync enable restarts the dividers together, as the Pico SDK's does **[unverified on RP1]**, and otherwise at most 1 tick.
- The origin offset (bench check 3) drops out of W against B.
- **Lockstep cannot drift.** Both SMs run on one clock, the program never stalls, and nothing is exec'd into them. So once it is verified, it holds.

**Residual, E** (which has no promised budget):
- A bracket width *w*, uniform, gives σ = 0.29w, so 3–9 µs for w = 10–30 µs **[unmeasured]**.
- A 120-pair fit extrapolated 1 s gives 0.18σ ≈ 0.5–1.6 µs.
- Crystal wander adds about 1 µs at 0.03 ppm/min **[assumed]**.
- Plus a constant (the bracket's position and the IRQ latency), which the bench loopback measures.

**Failure modes:**
- NTP steps and slews never touch W or B, and E's conversion follows them exactly.
- On restart, W and B are exact from the first frame. E warms up for about 30 s.
- A wrap is harmless: both SMs wrap together, and `_BoxClock` is unchanged (it tolerates ±35.8 min of divergence).
- **A frame with no lead edge drained is the new failure mode** (Q3).

**Format:**
- No record changes shape.
- B now means the wire edge on the PIO count.
- The timeline runs at RP1's crystal rate.
- `barcode_delay_us` (`73f4395`, unpushed) becomes 0 by construction.
- A header field naming the clock is advisable [mine]. **That is a contract change and needs a wl-preproc pin move**, and wl-phantom's too (both pin `27c0e97`).

**Size:**
- c-loop: about 1 week, plus bench, plus one board line.
- c-gen: about one more week.

### (d) Stamp B (and E) from RAW

**Mechanism:**
- `now_us()` on CLOCK_MONOTONIC_RAW.
- E cannot be stamped RAW by the kernel:
  - `line_event_timestamp` chooses only `ktime_get_real_ns()`, HTE or `ktime_get_ns()` (`gpiolib-cdev.c`);
  - libgpiod offers `GPIOD_LINE_CLOCK_MONOTONIC`, `REALTIME` and `HTE` (`include/gpiod.h`);
  - mainline's only HTE provider is Tegra194 (`drivers/hte/`; the Raspberry Pi kernel was not checked).
- **But E is not "partial at best".** MONO → RAW is exact in software (above).

**Residual:**
- This takes NTP out of B and E entirely.
- W against B still drifts at the difference between RP1's crystal and the CPU's.
- **It is a complete fix only if they share a reference (B1). Otherwise it fixes nothing on the hard path.**

**Format:** none in shape; the timeline rate changes.

**Size:** about 1 day. Its exact E conversion is also a component of (a) and (c).

---

## 3. What bench day must measure

**Run the acceptance gates on a long process.** Task 5b Step 3 and wl-phantom §5.5 should run on a process at least 8 h old, not a fresh 10-minute one (Q5).

| # | Measurement | Pass / fail |
|---|---|---|
| B1 | **Shared reference?**<br>- Jumper GPIO16 to GPIO20 and pulse at 1 Hz for 1 h.<br>- Sample (MONO, RAW) every 100 ms.<br>- Find the rate of W against E converted to RAW.<br>- Also `dmesg \| grep arch_timer`. | \|rate\| < 0.01 ppm means shared, and **(d) alone suffices**. Otherwise they are separate; record the rate. |
| B2 | **Today's W–B drift.** wl-phantom §5.5 run, at least 1 h. | Expected to fail. Report the ppm; it should be about B1's PIO-against-MONO rate. |
| B3 | **Lockstep.** Two SMs, the same program, both with jmp pin GPIO16, sync-enabled; 10⁵ strobes. | Tick difference identical on every event, and ≤ 1. |
| B4 | **Barcode visible to PIO.** c-direct with no wire; c-loop with the 11→35 jumper. | Every lead edge stamped. |
| B5 | **Lead-edge bracket width** (RAW either side of the first `set_value`), over 8 h. | Median ≤ 20 µs, and at least 95% of frames ≤ 100 µs. |
| B6 | **E mapping.**<br>- GPIO17 jumpered to GPIO19 (PIO) and GPIO20 (E) at once, for 8 h.<br>- Convert E by the fit and take E minus PIO. | Spread ≤ 25 µs at p99.9 (a proposed line, Q6). The median is E's constant. |
| B7 | **NTP.** Log `adjtimex` (frequency, offset, time constant) every 10 s, from a cold boot through 8 h. | Informational. It shows how far MONO bends, and whether a first-sync slew lands inside recording. |

---

## 4. Recommendation [mine]

**What to do:**
- Make the RP1 PIO count the box's one clock, by option (c-loop).
- A second state machine, running the existing capture program in lockstep, stamps the barcode's lead edge. W and B then come from one count, and the ±100 µs path holds by construction, with no fit, no NTP and no software latency in it.
- Edges move onto that count by (d)'s exact MONO→RAW conversion, then a sliding fit of one lead-edge pair per frame. Any residual lands on the one path that was promised no budget.
- Wire GPIO17→GPIO19 before layout. If B4 shows c-direct works, skip the wire.

**Why (c) and not (a):**
- (a) puts the fit's noise and an uncalibratable bias on W, which is the hard path.
- It also keeps a software-timed B.
- (c) puts them on E, and makes B the edge every receiving device actually sees.

**What would overturn it:**
1. **B1 shows RP1 and the CPU share a reference.** Then do (d) alone: about a day, no hardware, and no fit.
2. **B3 shows the two SMs cannot be held in lockstep, or the skew is not a stable constant ≤ 1 tick.** Then fall back to (a), fed by the same loopback's lead-edge pairs and fitted in RAW, and accept the fit residual on W.

---

## 5. Open questions only the rig owner can answer

- **Q1.** Spend GPIO19 on a barcode loopback (one resistor, before layout)? GPIO18 is already earmarked for the status LED (continuous-recording spec §10 item 3), so this uses the last spare.
- **Q2.** May session time run at RP1's crystal rate (free-running, tens of ppm from SI) rather than NTP's? Wall time would still come from barcode values.
- **Q3.** When a frame's lead edge is not drained, which: drop that `B` with an ERROR, restart the process, or fall back to a software-stamped `B` (which needs a marker, and so a contract change)?
- **Q4.** Name the clock in the header?
  - If yes: hold the unpushed header commit `73f4395`, so wl-preproc and wl-phantom move their pins once.
  - If no: ship without a field.
- **Q5.** Lengthen the ±100 µs acceptance gate to a day-length process, since the error grows from process start?
- **Q6.** Do edges need a pass line (photodiode onset above all)? If `pd1_comp` must meet ±100 µs, give it the fourth SM.

*Also noticed: the dependents block in wl-sync's `CLAUDE.md` says wl-preproc pins `90b931e`. wl-preproc `main`'s `pyproject.toml` pins `27c0e97` (moved 2026-10-22), so that block is stale and needs `wlo dependents wl-sync --write`.*
