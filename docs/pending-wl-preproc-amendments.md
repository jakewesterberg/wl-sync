# Amendments to wl-preproc

**Delivered 2026-10-19** as the dated section "2026-10-19: from wl-sync" of a new
`HANDOVER-wl-sync.md` in wl-preproc (`be8e95d`, committed there locally; not yet pushed).
Answers are read there. The format follows the convention wl-xcon uses for both repositories
(wl-xcon `docs/pending-wl-preproc-amendments.md`).

These are written here rather than applied there. The claims about wl-preproc were read at
wl-preproc `6fcad33` and re-read at `02fd6c0` before delivery. The claims about wl-sync were read
at wl-sync `2a95b8f`.

There are two asks and one note:

1. Move the pin, because the pinned wl-sync cannot read the logs the box writes today.
2. A question: may wl-sync tighten `SessionId.parse`?
3. A note, not an ask: what the session-identity change means for what wl-preproc reads.

---

# OPEN — 1. The pinned wl-sync cannot read today's logs

## The ask

Move `wl-sync @ git+…@90b931e8d4714a0b199510d5ccb8813114bb076e` (wl-preproc
`pyproject.toml:29`) to a current wl-sync commit, `2a95b8f` or later. Then regenerate the
`SyncBoxLogHeader` export that `wlpp schemas export` writes into `docs/schemas`. The header
gained fields, so wl-preproc's CI diff of that directory will change, as intended.

## Why

wl-preproc reads segments with wl-sync's own reader:

- `wl_preproc/timebase/_syncbox_log.py:20` imports `read_log`;
- `wl_preproc/events/extract.py:27` imports `CodeWord` and `read_log`.

So the pin decides what wl-preproc can read. At `90b931e` (2026-08-13), a log the box
writes today fails in two places:

- **The header.** `SyncBoxLogHeader` has `extra="forbid"` (pinned `wl_sync/log.py:36`). Today's
  header carries four fields that model does not have: `segment_index`, `ordering`,
  `clock_trusted` and `clock_reason` (today `wl_sync/log.py:58-72`, added in `f4f5ea8`,
  2026-08-17). Validation therefore fails before any record is read.
- **The records.** The pinned reader knows `E` and `W`, and raises `unknown log record type` on
  anything else (pinned `wl_sync/log.py:114`). Today the box also writes:
  - `B`: each barcode the box emitted, with its tick (`7b9d63a`);
  - `T`: the segment's trailer. Today's `read_log` skips it (`e0be66c`).

Every one of these changes landed after the pin, between 2026-08-17 and 2026-08-22 (`git log
90b931e..main -- wl_sync/log.py` in wl-sync).

**wl-sync's own fault, recorded so it is not repeated:** `SCHEMA_VERSION` is still `1`. The
format changed without a version bump, so nothing in a log tells an old reader why it fails. The
new fields all have defaults, so a current reader still reads August's logs. The change is
backward-compatible for reading, but not forward-compatible.

## What moving the pin brings besides readability

- **`B` records** give the tick at which the box emitted each barcode, on the box's own
  timebase. wl-preproc may find them useful beside decoding the barcode from edges. Whether to
  use them is wl-preproc's call.
- **`clock_trusted` / `clock_reason`** on every segment. One known fault on wl-sync's side, found
  on the box on 2026-10-19 and not yet fixed: after a power-up, every segment that day is
  flagged `ntp_unsynchronized`, even once NTP has synced. Trust is judged once, when the process
  starts, and that races NTP at boot. Until this is fixed, do not treat that flag on its own as
  a fault in the data.

---

# OPEN — 2. A question: may wl-sync tighten `SessionId.parse`?

## The question

`SessionId.parse` accepts text that is not a canonical id. Its pattern is
`^(\d{4})-(\d{2})-(\d{2})_(\d{2})$`, used with `re.match` (wl-sync `wl_sync/session.py:13`,
`:23`), and this lets through two kinds of text:

- **A trailing newline.** `$` matches just before a final `\n`, so `"2026-10-18_01\n"` parses.
- **Non-ASCII digits.** `\d` matches any Unicode decimal digit, and `int()` converts them, so an
  id written in, say, Arabic-Indic digits parses, then prints back as ASCII.

In both cases `str(SessionId.parse(text)) != text`.

wl-sync proposes `re.fullmatch` with `[0-9]`. Every id the box mints already matches that, so
no real id changes. But wl-preproc takes `SessionId` from wl-sync (`contracts/paths.py:12`,
`contracts/manifest.py:10`), so its parsing would tighten too. That is why wl-sync asks before
changing a form wl-preproc pins.

Meanwhile, the box's endpoint already canonicalises an id before using it.

**The ask:** agree to the tightening, or say why wl-preproc relies on the lenient form.

---

# NOTE — what session identity changes in what wl-preproc reads (not an ask)

Session identity landed on wl-sync `main` on 2026-10-19. The design is in wl-sync
`docs/superpowers/specs/2026-10-18-session-identity-design.md`. For wl-preproc:

- **A session's segments are in `<root>/<id>/syncbox/`.** That is the layout wl-preproc already
  reads: every `*.log` in a session folder's `syncbox/` is one recording (`timebase/extract.py`,
  `_RECORDING_GLOBS`). Nothing to change.
- **A session folder can hold several segments.** The box closes one segment and starts the next
  at every open and close, and at every restart. Inside a folder, the header's `segment_index`
  orders them (it counts from 0 within that folder).
- **Between visits the box records into `<root>/unassigned/<YYYY-MM-DD>/syncbox/`,** with the
  header id `<date>_00`, which is never minted for a session. That folder never holds a
  `session_manifest.yaml`. wl-preproc's watcher takes a folder as a session only once its
  manifest is there (wl-preproc `HANDOVER-wl-expcontroller.md:404-405`), so `unassigned/` is
  never ingested.
- **An id names the UTC day it opened, and keeps that id across midnight.**
- **The box writes its own `manifest.json` in each folder.** wl-preproc does not read it, and
  does not need to.
