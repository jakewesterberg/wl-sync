"""Pairing 31 panel BNC ports onto 16 dual bodies -- finding F6's schematic half.

WHY THIS IS SHARED RATHER THAN COPIED. This project's default is that a sheet's own
checker/generator keeps its own copy of anything sheet-specific, so a change to one sheet's
contract can never silently alter another's (see check_row_pitch_guard.py's own docstring
for the full statement of that discipline, and for the one exception it already carved out).
This module is that same exception: it has NO sheet-specific parameter. It is pure
bookkeeping -- "consume a refdes, hand back which body and which port" -- and every sheet
needs the identical behaviour. Five hand-copies of a refdes allocator is precisely how the
five sheets would drift apart.

WHAT F6 CHANGES. `BNC_PanelMountable_Vertical` puts the connector axis perpendicular to the
PCB, with a 9.65 mm barrel hole through the board: on a 430 x 240 mm board lying flat in a
2U chassis those 31 BNCs point at the lid, not at the panels they are supposed to emerge
from. The replacement is the Amphenol RF 031-6575, a right-angle DUAL jack whose two shells
are independently isolated -- so ports have to be paired two to a body.

THE PAIRING FALLS OUT OF THE SHEETS, which is the useful part:

    sheet             ports  bodies  spare  panel face (spec Sec.9.6)
    taskpc-digital        2       2      2  ONE PORT PER FACE -- see below
    pi-interface          5       3      1  back  (camera triggers)
    analog-frontend      10       5      0  back  (booth sensors, misc analog)
    mux-intan             8       4      0  front (Intan analog out)
    opto-intan            6       3      0  front (Intan digital out/in)
                         31      17      3

Every pair is within one sheet, so no generator has to reach into a sibling's refdes space
-- which matters, because all ten generators are refdes-pinned specifically so that none
reads another's numbering (see any generator's own build() baseline comment). It also groups
the panel sensibly on its own: camera triggers together, booth analog together, Intan analog
together, Intan digital together.

TASKPC-DIGITAL IS THE EXCEPTION, AND IT IS NOT AN ACCIDENT. Its two BNCs are the reward
remote and the reward driver output, and spec Sec.9.6 puts them on OPPOSITE faces on purpose
-- "the hardware connection follows the animal... the human controls face the person
operating the rig". A dual body is one piece of plastic through one panel, so those two ports
cannot share one. Each therefore gets its own body with a deliberate, wired spare port. That
is the 17th body, and the reason the total is not the 16 a pure per-sheet pairing would give.

SPARE PORTS ARE PLACED, NOT LEFT UNPLACED -- shell on the local ground, centre conductor on
an explicit no-connect. An unplaced unit is an ERC `missing_unit` warning and, worse, leaves
two footprint pads carrying no net at all. The connector is one physical part with two ports;
drawing both is the schematic agreeing with the panel.

REFDES ARE NOT RENUMBERED. Merging two ports into one body would ordinarily shift every
later J reference down by one, which this project forbids outright ("never renumber an
existing refdes"). So `take()` consumes a `next_ref("J")` for BOTH ports and uses only the
first, deliberately burning the second. The result is holes in the J sequence -- J6, J14,
J16, J21, J23, ... simply stop existing -- and every reference that is NOT a BNC keeps the
number it has today. Holes are free; renumbering would have touched both BOMs, several
checkers and the tests.
"""
from __future__ import annotations

import re

BNC_DUAL_LIB = "wl-sync"
BNC_DUAL_SYM = "BNC_Dual_RA_Isolated"
BNC_DUAL_FOOTPRINT = "wl-sync:BNC_Dual_RA_Isolated"

# The physical part, for BOM/value text. One purchased connector serves two ports.
BNC_DUAL_PART = "Amphenol RF 031-6575"


class DualBncAllocator:
    """Hands out (reference, unit) for a run of panel BNC ports on one sheet.

    Usage, once per sheet:

        bnc = DualBncAllocator()
        ...
        ref, centre, shell = bnc.place_port(sch, desc, x, y, footprint=BNC_DUAL_FOOTPRINT)
        sch.label(signal_net, *pin_pos(x, y, centre))
        sch.label(shield_net, *pin_pos(x, y, shell))

    `place_port()` must be called in the same order, and the same number of times, as the
    sheet previously called `sch.next_ref("J")` for its BNCs -- that is what keeps every
    other refdes on the sheet exactly where it is.

    It returns the centre and shell Pin objects rather than a pins dict on purpose. Unit 1's
    pads are numbered 1/2 and unit 2's are 3/4, so a call site that indexed `pins["1"]`
    would silently be wrong on every second port -- and wrong in the worst available way,
    since labelling a shell as a centre conductor produces a clean netlist. Naming the two
    roles removes the opportunity.
    """

    _ATTR = "_wl_sync_bnc_dual_allocator"

    def __init__(self) -> None:
        self._open_body: str | None = None
        self._pending_value: str | None = None
        self._pending_index: int | None = None
        self.bodies: list[str] = []
        self.ports: int = 0

    @classmethod
    def for_sheet(cls, sch) -> "DualBncAllocator":
        """The one allocator for this sheet, created on first use and stashed on the Sch.

        Attached to the sheet rather than threaded through call signatures because the five
        generators reach their BNC placements through five different shapes -- a per-channel
        helper called in a loop (analog-frontend, opto-intan), a numbered loop body
        (mux-intan), a fan-out helper (pi-interface) and two straight-line calls
        (taskpc-digital). Passing an allocator down all five would mean changing signatures
        that have nothing else to do with F6, and every such signature is one more thing a
        future edit can get wrong. Pairing state is per-sheet by construction here, which is
        exactly the invariant that makes the whole scheme safe: no sheet can accidentally
        pair a port with a sibling sheet's.
        """
        existing = getattr(sch, cls._ATTR, None)
        if existing is None:
            existing = cls()
            setattr(sch, cls._ATTR, existing)
        return existing

    # Unit -> (centre-conductor pad, shell pad), matching both the BNC_Dual_RA_Isolated
    # symbol's units and the footprint's pads. Kept in one place so the symbol, the
    # footprint and every call site cannot disagree.
    _PORT_PADS = {1: ("1", "2"), 2: ("3", "4")}

    def take(self, sch) -> tuple[str, int]:
        consumed = sch.next_ref("J")
        self.ports += 1
        if self._open_body is None:
            self._open_body = consumed
            self.bodies.append(consumed)
            return consumed, 1
        body = self._open_body
        self._open_body = None
        del consumed  # burned on purpose -- see this module's docstring
        return body, 2

    def take_spare(self) -> tuple[str, int]:
        """Claim the open body's UNUSED second port, consuming NO refdes.

        The distinction from `take()` is load-bearing and was found the hard way. `take()`
        consumes a `next_ref("J")` per port precisely so the 31 ports that already existed
        keep every sibling reference where it was. A spare port is different: it is a 32nd
        port that never had a reference of its own, so consuming one would push every LATER
        J reference on the sheet up by one. On pi-interface that silently moved the internal
        USB header from J18 to J19 -- straight onto analog-frontend's DB37 -- producing a
        duplicate reference across two sheets.

        It surfaced only as `kicad-cli sch export netlist`'s "schematic has annotation
        errors" line, which is a WARNING on stdout: the netlist still exported, ERC still
        reported 0 errors, and every checker still passed. Worth remembering as another
        member of the family this whole audit keeps finding -- a defect that no assertion
        in the toolchain was watching for.
        """
        assert self._open_body is not None, "no open body: take_spare() called on an even port count"
        body = self._open_body
        self._open_body = None
        return body, 2

    def place_port(self, sch, value: str, x: float, y: float, footprint: str = BNC_DUAL_FOOTPRINT,
                   spare: bool = False):
        """Place the next port and return `(ref, centre_pin, shell_pin)`.

        `spare=True` fills an odd sheet's leftover second port without consuming a refdes --
        see `take_spare()`.

        ONE BODY, ONE VALUE. The two ports get placed at two different points on the sheet
        with two different descriptions ("Intan analog output 1 (BNC)" and "... 2 (BNC)"),
        but they are two units of ONE component, and a component has a single Value. Writing
        a different Value on each unit makes the schematic fail annotation --
        `kicad-cli sch export netlist` reports "schematic has annotation errors" and the
        exported value is then simply whichever unit happened to be written last, i.e. the
        BOM silently describes one port and orders one connector.

        So when the second port lands, both units' Value properties are rewritten to a
        combined body-level description. Rewriting is done on `sch.body` -- `Sch.place()`
        appends exactly one entry per placement, so the index recorded at placement time
        addresses that entry unambiguously.
        """
        ref, unit = self.take_spare() if spare else self.take(sch)
        pins = sch.place(BNC_DUAL_LIB, BNC_DUAL_SYM, ref, value, x, y, unit=unit, footprint=footprint)
        centre_pad, shell_pad = self._PORT_PADS[unit]
        assert centre_pad in pins and shell_pad in pins, (
            f"{ref} unit {unit}: expected pads {centre_pad}/{shell_pad}, got {sorted(pins)}"
        )
        body_index = len(sch.body) - 1
        if unit == 1:
            self._pending_value = value
            self._pending_index = body_index
        else:
            combined = f"Dual panel BNC: {self._pending_value} | {value}"
            self._set_value(sch, self._pending_index, combined)
            self._set_value(sch, body_index, combined)
            sch.values[ref] = combined
            self._pending_value = None
            self._pending_index = None
        return ref, pins[centre_pad], pins[shell_pad]

    @staticmethod
    def _set_value(sch, index: int, value: str) -> None:
        entry = sch.body[index]
        patched, n = re.subn(
            r'(\(property "Value" ")(?:[^"\\]|\\.)*(")', lambda m: m.group(1) + value + m.group(2),
            entry, count=1,
        )
        assert n == 1, f"expected exactly one Value property in the placed symbol, found {n}"
        sch.body[index] = patched

    @property
    def spare_port(self) -> str | None:
        """The reference of a body left with an unused second port, if the sheet's port
        count is odd. pi-interface is the only such sheet (5 camera triggers), and its
        spare is a real, physically-present BNC on the BACK panel -- worth knowing about
        at panel-machining time, not an accounting artifact.

        (Said "front panel" until 2026-08-16, which was wrong: panel-elevations.md puts
        every pi-interface BNC on the back, and its front/back spare counts -- 1 front,
        2 back -- only reconcile with this one on the back. The FRONT spare is J5B, the
        second port of taskpc-digital's own reward-remote body.)

        Also no longer "unwired": as of 2026-08-16 this port is the eye camera's
        frame-time input. The property still reports it, because what it answers is "which
        body has an odd port out", which is what callers place into."""
        return self._open_body

    def summary(self, sheet: str) -> str:
        spare = f", 1 spare port on {self._open_body}" if self._open_body else ""
        return (
            f"{sheet}: {self.ports} BNC ports on {len(self.bodies)} dual bodies "
            f"({', '.join(self.bodies)}){spare}"
        )
