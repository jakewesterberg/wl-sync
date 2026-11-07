# What wl-expcontroller needs from wl-sync

> **Naming note, 2026-09-28.** `wl-expcontroller` was renamed `wl-xcon`. This
> document predates the rename and names the controller `wl-expcontroller`
> throughout, which is what it was called when this handover was written and
> consolidated, including the `expcontroller/` directory name in Ask 1 below —
> a name written into the rig directory layout, not merely prose about it.
> The registry carries `aliases: [wl-expcontroller]`, so the name still
> resolves.

**From:** `wl-expcontroller`, the behavioural task controller replacing NIMH MonkeyLogic.
**Date:** 2026-08-31. **Read against** `wl-sync` at `16ded9a`.

This is the consolidated version of `wl-expcontroller/docs/pending-wl-sync-amendments.md`.
Two asks, one of which has an answer this project did not supply, plus three dependencies
recorded so a change here becomes visible.

**Nothing here asks you to change the barcode codec, the log format, or session identity's
*form*.** Those are yours and wl-expcontroller consumes them unchanged — it deleted its own
duplicates of all three when it found them (its S3 spec is largely a list of things it stopped
doing because this repository already does them).

---

## Ask 1 — a rig host cannot learn the session id

`wl_sync/session.py` mints `YYYY-MM-DD_NN` and its docstring names the consumers: *"Everything
downstream — the rig directory layout, the ELN, wl-preproc — consumes it."* `wl-preproc`'s
`contracts/paths.py` imports `SessionId` from here and keys the whole session directory on it,
and wl-expcontroller writes into `expcontroller/` beneath that directory.

**But nothing offers the value to another machine.** The date half is derivable; `_NN` is not.

**Why not guess.** Assuming `_01` would make the task PC a second authority on session identity —
right almost always, and silently wrong exactly when the day is unusual, which is when a
misfiled recording costs most.

**The ask.** Any way for a rig host to *read* it rather than infer it. If the sync box's output
root is reachable from the task PC, reading the newest day directory may already be sufficient
and needs no code — **except that Ask 2 breaks that shortcut**, see below. A one-line file or a
trivial read-only endpoint would also do. No preference; the requirement is only that the value
be readable and that this box stay the only thing that mints it.

---

## Ask 2 — a day is not one session here, so something must mint `_02`

The continuous-recording design rules that *"a day is one session, hence `_01`; a restart joins
that directory as a new segment rather than minting `_02`."*

**Two animals will routinely work in one day on one rig** (rig owner, 2026-08-31). So that rule
does not hold for these rigs.

**And wl-preproc's frozen contract already settles what `_02` must mean.**
`contracts/manifest.py::SessionManifest` sits at the root of the session directory and carries
**exactly one `subject`** — a single string, not a list — beside the `session_id` it validates
against your form. A directory can describe one subject and no more. **So the session id must
change when the subject changes; there is no representation in which it does not.**

That is a consequence of a frozen interface in a third repository, not anyone's preference.

**The ask.** A subject change mints `_02`. The remaining question is mechanical: this box runs
continuously across both animals and cannot know when one leaves the chair, so **something has to
tell it.** Whether that is the task PC, wl.works, or a person pressing a button is yours — but
whatever it is, **that is also the moment the task PC learns the new id**, so both asks share one
mechanism and Ask 1's "read the newest directory" shortcut stops working without it.

---

## Three dependencies, recorded so a change here is visible

`wlo dependents wl-sync` would not otherwise show these. None need to change.

1. **This box independently records every event word the task PC strobes** (`W` records via PIO
   capture). That is why wl-expcontroller's own log is deliberately *not* a timing record and
   carries meaning only — a simplification taken on the strength of your capture, not its own.
2. **The barcode carries identity, not timing**, and one frame is guaranteed in any 2.0 s window.
   wl-expcontroller never emits one and never derives time from one.
3. **Cameras free-run and this box records their `ExposureActive` strobes** (GPIO 26/27). So the
   controller does not trigger cameras and does not set their rate — a role it might otherwise
   have assumed.

---

## One thing the breakout board's design now depends on, from our side

The board's §3.1 fixes two photodiode roles: `A_PD1` the task patch, `A_PD2` the flip patch
alternating every refresh. wl-expcontroller treats both as **inputs to the control loop**, not
only as recorded signals — a state can wait on `PD1_COMP` before advancing, and `PD2_COMP` drives
live dropped-frame detection.

Two consequences you may want to know:

- **The flip patch must alternate unconditionally**, including during blanks, aborts and pauses.
  A frame clock that stops during an abort is not a frame clock. That is our obligation, stated
  here because it is a property of the signal you specified.
- **Patch placement is now geometrically constrained.** The rigs use a split-screen mirror
  stereoscope, so both patches must sit outside *both* eyes' viewports or the flip patch becomes
  a flickering distractor in one eye's field. The chosen location is a ~~**bottom strip, 2.18 cm ×
  full panel width**, created by stopping the far mirror to ±17° vertical~~ **bottom strip,
  3.22 cm × full panel width, one of two (top and bottom) left by a removable mask at the panel
  starting at ±12°, screen at 50 cm** *(superseded 2026-09-28, replaced here 2026-10-18 at
  wl-xcon's request, XC-090; confirmed in the 2026-10-18 replies below)*. Worth confirming
  against your comparator design before the mirrors are mounted.

---

## One open item of yours that we would like assigned

Breakout spec §12 item 6 leaves the default eight analog channels for Intan as a mux setting.
Separately, three **misc analog BNC inputs** are unassigned. wl-expcontroller proposes **one of
them for an audio verification tap**: the rigs present auditory stimuli and auditory performance
feedback, audio onset timing on Linux is worse and less visible than video, and an electrical tap
of the audio output gives sound onset on the NI clock without room-acoustics smearing. It becomes
protocol V7 on our side. Your call whether that is a good use of the channel.

---

## Where the reasoning lives

`wl-expcontroller/docs/superpowers/specs/` — S3 for the integration and what we deleted,
S6 for the I/O side, S4 for the display and the audio tap, and
`2026-08-31-stereoscope-optics-drawing.md` for the patch-placement geometry.

---

# 2026-10-11: open asks from wl-xcon

**Why now.** The PI decided on 2026-10-11 to send every open ask from wl-xcon to its sibling
repositories at once. wl-xcon's engine build C is being planned, and build G (sound) cannot start
without an answer to Ask 7. **How to answer:** in this file under each ask, or in this repository's own
checkpoint, as before. "No" and "not now" are answers; "this is the wrong shape" is the most useful one.

**Read against** `wl-sync` at `d933deb`. wl-xcon's backlog IDs (`XC-###`) are given so a reply can
name them. All wl-xcon links are on `main`:
`https://github.com/jakewesterberg/wl-xcon/blob/main/<path>`.

**Nothing here asks you to change the barcode codec, the log format, or session identity's *form*.**
Asks 1 to 6 are the 2026-08-31 asks above, still open, with what has changed on our side. Asks 7 and 8
are new.

## Open asks

**1. XC-087: a rig host cannot learn the session id.** Unchanged from Ask 1 above. A way for the task PC
to *read* the id the sync box minted (shared output root, a one-line file, or a read-only endpoint; your
call). Why: `taskd` names its directory from it and must not guess `_NN`.
Source: [pending-wl-sync-amendments.md](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/pending-wl-sync-amendments.md).
Not asked: any change to the id's form, or for the task PC to mint anything. Blocks: the first run
against a real sync box; nothing in a named engine build. (Our directory is `xcon/`, not
`expcontroller/`; see the naming note at the top.)

**2. XC-088: a subject change mints `_02`, and something tells the box.** Unchanged from Ask 2 above.
`SessionManifest` carries exactly one `subject`, so two animals in one day need two ids. Which actor tells
the box (task PC, wl.works, a button) is yours to decide; it is also how Ask 1's reader learns the new id.
Source: [pending-wl-sync-amendments.md](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/pending-wl-sync-amendments.md).
Not asked: a choice of actor. Note that your continuous-recording design (`docs/superpowers/specs/2026-08-16-syncbox-continuous-recording-design.md:70`)
says "a restart joins that directory ... rather than minting `_02`", so this changes a design rule on
your side; it is not only a missing feature. Blocks: nothing in a named build.

**3. XC-089: assign a misc analog BNC to the audio verification tap.** Unchanged. One of the three misc
inputs (`A_MISC1` to `A_MISC3`) carries a copy of the audio output so NI records sound onset. Source:
[S4 §12 item 3](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S4-stimulus-presentation-design.md).
Why: validation V7 measures audio onset through it. Not asked: any change to the misc inputs (your
spec's §12 item 7 records they are input-only, which suits a tap). Blocks: XC-074 (V7), which waits on
the rig anyway, and the verification half of engine build G.

**4. XC-090: confirm the photodiode patch geometry, and replace the numbers in Ask "One thing the
breakout board's design now depends on" above.** That text says a bottom strip of 2.18 cm at ±17°. The
optics drawing now says: the field stop is a removable mask at the panel starting at ±12° (PI,
2026-09-28), giving **3.22 cm strips top and bottom** with the screen at 50 cm, patches in the bottom
strip, and direct viewing shares the same fixed screen at 50 cm. Please confirm the comparators and the
photodiode housings work with that placement. Source:
[optics drawing §8 item 3](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-stereoscope-optics-drawing.md).
Not asked: any board change; a "fine as stated" is the expected answer. Blocks: rig commissioning,
not an engine build.

**5. XC-091: is the delivered-line record readable live, or only at session end?** Our session manager
reconciles fluid against the box's delivered line (S8 §5.1). Since 2026-09-06 nothing in-session depends
on a live read, so session end is enough if that is what you have. What we read in your source:
reward lines are individual transitions logged as their own record type (`wl_sync/log.py:12-13`), and
the emit loop flushes every second (`wl_sync/service.py:200-205`). We could not tell from that whether a
second process can read an open segment. Source:
[S8 §8 item 2](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S8-session-management-design.md).
Not asked: a live interface. Blocks: nothing. Welfare-critical on our side, so please state it exactly.

**6. XC-092: `[flir_bfs_gpio]` cites the BFS-U3-200S6 page's 0.87 V low; our S0 spec's
figures come from the BFS-U3-16S2 page.** The 16S2 page gives 0.92 V unloaded and 0.86 V loaded at the same 5 V / 1 kΩ. Please add
the 16S2 figures beside the 200S6 row, or say the 200S6 row is meant to cover both (we have not verified
which model each rig runs). Your own note
(`hardware/datasheet-params.toml:591-628`) already says the best published low misses the 0.8 V buffer
input, so this is about which page the pin cites, not a new defect. Our reading is that nothing breaks:
the frame-time comparator's 2.50 V threshold leaves 1.58 V of low margin. Source:
[S0 §7.4](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S0-hosts-and-hardware-design.md#74-the-trigger-fan-out-board-custom-needs-a-design-check).
Not asked: a change to the front end. Blocks: wl-xcon's trigger fan-out board design check, which copies
your frame-time front end (`hardware/breakout/frame-time-inputs.md`) at `92714ce`; a change there is
visible to us only if you say so.

**7. Engine spec §21 (backlog XC-286 is the docs side): a two-channel line-out from the task PC card's
analog outputs, started by the task-patch edge.** The PI decided on 2026-10-07 that sounds play through
two of the PCIe-6343's four analog outputs, not through the host's sound stack. The output is armed at
the decision and **started by the task patch's next edge**, which marks the frame the visual change lands
on; the speaker tap (Ask 3) then measures the real onset. wl-xcon needs from you: (a) a place for the two
analog outputs to leave the rig as a line-out the speaker amplifier can take, and (b) confirmation that
`PD1_COMP` reaches the task PC on a pin the 6343 can use as a start trigger (a PFI line, not only a
P0.x line read in software). Source:
[engine spec §9.1 and §21](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-10-07-engine-design.md#9-media-and-sound-n6-nr5-nr6).
Two things the spec assumed that your source does not show: the spec says "beside the existing speaker
tap", but no speaker tap exists in this repository (Ask 3 is unassigned), and the misc BNCs are input-only
by construction (`docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md:990`, an output port
"would be a respin"). We also found no task-PC analog-output net in `hardware/README.md`; the task PC's
connector 0 carries nine analog *inputs*. So (a) may be new copper, or may be nothing at all if the card's
connector can feed the amplifier directly; that is your call, and we would rather hear it is outside the
board. Not asked: a design, a part, or a schedule. Blocks: engine **build G** (media and sound); builds
A to F do not need it.

**8. Engine spec §21: top-and-bottom test patches for validation V1's scan-out measurement.** The screen
scans top to bottom (Blur Busters, read 2026-10-07; UNVERIFIED for this panel) and both rig setups put
the light sensors at the bottom, so a stimulus higher on the screen lights later than the sensor sees.
V1 therefore needs the scan delay measured with a patch at the top and one at the bottom, and wl-xcon's
analysis applies it across a stimulus's vertical extent. The patches are drawn by wl-xcon's display; what
we need from you is the *sensor side*: can one photodiode be mounted at the top of the screen for the
V1 run (a rig procedure, for instance `A_PD2` moved temporarily, or a lead long enough), given that your
board fixes `A_PD1` and `A_PD2` to the task and flip patches? Or does a top reading need a third
photodiode channel? Source:
[engine spec §6.5 and §21](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-10-07-engine-design.md#6-when-n3-nr3);
[validation V1](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/validation.md). Not asked: a
change to the patch roles in normal use, or a measured scan delay (that is V1, on the rig). Blocks: the
scan-delay term in the engine's contingent-delay figure (spec §6.10), which is labeled a lower bound until
V1 exists; no engine build.

## Closed since last time

**None.** This file had no answer section, and we looked for answers in your source rather than assume
silence: `wl_sync/session.py` still mints `YYYY-MM-DD_NN` with nothing that offers it to another host,
`next_segment_index` still counts segments within a day, no misc BNC is assigned, and `hardware/` has no
3.22 cm figure. `[flir_bfs_gpio]` states its own 0.87 V finding but does not reply to Ask 6.

## Recorded dependency, not an ask

wl-xcon's S0 §7.4 trigger fan-out board copies this repository's frame-time input stage (100 Ω, BAT54S,
LM339 at 2.50 V with about 33 mV of hysteresis) and its '541 camera drive with 47 Ω series resistors
(your finding F4), read at `92714ce`. `wlo dependents wl-sync` does not show it.

---

# 2026-10-18: replies from wl-sync

**Read against** wl-xcon `main` at `a607414`; every wl-xcon passage relied on below was read there.
Numbered as the 2026-10-11 asks above. Paths without a repository name are this repository's.

## 1, 2. XC-087, XC-088: ~~not answered in this reply~~ answered below, in "2026-10-18, later: XC-087 and XC-088 answered"

Both are still open, and they share one mechanism: whatever tells the box the subject changed. That
reverses the continuous-recording design's "a day is one session" rule (its §3) and `cli.day_directory`'s
fixed `_01`. It needs its own design decision on this side, and this reply does not make it.

**Update, 2026-10-18, later:** the design decision is made, and the answer is in the section at the end of this file.

## 3. XC-089: the audio tap

**Assigned: `A_MISC3` is the audio verification tap** (decided 2026-10-18; spec §12 item 12).

- **Why not `A_MISC1`.** All three misc inputs are identical except `A_MISC1`, which also feeds the
  board's fourth comparator section (not fitted) and its DAC channel `VOUTD`
  (`hardware/gen/gen_breakout_comparators.py:373`). That makes it the only spare input that can ever
  be thresholded, and a tap wants its waveform recorded, not a threshold.
- **Range.** ±5 V at ÷1, ±10 V at ÷2, set by a ganged shunt across both legs
  (`hardware/gen/gen_breakout_analog_frontend.py:107`). Past that the clamps protect the hardware but the signal
  clips. A tap at a power amplifier's speaker terminals can exceed ±10 V; a line-level tap will not.
  Where you take the tap is yours to choose.
- **No anti-alias filter.** Only the microphone has one (spec §6.4). The recording NI scans at
  40 kHz (spec §9.3), so content above 20 kHz folds back. Onset timing at 25 µs per sample is
  unaffected, but a spectral measurement of the tap would not be.
- **Where it lands.** Recording NI AI15 (Connector 0 pin 23), **the task PC's own card as AI8**
  (Connector 0 pin 34, `hardware/gen/gen_breakout_taskpc_digital.py:1050`), and Intan through the mux. It is not in
  Intan's default eight (spec §3.2), and one mux write adds it. The task-PC copy lands on the same card
  as the analog outputs that play the sound (Ask 7), so V7 can measure command-to-tap latency on one
  clock.

## 4. XC-090: fine as stated, on two conditions

**Nothing on this board depends on where on the screen the patches sit.** Each comparator compares its
TIA's output with a DAC-set threshold (spec §6.5, §2 decision 10). Placement matters only through how
much light reaches the sensor.

- **Size.** The sensor is a Thorlabs FDS100: a TO-5 can 0.36 in (9.1 mm) across, with 13 mm² of active
  area (`[fds100]`). A 3.22 cm strip is three and a half cans tall, so both sensors fit side by side
  in the bottom strip, with room for housing walls.
- **Condition 1: the patch must fill the sensor's view.** The TIA gain (finding F3, `R38` = 180 kΩ,
  peak white near 2.0 V) was set from about 0.29 mW/cm² at the sensor. That assumes a sensor flush
  against a patch at least as large as its window. A smaller patch, or a sensor standing off the
  panel, gives a smaller signal. Thresholds step in 0.5 mV, so bring-up can follow it, but this is the
  one placement choice that moves this board's numbers.
- **Condition 2: the housings are not this repository's.** Nothing here specifies one. Building and
  measuring them stays with the rig build, as your direct-view design §4 already says.
- The threshold-setting work is still undone (spec §2 decision 11, "before first use"). The new
  placement doesn't change that.

The superseded figures (2.18 cm at ±17°) in "One thing the breakout board's design now depends on"
above are struck through and replaced in place, dated.

## 5. XC-091: live reads work on the box itself; here is exactly what you would be reading

1. **Nothing on the box records the real delivered line until bench day.** *(Updated later on
   2026-10-18, when Step A landed.)* `wl-sync record` without `--fake` now runs the RP1 backend, but
   that backend has never run on silicon. So the unit keeps `--fake` until bench acceptance
   (`deploy/wl-sync.service`), and in fake mode nothing reaches the pins. Everything below describes
   the hardware mode.
2. **What is recorded.** GPIO 23 (`rwd_dlvr`, `wl_sync/pins.py:32`) is one of the individually captured
   edge lines (`pins.py:40`). Every transition, rising and falling, is an `E,<tick_us>,23,<level>` line.
   `tick_us` is this box's own microsecond counter, unwrapped within one segment. It is neither wall
   time nor the task PC's clock.
3. **What "delivered" means.** `RWD_DLVR` is the reward-OR gate's output: the signal sent to the reward
   driver, from your commanded line or the manual one-shot (spec §3.1, §7.2). **It is not a measurement
   of fluid.** An unpowered or unplugged driver, an empty reservoir and a blocked line all still record
   as delivered. The record says what the driver was told to do, not what the animal received.
4. **When another process can see it.** Records are written as they arrive and pushed to the OS once
   per emit-loop iteration, with `flush()` and then `fsync()` (`wl_sync/segment.py:110-113`).
   **The loop runs once a second (on the wall clock's half seconds), so new records become visible
   about once a second.** *(Updated later on 2026-10-18. Before Step A it was a 200 ms barcode frame plus a fixed
   1 s sleep, about 1.2 s.)* Between flushes Python's own buffer can push out part of a line,
   so a reader can see a torn last line. Drop it and read it on the next pass; `wl_sync.log.read_log`
   already does this (`log.py:155`). The writer holds no lock (`"x"` is create-exclusive, not
   read-exclusive), so any process on the box can open the open segment read-only.
5. **Where it is, and who can reach it.** ~~The record is at `/data/rig1/<YYYY-MM-DD>_01/seg-NNN.log` on
   the box's own NVMe (the unit file and `cli.day_directory`).~~ *(Corrected 2026-10-18, later:
   session identity deleted `cli.day_directory` and changed the layout.)* The record is on the box's
   own NVMe, under the unit file's `--out /data/rig1`. During a visit it is in
   `/data/rig1/<id>/syncbox/`, where `<id>` is the id the open returned; between visits it is in
   `/data/rig1/unassigned/<YYYY-MM-DD>/syncbox/`, named by a UTC date (`wl_sync/sessions.py:48-59`).
   A visit's record is every `seg-NNN.log` in its folder, from `seg-000`: one segment for each run of
   the recording process during the visit. **Nothing in this repository makes that
   directory readable from another host.** NAS transfer is out of the design's scope
   (continuous-recording design §9) and not implemented. ~~So from the task PC, a live read and a
   session-end read both need the same new thing: the reader Ask 1 asks for.~~ *(Corrected
   2026-10-18, later.)* Ask 1's answer, the session endpoint below, does not change that. It serves
   the open session's id, not this record; reading the record over the network is a non-goal of the
   session-identity design (§2), which leaves room for such a route to be added later. So a read
   from the task PC, live or at session end, still needs something that does not exist yet, and
   XC-384's wait on XC-087 (wl-xcon `docs/backlog.md:35`, read at `7398d3e`) does not end with
   XC-087's answer.
6. ~~**There is no session end on this side.** The box records all day, and a segment closes only when
   the process stops. Your session ends in the middle of a segment, so a session-end read is also a
   read of an open segment. Live and session-end reads differ only in how often you read.~~
   *(Corrected 2026-10-18, later: session identity gave the box a session end.)* **A session ends
   at `POST /session/close`, and its last segment closes then.** The box still records all day, but
   a segment now closes at every open and every close, not only when the process stops: the run loop
   writes the segment's trailer, fsyncs it, and carries on in a new segment in the next folder
   (`wl_sync/cli.py:337-396`, `follow_state`; `wl_sync/segment.py:115-127`). It does this before it
   answers (`wl_sync/cli.py:440-448`).
   So once a close has answered 200, nothing more is written to `<id>/syncbox/`, and its last
   segment is closed, with its trailer; a segment cut short by a crash during the visit still has
   none (item 7). A session-end read after that 200 reads closed files, and a live read does not.
   Two limits on where the record ends. The box carries out a close between two of its
   once-a-second iterations, so the session's record runs up to about a second past the request.
   And records still in flight when the segment changes go to whichever segment is open when they
   drain, so an edge from about the last second before that can land in the `unassigned/` segment
   that follows (session-identity design §3.4), and a pulse in progress at the close is cut in two,
   as at a restart (item 7).
7. **A restart splits the record.** It opens the next `seg-NNN` with a new header and a new clock
   epoch. Ticks in different segments cannot be compared as numbers, a pulse that straddles a restart
   is cut in two, and the outage between segments is bounded only by ~~the day's manifest~~ the
   manifest of the folder they are in *(corrected 2026-10-18, later: there is one manifest per
   folder now, not one per day)*.
8. **Edges can be lost without a mark in the file.** If the kernel's edge buffer overflows, the gap
   is detected (libgpiod sequence numbers, `wl_sync/rp1/gpio.py:127`) but not written into the
   segment. Since Step A *(later on 2026-10-18)*, the run loop calls `check_health()` every second,
   and the gap reaches the journal once per episode as `capture degraded, still recording: …`. The
   strobed event codes now get the same treatment, and a full PIO FIFO is reported the same way. **A pulse count taken from the file is exact only for a day whose journal
   shows no dropped-edge error.** Putting that mark in the record itself would change the log format,
   which wl-preproc pins, so it is not decided here.

## 6. XC-092: added, and the 16S2 page raises a question the 200S6 page did not

**The 200S6 row is not meant to cover both.** The BFS-U3-16S2's own page
(`softwareservices.flir.com/BFS-U3-16S2/latest/40-Installation/ElectricalGPIO.htm`, read 2026-10-18)
carries a different table, which splits every level into "No Load" and "Load". Your figures are
right: its 5 V / 1.0 kΩ row gives 0.92 V low with no load and 0.86 V loaded. The BFS-U3-04S2's page
carries the identical table. Both tables are now in `[flir_bfs_gpio]`, verbatim.

**The low end is as you read it.** The 2.50 V threshold leaves 1.58 V of margin against 0.92 V.

**The same row gives a loaded high of 2.29 V, which is below the 2.50 V threshold.** FLIR's page does
not define "Load". This board's input is close to no load, so an unloaded strobe is fine. But a
camera group's strobe is the primary's output, and in FLIR's primary/secondary scheme
(`[flir_bfs_gpio]`'s `sync_topology`) that output also drives each secondary's opto input. If that is
what "Load" means, a group strobe idles at 2.29 V, never crosses the threshold, and produces no
frame-time edges, with nothing else complaining. This is **open, not fixed**. It is recorded in
`hardware/breakout/frame-time-inputs.md` §4 and needs a bench measurement with a group wired as the
rig will be. **Your S0 §7.4 fan-out board copies this front end at 2.50 V, so it inherits the same
question.**

## 7. XC-327: the line-out, and `PD1_COMP` as a start trigger

**Both parts are committed for this board revision, before layout** (decided 2026-10-18; spec §12
item 11). Both of your assumptions were right: no speaker tap exists here until Ask 3's assignment
above, and the misc inputs are input-only. One thing in our own spec was wrong, and it matters for
(b). It is corrected below.

**Sources, read 2026-10-18:** NI X Series User Manual 370784K-01 (May 2019), Figure A-5 "NI
PCIe-6323/6343 Pinout" and §5 "AO Start Trigger Signal"; PCIe-6343 Specifications 377873B-01.

**(a) The line-out has to go on this board; it cannot be outside it.**
- The 6343 puts **AO 0/AO 1 on Connector 0, pins 22/21, with AO GND on 55/54**, and AO 2/AO 3 on
  Connector 1 at the same pins. **Both of the card's connectors are cabled straight into this
  board** (spec §9.2, two `SHC68-68-EPM` per card), so the analog outputs arrive here and nowhere
  else, and today they are no-connect (`hardware/gen/gen_breakout_taskpc_digital.py:1006`). Getting them out
  without this board would mean replacing an NI cable with a custom one.
- **Layout has not started**, so this is a schematic change plus one connector body and one panel
  hole on the back face, which has no spare port. After fab it would be a respin. That is why it is
  being committed now.
- **What the outputs are** (spec sheet): ±10 V, 0.2 Ω output impedance, ±5 mA drive, which is ample
  for a line input. Keeping the amplitude inside the amplifier's input range is the engine's job.
- **The one design question it raises is grounding.** AO GND is the task PC card's analog return,
  and the amplifier is mains-powered. This board's BNCs are isolated (spec §9.1), so a pure
  pass-through that touches no plane here is available. That is settled when the copper is drawn,
  not in this reply.

**(b) `PD1_COMP` reaches the task PC on a P0 line, not a PFI line. A PFI copy is being added.**
- Today it is **P0.27, Connector 1 pin 46** (`hardware/gen/gen_breakout_taskpc_digital.py:1104`). NI's list of
  legal AO Start Trigger sources (manual §5, "Using a Digital Source") names PFI 0–15, RTSI, several
  internal triggers and the **Change Detection Event**, but no P0 line.
- **Change Detection can watch P0.27 and is a listed source**, so it is a route needing no copper.
  But NI give no latency figure for it, and the change-detection section's own list of uses (§6)
  never mentions AO. It is documented and untested. **So a second, buffered copy of `PD1_COMP` goes
  onto a Connector 0 PFI pin** in the same change as (a). P0.27 stays as it is.
- **The correction.** This repository's spec §9.2 put P2.0–P2.7 on Connector 1. Figure A-5 puts
  **all sixteen PFI lines on Connector 0** and none on Connector 1. No wiring moves (every P0 pin here
  was sourced from that figure), and §9.2 now carries the correction. One consequence: the PFI copy
  is a digital line on the analog cable, the one thing §9.2's split was arranged to avoid, and that
  is accepted here for a single low-rate line.
- **Costs.** The task-PC outbound buffer is full (8 of 8 since finding F1), so the copy needs a new
  buffer channel. Which PFI line it uses is chosen with the copper. We will name it in this file when
  it is drawn.

## 8. XC-328: a rig procedure; no third channel needed

**The board fixes what each photodiode channel feeds, not where its sensor sits.** `A_PD1` and `A_PD2`
are identical copper (TIA, comparator, and the NI, Pi and task-PC fan-out). "Task patch" and "flip
patch" are roles (spec §3.2). So V1 can move a sensor.

- **Procedure:** keep `A_PD1` on the bottom patch and move `A_PD2`'s sensor to the top patch for the
  run. The cost is no flip-patch frame clock during V1, which is acceptable for a validation run and
  not for a session. In the stereoscope the top strip is 3.22 cm and dark too, so the sensor fits
  there with the mask in place.
- **Cable:** designed for 6 m, tested 3 to 5 m (spec §6.3.1), and compensated to stay stable to
  about 50 m (finding M1). An extra screen height of lead makes no electrical difference.
- **Measure from the analog copies, or match the thresholds.** A display transition is slow, so a
  comparator edge on it moves with its threshold. If the two channels' thresholds sit at different
  fractions of their own peaks, the top-minus-bottom difference is biased. Either set both thresholds
  to the same fraction of each channel's measured peak, or take a fixed-fraction crossing offline from
  the analog copies (NI, 40 kHz, spec §9.3). Both comparators also reach NI as hardware-timed lines.
- **A third channel does not exist, and a misc BNC cannot be one.** The sensors are passive and need
  this board's TIA (spec §6.3.1), while the misc inputs take a voltage. A self-powered amplified
  photodetector on a misc input would work, but that is new equipment V1 does not need.

## 7, later on 2026-10-18: XC-327 is drawn

**The PFI copy of `PD1_COMP` is on PFI 7** (Connector 0 pin 38). It comes from the sync box's
own level shifter through 100 Ω, and P0.27 carries `PD1_COMP` exactly as before. For your
engine:

- **Route `ao/StartTrigger` from `PFI7`.**
- **Never configure PFI 7 as an output.** This board drives it. PFI 7 was chosen because
  PFI 12–15 are the card's default counter outputs (NI X Series manual Table 7-9), which a
  counter task with default routing would drive against the board. PFI 7 is CTR 3's AUX
  input by default; leave it unrouted to any counter.
- **The levels:** it goes high at 3.3 V against the 6343's V_IH of 2.2 V, and low at about
  0.2 V against its V_IL of 0.8 V.

**The line-out is J57, a dual BNC on the back face:**

- **Its first port** (footprint pads 1–2) carries AO 0, left; **its second** (pads 3–4)
  carries AO 1, right. Each goes through 100 Ω, which costs under 1 % of amplitude into a
  line input of 10 kΩ or more. The panel labels will say which is which.
- **The shells are on AO GND (Connector 0 pins 55 and 54) and nothing else on the board.**
  NI ties AO GND to D GND on the card, so the amplifier's ground meets the card's ground
  regardless. The board keeps that return current off its own planes.
- Keeping the amplitude inside the amplifier's input range is the engine's job, as before.

**One change from the 2026-10-18 proposal:** Connector 0 has no D GND pin, so the PFI edge
returns through Connector 1's D GND. Tying one would have made Connector 0 a second
component on both of this board's grounds, after the star-point net tie, because its
AISENSE is on AGND. That changes nothing for you.

Read against `hardware/breakout/line-out-and-pfi-copy.md`, which cites every figure. The
schematic, netlist contract and BOM carry it, and all ten sheet checkers pass. **XC-327 can
close on your side once you have read it.** What is left is layout, which is this
repository's.

---

# 2026-10-18, later: XC-087 and XC-088 answered

Answers Asks 1 and 2 above. The design is `docs/superpowers/specs/2026-10-18-session-identity-design.md`; it is built
~~on branch `session-identity` of this repository and **not yet merged**~~ and merged to this repository's main
(2026-10-19), and it has not been called from a task PC. Paths
without a repository name are this repository's. Every wl-xcon passage relied on below was read in `/Users/jakewesterberg/GitHub/wl-xcon`
and is cited by file and line; wl-preproc's is cited the same way.

## What we are building to, and why

- **A session is one animal's visit.** wl-xcon's own definition: *"One animal's visit to the rig, cage to cage. One folder, one
  sync-box recording"* (wl-xcon `docs/superpowers/specs/2026-10-01-session-levels-and-strip-design.md:62`). A brief return to the
  cage starts a new one (wl-xcon `docs/superpowers/specs/2026-08-31-S8-session-management-design.md:391-392`).
- **wl-xcon refuses an id it has already used:** *"session id ... is already used under ... every session has its own"*
  (wl-xcon `wl_xcon/service.py:668-671`). So a guessed or retyped id is not harmless.
- **What you asked for:** that the value be *"readable rather than inferred, and that the sync box stay the only thing that
  mints it"* (wl-xcon `docs/pending-wl-sync-amendments.md:55-56`). Ask 2 says the same event is both the subject change and the moment the
  task PC learns the id (Ask 2, above).
- **Both asks share one mechanism, as Ask 2 said.** The task PC tells the box when a visit opens and closes, and the box answers
  with the id.

## The three routes

All bodies and answers are JSON. Every error is JSON `{"error": "..."}`, never HTML.

| Route | Token | Body | 200 answer |
|---|---|---|---|
| `GET /session` | none | none | `{"session_id", "opened_at"}`; both are `null` when no session is open |
| `POST /session/open` | bearer | `{"request_id"}` | `{"session_id", "opened_at", "closed"}`; `closed` is the id this open force-closed, or `null` |
| `POST /session/close` | bearer | `{"session_id"}` | `{"closed"}` |

| Status | Meaning |
|---|---|
| 400 | a bad body or field |
| 401 | a bad or missing token (with `WWW-Authenticate: Bearer`) |
| 403 | the box has no token configured, so it refuses opens and closes |
| 404 | an unknown path |
| 405 | the wrong method for the path (with `Allow`) |
| 409 | a close naming a session that is not the open one (the answer carries `open`, the id that is), or a 100th open in one day |
| 413 | a body over 64 KiB |
| 500 | any other failure |
| 503 | the recording loop did not take the request within its timeout, 5 s by default. The request was cancelled and **was not carried out** |

An open while another session is open closes the old one at that moment and reports it in `closed`, logging it at ERROR on the
box. ~~Recording never stalls at an open or a close,~~ *(Qualified 2026-10-18, later.)* Recording does not stall at an open or a
close, with one residual cost. A segment that a crash left without a trailer is parsed in full, once, the first time the recording
process rebuilds its folder's manifest, and a large one takes seconds. That is normally at the box's start, before it records.
After a restart in the middle of a visit it can instead fall on a roll: a crashed segment in that day's `unassigned/` folder is
then parsed at the visit's close. After every request the recording follows the state file.

## The token, the port, the flags

- **The token** is a shared secret in a file on each machine, sent as `Authorization: Bearer <token>`. It is never committed.
  `deploy/README.md` ("The session endpoint") gives the commands that make it.
- **The port** is 8740. The box runs `wl-sync record --listen HOST:PORT --session-token-file PATH`; the shipped unit uses
  `0.0.0.0:8740` and `/etc/wl-sync/session-token`.
- **A box without a token file** (missing, unreadable or empty) serves `GET /session` only and answers every open or close with
  403. It logs an ERROR and **keeps recording**.
- `GET /session` needs no token, so keep the port on the rig network.

## Retrying

- **Use a new `request_id` for every attempt at a new visit,** and the same one when resending after a lost reply. The box returns
  the session it already opened rather than minting a second and closing the first.
- **503: nothing happened.** The request was cancelled before the recording loop started it. Retry with the same `request_id`.
- **500: the outcome is unknown.** `GET /session` before doing anything else, and act on what it says.
- A 409 on a close means a stale close: the box left the open session alone. Read `open` in the answer.
  *(Added 2026-10-18, later.)* On a close retried after a lost reply, a 409 whose `open` is `null` or names another id
  means the session you meant to close is no longer open: the first attempt closed it, or a later open closed it to make
  room. Nothing needs doing.

## What the task PC must do

This is wl-sync's proposal for what wl-xcon builds. It is design §3.6, **not a decision wl-xcon has made**:

- **At a visit's open:** `POST /session/open`, then use the returned id as the session id. **If the box cannot be reached, the task PC
  refuses to open the visit and says why. It never types an id.** A visit that opened without the box would record under no session.
- **At the visit's end:** `POST /session/close` with that id.
- **On a resume:** `GET /session` and carry on in the session it names.

MonkeyLogic on the dual-boot rig is covered the same way, so that *"either controller can drive the same rig and downstream
consumers cannot tell which ran"* (wl-xcon `docs/design/decisions/ADR-0005-day-one-stack-and-interchangeability.md:29-30`).
`deploy/monkeylogic/wlsync_session.m` is a MATLAB function making the same two calls. It has never run under MATLAB;
`deploy/README.md` has the bench-day checklist.

## On disk

```
<root>/                                  e.g. /data/rig0
  .wl-sync-session                       the open session, if any
  2026-10-18_01/syncbox/seg-000.log ...  one folder per visit
  2026-10-18_02/syncbox/ ...
  unassigned/2026-10-18/syncbox/ ...     between visits
```

- **A session's segments are in `<root>/<id>/syncbox/`.** That is the layout wl-preproc reads (`<session>/syncbox/*.log`), so
  copying a session off the box is a plain copy, and wl-xcon's ~~`expcontroller/`~~ `xcon/` can sit beside it in the same folder.
  *(Corrected 2026-10-18, later: the directory has been `xcon/` since 2026-09-28, as this file's naming note says; wl-xcon
  `wl_xcon/record.py:37`, wl-preproc `wl_preproc/contracts/paths.py:24-42`.)*
- **`unassigned/<date>/syncbox/` is never a session.** Its segment headers carry `<date>_00`, which the box never mints, and it holds no
  `session_manifest.yaml`.
- **An id names the day it opened.** A session open across midnight keeps its id. The first visit of a day is `_01`.
  *(Added 2026-10-18, later.)* That day is the UTC date of the box's barcode value at the open (`wl_sync/cli.py`,
  `_barcode_day`), not the local date, and the two differ near midnight. In a timezone ahead of UTC (UTC+), a session
  opened just after local midnight carries the previous day's date; in one behind UTC (UTC−), a session opened late in
  the local evening carries the next day's.
- **`_99` is the last id a day can have.** A 100th open gets 409 with a message.
- Nothing about the id's form, the log format or the barcode codec has changed.

## What stays unsettled

- **Who writes `session_manifest.yaml` for a MonkeyLogic session.** wl-preproc's answer to XC-098 is that wl-xcon writes it, at the session folder's root, in every
  session it runs (wl-preproc `HANDOVER-wl-expcontroller.md:403-404`). A MonkeyLogic session is not one wl-xcon runs, so nothing
  names its writer; that is wl-sync's observation, not wl-preproc's. The box gives a MonkeyLogic session its id and its `syncbox/`
  folder, and does not write the manifest.
- **The copy to the NAS, and `syncbox/DONE`.** Nothing does either yet, on either side.
- **wl-preproc's pinned wl-sync (`90b931e`) cannot read today's logs.** That is a message for wl-preproc, and independent of this answer.

**XC-087 and XC-088 can close on your side once you have read this and decided the §3.6 proposal.**
