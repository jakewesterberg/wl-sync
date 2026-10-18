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
    ls /data/rig1/unassigned/$(date -u +%F)/syncbox/    # between visits (UTC date)
    ls /data/rig1/*/syncbox/                         # one folder per visit
    cat /data/rig1/unassigned/$(date -u +%F)/syncbox/manifest.json

Segments land in `/data/rig1/unassigned/<date>/syncbox/` while no animal is in the rig,
and in `/data/rig1/<id>/syncbox/` during a visit, where `<id>` is the session id the
task PC was given (for example `2026-10-18_01`). Each folder has its own manifest.

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

Replace `$TOKEN` with the token. Read, open and close:

    curl http://wl-sync-rig0.local:8740/session

    curl -X POST http://wl-sync-rig0.local:8740/session/open \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"request_id": "visit-attempt-0001"}'

    curl -X POST http://wl-sync-rig0.local:8740/session/close \
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
MonkeyLogic already puts there). Check with `which wlsync_session`.

**The token.** Put the same token as the task PC's in a text file on the Windows boot, for
example `C:\wl\session-token`, readable only by the operator's account (remove inherited
permissions in the file's Security tab, or `icacls C:\wl\session-token /inheritance:r /grant:r %USERNAME%:R`).
Never commit it, and never paste it into a script. The function reads it with
`strtrim(fileread(...))` and never prints it.

**Where to call it.** Call `wlsync_session('open', box, token_file)` when a session starts and
`wlsync_session('close', box, token_file)` when it ends, with `box` like `'wl-sync-rig0.local:8740'`.
NIMH MonkeyLogic is believed to run a user `alert_function.m` with session-level cases (task
start and end), which would be the likely place. **Confirm this against the installed
MonkeyLogic version on bench day**, including the exact case names and whether an error
raised there stops the start. If MonkeyLogic offers no such hook, call the function from
whatever script launches the run.

**Use the returned id.** `id = wlsync_session('open', ...)` names MonkeyLogic's data file or
folder for the visit (for example `2026-10-18_02`), so wl-preproc can pair it with the box's
`<id>/syncbox/`.

**A failed open must stop the session from starting.** The function raises an error on every
failure, carrying the box's own message and HTTP status, and `open` retries only what is safe
(network failure, timeout, 503) using one `request_id`. Do not catch the error and carry on,
and never type an id by hand: the box mints every id, and a made-up one pairs with nothing.

**Bench-day checklist**, once on the Windows boot against the real box:

1. `wlsync_session('show', box)` returns `''` with nothing open (and the JSON null does not break it).
2. `id = wlsync_session('open', box, tf)` returns an id like today's `_01`, and `curl .../session` agrees.
3. `wlsync_session('show', box)` now returns the same id.
4. `wlsync_session('close', box, tf)` returns that id; `show` returns `''` again.
5. Retry with a lost reply: open, then drop the network for a moment (or point at a slow link) so the
   first reply is lost. Exactly one session must result, not two. A hang is a possible outcome,
   because `ConnectTimeout` bounds connecting only: if MATLAB hangs, stop MonkeyLogic, then
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
11. Name resolution: the Windows boot resolves `wl-sync-rig0.local`. If not, use the box's IP
    address or its tailnet name as `box`.
12. A 409: with curl, close a session id that is not the open one; the box answers 409 with `open`, which the function would show as `[the open session is ...]`.
13. `wlsync_session('close', box, tf)` with nothing open errors "nothing to close".
14. `wlsync_session('bogus', box)` errors naming `'open'`, `'close'` and `'show'`.
15. Open while a session is already open: the new id is returned and a `wlsync:autoclosed`
    warning names the session the box force-closed.

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
7. **Optionally, join the tailnet**, so the box is reachable from a different subnet,
   where `.local` names do not reach. `wl-stack/catalog/tailscale.md` is the lab's entry;
   on Debian, from Tailscale's own repository:

       curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
       curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
       sudo apt-get update && sudo apt-get install -y tailscale
       sudo systemctl enable --now tailscaled
       sudo tailscale up --hostname=wl-sync-rig0

   `tailscale up` prints a login link, which a person must open. Then:
   - **disable key expiry** for the node in the admin console. Otherwise it expires after
     180 days by default, and a box running unattended quietly drops off;
   - leave Tailscale SSH off, because key-only `sshd` already works.

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

This is what first ran the RP1 backend on silicon, on `wl-sync-rig0` on 2026-10-18.

1. **Build tools and libgpiod's command-line tools:**

       sudo apt-get install -y --no-install-recommends build-essential git gpiod python3-venv python3-dev

2. **piolib, compiled into this repository's shim.** piolib's API is almost entirely
   `static inline`, so `ctypes` cannot call it directly; `pio/wlsync_pio.c` explains why.
   The shim includes piolib's sources, so nothing else needs installing:

       git clone --depth 1 https://github.com/raspberrypi/utils.git ~/src/utils
       git clone https://github.com/jakewesterberg/wl-sync.git ~/src/wl-sync
       ~/src/wl-sync/pio/build-shim.sh ~/src/utils/piolib /tmp
       sudo install -m 0644 /tmp/libwlsync_pio.so /usr/local/lib/ && sudo ldconfig

   `/dev/pio0` should already be `root:gpio` with mode `0660`, and the first user is in
   `gpio`. If it is `root:root`, piolib's README gives a udev rule.
3. **wl-sync, with its `pi` extra:**

       python3 -m venv ~/wl-sync-venv
       ~/wl-sync-venv/bin/pip install -e ~/src/wl-sync[pi]

4. **Confirm the GPIO chip by label.** `gpiodetect` should list `gpiochip0
   [pinctrl-rp1] (54 lines)`, which is `--gpiochip`'s default.
5. **Run the hardware mode by hand**, as your own user rather than root:

       ~/wl-sync-venv/bin/wl-sync record --out /tmp/wl-bench/rig0 --duration 30

   With nothing wired to the pins, the run should give:
   - 30 consecutive barcode values;
   - no edge or word records;
   - a clean trailer, and a manifest with no gaps or overlaps;
   - no `capture degraded` lines.

   On the first run, every barcode after the first was 1 s apart to within 147 µs.

## Before first boot

- Fit the CR2032 to the CM5 IO Board. Nothing on the board can detect its absence.
- Configure NTP. `timedatectl show -p NTPSynchronized` must report `yes`.
- **Important:** the unit runs `--fake` until bench acceptance (see above). The hardware
  capture is wired but unproven, while the crash-stitching mechanism is ready to verify
  at bring-up.
