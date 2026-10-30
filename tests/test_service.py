import pytest

from wl_sync.backend import CaptureLoss, FakeBackend
from wl_sync.barcode import IDLE_MIN_US, decode_edges
from wl_sync.log import BarcodeEmitted, CodeWord, Edge, Loss, TICK_WRAP_US
from wl_sync.service import FRAME_TIME_GPIO, BarcodeGenerator, Recorder, frame_times

BARCODE_PIN = 17
DATA_BASE = 2
DATA_COUNT = 16
STROBE_PIN = 18
REWARD_PIN = 23


class _CollectingSink:
    def __init__(self):
        self.written = []

    def write(self, record):
        self.written.append(record)


def pulses_to_edges(pulses, start_us):
    edges = []
    tick = start_us
    for level, duration in pulses:
        edges.append((tick, level))
        tick += duration
    edges.append((tick, 0))
    return edges


def test_generator_emits_a_decodable_frame():
    backend = FakeBackend()
    BarcodeGenerator(backend, pin=BARCODE_PIN).emit_frame(1000)
    assert len(backend.emitted) == 1
    pin, pulses = backend.emitted[0]
    assert pin == BARCODE_PIN
    edges = pulses_to_edges(pulses, IDLE_MIN_US)
    assert [b.value for b in decode_edges(edges, start_us=0)] == [1000]


def test_generator_emits_exactly_the_value_it_is_given():
    """The generator alters nothing, INCLUDING a value that repeats one it already sent.

    It held a monotonicity clamp until 2026-08-22. The clamp was not wrong in itself --
    it moved to wl_sync.cli._ValueSource, which is tested for it -- but it sat
    downstream of the checkpoint, so a value that got clamped here went onto the wire
    differing from the one marked as spent. Passing 7 twice is therefore asserting the
    absence of that clamp: an emitter that silently corrects its caller cannot be
    checkpointed against.
    """
    backend = FakeBackend()
    generator = BarcodeGenerator(backend, pin=BARCODE_PIN)

    generator.emit_frame(7)
    generator.emit_frame(7)

    sent = [
        decode_edges(pulses_to_edges(pulses, IDLE_MIN_US), start_us=0)[0].value
        for _pin, pulses in backend.emitted
    ]
    assert sent == [7, 7]


def test_recorder_captures_strobed_code_words():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    backend.inject_word(1_000, 0x8001)
    backend.inject_word(2_000, 0x0001)
    assert recorder.records() == [
        CodeWord(tick_us=1_000, word=0x8001),
        CodeWord(tick_us=2_000, word=0x0001),
    ]


def test_recorder_captures_edges():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 3_000)
    backend.inject_edge(REWARD_PIN, 0, 3_500)
    assert recorder.records() == [
        Edge(tick_us=3_000, gpio=REWARD_PIN, level=1),
        Edge(tick_us=3_500, gpio=REWARD_PIN, level=0),
    ]


def test_records_are_returned_in_tick_order_across_types():
    """The two capture paths have no guaranteed delivery ordering between them,
    so a word may arrive after an edge that precedes it."""
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 5_000)
    backend.inject_word(1_000, 0x8001)
    assert [r.tick_us for r in recorder.records()] == [1_000, 5_000]


def test_out_of_order_delivery_does_not_fake_a_wrap():
    """Placement is by distance from the clock, not from the previous tick. A shared
    "falls below the previous tick" counter would read an edge at 5000 followed by a
    word at 1000 as a 32-bit wraparound."""
    backend = FakeBackend()
    backend.set_now_us(5_000)
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 5_000)
    backend.inject_word(1_000, 0x8001)
    assert [r.tick_us for r in recorder.records()] == [1_000, 5_000]


def test_the_word_path_crosses_a_wrap_with_the_clock():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    backend.set_now_us(TICK_WRAP_US - 500)
    backend.inject_word(TICK_WRAP_US - 500, 0x0001)
    backend.set_now_us(TICK_WRAP_US + 500)
    backend.inject_word(500, 0x0002)
    assert [r.tick_us for r in recorder.records()] == [
        TICK_WRAP_US - 500,
        TICK_WRAP_US + 500,
    ]


def test_a_word_after_an_edge_path_wrap_is_placed_after_the_wrap():
    """The clock crosses the wrap, so a word stamped after it is 2^32 us later than its raw
    tick says, on the same timebase as the edges. (This asserted CodeWord == 1_000 before
    the shared clock: a word that had not wrapped, which is the reported fault.)"""
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.set_now_us(TICK_WRAP_US - 200)
    backend.inject_edge(REWARD_PIN, 1, TICK_WRAP_US - 200)
    backend.set_now_us(TICK_WRAP_US + 200)
    backend.inject_edge(REWARD_PIN, 0, 200)
    backend.set_now_us(TICK_WRAP_US + 1_000)
    backend.inject_word(1_000, 0x0001)
    records = recorder.records()
    assert [r.tick_us for r in records if isinstance(r, Edge)] == [
        TICK_WRAP_US - 200,
        TICK_WRAP_US + 200,
    ]
    assert [r.tick_us for r in records if isinstance(r, CodeWord)] == [TICK_WRAP_US + 1_000]


def test_record_barcode_writes_a_barcode_record():
    """Breakage 5: record_barcode had no unit test at all -- its only coverage was
    end-to-end through run(), so nothing pinned its own behaviour."""
    backend = FakeBackend()
    sink = _CollectingSink()
    Recorder(backend, sink=sink).record_barcode(1_000, 212_000_000)
    assert sink.written == [BarcodeEmitted(tick_us=1_000, value=212_000_000)]


def test_a_wrap_seen_on_one_path_places_the_other_paths_after_it_too():
    """The reason barcodes go through Recorder at all. The three paths share one timebase
    and one wrap: once the edge path has crossed the wrap, a word and a barcode stamped
    after it are 2^32 us later than their raw ticks say, and are placed there -- on the
    same timebase as the edge, not 4295 s behind it. (Before the shared clock this test
    asserted the opposite, per-path independence: that is the fault, not a feature.)"""
    backend = FakeBackend()
    sink = _CollectingSink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])

    backend.set_now_us(1_000_000)
    recorder.record_barcode(1_000_000, 500)
    backend.set_now_us(TICK_WRAP_US - 200)
    backend.inject_edge(REWARD_PIN, 1, TICK_WRAP_US - 200)  # the clock crosses the wrap
    backend.set_now_us(TICK_WRAP_US + 200)
    backend.inject_edge(REWARD_PIN, 0, 200)
    backend.set_now_us(TICK_WRAP_US + 5_000)
    backend.inject_word(5_000, 0x0001)
    backend.set_now_us(TICK_WRAP_US + 2_000_000)
    recorder.record_barcode(2_000_000, 501)  # stamped after it, so placed after it

    ticks = {}
    for record in sink.written:
        ticks.setdefault(type(record).__name__, []).append(record.tick_us)
    assert ticks["BarcodeEmitted"] == [1_000_000, TICK_WRAP_US + 2_000_000]
    assert ticks["CodeWord"] == [TICK_WRAP_US + 5_000]
    assert ticks["Edge"] == [TICK_WRAP_US - 200, TICK_WRAP_US + 200]


def test_the_barcode_path_wraps_with_the_clock():
    """A day-long run's B ticks must cross each wrap with the clock, or they fall 2^32 us
    behind the E and W ticks they exist to align every 71.6 minutes. The edge after the
    wrap shares that timebase."""
    backend = FakeBackend()
    sink = _CollectingSink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_edges([REWARD_PIN])

    backend.set_now_us(TICK_WRAP_US - 500)
    recorder.record_barcode(TICK_WRAP_US - 500, 500)
    backend.set_now_us(TICK_WRAP_US + 500)
    recorder.record_barcode(500, 501)  # raw counter wrapped
    backend.set_now_us(TICK_WRAP_US + 1_000)
    backend.inject_edge(REWARD_PIN, 1, 1_000)  # same wrap, same timebase

    assert [r.tick_us for r in sink.written] == [
        TICK_WRAP_US - 500,
        TICK_WRAP_US + 500,
        TICK_WRAP_US + 1_000,
    ]


def test_recorder_ignores_unregistered_pins():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(99, 1, 1_000)
    assert recorder.records() == []


def test_frame_time_pins_match_the_board():
    """GPIO26/27 per spec §4. Named, because a bare pin number in a call site is the
    kind of thing that silently survives a board change."""
    assert FRAME_TIME_GPIO == {"cam_frame_eye": 26, "cam_frame_beh": 27}


def test_frame_times_are_the_falling_edges():
    """THE point of this function, and a hardware fact rather than a convention:
    ExposureActive asserts by pulling LOW, and that edge is actively driven by the
    camera's opto transistor. The RISING edge is RC through the 1k pull-up and varies
    with cable length, so anything timed off it carries a systematic error. See
    hardware/breakout/frame-time-inputs.md §6.
    """
    records = [
        Edge(tick_us=1_000, gpio=26, level=0),
        Edge(tick_us=1_200, gpio=26, level=1),
        Edge(tick_us=3_000, gpio=26, level=0),
        Edge(tick_us=3_200, gpio=26, level=1),
    ]
    assert frame_times(records, gpio=26) == [1_000, 3_000]


def test_frame_times_ignore_other_lines():
    records = [
        Edge(tick_us=1_000, gpio=26, level=0),
        Edge(tick_us=1_100, gpio=27, level=0),
        Edge(tick_us=1_200, gpio=REWARD_PIN, level=0),
    ]
    assert frame_times(records, gpio=27) == [1_100]


def test_frame_times_ignore_code_words():
    records = [
        CodeWord(tick_us=500, word=7),
        Edge(tick_us=1_000, gpio=26, level=0),
    ]
    assert frame_times(records, gpio=26) == [1_000]


def test_recorder_captures_both_frame_time_lines():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_edges(sorted(FRAME_TIME_GPIO.values()))
    backend.inject_edge(26, 0, 1_000)
    backend.inject_edge(27, 0, 1_100)
    assert frame_times(recorder.records(), gpio=26) == [1_000]
    assert frame_times(recorder.records(), gpio=27) == [1_100]


def test_recorder_streams_to_a_sink_in_arrival_order():
    """Arrival order, NOT tick order. Two capture paths are required to show this at
    all: within ONE path the delivered ticks are monotonically non-decreasing, so
    arrival and sorted order are identical and the test would prove nothing."""
    backend = FakeBackend()
    sink = _CollectingSink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 5_000)
    backend.inject_word(1_000, 0x8001)
    assert [record.tick_us for record in sink.written] == [5_000, 1_000]


def test_a_sink_replaces_in_memory_accumulation():
    """Nothing may accumulate when a sink is given, or an all-day run exhausts RAM."""
    backend = FakeBackend()
    recorder = Recorder(backend, sink=_CollectingSink())
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 1_000)
    assert recorder.records() == []


def test_the_default_recorder_is_unchanged():
    """Same injections as the sink test above, opposite result: the in-memory path
    sorts. If sorted() were dropped from records(), this would fail."""
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 5_000)
    backend.inject_word(1_000, 0x8001)
    assert [record.tick_us for record in recorder.records()] == [1_000, 5_000]


# --- one clock for all three paths ------------------------------------------------------
#
# FakeBackend.set_now_us() holds the box's clock at a TRUE elapsed time (it wraps to 32
# bits, as the hardware does); the delivery callbacks are then driven with raw ticks, so
# a test can say what time it is and what a path reports separately.

_MASK = TICK_WRAP_US - 1


def _tick_barcodes(backend, recorder, from_us, until_us, step_us=60_000_000):
    """The barcode path from `from_us` to `until_us`, thinned to once a minute: all that
    matters is that it reads the clock more often than every 71.6 minutes, as the real
    1 Hz one does. Returns the true time it stopped at."""
    true_us = from_us
    while true_us + step_us <= until_us:
        true_us += step_us
        backend.set_now_us(true_us)
        recorder.record_barcode(true_us & _MASK, 1)
    return true_us


def _word_recorder():
    backend = FakeBackend()
    sink = _CollectingSink()
    recorder = Recorder(backend, sink=sink)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    return backend, sink, recorder


def _words(sink):
    return [r for r in sink.written if isinstance(r, CodeWord)]


@pytest.mark.parametrize("wraps", [1.5, 2.5])
def test_a_word_after_a_silence_longer_than_a_wrap_lands_on_the_barcode_timebase(wraps):
    """The fault: the word path saw nothing for more than one wrap period, so its own
    unwrapper missed the wrap and placed every later word 2^32 us early against the
    barcodes wl-preproc aligns by."""
    backend, sink, recorder = _word_recorder()
    backend.inject_word(1_000_000, 0x0001)
    silence_end = int(wraps * TICK_WRAP_US) + 5_000_000
    _tick_barcodes(backend, recorder, 1_000_000, silence_end)
    backend.set_now_us(silence_end)
    backend.inject_word(silence_end & _MASK, 0x0002)

    assert [w.tick_us for w in _words(sink)] == [1_000_000, silence_end]
    barcodes = [r for r in sink.written if isinstance(r, BarcodeEmitted)]
    assert barcodes[-1].tick_us < silence_end <= barcodes[-1].tick_us + 60_000_000


def test_a_first_word_more_than_one_wrap_after_the_start_is_timed_correctly():
    """A box started hours before its first session: the word path's first record is
    its first reading, so it had no earlier tick to notice a wrap against."""
    backend, sink, recorder = _word_recorder()
    first = 2 * TICK_WRAP_US + 123_456_789
    _tick_barcodes(backend, recorder, 0, first - 1_000_000)
    backend.set_now_us(first)
    backend.inject_word(first & _MASK, 0x0001)
    assert [w.tick_us for w in _words(sink)] == [first]


def test_a_first_edge_more_than_one_wrap_after_the_start_is_timed_correctly():
    backend, sink, recorder = _word_recorder()
    first = TICK_WRAP_US + 42_000_000
    _tick_barcodes(backend, recorder, 0, first - 1_000_000)
    backend.set_now_us(first)
    backend.inject_edge(REWARD_PIN, 1, first & _MASK)
    assert [e.tick_us for e in sink.written if isinstance(e, Edge)] == [first]


def test_a_tick_slightly_after_now_maps_forward_not_one_wrap_back():
    """W ticks come from the RP1 crystal and now_us() from NTP-slewed CLOCK_MONOTONIC,
    so a word can be stamped up to a second AFTER the clock's reading. 'The latest value
    at or before now' would send it a whole wrap into the past; nearest does not."""
    backend, sink, recorder = _word_recorder()
    now = TICK_WRAP_US + 10_000_000
    _tick_barcodes(backend, recorder, 0, now)
    backend.set_now_us(now)
    backend.inject_word((now + 900_000) & _MASK, 0x0001)
    # and the same just across the wrap boundary
    just_before_wrap = 2 * TICK_WRAP_US - 300_000
    _tick_barcodes(backend, recorder, now, just_before_wrap)
    backend.set_now_us(just_before_wrap)
    backend.inject_word((just_before_wrap + 900_000) & _MASK, 0x0002)
    assert [w.tick_us for w in _words(sink)] == [now + 900_000, just_before_wrap + 900_000]


def test_interleaved_delivery_near_a_wrap_still_maps_each_record_to_its_true_time():
    """The reason the paths had separate unwrappers: an edge stamped just BEFORE a wrap
    can be delivered after a word stamped just AFTER it. A shared 'falls below the
    previous tick' counter reads that as a wrap and jumps everything 4295 s forward;
    nearest-to-now has no such state to confuse."""
    backend, sink, recorder = _word_recorder()
    wrap = TICK_WRAP_US
    _tick_barcodes(backend, recorder, 0, wrap - 1_000_000)
    backend.set_now_us(wrap + 500)
    backend.inject_word((wrap + 300) & _MASK, 0x0001)  # after the wrap, delivered first
    backend.inject_edge(REWARD_PIN, 1, (wrap - 200) & _MASK)  # before it, delivered second
    backend.inject_word((wrap + 400) & _MASK, 0x0002)
    assert [r.tick_us for r in sink.written[-3:]] == [wrap + 300, wrap - 200, wrap + 400]


# How far a capture path has delivered, on the box clock (Recorder.delivered_through). A
# mark is a now_us() READING, so it is unwrapped at or before now, never to the nearest
# value as ticks are. The two agree for a mark under 35.8 min old; each test below also
# holds the mark past that, where nearest carries it a wrap FORWARD, past the boundary,
# and a roll would stop waiting for a path that has delivered nothing since.

_STALE_US = 40 * 60 * 1_000_000  # 40 min: a path that stalled one roll ago


class _MarkedBackend(FakeBackend):
    """An edge path whose mark the test sets."""

    def __init__(self) -> None:
        super().__init__()
        self.mark = None

    def delivered_through(self):
        return {"edge capture": self.mark}


def _marked():
    backend = _MarkedBackend()
    recorder = Recorder(backend, sink=_CollectingSink())
    recorder.capture_edges([REWARD_PIN])
    return backend, recorder


def test_a_mark_read_just_before_a_wrap_is_exact_and_behind_a_boundary_after_it():
    backend, recorder = _marked()
    backend.set_now_us(TICK_WRAP_US - 100)
    recorder.now()
    backend.mark = TICK_WRAP_US - 101

    backend.set_now_us(TICK_WRAP_US + 50)  # the clock wraps, and the roll comes
    boundary = recorder.now()
    assert boundary == TICK_WRAP_US + 50
    assert recorder.delivered_through() == {"edge capture": TICK_WRAP_US - 101}

    backend.set_now_us(TICK_WRAP_US + 50 + _STALE_US)  # stalled, and a roll 40 min on
    boundary = recorder.now()
    [mark] = recorder.delivered_through().values()
    assert mark == TICK_WRAP_US - 101 < boundary


def test_a_mark_below_the_origin_is_negative_and_exact():
    """The edge pump's short-read mark, T - 20 ms - 1, taken 5 ms after capture began."""
    backend, recorder = _marked()
    backend.set_now_us(5_000)
    recorder.now()
    backend.mark = (5_000 - 20_001) % TICK_WRAP_US
    assert recorder.delivered_through() == {"edge capture": -15_001}

    backend.set_now_us(5_000 + _STALE_US)
    boundary = recorder.now()
    [mark] = recorder.delivered_through().values()
    assert mark == -15_001 < boundary


def test_a_mark_forty_minutes_stale_stays_behind_the_boundary():
    """Nearest would put this mark 1895 s AHEAD of the boundary."""
    backend, recorder = _marked()
    backend.set_now_us(1_000_000_000)
    recorder.now()
    backend.mark = 999_999_999

    backend.set_now_us(1_000_000_000 + _STALE_US)
    boundary = recorder.now()
    [mark] = recorder.delivered_through().values()
    assert mark == 999_999_999 < boundary


# --- L records: where the Recorder places a loss a capture path reports (2026-10-30) ----
#
# A backend reports a loss with raw ticks, as it hands over every tick (CaptureLoss). The
# Recorder places it on the box clock, in the stream of the path that reported it.


def _losses(sink):
    return [r for r in sink.written if isinstance(r, Loss)]


def test_a_loss_found_at_an_edge_sits_at_that_edges_tick_just_before_it():
    """An overflow is seen at the event that shows it, X, and is written just before X at
    X's tick, so it keeps X's place in the edge stream: nothing of the path is out of
    order around it. The window opens at the line's event before the loss, given as a
    distance back from X."""
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(5_000)
    backend.inject_edge(REWARD_PIN, 1, 1_000)
    backend.inject_edge_loss(
        CaptureLoss(
            cause="buffer_overflow", count=2, bound="exact", gpio=REWARD_PIN,
            after=4_000, span_us=3_000, ahead=True,
        )
    )
    backend.inject_edge(REWARD_PIN, 0, 4_000)

    assert sink.written == [
        Edge(tick_us=1_000, gpio=REWARD_PIN, level=1),
        Loss(
            tick_us=4_000, path="E", gpio=REWARD_PIN, count=2, bound="exact",
            cause="buffer_overflow", before_us=1_000, after_us=4_000,
        ),
        Edge(tick_us=4_000, gpio=REWARD_PIN, level=0),
    ]


def test_a_window_that_opened_hours_before_its_loss_is_on_the_right_wrap():
    """WHY THE START OF A WINDOW IS A DISTANCE, NOT A TICK. A lick line quiet for two hours
    overflows when it fires again: the last event before the loss is two hours old. As a
    raw 32-bit tick it is more than half a wrap from now, and placed nearest to now it
    lands a wrap off -- 71.6 minutes from the truth. The event that shows the loss is
    recent, so it is the one handed over raw, and the window's start is counted back from
    it exactly."""
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(1_000_000)
    backend.inject_edge(REWARD_PIN, 1, 1_000_000)
    later = 1_000_000 + 2 * 3_600_000_000
    _tick_barcodes(backend, recorder, 1_000_000, later)
    backend.set_now_us(later)
    backend.inject_edge_loss(
        CaptureLoss(
            cause="buffer_overflow", count=1, bound="exact", gpio=REWARD_PIN,
            after=later & _MASK, span_us=later - 1_000_000, ahead=True,
        )
    )
    [loss] = _losses(sink)
    assert (loss.before_us, loss.after_us, loss.tick_us) == (1_000_000, later, later)


def test_a_loss_found_after_later_edges_sits_at_the_highest_edge_tick():
    """A gap in the request's numbers is final only once its window closes, by which time
    later edges have been written. Its record sits after them, so its tick is the highest
    the edge path has written: at its window it would sit out of order. The window says
    when the loss was."""
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(10_000)
    for tick in (1_000, 3_000, 6_000, 5_500):
        backend.inject_edge(REWARD_PIN, 1, tick)
    backend.inject_edge_loss(
        CaptureLoss(
            cause="unaccounted", count=1, bound="at_least", after=3_000, span_us=2_000,
        )
    )
    [loss] = _losses(sink)
    assert loss == Loss(
        tick_us=6_000, path="E", gpio=None, count=1, bound="at_least",
        cause="unaccounted", before_us=1_000, after_us=3_000,
    )


def test_a_word_loss_with_nothing_after_it_follows_the_last_word():
    """A desync stops the word pump, so no word follows the loss and its window has no
    end. It lies after everything the path delivered: the last word opens the window, and
    the record sits at that word's tick."""
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(10_000)
    backend.inject_word(1_000, 0x8001)
    backend.inject_word(2_000, 0x0001)
    backend.set_now_us(20_000)
    backend.inject_word_loss(CaptureLoss(cause="desync", count=2, bound="at_least"))
    [loss] = _losses(sink)
    assert loss == Loss(
        tick_us=2_000, path="W", gpio=None, count=2, bound="at_least",
        cause="desync", before_us=2_000, after_us=None,
    )


def test_a_word_loss_before_any_word_sits_at_the_box_clock():
    """Nothing of the path to follow: the record goes where the box clock is, and its
    window is empty at both ends."""
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(7_000)
    backend.inject_word_loss(CaptureLoss(cause="desync", count=1, bound="at_least"))
    [loss] = _losses(sink)
    assert (loss.tick_us, loss.before_us, loss.after_us) == (7_000, None, None)


def test_no_loss_reported_means_no_loss_record():
    backend, sink, recorder = _word_recorder()
    backend.set_now_us(10_000)
    backend.inject_word(1_000, 0x8001)
    backend.inject_edge(REWARD_PIN, 1, 2_000)
    recorder.record_barcode(3_000, 500)
    assert _losses(sink) == []
