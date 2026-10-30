# Amendments to wl-preproc

**Delivered 2026-10-19** as the dated section "2026-10-19: from wl-sync" of a new
`HANDOVER-wl-sync.md` in wl-preproc, pushed to its `main` as `1b1771d` and `a85c5c5`. The
same two commits also sit, by accident, on wl-preproc's `plan/ni-clock` as `be8e95d` and
`a12b12b`: another session switched that checkout to its branch between this repository's read
and its commit. They are identical in content.
Answers are read there. The format follows the convention wl-xcon uses for both repositories
(wl-xcon `docs/pending-wl-preproc-amendments.md`).

These are written here rather than applied there. The claims about wl-preproc were read at
wl-preproc `6fcad33` and re-read at `02fd6c0` before delivery. The claims about wl-sync were read
at wl-sync `2a95b8f`. *(2026-10-30: everything added since, including the answers below and notes
4 and 5, was checked against wl-sync `ab06425` and wl-preproc `main` at `4bd10f1`.)*

The 2026-10-19 delivery held two asks and one note. wl-preproc answered both asks on 2026-10-22, in
its `HANDOVER-wl-sync.md`, "wl-preproc's answers, 2026-10-22":

1. ~~Move the pin.~~ **Answered** on 2026-10-22, and done (its `e8f1350`).
2. ~~May wl-sync tighten `SessionId.parse`?~~ **Answered yes**, and done.
3. A note, not an ask: what the session-identity change means for what wl-preproc reads.

*Added 2026-10-30, not yet delivered:*

- **wl-sync's answers to wl-preproc's 2026-10-22 items 3, 4, 5 and 6** (below).
- **Note 4:** two header fields, and a third moment a segment starts.
- **Note 5:** what a session's last records do now.

---

# ANSWERED — 1. The pinned wl-sync cannot read today's logs

**Answered by wl-preproc on 2026-10-22, and done.** Its `e8f1350` (committed 2026-10-27) moved the
pin to wl-sync `27c0e97`, and its barcodes are now the box's `B` records (its HANDOVER-wl-sync.md,
answer 1). Between `27c0e97` and what the box runs today (`4f1739c`), `wl_sync/log.py` and
`wl_sync/segment.py` changed only in docstrings, so that pin reads what the deployed box writes.
What follows is the 2026-10-19 text, kept as the record.

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

**Moving the pin is not enough on its own** *(added 2026-10-19, before the delivery was pushed;
found by the wl-phantom design's builder check)*. wl-preproc's `timebase/_syncbox_log.py:49-63`
finds the box's barcodes by decoding `Edge` records on `gpio_map["barcode_out"]`, which is the
shape its `synth/syncbox.py:36-41` writes. The real box never captures that line:

- GPIO17 is its own output, and its edge inputs are GPIO20–27 (`wl_sync/pins.py:40`).
- It records each barcode it emits as a `B` record instead (`wl_sync/log.py:17-19`).

So a real box log yields no barcodes through that function. The ask, in full: read the box's own
barcodes from its `B` records, and have `synth/syncbox.py` write them the same way.

**wl-sync's own fault, recorded so it is not repeated:** `SCHEMA_VERSION` is still `1`. The
format changed without a version bump, so nothing in a log tells an old reader why it fails. The
new fields all have defaults, so a current reader still reads August's logs. The change is
backward-compatible for reading, but not forward-compatible.

## What moving the pin brings besides readability

- **`B` records** give the tick at which the box emitted each barcode, on the box's own
  timebase. wl-preproc may find them useful beside decoding the barcode from edges. Whether to
  use them is wl-preproc's call.
- **`clock_trusted` / `clock_reason`** on every segment. ~~One known fault on wl-sync's side, found
  on the box on 2026-10-19 and not yet fixed: after a power-up, every segment that day is
  flagged `ntp_unsynchronized`, even once NTP has synced. Trust is judged once, when the process
  starts, and that races NTP at boot. Until this is fixed, do not treat that flag on its own as
  a fault in the data.~~
  - **Fixed 2026-10-30 in `2b420c0`** ("cli, clock: judge clock trust again at every roll"). It
    reaches wl-preproc once its pin moves past that commit.
  - Each segment is now judged when it begins. A segment begun by a session open or close asks
    NTP again. It is also untrusted if the barcode value the box is emitting is more than 60 s off
    the wall clock, under a new reason, **`barcode_off_clock`**.
  - A segment begun before NTP synced still says `ntp_unsynchronized`, which was true when it
    began.
  - `clock_reason` is still a free string, so nothing wl-preproc reads changes shape.
  - This update has not been delivered to wl-preproc. It is repeated in the answers below.

---

# ANSWERED — 2. A question: may wl-sync tighten `SessionId.parse`?

**Answered yes** by wl-preproc on 2026-10-22, in its HANDOVER-wl-sync.md, item 2: nothing
there relies on the lenient form. **Done 2026-10-30**, in the wl-sync commit "session:
SessionId.parse takes only the canonical form": `re.fullmatch` with `[0-9]`. The box's
`/session/close` still forgives one trailing newline, now explicitly in the endpoint.

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
  orders them (it counts from 0 within that folder). *(2026-10-30: and once at NTP's first sync,
  in the same folder. See note 4.)*
- **Between visits the box records into `<root>/unassigned/<YYYY-MM-DD>/syncbox/`,** with the
  header id `<date>_00`, which is never minted for a session. That folder never holds a
  `session_manifest.yaml`. wl-preproc's watcher takes a folder as a session only once its
  manifest is there (wl-preproc `HANDOVER-wl-expcontroller.md:404-405`), so `unassigned/` is
  never ingested.
- **An id names the UTC day it opened, and keeps that id across midnight.**
- **The box writes its own `manifest.json` in each folder.** wl-preproc does not read it, and
  does not need to.

---

# ANSWERS — wl-preproc's 2026-10-22 items 3, 4, 5 and 6 (2026-10-30, not yet delivered)

- **Item 3, "Please confirm that two segments in one session folder never share a clock: that a
  roll at an open or a close always changes folders."**
  - The second half holds: a roll at an open or a close always changes folders.
  - The first half no longer does. Two segments in one folder can share a clock in two cases:
    - when the box rolls once in place at NTP's first sync (note 4);
    - when a failed read of the box's session state rolls the recording out to `unassigned/` and
      back in (rare).
  - Every header now names its process (`process_uuid`, note 4). The same id means one clock and
    no restart; a different id, or `null`, means a restart and a new clock.
  - "One clock" there is one origin and one wrap count, not one rate. Code words drift from
    barcodes and edges (note 4).
- **Item 4, the two asks: both are built.**
  - The measured barcode delay is `barcode_delay_us`, in `73f4395` (note 4).
  - Records captured before a folder change now land in the closing segment, in `4043737`
    (note 5).
- **Item 5:** noted. Clock trust has since changed: it is judged again at every roll (`2b420c0`;
  answer 1 above has the detail). That commit runs on the box since 2026-10-30.
- **Item 6, unwrap against the box's one clock: fixed in `d309bf1`, and running on the box since
  2026-10-30.**
  - One box clock now unwraps every path. It places each raw tick at the value nearest the box's
    `now_us()`, which the 1 Hz barcode loop keeps current.
  - A path silent for 71.6 min or more, or whose first record comes that late, now lands
    correctly.
  - Runs that never missed a wrap give identical values.
  - Your pin gets it once it moves past `d309bf1`; every commit in notes 4 and 5 is later.
  - No real session carries the old fault: the box's unit still runs `--fake` until bench day.

---

# NOTE — 4. Two header fields, and a third moment a segment starts (2026-10-30, not an ask)

**Not yet delivered.** It is for wl-preproc before its pin moves past the second of these two
wl-sync commits:

- `73f4395`, "log, cli: every header names its process and the measured barcode delay";
- `56a89f8`, "cli: roll once in place when NTP first syncs".

The order was agreed with wl-preproc on 2026-10-30.

- **A new segment also starts once at NTP's first sync, in the same folder.** After a power-up
  the box starts before NTP has synced. It asks again every 10 s, backing off to every 300 s
  after asks that fail. The first time NTP says it has synced, the box closes that segment and
  starts the next one in the folder it is already in, one loop iteration later. If a session
  open or close rolls in that iteration, that roll asks NTP itself, and there is no roll in
  place. That is the third moment a segment starts, beside an open or close and a restart.
- **`process_uuid` tells that apart from a restart.** Every header now carries it.
  - It is one UUID4, made when the recording process starts.
  - It is the same in every segment that process writes, and new after every restart.
  - Segments with the same id are on one clock: their ticks compare by number.
  - **"One clock" is one origin and one wrap count, not one rate.**
    - `B` and `E` ticks count the box's CLOCK_MONOTONIC.
    - `W` ticks count RP1's own crystal, joined to that clock once, when capture starts, which
      is once per process.
    - So code words drift from barcodes and edges by RP1's crystal error times the time since
      the process started: about 0.18 s an hour at 50 ppm.
    - The real rate is unmeasured; bench day measures it.
    - wl-preproc places code words by barcodes, so this reaches its data.
    - The fix is open: wl-sync `docs/superpowers/specs/2026-10-30-one-clock-design.md` is a
      draft, to be decided after bench day. No real session is affected yet; the unit runs
      `--fake`.
  - A different id, or `null`, is a restart and a new clock. `null` means the segment was
    written before the field existed.
- **The field names are `process_uuid` and `barcode_delay_us`.** Both default to `null`, so old
  logs still read. `schema_version` stays 1. A reader pinned before `73f4395` rejects both
  fields, because the header forbids unknown ones.
- **Ids are canonical, so compare them as text.** The header refuses any other spelling of a
  UUID: upper case, no hyphens, braces, or a `urn:` prefix.
- **Readers add `barcode_delay_us` to `B` ticks** to get the time the frame's lead edge reached
  the wire.
  - It is in µs and always ≥ 0, because the tick is read before the frame goes out.
  - `null` means unmeasured: use each tick as it is.
  - The value is the bench loopback's median, measured through the box's own edge input.
- **The first `B` of each process is an exception.** On the box, its frame also waits for the
  output line to be requested, after its tick is read. So its lead edge is later by an amount
  nothing measures, and the loopback never includes that frame.
- **Two segments with the same id need not be adjacent in time.** The id promises a shared
  clock, not adjacency.
  - In `unassigned/`, a visit normally lies between two of them.
  - In a session folder this happens rarely: a failed read of the box's session state during a
    request rolls the recording out to `unassigned/` and back in.
  - To know whether two segments are adjacent, check the `B` values across the boundary. After
    a roll in place, the second segment's first value normally follows the first's last by one.
    It can follow by TWO when NTP's first sync stepped the clock by a fraction of a second: the
    box then places its frame grid mid-second again (wl-sync `bbc994a`), and that frame may
    skip one value. The gap is real elapsed time, not a loss. So treat +1 or +2 across a roll
    in place as adjacent.

The definitions are the field docstrings in wl-sync's `wl_sync/log.py`.

# NOTE — 5. Ask 4b is built: what a session's last records do now (2026-10-30, not an ask)

**Not yet delivered.** It is in wl-sync `4043737`, "cli, service, rp1: a roll waits for what was
captured before its boundary", which comes after the two commits in note 4.

- **Records captured before an open or close now land in the closing segment.** At each roll,
  the box takes a boundary tick, then waits until every capture path has delivered everything
  it captured up to that tick, and only then rolls. Session-identity spec §3.4, amended
  2026-10-30, has the rule.
- **The wait is short and bounded.** It is normally under about 65 ms, and it gives up at
  0.2 s. A path whose capture thread has died is not waited for. On a timeout, the box logs a
  WARNING naming the path, and its late records land in the next segment, as before.
- **Two things still cross the boundary, both small:**
  - records captured AFTER the boundary but delivered during the wait land in the closing
    segment, at most about 65 ms of them normally;
  - a code word's tick comes from the RP1 crystal, so a word in the closing segment can carry
    a tick after the boundary by that crystal's drift since the process started (note 4).
    That is about 0.18 s an hour at 50 ppm, and the real rate is unmeasured.
- **One assumption is unmeasured until bench day:** the kernel buffers an edge within 20 ms of
  stamping it. A runtime check warns if it ever looks otherwise.

