"""Generator for hardware/mule/mule.kicad_pcb -- the event-path mule board file.

Implements task-3-brief.md (.superpowers/sdd/2026-08-13-breakout-pcb/task-3-brief.md), as
corrected during execution (see the brief's own note): `kicad-cli` has no netlist-import or
Specctra-DSN subcommand, so there is no way to route this board or import a netlist from the
command line. What this generator produces instead: the board outline, the isolation slot,
every footprint placed per hardware/mule/floorplan.md, and every pad's net assigned directly
from the schematic's own exported netlist -- so the ratsnest pcbnew computes when a person
opens this file is present and correct, even though no track exists yet. A person routes it
and runs final DRC.

Prerequisites (in order -- `main()` checks each and fails loudly if skipped):
    python3 hardware/gen/gen_mule.py
    kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net \\
        hardware/mule/mule.kicad_sch

Why re-derive connectivity from mule.net rather than re-running gen_mule.build() net labels
directly: build() knows every NET NAME it assigned, but the authoritative statement of "which
physical (ref, pin) ended up on which net" is the exported netlist -- the same reasoning
hardware/gen/check_mule_netlist.py is built on (ERC/generation passing doesn't prove the
labels resolved correctly; reading the real exported netlist does). This generator imports
check_mule_netlist.parse_netlist() directly rather than re-implementing it.

Where each part's role (not just its bare reference number) comes from: gen_mule.build()
returns (sch, refs), where `refs` maps a role name ("r_in", "opto_pkg", "jack", ...) to the
reference designator(s) that play it. This generator is the only consumer of that map, and
placement code below is written against role names throughout -- never against a raw "R5" or
assumptions about generation order -- so a future edit to gen_mule.py's internal ordering
can't silently misplace a part here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_mule import build  # noqa: E402
from check_mule_netlist import DEFAULT_NET_PATH, parse_netlist  # noqa: E402
from kicad_pcb import Board  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "mule"
PCB_PATH = OUT / "mule.kicad_pcb"

# ---------------------------------------------------------------------------
# Board and floorplan constants -- see hardware/mule/floorplan.md for the placement
# rationale and the constraints a router must respect. Numbers here ARE the floorplan;
# floorplan.md explains WHY they are what they are.
# ---------------------------------------------------------------------------
BOARD_W = 100.0
# 90mm, not 80: the brief's "roughly 100 x 80mm" meets a real physical constraint here --
# a 40-pin 2.54mm-pitch header (J1/J2) is inherently ~52mm long (19 gaps at 2.54mm between
# 20 rows), and that length has to coexist in the SAME dimension as the isolation slot and
# the isolated corner below it. At exactly 80mm there is no non-overlapping way to fit
# both headers' full length, a >=2.5mm slot, and the isolated corner's own footprint
# (U5/U6/U7 plus their companions) with any real DRC margin. 90mm is the smallest round
# number that fits all three with genuine (not paper-thin) clearance -- confirmed by the
# DRC run this generator's own verification step requires, not assumed. See
# hardware/mule/floorplan.md.
BOARD_H = 90.0

# Isolation slot: a single straight horizontal cutout on Edge.Cuts, spanning most of the
# board width (bridges of solid substrate remain at both ends so the board stays one
# physical piece) at exactly the required 2.5mm minimum -- deliberately not more, because
# the isolated DC-DC's own pin pitch (TMA-0505S, 2.54mm SIP-7: the closest primary pin to
# the closest secondary pin is 5.08mm center-to-center, 3.58mm copper-to-copper after pad
# size) is the tightest constraint in the whole board; a wider slot would not leave enough
# copper-to-slot clearance under U7. See floorplan.md, "Why the slot is exactly 2.5mm".
SLOT_X0, SLOT_X1 = 6.0, 94.0
SLOT_Y0, SLOT_Y1 = 65.0, 67.5

# ---------------------------------------------------------------------------
# Footprint envelope reference (courtyard, mm, from the actual libraries this board uses --
# see task-3-report.md for how these were measured): TSSOP-20 7.8x7.0, DIP-8 9.73x10.66,
# SOT-223 8.8x7.2, SOT-23 3.86x3.4, SIP-7 DC-DC 6.61x20.0 (tall -- its 4 real pins run the
# full length of a 7-position 2.54mm strip), barrel jack 11.5x16.0, IDC-40 (bare 2x20
# header, chosen over the bulkier shrouded box header -- see floorplan.md) 6.09x51.81,
# 1x04 header 3.54x11.16, 1x02 header 3.54x6.09, 4-pos 5.08mm screw terminal 21.0x10.81,
# 0603 R/C 2.96x1.46, 0805 C 3.4x1.96.
# ---------------------------------------------------------------------------


def main():
    sch, refs = build()

    net_path = OUT / "mule.net"
    if not net_path.exists():
        print(f"error: {net_path} does not exist. Regenerate with:")
        print("  python3 hardware/gen/gen_mule.py")
        print(
            "  kicad-cli sch export netlist --format kicadsexpr -o hardware/mule/mule.net "
            "hardware/mule/mule.kicad_sch"
        )
        raise SystemExit(1)
    nets = parse_netlist(net_path.read_text())
    net_by_pin: dict[tuple[str, str], str] = {}
    for net_name, nodes in nets.items():
        for node in nodes:
            key = (node.ref, node.pin)
            assert key not in net_by_pin or net_by_pin[key] == net_name, (
                f"{key} appears on two different nets ({net_by_pin.get(key)!r} and "
                f"{net_name!r}) in {net_path} -- the exported netlist is internally "
                f"inconsistent, not a bug in this generator"
            )
            net_by_pin[key] = net_name

    def pad_nets_for(ref: str) -> dict[str, str]:
        """Every (ref, pin) in the netlist for this reference, keyed by pin NUMBER --
        which is exactly the footprint pad number for every part this board places (each
        footprint below was chosen so its own pad numbering equals the schematic symbol's
        pin numbering -- confirmed per-part in task-3-report.md, not assumed). A pin with
        no netlist entry (an explicit no-connect, or an unused physical pad like the
        barrel jack's power-detect switch terminal) simply has no key here, which
        Board.place() already treats as a real, intentionally-unconnected pad."""
        return {pin: net for (r, pin), net in net_by_pin.items() if r == ref}

    b = Board(project="mule", width=BOARD_W, height=BOARD_H, sch_filename="mule.kicad_sch")

    def lay(ref, x, y, rot=0):
        """Place `ref` at (x, y). libname/modname come from sch.footprints[ref]
        ("Libname:Modname", populated as a side effect of gen_mule.py's own place() calls
        -- the single source of truth for which real footprint this reference uses, so it
        can never drift from what the schematic itself carries), value from sch.values,
        net-per-pad from the exported netlist, and the schematic cross-link from
        sch.instance_uuid."""
        lib_id = sch.footprints[ref]
        assert lib_id, f"{ref} has no footprint assigned in gen_mule.py"
        libname, modname = lib_id.split(":", 1)
        b.place(
            libname, modname, ref, sch.values.get(ref, ""),
            x, y, rot=rot, pad_nets=pad_nets_for(ref), sch_uuid=sch.instance_uuid.get(ref),
        )

    # === Board outline and isolation slot ===================================
    b.edge_rect(0, 0, BOARD_W, BOARD_H)
    b.edge_rect(SLOT_X0, SLOT_Y0, SLOT_X1, SLOT_Y1)
    # Belt-and-suspenders on top of the physical cutout above: a router can't place copper
    # over a milled void regardless, but a keepout rule area is a strong, colored, hard to
    # miss visual cue in pcbnew for the person about to route this board, and gives DRC an
    # explicit rule to enforce approaching the slot, not just exactly at its edge. Sized a
    # little taller than the physical slot (1mm margin top and bottom) for that approach
    # margin.
    b.keepout_rect(SLOT_X0, SLOT_Y0 - 1.0, SLOT_X1, SLOT_Y1 + 1.0, "isolation_barrier")
    # Placed in the one stretch of the slot's own Y-band with no straddling component
    # nearby (X 71-94, clear of U7's courtyard which ends ~69.4 and of the SLOT_X1=94
    # bridge) -- short and small enough to avoid every silkscreen/copper/board-edge
    # clearance warning a longer or more central label collided with (see
    # task-3-report.md). The full electrical explanation lives in floorplan.md, including
    # the back-powering-through-clamps note the brief asks to carry into the bring-up
    # procedure -- silkscreen space here is too tight for that longer prose safely.
    b.silk_text("ISOLATION BARRIER", 84, SLOT_Y0 - 1.3, size=1.3)

    # === Task-PC and Pi IDC-40 headers, opposite edges =======================
    lay(refs["tpc_header"], 8, 7)
    lay(refs["pi_header"], 90, 7)

    # === 17-channel inbound path: series R + clamp diode per channel =========
    # Row pitch is wider than the bare 0603/SOT-23 courtyard needs (1.46mm / 3.4mm) so
    # each part's own Reference silkscreen text -- auto-placed just above its courtyard,
    # see Board.place() -- has room to clear the PREVIOUS row's courtyard too, not just
    # its own.
    for i, ref in enumerate(refs["r_in"]):
        lay(ref, 20, 9 + i * 3.0)
    for i, ref in enumerate(refs["clamp_diode"][:9]):
        lay(ref, 28, 9 + i * 5.5)
    for i, ref in enumerate(refs["clamp_diode"][9:]):
        lay(ref, 34, 9 + i * 5.5)

    # === Inbound buffers (3.3V) + their decouplers ============================
    buf_x = [46, 46, 46]
    buf_y = [12, 26, 40]
    cap_x = [54, 54, 54]
    for ref, x, y in zip(refs["buffers_3v3"], buf_x, buf_y):
        lay(ref, x, y)
    for ref, x, y in zip(refs["buffer_3v3_decouple_c"], cap_x, buf_y):
        lay(ref, x, y)

    # GPIO0/GPIO1 boot-contention series resistors, near U1 (which carries channels 0-1)
    lay(refs["gpio01_series_r"][0], 60, 9)
    lay(refs["gpio01_series_r"][1], 60, 12)

    # === Outbound 5V buffer + decoupler + barcode test header ================
    lay(refs["buffer_5v"], 60, 22)
    lay(refs["buffer_5v_decouple_c"], 60, 30)
    lay(refs["barcode_header"], 60, 40)

    # === Power: barrel jack, LDO, bulk caps ===================================
    lay(refs["jack"], 78, 11)
    lay(refs["ldo"], 78, 33)
    lay(refs["ldo_decouple_c"], 70, 35)
    lay(refs["bulk_c"][0], 78, 43)  # +5V -- below U8, clear of J8's wide (11.5x16mm) courtyard
    lay(refs["bulk_c"][1], 78, 49)  # +3V3

    # === Isolated cluster: U5/U6 (HCPL-4661) straddle the slot ================
    # rot=270 confirmed empirically (a standalone test board + render, see
    # task-3-report.md "rotation convention" -- not assumed from memory): at rot=270 the
    # primary-side pin group (1-4: A1/C1/C2/A2, DGND-domain) lands at the SAME absolute Y
    # as the placement origin, and the secondary-side group (5-8: GND/VO2/VO1/VCC,
    # ISO-domain) lands 7.62mm further down (larger Y) -- i.e. toward the isolated side.
    # Placing the origin so the primary row sits just above SLOT_Y0 puts the secondary row
    # just below SLOT_Y1, with the slot itself running under the package body between them.
    U5_X, U6_X = 20.0, 44.0
    OPTO_ORIGIN_Y = 62.5
    lay(refs["opto_pkg"][0], U5_X, OPTO_ORIGIN_Y, rot=270)
    lay(refs["opto_pkg"][1], U6_X, OPTO_ORIGIN_Y, rot=270)

    # LED series resistors: DGND-domain (both LED terminals reference DGND/the driving
    # buffer, not ISO_5V/ISO_GND -- see gen_mule.py's opto_channels wiring), so they belong
    # on the MAIN side of the slot despite being physically close to the isolated cluster.
    # X=16, not U5_X -- deliberately off the R_in column (X=20) so this pair's own length
    # (17 rows) is free to run all the way to Y=57 without a cross-column collision.
    lay(refs["opto_led_r"][0], 16, 50)
    lay(refs["opto_led_r"][1], 16, 54)
    lay(refs["opto_led_r"][2], U6_X, 50)
    lay(refs["opto_led_r"][3], U6_X, 54)
    # Bench-injection spare headers feed straight into U6's LED resistors -- also
    # DGND-domain, also main-side.
    lay(refs["spare_header"][0], 52, 45)
    lay(refs["spare_header"][1], 52, 53)

    # Pull-ups (to ISO_5V) and local bypass caps: genuinely isolated-domain, isolated side.
    lay(refs["opto_pullup_r"][0], U5_X, 73)
    lay(refs["opto_pullup_r"][1], U5_X, 77)
    lay(refs["opto_pullup_r"][2], U6_X, 73)
    lay(refs["opto_pullup_r"][3], U6_X, 77)
    # Positioned at the midpoint between each package's VCC (pad 8) and GND (pad 5) pins
    # -- both post-rotation pin positions computed and checked in task-3-report.md -- so
    # each cap sits ~4.5mm from both pins, comfortably inside the HCPL-4661 datasheet's
    # ~7mm bypass-placement requirement (not just "close enough to the package").
    lay(refs["opto_bypass_c"][0], 23.8, 72.5)  # U5: VCC (20,70.12), GND (27.62,70.12)
    lay(refs["opto_bypass_c"][1], 47.8, 72.5)  # U6: VCC (44,70.12), GND (51.62,70.12)

    # === Isolated DC-DC (TMA-0505S) also straddles the slot ===================
    # rot=0 (its native pinout): pins 1/2 (+Vin/-Vin, primary) at local y=0/2.54; pins
    # 4/6 (-Vout/+Vout, secondary) at local y=7.62/12.7 -- already Y-split in the
    # unrotated frame, so no rotation is needed to align it with the same horizontal slot
    # U5/U6 straddle.
    U7_X, U7_Y = 68.0, 61.2
    lay(refs["dcdc"], U7_X, U7_Y)
    lay(refs["dcdc_bypass_c"][0], 60, 55)  # input side (+5V/DGND), main
    lay(refs["dcdc_bypass_c"][1], 60, 82)  # output side (ISO_5V/ISO_GND), isolated
    lay(refs["bulk_c"][2], 68, 82)  # ISO_5V bulk 10uF, isolated

    # === Isolated-domain outputs: screw terminal + test point ================
    lay(refs["screw_terminal"], 74, 75)
    lay(refs["iso_tp"], 96, 75)

    OUT.mkdir(parents=True, exist_ok=True)
    PCB_PATH.write_text(b.render())
    print(f"wrote {PCB_PATH} ({PCB_PATH.stat().st_size} bytes)")
    print(f"{len(b.net_codes) - 1} nets, {len(b.footprints_text)} footprints placed")


if __name__ == "__main__":
    main()
