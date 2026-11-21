# Bench day: the sync box from `--fake` to one count

One ordered checklist, followed by hand at the rig, to take `wl-rig-sync0` (a CM5) from the
`--fake` unit to recording on one count. *(Written 2026-11-20, Task 1.23, at `15b3c83`.)*

The sources stay the authority. This file orders their steps and gives each one's command and
pass line, and it changes none of them. Where a step here and its source differ, the source is
right, and this file is the one to fix. The sources are:

- `docs/next-session.md`, Step B (`:107-312`);
- the one-clock plan's bench section, P0 and B1-B10 and the ±100 µs gate
  (`docs/superpowers/plans/2026-11-07-one-clock.md:1868-2106`);
- `deploy/README.md`;
- wl-phantom's `docs/bench/phase-1.md`, at wl-phantom `e142b14`.

**How to read a step.**
- **Cite.** Every step cites its source as file:line.
- **Pass.** A pass line in quotation marks is quoted from its source, never paraphrased.
- **Composed.** **[composed]** means *composed, not from a source*: no source gives that
  command, so it is written from the code and tools that exist (the user's ruling,
  2026-11-20, "Compose, marked").
- **Sudo.** **(you, sudo)** marks every step that needs sudo. The box's rule is that you
  run anything with sudo (plan `:1904`).
- **Where.** Every other step can be run by you or by the controller over ssh.

**Where each command runs.**
- **The box:** commands start `ssh wl-rig-sync0`. A sudo step uses `ssh -t`, so that sudo
  can ask for its password.
- **The Mac, wl-sync:** commands start `git`, and run from a wl-sync checkout on the Mac.
- **The Mac, wl-phantom:** commands start `.venv/bin/`, `ssh $BOX`, `rsync` or `curl`, and run
  from the wl-phantom checkout, with `BOX`, `GEN` and `MON` set as its `phase-1.md:27-36`
  says. One host name serves throughout, so set `BOX` to the ssh name, not to
  `phase-1.md:30`'s `wl-rig-sync0.local`:

      BOX=wl-rig-sync0
      curl -s http://$BOX:8740/session

  The `curl` checks that the name also resolves for HTTP, as a tailnet name does. An ssh-only
  `Host` alias would not. If it fails to resolve, use `BOX=wl-rig-sync0.local`, as
  `phase-1.md:30` has it, for the `http://` commands **[composed]**.

**Where results go.**
- **wl-sync:** each result goes into `docs/next-session.md` Step B, as a new dated entry
  (wl-phantom `phase-1.md:1269-1270`). The plan's bench item gets a dated RESULT note, as
  B3a's and B3b's have (plan `:2058`, `:2067`).
- **wl-phantom:** results go into the `Result:` lines of wl-phantom's own `phase-1.md`, which
  is committed there (`phase-1.md:7-9`, `:1262-1263`).

## The order, and what it needs

| Part | Steps | Wiring | The `--fake` service |
|---|---|---|---|
| A. Before you go, and the deploy | 1-4 | nothing | restarted once, still `--fake` |
| B. The jumper-only checks | 5-9 | jumpers from header pin 11 | running beside them |
| C. wl-phantom: P0, the ±100 µs gate, B2 | 10-20 | wl-phantom's two Picos | stopped (you, sudo), then started again |
| D. B7 at a reboot | 21 | nothing | restarted by the reboot |
| E. Leaving `--fake`, and the rate file | 22-23 | nothing | the unit edited (you, sudo) |
| F. The task PC, and afterwards | 24-25 | the rig | the unit on one count |

**Header pins.** Each is the CM5 IO Board's 40-pin header, from the repository's header
symbol (`hardware/lib/wl-sync.kicad_sym:1529-2012`, `RaspberryPi5_GPIO_Header`):

| GPIO | pin | GPIO | pin | GPIO | pin | GPIO | pin |
|---|---|---|---|---|---|---|---|
| 0 | 27 | 7 | 26 | 14 | 8 | 21 | 40 |
| 1 | 28 | 8 | 24 | 15 | 10 | 22 | 15 |
| 2 | 3 | 9 | 21 | 16 | 36 | 23 | 16 |
| 3 | 5 | 10 | 19 | 17 | 11 | 24 | 18 |
| 4 | 7 | 11 | 23 | 18 | 12 | 25 | 22 |
| 5 | 29 | 12 | 32 | 19 | 35 | 26 | 37 |
| 6 | 31 | 13 | 33 | 20 | 38 | 27 | 13 |

GND is at pins 6, 9, 14, 20, 25, 30, 34 and 39.

---

## A. Before you go, and the deploy

### 1. Before you go

Bring these:

- **The strobe source.** The sources ask for "a second board or a signal generator" on the
  strobe and data lines (`docs/superpowers/plans/2026-08-13-sync-box.md:1130`;
  next-session `:206-207`). Here that is wl-phantom's two Pico H boards: the generator and
  the monitor. Before the day, have their images built and the host tool installed, and
  rehearse with the fakes (`phase-1.md:38-63`). Have its seven scripts saved
  (`phase-1.md:64-344`), and the composed `mk_burst30.py` of step 15.
  - **Its limit is gone, from wl-phantom `684d895`**, on its main since 2026-11-21
    (origin/main `d3b8706`). Before it the generator timed in whole µs: `StrobeTiming` held integers
    (wl-phantom `wl_phantom/render.py:86-88` at main `e142b14`), and `run` never passed a tick
    but 1000 ns. From `684d895`, `run` takes `--tick-ns` and decimal `--width-us` and
    `--hold-us` (`phase-1.md` §3.2), which plays P0 item 5's 2.5 µs pass point and item 6's
    5.5 µs case (step 15). An older host tool refuses `--tick-ns`; install this one.
- **Jumper wires,** female to female:
  - two from header pin 11 (GPIO17): one to pin 38 (GPIO20), one to pin 36 (GPIO16);
  - for wl-phantom: 25 signal wires, the GPIO17 wire to both Picos' GP28, and three grounds,
    by `phase-1.md:350-390`.
- **The laptop.** `ssh wl-rig-sync0` must work, and the key must be loaded, as the brief
  for this runbook gives it:

      ssh-add ~/.ssh/wl_sync
      ssh wl-rig-sync0 'hostname; uptime'

- **The commit to deploy, pushed: a precondition.**
  - **Why a deploy.** The box runs `fc18551` under `--fake`, and `fc18551` needs shim
    version 5. Bench day needs version 6, so it needs a deploy of a version-6 commit first
    (step 3).
  - **What origin/main has now.** On 2026-11-20 origin/main is `48a073d`. It has carried
    version 6 since `64dbe0f` (Task 1.21 fix round 6).
  - **What it lacks.** Task 1.22 (`431a6c4`), Task 1.24's `--split` (`c8222a6`) and this
    runbook are on `one-clock-p1`, and not yet on origin/main. Steps 8 and 19 need
    `--split`.
  - **So the push comes first.** The deploy commit is whatever origin/main holds after you
    approve the push of `one-clock-p1`, and you will be asked that separately. Write its
    hash down on the Mac, after the push:

        git fetch origin && git rev-parse --short origin/main

- **Time.** About three days of bench time, since several runs last 8 h:
  - B6 and B8's day 1, 8 h (step 9);
  - B7, 8 h after a reboot (step 21);
  - B8's day 2, 8 h (step 23).
  wl-phantom's steps need most of a day (Part C).

### 2. Pre-check, read only

    ssh wl-rig-sync0 'systemctl is-active wl-sync; systemctl is-enabled wl-sync; systemctl cat wl-sync | grep -c -- "--fake"; pgrep -af "[w]l_sync.bench" || echo "no bench running"; ls -l /dev/pio0 /dev/gpiomem0; id -nG; ls /sys/bus/platform/devices | grep -i gpiomem; pinctrl get 17,18,19'
    ssh wl-rig-sync0 'git -C ~/src/wl-sync rev-parse --short HEAD; git -C ~/src/wl-sync status --short | head -5; git -C ~/src/utils log -1 --format=%h; ls ~/wl-sync-spike; curl -s http://localhost:8740/session; echo'
    ssh wl-rig-sync0 'cd ~/wl-sync-spike && LD_LIBRARY_PATH=$HOME/wl-sync-spike/lib-b3 venv/bin/python -m wl_sync.bench override'

The first line is next-session `:181`, with `systemctl is-enabled` added **[composed]**, for
step 21's reboot. The second line is **[composed]**; its commit check is `phase-1.md:852`'s.
The third is next-session `:182` and `:186`, with `lib-b3` (below).

**Pass:**
- "`active`, `1`, no bench running, both devices in group `gpio` with the user in it, and
  GPIO17-19 not under another function" (next-session `:181`);
- `fc18551` (or the last deploy) with a clean status, and utils `b57c48e` (README `:646-658`);
- `{"session_id": null, ...}`;
- "`no override` on GPIO18 and GPIO19" (next-session `:182`).

**Expect these on the box.**
- **GPIO19 reads `a7`, PIO19.** Spike S left it there (2026-11-20; next-session `:181`'s
  dated note). That is expected. It matters only to a run with `--dense-pin 19`.
- **The spike tree's shims.** In `~/wl-sync-spike`, `src` and `lib` are spike S's, version
  3. The version-6 shim is in `lib-b3`, and the spike venv is an editable install of
  `src-b3`. next-session `:182-186` names `lib`, but B3a and B3b ran on `lib-b3`
  (next-session `:175`).

**What a fail means:**
- **An inactive service:** read `journalctl -u wl-sync -n 50` before anything else
  (README `:42-43`).
- **A session open:** wait until it closes. A restart begins a new clock (README `:718-719`).
- **An override set:** clear it as next-session `:186` says, with no sudo:
  `override --pin <pin> --clear`.

### 3. The deploy, and shim version 6 (you, sudo, for two lines)

This deploys the commit you wrote down in step 1, which is origin/main after the push of
`one-clock-p1`. The deploy follows the install section's commands: the clone (README
`:623-626`) and the venv (`:656-657`). Rebuild the shim "whenever `pio/wlsync_pio.c` changes" (`:628`). The box
must be idle, and the restart must follow the install at once, so that the running process
is never left over new files **[composed]**.

    ssh wl-rig-sync0 'git -C ~/src/wl-sync fetch origin && git -C ~/src/wl-sync show origin/main:pio/wlsync_pio.c | grep "define WLSYNC_SHIM_VERSION"; curl -s http://localhost:8740/session; echo'

**Pass:** `#define WLSYNC_SHIM_VERSION 6` and `"session_id": null`.

    ssh wl-rig-sync0 'git -C ~/src/wl-sync merge --ff-only origin/main && ~/wl-sync-venv/bin/pip install -q -e "$HOME/src/wl-sync[pi]" && git -C ~/src/wl-sync rev-parse --short HEAD && ~/wl-sync-venv/bin/wl-sync record --help | grep -c -- --split'

**Pass:**
- The hash printed by `git -C ~/src/wl-sync rev-parse --short HEAD` is the one you wrote
  down in step 1.
- The `--split` count is 1 or more.

The path is double-quoted so the shell does not glob `[pi]`, and is written `$HOME` because a
quoted `~` would not expand. README `:664` gives it unquoted.

    ssh wl-rig-sync0 '~/src/wl-sync/pio/build-shim.sh ~/src/utils/piolib /tmp'

**Pass:** "It must compile under `-Wall -Wextra` with no warnings" (P0 item 1, plan
`:1880`). The script refuses, and writes no `.so`, if piolib is not at `b57c48e` (README `:646-658`; `pio/build-shim.sh:23-45`). The box's `~/src/utils` is at `b57c48e` (checked read-only on 2026-11-21), so this command needs no override and the refusal is not expected; do not set `WLSYNC_ALLOW_UNCHECKED_PIOLIB=1` here, because a refusal would mean the checkout moved.

    ssh -t wl-rig-sync0 'sudo install -m 0644 /tmp/libwlsync_pio.so /usr/local/lib/ && sudo ldconfig'

**(you, sudo)** This is README `:627` and P0 item 1's "Install it and run `ldconfig`".

    ssh -t wl-rig-sync0 'sudo systemctl restart wl-sync'

**(you, sudo)** The unit stays `--fake`.

Then check the shim. This is S.1's check, plan `:1950`, run against the installed shim
**[composed]**:

    ssh wl-rig-sync0 '~/wl-sync-venv/bin/python -c "from wl_sync.rp1 import backend as b; lib = b._load_shim(); print(lib.wlsync_shim_version(), [l.split()[-1] for l in open(\"/proc/self/maps\") if \"wlsync\" in l][0])"; systemctl is-active wl-sync'

**Pass:** `6 /usr/local/lib/libwlsync_pio.so`, then `active`. S.1's line was "`3`, and
`~/wl-sync-spike/lib/libwlsync_pio.so`" (plan `:1951`). Version 6 is next-session `:182`'s
"S.1's check prints `6`".

**A fail:**
- **The backend refuses the shim.** It names its rebuild: "`libwlsync_pio is version N, and
  this wl-sync needs 6: rebuild ...`" (README `:629-636`). Under `--fake` the backend never
  loads the shim, so the unit stays active.
- **A compiler warning.** It is a fault in our own build: stop, and fix it in the repository
  (plan `:2033`).

**Record:** the deployed commit, and the shim's version. Write them in next-session.

### 4. The spike tree, brought up to `--split`, no sudo [composed]

B1 (step 8) and B2 (step 19) record with `wl-sync record --split` from `~/wl-sync-spike`
with `lib-b3`. That is the user's ruling, 2026-11-20. `--split` is Task 1.24
(README `:226-252`).
- **The spike's code is the deployed code.** `src-b3` is rebuilt from step 3's deployed tree,
  on the box, as S.1 copied its build (plan `:1947`), and `lib-b3` is rebuilt from it too. It
  is not a separate copy.
- **What `rm -rf` removes.** It removes B3's tree, `src-b3` at `ab98afd`, and the shim built
  from it in `lib-b3`. Git keeps that commit, `ab98afd`. Spike S's `src` and `lib` are not
  touched.

```sh
ssh wl-rig-sync0 'rm -rf ~/wl-sync-spike/src-b3 && mkdir -p ~/wl-sync-spike/src-b3 && git -C ~/src/wl-sync archive --format=tar HEAD | tar -x -C ~/wl-sync-spike/src-b3 && ~/wl-sync-spike/src-b3/pio/build-shim.sh ~/src/utils/piolib ~/wl-sync-spike/lib-b3'
ssh wl-rig-sync0 '~/wl-sync-spike/venv/bin/pip install -q --no-deps -e "$HOME/wl-sync-spike/src-b3[pi]" && ~/wl-sync-spike/venv/bin/wl-sync record --help | grep -c -- --split'
ssh wl-rig-sync0 'cd ~/wl-sync-spike && LD_LIBRARY_PATH=$HOME/wl-sync-spike/lib-b3 venv/bin/python -c "from wl_sync.rp1 import backend as b; lib = b._load_shim(); print(lib.wlsync_shim_version(), [l.split()[-1] for l in open(\"/proc/self/maps\") if \"wlsync\" in l][0])"'
```

This build-shim.sh call also refuses a piolib not at utils `b57c48e` (`pio/build-shim.sh:23-45`); the same `~/src/utils` serves it, so it needs no override.

**Pass:**
- the shim compiles "under `-Wall -Wextra` with no warnings" (plan `:1880`);
- a `--split` count of 1 or more;
- `6` and the path of `~/wl-sync-spike/lib-b3/libwlsync_pio.so`, which is S.1's check, plan
  `:1950-1951`, on `lib-b3`.

The B3 harness of step 5 now runs from this tree too.

---

## B. The jumper-only checks, beside the `--fake` service

These record by hand, beside the `--fake` service, as spike S and B3 did (plan
`:1906-1927`; next-session `:176-177`).
- **Why they can share the box.** A `--fake` process never loads the shim, opens
  `/dev/pio0` or requests a GPIO line (plan `:1921-1925`).
- **One at a time.** Never run two of these at once: each opens the PIO block (next-session
  `:178-179`, `:145-147`).

### 5. B3a and B3b: passed, so do not re-run them

Both passed on the box on 2026-11-20/21 (next-session `:175`, `:184-185`; plan `:2058`,
`:2067`).
- **When to re-run them.** Only after a change to the board, the kernel, piolib or the
  shim's C.
- **Then.** Run next-session `:181-186` in order, with `lib-b3` for `lib` (step 2).

### 6. P0 item 2: 30 s with nothing wired

Nothing on the header.

    ssh wl-rig-sync0 'mkdir -p /tmp/wl-bench && ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --duration 30 > /tmp/wl-bench/p0-2.txt 2>&1; echo "exit $?"; grep -E "stamp machine|RX FIFO|PIO rate|capture" /tmp/wl-bench/p0-2.txt'
    ssh wl-rig-sync0 '~/wl-sync-venv/bin/python -c "import collections, pathlib, sys; from wl_sync.log import read_log; [s] = sorted(pathlib.Path(sys.argv[1]).rglob(\"seg-*.log\")); h, r = read_log(s); print(h.timebase, h.barcode_delay_us, dict(collections.Counter(type(x).__name__ for x in r)))" /tmp/wl-bench/rig0; cat /tmp/wl-bench/rig0/unassigned/*/syncbox/manifest.json'

The first command is README `:676`, with its output kept **[composed]**. The second is
S.2's read (plan `:1954-1955`), on this run's folder.

**Pass:**
- "It must start, which means the shim's version and all six encodings agree. The segment
  and trailer must be clean, with 30 `B`s and no `W`" (plan `:1881`).
- "30 consecutive barcode values; no edge or word records; a clean trailer, and a manifest
  with no gaps or overlaps; no `capture degraded` lines" (README `:679-682`).
- The journal says "`the stamp machine saw GPIO17 rise at start-up`", and the header says
  "`"rp1-pio"`, since no rate file exists yet" (README `:689-691`).
- The start-up INFO, "`the RX FIFO holds 16 words, measured at start-up: the code-word
  pairing counts on 16`" (next-session `:127-128`).
- `barcode_delay_us` 0 (README `:106-111`).

**A fail means:**
- **`the stamp machine's start-up test failed (<cause>)`** (README `:133`): the process
  recorded `"split"`. Read the cause (README `:137-150`), and repeat the test by hand as
  README `:152-164` says.
- **`event-code capture cannot run (<cause>)`** (README `:195`): the capture machine did
  not run. Read the cause (`:196-206`).
- **Any other start that refuses** is P0's Part 0 fault: stop. "A failure stops Task 5b
  and this design" (plan `:1894`, `:2027`).

### 7. The loopback, 60 s: GPIO17 to GPIO20

**Wire:** header pin 11 to pin 38 (next-session `:188-189`).

    ssh wl-rig-sync0 '~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --duration 60; ~/wl-sync-venv/bin/python -m wl_sync.bench loopback "/tmp/wl-bench/rig0/unassigned/$(date -u +%F)/syncbox"; echo "exit $?"'

These are next-session `:191-192`. Given a day folder, the loopback reads its newest
segment, which is this run's (`wl_sync/bench.py`, `main`).

**Pass:** "`wl_sync.bench` matches every frame decoded from GPIO20 against the `B` record
the box logged, and exits non-zero on any mismatch. ... The first frame is reported as not
checkable, by design" (next-session `:194-196`). So: `loopback on GPIO20: PASS` and
`exit 0`.

Its "lead edge after its logged tick, E's constant (B6)" is the edge input's own offset.
"Do not pass it as `--barcode-delay-us`" (README `:35-38`; next-session `:197-204`).

**Record:** the median and spread, as a first look at B6. Leave the jumper on for steps 8
and 9.

### 8. B1: a shared reference?, 1 h, `--split` [composed]

**Source.** "Run `dmesg | grep arch_timer`. Jumper GPIO16 to GPIO20 (header 36→38), pulse at
1 Hz for 1 h, and sample (MONO, RAW) every 100 ms. Report the rate of `W` against `E`
converted to RAW. Under 0.01 ppm, the RAW→P fit reduces to an offset" (plan `:2047`;
next-session `:263-265`). It is informational.

**What is composed.**
- **The pulses.** The source names nothing to drive them. GPIO17's barcode does it: GPIO16
  and GPIO20 are each jumpered from pin 11, so every barcode rise is a strobe (a `W`) and an
  edge (an `E`). The plan uses the same wire, 11→36, for B8, "so that each lead edge is a
  `W`" (plan `:2090`). The frames come once a second.
- **The recorder.** It runs `--split`, where `W` is on RP1's crystal and `E` on
  CLOCK_MONOTONIC (README `:236-239`). On one count both are on P, and the rate would be 0
  by construction.
- **The sampler and the fit.** Both are composed, below.

**Wire:** pin 11 to pin 38 (from step 7), and pin 11 to pin 36. That is GPIO17 to GPIO20 and
GPIO17 to GPIO16, so GPIO16 and GPIO20 are joined.

    ssh wl-rig-sync0 'dmesg | grep arch_timer'

If this says the operation is not permitted, run `ssh -t wl-rig-sync0 'sudo dmesg | grep
arch_timer'` **(you, sudo)**.

Save the two scripts on the box:

```sh
ssh wl-rig-sync0 'cat > ~/wl-sync-spike/b1_clocks.py' <<'EOF'
"""B1 (composed, not from a source): (MONO, RAW) in ns every 100 ms for S s, as CSV."""
import sys
import time

end = time.monotonic() + float(sys.argv[1])
while time.monotonic() < end:
    mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    raw = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    print(f"{mono},{raw}", flush=True)
    time.sleep(0.1)
EOF
ssh wl-rig-sync0 'cat > ~/wl-sync-spike/b1_rate.py' <<'EOF'
"""B1 (composed, not from a source): W against E converted to RAW.
usage: python b1_rate.py SEGMENT CLOCKS_CSV"""
import csv
import sys

from wl_sync.log import CodeWord, Edge, read_log


def slope(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


header, records = read_log(sys.argv[1])
w = [r.tick_us for r in records if isinstance(r, CodeWord)]
e = [r.tick_us for r in records if isinstance(r, Edge) and r.gpio == 20 and r.level == 1]
print(f"timebase {header.timebase}: {len(w)} W, {len(e)} rising E on GPIO20")
if len(w) != len(e) or len(w) < 2:
    sys.exit("W and E counts differ: a strobe or an edge was lost, so they cannot be paired in order")
w_per_mono = slope(e, w)
with open(sys.argv[2]) as f:
    rows = [(int(m), int(r)) for m, r in csv.reader(f)]
raw_per_mono = slope([m for m, _ in rows], [r for _, r in rows])
print(f"W against MONO: {(w_per_mono - 1) * 1e6:+.4f} ppm")
print(f"RAW against MONO: {(raw_per_mono - 1) * 1e6:+.4f} ppm")
print(f"W against RAW: {(w_per_mono / raw_per_mono - 1) * 1e6:+.4f} ppm (plan :2047: under 0.01 ppm is shared)")
EOF
```

Start the sampler and the recorder together. The sampler runs a minute longer, so that it
covers the whole recording:

    ssh wl-rig-sync0 'cd ~/wl-sync-spike && mkdir -p out && nohup ~/wl-sync-venv/bin/python b1_clocks.py 3660 > out/b1-clocks.csv 2>/dev/null < /dev/null & cd ~/wl-sync-spike && LD_LIBRARY_PATH=$HOME/wl-sync-spike/lib-b3 nohup venv/bin/wl-sync record --out out/b1 --split --duration 3600 > out/b1.txt 2>&1 < /dev/null &'

After the hour, find the segment and fit:

    ssh wl-rig-sync0 'cd ~/wl-sync-spike && grep -c ERROR out/b1.txt; for s in $(find out/b1 -name "seg-*.log" | sort); do venv/bin/python b1_rate.py "$s" out/b1-clocks.csv; done'

**Expect:** three ERROR lines, by design. `--split` says so at start, in the health check's
episode, and at the run's end (README `:243-251`). Then `timebase split`, and the three
rates.

**The pass is the source's.** "Under 0.01 ppm, the RAW→P fit reduces to an offset" (plan
`:2047`). Anything else is information, not a fail. A count mismatch means a lost strobe or
edge: re-run before reading the rate.

**Record:** the `arch_timer` line and the three rates. Keep the jumper from pin 11 to pin 38.
Take the jumper to pin 36 off.

### 9. B6, B10 and B8's day 1: one 8 h run with GPIO17 to GPIO20

**What the sources ask.**
- **B6.** "GPIO17 to GPIO20 alone (11→38), the edge input", over 8 h, then
  `python -m wl_sync.bench loopback`. "Report E's constant and its p50, p99 and p99.9 spread
  (Task 1.12)". It has no pass line, by the user's decision (plan `:2082-2084`).
- **The order.** B6 is its own 8 h run, and the ±100 µs gate comes after it, with this
  jumper off (the user's ruling, 2026-11-20, "Separate runs"; plan `:2106`'s dated note).

**What is composed.** This one run also carries two other checks:
- **B10:** "Count dropped `B`s over 8 h"; with c-direct it "runs on the built box as it
  stands" (plan `:2101-2102`).
- **B8's first day:** "Run the calibration over 8 h on two days. Report r, r per hour, and
  day to day" (plan `:2091`). Once its baseline reaches an hour, the box writes the rate file
  itself into its `--out`, every hour (README `:704-706`).

**Wire:** pin 11 to pin 38 only.

    ssh wl-rig-sync0 'mkdir -p /tmp/wl-bench && nohup ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0-b6 --duration 28800 > /tmp/wl-bench/b6.txt 2>&1 < /dev/null &'
    ssh wl-rig-sync0 'nohup sh -c "for i in \$(seq 1 50); do sleep 600; cat /tmp/wl-bench/rig0-b6/.wl-sync-pio-rate 2>/dev/null; done" > /tmp/wl-bench/b8-day1.jsonl 2>&1 < /dev/null &'

The second line snapshots the rate file every 10 min, for r per hour **[composed]**.

After 8 h:

    ssh wl-rig-sync0 'tail -8 /tmp/wl-bench/b6.txt; grep -c "level could not be read" /tmp/wl-bench/b6.txt; grep -c "full when drained" /tmp/wl-bench/b6.txt; for s in $(find /tmp/wl-bench/rig0-b6 -name "seg-*.log" | sort); do ~/wl-sync-venv/bin/python -m wl_sync.bench loopback "$s"; done; sort -u /tmp/wl-bench/b8-day1.jsonl'

**Pass, and what to read:**
- **B6:** no pass line. Record the `lead edge after its logged tick, E's constant (B6)`
  median, and its p50, p99 and p99.9 (`wl_sync/bench.py:131-139`).
- **B10:** "**Pass:** zero. Each drop is investigated" (plan `:2101`). Read it as the run's
  end line, `barcodes: E emitted, 0 dropped this run` (README `:258-259`), with no
  "`decoded, but the box dropped its B`" line from the loopback.
- **B8's first day:** "**Pass:** r within ±200 ppm" (plan `:2092`), with r the snapshots'
  `rate_ppb` (`wl_sync/calibration.py:243-259`). "If r moves more than 1 ppm within a day,
  the fixed-rate target cannot hold. That goes to the user" (plan `:2093`).
- **No rate file at all:** it is written only after an hour of pairs with NTP synchronised
  and STA_PLL set. A journal line `no clock pair has passed the PIO rate gate ...` names
  what held it, and "If it names `no STA_PLL`, the gate will not open by itself: bench B7
  ... is the check" (README `:740-747`).
- **Next-session's B-3 (failed level reads):** count the `level could not be read` lines.
  A few `errno 5` alone do not show the firmware failing (next-session `:140-142`).

**Copy the results off before any reboot.** `/tmp` may be emptied at boot. Run on the Mac:
`rsync -a wl-rig-sync0:/tmp/wl-bench/ ./bench-day/wl-bench/` **[composed]**.

Then take the jumper off. The header must be bare before wl-phantom's wiring.

---

## C. wl-phantom: P0, the ±100 µs gate, and B2

Follow wl-phantom's `phase-1.md` as written. It is the procedure, and this part says only
where the plan's items sit in it.

**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

**The service stays stopped through this part.** That follows `phase-1.md:428-431` (Ruling
R73): it keeps the bench recorder's log the only one in play. It is not a PIO need: the
`--fake` service never opens the block (plan `:1921-1922`).

**The bench recorders.** Each runs the deployed `wl-sync`, which `/usr/local/bin` links
(README `:669`). Each has its own `--out`, `--listen 0.0.0.0:8741` and no token, so the
endpoint serves GET only (`phase-1.md:822-843`). Start each from a second terminal with
`ssh -t`, so Ctrl-C stops it, and keep its stderr (`phase-1.md:908-911`). The gpiochip is
`/dev/gpiochip0`, by label (README `:672-673`); check it with `gpiodetect`.

### 10. Wiring, flashing, and the Pico-to-Pico checks

Stop the service:

    ssh -t wl-rig-sync0 'sudo systemctl stop wl-sync'

**(you, sudo)** This is `phase-1.md:437`.

Then run wl-phantom's steps 1-3 (`phase-1.md:348-814`):
- **Step 1, stage 1 only:** the grounds first, the generator to the monitor, and the box's
  GPIO17 (pin 11) to GP28 on both Picos. Do the meter check (`:368-391`).
- **Step 2:** flash, and tell the Picos apart (`:394-413`).
- **Step 3:** the loopback recorder of step 3.1 (`:433-449`), then 3.2 to 3.8. Each pass
  is the one its step gives.

**Pass:** every step-3 `Result:` passes. "A box result means nothing until the Pico-to-Pico
run says the phantom is right" (`phase-1.md:11-12`).


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 11. Into the box: the timebase, and P0 item 3's run (the gate's first run)

Stop step 3's recorder. Wire stage 2: every generator output to the box pin of its name,
with the pin numbers in the table above. Repeat the meter check (`phase-1.md:816-820`,
`:385-390`).

    ssh -t wl-rig-sync0 'mkdir -p /tmp/wl-phantom && timedatectl show -p NTPSynchronized --value && wl-sync record --out /tmp/wl-phantom/rig0 --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0.err'

These are `phase-1.md:827`, `:901` and `:909`. Then run the checks of 4.1, on the Mac:

    ssh $BOX 'git -C ~/src/wl-sync merge-base --is-ancestor 91efdf6 HEAD' && echo one-clock
    ssh $BOX 'head -1 $(find /tmp/wl-phantom/rig0 -name "seg-*.log" | sort | head -1)'
    .venv/bin/wl-phantom check-box http://$BOX:8741
    curl -s -o /dev/null -w '%{http_code}\n' -X POST http://$BOX:8741/session/open

**Pass:**
- "the second line prints `one-clock`" (`phase-1.md:856`);
- "its `"timebase"` is `"rp1-pio-si"` or `"rp1-pio"`, and its `"barcode_delay_us"` is 0"
  (`:866`);
- "`the box is idle`, and `403`" (`:884`).

Then 4.2's run (`phase-1.md:913-922`):

    ssh $BOX timedatectl timesync-status
    .venv/bin/python mk_timeline.py 630 0.5 t630
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 \
        --timeline t630.timeline.json --extras t630.extras.json --out runs/box-1
    ssh $BOX timedatectl timesync-status

**Pass:** `run valid` with no `run cut` line, and no `graded from` or `graded up to` line
(`phase-1.md:927-933`).


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 12. Compare: P0 items 3 and 4, Task 5b Steps 3 and 4, and the gate's first half

Stop the recorder with Ctrl-C, then run `phase-1.md:948-961`:

    mkdir -p runs/box-1-log
    rsync -a $BOX:/tmp/wl-phantom/rig0/ runs/box-1-log/
    .venv/bin/wl-phantom compare --run runs/box-1 \
        --box-log $(find runs/box-1-log -name 'seg-*.log' | sort) \
        --report runs/box-1-report.json --expect-timebase one-count

What this one comparison answers:

- **P0 item 3, which is Task 5b Step 3.**
  - P0's pass: "The recovered ticks must be within ±100 µs of ground truth, with zero
    dropped words. Check the FIFO full flag in the journal: there must be no
    `WordsDroppedError`." And: "**The slope must be 0 within the fit's error.**" (plan
    `:1883-1884`).
  - Task 5b's: "**Acceptance: recovered timestamps within ±100 µs of ground truth over a
    10-minute run, with zero dropped words.**" (sync-box plan `:1130`).
  - Read as **[composed]**: `PASSED`, empty `dropped`, and `rate_ppm` within `rate_se_ppm`
    of 0.
  - The journal check **[composed]**: `grep -c "full when drained" /tmp/wl-phantom/rig0.err`
    must print 0. Today the FIFO-full line reads `event-code capture: the PIO RX FIFO was
    full when drained: event codes may have been lost` (README `:315-316`).
  - On one count, "the box is expected to pass the rate check" (`phase-1.md:995-1005`).
- **P0 item 4.** "Measure the constant (median of recovered − truth). Expected: 3.0–4.0 µs,
  a median of about 3.5 ... minus the origin offset ... Record it in `docs/next-session.md`"
  (plan `:1885`).
  - Read as `offset_us` **[composed]**. `phase-1.md:1273-1278` says it is one combined
    constant, expected near 0 on one count.
  - Record both readings. next-session's items 3 and the known constant (`:226-228`,
    `:249-258`) are what it measures.
- **Task 5b Step 4, "parity with the fake"** (sync-box plan `:1132-1134`).
  - The plan's own text: "Replay the bench capture through `Recorder` and confirm the
    resulting log is byte-identical in structure to one produced by `FakeBackend` given the
    same injected words. The Task 4 tests already pin that shape, so this is the real payoff
    of the adapter."
  - **[composed]**, defined by the user's ruling of 2026-11-20, **for you to confirm**: it
    is wl-phantom grading the one-count box with `--expect-timebase one-count` and getting
    PASS.
  - So: `PASSED`, and no `warning:` line that names a timebase.
- **The gate's first half.** See step 13.

**A fail means** (and if you stop here, do step 20 before you leave):
- **A FAIL on the residual or the rate.** On a one-count domain it is "a fault of the box's
  W path" (`phase-1.md:1000-1002`). P0 then fails, and "A failure stops Task 5b and this
  design (spec §5, item 0), and needs its own diagnosis" (plan `:1894`).
- **A `split` domain.** The stamp machine failed its start-up test. Quote the ERROR in
  `rig0.err` (`phase-1.md:1279-1282`), restart the recorder, and run again.


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 13. The ±100 µs gate's second half: repeatability

**The gate.** "Run Task 5b Step 3 as written, and wl-phantom §5.5 on the B6 run. **Pass:**
±100 µs. A long process is not needed (spec §4; draft Q5 withdrawn)" (plan `:2106`).
- **Separate runs.** By the user's ruling of 2026-11-20, it is wl-phantom §5.5 standalone,
  after B6, with the jumper off and wl-phantom's own recorders on 8741 (plan `:2106`'s dated
  note).
- **Process age.** A fresh recorder process is enough (Ruling R43; spec `:52`, decision 14;
  next-session `:274-275`'s dated note).
- **What §5.5 is.** wl-phantom's design §5.5 is the self-consistency check (step 10), the
  code words (steps 11-12), and "Finally, **repeatability**" (wl-phantom
  `docs/superpowers/specs/2026-10-19-wl-phantom-design.md:298-338`). That last is
  `phase-1.md` step 6.

Run step 6 as written (`phase-1.md:1097-1120`). Its recorder:

    ssh -t wl-rig-sync0 'timedatectl show -p NTPSynchronized --value && wl-sync record --out /tmp/wl-phantom/rig0-b --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0-b.err'

The run, the comparison and the rule, on the Mac:

    ssh $BOX timedatectl timesync-status
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 \
        --timeline t630.timeline.json --extras t630.extras.json --out runs/box-2
    ssh $BOX timedatectl timesync-status
    mkdir -p runs/box-2-log
    rsync -a $BOX:/tmp/wl-phantom/rig0-b/ runs/box-2-log/
    .venv/bin/wl-phantom compare --run runs/box-2 \
        --box-log $(find runs/box-2-log -name 'seg-*.log' | sort) \
        --report runs/box-2-report.json --expect-timebase one-count
    .venv/bin/python repeat.py runs/box-1-report.json runs/box-2-report.json 630

Stop the recorder with Ctrl-C before the `rsync`.

**Pass:**
- The plan's "±100 µs" (plan `:2106`). Read as **[composed]**: both comparisons `PASSED`
  against their 100 µs budget.
- §5.5's repeatability: "`repeat.py` exits 0" (`phase-1.md:1159`).

**Record:** step 6's table (`phase-1.md:1122-1133`).


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 14. The GET flood during strobed codes (next-session Step B2, item 3)

"During a burst of strobed codes, flood the endpoint from another machine ... The journal
must show no `WordsDroppedError`" (next-session `:304-312`). **[composed]** The flood here
hits the bench recorder's GET-only endpoint, on 8741, while a 60 s run plays.

    ssh -t wl-rig-sync0 'wl-sync record --out /tmp/wl-phantom/rig0-flood --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0-flood.err'

On the Mac, in two terminals, start these about together:

    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 \
        --timeline t60.timeline.json --extras t60.extras.json --out runs/flood
    seq 5000 | xargs -P 32 -I{} curl -s -o /dev/null http://$BOX:8741/session

Stop the recorder, then copy and compare as in step 12, into `runs/flood-log`.

**Pass:**
- `grep -c "full when drained" /tmp/wl-phantom/rig0-flood.err` prints 0. The FIFO-full line
  is README `:315-316`; next-session `:308-309` quotes its older text.
- The comparison's `dropped` is empty.

**A fail** (and if you stop here, do step 20 before you leave): "If codes drop, cap the handler threads" (next-session `:311-312`). That is a code
change, for later.


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 15. P0 items 5 and 6: the data hold and the strobe limits [composed]

The sources:
- **Item 5.** "Drop the code lines 2.5, 2.0 and 1.0 µs after the strobe rises: at 2.5 µs
  every word must be right. This is the pass; at 2.0 µs a few may be wrong. This measures the
  skew; record the share; at 1.0 µs nearly every word is wrong" (plan `:1886-1889`).
- **Item 6.** "30-strobe bursts 6 µs apart, rise to rise, are captured whole; at 5.5 µs,
  some merge" (plan `:1892`).

**How the generator holds a word.** It holds the data `width + hold` µs after the strobe
rises (wl-phantom `wl_phantom/render.py`, `end = rise + width + hold`). The timings are whole
numbers of the generator's tick, which is 1000 ns unless `--tick-ns` says otherwise; at
`--tick-ns 500` they can be halves (wl-phantom `684d895`; the firmware takes any multiple of
125 ns, `firmware/generator/CMakeLists.txt:26-31`). A timing that is not a whole number of
ticks is refused before anything plays, so a 1000 ns run still refuses 1.5. So:
- 3.0 µs is `--width-us 1 --hold-us 2`, 2.5 µs is `--tick-ns 500 --width-us 1 --hold-us 1.5`,
  2.0 µs is `--width-us 1 --hold-us 1`, and 1.0 µs is `--width-us 1 --hold-us 0`. A 1 µs
  strobe is the box's minimum high (README `:519-522`). The 3 µs run is information only.
- 2.5 µs is exactly the box's "at least 2.5 µs after raising the strobe" (README `:519-522`),
  the pass line of item 5.
- The 5.5 µs case is `mk_burst30.py 5.5` at `--tick-ns 500 --setup-us 1 --width-us 1
  --hold-us 2`. At 1000 ns a 5.5 µs spacing would play as alternating 5 and 6 µs.

Save `mk_burst30.py` in the wl-phantom checkout, untracked, as `phase-1.md:64-66` says of its
scripts:

```python
"""usage: python mk_burst30.py SPACING_US OUT_PREFIX   (50 bursts of 30 words, one a second)"""
import json
import sys

from wl_phantom.timeline import Timeline

spacing_s, prefix = float(sys.argv[1]) * 1e-6, sys.argv[2]
words = [[5.0 + burst + spacing_s * i, burst * 30 + i + 1] for burst in range(50) for i in range(30)]
Timeline(
    format="wl-phantom-timeline/1", source=f"50 bursts of 30 words, {sys.argv[1]} us apart",
    duration_s=60.0, code_words=words, stim_events=[],
).dump(f"{prefix}.timeline.json")
json.dump({"frame_trains": [], "pulses": []}, open(f"{prefix}.extras.json", "w"))
```

Rehearse every run below on the fakes first (`--generator fake --monitor fake --box none`;
`phase-1.md:61-63`), the 500 ns ones too. This script's 6 µs timeline renders at `--setup-us 1
--width-us 1 --hold-us 2`. Those timings keep the data 3 µs after each rise and the strobe low
5 µs, inside the box's contract (README `:519-522`). Its 5.5 µs timeline renders at the same
timings and `--tick-ns 500`, and keeps the data 3 µs after each rise (at least 2.5 µs) and the
strobe low 4.5 µs (at least 1 µs). Only the spacing, 5.5 µs, is under the contract's 6 µs,
which is what the case tests.

All of these runs share one recorder, of at least 10 minutes (`phase-1.md:890-895`):

    ssh -t wl-rig-sync0 'wl-sync record --out /tmp/wl-phantom/rig0-p0 --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0-p0.err'

On the Mac:

    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline t60.timeline.json --extras t60.extras.json --width-us 1 --hold-us 2 --out runs/p0-hold3
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline t60.timeline.json --extras t60.extras.json --tick-ns 500 --width-us 1 --hold-us 1.5 --out runs/p0-hold2.5
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline t60.timeline.json --extras t60.extras.json --width-us 1 --hold-us 1 --out runs/p0-hold2
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline t60.timeline.json --extras t60.extras.json --width-us 1 --hold-us 0 --out runs/p0-hold1
    .venv/bin/python mk_burst30.py 6 burst6
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline burst6.timeline.json --extras burst6.extras.json --setup-us 1 --width-us 1 --hold-us 2 --out runs/p0-burst6
    .venv/bin/python mk_burst30.py 5.5 burst5.5
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 --timeline burst5.5.timeline.json --extras burst5.5.extras.json --tick-ns 500 --setup-us 1 --width-us 1 --hold-us 2 --out runs/p0-burst5.5

Stop the recorder and copy its log to `runs/p0-log`, as in step 12. Then compare each run:

    for r in p0-hold3 p0-hold2.5 p0-hold2 p0-hold1 p0-burst6 p0-burst5.5; do .venv/bin/wl-phantom compare --run runs/$r --box-log $(find runs/p0-log -name 'seg-*.log' | sort) --report runs/$r-report.json --expect-timebase one-count; done

**Read:** a word latched with the wrong data shows as a dropped word and an extra one
(`phase-1.md:1221-1224`).
- **At 3 µs and at 2.5 µs:** every word should be matched. 2.5 µs is item 5's pass.
- **At 2 µs:** record the share wrong (`extra` over the words sent). It measures the skew.
- **At 1 µs:** nearly all should be wrong.
- **At 6 µs:** every word should be matched. Item 6's "captured whole" is `dropped` and
  `extra` empty for `p0-burst6`.
- **At 5.5 µs:** some should merge (plan `:1892`). A merge is a dropped word with no extra.
  In the simulator (below) about half of each 30-strobe burst is lost at 5.5 µs, so a run that
  loses none differs from the simulator, and is worth noting.

**The 6 µs line has no margin.** In wl-sync's simulator (`tests/piosim.py`, running
`STROBE_CAPTURE`, with the rise stepped across the box's 1 µs test period in 40 phases), 30
strobes 6.000 µs apart are captured whole at every phase, 5.999 µs merges at one phase, 5.99 µs
at eleven, and 5.5 µs at all forty (15 or 16 of 30 captured). Reproduced on 2026-11-21 with a
short script over `tests.piosim.run` and `high_during`; the simulator's own merge tests are
`tests/test_rp1_program.py:136` and `:142`.

What a merge depends on **[derived]**:
- The capture program tests the strobe on RP1's clock (`pio/strobe_capture.pio:63-64`), and the
  generator's edges are exact on its own crystal. So the spacing the box sees is 6 µs / (1 + e)
  ≈ 6 µs × (1 − e), where e is the generator's rate against RP1's crystal (positive: generator
  fast). A fast generator puts every pair a little under 6 µs.
- The simulator's phase result is linear: a burst's strobes drift across the 1 µs test period
  by d, the shortfall from 6 µs, per pair, so a burst of 30 (29 pairs) merges at a fraction of
  phases of about 29 × d / 1 µs. Check: d = 0.01 µs gives 0.29, the simulator 11/40 = 0.275;
  d = 0.001 µs gives 0.029, the simulator 1/40 = 0.025.
- `p0-burst6` plays 50 bursts, so 50 × 29 = 1450 pairs. With d = 6 µs × e × 10⁻⁶ (e in ppm):
  expected merges = 1450 × 6 × 10⁻⁶ × e = **0.0087 × e**. At e = +10 ppm that is 0.09, and at
  +30 ppm it is 0.26.

**Reading rule.** Compute e after `p0-burst6`. If e ≤ 0, any merge at 6 µs is a box fault.
Otherwise about 0.0087 × e merges are expected in the run, so one rare merge is consistent with
e of about 10 ppm or more, and merges in many bursts are not consistent with any plausible e.
Do not use `rate_ppm`: on a one-clock box it reads about 0 by construction
(`phase-1.md:1007-1009`), whatever the crystals do. And the generator's rate against barcode
seconds, *g*, is not e on its own. Barcode seconds are the box's NTP-disciplined
CLOCK_MONOTONIC seconds, not RP1's crystal's (`wl_sync/cli.py:422-426`;
`wl_sync/rp1/backend.py:1607-1622`) and not scaled by the PIO rate, which scales only the
box's tick numbers (`wl_sync/log.py:288-294`). e = g − p, where p is RP1's rate against the
same barcode seconds, taken from the box's own `B` records. Both are against the same frames,
so NTP's frequency cancels.

To compute it, take `RATE_PPB` from the recorder's start line in its journal,
`applying the PIO rate calibration … %+d ppb` (`wl_sync/cli.py:531-540`; use 0 if the start
warned `no usable PIO rate calibration`, `:525-529`):

    grep -E "PIO rate calibration" /tmp/wl-phantom/rig0-p0.err

Then, with the run's `truth.json` and the copied log (one command, per 10-minute piece of the
truth's fit; it drops the first `B`, which is emitted at start, off the grid):

    .venv/bin/python -c "$(cat <<'EOF'
    import json,sys; from pathlib import Path; from statistics import linear_regression as lr; from wl_sync.log import iter_records, BarcodeEmitted; t=json.load(open(sys.argv[1])); r=int(sys.argv[2]); b=[(x.value,x.tick_us) for s in sys.argv[3:] for x in iter_records(Path(s)) if isinstance(x,BarcodeEmitted)][1:]; out=[]
    for lo,hi,f in t['fit'][0]: g=(t['tick_ns']*1e-9/f[1]-1)*1e6; w=[(v,k) for v,k in b if lo<=v<=hi]; p=(lr([v for v,_ in w],[k for _,k in w]).slope*(1+r*1e-9)/1e6-1)*1e6; out.append((round(g,2),round(p,2),round(g-p,2)))
    print("(generator ppm, RP1 ppm, generator fast over RP1 ppm) per piece:", out)
    EOF
    )" runs/p0-burst6/truth.json RATE_PPB $(find runs/p0-log -name 'seg-*.log' | sort)

It prints g, p and e for each piece. Tested on the fakes on 2026-11-21, with `RATE_PPB` 0: a
generator and an RP1 both at +20 ppm print e = 0.0, a generator alone at +20 prints e = +20.0,
and +30 against +10 prints +20.0. A nonzero `RATE_PPB` is not tested. Its limits: it assumes
one recorder process, as step 15 runs it; it is meaningless on a `split` domain, which
`--expect-timebase one-count` already warns of; and if an NTP step of the wall clock is logged
during the run, use only the frames after it (`wl_sync/cli.py:816-826`). The two crystals'
tolerances were not read from a datasheet **[unverified]**. None of this weakens item 6's pass
line. It means the contract's "at least 6 µs" (`wl_sync/rp1/backend.py:1017-1024`) has no
headroom, which matters to wl-xcon and MonkeyLogic.

**Open.** Items 5 and 6 need wl-phantom at `684d895` or later (its origin/main `d3b8706`,
2026-11-21; Bring these). "Confirm that the task PC holds its code ≥ 2.5 µs
**[unverified]**" (plan `:1890`; README `:519-522`). The 2.5 µs pass shows that the box reads
right when the code is held that long. Whether the task PC holds it that long is for the task
PC's own check.

**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 16. P0 item 7, which is Task 5b Step 5: one run past a counter wrap [composed]

"Run Task 5b Step 5: one run past a counter wrap (> 71.6 min) with strobes flowing. The ticks
must be continuous across the wrap, with no slope change, and the box must not hang" (plan
`:1893`; sync-box plan `:1136-1138`).

Start the recorder, then the 75-minute run within a few minutes of it. The run is longer than
the ring, so it streams while it plays (`phase-1.md:924-925`).

    ssh -t wl-rig-sync0 'wl-sync record --out /tmp/wl-phantom/rig0-wrap --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0-wrap.err'

On the Mac:

    .venv/bin/python mk_timeline.py 4500 0.5 t4500
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 \
        --timeline t4500.timeline.json --extras t4500.extras.json --out runs/wrap
    ssh $BOX 'pgrep -a wl-sync'

Then stop the recorder, copy its log to `runs/wrap-log`, and compare with `--expect-timebase
one-count`, as in step 12.

**Pass, read as [composed]:**
- `pgrep` shows the recorder still running after the run, so it did not hang.
- The comparison reads `PASSED over 1:15:00 of 1:15:00 scheduled (100%)`, with one tick
  domain. A domain longer than 20 minutes is fitted in 10-minute pieces (`phase-1.md:1232-1238`),
  so a slope change at the wrap shows as a residual past the budget.


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 17. The strobe-margin sweep

This is wl-phantom step 7, as written (`phase-1.md:1165-1253`): its own recorder,
`/tmp/wl-phantom/rig0-sweep`; then `sweep`, and `sweep-grade --expect-timebase one-count`
(`:1208-1214`).

**Pass:** "the report is written" (`:1247`). The margins are information: they become the
timing spec that wl-xcon and MonkeyLogic must meet (`:1299-1303`).


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 18. Next-session's silicon conditions, B-1 to B-5, from the runs above

These are next-session `:122-149`.
- **B-1, the FIFO's depth.** Each recorder's start-up check measures it. "the start-up INFO
  `the RX FIFO holds 16 words, measured at start-up: the code-word pairing counts on 16`
  says it passed" (next-session `:127-129`). Grep each `.err` file for it. The by-hand check
  on SM 0 has no tool: it is not run here.
- **B-2, an exact level read, and B-4, the cost of each call.** No tool exists, and they
  are not run here.
- **B-3, a failed level read** **[composed]**: count the failed level reads across the
  runs. Use `grep -c "level could not be read" /tmp/wl-phantom/*.err` on the box, and
  next-session `:140-142` for what a few `errno 5` mean.
- **B-5, nothing else pops SM 0.** During each run, `ssh $BOX 'pgrep -af "[w]l-sync|[w]l_sync"'`
  should show one recorder and nothing else **[composed]**.
- **Not on this bench:** next-session item 6, "The frame-time inputs see a high"
  (`:236-238`). It needs a camera group's strobe, wired as the rig will be.


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 19. B2: today's W–B drift, `--split`, at least 1 h, with the service stopped

"Run wl-phantom §5.5 for at least 1 h on the split backend. Report the ppm; it should match
B8's rate against B1's" (plan `:2049-2052`). It is expected to fail. It runs on `wl-sync
record --split` (the user's ruling, 2026-11-20; README `:226-252`; next-session `:266-270`).

**Where it runs.** It runs from `~/wl-sync-spike` with `lib-b3`, as the ruling says. Step 4
brought that tree up to `--split`. The command is README `:234`, with this part's `--out` and
`--listen` **[composed]**.

**Why the service is stopped.** This runs inside wl-phantom's procedure, which keeps the
bench recorder the only log in play (`phase-1.md:428-431`). It is not a PIO need. A `--fake`
process never opens the block (plan `:1921-1922`), and B1 ran `--split` beside the service
(step 8).

    ssh -t wl-rig-sync0 'cd ~/wl-sync-spike && timedatectl show -p NTPSynchronized --value && LD_LIBRARY_PATH=$HOME/wl-sync-spike/lib-b3 venv/bin/wl-sync record --out /tmp/wl-phantom/rig0-split --split --listen 0.0.0.0:8741 2>&1 | tee /tmp/wl-phantom/rig0-split.err'

**Expect three ERROR lines, by design** (README `:243-251`):
- at start: "`the stamp machine was not started (forced by --split, for the bench only):
  this process records timebase split, with B and E on CLOCK_MONOTONIC, until a restart
  without --split`";
- once a run, from the health check: "`capture degraded, still recording: timebase split
  forced by --split, for the bench only: B and E are on CLOCK_MONOTONIC in this process, not
  on RP1's count`";
- at the run's end: "`this run recorded timebase split, forced by --split, for the bench
  only: B and E were on CLOCK_MONOTONIC throughout`".

The header says `"split"` with `barcode_delay_us` `null` (README `:236-238`).

On the Mac:

    .venv/bin/python mk_timeline.py 3700 0.5 t3700
    ssh $BOX timedatectl timesync-status
    .venv/bin/wl-phantom run --generator $GEN --monitor $MON --box http://$BOX:8741 \
        --timeline t3700.timeline.json --extras t3700.extras.json --out runs/b2
    ssh $BOX timedatectl timesync-status

Stop the recorder, copy its log to `runs/b2-log`, and compare **without**
`--expect-timebase`. This box is split on purpose **[composed]**: `--expect-timebase` is for
a box that should be on one count (`phase-1.md:963-965`).

    .venv/bin/wl-phantom compare --run runs/b2 \
        --box-log $(find runs/b2-log -name 'seg-*.log' | sort) --report runs/b2-report.json

**Read:** "a rate near the NTP frequency, and a residual of milliseconds by the end" is the
split box's expected finding (`phase-1.md:979-994`).

**Record:** `rate_ppm` with `rate_se_ppm`, and both `timesync-status` frequencies. Set them
beside step 8's B1 rates and step 9's B8 r, which is what "it should match B8's rate against
B1's" asks.


**If you stop for any reason between steps 10 and 19, do step 20 (restart the service) before you leave.** A stopped box must never be left unrecording.

### 20. Put the box back

This is wl-phantom step 8, as written (`phase-1.md:1255-1265`):

    ssh $BOX 'pgrep -a wl-sync'
    ssh -t wl-rig-sync0 'sudo systemctl start wl-sync'
    curl http://$BOX:8740/session

The start is **(you, sudo)**.

**Pass:** no bench recorder is left, and the service answers "with `session_id` null"
(`:1259`). Unplug the jumpers, ground last (`:1261`).

Copy `/tmp/wl-phantom/*.err` off the box before step 21's reboot. Run on the Mac:
`rsync -a wl-rig-sync0:/tmp/wl-phantom/ ./bench-day/wl-phantom/` **[composed]**.

---

## D. B7 at a reboot

### 21. B7: NTP from a cold boot, 8 h (you, sudo)

**Source.** "From a cold boot, log `adjtimex` (`offset`, `freq`, `constant`, `status`) every
10 s for 8 h, and note any step (REALTIME−MONO). Confirm `STA_PLL` under timesyncd
**[unverified]**" (plan `:2088`).

**When.** "A cold boot is a reboot, which needs sudo, is the user's call, and restarts the
service" (plan `:1987`). Choose a time with no session open and nothing recording by hand.

**The logger.** Its probe is built (next-session `:169-170`), but a logger is not, so this
one is **[composed]**, from `wl_sync/calibration.py`'s `Timex` and `STA_PLL`. Save it before
the reboot:

```sh
ssh wl-rig-sync0 'cat > ~/b7_adjtimex.py' <<'EOF'
"""B7 (composed, not from a source): adjtimex every 10 s for 8 h, with REALTIME - MONO."""
import ctypes
import time

from wl_sync.calibration import STA_PLL, Timex

libc = ctypes.CDLL(None, use_errno=True)
print("mono_s,offset,freq,constant,status,sta_pll,realtime_minus_mono_ns", flush=True)
for _ in range(8 * 360 + 1):
    tx = Timex()
    tx.modes = 0
    libc.adjtimex(ctypes.byref(tx))
    d = time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    print(f"{time.monotonic():.1f},{tx.offset},{tx.freq},{tx.constant},{tx.status},"
          f"{bool(tx.status & STA_PLL)},{d}", flush=True)
    time.sleep(10)
EOF
```

Reboot **(you, sudo)**:

    ssh -t wl-rig-sync0 'sudo reboot'

As soon as it answers again, start the logger:

    ssh wl-rig-sync0 'nohup ~/wl-sync-venv/bin/python ~/b7_adjtimex.py > ~/b7.csv 2>&1 < /dev/null & uptime; systemctl is-active wl-sync'

**Expect:** `active`. If step 2 found the unit not enabled, start it with
`ssh -t wl-rig-sync0 'sudo systemctl start wl-sync'` **(you, sudo)**.

**After 8 h, read** `~/b7.csv`:
- whether `sta_pll` turns `True` once timesyncd has synced, which is the check README
  `:739-740` and `:752-754` wait on;
- the spread of `offset` and `freq`;
- any jump in `realtime_minus_mono_ns`, which is a step.

B7 is informational: "The spread of θ sets the gate's 1 ms and the 1 h baseline" (plan
`:2088`).

**A finding:** `sta_pll` never `True`. Then "no pair passes, and no rate file is ever
written" (README `:759-761`), and the unit can never record `"rp1-pio-si"`. That goes to the
user before step 22.

---

## E. Leaving `--fake`, and the rate file

### 22. Leaving `--fake` (you, sudo)

**What gates it.** By the user's ruling of 2026-11-20, "Both": Task 5b Steps 3–5, and the
±100 µs gate. README `:27`, next-session `:186` and plan `:2106` carry the ruling as dated
notes. That means all of these:

- **Task 5b Step 3:** step 12 passed.
- **Task 5b Step 4:** step 12's composed parity passed, and you confirmed the definition.
- **Task 5b Step 5:** step 16 passed.
- **The ±100 µs gate:** steps 12 and 13 passed.
- **P0's own pass,** since "A failure stops Task 5b and this design" (plan `:1894`): "items
  3, 5, 6 and 7". Items 5 and 6 are open until step 15's 2.5 µs and 5.5 µs runs have been played, which
  needs wl-phantom at `684d895` or later, its main (Bring these). What happens then
  is your call.

**Also confirm with wl-preproc first** that its small follow-up, which it asked for "after its
3b-2, before one count is deployed", has merged. Only the build's ledger records the ask
(`.superpowers/sdd/2026-11-07-one-clock/progress.md`, 2026-11-20). One count changes what
wl-preproc reads (`docs/pending-wl-preproc-amendments.md`, note 7).

**The edit.** It is next-session `:277` and plan `:645`: "drop `--fake` from
`deploy/wl-sync.service` and add `--gpiochip`". The chip is `gpiochip0`, by label (README
`:665-666`). The installed unit matched the repository's when last read, 2026-11-20 (the
build's ledger), so check it first:

    ssh wl-rig-sync0 'systemctl cat wl-sync | grep ^ExecStart; curl -s http://localhost:8740/session; echo'

**Pass:**
`ExecStart=/usr/local/bin/wl-sync record --out /data/rig0 --fake --listen 0.0.0.0:8740 --session-token-file /etc/wl-sync/session-token`,
and `"session_id": null`. The restart is a new clock, so make it between sessions (README
`:711-712`).

    ssh -t wl-rig-sync0 'sudo sed -i "s| --fake | --gpiochip /dev/gpiochip0 |" /etc/systemd/system/wl-sync.service && grep ^ExecStart /etc/systemd/system/wl-sync.service && sudo systemctl daemon-reload && sudo systemctl restart wl-sync'

**(you, sudo)** The `sed` is **[composed]**. Never add `--split` (README `:227-229`). Do not
add `--barcode-delay-us` (README `:35-38`).

**Pass:**
`ExecStart=/usr/local/bin/wl-sync record --out /data/rig0 --gpiochip /dev/gpiochip0 --listen 0.0.0.0:8740 --session-token-file /etc/wl-sync/session-token`.

A minute later:

    ssh wl-rig-sync0 'systemctl is-active wl-sync; journalctl -u wl-sync --since "-3 min" --no-pager | grep -E "stamp machine|RX FIFO|PIO rate|event-code capture|split"; head -1 "$(ls -t /data/rig0/unassigned/$(date -u +%F)/syncbox/seg-*.log | head -1)"'

**Pass:**
- `active`.
- "`the stamp machine saw GPIO17 rise at start-up`" (README `:136-137`).
- "`the RX FIFO holds 16 words, measured at start-up: the code-word pairing counts on 16`"
  (next-session `:127-128`).
- The WARNING "`no usable PIO rate calibration (no calibration file): ticks count RP1's
  crystal, timebase rp1-pio, until a later start finds one`" (README `:730-732`).
- No line `event-code capture cannot run`, and no line `the stamp machine's start-up test
  failed` (README `:195`, `:133`).
- The header's `"timebase": "rp1-pio"` and `"barcode_delay_us": 0` (README `:689-691`,
  `:106-111`).

**A fail:**
- **A `split` header, or either ERROR:** the process recorded split. Read the cause
  (README `:125-224`). The unit stays up, records anyway, and a restart may pass.
- **A unit that restarts every second:** one of the three fatal start-up checks failed
  (README `:219-222`).
- **To go back:** `sudo sed -i "s| --gpiochip /dev/gpiochip0 | --fake |"` on the same file,
  then the same `daemon-reload` and `restart` **(you, sudo)** **[composed]**.

**Then, in the repository:** the unit change goes into `deploy/wl-sync.service`, as plan
`:645` assigns. This runbook does not make it.

### 23. The rate file: 1 h, then restart once (you, sudo)

These are README `:712-724`:

    ssh wl-rig-sync0 'ls -l /data/rig0/.wl-sync-pio-rate; curl -s http://localhost:8740/session; echo'

Run this after at least 1 h. **Pass:** the file exists, and no session is open. If it does
not exist yet, wait: "it is first written once an hour of pairs has been taken with NTP
synchronized" (README `:715-717`).

    ssh -t wl-rig-sync0 'sudo systemctl restart wl-sync'

**(you, sudo)** Between sessions (README `:718-719`).

    ssh wl-rig-sync0 'journalctl -u wl-sync --since "-3 min" --no-pager | grep "PIO rate"; head -1 "$(ls -t /data/rig0/unassigned/$(date -u +%F)/syncbox/seg-*.log | head -1)"'

**Pass:** "`applying the PIO rate calibration written <time>: <+N> ppb, measured over <S> s
and <P> pairs; timebase rp1-pio-si`", "not the no-calibration WARNING" (README `:720-724`).
The header says `"timebase": "rp1-pio-si"`.

**B8's second day** **[composed]**: on another day, snapshot the unit's own rate file over 8 h,
as step 9 did. Replace `/tmp/wl-bench/rig0-b6` with `/data/rig0`, and write to
`~/b8-day2.jsonl`. B8's pass and its 1 ppm rule (plan `:2092-2093`) then read across both
days.

---

## F. The task PC, and afterwards

### 24. The task PC

- **MonkeyLogic's box address** is `'wl-rig-sync0.local:8740'` (`wlsync_session.m:11`,
  `:29`; README `:458-460`). If the Windows boot does not resolve `.local`, use the box's IP
  address or its tailnet name (README `:502-503`, item 11).
- **The endpoint from the task PC** (next-session Step B2, item 1, `:284-297`). Run README's
  three `curl` commands (`:422-430`), with `$TOKEN` the token. **Pass:** "Check that a
  folder `<root>/<id>/syncbox/` appears with a segment in it, that a repeated `request_id`
  returns the same id, that a wrong token gets 401, and that a close naming another id gets
  409 with `open`" (next-session `:286-288`).
- **The MonkeyLogic checklist,** README `:481-522`, items 1-18, once on the Windows boot
  against the real box (next-session `:298-303`). Item 18 is the task PC's strobe timing,
  which step 15's open point leans on.

### 25. Afterwards

- **Results.** Write each into `docs/next-session.md` Step B as a dated entry, using
  wl-phantom's checklist (`phase-1.md:1267-1325`). Add dated RESULT notes on the plan's P0,
  B1, B2, B6, B7, B8 and B10, as B3a and B3b have (plan `:2058`, `:2067`). Then commit
  wl-phantom's `phase-1.md` with its `Result:` lines filled (`phase-1.md:1262`).
- **`wl.yaml`.** Update `status` and `next`: what the box runs, its timebase, what passed,
  and what is open (items 5 and 6, if step 15 did not run them). Run `wl-check` (CLAUDE.md).
- **The repository's unit.** Make the step-22 change in `deploy/wl-sync.service` (plan
  `:645`).
- **wl-preproc.** Tell it the box records on one count, from which commit and since when,
  and the first `process_uuid`. Deliver note 8's two queued additions
  (`docs/pending-wl-preproc-amendments.md:525-545`).
- **next-session's spike commands.** next-session `:182-186` name `lib`, where spike S's
  version-3 shim sits. B3 ran on `lib-b3`, and step 4 rebuilds `lib-b3` from the deployed
  tree. A dated note at next-session `:182` says so (2026-11-20). Read `lib` as `lib-b3`
  there.
- **The spike tree.** `~/wl-sync-spike` stays until you say otherwise (plan `:2043`).
