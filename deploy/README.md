# Deploying the sync box

    sudo cp deploy/wl-sync.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now wl-sync

Edit `--out` in the unit to the rig's session root before enabling.

## Checking it

    systemctl status wl-sync          # is it running
    journalctl -u wl-sync -f          # what is it saying
    cat /data/rig1/$(date +%F)_01/manifest.json

The manifest is the thing to read. `gaps` lists spans where this box was **not
watching** — trials in those spans really happened and were recorded by the other
devices, but carry no sync-box coverage.

`clock_trusted: false` on a segment means gap arithmetic across that boundary is
unreliable. The commonest cause is a missing CR2032 on the CM5 IO Board; see
`hardware/assembly-checklist.md`.

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
