"""Freshness check for hardware/breakout/netlist-contract.json -- Task 12 fix round 1.

This is the ONLY file in the hardware contract test suite that is allowed to skip. See
test_netlist.py's own module docstring for the reasoning this mirrors: that file's 26
assertions verify the BOARD (does the design committed right now satisfy its own
invariants), using a committed snapshot that needs no toolchain, so it must run
unconditionally, everywhere, including CI -- and it does. This file verifies something
different: is that committed snapshot still an ACCURATE rendering of the schematic files
committed alongside it. Answering that requires actually running `kicad-cli` -- a real
export, not a re-parse -- and this project's CI (.github/workflows/ci.yml) has no KiCad
install step (see hardware/README.md's "Toolchain" section: KiCad is a local, interactive-
install dependency, never added to the Python-only CI matrix). So THIS file's own single
test skips, with an explicit reason, when `kicad-cli` is not on `PATH`. That optionality
is deliberately confined to this file alone -- it must never leak into test_netlist.py,
which is why that file no longer even imports kicad-cli's export format at all (see its
own docstring).

Skipping here does NOT weaken the contract: test_netlist.py's 26 assertions still run,
still read real (already-committed) data, and still fail loudly on a genuine electrical
regression, with or without this file's own test executing. What this file catches that
the other one structurally cannot: a sheet edited (e.g. a pull-up moved, a pin
reassigned) WITHOUT the corresponding `netlist-contract.json` regeneration -- a snapshot
that is stale but happens to still satisfy all 26 assertions (e.g. an edit to a net none
of the 12 checks currently inspects). That is exactly the failure mode a "contract" is
supposed to prevent from going silent: the whole-board assertions test the snapshot
faithfully, but only THIS test verifies the snapshot is still honest about the schematic.

HOW THIS FAILS IF SOMEONE FORGETS TO REGENERATE THE SNAPSHOT: any contributor who edits a
hardware/breakout/sheets/*.kicad_sch file and has KiCad installed locally (a precondition
for editing the schematic at all) will have this test run, not skip, the next time they
run `pytest`. It exports a fresh netlist from whatever is CURRENTLY on disk, builds a
fresh snapshot from it with the exact same generator hardware/README.md's own documented
regeneration recipe calls for, and byte-compares that against the committed
netlist-contract.json. A real difference -- ANY difference, since
gen_breakout_netlist_contract.py's own output is fully deterministic given the same input
netlist -- fails this test with a diff-shaped assertion message and the exact two commands
that fix it. This is a local, pre-commit-style gate (there is no server-side enforcement,
since CI cannot run kicad-cli at all -- see above); hardware/README.md's own "Regenerating
fab outputs" section documents the snapshot-regeneration step as part of every sheet
change for the same reason.

RESIDUAL LIMITATION, named rather than left implicit: this test verifies REPRODUCIBILITY
(does regenerating right now produce the same bytes already committed), not correctness.
A bug shared between the run that produced the currently-committed snapshot and this
test's own regeneration -- i.e. a latent bug in
hardware/gen/gen_breakout_netlist_contract.py's own parsing -- would reproduce identically
both times and this test would still pass. Catching THAT class of bug is what
test_netlist.py's own 26 assertions are for (they read the parsed data's actual electrical
content, independent of how it got there); this test only answers "is the snapshot fresh,"
never "is the snapshot correct."
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMATIC = REPO_ROOT / "hardware" / "breakout" / "breakout.kicad_sch"
COMMITTED_SNAPSHOT = REPO_ROOT / "hardware" / "breakout" / "netlist-contract.json"
GEN_SCRIPT = REPO_ROOT / "hardware" / "gen" / "gen_breakout_netlist_contract.py"

KICAD_CLI = shutil.which("kicad-cli")

pytestmark = pytest.mark.skipif(
    KICAD_CLI is None,
    reason=(
        "kicad-cli not found on PATH -- this project's CI runner (.github/workflows/ci.yml) "
        "installs no KiCad, so this freshness check cannot regenerate a netlist to compare "
        "against the committed hardware/breakout/netlist-contract.json. This does NOT weaken "
        "tests/hardware/test_netlist.py's own 26 whole-board contract assertions, which run "
        "unconditionally against that already-committed snapshot regardless of this skip -- "
        "see this file's own module docstring for the full split. Install KiCad "
        "(kicad.org) to run this check locally, e.g. after editing any "
        "hardware/breakout/sheets/*.kicad_sch or hardware/breakout/breakout.kicad_sch."
    ),
)


def test_netlist_contract_snapshot_is_fresh(tmp_path):
    net_path = tmp_path / "breakout.net"
    export = subprocess.run(
        [
            KICAD_CLI, "sch", "export", "netlist", "--format", "kicadsexpr",
            "-o", str(net_path), str(SCHEMATIC),
        ],
        capture_output=True, text=True,
    )
    assert export.returncode == 0, (
        f"kicad-cli sch export netlist failed (exit {export.returncode}):\n"
        f"stdout:\n{export.stdout}\nstderr:\n{export.stderr}"
    )

    regenerated_snapshot = tmp_path / "netlist-contract.json"
    gen = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), str(net_path), str(regenerated_snapshot)],
        capture_output=True, text=True,
    )
    assert gen.returncode == 0, (
        f"{GEN_SCRIPT} failed (exit {gen.returncode}):\nstdout:\n{gen.stdout}\nstderr:\n{gen.stderr}"
    )

    fresh = regenerated_snapshot.read_text()
    committed = COMMITTED_SNAPSHOT.read_text()
    assert fresh == committed, (
        f"{COMMITTED_SNAPSHOT} is STALE -- it does not match what kicad-cli produces right "
        f"now from the currently checked-in hardware/breakout/*.kicad_sch files. A sheet was "
        f"edited without regenerating the snapshot afterward. Fix with:\n"
        f"    kicad-cli sch export netlist --format kicadsexpr "
        f"-o hardware/breakout/breakout.net hardware/breakout/breakout.kicad_sch\n"
        f"    python3 hardware/gen/gen_breakout_netlist_contract.py\n"
        f"then commit the updated hardware/breakout/netlist-contract.json alongside the "
        f"sheet change."
    )
