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
  a flickering distractor in one eye's field. The chosen location is a **bottom strip, 2.18 cm ×
  full panel width**, created by stopping the far mirror to ±17° vertical. Worth confirming
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
