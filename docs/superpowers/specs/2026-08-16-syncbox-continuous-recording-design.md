# Sync box: continuous recording, crash stitching, and the outage as data

Design agreed 2026-08-16. Supersedes the implicit session model in
`docs/superpowers/plans/2026-08-13-sync-box.md`, which assumed a session is a bounded run
that starts, records, and writes its log at the end.

---

## 1. The reframing that forced this

The sync box is **the first device turned on each day and the last turned off**. It runs
continuously while every other recording device starts and stops blocks inside that run. A
"session" is therefore **a day**, not a block.

Two requirements follow, both stated by the rig owner:

1. **It must not fail mid-session** — and if it does, the restart must stitch to what came
   before rather than starting a disconnected record.
2. **The outage is itself data.** The other devices keep running while the sync box is down,
   so trials happen during the gap. Those trials really occurred and must be *recognisable as
   unwitnessed*, not silently absent. `wl-preproc` must be able to say "these trials have no
   sync-box coverage" positively, rather than discovering a hole.

Requirement 2 is the sharper one. It means the gap must be **explicit and machine-readable**,
not inferred.

## 2. What the existing code does today

Four findings, all verified against the source rather than assumed. Each is a reason the
current shape cannot meet §1.

**2.1 A crash loses the whole day, not the tail.** `Recorder` accumulates every record in an
in-memory list and `write_log()` writes them all at once at the end. At spec §4's own estimate
of ~2,000 edges/s, an eight-hour day is roughly 58 million records held in RAM with **nothing
on disk until shutdown**. This is not a tuning problem; it is the wrong shape for continuous
operation, and it is the single largest cause of the failure the rig owner fears.

**2.2 The stitching key has no persistence.** `BarcodeGenerator`'s docstring is explicit that
the counter exists for exactly this purpose — *"never reset — across sessions or reboots — so
every barcode the lab ever emits is globally unique and cross-session mis-alignment is
structurally impossible"*. But `next_value` lives only in memory and arrives as a constructor
argument. **Nothing writes it down.** A restart today either repeats values or needs a human to
supply the right one.

**2.3 The sync box does not log the barcodes it emits.** `log.py` defines exactly two record
types, `E` (edge) and `W` (code word). `emit_frame()` returns the value and drops it. So the
box hands every *other* device the alignment key and **keeps no copy** — its own log contains
no barcode↔tick mapping. Survivable while nothing needed to stitch; not survivable now.

**2.4 The tick clock genuinely restarts.** `_TickUnwrapper` accumulates its wrap offset in
memory, and RP1's counter wraps every `2^32` µs ≈ **71.6 minutes** — about 20 times a day. A
restart loses that accumulated offset, so post-restart ticks are **not comparable by number**
to pre-restart ticks. The barcode is the only thing that can re-align them, which is why 2.2
and 2.3 matter rather than being tidiness.

## 3. The record on disk

One directory per **day**; one segment file per **process run**.

```
<out>/2026-08-16_01/
├── manifest.json        what wl-preproc opens first
├── seg-000.log          one per process run
└── seg-001.log          after a restart
```

The directory carries the **session-id form `YYYY-MM-DD_NN`**, not a bare date:
`SyncBoxLogHeader` validates `session_id` against it and `session.py` records that the rig
layout, the ELN and `wl-preproc` all consume it, so the format does not change. A day is one
session, hence `_01`; a restart joins that directory as a new segment rather than minting `_02`.

**Why segments rather than one appended file.** Because of 2.4, ticks after a gap are not
comparable to ticks before it. A single file under a single header would be *lying about its
own timebase*. One header per segment states one clock epoch, which is true. It also means a
torn final line damages one segment rather than sitting in the middle of the day's only file,
and no already-written data is ever rewritten.

**New record type.** `B,tick_us,value` — one per second, ~86 KB/day against a ~1 GB day, i.e.
negligible. It closes 2.3 and makes each segment independently alignable. It also makes a
segment's barcode span recoverable **by reading it**, which is required because a crashed
segment cannot write a trailer.

**Segment header** extends `SyncBoxLogHeader` with: `segment_index`, `ordering` (see §5),
`clock_trusted`, `clock_reason`. `boot_id` and `written_at` already exist.

**Segment trailer**, defined here because the whole crash/clean distinction rests on it: a
single final line `T,<closed_at_iso>,<barcode_first>,<barcode_last>,<record_count>`, written
and `fsync`ed only during a clean shutdown. Its **presence** means clean; its **absence** means
crash. Nothing else is consulted, so the distinction never depends on a dying process managing
to record its own death. A segment whose last line is torn mid-write is likewise trailer-less
and therefore crash-closed, which is correct.

**Segment index** is `max(existing indices in the day directory) + 1`, or `000` if the
directory is new. Derived from the filesystem, so a restart needs no memory of how many runs
preceded it.

**`manifest.json`** is the outage contract. It is **rebuilt by scanning the segments at every
start**, so a crash can never leave it stale — it is derived state, never authoritative state
that a dying process had to manage to update.

```json
{ "day": "2026-08-16",
  "segments": [
    {"file": "seg-000.log", "barcode_first": 1000, "barcode_last": 4200,
     "clock_trusted": true, "clock_reason": "", "closed": "crash"},
    {"file": "seg-001.log", "barcode_first": 4700, "barcode_last": 9000,
     "clock_trusted": false, "clock_reason": "ntp_unsynchronized", "closed": "clean"}],
  "gaps": [ {"after": 4200, "before": 4700, "seconds": 500} ] }
```

`gaps` answers requirement §1.2 directly: trials landing between barcodes 4200 and 4700 are
**real but unwitnessed**.

`clock_reason` sits beside `clock_trusted` here as well as in the segment header, because §7
requires the machine-readable reason in **both** and this file is what an operator opens
first. It is `""` on a trusted segment.

`closed` has **three** values, not two. `"clean"` and `"crash"` are derived from whether the
segment has a trailer — never from the crashed process's cooperation. `"unreadable"` is the
third: the segment could not be parsed at all (permissions, or media corruption surfacing as
a `UnicodeDecodeError`). It is a separate value because "we could not read this" and "this
process died" are different facts and only one says anything about the run. An unreadable
segment reports no barcodes, so the day's gap arithmetic carries the last witnessed value
across it — an outage, which is what it is. One bad file must never cost the box the rest of
the day, since the manifest is rebuilt before every run records anything.

## 4. The barcode counter is a clock

**Epoch-derived.** `value = floor((now_utc − 2020-01-01T00:00:00Z).total_seconds())`. At 32
bits and one per second that is 136 years of unique values, good to ~2156. A restart needs no
memory of the past: it reads the clock.

**Why the counter advances by elapsed time rather than resuming at `last + 1`.** So that the
outage is visible **in the shared key itself**. A device that recorded a barcode on each side
of the gap computes its duration from the values alone — no manifest, no trust in its own
clock. Critically it **fails safe**: a naive consumer assuming "one barcode = one second" gets
the *right* answer across a gap. Contiguous numbering would make 1000 and 1001 five hundred
seconds apart and silently mis-place every trial after the boundary.

The cost, stated plainly: the counter is no longer "how many barcodes we have ever emitted",
it is "seconds since 2020". `BarcodeGenerator`'s docstring needs rewriting and its
`start_value` argument goes away.

**Monotonicity clamp.**

    next = max(value_from_clock, checkpoint + 1)

with `checkpoint` at `<out>/.wl-sync-barcode-checkpoint`. It lives at the **`--out` root, not
inside a day directory**, because it must survive across days; it is the one piece of genuinely
persistent state this design keeps. This guarantees the counter is unique and increasing
**however broken the clock is**. When the clock is healthy `value_from_clock` always dominates
and the clamp never binds.

**The checkpoint is the exact last-emitted value, rewritten every second** — settled
2026-08-17, after two attempts at a staleness margin both failed on the real binary. The run
loop writes `value` immediately after emitting it, at the same 1 Hz cadence the segment
`fsync` already runs at. The clean close writes the same thing. There is no margin, no
interval, and no high-water mark anywhere.

*Why it is written this way rather than periodically.* The property that matters — the
checkpoint is never behind the last value emitted — stops being an **argument** and becomes an
**identity**: `checkpoint == last emitted`, true by construction, independent of how long a
loop iteration takes or where a crash lands. `write_checkpoint` is atomic (temp file plus
rename), and at ~30 bytes/s the cost is ~2.6 MB/day on the NVMe §4.1 chose explicitly. A crash
loses at most one second of staleness, the same bound the segment already accepts.

*The two failed attempts, recorded because they failed the same way.* **A margin the restart
cannot verify becomes a ratchet under `Restart=always`.**

- **`+ checkpoint_interval + 1` at read time.** The clamp then bound on *every* restart inside
  an interval, including clean ones where the exact last barcode was already on disk. Two real
  runs 5 s apart reported a 61 s gap, and ten restarts over ten seconds left the counter 600 s
  ahead of the wall clock.
- **A high-water mark at write time**, `value + checkpoint_interval`, written periodically and
  once at startup. The startup write was added to close a genuine hole — without it the
  invariant was false for a run's first interval — but it made every run leave a mark ahead of
  what it had actually emitted, and the next restart resumed past that mark. Measured: **25
  crash restarts over 75 s of real time on a perfectly healthy clock left the counter 1449 s
  ahead of wall time**, with spurious `clock_trusted: false` from the third restart. One crash
  costs one interval and looks survivable; the compounding only appears under a loop.

Both destroy the same premise — that a barcode *is* seconds since 2020 — and both were
invisible to probes that closed cleanly, because only a genuine crash leaves the margin
behind. Storing the exact value removes the margin rather than relocating it, and **no ratchet
is possible without one**.

*What this costs, and what it buys.* The earlier design claimed an **over-states, never
under-states** property, carried by the margin: a crash could leave the resumed counter up to
`checkpoint_interval` ahead of truth, over-stating a gap, which is conservative. That property
is gone with the margin — and is no longer needed. On a healthy clock the restart resumes at
`value_from_clock`, which *is* truth, so the gap is neither inflated nor deflated. The margin
was only ever compensating for the checkpoint's own staleness, and the checkpoint is no longer
stale.

On a **broken** clock the clamp holds the counter at `checkpoint + 1` and a gap can be
under-stated — but that was true of every version of this design, is what `clock_trusted:
false` exists to mark, and is bounded by §7's tolerance. §4's actual guarantee is unchanged and
is the one the rig owner asked for: *a bad clock degrades the precision of gap length, never
the integrity of the alignment key.*

The clamp is what makes the following true, and it was a question the rig owner raised
directly: **a bad clock degrades the precision of gap length, never the integrity of the
alignment key.** Without it, a backwards clock jump could emit a barcode value already used —
two different moments carrying one identity, which is the one thing the barcode must never do.

## 5. Streaming and durability

**The obstacle.** `Recorder.records()` sorts, because the two capture paths deliver
independently — `_TickUnwrapper`'s docstring notes "a word can arrive after an edge that
precedes it in time". A stream cannot be globally sorted.

**Resolution: declare the ordering rather than fake it.** The header carries
`ordering: "per-path"`. Each path is individually ordered, so a reader does a trivial n-way
merge. A time-based reorder buffer was considered and rejected: it invents a tuning parameter
and still needs a late-arrival escape hatch, in exchange for a property downstream recovers
for free.

**There are THREE paths, not two** — corrected 2026-08-17. `B` records were originally
written straight to the sink with a raw `now_us()` tick, bypassing `Recorder` and therefore
bypassing unwrapping: after the first 71.6-minute wrap every barcode's tick was `k·2³²` µs
adrift of the `E` and `W` ticks it exists to align. Barcodes now go through `Recorder` on
their own path with their own `_TickUnwrapper`, which is safe at 1 Hz because one second is
four orders of magnitude below the wrap period. The tick is sampled **before** the frame is
emitted, since a frame takes 200 ms and `Barcode.start_us` — what every receiving device
reports — is the lead rising edge.

**Structure.** `Recorder(backend, sink=…)` takes a sink with a `write(record)` method. The
default remains the in-memory list, so **every existing test is unchanged**; the run loop
passes a `SegmentWriter`. Semantics stay in `Recorder`; durability lives in the sink.

**`SegmentWriter`** appends each record on arrival and `fsync`s once per second, piggybacked
on the barcode tick. Crash cost ≤ 1 second of records. Memory is bounded — nothing accumulates,
so finding 2.1 disappears rather than being mitigated.

`fsync` at 1 Hz is free here: spec §4.1 puts the load at ~40 KB/s onto an **NVMe SSD**, chosen
explicitly because *"SD cards fail abruptly and on power loss"*.

## 6. Entry point and lifecycle

```
wl-sync record --out /data/rig1 [--fake] [--duration S]
```

- **`--out` is required, with no default.** The day directory roots there. No filesystem
  convention is invented, because the rig layout is consumed by `wl-preproc` and the ELN and
  is defined outside this repo.
- **The day directory is chosen at start and does not roll over.** The box is off overnight; a
  run crossing midnight stays in its starting day rather than splitting mid-run.
- **`SIGTERM`/`SIGINT` → clean close:** stop capture, flush, write the trailer, mark
  `closed: "clean"`, update the manifest.
- **Crash → no trailer.** The next start records `closed: "crash"` while rebuilding the
  manifest from what is actually on disk.
- **`Restart=always` in the systemd unit.** This is what makes stitching *automatic*: the box
  returns, derives its counter from the clock, opens the next segment, and the gap appears in
  the manifest with nobody present.
- **`--fake`** runs the whole path on `FakeBackend`, so this is launchable **before** Task 5b
  exists — and doubles as the harness 5b's bench acceptance steps require.
- **`--duration S`** bounds a run to S **seconds**, which Task 5b step 5 needs for its past-one-wrap test
  (≥ 71.6 minutes).

## 7. Clock trust and degradation

The clock is a **hard dependency** of §4, and the spec never previously named one. The chosen
board supplies it: the **CM5 IO Board has a CR2032 socket** for the RTC, ~5-year life.

**This is an invisible dependency of exactly the species this project keeps getting bitten
by** — the same shape as `force_eeprom_read=0` and the CONFIG4 switch. An uninstalled coin
cell is not visible on any board, in any checker, or in any BOM for our PCB. So the software
must make it loud.

**On an untrustworthy clock — RTC unset, NTP never synced, or time moved backwards — the box
records anyway** and marks the record. It never blocks the rig, because refusing to start
would stop the first device of the day over a $0.30 battery. Degradation is explicit:
`clock_trusted: false` plus a machine-readable `clock_reason` in both the segment header and
the manifest, so `wl-preproc` can distrust gap arithmetic across that boundary specifically
while still using every barcode normally.

*"Records anyway" means what it says, including at startup.* A pre-epoch reading — a dead or
missing CR2032 typically reads 2000-01-01, a Pi with no RTC and no network reads 1970 — is a
**degradation, not an error**. The box resumes from `checkpoint + 1` if a checkpoint exists and
from the epoch otherwise, flags `clock_before_epoch`, and derives its day directory from the
resumed *barcode value* rather than from the bogus reading, so a restart on a dead battery
rejoins the day it was already writing to instead of minting an orphan directory per boot.
Refusing to start is the one response this section rules out, and under `Restart=always` it is
an unbounded crash loop recording nothing.

*"Time moved backwards" carries a tolerance.* A backwards movement smaller than
`CLOCK_TRUST_TOLERANCE_S` (60 s) does **not** set the flag. The checkpoint is the exact
last-emitted value, so any lead it has over the wall clock means the clock has come back to a
reading the box was already past — but small NTP corrections do that on healthy machines, and
flagging them would send an operator to check a coin cell over a working clock. The comparison
is strict (`value_from_clock(now) + tolerance < checkpoint`), so a tolerance of N tolerates a
discrepancy of exactly N.

The price, stated because it is a real weakening of this section: a backwards jump inside the
tolerance goes unmarked, and the clamp will hold the counter still across it, so a gap can be
**under-stated by up to the tolerance without a mark**. Uniqueness is unaffected — the clamp
still guarantees it. This is a deliberate trade against alarm fatigue: a flag that fires on
every ordinary NTP correction, or on every crash restart, is a flag nobody reads on the one
box where it means something.

**There is no panel indicator available for this.** The heartbeat LED is a buffered leg off
`BARCODE_RAW` itself, so it blinks with the barcode waveform; software could only change its
pattern by corrupting the barcode. `journalctl` and `systemctl status` are the honest status
surface today. See §10.

## 8. Testing

Everything in §3–§6 is testable on a laptop with no Pi present, which is the existing
discipline (`FakeBackend`, and Task 5a's split of pure logic from hardware).

- Counter: epoch derivation; the clamp holding under a stalled, a drifting and a
  **backwards-jumping** clock; monotonicity and uniqueness across a simulated restart.
- Segments and manifest: a clean stop producing `closed: "clean"`; a **simulated crash**
  (writer killed without a trailer, including mid-line) producing `closed: "crash"` and a
  correctly bounded gap; manifest rebuilt correctly from segments alone.
- Streaming: records reach disk within one `fsync` window; memory does not grow with run
  length; a per-path merge of a written segment reproduces tick order.
- End to end: `--fake --duration` produces a valid day directory.

## 9. Out of scope

- **`wl-preproc`'s handling** of unwitnessed trials. This design's job is to make the outage
  unambiguous and machine-readable; what preproc does with it belongs to that repo.
- **NAS transfer at session end** — ordinary Linux, no timing content.
- **Timebase fitting** — `wl-preproc`'s, since it needs barcodes from several devices at once.
- **Task 5b, the RP1 backend.** This design is deliberately backend-agnostic and runs on
  `FakeBackend` today.

## 10. Dependencies — all three resolved 2026-08-16

| # | Item | Decision | Owner |
|---|---|---|---|
| 1 | **A CR2032 must be fitted to the CM5 IO Board.** §4 depends on it and nothing on the board, in any checker, or in our BOM can see whether it is there — the same invisible-dependency species as `force_eeprom_read=0` and the CONFIG4 switch | **Record it in two places people actually read:** a new `hardware/assembly-checklist.md` for build steps no schematic can express (fit the coin cell, machine all 34 BNC holes, colour-code the reward group), plus a spares line in the order BOM. The software's `clock_trusted: false` becomes a backstop rather than the only defence | Assembly |
| 2 | **No time source was named** anywhere in spec or plan | **NTP over the existing Ethernet**, which is already required for NAS transfer, so no new hardware. Server address chosen at bring-up. NTP disciplines the RTC so drift never accumulates; the RTC covers power-off and any network outage | Bring-up config |
| 3 | **No software-controllable panel indicator exists** — the heartbeat LED is a buffered leg off `BARCODE_RAW` itself, so it blinks with the barcode and software cannot change its pattern without corrupting the barcode | **Add a populated status LED on GPIO18**, before layout. One LED, one resistor, one spare buffer channel. Carries `clock_trusted: false`, disk filling, and no-event-codes-for-N-minutes — the states that otherwise look identical to a healthy box from across the room | **Hardware, and it blocks layout** |

### 10.1 What decision 3 costs elsewhere

Taking GPIO18 is a real hardware change and must land **before layout**:

- `gen_breakout_pi_interface.py`: GPIO18 moves from `"spare"` to a buffered output driving the
  LED, with its own series resistor and buffer channel — the same treatment the barcode
  heartbeat LED already gets, and out-of-band refdes per the `R198`/`R190` precedent.
- Spare GPIO drops from **2 (18, 19) to 1 (19)** — still PWM-capable. Spec §4's GPIO map,
  `check_breakout_pi_interface_netlist.py`'s `SPARE_GPIOS`, `camera-sync-change.md` and
  `frame-time-inputs.md` all state the current count and will need updating together.
- A **blink vocabulary** has to be defined: what distinguishes healthy from clock-suspect from
  disk-filling, distinguishably at a glance, next to a heartbeat LED already blinking at 1 Hz.
  That belongs in the implementation plan, not here.
