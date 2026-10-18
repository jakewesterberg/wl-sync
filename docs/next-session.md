# Next session — sync box

**State at handoff (2026-10-18):** Step A has landed, and **the RP1 backend has now run on
silicon**: 30 s on the CM5 with nothing wired, recording clean (§0). 308 tests passed at that handoff (404 on the branch below). §6 lists
this session's commits. What is left of bench day needs signals on the pins.

**Session identity is built, and not merged.** It is on branch `session-identity`: the box
mints one session per animal's visit when the task PC asks over HTTP (`wl_sync/endpoint.py`,
`wl_sync/sessions.py`), and records each visit into `<root>/<id>/syncbox/`. Design:
`docs/superpowers/specs/2026-10-18-session-identity-design.md`. It answers wl-xcon's XC-087 and
XC-088 (`HANDOVER-wl-expcontroller.md`, "2026-10-18, later"). The tests run on a laptop against
the fake backend; nothing has called it from the task PC, and the MonkeyLogic recipe
(`deploy/monkeylogic/wlsync_session.m`) has never run under MATLAB.

> **Read this first, because the previous record does not travel.**
> `.superpowers/sdd/` is gitignored (`.superpowers/sdd/.gitignore` is `*`), so the SDD
> ledger that carried earlier handoffs exists **only on the machine that wrote it**.
> Everything from it that still matters has been copied into this file, which is tracked.
> If you are on a different machine, that ledger is gone and this is all there is.

---

## 0. The first run on silicon, and what it took (2026-10-18)

**The piolib binding could not have worked.** Found on the box, from piolib's own header and
`nm -D`, the first time it was built:

- **Of the 28 functions the backend bound through `ctypes`, `libpio.so` exports three**
  (`pio_init`, `pio_open`, `pio_close`). The rest are `static inline` in `piolib.h`,
  dispatching through a per-chip table of function pointers.
- **The library is `libpio.so`, not `libpiolib.so`**, so the loader would not have found it
  anyway.
- **`clock_get_hz(0)` is `clk_gpout0`, not `clk_sys`**, and RP1 answers every index but
  `clk_sys` with 4294967295. The clock divider would have been about 2,147 rather than 100.
- **`pio_null` and `pio_isr` carry flag bits** in piolib's enum. The bare 3 and 6 the backend
  passed were not those values.

**The fix is a C shim, `pio/wlsync_pio.c`.** `pio/build-shim.sh` compiles it on the box
together with piolib's sources into `libwlsync_pio.so`. It exports only scalar-argument
functions, configures the state machine in C, and turns piolib's `exit(1)` on error into
returned errnos. The encoder check now runs after the block is opened, because piolib's
encoders read the current block.

**On silicon, with nothing wired to the pins:**
- PIO 0 opens, `clk_sys` reads 200 MHz, and all seven encodings agree with piolib's.
- `gpiodetect` shows `gpiochip0 [pinctrl-rp1] (54 lines)`, so bench check 2 is settled.
- `/dev/pio0` is `root:gpio` with mode `0660`, so the box runs as its own user, not root.
- A 30 s run recorded 30 consecutive barcode values, a clean trailer, an empty manifest
  gaps list and no health complaints.
- Every barcode after the first was 1 s apart to within 147 µs.

`deploy/README.md` gives the steps.

---

## 1. What Step A found, beyond its own list

Step A was four items: wrap the sink in `QueuedSink`, call `check_health()` every
iteration, fix the close ordering, and replace the `--fake` gate. All four are done. Working
through them turned up two more, and both are fixed:

- **Nothing drained the PIO FIFO.** `Rp1Backend.drain()` existed and nothing called it.
  The joined RX FIFO holds 8 words, which is **four events**. On the box, the first burst
  of codes would have filled it, and `push noblock` would have discarded every later word
  without a sound. `check_health()` did not look at that path either. There is now a
  word-pump thread. Its decisions live in `WordPump` (`wl_sync/rp1/pio_capture.py`):
  - a full FIFO is held as an error and capture continues;
  - a desync, or a failing consumer, stops the pump.
  `check_health()` reports both. `tests/test_rp1_backend.py` drives that glue against a
  stand-in for libpiolib.
- **The loop was not 1 Hz on hardware.** A frame holds the pin for 200 ms, and the loop
  slept a fixed second after it, so the period was 1.2 s. Frames now land on the wall
  clock's half seconds, located once on the monotonic clock. A grid point that would leave
  the decoder less than its 400 ms of idle is skipped. Half seconds rather than whole ones,
  because an exact 1 s period sitting on a second boundary reads one second twice under
  jitter, and the clamp then reports a healthy clock as stalled.

**An independent review then found more, all fixed with tests that failed first:**

- **Only the first barcode of a run would have decoded** *(pre-existing, since `0cf94d8`)*.
  `emit_pulses` set each level at its pulse's start, so after TRAIL HIGH the pin stayed
  high between frames. The decoder needs a rising edge after 400 ms of LOW, which never
  came again. The codec's tests could not see it, because their pulse model ends every
  frame LOW.
- **One dead capture path hid the other.** `check_health()` raised at the first problem.
  It now raises `CaptureDegraded` carrying every problem, and the run loop keys episodes on
  the *kinds* of problem, not the message.
- **One kernel overflow became a growing "lost" report every second** *(pre-existing)*.
  `SeqnoTracker` raised before moving its baseline.
- **A wrong `--gpiochip` left a header-only segment per restart.** The chip is now checked
  when the backend is built, before `run()` creates anything.
- **A failing `backend.close()` lost the trailer**, so a good run read as a crash. Now it is
  logged and the segment still closes. Health is also checked once after capture stops,
  so a failure in the last second is reported.
- **A FIFO found holding 8 words now counts as full**, since any push into it was dropped.

Decided along the way. A `check_health()` failure is logged at ERROR once per episode
("capture degraded, still recording: ...") and recording continues. The unit still ships
`--fake` until bench acceptance (`deploy/README.md`).

---

## 2. Next steps, in order

### Step B — bench day *(on the CM5, which is ready)*

The box is `wl-sync-rig0`, a CM5 Lite running Raspberry Pi OS Lite (trixie) from its
NVMe, with wl-sync in `~/wl-sync-venv` and `libwlsync_pio.so` installed (§0, and
`deploy/README.md`). `ssh wl-sync-rig0` reaches it over the tailnet.

**First, with one jumper wire and nothing else: the box decoding its own barcode.** Connect
GPIO17 (the barcode output, header pin 11) to GPIO20 (an edge input, header pin 38), then:

    ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --duration 60
    ~/wl-sync-venv/bin/python -m wl_sync.bench loopback "/tmp/wl-bench/rig0/unassigned/$(date -u +%F)/syncbox"

`wl_sync.bench` matches every frame decoded from GPIO20 against the `B` record the box logged,
and exits non-zero on any mismatch. That checks the end-of-frame fix on silicon, where no fake
could. The first frame is reported as not checkable, by design. The lead-edge offset it prints
is the software latency between the logged tick and the pin rising; record it here. Without
the jumper it says so, which was checked on the box.

Then Task 5b Steps 3–5, in `docs/superpowers/plans/2026-08-13-sync-box.md`, which need a
second board or a signal generator on the strobe and data lines:

    ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0

**Check these first, in this order.** The first three are listed the same way in
`pio/strobe_capture.pio`.

1. **`jmp x--, <next instruction>` is a legal one-cycle decrement.** The entire ±100 µs
   argument rests on it. If it is not, the padding scheme collapses and the capture path
   starts losing time again — roughly 2 µs per event, 1.2 ms over a 600-event run.
2. ~~**Which chardev carries RP1's bank.**~~ **Settled 2026-10-18:** it is `gpiochip0
   [pinctrl-rp1] (54 lines)`, the default.
3. **The timebase origin offset** between capturing `_origin_ns` and
   `pio_sm_set_enabled`. This is spent directly out of the ±100 µs budget and **must be
   measured**, not assumed.
4. **The word pump keeps up.** `_WORD_POLL_S` is 1 ms against a four-event FIFO, and it is
   a guess. The known way to miss it is `emit_pulses`, which spins holding the GIL for up
   to 2 ms per pulse, against Python's 5 ms default switch interval. **A
   `WordsDroppedError` in the journal that coincides with barcode frames is that.**
5. ~~**The GPIO chip's label.**~~ **Settled:** `pinctrl-rp1`, as `_check_chip` expects. Note
   that `gpiochip10` has 32 lines, so the size check alone would not catch it; the label
   warning would.
6. **The frame-time inputs see a high.** FLIR's 16S2/04S2 table gives a *loaded* high of
   2.29 V, below the comparator's 2.50 V threshold. Measure a camera group's strobe wired
   as the rig will be. See `hardware/breakout/frame-time-inputs.md` §4.

There is also a **known constant** bias of about 2.5 µs, because `mov isr, x` runs 5
cycles after the strobe is observed. Constant is the important word: it is a host-side
subtraction, not jitter. Measure it rather than trusting the comment, since it depends on
final instruction placement.

After acceptance: drop `--fake` from `deploy/wl-sync.service` and add `--gpiochip`.

### Step B2 — session identity on the bench *(after the branch merges, or from it)*

The loopback above ran with no session open, so it recorded into `unassigned/<date>/syncbox/`.
Then, with the unit's endpoint (`--listen 0.0.0.0:8740 --session-token-file ...`) running:

1. **The endpoint from the task PC.** Run the three `curl` commands in `deploy/README.md`
   ("The session endpoint") from the task PC, not from the box: `GET /session`, then
   `POST /session/open`, then `POST /session/close`. Check that a folder `<root>/<id>/syncbox/`
   appears with a segment in it, that a repeated `request_id` returns the same id, that a wrong
   token gets 401, and that a close naming another id gets 409 with `open`.
2. **The MonkeyLogic checklist** in `deploy/README.md` ("MonkeyLogic on the dual-boot rig",
   15 items), once on the Windows boot against the real box. Items 8 to 10 are MATLAB and
   MonkeyLogic details that nobody has confirmed.

### ~~Before layout~~ *(spec §12 item 11) — drawn 2026-10-18*

The task-PC line-out (J57) and the PFI copy of `PD1_COMP` (PFI 7) are in the schematic, the
netlist contract and the BOM. `hardware/breakout/line-out-and-pfi-copy.md` gives the
reasoning. In layout, keep R221–R223 beside Connector 0. The back face has 7.2 mm of slack
left, so no further panel connector fits. One fix-up came with it:
`gen_breakout_taskpc_digital.py` no longer claims P2 is on Connector 1.

---

## 3. What is deliberately not done

- **A near-ceiling corrupt checkpoint still crash-loops.** Rig owner's call: "if it
  happens in 2156, it does not matter, do whatever is simplest." Stated, not fixed.
- **Task 1's report and Task 6's brief** carry a rationale now known to be wrong. They
  are a *record of what happened*; editing them would falsify the record.
- **`emit_pulses` spins with the GIL** for up to 2 ms per pulse, 36 pulses per frame.
  It could in principle delay the edge pump's *draining* — not its timestamps, which
  are kernel-stamped. With `event_buffer_size=1024` this should be fine; if bench day
  shows sequence gaps correlated with barcode frames, this is the cause. The word pump
  is more exposed than the edge pump, because it has a four-event FIFO where the kernel
  gives edges a 1024-event buffer. That is bench check 4.
- **A dropped edge, or a full FIFO, leaves no mark in the segment.** It reaches the
  journal only. Marking it in the file would change the log format, which wl-preproc pins
  at `90b931e`, so it needs to be done together with wl-preproc.
- **A desync stops event-code capture for the rest of the run.** It is logged, and the
  other paths keep recording. Exiting instead would let `Restart=always` bring capture back
  with a fresh state machine within about two seconds. That was not chosen; reconsider it
  if bench day shows desyncs.
- **Four minor review findings, recorded rather than fixed.**
  - `close()` carries on to `release()` and `pio_close` even when a thread's join timed
    out, so a thread could still be inside piolib.
  - Strobes captured while the edge thread's join waits (up to 0.2 s) are discarded when
    the state machine is disabled.
  - Held errors are handed from the pump threads to `check_health()` without a lock, so a
    stop's cause could be overwritten. This was not reproduced.
  - A desync refuses the valid pairs that came before the slip in the same batch.
  None changes what a healthy day records.
- ~~**wl-xcon's Asks 1 and 2** (session id readable by a rig host, and `_02` on a subject
  change) are open. They reverse the continuous-recording design's "a day is one session"
  rule (§3), and need their own design decision.~~ **Answered 2026-10-18, on branch
  `session-identity`:** see `HANDOVER-wl-expcontroller.md`, "2026-10-18, later: XC-087 and
  XC-088 answered". The continuous-recording design's §3 is amended.

### Open items from session identity

- **wl-preproc's pinned wl-sync (`90b931e`) cannot read today's logs.** Its header model forbids
  the fields added since August, and its reader raises on `B` and `T` records (design §7). The
  layout is unchanged from what wl-preproc reads, `<session>/syncbox/*.log`; the format is not.
  A message for wl-preproc.
- **`SessionId.parse` accepts a trailing newline**, because its regex is anchored with `$`. The
  endpoint canonicalises what it is given, and the form is pinned by wl-preproc, so this is a
  question for wl-preproc, not a silent fix here.
- **`write_manifest` scans a crashed segment that has no trailer in full, on the run-loop
  thread, at every roll out of its folder.** That can skip barcode seconds after an earlier crash.
- **The unit still has `--fake`** until bench acceptance (above).

---

## 4. Things that are true and easy to get wrong

Each of these was arrived at the hard way. They are load-bearing.

- **The barcode value is decided in exactly one place** (`cli._ValueSource`) and marked
  spent *before* it reaches the wire. A second clamp anywhere downstream re-opens the
  duplicate-barcode defect one line lower. `BarcodeGenerator` chooses nothing on
  purpose.
- **Mark-before-emit burns one value per crash restart.** That is fine only because
  `RestartSec=1` means the clock advances one per second too. A faster restart cadence
  creeps. The rejected design burned 60 s per restart *regardless* of cadence.
- **`QueuedSink.flush()` is a barrier, not a marker.** It waits, which is what preserves
  spec §4's one-second crash-loss bound exactly rather than amending it.
- **A dropped edge does not stop capture; a failing consumer does.** Deliberate: a few
  lost edges are a small detectable gap, and stopping would cost the rest of the day's
  edges instead.
- **The next frame's target is always past this frame's end plus the decoder's idle**,
  so two frames can never share a second, whatever the float error in the clock.
- **The grid is placed once, at start.** CLOCK_MONOTONIC follows the wall clock's NTP rate
  adjustments, so it stays mid-second. A wall-clock *step* moves it until the next restart.
- **Two FIFO words per event, not one.** 16 data lines leave 16 delta bits = 65.535 ms,
  against ~3 s trials. No clock divider fixes it. The upper-bits-clear check on the data
  word is what makes a dropped word detectable rather than a permanent silent swap.

---

## 5. Verification conventions this project holds to

Worth keeping, because they caught real defects today.

- **Run the suite the way CI does.** `pytest`, not `python -m pytest`. They differed for
  weeks (`sys.path`), green locally and red in CI. `pythonpath = ["."]` fixes it, but the
  habit is the safeguard.
- **A new test must be shown to FAIL against the pre-fix sources**, not merely pass
  after. Where a test only closes a coverage gap, say so in its docstring rather than
  counting it as discrimination.
- **Measure, do not argue.** Every design decision that mattered today was settled by a
  number: 229 ms of lock blocking, 1449 s of drift, 313 lost increments in 2000,
  212.93 ms → 0.16 ms.
- **Fetch the real API rather than guessing.** piolib's `pio_encode_*` removed the
  assembler from the path entirely; libgpiod's `EdgeEvent.global_seqno` is a kernel-drop
  detector that would never have been guessed. Both *changed* the design rather than
  confirming it.

---

## 6. This session's commits (2026-10-18)

| Subject |
|---|
| Hardware docs: the 16S2's GPIO table, the 6343's PFI pins, and two commitments |
| Reply to wl-xcon's asks of 2026-10-11 |
| wl.yaml: say where the build is |
| Step A: wire the RP1 backend into the run loop |
| wl.yaml: Step A has landed |
| deploy: how the sync box's OS was set up, headless |
| deploy: the sync box can join the tailnet |
| RP1 backend: bind piolib through a C shim, and first run on silicon |
| bench: check the box's own barcode, read back through an edge input |
| Breakout: propose the task-PC line-out and the PFI copy of PD1_COMP |
| Breakout: draw the task-PC line-out and the PFI copy of PD1_COMP |

**The capture logic is verified against fakes on a laptop. On silicon, so far: the shim,
the encodings, the chip, the pacing and a clean 30 s run, all with nothing wired.**
