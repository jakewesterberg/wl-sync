# Task-PC line-out and the PFI copy of `PD1_COMP` — proposal

**APPROVED and DRAWN 2026-10-18**, with D3 reversed while drawing (§3). Originally
proposed the same day, before it was drawn. It implements spec §12 item 11, committed to wl-xcon
(XC-327) for before layout. Four choices below (D1–D4) need approval before the generators
change. Every fact cites its primary source; nothing here is read off a model.

wl-xcon's engine plays sound through two of the task PC card's analog outputs. The output is
started by the task patch's next edge (its engine spec §9.1). That needs two things from this
board:

- **a line-out** for the two outputs;
- **`PD1_COMP` on a pin the card can use as a start trigger.**

---

## 1. Facts this rests on

Sources:
- **[M]** NI X Series User Manual 370784K-01 (May 2019), read 2026-10-18.
- **[S]** NI PCIe-6343 Specifications 377873B-01 (May 2026), read 2026-10-18.

| Fact | Source |
|---|---|
| AO 0 and AO 1 are Connector 0 pins 22 and 21; AO GND is pins 55 and 54 | [M] Figure A-5 |
| Both of the card's connectors are cabled into this board, so the AO pins arrive here and nowhere else | spec §9.2–9.3 |
| AO: ±10 V, 0.2 Ω output impedance, ±5 mA drive | [S] Analog Output |
| *"All three ground references—AI GND, AO GND, and D GND—are connected on the device"* | [M] Table 3-1 "I/O Connector Signals", AO GND and D GND rows |
| All sixteen PFI lines are on Connector 0, and none on Connector 1 | [M] Figure A-5; spec §9.2's 2026-10-18 note |
| Every PFI line is some counter's default terminal; **PFI 12, 13, 14, 15 are the default counter OUTPUTS**, and PFI 14 is also FREQ OUT | [M] Table 7-9 |
| Digital input: V_IH 2.2–5.25 V, V_IL 0–0.8 V, 50 kΩ pull-down | [S] Digital I/O |
| `PD1_COMP` reaches the task PC today on P0.27 (Connector 1 pin 46), which cannot be an AO Start Trigger | `gen_breakout_taskpc_digital.py:1104`; [M] §5 |
| The back face has 25.2 mm of slack; one more dual BNC body costs 18.0 mm | `panel-elevations.md` §2 |
| The task-PC outbound buffer is 8/8 full; `pi-interface`'s level-shift `SN74LVC541A` on +3V3 uses 1 of 8 | `gen_breakout_taskpc_digital.py`; `frame-time-inputs.md` §7 |

---

## 2. The line-out

**One new isolated dual BNC body on the back face**, the board's existing part:

| | Port A | Port B |
|---|---|---|
| Centre | AO 0 (left), through a series resistor | AO 1 (right), through a series resistor |
| Shell | AO GND (pins 55, 54) | AO GND |

**The shells go to AO GND and to nothing else on the board.** NI ties AO GND to D GND on the
card itself, so the amplifier's ground meets the card's ground whatever this board does. What
the board controls is whether the audio return current crosses its own planes. With isolated
shells (spec §9.1) wired only to the AO GND pins, it never does. The current goes out on one
cable conductor and back on another.

**No clamp diodes.** The board's rails are ±12 V on AGND. Clamping the AO lines to them would
join the card's AO domain to this board's analog ground, which the shells exist to avoid.

**Series resistor: 100 Ω** *(D4)*. It protects the AO from a shorted cable, and isolates the
coax capacitance from the card's output stage. Into a line input of 10 kΩ or more, it costs
under 1 % of amplitude. This mirrors the 47 Ω the board already puts in series with every
output that drives coax (finding F4).

**The panel cost:** 25.2 mm of back-face slack becomes about 7 mm. After this, the back face
has no room for any further connector.

---

## 3. The PFI copy of `PD1_COMP`

**`PD1_COMP` → a spare channel on `pi-interface`'s `SN74LVC541A` → 100 Ω → Connector 0
pin 38, which is PFI 7 (P1.7).** P0.27 stays exactly as it is.

- **Why PFI 7** *(D2)*. It avoids the four default counter outputs (PFI 12–15). If wl-xcon ever
  starts a counter task with default routing, the card drives that output pin, and this board
  would be driving it at the same moment. PFI 7 is CTR 3's AUX by default, which is an input,
  on the counter least likely to be in use. **wl-xcon must route `ao/StartTrigger` from
  `/Dev/PFI7` and must never configure PFI 7 as an output.** That goes in the reply.
- **Why that buffer.** It is the only package on the board with channels free (7 of 8).
  `PD1_COMP` is an open-collector comparator output pulled to +3V3, so a 3.3 V buffer is its
  natural reader. The buffer's output against the card's inputs:
  - high: 3.3 V against V_IH min 2.2 V, a 1.1 V margin;
  - low: about 0.2 V against V_IL max 0.8 V.

  One more CMOS input on `PD1_COMP` adds no load worth counting.
- **The return path** *(D3)*. Connector 0's D GND pins stay no-connect, so the PFI edge
  returns through Connector 1's D GND, in the parallel cable. **Reversed while drawing,
  2026-10-18.** This proposed tying pin 36 to DGND, so the return ran beside the signal.
  But Connector 0 already has AISENSE on AGND, so that would have made it the second
  component on the board, after the star-point net tie NT1, to touch both grounds.
  `check_breakout_power_netlist.py` caught it. It is not an electrical bond, because
  AISENSE is a high-impedance input on the card. But the star-point invariant is worth
  more than a slightly tighter return loop for a line that changes only at stimulus
  rate.
- **What spec §9.2 gives up.** One digital line now rides the analog cable, the thing the
  connector split was arranged to avoid. It is accepted for a single line that changes at
  stimulus rate, not continuously.

---

## 4. Alternatives considered

| Instead | Why not |
|---|---|
| A 3.5 mm stereo jack for the line-out *(D1)* | Smaller, one connector, and what consumer amplifiers take. But it is a second connector type on a board that standardised on BNC, and the 3.5 mm TRS was removed once already (spec §9.6). A BNC-to-RCA or BNC-to-3.5 mm adapter at the amplifier costs a few dollars. **Recommended: BNC**, unless the amplifier is known to want 3.5 mm. |
| Change Detection on P0.27 as the start trigger | NI lists it as a legal source, and it needs no copper, but NI gives no latency figure and nobody has tested it (spec §12 item 11). It stays available as a fallback. |
| Move `PD1_COMP` from P0.27 to the PFI pin | P0.27 is hardware-timed digital input; PFI is static only (spec §9.2). wl-xcon reads `PD1_COMP` in its control loop as well, so keep both. |
| A new `SN74AHCT541` package for the copy | It would work, but the LVC package's spare channel is free and already the right logic level. A new package is board area for nothing. |

---

## 5. What drawing it will touch

- **`gen_breakout_taskpc_digital.py`** (Connector 0 lives here): AO 0/1, AO GND and PFI 7
  wired; the new BNC body; two 100 Ω resistors. Fix its note that puts P2 on Connector 1
  at the same time.
- **`gen_breakout_pi_interface.py`:** one more channel on the level-shift `SN74LVC541A`, from
  `PD1_COMP` to a new `PD1_COMP_PFI` net, with its 100 Ω.
- **The netlist checkers** for both sheets, `netlist-contract.json`, and the BOM: one dual BNC,
  three resistors.
- **`panel-elevations.md`**: back face 10 bodies, slack about 7 mm. **Spec** §9.1, §9.6 and
  §12 item 11.
- **The wl-xcon handover:** name PFI 7, and state the never-an-output rule.

---

## 6. Decisions needed

| # | Decision | Proposed |
|---|---|---|
| D1 | Line-out connector | One isolated dual BNC body, back face |
| D2 | PFI line for the `PD1_COMP` copy | PFI 7 (Connector 0 pin 38) |
| D3 | Tie Connector 0's D GND pin 36 to DGND | ~~Yes~~ **No: reversed while drawing (§3)** |
| D4 | Series resistance on each AO and on the PFI copy | 100 Ω |
