# D3 — panel thickness: **CLOSED by decision, 2026-08-15**

**Decision: the front and rear panels are 3.0 mm aluminium.**

D3 was written as a question for the vendors — *what panel thickness will these connectors
accept?* That framing is backwards, and noticing so is what closed it. **We are having the
panels machined.** We choose the thickness; the connectors only have to accept it. The
question is therefore not "what is allowed" but "is 3.0 mm inside every stated limit on this
board", and that is answerable from documents already pinned in `../datasheet-params.toml`.

## Why 3.0 mm clears everything

| Connector | Stated limit | Binds? |
|---|---|---|
| M12 inlet, Phoenix 1551833 | **max 3.5 mm** (`m12_inlet.panel_thickness_max_mm`) | The only stated maximum on the board. 3.0 mm leaves 0.5 mm. |
| MDR68, MH 3700-0121-01 | none stated; panel mounting is #2-56, 2 places | **No.** Screw length is our selection, not a connector property. |
| BNC dual, Amphenol 031-6575 | none stated; 1/2-28 UNEF bushings | **No** — see below. |

2–3 mm aluminium is ordinary 2U rack panel stock, and 3.0 mm is the stiffer end of it, which
is worth having across a ~450 mm span carrying 31 BNC holes.

## Why the BNC thread length stopped mattering

Finding F6 replaces `BNC_PanelMountable_Vertical` with a **PCB-mounted right-angle** part.
That change moves the mechanical load: the connector is anchored by its own PCB tails and
ground/retention terminals, and the panel becomes **clearance and location**, not structure.

So the 1/2-28 UNEF nut is no longer carrying anything. It locates the port and dresses the
hole. If Amphenol's usable thread turns out shorter than 3.0 mm, the escape hatch is a
machinist detail that costs nothing: **spot-face the BNC holes locally to ~2.0 mm** and leave
the rest of the panel at 3.0 mm. No board change, no part change, no schedule.

This is the opposite of the M12's situation, where the connector *is* the mounting (ruling
R1 — panel-mounted, flying leads, 3–4 N·m installation torque) and its 3.5 mm maximum is a
real limit on a real load path.

## What was NOT worth asking, and why

Recorded because the first draft of this file asked all of it, and trimming it is the useful
part:

- **"Confirm 0.5 A per contact."** Already answered. `mdr68.contact_current_rating_a = 0.5`
  is read off MH drawing rev 3.0. **The drawing is the primary document** — distributors
  listing 3 A are the thing being overruled, not a reason to re-ask.
- **"Confirm the row datum."** `row_offsets_from_edge_mm = [5.10, 1.905, 3.81, 5.715]` has
  exactly one physically possible reading: first row 5.10 mm from the datum edge, then
  uniform 1.905 mm increments. Read as four absolute offsets it would put rows at 5.10 and
  5.715 — **0.615 mm apart, with 0.85 mm holes**, which is not a connector.
- **"Confirm the panel hole pattern."** Ø12.83 at 16.00 mm centres is already pinned
  (`bnc_dual_isolated.panel_hole_dia_mm`, `.panel_hole_pitch_mm`).

## What genuinely is not known, and how it is handled instead

One thing. **The MDR68's tail-row-to-contact-number map.** The drawing gives the geometry —
four staggered rows at 1.905 mm — but not which contact numbers land in which row.

> **Corrected 2026-08-16.** This first read *"getting it wrong scrambles 68 signals on a board
> that passes ERC and every test."* That overstates it, and the correction matters because it
> changes what kind of check is needed. All four candidates produce **different hole
> patterns**, not merely different numbering — the four tail rows carry X phases (0,1,0,1),
> (1,0,1,0), (0,1,1,0) and (1,0,0,1) on the 1.27 mm mating grid, all distinct. A pin would
> have to move 1.27 mm laterally *and* 1.905 mm in depth to seat in the wrong one, far beyond
> any contact compliance. **A wrong choice is caught when the connector will not seat** — a
> respin, expensive, but not silent. And the answer is visible in any drawing, 3D model or
> photograph showing the hole pattern, which is a far lower bar than a manufacturer
> confirmation.

It is a smaller unknown than it first looks. The tails run straight back from the mating
contacts, so **each contact's X position is fixed by its mating position**; the stagger only
redistributes depth. Mating row A reaches the board shallower than row B. That leaves four
candidate arrangements, not twenty-four.

Handled without blocking anything:

1. `gen_wl_sync_footprints.py` selects the arrangement through a single named constant,
   `MDR68_TAIL_ORDER`, with **all four candidates enumerated beside it**. Changing the
   choice is a one-line edit and a regenerate.
2. The assumption is stated in the footprint's own `descr` field, so it travels with the
   artifact rather than living only in a document.
3. `check_footprint_geometry.py` asserts the footprint matches the selected arrangement and
   that the assumption note is present — so it cannot be silently dropped.

**Before fab**, confirm the arrangement against MH's own drawing or 3D model — a few minutes
with a document already in hand, not a vendor round-trip. Record the answer here when done.

The same reasoning applies to the dual BNC's four tails, with more corroboration: KiCad's
`BNC_Amphenol_031-6575_Horizontal` and `BNC_Win_364A2x95_Horizontal` were drawn independently
from two manufacturers' drawings and **agree** that the two centreline holes are the centre
conductors and the ±2.54 mm pair are the shells.

## Consequences to carry forward

- Panel machining may be dimensioned and released at **3.0 mm**.
- `spec §` panel material/thickness should record 3.0 mm aluminium with the BNC spot-face
  note, so the machinist gets the escape hatch and not just the nominal.
- The 3M-derived **2.00 mm figure is dead** and should not reappear. It came from a part that
  was evaluated and not selected; see this finding's entry in `parametric-audit.md`.
