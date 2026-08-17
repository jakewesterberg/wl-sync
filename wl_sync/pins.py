"""The board's GPIO allocation, in one place.

Transcribed from spec Sec.4's own table. A bare pin number at a call site survives a
board change silently, which is this project's named recurring failure -- right about
the model, wrong about the board.
"""

from __future__ import annotations

CODE_DATA_BASE = 0
CODE_DATA_COUNT = 16
CODE_STROBE_PIN = 16
BARCODE_PIN = 17

# Camera frame-time inputs, one exposure strobe per camera group. These were spare
# until 2026-08-16; the cameras free-run, so this box records when they exposed rather
# than telling them when to. See hardware/breakout/frame-time-inputs.md.
FRAME_TIME_GPIO = {"cam_frame_eye": 26, "cam_frame_beh": 27}

GPIO_MAP: dict[str, int] = {
    **{f"evt_d{i}": CODE_DATA_BASE + i for i in range(CODE_DATA_COUNT)},
    "evt_strobe": CODE_STROBE_PIN,
    "barcode_out": BARCODE_PIN,
    "pd1_comp": 20,
    "pd2_comp": 21,
    "rwd_cmd": 22,
    "rwd_dlvr": 23,
    "stim_trig": 24,
    "acc_trig": 25,
    **FRAME_TIME_GPIO,
}

# Individually-captured input transitions: everything above the contiguous capture
# window. GPIO18/19 are spare.
EDGE_PINS: tuple[int, ...] = (20, 21, 22, 23, 24, 25, 26, 27)
