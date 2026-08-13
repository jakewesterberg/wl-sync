"""Barcode codec. Pure — no hardware dependency, so it runs in CI.

Frame layout, 200 ms total:

    LEAD   HIGH 10 ms
    GAP1   LOW  10 ms
    DATA   32 x 5 ms, MSB first, HIGH = 1
    GAP2   LOW  10 ms
    TRAIL  HIGH 10 ms

Emitted once per second, so 800 ms of idle LOW separates frames. A frame starts
at "a rising edge after at least IDLE_MIN_US of LOW", which is what distinguishes
the lead pulse from a run of set bits. The longest LOW run *inside* a frame is
180 ms — an all-zero payload plus both gaps — comfortably under the threshold.

Consequence of requiring the preceding idle: a frame decodes only if the window
also contains the end of the previous frame. That yields one barcode guaranteed
in any 2.0 s window and two in any 3.0 s window. One is sufficient, because the
timebase inherits clock rate from a session-wide fit and needs only an offset.

Generation is deliberately tolerant: 5 ms slots sampled at 30 kHz give 150
samples per bit, so software-timed output on Pi 5 is fine. The receivers time
their own edges; the barcode carries identity, not timing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

BIT_SLOT_US = 5_000
WRAPPER_US = 10_000
N_BITS = 32
FRAME_US = 4 * WRAPPER_US + N_BITS * BIT_SLOT_US  # 200_000
INTERVAL_US = 1_000_000
IDLE_MIN_US = 400_000

_MAX_VALUE = (1 << N_BITS) - 1
_QUARTER = WRAPPER_US // 4


@dataclass(frozen=True, slots=True)
class Barcode:
    value: int
    start_us: int  # tick of the LEAD rising edge


def encode(value: int) -> list[tuple[int, int]]:
    """Return (level, duration_us) pulses for one frame."""
    if not 0 <= value <= _MAX_VALUE:
        raise ValueError(f"barcode value out of 32-bit range: {value}")
    pulses: list[tuple[int, int]] = [(1, WRAPPER_US), (0, WRAPPER_US)]
    for position in range(N_BITS - 1, -1, -1):
        pulses.append(((value >> position) & 1, BIT_SLOT_US))
    pulses.append((0, WRAPPER_US))
    pulses.append((1, WRAPPER_US))
    return pulses


def edges_from_samples(
    trace: Sequence[int], fs_hz: float, t0_us: int = 0
) -> list[tuple[int, int]]:
    """Convert a sampled digital trace into (tick_us, level) transitions."""
    edges: list[tuple[int, int]] = []
    previous: int | None = None
    for index, sample in enumerate(trace):
        level = 1 if sample else 0
        if level != previous:
            edges.append((t0_us + round(index / fs_hz * 1_000_000), level))
            previous = level
    return edges


def _level_at(edges: Sequence[tuple[int, int]], tick_us: int) -> int:
    level = 0
    for edge_tick, edge_level in edges:
        if edge_tick > tick_us:
            break
        level = edge_level
    return level


def decode_edges(
    edges: Sequence[tuple[int, int]], start_us: int | None = None
) -> list[Barcode]:
    """Decode complete frames. Partial and unverifiable frames are discarded.

    ``start_us`` declares when the record begins, with the line known LOW.
    Supplying it lets the *first* frame decode; without it that frame is skipped,
    because a line that starts LOW and never transitions produces no edge, so
    the preceding idle cannot be verified. The Pi always knows its own record
    start, and ``edges_from_samples`` synthesises an edge at ``t0``, so in
    practice only hand-built edge lists need this.
    """
    ordered = sorted(edges)
    if not ordered:
        return []

    barcodes: list[Barcode] = []
    last_falling: int | None = start_us

    for tick, level in ordered:
        if level == 0:
            last_falling = tick
            continue
        if last_falling is None or tick - last_falling < IDLE_MIN_US:
            continue

        start = tick
        if ordered[-1][0] < start + FRAME_US:
            continue

        if _level_at(ordered, start + _QUARTER) != 1:
            continue
        if _level_at(ordered, start + 3 * _QUARTER) != 1:
            continue
        if _level_at(ordered, start + WRAPPER_US + _QUARTER) != 0:
            continue
        if _level_at(ordered, start + WRAPPER_US + 3 * _QUARTER) != 0:
            continue

        data_start = start + 2 * WRAPPER_US
        value = 0
        for bit in range(N_BITS):
            centre = data_start + bit * BIT_SLOT_US + BIT_SLOT_US // 2
            value = (value << 1) | _level_at(ordered, centre)

        gap2 = data_start + N_BITS * BIT_SLOT_US
        if _level_at(ordered, gap2 + WRAPPER_US // 2) != 0:
            continue
        if _level_at(ordered, gap2 + WRAPPER_US + WRAPPER_US // 2) != 1:
            continue

        barcodes.append(Barcode(value=value, start_us=start))
        last_falling = None
    return barcodes
