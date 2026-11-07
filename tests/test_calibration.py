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
    assert est.raw_ratio()[0] == -10_000


def test_a_wide_bracket_is_not_a_pair():
    est = RateEstimator()
    for k in range(5):
        est.add(pair(k, k * 60 * 1_000_025), True)
    before = (est.si(), est.raw_ratio())
    est.add(pair(5, 999_999_999, width=BRACKET_WIDTH_MAX_NS + 1), True)
    assert (est.si(), est.raw_ratio()) == before


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


def test_adjtimex_offset_is_nanoseconds_under_sta_nano_and_microseconds_otherwise():
    assert kernel_ntp_from(Timex(status=STA_PLL | STA_NANO, offset=250_000)) == KernelNtp(False, True, 250_000)
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
