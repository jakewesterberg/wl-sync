# Breakout board: layout constraint set

This is the constraint set for plan Task 14, Step 2. It covers layout only: what a person
driving pcbnew must respect, and where each rule comes from. The floorplan (Step 1) and the
CM5 placement decision (Step 1b) are separate documents, and a constraint that waits on one of
them says so. Nothing here was measured on hardware, and no `breakout.kicad_pcb` exists yet.

It was merged on 2026-11-20 from two independent drafts and a scored comparison of them, then
re-checked against the repository as it stands after layout task L1 (commits `26b9ec1` and
`155ffc6` on `breakout-layout`), which drew `U63`'s input filter, `J19`'s shell position and
the `U3`/`U4` PowerPAD.

## How to read this

**Every constraint has an ID, the rule, and its source.** A source is a `file:line`, a key in
`hardware/datasheet-params.toml`, or a named standard. A rule the sources do not give is
marked **[engineering judgment]** with its reason. Treat those as proposals to accept or
change, not as requirements the design already carries. Where the sources lack a value, the
text says "not in the sources" and gives none.

**Line numbers are those of the commit that added this file.** Three sheets were regenerated
by L1 (`power`, `analog-frontend`, `opto-intan`), and in them each line of an on-sheet note is
its own `(text ...)` element, so a note is cited by the line that carries the quoted words.

| Short | File |
|---|---|
| PLAN | `docs/superpowers/plans/2026-08-13-breakout-pcb.md` |
| SPEC | `docs/superpowers/specs/2026-08-13-syncbox-breakout-pcb-design.md` |
| NET | `hardware/breakout/netlist-contract.json` (a component's value line, or a net's first line) |
| TOML | `hardware/datasheet-params.toml` (cited as `[section].key`, with the line) |
| DR | `hardware/breakout/design-review.md` |
| PA | `hardware/breakout/parametric-audit.md` |
| PE | `hardware/breakout/panel-elevations.md` |
| FT | `hardware/breakout/frame-time-inputs.md` |
| LO | `hardware/breakout/line-out-and-pfi-copy.md` |
| F6 | `hardware/breakout/f6-review-brief.md` |
| D3 | `hardware/breakout/d3-panel-thickness.md` |
| MULE | `hardware/mule/floorplan.md` |
| ASM | `hardware/assembly-checklist.md` |
| MURATA | Murata reference sheet *GA342DR7GF221KW02-B0B* (4520M/1808, 220 pF; "as of Apr.3,2026"), cited by page. It is not in this repository. |
| GENPWR | `hardware/gen/gen_breakout_power.py` |
| GENOI | `hardware/gen/gen_breakout_opto_intan.py` |
| CHKOI | `hardware/gen/check_breakout_opto_intan_netlist.py` |
| sheet names | `hardware/breakout/sheets/<name>.kicad_sch` |

**Footprint geometry for stock parts is a measurement, not a source document.** The pad
positions quoted for the SOIC-16, the three DC/DC converters, the 1808 capacitor and the
MSOP-10 were read from the KiCad 10.0.5 library files the sheets name. Those files are not in
this repository. A different library version could move the numbers, so re-check them against
the footprints in the board file. Each such number is labelled where it appears.

**The project file carries no net classes or design rules.** `breakout.kicad_pro` has only the
keys `board` and `schematic`. Every width and clearance in §4 is therefore new, and most are
judgment.

---

## 0. Gates and precedence

| ID | Constraint | Source |
|---|---|---|
| GT-1 | Layout does not begin until a person has completed the sign-off block in `design-review.md`. As read for this document, the reviewer, date, decision and signature fields are blank. | DR:35–37; DR:352–366 |
| GT-2 | Take the connector faces from SPEC §9.6 and `panel-elevations.md`, not from PLAN's Task 14 Step 1. The plan puts the rig-facing connectors on the front and the MDR68s, Intan BNCs, inlet and Pi cutouts on the rear. SPEC §9.6 says "The front/rear assignment was backwards" and reverses it, and PE was drawn from the corrected version. SPEC §9.4's own sentence "Rig-facing connectors front, equipment-facing rear" predates the correction in the same way. | PLAN:873–878 (the stale text); PLAN:880–899 (the dated note, recording the ruling of 2026-11-20 that the spec wins); SPEC:789–799; PE:17–25; SPEC:767 |
| GT-3 | Where a sheet's text note disagrees with the netlist, the netlist governs. Known stale notes: (a) opto-intan still says `ISO_5V` comes from an `LD1117S50`, but the netlist has `U63`, a `TMR 1-0511`; (b) pi-interface still lists GPIO18 and GPIO19 as camera trigger outputs, but `J7` pins 12 and 35 are unconnected and the cameras free-run; (c) analog-frontend still gives the photodiode stage as Rf = 1M and Cf = 3.3 pF, but `R38`/`R40` are 180 kΩ and `C48`/`C50` are 22 pF; (d) the power generator's docstring calls the 3.3 V regulator "U2", and in the netlist it is `U1`. | **[engineering judgment]** No document states a precedence, and the netlist contract is the one artefact a test regenerates and compares on every run. Stale notes: `opto-intan.kicad_sch`:3460 against NET:558 and SPEC:507–511; `pi-interface.kicad_sch`:3348 and :3578 against NET:226 and SPEC:156, 171; `analog-frontend.kicad_sch`:3190 against NET:302, 304, 52, 54 and PA:236, 503; GENPWR:621 against NET:496 |
| GT-4 | Do not route any dual-BNC pad until pad 1 is confirmed as port A's centre conductor and pad 2 as its shell (and pads 3 and 4 likewise for port B), from the 3D model or by continuity on a sample. The roles were inferred from one third-party footprint. If they are wrong, every BNC channel drives its shield and grounds its centre, with a clean netlist and a clean ERC, and the cost is a respin. | F6:17–52. Making it a gate on routing is **[engineering judgment]**: the source asks the question and nothing in the repository closes it. See Open question 12. |

---

## 1. Board and stack-up

| ID | Constraint | Source |
|---|---|---|
| S-1 | Four copper layers, in the order signal / GND / power / signal (L1 / L2 / L3 / L4). | PLAN:919; SPEC:767–769 |
| S-2 | The outline is nominally 430 × 240 mm. Do not freeze the depth until the dual BNC's protrusion through the panel is measured, because that protrusion sets the board's set-back from each panel. | SPEC:767–769; PLAN:867; PE:125–128; DR:226–230 |
| S-3 | Board thickness 1.6 mm, 35 µm copper on all four layers. | **[engineering judgment]** Thickness, copper weight, dielectric and finish are not in the sources. This is what a fab quotes by default for four layers, and every current the sources give fits it (§4). Stiffness across a 430 mm span is Open question 8. |
| S-4 | All components on L1. | **[engineering judgment]** The first board is assembled turnkey and the rest are hand-built from it (SPEC:923–929), and one populated side keeps both routes simple. The mule did the same (MULE:12). The sources do not require it here. |
| S-5 | Minimum track and gap 0.15 mm, used only where a package forces it. The default is in §4. | **[engineering judgment]** The finest part is the `MCP4728` in MSOP-10 at 0.5 mm pitch (SPEC:944–950; `comparators.kicad_sch`:1711), whose stock footprint has 0.35 mm pads and so a 0.15 mm gap (KiCad library). Everything else is SOIC, TSSOP or coarser (SPEC:940–942). No fab house or capability is in the sources. |
| S-6 | L2 and L3 are each split into the same four regions (AGND, DGND, NI_GND, INTAN_GND), with the splits in the same place on both layers, so no domain's plane lies over or under another domain's. | PLAN:920–921 for the analog/digital split. The congruent L3 split is **[engineering judgment]**: an overlapping plane pair is a capacitor across the barrier the split exists to make, and SPEC already names barrier capacitance as a coupling path (SPEC:526). |
| S-7 | L3 carries `+12V` and `-12V` in the AGND region, `+5V` and `+3V3` in the DGND region, `NI_5V` in the NI region, and `ISO_P12`, `ISO_N12` and `ISO_5V` in the Intan region. | Rail-to-domain membership from the decoupling returns: NET:709, 807, 609, 574, 3208, 3005, 2970, 2941; SPEC:552–558. How each region's L3 is divided between its rails is **[engineering judgment]**: it is left to the router. |

---

## 2. Ground domains, planes and return paths

The domains are SPEC:299–306. Their nets, as the netlist has them:

| Domain | Ground net | Rails | NET lines |
|---|---|---|---|
| Analog | `AGND` | `+12V`, `-12V` | 942, 709, 807 |
| Digital | `DGND` | `+5V`, `+3V3` | 2040, 609, 574 |
| NI isolated | `NI_GND` | `NI_5V` | 3273, 3208 |
| Intan isolated | `INTAN_GND` | `ISO_P12`, `ISO_N12`, `ISO_5V`, and the raw and filtered ±15 V | 2865, 3005, 2970, 2941, 3035, 3000 |

**The board has four net ties, not one.** `NT1` joins `AGND` to `DGND`. `NT2`, `NT3` and `NT4`
join `FAN_RTN`, `CHASSIS_GND` and `ISO_5V_RTN` to their planes (NET:261–264).
The design review's net-tie item still says the board places exactly one `NetTie_2` and the BOM
lists exactly one `NT` reference (DR:87–95). That text
is stale. What it protects still holds: `NT1` is the only component with pins on both `AGND`
and `DGND`.

| ID | Constraint | Source |
|---|---|---|
| G-1 | `AGND` and `DGND` join at `NT1` and nowhere else: no second tie, no stitching via between the two pours, no pour overlap, no shared component pad, no mounting-hardware path. | PLAN:920–921; SPEC:303; NET:261, 942, 2040; DR:87–95 for the intent (its count is stale, as above) |
| G-2 | `NT1` sits at the supply inlet header `J1`. | PLAN:920–921 ("star tie at the power inlet only"); PLAN:486; GENPWR:566; `power.kicad_sch`:3289 and :3099 |
| G-3 | `FAN_RTN` joins `DGND` only at `NT2`. `CHASSIS_GND` joins `DGND` only at `NT3`. `ISO_5V_RTN` joins `INTAN_GND` only at `NT4`. Route each of the three as its own trace or island, and never flood it into the plane it ties to. | NET:262–264, 2762, 2036, 2963; `power.kicad_sch`:3289, :3039; SPEC:518–520; PA:322–325 |
| G-4 | `NT2` is placed at the inlet beside `NT1`, so the fan feed and return are star-connected there and nowhere else. | SPEC:884–889 ("star-connected at the power inlet only"). "Beside `NT1`" is **[engineering judgment]**: the source names the inlet, not a position at it. |
| G-5 | `NT3` is placed at the inlet star cluster, and `J56` (the chassis earth stud) connects to it by a dedicated trace. | The single-point rule is `power.kicad_sch`:3039 and SPEC:864. The location is **[engineering judgment]**: the sources fix the topology but not where the tie sits, and beside `NT1` keeps chassis current out of both planes. See Open question 18. |
| G-6 | `NT4` is placed at `U63`'s secondary pins, so `ISO_5V_RTN` is a short local net and the converter's switching return enters `INTAN_GND` at that one point. The net has five pins: `U63` pin 6, `C137` pin 2, `C138` pin 2, `C162` pin 2 and `NT4` pin 1. | SPEC:518–520; NET:2963; GENOI:775, :799. "At `U63`'s pins" is **[engineering judgment]** as a distance: the sources say one controlled point and that the loop closes at the part, and give no dimension. |
| G-7 | Use the four net-tie footprints as drawn (`NetTie-2_SMD_Pad2.0mm`). Do not replace a tie with a zero-ohm link or a pour neck. | `power.kicad_sch`:10206, `power.kicad_sch`:7069, `power.kicad_sch`:7839, `opto-intan.kicad_sch`:6973; GENPWR:120; PLAN:486–488 (the tie is what makes a plane-split error visible as a rule violation). Keeping the footprint rather than an equivalent is **[engineering judgment]**: the netlist checkers and the contract name these four references. |
| G-8 | `NT1` and the copper on both sides of it carry at least 1 A continuously, and no connection to it is narrower than the tie's own 2 mm bridge. All digital-side supply return reaches the inlet through `NT1`, because `J1`'s two ground pins are both `AGND`: `+5V` at up to 615 mA, the fans at about 240 mA through `NT2`, and `U2`'s primary current. `U2`'s share is not in the sources. | Currents: NET:220 (`J1` pins 4 and 5 on `AGND`); SPEC:589; SPEC:877; NET:497 (`U2` pin 1 on `DGND`). The 1 A figure and the width rule are **[engineering judgment]**: the tie must not become the narrowest conductor in the main return. The 2 mm bridge is the stock footprint's copper polygon (KiCad library). By the reasoning of N-1 the fault case is larger: `F4` holds 1.10 A and `F1` holds 0.5 A. |
| G-9 | The AGND/DGND gap on L2 and L3 is at least 1.0 mm wide. | **[engineering judgment]** No width is in the sources. 1 mm keeps the split visible in DRC and in the render check of V-4, and is far above any spacing the 24 V between the two domains' rails needs (N-11). |
| G-10 | No track on L1 or L4 crosses the AGND/DGND split except the nets in G-11. Those cross once each, in one corridor placed as close to `NT1` as the floorplan allows. | **[engineering judgment]** With a single star tie, the return current of any signal that crosses the split closes through `NT1`, so the crossing's distance from the tie is its loop area. The sources state the split and the single tie and give no crossing rule. See Open question 16. |
| G-11 | The nets that must cross between the AGND and DGND regions, for G-10: the comparator outputs `PD1_COMP`, `PD2_COMP`, `ACC_TRIG`, `CAM_FRAME_EYE` and `CAM_FRAME_BEH` (LM339s with V− on `AGND`, loads on `DGND`); `I2C_SCL`, `I2C_SDA` and `+3V3` to `U55`; the 32 mux address lines `MUX1_A0` to `MUX8_A3` from `U67` and `U68`; `+12V` to `U2`, whose return is `DGND`; the two frame-time inputs and `CAM_FRAME_THR` into `U76`; and `PD1_COMP_PFI` to `J2` pin 38. | NET:3355, 3400, 922, 1966, 1945 (the outputs); NET:550, with `U55` pins 4 and 10 on `AGND` and pin 1 on `+3V3`; NET:3040 onward, from NET:562 and NET:563; NET:497, with pin 1 on `DGND` and pin 3 on `+12V`; NET:1987; NET:3384; FT:82–91 |
| G-12 | `U55` (`MCP4728`) sits in the AGND region with its 100 nF `C120` directly across VDD and VSS. Its VSS is `AGND` although its VDD is `+3V3`, so the thresholds are in the same ground domain as the signals they are compared with. | `comparators.kicad_sch`:4190; NET:550, 124 (`C120` across `+3V3` and `AGND`) |
| G-13 | `U54` and `U76` (`LM339`) sit in the AGND region: V+ is `+12V` and V− is `AGND`. `U76` sits wholly in the AGND domain, and only its passives touch `DGND`. | NET:549, 571 (pin 3 on `+12V`, pin 12 on `AGND`); `comparators.kicad_sch`:4895; FT:82; TOML `[lm339]` note (lines 215–217) |
| G-14 | The `+5V` entry bulk and bypass capacitors return to `DGND`. The ±12 V entry capacitors return to `AGND`. Place each group on its own side of `NT1`. | GENPWR:414–425; NET:5–10 |
| G-15 | `J1` pins 4 and 5 are both `AGND`. Bring both into the AGND side of the star with full-width copper. Neither is a shield or chassis pin. | NET:220; PA:752–769 (pin 5 "keeps halving the supply return resistance") |
| G-16 | On both NI analog connectors (`J2`, `J36`) the only pin tied to a board ground is pin 62 (AISENSE to `AGND`). Leave every other ground pin of Connector 0 unconnected: no thermal relief, no pour contact. `J2` has no `DGND` pin by design. | NET:221, 245; DR:78–85; `taskpc-digital.kicad_sch`:7010; `analog-ni.kicad_sch`:6408; LO:85–92 |
| G-17 | `AO_GND_TPC` (`J2` pins 54 and 55 to both `J57` shells) is routed as a trace only. No board plane, pour or tie may carry it. | NET:1081; `taskpc-digital.kicad_sch`:6110, :6620, :6730; LO:48–52 |
| G-18 | Each of the ten rig-facing BNC shields is its own net and reaches `AGND` only through its own 10 Ω resistor (for example `A_PD1_SHLD` through `R35`, `A_MIC_SHLD` through `R43`). Do not merge shields with each other or with the AGND pour at the connector. | SPEC:338–342; SPEC:679–683; PA:392–396; NET:1831, 1557 and the other eight `*_SHLD` nets; NET:299, 307 (10 Ω); F6:70–74 |
| G-19 | `ACCESIO_AGND` (`J19` pins 1 to 19) is its own net, joined to `AGND` only through `R27` (10 Ω). It is the "−" reference of `U16` to `U21`. Keep it out of the AGND pour. | NET:890, 291, 511 |
| G-20 | `ACCESIO_SHELL` (`J19`'s two shell pads, both numbered `SH`) is its own net and reaches `AGND` only through the position `R224`. Do not let the AGND pour, any plane or `ACCESIO_AGND` touch either shell pad. `R224` is placed at `J19`. | NET:918, 488; PA:788–798; PA:805; `analog-frontend.kicad_sch`:3200, :3130. The two `SH` pads are 4 mm pads on 3.2 mm holes in the stock D-sub footprint (`analog-frontend.kicad_sch`:13961; KiCad library). Placing `R224` at the connector is **[engineering judgment]**: shell current should reach its bond before it runs beside the eye-channel traces. See Open question 19. |
| G-21 | The 14 Intan BNC shells (`J37`, `J39`, `J41`, `J43`, `J46`, `J48`, `J50`, pads 2 and 4) land directly on the `INTAN_GND` plane, with no bond resistor. | `mux-intan.kicad_sch`:7342; NET:246, 251, 2865 |
| G-22 | The shells of `J5`, `J6`, `J13`, `J15` and `J17` are `DGND`, including the two frame-time ports `J17B` and `J6B`. The frame-time clamps and the `CAM_FRAME_THR` divider are DGND-referenced although they are drawn on the comparators sheet. | NET:224, 225, 232, 233, 234; FT:71–80; NET:473, 474 (`+5V` and `DGND`) |
| G-23 | Task-PC digital returns stay in `DGND`: `J3`, the inbound `SN74LVC541A`s (`U5`, `U6`, `U7`) and their clamps sit over the DGND region. | SPEC:310–312; NET:222, 500 |
| G-24 | Fan current never returns through `AGND`. `FAN_12V` and `FAN_RTN` are a dedicated pair from the inlet to `J52` to `J55`, and that pair is not routed across the AGND region. | SPEC:884–889; `power.kicad_sch`:3299; NET:2749, 2762. Keeping the pair out of the AGND region is **[engineering judgment]**: it follows from the rule, but the sources state only the return path. |

---

## 3. Isolation barriers and slots

**Only the NI domain is galvanically isolated.** The DGND to NI_GND barrier is galvanic: nothing
joins the two domains but the optocouplers. The DGND to INTAN_GND barrier is galvanic only at
its own straddlers, and the Intan domain as a whole is not isolated from the board:

- each of the eight Intan-side `INA105`s joins `INTAN_GND` to an AGND-referenced node through
  its own resistors, 25 kΩ per input and 50 kΩ common mode (TOML `[ina105]`, lines 132–134).
  Eight in parallel is about 6 kΩ;
- SPEC says of that stage "no galvanic break" (SPEC:294);
- `NT1` joins `AGND` to `DGND` (NET:261);
- at every one of those parts `INTAN_GND` copper (pin 1) lies 0.67 mm from `AGND` copper
  (pin 2): SOIC-8, 1.27 mm pitch, 0.6 mm pads (NET:534; `mux-intan.kicad_sch`:1534; KiCad library);
- the offset the Intan domain can take from `AGND` is bounded by the `INA105`'s 20 V
  common-mode range (TOML `[ina105].v_cm_range_v`, line 131).

**What the slot is for, per the sources.** The plan asks for a "clearance slot" and gives no
voltage for it (PLAN:919–920). The mule says what its slot does: it leaves "no copper path"
between the regions except through the straddling packages, "the entire point of the barrier"
(MULE:70–74). SPEC gives the barriers' purpose as keeping loop current out of signal
returns (SPEC:284–287). So on the Intan side the slot is a guarantee about copper, and its margins
are fabrication margins: how close copper may sit to a routed edge.

**What the sources give, and what they do not.** The plan's slot is the only clearance
requirement stated anywhere. The sources give no working voltage across the NI barrier, no
pollution degree or material group for the board, and no isolation rating for the `ACSL-6xx0`
(`[acsl_6xx0]`, TOML:35 onward, has none). A creepage figure from IEC 62368-1 or IEC 60664-1
is indexed by exactly those inputs, so none can be derived for the NI barrier. For the Intan
side the list above already bounds the matter, and no creepage figure applies there. Nothing
in the sources claims either barrier is safety insulation, and the audit calls `U63`'s
insulation functional (PA:880–884). Component ratings the sources do give, for reference:

| Part | Rating | TOML |
|---|---|---|
| `IH1215D` (`U2`) | 1000 VDC isolation, 60 pF barrier capacitance | `[ih_series].isolation_vdc`, `.isolation_capacitance_pf` (lines 279–280) |
| `TMR 1-0511` (`U63`) | 1500 VDC, 1061 VAC for 60 s, 50 pF | `[tmr_1_0511].isolation_vdc`, `.isolation_test_vac_60s`, `.isolation_capacitance_pf` (lines 763–765) |
| `C162` (220 pF, 1808) | 2 kV. Traco's table asks for it, and the part fitted, KEMET `C1808C221KGRACTU`, carries it. | `[tmr_1_0511].emc_filter_c2`, `.emc_filter_c2_fitted` (lines 797, 806) |
| Dual BNC | 1500 Vrms dielectric withstanding. The drawing's key names no terminals. Separately, the body is glass-filled PBT, so each shell is isolated from the panel and from its neighbour. | `[bnc_dual_isolated].dwv_vrms` (line 541); note (lines 550–552) |
| MDR68 | 500 VAC dielectric withstanding | `[mdr68].dielectric_withstanding_vac` (line 459) |
| `ACSL-6400` / `-6420` | not in the sources | none |
| `TMA-0505S` (`U62`, DNP) | not pinned to a primary document | `[tma_0505s].status` (line 716) |

**Copper-to-copper gap at each part that straddles the NI or the DGND/Intan barrier.** These
are computed from KiCad library pad positions and sizes, and the last column is what a centred
2.5 mm slot leaves on each side. Under `C162` the slot is 1.5 mm by ruling (B-14), which leaves
0.55 mm.

| Part | Footprint | Primary to secondary | Copper gap | Left per side |
|---|---|---|---|---|
| `U56`–`U61`, `U64`, `U65` | `SOIC-16_3.9x9.9mm_P1.27mm` (`opto-ni.kicad_sch`:1840, `opto-intan.kicad_sch`:6853) | pin 1–8 column to pin 9–16 column; pad centres ±2.475 mm, pads 1.95 mm long | 3.00 mm | 0.25 mm |
| `C162` | `C_1808_4520Metric` (`opto-intan.kicad_sch`:8529) | pad 1 to pad 2; pad centres ±2.075 mm, pads 1.55 mm long | 2.60 mm | 0.05 mm at 2.5 mm; **0.55 mm at the ruled 1.5 mm** |
| `U63` | `Converter_DCDC_TRACO_TMR-1-xxxx_Single_THT` (`opto-intan.kicad_sch`:9783) | pin 2 to pin 4; 5.08 mm centres, 1.5 mm pads | 3.58 mm | 0.54 mm |
| `U62` (DNP) | `Converter_DCDC_TRACO_TMA-05xxS_12xxS_Single_THT` (`opto-ni.kicad_sch`:7149) | pin 2 to pin 4; 5.08 mm centres, 1.5 mm pads | 3.58 mm | 0.54 mm |
| `U2` | `Converter_DCDC_XP_POWER-IHxxxxD_THT` (`power.kicad_sch`:10599) | pin 3 to pin 4; 7.62 mm centres, 1.85 mm pads | 5.77 mm | 1.64 mm |

The mule reports the same 3.58 mm and about 0.5 mm for the same SIP pin spacing (MULE:49–57).

| ID | Constraint | Source |
|---|---|---|
| B-1 | "Domain regions placed so each isolation barrier is a **single straight line** across the board, never a zigzag". | PLAN:873–878. The plan wrote this for the old face assignment; B-2 re-derives it. |
| B-2 | **Re-derived for the corrected faces.** (a) Each domain reaches the edge its connectors are on: `NI_GND` reaches the front edge at `J45`; `INTAN_GND` reaches the front edge at its seven BNC bodies; `AGND` reaches the front edge at `J36` and the back edge at `J2`, `J19` and the five analog bodies; `DGND` reaches the front edge at `J5` and `J4` and the back edge at `J3`, `J6`, `J13`, `J15` and `J17`. (b) Each barrier is built from straight segments, and each segment faces one neighbouring domain only. The NI barrier faces `DGND` only and is one segment. The Intan boundary has a DGND-facing segment (`U64`, `U65`, `U2`, `U63`, `C162`) and an AGND-facing segment (the eight `INA105`s), so it has a corner unless both regions meet it along one line. (c) No straddling part sits at a corner, and each slot is one straight run within one segment. | **[engineering judgment]** The plan's "rear corner" placement cannot hold once SPEC §9.6 puts the recording-NI and Intan connectors on the front (GT-2), so the rule is restated in the form the corrected faces allow. What is sourced: straightness (B-1); the faces (SPEC:798–799; PE:36–42); each connector's ground net (NET:250, 245, 224, 223, 221, 222); and which parts straddle which boundary (CHKOI:698 onward for the four on the opto-intan sheet; `mux-intan.kicad_sch`:7242, :7252 for the `INA105`s; NET:497, 551–557; DR:97–106 for the class). Which arrangement to draw is Open question 1. |
| B-3 | A routed slot at least 2.5 mm wide runs under every optocoupler, between its two pin columns (`U56`–`U61` on the NI barrier, `U64` and `U65` on the Intan barrier), and under `U2`, between its primary and secondary pins. | PLAN:919–920; NET:551–556, 559, 560, 497 |
| B-4 | The same slot runs under `U63` and under the DNP fallback `U62`. | **[engineering judgment]** The plan says "the isolated DC-DC" in the singular (PLAN:920), written when `U2` was the only one. `U63` and `U62` are galvanic barriers of the same kind, and `U63` is the switcher whose coupling across the barrier SPEC calls out (SPEC:526–529). B-14 is a ruled local departure from this row. See Open question 5. |
| B-5 | The slots are real cutouts on Edge.Cuts, not a keep-out convention, and no slot reaches a board edge. | MULE:42–47, the mule's practice, which MULE:70–74 says this board reuses |
| B-6 | No track, via or pour on any layer crosses a barrier line. The only things that span the NI barrier or the DGND/Intan barrier are the straddling parts of the table above. `C162` is one of them: it is the fourth declared straddler on the opto-intan sheet, beside `U63`, `U64` and `U65`. | MULE:70–74; DR:97–106; CHKOI:698 |
| B-7 | Between slots, along the rest of each barrier line, keep a copper-free band at least 2.5 mm wide on all four layers. | **[engineering judgment]** The plan specifies the slot only under the parts. An unslotted stretch with a narrower gap would be the weakest point of the same barrier. |
| B-8 | A keep-out rule area covers each slot, extending 1 mm beyond the cutout on each side and blocking tracks, vias and zones on all layers. Footprint pads may sit inside it: the mule's DC/DC pads sit about 0.5 mm from the slot, inside its 1 mm keep-out, and adding the keep-out raised no new violation. | MULE:76–82; MULE:52–55. "All layers" is **[engineering judgment]**: the mule has two layers, and here the keep-out must also hold the L2 and L3 pours back. |
| B-9 | Carry the keep-out from each slot end to the board edge, or to the next barrier segment, so nothing routes around the end of a slot. | **[engineering judgment]** The mule relies on "nothing can route around either end" (MULE:42–47) because its parts fill the span. On a 430 mm board a barrier that does not span the board needs the keep-out to close that path. |
| B-10 | Place each `ACSL` package with its long axis along the barrier: pins 1 to 8 on the DGND side, pins 9 to 16 on the isolated side. This holds for the bidirectional `U65` too, whose pins 1 to 8 are all DGND-side and 9 to 16 all Intan-side. | NET:551 (pins 1–8 on `EVT_D*_NI_LEDA` and `EVT_D*_BUF`, pins 9–16 on `NI_GND`, `NI_5V` and `EVT_D*_NI`); NET:560 (pins 1 and 4 on `DGND` and `+5V`, pins 9 and 12 on `INTAN_GND` and `ISO_5V`, pins 13 to 16 on Intan-side LED nets) |
| B-11 | **SOIC-16 optocouplers: the 2.5 mm slot leaves about 0.25 mm of laminate between each pad row and the slot edge.** That is half the mule's tightest margin, which the mule already calls the tightest on its board. Confirm the fab's minimum copper-to-routed-edge distance before committing. | The geometry table above; MULE:52–55; SPEC:495. The fab's capability is not in the sources. See Open question 4. |
| B-12 | DC/DC orientation and margins. `U63` and `U62`: the slot passes between pin 2 and pin 4, with about 0.54 mm each side, and it cannot be made wider there. On `U63` pins 1 and 2 are primary and pins 4 and 6 secondary. `U2`: pins 1 and 3 are primary, pins 4 to 6 secondary, with about 1.6 mm each side. `U2` pin 2 is no-connect and sits at the secondary end, so treat its pad as Intan-side. | Pin roles: NET:497, 558, 557; TOML `[tmr_1_0511].pinout` (line 774). Geometry: the table above. Treating `U2`'s pin 2 pad as Intan-side is **[engineering judgment]**: what the pin connects to inside the module is not in the sources, and the pad lies beyond the slot from the primary pins. |
| B-13 | `C162` sits across the Intan barrier line beside `U63`, with pad 1 (`DGND`) on the DGND side and pad 2 on the Intan side. Each pad joins the converter's own pin (pin 1, pin 6) by the shortest trace. Pad 2 connects to `ISO_5V_RTN`, never to the `INTAN_GND` plane. B-8's keep-out covers the inner part of each pad, and of `U63` pins 2 and 4, so each trace leaves its pad from the outer end. | NET:166; `opto-intan.kicad_sch`:2970, :3050; GENOI:798–799; TOML `[tmr_1_0511].emc_filter_topology` (line 793) |
| B-14 | **The slot narrows to 1.5 mm under `C162` only**, which leaves 0.55 mm of laminate between each pad and the routed edge. Everywhere else it stays at 2.5 mm or more. `C162`'s pad gap is 2.60 mm, the smallest among the parts that straddle the DGND/Intan barrier, and a 2.5 mm slot would leave 0.05 mm a side. This is a fabrication margin, copper beside a routed edge, and not a creepage requirement: the Intan domain's copper already sits 0.67 mm from `AGND` copper at each `INA105` (the lead of this section). Do not lengthen `C162`'s pads inward, and keep the laminate between them free of copper on all four layers, as B-6 and B-7 require of the whole line. | **Ruled by the user on 2026-11-20: "Narrower slot under C162"** (PA:891–930). It is a deliberate local departure from B-4's judgment, not from a sourced rule: the plan's slot names optocouplers and "the isolated DC-DC" (PLAN:920). Geometry: the table above. The practice is a capacitor maker's own: MURATA p.26, "2-2. Dimensions of Slit (Example)", row "4.5×2.0", gives a slit width d of 1.0 to 2.8 mm under a part of this case size. That drawing is Murata's, for Murata's part and land (gap 2.8 to 3.4 mm, same page), not KEMET's for the part fitted. The fab's minimum copper-to-routed-edge distance is not in the sources; see Open question 4. |
| B-15 | `C162` stays reachable with an iron after assembly, clear of the BNC bodies and of `U63`'s body. | PA:846–849; ASM:21. Access is **[engineering judgment]**: the ruling has the part removed and refitted on board 1. |
| B-16 | No Intan-island copper under or beside the primary pins of `U2` and `U63`, and no DGND copper under their secondary pins, on any layer. | **[engineering judgment]** The two converters already couple about 110 pF from `DGND` to the Intan reference, and `C162` takes it to about 330 pF (PA:866; TOML lines 280, 765). SPEC names that coupling as the residual that belongs to layout (SPEC:526). Overlapping copper adds to it. |
| B-17 | The eight Intan-side `INA105`s (`U39`, `U41`, … `U53`) are placed on the Intan island. Only two AGND-domain conductors reach each one: `MUX_OUTn` at pin 3 and `AGND` at pin 2. These 16 traces are the only copper that crosses between AGND and INTAN_GND. No slot is required under these parts. The 2.5 mm copper-free band of B-7 applies, with these traces crossing it. | `mux-intan.kicad_sch`:7242, :7252; NET:534 (pin 2 on `AGND`, pin 3 on `MUX_OUT1`, pins 1, 4 and 7 on `INTAN_GND`, `ISO_N12` and `ISO_P12`). Applying B-7's band here, and putting the whole package on the Intan island, are **[engineering judgment]**: the plan names slots only for optocouplers and the DC/DC, and pins 2 and 3 sit between pin 1 and pin 4 at 1.27 mm pitch, so no slot can pass under the package. The other workable shape is an L2 gap that notches round pins 2 and 3. See Open question 6. |
| B-18 | Route each `MUX_OUTn` across the gap side by side with its own `AGND` sense trace, taken from the AGND plane at that channel's mux, and keep the two the same length. | **[engineering judgment]** The amplifier rejects the AGND to INTAN_GND difference only as far as its two input legs match. TOML `[ina105].note` (lines 139–141) records how series imbalance in one leg costs CMRR, and a side-by-side pair also keeps the crossing loop small. |
| B-19 | The eight muxes (`U38`, `U40`, … `U52`) sit entirely in the AGND region and never straddle. Place each mux next to its `INA105`. | `mux-intan.kicad_sch`:7227, :7232; SPEC:390–392; NET:533. Adjacency is **[engineering judgment]**: it keeps the two legs of B-18 short. |
| B-20 | The NI island and the Intan island do not border each other. If the floorplan forces them adjacent, B-7's band applies between them. | **[engineering judgment]** The plan kept them in separate corners (PLAN:873–878); under SPEC §9.6 both are front-edge regions, and nothing electrical joins them. |
| B-21 | Expect, and accept, `silk_edge_clearance` warnings on straddling parts' own outlines. Accept no other DRC class at a slot. | MULE:191–194 |
| B-22 | `U62` and its companions `C135`, `C136`, `FB3` and `R170` are placed and routed as if populated, across the NI barrier with its slot, and marked DNP. | DR:307–311; `opto-ni.kicad_sch`:7689–7694; NET:557, 139, 140, 219, 434 |
| B-23 | The 1.5 mm section of B-14 is as short as it can be: it spans `C162`'s body width and no more, and the slot returns to 2.5 mm on both sides. B-8's keep-out follows the step, 1 mm beyond the cutout on each side. | MURATA p.26: "the length of the slit design should be as short as possible to prevent mechanical damage in the capacitor", with a slit length e of 3.6 to 4.1 mm tabled for the 4.5 × 2.0 mm case; PA:924. The length here is **[engineering judgment]**: Murata's e is for Murata's part, and the stock 1808 land is 2.3 mm wide (KiCad library), so the figure to draw waits on the placed footprint. |

---

## 4. Net classes, widths and clearances

**The currents are sourced; the widths are not.** No width or clearance figure exists in the
sources. Where a figure is quoted from IPC-2221 it is one of two things: the usual curve fit
to that standard's conductor-sizing charts, I = k·ΔT^0.44·A^0.725 with A in square mils and
k = 0.048 for outer layers and 0.024 for inner ones, taken at a 10 °C rise in 35 µm copper;
or a row of its Table 6-1, electrical conductor spacing (B1 internal conductors, B2 external
uncoated conductors). The standard itself is not in the repository, and its revision is
Open question 22.

| ID | Class and nets | Width | Sourced current | Source |
|---|---|---|---|---|
| N-1 | **INLET**: `P12_RAW`, `P5_RAW`, `P12_FUSED`, `P5_FUSED`, and `+5V` from `D3` to its plane | 1.0 mm | `+5V` 475 mA typical, 615 mA maximum, behind `F4` at 1.10 A hold. `P12_RAW` carries the main branch (97 to 121 mA) plus the fan branch (about 240 mA), behind `F1` and `F2` at 0.5 A hold each. | SPEC:589; TOML `[polyfuse_1206l110].i_hold_a` (line 346); PA:470–475; SPEC:877; NET:3350, 3342, 3338. The width is **[engineering judgment]**: copper should never be the element that opens before the fuse, so it is sized to the fuse's hold current and not to the load. The IPC-2221 curve fit gives 0.34 mm on an outer layer and 0.89 mm on an inner one as minimums at 1.1 A. |
| N-2 | **INLET_N12**: `N12_RAW`, `N12_FUSED`, and `-12V` from `D2` to its plane | 0.8 mm | 54 mA typical, 66 mA maximum, behind `F3` at 0.5 A hold | PA:664; TOML `[polyfuse_1206l050].i_hold_a` (line 316); NET:3204, 3200. The width is **[engineering judgment]**, by the rule of N-1. |
| N-3 | **FAN**: `FAN_12V_RAW`, `FAN_12V`, `FAN_RTN` | 0.8 mm | about 240 mA for four fans; `F1` holds 0.5 A and trips at 1.0 A | SPEC:877; TOML `[polyfuse_1206l050].i_trip_a` (line 317); NET:2758, 2749, 2762. The width is **[engineering judgment]**, sized to the fuse's trip current: the curve fit gives 0.30 mm outer and 0.78 mm inner at 1.0 A. |
| N-4 | **NI_PWR**: `NI_5V` and `NI_GND` from `J45` to their planes | 0.6 mm from each connector pin | domain total 151 to 199 mA; up to 283 mA through contacts rated 0.5 A; budget 250 mA per connector | SPEC:623–630; TOML `[mdr68].contact_current_rating_a` and note (lines 458, 463–465); `opto-ni.kicad_sch`:7654; NET:3208, 3273. The width is **[engineering judgment]**. Use both `NI_5V` pins (8 and 14) with symmetric copper, and all twelve `NI_GND` pins. |
| N-5 | **ISO_PWR**: `ISO_P15_RAW`, `ISO_N15_RAW`, `ISO_P15_FILT`, `ISO_N15_FILT`, `ISO_P12`, `ISO_N12`, `ISO_5V`, `ISO_5V_RTN`, `U63_VIN` | 0.5 mm | 66 mA per rail is `U2`'s rating; `ISO_P12` and `ISO_N12` carry 12 to 16 mA each; `ISO_5V` 63 to 91 mA; `U63`'s input 83 to 120 mA | TOML `[ih_series].i_out_per_rail_ma` (line 268); PA:303; SPEC:588–589; NET:3035, 3000, 3029, 2994, 3687. The width is **[engineering judgment]**. |
| N-6 | **RAIL_BRANCH**: branches off the `+12V`, `-12V`, `+5V` and `+3V3` planes to component pins | 0.4 mm | per-IC supply current. The `+3V3` rail's total load is not in the sources. | **[engineering judgment]** A plane feeds every rail (S-7), so branches are short stubs, and the value only keeps them from being routed at signal width. GENPWR:617 records that `+3V3` was never checked against a load figure. |
| N-7 | **ANALOG**: all `A_*` nets, `MUX_OUT1` to `8`, `INTAN_AO*`, the `*_THR` threshold nets, `AO_L_*` and `AO_R_*` | 0.25 mm | signal level | **[engineering judgment]** Wider than the digital default for hand rework on the nets most likely to be probed. The sources give no impedance requirement for them. |
| N-8 | **DIGITAL**: everything else, including `EVT_*`, optocoupler LED and output nets, `MUXn_A*` and I²C | 0.2 mm | LED nets carry 12.65 mA nominal, 13.90 mA at the maximum corner | SPEC:581–582. The width is **[engineering judgment]**: 0.2 mm, not the 0.15 mm one package needs, on a board built five-off by hand. |
| N-9 | **USB**: `USB_DP_PI`, `USB_DM_PI` | one coupled pair, matched in length, over unbroken `DGND`; `J18` pin 1 (`USB_VBUS_PI`) is left unconnected | none | NET:3704, 3700; `control-usb-i2c.kicad_sch`:2528. Pair routing is **[engineering judgment]**: USB 2.0 specifies D+ and D− as a 90 Ω differential pair, but the stack-up dielectric is not in the sources, so no width or gap is given. |

| ID | Constraint | Source |
|---|---|---|
| N-10 | `+5V` copper drops no more than 25 mV from `D3`'s cathode to the farthest optocoupler LED resistor at 615 mA. That is 41 mΩ, about 83 squares of 35 µm copper. | The margin is sourced: the worst-case LED current is 8.6 mA against an 8 mA floor (`opto-ni.kicad_sch`:7544), and the supply's ±2 % tolerance is load-bearing (SPEC:597–603). The 25 mV figure is **[engineering judgment]**: 0.6 mA through 249 Ω is about 0.15 V of margin, and 25 mV spends a sixth of it. |
| N-11 | Default copper-to-copper clearance 0.2 mm within a domain. | **[engineering judgment]** Within-domain differences are at most 24 V (`+12V` to `-12V`, and `ISO_P12` to `ISO_N12`), apart from the raw ±15 V pair of N-12. IPC-2221 Table 6-1 asks 0.05 mm internal (B1) and 0.1 mm external uncoated (B2) for 16 to 30 V, so 0.2 mm covers both. |
| N-12 | 0.6 mm on outer layers, and 0.1 mm on inner layers, between `ISO_P15_RAW`/`ISO_P15_FILT` and `ISO_N15_RAW`/`ISO_N15_FILT`. | The unregulated `IH1215D` may reach about ±16.5 V at light load (PA:368–369; TOML `[ih_series]` lines 271, 273). That is about 33 V between the two nets, the 31 to 50 V row of IPC-2221 Table 6-1: B2 0.6 mm, B1 0.1 mm. |
| N-13 | 0.6 mm on outer layers from any net between a panel connector and its series resistor (`*_RAW`, `*_DAC`, `*_BNC`, `*_SHLD` and the like) to any other net. | **[engineering judgment]** These are the only nets that see a mis-plug voltage directly. The clamps are checked for ±24 V on digital inputs and ±48 V on analog ones (TOML `[bat54s]` note, lines 877–879; PA:964–966). An analog input at ±48 V against a ±12 V rail is 60 V, and both the 31 to 50 V and 51 to 100 V rows of IPC-2221 Table 6-1 give B2 0.6 mm. |
| N-14 | Via: 0.3 mm drill, 0.6 mm pad as the default. | **[engineering judgment]** No fab capability is in the sources. This is a size any four-layer process offers. |
| N-15 | Drill sizes for the two custom connectors come from TOML, not from a library default: MDR68 contacts 0.85 mm and mounting holes 2.77 mm, non-plated; BNC signal holes 0.89 mm and ground-terminal holes 2.01 mm. | TOML `[mdr68].contact_hole_dia_mm`, `.mounting_hole_dia_mm` (lines 448, 451); `[bnc_dual_isolated].pcb_signal_hole_dia_mm`, `.pcb_ground_hole_dia_mm` (lines 531, 533); DR:187–201 |

---

## 5. Placement and routing of sensitive nets

### 5.1 The barcode family

The barcode leaves the sync module on `J7` pin 11 as `BARCODE_RAW` (NET:1931), one
driver into eleven buffer inputs on `U15` and `U74`. From there it leaves the board at seven
ports: to NI through `U60` (`BARCODE_BUF` → `BARCODE_NI` → `J45` pin 3), to Intan through `U64`
(`BARCODE_INTAN_BUF` → `BARCODE_INTAN` → `R205` → `BARCODE_INTAN_BNC` → `J46` pin 3), and to
five camera BNC ports (`CAM_SYNC_EYE` and `CAM_SYNC_BEH1` to `4` on `J13`, `J15` and `J17`). It
also feeds five on-board 2-pin headers (`BARCODE_PI` on `J8` to `J12`) and the heartbeat LED
(`BARCODE_HB`). NET:1883–1931 (the `BARCODE_*` nets), 1996–2028 (the `CAM_SYNC_*` nets).

**"The opposite side of the board" has two readings, and the sources do not choose.** The
plan's sentence is K-1. "Side" can mean the opposite lateral region of the board, or the
opposite copper layer. Its Task 15 render check asks for the "barcode net away from the analog
region", which supports the region reading, but nothing rules the layer reading out. K-2 and
K-3 state the rule under each. A layout can satisfy both at once. Open question 2 asks which
was meant.

| ID | Constraint | Source |
|---|---|---|
| K-1 | "The barcode net routed on the opposite side of the board from every analog channel and kept away from the front edge", because "it carries a high edge density and coupling into a headstage is a QC failure on real data". | PLAN:921–923. SPEC gives the same reason for the external barcode cable, which "must be routed away from headstage cables" (SPEC:771–773). |
| K-2 | **Region reading.** No barcode-family net enters the AGND region, or runs over or under it, on any layer. Under SPEC §9.7 the CM5 and the digital section are back-left and the analog front ends back-right, so the barcode stays on the CM5 side. | **[engineering judgment]** This is one reading of K-1, not the plan's own words. Supported by PLAN:975–976 and SPEC:834–839. "On any layer" is added so DRC or a reviewer can check the rule against the plane outline. |
| K-3 | **Layer reading.** Analog channels are routed on L1 over the L2 AGND plane, and barcode-family nets on L4. Where a barcode net runs on L4, the L3 copper above it is a DGND-domain rail (`+5V` or `+3V3`), never `+12V` or `-12V`. | **[engineering judgment]** This is the other reading of K-1. An L4 trace's return current flows in the adjacent L3 copper, so that copper must belong to the digital domain. Two costs follow from the rest of this document: every part is on L1 (S-4), so each barcode pin needs a via; and L4's reference is a power plane, not a ground plane. |
| K-4 | **Front edge, re-derived for the corrected faces.** The barcode cannot be kept off the front edge: `J45` pin 3 and `J46` pin 3 are front-face barcode ports. The rule becomes: the two isolated-side nets (`BARCODE_NI`; `BARCODE_INTAN` and `BARCODE_INTAN_BNC`) take the shortest path from `U60` and `U64` to their connectors, inside their own islands, and inside the Intan island they stay clear of `INTAN_AO*` and the `INA105`s. Every DGND-side barcode net stays away from the Intan analog outputs (`J37` to `J43` and the eight `INA105`s) and from `J36`. On the front face, the `J46` body goes at the end of the Intan group farthest from `J37` to `J43`. | **[engineering judgment]** The plan's reason is coupling into a headstage, and its front edge was the rig-sensor edge. SPEC §9.6 moved the Intan and recording-NI connectors there (GT-2), so the literal rule cannot be met and the reason is applied in its place. Pins: NET:250, 251, 1914, 1896, 1901. See Open question 3. |
| K-5 | On the back face, the three camera-sync bodies (`J13`, `J15`, `J17`) go at the CM5 end of the BNC row and the five analog bodies (`J20`, `J22`, `J24`, `J26`, `J29`) at the far end, with `J6`, `J57` and the non-BNC connectors between them. | Both groups are on the back face (PE:36–42); SPEC:834–839. The ordering is **[engineering judgment]**: it is the only way to separate the two groups on a face that carries both. See Open question 3. |
| K-6 | `U15` and `U74` are placed beside `J7`, so `BARCODE_RAW` is short. | NET:1931 (one driver, eleven inputs). Placement is **[engineering judgment]**: the net with the most loads should be the shortest. PA:273–276 gives the audit's reason the buffer stays with its source GPIO and its BNCs. |
| K-7 | The five 47 Ω resistors `R198` to `R202` are placed at the buffer outputs, not at the BNCs. | NET:462–466; PA:267–284 (they are source terminations). "At the driver" is **[engineering judgment]**: a series termination works only at the source end. |
| K-8 | `BARCODE_PI`'s five 2-pin headers (`J8` to `J12`) are grouped at `U15` on `DGND` with short stubs. | NET:1923, 227–231. Placement is **[engineering judgment]**: five unterminated stubs hang on one output. |
| K-9 | `BARCODE_BUF` and `BARCODE_INTAN_BUF` approach `U60` and `U64` across `DGND` only. | NET:1883, 1905. **[engineering judgment]**, as K-2 applied to these two nets. |
| K-10 | The heartbeat LED `D43` is driven only from its own buffer leg (`BARCODE_HB`: `U15` pin 13 → `R195` → `D43`). Tap no other barcode net for it. | `power.kicad_sch`:3089, :3009 and :2989 ("not to load the barcode net itself"); NET:1888, 459 |

### 5.2 Photodiode transimpedance stages

| ID | Constraint | Source |
|---|---|---|
| K-11 | `U22` and its feedback parts sit directly behind `J20`. The summing-junction nets `A_PD1_CLAMP` and `A_PD2_CLAMP` are as short as placement allows, on L1 only, with no via. Each carries the op-amp "−" pin, `R34`/`R36`, `R38`/`R40`, `C48`/`C50`, the clamp `D29`/`D30` and the DNP pads `R196`/`R197`. | NET:1795, 1842; SPEC:398–399; PA:240–243. "Shortest, L1 only, no via" is **[engineering judgment]**: stray capacitance at this node adds to the input capacitance the 22 pF compensation was sized against. |
| K-12 | Route `U22` pins 3 and 5 from the shield pads (`A_PD1_SHLD`, `A_PD2_SHLD`), not from `AGND`, and ring each summing junction with a guard trace on that same shield net. | The "+" input is referenced to the local shield: `analog-frontend.kicad_sch`:3320; NET:517, 1831, 1878. The guard ring is **[engineering judgment]**: with 180 kΩ feedback (NET:302, 304), surface leakage from a ±12 V neighbour is a real offset, and a guard at the same potential is the standard remedy. |
| K-13 | Keep the DNP trim positions `R196` and `R197` as pads in parallel with `R38` and `R40`. Do not turn them into a jumper or a test point. | PA:240–243; PA:261–263; DR:311 |

### 5.3 Analog front ends and distribution

| ID | Constraint | Source |
|---|---|---|
| K-14 | Each analog input's series resistor and clamp diode are placed at its connector, between the connector and the first amplifier. | SPEC:377; SPEC:648; PLAN:37. "At the connector" follows FT:180–183, which states it for the frame-time inputs. Applying it to the analog inputs is **[engineering judgment]**. |
| K-15 | Each front-end difference amplifier senses centre against shield at the connector. Route the centre and shield conductors from the BNC to the receiver as an adjacent pair, and place the 10 Ω shield resistor at the connector end. | SPEC:338–342; `analog-frontend.kicad_sch`:3360. Pair routing and the resistor's position are **[engineering judgment]**: a small loop makes disturbance arrive as common mode, and circulating shield current should reach `AGND` before it runs beside the sense trace. |
| K-16 | The analog section occupies the back-right corner, farthest from intake and exhaust, at its own connectors. | SPEC:836–839 |
| K-17 | The microphone chain (`U23` and the `A_MIC_*` nets) and the two photodiode stages are the parts kept farthest from the fan headers, the fan pair of N-3, `U2` and `U63`. | SPEC:785–787; PLAN:915 for the fans; SPEC:417–424 (the microphone is the one broadband channel). Extending the fan rule to the two converters is **[engineering judgment]**: they are the board's switchers. |
| K-18 | The sixteen `A_*` distribution nets, and the buffered copies to `J36` (front) and `J2` (back), run over `AGND` for their whole length. | **[engineering judgment]** Each has about a dozen pins spread between the back-right front ends, the mux bank and both NI Connector 0s (for example `A_MIC`, NET:1494). A return-plane rule is the only way to hold that together. |
| K-19 | Each NI and task-PC buffer output has its own trace through its own 100 Ω to its own connector pin, with the 100 Ω at the op-amp output. Each Intan `INA105`'s 100 Ω output resistor is at the `INA105` output pin. | `analog-ni.kicad_sch`:6343–6348; TOML `[ina105].c_load_stable_pf` (line 136); PA:955–957. The resistor's position is **[engineering judgment]**: it isolates the amplifier from trace and cable capacitance only when it sits at the amplifier. |
| K-20 | AISENSE on each NI Connector 0 (`J36` pin 62, `J2` pin 62) reaches `AGND` by a dedicated trace taken to the plane at the NI buffer bank, not by a pour contact at the connector. | The tie itself: DR:78–85; `analog-ni.kicad_sch`:6408. Where it lands is **[engineering judgment]**: NRSE rejects the difference between AISENSE and the buffers' reference, so AISENSE should sense where the buffers return. |
| K-21 | `PD1_COMP_PFI` is the one digital line on an analog connector (`J2` pin 38). Keep it away from the AI traces and the AO pair on its approach to `J2`, and place `R221` to `R223` beside `J2`. | NET:3384; LO:94–96; `taskpc-digital.kicad_sch`:6600. The separation is **[engineering judgment]**: the acceptance in LO assumes the line does not couple into its cable neighbours on the board either. |
| K-22 | Route `AO_L_*` and `AO_R_*` beside the `AO_GND_TPC` trace, out and back on adjacent conductors. | **[engineering judgment]** That is what keeps the audio return off the board's planes, which G-17 requires (LO:48–52). |
| K-23 | The three ÷1/÷2 shunt headers (`J28`, `J30`, `J32`) are placed so a 2-gang shorting block can be fitted and moved by hand, clear of the BNC bodies, and both legs' matched divider pairs are laid out symmetrically. | NET:241, 243, 244 (each header's own description requires one 2-gang block). Access and symmetry are **[engineering judgment]**: the design exists so the two legs divide identically. |

### 5.4 Comparators, DAC and control bus

| ID | Constraint | Source |
|---|---|---|
| K-24 | The comparator outputs (`PD1_COMP`, `PD2_COMP`, `ACC_TRIG`) are routed directly, without detours. The 2.2 kΩ pull-ups were sized for a rise under 1 µs into an estimated 100 pF that includes the trace. | PA:587, 595–600 |
| K-25 | `U55` is placed beside `U54`, so the four threshold nets are short and stay over `AGND`. `U54`, `U76` and `U55` sit at the AGND side of the AGND/DGND boundary, so the crossings of G-11 are short. | `comparators.kicad_sch`:4190; NET:3392, 3428, 934, 1607. Adjacency and the boundary position are **[engineering judgment]**. |
| K-26 | Each comparator channel's hysteresis network (10 kΩ series, 1 MΩ feedback) is placed at the comparator pins. | **[engineering judgment]** These are high-impedance nodes, and a hysteresis of about 33 mV (FT:102) is easily eroded by coupled noise. |
| K-27 | `U54` and `U76` are kept away from the CM5, the exhaust path, `U1`, `U2`, `U63` and the LDOs. | TOML `[lm339].t_j_range_lm339x_c` and note (lines 212, 221–223); SPEC:775–778. The list of heat sources is **[engineering judgment]**: the sources give the margin, not what to keep clear of. |
| K-28 | For each frame-time input, the 100 Ω series resistor and `BAT54S` clamp (`R211` and `D45` for `J17B`, `R216` and `D46` for `J6B`) are placed at the connector, not at `U76`. | FT:180–183; `comparators.kicad_sch`:4855–4860; NET:475, 211, 480, 212 |
| K-29 | Keep the I²C bus short. The pull-ups were chosen for about 100 pF of bus, and the audit's 100 kHz recommendation assumes about 150 pF. The bus has four devices (`U55`, `U66`, `U67`, `U68`) and crosses into the AGND region once, for `U55`. | `control-usb-i2c.kicad_sch`:2573, :2598; PA:958–961; NET:2779, 2786 |
| K-30 | `U66` (`MCP2221A`) is placed beside the internal USB header `J18`. | NET:235, 561. **[engineering judgment]**: it keeps the pair of N-9 short. |
| K-31 | `U67` and `U68` (VSS on `DGND`) are placed on the DGND side of the AGND/DGND boundary next to the mux bank. Their 32 address lines cross in the G-10 corridor as one bundle and stay clear of the muxes' analog inputs. | NET:562, 563, 3040 onward; SPEC:206–209 (the lines are configuration-time state). Placement is **[engineering judgment]**. |

### 5.5 Digital paths

| ID | Constraint | Source |
|---|---|---|
| K-32 | When routing the outputs of `U8` to `U11` and `U15` toward `J3` and `J45`, keep the runs short and review series termination (typically 22 to 33 Ω, close to the driver) from trace length and observed ringing. The schematic has no footprints for such resistors. | DR:165–183. Adding the parts is a schematic change; see Open question 9. |
| K-33 | Each LED-driver pair (`U70` to `U73` doubling `U8` to `U11`) is placed side by side, and the two paralleled outputs join by short, equal traces before running to the LED cathode. | `opto-ni.kicad_sch`:7564; TOML `[sn74ahct541].i_gnd_abs_max_ma` and note (lines 83, 90–91); NET:503, 565. Symmetry is **[engineering judgment]**: paralleled outputs share current only when the resistance in each leg matches. |
| K-34 | Each optocoupler's 249 Ω LED resistors are on the DGND side and its 3.9 kΩ pull-ups on the isolated side, each on its own side of the slot. For the two Intan-side LEDs of `U65` the LED resistors are 301 Ω on `ISO_5V`, on the Intan side. | NET:420, 421 (`R156` 249 Ω on `+5V`, `R157` 3.9 kΩ on `NI_5V`, the pattern every channel repeats); NET:447, 449 (301 Ω on `ISO_5V`); `opto-intan.kicad_sch`:3220 |
| K-35 | A 100 nF bypass sits at each optocoupler supply pin pair (pins 10 and 15 against 9 and 16 on the `ACSL-6400`), on the isolated side. | NET:3208 (fourteen capacitors, `C121` to `C134`), 3273. Proximity is **[engineering judgment]**: the `ACSL-6xx0`'s own bypass requirement is not in the sources, and the mule's 7 mm figure belongs to a different part, the `HCPL-4661` (MULE:98–101). |
| K-36 | The Intan digital output resistors (`R204` to `R208`, 100 Ω) and the reward output resistor (`R203`, 100 Ω, on `J6`) are placed at their connectors. | PA:558–571: `R203` handles fault current, "not transmission-line matching", and `R204` to `R208` buy "mis-plug protection rather than termination". NET:467–472. Placement is **[engineering judgment]**: protection belongs between the panel and the silicon, as in K-28. |
| K-37 | The 330 Ω series resistors on GPIO0 and GPIO1 (`R25`, `R26`) stay in series between the board's buffer and `J7` pins 27 and 28, placed at `J7`. Do not let a stub bypass them. | `pi-interface.kicad_sch`:3038; NET:289, 290; SPEC:202–204. "At `J7`" is **[engineering judgment]**: the contention they limit happens at the module's pins during its boot probe. |

---

## 6. Power entry and converters

| ID | Constraint | Source |
|---|---|---|
| P-1 | `J1` is a 5-way 2.54 mm header fed by flying leads from the panel-mounted M12. It need not be at the board edge, so place it at the AGND/DGND boundary, near the M12's position on the back face. | TOML `[m12_inlet].termination` and note (lines 482, 502–504); `power.kicad_sch`:10041; PE:79. Putting it on the boundary is **[engineering judgment]**: G-1 and G-2 fix the star at `J1`, so `J1`'s position decides how well G-10 can be met. |
| P-2 | The protection chain is laid out in netlist order immediately at `J1`: `P12_RAW` → `F2` → `D1` → `+12V` and, in parallel from `P12_RAW`, `F1` → `D44` → `FAN_12V`; `N12_RAW` → `F3` → `D2` → `-12V`; `P5_RAW` → `F4` → `D3` → `+5V`. Nothing else connects to a `*_RAW` or `*_FUSED` net. | PA:462–475; NET:213–216, 167–169, 210. Grouping the parts at `J1` is **[engineering judgment]**: they protect everything downstream, and the star point is already there. |
| P-3 | `U2` (`IH1215D`) has its primary on `+12V` and `DGND`. Place it on the DGND/Intan barrier with `C9` (`+12V` to `DGND`) at its primary pins, and bring its `+12V` feed across the AGND/DGND boundary in the G-10 corridor. | GENPWR:681–683; NET:497, 13. The corridor routing is **[engineering judgment]**. |
| P-4 | The Intan supply chain is laid out in line on the Intan island, each pi filter compact and in order: `U2` pin 5 → `C10` → `FB1` → `C11` → `U3` for the positive rail, and `U2` pin 4 → `C16` → `FB2` → `C17` → `U4` for the negative. No branch is taken off before the LDOs. | PLAN:481; NET:14, 15, 20, 21, 217, 218; SPEC:558. Compact placement is **[engineering judgment]**: a filter's input and output copper must not couple round the ferrite. |
| P-5 | `U3` and `U4`: input and output capacitors (2.2 µF minimum each; `C14` and `C20` are the output capacitors) at the pins. Pin 7 (DNC) gets no copper at all, not even a stub or a pour contact. The FB and NR/SS parts (`R1`/`R2`/`C13`/`C12` on `U3`, `R3`/`R4`/`C19`/`C18` on `U4`) are on short traces at the pins. | TOML `[tps7a4901_tps7a3001].c_in_min_uf`, `.c_out_min_uf`, `.dnc_handling` (lines 690, 691, 696); NET:498, 499 (pin 7 unconnected on both), 265–268. "At the pins" is **[engineering judgment]**: FB sits on a 90.9 kΩ/10 kΩ divider on `U3` and a 93.1 kΩ/10 kΩ one on `U4`, so it is a high-impedance node. |
| P-6 | `U3` and `U4` pad 9 (the PowerPAD) is on `INTAN_GND`. Solder it to an `INTAN_GND` copper area on L1 and tie that area to the L2 `INTAN_GND` plane with vias under the pad. No other net's copper passes under either part. | NET:498, 499 (pin 9 on `INTAN_GND`); PA:813–823, built at PA:825; TOML `[tps7a4901_tps7a3001].powerpad_handling` and note (lines 698, 700–706); `power.kicad_sch`:8384. The vias are **[engineering judgment]**: the datasheet says to solder the pad to the plane, and on a board with its ground on L2 vias are how a top-side pad reaches it. |
| P-7 | `U63`'s input filter is placed at the converter's primary pins, in Traco's order: `C161` across `+5V` and `DGND` on the source side of `L1`; `L1` in series from `+5V` to `U63` pin 2; nothing between `L1` and pin 2. | `opto-intan.kicad_sch`:3390, :3050; TOML `[tmr_1_0511].emc_filter_topology` (line 793); SPEC:531–550; NET:165, 260 |
| P-8 | `U63_VIN` has two pins, `L1` pin 2 and `U63` pin 2. Route it as one short trace on L1: no via, no pour, no test pad, and no contact with the `+5V` plane. | NET:3687; TOML `[tmr_1_0511]` note (lines 857–859). "Short, L1 only" is **[engineering judgment]**: copper added to this node sits downstream of the filter, where Traco's figure has nothing. |
| P-9 | Keep the input loop small: `C161` → `L1` → `U63` pin 2 → `U63` pin 1 → `C161`. Return `C161`'s `DGND` pad to `U63` pin 1 on L1 before either reaches the `DGND` plane. `C155` and `C156` are on the source side of `L1`, in parallel with `C161`, so place them beside `C161`, not beside pin 2. | The converter reflects 80 mA peak to peak of ripple into its input, and this loop is where it flows: TOML `[tmr_1_0511].reflected_ripple_ma_pp` (line 760); note (lines 857–859); NET:159, 160. The loop rule is **[engineering judgment]**: the sources say the parts go at the converter's pins and give no area. |
| P-10 | Take `U63`'s `+5V` feed (to `C161` and `L1`) from the `+5V` plane entry as its own branch, separate from the branch that feeds the optocoupler LED resistors. | **[engineering judgment]** What ripple the filter leaves should not ride on the rail whose tolerance sets the LED current (N-10). SPEC puts the converter's primary on `+5V`/`DGND` because that ground already switches the optocoupler LEDs (SPEC:516–517). |
| P-11 | `L1`'s footprint may have each pad's outer toe lengthened by about 0.5 mm. Leave its 0.8 mm inner gap as it is, and leave room round it for hot air. | SPEC:952–962; `opto-intan.kicad_sch`:10101 (`L_Ferrocore_DLG-0302`: two 1.4 × 3.0 mm pads 0.8 mm apart, KiCad library) |
| P-12 | `U1` (the 3.3 V LDO, SOT-223 with the tab on pin 2): the tab is `+3V3`, not ground. Give it a `+3V3` copper area and keep it clear of the `DGND` pour. | `power.kicad_sch`:8483 (`SOT-223-3_TabPin2`); NET:496 (`U1` pin 2 on `+3V3`). The copper area is **[engineering judgment]**. |
| P-13 | `U1` is placed close to the `+5V` entry capacitors, because it has no input capacitor of its own. The generator recommends a local bypass at layout time regardless, and the schematic has no part for one. | GENPWR:621–632 (the docstring calls the part "U2"; see GT-3). See Open question 20. |
| P-14 | The four fan headers `J52` to `J55` are placed near the fans they serve: two toward the front-right intake strip, two toward the left side panel. | SPEC:828; SPEC:872–874. Header position is **[engineering judgment]**. It is subject to G-24 and K-17. |
| P-15 | Test points `TP1` to `TP7` are reachable with the lid off. `TP5`, `TP6` and `TP7` (`ISO_P12`, `ISO_N12`, `INTAN_GND`) sit together on the Intan island. | NET:489–495; `power.kicad_sch`:3249, :3049, :3119; SPEC:867. Grouping is **[engineering judgment]**: the isolated rails can only be measured against `INTAN_GND`. |

---

## 7. Keep-outs

| ID | Constraint | Source |
|---|---|---|
| X-1 | The barrier keep-outs of B-7, B-8, B-9, B-14 and B-23. | as there |
| X-2 | The CM5 IO Board (160 × 90 mm) sits over the digital region only, at the back-left by the exhaust. No analog front end, no part of the AGND region and none of the airflow path serving it lies under it. If it cannot be placed so, the decision reverts to a Pi 5 with a PCIe-FPC NVMe adapter, before panels are machined. | SPEC:834–835; SPEC:901–909; PLAN:909–913 |
| X-3 | Under the IO Board's shadow, component height is limited by its standoff. | **[engineering judgment]** The limit is real, but the standoff height is not in the sources. See Open question 10. |
| X-4 | Behind each MDR68 reserve the full body depth of 51.32 mm from the mating face, by 63.86 mm wide, with 66.0 mm allowed per connector along the panel. | TOML `[mdr68].body_depth_mm`, `.overall_width_mm` (lines 455, 454); PE:65 |
| X-5 | Behind each dual BNC reserve 36.2 mm of depth by 14.4 mm of width, 29.25 mm tall, and keep its 15.4 × 37.2 mm courtyard clear. In the footprint the body runs 35.15 mm from pad 1 toward the panel. | TOML `[bnc_dual_isolated].body_depth_mm`, `.body_width_mm`, `.body_height_mm` (lines 538, 536, 537); PE:56–59; `hardware/lib/wl-sync.pretty/BNC_Dual_RA_Isolated.kicad_mod`:53 (F.Fab) and :67 (F.CrtYd) |
| X-6 | The two unnumbered 2.01 mm retention terminals of each BNC body carry no net. Keep every net's copper, and every zone, clear of them by the default clearance. | `hardware/lib/wl-sync.pretty/BNC_Dual_RA_Isolated.kicad_mod`:39, 46; TOML `[bnc_dual_isolated].pcb_ground_hole_dia_mm`, `.ground_terminal` (lines 533, 547). Treating them as isolated metal is **[engineering judgment]**. The sources say each shell is independently isolated, and that a footprint which commons the shells with the ground terminals models a different, front-isolated part (PA:392–396); whether the terminals touch either shell inside this part is not stated. See Open question 11. |
| X-7 | The MDR68 mounting holes (2.77 mm, 57.93 mm apart) are non-plated. Keep copper on all layers clear of them. | TOML `[mdr68].mounting_hole_dia_mm`, `.mounting_hole_span_mm` (lines 451–452); `hardware/lib/wl-sync.pretty/MDR68_Male_RightAngle.kicad_mod`:486, 492. The copper clearance is **[engineering judgment]**: whatever hardware passes through a non-plated hole belongs to no net, so copper beside it is an unplanned contact. |
| X-8 | Nothing overhangs the left or right board edge. The 20 mm between the board and each chassis side is the air path to the side exhaust. | SPEC:852–856 |
| X-9 | Tall parts are kept out of the line between the front-right intakes and the left-side exhaust where they would shade the diagonal flow. | SPEC:828–830. What counts as tall is **[engineering judgment]**: the sources give the flow path, not a height. |
| X-10 | No fan, fan header or fan trace beside the microphone preamp or the photodiode front ends. | SPEC:785–787; PLAN:915 |

---

## 8. Connectors and mechanical

| ID | Constraint | Source |
|---|---|---|
| M-1 | Place connectors first. Everything else is placed to serve them. | PLAN:926–927 |
| M-2 | Panel connector positions are dimensioned to ±0.1 mm, and every panel hole is derived from its placed footprint's position, never from a hand measurement. | PLAN:923–924; PLAN:956–957 |
| M-3 | Face assignment. Front: the seven Intan bodies (`J37`, `J39`, `J41`, `J43`, `J46`, `J48`, `J50`), `J5` (reward remote, with the spare port `J5B`), `J36` and `J45` (recording NI), and the reward button. Back: `J20`, `J22`, `J24`, `J26`, `J29` (rig analog), `J13`, `J15`, `J17` (camera sync, with frame-time in on `J17B`), `J6` (reward driver, with frame-time in on `J6B`), `J57` (line-out), `J2` and `J3` (task-PC NI), `J19` (DB37). | SPEC:789–799; PE:23–25, 36–42, 65–82 |
| M-4 | Dual BNC bodies are on an 18.0 mm column pitch, in one row per face. That pitch rests on a nut across-corners figure of about 16.5 mm that was never sourced from a drawing. Confirm it before fixing positions. | PE:56–59; F6:88–100. See Open question 13. |
| M-5 | Front-edge board-mounted items total 132.0 mm (two MDR68) plus 140.4 mm (eight BNC bodies). Back-edge items total 132.0 + 176.4 + 69.4 mm (two MDR68, ten BNC bodies, DB37). With the panel furniture, the back face has 7.2 mm of slack in a 450 mm panel and takes no further connector. | PE:65–82; PE:27–30 |
| M-6 | The right-hand part of the front face is a 170 × 82 mm strip for the two 60 mm intake fans. No front-face connector is placed behind it. | SPEC:811–812; SPEC:828; PE:68 |
| M-7 | BNC port axes are 6.63 mm and 22.63 mm above the board's top surface, so the standoff height sets where the BNC row lands on both panels. Choose the standoff (anything from about 5 mm up fits) before fixing panel drawings. | PE:104–120; DR:378. See Open question 17. |
| M-8 | The MDR68 row will not centre on the same height as the BNC row (a 9.10 mm body against a 29.25 mm one). Plan for it to sit lower on the panel, or to be shimmed. | PE:121–124; TOML `[mdr68].body_height_mm` (line 456) |
| M-9 | The board's set-back from each panel equals the BNC body depth (36.2 mm) less its protrusion through the panel, which is not dimensioned. Both the front and back edges carry BNCs, so the board depth and the chassis depth are tied together by twice this figure. | PE:125–128; DR:226–230. The "twice" is arithmetic on M-3. |
| M-10 | Panels are 3.0 mm aluminium. | PE:130–132; D3:3 |
| M-11 | The MDR68 footprint is built from the MH drawing: four staggered rows of 17 at 1.905 mm, 0.85 mm holes, a 41.91 mm array. Its tail-row-to-contact map is unverified. Route to it, and do not release for fabrication until the map is confirmed. The sources require that before fab, not before layout. | TOML `[mdr68]` (lines 446–453); DR:207–224 ("required before fab — not before Task 14", :221); DR:376; D3:83 |
| M-12 | Confirm the MDR68 map before routing the escape from any of the four connectors, not only before fab. | **[engineering judgment]** This is deliberately earlier than M-11's source. Each contact's X position is fixed and only its depth row may change (D3:69–71), so a late change re-routes the escape of up to 4 × 68 pins, and the check is minutes with a drawing already in hand. |
| M-13 | The M12 inlet is panel furniture with flying leads. It has no footprint and puts no load on the board. | TOML `[m12_inlet].termination`, note (lines 482, 502–504); PA:779–782 |
| M-14 | `J19` is a board-mounted horizontal DB37 on the back edge, allowed 69.4 mm of panel width. | `analog-frontend.kicad_sch`:13961; PE:78 |
| M-15 | `J56` (the chassis earth stud) is an M3 plated mounting-hole pad on `CHASSIS_GND`. Place it where a bond strap can reach it. | `power.kicad_sch`:8882; NET:258, 2036; SPEC:864. Accessibility is **[engineering judgment]**. |
| M-16 | Board mounting holes other than `J56` are isolated: non-plated, or plated with no net and clear of every plane. | **[engineering judgment]** The schematic has no mounting-hole part other than `J56` (NET `component_values`; `J19`'s two `SH` pads are the D-sub's own shell holes, G-20), and a second metal path to the chassis would defeat the single-point bond of G-3. |
| M-17 | `J7` (the 40-pin GPIO header) and `J18` (the internal USB header) are placed where the ribbon and the USB cable from the CM5 IO Board reach them, inside the digital region. | NET:226, 235; SPEC:206–209; SPEC:901–904. Position is **[engineering judgment]**; it depends on Step 1b. |
| M-18 | The four fan headers are 3-pin 2.54 mm on the PC fan pinout, with pin 3 left unconnected but present. | SPEC:872–874; SPEC:895–899; `power.kicad_sch`:3269; NET:254–257 |
| M-19 | The reward button `J4` belongs to the front face. | SPEC:91; SPEC:798; `taskpc-digital.kicad_sch`:24922. Whether it is soldered to the board at the front edge or panel-mounted and wired is Open question 14. |
| M-20 | Board silkscreen port labels match the panel's: `J5B` "spare" (front), `J17B` "CAM FRAME EYE" and `J6B` "CAM FRAME BEH" (back). | PE:152–161 |

---

## 9. Thermal and airflow

| ID | Constraint | Source |
|---|---|---|
| T-1 | Air crosses the board diagonally, from two 60 mm intakes on the front-right to two 60 mm exhausts on the left side panel. Placement follows from it. | SPEC:822–830 |
| T-2 | The CM5 and its NVMe are downstream of the analog section in that flow, at the exhaust. | SPEC:783–784; SPEC:834–835; PLAN:905–907 |
| T-3 | The analog section wants thermal stability rather than cooling. Do not place it in the direct airstream. | SPEC:836–839 |
| T-4 | The heat-dissipating parts on the board itself (`U1`, `U2`, `U3`, `U4`, `U63`) are kept off the analog region and out of the path between it and still air. | SPEC:775–778 for the sensitivity. The list is **[engineering judgment]**: the sources quantify only `U3` and `U4`, at about 0.16 W and 0.07 W (PA:822). |

---

## 10. Assembly

| ID | Constraint | Source |
|---|---|---|
| D-1 | Only hand-solderable packages: nothing finer than SOIC or TSSOP, nothing below 0603, no BGA or QFN. There are two recorded exceptions, the `MCP4728` (`U55`, MSOP-10 at 0.5 mm pitch) and the bottom-terminated inductor `L1`. Leave room round both for flux, a fine tip or hot air, and magnification. | SPEC:940–942; SPEC:944–950; SPEC:952–962. The rework room is **[engineering judgment]**: it serves the hand-build plan. |
| D-2 | Place every DNP footprint. There are ten: `U62`, `C135`, `C136`, `FB3`, `R170` (opto-ni's isolated 5 V fallback); `R120`, `R121` (the fourth comparator channel); `R196`, `R197` (photodiode gain trim); and `R224` (`J19`'s shell bond, added by L1). | DR:307–318 for the first nine; PA:805 for the tenth. Re-derived for this document from `(dnp yes)` in the committed sheets. |
| D-3 | `R224` takes 0 Ω, 10 Ω or nothing, chosen at bring-up, so its 0603 pads stay reachable after the DB37 is fitted. | PA:788–798; `analog-frontend.kicad_sch`:3030; `analog-frontend.kicad_sch`:20672. Access is **[engineering judgment]**. |
| D-4 | `C162` is fitted on every board as built and may be removed after the board-1 noise measurement. See B-15. | PA:886–889; ASM:21 |

---

## 11. Verification

| ID | Constraint | Source |
|---|---|---|
| V-1 | DRC is run as `kicad-cli pcb drc --exit-code-violations --severity-all -o hardware/breakout/drc.rpt hardware/breakout/breakout.kicad_pcb`, and the expected result is exit code 0. | PLAN:932–933 |
| V-2 | The net classes of §4 and the keep-outs of §3 and §7 are entered as board rules before routing starts, so V-1 tests them. | **[engineering judgment]** The project file has none today, and a rule that exists only in this document is not checked by V-1. |
| V-3 | Every panel connector's placed coordinates are checked against the panel drawing before panels are machined. | DR:234–256; DR:379 |
| V-4 | Render the top and bottom, and confirm that the isolation slots are unbroken, that no copper crosses them, and that the barcode net is away from the analog region. The plan's check also says "and the front edge"; read that part as K-4. | PLAN:975–976; PLAN:880–899 |
| V-5 | When the netlist is exported for import into the board, treat "schematic has annotation errors" on `kicad-cli`'s stdout as a failure. | F6:173–175 |

---

## Open questions for the designer

1. **Where the domains go under the corrected faces.** The plan's Step 1 put `NI_GND` and
   `INTAN_GND` each in a rear corner and `AGND` front-centre. SPEC §9.6 moved the recording-NI
   and Intan connectors to the front (GT-2). The front edge now has to carry all four domains:
   `INTAN_GND` (seven BNC bodies), `NI_GND` (`J45`), `AGND` (`J36`) and `DGND` (`J5`, `J4`),
   with the right-hand 170 mm taken by the fan strip (M-6). The Intan island borders `DGND` at
   its optocouplers and converters and `AGND` at its `INA105`s. Which arrangement keeps each
   barrier as straight as B-2 asks, and where is the Intan boundary's corner?

2. **"The opposite side of the board": region or layer?** The plan's barcode rule (K-1) reads
   either way. K-2 states it as a region rule and K-3 as a layer rule. Which did the rule
   mean? If the layer reading, is the barcode to be on L4 against a power plane, with a via at
   every pin, as K-3 notes?

3. **The barcode at the front edge, and on the back face.** K-4 replaces "kept away from the
   front edge" with "kept away from the Intan analog outputs and `J36`". Is that the intended
   reading? On the back face the five camera-sync ports share a row with the analog inputs
   (K-5): is there a minimum separation wanted between the last camera-sync body and the first
   analog body?

4. **The slot under the SOIC-16 optocouplers.** A 2.5 mm slot leaves about 0.25 mm between
   pad copper and the routed edge on each side (B-11). Is that acceptable to the fab? If not,
   which gives: shorter pads, a narrower slot under these eight parts, or a copper-free gap in
   place of a routed slot? The mule validated the slot under DIP-8 parts with 6.02 mm between
   pad rows, which is a different case. The same answer settles `C162`, whose ruled 1.5 mm
   slot leaves 0.55 mm (B-14), and the stepped outline of B-23.

5. **Slots at `U63` and `U62`.** The plan names one DC/DC. B-4 extends the slot to `U63` and
   `U62`: is that wanted? `C162` is settled: the slot narrows to 1.5 mm under it (B-14, ruled
   2026-11-20). That is a fabrication margin and not a creepage one, because the Intan domain
   is not galvanically isolated from the board (§3's lead).

6. **The AGND/INTAN_GND boundary at the `INA105`s.** No slot fits under an SOIC-8 whose AGND
   pins sit between its Intan pins. B-17 puts the whole package on the Intan island with two
   traces crossing a copper-free band. The alternative is an L2 gap that notches round pins 2
   and 3. Which is wanted, and is the 2.5 mm band the right width for a boundary that is not
   galvanic?

7. **What the NI barrier must withstand.** No working voltage, pollution degree or insulation
   class is stated for it, and the `ACSL-6xx0` isolation and creepage ratings are not in
   `datasheet-params.toml`. If any standard's creepage figure is meant to apply there, those
   inputs are needed first. The Intan side needs no such figure: the sources bound it (the
   `INA105`s' resistive path and 20 V common-mode range, "no galvanic break", functional
   insulation at `U63`; §3's lead).

8. **Stack-up, fab and mounting.** Board thickness, copper weight per layer, dielectric,
   finish and the fab house are not in the sources, and neither are its minimum track, space
   and copper-to-routed-edge figures (S-3, S-5, B-11). The board is 430 × 240 mm and takes
   insertion force from 18 BNC bodies and four MDR68s on two opposite edges. How many mounting
   points, where, of what material, and is 1.6 mm stiff enough? Is any impedance control
   wanted (N-9)?

9. **Series-termination footprints on the MDR68-bound buffer outputs.** The design review
   answers when to decide: during layout, from trace length and observed ringing (K-32). What
   it leaves open is the footprints. The schematic has none, and adding them is a schematic
   change. Add 0 Ω positions before routing, or route without them?

10. **CM5 IO Board mounting.** Standoff height, orientation, fixing points and which of its
    edges faces the back panel are not in the sources. PE allows 45 mm of back-panel width
    for its port cutouts against a 160 mm board edge. Does the back-left corner hold it
    together with the back-face connector row of M-5, or does the plan's fallback to a Pi 5
    apply? This is Step 1b's decision, and X-2, X-3 and M-17 wait on it.

11. **BNC retention terminals.** The sources establish that each shell is independently
    isolated, so the two 2.01 mm terminals do not common the shells (X-6). They do not say
    whether the terminals touch either shell. Are they to be soldered to isolated pads only?

12. **BNC pad roles and shell pairing.** GT-4 holds routing until pad 1 is confirmed as a
    centre conductor. Who confirms it, from the 3D model or a sample, and by when? The pairing
    of each shell pad with its port is unconfirmed in the same way; it matters only on the
    five bodies with individually routed shields (`J20`, `J22`, `J24`, `J26`, `J29`).

13. **The BNC nut and the BNC protrusion.** The 18.0 mm pitch rests on an unsourced nut
    dimension (M-4), and the board's set-back rests on an undimensioned protrusion (M-9). Both
    need a sample or the 3D model.

14. **Reward button `J4`.** It has a board footprint (`Button_Switch_THT:SW_PUSH-12mm`), but
    PE lists the button as chassis furniture and the design review calls it panel or chassis
    mount, with its MPN still open. Is it soldered to the board at the front edge, or
    panel-mounted and wired to those pads?

15. **Indicator visibility.** The power-good LEDs (`D40` to `D42`) and the barcode heartbeat
    (`D43`) are parts on the board, and SPEC wants the heartbeat visible "from across the
    room". Light pipes, panel windows, or board-edge placement?

16. **Where the AGND/DGND crossings go.** G-10 asks for one corridor near `NT1`, and P-1
    proposes putting `J1` on the boundary to make that possible. If the inlet has to sit
    elsewhere, which of the G-11 crossings may cross away from the tie?

17. **Standoff height and the MDR68 row.** Centre the BNC pairs on the panel with a standoff
    of about 26 mm, or use a short standoff and accept an off-centre row? Shim the MDR68s or
    let them sit low (M-7, M-8)?

18. **Where `J56` and `NT3` sit.** G-5 puts `NT3` at the inlet star and M-15 wants the stud
    reachable by a bond strap. Where is the strap's other end, and does that place the stud
    near the inlet?

19. **`J19`'s shell and the panel.** `R224` leaves the shell-to-`AGND` bond to bring-up
    (G-20). If the DB37's flange or jackposts touch the back panel, the shell is at chassis
    potential whatever `R224` holds. Is the DB37 to be insulated from the panel?

20. **A local input bypass for `U1`.** The generator recommends one at layout time (P-13).
    The schematic has no part for it. Add one, which is a schematic change, or rely on
    placement near the entry capacitors?

21. **The `+3V3` load.** No budget for the `+3V3` rail is in the sources (N-6). The −12 V
    load is: 54 mA typical, 66 mA maximum (N-2).

22. **IPC-2221 revision and class.** §4 quotes the standard's conductor-sizing curve fit and
    its Table 6-1. The standard is not in the repository. Does the lab want these checked
    against a specific revision before they become board rules?
