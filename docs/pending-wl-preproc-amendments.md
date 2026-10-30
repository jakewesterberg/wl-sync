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
4 and 5, was checked against wl-sync `ab06425` and wl-preproc `main` at `4bd10f1`; note 6 against
the wl-sync commit that adds it.)*

The 2026-10-19 delivery held two asks and one note. wl-preproc answered both asks on 2026-10-22, in
its `HANDOVER-wl-sync.md`, "wl-preproc's answers, 2026-10-22":

1. ~~Move the pin.~~ **Answered** on 2026-10-22, and done (its `e8f1350`).
2. ~~May wl-sync tighten `SessionId.parse`?~~ **Answered yes**, and done.
3. A note, not an ask: what the session-identity change means for what wl-preproc reads.

*Added 2026-10-30, not yet delivered:*

- **wl-sync's answers to wl-preproc's 2026-10-22 items 3, 4, 5 and 6** (below).
- **Note 4:** two header fields, and a third moment a segment starts.
- **Note 5:** what a session's last records do now.
- **Note 6:** an `L` record marks lost capture events in the segment.

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

# NOTE — 4. Three header fields, and a third moment a segment starts (2026-10-30, not an ask)

**Not yet delivered.** It is for wl-preproc before its pin moves past these wl-sync commits:

- `73f4395`, "log, cli: every header names its process and the measured barcode delay";
- `56a89f8`, "cli: roll once in place when NTP first syncs";
- `c67599f`, "log, cli: every header names the clocks its ticks count" (added later the same day,
  the user's decision, so that one pin move covers it too).

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
- **The field names are `process_uuid`, `barcode_delay_us` and `timebase`.** All three default to
  `null`, so old logs still read. `schema_version` stays 1. A reader pinned before them rejects
  them, because the header forbids unknown ones.
- **`timebase` names the clocks the segment's ticks count.** It is free text, never empty.
  - `"split"`: the RP1 backend. Code words count RP1's crystal, barcodes and edges
    CLOCK_MONOTONIC, and they drift apart, as above.
  - `"fake"`: the test backend, which the box's unit runs until bench acceptance.
  - The one-clock build will write a new value. It is free text precisely so that your reader,
    pinned today, still reads those segments. Treat a value you do not know as "relative rates
    unknown".
  - `null`: written before the field existed.
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
- **The wait is short and bounded.** It is normally under about 65 ms, about 40 ms more while
  an edge loss is pending (note 6), and it gives up at 0.2 s. A path whose capture thread has died is not waited for. On a timeout, the box logs a
  WARNING naming the path, and its late records land in the next segment, as before.
- **Two things still cross the boundary, both small:**
  - records captured AFTER the boundary but delivered during the wait land in the closing
    segment, at most about 65 ms of them normally;
  - a code word's tick comes from the RP1 crystal, so a word in the closing segment can carry
    a tick after the boundary by that crystal's drift since the process started (note 4).
    That is about 0.18 s an hour at 50 ppm, and the real rate is unmeasured.
- **One assumption is unmeasured until bench day:** the kernel buffers an edge within 20 ms of
  stamping it. A runtime check warns if it ever looks otherwise.

# NOTE — 6. An `L` record marks lost capture events in the segment, and how `E` is ordered (2026-10-30)

**Not yet delivered.** It is in the wl-sync commit "log, rp1, service: an L record marks lost
capture events in the segment", which comes after the commits in notes 4 and 5. wl-sync asks
that it ship in the same pin move as the header fields, so that wl-preproc's pin moves once,
past both. wl-preproc's session agreed to that in a message on 2026-10-30, before its user had
been asked; this is the request in writing.
A reader pinned before it raises `unknown log record type` on the first `L` line, so nothing is
deployed until the pin has moved, the same hold as the header fields. **The user approved the
format on 2026-10-30**, with every choice below that was marked provisional.

## How `E` is ordered: decided

The header's `ordering: "per-path"` promises each path's records in that path's tick order. That
holds for `W` and `B`. For `E` it holds only on each line:
- Across lines, `E` records are in the kernel buffer's order. Two lines' IRQ threads are not
  ordered, so an edge can be written up to `EDGE_STAMP_LAG_US` behind one written before it.
- That is 20 ms, the allowance the box's roll barrier assumes. Bench day has not measured it.
- GPIO26 and GPIO27, the cameras' frame-time lines, fire together on every frame, so inversions of
  microseconds are routine.

This predates the `L` record. Your readers filter records by type, so nothing of yours is wrong
today. **The user decided on 2026-10-30: `E` is ordered per line.** The `ordering` field's
definition and the spec now say so. Its value stays `"per-path"`, so this changes only text, and
the header is as you will pin it. A reader that needs `E` in tick order across lines sorts it by
tick. A reorder buffer before delivery was weighed and not chosen: it would delay every edge by
20–40 ms, and an edge later than the allowance would still land out of order.

## The record

The box has always found its own losses and named them in the journal. Now each one is also a
record, in the segment open when the box finds it:

```
L,tick_us,path,gpio,count,bound,cause,before_us,after_us
L,40400,E,20,2,exact,buffer_overflow,40100,40400
L,8500,W,,3,at_least,desync,8500,
```

- **`path`** is `W` or `E`, the capture path that lost the events. **`gpio`** is the line, for an
  `E` loss whose line is known.
- **`count`** is at least 1: code words on `W`, edges on `E`. **`bound`** is `exact` or
  `at_least`.
- **`before_us` / `after_us`** are the window: the ticks of the last record of the path known to
  precede the loss and the first known to follow it. Either may be empty, meaning that side is
  open. An empty field is never 0.
- **`tick_us` is an ordering key, not the time of the loss.** The window gives that.
- **Every number is ASCII digits only:** no sign, no separator.

**Each cause has one shape** (strict, as a reader is easier to loosen than to
tighten). The reader refuses anything else, and the box cannot write it:

| cause | path | `gpio` | `bound` | `before_us` | `after_us` | `tick_us` |
|---|---|---|---|---|---|---|
| `buffer_overflow` | E | the line | `exact` | given | given | `= after_us` |
| `coalesced` | E | the line | `at_least` | given | given | `= after_us` |
| `unaccounted` | E | empty | `at_least` | optional | given | `>= after_us` |
| `desync` | W | empty | `at_least` | optional | empty | `= before_us`, if given |
| `fifo_full` | W | empty | `at_least` | optional | optional | reserved |

For every cause, `count >= 1`, every tick and `gpio` are `>= 0`, and `before_us <= after_us` when
both are given.

**What each cause means:**

| cause | count | window | where it sits |
|---|---|---|---|
| `buffer_overflow` | the numbers missing from the line's own count: exact | the line's event before the gap, to the event that showed it | just before that event, at its tick |
| `coalesced` | 1: the least a pair at one level can mean | the pair | just before the pair's second event, at its tick |
| `unaccounted` | the request numbers missing that nothing explains | the events numbered just below and just above the gap | the highest `E` tick written when its window closed, 20 to 40 ms after the read that showed it |
| `desync` | the strobes in the refused batch | the last code word recorded, to empty: the word pump stops, so every later strobe in that process is lost too, uncounted | the last code word's tick. Before any code word, the box clock's tick when the record is made, with the window empty at both ends |
| `fifo_full` | — | — | **reserved**: not written by this version, because a full FIFO may have dropped nothing. It is in the format so that your reader already accepts it once a box can write it, from the PIO's overflow flag; its tick and window will be defined then |

**The window has two exceptions,** each by microseconds, both from the kernel's IRQ-thread race
(kept rather than widened):
- `coalesced`, at a line's first event or its first after an overflow: the lost edge can lie
  before `before_us`, by the time the race took.
- `unaccounted`: the window rests on the request's numbers following the edges' stamps. An edge
  stamped from the IRQ thread can break that. The window is then `[after_us, after_us]`, and the
  loss can lie just outside it.

**Order.** An `L` record is in the stream of the path it reports on, and keeps the order that path
has. `W` is in tick order, and so is `W`'s `L`. `E` is in tick order on each line (above):
- an `L` record with a GPIO is written just before the event that showed it, at that event's tick.
  It keeps its line's order, and across lines it sits exactly where that event does.
- an `unaccounted` record, with no GPIO, sits at the highest `E` tick written before it. Nothing
  written before it is ahead of it, and it makes no `E` record written after it later than that
  record already is.

`read_log` returns them as `Loss` records, so a reader that filters records by type is unaffected.

**Which segment:**
- One written just before an event lands with that event. They are two writes, though: a roll
  queued between them puts the record last in the closing segment and the event first in the
  next, with `after_us` in that next file.
- An `unaccounted` record whose window ends at or before a roll's boundary lands in the closing
  segment: the roll waits for it, as for any record, up to 0.2 s. A pending loss adds about 40 ms
  to that wait (34–48 ms in simulation; not a bound). If the wait gives up, the record lands in the
  next segment.
- A loss is found only when something shows it, so its record can be in a later segment than the
  loss. An overflow on a line that stays quiet across a roll is one such case: its `before_us` is
  then in an earlier segment, on the same clock (same `process_uuid`).

**Counts do not add across records.** One lost edge can be named twice. An overflow on a line that
does not fire again within the window is first an `unaccounted` record, then a `buffer_overflow`
one when the line fires. Each record is true alone.

**No record where the count cannot be bounded below by 1:**
- a FIFO found full, which may have dropped nothing;
- a gap still open when capture stops, or when the edge capture thread dies, whose event may
  have been a moment from arriving;
- the second of two pairs in a row on one line, which in the IRQ-thread race is the same damaged
  edge as the first. A genuine second loss in a run of pairs then has no record.

The journal still reports each of them. A capture thread that stops loses everything after it,
uncounted: its journal line says so, and it has no record. `unaccounted` rests on the 20 ms
allowance above. An edge buffered later than that is written as lost, as the journal reports it.

**The trailer's count includes `L` records.** `schema_version` stays 1.

The definitions are the `Loss` docstring in wl-sync's `wl_sync/log.py`.
