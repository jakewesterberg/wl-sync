# What wl-expcontroller needs from wl-sync

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
