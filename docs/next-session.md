# Next session — sync box

**State at handoff:** `main` @ `5440d22`, 274 tests passing, CI green on 3.11 and 3.13,
working tree clean, nothing unpushed.

> **Read this first, because the previous record does not travel.**
> `.superpowers/sdd/` is gitignored (`.superpowers/sdd/.gitignore` is `*`), so the SDD
> ledger that carried the last two handoffs exists **only on the machine that wrote it**.
> Everything from it that still matters has been copied into this file, which is tracked.
> If you are on a different machine, that ledger is gone and this is all there is.

---

## 1. The one open loop, and it is mine

`Rp1Backend.check_health()` exists, its docstring says **"THE RUN LOOP MUST CALL THIS,
once per iteration"** — and `wl_sync/cli.py` does not call it. Nor does it wrap the sink
in `QueuedSink`. The hardware path is **built but not wired**.

That matters more than it sounds. Every capture path in the RP1 backend fails by going
**quiet**, not by complaining:

| Failure | What you see without `check_health()` |
|---|---|
| Edge pump thread dies | Rewards and licks stop appearing. Segment keeps growing. |
| Kernel event buffer overflows | Edges silently missing. Trailer says `clean`. |
| PIO RX FIFO drops words | Event codes missing. Nothing raises. |

So a day can be recorded with half the rig's signals absent and a clean trailer on the
end saying nothing was wrong. Wiring this is the difference between the safeguards
existing and the safeguards working.

**This is laptop work.** It needs `FakeBackend`, not a Pi.

---

## 2. Next steps, in order

### Step A — wire the hardware path into `run()` *(no Pi needed)*

1. `Recorder(backend, sink=QueuedSink(writer))` on the hardware path. `QueuedSink` is
   what makes `on_edge` safe to call from the capture thread; without it a capture
   thread blocks on `os.fsync()` and the PIO FIFO overflows. **Do not substitute a
   lock** — that is the thing measured at 229 ms of blocking, and it drops words.
2. Call `backend.check_health()` once per loop iteration, next to `writer.flush()`.
   Decide what a raise should do: almost certainly log loudly and keep recording,
   since stopping the box loses more than a degraded record does.
3. `close()` ordering: `QueuedSink.close()` drains and closes the wrapped
   `SegmentWriter`, so `cli.run()`'s existing `writer.barcode_last` read must happen
   **after** that, and read through `QueuedSink.sink`.
4. Replace the `--fake` gate at `cli.py:377`. The message still says "until the RP1
   backend (Task 5b) lands" — it has landed, it is just not connected.

Everything above is testable against `FakeBackend`.

### Step B — bench day *(needs a Pi 5)*

Task 5b Steps 3–5, in `docs/superpowers/plans/2026-08-13-sync-box.md`. **Check these
three first**, in this order — they are listed the same way in
`pio/strobe_capture.pio`:

1. **`jmp x--, <next instruction>` is a legal one-cycle decrement.** The entire ±100 µs
   argument rests on it. If it is not, the padding scheme collapses and the capture path
   starts losing time again — roughly 2 µs per event, 1.2 ms over a 600-event run.
2. **Which chardev carries RP1's bank.** `Rp1Backend(chip_path=...)` defaults to
   `/dev/gpiochip0`; it has moved between releases. Confirm by **label**, not by hope.
3. **The timebase origin offset** between capturing `_origin_ns` and
   `pio_sm_set_enabled`. This is spent directly out of the ±100 µs budget and **must be
   measured**, not assumed.

Also on the bench: there is a **known constant** ~2.5 µs bias, because `mov isr, x` runs
5 cycles after the strobe is observed. Constant is the important word — it is a
host-side subtraction, not jitter — but measure it rather than trusting the comment,
since it depends on final instruction placement.

---

## 3. What is deliberately not done

- **A near-ceiling corrupt checkpoint still crash-loops.** Rig owner's call: "if it
  happens in 2156, it does not matter, do whatever is simplest." Stated, not fixed.
- **Task 1's report and Task 6's brief** carry a rationale now known to be wrong. They
  are a *record of what happened*; editing them would falsify the record.
- **`emit_pulses` spins with the GIL** for up to 2 ms per pulse, 36 pulses per frame.
  It could in principle delay the edge pump's *draining* — not its timestamps, which
  are kernel-stamped. With `event_buffer_size=1024` this should be fine; if bench day
  shows sequence gaps correlated with barcode frames, this is the cause.

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

## 6. Today's commits (`0966ecf..5440d22`)

| SHA | Subject |
|---|---|
| `0d9edb3` | mark the barcode before it reaches the wire — 3 deferred defects + residuals |
| `d6769f0` | stop the suite's result depending on how it was launched |
| `320af55` | two words per event, because 16 delta bits cannot span a trial gap |
| `90b0382` | PIO strobe capture program and a piolib backend bound to the real API |
| `d47e295` | give the sink a single owner thread, because a lock would drop words |
| `0cf94d8` | edge capture and barcode output via libgpiod v2 |
| `e32b9db` | background failures must be loud, not fatal and not silent |
| `5440d22` | backend.py no longer omits edge capture, and says how it fails |

**Everything above is verified against `FakeBackend` on a laptop. The bench is the first
real test of any of it.**
