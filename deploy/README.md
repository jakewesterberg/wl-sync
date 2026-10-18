# Deploying the sync box

    sudo cp deploy/wl-sync.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now wl-sync

Edit `--out` in the unit to the rig's session root before enabling. Its last component
becomes `rig` in every log header, so `/data/rig0` for a box called `wl-sync-rig0`.

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

    wl-sync record --out /data/rig1 --gpiochip /dev/gpiochipN

Once Task 5b Steps 3–5 pass, drop `--fake` from `ExecStart` and add that `--gpiochip`.

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

## Setting up the OS, once per box

Done this way for `wl-sync-rig0` on 2026-10-18, with no screen and no NVMe adapter. It
ended running Raspberry Pi OS Lite, Debian 13 "trixie", kernel `6.18.50+rpt-rpi-2712`, from
the NVMe.

1. **Write Raspberry Pi OS Lite (64-bit) to a USB drive** with Raspberry Pi Imager. Use
   Raspberry Pi OS, not another distribution: PIO reaches Raspberry Pi's own kernel first
   (`docs/superpowers/plans/2026-08-13-sync-box.md`). In Imager's settings:
   - hostname `wl-sync-rigN`;
   - a user, with SSH by public key only;
   - no Wi-Fi.
2. **Boot from the USB drive, with the SD slot empty.** The CM5's `BOOT_ORDER=0xf2461`,
   read right to left, tries SD, then NVMe, then USB, then network. A blank or
   non-bootable NVMe therefore falls through to USB.
3. **Give it a network that hands out addresses.** On a direct cable with no DHCP,
   NetworkManager gives up and takes the link down, so the box answers for a few seconds
   and then vanishes. Use the lab network, or a Mac's Internet Sharing over its Ethernet
   adapter, which puts the box on 192.168.2.x. Then `ssh <user>@wl-sync-rigN.local`.
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

Two things from that first setup to know about:

- **Its root steps used a temporary passwordless-`sudo` drop-in,**
  `/etc/sudoers.d/090-wl-sync-setup`. Delete it when bring-up is done, because a box left
  running unattended for years should not keep passwordless root.
- **It dropped off three times while being set up, cause not found.** Power read clean
  every time it could be checked, and it was not heat. The journal now persists, so after
  a repeat, `journalctl -b -1 -n 50` shows whether the previous boot ended in a clean
  shutdown or just stopped.

## Before first boot

- Fit the CR2032 to the CM5 IO Board. Nothing on the board can detect its absence.
- Configure NTP. `timedatectl show -p NTPSynchronized` must report `yes`.
- **Important:** the unit runs `--fake` until bench acceptance (see above). The hardware
  capture is wired but unproven, while the crash-stitching mechanism is ready to verify
  at bring-up.
