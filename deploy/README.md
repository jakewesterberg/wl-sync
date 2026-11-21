# Deploying the sync box

    sudo cp deploy/wl-sync.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now wl-sync

Edit `--out` in the unit to the rig's session root before enabling. Its last component
becomes `rig` in every log header, so `/data/rig0` for a box called `wl-rig-sync0`.
The folder names the rig box: rig0 is the first, and the next box writes `/data/rig1`.

A new box needs its OS first: see [Setting up the OS](#setting-up-the-os-once-per-box).

**The unit ships in fake mode on purpose, until bench acceptance.** `ExecStart`
carries `--fake`. Without it `wl-sync record` runs the RP1 backend, which is wired into
the run loop (2026-10-18) but has never run on silicon. If the backend cannot be built
(no libpiolib, a missing chip, a chip too small to be RP1's bank), the unit restarts every
second and records nothing. A failure later, while starting PIO capture, still leaves one
empty segment per restart. Everything except the hardware capture is
real in fake mode, so crash stitching, the manifest and clock degradation can all be
verified at bring-up.

On bench day, run the hardware mode by hand first, with the GPIO chip confirmed by
label (`gpioinfo`):

    wl-sync record --out /data/rig0 --gpiochip /dev/gpiochipN

Once Task 5b Steps 3–5 pass, drop `--fake` from `ExecStart` and add that `--gpiochip`. *[2026-11-21, the user's ruling on the bench-day runbook's C3, "Both": that waits for Task 5b Steps 3–5 and for the ±100 µs gate (`docs/superpowers/plans/2026-11-07-one-clock.md:2106`; `docs/next-session.md:186`). The hand run above writes `/data/rig0`, which the `--fake` service writes too: stop the service first (`sudo systemctl stop wl-sync`), or give the hand run another `--out`, such as `/tmp/wl-bench/rig0`. The day's order is `docs/bench-day.md`.]*
*(Added 2026-10-30; amended 2026-11-19 for the one-clock build.)* `--barcode-delay-us N`
is for a process that records `"split"` alone (below). Such a process writes the flag into
every header (`wl_sync/rp1/backend.py:802`; `wl_sync/cli.py:555-563`, `:572`), and N is
the loopback's median "lead edge after its logged tick" (`docs/next-session.md`, Step B),
in whole µs, read from a segment whose header says `"split"`. On one count the backend
stamps the lead edge itself and every header says `barcode_delay_us` 0: a flag given is
not used, with a WARNING (`wl_sync/rp1/backend.py:442`; `wl_sync/cli.py:555-563`). There
the loopback's median is "E's constant (B6)" (`wl_sync/bench.py:103`), the edge input's
own offset and not a barcode delay. Do not pass it as `--barcode-delay-us`: a later
process that falls back to `"split"` would write it into every header. With no split
measurement, leave the flag out, and a split process's headers say `null`, unmeasured.

## Checking it

    systemctl status wl-sync          # is it running
    journalctl -u wl-sync -f          # what is it saying
    ls /data/rig0/unassigned/$(date -u +%F)/syncbox/    # between visits (UTC date)
    ls /data/rig0/*/syncbox/                         # one folder per visit
    cat /data/rig0/unassigned/$(date -u +%F)/syncbox/manifest.json

Segments land in `/data/rig0/unassigned/<date>/syncbox/` while no animal is in the rig,
and in `/data/rig0/<id>/syncbox/` during a visit, where `<id>` is the session id the
task PC was given (for example `2026-10-18_01`). Each folder has its own manifest.

The manifest is the thing to read. `gaps` lists spans where this box was **not
watching** — trials in those spans really happened and were recorded by the other
devices, but carry no sync-box coverage.

**In `unassigned/`, a gap also spans every visit.** A visit is recorded in its own
`<id>/syncbox/`, so the between-sessions folder skips from the last barcode before the
open to the first one after the close. A gap there is an outage only if no session
folder covers it: check the `<id>/syncbox/manifest.json` of each visit that day.

**A 2 s gap between two segments with the same `process_uuid`** (added 2026-10-30) is almost
certainly not an outage either; the journal's "NTP synchronized" roll line at that moment
confirms it. After NTP's first sync the box rolls once (in place, or between sessions into
the new day's `unassigned/` folder if the sync changed the day), and if that sync
stepped the clock by a fraction of a second, the frame grid is placed again and one barcode
value may be skipped. The box was watching throughout.

`overlaps` should be **empty**, and anything in it is an alarm rather than a note.
It means the barcode counter went backwards or repeated, so one barcode value names
two different moments and a trial can be attributed to the wrong one. Ordinary
outages, restarts and bad clocks all show up in `gaps` or `clock_trusted`, never
here. If it is ever non-empty, say so before the day's data is aligned.

`clock_trusted: false` on a segment means gap arithmetic across that boundary is
unreliable. The manifest carries `clock_reason` next to it, which says which:

| `clock_reason` | what it means |
|---|---|
| `clock_before_epoch` | the clock reads before 2020 — a dead or missing CR2032 on the CM5 IO Board, or a Pi with no RTC and no network. The box records anyway, resuming from the checkpoint; see `hardware/assembly-checklist.md` |
| `clock_behind_checkpoint` | the clock is more than 60 s behind where this box had already counted to. It moved backwards or stalled by more than an ordinary NTP correction. Judged when the process starts, so it appears only on a run's first segment; the run's later segments say `barcode_off_clock` while its values stay ahead |
| `checkpoint_exhausted` | the saved counter sits at the 32-bit ceiling — corruption, or the year 2156. Seen only alongside `clock_before_epoch`; the box restarts from the epoch rather than refusing to start |
| `ntp_unsynchronized` | `timedatectl` reported NTP not synchronised when the segment began. Each segment is judged when it begins. *Since 2026-10-30,* a run that began unsynchronised asks again every 10 s, and the first time NTP says it has synced, the box closes that segment after the next frame and begins the next in the same folder (journal: `NTP synchronized`). After a power-up the flag therefore stops within about 10 s of the sync, where it used to last until a session opened or closed. That ask gives up after 0.3 s, which fits in the ~0.75 s after a frame with ~0.45 s to spare, so a hung `timedatectl` should not cost a barcode. Each ask that fails doubles the wait before the next, up to 5 min. If `timedatectl` cannot be asked, a new segment takes the run's last answer, so a failed probe never makes a segment look better |
| `barcode_off_clock` | only on a segment begun by a roll, that is a session open or close, or the roll when NTP first syncs: the barcode value the box was emitting was more than 60 s off the wall clock, in either direction. Usually the clock was stepped, by NTP, while the box was recording: after a step back the values stay ahead for the rest of the run, however right NTP has made the clock, and a step forward just before the roll makes that boundary claim time that never passed. Otherwise the run began with its values clamped ahead of the clock, and its first segment then says `clock_behind_checkpoint`; or the clock reads past 2156, which no value can name. Not, on its own, a coin-cell problem |
| `unreadable_header` / `unreadable_segment` | the segment itself could not be read — media or permissions, not the clock. `closed: "unreadable"` marks the same thing |

An ordinary crash and restart does **not** set this flag: the trust test carries 60 s
of tolerance precisely so that a crashing process does not send anyone to check a
battery. (That tolerance used to be described as "a whole checkpoint interval". There
is no checkpoint interval any more — the checkpoint is written every second — and the
60 s now covers only ordinary NTP corrections.)

`boot_id` in the header distinguishes "the process restarted" from "the machine
rebooted" — under `Restart=always` those look identical from a timestamp, and
telling them apart is the first question a crash loop raises. Empty (`""`) means
the platform does not expose one.

*Added 2026-10-30:* three more header fields. Segments written before then have `null` in all three.

- **`process_uuid`** names the process that wrote the segment. It is one UUID, made when
  `wl-sync record` starts and written into every segment that process writes. Every restart
  makes a new one. Segments with the same id are on one clock: one origin, and under
  `"rp1-pio-si"` or `"rp1-pio"` one rate too. Under `"split"` code words still drift from
  barcodes and edges at RP1's crystal error (see `wl_sync/log.py`). A new id is a restart.
  The id does not say whether anything lies between two segments; the `B` values do. The
  start's journal line prints it.
- **`barcode_delay_us`** is how long after a `B` record's tick the frame's lead edge reaches
  the wire, in µs. Add it to a `B` tick to get the wire time. *Since the one-clock build
  (2026-11-19)* it is 0 under `"rp1-pio-si"` and `"rp1-pio"`, because the tick is the
  stamp of the edge itself. Under `"split"` the first `B` of a process can be later by an
  amount nothing measures, and the delay is `null` until bench day measures it and the unit
  passes `--barcode-delay-us` (above).
- **`timebase`** names the clocks the segment's ticks count. `"fake"` under the `--fake`
  unit. On the RP1 backend, since the one-clock build (2026-11-19), one of three:
  - `"rp1-pio-si"`: code words, barcodes and edges all on RP1's PIO count, scaled to SI µs
    by one rate, fixed for the process, from the PIO rate file
    ([below](#the-pio-rate-file-once-per-install-or-board)).
  - `"rp1-pio"`: the same at RP1's own crystal rate, because no calibration was usable at
    start. All three still share one rate.
  - `"split"`, the fallback: code words on RP1's crystal, barcodes and edges on
    CLOCK_MONOTONIC, which drift apart from capture start. Every RP1 segment written
    from 2026-10-30, when the field was added, until the build says it; earlier ones say
    `null` (above). Since the build a process writes it only when its stamp machine
    failed its start-up test, or when it cannot capture code words at all (both below),
    or, on the bench alone, when `--split` forces it *(added 2026-11-21; below)*.

**A process on the RP1 backend that records `"split"` says why, once, at start** *(added
2026-11-19)*. Before the first segment the box tests the stamp machine, which counts each
barcode's lead edge as it rises on GPIO17: a third PIO state machine, enabled through the
same sync enable the production machines are, watches GPIO17 rise once. If the stamp
machine or the test machine does not start, the sync enable is refused, or the PIO does not
see the rise, the journal has

    the stamp machine's start-up test failed (<cause>): this process records timebase split, with B and E on CLOCK_MONOTONIC and W on RP1's crystal, until a restart passes it

and the box records anyway, with its barcodes timed as before the one-clock build
(`wl_sync/rp1/backend.py:684-697`). A pass logs `the stamp machine saw GPIO17 rise at
start-up` instead. `<cause>` is one of:

- `the stamp machine did not start (errno N)`. EBUSY (16) is a PIO block with no free
  state machine (`pio/wlsync_pio.c:220-222`).
- `the test machine did not start (errno N)`.
- `the sync enable was refused: the test machine stamped GPIO17's rise only when enabled
  alone` *(added 2026-11-20)*. Enabled in sync, the test machine stamped nothing; enabled
  the plain way, 0.5 s later, it stamped a second pulse. piolib discards a refused sync
  enable's result (`pio/wlsync_pio.c:252-255`), so this is how the box sees one, and a box
  that refuses it would otherwise have recorded one count with every `B` dropped.
- `no stamp when GPIO17 rose, enabled in sync or alone: the PIO does not see GPIO17 rise`:
  neither pulse was stamped. (Until 2026-11-20 this read `no stamp when GPIO17 rose`, after
  one pulse.)
- `the test could not run (...)`, with the error.

(`wl_sync/rp1/backend.py:685`, `:886`, `:112-118`, `:905-908`.) On a failure the box drives
GPIO17 twice, 1 ms high each and 0.5 s apart; no decoder takes either for a frame. To repeat
the test by hand, stop the service (`sudo systemctl stop wl-sync`) and run spike S.4's check:

    ~/wl-sync-venv/bin/python -m wl_sync.bench stamps

It builds the backend as the unit does, so it repeats the start-up test first. A test that
fails again prints `start-up test failed (<cause>)`, runs no frame, and exits 1
(`wl_sync/bench.py:363-370`). A test that passes is followed by 60 of the box's own
frames, one a second (`--frames N` for more; S.4 ran 300). It passes when every lead edge
is stamped, every later rising edge is accounted for, and nothing is stamped between
frames (`wl_sync/bench.py:220-223`). Then `sudo systemctl start wl-sync`: a process whose
test passes records one count again.

If the test passes and the frames still get no stamp, spike S.3b's pull toggles show
whether the machines run at all. Run

    ~/wl-sync-venv/bin/python -m wl_sync.bench stamps --stamp-pin 19 --frames 60

and from a second session, about 5 s in:

    for i in 1 2 3 4 5; do pinctrl set 19 pu; pinctrl set 16 pu; sleep 1; pinctrl set 19 pd; pinctrl set 16 pd; sleep 4; done

`stamps_seen 5` and `words 5` mean the stamp machine and the capture machine both run
after their sync enable, as they did in the spike (S.3b, 2026-11-07; `pinctrl` needed no
`sudo` there). A pull-up is not a lead edge and an unwired GPIO19 stamps no frame, so that
run fails by design. A count of 0 means that machine did not run, or the pulls did not
reach the pins. The start-up test sees a box that refuses the sync enable (above), but not
a refusal of the production machines' own enable after the test's was obeyed: that one is
invisible to the shim (`pio/wlsync_pio.c:252-255`), and only these toggles see it. It is
the gap left open on 2026-11-20 (`wl_sync/rp1/backend.py:1125-1132`): the mask holds only
machines the shim claimed, so it would be a transient failure of RP1's firmware, and every
`B` of that process would be dropped with its ERROR until a restart. The toggles also need
a passing test. A failed test's cause tells a refused sync enable from a PIO that cannot
see GPIO17 only by inference: a PIO that misses just the first rise reads as the first,
and a box that refuses the plain enable too as the second (the same blind spot).

**A process that cannot capture code words records anyway** *(added 2026-11-20)*. Until
then a capture machine that could not be configured raised at construction, and under
`Restart=always` the unit came back every second to fail again. Now the box records `B`
and `E` without `W`. If the shim cannot be loaded, the PIO block cannot be opened, or the
capture machine cannot be configured, the journal has, once, at start

    event-code capture cannot run (<cause>): this process records timebase split with no W records, and B and E on CLOCK_MONOTONIC, until a restart starts the capture machine

(`wl_sync/rp1/backend.py:563-581`). `<cause>` is the load's own message (`libwlsync_pio not
found (...)`, or `libwlsync_pio is version N, and this wl-sync needs 6: rebuild ...`, which
names the rebuild), `opening PIO block 0 failed: errno N`, `the capture machine did not
start (errno N)` (`wl_sync/rp1/backend.py:617`, `:621`, `:655`), or, since 2026-11-20,
`the capture machine's RX FIFO cannot be measured (depth D, level L): the code-word pairing
is proven from both` (`:660-665`). In that last, a negative level is the shim's errno:
`level -9` (EBADF) means it did not find piolib's descriptor for the block, the likeliest
cause a piolib other than the pinned one (`pio/wlsync_pio.c:54-67`), and `level -5` (EIO)
that the kernel's answer was refused (`pio/wlsync_fifo_state.h`). A depth that is not even
and at least 2 is piolib's own answer, and is refused too. If the capture machine
was configured and cannot be enabled when capture starts, in a split process, it is

    event-code capture could not start (enabling the state machine failed: errno N): this process records no W records, and B and E go on, on CLOCK_MONOTONIC, until a restart starts the capture machine

and on one count, where the shim's one errno needs a closed block and is not expected,

    the capture and stamp machines could not start (enabling the state machines failed: errno 19): this process records no W and no B records, and E goes on, extrapolated from the sync enable, until a restart starts them

(`wl_sync/rp1/backend.py:1072-1085`). Either way the health check holds one episode for the
run, `capture degraded, still recording: event-code capture did not start (<cause>): no
code word is recorded in this process` (`wl_sync/rp1/backend.py:1532-1538`). The segments
say `"split"` (or the one-count value they had), with no `W` records, and nothing in them
says `W` is missing: the journal is the only witness (`wl_sync/log.py:222-233`). Three
start-up checks stay fatal, and still restart the unit every second: a GPIO chip that is
not RP1's bank (nothing could be recorded), piolib's encodings disagreeing with this
package's, and an impossible `clk_sys` (either would put wrong ticks in every `W`).
`bench stamps` refuses such a process, printing `event-code capture cannot run (<cause>):
this process records split, and has no stamps to count` (`wl_sync/bench.py:356-362`).

**`--split` forces the fallback, for bench check B2 alone** *(added 2026-11-21, Task 1.24;
the user's ruling)*. `--split` is for the bench, never for the unit: the shipped
`deploy/wl-sync.service` must not carry it, and a test fails if it does
(`tests/test_cli.py`, `test_the_shipped_unit_never_records_split_by_force`). B2 measures
the split backend's drift on current code (plan
`docs/superpowers/plans/2026-11-07-one-clock.md:2049-2051`), and on a box whose start-up
test passes nothing else records split. Run by hand, as the other bench runs are:

    ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --split

The process takes the fallback's own path, as a stamp machine that does not start does, so
its headers say `"split"` with `barcode_delay_us` `null` (or `--barcode-delay-us N`), and
its records are a failed start-up test's: `W` on RP1's crystal, `B` and `E` on
CLOCK_MONOTONIC. No stamp machine starts and the start-up test does not run: what it
witnesses is what one count's `B` needs, and split's does not. The capture machine's own
start-up checks all run, and fail as they do without the flag
(`wl_sync/rp1/backend.py:582-585`, `:724-755`). It says so three times, each at ERROR, so
a process recording split by force never passes silently. At start:

    the stamp machine was not started (forced by --split, for the bench only): this process records timebase split, with B and E on CLOCK_MONOTONIC, until a restart without --split

then once a run, from the health check, `capture degraded, still recording: timebase split
forced by --split, for the bench only: B and E are on CLOCK_MONOTONIC in this process, not
on RP1's count`, and at the run's end `this run recorded timebase split, forced by
--split, for the bench only: B and E were on CLOCK_MONOTONIC throughout`
(`wl_sync/rp1/backend.py:748-752`, `:1542-1547`, `:1969-1973`). `--split` with `--fake` is
refused, exit 2: the fake backend has no split to force (`wl_sync/cli.py:1913-1917`).

**A dropped barcode is a journal ERROR, and only that** *(added 2026-11-19)*. On one count a
frame whose lead edge was not stamped has no `B` record, and no `L` record either. Every
device recorded the frame. The first drop of a run logs `barcode N dropped: <cause>. Every
device recorded the frame; this log has no B for it`, and a run of more than one ends with
`K barcodes dropped in a row, N to M: <advice>` (`wl_sync/cli.py:1388-1422`). Each run's
end logs `barcodes: E emitted, D dropped this run`. `journalctl -u wl-sync -p err` shows
the ERRORs. Advice that names `python -m wl_sync.bench stamps` means the checks above.

**Stamps between frames are a WARNING an episode** *(added 2026-11-20)*. On one count a
stamp on GPIO17 that came before a frame's lead edge, or before its window, is discarded,
and the frame keeps its `B`. The first frame that has such stamps opens an episode and logs

    N stamps on GPIO17 between frames, before barcode V, discarded: late stamps of the frame before, a fit that has drifted, or something else driving the barcode line

The episode ends only after 60 frames in a row with none, a minute
(`BETWEEN_CLEAN_FRAMES`), so frames hit now and then, less than a minute apart, are one
episode. Its end is

    stamps on GPIO17 between frames ended: none in the 60 frames up to barcode V; the episode discarded N stamp(s), before F frame(s) of barcodes V1 to V2

where F counts the frames that had stamps, not the frames from V1 to V2. If capture stops
first, it ends with `capture stopped during an episode of stamps on GPIO17 between frames:
N stamp(s) discarded, before F frame(s) of barcodes V1 to V2`
(`wl_sync/cli.py:1480-1517`). Both are WARNINGs, so `journalctl -u wl-sync -p warning`
shows every episode's start and end; an end comes a minute after the last stamp it counts.
Each run's end logs `stamps on GPIO17 between frames: N discarded this run, before F
frame(s)` (`:1519-1523`). Until 2026-11-20 the first line came once for every frame that
had any, so a drain that gave up on every frame gave one a second. A lone misread stamp,
after the stamp FIFO lost a word, is an episode of one frame.

**A desynced stamp stream is an ERROR, and stamping goes on** *(added 2026-11-20)*. When
the stamp machine's FIFO fills and the drain runs between a pair's two pushes, one word is
left alone and the pairing slips. The journal has

    capture degraded, still recording: barcode stamping: the stamp FIFO lost a word, so its data/counter pairing slipped (a counter, 0x..., where a data word belongs): resynchronised on that counter, and stamping goes on; a frame whose lead edge's stamp was lost is dropped; N desync(s) this run

(`wl_sync/rp1/pio_capture.py:658-663`). Like every health ERROR it is given once an
episode: desyncs found in consecutive health checks, a second apart, share one line, and
N, the run's count so far, says how many there have been. The full FIFO behind a desync
has its own line, `barcode stamping: the stamp machine's RX FIFO was full when drained:
barcode stamps may have been lost` (`wl_sync/rp1/pio_capture.py:647-650`), only when a
health check fell between the two; found by the same check, only the desync is given. A frame whose lead
edge's stamp was lost has its own `barcode N dropped` ERROR, and the frames after it are
stamped. Each run's end on one count logs `barcode stamping: N desync(s) this run, each
resynchronised`, or `barcode stamping: 0 desync(s) this run`
(`wl_sync/rp1/backend.py:1998-2002`). Until 2026-11-20 a desync stopped the stamp drain,
and every later frame was dropped until a restart. `barcode stamping stopped` now means
the drain itself failed, with its cause in the same ERROR, and a restart starts it again.

**A desynced code-word stream is an ERROR, and capture goes on** *(added 2026-11-20)*. When
the capture machine's FIFO fills and the drain pops while strobes come, a word can be
lost, and the pairing of data words with counters slips. The journal has

    capture degraded, still recording: event-code capture: the code-word FIFO may have dropped a word, so at least N strobe(s) whose data/counter pairing cannot be proven are left out, with an L record in the segment: paired again from the next pair proven whole, and capture goes on; D desync(s) this run

(`wl_sync/rp1/pio_capture.py:536-541`). The word pump writes only the pairs the drain's own
counts prove are one strobe's two words, and drops the rest, so no `W` in the segment is a
misreading across a lost word. At least N strobes were dropped, and the segment's `desync`
`L` record carries the same count, just after the last `W` before them. Like every health
ERROR it is given once an episode, and D, the run's count so far, says how many desyncs
there have been: one for each unbroken run of dropped words, which can give an `L` record
at each drain it spans. The full FIFO behind it has its own line, `event-code capture: the PIO RX FIFO was
full when drained: event codes may have been lost` (`:502-505`), only when a health check
fell between the two. Each run's end logs `event-code capture: N desync(s) this run, each
resynchronised`, or `event-code capture: 0 desync(s) this run`
(`wl_sync/rp1/backend.py:1983-1987`), in either timebase. Until 2026-11-20 a desync
stopped event-code capture for the rest of the process; `event-code capture stopped: no
later code word can be recorded` now means the drain or its consumer failed. In the 65.5 ms
before each 71.6-minute wrap of the PIO count a counter cannot be told from a code, so a
desync there drops every code word until the wrap.

**A FIFO level that cannot be read is an ERROR, and capture goes on** *(added 2026-11-20)*. The
pairing above rests on every level read being exact, and piolib's read 0 when it failed. The
shim's read says it failed (since version 4), and the journal has

    capture degraded, still recording: event-code capture: the code-word FIFO's level could not be read (errno E): that drain read nothing, and capture goes on

or, when the read after popping failed, `... the words that drain popped are left out, with an
L record in the segment, and capture goes on`; for the stamp machine, `barcode stamping: the
stamp FIFO's level could not be read (errno E): that drain read nothing, and stamping goes on`
(`wl_sync/rp1/pio_capture.py:506-511`, `wl_sync/rp1/backend.py:1213-1216`).

**The FIFO's depth is measured at start-up** *(added 2026-11-20)*. RP1's FIFOs hold 8 words a way,
16 joined, by piolib's count. After the start-up test sees GPIO17 rise, the box pulses GPIO17
nine more times, 100 µs each, without draining the test machine's FIFO, and reads its level
after each (`wl_sync/rp1/backend.py:954-1005`). It must read 2, 4, ... 16, 16. The box's
level field itself saturates at 15, `full` set at 16 words, and the shim reads 15 with
`full` as 16 (`pio/wlsync_fifo_state.h`; bench probe, wl-rig-sync0, 2026-11-20). A
version 4 shim, built before that fix, read `[0, 2, ..., 14, 15, 15]`; version 6 is
required (5 plus the bench's width meter, Task 1.21), and the backend refuses another. If the check does
not read 2, 4, ... 16, 16, the process records split with no `W`, and the journal has
`event-code capture cannot run (the RX FIFO read levels [...] as the test machine filled it,
where its depth of 16 words gives [...]): this process records timebase split with no W
records, ...`: a FIFO shallower than the pairing counts on could make a wrong `W`. A process
whose start-up test failed has the depth unmeasured, and counts on 8, which is safe whether or
not the join took. No decoder takes the nine pulses for a frame, and the test's quiet follows
them.

**The journal says when E is extrapolated** *(added 2026-11-20)*. E ticks and roll
boundaries are placed through the RAW→P fit, which a lead-edge pair moves once a frame.
When no pair comes for 20 frames, or for 20 frames' time, the journal has

    the RAW->P fit's last lead-edge pair is S s old, F frame(s) ago: E ticks and roll boundaries are extrapolated from it, drifting from P by the fit's slope error, until a lead edge is matched again

once, at WARNING (`wl_sync/rp1/backend.py:1874-1879`), or, when no pair has come since
capture started, `the RAW->P fit has had no lead-edge pair since capture started S s ago,
F frame(s): ...` (`:1866-1872`). The edges are still written. Their error grows with the
pair's age, by at most 5 ppm of it on a calibrated box before its slope is fitted, which is
100 us at 20 s (`EXTRAPOLATION_WARN_FRAMES`, `wl_sync/rp1/backend.py:139-155`). The
episode ends with

    a lead-edge pair reached the RAW->P fit again after F frame(s) without one: E ticks and roll boundaries had been extrapolated from a pair up to S s old

or `the RAW->P fit took its first lead-edge pair after F frame(s) without one: ...`
(`:1645-1650`, `:1638-1643`). If capture stops first it ends with `capture stopped F
frame(s) after the RAW->P fit's last lead-edge pair: ...`, or `capture stopped F frame(s)
after it started, with no lead-edge pair for the RAW->P fit: ...` (`:1789-1794`,
`:1782-1787`). All are WARNINGs, so `journalctl -u wl-sync -p warning` shows every
episode's start and end. An episode that runs on while the `barcode N dropped` ERRORs say
`their stamps lay outside the lead edges' windows` is a fit lost for good: the box does
not restart itself (the user, 2026-11-20), and a restart between sessions starts a new fit
at the sync enable.

A fit that moves back, as R19's restart can, places a line's next edges behind the one
before; each is written at that line's last tick instead, so each line stays in tick
order. Nothing is logged for each. Each run's end gives the count and the furthest, `edges
written at their line's last tick, to keep each line in tick order, this run: N, the
furthest D us behind it` (`wl_sync/cli.py:1303-1310`) *(added 2026-11-20)*.

A box stuck restarting will show as repeated start entries in `journalctl -u wl-sync`,
so an operator knows what a crash loop looks like as distinct from a healthy run.

## The session endpoint

The unit starts `wl-sync` with `--listen 0.0.0.0:8740 --session-token-file
/etc/wl-sync/session-token`. Through that port the task PC opens a session when an
animal enters and closes it when it leaves, and the box cuts the recording into one
folder per visit. Without the endpoint the box still records, into `unassigned/`.

**The token file.** Make the token once, and put the same one on the task PC:

    sudo install -d -m 0700 -o root -g root /etc/wl-sync
    sudo sh -c "umask 077; python3 -c 'import secrets; print(secrets.token_hex(32))' > /etc/wl-sync/session-token"
    sudo chmod 0600 /etc/wl-sync/session-token

The unit has no `User=`, so it runs as root and can read a root-only file. Never commit
the token. If the file is missing, unreadable or empty, the box logs an ERROR naming the
path (`journalctl -u wl-sync -p err`), serves `GET` only, and keeps recording.

**The port** is 8740, on the rig network. If something else holds it, the box logs an
ERROR and records without the endpoint.

**Who can read it.** `GET /session` needs no token: anyone who can reach the port can
see the open session id. Opening and closing need the token, so keep the port on the rig
network.

**On the tailnet, it is reachable from the tailnet too.** `0.0.0.0` listens on every
interface the box has, so once the box has joined the tailnet (step 7 of [Setting up
the OS](#setting-up-the-os-once-per-box)), every tailnet device that Tailscale's access
rules let reach the box can reach the endpoint as well. To serve the rig network only,
change `--listen` in the installed unit (`/etc/systemd/system/wl-sync.service`) to the
rig interface's own address, `--listen <rig-address>:8740`, then `sudo systemctl
daemon-reload` and `sudo systemctl restart wl-sync`. That address must be fixed, and on
the box when the unit starts: a bind that fails is logged at ERROR, and the box records
without the endpoint until the service next restarts.

Replace `$TOKEN` with the token. Read, open and close:

    curl http://wl-rig-sync0.local:8740/session

    curl -X POST http://wl-rig-sync0.local:8740/session/open \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"request_id": "visit-attempt-0001"}'

    curl -X POST http://wl-rig-sync0.local:8740/session/close \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"session_id": "2026-10-18_01"}'

Use a new `request_id` for every attempt at a new visit. Resending the same one after a
lost reply returns the session it already opened instead of opening a second.

## MonkeyLogic on the dual-boot rig

On days the rig boots Windows and MonkeyLogic runs the task instead of wl-xcon, MonkeyLogic
makes the same two calls from MATLAB. Without them the whole visit records into
`unassigned/` and never becomes a session. Nothing new is installed on the Windows boot:
`deploy/monkeylogic/wlsync_session.m` is one file.

**This cannot be tested in this repository.** It has never run under MATLAB, MonkeyLogic or
against the box. The function's MATLAB calls are believed to be documented, and the ones
named in the checklist below are unconfirmed; none of it is confirmed until someone runs it on the
Windows boot.

**Install.** Copy `wlsync_session.m` into a folder on MATLAB's path (`addpath`, or a folder
MonkeyLogic already puts there). Check with `which wlsync_session`. It needs MATLAB R2016b
or later, which is believed and unverified: `matlab.net.http` and `jsonencode` are
believed to need that release.

**The token.** Put the same token as the task PC's in a text file on the Windows boot, for
example `C:\wl\session-token`, readable only by the operator's account (remove inherited
permissions in the file's Security tab, or `icacls C:\wl\session-token /inheritance:r /grant:r %USERNAME%:R`).
Never commit it, and never paste it into a script. The function reads it with
`strtrim(fileread(...))` and never prints it.

**Where to call it.** Call `id = wlsync_session('open', box, token_file)` when a session starts
and `wlsync_session('close', box, token_file, id)` when it ends, with `box` like
`'wl-rig-sync0.local:8740'` and `id` the one `'open'` returned.
NIMH MonkeyLogic is believed to run a user `alert_function.m` with session-level cases (task
start and end), which would be the likely place. **Confirm this against the installed
MonkeyLogic version on bench day**, including the exact case names and whether an error
raised there stops the start. If MonkeyLogic offers no such hook, call the function from
whatever script launches the run.

**Use the returned id.** `id = wlsync_session('open', ...)` names MonkeyLogic's data file or
folder for the visit (for example `2026-10-18_02`), so wl-preproc can pair it with the box's
`<id>/syncbox/`.

**Close by that id.** With `id`, `'close'` asks the box to close exactly that session, and the
box refuses (HTTP 409) if it is not the open one. That refusal is what stops a late close from
ending the next visit's session. Without `id`, `'close'` closes whatever the box says is open
at that moment, and so skips the refusal. Leave the id out only when closing by hand.

**A failed open must stop the session from starting.** The function raises an error on every
failure, carrying the box's own message and HTTP status, and `open` retries only what is safe
(network failure, timeout, 503) using one `request_id`. Do not catch the error and carry on,
and never type an id by hand: the box mints every id, and a made-up one pairs with nothing.

**Bench-day checklist**, once on the Windows boot against the real box:

1. `wlsync_session('show', box)` returns `''` with nothing open (and the JSON null does not break it).
2. `id = wlsync_session('open', box, tf)` returns an id like today's `_01`, and `curl .../session` agrees.
3. `wlsync_session('show', box)` now returns the same id.
4. `wlsync_session('close', box, tf, id)` returns that id; `show` returns `''` again.
5. Retry with a lost reply: open, then drop the network for a moment (or point at a slow link) so the
   first reply is lost. Exactly one session must result, not two. A hang is a possible outcome,
   because `ConnectTimeout` is believed to bound connecting only (unverified, see item 8): if MATLAB hangs, stop MonkeyLogic, then
   run `wlsync_session('show', box)` to see what the box did.
6. A wrong token: the error reads HTTP 401 with the box's message, and the token appears nowhere.
7. An unreachable box (wrong host, or the box off): the error says unreachable after 4 attempts,
   in about 3 s of waiting plus the timeouts.
8. Unconfirmed MATLAB details, fix if they fail: the `HTTPOptions` property name `'ConnectTimeout'`
   (and whether it also bounds a slow reply), and the type of `resp.Body.Data` for a JSON body
   (the function handles a struct, text or bytes).
9. The MonkeyLogic hook: where it fires, and that a failed open really stops the start.
10. Error classification: that `send` raises `matlab.net.http.HTTPException` (believed, unverified)
    for a refused connection, unknown host and timeout, so item 7 reads "unreachable". Then break an
    option on purpose (misspell the `HTTPOptions` property): the error must be `wlsync:internal` with
    MATLAB's own message, and must not be retried (no 3 s wait).
11. Name resolution: the Windows boot resolves `wl-rig-sync0.local`. If not, use the box's IP
    address or its tailnet name as `box`.
12. A 409: with a session open, `wlsync_session('close', box, tf, old)`, where `old` is an
    earlier id that is not the open one, errors with HTTP 409 and `[the open session is ...]`,
    and the open session stays open (`show`).
13. `wlsync_session('close', box, tf)` with nothing open errors "nothing to close".
14. `wlsync_session('bogus', box)` errors naming `'open'`, `'close'` and `'show'`.
15. Open while a session is already open: the new id is returned and a `wlsync:autoclosed`
    warning names the session the box force-closed.
16. The MATLAB release (`version`) is R2016b or later. That minimum is believed, not
    confirmed; on an older release, a failure in `matlab.net.http` or `jsonencode` is the
    likely sign.
17. MATLAB sends `Content-Length` with the body, not chunked encoding. The box answers a
    chunked body with HTTP 400 "a valid Content-Length is required", so if item 2 fails
    with that message, this is why. The box closes the connection with the chunked body
    unread, so the same cause might instead surface as a reset connection, which the
    function reports as unreachable even though `show` works.
18. The strobe and code lines, which the task PC drives for the capture program: hold the code
    at least 2.5 µs after raising the strobe, keep the strobe high at least 1 µs and low at
    least 1 µs, and rise at least 6 µs after the previous rise (the contract in
    `wl_sync/rp1/backend.py:1017-1024`).

## Setting up the OS, once per box

Done this way for `wl-rig-sync0` on 2026-10-18, with no screen and no NVMe adapter. It
ended running Raspberry Pi OS Lite, Debian 13 "trixie", kernel `6.18.50+rpt-rpi-2712`, from
the NVMe.

1. **Write Raspberry Pi OS Lite (64-bit) to a USB drive** with Raspberry Pi Imager. Use
   Raspberry Pi OS, not another distribution: PIO reaches Raspberry Pi's own kernel first
   (`docs/superpowers/plans/2026-08-13-sync-box.md`). In Imager's settings:
   - hostname `wl-rig-syncN`;
   - a user, with SSH by public key only;
   - no Wi-Fi.
2. **Boot from the USB drive, with the SD slot empty.** The CM5's `BOOT_ORDER=0xf2461`,
   read right to left, tries SD, then NVMe, then USB, then network. A blank or
   non-bootable NVMe therefore falls through to USB.
3. **Give it a network that hands out addresses.** On a direct cable with no DHCP,
   NetworkManager gives up and takes the link down, so the box answers for a few seconds
   and then vanishes. Use the lab network, or a Mac's Internet Sharing over its Ethernet
   adapter, which puts the box on 192.168.2.x. Then `ssh <user>@wl-rig-syncN.local`.
4. **Set what this box needs that a stock image lacks:**
   - **`force_eeprom_read=0`** at the end of `/boot/firmware/config.txt`, which ends in an
     `[all]` section. GPIO 0/1 carry event-code bits 0 and 1, and the boot-time HAT probe
     would fight the breakout board's drivers on them (spec §4).
   - **A persistent journal.** Raspberry Pi OS keeps it in RAM
     (`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`), so every error is
     lost at reboot, and the journal is this box's only status surface:

         sudo mkdir -p /etc/systemd/journald.conf.d
         printf '[Journal]\nStorage=persistent\n' | sudo tee /etc/systemd/journald.conf.d/50-wl-sync-persistent.conf
         sudo systemctl restart systemd-journald

   - **No radio.** `ls /sys/class/ieee80211` must print nothing. The part wanted is
     `CM5004000`. A wireless module needs `dtoverlay=disable-wifi` and
     `dtoverlay=disable-bt` (spec §4.2).
5. **Clone onto the NVMe with `rpi-clone`** (github.com/geerlingguy/rpi-clone). Its
   installer is piped into `sudo bash`, so read it first. Before running it:
   - Install `parted`, `fdisk` and `dosfstools` with `apt`. Its own check for missing
     tools runs before `-U` takes effect, so without a terminal it aborts; and its package
     names predate Trixie.
   - Then run `sudo rpi-clone -U nvme0n1`. **It erases the NVMe.** It took 55 s for 3.9 GB.
6. **Power off, remove the USB drive, and power on.** Then check:
   - `findmnt -n -o SOURCE /` reads `/dev/nvme0n1p2`;
   - `vcgencmd get_throttled` reads `0x0`;
   - `od -An -tu4 --endian=big /proc/device-tree/chosen/power/max_current` reads `5000`,
     meaning the supply negotiated the full 5 A;
   - `timedatectl show -p NTPSynchronized` reads `yes`.
7. **Optionally, join the tailnet**, so the box is reachable from a different subnet,
   where `.local` names do not reach. `wl-stack/catalog/tailscale.md` is the lab's entry;
   on Debian, from Tailscale's own repository:

       curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
       curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
       sudo apt-get update && sudo apt-get install -y tailscale
       sudo systemctl enable --now tailscaled
       sudo tailscale up --hostname=wl-rig-sync0

   `tailscale up` prints a login link, which a person must open. Then:
   - **disable key expiry** for the node in the admin console. Otherwise it expires after
     180 days by default, and a box running unattended quietly drops off;
   - leave Tailscale SSH off, because key-only `sshd` already works.

   **Renaming an existing box to the lab host grammar** (`wl-sync-rig0` became `wl-rig-sync0`,
   2026-11-20; a person runs these, once, on the box):

       sudo hostnamectl set-hostname wl-rig-sync0
       sudo tailscale set --hostname=wl-rig-sync0
       sudo sed -i 's/wl-sync-rig0/wl-rig-sync0/g' /etc/hosts
       # then: rename the node in the Tailscale admin console if it does not follow,
       # and update any ~/.ssh/config Host entries that say wl-sync-rig0

   `hostnamectl` does not touch `/etc/hosts`: until its `127.0.1.1` line names the
   new hostname, every `sudo` warns "unable to resolve host". Seen on the real
   rename, 2026-11-20.


   `wl-sync` itself does not need Tailscale, so it is not in `wl.yaml`.

Two things from that first setup to know about:

- **Its root steps used a temporary passwordless-`sudo` drop-in,**
  `/etc/sudoers.d/090-wl-sync-setup`. Delete it when bring-up is done, because a box left
  running unattended for years should not keep passwordless root.
- **It dropped off three times while being set up, cause not found.** Power read clean
  every time it could be checked, and it was not heat. The journal now persists, so after
  a repeat, `journalctl -b -1 -n 50` shows whether the previous boot ended in a clean
  shutdown or just stopped.

## Installing wl-sync and its hardware backend, once per box

This is what first ran the RP1 backend on silicon, on `wl-rig-sync0` on 2026-10-18.

1. **Build tools and libgpiod's command-line tools:**

       sudo apt-get install -y --no-install-recommends build-essential git gpiod python3-venv python3-dev

2. **piolib, compiled into this repository's shim.** piolib's API is almost entirely
   `static inline`, so `ctypes` cannot call it directly; `pio/wlsync_pio.c` explains why.
   The shim includes piolib's sources, so nothing else needs installing:

       git clone --depth 1 https://github.com/raspberrypi/utils.git ~/src/utils
       git clone https://github.com/jakewesterberg/wl-sync.git ~/src/wl-sync
       ~/src/wl-sync/pio/build-shim.sh ~/src/utils/piolib /tmp
       sudo install -m 0644 /tmp/libwlsync_pio.so /usr/local/lib/ && sudo ldconfig

   Rebuild and reinstall the shim whenever `pio/wlsync_pio.c` changes. The backend reads
   `wlsync_shim_version` at start and refuses to use a shim of another version, naming this
   step. *Since 2026-11-20 the unit then stays active:* the process records split with no
   `W`, and the journal's `event-code capture cannot run (libwlsync_pio is version N, and
   this wl-sync needs 6: rebuild and reinstall it with pio/build-shim.sh ...)` names the
   rebuild (above, "A process that cannot capture code words"). A shim that says 6 and
   lacks a function reads `libwlsync_pio reports version 6 but lacks a function ...`, and
   ends the same way. Version 2 (2026-11-07): `entry` added, `wlsync_encode_jmp` removed.
   It starts the program at its entry. Version 3 (2026-11-07): `wlsync_start_stamp` and `wlsync_enable_in_sync`
   added, so a second state machine can stamp the barcode output. Version 4 (2026-11-20):
   `wlsync_sm_rx_level` returns a negative errno when the read fails, where piolib's read 0,
   and `wlsync_rx_fifo_depth` gives the FIFO's depth; the code-word pairing rests on both.
   Version 5 (2026-11-20): `wlsync_sm_rx_level` reads a full FIFO as its depth, 16, from
   RP1's level field, which saturates at 15, and its full flag; version 4 read 15, and its
   box failed the start-up depth check. Version 6 (2026-11-20): `wlsync_start_meter`, the
   bench harness's width meter (`wl_sync/rp1/meter.py`); the recording path never calls it.

   The shim reads the start of piolib's private RP1 handle, as it is at utils `b57c48e`, to
   find the block's descriptor (`pio/wlsync_pio.c:41-52`). The clone above takes utils'
   newest commit, whose handle may have moved. That is caught, not misread: every level
   read gives `-9`, and the process records split with no `W` (`deploy/README.md:203-204`).
   `build-shim.sh` warns when it builds against another commit. To build at the checked
   one, clone without `--depth 1`, then `git -C ~/src/utils checkout b57c48e`.

   `/dev/pio0` should already be `root:gpio` with mode `0660`, and the first user is in
   `gpio`. If it is `root:root`, piolib's README gives a udev rule.
3. **wl-sync, with its `pi` extra:**

       python3 -m venv ~/wl-sync-venv
       ~/wl-sync-venv/bin/pip install -e ~/src/wl-sync[pi]

4. **Link the command the unit runs.** `ExecStart` calls `/usr/local/bin/wl-sync`, and
   nothing above creates it:

       sudo ln -sfn ~/wl-sync-venv/bin/wl-sync /usr/local/bin/wl-sync

   *(Added 2026-10-19, found by the first install of the unit on `wl-rig-sync0`.)*
5. **Confirm the GPIO chip by label.** `gpiodetect` should list `gpiochip0
   [pinctrl-rp1] (54 lines)`, which is `--gpiochip`'s default.
6. **Run the hardware mode by hand**, as your own user rather than root:

       ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --duration 30

   With nothing wired to the pins, the run should give:
   - 30 consecutive barcode values;
   - no edge or word records;
   - a clean trailer, and a manifest with no gaps or overlaps;
   - no `capture degraded` lines.

   On the first run, every barcode after the first was 1 s apart to within 147 µs.

   *(Added 2026-11-19, for the one-clock build.)* Nothing is wired for the barcode stamps
   either. The stamp machine reads GPIO17, the barcode output, itself: 300 of 300 lead
   edges were stamped that way in bench S.4 (2026-11-07), and 28,800 of 28,800 over 8 h in
   S.5. GPIO19 is spare. The journal should say `the stamp machine saw GPIO17 rise at
   start-up`, and a first run's header `"rp1-pio"`, since no rate file exists yet (next
   section).

## The PIO rate file, once per install or board

*(Added 2026-11-19, for the one-clock build.)* On one count the RP1 backend scales every
tick by one rate, fixed for the process. Taken from a usable rate file, it makes the ticks
SI µs, and the header says `"rp1-pio-si"`; with none it is 1, the ticks count RP1's
crystal, and the header says `"rp1-pio"` (`wl_sync/log.py:206-216`). Each process looks
for the file at start, as `.wl-sync-pio-rate` in its `--out` directory: under this
repository's unit that is `/data/rig0/.wl-sync-pio-rate` (`deploy/wl-sync.service:29`;
`wl_sync/cli.py:516-549`), which is also what the unit installed on wl-rig-sync0 writes
(read on the box on 2026-11-07, `docs/superpowers/plans/2026-11-07-one-clock.md:1907-1908`).
The next rig box writes `/data/rig1`, so its file is `/data/rig1/.wl-sync-pio-rate`.
The box writes the file itself: once its own baseline of NTP-disciplined clock pairs
reaches an hour, and every hour after (`wl_sync/cli.py:1714-1738`). A file is used only on
the board whose serial it names, and only if its rate is within ±200 ppm of RP1's crystal
and its P/RAW ratio within ±1000 ppm (`wl_sync/calibration.py:262-285`). A process that
records `"split"` neither applies nor writes one, and scales no tick: its `W` ticks count
RP1's crystal, and its `B` and `E` ticks CLOCK_MONOTONIC (`wl_sync/rp1/backend.py:1917-1918`;
`wl_sync/log.py:217-233`).

**Once the unit runs the RP1 backend, after an install or a board swap:**

1. Let the box run at least 1 h.
2. Before the restart, check that `.wl-sync-pio-rate` exists in the `--out` directory. If
   it does not yet, wait: it is first written once an hour of pairs has been taken with NTP
   synchronized (`wl_sync/calibration.py:89-125`).
3. Restart it once, between sessions: `sudo systemctl restart wl-sync`. Between sessions,
   because a restart begins a new clock (`process_uuid`).
4. After it, the start's journal line names the applied rate,

       applying the PIO rate calibration written <time>: <+N> ppb, measured over <S> s and <P> pairs; timebase rp1-pio-si

   not the no-calibration WARNING (`wl_sync/cli.py:525-541`).

Until that restart, the process records `"rp1-pio"`, because no calibration was usable when
it started (`docs/superpowers/specs/2026-10-30-one-clock-design.md:311`). Its ticks
share one rate, RP1's crystal's, which is not SI. The journal says so at start:

    no usable PIO rate calibration (<why>): ticks count RP1's crystal, timebase rp1-pio, until a later start finds one

`<why>` is `no calibration file`, an unreadable file, another board's, a rate outside
±200 ppm, a P/RAW ratio outside ±1000 ppm, or `this board's serial cannot be read`. Without a serial a WARNING also says
that this process's rate cannot be kept for the next start (`wl_sync/cli.py:542-548`).
Later starts take the newest file. During a run, a WARNING that the calibration "most
likely came from other hardware", or that this process measures a rate more than 1 ppm
from the one it applies, changes nothing in that run: the process keeps the rate it
started with (`wl_sync/cli.py:1663-1712`).

If an hour of pairs passes with none gated, the journal has, once a process, `no clock
pair has passed the PIO rate gate in S s of narrow pairs, N of them, held by <conditions>:
...` (`wl_sync/cli.py:1631-1661`) *(added 2026-11-20)*. It means that no pair has passed
the gate in the hour, so this process has no rate yet and has written no rate file. If the
gate opens later, the file is written once an hour of pairs has passed it, as in step 2,
so a condition that clears by itself, such as NTP synchronising late, needs only more
time. If it names `no STA_PLL`, the gate will not open by itself: bench B7, which logs
whether timesyncd sets STA_PLL, is the check.

## Before first boot

- Fit the CR2032 to the CM5 IO Board. Nothing on the board can detect its absence.
- Configure NTP. `timedatectl show -p NTPSynchronized` must report `yes`. *(Added
  2026-11-19, for the one-clock build.)* **systemd-timesyncd must be the NTP client.** The
  PIO rate (above) is measured only over clock pairs taken while NTP is synchronized and
  the kernel reports its PLL on and an outstanding offset of at most 1 ms
  (`wl_sync/calibration.py:89-125`). The offset check keeps out the first sync's slew, and
  it assumes a client that corrects the clock through the kernel's PLL. A builder check
  found that under chrony the offset stays 0, so large slews would pass it; that is
  unverified here (`docs/superpowers/specs/2026-10-30-one-clock-design.md:268-272`). That
  timesyncd sets the PLL flag is unverified too, until bench B7 logs it. If it does not,
  no pair passes, and no rate file is ever written.
- **Important:** the unit runs `--fake` until bench acceptance (see above). The hardware
  capture is wired but unproven, while the crash-stitching mechanism is ready to verify
  at bring-up.
  - *(Added 2026-11-20, Task 1.21.)* Bench B3a and B3b are part of that acceptance:
    `python -m wl_sync.bench skew` and `streams`. Never run them under the unit, or beside
    a unit that runs the RP1 backend: each opens the PIO block alone, and `streams` writes
    GPIO18's (or GPIO19's) RP1 registers through `/dev/gpiomem0`. They run from
    `~/wl-sync-spike` beside the `--fake` service, in the order of `docs/next-session.md`,
    Step B. A run killed with SIGKILL between a pulse's two stores can leave the pin forced
    high under its override, which is harmless (GPIO18 and GPIO19 are no-connect) and which
    `python -m wl_sync.bench override` shows, printing for each pin the command that
    clears it with no sudo (`override --pin <pin> --clear`, the run's dense pin).
