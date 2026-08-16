# Handoff — implementing the parametric audit

Written 2026-08-16 at the end of the audit session, for whoever implements the findings.

**Read first:** `parametric-audit.md` (20 findings, reasoning, numbers) and
`../datasheet-params.toml` (19 parts, 18 pinned to primary documents). Those are complete.
This file records only what was NOT written down there — decisions in progress, traps found,
and the implementation order.

---

## 1. A workflow trap that will bite you

**The checkers and the contract generator read `hardware/breakout/breakout.net`, which is
gitignored and easily stale.**

During this session they ran against a nine-hour-old netlist, reported confident results
about a schematic that no longer existed, and gave *both* false passes and false failures.
The regenerated sheets were correct; the tools were looking at the wrong file.

Always, after editing a generator:

```bash
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"
python3 hardware/gen/gen_breakout_<sheet>.py
kicad-cli sch export netlist --format kicadsexpr \
    -o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch
python3 hardware/gen/gen_breakout_netlist_contract.py
for c in hardware/gen/check_*.py; do python3 "$c" >/dev/null || echo "FAIL $c"; done
python3 -m pytest tests/ -q
```

Note the checkers *default* to `/tmp/breakout.net` while the contract generator defaults to
`hardware/breakout/breakout.net`. **Two different paths.** Export to both, or pass the path
explicitly. This is worth fixing properly — a single source of truth with a freshness guard
would remove a whole class of wasted debugging.

## 2. F1 must land as one change — this was tried and reverted — **DONE 2026-08-16**

> **Implemented, both halves together.** See `parametric-audit.md`'s own F1 entry for what
> shipped and how it was verified. Three things this section predicted turned out slightly
> differently, recorded because the differences are the useful part:
>
> - **The `"expected '430'"` self-test trap is real and it fired**, exactly as warned — on
>   `check_taskpc_digital_netlist.py` rather than only on the opto checkers. Its node-count
>   control asserted on `"expected exactly 4 nodes"`, and F1 moves that count to 5. It is now
>   pinned to the invariant part of the message (`"nodes (series R, clamp diode"`) rather than
>   to the number, so the next count change does not re-break it.
> - **9 packages was right, for different arithmetic.** "30 LEDs plus ~12 other outputs"
>   over-counts: 2 of the 30 are not buffer-driven (an ACSL-6420 drives one; one is an
>   unpopulated spare) and there are 8, not ~12, other outputs. But F1 also *adds* a 29th
>   buffer-driven LED, because `RHS_STIM_OUT`'s LED has to move onto a buffered leg once the
>   resistor changes. 29 × 2 + 8 = 66 channels = 9 packages, 6 spare.
> - **The refdes hazard was worse than "check `build()`'s baseline comment".** It was not
>   latent: regenerating either opto sheet — which F1 *requires* — renumbered its entire
>   refdes space before any F1 edit was made. Both are now pinned. Five other generators are
>   still armed; see the table at the end of `parametric-audit.md`'s F1 entry.
>
> The rest of this section is left as written, as the record of what was known going in.

The resistor half alone was implemented, verified (12/12 checkers passed, correct values in
both sheets) and then **reverted**, because `test_driver_pin_sink_load` correctly rejected it:
12.65 mA against a 7.5 mA per-output budget.

Neither half is shippable alone. 430 Ω starves the LED below its switching threshold; 249 Ω
overloads the driver. Both halves together:

**(a) Two resistor values, not one.** `+5V` reaches the LED through `F4` and `D3` (~0.42 V of
drop); `ISO_5V` is regulated locally with nothing in series. A single value on both would push
the ISO_5V channels to 15.1 mA, past the absolute maximum.

| Rail | Rs | worst / typ / best |
|---|---|---|
| `+5V` (30 LEDs) | **249 Ω** | 9.0 / 11.3 / 13.4 mA |
| `ISO_5V` (2 LEDs) | **301 Ω** | 8.8 / 10.7 / 12.5 mA |

Assumes the external +5 V supply specified at **±2%** — that tolerance is now load-bearing and
belongs in the spec beside the current rating.

In `gen_breakout_opto_intan.py`, `opto_channel()` already receives `led_hi` as a parameter, so
selection is `LED_R_OHMS_ISO if led_hi == "ISO_5V" else LED_R_OHMS_MAIN`. The matching checker
needs the same rail-aware expectation, and its **self-test negative controls** assert on the
complaint text (`"expected '430'"`) so they must be updated too, or they fail as
"wrong complaint".

**(b) Parallel two buffer outputs per LED.** Drops per-output current to ~6.3 mA and package
ground current to ~44 mA against a 75 mA absolute maximum. Needs **9 `SN74AHCT541` packages
against today's 5**. The driving buffers live on `taskpc-digital.kicad_sch` (`U8`–`U11`) and
`pi-interface.kicad_sch` (`U15`) — this is a cross-sheet change.

> **Refdes hazard.** This project has a known stale-`ref_start` bug: an out-of-band edit on one
> sheet silently renumbers refdes on others at the next regeneration. It was found and pinned
> during the panel-instrumentation task. Allocating four new packages must not disturb existing
> references — check `build()`'s own baseline comment in each generator before minting refs.

## 3. The fuse architecture — decided in principle, not implemented

Finding F7 says `F1` (fan) sits downstream of `F2` (main +12 V) at **identical ratings**
(0.5 A hold / 1.0 A trip), so there is no selective coordination — a fan fault at 0.9 A is
below `F1`'s trip but over `F2`'s hold, and `F2` can trip first, killing the analog rails to
protect the fans.

Two options were worked through:

- **Preferred: give the fan branch its own reverse-polarity diode and tap it from `P12_RAW`.**
  Then `F1` and `F2` are genuinely parallel branches. `F2` carries only 97–121 mA (3.7× margin
  at 0.5 A, unchanged) and a fan fault cannot reach the analog rail at all. Costs one `SS14`.
- Alternative: keep the tap at `+12V` and upsize `F2` to 1.5 A so its trip sits above `F1`'s.
  No new part, but it loosens protection on the main rail to 1.5 A hold against a 100 mA load.

**Not yet resolved:** `F4` (+5 V) must rise from 0.5 A — the rail carries 392–495 mA and the
part holds ~380 mA derated. **A 1.1 A PPTC may not exist in 1206.** If it doesn't, the options
are a larger package (which would be this board's *third* hand-solderability exception, and the
project's global constraint permits exactly two) or **splitting +5 V into two fused branches**
using the already-qualified `1206L050/15YR` — one for the ~340 mA LED supply, one for the
~155 mA logic/regulator branch. The split also buys fault isolation between them. Check
Littelfuse's 1206L range before deciding.

Note the existing generator comment at `gen_breakout_power.py` already flagged that fan current
passes through `F2` and that ±12 V was never independently totalled. It worried about *margin*;
the coordination problem is the part it missed.

## 4. Panel layout — numbers ready, drawings not redrawn

Amphenol `031-6575` ports are **16.00 mm apart, stacked vertically**, so two rows of duals fit
a 2U face (58 mm of 82 mm usable). That halves the horizontal span versus one row:

| Face | Content | Width |
|---|---|---|
| Front | 4×2 duals = 80 mm · 2× MDR68 at 63.86 mm = 128 mm · 2× 60 mm fans ≈ 130 mm · button ≈ 20 mm | **≈ 358 mm** |
| Back | 4×2 duals = 80 mm · 2× MDR68 = 128 mm · M12 ≈ 20 mm · CM5 cutouts ≈ 45 mm | **≈ 273 mm** |

Against ~450 mm usable between rack ears, both have real margin. At 16 mm centres adjacent
mating plugs nearly touch — normal for stacked duals, but plugs mate one at a time.

**Footprint warning:** KiCad's `BNC_Amphenol_031-6575_Horizontal` is **self-contradictory** —
described as dual, it draws a single body with one bayonet circle and four clustered pads. Do
not use it. Use `BNC_Win_364A2x95_Horizontal`'s geometry, which matches the Amphenol drawing's
actual pattern (4 × Ø0.89 signal, 2 × Ø2.01 ground) and the Winchester family convention.

## 5. Implementation order, and why

Dependencies are real here — doing these out of order means redoing arithmetic.

1. **Power chain** — fuse architecture and the fan tap. Everything downstream depends on the
   rail drop. *(Though note: the LED resistor was checked against both fuse options and moves
   only 0.2 mA, so F1 is not actually blocked on this.)*
2. ~~**F1 as one change** — two resistor values plus output paralleling across 9 packages.
   Largest single change in the audit.~~ **DONE 2026-08-16** — see §2 above. Note it landed
   *before* step 1 rather than after, which the parenthetical below already allowed for; the
   fuse choice moves LED current by ~0.2 mA and 249 Ω clears both bounds either way.
3. **Isolated domain** — `LD1117S50` → second `TMA-0505S` (F5). Pin `tma_0505s` in
   `datasheet-params.toml` first; it is the one entry still outstanding.
4. **Analog corrections** — TIA `R38` 1 MΩ → 180 kΩ and `C48` 3.3 pF → 22 pF (F3, M1), mux
   retap to `A_PD1_NI_BUF`/`A_PD2_NI_BUF` (M2, free), DAC to internal reference gain 1 (D1),
   comparator pull-ups 10 kΩ → 2.2 kΩ (M4).
5. **Connectors** — rebuild the MDR68 footprint from the MH drawing (current one has a 0.5 mm
   drill against a required 0.85 mm), delete `M12A_5_Panel` and replace with a 5-way header,
   build the BNC footprint, redraw the panel elevations.
6. **Parametric checkers** — every limit they need is already in `datasheet-params.toml`.

## 6. The one-line lesson

`test_driver_pin_sink_load` already checked that LED current does not **exceed** what the
driver can sink. Nobody checked that it **reaches** what the optocoupler needs to switch. Half
a two-sided constraint was tested, and the untested half was wrong on 30 channels for
seventy-three commits.

When writing the checkers in step 6, write both bounds.

> **Done for F1's own family of checks (2026-08-16), in `tests/hardware/test_netlist.py`.**
> Five new assertions, each with a negative control: worst-case LED current clears the 8 mA
> guardbanded floor; best-case stays under the 15 mA absolute maximum; per-pin sink divides
> across paralleled drivers; per-**package** ground current against ±75 mA; paralleled outputs
> have tied inputs. They read limits from `hardware/datasheet-params.toml` rather than
> re-typing them, so a datasheet revision propagates.
>
> Two things worth carrying into the remaining checkers. **The lower-bound check needs the
> series drops in front of the rail**, or it is just as wrong as the sizing that caused F1 —
> `+5V` at 249 Ω passes a naive 5.00 V model by a wide margin and only becomes tight once
> `F4` and `D3` are in it. And **a control that asserts on a complaint's message text will
> rot**; assert on the invariant part of the message, not on a number the finding is about to
> change.
