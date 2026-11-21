import dataclasses
import types

import pytest

from wl_sync import calibration
from wl_sync.calibration import (
    KERNEL_OFFSET_MAX_NS,
    REALTIME_STEP_NS,
    STA_NANO,
    STA_PLL,
    STA_UNSYNC,
    KernelNtp,
    RateEstimator,
    Timex,
    kernel_ntp_from,
    gate_holds,
    probe_kernel_ntp,
    si_gate,
)
from wl_sync.clockmap import BRACKET_WIDTH_MAX_NS, ClockPair

S = 10**9
GOOD = KernelNtp(unsynchronized=False, pll=True, offset_ns=0)


def pair(k, p, *, width=1_000, step=0, mono=None):
    mono = k * 60 * S if mono is None else mono
    return ClockPair(p=p, mono_ns=mono, raw_ns=mono, width_ns=width,
                     realtime_minus_mono_ns=step)


def test_the_constants_are_the_briefs():
    assert (STA_PLL, STA_UNSYNC, STA_NANO) == (0x0001, 0x0040, 0x2000)
    assert (KERNEL_OFFSET_MAX_NS, REALTIME_STEP_NS) == (1_000_000, 1_000_000)


def test_the_si_rate_is_the_slope_of_p_on_mono():
    est = RateEstimator()
    for k in range(61):
        est.add(pair(k, k * 60 * 1_000_025), True)
    got = est.si()
    assert got.rate_ppb == 25_000
    assert got.p_per_raw_ppb == 25_000
    assert got.baseline_s == 3600.0
    assert got.pairs == 61


def test_ungated_pairs_never_reach_the_si_rate():
    est = RateEstimator()
    for k in range(10):
        est.add(pair(k, k * 60 * 1_000_025), False)
    assert est.si() is None
    assert est.raw_ratio() is not None
    assert est.raw_ratio()[0] == 25_000


def test_a_step_in_realtime_restarts_the_baseline():
    est = RateEstimator()
    p = 0
    for k in range(50):
        est.add(pair(k, p), True)
        p += 60 * 1_000_025
    for k in range(50, 100):
        est.add(pair(k, p, step=2_000_000), True)
        p += 60 * 999_990
    got = est.si()
    assert got.rate_ppb == -10_000
    assert got.pairs == 50
    # A REALTIME step does not move RAW, so the P/RAW witness keeps its whole baseline.
    ppb, baseline_s = est.raw_ratio()
    assert baseline_s == 99 * 60.0
    assert -10_000 < ppb < 25_000


def test_a_wide_bracket_is_not_a_pair():
    est = RateEstimator()
    for k in range(5):
        est.add(pair(k, k * 60 * 1_000_025), True)
    before = (est.si(), est.raw_ratio())
    est.add(pair(5, 999_999_999, width=BRACKET_WIDTH_MAX_NS + 1), True)
    assert (est.si(), est.raw_ratio()) == before


def test_a_realtime_step_of_exactly_the_limit_does_not_restart_the_baseline():
    """Coverage: the step limit is exclusive, so 1_000_000 ns stays in one baseline
    and 1_000_001 ns starts another."""
    for step, pairs in ((REALTIME_STEP_NS, 4), (REALTIME_STEP_NS + 1, 2)):
        est = RateEstimator()
        for k in range(4):
            est.add(pair(k, k * 60 * 1_000_025, step=0 if k < 2 else step), True)
        assert est.si().pairs == pairs


def test_one_pair_gives_no_estimate():
    est = RateEstimator()
    est.add(pair(0, 0), True)
    assert est.si() is None and est.raw_ratio() is None


def test_the_residual_is_the_rms_about_the_line():
    est = RateEstimator()
    for k in range(40):
        est.add(pair(k, k * 60 * 1_000_000 + (3 if k % 2 else -3)), True)
    assert est.si().residual_rms_us == pytest.approx(3.0, abs=0.1)


@pytest.mark.parametrize("known, kernel, p, expected", [
    (True, GOOD, pair(0, 0), True),
    (False, GOOD, pair(0, 0), False),
    (None, GOOD, pair(0, 0), False),
    (True, None, pair(0, 0), False),
    (True, dataclasses.replace(GOOD, unsynchronized=True), pair(0, 0), False),
    (True, dataclasses.replace(GOOD, pll=False), pair(0, 0), False),
    (True, dataclasses.replace(GOOD, offset_ns=1_000_001), pair(0, 0), False),
    (True, dataclasses.replace(GOOD, offset_ns=-1_000_001), pair(0, 0), False),
    (True, dataclasses.replace(GOOD, offset_ns=-1_000_000), pair(0, 0), True),
    (True, GOOD, pair(0, 0, width=BRACKET_WIDTH_MAX_NS + 1), False),
])
def test_the_gate_opens_only_on_a_synced_kernel_pll_with_a_small_offset(known, kernel, p, expected):
    assert si_gate(known, kernel, p) is expected


def test_the_gate_is_open_at_exactly_the_offset_limit():
    """Coverage: the offset limit is inclusive."""
    kernel = dataclasses.replace(GOOD, offset_ns=KERNEL_OFFSET_MAX_NS)
    assert si_gate(True, kernel, pair(0, 0)) is True


@pytest.mark.parametrize("known, kernel, expected", [
    (True, GOOD, ()),
    (None, GOOD, (calibration.HELD_NTP_UNKNOWN,)),
    (False, GOOD, (calibration.HELD_NTP_UNSYNCED,)),
    (True, None, (calibration.HELD_KERNEL_UNREAD,)),
    (None, None, (calibration.HELD_NTP_UNKNOWN, calibration.HELD_KERNEL_UNREAD)),
    (True, dataclasses.replace(GOOD, unsynchronized=True), (calibration.HELD_STA_UNSYNC,)),
    (True, dataclasses.replace(GOOD, pll=False), (calibration.HELD_NO_PLL,)),
    (True, dataclasses.replace(GOOD, offset_ns=-1_000_001), (calibration.HELD_OFFSET,)),
    (True, dataclasses.replace(GOOD, offset_ns=-1_000_000), ()),
    (False, KernelNtp(unsynchronized=True, pll=False, offset_ns=5_000_000), (
        calibration.HELD_NTP_UNSYNCED, calibration.HELD_STA_UNSYNC,
        calibration.HELD_NO_PLL, calibration.HELD_OFFSET,
    )),
])
def test_the_gate_names_every_condition_that_holds_it(known, kernel, expected):
    """Final review M4: a gate shut for an hour is named in the journal by what held it,
    so each of si_gate's conditions but the bracket's has a name, and a narrow pair is
    gated exactly when none holds."""
    assert gate_holds(known, kernel) == expected
    assert si_gate(known, kernel, pair(0, 0)) is (expected == ())


def test_adjtimex_offset_is_nanoseconds_under_sta_nano_and_microseconds_otherwise():
    timex = Timex(status=STA_PLL | STA_NANO, offset=250_000)
    assert kernel_ntp_from(timex) == KernelNtp(False, True, 250_000)
    assert kernel_ntp_from(Timex(status=STA_UNSYNC, offset=250)) == KernelNtp(True, False, 250_000)


def test_where_libc_has_no_adjtimex_the_kernel_cannot_be_asked(monkeypatch):
    monkeypatch.setattr(calibration, "_libc", types.SimpleNamespace())
    assert probe_kernel_ntp() is None


def test_a_failing_adjtimex_is_none(monkeypatch):
    monkeypatch.setattr(calibration, "_libc", types.SimpleNamespace(adjtimex=lambda ref: -1))
    assert probe_kernel_ntp() is None


def test_a_raising_adjtimex_is_none(monkeypatch):
    def boom(ref):
        raise OSError("no")
    monkeypatch.setattr(calibration, "_libc", types.SimpleNamespace(adjtimex=boom))
    assert probe_kernel_ntp() is None


def test_a_working_adjtimex_is_read_with_modes_zero(monkeypatch):
    seen = []

    def fake(ref):
        tx = ref._obj
        seen.append(tx.modes)
        tx.status = STA_PLL | STA_NANO
        tx.offset = 42
        return 0

    monkeypatch.setattr(calibration, "_libc", types.SimpleNamespace(adjtimex=fake))
    assert probe_kernel_ntp() == KernelNtp(False, True, 42)
    assert seen == [0]


def test_timex_is_the_linux_size_on_lp64():
    import ctypes
    if ctypes.sizeof(ctypes.c_long) == 8:
        assert ctypes.sizeof(Timex) == 208


# --- Task 1.5: the calibration file ------------------------------------------------

import datetime  # noqa: E402
import os  # noqa: E402

from wl_sync.calibration import (  # noqa: E402
    P_PER_RAW_BOUND_PPB,
    RATE_BOUND_PPB,
    RATE_FILE_NAME,
    Calibration,
    probe_board_serial,
    read_calibration,
    write_calibration,
)

SERIAL = "0123456789abcdef"  # made up


def make_cal(**over):
    fields = dict(serial=SERIAL, rate_ppb=-12_345, p_per_raw_ppb=3_000, baseline_s=3600.5,
                  pairs=60, residual_rms_us=1.5, boot_id="boot-a",
                  written_at=datetime.datetime(2026, 11, 7, 12, 0, tzinfo=datetime.timezone.utc))
    fields.update(over)
    return Calibration(**fields)


def test_a_calibration_round_trips_through_its_file(tmp_path):
    path = tmp_path / RATE_FILE_NAME
    cal = make_cal()
    write_calibration(path, cal)
    assert read_calibration(path, SERIAL) == (cal, "")
    assert cal.written_at.tzinfo is not None
    with pytest.raises(Exception):
        cal.rate_ppb = 1  # frozen


def test_no_file_is_no_calibration_and_says_so(tmp_path):
    assert read_calibration(tmp_path / RATE_FILE_NAME, SERIAL) == (None, "no calibration file")


@pytest.mark.parametrize("junk", ["not json at all", "{}"])
def test_an_unreadable_file_is_no_calibration(tmp_path, junk):
    path = tmp_path / RATE_FILE_NAME
    path.write_text(junk)
    cal, reason = read_calibration(path, SERIAL)
    assert cal is None
    assert reason.startswith("the calibration file is unreadable (")


def test_an_unknown_field_is_unreadable(tmp_path):
    path = tmp_path / RATE_FILE_NAME
    write_calibration(path, make_cal())
    import json
    data = json.loads(path.read_text())
    data["surprise"] = 1
    path.write_text(json.dumps(data))
    cal, reason = read_calibration(path, SERIAL)
    assert cal is None and reason.startswith("the calibration file is unreadable (")


@pytest.mark.parametrize(
    "field, value",
    [("rate_ppb", True), ("pairs", True), ("baseline_s", "NaN"), ("residual_rms_us", "Infinity")],
)
def test_a_bool_where_an_int_belongs_or_a_non_finite_float_is_unreadable(tmp_path, field, value):
    """A hand-edited file: lax pydantic would read true as 1 and "NaN" as a float."""
    path = tmp_path / RATE_FILE_NAME
    write_calibration(path, make_cal())
    import json
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    cal, reason = read_calibration(path, SERIAL)
    assert cal is None and reason.startswith("the calibration file is unreadable (")


def test_a_directory_in_place_of_the_file_is_unreadable_not_a_raise(tmp_path):
    cal, reason = read_calibration(tmp_path, SERIAL)
    assert cal is None and reason.startswith("the calibration file is unreadable (")


def test_another_boards_calibration_is_refused(tmp_path):
    path = tmp_path / RATE_FILE_NAME
    write_calibration(path, make_cal(serial="aaaa"))
    assert read_calibration(path, "bbbb") == (
        None, "it is another board's (serial aaaa, this board bbbb)")


def test_an_unreadable_serial_refuses_every_calibration(tmp_path):
    path = tmp_path / RATE_FILE_NAME
    write_calibration(path, make_cal())
    assert read_calibration(path, None) == (None, "this board's serial cannot be read")


def test_a_rate_beyond_200_ppm_is_refused_and_200_is_not(tmp_path):
    path = tmp_path / RATE_FILE_NAME
    assert RATE_BOUND_PPB == 200_000
    for ok in (-200_000, 200_000):
        write_calibration(path, make_cal(rate_ppb=ok))
        assert read_calibration(path, SERIAL)[0].rate_ppb == ok
    for bad, text in ((200_001, "+200001"), (-200_001, "-200001")):
        write_calibration(path, make_cal(rate_ppb=bad))
        assert read_calibration(path, SERIAL) == (
            None, f"its rate, {text} ppb, is outside +-200 ppm")


def test_a_p_per_raw_ratio_beyond_1000_ppm_is_refused_and_1000_is_not(tmp_path):
    """Final review M3. The ratio seeds the RAW->P fit (wl_sync/rp1/backend.py:1923), so
    one that parses but is wrong maps E wrongly: at -10^9 ppb or below the slope is 0 or
    less, every E lands at about P 0 and every B is dropped. It is bounded as the rate is,
    and refused with its own reason."""
    path = tmp_path / RATE_FILE_NAME
    assert P_PER_RAW_BOUND_PPB == 1_000_000
    for ok in (-1_000_000, 1_000_000):
        write_calibration(path, make_cal(p_per_raw_ppb=ok))
        assert read_calibration(path, SERIAL)[0].p_per_raw_ppb == ok
    for bad, text in ((1_000_001, "+1000001"), (-1_000_001, "-1000001"),
                      (-1_000_000_000, "-1000000000")):
        write_calibration(path, make_cal(p_per_raw_ppb=bad))
        assert read_calibration(path, SERIAL) == (
            None, f"its P/RAW ratio, {text} ppb, is outside +-1000 ppm")


def test_the_calibration_and_its_directory_are_both_fsynced(tmp_path, monkeypatch):
    """Mirrors tests/test_clock.py's checkpoint fsync test: by inode, so it asserts which
    objects were made durable, not how the code was written."""
    synced = []
    real_fsync = os.fsync

    def spy(fd):
        synced.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    path = tmp_path / RATE_FILE_NAME
    write_calibration(path, make_cal())
    assert path.stat().st_ino in synced, "the calibration's bytes were never fsynced"
    assert tmp_path.stat().st_ino in synced, "the rename was never fsynced"


def test_the_board_serial_comes_from_the_device_tree_then_cpuinfo(tmp_path, monkeypatch):
    tree = tmp_path / "serial-number"
    cpuinfo = tmp_path / "cpuinfo"
    monkeypatch.setattr(calibration, "DEVICE_TREE_SERIAL", tree)
    monkeypatch.setattr(calibration, "CPUINFO", cpuinfo)
    assert probe_board_serial() is None  # neither exists

    cpuinfo.write_text("processor\t: 0\nSerial\t\t: 00000000cafe0001\nModel\t: x\n")
    assert probe_board_serial() == "00000000cafe0001"

    tree.write_bytes(b"0123456789abcdef\x00")  # the device tree NUL-terminates
    assert probe_board_serial() == "0123456789abcdef"

    tree.write_bytes(b"\x00")  # empty: fall through to cpuinfo
    assert probe_board_serial() == "00000000cafe0001"


def test_probe_board_serial_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "DEVICE_TREE_SERIAL", tmp_path)  # a directory
    monkeypatch.setattr(calibration, "CPUINFO", tmp_path / "missing")
    assert probe_board_serial() is None
