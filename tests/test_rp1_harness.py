"""The B3a and B3b bench harness (wl_sync.rp1.harness), against stand-ins for the shim,
libgpiod and RP1's GPIO registers.

What the stand-ins exercise is the harness's own glue: which registers the writer touches
and that it puts them back, which shim calls are made and in what order, that one thread
makes them all, and that each stamp is taken for the edge that made it. What they cannot
exercise is silicon: whether the PIO sees GPIO18 unclaimed, what the sync enable does to
the dividers, how wide a high the writer makes. Those are the box run's (the design,
.superpowers/sdd/2026-11-07-one-clock/b3-harness-design.md, section 6).
"""

import pathlib
import threading
import tomllib
import types

import pytest

from wl_sync.rp1 import harness

_RP1 = tomllib.loads(
    (pathlib.Path(__file__).parent.parent / "hardware" / "datasheet-params.toml").read_text()
)["rp1_peripherals"]


class _Clock:
    """One thread's clocks. RAW advances `read_ns` a read and by each sleep; the PIO
    count `p_now()` is true time since the sync enable times (1 + rp1_ppm 1e-6), in us,
    and RAW is true time times (1 + cpu_ppm 1e-6). A read samples at its middle."""

    CLOCK_MONOTONIC = 1
    CLOCK_MONOTONIC_RAW = 4

    def __init__(self, cpu_ppm=0.0, rp1_ppm=0.0, read_ns=100, start_ns=10**12):
        self.true_ns = start_ns
        self._cpu = 1 + cpu_ppm * 1e-6
        self._rp1 = 1 + rp1_ppm * 1e-6
        self._read_ns = read_ns
        self.p_zero_ns = None
        self.failure = None  # raised by the next read, once

    def clock_gettime_ns(self, clock):
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        self.true_ns += self._read_ns // 2
        raw = round(self.true_ns * self._cpu)
        self.true_ns += self._read_ns - self._read_ns // 2
        return raw

    def sleep(self, seconds):
        self.true_ns += round(seconds * 1e9)

    def advance(self, ns):
        self.true_ns += ns

    def start_p(self):
        self.p_zero_ns = self.true_ns

    def p_now(self, extra_us=0):
        return int((self.true_ns - self.p_zero_ns) * self._rp1 / 1_000 + extra_us)


# ---------------------------------------------------------------------------
# The register writer, against a model of IO_BANK0
# ---------------------------------------------------------------------------

_STATUS_OUTTOPAD = 1 << _RP1["status_outtopad_bit"]
_STATUS_OETOPAD = 1 << _RP1["status_oetopad_bit"]
_STATUS_INFROMPAD = 1 << _RP1["status_infrompad_bit"]
_STATUS_OUTFROMPERI = 1 << _RP1["status_outfromperi_bit"]
_STATUS_OEFROMPERI = 1 << _RP1["status_oefromperi_bit"]
_F_M_RESET = 0x04 << 5  # Table 8: F_M, bits 11:5, resets to 0x04
_NULL = 0x1F


class _Bank:
    """IO_BANK0 as /dev/gpiomem0 maps it from offset 0: GPIOn_STATUS at 8n, GPIOn_CTRL at
    8n + 4, each through four aliases (normal, XOR, SET, CLEAR; RP1 Peripherals, section
    2.4, printed p.11). CTRL is read-write and STATUS read-only (Tables 7 and 8, printed
    p.20-22). The pad's output is OUTOVER applied to RIO's level, which libgpiod sets
    through `drive`, and its input is that output while the output is enabled.

    `writes` and `reads` record every access, as (offset, value) and offset. `on_pad` is
    called with (pin, level) at each change of a pad's level, which is where the board
    stamps a rise. `frozen` makes STATUS read 0 whatever the pads do, as a map that is
    not IO_BANK0 would; `ignore_clear` makes CLEAR writes do nothing.

    WRITES ARE POSTED (box step 2, 2026-11-20). A write lands only when the next read of
    the bank is made, as a posted PCIe write is forced out by a read behind it; until then
    the pad does not move. Two writes with no read between them land together, at that
    read, and the high between them lasts nothing: what the probe saw on the box, where
    the writer's highs were stamped 27 times in 600. `posted = False` lands each write as
    it is made."""

    def __init__(self):
        self.ctrl = {pin: _F_M_RESET | _NULL for pin in range(28)}
        self.rio_out = dict.fromkeys(range(28), 0)
        self.rio_oe = dict.fromkeys(range(28), 0)
        self.writes = []
        self.reads = []
        self.on_pad = None
        self.frozen = False
        self.ignore_clear = False
        self.closed = False
        self.posted = True
        self.pending = []

    def hold_as_output(self, pin, level=0):
        """What libgpiod's output request leaves [inferred: that the kernel's driver
        selects SYS_RIO for it]."""
        self.ctrl[pin] = (self.ctrl[pin] & ~0x1F) | _RP1["funcsel_sys_rio"]
        self.rio_oe[pin] = 1
        self.drive(pin, level)

    def drive(self, pin, level):
        before = self.pad(pin)
        self.rio_out[pin] = level
        self._changed(pin, before)

    def _out(self, pin):
        over = (self.ctrl[pin] >> 12) & 3
        if self.ctrl[pin] & 0x1F != _RP1["funcsel_sys_rio"]:
            peripheral = 0
        else:
            peripheral = self.rio_out[pin]
        return (peripheral, 1 - peripheral, 0, 1)[over]

    def _oe(self, pin):
        over = (self.ctrl[pin] >> 14) & 3
        peripheral = self.rio_oe[pin] if self.ctrl[pin] & 0x1F == _RP1["funcsel_sys_rio"] else 0
        return (peripheral, 1 - peripheral, 0, 1)[over]

    def pad(self, pin):
        return self._out(pin) if self._oe(pin) else 0

    def _changed(self, pin, before):
        after = self.pad(pin)
        if after != before and self.on_pad is not None:
            self.on_pad(pin, after)

    def _decode(self, offset):
        assert offset % 4 == 0 and 0 <= offset < _RP1["aliases_span"], hex(offset)
        alias, register = offset & 0x3000, offset & 0xFFF
        pin, is_ctrl = divmod(register, 8)
        assert pin < 28, hex(offset)
        return alias, pin, is_ctrl == 4

    def read32(self, offset):
        self._flush()  # a read does not pass the posted writes ahead of it
        self.reads.append(offset)
        _, pin, is_ctrl = self._decode(offset)
        if is_ctrl:
            return self.ctrl[pin]
        if self.frozen:
            return 0
        rio = self.ctrl[pin] & 0x1F == _RP1["funcsel_sys_rio"]
        return ((self._out(pin) and _STATUS_OUTTOPAD) | (self._oe(pin) and _STATUS_OETOPAD)
                | (self.pad(pin) and _STATUS_INFROMPAD)
                | (rio and self.rio_out[pin] and _STATUS_OUTFROMPERI)
                | (rio and self.rio_oe[pin] and _STATUS_OEFROMPERI))

    def write32(self, offset, value):
        self.writes.append((offset, value))
        self._decode(offset)
        if self.posted:
            self.pending.append((offset, value))
        else:
            self._land(offset, value)

    def _flush(self):
        pending, self.pending = self.pending, []
        for offset, value in pending:
            self._land(offset, value)

    def _land(self, offset, value):
        alias, pin, is_ctrl = self._decode(offset)
        if not is_ctrl:
            return  # read-only
        before = self.pad(pin)
        if alias == 0:
            self.ctrl[pin] = value
        elif alias == 0x1000:
            self.ctrl[pin] ^= value
        elif alias == 0x2000:
            self.ctrl[pin] |= value
        elif not self.ignore_clear:
            self.ctrl[pin] &= ~value
        self._changed(pin, before)

    def close(self):
        self.closed = True  # unmapping lands nothing: a write still posted stays so


@pytest.fixture
def bank(monkeypatch):
    """A bank with GPIO18 held low as libgpiod's output, the clock on harness's `time`,
    and the writer's map opened onto the bank."""
    model = _Bank()
    model.hold_as_output(18)
    model.clock = _Clock()
    monkeypatch.setattr(harness, "time", model.clock)
    monkeypatch.setattr(harness, "_SPIN_NS", 0)
    model.opened = []

    def open_map(path):
        model.opened.append(path)
        return model

    model.open_map = open_map
    return model


def _writer(bank, pin=18):
    return harness.PulseWriter(pin, open_map=bank.open_map)


def test_the_writers_offsets_and_values_are_the_datasheets():
    """Every number the writer uses is RP1 Peripherals', as hardware/datasheet-params.toml
    records it with its page, so a reading of the document is checked against the code."""
    base, stride = _RP1["gpio_ctrl_offset_stride"]
    assert harness.ctrl_offset(18) == base + 18 * stride == 0x094
    assert harness.ctrl_offset(19) == 0x09C
    base, stride = _RP1["gpio_status_offset_stride"]
    assert harness.status_offset(18) == base + 18 * stride == 0x090
    assert harness.ALIAS_SET == _RP1["alias_set"] == 0x2000
    assert harness.ALIAS_CLEAR == _RP1["alias_clear"] == 0x3000
    assert harness.IO_BANK0_SPAN == _RP1["aliases_span"] == 0x4000
    # Not the datasheet's: the box's (wl-rig-sync0, 2026-11-20, box step 1).
    assert harness.GPIOMEM_MAP_LENGTH == _RP1["gpiomem0_map_length"] == 0x30000
    high, low = _RP1["ctrl_outover_bits"]
    assert harness.OUTOVER_MASK == ((1 << (high - low + 1)) - 1) << low == 0x3000
    assert harness.STATUS_OUTTOPAD == _STATUS_OUTTOPAD
    assert harness.STATUS_OETOPAD == _STATUS_OETOPAD
    assert harness.STATUS_INFROMPAD == _STATUS_INFROMPAD
    assert harness.STATUS_OUTFROMPERI == _STATUS_OUTFROMPERI == 1 << 8
    assert harness.STATUS_OEFROMPERI == _STATUS_OEFROMPERI == 1 << 12
    assert harness.FUNCSEL_SYS_RIO == _RP1["funcsel_sys_rio"]
    assert harness.GPIOMEM_PATH == "/dev/gpiomem0"


def test_a_pulse_writes_only_the_dense_pins_ctrl_and_only_its_override(bank):
    """Two writes a pulse: OUTOVER's two bits through CTRL's SET alias, drive high, then
    through its CLEAR alias, back to the level libgpiod holds. Nothing else is written."""
    pads = []
    bank.on_pad = lambda pin, level: pads.append((pin, level))
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        pads.clear()
        writer.pulse(0)
    assert bank.opened == ["/dev/gpiomem0"]
    assert bank.writes[:2] == [(0x2094, 0x3000), (0x3094, 0x3000)]
    assert {offset for offset, _ in bank.writes} <= {0x2094, 0x3094}
    assert all(value == 0x3000 for _, value in bank.writes)
    assert pads == [(18, 1), (18, 0)]
    assert bank.closed


def test_on_gpio19_the_writer_touches_gpio19s_ctrl_alone(bank):
    bank.hold_as_output(19)
    with _writer(bank, 19) as writer:
        writer.verify(lambda high: bank.drive(19, int(high)))
        writer.pulse(0)
    assert {offset for offset, _ in bank.writes} == {0x209C, 0x309C}


@pytest.mark.parametrize("pin", [16, 17, 20, 0, 27, 28, -1])
def test_any_pin_but_a_spare_one_is_refused_before_anything_is_mapped(bank, pin):
    with pytest.raises(ValueError, match="spare"):
        harness.PulseWriter(pin, open_map=bank.open_map)
    assert bank.opened == []


@pytest.mark.parametrize("claim, said", [
    ("pio", "PIO"),
    ("null", "function 31"),
    ("input", "not driven as an output"),
    ("high", "is high"),
    ("outover", "override"),
    ("oeover", "override"),
])
def test_a_pin_held_by_anything_but_this_harness_libgpiod_output_is_refused(
    bank, claim, said
):
    """Refused, with nothing written and the map closed: the PIO's pin (FUNCSEL a7), a pin
    no function holds, one not driven as an output, one already high, and one whose
    output or output-enable someone else overrides."""
    if claim == "pio":
        bank.ctrl[18] = (bank.ctrl[18] & ~0x1F) | _RP1["funcsel_pio"]
    elif claim == "null":
        bank.ctrl[18] |= 0x1F
    elif claim == "input":
        bank.rio_oe[18] = 0
    elif claim == "high":
        bank.drive(18, 1)
    elif claim == "outover":
        bank.ctrl[18] |= 3 << 12
    else:
        bank.ctrl[18] |= 3 << 14
    writer = _writer(bank)
    with pytest.raises(harness.WriterRefused, match=said):
        writer.open()
    assert bank.writes == []
    assert bank.closed
    writer.close()  # nothing to restore
    assert bank.writes == []


def test_the_map_is_checked_against_libgpiod_before_the_first_write(bank):
    """verify() only reads: STATUS must follow the level libgpiod sets, high and low, and
    the pin ends low."""
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        assert bank.writes == []
        assert bank.pad(18) == 0
        assert set(bank.reads) == {0x090, 0x094}


def test_a_map_whose_status_does_not_follow_the_pin_is_refused_with_nothing_written(bank):
    writer = _writer(bank)
    writer.open()
    bank.frozen = True
    with pytest.raises(harness.WriterRefused, match="does not follow"):
        writer.verify(lambda high: bank.drive(18, int(high)))
    writer.close()
    assert bank.writes == []  # not even a CLEAR, through a map found not to be IO_BANK0
    assert bank.closed


def test_a_pulse_is_refused_before_verify(bank):
    with _writer(bank) as writer, pytest.raises(harness.WriterRefused, match="verify"):
        writer.pulse(0)
    assert bank.writes[:1] != [(0x2094, 0x3000)]


def test_each_pulse_is_bounded_by_raw_before_its_rise_and_after_its_fall(bank):
    """The RAW read before the SET store and the one after the CLEAR's read-back bound the
    high, given that a read cannot complete before the posted writes ahead of it (an
    engineering judgment from PCIe's ordering rules, `PulseWriter.pulse`)."""
    pads = []
    bank.on_pad = lambda pin, level: pads.append(bank.clock.true_ns)
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        pads.clear()
        bounds = writer.pulse(3_000)
        before, after = bounds.before, bounds.after
        # The SET's read-back, the CLEAR's, then the first pulse's CTRL read-back (M3).
        assert bank.reads[-3:] == [0x090, 0x090, 0x094]
    rise, fall = pads
    assert before <= rise and fall <= after
    assert fall - rise >= 3_000


@pytest.mark.parametrize("failure", [RuntimeError("piolib"), KeyboardInterrupt()])
def test_a_pulse_cut_short_still_clears_the_override(bank, failure):
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        prior = bank.ctrl[18]
        bank.clock.failure = None
        original = harness.time.clock_gettime_ns
        calls = []

        def failing(clock):
            calls.append(clock)
            if len(calls) == 2:  # inside the hold, after the SET write
                raise failure
            return original(clock)

        bank.clock.clock_gettime_ns = failing
        with pytest.raises(type(failure)):
            writer.pulse(3_000)
        bank.clock.clock_gettime_ns = original
        assert bank.writes[-1] == (0x3094, 0x3000)
        assert bank.ctrl[18] == prior and bank.pad(18) == 0


def test_a_writer_that_never_pulsed_writes_nothing_at_close(bank):
    """Opened and verified, but never armed: close() unmaps, and writes nothing."""
    writer = _writer(bank)
    writer.open()
    writer.verify(lambda high: bank.drive(18, int(high)))
    writer.close()
    assert bank.writes == [] and bank.closed


def test_close_after_a_pulse_clears_the_override_and_checks_ctrl_is_as_it_was(bank):
    prior = bank.ctrl[18]
    writer = _writer(bank)
    writer.open()
    writer.verify(lambda high: bank.drive(18, int(high)))
    writer.pulse(0)
    bank.writes.clear()
    writer.close()
    assert bank.writes == [(0x3094, 0x3000)]
    assert bank.ctrl[18] == prior and bank.closed
    writer.close()  # once only
    assert bank.writes == [(0x3094, 0x3000)]


def test_close_restores_an_override_left_high_after_the_writer_armed(bank):
    """An override found set at close, once the writer has written (as a signal landing
    between the two stores would leave it), is cleared by close()."""
    writer = _writer(bank)
    writer.open()
    writer.verify(lambda high: bank.drive(18, int(high)))
    writer.pulse(0)
    bank.write32(0x2094, 0x3000)
    bank.writes.clear()
    writer.close()
    assert bank.writes == [(0x3094, 0x3000)]
    assert bank.pad(18) == 0


def test_a_restore_that_did_not_take_is_raised_not_hidden(bank):
    writer = _writer(bank)
    writer.open()
    writer.verify(lambda high: bank.drive(18, int(high)))
    writer.pulse(0)
    bank.write32(0x2094, 0x3000)
    bank.ignore_clear = True
    with pytest.raises(harness.WriterError, match="not restored"):
        writer.close()
    assert bank.closed


def test_a_stall_before_the_set_store_does_not_shorten_the_high(bank):
    """The hold is timed from a RAW read after the SET store, so a preemption between the
    first RAW read and the store lengthens the pulse's bracket, never shortens its high
    below the hold (review I2). Here the store itself is 4 us late."""
    pads = []
    bank.on_pad = lambda pin, level: pads.append(bank.clock.true_ns)
    real = bank.write32

    def late_set(offset, value):
        if offset == 0x2094:
            bank.clock.advance(4_000)
        real(offset, value)

    bank.write32 = late_set
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        pads.clear()
        before, set_after, clear_before, after, _, _ = writer.pulse(2_000)
    rise, fall = pads
    assert fall - rise >= 2_000
    # The fake lands a store as it is issued; on RP1 it lands about 1 us later (N6).
    assert before <= rise <= set_after and clear_before <= fall <= after
    assert clear_before - set_after >= 2_000


def test_a_ctrl_word_changed_by_the_first_pulse_is_raised_at_once(bank):
    """After the first pulse CTRL is read back against the word open() read: a store that
    was split or widened, so touched other bits, stops the writer then, not at close
    (review M3)."""
    real = bank.write32

    def widened(offset, value):
        real(offset, value | (0x1 << 5) if offset == 0x2094 else value)

    bank.write32 = widened
    writer = _writer(bank)
    writer.open()
    writer.verify(lambda high: bank.drive(18, int(high)))
    with pytest.raises(harness.WriterError, match="first pulse"):
        writer.pulse(0)
    with pytest.raises(harness.WriterError, match="not restored"):
        writer.close()
    assert bank.closed


def test_a_fall_that_did_not_land_is_raised(bank):
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        writer.pulse(0)  # the first pulse, whose CTRL read-back would raise first
        bank.ignore_clear = True
        with pytest.raises(harness.WriterError, match="still high"):
            writer.pulse(0)
        bank.ignore_clear = False


def test_a_rise_by_libgpiod_is_read_back_through_the_map(bank):
    """After libgpiod's rise, one STATUS read: it returns once the rise's write has
    landed, so the RAW read after it starts the high's lower bound [inferred: that the
    kernel's write is ordered before this process's read of the same block]."""
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        bank.drive(18, 1)
        landed = writer.rise_landed()
        assert landed == bank.clock.clock_gettime_ns(4) - 100
        bank.drive(18, 0)
        with pytest.raises(harness.WriterError, match="did not rise"):
            writer.rise_landed()


@pytest.fixture
def window_only(monkeypatch):
    """mmap as rpi-gpiomem answers it on the box: EINVAL for any length but the whole
    window, 0x30000 (wl-rig-sync0, 2026-11-20: 0x1000 and 0x4000 refused, 0x30000 taken)."""
    import errno

    real = harness.mmap.mmap
    lengths = []

    def mmap(fd, length, *args, **kwargs):
        lengths.append(length)
        if length != 0x30000:
            raise OSError(errno.EINVAL, "Invalid argument")
        return real(fd, length, *args, **kwargs)

    monkeypatch.setattr(harness.mmap, "mmap", mmap)
    return lengths


def test_the_gpiomem_map_takes_the_whole_window_as_the_box_requires(tmp_path, window_only):
    """Box step 1 (2026-11-20): rpi-gpiomem refuses a map shorter than its 0x30000 window,
    so a 0x4000 map failed with EINVAL and `override` could not read."""
    path = tmp_path / "gpiomem0"
    path.write_bytes(bytes(0x30000))
    regs = harness.Gpiomem(str(path))
    assert window_only == [0x30000]
    regs.close()


def test_the_gpiomem_map_reads_and_writes_32_bit_words_at_exact_offsets(
    tmp_path, window_only
):
    """The map itself, on a file the size of the window: each access is one little-endian
    32-bit word at its offset, and anything else is refused, IO_BANK0's 16 kB of aliases
    the only part of the window reachable through it."""
    path = tmp_path / "gpiomem0"
    path.write_bytes(bytes(0x30000))
    regs = harness.Gpiomem(str(path))
    regs.write32(0x2094, 0x3000)
    regs.write32(0x0094, 0xDEADBEEF)
    assert regs.read32(0x2094) == 0x3000
    assert regs.read32(0x0094) == 0xDEADBEEF
    for bad in (0x2095, -4, 0x4000, 0x2FFFC, 0x30000):
        with pytest.raises(ValueError):
            regs.write32(bad, 0)
        with pytest.raises(ValueError):
            regs.read32(bad)
    regs.close()
    regs.close()
    data = path.read_bytes()
    assert data[0x2094:0x2098] == (0x3000).to_bytes(4, "little")
    assert data[0x0094:0x0098] == (0xDEADBEEF).to_bytes(4, "little")
    rest = data[:0x0094] + data[0x0098:0x2094] + data[0x2098:]
    assert rest == bytes(len(rest))


# ---------------------------------------------------------------------------
# The block, the lines and the drive-settle-drain loop
# ---------------------------------------------------------------------------

from wl_sync.rp1 import program  # noqa: E402

_EBUSY = 16


class _Shim:
    """libwlsync_pio.so, version 5's exports, for the harness: successive free state
    machines (four in the block, pio/wlsync_pio.c:199-202), each with its own RX FIFO of
    `depth` words that drops what it cannot hold, as `push noblock` does. `calls` records
    each call's name and thread; `enables` each mask the sync enable was given.
    `refuse_sync` makes the sync enable return 0 and run nothing, as piolib's discarded
    refusal does (pio/wlsync_pio.c:252-262)."""

    def __init__(self, board):
        self.board = board
        self.calls = []
        self.opened = False
        self.closed = 0
        self.claimed = {}  # sm -> jmp pin
        self.claims = []  # (sm, jmp pin, claim_jmp_pin)
        self.enabled = set()
        self.enables = []
        self.fifo = {}
        self.depth = 16
        self.refuse_sync = False
        self.level_errno = {}
        self.capture_args = None

    def _call(self, name):
        self.calls.append((name, threading.get_ident()))

    def wlsync_pio_open(self, index):
        self._call("open")
        self.opened = True
        return 0

    def wlsync_pio_close(self):
        self._call("close")
        self.closed += 1
        self.opened = False
        self.enabled.clear()

    def wlsync_encode_jmp_pin(self, addr):
        return program.encode_jmp_pin(addr)

    def wlsync_encode_jmp_x_dec(self, addr):
        return program.encode_jmp_x_dec(addr)

    def wlsync_encode_in_pins(self, count):
        return program.encode_in_pins(count)

    def wlsync_encode_push_noblock(self):
        return program.encode_push_noblock()

    def wlsync_encode_mov_isr_x(self):
        return program.encode_mov_isr_x()

    def wlsync_encode_mov_x_not_null(self):
        return program.encode_mov_x_not_null()

    def wlsync_clk_sys_hz(self):
        return 200_000_000

    def wlsync_rx_fifo_depth(self):
        return self.depth

    def _claim(self, pin):
        free = [sm for sm in range(4) if sm not in self.claimed]
        if not free:
            return -_EBUSY
        self.claimed[free[0]] = pin
        self.fifo[free[0]] = []
        return free[0]

    def wlsync_start_capture(self, words, length, wrap_target, wrap, entry, data_base,
                             data_count, strobe_pin, clkdiv, seed):
        self._call("start_capture")
        self.capture_args = (list(words), length, wrap_target, wrap, entry, data_base,
                             data_count, strobe_pin, clkdiv, seed)
        return self._claim(strobe_pin)

    def wlsync_start_stamp(self, wrap_target, wrap, entry, data_base, jmp_pin,
                           claim_jmp_pin, clkdiv, seed):
        self._call("start_stamp")
        sm = self._claim(jmp_pin)
        if sm >= 0:
            self.claims.append((sm, jmp_pin, claim_jmp_pin, (wrap_target, wrap, entry,
                                                             data_base, clkdiv, seed)))
        return sm

    def wlsync_enable_in_sync(self, mask):
        self._call("enable_in_sync")
        self.enables.append(mask)
        if not self.refuse_sync:
            self.enabled |= {sm for sm in range(4) if mask >> sm & 1}
            self.board.clock.start_p()
        return 0

    def wlsync_sm_set_enabled(self, sm, on):
        self._call("set_enabled")
        if on:
            if not self.enabled:
                self.board.clock.start_p()
            self.enabled.add(sm)
        else:
            self.enabled.discard(sm)
        return 0

    def push(self, sm, words):
        fifo = self.fifo[sm]
        for word in words:
            if len(fifo) < self.depth:
                fifo.append(word)

    def wlsync_sm_rx_full(self, sm):
        self._call("rx_full")
        return int(len(self.fifo[sm]) >= self.depth)

    def wlsync_sm_rx_level(self, sm):
        self._call("rx_level")
        if sm in self.level_errno:
            return -self.level_errno[sm]
        return len(self.fifo[sm])

    def wlsync_sm_get(self, sm):
        self._call("get")
        return self.fifo[sm].pop(0)


class _Board:
    """The box with nothing wired: a rise of any pad is stamped, at the PIO count, by every
    enabled stamp machine whose jmp pin it is, unless the PIO is blind to the pin.

    Knobs: `skew_us` adds ticks to one machine's stamps; `phase` maps the edge's number to
    ticks added to a machine's (dividers out of phase); `loss_us` takes that many ticks
    from a machine's count at each of its events; `short_loss_us` the same, at the fall of
    a high under 5 us only; `blind` the pins no machine sees; `crosstalk` maps a pin to
    another whose machines also stamp its rises; `w_on_rise` pushes a code word to the
    capture machine at the next rise. A high shorter than 1 us is not seen: its stamps
    are taken back at its fall."""

    def __init__(self, clock):
        self.clock = clock
        self.bank = _Bank()
        self.bank.on_pad = self.pad_changed
        self.shim = _Shim(self)
        self.skew_us = {}
        self.phase = {}
        self.loss_us = {}
        self.short_loss_us = {}
        self.lost = {}
        self.blind = set()
        self.crosstalk = {}
        self.rises = {}
        self.rose_at = {}
        self.pushed = {}
        self.w_on_rise = False
        self.used = {}

    def pad_changed(self, pin, level):
        shim = self.shim
        if not level:
            high = self.clock.true_ns - self.rose_at.get(pin, self.clock.true_ns)
            if high < 1_000:
                # Under the 1 us the program needs (backend.py:975-976): unseen, so the
                # stamps the rise pushed are taken back (fix round 4's fake).
                for sm, words in self.pushed.pop(pin, []):
                    fifo = shim.fifo[sm]
                    if fifo[-len(words):] == words:
                        del fifo[-len(words):]
                    self.lost[sm] = self.lost.get(sm, 0) - self.loss_us.get(sm, 0)
                return
            if high < 5_000:
                for sm in self._watching(pin):
                    self.lost[sm] = self.lost.get(sm, 0) + self.short_loss_us.get(sm, 0)
            return
        self.pushed[pin] = []
        self.rose_at[pin] = self.clock.true_ns
        number = self.rises[pin] = self.rises.get(pin, 0) + 1
        if self.w_on_rise and 0 in shim.enabled:
            shim.push(0, [5, (~1) & 0xFFFF_FFFF])
            self.w_on_rise = False
        for sm in self._watching(pin):
            p = (self.clock.p_now() + self.skew_us.get(sm, 0)
                 + self.phase.get(sm, lambda k: 0)(number) - self.lost.get(sm, 0))
            shim.push(sm, [0, (~p) & 0xFFFF_FFFF])
            self.pushed[pin].append((sm, [0, (~p) & 0xFFFF_FFFF]))
            self.lost[sm] = self.lost.get(sm, 0) + self.loss_us.get(sm, 0)

    def _watching(self, pin):
        pins = {pin, *([self.crosstalk[pin]] if pin in self.crosstalk else [])}
        return [sm for sm, jmp in self.shim.claimed.items()
                if sm != 0 and jmp in pins - self.blind and sm in self.shim.enabled]


def _gpiod_stub(board, set_cost_ns=20_000):
    """libgpiod v2's bindings, as the harness uses them: one multi-line output request,
    `set_value` and `set_values`, each costing `set_cost_ns` with its write at the middle,
    and line info that says which lines another consumer holds (`board.used`)."""
    line = types.ModuleType("gpiod.line")
    line.Direction = types.SimpleNamespace(INPUT="in", OUTPUT="out")
    line.Value = types.SimpleNamespace(ACTIVE=1, INACTIVE=0)
    module = types.ModuleType("gpiod")
    module.line = line
    module.LineSettings = lambda **kwargs: kwargs
    module.requests = []
    module.released = []
    module.set_cost_ns = set_cost_ns

    class _Request:
        def __init__(self, pins):
            self.pins = pins

        def set_values(self, values):
            half = module.set_cost_ns // 2
            board.clock.advance(half)
            for pin, value in values.items():
                assert pin in self.pins, pin
                board.bank.drive(pin, int(value == line.Value.ACTIVE))
            board.clock.advance(module.set_cost_ns - half)

        def set_value(self, pin, value):
            self.set_values({pin: value})

        def release(self):
            module.released.append(dict((p, board.bank.rio_out[p]) for p in self.pins))

    def request_lines(path, config, consumer):
        [(pins, settings)] = config.items()
        module.requests.append((path, pins, settings, consumer))
        for pin in pins:
            board.bank.hold_as_output(pin, 0)
        return _Request(pins)

    class Chip:
        def __init__(self, path):
            self.path = path

        def get_line_info(self, pin):
            consumer = board.used.get(pin)
            return types.SimpleNamespace(used=consumer is not None, consumer=consumer or "")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    module.request_lines = request_lines
    module.Chip = Chip
    return module, line


@pytest.fixture
def board(monkeypatch):
    import sys

    clock = _Clock()
    model = _Board(clock)
    module, line = _gpiod_stub(model)
    model.gpiod = module
    monkeypatch.setitem(sys.modules, "gpiod", module)
    monkeypatch.setitem(sys.modules, "gpiod.line", line)
    monkeypatch.setattr(harness, "time", clock)
    monkeypatch.setattr(harness, "_SPIN_NS", 0)
    return model


def _rig(board, stamp_pins, dense_pin=None):
    return harness.Rig.open(
        "/dev/gpiochip0", stamp_pins, dense_pin=dense_pin,
        load=lambda: board.shim,
        open_map=lambda path: board.bank,
    )


def test_the_rig_requests_its_lines_low_then_opens_the_block_and_starts_unclaimed_machines(
    board,
):
    """The order: the lines, low, under the harness's own consumer name; then the block,
    the capture machine as production configures it, each stamp machine on the loaded
    program with claim_jmp_pin 0; nothing enabled until the sync enable."""
    from wl_sync.pins import CODE_DATA_BASE, CODE_DATA_COUNT, CODE_STROBE_PIN

    rig = _rig(board, [17, 17, 17])
    [(path, pins, settings, consumer)] = board.gpiod.requests
    assert (path, pins, consumer) == ("/dev/gpiochip0", (17,), "wl-sync-bench")
    assert settings == {"direction": "out", "output_value": 0}
    shim = board.shim
    assert [name for name, _ in shim.calls[:2]] == ["open", "start_capture"]
    words, length, *rest = shim.capture_args
    assert words == list(program.STROBE_CAPTURE) and length == len(words)
    assert rest == [program.WRAP_TARGET, program.WRAP, program.ENTRY, CODE_DATA_BASE,
                    CODE_DATA_COUNT, CODE_STROBE_PIN, 100.0, program.encode_mov_x_not_null()]
    assert [(sm, pin, claim) for sm, pin, claim, _ in shim.claims] == [
        (1, 17, 0), (2, 17, 0), (3, 17, 0)]
    assert all(args == (program.WRAP_TARGET, program.WRAP, program.ENTRY, CODE_DATA_BASE,
                        100.0, program.encode_mov_x_not_null()) for *_, args in shim.claims)
    assert shim.enabled == set()
    assert rig.block.stamp_sms == [1, 2, 3] and rig.block.capture_sm == 0
    rig.close()


def test_one_sync_enable_holds_every_machine_and_close_disables_them_all(board):
    rig = _rig(board, [17, 17])
    rig.block.enable_in_sync()
    assert board.shim.enables == [0b0111]
    rig.close()
    assert board.shim.closed == 1 and board.shim.enabled == set()
    assert board.gpiod.released == [{17: 0}]
    rig.close()  # once only
    assert board.shim.closed == 1


def test_every_piolib_call_is_made_on_one_thread_and_another_is_refused(board):
    rig = _rig(board, [17])
    rig.block.enable_in_sync()
    rig.edge(17, 20_000)
    assert {thread for _, thread in board.shim.calls} == {threading.get_ident()}
    failures = []

    def elsewhere():
        try:
            rig.drain()
        except harness.HarnessError as error:
            failures.append(error)

    thread = threading.Thread(target=elsewhere)
    thread.start()
    thread.join()
    assert len(failures) == 1 and "thread" in str(failures[0])
    rig.close()


def test_each_edge_is_drained_after_the_settle_and_each_stamp_is_the_edges_own(board):
    rig = _rig(board, [17, 17])
    rig.block.enable_in_sync()
    event = rig.edge(17, 20_000)
    assert set(event.stamps) == {1, 2}
    assert event.stamps[1] == event.stamps[2] and len(event.stamps[1]) == 1
    # The rise's write lies inside its bracket, and the drain waited out the settle.
    assert event.rise_before < event.rise_after < event.fall_before < event.fall_after
    assert event.drained_at - event.rise_before >= harness.SETTLE_NS
    rig.close()


def test_a_machine_that_did_not_stamp_has_an_empty_list(board):
    rig = _rig(board, [17, 18])
    rig.block.enable_in_sync()
    event = rig.edge(17, 20_000)
    assert len(event.stamps[1]) == 1 and event.stamps[2] == []
    rig.close()


def test_a_fifo_level_that_cannot_be_read_is_counted_and_nothing_is_popped(board):
    rig = _rig(board, [17])
    rig.block.enable_in_sync()
    board.shim.level_errno[1] = 5
    rig.edge(17, 20_000)
    assert rig.tally.level_errors == 1 and len(board.shim.fifo[1]) == 2
    rig.close()


def test_a_word_from_the_capture_machine_is_counted(board):
    rig = _rig(board, [17])
    rig.block.enable_in_sync()
    board.w_on_rise = True
    rig.edge(17, 20_000)
    assert rig.tally.words == 2
    rig.close()


def test_a_full_fifo_is_counted(board):
    rig = _rig(board, [17])
    rig.block.enable_in_sync()
    board.shim.push(1, [0, 1] * 8)
    rig.drain()
    assert rig.tally.full == 1
    rig.close()


def test_a_lone_counter_is_a_slip_and_is_still_a_stamp(board):
    rig = _rig(board, [17])
    rig.block.enable_in_sync()
    board.shim.push(1, [(~5) & 0xFFFF_FFFF])
    assert rig.drain() == {1: [5]}
    assert rig.tally.slips == 1
    rig.close()


@pytest.mark.parametrize("pin", [17, 18])
def test_a_line_another_consumer_holds_is_refused_before_anything_opens(board, pin):
    board.used[pin] = "someone"
    with pytest.raises(harness.HarnessError, match="someone"):
        _rig(board, [17, 18], dense_pin=18)
    assert board.gpiod.requests == [] and board.shim.calls == []


def test_a_stamp_pin_that_is_neither_gpio17_nor_spare_is_refused(board):
    with pytest.raises(ValueError, match="GPIO20"):
        _rig(board, [17, 20])
    assert board.shim.calls == []


def test_a_block_with_no_machine_left_closes_and_raises(board):
    with pytest.raises(harness.PiolibError, match="errno 16"):
        _rig(board, [17, 17, 17, 17])
    assert board.shim.closed == 1 and board.gpiod.released == [{17: 0}]


def test_the_rig_opens_the_writer_on_the_dense_pin_and_verifies_it_before_the_block(board):
    rig = _rig(board, [18, 17], dense_pin=18)
    assert board.bank.writes == []
    assert board.shim.calls[0][0] == "open"
    assert set(board.bank.reads) == {0x090, 0x094}
    rig.block.enable_in_sync()
    event = rig.short(2_000, "w2")
    assert event.stamps[1] and not event.stamps[2]
    assert event.upper_ns < 5_000 and event.lower_ns >= 2_000
    assert event.rise_after is not None and event.bracket_ns < event.upper_ns
    rig.close()
    assert board.bank.writes[-1] == (0x3094, 0x3000) and board.bank.closed


def test_a_dense_pin_the_writer_refuses_closes_the_lines_and_opens_no_block(board):
    real = board.gpiod.request_lines

    def held_by_the_pio(path, config, consumer):
        request = real(path, config, consumer)
        board.bank.ctrl[18] = (board.bank.ctrl[18] & ~0x1F) | 7
        return request

    board.gpiod.request_lines = held_by_the_pio
    with pytest.raises(harness.WriterRefused, match="PIO"):
        _rig(board, [18, 17], dense_pin=18)
    assert board.shim.calls == [] and len(board.gpiod.released) == 1


def test_a_held_high_on_the_dense_pin_is_read_back_and_has_a_lower_bound(board):
    rig = _rig(board, [18, 17], dense_pin=18)
    rig.block.enable_in_sync()
    event = rig.edge(18, 10_000)
    assert event.rise_landed and event.lower_ns >= 10_000
    assert event.upper_ns >= event.lower_ns
    rig.close()


def test_the_close_restores_the_writer_even_when_the_block_fails_to_close(board):
    rig = _rig(board, [18, 17], dense_pin=18)
    rig.short(2_000)  # armed, so there is an override to restore

    def failing_close():
        raise RuntimeError("close")

    board.shim.wlsync_pio_close = failing_close
    with pytest.raises(RuntimeError, match="close"):
        rig.close()
    assert board.bank.writes[-1] == (0x3094, 0x3000) and board.bank.closed
    assert len(board.gpiod.released) == 1


def test_the_running_check_passes_when_every_machine_stamps_the_pulse(board):
    rig = _rig(board, [17, 17])
    rig.block.enable_in_sync()
    assert rig.running_check(17) == ""
    rig.close()


def test_the_running_check_tells_a_refused_sync_enable(board):
    """No stamp after the sync enable, then a stamp with each machine enabled alone, the
    plain way: the start-up test's reading (wl_sync/rp1/backend.py:856-860)."""
    board.shim.refuse_sync = True
    rig = _rig(board, [17, 17])
    rig.block.enable_in_sync()
    assert rig.running_check(17) == harness.SYNC_REFUSED.format(pin=17)
    rig.close()


def test_the_running_check_names_a_pin_the_pio_does_not_see(board):
    board.blind = {18}
    rig = _rig(board, [18, 17], dense_pin=18)
    rig.block.enable_in_sync()
    assert rig.running_check(17) == ""
    assert rig.running_check(18) == harness.BLIND.format(pin=18)
    rig.close()


def test_a_dense_pin_the_block_takes_is_refused_once_the_block_is_open(board):
    """The writer reads the pin again once the PIO block is open: a start that handed the
    pin to the PIO would otherwise be overridden by the writer from then on."""
    real = board.shim.wlsync_start_stamp

    def claiming(wrap_target, wrap, entry, data_base, jmp_pin, claim, clkdiv, seed):
        if jmp_pin == 18:
            board.bank.ctrl[18] = (board.bank.ctrl[18] & ~0x1F) | 7
        return real(wrap_target, wrap, entry, data_base, jmp_pin, claim, clkdiv, seed)

    board.shim.wlsync_start_stamp = claiming
    with pytest.raises(harness.WriterRefused, match="PIO"):
        _rig(board, [18, 17], dense_pin=18)
    assert board.shim.closed == 1 and len(board.gpiod.released) == 1
    assert board.bank.writes == []  # the pin is the PIO's now: not even a CLEAR


def test_nothing_but_the_bench_reaches_the_harness_or_rp1s_registers():
    """The register writer is confined to the bench harness (the user's ruling,
    2026-11-20): no module of the package but wl_sync/bench.py names the harness, and none
    but the harness names /dev/gpiomem."""
    root = pathlib.Path(harness.__file__).parent.parent
    for path in sorted(root.rglob("*.py")):
        text = path.read_text()
        name = path.relative_to(root.parent).as_posix()
        if name not in ("wl_sync/bench.py", "wl_sync/rp1/harness.py"):
            assert "harness" not in text, name
        if name != "wl_sync/rp1/harness.py":
            assert "gpiomem" not in text, name


def test_an_override_found_set_is_refused_naming_the_command_that_clears_it(bank):
    bank.ctrl[18] |= 3 << 12
    with pytest.raises(harness.WriterRefused,
                       match=r"python -m wl_sync.bench override --pin 18 --clear"):
        _writer(bank).open()
    assert bank.writes == []


def _clear(board, pin=18):
    return harness.clear_override(pin, "/dev/gpiochip0", open_map=lambda path: board.bank)


def test_clearing_a_stuck_override_checks_the_map_and_writes_one_clear(board):
    """The recovery for an override a killed run left set (review I3). libgpiod takes the
    line low, and STATUS's OUTFROMPERI, which is before the override (Table 7, p.20-21),
    must follow it while OUTTOPAD stays forced high; then one CLEAR of OUTOVER, and CTRL
    and STATUS read back."""
    board.bank.hold_as_output(18)
    board.bank.ctrl[18] |= 3 << 12
    board.bank.rio_oe[18] = 0  # as libgpiod's release may leave it
    said = _clear(board)
    assert board.bank.writes == [(0x3094, 0x3000)]
    assert board.bank.ctrl[18] & 0xF000 == 0 and board.bank.pad(18) == 0
    assert "cleared" in said and board.bank.closed
    [(_, pins, _, consumer)] = board.gpiod.requests
    assert pins == (18,) and consumer == "wl-sync-bench"
    assert board.gpiod.released == [{18: 0}]


def test_clearing_a_pin_with_no_override_is_refused_as_nothing_to_clear(board):
    """Review N4: a clear of the wrong pin must not read as a success."""
    with pytest.raises(harness.NothingToClear, match="GPIO18 has no override set"):
        _clear(board)
    assert board.bank.writes == []


@pytest.mark.parametrize("outover", [1, 2])
def test_clearing_refuses_an_override_the_harness_never_writes(board, outover):
    """Review N3: the writer only ever sets OUTOVER to 3, so any other value is someone
    else's, and is refused with nothing written, naming the value found."""
    board.bank.ctrl[18] |= outover << 12
    with pytest.raises(harness.WriterRefused, match=f"OUTOVER {outover}"):
        _clear(board)
    assert board.bank.writes == [] and board.bank.closed


@pytest.mark.parametrize("fault, said", [
    ("frozen", "does not follow"),
    ("pio", "PIO"),
    ("oeover", "output enable"),
    ("function", "runs function 1, not SYS_RIO"),
    ("no_oe", "does not follow"),
])
def test_clearing_is_refused_with_nothing_written_unless_the_pin_is_provably_ours(
    board, fault, said
):
    board.bank.ctrl[18] |= 3 << 12
    real = board.gpiod.request_lines

    def request(path, config, consumer):
        made = real(path, config, consumer)
        if fault == "frozen":
            board.bank.frozen = True
        elif fault == "pio":
            board.bank.ctrl[18] = (board.bank.ctrl[18] & ~0x1F) | 7
        elif fault == "function":
            board.bank.ctrl[18] = (board.bank.ctrl[18] & ~0x1F) | 1
        elif fault == "no_oe":  # the funcsel's output enable off: OEFROMPERI clear (N5)
            board.bank.rio_oe[18] = 0
        else:
            board.bank.ctrl[18] |= 3 << 14
        return made

    board.gpiod.request_lines = request
    with pytest.raises(harness.WriterRefused, match=said):
        _clear(board)
    assert board.bank.writes == [] and board.bank.closed
    assert len(board.gpiod.released) == 1


def test_clearing_refuses_a_line_another_consumer_holds_before_mapping(board):
    board.used[18] = "someone"
    opened = []
    with pytest.raises(harness.HarnessError, match="someone"):
        harness.clear_override(18, "/dev/gpiochip0", open_map=lambda p: opened.append(p))
    assert opened == [] and board.bank.writes == []


@pytest.mark.parametrize("pin", [17, 20])
def test_clearing_takes_a_spare_pin_only(board, pin):
    with pytest.raises(ValueError, match="spare"):
        _clear(board, pin)
    assert board.gpiod.requests == []


def test_reading_the_overrides_only_reads(board):
    board.bank.hold_as_output(18)
    board.bank.ctrl[19] |= 3 << 12
    found = harness.read_overrides((18, 19), open_map=lambda path: board.bank)
    assert [(pin, outover, oeover) for pin, _, _, outover, oeover in found] == [
        (18, 0, 0), (19, 3, 0)]
    assert board.bank.writes == [] and board.gpiod.requests == [] and board.bank.closed


@pytest.mark.parametrize("offset", [0x094, 0x1094, 0x2090, 0x309C, 0x2098, 0x4094, 0x0])
def test_the_writer_refuses_any_store_but_its_pins_set_and_clear(bank, offset):
    """The map now spans the whole 0x30000 window, so the writer's own guard is what keeps
    every store on the dense pin's CTRL SET and CLEAR aliases: any other offset, or any
    other value, is refused with nothing written."""
    writer = _writer(bank)
    writer.open()
    with pytest.raises(harness.WriterError, match="only"):
        writer._store(offset, harness.OUTOVER_MASK)
    with pytest.raises(harness.WriterError, match="only"):
        writer._store(0x2094, 0x1000)
    assert bank.writes == []


def test_on_posted_writes_each_high_lasts_its_hold_and_lies_inside_its_bounds(bank):
    """Box step 2: with writes that land only at the next read, a SET and a CLEAR with no
    read between them made a high of nothing. Each store is now read back before RAW is
    taken, so the SET has landed when the hold starts and the CLEAR when the upper bound
    ends (fix round 4)."""
    pads = []
    bank.on_pad = lambda pin, level: pads.append((level, bank.clock.true_ns))
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        pads.clear()
        bounds = writer.pulse(1_500)
    (_, rise), (_, fall) = pads
    assert fall - rise >= 1_500
    assert bounds.before <= rise <= bounds.set_after
    assert bounds.clear_before <= fall <= bounds.after
    assert bounds.clear_before - bounds.set_after >= 1_500
    assert bounds.set_read_ns > 0 and bounds.clear_read_ns > 0


def test_a_set_whose_read_back_shows_no_rise_is_raised_with_the_override_cleared(bank):
    """If the SET's read-back reads the pad low, the write did not land before the read,
    so the ordering the bounds rest on does not hold: raised, and the CLEAR still made."""
    with _writer(bank) as writer:
        writer.verify(lambda high: bank.drive(18, int(high)))
        writer.pulse(0)
        real = bank._land

        def dropped_set(offset, value):
            if offset != 0x2094:
                real(offset, value)

        bank._land = dropped_set
        with pytest.raises(harness.WriterError, match="did not rise"):
            writer.pulse(1_000)
        bank._land = real
        assert bank.writes[-1] == (0x3094, 0x3000)


def test_a_libgpiod_fall_on_the_dense_pin_is_read_back_so_its_upper_bound_is_one(board):
    """The fall is read back too, so a libgpiod high on the dense pin is bounded both ways,
    and the path rule can be applied to it as to the writer's (fix round 4)."""
    rig = _rig(board, [18, 17], dense_pin=18)
    rig.block.enable_in_sync()
    event = rig.edge(18, 0)
    assert event.rise_landed and event.fall_landed and event.bounded
    rig.close()
