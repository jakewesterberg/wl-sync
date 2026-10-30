# Next session — sync box

**State at handoff (2026-10-18):** Step A has landed, and **the RP1 backend has now run on
silicon**: 30 s on the CM5 with nothing wired, recording clean (§0). 308 tests passed at that handoff (~~404~~ 410 with session identity, below, *updated 2026-10-18 after the final review's fixes*). §6 lists
this session's commits. What is left of bench day needs signals on the pins.

**Session identity is built, and merged to main (2026-10-19).** The box
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
  clock's half seconds, located on the monotonic clock (again after a wall-clock step, since
  `bbc994a`). A grid point that would leave
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

**For the one-clock decision (added 2026-10-30).** `docs/superpowers/specs/2026-10-30-one-clock-design.md`
§3 lists the measurements that decide how code words and barcodes get onto one clock (B1–B7).
The cheapest two can be done on this same bench day:
- **B1:** `dmesg | grep arch_timer` on the box. Then, with GPIO16 jumpered to GPIO20, find the
  rate of code words against edges converted to `CLOCK_MONOTONIC_RAW`. Under 0.01 ppm means RP1
  and the CPU share a reference, and the design's simplest option is enough.
- **B4:** whether a PIO state machine can see GPIO17's barcode with no extra wire.

Run wl-phantom's ±100 µs check on a recorder process hours old, not a fresh one: the drift grows
from capture start, which happens once per process.

After acceptance: drop `--fake` from `deploy/wl-sync.service` and add `--gpiochip`.

### Step B2 — session identity on the bench *(on main since 2026-10-19)*

The loopback above ran with no session open, so it recorded into `unassigned/<date>/syncbox/`.
Then, with the unit's endpoint (`--listen 0.0.0.0:8740 --session-token-file ...`) running:

1. **The endpoint from the task PC.** Run the three `curl` commands in `deploy/README.md`
   ("The session endpoint") from the task PC, not from the box: `GET /session`, then
   `POST /session/open`, then `POST /session/close`. Check that a folder `<root>/<id>/syncbox/`
   appears with a segment in it, that a repeated `request_id` returns the same id, that a wrong
   token gets 401, and that a close naming another id gets 409 with `open`.

   **Done from a Mac over the tailnet, 2026-10-19,** with the unit installed on `wl-sync-rig0`
   (`--out /data/rig0 --fake`, started, not enabled at boot). Every check above passed:
   `2026-10-19_01` minted, the retry returned it with `closed: null`, 401 without and with a
   wrong token, 409 naming the open id on a stale close, then a clean close. The barcode ran
   unbroken across both rolls (`…568 → 569`, `…584 → 585`), every closed segment has its
   trailer, and the session's manifest is clean. The suite also passed on the box itself (409,
   1 skip: KiCad). **What is left of this item** is the task PC itself: its network path and
   its client, not the box.
2. **The MonkeyLogic checklist** in `deploy/README.md` ("MonkeyLogic on the dual-boot rig",
   ~~15~~ 17 items), once on the Windows boot against the real box. Items 8 to 10 are MATLAB and
   MonkeyLogic details that nobody has confirmed. *(Added 2026-10-18, after the final review.)*
   So are items 16 and 17: the believed minimum release, R2016b, and whether MATLAB sends
   `Content-Length`. The box answers a chunked body with 400, so item 17 decides whether
   `'open'` and `'close'` work at all.
3. *(Added 2026-10-18, after the final review.)* **A `GET /session` flood must not cost event
   codes.** Needs Step B's strobe source. During a burst of strobed codes, flood the endpoint
   from another machine, for example
   `seq 5000 | xargs -P 32 -I{} curl -s -o /dev/null http://wl-sync-rig0.local:8740/session`.
   The journal must show no `WordsDroppedError` (`capture degraded, still recording:
   event-code capture: the PIO RX FIFO was full when drained ...`). `GET` needs no token, and
   the endpoint runs one thread per connection with no cap, so a flood competes with the 1 ms
   word pump for the GIL. If codes drop, cap the handler threads; that was deliberately not
   done before this measurement.

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
  (at `27c0e97` since 2026-10-22), so it needs to be done together with wl-preproc.
  **2026-10-30:** wl-sync proposed a loss record in the same pin move as the header fields.
  wl-preproc's session agreed, in a message between the two sessions on 2026-10-30, and named
  what it would do with one. Its user had not yet been asked, and no wl-preproc document
  records the request. **The user approved the format on 2026-10-30, and the `L` record is on
  `main` as `93f02e5`.** wl-preproc's requester confirmed the batching the same day: a lost code
  word inside a run will fail that session's timing check there; a lost edge is reported only.
  Edges are declared ordered per line (`SyncBoxLogHeader.ordering`, decided the same day).
- **A desync stops event-code capture for the rest of the run.** It is logged, and the
  other paths keep recording. Exiting instead would let `Restart=always` bring capture back
  with a fresh state machine within about two seconds. That was not chosen; reconsider it
  if bench day shows desyncs.
- **Four minor review findings, recorded rather than fixed.**
  - `close()` carries on to `release()` and `pio_close` even when a thread's join timed
    out, so a thread could still be inside piolib.
  - Strobes captured while the edge thread's join waits are discarded when the state machine
    is disabled. The join now waits for the edge thread's last look (`EdgePump.finish`): about
    0.12 s on the box's clock, at most 0.82 s on a clock that stands still, inside
    `_STOP_JOIN_S` (1 s).
  - Held errors are handed from the word pump's thread to `check_health()` without a lock, so
    a stop's cause could be overwritten. This was not reproduced. The edge pump's hand-off is
    locked (`EdgePump.take_error`, `fe5a13d`).
  - A desync refuses the valid pairs that came before the slip in the same batch.
  None changes what a healthy day records.
- ~~**wl-xcon's Asks 1 and 2** (session id readable by a rig host, and `_02` on a subject
  change) are open. They reverse the continuous-recording design's "a day is one session"
  rule (§3), and need their own design decision.~~ **Answered 2026-10-18, merged to main
  2026-10-19:** see `HANDOVER-wl-expcontroller.md`, "2026-10-18, later: XC-087 and
  XC-088 answered". The continuous-recording design's §3 is amended.

### Open items from session identity

- ~~**wl-preproc's pinned wl-sync (`90b931e`) cannot read today's logs.**~~ *(resolved
  2026-10-22: wl-preproc moved its pin to `27c0e97`, wl-sync's main of that day, for exactly
  this; its `pyproject.toml` says so.)* Its header model forbade the fields added since August,
  and its reader raised on `B` and `T` records (design §7).
- **~~`SessionId.parse` accepts a trailing newline~~** *(fixed in `5dc0f1e`, 2026-10-30; wl-preproc
  agreed)*, because its regex is anchored with `$`. The
  endpoint canonicalises what it is given, and the form is pinned by wl-preproc, so this is a
  question for wl-preproc, not a silent fix here.
- ~~**`write_manifest` scans a crashed segment that has no trailer in full, on the run-loop
  thread, at every roll out of its folder.** That can skip barcode seconds after an earlier crash.~~
  **Fixed 2026-10-18, after the final review:** segment summaries are memoised in
  `wl_sync/manifest.py` (`_summarise_cached`), checked against each file's inode, size and
  mtime. **What is left:** a crashed segment is still parsed in full once per run of the
  process, by the first rebuild of its folder's manifest. That is normally the start, before
  recording begins, where it lengthens the restart's outage. After a restart in the middle of a
  visit, a crashed segment in that day's `unassigned/` folder is instead parsed at the visit's
  close, on the run-loop thread.
- **The unit still has `--fake`** until bench acceptance (above).

### Found on the box, 2026-10-19 (not fixed; each needs a decision)

- **Code words and barcodes are timed by different clocks, and NTP slews one of them.**
  - Code-word ticks come from RP1's PIO counter, which runs off the crystal and is joined to
    `CLOCK_MONOTONIC` once, when capture starts (`wl_sync/rp1/backend.py:280-281`).
  - Barcode (`B`) and edge ticks come from `CLOCK_MONOTONIC`, which follows NTP's frequency
    correction.
  - On the box NTP was applying **−6.5 ppm** (`timedatectl timesync-status`), so the two paths
    drift about 6.5 µs per second.
  - That uses up the ±100 µs budget about 15 s after capture starts, and reaches about 4 ms
    after 10 minutes.
  - **Two corrections (2026-10-30), from the draft design below:**
    - The error grows from when capture starts, which is once per process (`cli.py:504`). A
      roll does not restart it. So a session opened 2 h into a process starts about 47 ms off
      at 6.5 ppm, not at zero.
    - The rate is probably not 6.5 ppm. That figure holds only if RP1 and the CPU share an
      oscillator, and RP1 has its own 50 MHz crystal (RP1 datasheet §2.5; the Linux patch
      "arm64: dts: bcm2712: Add external clock for RP1 chipset on Rpi5"). The real rate is
      then RP1's crystal against NTP time, which is unknown. wl-phantom's "+6.5 ppm"
      prediction rests on the same assumption.
  - Nothing has measured it yet, because no code words have been captured on silicon. Bench day
    measures it (wl-phantom's design, §5.5). Run that check on a process hours old, not a fresh
    one.
  - **The design:** `docs/superpowers/specs/2026-10-30-one-clock-design.md`. **The user approved
    its direction on 2026-10-30**: RP1's PIO count becomes the box's one clock, with a second state
    machine stamping each barcode's lead edge from a GPIO17→GPIO19 loopback, and edges mapped onto
    that count in software. Their answers to its questions:
    - **Session time is corrected to SI seconds** rather than left at RP1's crystal rate. This
      changes the design, which assumed the crystal rate; the plan must say how.
    - **A frame whose lead edge is not captured** loses its `B` record, with an ERROR. No format
      change.
    - **Edges get no pass line** in this build; the software mapping is enough, and bench day
      measures its spread.
    - **The header names its clocks now** (`timebase`, `c67599f`), so the build changes only the
      value.
    Nothing of it is built yet.
- **After a power-up on a box whose RTC is dead, the NTP roll keeps a now-trusted segment in the
  previous day's folder** *(found in the final review, 2026-10-30; not fixed; the user's call)*.
  - Between sessions the box resumes the checkpoint's `unassigned/<day>/` folder. If the clock
    then reads 2000-01-01 and NTP syncs to the next day, the roll in place stays in that folder.
  - The trusted segment then carries the earlier day's id and the new day's barcode values. It
    stays there until the first session opens; there is no midnight roll between visits.
  - wl-preproc never reads `unassigned/`, so the impact is low. But
    `ls unassigned/$(date -u +%F)` finds nothing.
  - **Options:** let the NTP roll move to today's `unassigned/` when the day differs, which is a
    folder-changing roll that `process_uuid` already covers; or document the behaviour.
  - **The user chose the fix (2026-10-30).** It ships after this push.
- **After a power-up or reboot, every segment that day is flagged `ntp_unsynchronized`, even once
  NTP has synced.**
  - Trust is judged once, at process start (`cli.py:273`), and a roll reuses it (`cli.py:368`).
  - At boot the unit started at 12.5 s, and NTP synced at 33.5 s.
  - The options:
    1. re-judge trust at every roll;
    2. also roll once when NTP first syncs;
    3. make the unit wait for sync, with a timeout.

    The user chose 1 and 2 (2026-10-30).
  - **Option 1 is on `main` as `2b420c0`.**
    - A segment begun by a session open or close asks NTP again.
    - It is also untrusted, as `barcode_off_clock`, if the value being emitted is more than 60 s
      off the wall clock.
    - A probe that fails at a roll falls back to the run's last definite answer, so it never
      upgrades trust.
    - The probe costs 62 ms cold and 9 ms warm on wl-sync-rig0.
    - **Deployed to wl-sync-rig0 on 2026-10-30** (box on `4f1739c`, service restarted 10:14 CET).
  - **Option 2 is built, on local `main` as `56a89f8`, after the header commit `73f4395`.** Neither
    is pushed or deployed. It rolls inside the same folder, which wl-preproc's merged reader
    would take for a box restart, hence the order agreed with wl-preproc on 2026-10-30:
    - Each header first gains a per-process id: a UUID4 made at process start, the same in every
      segment that process writes.
    - It lands in one commit with ask 4a's barcode-delay field (null = unmeasured), so
      wl-preproc moves its pin once.
    - The header has `extra="forbid"`, so a new field breaks every older pinned reader.
    - Nothing new is deployed to the box until wl-preproc says its pin has moved. wl-phantom
      also reads the box's segments through a pinned wl-sync (`27c0e97`), so its pin must move
      too, before the box writes the new headers. **Tightened by wl-preproc on 2026-10-30:** hold
      the deploy until wl-preproc says its follow-up has MERGED, not only its pin. At its current
      pin, a second segment in a session folder reads as a restart, so the NTP roll in place would
      quarantine an open session's later code words. The follow-up reads `process_uuid`.
    - **Built as:** `process_uuid` (UUID4 text; the contract is a shared clock, not adjacency)
      and `barcode_delay_us` (`--barcode-delay-us N`, ASCII digits, at most 100 000; null =
      unmeasured). The NTP roll fires one loop iteration after NTP's first yes, so a step has
      been absorbed. Its poll has its own 0.3 s timeout and backs off to 300 s after failures.
    - **Bench day:** the first `B` of each process carries an extra delay, from the lazy GPIO
      line request in `Rp1Backend.emit_pulses`. Requesting the line before the run loop would
      remove it; that is a hardware-path change left for the bench.
- **Code words were timed 71.6 min early after a gap: fixed on `main` as `d309bf1`, deployed to
  wl-sync-rig0 on 2026-10-30.**
  - Found by wl-preproc (its ask 6).
  - **The fault:** each path (W, E, B) had its own unwrapper, which noticed a 32-bit wrap only
    when its next raw tick was lower.
    - W and E missed a wrap whenever they went 71.6 min without a record, or first spoke more
      than 71.6 min after start.
    - Every later record on that path was then 4294.97 s early against the barcodes, silently.
  - **The fix:** one clock of the box's `now_us()`, kept current by the 1 Hz barcode loop.
    - Every path takes the value matching its raw tick that is nearest that clock.
    - Runs that never missed a wrap give identical values.
- **wl-preproc's ask 4b is built, on local `main` as `4043737`, not pushed.** Records captured
  before a folder change land in the closing segment: a roll waits until every capture path has
  delivered through its boundary (spec §3.4, amended 2026-10-30).
  - The edge wait dropped from 0.2 s to 20 ms. The barrier normally waits at most about 65 ms;
    its timeout is 0.2 s, and a dead capture path is skipped rather than waited for.
  - **Bench day must measure** the kernel's stamp-to-buffer lag for edges, which the barrier
    assumes is under 20 ms (`EDGE_STAMP_LAG_US`). A runtime witness warns if the lag ever
    looks larger, but its bound includes the pump's own delays. Measure the CPU cost of the
    20 ms wait on the CM5 too.
- **`SeqnoTracker` reports false edge drops when two lines' events arrive out of order**, which
  the camera lines GPIO26/27 make routine (the kernel numbers events in the hard IRQ and buffers
  them from per-line threads). Found 2026-10-30 in the kernel source; unverified on hardware.
  **Fixed on local `main` as `fe5a13d`, not pushed.**
  - A gap in the request's numbers is held until the roll barrier's watermark passes the read
    that showed it (20–40 ms on a healthy pump), or 1024 events later, and closes silently if
    its number arrives.
  - Each line's own numbers and levels name the cause of a real loss: a kernel buffer overflow,
    coalesced interrupts (two events in a row at one level), or, with no line to blame,
    unaccounted. Each is its own `DroppedEdgeError` subclass.
  - At stop, the edge thread's last look delivers the kernel's whole backlog and settles every
    held gap before `close()` releases the request.
  - The commit message lists the known limits, each pinned by a test.
  - **Bench day:** look for the reorder on GPIO26/27 at camera rate. The commit's fix assumes it,
    from the kernel source.
- **A 4-hour soak of the RP1 backend with nothing wired passed** (2026-10-19, 11:42–15:42 UTC,
  `wl-sync record --out ~/wl-soak/rig0 --duration 14400`, run as the user beside the fake-mode
  service). Results:
  - 14 400 consecutive barcode values, clean trailer, no gaps or overlaps, no
    `capture degraded` lines;
  - 22 min of CPU in 4 h, and RSS flat at 35.9 MB;
  - NTP quiet after its initial sync.

  Two things it showed:
  - **The first frame is off the grid by design.** It is emitted at start and the second waits
    for the wall-clock grid, 1.444 s later in this run. So a fit of `B` ticks against values
    sees one start-up outlier per process.
  - **The emit loop sometimes wakes about 4 ms late.** 420 of the 14 400 intervals were off by
    3.5–4.5 ms, in pairs: runs of late frames that end, about 210 runs in 4 h. 4 ms is one tick
    of a 250 Hz kernel timer.
    - It is harmless to alignment, because the tick is sampled after the wake, just before
      emission.
    - **It bears directly on bench check 4.** The word pump polls every 1 ms against a
      four-event FIFO. A 4 ms late wake during a burst of words 1 ms apart would overflow it.
      Bench day must test word bursts, and the poll may need a different waiting strategy.
  - The start-up journal line said `first barcode 214573321`, but the first `B` record was
    `…322`: it printed the floor `_resume()` computed. **Fixed in `bdd84e7`:** the start line
    says `barcodes from N`, and a line after the first frame says `first barcode N emitted`.
- **`wl_sync.barcode.decode_edges` is quadratic on long records.** `_level_at`
  (`wl_sync/barcode.py:74-80`) scans the edge list from its start on each call, and
  `decode_edges` calls it about 38 times per frame (`:113-131`). Decoding four hours of barcode
  took 687 s in wl-phantom's tests, so a day's record takes hours. wl-preproc decodes whole
  sessions with this function (`timebase/segments.py`, `timebase/_syncbox_log.py`).
  - A `bisect` on the sorted ticks would make it linear with identical output.
  - **Fixed in `dc68cad`, on `main` since 2026-10-30.** `_level_at` now bisects the sorted
    ticks. Four hours (532,800 edges, 14,400 frames) decode in 0.25 s. Two tests in
    `tests/test_barcode.py` hold it there:
    - one compares the new decoder with a verbatim copy of the old linear-scan decoder, over
      300 messy records (a one-off sweep of 5,000 found no mismatch);
    - one decodes four hours within 30 s. The old code was still running at 90 s.
  - The codec is pinned by wl-preproc, so the outputs must stay identical. wl-preproc pins
    `27c0e97`, before the fix, and gets it when its pin moves.
  - wl-phantom also pins `27c0e97`, and decodes one frame at a time
    (its `clockfit.barcode_frames`) until its pin moves.
  - *Found 2026-10-19, by wl-phantom's phase 1.*
- **A crash and a reboot in the middle of a session were both tested on the box,** and both
  passed:
  - the box resumed into the open session;
  - the crashed segment is marked `crash`;
  - a stop at reboot writes a clean trailer;
  - no barcode value repeated;
  - the outages show as gaps.

### Recorded, not fixed, from the session-identity build

The build's ledger is gone (see the note at the top). These are the findings it parked as harmless
enough to leave, which the final whole-branch review agreed with:

- **A missed close leaves the session open.** The box keeps recording the empty rig into it, through
  midnight and through reboots, until the next open force-closes it with an ERROR line. That is spec
  §3.1's rule, not a bug, but it explains a session folder that runs into the night.
- **The endpoint has no cap on handler threads.** Step B2 item 3 measures whether that matters.
- **An open that fails after saving its state** (a failed directory fsync) logs the OSError at ERROR
  and the move into the session, but no "opened" or forced-close line.
- **~~Untested paths~~** *(each now has a test, `18cac6e`, 2026-10-30; none found a bug)*:
  - a corrupt state file, and a hand-deleted session folder, at the run level (the unit tests cover
    `read_open`);
  - `--session-token-file` without `--listen`, which logs a WARNING;
  - the endpoint shutting down when `run` raises;
  - a token file that is present but unreadable.
- **~~Test hygiene~~** *(fixed in `18cac6e`)*. The end-to-end tests' teardown does not set `done` when an assertion fails, so a
  failing test runs its thread to the end, about 10 s. `tests/test_cli.py` also has one older
  `thread.join()` without a timeout.
- **`next_id` counts a stray regular file named like an id**, so that id is skipped. This is
  conservative.

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
- **The grid is placed at start, and again after a wall-clock step** *(since `bbc994a`,
  2026-10-30)*. CLOCK_MONOTONIC follows the wall clock's NTP rate adjustments, so slewing
  keeps it mid-second. A step used to leave it at an arbitrary phase until the next restart,
  where a late wake near a second edge read one second high and the floor carried that
  second for the rest of the process. The re-place costs at most one skipped value, which can
  fall across the NTP-sync roll. A backward step still leaves values ahead, by the floor's
  design.
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
