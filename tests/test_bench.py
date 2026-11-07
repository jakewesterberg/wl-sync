"""The loopback check: the box's own barcode, recorded back through an edge input.

GPIO17 (the barcode output) jumpered to GPIO20 (an edge input) puts every frame the box
emits into its own segment twice: once as the `B` record it logged, once as the edges
it saw. Decoding the edges and matching them against the `B` records is the one test of
the barcode output that no fake can stand in for, because the pin is the thing tested.
"""

import datetime

import pytest

from wl_sync.barcode import encode, rises_us
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


# --- The stamp run: the stamp machine's view of the box's own barcode (Task 1.10) ---------
#
# Run on the box for the spike, so these script the backend: each frame's FrameStamp, and
# the match and bracket width it leaves in `last_frame`.


class _ScriptedBackend:
    """`emit_pulses`, `last_frame` and `take_pairs`, as Rp1Backend has them, scripted."""

    def __init__(self, frames):
        self._frames = iter(frames)
        self.last_frame = None
        self.emitted = []
        self.pairs_taken = 0

    def emit_pulses(self, pin, pulses):
        self.emitted.append((pin, list(pulses)))
        stamp, self.last_frame = next(self._frames)
        return stamp

    def take_pairs(self):
        self.pairs_taken += 1
        return []


def _whole(value, k, width_ns=30_000, between=0, short=0):
    """A frame stamped at its lead edge, with `short` of its later edges unseen."""
    from wl_sync.backend import FrameStamp
    from wl_sync.clockmap import LeadStamp
    from wl_sync.rp1.backend import FrameTrace

    p = 1_000_000 * (k + 1)
    consumed = len(rises_us(encode(value))) - 1 - short
    lead = LeadStamp(p=p, between=between, consumed=consumed, leftover=(), nearest=None)
    return FrameStamp(p, between=between), FrameTrace(lead, width_ns)


def _unstamped(width_ns=30_000):
    from wl_sync.backend import STAMP_NOT_SEEN, FrameStamp
    from wl_sync.clockmap import LeadStamp
    from wl_sync.rp1.backend import FrameTrace

    lead = LeadStamp(p=None, between=0, consumed=0, leftover=(), nearest=None)
    return FrameStamp(None, STAMP_NOT_SEEN), FrameTrace(lead, width_ns)


def test_a_stamp_run_counts_lead_edges_later_edges_and_widths():
    from wl_sync.bench import stamp_run
    from wl_sync.pins import BARCODE_PIN

    values = [FIRST, FIRST + 1, FIRST + 2]
    backend = _ScriptedBackend(
        [_whole(v, k, width_ns=30_000 + 1_000 * k) for k, v in enumerate(values)]
    )
    paced = []
    report = stamp_run(backend, values, lambda: paced.append(len(backend.emitted)))

    assert report.passed, report.summary()
    assert report.frames == 3 and report.stamped == 3
    assert report.missed == {} and report.edges_short == 0 and report.between == 0
    assert report.stamps_seen == sum(len(rises_us(encode(v))) for v in values)
    assert report.widths_ns == [30_000, 31_000, 32_000]
    assert "p99.9" in report.summary()
    assert backend.emitted == [(BARCODE_PIN, encode(v)) for v in values]
    assert paced == [1, 2], "pace() is called between frames, not before or after them"
    assert backend.pairs_taken == 3


def test_a_stamp_run_with_no_stamp_fails_and_counts_by_cause():
    from wl_sync.backend import STAMP_NOT_SEEN
    from wl_sync.bench import stamp_run

    values = [FIRST, FIRST + 1, FIRST + 2]
    backend = _ScriptedBackend([_unstamped() for _ in values])
    report = stamp_run(backend, values, lambda: None)

    assert not report.passed
    assert report.stamped == 0
    assert report.missed == {STAMP_NOT_SEEN: 3}
    assert report.stamps_seen == 0
    assert report.edges_short == 0, "a frame with no lead stamp is counted as missed only"
    assert STAMP_NOT_SEEN in report.summary()


def test_a_shifted_frames_aligned_lead_is_counted_among_the_stamps_seen():
    """Ruling R26: a lead the frame's rises align on outside its window is the match's
    `lead`, counted in neither `between` nor `consumed`. The stamps seen still count it,
    and its distance goes to `far_us`."""
    from wl_sync.backend import FrameStamp
    from wl_sync.bench import STAMP_TOO_FAR, stamp_run
    from wl_sync.clockmap import LeadStamp
    from wl_sync.rp1.backend import FrameTrace

    rises = len(rises_us(encode(FIRST)))
    lead = LeadStamp(
        p=None, between=1, consumed=rises - 1, leftover=(), nearest=15_000, lead=5_000_000
    )
    too_far = "the stamp lies 15000 us from the frame's lead edge"
    shifted = FrameStamp(None, too_far, between=1), FrameTrace(lead, 30_000)
    report = stamp_run(_ScriptedBackend([shifted]), [FIRST], lambda: None)

    assert report.stamps_seen == rises + 1  # its lead, its later edges, and one between
    assert report.missed == {STAMP_TOO_FAR: 1} and report.far_us == [15_000]


def test_a_frame_missing_a_later_edge_is_short():
    from wl_sync.bench import stamp_run

    values = [FIRST, FIRST + 1, FIRST + 2]
    frames = [_whole(v, k, short=1 if k == 1 else 0) for k, v in enumerate(values)]
    report = stamp_run(_ScriptedBackend(frames), values, lambda: None)

    assert report.stamped == 3
    assert report.edges_short == 1
    assert not report.passed


def test_stamps_between_frames_fail_the_run():
    from wl_sync.bench import stamp_run

    values = [FIRST, FIRST + 1, FIRST + 2]
    frames = [_whole(v, k, between=2 if k == 2 else 0) for k, v in enumerate(values)]
    report = stamp_run(_ScriptedBackend(frames), values, lambda: None)

    assert report.stamped == 3 and report.edges_short == 0
    assert report.between == 2
    assert report.stamps_seen == sum(len(rises_us(encode(v))) for v in values) + 2
    assert not report.passed


def test_the_stamps_command_builds_starts_and_closes_the_backend_and_exits_on_the_run(
    monkeypatch, capsys
):
    """The command S runs on the box, against a stand-in for Rp1Backend: what it is built
    with, what it starts, the values it sends, that it closes, and that the exit status
    is the run's."""
    import random

    from wl_sync import bench
    from wl_sync.backend import FrameStamp
    from wl_sync.clockmap import LeadStamp
    from wl_sync.pins import (
        BARCODE_PIN, CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN, EDGE_PINS,
    )
    from wl_sync.rp1 import backend as rp1
    from wl_sync.rp1.backend import FrameTrace

    built = []
    stamping = True

    class _Box:
        stamp_test_failure = ""  # the start-up test passed

        def __init__(self, **kwargs):
            built.append(self)
            self.kwargs = kwargs
            self.started = []
            self.emitted = []
            self.last_frame = None
            self.closed = False

        def start_strobed_capture(self, data_base, data_count, strobe_pin, on_word,
                                  on_loss=None):
            self.started.append(("codes", data_base, data_count, strobe_pin))
            on_word(1, 2)  # with nothing wired, a W is itself a finding

        def start_edge_capture(self, pins, on_edge, on_loss=None):
            self.started.append(("edges", tuple(pins)))

        def emit_pulses(self, pin, pulses):
            self.emitted.append((pin, list(pulses)))
            if not stamping:
                stamp, self.last_frame = _unstamped()
                return stamp
            p = 1_000_000 * len(self.emitted)
            lead = LeadStamp(p, 0, len(rises_us(pulses)) - 1, (), None)
            self.last_frame = FrameTrace(lead, 30_000)
            return FrameStamp(p)

        def take_pairs(self):
            return []

        def check_health(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(rp1, "Rp1Backend", _Box)
    monkeypatch.setattr(bench, "STAMP_RUN_PERIOD_S", 0)

    assert bench.main(["stamps", "--stamp-pin", "17", "--frames", "4",
                       "--gpiochip", "/dev/gpiochip4"]) == 0
    [box] = built
    assert box.kwargs == {"chip_path": "/dev/gpiochip4", "stamp_pin": 17}
    assert box.started == [
        ("codes", CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN),
        ("edges", tuple(EDGE_PINS)),
    ]
    rng = random.Random(4)
    assert box.emitted == [(BARCODE_PIN, encode(rng.getrandbits(32))) for _ in range(4)]
    assert box.closed
    out = capsys.readouterr().out
    assert "PASS" in out and "words 1" in out

    stamping = False
    assert bench.main(["stamps"]) == 1
    assert built[1].kwargs == {"chip_path": "/dev/gpiochip0", "stamp_pin": 17}
    assert built[1].closed
    assert "FAIL" in capsys.readouterr().out

    assert bench.main(["stamps", "--stamp-pin", "19", "--frames", "1"]) == 1
    assert built[2].kwargs == {"chip_path": "/dev/gpiochip0", "stamp_pin": 19}
    capsys.readouterr()
    with pytest.raises(SystemExit) as refused:
        bench.main(["stamps", "--stamp-pin", "20"])
    assert refused.value.code == 2
    assert len(built) == 3
    capsys.readouterr()


def test_the_stamps_command_reports_a_failed_start_up_test_and_runs_no_frame(
    monkeypatch, capsys
):
    """A process whose stamp machine failed its start-up test records split, where B is
    the clock read before each frame and nothing is stamped: run, every frame would count
    as stamped, and the run would pass on stamps it never saw."""
    from wl_sync import bench
    from wl_sync.rp1 import backend as rp1

    built = []

    class _Box:
        stamp_test_failure = "no stamp when GPIO17 rose"

        def __init__(self, **kwargs):
            built.append(self)
            self.started = []
            self.emitted = []
            self.closed = False

        def start_strobed_capture(self, *args, **kwargs):
            self.started.append("codes")

        def start_edge_capture(self, *args, **kwargs):
            self.started.append("edges")

        def emit_pulses(self, pin, pulses):
            self.emitted.append(pin)
            raise AssertionError("a frame was emitted")

        def check_health(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(rp1, "Rp1Backend", _Box)
    monkeypatch.setattr(bench, "STAMP_RUN_PERIOD_S", 0)

    assert bench.main(["stamps", "--frames", "3"]) == 1
    [box] = built
    assert box.emitted == [] and box.started == []
    assert box.closed
    assert capsys.readouterr().out.splitlines() == [
        "start-up test failed (no stamp when GPIO17 rose): this process records split, "
        "and has no stamps to count"
    ]


def test_a_short_run_reports_no_tail_beyond_its_widest_bracket():
    """`statistics.quantiles` defaults to "exclusive", which extrapolates past the
    sample: 60 widths whose widest is 90 us gave a p99.9 of 90.9 us."""
    import re

    from wl_sync.bench import StampRunReport

    widths = [31_000 + 1_000 * k for k in range(60)]
    assert max(widths) == 90_000
    report = StampRunReport(frames=60, stamped=60, widths_ns=widths)
    found = re.search(r"p99 ([\d.]+) us, p99\.9 ([\d.]+) us", report.summary())
    assert found, report.summary()
    assert float(found.group(1)) <= 90.0 and float(found.group(2)) <= 90.0, found.group(0)


def test_misses_are_counted_by_kind_with_their_distances_as_a_range():
    """A miss's cause text carries its distance, so counted by text a drifting map gave
    the summary one line per distance."""
    from wl_sync.backend import STAMP_NOT_SEEN, FrameStamp
    from wl_sync.bench import STAMP_TOO_FAR, stamp_run
    from wl_sync.clockmap import LEAD_STAMP_TOLERANCE, LeadStamp
    from wl_sync.rp1.backend import STAMPS_NOT_STARTED, STAMPS_STOPPED, FrameTrace

    def far(us):
        cause = (f"the stamp lies {us} us from the frame's lead edge as the CPU "
                 f"bracketed it ({LEAD_STAMP_TOLERANCE} us allowed)")
        # A lead aligned outside its window, as the match leaves it (Ruling R26).
        lead = LeadStamp(None, 0, 3, (), us, lead=1_000_000 - us)
        return FrameStamp(None, cause), FrameTrace(lead, 30_000)

    def without_a_match(cause):
        return FrameStamp(None, cause), FrameTrace(LeadStamp(None, 0, 0, (), None), 30_000)

    frames = [far(5_010), _unstamped(), far(4_990), far(5_003),
              without_a_match(STAMPS_STOPPED), without_a_match(STAMPS_NOT_STARTED)]
    values = [FIRST + k for k in range(len(frames))]
    report = stamp_run(_ScriptedBackend(frames), values, lambda: None)

    assert report.missed == {
        STAMP_TOO_FAR: 3, STAMP_NOT_SEEN: 1, STAMPS_STOPPED: 1, STAMPS_NOT_STARTED: 1,
    }
    summary = report.summary()
    assert summary.count("from the frame's lead edge") == 1, summary
    assert (
        "missed 3: the stamp lies 4990-5010 us from the frame's lead edge as the CPU "
        f"bracketed it ({LEAD_STAMP_TOLERANCE} us allowed)"
    ) in summary, summary
    assert f"missed 1: {STAMP_NOT_SEEN}" in summary


def test_a_sigterm_ends_the_stamps_command_closed_and_summarised(monkeypatch, capsys):
    """`pkill -f 'wl_sync.bench stamps'` is how the spike ends S.5 early. SIGTERM's
    default ends the process where it stands: no close(), and no summary of the hours
    already run. The command turns it into SystemExit, so both happen, and exits 143."""
    import signal

    from wl_sync import bench
    from wl_sync.backend import FrameStamp
    from wl_sync.clockmap import LeadStamp
    from wl_sync.rp1 import backend as rp1
    from wl_sync.rp1.backend import FrameTrace

    built = []

    class _Box:
        stamp_test_failure = ""  # the start-up test passed

        def __init__(self, **kwargs):
            built.append(self)
            self.emitted = 0
            self.last_frame = None
            self.closed = False

        def start_strobed_capture(self, *args, **kwargs):
            pass

        def start_edge_capture(self, *args, **kwargs):
            pass

        def emit_pulses(self, pin, pulses):
            self.emitted += 1
            if self.emitted == 3:
                signal.raise_signal(signal.SIGTERM)  # pkill, mid-frame
            lead = LeadStamp(self.emitted, 0, len(rises_us(pulses)) - 1, (), None)
            self.last_frame = FrameTrace(lead, 30_000)
            return FrameStamp(self.emitted)

        def take_pairs(self):
            return []

        def check_health(self):
            pass

        def close(self):
            self.closed = True

    class _Unhandled(Exception):
        """What this test's own handler raises: SIGTERM's default would end pytest."""

    def unhandled(signum, frame):
        raise _Unhandled(signum)

    monkeypatch.setattr(rp1, "Rp1Backend", _Box)
    monkeypatch.setattr(bench, "STAMP_RUN_PERIOD_S", 0)
    outer = signal.signal(signal.SIGTERM, unhandled)
    try:
        status = bench.main(["stamps", "--frames", "5"])
        assert signal.getsignal(signal.SIGTERM) is unhandled, "the handler was not put back"
    finally:
        signal.signal(signal.SIGTERM, outer)
    [box] = built
    assert box.closed
    assert status == 128 + signal.SIGTERM
    out = capsys.readouterr().out
    assert "frames 2, stamped 2" in out, out
    assert "stopped by SIGTERM after 2 of 5 frames" in out, out
