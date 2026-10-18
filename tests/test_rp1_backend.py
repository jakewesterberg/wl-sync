"""Rp1Backend's strobed-capture thread, against a stand-in for libpiolib.

The backend loads piolib in its constructor rather than at import, so a fake library
can take its place on a laptop. The fake accepts every configuration call and turns
the RX FIFO into a list the test fills. What that exercises is the backend's own
glue: that something drains the FIFO, that what goes wrong reaches check_health(),
and the order things stop in. What it cannot exercise is silicon -- the PIO program,
real FIFO timing, the chardev -- which is bench day's.
"""

import threading
import time

import pytest

from wl_sync.rp1 import backend as rp1
from wl_sync.rp1.backend import Rp1Backend, _PioSmConfig

DATA = 16


def stream(*events):
    entries = []
    for tick, word in events:
        entries.append(word)
        entries.append((~tick) & 0xFFFFFFFF)
    return entries


class _FakePiolib:
    """Every call succeeds; the RX FIFO is `fifo`, and `log` is the order of events."""

    def __init__(self) -> None:
        self.fifo: list[int] = []
        self.full = False
        self.drains = 0
        self.log: list[str] = []
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return lambda *args: 0

    def pio_open(self, index):
        return 1

    def pio_get_default_sm_config(self):
        return _PioSmConfig()

    def clock_get_hz(self, clock):
        return 200_000_000

    def pio_sm_set_enabled(self, pio, sm, enabled):
        self.log.append(f"enabled={enabled}")

    def push(self, entries) -> None:
        with self._lock:
            self.fifo.extend(entries)

    def pio_sm_is_rx_fifo_full(self, pio, sm):
        self.drains += 1  # the first call of every drain
        return self.full

    def pio_sm_get_rx_fifo_level(self, pio, sm):
        with self._lock:
            return len(self.fifo)

    def pio_sm_get(self, pio, sm):
        with self._lock:
            return self.fifo.pop(0)


@pytest.fixture
def lib(monkeypatch, gpiod):
    fake = _FakePiolib()
    monkeypatch.setattr(rp1, "_load_piolib", lambda: fake)
    monkeypatch.setattr(rp1, "_assert_encoders_agree", lambda _lib: None)
    return fake


def _until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.001)
    return True


def _next_complaint(backend, timeout=1.0):
    """What check_health() next raises, or None if it stays healthy until the timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            backend.check_health()
        except RuntimeError as error:
            return str(error)
        time.sleep(0.001)
    return None


def _started(lib, on_word):
    backend = Rp1Backend()
    backend.start_strobed_capture(0, DATA, DATA, on_word)
    return backend


def test_strobed_words_are_delivered_without_anyone_calling_drain(lib):
    """drain() existed and nothing called it, so on the box the FIFO -- 8 words, four
    events -- filled on the first burst of codes and `push noblock` discarded every
    word after, silently. Capture has to drain itself."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append((tick, word)))
    try:
        lib.push(stream((10, 1), (20, 2)))
        assert _until(lambda: len(seen) == 2), f"delivered {seen}"
        assert seen == [(10, 1), (20, 2)]
    finally:
        backend.close()


def test_a_full_fifo_reaches_check_health_and_capture_continues(lib):
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        lib.full = True
        lib.push(stream((10, 1)))
        assert _until(lambda: seen == [1])
        lib.full = False
        # Two more drains, so the one that may already have read "full" has finished.
        settled = lib.drains + 2
        assert _until(lambda: lib.drains >= settled)
        with pytest.raises(RuntimeError, match="event-code capture: .*FIFO was full"):
            backend.check_health()

        lib.push(stream((20, 2)))
        assert _until(lambda: seen == [1, 2]), "capture stopped after a full FIFO"
        backend.check_health()  # reported once, and nothing new since
    finally:
        backend.close()


def test_a_desynced_stream_is_reported_and_then_reported_as_stopped(lib):
    """The pump stops on a desync. check_health() says why once, and from then on says
    that the thread is not running, so the journal shows both what happened and that
    the event codes are not coming back on their own."""
    backend = _started(lib, lambda tick, word: None)
    try:
        lib.push(stream((10, 1))[1:] + [0])  # one word lost: the pairing has slipped
        first = _next_complaint(backend)
        assert first is not None and "pairing has slipped" in first, first
        second = _next_complaint(backend)
        assert second is not None and "event-code capture thread stopped" in second, second
    finally:
        backend.close()


def test_close_delivers_what_the_fifo_held_before_the_state_machine_stops(lib):
    """The last words in the FIFO are the end of the session's last trial. close() stops
    the pump with one final drain and only then disables the state machine, so nothing
    the PIO captured is left behind in it."""
    def on_word(tick, word):
        lib.log.append(f"word={word}")

    backend = _started(lib, on_word)
    lib.push(stream((10, 7)))
    backend.close()

    assert "word=7" in lib.log
    assert lib.log.index("word=7") < lib.log.index("enabled=False")


class _FakeTime:
    """A monotonic clock that advances 20 us per read and by exactly what is slept."""

    CLOCK_MONOTONIC = 1

    def __init__(self) -> None:
        self.ns = 10**12

    def clock_gettime_ns(self, clock):
        self.ns += 20_000
        return self.ns

    def sleep(self, seconds):
        self.ns += int(seconds * 1e9)


@pytest.fixture
def gpiod(monkeypatch):
    """A stand-in for the libgpiod v2 bindings that records every level set."""
    import sys
    import types

    line = types.ModuleType("gpiod.line")
    line.Direction = types.SimpleNamespace(INPUT="in", OUTPUT="out")
    line.Value = types.SimpleNamespace(ACTIVE=1, INACTIVE=0)
    line.Edge = types.SimpleNamespace(BOTH="both")
    line.Clock = types.SimpleNamespace(MONOTONIC="mono")

    module = types.ModuleType("gpiod")
    module.line = line
    module.sets = []
    module.chips = {"/dev/gpiochip0": ("pinctrl-rp1", 54)}
    module.LineSettings = lambda **kwargs: kwargs

    class _Request:
        def set_value(self, pin, value):
            module.sets.append((module.clock.ns, value))

        def wait_edge_events(self, timeout):
            if module.edge_failure is not None:
                raise module.edge_failure
            time.sleep(0.001)
            return False

        def read_edge_events(self):
            return []

        def release(self):
            pass

    def request_lines(path, config, consumer, **kwargs):
        if path not in module.chips:
            raise FileNotFoundError(path)
        return _Request()

    class Chip:
        def __init__(self, path):
            if path not in module.chips:
                raise FileNotFoundError(path)
            self._label, self._lines = module.chips[path]

        def get_info(self):
            return types.SimpleNamespace(label=self._label, num_lines=self._lines)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    module.request_lines = request_lines
    module.Chip = Chip
    module.clock = _FakeTime()
    module.edge_failure = None
    monkeypatch.setitem(sys.modules, "gpiod", module)
    monkeypatch.setitem(sys.modules, "gpiod.line", line)
    return module


def test_every_barcode_frame_decodes_not_just_the_first(lib, gpiod):
    """A frame ends on TRAIL HIGH, and emit_pulses set each level at its pulse's START
    and returned, so the pin stayed HIGH between frames. The decoder finds a frame by
    a rising edge after 400 ms of LOW, which the stuck-high pin never produced again:
    on the box, only the first barcode of a run would have decoded. The codec's own
    tests could not see it, because their pulse model ends every frame LOW."""
    from wl_sync.barcode import decode_edges, encode

    import wl_sync.rp1.backend as rp1_module

    rp1_module.time = gpiod.clock
    try:
        backend = Rp1Backend()
        first = gpiod.clock.ns
        for value in (1000, 1001, 1002):
            backend.emit_pulses(17, encode(value))
            gpiod.clock.sleep(0.8)  # the run loop's idle between frames
    finally:
        rp1_module.time = time

    edges = []
    for ns, level in gpiod.sets:
        if not edges or edges[-1][1] != level:
            edges.append((ns // 1000, level))
    values = [b.value for b in decode_edges(edges, start_us=first // 1000 - 1_000_000)]
    # The last frame is not asserted: a decoder only accepts a frame once the record
    # runs past its end, and this record stops at the third frame's final edge.
    assert values[:2] == [1000, 1001]



def test_a_dead_edge_path_does_not_hide_a_failing_event_code_path(lib, gpiod):
    """check_health() raised at the FIRST problem. Once the edge thread had died, every
    call stopped at "edge capture thread stopped" and never looked at the event codes
    again, so a desync after it went unreported for the rest of the day."""
    backend = _started(lib, lambda tick, word: None)
    try:
        backend.start_edge_capture([20, 21], lambda gpio, level, tick: None)
        gpiod.edge_failure = OSError("EIO")
        assert _until(lambda: not backend._edge_thread.is_alive())
        lib.push(stream((10, 1))[1:] + [0])  # and then the pairing slips
        assert _until(lambda: not backend._word_thread.is_alive())

        with pytest.raises(RuntimeError) as raised:
            backend.check_health()
        assert "edge capture" in str(raised.value)
        assert "event-code capture" in str(raised.value)
    finally:
        backend.close()



def test_a_chip_that_does_not_exist_is_refused_before_anything_is_recorded(lib, gpiod, tmp_path):
    """The chip was first opened inside run(), after the segment file existed, so a wrong
    --gpiochip crash-looped and left a header-only segment behind on every restart --
    up to 86,400 a day under RestartSec=1, each one re-read by every manifest rebuild.
    It is checked when the backend is built, before run() creates anything."""
    from wl_sync.cli import main

    with pytest.raises(RuntimeError, match="/dev/gpiochip9"):
        main(["record", "--out", str(tmp_path), "--duration", "1", "--gpiochip", "/dev/gpiochip9"])
    assert list(tmp_path.rglob("seg-*.log")) == []


def test_a_chip_too_small_for_the_capture_pins_is_refused(lib, gpiod):
    gpiod.chips["/dev/gpiochip4"] = ("gpio-brcmstb@107d517c00", 17)
    with pytest.raises(RuntimeError, match="17 lines"):
        Rp1Backend(chip_path="/dev/gpiochip4")


def test_a_fifo_found_holding_eight_words_counts_as_full(lib):
    """Fullness was sampled once, before reading, so a FIFO that filled between that
    sample and the read went unwitnessed. Eight words waiting IS a full joined FIFO,
    and any push into it was dropped, so the level is a witness as well."""
    seen = []
    backend = _started(lib, lambda tick, word: seen.append(word))
    try:
        lib.push(stream((10, 1), (20, 2), (30, 3), (40, 4)))
        assert _until(lambda: len(seen) == 4)
        complaint = _next_complaint(backend)
        assert complaint is not None and "FIFO was full" in complaint, complaint
    finally:
        backend.close()


def test_the_hardware_backend_is_built_on_the_chip_it_is_given(monkeypatch):
    """Closes a coverage gap rather than discriminating against the shipped code: a
    mutant passing the chip positionally survived the suite. Rp1Backend's first
    positional parameter is the PIO index, so that mutant opens the default chip
    whatever --gpiochip says."""
    from wl_sync import cli

    built = []
    monkeypatch.setattr(rp1, "Rp1Backend", lambda *args, **kwargs: built.append((args, kwargs)))
    cli._hardware_backend("/dev/gpiochip4")
    assert built == [((), {"chip_path": "/dev/gpiochip4"})]
