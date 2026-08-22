# Deploying the sync box

    sudo cp deploy/wl-sync.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now wl-sync

Edit `--out` in the unit to the rig's session root before enabling.

**The unit ships in fake mode on purpose.** `ExecStart` carries `--fake`, because
until the RP1 backend (Task 5b) lands `wl-sync record` rejects any other mode and
exits 2 — a unit without it goes to `failed` within about five seconds of
`systemctl enable --now`. Everything except the hardware capture is real in this
mode, so crash stitching, the manifest and clock degradation can all be verified at
bring-up. Remove the flag when the backend lands.

## Checking it

    systemctl status wl-sync          # is it running
    journalctl -u wl-sync -f          # what is it saying
    cat /data/rig1/$(date +%F)_01/manifest.json

The manifest is the thing to read. `gaps` lists spans where this box was **not
watching** — trials in those spans really happened and were recorded by the other
devices, but carry no sync-box coverage.

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
| `clock_behind_checkpoint` | the clock is more than 60 s behind where this box had already counted to. It moved backwards or stalled by more than an ordinary NTP correction |
| `checkpoint_exhausted` | the saved counter sits at the 32-bit ceiling — corruption, or the year 2156. Seen only alongside `clock_before_epoch`; the box restarts from the epoch rather than refusing to start |
| `ntp_unsynchronized` | `timedatectl` reports NTP has never synced |
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

A box stuck restarting will show as repeated start entries in `journalctl -u wl-sync`,
so an operator knows what a crash loop looks like as distinct from a healthy run.

## Before first boot

- Fit the CR2032 to the CM5 IO Board. Nothing on the board can detect its absence.
- Configure NTP. `timedatectl show -p NTPSynchronized` must report `yes`.
- **Important:** only `--fake` backend is available until the RP1 backend lands in a
  later task; the hardware capture will not work yet, but the crash-stitching mechanism
  is ready to verify at bring-up.
