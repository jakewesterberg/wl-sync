"""Generator for hardware/breakout/sheets/analog-frontend.kicad_sch -- Task 10a of
docs/superpowers/plans/2026-08-13-breakout-pcb.md (.superpowers/sdd/2026-08-13-breakout-pcb/
task-10-brief.md, Step 1 only -- Task 10 was split into 10a-10d, one sheet each, per
progress.md's own ruling: "Order is forced by data flow: 10a analog-frontend produces the
A_* nets that 10b (NI fan-out), 10c (mux + Intan difference amps) and 10d (comparators) all
consume."). This sheet builds ONLY the 16 front ends -- no NI/task-PC/Intan fan-out, no mux,
no comparators; those are 10b/10c/10d's own child sheets, consuming the 16 `A_*` nets this
file produces by label name, the same cross-sheet-by-global-label convention every sheet in
this project already uses.

THE CORE DESIGN DECISION, stated once here rather than scattered per channel: every one of
the 16 sources is received DIFFERENTIALLY, not with a plain buffer to AGND. Spec Sec.5.6
("The Faraday cage, and why rig-facing inputs are received differentially"): the rig sits in
a Faraday cage sound booth; sensors inside are battery powered so no mains conductor crosses
the cage wall, and whether the bulkhead bonds each cable shield to the cage shell is left
UNDECIDED. If it does, circulating current flows in the shield -- which is also the signal
return -- and a plain AGND-referenced buffer would add that voltage straight to the signal.
A shield cannot simply be lifted instead (it IS the return path), so the fix has to be at the
receiver: sense centre against shield AT THE CONNECTOR, and let a difference amplifier turn
the disturbance into common mode instead of adding it to the signal. This is what lets the
bulkhead decision be made, or reversed, later, without touching this board. The six ACCESIO
eye channels get the identical treatment, sensed against the ACCESIO connector's OWN AGND
pins rather than board AGND -- it sits on a separately-earthed eye-tracker PC, the same class
of ground offset as a bulkhead-bonded BNC shield.

Every one of the 16 front ends shares one COMMON stage before it diverges by source type:
    connector -> 1k series -> BAT54S clamp (COM) to +12V/-12V -> [source-specific back half]
implemented once as `bnc_front_end()` (BNC sources) and inlined identically for the six
ACCESIO channels (`accesio_channel()`, no BNC connector -- the DB37 pin plays that role, and
"shield" becomes the connector's own bonded AGND pins instead of a per-channel 10R node).

Four back-half topologies, by source type (spec Sec.3.2's own "16 sources" table; the exact
channel-to-topology mapping restated in CHANNEL_KIND below):

  1. PLAIN DIFFERENCE RECEIVE (13 of 16: the 6 ACCESIO eye channels, ambient light,
     accelerometer, joystick X/Y, and the 3 misc inputs) -- one INA105KU per channel, TI's
     monolithic precision unity-gain difference amplifier (matched, laser-trimmed internal
     resistor network -- exactly the "4-resistor difference amplifier" the brief allows as an
     integrated part, no external 0.1% resistors needed). The brief's own prose names
     "INA134-class" as the target part family; INA134 itself is an audio-specific part with
     no stock KiCad symbol, and this codebase's own convention (hardware/README.md's
     '541-family Value-substitution precedent) is to use the real, available part that fills
     the same topological role rather than block on the exact named part number -- INA105 IS
     that role (TI's own general-purpose "precision unity-gain difference amplifier", not a
     lesser substitute: current production, SOIC-8, hand-solderable, and its own datasheet
     names ground-loop elimination and differential-to-single-ended recovery as headline
     applications -- literally this stage's job). REF (pin 1) ties to AGND -- this is what
     lands the amplifier's OUTPUT in the AGND domain regardless of what the shield/ACCESIO_AGND
     reference is doing; "+"/"-" (pins 3/2) take signal/shield; SENSE (pin 5) shorts to OUTPUT
     (pin 6) for unity gain, the standard INA105 application-circuit configuration.

  2. PHOTODIODE TRANSIMPEDANCE (2 of 16: A_PD1, A_PD2) -- spec Sec.6.3.1: photodiodes are
     PASSIVE, so the input stage is a TIA, not a difference amplifier. The photodiode itself
     lives at the remote sensor end (not on this board -- like the microphone/accelerometer/
     joystick, only the local half of the circuit is captured here); its cathode rides the
     coax centre conductor into this board's summing junction, its anode rides the shield.
     TIA "-" (the summing junction) takes the clamp node (same role "+"/plain-diff-amp's
     signal input plays elsewhere); TIA "+" is referenced to the LOCAL SHIELD node, not AGND
     -- this is what gives the same shield-disturbance immunity the plain difference-amp
     channels get: negative feedback forces the summing junction (and hence the photodiode's
     cathode) to track the non-inverting input, so BOTH the photodiode's terminals sit at
     whatever the shield's local potential is, at every instant -- true photovoltaic
     (zero-bias) operation is preserved regardless of what the shield is doing relative to
     AGND, rather than a shield excursion appearing as an actual bias voltage across the
     diode (which a naive REF-to-AGND TIA would allow, and which risks forward-biasing a
     "zero bias" photodiode into real conduction under a large enough disturbance -- a
     harder failure than a linear common-mode offset). One OPA2197xD (dual, TI OPA197
     family: e-trim precision, ~10MHz GBW, CMOS input -- pA-range bias current, matching
     what a TIA needs to avoid its own added dark-current-like error) covers both PD1 and
     PD2's TIA stage in one SOIC-8 package. Rf=1M/Cf=3.3pF feedback per spec Sec.6.3.1's own
     worked example ("1 MOhm feedback resistor and a 10 MHz op-amp"), sized here for 6m of
     coax (~600pF, this task's own design point; spec's own worked example used 3-5m/
     300-500pF and landed ~57kHz) via the standard TIA compensation formula
     Cf = sqrt(Cin / (2*pi*Rf*GBW)): sqrt(600pF / (2*pi*1M*10MHz)) = 3.09pF, rounded to the
     standard 3.3pF -- closed-loop bandwidth sqrt(GBW/(2*pi*Rf*Cin)) = sqrt(10MHz/(2*pi*1M*
     600pF)) = ~51.5kHz, comfortably above the ~10kHz these channels need. Followed by a
     single-pole passive RC anti-alias low-pass (1.6k/6.8nF, fc = 1/(2*pi*R*C) = ~14.6kHz) --
     "a low-pass that doubles as anti-aliasing", spec's own phrase; no second active stage is
     needed or budgeted (the dual op-amp's own two channels are both already spoken for by
     the two TIAs).

  3. MICROPHONE: DIFFERENCE RECEIVE + 4TH-ORDER LOW-PASS (1 of 16: A_MIC) -- spec Sec.6.4:
     NI X-series has no anti-alias filter, and the mic is the only broadband source (every
     other channel is inherently band-limited by its own source). The cutoff is set by
     Intan, not NI: Intan samples at 30kHz (Nyquist 15kHz) which is TIGHTER than NI's 20kHz
     at a 40kHz scan, and a 2nd-order section is only ~4dB down at 15kHz -- hence 4th order,
     "two Sallen-Key sections on the buffer already present" (spec's own words). Read
     literally: this channel's own difference-receive stage needs an active device anyway,
     and reusing OTHER SECTIONS OF THAT SAME PACKAGE for the filter (rather than adding a
     second, filter-only part) is what "the buffer already present" and "no new part types"
     both point at. INA105 cannot fill this role (its internal resistor network is fixed --
     there is no way to configure a Sallen-Key filter around one), so A_MIC alone breaks from
     topology 1: one OPA4197xD (quad, same OPA197 family as the TIA, TSSOP/SOIC-14) provides
     (a) a DISCRETE 4-resistor difference amplifier (unit 1; 10.0k 0.1% x4, unity gain -- the
     brief's OTHER allowed topology, used here specifically because it shares a package with
     general-purpose op-amp channels the filter also needs) for the receive stage, referenced
     to AGND the same way as every other channel (R4 returns to AGND, not to a separate REF
     pin -- a plain op-amp diff-amp has no REF pin; the same electrical effect), (b) two
     cascaded unity-gain Sallen-Key low-pass sections (units 2, 3) tuned to a 4th-order
     Butterworth response at ~12kHz (Q1=0.5412, Q2=1.3066 -- the standard textbook Butterworth
     cascade Q table for a 4-pole filter, "equal-R" Sallen-Key design method: R1=R2=R,
     C1=4*Q^2*C2 -- see _mic_channel()'s own inline derivation for the exact component
     values), and (c) a safely tied-off spare 4th channel (unit 4: "+" to AGND, "-" shorted to
     its own output -- a unity-gain follower with a defined input, the standard way to leave
     an unused op-amp section from going undefined/oscillating).

  4. MISC 1-3: DIFFERENCE RECEIVE + SWITCHABLE /1//2, BOTH LEGS, MECHANICALLY GANGED (3 of
     16: A_MISC1-3) -- spec Sec.6.1: board-wide analog convention is +-5V, and "the three
     misc inputs carry switchable /1//2 attenuation so they accept +-10V". Otherwise
     identical to topology 1 (INA105), with a shunt-selectable divider inserted on BOTH
     legs -- signal (between the clamp node and the amplifier's own "+" input) AND shield
     (between the shield node and the amplifier's own "-" input): two ALWAYS-PRESENT 10.0k
     0.1% resistors per leg form a fixed /2 tap (loading the clamp/shield node negligibly --
     20k into a source the 1k series resistor, or the shield's own 10R AGND bond, already
     limits); each leg's own COMMON node (the one that actually reaches the amplifier) is
     bridged by a physical shunt to EITHER the raw node directly (/1) OR the /2 tap (/2) at
     assembly/config time -- which position is populated is not a schematic-level electrical
     fact (same "the file that places a symbol only wires ITS OWN board" discipline this
     project already applies to placeholder connectors), so this generator wires every
     header pin to its own distinct net and leaves the actual bridge to the physical shunt,
     documented on-sheet. Divider resistors built once as `_atten_divider()` and called per
     leg; the jumper header itself is built once as `_atten_leg_pair()` and called ONCE per
     channel, carrying BOTH legs on one physical part (fix round 2, below) -- neither is
     hand-duplicated.

     FIX ROUND 1 (post-review; task-10a-report.md): the version of this sheet that first
     passed architecture review divided ONLY the signal leg, leaving the shield leg wired
     straight into the INA105 "-" input undivided. With signal Vsig and shield disturbance
     Vshield (the shield's own local potential -- spec Sec.5.6's circulating-current
     concern): /1 mode correctly output Vsig (both legs undivided alike, the disturbance
     term cancels the same way it does on every other channel); /2 mode output
     Vsig/2 - Vshield/2 -- HALF the shield disturbance leaked through uncancelled, because
     attenuating one leg of a difference amplifier without the other is a leg-gain
     mismatch, and common-mode rejection only holds when both legs see the same gain.
     Giving the shield leg the IDENTICAL divider structure the signal leg already had
     fixes this: /2 mode now computes (clamp_net/2) - (shield_net/2) =
     (Vsig+Vshield)/2 - Vshield/2 = Vsig/2 exactly -- the same clean cancellation /1 mode
     already had, now in both jumper positions. Resistor tolerance also tightened from the
     original's unstated (1%) to 0.1% on all four divider resistors (both legs): matched-
     leg tolerance now matters here for the same CMRR reason mic_channel()'s own discrete
     diff-amp already uses 0.1% parts, since a leg-to-leg mismatch (not just an individual
     divider's own accuracy) is exactly what this fix exists to close out.

     Fix round 1 left the two per-channel jumpers (signal leg, shield leg) on TWO
     INDEPENDENT `Conn_01x03` headers, documented -- both headers' own description text and
     on-sheet -- as "MUST be set to the SAME position", not enforced by the schematic
     itself: which position a shunt jumper occupies is already a physical/assembly-time
     fact this project's own convention leaves out of schematic capture. Fix round 1's own
     docstring named the residual risk explicitly: "A single mechanically-ganged 2-pole
     part... would remove that residual assembly-discipline risk, but was judged more
     component-library risk than this fix warrants: two of an ALREADY-PROVEN part
     (Connector_Generic:Conn_01x03)... versus a part this project has never used."

     FIX ROUND 2 (post-review; task-10b-report.md): the judgment call fix round 1 made
     turned out wrong the moment a reviewer asked "what happens years from now when someone
     reconfigures one channel's range and forgets the second jumper" -- a genuine
     field-reconfiguration risk, not merely an initial-assembly one, and "documented on two
     headers" does not survive that scenario: nothing on the board stops the two shunts
     from disagreeing, silently re-creating the exact half-cancelled-disturbance defect fix
     round 1 itself closed out. Replaces the two independent `Conn_01x03` headers with ONE
     `Conn_02x03` header (`_atten_leg_pair()`, below) populated with a SINGLE 2-gang
     shorting block spanning both legs at the same column position -- `Conn_02x03` is
     already in the `Connector_Generic` family this exact sheet already uses (`Conn_01x03`),
     so this needed no new symbol or footprint authoring, resolving fix round 1's own
     "more component-library risk than this fix warrants" concern: there IS no new
     component library risk here, only a different member of an already-proven family.
     Both legs' own jumper positions are now the SAME PHYSICAL PART -- disagreement is a
     structural impossibility, not an assembly-discipline hope.

     Placed BEFORE the amplifier (not after), on both legs, same as the original design:
     INA105's own linear common-mode range is comfortably exceeded by a raw +-10V input on
     a +-12V supply, so the divider has to land the signal at +-5V (this board's own
     native convention) before it reaches the amplifier, not after. This fix does not
     change that exposure: the "+" leg sees exactly what it saw before (clamp_net
     undivided at /1, clamp_net/2 at /2, unchanged); the "-" leg's own swing only SHRINKS
     (shield_net undivided at /1, unchanged from before the fix, down to shield_net/2 at
     /2, previously undivided) -- so common-mode margin at the amplifier's own inputs can
     only improve or hold relative to the design that already passed review, never worsen.

Consumes (Task 7, already committed on power.kicad_sch, same global-label names): `+12V`,
`-12V`, `AGND`. Produces (this task's own net contract, spec Sec.3.2's "16 sources" table):
`A_EYE_LX`, `A_EYE_LY`, `A_EYE_RX`, `A_EYE_RY`, `A_EYE_LP`, `A_EYE_RP`, `A_PD1`, `A_PD2`,
`A_AMB`, `A_ACC`, `A_JOY_X`, `A_JOY_Y`, `A_MIC`, `A_MISC1`, `A_MISC2`, `A_MISC3`.

None of these 16 nets trips kicad-cli sch erc's own `isolated_pin_label` warning ("Label
connected to only one pin"), even though Tasks 10b/10c/10d (NI fan-out, mux+Intan diff-amps,
comparators) -- the sheets that will eventually LOAD each one -- don't exist yet: every net's
OWN driving stage already puts a second pin on it by construction, not as a workaround for
this check specifically. INA105's application circuit shorts SENSE to OUTPUT (both pins carry
the channel's own net); the photodiode AA filter's R and C share their net at the filter's own
midpoint; the MIC channel's final Sallen-Key section shorts its own output to its own inverting
input (unity-gain feedback). Confirmed empirically, not assumed: `kicad-cli sch erc` on the
full hierarchy reports 31 warnings, all pre-existing (Task 8's 9 `A_*_TPC` task-PC-connector
placeholders awaiting THIS task, Tasks 8/9's 19 `*_BUF` outbound-buffer nets awaiting Task 11's
optocouplers, Task 9's 3 `USB_*_PI` nets awaiting Task 12) -- zero of the 31 name any of this
sheet's own 16 `A_*` nets. See check_breakout_analog_frontend_netlist.py and
task-10a-report.md for the full accounting.

Non-negotiables carried over from every prior child-sheet generator (hardware/README.md's own
"KiCad gotchas" -- constraint numbers below match this task's own brief):
  1. lib_symbols handled entirely by kicad_sch.py's own extract_symbol()/ensure_lib_symbol()
     machinery (full lib id keys, bare child-unit names) -- this file never hand-writes
     lib_symbols text, so constraint 1 is satisfied by construction, not by care.
  2. `(instances (path ...))` -- this sheet reads breakout.kicad_sch's own committed text for
     find_root_uuid()/find_sheet_instance_path(), exactly like power/taskpc-digital/
     pi-interface before it.
  3. Refdes seeding clears ALL three already-committed siblings (power, taskpc-digital,
     pi-interface) via merge_max_refs(), not just the most recent one.

Run directly: `python3 hardware/gen/gen_breakout_analog_frontend.py` (writes
hardware/breakout/sheets/analog-frontend.kicad_sch). Requires
hardware/breakout/breakout.kicad_sch and all three sibling sheets to already exist on disk.

hardware/breakout/sym-lib-table gained one new entry for this task: `Amplifier_Difference`
(INA105KU) -- `Amplifier_Operational` (OPA2197xD/OPA4197xD) was already registered at Task 6.
No new fp-lib-table entries were needed (Package_SO, Package_TO_SOT_SMD, Connector_Coaxial,
Connector_Dsub, Connector_PinHeader_2.54mm, Resistor_SMD, Capacitor_SMD were all already
registered by Tasks 7-9).
"""
from pathlib import Path

from kicad_sch import (
    Sch,
    find_max_refs,
    find_root_uuid,
    find_sheet_instance_path,
    merge_max_refs,
    pin_pos,
    write_project_stub,
)

OUT = Path(__file__).resolve().parent.parent / "breakout" / "sheets"
BREAKOUT_ROOT_SCH = OUT.parent / "breakout.kicad_sch"
POWER_SCH = OUT / "power.kicad_sch"                    # already-committed sibling (Task 7)
TASKPC_SCH = OUT / "taskpc-digital.kicad_sch"           # already-committed sibling (Task 8)
PI_INTERFACE_SCH = OUT / "pi-interface.kicad_sch"       # already-committed sibling (Task 9)
ANALOG_FRONTEND_SHEETFILE = "sheets/analog-frontend.kicad_sch"  # exactly as gen_breakout.py's
# own SHEET_NAMES / f"sheets/{name}.kicad_sch" spells it.

# ---------------------------------------------------------------------------
# Footprints -- same discipline as every other generator: the real part's own package
# where one is already fixed by this task's design, a real stock KiCad footprint otherwise.
# ---------------------------------------------------------------------------
FOOTPRINT_R = "Resistor_SMD:R_0603_1608Metric"
FOOTPRINT_C_SMALL = "Capacitor_SMD:C_0603_1608Metric"
FOOTPRINT_SOT23 = "Package_TO_SOT_SMD:SOT-23"                       # BAT54S
FOOTPRINT_SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"             # INA105KU, OPA2197xD
FOOTPRINT_SOIC14 = "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"           # OPA4197xD
FOOTPRINT_HDR1X03 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
FOOTPRINT_HDR2X03 = "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical"  # MISC1-3's
# /1//2 shunt header, fix round 2 -- see _atten_leg_pair()'s own docstring. Same
# Connector_PinHeader_2.54mm family/pitch as FOOTPRINT_HDR1X03 above, already registered.
FOOTPRINT_BNC = "Connector_Coaxial:BNC_PanelMountable_Vertical"     # same isolated (2-pad,
# no separate chassis pad) BNC every other panel BNC on this board uses -- spec Sec.9.1's
# "Isolated BNCs throughout", not a different/new part for these 10 positions.
FOOTPRINT_DSUB37 = (
    "Connector_Dsub:DSUB-37_Pins_Horizontal_P2.77x2.84mm_EdgePinOffset9.90mm_Housed_"
    "MountingHolesOffset11.32mm"
)  # real, current, stock KiCad DSUB-37 male ("Pins", matching the symbol's own "board
# carries MALE" per hardware/README.md), Housed (panel-lockable) -- like every connector in
# this project not yet locked to a specific ordered MPN, the exact manufacturer part is a
# layout-stage/procurement decision this schematic-capture task does not make (see
# hardware/README.md's "Custom connector footprints" section for the project's convention).

# ---------------------------------------------------------------------------
# Layout grid -- 1.27mm (KiCad's schematic connection grid), same GRID() helper every
# generator in this project uses.
# ---------------------------------------------------------------------------
G = 1.27


def GRID(v: float) -> float:
    return round(round(v / G) * G, 2)


Y0 = GRID(20)
ROW_DY = GRID(22.86)  # 0.9in -- comfortable room for a row's own satellite passives

X_SRC = GRID(20)          # BNC connector (BNC-fronted channels) / unused for ACCESIO rows
X_R1K = GRID(45)          # series 1k
X_CLAMP = GRID(65)        # BAT54S
X_SHELLR = GRID(45)       # shell-bond 10R (BNC channels only) -- offset below the row
SHELLR_DY = GRID(10.16)
X_DIFFAMP = GRID(95)      # INA105 (plain channels, ACCESIO channels)
DECOUPLE_DX = GRID(15.24)
DECOUPLE_DY = GRID(5.08)  # NOT 7.62: Device:C's own pins sit +-3.81mm local from its origin,
# so a decoupling pair at row_y -+ DECOUPLE_DY reaches as far as row_y -+ (DECOUPLE_DY+3.81).
# ROW_DY=22.86 is exactly 2x11.43 -- 7.62+3.81 -- so at DECOUPLE_DY=7.62 every row's own
# far decoupling pin (AGND, on the -12V cap's pin 2) lands EXACTLY on the NEXT row's own
# near decoupling pin (+12V, on that row's +12V cap's pin 1): a real short between two
# entire nets, not a cosmetic overlap -- caught by kicad-cli sch erc's own
# `multiple_net_names` ("Both +12V and AGND are attached to the same items"; confirmed
# independently by tracing every Sch.label() call in-process and finding 10 coordinates
# with 2 distinct net names, all explained by this one systematic off-by-exactly-half-a-row
# collision). 5.08+3.81=8.89mm reach, comfortably inside half a row (11.43mm) with margin.

X_DIV = GRID(80)          # MISC only: /1//2 divider resistors (SIGNAL leg)
X_JUMPER = GRID(80)       # MISC only: the /1//2 shunt header (BOTH legs, fix round 2 --
# see _atten_leg_pair()'s own docstring), offset below the SIGNAL leg's own divider.
JUMPER_DY = GRID(11.43)
X_DIV_SHLD = GRID(99)     # MISC only: mirrored /1//2 divider resistors (SHIELD leg, fix
# round 1) -- a FRESH X column, deliberately not X_DIV: this sheet's own history
# (task-10a-report.md, Concern 2) already hit a real rail short from two rows' own Y
# arithmetic coinciding exactly, and the same trap is live here too -- (X_DIV, row_y -
# JUMPER_DY) for row i lands EXACTLY on (X_DIV, row_y + JUMPER_DY) for row i-1 (both MISC
# rows; JUMPER_DY is exactly half of ROW_DY), so mirroring the shield leg's own divider at
# the signal leg's own X with a flipped-sign Y offset would silently short row i's own
# shield-leg divider onto row (i-1)'s own signal-leg jumper header. A distinct X sidesteps
# the question by construction; confirmed empirically collision-free (not merely reasoned
# about) by this task's own coordinate-collision scan and by `kicad-cli sch erc` reporting
# zero new violations -- see task-10a-report.md. (Fix round 2 removed the shield leg's own
# SEPARATE jumper-header X column, X_JUMPER_SHLD == X_DIV_SHLD pre-fix-round-2: there is
# now only ONE merged header, placed at X_JUMPER, carrying both legs -- see
# _atten_leg_pair()'s own docstring. X_DIV_SHLD remains, unchanged, for the shield leg's
# own divider RESISTORS, which fix round 2 does not touch.)
X_DIFFAMP_MISC = GRID(118)

X_TIA = GRID(90)          # photodiode TIA op-amp unit
X_RF = GRID(90)           # Rf/Cf feedback, offset above/below the TIA
RF_DY = GRID(8.89)
X_AAR = GRID(112)         # anti-alias RC low-pass
X_AAC = GRID(128)

X_MIC_DA = GRID(95)       # MIC discrete diff-amp op-amp unit
X_MIC_DA_R = GRID(80)     # MIC discrete diff-amp's own 4 resistors
X_MIC_SK1 = GRID(140)     # Sallen-Key section 1 op-amp unit
X_MIC_SK1_R = GRID(125)
X_MIC_SK2 = GRID(185)     # Sallen-Key section 2 op-amp unit
X_MIC_SK2_R = GRID(170)
X_MIC_SPARE = GRID(225)   # spare 4th quad channel, tied off

X_ACCESIO = GRID(20)
Y_ACCESIO = GRID(410)
X_ACCESIO_BONDR = GRID(70)

X_NOTE1, Y_NOTE1 = GRID(240), GRID(20)
X_NOTE2, Y_NOTE2 = GRID(240), GRID(90)
X_NOTE3, Y_NOTE3 = GRID(240), GRID(160)
X_NOTE4, Y_NOTE4 = GRID(240), GRID(230)
X_NOTE5, Y_NOTE5 = GRID(240), GRID(320)
NOTE_DY = GRID(5.08)

# ---------------------------------------------------------------------------
# Channel contract -- spec Sec.3.2's "16 sources" table, this task's own net names.
# ---------------------------------------------------------------------------
ACCESIO_MAP = [  # (DB37 pin number carrying this DAC channel, contract net, description)
    ("20", "A_EYE_LX", "Eye X, left (ACCESIO DAC0)"),
    ("21", "A_EYE_LY", "Eye Y, left (ACCESIO DAC1)"),
    ("22", "A_EYE_RX", "Eye X, right (ACCESIO DAC2)"),
    ("23", "A_EYE_RY", "Eye Y, right (ACCESIO DAC3)"),
    ("24", "A_EYE_LP", "Eye pupil, left (ACCESIO DAC4)"),
    ("25", "A_EYE_RP", "Eye pupil, right (ACCESIO DAC5)"),
]
ACCESIO_SPARE_PINS = tuple(str(n) for n in range(26, 36)) + ("36", "37")  # DAC6-DAC15 (pins
# 26-35): the symbol models the connector's full 16-channel DB37 pin map (it is the SAME
# physical pinout as the USB-AO16-16A, per gen_wl_sync_lib.py's own SYM_ACCESIO docstring --
# "the -8A this board actually uses is the same connector/pinout, populated to DAC0-DAC7 of
# the 16 available"), and only 6 of those populated 8 (DAC0-DAC5) carry this task's own 16
# sources -- DAC6/DAC7 (pins 26/27) are populated-but-unused, DAC8-DAC15 (pins 28-35) are
# simply absent on the -8A card. Plus A_IN0/A_IN1 (36/37, the ACCES card's own auxiliary
# inputs -- not part of this task's 16-source contract). All 12 marked no_connect.

PLAIN_BNC_MAP = [  # (contract net, description)
    ("A_AMB", "Ambient light sensor (battery-powered, in booth)"),
    ("A_ACC", "Accelerometer motion energy (battery-powered custom device)"),
    ("A_JOY_X", "Joystick X (battery-powered, in booth)"),
    ("A_JOY_Y", "Joystick Y (battery-powered, in booth)"),
]
MISC_MAP = [
    ("A_MISC1", "Misc analog input 1 (panel BNC, /1//2 selectable)"),
    ("A_MISC2", "Misc analog input 2 (panel BNC, /1//2 selectable)"),
    ("A_MISC3", "Misc analog input 3 (panel BNC, /1//2 selectable)"),
]

CONTRACT_NETS_PRODUCED = (
    [net for _, net, _ in ACCESIO_MAP]
    + ["A_PD1", "A_PD2", "A_MIC"]
    + [net for net, _ in PLAIN_BNC_MAP]
    + [net for net, _ in MISC_MAP]
)
assert len(CONTRACT_NETS_PRODUCED) == 16
assert len(set(CONTRACT_NETS_PRODUCED)) == 16
CONTRACT_NETS_CONSUMED = ["+12V", "-12V", "AGND"]


# ---------------------------------------------------------------------------
# Low-level helpers -- duplicated from prior generators' own two_pin()/bidirectional_clamp()
# rather than imported: kicad_sch.py, not any one generator, is this project's shared
# machinery (same convention every generator here follows).
# ---------------------------------------------------------------------------


def lbl(sch, x, y, pins, pin_num, net):
    """Label one pin of an already-placed instance. `pins` is what place() returned;
    `x, y` its own placement origin. Collapses the pin_pos()+label() pair used at nearly
    every call site below into one line."""
    px, py = pin_pos(x, y, pins[pin_num])
    sch.label(net, px, py)


def two_pin(sch, libname, symname, ref_prefix, value, x, y, net1, net2, footprint=""):
    """Place a 2-pin part between two labeled nets -- same pattern as every other
    generator in this project's own two_pin()."""
    ref = sch.next_ref(ref_prefix)
    pins = sch.place(libname, symname, ref, value, x, y, footprint=footprint)
    lbl(sch, x, y, pins, "1", net1)
    lbl(sch, x, y, pins, "2", net2)
    return ref


def decouple(sch, x, y, rail):
    return two_pin(sch, "Device", "C", "C", "100nF", x, y, rail, "AGND", footprint=FOOTPRINT_C_SMALL)


def bidirectional_clamp(sch, x, y, net_signal, net_hi, net_lo):
    """Place a BAT54S wired as a genuine two-rail series-pair clamp -- IDENTICAL topology
    to gen_breakout_taskpc_digital.py's own bidirectional_clamp() (itself reused verbatim
    from gen_mule.py, validated on the mule board), just against +-12V instead of DGND/+5V:
    every front end on this sheet clamps to the SAME dual rails its own active device is
    powered from (the standard "clamp to the supply rails" input-protection pattern), rather
    than to some other reference.

    pin 1 "A" (anode only) -> net_lo (-12V); pin 3 "COM" (the genuine series midpoint) ->
    net_signal; pin 2 "K" (cathode only) -> net_hi (+12V). D1 = anode(1)->cathode(3=COM)
    conducts when net_lo exceeds signal by more than a diode drop (low-side clamp); D2 =
    anode(3=COM)->cathode(2) conducts when signal exceeds net_hi by more than a diode drop
    (high-side clamp). Confirmed against Diode:BAT54S's own extracted pin table (pin1=A,
    pin2=K, pin3=COM) -- same derivation gen_breakout_taskpc_digital.py's own docstring
    gives, not re-guessed here.
    """
    ref = sch.next_ref("D")
    pins = sch.place("Diode", "BAT54S", ref, "BAT54S", x, y, footprint=FOOTPRINT_SOT23)
    lbl(sch, x, y, pins, "1", net_lo)
    lbl(sch, x, y, pins, "3", net_signal)
    lbl(sch, x, y, pins, "2", net_hi)
    return ref


def bnc_front_end(sch, x, y, name, desc):
    """The stage common to all 10 BNC-sourced channels: isolated BNC -> 1k series -> BAT54S
    clamp to +-12V. Returns (clamp_net, shield_net) for the caller's own back-half to
    consume -- clamp_net is the post-series-resistor node (BOTH the diode clamp's COM and
    whatever comes next's own signal input); shield_net is the connector's OWN shell pin,
    directly (no series element) -- the "senses centre against shield AT THE CONNECTOR"
    requirement (spec Sec.5.6), not some already-processed version of it.

    shield_net additionally gets its own 10R bond to AGND here (spec Sec.5.6: "The shield
    ties to AGND through ~10 Ohm, providing the return and a DC reference") -- placed as a
    THIRD, independent label on the same net (BNC shell pin, resistor's own far end, and
    -- wired by the caller -- the receiving amplifier's reference input all share this one
    label), not as a two_pin() bridging the connector itself (the connector's shell pin IS
    already on shield_net directly; the 10R sits in a PARALLEL branch to AGND, not in series
    between the connector and the amplifier).
    """
    ref = sch.next_ref("J")
    pins = sch.place("Connector", "Conn_Coaxial", ref, desc, x, y, footprint=FOOTPRINT_BNC)
    raw_net = f"{name}_RAW"
    clamp_net = f"{name}_CLAMP"
    shield_net = f"{name}_SHLD"
    lbl(sch, x, y, pins, "1", raw_net)     # "In" -- coax centre conductor
    lbl(sch, x, y, pins, "2", shield_net)  # "Ext" -- shell
    two_pin(sch, "Device", "R", "R", "1k", x + X_R1K, y, raw_net, clamp_net, footprint=FOOTPRINT_R)
    two_pin(
        sch, "Device", "R", "R", "10", x + X_SHELLR, y + SHELLR_DY, shield_net, "AGND",
        footprint=FOOTPRINT_R,
    )
    bidirectional_clamp(sch, x + X_CLAMP, y, clamp_net, "+12V", "-12V")
    return clamp_net, shield_net


def diffamp_ina105(sch, x, y, plus_net, minus_net, out_net):
    """INA105KU wired as a unity-gain difference receiver -- the standard application
    circuit straight from TI's own datasheet: SENSE (5) shorted to OUTPUT (6), REF (1) tied
    to the reference the output should land relative to (AGND, here, for every caller --
    this is what puts the output back in the AGND domain regardless of whatever minus_net
    is doing). "+"/"-" (3/2) take the signal/shield pair.
    """
    ref = sch.next_ref("U")
    pins = sch.place("Amplifier_Difference", "INA105KU", ref, "INA105KU", x, y, footprint=FOOTPRINT_SOIC8)
    lbl(sch, x, y, pins, "1", "AGND")
    lbl(sch, x, y, pins, "2", minus_net)
    lbl(sch, x, y, pins, "3", plus_net)
    lbl(sch, x, y, pins, "4", "-12V")
    lbl(sch, x, y, pins, "5", out_net)
    lbl(sch, x, y, pins, "6", out_net)
    lbl(sch, x, y, pins, "7", "+12V")
    # pin 8 (NC) is typed no_connect in the library itself -- no explicit marker needed
    # (unlike a genuinely passive/unused pin), same as every other symbol in this project
    # whose library already types a pin no_connect.
    decouple(sch, x - DECOUPLE_DX, y - DECOUPLE_DY, "+12V")
    decouple(sch, x - DECOUPLE_DX, y + DECOUPLE_DY, "-12V")
    return ref


# ---------------------------------------------------------------------------
# Per-source-type channel builders
# ---------------------------------------------------------------------------


def plain_bnc_channel(sch, y, name, desc):
    clamp_net, shield_net = bnc_front_end(sch, X_SRC, y, name, desc)
    diffamp_ina105(sch, X_DIFFAMP, y, clamp_net, shield_net, name)


def _atten_divider(sch, x_div, y, raw_net, tag):
    """One leg's own ALWAYS-PRESENT /2-tap divider -- unchanged in substance from fix round
    1's own single-leg `_atten_leg()`, factored out separately now that the JUMPER header
    itself is shared between both legs (see `_atten_leg_pair()`) rather than placed once per
    leg. Two 10.0k 0.1% resistors form a fixed /2 tap from raw_net to AGND (0.1%: leg-to-leg
    matching matters here, the same CMRR reason mic_channel()'s own discrete diff-amp
    already uses 0.1% parts for). Returns (mid_net, div_net) -- div_net is NOT yet wired to
    anything (the header doesn't exist until the caller places it); it is simply this leg's
    own reserved "COMMON, to the amplifier" net name.
    """
    mid_net = f"{tag}_MID"   # the /2 tap (always present, whether or not selected)
    div_net = f"{tag}_DIV"   # what the amplifier's own "+"/"-" input actually sees
    two_pin(
        sch, "Device", "R", "R", "10.0k 0.1%", x_div, y - GRID(6.35), raw_net, mid_net,
        footprint=FOOTPRINT_R,
    )
    two_pin(
        sch, "Device", "R", "R", "10.0k 0.1%", x_div, y + GRID(6.35), mid_net, "AGND",
        footprint=FOOTPRINT_R,
    )
    return mid_net, div_net


def _atten_leg_pair(sch, x_div_sig, x_div_shld, y, x_jum, y_jum, sig_raw_net, shld_raw_net, tag, desc):
    """FIX ROUND 2 (post-review; task-10b-report.md): replaces fix round 1's own TWO
    INDEPENDENT `Conn_01x03` shunt headers (one per leg, each its own physically separate
    jumper -- see task-10a-report.md's own "Fix round 1") with ONE `Conn_02x03_Top_Bottom`
    header carrying BOTH legs, so a single mechanically-ganged 2-GANG SHORTING BLOCK (never
    two independent 1-gang shunts -- see this header's own Description property and the
    on-sheet note below) bridges both legs' identical column position simultaneously. This
    is what upgrades "both jumpers MUST be set to the same position" from a documentation-
    only requirement -- fix round 1's own residual risk, named explicitly in its own
    docstring: "A single mechanically-ganged 2-pole part... would remove that residual
    assembly-discipline risk, but was judged more component-library risk than this fix
    warrants" -- to a PHYSICAL IMPOSSIBILITY: a 2-gang shorting block spanning columns (1,2)
    or (2,3) cannot be placed with one gang at one column and the other gang at a different
    column, because it is one mechanical part. `Connector_Generic:Conn_02x03` is already a
    registered, already-used symbol family on this sheet (Conn_01x03), so this needs no new
    symbol or footprint authoring -- see hardware/README.md.

    `Conn_02x03_Top_Bottom`'s own pin numbering (confirmed directly against the raw
    Connector_Generic library text via kicad_sch.py's own extract_symbol()/unit_pins(), not
    assumed): physical ROW 1 = pins 1,2,3 (one column position each), ROW 2 = pins 4,5,6,
    with pin N and pin N+3 sharing the SAME column position (e.g. pins 1 and 4 both sit at
    the header's own first column) -- exactly the geometry a 2-gang shorting block needs,
    since it spans two ADJACENT COLUMNS across BOTH rows at once. Row 1 (pins 1-3) carries
    the SIGNAL leg, row 2 (pins 4-6) the SHIELD leg, each at the identical per-leg role fix
    round 1's own single-header pin1=raw/pin2=common/pin3=mid convention already
    established: pin1=sig_raw pin2=sig_common(to amp) pin3=sig_mid; pin4=shld_raw
    pin5=shld_common(to amp) pin6=shld_mid. A 2-gang shorting block at column position
    (1,2) bridges pin1-pin2 AND pin4-pin5 together (x1, both legs undivided); at (2,3) it
    bridges pin2-pin3 AND pin5-pin6 together (x2, both legs divided) -- the same x1/x2
    semantics fix round 1 already had, now unable to disagree between legs by construction.

    Returns (sig_div_net, shld_div_net) -- what the amplifier's own "+"/"-" inputs wire to.
    """
    sig_mid, sig_div = _atten_divider(sch, x_div_sig, y, sig_raw_net, tag)
    shld_mid, shld_div = _atten_divider(sch, x_div_shld, y, shld_raw_net, f"{tag}_SHLD")

    jref = sch.next_ref("J")
    jpins = sch.place(
        "Connector_Generic", "Conn_02x03_Top_Bottom", jref,
        f"{desc} -- /1//2 shunt header, BOTH legs (fix round 2): row 1 (pins 1-3) = "
        f"SIGNAL leg, row 2 (pins 4-6) = SHIELD leg. Populate with ONE 2-GANG SHORTING "
        f"BLOCK spanning both rows at the SAME column position -- pins 1-2 + 4-5 for x1 "
        f"(direct), pins 2-3 + 5-6 for x2 (divider tap) -- NEVER two independent 1-gang "
        f"shunts: that is the exact defect this header replaces (fix round 1 left it "
        f"physically possible for the two legs' own jumpers to disagree; a mechanically-"
        f"ganged 2-gang block makes disagreement physically impossible instead of merely "
        f"documented against).",
        x_jum, y_jum, footprint=FOOTPRINT_HDR2X03,
    )
    lbl(sch, x_jum, y_jum, jpins, "1", sig_raw_net)
    lbl(sch, x_jum, y_jum, jpins, "2", sig_div)
    lbl(sch, x_jum, y_jum, jpins, "3", sig_mid)
    lbl(sch, x_jum, y_jum, jpins, "4", shld_raw_net)
    lbl(sch, x_jum, y_jum, jpins, "5", shld_div)
    lbl(sch, x_jum, y_jum, jpins, "6", shld_mid)
    return sig_div, shld_div


def misc_channel(sch, y, name, desc):
    """Adds a /1//2 selectable divider on BOTH legs -- signal (clamp_net) and shield --
    between the clamp/shield nodes and the amplifier's own "+"/"-" inputs, the two legs'
    own dividers matched resistor-for-resistor and their own jumper positions now
    MECHANICALLY GANGED on one `Conn_02x03` header (fix round 2 -- see
    `_atten_leg_pair()`'s own docstring), so common-mode shield disturbance still cancels
    at the INA105 output regardless of which position the (now physically inseparable)
    2-gang shorting block is set to. See module docstring, topology 4 ("FIX ROUND 1"), for
    the full derivation of why the shield leg needs its own divider at all -- the original
    version of this channel divided the signal leg only, which left half the shield
    disturbance uncancelled in /2 mode; fix round 2 (this version) closes the RESIDUAL risk
    fix round 1 left open, that the two legs' own separately-settable jumpers could still
    be left in disagreeing positions by an assembly or field-reconfiguration mistake.
    """
    clamp_net, shield_net = bnc_front_end(sch, X_SRC, y, name, desc)
    sig_div, shld_div = _atten_leg_pair(
        sch, X_DIV, X_DIV_SHLD, y, X_JUMPER, y + JUMPER_DY, clamp_net, shield_net, name, desc,
    )
    diffamp_ina105(sch, X_DIFFAMP_MISC, y, sig_div, shld_div, name)


def place_accesio_block(sch):
    """Places the ACCESIO connector ONCE (it carries all 6 eye channels), ties its 19 AGND
    pins together into one local net and bonds THAT to AGND through ~10R -- the identical
    "shield" role a BNC's own shell plays elsewhere on this sheet, just applied to a
    multi-pin ground bundle instead of a coax shield (module docstring: "the six ACCESIO
    eye channels get the same treatment... it sits on a separately-earthed eye-tracker PC,
    the same class of ground offset"). Spare DAC6/DAC7 and the card's own A_IN0/A_IN1 are
    genuinely unused by this task's 16-source contract and are marked no_connect --
    ACCESIO_AO16_DB37M's pins are typed passive (a generic connector), so (unlike INA105's
    own library-typed NC pin) an explicit marker IS needed here or ERC flags them unconnected.
    """
    ref = sch.next_ref("J")
    pins = sch.place(
        "wl-sync", "ACCESIO_AO16_DB37M", ref,
        "ACCES I/O USB-AO16-8A mating DB37 -- six eye channels (spec Sec.3.2)",
        X_ACCESIO, Y_ACCESIO, footprint=FOOTPRINT_DSUB37,
    )
    for n in range(1, 20):
        lbl(sch, X_ACCESIO, Y_ACCESIO, pins, str(n), "ACCESIO_AGND")
    for pin_num, net, _desc in ACCESIO_MAP:
        lbl(sch, X_ACCESIO, Y_ACCESIO, pins, pin_num, f"{net}_DAC")
    for pin_num in ACCESIO_SPARE_PINS:
        px, py = pin_pos(X_ACCESIO, Y_ACCESIO, pins[pin_num])
        sch.no_connect(px, py)
    two_pin(
        sch, "Device", "R", "R", "10", X_ACCESIO_BONDR, Y_ACCESIO, "ACCESIO_AGND", "AGND",
        footprint=FOOTPRINT_R,
    )
    return ref


def accesio_channel(sch, y, name, desc):
    """One eye channel's own 1k series + BAT54S clamp + INA105 receive stage -- identical
    back half to plain_bnc_channel()'s, front end is the ACCESIO connector's own DACn pin
    (labelled by place_accesio_block()) instead of a BNC, and the "shield"/reference is the
    shared ACCESIO_AGND net instead of a per-channel 10R shell node.
    """
    dac_net = f"{name}_DAC"
    clamp_net = f"{name}_CLAMP"
    two_pin(sch, "Device", "R", "R", "1k", X_R1K, y, dac_net, clamp_net, footprint=FOOTPRINT_R)
    bidirectional_clamp(sch, X_CLAMP, y, clamp_net, "+12V", "-12V")
    diffamp_ina105(sch, X_DIFFAMP, y, clamp_net, "ACCESIO_AGND", name)


def _tia_channel(sch, y, x_tia, unit_pins, minus_pin, plus_pin, out_pin, name, clamp_net, shield_net):
    """One photodiode's own TIA stage, using one unit of an already-placed dual op-amp.
    "-" (minus_pin) = summing junction = clamp_net (photodiode cathode, via the common 1k
    series + BAT54S clamp every channel gets); "+" (plus_pin) = shield_net, NOT AGND -- see
    module docstring, topology 2, for why. Rf=1M/Cf=3.3pF feedback, then a single-pole
    passive RC anti-alias low-pass (1.6k/6.8nF, fc ~14.6kHz) whose own R/C midpoint IS the
    contract net `name` -- no separate output-labelling step needed.
    """
    lbl(sch, x_tia, y, unit_pins, minus_pin, clamp_net)
    lbl(sch, x_tia, y, unit_pins, plus_pin, shield_net)
    raw_net = f"{name}_RAWOUT"
    lbl(sch, x_tia, y, unit_pins, out_pin, raw_net)
    two_pin(sch, "Device", "R", "R", "1M", X_RF, y - RF_DY, raw_net, clamp_net, footprint=FOOTPRINT_R)
    two_pin(sch, "Device", "C", "C", "3.3pF", X_RF, y + RF_DY, raw_net, clamp_net, footprint=FOOTPRINT_C_SMALL)
    two_pin(sch, "Device", "R", "R", "1.6k", X_AAR, y, raw_net, name, footprint=FOOTPRINT_R)
    two_pin(sch, "Device", "C", "C", "6.8nF", X_AAC, y, name, "AGND", footprint=FOOTPRINT_C_SMALL)


def photodiode_channels(sch, y_pd1, y_pd2):
    """Both photodiode channels share ONE OPA2197xD (dual): unit 1 -> PD1's TIA, unit 2 ->
    PD2's TIA, unit 3 -> the package's own shared power pins (+-12V) plus decoupling. See
    module docstring, topology 2, for the full TIA derivation (Rf/Cf sizing, why "+" is
    shield-referenced not AGND-referenced).
    """
    clamp1, shield1 = bnc_front_end(sch, X_SRC, y_pd1, "A_PD1", "Photodiode 1 (passive, coax, photovoltaic mode)")
    clamp2, shield2 = bnc_front_end(sch, X_SRC, y_pd2, "A_PD2", "Photodiode 2 (passive, coax, photovoltaic mode)")

    ref = sch.next_ref("U")
    y_pwr = (y_pd1 + y_pd2) / 2
    pins1 = sch.place("Amplifier_Operational", "OPA2197xD", ref, "OPA2197IDR", X_TIA, y_pd1, unit=1, footprint=FOOTPRINT_SOIC8)
    pins2 = sch.place("Amplifier_Operational", "OPA2197xD", ref, "OPA2197IDR", X_TIA, y_pd2, unit=2, footprint=FOOTPRINT_SOIC8)
    pinsp = sch.place("Amplifier_Operational", "OPA2197xD", ref, "OPA2197IDR", X_TIA, y_pwr, unit=3, footprint=FOOTPRINT_SOIC8)
    lbl(sch, X_TIA, y_pwr, pinsp, "4", "-12V")
    lbl(sch, X_TIA, y_pwr, pinsp, "8", "+12V")
    decouple(sch, X_TIA - DECOUPLE_DX, y_pwr - DECOUPLE_DY, "+12V")
    decouple(sch, X_TIA - DECOUPLE_DX, y_pwr + DECOUPLE_DY, "-12V")

    _tia_channel(sch, y_pd1, X_TIA, pins1, "2", "3", "1", "A_PD1", clamp1, shield1)
    _tia_channel(sch, y_pd2, X_TIA, pins2, "6", "5", "7", "A_PD2", clamp2, shield2)
    return ref


def _sallen_key_unity(sch, x, y, x_r, unit_pins, plus_pin, minus_pin, out_pin, in_net, out_net, ra, rb, ca, cb):
    """One unity-gain Sallen-Key low-pass section, standard non-inverting topology:
        in_net -> Ra -> n1 -> Rb -> "+"
        n1 -> Ca -> output (feedback cap)
        "+" -> Cb -> AGND
        output -> "-" directly (100% feedback -> unity gain)
        output = out_net
    `unit_pins`/`plus_pin`/`minus_pin`/`out_pin` select which op-amp unit/pins to use, so
    this one function serves both Sallen-Key sections in _mic_channel() below.
    """
    n1 = f"{out_net}_SKN"
    two_pin(sch, "Device", "R", "R", ra, x_r, y - GRID(6.35), in_net, n1, footprint=FOOTPRINT_R)
    plus_net = f"{out_net}_SKP"
    two_pin(sch, "Device", "R", "R", rb, x_r, y + GRID(6.35), n1, plus_net, footprint=FOOTPRINT_R)
    lbl(sch, x, y, unit_pins, plus_pin, plus_net)
    lbl(sch, x, y, unit_pins, out_pin, out_net)
    two_pin(sch, "Device", "C", "C", ca, x_r + GRID(12.7), y - GRID(6.35), n1, out_net, footprint=FOOTPRINT_C_SMALL)
    two_pin(sch, "Device", "C", "C", cb, x_r + GRID(12.7), y + GRID(6.35), plus_net, "AGND", footprint=FOOTPRINT_C_SMALL)
    lbl(sch, x, y, unit_pins, minus_pin, out_net)  # unity-gain follower feedback


def mic_channel(sch, y, name, desc):
    """A_MIC: difference receive (discrete 4R network, unit 1 of a quad) + two cascaded
    Sallen-Key sections (units 2, 3) = 4th-order ~12kHz Butterworth low-pass, all on ONE
    OPA4197xD -- "two Sallen-Key sections on the buffer already present" (spec Sec.6.4). See
    module docstring, topology 3, for the full derivation (why INA105 can't fill this role,
    why reusing one quad package rather than adding a filter-specific part is "no new part
    types").

    4th-order Butterworth cascade Q values (standard 2-biquad table): Q1=0.5412 (section 1,
    lower Q), Q2=1.3066 (section 2, higher Q). Equal-R Sallen-Key design method: with
    R1=R2=R, Q = 0.5*sqrt(C1/C2), so C1 = 4*Q^2*C2; choosing a convenient C2 and solving
    R = 1/(2*pi*f0*sqrt(C1*C2)) for f0=12kHz:
      Section 1: C2=1.0nF, C1=4*0.5412^2*1.0nF=1.172nF -> nearest standard 1.2nF;
                 R = 1/(2*pi*12000*sqrt(1.2nF*1.0nF)) = 12.1k (E96).
                 Actual: Q=0.5*sqrt(1.2/1.0)=0.548, f0=12.0kHz.
      Section 2: C2=220pF, C1=4*1.3066^2*220pF=1502pF -> nearest standard 1.5nF;
                 R = 1/(2*pi*12000*sqrt(1.5nF*220pF)) = 23.2k (E96).
                 Actual: Q=0.5*sqrt(1500/220)=1.306, f0=11.9kHz.
    Both sections landing within ~1% of the target f0/Q using standard-value parts is the
    expected/normal outcome of this design method, not a fudge.
    """
    clamp_net, shield_net = bnc_front_end(sch, X_SRC, y, name, desc)

    ref = sch.next_ref("U")
    pins1 = sch.place("Amplifier_Operational", "OPA4197xD", ref, "OPA4197IDR", X_MIC_DA, y, unit=1, footprint=FOOTPRINT_SOIC14)
    pins2 = sch.place("Amplifier_Operational", "OPA4197xD", ref, "OPA4197IDR", X_MIC_SK1, y, unit=2, footprint=FOOTPRINT_SOIC14)
    pins3 = sch.place("Amplifier_Operational", "OPA4197xD", ref, "OPA4197IDR", X_MIC_SK2, y, unit=3, footprint=FOOTPRINT_SOIC14)
    pins4 = sch.place("Amplifier_Operational", "OPA4197xD", ref, "OPA4197IDR", X_MIC_SPARE, y - GRID(15.24), unit=4, footprint=FOOTPRINT_SOIC14)
    pinsp = sch.place("Amplifier_Operational", "OPA4197xD", ref, "OPA4197IDR", X_MIC_DA, y - GRID(15.24), unit=5, footprint=FOOTPRINT_SOIC14)
    lbl(sch, X_MIC_DA, y - GRID(15.24), pinsp, "4", "+12V")
    lbl(sch, X_MIC_DA, y - GRID(15.24), pinsp, "11", "-12V")
    decouple(sch, X_MIC_DA - DECOUPLE_DX, y - GRID(15.24) - DECOUPLE_DY, "+12V")
    decouple(sch, X_MIC_DA - DECOUPLE_DX, y - GRID(15.24) + DECOUPLE_DY, "-12V")

    # -- Discrete unity-gain difference amplifier (unit 1): R1/R2 set the inverting side,
    # R3/R4 the non-inverting side; R1=R2=R3=R4 (0.1%, matched) gives unity gain and good
    # CMRR the same way INA105's own internal network does, just built from four discrete
    # precision resistors instead of one trimmed IC network (brief's OTHER allowed topology).
    node_minus = f"{name}_DAN"
    node_plus = f"{name}_DAP"
    da_out = f"{name}_DA"
    two_pin(sch, "Device", "R", "R", "10.0k 0.1%", X_MIC_DA_R, y - GRID(6.35), clamp_net, node_minus, footprint=FOOTPRINT_R)
    two_pin(sch, "Device", "R", "R", "10.0k 0.1%", X_MIC_DA_R, y - GRID(19.05), node_minus, da_out, footprint=FOOTPRINT_R)
    two_pin(sch, "Device", "R", "R", "10.0k 0.1%", X_MIC_DA_R, y + GRID(6.35), shield_net, node_plus, footprint=FOOTPRINT_R)
    two_pin(sch, "Device", "R", "R", "10.0k 0.1%", X_MIC_DA_R, y + GRID(19.05), node_plus, "AGND", footprint=FOOTPRINT_R)
    lbl(sch, X_MIC_DA, y, pins1, "2", node_minus)
    lbl(sch, X_MIC_DA, y, pins1, "3", node_plus)
    lbl(sch, X_MIC_DA, y, pins1, "1", da_out)

    # -- Sallen-Key sections 1 and 2 --
    sk1_out = f"{name}_S1"
    _sallen_key_unity(sch, X_MIC_SK1, y, X_MIC_SK1_R, pins2, "5", "6", "7", da_out, sk1_out, "12.1k", "12.1k", "1.2nF", "1.0nF")
    _sallen_key_unity(sch, X_MIC_SK2, y, X_MIC_SK2_R, pins3, "10", "9", "8", sk1_out, name, "23.2k", "23.2k", "1.5nF", "220pF")

    # -- Spare 4th channel: safely tied off (unity-gain follower, "+" defined to AGND) --
    spare_fb = f"{name}_SPARE_FB"
    lbl(sch, X_MIC_SPARE, y - GRID(15.24), pins4, "12", "AGND")
    lbl(sch, X_MIC_SPARE, y - GRID(15.24), pins4, "13", spare_fb)
    lbl(sch, X_MIC_SPARE, y - GRID(15.24), pins4, "14", spare_fb)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build() -> tuple[Sch, dict]:
    breakout_text = BREAKOUT_ROOT_SCH.read_text()
    breakout_root_uuid = find_root_uuid(breakout_text)
    instance_path = find_sheet_instance_path(breakout_text, breakout_root_uuid, ANALOG_FRONTEND_SHEETFILE)
    ref_start = merge_max_refs(
        find_max_refs(POWER_SCH.read_text()),
        find_max_refs(TASKPC_SCH.read_text()),
        find_max_refs(PI_INTERFACE_SCH.read_text()),
    )

    sch = Sch(project="breakout", instance_path_prefix=instance_path, ref_start=ref_start)
    refs: dict = {}

    place_accesio_block(sch)
    for i, (_pin, name, desc) in enumerate(ACCESIO_MAP):
        accesio_channel(sch, Y0 + i * ROW_DY, name, desc)

    photodiode_channels(sch, Y0 + 6 * ROW_DY, Y0 + 7 * ROW_DY)
    mic_channel(sch, Y0 + 8 * ROW_DY, "A_MIC", "Microphone (battery-powered, in booth)")

    for i, (name, desc) in enumerate(PLAIN_BNC_MAP):
        plain_bnc_channel(sch, Y0 + (9 + i) * ROW_DY, name, desc)

    for i, (name, desc) in enumerate(MISC_MAP):
        misc_channel(sch, Y0 + (13 + i) * ROW_DY, name, desc)

    for line_idx, line in enumerate([
        "Analog front end (Task 10a) -- 16 sources, every one received DIFFERENTIALLY.",
        "Consumes +12V/-12V/AGND (Task 7). Produces the 16 A_* nets below; Tasks 10b/10c/",
        "10d (NI fan-out, mux+Intan diff-amps, comparators) consume them by this same",
        "global-label name -- neither exists yet, but each net's own driving stage",
        "already puts 2 pins on it (SENSE=OUTPUT / filter R+C midpoint / SK follower",
        "feedback), so kicad-cli sch erc's isolated_pin_label check does not fire here",
        "even without a downstream load -- see check_breakout_analog_frontend_netlist.py",
        "and task-10a-report.md for the full ERC accounting.",
        "",
        "WHY DIFFERENTIAL (spec Sec.5.6): the rig sits in a Faraday cage sound booth.",
        "Sensors inside are battery powered so no mains conductor crosses the cage wall --",
        "only signal, through a BNC shield. Whether the bulkhead bonds that shield to the",
        "cage shell is UNDECIDED. If it does, circulating current in the shield (which is",
        "also the signal return) would add straight onto the signal through a plain AGND-",
        "referenced buffer. The shield cannot simply be lifted -- it IS the return path.",
        "So every front end senses centre against shield AT THE CONNECTOR and a difference",
        "amplifier turns the disturbance into common mode instead. This is what lets the",
        "bulkhead decision be made, or reversed, later, without touching this board.",
        "The six ACCESIO eye channels get the identical treatment against the ACCESIO's",
        "own AGND pins (bonded to board AGND through ~10R, same as a BNC shell) -- it sits",
        "on a separately-earthed eye-tracker PC, the same class of ground offset.",
    ]):
        sch.text(line, X_NOTE1, Y_NOTE1 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "Common front end, every one of the 16 sources: isolated BNC (or ACCESIO DB37",
        "pin) -> 1k series -> BAT54S clamp to +12V/-12V (protects the receiving op-amp's",
        "own supply rails) -> source-specific back half below.",
        "",
        "13 of 16 (6 ACCESIO eyes, ambient, accelerometer, joystick X/Y, misc x3): one",
        "INA105KU per channel -- TI's monolithic precision unity-gain difference",
        "amplifier (matched internal resistor network, REF pin lands the output on AGND",
        "regardless of what the shield/ACCESIO_AGND reference is doing). Named",
        "'INA134-class' in the brief; INA134 itself has no stock KiCad symbol and is an",
        "audio-specific part -- INA105 fills the identical topological role and is a",
        "real, current, hand-solderable TI part (same Value-substitution discipline as",
        "the mule's SN74LVC541APW-on-74LS541-symbol precedent, hardware/README.md).",
    ]):
        sch.text(line, X_NOTE2, Y_NOTE2 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "Photodiodes (A_PD1/A_PD2, spec Sec.6.3.1): PASSIVE, so the input stage is a",
        "transimpedance amplifier, not a difference amp. Photodiode lives at the remote",
        "sensor (not on this board, like the mic/accelerometer/joystick) -- cathode on",
        "the coax centre (TIA summing junction, op-amp '-'), anode on the shield.",
        "Non-inverting input ('+') is referenced to the LOCAL SHIELD, not AGND: feedback",
        "forces the summing junction to track '+', so both photodiode terminals sit at",
        "the shield's own local potential at every instant -- true photovoltaic (zero-",
        "bias) operation survives a shield disturbance instead of that disturbance",
        "appearing as a real bias voltage across the diode.",
        "One OPA2197xD (dual, TI OPA197 family, CMOS input, ~10MHz GBW) covers both",
        "channels. Rf=1M/Cf=3.3pF (Cf sized for 6m coax, ~600pF, via",
        "Cf=sqrt(Cin/(2*pi*Rf*GBW)); closed-loop BW ~51.5kHz, comfortably above the",
        "~10kHz these channels need -- spec's own 3-5m/300-500pF worked example lands",
        "~57kHz the same way). Single-pole passive RC anti-alias low-pass follows",
        "(1.6k/6.8nF, fc~14.6kHz) -- 'a low-pass that doubles as anti-aliasing'.",
    ]):
        sch.text(line, X_NOTE3, Y_NOTE3 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "Microphone (A_MIC, spec Sec.6.4): NI X-series has no anti-alias filter and the",
        "mic is the only broadband source. Cutoff is set by INTAN, not NI -- Intan",
        "samples at 30kHz (Nyquist 15kHz), tighter than NI's 20kHz at a 40kHz scan; a",
        "2nd-order section is only ~4dB down at 15kHz, hence 4th order: two Sallen-Key",
        "sections 'on the buffer already present'. One OPA4197xD (quad) provides: unit 1",
        "= discrete 4-resistor (10.0k 0.1%) unity-gain difference receiver (INA105 can't",
        "fill this role -- its internal network can't be reconfigured into a filter);",
        "units 2/3 = two cascaded unity-gain Sallen-Key low-pass sections, 4th-order",
        "Butterworth at ~12kHz (Q1=0.5412/Q2=1.3066, standard cascade table; equal-R",
        "design method -- see mic_channel()'s own docstring for the full component-value",
        "derivation); unit 4 = spare, tied off ('+' to AGND, unity-gain follower).",
    ]):
        sch.text(line, X_NOTE4, Y_NOTE4 + line_idx * NOTE_DY)

    for line_idx, line in enumerate([
        "Misc 1-3 (A_MISC1-3, spec Sec.6.1): board-wide analog convention is +-5V; misc",
        "inputs additionally accept +-10V via switchable /1//2 attenuation, on BOTH legs",
        "(fix round 1 -- task-10a-report.md: dividing the signal leg alone left half the",
        "shield disturbance uncancelled in /2 mode). Each leg (signal = clamp node,",
        "shield = shield node) gets its OWN matched pair of ALWAYS-PRESENT 10.0k 0.1%",
        "resistors forming a fixed /2 tap to AGND.",
        "",
        "FIX ROUND 2 (task-10b-report.md): both legs' own COMMON node (wired to the",
        "amplifier) is bridged by a physical shunt to EITHER the raw node (direct = /1)",
        "OR the /2 tap (/2) at assembly/field-reconfiguration time -- not a schematic-",
        "level electrical fact, so every header pin is wired to its own distinct net here",
        "and the bridge is left to the physical shunt. BOTH LEGS NOW SHARE ONE PHYSICAL",
        "Conn_02x03 HEADER, populated with a SINGLE 2-GANG SHORTING BLOCK spanning both",
        "rows at the same column position (row 1 = signal leg pins 1-3, row 2 = shield",
        "leg pins 4-6; bridge 1-2+4-5 for x1, 2-3+5-6 for x2) -- replacing fix round 1's",
        "own two INDEPENDENT Conn_01x03 headers, which left it physically possible (not",
        "just against instructions) for the two legs to disagree: a real risk years from",
        "now when someone reconfigures a channel's range and forgets the second jumper,",
        "silently re-creating the exact half-cancelled-shield-disturbance defect fix",
        "round 1 closed out. NEVER populate this header with two independent 1-gang",
        "shunts -- that reintroduces the identical defect on a 2-row footprint.",
        "Both dividers sit BEFORE the amplifier: INA105's own linear common-mode range",
        "does not want a raw +-10V swing directly.",
    ]):
        sch.text(line, X_NOTE5, Y_NOTE5 + line_idx * NOTE_DY)

    return sch, refs


def main():
    sch, refs = build()
    OUT.mkdir(parents=True, exist_ok=True)
    sch_path = OUT / "analog-frontend.kicad_sch"
    sch_path.write_text(sch.render(paper="A2"))
    write_project_stub(OUT.parent / "breakout.kicad_pro")  # no-op if already written
    print(f"wrote {sch_path} ({sch_path.stat().st_size} bytes)")
    print(f"reference counters: {sch.ref_counters}")
    return refs


if __name__ == "__main__":
    main()
