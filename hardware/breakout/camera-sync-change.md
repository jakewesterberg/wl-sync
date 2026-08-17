# Camera lines: triggers → barcode sync

Decided and implemented 2026-08-16, in design phase, before layout.

**The decision.** The cameras **free-run**. They are not clocked by this box, so they do not
need a trigger — they need a shared **timebase they can record**, which is what the barcode
already is. The five camera-facing panel BNCs now carry `BARCODE_RAW` instead of the
hardware-PWM triggers from GPIO18/19.

**What it cost on the board: nothing.** Same buffers, same 47 Ω series resistors, same five
BNCs, same panel. Only the *input net* of five buffer channels moved. No new parts, no new
panel holes, no refdes disturbed.

---

## 1. What changed

| Before | After |
|---|---|
| GPIO18 → `CAM_TRIG_EYE_RAW` → U15.A3 → `CAM_TRIG_EYE_BUF` → R198 → BNC | `BARCODE_RAW` → U15.A3 → `CAM_SYNC_EYE_BUF` → R198 → BNC |
| GPIO19 → `CAM_TRIG_BEH_RAW` → U15.A4, U74.A2/A3/A4 → 4× BNC | `BARCODE_RAW` → same four channels → `CAM_SYNC_BEH1..4_BUF` |
| GPIO18, GPIO19 — hardware PWM, camera triggers | **spare** (no-connect), and PWM-capable |
| GPIO26, GPIO27 — spare | spare (unchanged) |

Nets renamed `CAM_TRIG_*` → `CAM_SYNC_*`, because the old names would have described a clock
the lines no longer carry. On this project a name that is right about the model and wrong
about the board is the recurring failure — see `parametric-audit.md`'s closing note.

**Spare GPIO count goes 2 → 4, and two of the four are hardware-PWM capable.**

## 2. The consequence that is bigger than the change

Spec §4 justifies the **PIO capture window starting at GPIO0** like this:

> *PIO parallel capture reads a contiguous pin range, and camera triggers must land on a
> hardware PWM pin (12, 13, 18, 19). Every 17-wide contiguous window inside GPIO2–27 contains
> 12 and 13, and only the window starting at 2 leaves even one PWM pin free — insufficient
> for two triggers. Starting at 0 consumes 12 and 13 while leaving 18 and 19 free.*

**That constraint is now gone.** No camera line needs a PWM pin, so the window no longer has
to start at 0.

Why that matters: GPIO0/1 are in the capture window *only* because it had to start at 0, and
they are the two pins the Pi probes as I²C at boot looking for a HAT ID EEPROM. That is why
they carry 330 Ω series resistors and why `force_eeprom_read=0` is a required `config.txt`
setting. **Move the window to GPIO2–18 and that whole mitigation disappears.**

**It does not, however, free pins — it costs one.** A 17-wide window that avoids both 18 and
19 must end at 17 or below, so it must start at 0 or 1, and both of those windows contain
GPIO0 and/or GPIO1. Escaping the I²C pins while keeping two free PWM pins is not on the
menu; it is a forced trade:

| | Current (0–16) | Proposed (2–18) |
|---|---|---|
| Spare GPIOs | **4** (18, 19, 26, 27) | **3** (19, 26, 27) |
| …of which PWM-capable | 2 | 1 |
| GPIO0/1 | in-window: 330 Ω ×2 + `force_eeprom_read=0` | out of window; Pi *outputs* only |

So the real argument for moving is **not pins** — it is deleting the `force_eeprom_read=0`
dependency, an invisible rig-config setting no checker can see, the same species as the
CONFIG4 switch F2 was praised for retiring. That is a narrower claim than "two more free
pins", which was simply wrong.

**Not done, and now recommended against.** Moving the window re-maps 17 event lines across
`taskpc-digital` and `pi-interface` to net one *fewer* spare pin, and §3's frame-time inputs
need two of the spares that remain. Recorded anyway because it is far cheaper to settle in
design phase than after layout, and because the *reason* spec §4 gives for the GPIO0 window
no longer exists — so anyone reading that justification later would be reading one that has
expired, whichever way the window lands.

## 3. What this change does NOT solve

**Frame times are no longer known by construction.** Spec §12 item 1 dropped camera
exposure-active returns as Pi inputs, and the stated justification was *"the Pi triggers the
cameras so frame times are known by construction, and the trigger-count-versus-frames check
still runs off the camera sidecar."* Free-running cameras make both halves false: the box
does not know frame times, and there are no triggers to count frames against.

The intended replacement, per the rig design being sketched (FLIR USB cameras with a DIO
data logger alongside, custom software):

- The **logger** captures camera strobes and the barcode on the same timebase.
- The **barcode** ties the logger's timebase to this box's, once per second.
- Frame times therefore reach the analysis **as data**, not as a wire.

**This is why frame times do not need to be forwarded to NI in hardware**, which is fortunate:
spec §9.2 records that **NI connector 1 has zero spare hardware-timed lines at 24**. There is
no capacity to add a frame-time line without either mixing digital onto the analog cable or
dropping an existing signal. Since `BARCODE_NI` already puts the same barcode into the NI
recording, NI and the camera logger share a timebase without any additional wire.

**Taken up, 2026-08-16.** Frame times *were* wanted in hardware at the Pi, and GPIO26/27 now
carry them — one exposure-strobe input per camera group, on the two spare back-panel BNC
ports, through a second LM339 on `comparators.kicad_sch`. The strobe cannot drive a
`74LVC541A` input directly (its best published low is 0.87 V against a 0.8 V V_IL max — a
silent 70 mV miss), which is why it takes a comparator rather than this board's usual inbound
buffer. See `frame-time-inputs.md`. The remaining two spares are 18/19, both PWM-capable.

This does not replace the logger path above; it adds a hardware timestamp alongside it.

## 4. Plan and spec changes this implies

Not yet applied — these are decisions, listed for review:

| Document | Change |
|---|---|
| Spec §4 GPIO map | 18, 19 → spare (were ohDPI / behaviour camera trigger). Note 4 spares, 2 PWM-capable |
| Spec §4 capture-window rationale | The PWM constraint expired; either re-justify GPIO0–16 on other grounds, or record it as now-optional (see §2) |
| Spec §9.1 panel inventory | "Camera triggers | BNC | 5" → camera **sync/barcode** lines |
| Spec §9.4 | "only two hardware PWM pins survive the contiguous capture range" no longer constrains anything |
| Spec §12 item 1 | Its justification is void. Restate: frame times come from the camera logger, aligned by barcode |
| Spec §6 | Eye channels at "500 Hz — camera frame rate" is now the **camera's** rate, not one this box sets |
| Task 15 panel drawings | BNC labels: "CAM TRIG" → "CAM SYNC" |
| `wl_sync` | Stop driving hardware PWM on 18/19. `backend.py`'s PWM entry point becomes unused |

## 5. Verification

13/13 checkers, 121 tests, ERC 0 errors / 3 pre-existing warnings, BOMs re-synced.

One negative control was **retired rather than repaired**, and the reason is worth recording.
`check_breakout_pi_interface_netlist.py` had a control that swapped two camera buffer
channels' outputs to catch a permutation. Both channels now take the same input, so swapping
two outputs carrying an identical signal is electrically a no-op — **the defect it guarded
against stopped existing**, rather than moving. Keeping it would have been a control that
passes vacuously, which this project has now hit six times. It was replaced with one that
corrupts finding F4's property instead: two camera BNCs sharing one driver pin, which is
still a real defect (118 mA on every edge against a 25 mA absolute maximum) and does not care
whether the signal is a trigger or a barcode.
