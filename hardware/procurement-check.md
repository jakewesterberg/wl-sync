# Breakout board — BOM audit and procurement check

Task 13 (BOM lock and pre-layout design review). This is the file the implementation plan's
own Task 0 ("Unblock procurement") was always going to produce
(`docs/superpowers/plans/2026-08-13-breakout-pcb.md`, Track 0) — Task 0 is human-led and was
not run separately before Task 13 started, so its scope (distributor stock, lead time, and a
per-part MPN/manufacturer record) is folded in here rather than skipped. Companion files:
`hardware/breakout/breakout-bom.csv` (the locked BOM export, unedited `kicad-cli` output),
`hardware/breakout/breakout-bom-order.csv` (fix round 1 — the same 101 rows plus a corrected,
orderable part-number column; see `hardware/README.md` for which of the two files to purchase
from), and `hardware/breakout/design-review.md` (the pre-layout sign-off checklist, which
references this file for every "stock confirmed" line item).

**Checked:** 2026-08-15, against DigiKey (primary), cross-checked against Mouser, Arrow, RS
Components, and Distrelec where DigiKey's own listing was incomplete or absent. Stock is a
snapshot — anything ordered later should be re-checked, especially the items flagged below.
**Fix round 1 (same date):** three findings below (`ADG1206YRUZ-REEL7`, MDR68, `IH1215D`) were
re-checked against additional distributors and the primary XP Power datasheet after a reviewer
found the original framing overstated two risks and flagged one non-issue; each corrected
entry below states explicitly what changed and why. Full fix-round detail:
`.superpowers/sdd/2026-08-13-breakout-pcb/task-13-report.md`'s own "Fix round 1" section.

**Board quantity assumed:** spec §10.1's fab run of 5 (2 production + 1 hand-assembled + 2
spares) — 5 boards' worth of active parts, unless noted.

**Addendum, 2026-08-17 — fan headers (spec §9.5), added to `power.kicad_sch` after this
file's own Task 13 lock.** Eight new component references not present in the locked
101-row `breakout-bom.csv`/`breakout-bom-order.csv`: four `Connector_Generic:Conn_01x03`
chassis fan headers (`J52`–`J55`), one polyfuse (`F1`, Littelfuse `1206L050/15YR`), one
more each of the already-tracked "10uF" and "100nF" rows (`C147`, `C148` — the fan feed's
own local bulk capacitance on `FAN_12V`, after the fuse), and a second net tie (`NT2`,
ties `FAN_RTN` to `DGND` at exactly one point — the same discipline as `NT1`'s own
AGND/DGND star point). Two more new references, `#PWR9`/`#PWR10` (both `PWR_FLAG`), carry
no footprint and are not physical components at all.

**Resolved 2026-08-15 — both CSVs regenerated against this exact delta.** Re-running §1's
own export command folds the eight new references in as **five** new grouped rows, not
six: `kicad-cli` groups by Value, so `C147` (Value `10uF`) and `C148` (Value `100nF`) fold
into their respective existing rows' Reference lists rather than adding new ones, and
`NT2` — sharing the literal Value string `NetTie_2` with `NT1` — folds into that same row
too, becoming `"NT1,NT2"`. Only `F1`, `J52`, `J53`, `J54`, and `J55` come out as genuinely
new rows. `#PWR9`/`#PWR10` need no entry in either CSV: confirmed directly against the
regenerated export that `kicad-cli sch export bom` emits no row for either (power-flag
symbols carry no footprint and aren't placed on the board). Net result: `breakout-bom.csv`
101 → 106 grouped rows, re-exported with the unmodified §1 command and diffed
byte-identical against a fresh run of it; `breakout-bom-order.csv` updated to match,
row-for-row, using the same Order Code/Note convention every pre-existing row of the same
component class already uses (`GENERIC` treatment for the fan headers, matching `J7`/`J18`;
a real Order Code for `F1`, matching every other fully-sourced part; folded into `NT1`'s
own row for `NT2`, matching how that row already treats net-tie artwork as non-purchasable).
Cross-checked by set comparison over both files' expanded Reference columns: 265
references each side, zero only-in-raw, zero only-in-order.

- **`Connector_Generic:Conn_01x03` × 4/board = 20** (`PinHeader_1x03_P2.54mm_Vertical`
  footprint). Same commodity 2.54mm pin-header family as the `Conn_02x03` MISC shunt
  headers already covered in §5.2's last row, and the same generic, not-locked-to-one-MPN
  treatment `breakout-bom-order.csv` already gives `J7`/`J18`. **Checked 2026-08-15,
  direct DigiKey product-page fetches (not search-snippet level):** two real,
  current-production, Active-lifecycle 3-position/0.100"(2.54mm)/through-hole SKUs from
  Sullins alone confirm the family is real and in production — `PRPC003SFAN-RC` (83 units
  in stock, 4-week mfr lead) and `PRPC003SAAN-RC` (0 in stock at check time, 4-week mfr
  lead). Per-SKU stock swings low-to-zero the way any one tape-and-reel variant of a
  fragmented commodity class can; the class itself is carried by at least five
  manufacturers (Sullins, TE, Molex, Amphenol, Würth) across dozens of interchangeable
  plating/tail-length SKUs. No supply risk — same conclusion this document already reaches
  for every other generic 2.54mm header on this board, now backed by a direct distributor
  check rather than assumed. Mates with any 3- or 4-pin PC/Noctua-class fan plug (standard
  pinout: pin 1 GND, pin 2 +12V, pin 3 tach — pin 3 deliberately unconnected on this board,
  spec §9.5).
- **`F1` polyfuse, Littelfuse `1206L050/15YR` × 1/board = 5.** Real, current, well-stocked
  PPTC resettable fuse: 500 mA hold / 1 A trip / 15 V max / 100 A max fault-interrupt
  rating, 1206 (3216 metric) package, `cURus`/`TUV` approved. **34,076 units in stock at
  DigiKey** (checked 2026-08-17, direct product-page fetch), Active lifecycle. **Re-checked
  2026-08-15, same method (direct DigiKey product-page fetch): 122,476 units in stock,
  13-week manufacturer lead time, still Active** — stock is a snapshot and moved up
  between the two checks, same part and distributor both times. No risk under either
  reading — both readings are far above any 4-week threshold at the 5-unit volume this run
  needs.
- **10uF bulk / 100nF small × 1 more each = 5 more each.** Same `C_0805_2012Metric`/
  `C_0603_1608Metric` footprints and jellybean sourcing as every other instance of these
  two rows (§5.4) — folds into the existing "no individual stock check" treatment, not a
  new part class.
- **`NT2` net tie × 1/board = 5.** Same `NetTie:NetTie-2_SMD_Pad2.0mm` footprint as `NT1`,
  which §2/§5.2 already cover as PCB artwork rather than a purchasable part — folds into
  `NT1`'s own existing BOM row rather than adding a new one, since both share the literal
  Value `NetTie_2`.

**The external supply's +12 V requirement grows by ~240 mA** (spec §9.5: four fans at
~0.06 A each, on the same rail the M12 inlet's own +12 V pin already carries — see
§5.2's `M12A-05PFFP-SF8001` row). This is a load added to the +12 V rail the external
brick must source, not a new BOM line of its own; recorded here so whoever specs or
re-quotes the external supply sees the corrected figure rather than the pre-fan-header
one.

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
was passed). **Seven** components are genuinely Do-Not-Populate, across two independent
groups (count corrected 2026-08-15: this section previously said five, having missed the
comparator sheet's own two; both counts are now tool-derived from `(dnp yes)` in the
committed sheet sources rather than tallied by hand).

*Group 1 — `opto-ni.kicad_sch`'s documented NI-side isolated-5V fallback* (populated only if
bench measurement shows the 3.9 kΩ pull-up budget in spec §8.1 doesn't hold): **U62**
(`TMA-0505S`), **C135**, **C136** (the fallback's own input/output bulk caps, folded into the
"10uF" row's total of 14 — 12 are real), **FB3** (folded into the "600R@100MHz" row's total
of 3 — 2 are real), and **R170** (the single `0` Ω bridge that shorts the fallback's
footprint onto `NI_5V` when it is not populated — this is the only 0 Ω part on the board,
which is how it was identified).

*Group 2 — `comparators.kicad_sch`'s fourth, unpopulated comparator channel* (`A_MISC1` →
`A_MISC1_COMP`, a populate option rather than a respin): **R121** (`10k`, that channel's
pull-up, folded into the "10k" row) and **R120** (`1M`, its hysteresis feedback, folded into
the "1M" row). The LM339's own `+`/`-` input pins for this channel are real, permanent wires
and the package is stuffed regardless — only these two resistors are unstuffed. Note that
populating them is **not** sufficient to use the channel: `A_MISC1` is ±5 V and the LM339
now runs from +12 V and AGND, so that channel also needs an input offset network ahead of the
comparator's `+` pin. See the on-sheet note and `gen_breakout_comparators.py`.

None of this is visible from `breakout-bom.csv` alone; call it out here so assembly doesn't
populate 7 positions that are deliberately empty on every board built to this lock.

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
| Resistors, all values combined | 189 | 3 DNP (R170, R120, R121) → 186 real | 930 real |
| U1 LD1117S33TR_SOT223 | 1 | — | 5 |
| U2 IH1215D | 1 | — | 5 |
| U3 TPS7A4901 / U4 TPS7A3001 | 1 each | — | 5 each |
| U5–U7,U14 SN74LVC541APW | 4 | — | 20 |
| U8–U11,U15 SN74AHCT541PW | 5 | — | 25 |
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
`SN74AHCT541PW`, `SN74HCT32D`, `SN74HCT14D`), the two LDOs (`LD1117S33TR_SOT223`,
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
| `SN74AHCT541PW` (×5, U8–U11/U15) | Not confirmed obsolete, but DigiKey's own listing for this exact string surfaces only the `-PWR` tape-and-reel SKU — unlike `SN74HCT541PW` (the part it replaced on this same 5 refdes, §5.3), which DigiKey lists directly as bare `-PW` | `SN74AHCT541PWR` — Active, tape-and-reel, DigiKey | Same "confirm packaging at order time" class as the row above, not the confirmed-discontinued class of `INA105KU`/`SN74HCT14D`; recorded because the swap changed which SKU string is the right one to search for |

None of this is a schematic defect (the symbol correctly represents the part in every case;
see §3) and none of it is being fixed here — it is recorded so procurement orders the
tape-and-reel variant for `INA105KU` and `SN74HCT14D` specifically, and confirms packaging
for `SN74AHCT541PW`, not just the string printed in the BOM.

## 5. Distributor stock and lead time — the parts flagged unverified, plus every other active component

DigiKey unless noted. "Active" = manufacturer lifecycle status, not a comment on stock.

### 5.1 Explicitly flagged unverified in the task brief

| Part (as ordered) | Qty/bd ×5 | Distributor | Stock | Mfr. lead time | Status | Note |
|---|---:|---|---:|---:|---|---|
| `INA105KU/2K5` (not bare `INA105KU` — see §4) | 21 × 5 = 105 | DigiKey | 3,085 | 9 weeks | Active | Bare `INA105KU` obsolete; reel variant covers the whole run with margin |
| `ADG1206YRUZ-REEL7` | 8 × 5 = 40 | DigiKey | **0** at DigiKey (450 due 2026-11-25, 550 due 2026-12-16) | 11 weeks (DigiKey factory) | Active | **Corrected, fix round 1 — see §6.** DigiKey's own zero is real but not representative of the part overall: Arrow lists **713** of the exact `-REEL7` SKU in stock, LCSC lists **86**, and Octopart's own cross-distributor aggregate shows roughly 255,894 units across authorized channels — all three search-result-level, not independently re-fetched from each distributor's own live page. (Supersedes this document's earlier "Arrow separately showed 8 units of the bare non-reel part" — that figure did not reflect the actual `-REEL7` SKU.) Not scarce. Pin-compatible fallback if this ever tightens: ADI's own `ADG5206` (`ADG5206BRUZ`, same 28TSSOP pinout), ~4,000 units at DigiKey itself, also search-level |
| `LM339AD` | 1 × 5 = 5 | DigiKey | In stock, ships same day | — | Active | Trivial volume, common part, no risk |
| `MCP4728-E/UN` | 1 × 5 = 5 | DigiKey | In stock, ships same day | — | Active | No risk. Package remains the documented MSOP-10/0.5 mm exception (spec §10.1) — confirmed directly in the BOM's own Footprint column, no other part on the board shares that pitch |
| `ACSL-6400-00TE` | 7 × 5 = 35 | DigiKey | 8,037 | — (ships today) | Active | No risk |
| `ACSL-6420-00TE` | 1 × 5 = 5 | DigiKey | 733 | 17 weeks | Active | Current stock covers this order by a wide margin; the 17-week figure is only relevant if DigiKey's own stock is drawn down by other customers before this order is placed |

### 5.2 Connectors — the schedule risk named in the brief

| Part | Qty/bd ×5 | Distributor | Stock | Mfr. lead time | Note |
|---|---:|---|---:|---:|---|
| MDR68 male right-angle (MH Connectors `3700-0121-01`) | 4 × 5 = 20 | **Not found at DigiKey or Mouser** in this check | RS Components (stock #813-3313): search-result snippet shows ~541 units; Distrelec: search-result snippet shows in-stock, next-day delivery. **Corrected, fix round 1** — real regional stock exists; neither figure is from an authenticated distributor login | Not obtained by direct fetch, either session (RS/Distrelec blocked 403/timeout both times) — the stock figures at left are search-result level only | **Watch item, not the widest error bar — see §6 (corrected, fix round 1).** ~541 units at RS alone covers this run's 20-unit need many times over. Still worth a direct account-based stock check before the production order — a search snippet is not a live quote — but this is no longer the standout schedule risk the original framing implied |
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
| `IH1215D` (isolated ±15V DC-DC, sole supply for the whole Intan-isolated domain) | 1 × 5 = 5 | XP Power / DigiKey | **Resolved, fix round 1 — `IH1215D` is correct, not ambiguous.** DigiKey lists it directly (product 4487834): "Isolated Module DC DC Converter 2 Output 15V -15V 66mA, 66mA, 10.8V-13.2V Input," package DIP. DigiKey's separate `IH1215S` listing carries the *identical* electrical description, package SIP. XP Power's own IH-series page states the suffix directly: `S` = SIP-7 package, `D` = DIP-14 package — packaging, not output count — and the whole family is dual-output regardless. This board's footprint (`Converter_DCDC_XP_POWER-IHxxxxD_THT`) is the through-hole DIP variant, so `D` is right as written | 16 weeks (DigiKey factory quote); DigiKey itself holds 0 direct stock, ~38 units via DigiKey Marketplace/WEC at check time | No longer an order-code risk — see §6 (corrected, fix round 1). Only 5 needed; Marketplace stock covers it, still the domain's sole supply (single point of failure by design) |
| `TMA-0505S` | DNP (§2) | DigiKey | In stock, ships today | — | Fallback footprint only — not populated on any board built to this lock, no procurement action needed unless bench testing changes that |
| `LD1117S33TR` / `LD1117S50TR` | 1 ea × 5 | DigiKey (ST) | In stock, ships today | — | No risk |
| `SN74LVC541APW` / `SN74AHCT541PW` / `SN74HCT32D` / `SN74HCT14D` | 4/5/1/1 × 5 | DigiKey (TI) | Listed, real current parts; `SN74HCT14D` bare code obsolete (§4). `SN74AHCT541PW`: DigiKey's own listing surfaces only the `-PWR` tape-and-reel SKU, unlike its `SN74HCT541PW` predecessor — not confirmed obsolete, just order `SN74AHCT541PWR`, the same §4 packaging-suffix class as `LM339`/`MCP4728`/`ADG1206YRUZ` | — | High-volume commodity logic; only the §4 suffix issue is a real finding. `SN74AHCT541PW` replaced `SN74HCT541PW` on U8–U11/U15 — 8mA IOL vs 6mA, identical pinout/footprint; see hardware/breakout/design-review.md |
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

1. **`ADG1206YRUZ-REEL7` — corrected, fix round 1: not a scarce part.** DigiKey itself shows
   **0** in stock (450 due 2026-11-25, 550 due 2026-12-16, 11-week quoted factory lead) — that
   figure is real, but it does not mean the part is hard to get: Arrow carries 713 of the exact
   `-REEL7` SKU, LCSC carries 86, and Octopart's cross-distributor aggregate shows roughly
   255,894 units across authorized channels (search-result-level for all three, not
   independently re-confirmed against each distributor's own live page this session — the same
   confidence caveat as MDR68 below). 40 units are needed for the full 5-board run; Arrow alone
   covers that more than 17× over. This part was originally described as possibly rivalling the
   NI acquisition cards' own fixed 12–13 week lead (spec §10.2) as the longest-lead item on the
   whole procurement list — it does not: DigiKey's own restock lands in 3–4 months regardless of
   this board's schedule, and Arrow/LCSC stock is available today. A real, pin-for-pin-compatible
   alternate exists besides: Analog Devices' own `ADG5206` (`ADG5206BRUZ`, 28TSSOP — same
   package/pinout, ADI's own generation-upgrade documentation names it the latch-up-immune
   replacement for `ADG1206`), with roughly 4,000 units of the tape-and-reel variant
   (`ADG5206BRUZ-RL7TR-ND`) at DigiKey itself (also search-result-level). Order the specified
   `ADG1206YRUZ-REEL7` from Arrow or LCSC; `ADG5206` is recorded here as a fallback if that
   sourcing ever tightens, not a recommended substitution — swapping the part number is a
   schematic change and out of scope for this audit.
2. **MDR68 (`3700-0121-01`) — corrected, fix round 1: less dire than originally reported, still
   a watch item.** Still not found at DigiKey or Mouser (unchanged). This session's re-check
   found real stock elsewhere: a search-result-level RS Components (UK) listing shows roughly
   541 units, and a search-result-level Distrelec listing shows the part in stock with next-day
   delivery — both are exactly the regional pools the original session's direct fetches were
   blocked from retrieving (403/timeout, and this session's own direct RS/Distrelec fetch
   attempts hit the same block, so this remains search-snippet-level, not an authenticated
   distributor lookup). 20 units are needed for the full 5-board run; RS's own ~541 alone
   comfortably covers that. Still worth a direct account-based stock query before the production
   order — a snippet can be stale or region-locked in ways a real account login is not — but
   this is a **watch item now, not the standout schedule risk** the original framing implied.
   (Spec §10.2 itself independently named this connector class "the widest schedule error bar"
   before any of this project's own stock checks ran; that is the spec's own pre-Task-13 risk
   assessment, left as-is rather than edited here, and is a different claim from this document's
   own finding above.)
3. **`IH1215D` — resolved, fix round 1: the code was correct all along, not ambiguous.** XP
   Power's IH-series suffix denotes package only (`S` = SIP-7, `D` = DIP-14); the entire family
   is dual-output regardless of suffix — confirmed directly against XP Power's own IH-series
   product page and cross-checked against DigiKey's own separate listings for `IH1215S` and
   `IH1215D` (identical electrical description, differing only in the package attribute). This
   board's footprint is the through-hole DIP variant (`Converter_DCDC_XP_POWER-IHxxxxD_THT`), so
   `D` is the right suffix for this design exactly as written — no schematic change needed, and
   this should not be re-opened without new evidence. The one real remaining item on this part:
   DigiKey itself holds 0 direct stock (16-week factory lead), with roughly 38 units available
   via DigiKey Marketplace against a 5-unit need — enough, but worth a direct quote before
   ordering, since it remains the sole supply for the entire Intan-isolated domain (a single
   point of failure by design, not a sourcing defect).
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
  `SN74AHCT541PW`'s TSSOP-20, `ADG1206YRUZ`'s TSSOP-28, `TPS7A4901`/`TPS7A3001`'s HVSSOP-8, all
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

**Fix round 1 additions (same date, re-check after reviewer correction):** direct fetches were
attempted against XP Power's IH-series product page, DigiKey's `ADG5206`/`ADG5206BRUZ` pages,
Analog Devices' `ADG5206`/`ADG5207` datasheet PDF, and RS Components'/Distrelec's own MDR68
listings — all blocked or timed out (the same failure class as the original session's
RS/Distrelec attempts). The three corrected findings above instead rest on search-result-level
evidence, explicitly flagged as such at each site: Arrow, LCSC, and Octopart listings for
`ADG1206YRUZ-REEL7`; DigiKey's own `ADG5206BRUZ` tape-and-reel listing; Analog Devices' own
generation-upgrade cross-reference documentation (`ADG5206` as `ADG1206`'s pin-compatible
successor), corroborated across three independent search results; RS Components (UK, stock
#813-3313) and Distrelec listings for MDR68; and DigiKey's own separate `IH1215D`/`IH1215S`
product listings (product IDs 4487834 and 1470-1454-5-ND respectively) plus XP Power's own
IH-series product page text for the package-suffix resolution. None of this is an authenticated
distributor-account lookup — treat every fix-round-1 stock figure above as a starting point for
a real quote, not a final number.
