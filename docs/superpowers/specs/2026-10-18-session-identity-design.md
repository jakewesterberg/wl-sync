# Session identity: the box mints one session per visit

**Written 2026-10-18.** Answers wl-xcon's XC-087 and XC-088 (`HANDOVER-wl-expcontroller.md`,
Asks 1 and 2). It supersedes one rule of the continuous-recording design
(`2026-08-16-syncbox-continuous-recording-design.md` §3): *"a day is one session, hence `_01`"*.
The box still records all day without stopping. What changes is that its record is now cut
into one session per animal's visit, and the task PC is told the id instead of a person typing
one.

---

## 1. Why

Three repositories already depend on a rule this repository does not yet implement.

- **wl-xcon: a session is one animal's visit, cage to cage.** *"One animal's visit to the rig,
  cage to cage. One folder, one sync-box recording"* (wl-xcon
  `docs/superpowers/specs/2026-10-01-session-levels-and-strip-design.md:62`). An animal taken
  back to its cage and brought out again starts a new session; that was the PI's ruling,
  2026-09-20 (wl-xcon S8, `…S8-session-management-design.md:386`). wl-xcon refuses to open a
  session id it has already used (`wl_xcon/service.py:668`).
- **wl-preproc: one sync-box recording holds one animal.** *"Each animal gets its own sync-box
  session, as wl-xcon's XC-088 asks wl-sync to make happen"* (wl-preproc
  `docs/superpowers/specs/2026-10-01-runs-and-trials-design.md:43`). It reads every `*.log` in a
  session folder's `syncbox/` as one recording (`wl_preproc/contracts/paths.py`,
  `timebase/extract.py:415`). Its `SessionManifest` carries one `subject`. It quarantines a
  folder whose manifest's `session_id` differs from the folder's name (`ingest/watcher.py:352`).
- **The task PC needs to learn the id, not guess it.** Today an operator types it into the
  console. wl-xcon asked only that the value be *"readable rather than inferred, and that the
  sync box stay the only thing that mints it"* (wl-xcon `docs/pending-wl-sync-amendments.md:55`).

**There is always a session open on the task PC whenever an animal is on the rig** (PI,
2026-10-18), so the task PC's console is where a visit begins and ends.

---

## 2. Goals, non-goals, anti-goals

**Goals.**
- The task PC opens a session when a visit opens. The box mints the next id for the day and
  returns it.
- The task PC closes the session when the visit ends.
- Each session's sync-box record lands in its own folder, laid out as wl-preproc reads it.
- Any tool can read which session, if any, is open.

**Non-goals.**
- Copying sessions to the NAS.
- Reading the reward-delivered record over the network (XC-091, XC-384). The endpoint must allow
  such a route to be added later, without changing the routes below.
- Two rigs minting the same id on the same day. Each box records under its own root
  (`/data/rigN`); whether the NAS shares one root is wl-preproc's question.
- The subject. The box does not learn or record it; it stays in the session manifest wl-xcon
  writes.

**Anti-goals: any of these is a failure, whatever else works.**
- A second source of session ids, or a guessed one.
- A session folder holding two animals' records.
- Recording that stalls, or loses records, at an open or a close.
- The box refusing to record because the task PC is unreachable. The box records regardless; a
  visit simply cannot open without it.
- A change to the session id's form, the log format, or the barcode codec. wl-preproc pins all
  three.

---

## 3. How it works

### 3.1 A session's life

| Event | The box |
|---|---|
| The task PC asks to open (decided: always when a visit opens) | Mints the next id, saves it as the open session, rolls its recording into that session's folder, and answers with the id |
| The task PC asks to close (decided) | Rolls its recording back into the between-sessions area, and clears the open session |
| An open while a session is still open, because a close was missed (decided) | Closes the old session at that moment, logs it at ERROR, and opens the new one. Recording never stalls |
| The box process restarts, or the box reboots | Resumes into whatever session was open, from its saved state. A restart is not a visit (my call) |
| The task PC crashes and resumes (wl-xcon XC-026) | Nothing: the task PC reads the open session and carries on in it |
| A session is open across midnight | It keeps its id. The id names the day it opened (my call) |

**Between visits, the box records into a between-sessions area**, so nothing is lost
(decided). That area must never be mistaken for a session, by name or by content (my call;
§3.3).

### 3.2 Minting

- **The id is the day plus the next unused index**: one more than the highest `_NN` already
  present under the root for that day, counting closed sessions. So an id is never reused, even
  after a restart. The first visit of a day is `_01`.
- **"The day" is decided exactly as the box decides it today** (`cli._resume`). With a trusted
  clock it is the wall-clock date. On a dead clock it is the date implied by the resumed barcode
  value, so a box on a bad battery does not mint ids named after 1970.
- **`_99` is the last id a day can have**, since the form has two digits. A 100th open is
  refused, with a message (my call).

### 3.3 On disk

```
<root>/                                  e.g. /data/rig0
  .wl-sync-barcode-checkpoint            unchanged
  .wl-sync-session                       NEW: the open session, if any (JSON)
  2026-10-18_01/
    syncbox/
      seg-000.log  seg-001.log ...       this visit's segments
      manifest.json                      as today, for this folder's segments
  2026-10-18_02/
    syncbox/ ...
  unassigned/
    2026-10-18/
      syncbox/ seg-000.log ... manifest.json    between visits, and before the first
```

- **A session's segments go in `<root>/<id>/syncbox/`** (my call). That is the layout wl-preproc
  reads, so copying a session off the box is a plain copy, and wl-xcon's `xcon/` and the
  session manifest sit beside it in the same folder on the NAS.
- **`unassigned/` can never be read as a session.** Its name cannot parse as a session id, and it
  never holds a `session_manifest.yaml`, which is how wl-preproc recognises a session folder.
- **`.wl-sync-session`** holds `{"session_id", "opened_at", "request_id"}`, or is absent when no
  session is open. It is written atomically, and fsynced, **before** the recording rolls into
  the new folder. So a crash at any point leaves the box resuming into the session it was about
  to open, never into an unrecorded one.
- **A segment header is unchanged.** `session_id` is the folder's id. Segments in `unassigned/`
  carry the day's `_00`, which `SessionId.parse` accepts, so the header's validation is
  unchanged. `_00` is never minted for a session (my call). The segment index counts within its
  folder, as it does in a day directory today.

### 3.4 Rolling the recording

- **The run loop does the rolling, on its own thread.** The endpoint asks the run loop to open or
  close, and waits for the answer. The capture threads deliver into `QueuedSink` as now. They
  never see a folder change, and are never blocked by one.
- **A roll closes the current segment cleanly, with its trailer, and opens the next segment in
  the new folder**, between two emit-loop iterations. The barcode continues across it without a
  gap: the next frame lands in the new folder. Records queued during the roll go to whichever
  segment is open when they drain. That costs at most one second of edges landing on the
  boundary's far side; wl-preproc fits each segment separately anyway.
- **The box's per-folder `manifest.json`** keeps its present meaning and format, now one per
  folder rather than one per day.

### 3.5 The endpoint

**A small HTTP/JSON service inside the recording process** (decided: a web endpoint), using the
Python standard library, so no new dependency (my call). It listens on the rig network, on a
port set on the command line.

| Route | Who | Does |
|---|---|---|
| `GET /session` | anyone (decided) | `{"session_id": "2026-10-18_02" or null, "opened_at": ISO 8601 or null}` |
| `POST /session/open` | token holder (decided) | Body `{"request_id": "<client-chosen, unique per attempt>"}`. Answers `{"session_id", "opened_at", "closed": <the id it closed, or null>}` |
| `POST /session/close` | token holder | Body `{"session_id": "<the one to close>"}`. Answers `{"closed": "<id>"}` |

- **The token** is a shared secret in a file on each machine, sent as `Authorization: Bearer
  <token>` (decided). Without a token file the box serves `GET` only and refuses opens and
  closes.
- **`request_id` makes an open safe to retry.** If the reply to an open is lost and the task PC
  asks again with the same `request_id`, the box returns the session it already opened rather
  than minting another and closing the first (my call). The last open's `request_id` is kept in
  `.wl-sync-session` for exactly this.
- **A close names the session it closes.** A close for a session that is not the open one is
  refused with HTTP 409, and the answer says which session is open. A stale close can never shut
  the next visit's session.
- **Errors.** A bad token gets 401. If the run loop cannot take the request within a few
  seconds, the answer is 503, so the task PC can say why. A 100th open in a day gets 409.

### 3.6 What the task PC does under wl-xcon (wl-xcon's side, for its own design)

- **At the visit's open:** `POST /session/open`, then use the returned id. If the box cannot be
  reached, refuse to open and say why (decided). Never type an id instead.
- **At the visit's end:** `POST /session/close` with that id.
- **On a resume:** `GET /session`.

### 3.7 MonkeyLogic, on the dual-boot rig

wl-xcon's ADR-0005 keeps MonkeyLogic as a dual-boot fallback, on the condition that *"either
controller can drive the same rig and downstream consumers cannot tell which ran"* (wl-xcon
`docs/design/decisions/ADR-0005-day-one-stack-and-interchangeability.md`, decisions 2–3).
wl-xcon's XC-319 expects real MonkeyLogic sessions with animals. Without this section, a
MonkeyLogic visit would record entirely into `unassigned/` and never become a session.

**MonkeyLogic makes the same two calls from MATLAB** (decided): `POST /session/open` when its
session starts and `POST /session/close` when it ends, using MATLAB's `webwrite`, with the token
sent as a header. This repository ships the recipe: a short MATLAB function and how to hook it
into MonkeyLogic's start and stop. The box still mints every id; nothing new is installed on
the Windows boot.

**So the endpoint must stay callable from plain HTTP clients** (MATLAB's `webwrite`, `curl`,
PowerShell's `Invoke-RestMethod`). JSON bodies, a bearer token in the `Authorization` header,
and no client library. A `request_id` can be anything unique per attempt, such as a UUID from
`java.util.UUID.randomUUID` in MATLAB.

---

## 4. What does not change

- `SessionId`'s form, `YYYY-MM-DD_NN`.
- The log format and `SyncBoxLogHeader`'s fields.
- The barcode codec and its one-per-second cadence. Barcode values remain seconds since 2020,
  and are unique across the whole box, not per session.
- The box records from power-on to power-off, under `Restart=always`.

---

## 5. Constraints on the build

- **This adds a published interface: the endpoint.** `wl.yaml`'s `publishes` gains it, and
  CLAUDE.md says to check what depends on this package before changing what it publishes.
  wl-xcon will be its first consumer.
- **The continuous-recording design's "a day is one session" rule must be amended** in the same
  change, with a pointer here, not silently contradicted.
- **The run loop and the capture threads keep their present guarantees**: one owner of each
  segment, no capture thread ever blocked on disk, and a one-second crash-loss bound.

---

## 6. Left to the builder

- The port number and its command-line flag name, and the token file's flag and default path.
- The exact time a roll waits for, before giving up with a 503.
- The journal lines' wording. Every open, close and forced close is logged.
- How tests drive the endpoint. Laptop tests against `FakeBackend`, as the run loop's are, are
  expected.
- `wl_sync.manifest.build_manifest` takes the folder name as the manifest's `day`. In the new
  layout that folder is `syncbox/`, so the key must come from the session (or `unassigned/`
  day) folder above it.
- The order of steps at a close, and cancelling a roll that has timed out with a 503.

## 7. Outside this design, recorded so they are not lost

- **Copying sessions to the NAS, and writing `syncbox/DONE`.** Nothing does this yet, on either
  side.
- **Who writes `session_manifest.yaml` for a MonkeyLogic session.** ~~wl-preproc records it as
  *"not yet settled"*.~~ **Corrected 2026-10-18:** wl-preproc's answer to XC-098 is that wl-xcon writes it,
  at the session folder's root, in every session it runs (wl-preproc `HANDOVER-wl-expcontroller.md:403-404`).
  "Not yet settled" there refers to sessions with no task PC. A MonkeyLogic session is not one wl-xcon
  runs, so nothing names its writer. The box gives a MonkeyLogic session its id and its sync-box folder; it does
  not write the manifest.
- **wl-preproc's pinned wl-sync (`90b931e`) cannot read today's segment logs.** Its header model
  forbids the fields added since August, and its reader raises on `B` and `T` records (checked
  in this repository's history, 2026-10-18). This is a message for wl-preproc, independent of
  this design.
- **Two rigs sharing a NAS root** would both have `_01` on the same day.
