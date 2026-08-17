from wl_sync.backend import FakeBackend
from wl_sync.barcode import IDLE_MIN_US, decode_edges
from wl_sync.log import CodeWord, Edge, TICK_WRAP_US
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
    BarcodeGenerator(backend, pin=BARCODE_PIN, start_value=1000).emit_frame()
    assert len(backend.emitted) == 1
    pin, pulses = backend.emitted[0]
    assert pin == BARCODE_PIN
    edges = pulses_to_edges(pulses, IDLE_MIN_US)
    assert [b.value for b in decode_edges(edges, start_us=0)] == [1000]


def test_generator_increments_monotonically():
    backend = FakeBackend()
    generator = BarcodeGenerator(backend, pin=BARCODE_PIN, start_value=7)
    assert [generator.emit_frame() for _ in range(3)] == [7, 8, 9]
    assert generator.next_value == 10


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
    """Unwrapping is per path. Sharing one counter across both would read an
    edge at 5000 followed by a word at 1000 as a 32-bit wraparound."""
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, 5_000)
    backend.inject_word(1_000, 0x8001)
    assert max(r.tick_us for r in recorder.records()) < TICK_WRAP_US


def test_recorder_unwraps_each_path_independently():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    backend.inject_word(TICK_WRAP_US - 500, 0x0001)
    backend.inject_word(500, 0x0002)
    assert [r.tick_us for r in recorder.records()] == [
        TICK_WRAP_US - 500,
        TICK_WRAP_US + 500,
    ]


def test_edge_path_wraps_without_disturbing_the_word_path():
    backend = FakeBackend()
    recorder = Recorder(backend)
    recorder.capture_codes(DATA_BASE, DATA_COUNT, STROBE_PIN)
    recorder.capture_edges([REWARD_PIN])
    backend.inject_edge(REWARD_PIN, 1, TICK_WRAP_US - 200)
    backend.inject_edge(REWARD_PIN, 0, 200)
    backend.inject_word(1_000, 0x0001)
    ticks = {type(r).__name__: r.tick_us for r in recorder.records()}
    assert ticks["CodeWord"] == 1_000


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
    all: within ONE path the unwrapper's output is monotonically non-decreasing, so
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
