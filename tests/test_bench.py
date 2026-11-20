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


def _segment(tmp_path, frames=5, lead_offset_us=60, return_low=True, wrong_value_at=None,
             timebase=None, no_b_at=None, offsets=None):
    """`no_b_at` leaves that frame's `B` out of the log, its edges still on the wire;
    `offsets` gives each frame its own lead offset."""
    records, level = [], 0
    header = _header()
    if timebase is not None:
        header = header.model_copy(update={"timebase": timebase})
    for k in range(frames):
        tick = 1_000 + k * 1_000_000
        if k != no_b_at:
            records.append(BarcodeEmitted(tick_us=tick, value=FIRST + k))
        on_wire = FIRST + k + (1000 if k == wrong_value_at else 0)
        offset = offsets[k] if offsets else lead_offset_us
        edges, level = _frame_edges(on_wire, tick + offset, level, return_low)
        records.extend(edges)
    path = tmp_path / "seg-000.log"
    write_log(path, header, records)
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


def test_a_frame_whose_b_was_dropped_is_reported_as_dropped(tmp_path):
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, timebase="rp1-pio-si", no_b_at=2), LOOP_GPIO)

    assert report.timebase == "rp1-pio-si"
    assert report.dropped == [FIRST + 2]
    assert report.unexpected == []
    assert not report.passed
    assert "dropped" in report.summary()


def test_on_a_split_segment_a_frame_never_logged_is_still_unexpected(tmp_path):
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, timebase="split", no_b_at=2), LOOP_GPIO)

    assert report.dropped == []
    assert report.unexpected == [FIRST + 2]
    assert not report.passed


def test_the_lead_edge_offset_is_reported_as_es_constant_with_its_spread(tmp_path):
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, frames=8, timebase="rp1-pio",
                               offsets=[0, 50, 60, 70, 80, 90, 100, 110]), LOOP_GPIO)

    summary = report.summary()
    assert "E's constant (B6)" in summary
    assert "median" in summary
    assert "p50" in summary and "p99 " in summary and "p99.9" in summary


def test_the_spread_gives_each_inclusive_quantile_at_its_own_cut(tmp_path):
    """Coverage: pins the cut points and the method, which passed when they were written
    and which the test above, checking labels alone, would let swap or slip a cut. Seven
    offsets 1000 us apart put 6 us between neighbouring cuts, so p50, p99 and p99.9 are
    cuts 499, 989 and 998 to the us, inclusive; the default, exclusive, extrapolates p99
    to 7920 us, past the widest offset."""
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, frames=8, timebase="rp1-pio",
                               offsets=[k * 1_000 for k in range(8)]), LOOP_GPIO)

    assert report.lead_offsets_us == [1_000, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000]
    summary = report.summary()
    assert summary.endswith(
        "median 4000 us, max 7000 us; p50 4000 us, p99 6940 us, p99.9 6994 us"
    ), summary


@pytest.mark.parametrize("frames, has_spread", [(2, False), (3, True)])
def test_the_spread_needs_two_offsets(tmp_path, frames, has_spread):
    """Coverage: pins the `>= 2` guard, untested until now. The first frame is not
    checkable, so 2 frames give one offset, where the quantiles have too few points
    (statistics raises below two), and 3 give two."""
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, frames=frames, timebase="rp1-pio"), LOOP_GPIO)

    assert len(report.lead_offsets_us) == frames - 1
    line = report.summary().splitlines()[-1]
    assert line.startswith("  lead edge after its logged tick")
    assert ("; p50 " in line) is has_spread, line


@pytest.mark.parametrize(
    "timebase, label",
    [
        ("rp1-pio-si", "lead edge after its logged tick, E's constant (B6):"),
        ("rp1-pio", "lead edge after its logged tick, E's constant (B6):"),
        ("split", "lead edge after its logged tick, the barcode delay to pass as "
                  "--barcode-delay-us:"),
        ("fake", "lead edge after its logged tick (timebase fake; only a split segment's "
                 "is a --barcode-delay-us):"),
        (None, "lead edge after its logged tick (no timebase in the header; only a split "
               "segment's is a --barcode-delay-us):"),
    ],
    ids=["rp1-pio-si", "rp1-pio", "split", "fake", "none"],
)
def test_the_lead_edge_offset_is_labelled_by_the_segments_timebase(tmp_path, timebase, label):
    """Final review M9. On one count the median is E's constant: B is the lead edge's own
    stamp, so there is no barcode delay. On split it is exactly what deploy/README.md:28-38
    tells the operator to pass as --barcode-delay-us, which the one-count label told them
    not to. Any other timebase, or none, is neither, and says where the flag's value
    comes from."""
    from wl_sync.bench import loopback

    report = loopback(_segment(tmp_path, timebase=timebase), LOOP_GPIO)

    assert f"  {label} median 60 us" in report.summary()


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
        capture_failure = ""

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


@pytest.mark.parametrize("cause", ["test", "capture"])
def test_the_stamps_command_reports_a_failed_start_up_test_and_runs_no_frame(
    monkeypatch, capsys, cause
):
    """A process whose stamp machine failed its start-up test records split, where B is
    the clock read before each frame and nothing is stamped: run, every frame would count
    as stamped, and the run would pass on stamps it never saw. A process with no PIO
    machine at all (Task 1.16) is refused too, and named for its own cause, the shim, the
    block or the capture machine, which the backend also gives as its test's failure
    (Task 1.16's review, Minor 1; Ruling R37)."""
    from wl_sync import bench
    from wl_sync.rp1 import backend as rp1

    built = []
    reason = {"test": "no stamp when GPIO17 rose",
              "capture": "opening PIO block 0 failed: errno 2"}[cause]

    class _Box:
        stamp_test_failure = reason
        capture_failure = reason if cause == "capture" else ""

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
    said = "start-up test failed" if cause == "test" else "event-code capture cannot run"
    assert capsys.readouterr().out.splitlines() == [
        f"{said} ({reason}): this process records split, and has no stamps to count"
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
        capture_failure = ""

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


# ---------------------------------------------------------------------------
# B3a and B3b (Task 1.21): the analysis alone, then the commands against the harness's
# stand-ins (tests/test_rp1_harness.py)
# ---------------------------------------------------------------------------

from tests.test_rp1_harness import _Board, _Clock, board  # noqa: E402,F401


def _skew_event(index, stamps):
    from wl_sync.rp1.harness import Event

    return Event(index, (17,), "skew", 1_000 * index, 1_000 * index + 20, 1_000 * index + 40,
                 1_000 * index + 60, False, 1_000 * index + 100,
                 {sm: list(found) for sm, found in stamps.items()})


def _skew_report(edges, stamp_of):
    from wl_sync.bench import SkewReport

    report = SkewReport(machines=[1, 2, 3])
    for k in range(edges):
        report.add(_skew_event(k, {sm: stamp_of(sm, k) for sm in (1, 2, 3)}))
    return report


@pytest.mark.parametrize("d", [0, 1, -1])
def test_one_difference_per_pair_on_every_edge_of_at_most_one_tick_passes(d):
    report = _skew_report(50, lambda sm, k: [1_000 + 10 * k + (d if sm == 3 else 0)])
    assert report.passed, report.summary()
    summary = report.summary()
    assert summary.splitlines()[0] == "start skew (B3a): PASS"
    assert f"SM3-SM1: {d} on 50 of 50" in summary and "SM2-SM1: 0 on 50 of 50" in summary
    assert f"SM3-SM2: {d} on 50 of 50" in summary


def test_a_constant_difference_of_two_ticks_fails():
    report = _skew_report(50, lambda sm, k: [1_000 + k + (2 if sm == 2 else 0)])
    assert not report.passed
    assert "SM2-SM1: 2 on 50 of 50" in report.summary()


def test_a_difference_of_zero_and_one_mixed_fails_with_both_counts():
    report = _skew_report(40, lambda sm, k: [1_000 + k + (k % 4 == 0 if sm == 2 else 0)])
    assert not report.passed
    assert "SM2-SM1: 0 x30, 1 x10" in report.summary(), report.summary()


def test_the_difference_is_read_signed_across_the_counts_wrap():
    report = _skew_report(3, lambda sm, k: [0xFFFF_FFFF if sm == 1 else 0])
    assert report.differences[(1, 2)] == {1: 3}
    assert report.passed


def test_a_missed_edge_fails_by_machine_and_is_in_no_difference():
    report = _skew_report(10, lambda sm, k: [] if (sm == 2 and k == 4) else [k])
    assert not report.passed
    assert report.missed == {2: 1} and report.differences[(1, 2)] == {0: 9}
    assert "missed SM2 1" in report.summary()


def test_two_stamps_for_one_edge_fail():
    report = _skew_report(10, lambda sm, k: [k, k + 5] if (sm == 1 and k == 2) else [k])
    assert not report.passed and report.extra == {1: 1}


@pytest.mark.parametrize("field", ["words", "full", "slips", "level_errors", "carried"])
def test_anything_but_stamps_in_the_fifos_fails_the_skew(field):
    report = _skew_report(10, lambda sm, k: [k])
    setattr(report, field, 1)
    assert not report.passed


def test_a_skew_run_cut_short_is_inconclusive_never_a_pass():
    report = _skew_report(10, lambda sm, k: [k])
    report.stopped = True
    assert not report.passed
    assert report.summary().splitlines()[0] == "start skew (B3a): INCONCLUSIVE"


def test_the_line_fit_recovers_a_known_slope_on_integers():
    """Ordinary least squares on exact integer sums: x in RAW ns about 10^15, y in us. A
    float sum of 10^30-sized squares would lose the digits a 0.01 ppm line needs."""
    from wl_sync.bench import LineFit

    fit = LineFit()
    ratio = (1 - 7e-6) / (1 + 3e-6)
    for k in range(5_000):  # an hour, a point each 0.72 s
        x = 10**15 + 720_000_000 * k
        fit.add(2 * x, int(720_000 * k * ratio) + 12_345)
    assert fit.n == 5_000
    assert abs(float(fit.slope_us_per_us()) / ratio - 1) < 1e-10
    assert fit.stderr_us_per_us() < 1e-9


def test_the_slopes_difference_is_in_ppm_with_its_error():
    from wl_sync.bench import LineFit, slope_difference_ppm

    a, b = LineFit(), LineFit()
    for k in range(1_000):  # a point a second
        a.add(2 * 10**9 * k, int(10**6 * k * (1 + 5e-6)))
        b.add(2 * 10**9 * k + 7, int(10**6 * k))
    delta, sigma = slope_difference_ppm(a, b)
    assert abs(delta - 5) < 0.01 and sigma < 0.01


def test_the_short_exit_and_held_bounds_are_the_programs_own():
    """tests/piosim.py runs the program's words: a high of SHORT_EXIT_MAX_NS or less leaves
    by the short exit at both sampling phases, one of HELD_MIN_NS or more takes the held
    loop at both, and 5.5 us depends on the phase. That is how a high's RAW bound names
    the path it took."""
    from tests.piosim import high_during, run
    from wl_sync.bench import HELD_MIN_NS, SHORT_EXIT_MAX_NS
    from wl_sync.rp1.program import ENTRY, STROBE_CAPTURE, WRAP, WRAP_TARGET

    def held(width_cycles, start):
        calls = []
        high = high_during([(start, start + width_cycles)])

        def strobe(cycle):
            calls.append(high(cycle))
            return calls[-1]

        result = run(STROBE_CAPTURE, entry=ENTRY, wrap_target=WRAP_TARGET, wrap=WRAP,
                     cycles=start + width_cycles + 40, strobe=strobe)
        assert len(result.pushes) == 2  # stamped once, either way
        return calls[calls.index(True) + 1]  # the jmp pin at 9, after the capture

    cycle_ns = 500
    for start in (20, 21):
        for width in range(2, SHORT_EXIT_MAX_NS // cycle_ns + 1):
            assert not held(width, start)
        for width in range(HELD_MIN_NS // cycle_ns, HELD_MIN_NS // cycle_ns + 6):
            assert held(width, start)
    assert {held(11, 20), held(11, 21)} == {True, False}


@pytest.fixture
def run_fast(monkeypatch):
    """B3b's hour and its 10^5 events, scaled down to a laptop's minute."""
    from wl_sync import bench

    monkeypatch.setattr(bench, "B3B_MIN_DURATION_S", 60)
    monkeypatch.setattr(bench, "B3B_MIN_A_EVENTS", 1_000)
    monkeypatch.setattr(bench, "PROBE_SHAPE_PULSES", 20)
    monkeypatch.setattr(bench, "PROBE_RISES", 20)


def _patch_rig(monkeypatch, board, clock=None):
    """The commands build their rig with the shim and /dev/gpiomem0; both are the board's."""
    from wl_sync.rp1 import backend as rp1
    from wl_sync.rp1 import harness

    monkeypatch.setattr(rp1, "_load_shim", lambda: board.shim)
    monkeypatch.setattr(harness, "Gpiomem", lambda path: board.bank)
    if clock is not None:
        board.clock = clock
        monkeypatch.setattr(harness, "time", clock)


def test_the_skew_command_runs_three_machines_on_gpio17_and_passes(board, monkeypatch, capsys):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    assert bench.main(["skew", "--machines", "3", "--edges", "200"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "start skew (B3a): PASS", out
    assert "edges 200; stamped SM1 200, SM2 200, SM3 200" in out
    assert "stamp SM1, SM2, SM3 on GPIO17, unclaimed; one sync enable" in out
    assert board.shim.enables == [0b1111] and board.shim.closed == 1
    assert board.gpiod.released == [{17: 0}]
    assert [c for _, _, c, _ in board.shim.claims] == [0, 0, 0]


def test_the_skew_command_fails_on_dividers_out_of_phase(board, monkeypatch, capsys):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.phase = {3: lambda k: k % 2}
    assert bench.main(["skew", "--edges", "100"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "start skew (B3a): FAIL"
    assert "SM3-SM1: 0 x" in out and ", 1 x" in out


@pytest.mark.parametrize("fault, said", [
    ("refuse_sync", "the sync enable was refused"),
    ("blind", "the PIO does not see GPIO17"),
])
def test_the_skew_command_stops_when_the_machines_are_not_seen_running(
    board, monkeypatch, capsys, fault, said
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    if fault == "refuse_sync":
        board.shim.refuse_sync = True
    else:
        board.blind = {17}
    assert bench.main(["skew", "--edges", "100"]) == 1
    out = capsys.readouterr().out
    assert said in out and "edges 0" in out
    assert board.shim.closed == 1


def test_the_skew_command_takes_two_or_three_machines_only(board, monkeypatch):
    from wl_sync import bench

    for machines in ("1", "4"):
        with pytest.raises(SystemExit) as exited:
            bench.main(["skew", "--machines", machines])
        assert exited.value.code == 2


def test_the_skew_command_records_every_edge_as_integers(board, monkeypatch, tmp_path):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    path = tmp_path / "b3a.csv"
    assert bench.main(["skew", "--machines", "2", "--edges", "30", "--record", str(path)]) == 0
    lines = path.read_text().splitlines()
    comments = [line for line in lines if line.startswith("#")]
    assert any(line.startswith("# enable_raw_ns=") for line in comments)
    header, *rows = [line for line in lines if not line.startswith("#")]
    assert header == bench.RECORD_COLUMNS
    assert len(rows) == 31  # the running check's pulse, then the 30 edges
    for row in rows:
        phase, index, pins, shape, *raw, landed, drained, stamps = row.split(",")
        assert all(value == "" or int(value) >= 0 for value in raw)
        assert {pair.split("=")[0] for pair in stamps.split()} == {"1", "2"}
        assert all(int(pair.split("=")[1]) >= 0 for pair in stamps.split())


def test_the_skew_command_closes_the_block_and_lines_when_the_run_raises(
    board, monkeypatch, capsys
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.shim.wlsync_sm_get
    calls = []

    def failing(sm):
        calls.append(sm)
        if len(calls) == 40:
            raise RuntimeError("piolib")
        return original(sm)

    board.shim.wlsync_sm_get = failing
    with pytest.raises(RuntimeError, match="piolib"):
        bench.main(["skew", "--edges", "100"])
    assert board.shim.closed == 1 and board.gpiod.released == [{17: 0}]


def test_a_sigterm_ends_the_skew_command_closed_and_inconclusive(board, monkeypatch, capsys):
    import signal

    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.shim.wlsync_sm_get
    calls = []

    def terminating(sm):
        calls.append(sm)
        if len(calls) == 60:
            signal.raise_signal(signal.SIGTERM)
        return original(sm)

    board.shim.wlsync_sm_get = terminating

    def unhandled(signum, frame):
        raise AssertionError("SIGTERM's handler was not the command's")

    outer = signal.signal(signal.SIGTERM, unhandled)
    try:
        status = bench.main(["skew", "--edges", "1000"])
        assert signal.getsignal(signal.SIGTERM) is unhandled
    finally:
        signal.signal(signal.SIGTERM, outer)
    assert status == 128 + signal.SIGTERM
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "start skew (B3a): INCONCLUSIVE", out
    assert "stopped by SIGTERM after" in out
    assert board.shim.closed == 1


def test_the_streams_probe_sees_both_pins_and_shows_the_short_exit(
    board, monkeypatch, capsys, run_fast
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    assert bench.main(["streams", "--probe-only"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "independent streams (B3b) probe: PASS", out
    assert "GPIO18 rises 20, stamped A 20, B 0; GPIO17 rises 20, stamped B 20, A 0" in out
    assert "short-exit path (meter width <= 5.0 us): shown" in out
    assert board.bank.writes and board.bank.writes[-1] == (0x3094, 0x3000)
    assert board.bank.ctrl[18] & 0x3000 == 0
    assert [(pin, c) for _, pin, c, _ in board.shim.claims] == [(18, 0), (17, 0)]


def test_a_blind_dense_pin_stops_streams_before_the_run(board, monkeypatch, capsys, run_fast):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.blind = {18}
    assert bench.main(["streams", "--duration", "60"]) == 1
    out = capsys.readouterr().out
    assert "the PIO does not see GPIO18, unclaimed" in out
    assert "--dense-pin 19" in out
    assert "A (GPIO18)" not in out
    assert board.shim.closed == 1 and board.bank.writes == []  # the writer never armed


def test_crosstalk_between_the_pins_fails_the_probe(board, monkeypatch, capsys, run_fast):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.crosstalk = {18: 17}
    assert bench.main(["streams", "--probe-only"]) == 1
    assert "B 20" in capsys.readouterr().out


def test_writer_highs_too_wide_for_the_short_exit_stop_streams(
    board, monkeypatch, capsys, run_fast
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board, _Clock(read_ns=3_000))
    assert bench.main(["streams", "--duration", "60"]) == 1
    out = capsys.readouterr().out
    assert "short-exit path (meter width <= 5.0 us): not shown" in out


def _streams(board, monkeypatch, capsys, *args, clock=None):
    from wl_sync import bench

    _patch_rig(monkeypatch, board, clock)
    status = bench.main(["streams", "--duration", "60", "--rate", "20", *args])
    return status, capsys.readouterr().out


def test_two_crystals_pass_b3b_with_both_slopes_about_ten_ppm_from_one(
    board, monkeypatch, capsys, run_fast
):
    status, out = _streams(board, monkeypatch, capsys, "--marker",
                           clock=_Clock(cpu_ppm=3, rp1_ppm=-7))
    assert status == 0, out
    assert out.splitlines()[0] == "independent streams (B3b): PASS", out
    import re

    slopes = [float(s) for s in re.findall(r"slope (\d\.\d+)", out)]
    assert len(slopes) >= 2 and all(abs(s - (1 - 10e-6)) < 1e-7 for s in slopes), out
    assert "held path (meter width >= 6.0 us)" in out
    assert "short exit (meter width <= 5.0 us)" in out
    assert re.search(r"marker A-B: 0 on 60 of 60 frames", out), out
    assert board.bank.ctrl[18] & 0x3000 == 0 and board.shim.closed == 1


def test_a_tick_lost_at_each_dense_event_fails_b3b_near_its_rate(
    board, monkeypatch, capsys, run_fast
):
    import re

    board.loss_us = {1: 1}  # A is SM1, the first stamp machine
    status, out = _streams(board, monkeypatch, capsys)
    assert status == 1
    assert out.splitlines()[0] == "independent streams (B3b): FAIL", out
    delta = float(re.search(r"A - B: (-?[\d.]+) ppm", out).group(1))
    assert -25 < delta < -15, out  # 1 us lost at each of 20 events a second


def test_a_tick_lost_on_the_short_exit_alone_fails_b3b(board, monkeypatch, capsys, run_fast):
    """Half the dense events are the writer's short highs, so 1 us lost at each of them is
    10 us a second at 20 events a second: A's count falls 10 ppm behind B's. Which path
    lost it the slopes cannot say, since both paths share A's one counter."""
    import re

    board.short_loss_us = {1: 1}
    status, out = _streams(board, monkeypatch, capsys)
    assert status == 1
    assert out.splitlines()[0] == "independent streams (B3b): FAIL", out
    delta = float(re.search(r"A - B: (-?[\d.]+) ppm", out).group(1))
    assert -13 < delta < -7, out


def test_a_streams_run_shorter_than_an_hour_is_inconclusive(
    board, monkeypatch, capsys, run_fast
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    assert bench.main(["streams", "--duration", "30", "--rate", "20"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "independent streams (B3b): INCONCLUSIVE", out
    assert "30 s of the 60 s" in out


@pytest.mark.parametrize("pin", ["17", "20", "16"])
def test_the_streams_command_takes_a_spare_dense_pin_only(pin):
    from wl_sync import bench

    with pytest.raises(SystemExit) as exited:
        bench.main(["streams", "--dense-pin", pin])
    assert exited.value.code == 2


def test_the_streams_command_runs_on_gpio19_when_asked(board, monkeypatch, capsys, run_fast):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    assert bench.main(["streams", "--dense-pin", "19", "--probe-only"]) == 0
    assert {offset for offset, _ in board.bank.writes} == {0x209C, 0x309C}
    assert "GPIO19 rises 20" in capsys.readouterr().out


def test_a_writer_that_refuses_the_dense_pin_stops_streams_with_nothing_written(
    board, monkeypatch, capsys, run_fast
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    real = board.gpiod.request_lines

    def held_by_the_pio(path, config, consumer):
        request = real(path, config, consumer)
        board.bank.ctrl[18] = (board.bank.ctrl[18] & ~0x1F) | 7
        return request

    board.gpiod.request_lines = held_by_the_pio
    assert bench.main(["streams", "--probe-only"]) == 1
    assert "GPIO18 is the PIO's" in capsys.readouterr().out
    assert board.bank.writes == [] and board.shim.calls == []


def test_a_sigterm_ends_streams_with_the_writer_restored_and_inconclusive(
    board, monkeypatch, capsys, run_fast
):
    import signal

    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.bank.write32
    writes = []

    def terminating(offset, value):
        original(offset, value)
        writes.append(offset)
        if len(writes) == 101 and offset == 0x2094:  # mid-pulse, the override set
            signal.raise_signal(signal.SIGTERM)

    board.bank.write32 = terminating
    outer = signal.signal(signal.SIGTERM, lambda *a: None)
    try:
        status = bench.main(["streams", "--duration", "60", "--rate", "20"])
    finally:
        signal.signal(signal.SIGTERM, outer)
    assert status == 128 + signal.SIGTERM
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "independent streams (B3b): INCONCLUSIVE", out
    assert "stopped by SIGTERM" in out
    assert board.bank.ctrl[18] & 0x3000 == 0 and board.bank.pad(18) == 0
    assert board.shim.closed == 1


def test_the_streams_command_records_every_edge_of_probe_and_run(
    board, monkeypatch, capsys, tmp_path, run_fast
):
    path = tmp_path / "b3b.csv"
    status, out = _streams(board, monkeypatch, capsys, "--duration", "60",
                           "--record", str(path))
    from wl_sync import bench

    rows = [line for line in path.read_text().splitlines() if not line.startswith("#")]
    assert rows[0] == bench.RECORD_COLUMNS
    phases = {row.split(",")[0] for row in rows[1:]}
    assert phases == {"probe", "run"}


def test_the_streams_record_says_how_its_meter_counts_become_widths(
    board, monkeypatch, capsys, tmp_path, run_fast
):
    """The CSV alone turns each meter count back into a width: it names clk_sys and the
    meter's machine and scale (Task 1.21 fix round 6's review, Minor 7). Red before the
    two comment lines were written."""
    path = tmp_path / "b3b.csv"
    _streams(board, monkeypatch, capsys, "--duration", "60", "--record", str(path))
    comments = [line[2:] for line in path.read_text().splitlines() if line.startswith("# ")]
    hz = [line for line in comments if line.startswith("clk_sys_hz=")]
    assert hz == [f"clk_sys_hz={200_000_000}"]
    [scale] = [line for line in comments if line.startswith("meter=")]
    assert scale == "meter=SM3 on GPIO18: width_ns = (2 count + 1) x 5, +-5"


def test_the_default_writer_holds_are_proven_highs_of_at_least_one_microsecond():
    """The hold is timed from the SET's read-back, so it is a proven lower bound on the
    high (fix round 4): 1 us, the program's minimum (backend.py:975-976), is enough, and
    the holds stop at 2 us to leave two read-backs room under 5 us."""
    from wl_sync import bench

    assert bench.SHORT_HOLDS_NS == (1_000, 1_500, 2_000)
    assert min(bench.SHORT_HOLDS_NS) >= 1_000


def test_a_missed_writer_high_is_named_as_one(board, monkeypatch, capsys, run_fast):
    """A miss on a register-writer pulse is the harness's, not the program's, until shown
    otherwise; the fault says which kind of high it was (review I2)."""
    real = board.bank._land
    sets = []

    def dropping(offset, value):  # where a posted SET lands (fix round 4)
        if offset == 0x2094:
            sets.append(offset)
            if len(sets) in (150, 151):
                board.blind.add(18)
        real(offset, value)
        board.blind.discard(18)

    board.bank._land = dropping
    status, out = _streams(board, monkeypatch, capsys)
    assert status == 1
    assert "fails: A missed 2 (register-writer highs 2, libgpiod highs 0)" in out, out


def test_a_sighup_ends_streams_closed_restored_and_inconclusive(
    board, monkeypatch, capsys, run_fast
):
    """An ssh session that drops sends SIGHUP, whose default would end the process
    between a pulse's two stores with no restore (review I3). It is handled as SIGTERM."""
    import signal

    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.bank.write32
    writes = []

    def hanging_up(offset, value):
        original(offset, value)
        writes.append(offset)
        if len(writes) == 201 and offset == 0x2094:
            signal.raise_signal(signal.SIGHUP)

    board.bank.write32 = hanging_up
    outer = signal.signal(signal.SIGHUP, lambda *a: None)
    try:
        status = bench.main(["streams", "--duration", "60", "--rate", "20"])
        assert signal.getsignal(signal.SIGHUP) is not bench._terminate
    finally:
        signal.signal(signal.SIGHUP, outer)
    assert status == 128 + signal.SIGHUP
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "independent streams (B3b): INCONCLUSIVE", out
    assert "stopped by SIGHUP" in out
    assert board.bank.ctrl[18] & 0x3000 == 0 and board.bank.pad(18) == 0


def test_the_override_command_reads_both_spare_pins_and_writes_nothing(
    board, monkeypatch, capsys
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.hold_as_output(18)
    assert bench.main(["override"]) == 0
    out = capsys.readouterr().out
    assert "GPIO18:" in out and "GPIO19:" in out and "OUTOVER 0, OEOVER 0" in out
    assert board.bank.writes == [] and board.gpiod.requests == []


def test_the_override_command_fails_on_an_override_left_set_and_names_the_clear(
    board, monkeypatch, capsys
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.ctrl[18] |= 3 << 12
    assert bench.main(["override"]) == 1
    out = capsys.readouterr().out
    assert "GPIO18:" in out and "OUTOVER 3" in out
    assert "python -m wl_sync.bench override --pin 18 --clear" in out
    assert board.bank.writes == []


def test_the_override_command_clears_one_pin_when_asked(board, monkeypatch, capsys):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.hold_as_output(18)
    board.bank.ctrl[18] |= 3 << 12
    assert bench.main(["override", "--pin", "18", "--clear"]) == 0
    assert board.bank.writes == [(0x3094, 0x3000)]
    assert "cleared" in capsys.readouterr().out


def test_the_override_command_refuses_to_clear_without_naming_a_pin(board, monkeypatch):
    from wl_sync import bench

    with pytest.raises(SystemExit) as exited:
        bench.main(["override", "--clear"])
    assert exited.value.code == 2


@pytest.mark.parametrize("pin", ["17", "20"])
def test_the_override_command_takes_a_spare_pin_only(board, monkeypatch, pin):
    """Coverage: `override --pin` names GPIO18 or GPIO19 only, refused by argparse with
    nothing read or written (Task 1.22; the one choices refusal no test showed red: with
    `choices` dropped from `--pin`, every other test passed)."""
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    for argv in (["override", "--pin", pin], ["override", "--pin", pin, "--clear"]):
        with pytest.raises(SystemExit) as exited:
            bench.main(argv)
        assert exited.value.code == 2
    assert board.bank.writes == []


def test_the_override_command_reports_a_refused_clear_with_nothing_written(
    board, monkeypatch, capsys
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.ctrl[18] |= 3 << 12
    board.used[18] = "someone"
    assert bench.main(["override", "--pin", "18", "--clear"]) == 1
    assert "someone" in capsys.readouterr().out and board.bank.writes == []


def test_a_close_that_fails_still_prints_the_runs_summary(board, monkeypatch, capsys):
    """Review M2: a close that raised in the command's `finally` lost the summary of the
    run before it."""
    from wl_sync import bench

    _patch_rig(monkeypatch, board)

    def failing_close():
        raise RuntimeError("pio_close")

    board.shim.wlsync_pio_close = failing_close
    assert bench.main(["skew", "--edges", "20"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "start skew (B3a): PASS", out
    assert "closing the rig failed: RuntimeError('pio_close')" in out
    assert board.gpiod.released == [{17: 0}]


def test_a_close_that_fails_after_the_run_raised_is_noted_on_the_runs_error(
    board, monkeypatch
):
    """Review N1: the run's own error propagates, and carries the close's as a note."""
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.shim.wlsync_sm_get
    calls = []

    def failing(sm):
        calls.append(sm)
        if len(calls) == 40:
            raise RuntimeError("piolib")
        return original(sm)

    def failing_close():
        raise RuntimeError("pio_close")

    board.shim.wlsync_sm_get = failing
    board.shim.wlsync_pio_close = failing_close
    with pytest.raises(RuntimeError, match="piolib") as raised:
        bench.main(["skew", "--edges", "100"])
    assert any("pio_close" in note for note in getattr(raised.value, "__notes__", [])), (
        raised.value)
    assert board.gpiod.released == [{17: 0}]


def test_a_sighup_nohup_ignores_stays_ignored_and_the_run_goes_on(
    board, monkeypatch, capsys, run_fast
):
    """Review N2: under nohup SIGHUP is ignored, and the harness keeps it so, or the hour
    it was launched to survive would end."""
    import signal

    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    original = board.bank.write32
    writes = []

    def hanging_up(offset, value):
        original(offset, value)
        writes.append(offset)
        if len(writes) == 201 and offset == 0x2094:
            signal.raise_signal(signal.SIGHUP)

    board.bank.write32 = hanging_up
    outer = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        status = bench.main(["streams", "--duration", "60", "--rate", "20"])
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, outer)
    out = capsys.readouterr().out
    assert status == 0, out
    assert "stopped by" not in out and "run 60 s of the 60 s asked" in out


def test_the_override_command_prints_each_pins_own_clear_command(board, monkeypatch, capsys):
    """Review N4: the recovery is the command `override` prints for the pin it found."""
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.ctrl[19] |= 3 << 12
    assert bench.main(["override"]) == 1
    out = capsys.readouterr().out
    assert "python -m wl_sync.bench override --pin 19 --clear" in out
    assert "--pin 18 --clear" not in out


def test_clearing_a_pin_with_nothing_set_exits_nonzero_and_says_so(
    board, monkeypatch, capsys
):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.ctrl[19] |= 3 << 12  # the run used GPIO19, and 18 is asked
    assert bench.main(["override", "--pin", "18", "--clear"]) == 1
    out = capsys.readouterr().out
    assert "NOT CLEARED" in out and "no override set" in out
    assert board.bank.writes == []


@pytest.mark.parametrize("writer, landed, upper, lower, path", [
    (True, False, 4_000, 1_000, "short"),
    (False, True, 4_000, 1_000, "short"),   # a libgpiod high read back at both ends
    (False, False, 4_000, 1_000, None),     # not read back: its upper bound is no bound
    (False, True, 20_000, 10_000, "held"),
    (True, False, 5_500, 1_000, None),      # between the bounds' reach
])
def test_a_highs_path_is_judged_by_its_bounds_whoever_drove_it(writer, landed, upper,
                                                                lower, path):
    """Fix round 4: the path rule is the same for libgpiod's highs as for the writer's,
    once both ends are read back."""
    from wl_sync.bench import path_of
    from wl_sync.rp1.harness import Event

    rise_after = 1_000 + 300
    event = Event(1, (18,), "s", 1_000, rise_after, rise_after + lower, 1_000 + upper,
                  landed, 0, {}, writer, landed, None)
    assert path_of(event) == path


@pytest.mark.parametrize("width_ns, path", [
    (4_990, "short"), (4_995, "short"), (4_996, None), (5_500, None), (6_004, None),
    (6_005, "held"), (1_000_000, "held"), (1_000, "short"),
])
def test_the_meters_width_names_the_path_with_its_uncertainty(width_ns, path):
    """Fix round 6: short when the widest the high can be is 5.0 us, held when the
    narrowest is 6.0 us (the program's own bounds, piosim), the meter's 5 ns either way;
    between, neither, and said so."""
    from wl_sync.bench import path_of_width

    assert path_of_width(width_ns, 5) == path


def test_the_probe_judges_the_short_exit_by_the_meter_not_the_cpu(
    board, monkeypatch, capsys, run_fast
):
    """As on the box (box step 2): each register read costs 3 us, so no CPU bound proves a
    writer high short, but the PIO's own width meter does, and the probe passes on it."""
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.bank.read_cost_ns = 3_000
    assert bench.main(["streams", "--probe-only"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "independent streams (B3b) probe: PASS", out
    assert "short-exit path (meter width <= 5.0 us): shown" in out, out
    assert "0 proven short by the CPU" in out
    assert "width meter, median:" in out


def test_a_width_meter_that_misses_highs_fails_the_probe(board, monkeypatch, capsys, run_fast):
    from wl_sync import bench

    _patch_rig(monkeypatch, board)
    board.meter_drop = 3
    assert bench.main(["streams", "--probe-only"]) == 1
    out = capsys.readouterr().out
    assert "the width meter measured" in out, out


def test_the_runs_path_shares_are_the_meters(board, monkeypatch, capsys, run_fast):
    """B3b's per-path share (the user's ruling, ">= 25% each") counts by the meter: with
    3 us reads no CPU bound credits the short exit, and the meter does."""
    import re

    board.bank.read_cost_ns = 3_000
    status, out = _streams(board, monkeypatch, capsys)
    assert status == 0, out
    short = int(re.search(r"short exit \(meter width <= 5.0 us\) (\d+)", out).group(1))
    held = int(re.search(r"held path \(meter width >= 6.0 us\) (\d+)", out).group(1))
    assert short >= 500 and held >= 500, out


def test_a_width_meter_that_misses_a_run_high_fails_b3b(board, monkeypatch, capsys, run_fast):
    real = board._meter
    calls = []

    def dropping(pin, level):
        if not level:
            calls.append(pin)
            if len(calls) == 900:
                board.meter_drop = 1
        real(pin, level)

    board._meter = dropping
    status, out = _streams(board, monkeypatch, capsys)
    assert status == 1
    assert "fails: width meter missed 1" in out, out
