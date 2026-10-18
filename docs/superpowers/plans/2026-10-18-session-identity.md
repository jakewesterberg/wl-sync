# Session Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The sync box mints one session per animal's visit, `_01`, `_02`, and so on. It does so
when the task PC asks over a small HTTP endpoint, and records each visit into its own
`<root>/<id>/syncbox/` folder.

**Architecture:** Several pieces, each in its own place:
- **The decisions** (which id to mint, the saved open session, the folder paths) live in a new,
  laptop-testable `wl_sync/sessions.py`.
- **The run loop** (`wl_sync/cli.py`) drains session requests between emit iterations, and rolls
  the recording into the right folder through `QueuedSink.roll()`. Rolling happens on the sink's
  own owner thread, so capture threads never see it.
- **The endpoint** is a stdlib HTTP server (`wl_sync/endpoint.py`). It reads the saved session
  for `GET`, and hands `POST`s to the run loop through a queue.
- **MonkeyLogic** gets a MATLAB recipe.

**Tech Stack:** Python ≥3.11, standard library only (`http.server`, `json`, `hmac`,
`concurrent.futures`), pydantic (already a dependency), and pytest with `FakeBackend`.

**Spec:** `docs/superpowers/specs/2026-10-18-session-identity-design.md`. Read it first. Section
numbers below are the spec's.

## Global Constraints

- No new runtime dependency (spec §3.5, "using the Python standard library").
- `SessionId`'s form `YYYY-MM-DD_NN`, the log format, `SyncBoxLogHeader`'s fields and the barcode
  codec do not change (spec §2 anti-goals, §4).
- **Paths:**
  - a session's segments: `<root>/<id>/syncbox/`;
  - between visits: `<root>/unassigned/<YYYY-MM-DD>/syncbox/`;
  - the open session: `<root>/.wl-sync-session`, JSON `{"session_id", "opened_at",
    "request_id"}` (spec §3.3).
- **Ids:**
  - `_00` is never minted; it is the header id of `unassigned/` segments;
  - `_99` is the last id of a day, and a 100th open is refused (spec §3.2–3.3);
  - "the day" is the UTC date implied by the barcode value the loop is emitting. With a healthy
    clock that is the wall-clock date (spec §3.2).
- **State is saved before the recording rolls**, at both an open and a close (spec §3.3).
- **Capture threads never block on disk.** The segment keeps a single owner (`QueuedSink`), and
  the crash-loss bound stays one second (spec §5).
- **HTTP** (spec §3.5):
  - JSON bodies, and `Authorization: Bearer <token>`;
  - `GET /session` open to anyone;
  - `POST /session/open` and `POST /session/close` only with the token;
  - without a token file, the box serves `GET` only.
- **Tests:**
  - run the suite as CI does, with bare `pytest`;
  - every new test must be shown failing first, and a test that only closes a coverage gap says
    so in its docstring (`docs/next-session.md` §5);
  - laptop tests use `FakeBackend`.

## Review Focus

1. **A corrupt or unreadable `.wl-sync-session`** must not crash-loop the box under
   `Restart=always`. It reads as "no session open", logged at ERROR, as a corrupt checkpoint
   already degrades (`clock.read_checkpoint`). *Test: Task 1,
   `test_a_corrupt_state_file_reads_as_no_session_open`.*
2. **A retried open after a 503.** The first request may still have been processed. The retry,
   with the same `request_id`, must get the same session back, not a new one that closes the
   first. *Tests: Task 1, `test_reopening_with_the_same_request_id_returns_the_same_session`;
   Task 7, end-to-end.*
3. **A box restart mid-session** must resume into the session's own folder, with the next segment
   index there, and never into `unassigned/`. *Test: Task 4,
   `test_a_restart_resumes_into_the_open_session`.*
4. **An open on a dead clock** must name the session by the barcode-implied date, not 1970 or
   2000. *Test: Task 5, `test_an_open_on_a_dead_clock_is_named_by_the_barcode_day`.*
5. **A stale close**, for a session that is no longer the open one, must change nothing and say
   which session is open. *Tests: Task 1, `test_a_close_for_another_session_is_refused`; Task 6,
   the 409 answer.*

---

### Task 1: `wl_sync/sessions.py` — ids, folders, the saved open session

**Files:**
- Create: `wl_sync/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces:
  - `SESSION_STATE_NAME = ".wl-sync-session"`
  - `session_dir(root: Path, session_id: SessionId) -> Path`, which is `root/str(id)/"syncbox"`
  - `unassigned_dir(root: Path, day: datetime.date) -> Path`, which is
    `root/"unassigned"/day.isoformat()/"syncbox"`
  - `unassigned_id(day: datetime.date) -> SessionId`, which is `SessionId(day, 0)`
  - `@dataclass(frozen=True, slots=True) class OpenSession: session_id: SessionId; opened_at:
    datetime.datetime; request_id: str`
  - `read_open(root: Path) -> OpenSession | None`
  - `write_open(root: Path, session: OpenSession) -> None`: atomic, as `clock.write_checkpoint`
    is, so write a temporary file, fsync it, `replace`, and fsync the directory
  - `clear_open(root: Path) -> None`
  - `next_id(root: Path, day: datetime.date) -> SessionId`
  - `class SessionError(Exception)`
  - `class SessionsExhausted(SessionError)`
  - `class WrongSession(SessionError)` with attribute `open: SessionId | None`
  - `@dataclass(frozen=True, slots=True) class Opened: session: OpenSession; closed: SessionId |
    None; reused: bool`
  - `open_session(root: Path, request_id: str, now: datetime.datetime, day: datetime.date) ->
    Opened`
  - `close_session(root: Path, session_id: SessionId) -> SessionId`
  - `@dataclass class SessionRequest: kind: Literal["open", "close"]; argument: str; reply:
    concurrent.futures.Future`. For an open, `argument` is the `request_id`; for a close, the
    session id string. It lives here, not in `cli.py`, because both `cli.py` (Task 5) and
    `endpoint.py` (Task 6) use it, and `cli.py` imports `endpoint.py` (Task 7).

- [ ] **Step 1: Write the failing tests** in `tests/test_sessions.py`:
  - `test_session_and_unassigned_folders_are_laid_out_as_wl_preproc_reads_them`:
    `session_dir(root, SessionId.parse("2026-10-18_02")) == root/"2026-10-18_02"/"syncbox"`, and
    `unassigned_dir(root, date(2026,10,18)) == root/"unassigned"/"2026-10-18"/"syncbox"`.
  - `test_the_first_id_of_a_day_is_01`, on an empty root.
  - `test_ids_count_every_folder_already_used_that_day`: with `2026-10-18_01/` and
    `2026-10-18_03/` present, and also `2026-10-17_07/`, `next_id` returns `_04`. Other days do
    not count.
  - `test_the_unassigned_folder_never_counts_as_a_session`: `unassigned/2026-10-18/` alone means
    `next_id` returns `_01`.
  - `test_the_100th_open_of_a_day_is_refused`: with `_99` present, `next_id` raises
    `SessionsExhausted`.
  - `test_opening_creates_the_folder_before_saving_state`: after `open_session`, both
    `root/<id>/` and the state file exist, and `read_open(root).session_id` is the new id.
  - `test_reopening_with_the_same_request_id_returns_the_same_session`: open twice with
    `request_id="r1"`. The second call gives `reused is True`, the same `session_id`, and
    `closed is None`. No `_02` folder exists.
  - `test_an_open_while_another_is_open_reports_the_one_it_closed`: open `"r1"`, then open
    `"r2"`. The second gives `closed == <first id>`, and `read_open` is the second session.
  - `test_a_close_for_another_session_is_refused`: open `_01`, then close `_02`. It raises
    `WrongSession` with `.open == SessionId.parse("..._01")`, and `read_open` is unchanged.
  - `test_a_close_with_nothing_open_is_refused`: `WrongSession` with `.open is None`.
  - `test_closing_clears_the_saved_session`: `read_open(root) is None` afterwards.
  - `test_a_corrupt_state_file_reads_as_no_session_open`: write `"{not json"` to the state file.
    `read_open` returns `None` and logs at ERROR, checked with `caplog` on logger
    `wl_sync.sessions`.

- [ ] **Step 2: Run them, and confirm they fail**

  Run: `pytest tests/test_sessions.py -q`. Expected: all fail with
  `ModuleNotFoundError: No module named 'wl_sync.sessions'`.

- [ ] **Step 3: Implement `wl_sync/sessions.py`.** `next_id` scans `root.glob(f"{day}_??")`
  through `SessionId.parse`, ignoring names that do not parse. It takes 1 + the highest index,
  or 1 when there is none. `open_session`:
  - returns `Opened(current, None, True)` when `current.request_id == request_id`;
  - otherwise mints, makes `root/<id>/` (so the id is spent even if the box dies before writing
    a segment), then calls `write_open`, and returns `closed=current.session_id if current else
    None`.

  `opened_at` is serialised as ISO 8601 with its offset. Follow `clock.py`'s module-docstring
  style: say why, and name the failure each choice prevents.

- [ ] **Step 4: Run them, and confirm they pass**

  Run: `pytest tests/test_sessions.py -q`, then the full `pytest`. Expected: all pass.

- [ ] **Step 5: Commit**

  `git add wl_sync/sessions.py tests/test_sessions.py && git commit -m "sessions: mint one id per visit, and save the open session"`

---

### Task 2: `QueuedSink.roll()` — change segments on the sink's owner thread

**Files:**
- Modify: `wl_sync/service.py` (`QueuedSink`, and a `_RollRequest` beside `_CloseRequest`)
- Test: `tests/test_queued_sink.py`

**Interfaces:**
- Produces: `QueuedSink.roll(make_sink: Callable[[], RecordSink], *close_args) -> None`. It
  blocks until the owner thread has closed the current sink with `close(*close_args)` and
  replaced it with `make_sink()`. `QueuedSink.sink` then names the new sink. A failure in either
  half is held and re-raised, as `close()` and `flush()` already do.

- [ ] **Step 1: Write the failing tests** in `tests/test_queued_sink.py`:
  - `test_a_roll_splits_records_at_the_moment_it_was_asked`: write records 1–3, `roll(...)`, then
    write 4–6. The first sink holds 1–3 and was closed with the given args; the second holds 4–6.
    `queued.sink is second`.
  - `test_no_record_is_lost_across_a_roll_under_concurrent_writers`: use `_hammer` from the same
    file, with WRITERS × PER_WRITER records, and roll once mid-way from the main thread. The
    count across both sinks is WRITERS × PER_WRITER exactly, with no duplicates.
  - `test_a_failing_new_sink_does_not_die_quietly`: `make_sink` raises `OSError`. `roll` raises
    `RuntimeError` chained from it, and so does the next `write`.

- [ ] **Step 2: Run them, and confirm they fail.** Run:
  `pytest tests/test_queued_sink.py -q -k roll`. Expected: `AttributeError: 'QueuedSink' object
  has no attribute 'roll'`.

- [ ] **Step 3: Implement.** `_RollRequest(_FlushBarrier)` carries `(make_sink, close_args)`.
  `_drain` handles it like `_CloseRequest`: close the current sink, assign
  `self.sink = make_sink()`, then `done.set()` in `finally`. Write a docstring on why rolling
  belongs on the owner thread: the capture threads keep writing into the same `QueuedSink` and
  never see a segment change.

- [ ] **Step 4: Run `pytest`.** Expected: all pass.

- [ ] **Step 5: Commit**

  `git commit -am "QueuedSink.roll: change segments on the owner thread"`

---

### Task 3: The box's manifest names its session, not `syncbox`

**Files:**
- Modify: `wl_sync/manifest.py:237` (the `"day"` value in `build_manifest`)
- Test: `tests/test_manifest.py`

**Interfaces:**
- Produces: `build_manifest(directory)` reports `"day"` as `directory.parent.name` when
  `directory.name == "syncbox"`, and as `directory.name` otherwise. The key stays `"day"`,
  because the manifest's format does not change (spec §3.4).

- [ ] **Step 1: Write the failing test** `test_a_session_folders_manifest_names_the_session`:
  `build_manifest(root/"2026-10-18_02"/"syncbox")["day"] == "2026-10-18_02"`, and
  `build_manifest(root/"unassigned"/"2026-10-18"/"syncbox")["day"] == "2026-10-18"`.
- [ ] **Step 2: Run it.** `pytest tests/test_manifest.py -q -k names_the_session`. Expected: it
  fails, with `'syncbox' != ...`.
- [ ] **Step 3: Implement it**, as one expression with a comment giving the reason.
- [ ] **Step 4: Run `pytest`.** Expected: all pass.
- [ ] **Step 5: Commit**

  `git commit -am "manifest: name the session folder, not syncbox/"`

---

### Task 4: The run loop records into the open session, or between sessions

**Files:**
- Modify: `wl_sync/cli.py`:
  - `run()`'s set-up, which today does `day = day_directory(...)` through to `writer =
    SegmentWriter(...)`;
  - its end, which reads `writer.barcode_last`;
  - delete `day_directory`.
- Modify: `tests/test_cli.py`. Replace the two `day_directory` tests, and update every test that
  names `<date>_01` (33 references; `grep -n "day_directory\|_01\"" tests/test_cli.py`).

**Interfaces:**
- Consumes: Task 1's `read_open`, `session_dir`, `unassigned_dir`, `unassigned_id`; Task 2's
  `QueuedSink.roll`.
- Produces: `run(...)` returns the `syncbox/` directory it last recorded into. Every segment's
  header `session_id` is its folder's id: the open session's, or `unassigned_id(day)`. The journal
  start line says which folder it is recording into.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`:
  - `test_with_no_session_open_the_box_records_between_sessions`: `run(...)` returns
    `tmp_path/"unassigned"/"2026-08-16"/"syncbox"`. The segment header's `session_id` is
    `"2026-08-16_00"`. No `2026-08-16_01/` folder exists.
  - `test_a_restart_resumes_into_the_open_session`: first `sessions.write_open(tmp_path,
    OpenSession(SessionId.parse("2026-08-16_02"), <aware dt>, "r1"))`, with
    `tmp_path/"2026-08-16_02"/"syncbox"/seg-000.log` already present from a "previous run" (write
    it with `SegmentWriter`). `run(...)` records into `seg-001.log` there, and its header says
    `"2026-08-16_02"`.
  - Then update the existing tests to the new layout, keeping their intent. Each test's own
    docstring says what it protects; that claim must still hold. For example, the dead-clock test
    asserts `day == tmp_path/"unassigned"/"2020-01-01"/"syncbox"`, and that no `2000-01-01`
    folder exists anywhere.

- [ ] **Step 2: Run them.** `pytest tests/test_cli.py -q`. Expected: the two new tests fail, and
  so does every updated assertion.

- [ ] **Step 3: Implement.** At start, `open = sessions.read_open(out)`. The folder is
  `session_dir(out, open.session_id)` if a session is open, else `unassigned_dir(out, day_date)`.
  `mkdir(parents=True)` it, `write_manifest` it, and take the segment index from it. Keep
  `day_date` from `_resume` exactly. Read `barcode_last` from `sink.sink`, because a roll (Task 5)
  replaces the writer. Delete `day_directory`, and point its two tests at `sessions`.

- [ ] **Step 4: Run `pytest`.** Expected: all pass.

- [ ] **Step 5: Commit**

  `git commit -am "run: record into the open session's folder, or between sessions"`

---

### Task 5: The run loop opens and closes sessions on request

**Files:**
- Modify: `wl_sync/cli.py` (`run()`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1's `open_session`, `close_session`, `SessionRequest` and the exceptions;
  Task 2's `roll`.
- Produces:
  - `run(..., requests: queue.SimpleQueue[SessionRequest] | None = None)`.
  - An open's future resolves to `sessions.Opened`; a close's resolves to the closed
    `SessionId`. `SessionError`s are set as the future's exception.

- [ ] **Step 1: Write the failing tests.** They put requests on a queue from inside `tick_fn`, so
  each is handled at a known iteration:
  - `test_an_open_rolls_the_recording_into_the_new_session`: open at iteration 2. The future gives
    `2026-08-16_01`. Its `syncbox/seg-000.log` holds that frame and every later one. The
    `unassigned` segment has a trailer, and holds frames 1–2 only.
  - `test_a_close_rolls_the_recording_back_between_sessions`: open, then close. The session's
    segment has a trailer, and later frames land in a new `unassigned` segment.
  - `test_an_open_while_open_closes_the_old_session_and_says_so`: two opens. The second's
    `closed` is the first's id. There is an ERROR line naming both.
  - `test_an_open_on_a_dead_clock_is_named_by_the_barcode_day`: a 2000-01-01 clock with no
    checkpoint opens `2020-01-01_01`.
  - `test_session_errors_reach_the_caller_and_recording_continues`: a close of a session that is
    not open sets `WrongSession` on the future, and every frame is still recorded.
  - `test_a_cancelled_request_is_never_carried_out`: `future.cancel()` before it is drained means
    no session is opened.

- [ ] **Step 2: Run them.** Expected: `TypeError: run() got an unexpected keyword argument
  'requests'`, then assertion failures once the parameter is added.

- [ ] **Step 3: Implement.** After `health()` and before pacing, drain `requests` without
  blocking. Skip any whose `reply.set_running_or_notify_cancel()` returns False. "The day" is
  `(BARCODE_EPOCH + timedelta(seconds=value)).date()`, for the value just emitted.
  - **Open:** `open_session` first, then, unless `reused`, `sink.roll(make_segment(new_dir,
    new_id), now_fn())`, then `write_manifest(old_dir)`.
  - **Close:** `close_session` first, then roll into `unassigned_dir(out, day)`, then the
    manifest.

  `make_segment` builds the header exactly as the start of `run()` does, with that folder's id
  and next index. Log every open, close and forced close.

- [ ] **Step 4: Run `pytest`.** Expected: all pass.

- [ ] **Step 5: Commit**

  `git commit -am "run: open and close sessions between emit iterations"`

---

### Task 6: `wl_sync/endpoint.py` — the HTTP endpoint

**Files:**
- Create: `wl_sync/endpoint.py`
- Test: `tests/test_endpoint.py`

**Interfaces:**
- Consumes: Task 1's `read_open`, `SessionRequest`, `Opened` and the exceptions.
- Produces: `serve(root: Path, requests: queue.SimpleQueue, token: str | None, host: str, port:
  int, timeout_s: float = 5.0) -> http.server.ThreadingHTTPServer`, already serving on a daemon
  thread. `server.server_address` gives the bound port; the tests use port 0.

| Request | Answer |
|---|---|
| `GET /session` | 200 `{"session_id": str or null, "opened_at": str or null}` |
| `POST /session/open {"request_id": str}` | 200 `{"session_id", "opened_at", "closed": str or null}` |
| `POST /session/close {"session_id": str}` | 200 `{"closed": str}` |
| no token configured, any POST | 403 |
| wrong or missing bearer token | 401 (compared with `hmac.compare_digest`) |
| bad JSON, or a missing field | 400 |
| `WrongSession` | 409 `{"error": str, "open": str or null}` |
| `SessionsExhausted` | 409 `{"error": str}` |
| run loop does not answer within `timeout_s` | 503, after `reply.cancel()` |
| any other path | 404 |

- [ ] **Step 1: Write the failing tests**, one per row, with `urllib.request` against a
  `serve(...)` on port 0. Each POST test answers its request from a helper thread that reads the
  queue and resolves the future, standing in for the run loop. The 503 test leaves it unanswered,
  sets `timeout_s=0.2`, and asserts the future was cancelled.
- [ ] **Step 2: Run them.** `pytest tests/test_endpoint.py -q`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement.** Use a `BaseHTTPRequestHandler` subclass, and route log lines through
  `logging`, not stderr. `GET` reads `read_open(root)` directly. A 503 cancels first; if the
  cancel fails because the loop has already started, wait for the result instead. Leave room for
  a later route that serves the reward-delivered record (spec §2): routes are a table, not a
  chain of `if`s.
- [ ] **Step 4: Run `pytest`.** Expected: all pass.
- [ ] **Step 5: Commit**

  `git add wl_sync/endpoint.py tests/test_endpoint.py && git commit -m "endpoint: open, close and read sessions over HTTP"`

---

### Task 7: `wl-sync record` serves the endpoint; the unit and the deploy notes

**Files:**
- Modify: `wl_sync/cli.py` (`main()`: flags, starting the endpoint, passing `requests` to `run`)
- Modify: `deploy/wl-sync.service`, `deploy/README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 5's `run(requests=)`; Task 6's `serve`.
- Produces:
  - `--listen HOST:PORT`, default none, meaning no endpoint;
  - `--session-token-file PATH`, default none, meaning the box serves `GET` only. The file holds
    one line, and is stripped of whitespace.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_task_pc_can_open_and_close_a_session_end_to_end`: run `run()` in a thread, with a
    real `serve(...)` on port 0 and a 30-iteration duration. Its `tick_fn` sleeps 0.05 s so the
    endpoint can interleave. POST open with the token, GET (expect the id), POST close, then
    join. The session's `syncbox/` holds a segment with a trailer.
  - `test_a_retried_open_after_a_lost_reply_gets_the_same_session`: the same, opening twice with
    one `request_id`. The second answer's `session_id` equals the first's, and `closed` is null.
  - Update `test_the_shipped_unit_starts_a_mode_the_cli_actually_accepts` to replace `--listen`'s
    value with `127.0.0.1:0`, as it already replaces `--out`'s.
- [ ] **Step 2: Run them.** Expected: they fail, because `main` has no `--listen`.
- [ ] **Step 3: Implement.**
  - **`main()`:** parse the flags, read the token file, `serve(...)`, then `run(...,
    requests=queue)`. Shut the server down when `run` returns.
  - **The unit:** add `--listen 0.0.0.0:8740 --session-token-file /etc/wl-sync/session-token`.
    The port is this plan's call.
  - **`deploy/README.md`:** a section on the endpoint covering the token file (`install -m 0600`,
    root-owned), the port, and the three `curl` commands that open, read and close a session.
- [ ] **Step 4: Run `pytest`.** Expected: all pass.
- [ ] **Step 5: Commit**

  `git commit -am "record: serve the session endpoint; unit and deploy notes"`

---

### Task 8: The MonkeyLogic recipe

**Files:**
- Create: `deploy/monkeylogic/wlsync_session.m`
- Modify: `deploy/README.md` (a "MonkeyLogic on the dual-boot rig" section)

**Interfaces:**
- Consumes: Task 6's routes, exactly.
- Produces: the MATLAB function `id = wlsync_session(action, box, token_file)`. `action` is
  `'open'`, `'close'` or `'show'`; `box` is like `'wl-sync-rig0:8740'`.

- [ ] **Step 1: Write the function** with `webwrite` and `webread`. The options are
  `weboptions('MediaType','application/json','HeaderFields',{'Authorization',['Bearer '
  token]},'Timeout',10)`. `request_id` is `char(java.util.UUID.randomUUID)`. `'close'` first
  reads the open id with `'show'`, then closes it. It errors with the box's own message on any
  non-200, so MonkeyLogic's start fails loudly rather than recording into `unassigned/` (spec
  §3.7).
- [ ] **Step 2: Document it** in the README: where to call it in MonkeyLogic's session start and
  stop, and that the id it returns names MonkeyLogic's data folder for wl-preproc. **This cannot
  be tested in this repository, and the README says so.** List it among bench-day checks, to be
  run once on the Windows boot against the real box.
- [ ] **Step 3: Commit**

  `git add deploy/monkeylogic deploy/README.md && git commit -m "deploy: the MonkeyLogic session recipe"`

---

### Task 9: Contracts and records

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-syncbox-continuous-recording-design.md` (§3)
- Modify: `wl.yaml` (`publishes`, and `status`)
- Modify: `HANDOVER-wl-expcontroller.md` (answer Asks 1 and 2: XC-087, XC-088)
- Modify: `docs/next-session.md`

- [ ] **Step 1: Amend the continuous-recording design's §3**, dated. "A day is one session" is
  superseded by the session-identity design, with a pointer to it; the original stays
  struck through, not deleted, per this repository's record convention.
- [ ] **Step 2: Add the endpoint to `wl.yaml`'s `publishes`:**

  ```yaml
  - name: session-endpoint
    kind: http-api
    at: wl_sync/endpoint.py
    stability: experimental
    what: >-
      GET /session reads the open session; POST /session/open and /session/close
      (bearer token) open and close one per animal's visit. The task PC and
      MonkeyLogic call it; the box is the only thing that mints a session id.
  ```

  Update `status`. Then run `wl-check` (CLAUDE.md, pinned `wl-manifest` as CI pins it).
  Expected: exit 0.
- [ ] **Step 3: Answer Asks 1 and 2 in the handover**, as a dated section. Give the three routes,
  the rule that the task PC must refuse to open without the box, the `request_id` retry rule, and
  the folder layout. State what wl-xcon must build (spec §3.6) and what stays unsettled: the
  manifest writer for MonkeyLogic sessions, and the NAS copy.
- [ ] **Step 4: Update `next-session.md`**, then run `pytest` and `wl-check`. Expected: both pass.
- [ ] **Step 5: Commit**

  `git commit -am "Session identity: amend the recording design, publish the endpoint, answer XC-087/088"`
