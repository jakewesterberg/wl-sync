# D5: code words, barcodes and edges on one clock, in SI seconds (approved; reworked)

**Status:** approved by the user on 2026-10-30 (D5) and reworked on 2026-11-07 for their answers. The main change is that session time is now in SI seconds. Revised the same day after a builder check (ruling OC-R1): the capture program does not count 2:1 and is not wrap-safe, so it is fixed first (Part 0, §2.1). *[2026-11-07: amended again the same day for the user's c-direct answer, after the spike. See decision 2's note.]* Nothing in it is built. It is written for a builder to plan from.

**The approval, verbatim** (wl-phantom's main checkout, `.superpowers/sdd/2026-10-19-phase-1-generator-and-monitor/questions-for-you.md:331-333`):
> D5: PIO-clock design approved. Answers: session time corrected to SI seconds (not RP1's crystal rate; the design must be reworked for this); a missed lead edge drops its B with an ERROR; no pass line for edges (software mapping); the header names its clocks (done, `timebase`). Next: plan + build.

**Read for this rework:**
- wl-sync at `a452599`, branch `one-clock`. Every `file:line` below is at that commit.
- wl-preproc `main` at `d48943c`, read with `git show`:
  - `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` §4.3 and §4.5;
  - `docs/superpowers/specs/2026-11-04-syncbox-header-design.md` §4, §5, §7 and amendment 11;
  - `wl_preproc/timebase/fit.py`, `wl_preproc/timebase/clock.py` and `pyproject.toml`.
- wl-phantom `main` at `84f2bac`: `wl_phantom/compare.py` and `pyproject.toml`.
- man7.org, read 2026-11-07: clock_gettime(2) and adjtimex(2).
- **Carried from the draft, not re-read here.** The draft read wl-sync `9be4505`, wl-preproc `4bd10f1` and wl-phantom `44d4e36`, and these external sources, each cited where it is used:
  - piolib (`raspberrypi/utils` master);
  - the RP1 peripherals datasheet (build 2023-11-07, `b9b6f74`);
  - Linux master, systemd main and libgpiod master.

**Marks:**
- **[user]** is a decision of the user's, dated.
- **[mine]** is a judgement of mine (the draft's author's and this rework's).
- **[OC-R1]** is a ruling of 2026-11-07 by the session coordinating the build. It followed a builder check of `1fc21b6`, which found that the capture program loses ticks and is not wrap-safe. It is not the user's.
- **[unverified]** means no primary source confirms it. Each one is in §4, what bench day must measure.

**Terms used below:**
- **P**: the PIO count, the number both state machines count. It is 0 at the sync enable (`service.py:105-107`) and unwrapped.
- **r**: PIO counts per SI microsecond, fixed for each process (§2.2).
- **A written tick**: `round(P / r)`, in SI µs since capture started.
- **MONO and RAW**: CLOCK_MONOTONIC and CLOCK_MONOTONIC_RAW.

---

## 0. Decisions at a glance

| # | Decision | Whose | § |
|---|---|---|---|
| 1 | RP1's PIO count is the box's one clock. A second state machine, running the same program as the code-word machine, stamps each barcode lead edge through a GPIO17→GPIO19 loopback, so W and B are one count. *[2026-11-07, c-direct (the user): it reads GPIO17 itself, with no loopback. See decision 2's note.]* | [user, 2026-10-30] | 2.1 |
| 2 | GPIO19 is spent on the loopback. It was not answered on its own (draft Q1), but the recommendation approved names it. B4 can still remove the wire. *[2026-11-07, c-direct (the user): B4 removed it. In the spike, B4's c-direct case stamped 300 of 300 lead edges with nothing wired (the plan's bench S.4), and the user chose "Adopt c-direct". The consequences: the stamp machine reads GPIO17 itself and leaves it libgpiod's output; there is no GPIO17→GPIO19 wire on the bench or the board; GPIO19 stays spare; `gpio_map` gains nothing; and the board's loopback resistor (the plan's Task 1.14) is dropped for good.]* | [user, 2026-10-30], by the approval; *c-direct [user, 2026-11-07]* | 2.1 |
| 3 | Session time is in SI seconds, not RP1's crystal rate. | [user, 2026-10-30] | 2.2 |
| 4 | SI is reached by **one rate, fixed for the whole process**: an affine map from P to SI µs. A map that follows NTP continuously is rejected. | [mine] | 2.2 |
| 5 | That rate is measured against NTP-disciplined MONO over at least 1 h, by earlier processes, and persisted. It is never changed inside a process, and not at a roll. | [mine] | 2.2 |
| 6 | A process with no usable calibration records at RP1's rate, says so in `timebase` (`"rp1-pio"`), and does not wait or refuse. | [mine] | 2.2 |
| 7 | A missed lead edge drops that frame's `B`, with an ERROR. | [user, 2026-10-30] | 2.5 |
| 8 | Dropped `B` records are logged per episode: one ERROR at the first, and one naming the whole range when it ends. | [mine] | 2.5 |
| 9 | The end-of-run checkpoint takes the last value **emitted**, not the last `B` written. The NTP roll waits for a frame whose `B` was kept. | [mine], required by decision 7 | 2.5 |
| 10 | Edges stay kernel-stamped on MONO and are mapped onto P in software: MONO→RAW, then RAW→P. There is no pass line, and bench day measures them. | [user, 2026-10-30], mechanism [mine] | 2.3 |
| 11 | The header names its clocks in `timebase`. The new values are `"rp1-pio-si"` and `"rp1-pio"`. No header field is added. | [user, 2026-10-30], values [mine] | 2.6 |
| 12 | `barcode_delay_us` is 0 on the new backend, by construction. | [mine] | 2.6 |
| 13 | The word pump's thread drains both state machines, so piolib is used from one thread during capture. | [mine] | 2.1 |
| 14 | Draft Q5, run the acceptance gate on a long process, is withdrawn: W–B error no longer grows with process age. | [mine] | 4 |
| 15 | **Part 0:** the capture program is fixed so that every path, entry and exit included, costs 2 cycles per decrement, on both machines alike. It is built first, because it fixes today's W path whatever the open questions' answers. | [OC-R1] | 2.1 |
| 16 | Every `jmp x--` is wrap-safe: when X was 0, it falls through only to an instruction that continues its path correctly. | [OC-R1] | 2.1 |
| 17 | B3 is split: B3a keeps the shared-pin test for start skew, and B3b tests the two machines on independent event streams. | [OC-R1] | 4 |
| 18 | After a frame, every stamp up to the frame's end is consumed. Only stamps between frames warn. | [OC-R1] | 2.5 |
| 19 | systemd-timesyncd is required on the box (deploy docs, `wl.yaml`). | [OC-R1] | 2.2 |

---

## 1. The fault (condensed from the draft; line numbers updated)

### How each record is timed today (`timebase = "split"`, `rp1/backend.py:260-262`)

| Record | Where its tick comes from | Which oscillator |
|---|---|---|
| `W` | The PIO state machine's X register, decremented every 2 cycles at 2 MHz in the idle loop (`rp1/program.py:87-107`, `rp1/backend.py:50-52`, `:340`). Not across a captured event's entry and exit, which lose 1.5 ticks (§2.1, Part 0). *[Part 0, 2026-11-07: every path now counts 2:1 (`tests/test_rp1_program.py:80`).]* | RP1's crystal. RP1 datasheet §2.5: *"The reference clock is a 50MHz crystal input"*. clk_sys comes from the integer Core PLL (§2.5.2). |
| `B` | `backend.now_us()` = `CLOCK_MONOTONIC − _origin_ns`, read immediately before the frame (`cli.py:787-790`, `rp1/backend.py:291-293`). | The CPU's system counter, plus NTP. |
| `E` | The kernel's `ktime_get_ns()` in the hard IRQ (Linux `drivers/gpio/gpiolib-cdev.c`, `line_event_timestamp`), minus the same origin (`rp1/backend.py:435-446`, `rp1/gpio.py:210-228`). | The CPU's system counter, plus NTP. |

**The join happens once.** `_origin_ns` is read just before the state machine is enabled (`rp1/backend.py:345-346`).

**clk_sys is a constant, not a measurement.** piolib's `rp1_clock_get_hz` returns a hard-coded `200 * MHZ` for clk_sys (`piolib/pio_rp1.c`).

**A second fault, in today's W path alone (found 2026-11-07).** The capture program loses 1.5 ticks per event, and it can leave the program when its counter wraps. §2.1's Part 0 gives the detail and the fix.

**The error grows from capture start.**
- Capture starts once per process (`cli.py:503-504`).
- A roll does not reset it: *"the ticks run on, on that one clock, which a roll does not reset"* (`log.py:117-119`).

**The rate is RP1's crystal against NTP time, not NTP's −6.5 ppm.**
- RP1 has its own 50 MHz crystal. Sources: RP1 datasheet §2.5, and the Linux DT patch "arm64: dts: bcm2712: Add external clock for RP1 chipset on Rpi5": *"The RP1 found on Raspberry Pi 5 board needs an external crystal at 50MHz"*.
- The CPU counter is believed to run from a separate 54 MHz reference **[unverified on CM5; B1]**.

**What it costs against the ±100 µs budget:**
- At 6.5 ppm the budget is gone after 15 s, and the error reaches 23 ms at 1 h.
- At 20 ppm the budget is gone after 5 s, and the error reaches 72 ms at 1 h.
- Matching two free-running crystals well enough to hold ±100 µs over 10 h needs |r| < 0.0056 ppm, which no pair of crystals holds.

**Who it hurts:**
- **wl-preproc.**
  - Design §4.3: *"Event strobe capture | ~100 µs or better | The only hard one."*
  - `timebase/clock.py:123`: *"Identity rate: session time IS the sync box's own timeline"*.
  - Every device is placed through the box's `B` records (`timebase/clock.py:147`, `barcode_reference`).
  - So a code word sits *r·t* from the data aligned to it.
- **wl-phantom.** `compare.py:37-64` expects the split box to fail, and predicts +6.5 ppm.
- **No real session carries the fault yet.** The unit runs `--fake` until bench acceptance (`docs/next-session.md:100`).

---

## 2. The design

### 2.1 One count: W and B on the PIO counter (option c-loop) [user, 2026-10-30]

#### Part 0: the capture program, fixed first [OC-R1]

**Entry and exit lose time.** This was verified in `rp1/program.py`, and by a cycle simulation of its listing [mine, 2026-11-07].
- The idle loop costs 2 cycles per decrement: `jmp pin` at 0, then `jmp x--` at 1 (`program.py:91-93` at c744189).
- **Entering a capture** costs 3 cycles for one decrement: the taken `jmp pin` at 0, then 2 and 3.
- **Leaving it** costs 4 cycles for one decrement: `jmp pin` at 10 not taken, then `jmp` to idle at 11 (`program.py:103-106` at c744189), then 0 and 1.
- So each captured event loses 1.5 ticks, for any strobe at least 2 cycles wide. The simulation lost 600 ticks over 400 events.

**Why the tests missed it.** `cycles_per_decrement_by_path` (`program.py:113-128` at c744189) is a hand-written table that leaves out the entry and the exit. Its test (`tests/test_rp1_program.py:69-83` at c744189) checks the table, not the program.

**What it costs:**
- **Today's W path** loses 1.5 µs per strobe. That is 900 µs over the 600-strobe run of Task 5b Step 3, the run `program.py:116-119` at c744189 cites, so it fails that gate on arithmetic alone.
- **An unfixed stamp machine** would lose 1.5 ticks per rising edge of the barcode.
  - This year's values have 5–13 rising edges per frame, mean 9.1 (computed with `barcode.encode`).
  - That is about 14 ppm of W–B drift, varying with the value and with the strobe rate.
  - The draft's B3, with both machines on GPIO16, feeds both machines one stream, so it cannot show this.

**The wrap fault.**
- If X reaches 0 at the held loop's `jmp x--` (`program.py:106` at c744189), it falls through to offset + 13, outside the program. What runs next depends on where `pio_add_program` placed the program.
- Only instruction 1 is protected, by `WRAP = 1` (`program.py:109-110` at c744189).
- The barcode is high about 10% of every second, so the stamp machine would hit this at about one wrap in 10, roughly once every 12 h. W would hit it rarely.
- The simulation left the program in 60 of 1,200 runs started with X near 0.

**The requirements [OC-R1]:**
- Every path costs exactly 2 cycles per decrement, including the entry into a capture and the exit back to idle.
- Every `jmp x--` is wrap-safe. When X was 0 it falls through only to an instruction that continues its path correctly, or it sits at the wrap.
- Both machines run the same program, so W and B keep the same latency.
- The test derives each path's cost by simulating `STROBE_CAPTURE` itself, including X = 0 at every `jmp x--`. That replaces the hand-written table.

**A candidate that meets them** [mine; simulated, not run on silicon]. It is 13 instructions, as today:

```
 0  jmp x--, 1     ; entry: pairs with the taken jmp pin at 11
 1  in pins, 16
 2  jmp x--, 3
 3  push noblock   ; word 1: data
 4  jmp x--, 5
 5  mov isr, x
 6  jmp x--, 7
 7  push noblock   ; word 2: counter
 8  jmp x--, 9     ; held: the decrement, falling into the test either way
 9  jmp pin, 8     ; held while high
10  jmp x--, 11    ; exit: pairs with the untaken test, falling into idle either way
11  jmp pin, 0     ; idle (wrap_target)
12  jmp x--, 11    ; idle (wrap)
```

**What the simulation showed:**
- Over 400 random events, widths from 2 to 300 cycles, it lost 0 ticks. Today's listing lost 600 on the same events.
- It stayed in the program in all 1,200 runs started with X near 0.
- The counter it pushed lay a constant 4.0–4.5 ticks after the rising edge. The half tick is the pin being sampled every 2 cycles.
  *[corrected 2026-11-07, plan 2026-11-07-one-clock: the simulation read X at the second `push`, but `mov isr, x` latches it one decrement earlier. And the strobe is tested every 2 cycles, so a real edge is seen 0–1 µs late. The word therefore holds the count 3.0–4.0 ticks after the edge (a mean of 3.5; the spread is a full tick, not a half), and the pair is in the FIFO within 5.5 µs of the edge (`tests/test_rp1_program.py:159`).]*

**What the candidate changes for the builder:**
- Execution starts at the idle entry, offset + 11, so the shim passes that to `pio_sm_init` (`wlsync_pio.c:127`), not offset + 0.
- `in pins` samples the data 1 µs after the strobe is seen, against 0.5 µs today. Whether the task PC holds its code that long is **[unverified]**, a Task 5b check.
  *[corrected 2026-11-07: the task PC must hold its code ≥ 2.5 µs after raising the strobe (`wl_sync/rp1/backend.py:308-309`); whether it does is still **[unverified]**, P0 item 5.]*
- The ≈2.5 µs constant in `docs/next-session.md:170-179` becomes this listing's constant.

**Part 0 stands alone [OC-R1].** It fixes today's W path whatever the answers in §8, and without it Task 5b Step 3 cannot pass. So it is built before the rest.

#### The stamp machine

**Mechanism (the draft's (c), kept, on Part 0's program):**
- A second state machine runs the same program as the code-word machine: Part 0's, at the same offset in instruction memory, with the same divider.
  - Its jmp pin is GPIO19, wired to GPIO17, the barcode output.
  - On the board this is a series resistor, before layout (`wl.yaml:37-45`). On the bench it is a jumper from header pin 11 to pin 35.
  - *[2026-11-07, c-direct (the user): its jmp pin is GPIO17 itself. The machine leaves the pin's function alone, so the pin stays libgpiod's output, and nothing is wired: no resistor and no jumper.*
    - *Bench S.4 found that the PIO reads GPIO17's pad whatever its function. GPIO17 stayed an output (`pinctrl`: `17: op dl pd | lo`). The PIO still stamped every lead edge, 300 of 300.*
    - *"The edge on the wire" below is GPIO17's own edge. The shim's start initialises no pin in this case (`claim_jmp_pin` 0, the plan's Task 1.9), so the "jmp pin only" step below no longer applies.*
    - *A process's start now tests that the stamp machine starts and sees GPIO17 (the plan's Task 1.10a; §8 Q2's note).]*
- Both state machines are seeded by `pio_sm_exec`, as today (`pio/wlsync_pio.c:129-132`).
- They are enabled together through `pio_enable_sm_in_sync_mask` → `PIO_IOC_SM_ENABLE_SYNC` (`piolib.h`, `pio_rp1.c`). So they count one P.
- The second machine's push at a frame's first rising edge **is** that frame's `B`: the edge on the wire, on the count `W` uses.
- The same program gives the same pin-to-push latency on both: 4.0–4.5 ticks for the candidate. So the latency is shared by W and B, and cancels between them.
  *[corrected 2026-11-07: 3.0–4.0 ticks. Its mean is shared by W and B and cancels between them; each stamp keeps its own spread of up to 1 tick.]*

**What the shim needs (`pio/wlsync_pio.c`).** `wlsync_start_capture` cannot simply be called twice:
- It adds the program each time (`:104-108`). The second machine must reuse the first one's offset.
- It requires the jmp pin directly above the data window (`:91`).
- It calls `pio_gpio_init` on every pin from the window's base to the strobe (`:114-116`). For a second machine that would take GPIO17 away from libgpiod, which drives it.

So the build adds a stamp-machine start:
- It initialises only the jmp pin (GPIO19; `PIO[19]` is function a7, datasheet Table 4).
  - *[2026-11-07, c-direct: under c-direct it initialises none. GPIO17 keeps libgpiod's function. The start can still claim a spare pin, for the bench's controls (the plan's Task 1.9b).]*
- It reads `in pins, 16` from GPIO0, so `pair_fifo`'s upper-half check still holds (`rp1/pio_capture.py:57-60`).
- It adds a sync enable for both machines.

piolib gives 4 state machines per block (`MAX_PIO_SMS 4`, `piolib.h`), and this uses 2.
*[2026-11-07, c-direct: a third is claimed at each start for the start-up test, and left disabled (the plan's Task 1.10a). One stays free.]*

**Who drains the stamp FIFO: the word pump's thread [mine; this changes the draft].**
- The draft had the run loop drain it once after each frame.
- That would call piolib from a second thread during capture. Today piolib is used from the pump's thread alone while capture runs (`rp1/backend.py:359-361`: *"Called from ONE thread, the pump's own"*).
- The shim's calls share one handle (`wlsync_pio.c:20`, `:30`) and one per-block error flag (`:103`, `:107`, `:140-142`).
- So the pump, which already polls every 1 ms (`rp1/backend.py:69`), also drains the stamp machine. It hands each stamp (a raw 32-bit P) to a small queue that `emit_pulses` reads after the frame.
- At a 1 ms poll the 4-pair FIFO (`rp1/backend.py:122`) cannot overflow inside a frame: rising edges are at least 10 ms apart (`barcode.py:3-9`).

**`W` against `B`:**
- No drift, given Part 0. With every path at 2 cycles per decrement, each machine's count is cycles / 2 whatever its events. Without Part 0 the stamp machine drifts about 14 ppm from W.
- The constant is ≤ 1 tick if the sync enable starts both dividers together, as the Pico SDK's does **[unverified on RP1; B3a]**.
- The origin offset (bench check 3, `docs/next-session.md:147-149`) drops out of W against B.

### 2.2 Session time in SI seconds [user, 2026-10-30; mechanism mine]

**The requirement.**
- Under 2.1, W and B share RP1's crystal, so they align with each other.
- But RP1's crystal is off SI by its tolerance. That tolerance is **[unverified]**, plausibly tens of ppm.
- The user wants every written tick to be an SI microsecond.

#### Why one rate per process, and not a map that tracks NTP [mine]

**wl-preproc fits every device with one rate per session.**
- Design §4.5 (`2026-08-12-wl-preproc-design.md:361`): *"Rate is fitted once per `(system, session)`, pooling barcodes across all of that system's segments."*
- `fit.py:6` says the same.
- So any curvature in the box's `B` timeline, relative to a device's crystal, becomes that device's misalignment against `W`.
- `W` rides with `B`, but the eye, neural and camera data are placed by a straight line through `B`.

**A rate change mid-session bends `B`.** A change of Δ in the middle of a session of length T leaves a residual of about Δ·T/8 against the best line. I computed that: for T = 4 h, 1 ppm leaves 1.8 ms, and 0.01 ppm leaves 18 µs.

**A map that tracks NTP fails on both counts.**
- It inherits NTP's phase error θ(t), MONO minus UTC. That error is what NTP leaves behind. It is plausibly a millisecond or so over a network **[unverified on the box; B7]**.
- Every change in θ within a session would land on every device.
- MONO also bends while NTP slews: CLOCK_MONOTONIC *"is affected by frequency adjustments"* (clock_gettime(2)). The kernel slews at up to 500 ppm (`cli.py:78-81`).

**An affine map adds no curvature at all.** `tick = round(P / r)` with r fixed is a straight line in P. The ticks are then SI on average, to the accuracy of r. RP1's own wander within the process stays in the timeline, as it does for any crystal (B8 measures it).

#### The map

- **Written tick:** `round(P / r)`.
  - P is unwrapped (`_BoxClock`, `service.py:70-157`, unchanged, still counting P mod 2³²).
  - r is fixed for the process.
  - Use integer arithmetic, rounded to the nearest µs: monotone and deterministic.
- **Where it is applied: once, where a tick becomes a record.**
  - That is in the Recorder: `_on_word`, `_on_edge` and `record_barcode` (`service.py:253-261`, `:347-357`).
  - The same scale applies to `now()` and `delivered_through()` (`:359-378`), so a roll's boundary and the delivery marks compare in one unit.
  - Backends keep handing over raw 32-bit counts, as the protocol says today (`backend.py:135-137`).
- **r comes from the calibration in force when the process starts (below).** With none, r = 1: RP1's crystal rate, and `timebase` says so (§2.6).

#### The SI reference: MONO, over a long baseline [mine]

**What the box has.**
- MONO is the clock NTP disciplines. Over a long baseline its phase follows UTC to within θ, so its long-run rate is SI.
- RAW is the CPU's crystal alone, *"not subject to frequency adjustments"* (clock_gettime(2)).
- MONO plus a calibrated r is therefore the SI source.

**Why not RAW plus NTP's frequency word.** adjtimex's `freq` is the PLL's frequency state, *"ppm … with a 16-bit fractional part"* (adjtimex(2)). It excludes the phase slews that also carry part of NTP's correction. So it is right only at steady state. It is logged for comparison (B7) and not used.

**Each frame gives one pair.**
- `emit_pulses` reads MONO and RAW immediately before and after the lead edge's `set_value` (`rp1/backend.py:581`): the bracket.
- It also reads REALTIME−MONO.
- The stamp machine gives that edge's P.
- So each frame gives (P, MONO midpoint, RAW midpoint, bracket width w, REALTIME−MONO). This is the draft's option (a) pair source, fed by (c)'s loopback *[2026-11-07, c-direct: fed by the stamp machine reading GPIO17 itself (§2.1's note)]*. It serves three uses:
  1. the SI estimate (here);
  2. the edge map (§2.3);
  3. `now_us()` (§2.4).

**Which pairs the SI estimate uses (the gate):**
- The run's last definite NTP answer is yes. This is `ntp_known`, the value the NTP roll already uses (`cli.py:445-462`, `:535-562`).
- `adjtimex` with `modes = 0` shows STA_UNSYNC clear and an outstanding `offset` of at most 1 ms [mine].
  - Ordinary users may call it with modes 0 (adjtimex(2): *"Ordinary users are restricted to a value of either 0 or ADJ_OFFSET_SS_READ"*).
  - `offset` is in ns when STA_NANO is set.
  - This excludes the first sync's slew, which can be up to 0.4 s (systemd `timesyncd-manager.c`, `NTP_MAX_ADJUST`).
- **systemd-timesyncd must be the box's NTP client [OC-R1].**
  - The offset gate assumes the client corrects the clock through the kernel's PLL.
  - The builder check found that under chrony `offset` stays 0, so large slews would pass the gate. That was not verified here.
  - At build, the deploy docs require timesyncd, and `wl.yaml`'s `third_party` gains it with its reason (CLAUDE.md: a dependency added updates `third_party`).
  - As a self-check [mine], the gate also requires STA_PLL in adjtimex's `status`. That timesyncd sets it is **[unverified; B7 logs `status`]**.
- The bracket width w is under the B5 limit.

**How NTP steps and slews are handled:**
- **A step** leaves MONO and RAW alone. MONO *"is not affected by discontinuous jumps in the system time"* (clock_gettime(2)).
  - A step does break MONO's phase lock to UTC by the step's size, and it shows as a jump in REALTIME−MONO.
  - A jump of more than 1 ms between consecutive pairs restarts the baseline [mine].
  - The run loop's own step detection (`cli.py:877-909`) has a 0.2 s tolerance, which is too coarse for this.
- **A slew** changes MONO's rate.
  - Small slews are NTP following UTC, which is what the long baseline averages.
  - Large ones are kept out by the offset gate.
  - Neither reaches W or B, whose map is fixed. Neither reaches E either, which is mapped through RAW (§2.3).

**The estimate.**
- r is the least-squares slope of P against MONO over the gated pairs of this process's current baseline.
- Its error is about (|θ_start| + |θ_end|) / T.
- With θ ≤ 1 ms **[unverified; B7]**: T = 1 h gives ≤ 0.56 ppm, and T = 8 h gives ≤ 0.07 ppm.
- Pair noise does not matter here: a few µs over 3600 pairs.

#### Persisting it

**The file** [mine]: `.wl-sync-pio-rate`, beside the barcode checkpoint in the rig root (`cli.py:443`).
- It is written atomically and durably, as `write_checkpoint` writes (`clock.py:102-130`).
- It is first written once the baseline reaches 1 h, then rewritten every hour.
- It holds:
  - the board's serial number;
  - r, as a ppb offset from 1;
  - the P/RAW ratio;
  - the baseline's length and pair count;
  - the residual RMS;
  - `boot_id` and the time it was written.

**At start, the file is used if:**
- it parses;
- its serial matches this board's; and
- |r − 1| ≤ 200 ppm.

The bound is a sanity check, as `_CLK_SYS_SANE_HZ` is (`rp1/backend.py:123`). A clk_sys that is not really 200 MHz, or a broken reference, lands outside it. Where the board serial is read on a CM5 is **[unverified; B9]**.

**Otherwise r = 1**, `timebase` is `"rp1-pio"`, and a WARNING says why: no file, unreadable, another board's, or out of bounds.

**Two witnesses during the run, in the journal only [mine]:**
- **The P/RAW ratio**, measured live within about 10 s, against the file's.
  - A difference above 5 ppm is a WARNING.
  - It means the file most likely came from other hardware, even with a matching serial.
- **This process's own SI estimate**, once its baseline reaches 1 h, against the r it applied.
  - A difference above 1 ppm is a WARNING naming the difference.
  - The next start uses the newer estimate.

#### Before NTP has synced, and the existing NTP machinery

**Before any sync in this process, r is unaffected.**
- r is the calibration measured by an earlier process while NTP was synced. It is a property of the crystal, not of this boot's NTP state.
- A box powered up before NTP syncs therefore still writes SI ticks, given a calibration.
- The estimator collects nothing until the gate opens.

**A box that has never been calibrated** (a new install, or a lost or mismatched file) records at RP1's rate under `"rp1-pio"` until its next start [mine].
- It never waits, refuses, or changes rate mid-process. The box records anyway (`cli.py:170-177`).
- Bench day's 8 h runs leave a calibration before any production start (§4, B8).

**`clock_trusted` and `clock_reason` are unchanged.** They judge the wall clock, which gives the barcode **values** (`clock.py:139-184`, `:187-260`), not the tick rate.

**The NTP roll changes nothing about ticks** (`cli.py:818-868`, in place or into today's `unassigned/`).
- The process keeps its r, so the ticks run on across the roll at one rate.
- The new segment's `timebase` is the old one's, because the regime is fixed at the start.
- The one change to the NTP roll is in §2.5.

#### Precision against the ±100 µs budget

The SI correction is not on the ±100 µs path.
- W and B share the map.
- Each device fits its own rate to B.

What matters for the budget is that the map is affine, which it is by construction. The figures are in §3.

### 2.3 Edges: software mapping, no pass line [user, 2026-10-30; mechanism mine]

**E stays on the kernel's MONO stamp.**
- libgpiod offers MONOTONIC, REALTIME and HTE clocks (`include/gpiod.h`).
- mainline's only HTE provider is Tegra194 (`drivers/hte/`).
- The request uses MONOTONIC (`rp1/backend.py:435-439`).

**The kernel stamp is mapped onto P in three steps, in the backend's edge pump:**
1. **MONO → RAW.**
   - Both come from one counter.
   - The edge pump reads a (MONO, RAW) pair on every pass. It waits 20 ms per pass (`rp1/gpio.py:25`), so that is about 50 Hz.
   - It interpolates between pairs. The draft puts the error under 1 µs, more only at the very edge of a 500 ppm slew.
2. **RAW → P.**
   - A sliding least-squares fit over the last ~120 lead-edge pairs: the RAW bracket midpoint against P.
   - Brackets wider than B5's limit are rejected.
   - It is seeded with the calibration's P/RAW ratio, so it is good from the first pair rather than after ~30 s.
3. **P → tick.** The Recorder's `round(P / r)`, as for W and B.

**What changes in the code:**
- Every `edge_tick_us(·, origin_ns)` in `rp1/gpio.py` becomes this map: `:210-228`, `:760`, `:771`, `:800`, `:901-910`.
- An L record's `span_us` (`:901-902`) is a MONO distance, possibly hours long. It is converted at the current P/RAW ratio rather than taken as µs. At 50 ppm an hour is 180 ms otherwise.

**No constant is subtracted** [mine]. The bracket's position and the IRQ latency put E at a constant offset. B6 measures it and `bench loopback` reports it. No pass line [user].

### 2.4 `now_us()`: the box's estimate of its own clock

`now_us()` no longer produces any written tick. W and B come from the machines, and E from the map.

**It is computed:**
- RAW now → P through the §2.3 fit.
- Before the first pair, through the origin join: RAW read just before the sync enable, as `_origin_ns` is read today (`rp1/backend.py:345`), at the calibration's ratio.

**It serves the bookkeeping only:**
- `_BoxClock`'s unwrap, which tolerates ±35.8 min (`service.py:81-85`);
- a roll's boundary and the delivery marks (`cli.py:290-370`);
- the edge witness.

**The improvement.**
- Today's marks carry W's drift of ppm × run time (`cli.py:313-316`).
- Under this design they carry the estimate's error: µs after the first pairs, tens of µs before.
- `WORDS_LAG_US` (`rp1/pio_capture.py:86-91`, 10 µs) is widened to cover the warm-up error [mine]. 1 ms costs a roll nothing measurable.

### 2.5 A missed lead edge [user, 2026-10-30: drop its B with an ERROR]

**Detection, in `emit_pulses` after the frame** (steps 1 and 3 are [OC-R1]; in `1fc21b6` they warned on every frame):
1. `emit_pulses` waits until the pump has drained past **the frame's end**, not only the lead edge's bracket. It waits at most 50 ms past that end [mine]. The frame ends low (`rp1/backend.py:570-581`).
2. Of the stamps queued since the previous frame ended, it takes the first that lies within the lead edge's bracket mapped to P, ±1 ms [mine].
   - 1 ms covers the warm-up map many times over.
   - This frame's later edges are ≥ 20 ms after the lead (`barcode.py:5-7`).
   - Only a glitch within 1 ms of the lead edge could pass for it. B10 would show one.
3. Every other stamp from the bracket to the frame's end is consumed silently. These are the frame's own later rising edges, 4–12 a frame.
4. A stamp between the previous frame's end and this frame's bracket is discarded with a WARNING: `%d stamps on GPIO19 between frames, before barcode %d, discarded: something drove the loopback between frames`. Nothing else drives GPIO17 between frames.
   *[2026-11-07, c-direct: the stamps are GPIO17's, and there is no loopback. The plan's Task 1.7 rewords the WARNING to name GPIO17. It also names a stamp's other ways to land between frames: a drain too late for the frame before, and a fit that has drifted.]*
5. No stamp in the window means the B is dropped.

**The protocol change.**
- `emit_pulses` returns the stamp's raw P, or None (`backend.py:96-97`).
- The run loop records the `B` from it (replacing `cli.py:787-790`), or drops it.
- `FakeBackend` returns its clock, or None when a test asks it to.

**The journal [mine: episodes, the rule `cli.py:1093-1096` and `:1019-1026` give, so a cut wire is not 86,400 lines a day]:**
- At the first drop of an episode:
  `ERROR barcode %d dropped: <cause>. Every device recorded the frame; this log has no B for it`
  - The cause is `its lead edge was not stamped`, or
  - `the stamp lies %d us from the frame's lead edge as the CPU bracketed it (%d us allowed)`.
- When the episode ends (the next kept `B`, or the run's end), if it dropped more than one:
  `ERROR %d barcodes dropped in a row, %d to %d: their lead edges were not stamped; check the GPIO17->GPIO19 loopback`
  *[2026-11-07, c-direct: there is no loopback to check. The plan's Task 1.7 ends this line with advice for each kind of cause in the episode instead. The kinds are: not stamped, stamped too far, the stamp machine did not start, and the stamp drain stopped. The last two are causes the build added.]*
- At the run's end: `barcodes: %d emitted, %d dropped this run`.

**What a dropped B costs:**
- One matched pair per device. wl-preproc matches by value, and *"A device barcode absent from the reference is dropped rather than paired"* (`fit.py:89`).
- The value is still spent. It was checkpointed before the frame (`cli.py:773`), and no `L` record is written: the L format's paths are W and E (`log.py:316-317`), and the user's answer adds no format.

**Two rules a dropped B forces [mine]:**
- **The end-of-run checkpoint must take the last value emitted, not the last `B` written.**
  - Today it reads `sink.sink.barcode_last`, or `carried_last` across a roll (`cli.py:956-966`, `segment.py:103-105`).
  - If the last frames' `B`s were dropped, that rewinds the checkpoint behind values the devices recorded, and the next start can re-issue them. That breaks the uniqueness `next_value` exists for (`clock.py:52-82`).
  - The run loop tracks the last value emitted.
- **The NTP roll waits for a frame whose `B` was kept.** `ntp_roll_due` (`cli.py:859-868`) stays set while the last frame's `B` was dropped.
  - wl-preproc reads a roll in place as adjacent when the value steps by 1 or 2 (header spec §4).
  - With the grid's possible one-value skip (`log.py:135-141`), a dropped `B` before the roll would make it 3, which wl-preproc reads as a gap.
  - The frame after the roll cannot be protected this way (§7).

**A loopback absent for a whole process** (a wire missing or cut) drops every `B`. The box then logs no barcodes, and wl-preproc cannot time its sessions: *"the sync box log decoded no barcodes"* (`timebase/clock.py:130-131`). Whether the box should fall back is Q2.
*[Answered 2026-11-07 (the user, Q2): test, then fall back. A process whose loopback fails its start-up test records `"split"`, so it keeps its `B` records (§8 Q2).]*
*[2026-11-07, c-direct (the user): there is no wire to be missing or cut. Two things can still fail for a whole process: the stamp machine's start, and the PIO's sight of GPIO17. The start-up test checks both, and a process that fails it records `"split"` (§8 Q2's note).]*

### 2.6 What is written

**`timebase`.**
- It is free text, *"NOT A CLOSED SET"* (`log.py:197-202`).
- It is set per instance at start, from the calibration. Today it is a class attribute (`wl_sync/backend.py:91-94`, `rp1/backend.py:262`).
- The two new values [mine; `"rp1-pio"` is already the example in `tests/test_log.py:282`]:
  - **`"rp1-pio-si"`:**
    - W and B count RP1's PIO counter. B is the barcode's lead edge, stamped by a second state machine.
    - E is kernel-stamped on CLOCK_MONOTONIC and mapped onto that count in software.
    - Every tick is scaled to SI µs by one rate, fixed for the process and measured against NTP-disciplined time by earlier processes.
    - All paths share one rate.
  - **`"rp1-pio"`:** the same, unscaled. Ticks count RP1's crystal, because no calibration was usable at start. All paths still share one rate.
- `"split"` stays defined for segments already written. The build removes the split backend unless Q2 keeps it as a fallback.
  *[Answered 2026-11-07 (the user, Q2): Q2 keeps it. The split backend is not removed. It stays as a per-process mode, for a process whose loopback fails its start-up test (§8 Q2).]*
  *[2026-11-07, c-direct: read that as a process whose stamp machine fails its start-up test.]*
- `"fake"` is unchanged.

**No header field is added** [mine]. The header forbids unknown fields (`log.py:57`), so a field would make every pinned reader refuse new segments. The user has said the header names its clocks through `timebase`. The applied r, its age and its baseline go to the journal at start.

**`barcode_delay_us` is 0** [mine].
- A `B` tick is now the PIO's stamp of the lead edge itself.
- The latency it shares with W cancels (§2.1).
- The first-`B`-of-a-process exception (`log.py:173-177`) is gone with it.
- The backend declares the value. Under the new backend, a `--barcode-delay-us` given (`cli.py:1312-1320`) gets a WARNING and is not used: it would describe a software delay that no longer exists.

**Records keep their shape.** Docstrings that change:
- `BarcodeEmitted`: the stamped edge, and it may be absent (`log.py:240-258`).
- `process_uuid`: *"not one rate"* is no longer true of the new values (`log.py:108-122`).
- `Loss`: *"B is never lost"* (`log.py:316-317`). It is never recorded as lost; a dropped `B` is a journal ERROR.
- `barcode_delay_us` (`log.py:158-180`).
- The roll barrier's drift note (`cli.py:313-316`).

**`GPIO_MAP` gains `"barcode_loop": 19`** (`pins.py:20-36`). That map is written into every header (`cli.py:273`); it changes content, not schema. `EDGE_PINS` is unchanged.
*[2026-11-07, c-direct (the user): it gains nothing.*
- *Under c-direct nothing is wired to GPIO19, and nothing reads it, so the entry would be false in every header.*
- *The stamp machine reads `"barcode_out"` itself.*
- *The key, added on the build's branch, never reached main. With it gone, `gpio_map`'s content stays main's (the plan's Task 1.9b).]*

### 2.7 Failure modes

| Event | W, B | E | SI rate |
|---|---|---|---|
| NTP step | untouched | untouched (RAW) | baseline restarts |
| NTP slew | untouched (fixed map) | untouched (mapped through RAW) | gated by `offset` ≤ 1 ms; small slews averaged |
| Before NTP's first sync | exact | exact once paired | applied from the file; nothing collected |
| No calibration | exact, at RP1's rate (`"rp1-pio"`) | exact once paired | collected; used from the next start |
| NTP roll | ticks run on, same r | same | unaffected |
| Restart | a new process: new P origin, possibly a newer r | warm, seeded by the file | — |
| P wrap (71.6 min) | both machines wrap together, given Part 0's wrap-safe `jmp x--` (today's held loop leaves the program, `program.py:106` at c744189); `_BoxClock` unchanged | mapped into P mod 2³² and unwrapped | unwrapped counts |
| Missed lead edge | that `B` dropped, ERROR (§2.5) | one pair fewer | one pair fewer |
| Stamp FIFO dead (pump thread stopped) | every `B` dropped; health reports the stop (`rp1/backend.py:535-542`) | map extrapolates and degrades | stops |

### 2.8 Files and size

| File | Change |
|---|---|
| `wl_sync/rp1/program.py`, `pio/strobe_capture.pio`, `tests/test_rp1_program.py` | **Part 0, first:** the fixed listing; the cycle table replaced by a simulation of `STROBE_CAPTURE`, with X = 0 at every `jmp x--`; `_assert_encoders_agree` follows the instructions used. |
| `pio/wlsync_pio.c` | Part 0: start at the idle entry. Then the stamp-machine start (jmp pin only, shared offset) and a sync enable of both. |
| `wl_sync/rp1/backend.py` | Second machine; the pump drains both; `emit_pulses` brackets the lead edge and returns the stamp; `now_us()` from the map; `timebase` per instance; `barcode_delay_us` 0. |
| `wl_sync/rp1/gpio.py` | `edge_tick_us` → the map; the (MONO, RAW) table on each pass; `span_us` conversion. |
| `wl_sync/rp1/pio_capture.py` | Stamp pairs through `pair_fifo`; `WORDS_LAG_US`. |
| A new pure module, e.g. `wl_sync/clockmap.py` | MONO→RAW table, RAW→P fit, stamp validation, SI estimator and gate, calibration file read and write. Laptop-tested. |
| `wl_sync/backend.py` | Protocol: `emit_pulses` → `int \| None`, `timebase` per instance, the backend's `barcode_delay_us`. `FakeBackend` gets two clocks (a P rate and a CPU rate), so a test fails on today's drift and passes on the map. |
| `wl_sync/service.py` | The Recorder applies `round(P / r)` at each record, to `now()` and to `delivered_through()`. |
| `wl_sync/cli.py` | `B` from the stamp; dropped-`B` episodes; the last-emitted checkpoint; the NTP-roll wait; calibration read at start and persisted hourly; `--barcode-delay-us` handling. |
| `wl_sync/log.py`, `wl_sync/pins.py` | Docstrings (§2.6); `barcode_loop`. *[2026-11-07, c-direct: no `barcode_loop` (§2.6's note).]* |
| `wl_sync/bench.py` | The loopback report gives E's constant and spread (B6) and dropped `B`s. |
| `hardware/gen/gen_breakout_pi_interface.py`, breakout spec §4 | GPIO19 loopback resistor. *[2026-11-07, c-direct: dropped for good; nothing changes on the board.]* |
| `deploy/README.md`, `wl.yaml`, `docs/pending-wl-preproc-amendments.md` | New `timebase` values, the calibration file, the delay; systemd-timesyncd required (`third_party`); the status at build. |

**Size** [mine]:
- Part 0: about 1–2 days, first.
- c-loop: about 1 week, as the draft estimated.
- The SI calibration, the map and their tests: about 3 days.
- Then bench.

---

## 3. Residuals against the ±100 µs budget

| Quantity | On the ±100 µs path? | Figure |
|---|---|---|
| W against B | Yes | No drift, given Part 0. Without it: ~14 ppm on the stamp machine, and 1.5 µs per strobe on W. A constant ≤ 1 tick (B3a). Converting both through one map differs by \|r−1\| × (≤ 1 s to the nearest frame): ≤ 0.2 µs at 200 ppm. Rounding ±0.5 µs. |
| `B`'s straightness, relative to each device's crystal (wl-preproc's one-rate fits) | Yes: W reaches the devices through it | The affine map adds 0. RP1's own wander remains, as any crystal's does (B8). |
| `B` jitter | Yes | From the software tick's ~150 µs per frame (wl-phantom `compare.py:126`) to the PIO's tick. |
| Absolute rate against SI | No (devices fit their own rate) | Target ≤ 1 ppm [mine]: 3.6 ms of duration an hour, ~0.3 ns on a 300 ms reaction time. ≤ 0.56 ppm at a 1 h baseline if θ ≤ 1 ms **[unverified; B7, B8]**. |
| E | No pass line [user] | Per pair σ = 0.29w: 3–9 µs for w = 10–30 µs **[unmeasured; B5]**. A 120-pair fit extrapolated 1 s gives 0.18σ ≈ 0.5–1.6 µs. Crystal wander about 1 µs at 0.03 ppm/min **[assumed]**. Plus a constant (B6). |
| `now_us()` | No (bookkeeping) | µs after the first pairs; within `WORDS_LAG_US` as widened (§2.4). |

---

## 4. What bench day must measure

**When:**
- "**Now**" can run on today's split backend with a jumper.
- "**Build**" needs the shim's second machine.
- "**After**" is the built box.

| # | When | Measurement | Line |
|---|---|---|---|
| B1 | Now | **Shared reference?** `dmesg \| grep arch_timer`. With GPIO16 jumpered to GPIO20, pulse at 1 Hz for 1 h, sample (MONO, RAW) every 100 ms, and find the rate of W against E converted to RAW. | Informational. It no longer decides the design (§5). If shared (under 0.01 ppm), the RAW→P fit reduces to an offset. |
| B2 | Now | **Today's W–B drift.** wl-phantom §5.5, at least 1 h. | Expected to fail. Report the ppm; it should match B8's rate against B1's. |
| P0 | Build, first | **Part 0 on silicon.** Task 5b Step 3 (`docs/next-session.md:128-159`) on the fixed program, with one machine. | ±100 µs and zero dropped words over 600 strobes. No loss per strobe: the old listing's 1.5 µs a strobe would show as a slope against the strobe count. |
| B3a | Build | **Start skew.** Two machines on Part 0's program, both with jmp pin GPIO16, sync-enabled; 10⁵ strobes. | The tick difference is identical on every event, and ≤ 1. |
| B3b | Build | **No loss per event, on independent streams [OC-R1].** Machine A's jmp pin takes a dense stream: a spare output toggled in software with RAW brackets, for example GPIO18→GPIO16 (header pin 12→36), ≥ 10⁵ events, widths down to a few µs. Machine B takes the barcode loop (11→35). Over ≥ 1 h, fit each machine's count against RAW through its own events' brackets. *[2026-11-07, c-direct: no jumper. Machine A reads GPIO18 itself, unclaimed **[unverified for GPIO18]**, and machine B reads GPIO17 itself (the plan's bench S, "If S.4 passes").]* | The two slopes agree within 0.01 ppm, whatever each stream's rate. A loss per event shows as ticks lost × event rate: the old listing at 1 kHz is 1,500 ppm. **B3a and B3b together are a gate (§5).** |
| B4 | Build | **Barcode visible to PIO.** c-direct with no wire, then c-loop with the 11→35 jumper. | Every lead edge stamped. c-direct working removes the wire. *[2026-11-07: answered. c-direct stamped 300 of 300 (the plan's S.4), and the user adopted it. c-loop is not needed.]* |
| B5 | Now | **Bracket width** (RAW either side of the first `set_value`) over 8 h. | Informational. It sets the rejection limit for wide brackets (§2.2, §2.3). |
| B6 | After | **E mapping.** GPIO17 to GPIO19 and to GPIO20 at once, 8 h: E mapped minus the stamp (`bench loopback`). *[2026-11-07, c-direct: GPIO17 to GPIO20 alone (11→38). The stamp machine reads GPIO17 itself.]* | **No pass line** [user]. Report the median (E's constant) and the spread at p50, p99 and p99.9. |
| B7 | Now | **NTP.** Log `adjtimex` (`offset`, `freq`, `constant`, `status`) every 10 s from a cold boot through 8 h. Note any step (REALTIME−MONO). | Informational. It sets the gate's 1 ms and the 1 h baseline: θ's spread is the SI estimate's error. |
| B8 | After (or Now: GPIO17 jumpered to GPIO16 makes each lead edge a `W`, against its `B` on MONO) | **RP1 against SI.** The calibration over 8 h, on two days. Report r, r per hour, and day to day. | r within ±200 ppm (that confirms clk_sys = 200 MHz too). **If r moves more than 1 ppm within a day, the fixed-rate target cannot hold: revisit (§5).** Leaves the first calibration file. |
| B9 | Now | **The board serial** a CM5 exposes (`/proc/device-tree/serial-number` or `/proc/cpuinfo`) **[unverified]**, and whether the RP1 crystal sits on the module **[unverified]**. | It names the calibration key. |
| B10 | After | **Missed stamps.** Count dropped `B`s over 8 h with the loopback fitted. *[2026-11-07, c-direct: there is no loopback to fit, so it runs on the built box as it stands.]* | Zero. Any drop is investigated, because each costs every device a matched pair. |
| — | After | **The ±100 µs gate.** Task 5b Step 3 as written (`docs/next-session.md:128-159`), and wl-phantom §5.5 on the B6 run. | ±100 µs. Draft Q5 is withdrawn [mine]: W–B error no longer grows with process age, and the 8 h runs cover several wraps. |

---

## 5. What would overturn it

0. **P0 shows that Part 0's program does not count 2:1 on RP1.** That stops Task 5b as well as this design, and needs its own diagnosis before anything else.
1. **B3a or B3b shows the two machines cannot be held in lockstep.** That is: the skew is not a constant ≤ 1 tick, or a machine still loses ticks per event.
   - Fall back to the draft's (a): W fitted onto the stamp machine's count, or onto RAW, through the same loopback pairs. *[2026-11-07, c-direct: the same lead-edge pairs, from the stamp machine on GPIO17.]*
   - Accept the fit's residual on W.
   - The SI map applies unchanged.
2. **B8 shows RP1's rate moving more than 1 ppm within a day.** A rate fixed per process then misses the SI target. The choices are:
   - accept it, since devices fit their own rate and the ±100 µs path is unaffected;
   - change r only at a session's open roll, which no session spans;
   - a tracking map, which wl-preproc's one-rate fits make costly (§2.2).
   This is for the user.
3. **B1 no longer overturns anything** [mine].
   - The draft took a shared reference to mean "(d) alone suffices".
   - The approved design puts B on the wire edge, which (d) cannot.
   - The SI correction needs the same calibration whatever the CPU's counter does.
   - A shared reference only makes the E fit an offset, with no code change.

---

## 6. The record: options not chosen, and the draft's questions

**Not chosen, kept as the reason for the choice:**
- **(a) Re-anchor continuously.** Fit P to MONO and convert W, with pairs from a host `pio_sm_exec` sample.
  - It puts a fit's noise, and a bias of up to w/2 that cannot be calibrated, on W, which is the hard path.
  - It keeps a software-timed B.
  - Whether an exec'd instruction costs the machine a cycle on RP1 is **[unverified]**.
  - Its pair-collection idea survives, fed instead by (c)'s loopback (§2.2). *[2026-11-07, c-direct: fed by the stamp machine reading GPIO17.]*
  - It is still the fallback if B3a or B3b fails.
- **(b) Move edges onto PIO.** It needs one machine per line, and 2–3 are free (`MAX_PIO_SMS 4`; RP1's block count **[unverified, believed 1]**).
  - Partial (b), for example `pd1_comp` on a free machine, was tied to a pass line for edges. The user declined one [2026-10-30], so it is not built.
- **(d) Stamp B and E from RAW.** It takes NTP out of B and E, but not RP1 against the CPU, and B stays software-timed.
  - Its exact MONO→RAW conversion is step 1 of §2.3.
- **c-direct** (GPIO17 as the jmp pin, no wire) is not rejected. B4 decides it.
  - Datasheet §3.1.1: *"If the same peripheral input is connected to multiple GPIOs, the peripheral sees the logical OR"*. That suggests inputs are routed by FUNCSEL, and so that c-direct will not work.
  - *[2026-11-07: B4 decided it, and c-direct works. The plan's bench S.4 ran on `wl-sync-rig0` with nothing wired: `bench stamps --stamp-pin 17 --frames 300`.*
    - *300 of 300 lead edges were stamped, with `edges_short` 0, `between` 0, and 3,080 stamps seen.*
    - *GPIO17 stayed libgpiod's output throughout (`pinctrl`: `17: op dl pd | lo // GPIO17 = output`).*
    - *So the PIO reads GPIO17's pad whatever its function **[inferred from S.4; no document read says so]**. The datasheet's sentence did not predict that.*
    - *The user adopted c-direct the same day (decision 2's note). An 8 h run on GPIO17 (S.5) is counting missed stamps.]*
- **c-gen** (PIO generates the barcode) is not in this build. It needs a new program (about 15–19 of the 19 free instructions **[unverified]**).
  - It would remove `emit_pulses`' GIL spin (`rp1/backend.py:63-69`).
  - It is a separate decision.
- **A map that tracks NTP continuously** (new in this rework) is rejected for the reason in §2.2.
- **A header field for the applied rate** is rejected for the reason in §2.6.
- **Corrected, not chosen.** The draft, and this rework's `1fc21b6`, said the second machine runs "the existing capture program unchanged", with W against B at "zero drift, by construction".
  - Both claims were wrong: the program loses 1.5 ticks per event and is not wrap-safe.
  - OC-R1 corrected them (§2.1, Part 0).

**The draft's questions, as answered:**
- **Q1, GPIO19:** answered by the approval of the recommendation that spends it (decision 2). *[2026-11-07, c-direct: not spent after all. GPIO19 stays spare (decision 2's note).]*
- **Q2, the rate:** SI seconds [user]. This rework is the answer's design.
- **Q3, a lead edge not drained:** drop that `B` with an ERROR [user] (§2.5).
- **Q4, name the clock:** `timebase` [user, done in `c67599f`]. The values are in §2.6.
- **Q5, a long gate:** withdrawn [mine] (§4).
- **Q6, a pass line for edges:** none [user].

---

## 7. Consequences for other repositories

**The build forces no pin move.** Both wl-preproc and wl-phantom pin `fe1ea63` (their `pyproject.toml`s). Nothing in the header's schema changes, and `timebase` is free text.

**wl-preproc** (to be told at build, through `docs/pending-wl-preproc-amendments.md`):
- It should learn `"rp1-pio-si"` and `"rp1-pio"`. Both are one rate across paths.
  - Until it does, its daily report names those sessions as on a clock *"whose relative rates are not known to be one"* (header spec §7). Its "a later piece learns its name" covers this.
- **A dropped `B` can widen a value step at a roll in place to 3**, which header spec §4 reads as *"a gap"*: a lost stretch of the W path that fails the timing check when a run spans it.
  - The box protects the frame before the roll (§2.5), not the one after.
  - With the box's ERROR the only witness, wl-preproc should decide whether its rule allows 3 when the ticks agree with one dropped frame.
- `barcode_delay_us` is 0, and the first `B` of a process is no longer late. Its drop of each process's first `B` (header spec §5) becomes unnecessary, and stays harmless.

**wl-phantom:**
- Its prediction of +6.5 ppm (`compare.py:37-64`) is for the split backend. On `"rp1-pio-si"` expect a W–B rate of 0 within its standard error, and `B` jitter of a tick rather than ~150 µs.
- Its one slope shared by every domain assumes *"The monotonic clock's rate does not change at a restart"* (`compare.py:124-125`). A restart may apply a newer r, which changes the rate by the calibration's update (≤ ~1 ppm expected).

---

## 8. Questions only the user can answer

- **Q1. Is "SI seconds" meant as this design reaches it, and what of a box with no calibration?**
  - **The design:** RP1's count scaled by one rate, fixed for each process and measured against NTP by earlier processes. That makes ticks SI to within about 1 ppm on average, with the crystal's own wander within a day left in.
  - **The alternative** is to follow NTP continuously. It would also remove the wander, but it puts NTP's phase error (unmeasured, plausibly a millisecond) into every device's alignment through wl-preproc's one-rate fits, and needs wl-preproc to fit rate piecewise.
  - **The uncalibrated case.** After a fresh install, a board swap or a lost file, the box records `"rp1-pio"`, at RP1's rate, until its next start. On a box left running, that can be weeks. The options:
    - **accept it.** This is the default if unanswered.
    - **restart automatically between sessions** once a 1 h estimate exists. One rate per process still holds; the cost is a restart nobody asked for, which is a new clock.
    - **apply the new r at the next session-open roll.** No session spans that roll, but it breaks one rate per `process_uuid` (`log.py:108-122`) and wl-phantom's one slope shared by every domain (`compare.py:124-125`).
  - The answers decide whether the map is affine or tracking, and whether a process may change its rate.
  - *Answered 2026-11-07 (the user): "Accept + install step".* The design stands: one rate per process, and with no usable calibration a process records `"rp1-pio"` until its next start. The deploy docs add one step: after an install or a board swap, the box runs at least 1 h and is then restarted once, between sessions. Nothing else changes, and no other repository changes.
- **Q2. When the loopback is absent for a whole process, should the box fall back?**
  - Under your Q3 answer every frame's `B` is dropped, so that process logs no barcodes and its sessions cannot be timed from the box.
  - **The edges lose their anchor too.** Without the loopback the RAW→P fit gets no pairs, so E is mapped from the origin join at the calibration's P/RAW ratio.
    - With a calibration file, E drifts from W by however far that ratio has moved since it was measured **[unverified; B8]**.
    - Without one, E drifts by the two crystals' difference: tens of ppm **[unverified]**, about 0.1 s an hour at 30 ppm.
  - **Keep that:** a loud ERROR episode and nothing else. This is the default if unanswered.
  - **Or test and fall back:** the box tests the loopback before its first segment, with one short pulse the decoders reject (it fails the lead's quarter-point checks, `barcode.py:120-123`). The pulse must end more than `IDLE_MIN_US`, 0.4 s, before the first frame, or the decoders lose that frame too (`barcode.py:37`, `:113`). If the test fails, that process falls back to today's split clock (`timebase "split"`, software-timed `B`). B and E are then on MONO together, and only W drifts.
  - The fallback keeps the split backend in the code, and gives a session with W drifting from B and E instead of one with no `B` and E unanchored.
  - *Answered 2026-11-07 (the user): "Test, then fall back".* The box tests the loopback before its first segment. On a failure, that process records `"split"`: `B` read from MONO before the frame, and edges through the MONO origin. The split backend stays in the code as a per-process mode. How the test is made is the plan's (Task 1.10a).
  - *[2026-11-07, c-direct (the user): there is no loopback, so what the test witnesses changes. Its pulse, its place and its fallback do not.*
    - *Under c-direct, two things can fail for a whole process: the stamp machine's start, and the PIO's sight of GPIO17 while libgpiod drives it. S.4 found that sight on this box. It belongs to the silicon and the kernel, not to the wiring.*
    - *Before the first segment, the box configures both machines and leaves them disabled. It then drives the same 1 ms pulse on GPIO17, witnessed by a third state machine on GPIO17, enabled alone and then disabled.*
    - *A machine that does not start, or a pulse that no machine stamps, puts the process on `"split"`, as answered.*
    - *The sync enable stays untested (a refused one cannot be seen).*
    - *The plan's Task 1.10a has the mechanism.]*
- **Q3. What is the hardware and deploy scope of this work?**
  - **A spike on the box first?** May the build use wl-sync-rig0 over ssh for a spike before the ~1.5 weeks of software that rest on it? The spike would cover Part 0's program, the stamp machine, B3a and B3b. A failure there changes the design (§5).
  - **Who fits the jumpers?**
    - 11→35 for the loopback (B4, B3b, B6, B8, B10); *[2026-11-07, c-direct: none of them needs it now]*
    - 11→38 for B6's edge input;
    - 12→36 if B3b's dense stream comes from GPIO18.
  - **Is moving the unit off `--fake` part of this work?** The unit leaves `--fake` after bench acceptance (`docs/next-session.md:192`). The deploy hold on the box is lifted, pending your restart (wl-preproc `d48943c`: "wl-sync told the box may run its change").
  - *Answered 2026-11-07 (the user): a spike first, "Yes, spike first"; the hardware, "Not yet".*
    - Nothing physical for now: no jumpers, no signal generator, no board. So nobody fits the jumpers above yet.
    - The spike covers only what needs no wiring (the plan's bench section S). B3a and B3b wait for jumpers, unless B4's c-direct case works there. *[2026-11-07: it did (S.4), so they can run with no wire, on a harness not yet planned.]*
    - The unit stays `--fake`, and moving it off is not part of this work.
    - The board's loopback resistor (the plan's Task 1.14) is not built. *[2026-11-07, c-direct (the user): dropped for good.]*
