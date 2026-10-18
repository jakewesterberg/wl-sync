# Next session — sync box

**State at handoff (2026-10-18):** Step A has landed. 303 tests pass, and CI ran green on
`16d7a71`, the last pushed commit. §6 lists this session's commits. The hardware path is now
wired into the run loop, and **none of it has run on silicon.** Bench day is next.

> **Read this first, because the previous record does not travel.**
> `.superpowers/sdd/` is gitignored (`.superpowers/sdd/.gitignore` is `*`), so the SDD
> ledger that carried earlier handoffs exists **only on the machine that wrote it**.
> Everything from it that still matters has been copied into this file, which is tracked.
> If you are on a different machine, that ledger is gone and this is all there is.

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

### Step B — bench day *(needs a Pi 5 or the CM5)*

Task 5b Steps 3–5, in `docs/superpowers/plans/2026-08-13-sync-box.md`. Run the hardware
mode by hand first:

    wl-sync record --out /data/rig1 --gpiochip /dev/gpiochipN

**Check these first, in this order.** The first three are listed the same way in
`pio/strobe_capture.pio`.

1. **`jmp x--, <next instruction>` is a legal one-cycle decrement.** The entire ±100 µs
   argument rests on it. If it is not, the padding scheme collapses and the capture path
   starts losing time again — roughly 2 µs per event, 1.2 ms over a 600-event run.
2. **Which chardev carries RP1's bank.** Confirm it by **label** (`gpioinfo`), not by hope.
   Pass it with `--gpiochip`. The default is `/dev/gpiochip0`.
3. **The timebase origin offset** between capturing `_origin_ns` and
   `pio_sm_set_enabled`. This is spent directly out of the ±100 µs budget and **must be
   measured**, not assumed.
4. **The word pump keeps up.** `_WORD_POLL_S` is 1 ms against a four-event FIFO, and it is
   a guess. The known way to miss it is `emit_pulses`, which spins holding the GIL for up
   to 2 ms per pulse, against Python's 5 ms default switch interval. **A
   `WordsDroppedError` in the journal that coincides with barcode frames is that.**
5. **The GPIO chip's label.** `_check_chip` warns if the label lacks "rp1". "pinctrl-rp1"
   is unverified, so record the real one here.
6. **The frame-time inputs see a high.** FLIR's 16S2/04S2 table gives a *loaded* high of
   2.29 V, below the comparator's 2.50 V threshold. Measure a camera group's strobe wired
   as the rig will be. See `hardware/breakout/frame-time-inputs.md` §4.

There is also a **known constant** bias of about 2.5 µs, because `mov isr, x` runs 5
cycles after the strobe is observed. Constant is the important word: it is a host-side
subtraction, not jitter. Measure it rather than trusting the comment, since it depends on
final instruction placement.

After acceptance: drop `--fake` from `deploy/wl-sync.service` and add `--gpiochip`.

### Before layout *(spec §12 item 11, committed to wl-xcon)*

Draw the task-PC line-out (AO 0/1 from Connector 0) and a buffered PFI copy of `PD1_COMP`.
The outbound bank is 8/8, so the copy needs a new channel. Regenerating that sheet also
fixes `gen_breakout_taskpc_digital.py`'s note that puts P2 on Connector 1; spec §9.2 has
the correction.

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
- **wl-xcon's Asks 1 and 2** (session id readable by a rig host, and `_02` on a subject
  change) are open. They reverse the continuous-recording design's "a day is one session"
  rule (§3), and need their own design decision. See `HANDOVER-wl-expcontroller.md`.

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

**Everything above is verified against `FakeBackend`, and against a stand-in for libpiolib,
on a laptop. The bench is the first real test of any of it.**
