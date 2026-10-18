"""The loopback check: the box's own barcode, recorded back through an edge input.

GPIO17 (the barcode output) jumpered to GPIO20 (an edge input) puts every frame the box
emits into its own segment twice: once as the `B` record it logged, once as the edges
it saw. Decoding the edges and matching them against the `B` records is the one test of
the barcode output that no fake can stand in for, because the pin is the thing tested.
"""

import datetime

from wl_sync.barcode import encode
from wl_sync.log import BarcodeEmitted, Edge, SyncBoxLogHeader, write_log
from wl_sync.pins import GPIO_MAP

LOOP_GPIO = 20
FIRST = 214_507_817


def _header():
    return SyncBoxLogHeader(
        schema_version=1,
        session_id="2026-10-18_01",
        rig="rig0",
        boot_id="",
        written_at=datetime.datetime(2026, 10, 18, 17, 30, tzinfo=datetime.timezone.utc),
        gpio_map=GPIO_MAP,
    )


def _frame_edges(value, start_us, level, return_low=True):
    """The edges one frame puts on the input, from the level the pin was left at.

    The pin's level carries over from frame to frame, as a real one's does: a pin left
    HIGH produces no rising edge when the next frame's LEAD drives it HIGH again.
    """
    edges, tick = [], start_us
    for pulse_level, duration in encode(value):
        if pulse_level != level:
            edges.append(Edge(tick_us=tick, gpio=LOOP_GPIO, level=pulse_level))
            level = pulse_level
        tick += duration
    if return_low and level != 0:
        edges.append(Edge(tick_us=tick, gpio=LOOP_GPIO, level=0))
        level = 0
    return edges, level


def _segment(tmp_path, frames=5, lead_offset_us=60, return_low=True, wrong_value_at=None):
    records, level = [], 0
    for k in range(frames):
        tick = 1_000 + k * 1_000_000
        records.append(BarcodeEmitted(tick_us=tick, value=FIRST + k))
        on_wire = FIRST + k + (1000 if k == wrong_value_at else 0)
        edges, level = _frame_edges(on_wire, tick + lead_offset_us, level, return_low)
        records.extend(edges)
    path = tmp_path / "seg-000.log"
    write_log(path, _header(), records)
    return path


def test_a_healthy_loopback_decodes_every_frame_it_can(tmp_path):
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path), LOOP_GPIO)

    assert report.passed, report.summary()
    # The first frame cannot be checked: a frame is found by a rising edge after
    # IDLE_MIN_US of LOW, and nothing in the record precedes the first one.
    assert report.unverifiable == [FIRST]
    assert report.matched == 4
    assert report.lead_offsets_us == [60, 60, 60, 60]


def test_the_stuck_high_pin_fails_the_loopback(tmp_path):
    """The defect this check exists to see on silicon: emit_pulses left the pin HIGH
    after TRAIL, so no later frame had the LOW idle before it that the decoder needs."""
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, return_low=False), LOOP_GPIO)

    assert not report.passed
    assert len(report.missing) >= 3, report.summary()


def test_a_frame_that_decodes_to_a_value_never_logged_fails(tmp_path):
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, wrong_value_at=2), LOOP_GPIO)

    assert not report.passed
    assert report.unexpected == [FIRST + 2 + 1000]


def test_a_loopback_with_no_edges_says_the_wire_is_missing(tmp_path):
    from wl_sync.bench import loopback

    path = tmp_path / "seg-000.log"
    write_log(path, _header(), [BarcodeEmitted(tick_us=1_000 + k * 1_000_000, value=FIRST + k)
                                for k in range(3)])
    report = loopback(path, LOOP_GPIO)

    assert not report.passed
    assert "no edges on GPIO20" in report.summary()


def test_the_command_line_exits_nonzero_on_a_failed_loopback(tmp_path, capsys):
    from wl_sync.bench import main

    assert main(["loopback", str(_segment(tmp_path)), "--gpio", str(LOOP_GPIO)]) == 0
    assert main(["loopback", str(_segment(tmp_path, return_low=False))]) == 1
    assert "FAIL" in capsys.readouterr().out
