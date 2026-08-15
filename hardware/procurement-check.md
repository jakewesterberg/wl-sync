# Breakout board — BOM audit and procurement check

Task 13 (BOM lock and pre-layout design review). This is the file the implementation plan's
own Task 0 ("Unblock procurement") was always going to produce
(`docs/superpowers/plans/2026-08-13-breakout-pcb.md`, Track 0) — Task 0 is human-led and was
not run separately before Task 13 started, so its scope (distributor stock, lead time, and a
per-part MPN/manufacturer record) is folded in here rather than skipped. Companion files:
`hardware/breakout/breakout-bom.csv` (the locked BOM export) and
`hardware/breakout/design-review.md` (the pre-layout sign-off checklist, which references
this file for every "stock confirmed" line item).

**Checked:** 2026-08-15, against DigiKey (primary), cross-checked against Mouser, Arrow, RS
Components, and Distrelec where DigiKey's own listing was incomplete or absent. Stock is a
snapshot — anything ordered later should be re-checked, especially the items flagged below.

**Board quantity assumed:** spec §10.1's fab run of 5 (2 production + 1 hand-assembled + 2
spares) — 5 boards' worth of active parts, unless noted.

---

## 1. How the BOM was produced, and two tooling findings worth recording

```
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN,Manufacturer,Qty" \
  --group-by Value -o hardware/breakout/breakout-bom.csv hardware/breakout/breakout.kicad_sch
```

This is the exact command specified for Task 13, run unmodified — `hardware/breakout/
breakout-bom.csv` is its literal, unedited output (101 grouped rows). Auditing it line by
line surfaced two things about the command itself, not the design, worth recording so the
next person who copies that command from a brief doesn't repeat them:

- **`MPN` and `Manufacturer` are blank on every row.** Confirmed by grep: no
  `hardware/breakout/sheets/*.kicad_sch` file, nor `breakout.kicad_sch` itself, contains a
  single `property "MPN"`. `kicad-cli` can only export a field that exists on the schematic
  component — these two were never added as properties by any generator. This is not a data
  loss (the schematic never carried this data to begin with), but it means
  `breakout-bom.csv`'s own MPN/Manufacturer columns are not a substitute for §4 below; this
  file is. Adding `MPN`/`Manufacturer` properties to every symbol would touch every sheet's
  `.kicad_sch` — out of scope for an audit task that does not modify schematics.
- **`Qty` is blank on every row too, for a different reason: it is the wrong token.**
  `kicad-cli sch export bom --help` names the generated field `QUANTITY` ("can be specified
  with or without `${}` delimiters"); `Qty` is only the *default label* `kicad-cli` prints
  above that column when both `--fields` and `--labels` are left at their own defaults
  together. Overriding `--fields` with the literal string `Qty` (as directed) asks for a
  component property named `Qty`, which — like `MPN`/`Manufacturer` — no symbol carries, so
  it exports empty. Confirmed by re-running with `--fields
  "Reference,Value,Footprint,MPN,Manufacturer,\${QUANTITY}"` against the same schematic: every
  row gets a correct integer. `hardware/breakout/breakout-bom.csv` is left exactly as the
  specified command produces it (unmodified, per-row Qty column blank); the corrected
  per-line quantities the specified command was evidently meant to produce are in §2 below,
  computed from the `QUANTITY`-token re-export, not hand-counted.

**A BOM without quantities is not fully usable for ordering.** That is what makes this worth
flagging rather than silently working around: anyone re-running the brief's own command verbatim
gets the same blank column. Recorded here as a documented BOM-export gotcha; §2 below supplies
the missing numbers for this lock.

## 2. Corrected per-line quantities (all 101 rows) and DNP visibility

Every row in `breakout-bom.csv`, cross-referenced against a `QUANTITY`-token re-export so the
count is tool-computed (KiCad expanding its own `R5-R23` range notation), not manually
tallied from the CSV's comma/dash-compressed Reference strings.

**DNP is invisible in the locked CSV as well** — `DNP` was not in the requested `--fields`
list either, and `kicad-cli sch export bom` includes DNP parts by default (no `--exclude-dnp`
was passed). Five components are genuinely Do-Not-Populate — `opto-ni.kicad_sch`'s documented
NI-side isolated-5V fallback (populated only if bench measurement shows the 3.9 kΩ pull-up
budget in spec §8.1 doesn't hold): **U62** (`TMA-0505S`), **C135**, **C136** (the fallback's
own input/output bulk caps, folded into the "10uF" row's total of 14 — 12 are real),
**FB3** (folded into the "600R@100MHz" row's total of 3 — 2 are real), and **R170** (the
single `0` Ω bridge that shorts the fallback's footprint onto `NI_5V` when it is not
populated — this is the only 0 Ω part on the board, which is how it was identified). None of
this is visible from `breakout-bom.csv` alone; call it out here so assembly doesn't populate
5 positions that are deliberately empty on every board built to this lock.

| Value / description | Real per-board qty | DNP within this row | ×5 boards |
|---|---:|---|---:|
| 10uF (bulk) | 14 | 2 DNP (C135, C136) → 12 real | 60 real |
| 100nF | 119 | — | 595 |
| 10nF | 4 | — | 20 |
| 3.3pF / 6.8nF / 1.2nF / 1.0nF / 1.5nF / 220pF / 220nF | 1–2 each | — | ×5 |
| SS14 | 3 | — | 15 |
| BAT54S | 36 | — | 180 |
| 600R@100MHz ferrite | 3 | 1 DNP (FB3) → 2 real | 10 real |
| M12A_5 | 1 | — | 5 |
| MDR68 (4 distinct connector roles) | 4 | — | 20 |
| BNC (29 real footprint instances; §5 below) | 29 | — | 145 |
| Conn_02x03 (misc ÷1/÷2 shunt headers) | 3 | — | 15 (+3 shorting blocks/bd, not in this BOM — see §5) |
| Resistors, all values combined | 189 | 1 DNP (R170) → 188 real | 940 real |
| U1 LD1117S33TR_SOT223 | 1 | — | 5 |
| U2 IH1215D | 1 | — | 5 |
| U3 TPS7A4901 / U4 TPS7A3001 | 1 each | — | 5 each |
| U5–U7,U14 SN74LVC541APW | 4 | — | 20 |
| U8–U11,U15 SN74HCT541PW | 5 | — | 25 |
| U12 SN74HCT32D / U13 SN74HCT14D | 1 each | — | 5 each |
| U16–U21,U24–U30,U39,U41,U43,U45,U47,U49,U51,U53 INA105KU | 21 | — | 105 |
| U22 OPA2197IDR / U23 OPA4197IDR | 1 each | — | 5 each |
| U31–U37 OPA4192IDR | 7 | — | 35 |
| U38,U40,U42,U44,U46,U48,U50,U52 ADG1206YRUZ | 8 | — | 40 |
| U54 LM339 | 1 | — | 5 |
| U55 MCP4728 | 1 | — | 5 |
| U56–U61,U64 ACSL-6400-00TE | 7 | — | 35 |
| U62 TMA-0505S | 1 | **fully DNP** | 0 populated |
| U63 LD1117S50TR_SOT223 | 1 | — | 5 |
| U65 ACSL-6420-00TE | 1 | — | 5 |
| U66 MCP2221A | 1 | — | 5 |
| U67, U68 MCP23017 (0x20 / 0x21) | 1 each | — | 5 each |

## 3. Value-overridden symbols — verified against the actual part being bought

Every stock-symbol-with-overridden-Value instance found in the schematic sources, checked
against what its Value string names versus what the symbol library entry is called:

| Symbol placed | Value / MPN in schematic | Real part it names | Verdict |
|---|---|---|---|
| `Amplifier_Operational:OPA4197xD` (analog-ni.kicad_sch, ×7) | `OPA4192IDR` | TI OPA4192IDR — 36 V, precision, e-trim quad op-amp, real current part, same SOIC-14 pinout as OPA4197 | **Correct, deliberate override.** No stock KiCad symbol exists for OPA4192 (checked directly by the generator's own author against the library); OPA4197xD is pin-compatible and is used only as a drawing stand-in. This is the exact "OPA4192 on an OPA4197xD symbol" case — verified consistent across all 7 placements (`U31`–`U37`), same Value string every time |
| `Amplifier_Operational:OPA4197xD` (analog-frontend.kicad_sch, ×1, mic channel) | `OPA4197IDR` | TI OPA4197IDR — the actual OPA4197, no substitution | **Correct, no override** — Value names the same part the symbol is |
| `Amplifier_Operational:OPA2197xD` (analog-frontend.kicad_sch, TIA, ×1) | `OPA2197IDR` | TI OPA2197IDR | **Correct, no override** |
| `Amplifier_Difference:INA105KU` (analog-frontend.kicad_sch ×13, mux-intan.kicad_sch ×8) | `INA105KU` | TI INA105KU — real stock KiCad symbol, name matches Value exactly | **Correct, not an override at all.** `INA105KU` is a genuine entry in KiCad's own `Amplifier_Difference` library (confirmed directly in the installed library file); this part substitutes for the plan's originally-named `INA134` (no stock symbol, audio-specific part) at the *part-selection* level, documented on-sheet — but the symbol-vs-Value pairing itself has no mismatch. See §4 for a real, separate finding on this part: the bare order code is obsolete |
| `wl-sync:ADG1206YRUZ` (mux-intan.kicad_sch, ×8) | `ADG1206YRUZ` | Analog Devices ADG1206YRUZ | **Correct** — hand-built symbol (no stock equivalent), Value matches |
| `Comparator:LM339` (comparators.kicad_sch, ×1) | `LM339` | Generic TI/ON/ST LM339 family | Symbol/Value consistent, but see §4 — `LM339` alone is a device family, not an orderable SKU |
| `Analog_DAC:MCP4728` (comparators.kicad_sch, ×1) | `MCP4728` | Microchip MCP4728 | Symbol/Value consistent; see §4 — bare `MCP4728` also needs an ordering suffix |
| `wl-sync:ACSL-6400` / `ACSL-6420` (opto-ni.kicad_sch ×7, opto-intan.kicad_sch ×1) | `ACSL-6400-00TE` / `ACSL-6420-00TE` | Broadcom ACSL-6400-00TE / ACSL-6420-00TE | **Correct** — full orderable MPN, hand-built symbols (no stock equivalent) |

**No disagreements found.** Every override in this schematic names the part actually being
bought, not the symbol borrowed to draw it. The `74xx`-family buffers (`SN74LVC541APW`,
`SN74HCT541PW`, `SN74HCT32D`, `SN74HCT14D`), the two LDOs (`LD1117S33TR_SOT223`,
`LD1117S50TR_SOT223`), `TPS7A4901`/`TPS7A3001`, and `IH1215D`/`TMA-0505S` all follow the same
pattern already established elsewhere in this project (hardware/README.md's "541-family
Value-substitution precedent") and were checked the same way — symbol library entry vs. Value
string vs. what the part actually is — with no discrepancy found.

## 4. Real finding: bare order codes vs. the currently-orderable SKU

Distinct from §3 (symbol vs. Value): several Value strings are the right *part*, but not a
directly orderable *SKU* — the base part number without a packaging suffix. For most of
these (`LM339`, `MCP4728`, `ADG1206YRUZ`) this is normal, deferred-to-purchasing practice
(tube vs. tape-and-reel is a quantity-dependent decision). For two of them it is not
deferrable, because the bare part itself is discontinued:

| Value as written | Status of that exact string | Active equivalent | Note |
|---|---|---|---|
| `INA105KU` (×21) | **Obsolete / no longer manufactured** (TI, confirmed directly on TI's and DigiKey's own product pages) | `INA105KU/2K5` — Active, same die/package/pinout, tape-and-reel only | 21/board × 5 = 105 needed; `INA105KU/2K5` has 3,085 in stock at DigiKey (§5) — no supply problem, but the literal string in the schematic cannot be ordered as written |
| `SN74HCT14D` (×1) | **Obsolete / no longer manufactured** (TI) | `SN74HCT14DR` — Active, tape-and-reel | Only 1/board; trivial to resolve at order time, flagged for completeness |
| `LM339`, `MCP4728`, `ADG1206YRUZ` | Not obsolete — generic/base part numbers without a packaging suffix | `LM339AD`/`LM339DR` (TI); `MCP4728-E/UN` (Microchip); `ADG1206YRUZ` (tube) or `-REEL7` (reel) | Normal; purchasing selects packaging by order volume. `ADG1206YRUZ`'s own reel variant has a real stock problem independent of the suffix question — see §5 |

None of this is a schematic defect (the symbol correctly represents the part in every case;
see §3) and none of it is being fixed here — it is recorded so procurement orders the
tape-and-reel variant for `INA105KU` and `SN74HCT14D` specifically, not the string printed in
the BOM.

## 5. Distributor stock and lead time — the parts flagged unverified, plus every other active component

DigiKey unless noted. "Active" = manufacturer lifecycle status, not a comment on stock.

### 5.1 Explicitly flagged unverified in the task brief

| Part (as ordered) | Qty/bd ×5 | Distributor | Stock | Mfr. lead time | Status | Note |
|---|---:|---|---:|---:|---|---|
| `INA105KU/2K5` (not bare `INA105KU` — see §4) | 21 × 5 = 105 | DigiKey | 3,085 | 9 weeks | Active | Bare `INA105KU` obsolete; reel variant covers the whole run with margin |
| `ADG1206YRUZ-REEL7` | 8 × 5 = 40 | DigiKey | **0** (450 due 2026-11-25, 550 due 2026-12-16) | 11 weeks | Active | **Flag — see §6.** Arrow separately showed 8 units of the bare (non-reel) part in stock, ships next day — enough for roughly one board, not five |
| `LM339AD` | 1 × 5 = 5 | DigiKey | In stock, ships same day | — | Active | Trivial volume, common part, no risk |
| `MCP4728-E/UN` | 1 × 5 = 5 | DigiKey | In stock, ships same day | — | Active | No risk. Package remains the documented MSOP-10/0.5 mm exception (spec §10.1) — confirmed directly in the BOM's own Footprint column, no other part on the board shares that pitch |
| `ACSL-6400-00TE` | 7 × 5 = 35 | DigiKey | 8,037 | — (ships today) | Active | No risk |
| `ACSL-6420-00TE` | 1 × 5 = 5 | DigiKey | 733 | 17 weeks | Active | Current stock covers this order by a wide margin; the 17-week figure is only relevant if DigiKey's own stock is drawn down by other customers before this order is placed |

### 5.2 Connectors — the schedule risk named in the brief

| Part | Qty/bd ×5 | Distributor | Stock | Mfr. lead time | Note |
|---|---:|---|---:|---:|---|
| MDR68 male right-angle (MH Connectors `3700-0121-01`) | 4 × 5 = 20 | **Not found at DigiKey or Mouser** in this check | RS Components (stock #813-3313) and Distrelec both list it as a current, orderable part | Not obtained — RS/Distrelec fetches were blocked (403/timeout) in this pass | **Flag — see §6.** This is the connector the spec's own §10.2 already named as the widest error bar (4–8 weeks if unstocked); its absence from the two most commonly-used US distributors in this search is consistent with that, not a surprise. A direct account-based stock check at RS or Distrelec (or a search for a pin-compatible alternate MDR68 MPN) should happen before the production order, not at fab time |
| Isolated BNC, right-angle, PCB-mount (Amphenol RF family, e.g. `031-6575`/`031-6576`) | 29 × 5 = 145 | DigiKey | Multiple compatible MPNs, 1,000+ units each ("Immediate") | — | No risk — several real, current, well-stocked isolated right-angle BNC part numbers exist in this exact family; exact MPN is still a layout-stage decision per this project's own established convention (footprint is generic `BNC_PanelMountable_Vertical`) |
| `M12A-05PFFP-SF8001` (5-pos inlet) | 1 × 5 = 5 | DigiKey | 811 | 15 weeks | Re-confirmed independently this session — same 811-unit figure hardware/README.md already recorded at Task 7. Current stock covers the run; 15-week figure only matters if DigiKey depletes first |
| 3.5 mm TRS, PCB mount (remote reward jack) | 1 × 5 = 5 | DigiKey | Several Switchcraft/Same Sky options in stock (e.g. `SJ1-3523NG`) | — | No risk. **But see §6** — the schematic currently represents this position as a generic 2-pin header placeholder, not a real TRS footprint |
| `Conn_02x03` + 2-gang shorting block (misc ÷1/÷2 jumpers) | 3 headers + 3 blocks per bd | — | Generic 2.54 mm pin-header family, multiple manufacturers (Sullins, TE, Amphenol, Adam Tech) | — | No risk — this is a commodity part class; not individually spot-checked beyond confirming the family is common, since the design's own footprint/pad-adjacency requirement (README's "Contact arrangement" discipline applied to this connector) is already independently enforced by `check_breakout_analog_frontend_netlist.py`'s `verify_footprint_pad_adjacency()`, not a sourcing question |

### 5.3 Other active components (specialty analog / power)

| Part | Qty/bd ×5 | Distributor | Stock | Mfr. lead time | Note |
|---|---:|---|---:|---:|---|
| `OPA4192IDR` | 7 × 5 = 35 | DigiKey | 884 | 12 weeks | Covers the run |
| `OPA4197IDR` / `OPA2197IDR` | 1 ea × 5 | DigiKey | Listed, real current parts | — | Low volume, no risk |
| `TPS7A4901DGNR` / `TPS7A3001DGNR` | 1 ea × 5 | DigiKey | Mixed signal — one listing showed "ships today," another showed "backorder" for `TPS7A4901DGNR` specifically | Not confirmed precisely | Low volume (5 each); worth a direct re-check at order time given the conflicting signal, but not schedule-threatening at this quantity |
| `IH1215D` (isolated ±15V DC-DC, sole supply for the whole Intan-isolated domain) | 1 × 5 = 5 | XP Power / DigiKey / Mouser | **Could not conclusively confirm the exact order code** | — | **Flag — see §6.** The part exists as a real KiCad stock symbol (confirmed directly in `Converter_DCDC_Isolated.kicad_sym`) with a matching electrical description, and XP Power's IH family does include a 12V-in/dual/±15V/2W member — but a direct fetch of XP Power's own current IH-series product page listed the 12V-input dual-output part as `IH1215S`, not `IH1215D`, while the "D" (dual-output) suffix convention is independently confirmed correct for XP Power's *IA* series (`IA1215D`, a real, listed, DigiKey-stocked part) and for two other IH-series members found directly (`IH0515D`, `IH2415D`). This is a genuine, unresolved conflict between sources, not a confident finding either way |
| `TMA-0505S` | DNP (§2) | DigiKey | In stock, ships today | — | Fallback footprint only — not populated on any board built to this lock, no procurement action needed unless bench testing changes that |
| `LD1117S33TR` / `LD1117S50TR` | 1 ea × 5 | DigiKey (ST) | In stock, ships today | — | No risk |
| `SN74LVC541APW` / `SN74HCT541PW` / `SN74HCT32D` / `SN74HCT14D` | 4/5/1/1 × 5 | DigiKey (TI) | Listed, real current parts; `SN74HCT14D` bare code obsolete (§4) | — | High-volume commodity logic; only the §4 suffix issue is a real finding |
| `SS14`, `BAT54S` | 3/36 × 5 | DigiKey (multiple manufacturers: onsemi, MCC, Diotec, Nexperia, SMC, TSC) | Tens of thousands of units across manufacturers | — | No risk — true jellybean parts |
| `MCP23017` (×2), `MCP2221A` | 1 ea × 5 | DigiKey (Microchip) | Listed, real current parts, multiple package-suffix variants in stock | — | No risk |

### 5.4 Passives (resistors, capacitors)

Not individually itemized — every value used (§2) is a standard E24/E96 0603/0805/1206
ceramic capacitor or thick-film resistor, available from 5+ manufacturers each (Yageo,
Murata, Kemet, Samsung, TDK, Vishay, Panasonic). This is the class of part a turnkey
assembler's own library covers without a special order, consistent with spec §10.1's own
"hand-solderable... exist in a turnkey assembler's parts library" framing. No individual
stock check performed; flag if a specific value turns out to be non-standard at layout time
(none found here — see §7's package/passive-floor confirmation).

## 6. Items over 4 weeks, or otherwise schedule-threatening — called out explicitly

Per-instruction: anything over 4 weeks changes the schedule (spec §10.3 has no slack for a
respin) and must be flagged here, not buried in a table.

1. **`ADG1206YRUZ-REEL7` — 0 units in stock at DigiKey, 11-week manufacturer lead time**, next
   restock not until late November/December 2026. 8 needed per board, 40 for the full 5-board
   run. This is the one part in this audit with a *current, numeric, zero-stock* finding at a
   major distributor, not just a long catalog lead time behind healthy stock — order this one
   first, or confirm Arrow/Mouser/an alternate distributor can actually supply 40 units before
   the schedule assumes otherwise.
2. **MDR68 (`3700-0121-01`) — not found at DigiKey or Mouser**, the two most commonly used US
   distributors; confirmed listed (but stock quantity not obtained) at RS Components and
   Distrelec. This is exactly the risk spec §10.2 already named ("4–8 weeks if no distributor
   holds stock") and it is not resolved by this check — it needs a direct account-based stock
   query, not a search-engine snippet, before the production order.
3. **`IH1215D` — exact XP Power order code unresolved** (§5.3). Low quantity (5 units total)
   makes this cheap to resolve and low-risk to the schedule by itself, but it is the sole
   supply for the entire Intan-isolated domain (a single point of failure), so getting the
   order code right matters more than the unit count suggests. Resolve directly with XP Power
   or a distributor part-number lookup, not by inference, before ordering.
4. **`M12A-05PFFP-SF8001` (15 weeks) and `ACSL-6420-00TE` (17 weeks)** — both manufacturer
   lead times exceed 4 weeks, but both are already covered by DigiKey's own current stock
   (811 and 733 units respectively) against a 5-unit order each. Flagged per instruction; not
   schedule-threatening at this quantity unless that stock is drawn down before ordering.
5. **`INA105KU/2K5` (9 weeks)** and **`OPA4192IDR` (12 weeks)** — both exceed 4 weeks, both
   comfortably covered by current DigiKey stock (3,085 and 884 units) against this run's needs
   (105 and 35). Flagged per instruction, not schedule-threatening as things stand.

Everything else checked in §5 either ships same-day from stock or is a high-volume commodity
part with no meaningful lead time.

## 7. Package and passive-floor confirmation (spec §10.1's hand-solder constraint)

Checked directly against `breakout-bom.csv`'s own Footprint column, not assumed:

- **No BGA or QFN anywhere** — confirmed (`grep -i "bga\|qfn"` across the full BOM: no hits).
- **No passive finer than 0603** — confirmed (every `Resistor_SMD`/`Capacitor_SMD` footprint
  is `_0603_1608Metric`, `_0805_2012Metric`, or `_1206_3216Metric`; nothing at 0402/0201).
- **Nothing finer than TSSOP/0.65 mm except the documented `MCP4728` exception** — confirmed:
  every non-SOIC package on the board is capped at 0.65 mm pitch (`SN74LVC541APW`/
  `SN74HCT541PW`'s TSSOP-20, `ADG1206YRUZ`'s TSSOP-28, `TPS7A4901`/`TPS7A3001`'s HVSSOP-8, all
  P0.65mm); `MCP4728` alone sits at 0.5 mm (MSOP-10) — the sole 0.5 mm-pitch part on the
  board, matching spec §10.1's own "MCP4728... finer than anything else on this board" note
  exactly.
- **A minor footprint-consistency note, not a constraint violation:** the "10uF" bulk
  capacitor is placed in two different sizes depending on which sheet: `C_0805_2012Metric` on
  `power.kicad_sch` (Task 7), `C_1206_3216Metric` on `opto-ni.kicad_sch`/`opto-intan.kicad_sch`
  (Task 11) — both are ≥0603 and both are real, correct choices for a 10uF ceramic at their
  respective voltage/DC-bias context, but a single grouped BOM line ("10uF", `--group-by
  Value`) now spans two physically different parts, visible in `breakout-bom.csv`'s own
  Footprint column as a comma-joined pair. Not a defect; worth a design-review glance so
  whoever places parts at layout doesn't assume one footprint for all 14 references.
- Similarly, the `600R@100MHz` ferrite bead's Footprint column shows two variants
  (`L_0805_2012Metric` and the `_HandSolder` pad variant) for the same reason — both are the
  correct part in two different footprint drawings, not a mismatch.

## 8. Sources consulted

DigiKey product pages (direct fetch, this session): INA105KU, INA105KU/2K5,
ADG1206YRUZ-REEL7, ACSL-6420-00TE, M12A-05PFFP-SF8001. DigiKey/Mouser/Arrow/RS/Distrelec/XP
Power/TI/Microchip/Analog Devices/Broadcom search results (this session, 2026-08-15) for every
other part in §5. hardware/README.md's own "Custom connector footprints" section (M12 stock
figure corroboration, Task 7's own prior check). `docs/superpowers/plans/2026-08-13-breakout-pcb.md`
(Task 0's own original scope, folded in here).
