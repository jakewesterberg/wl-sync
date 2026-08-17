from wl_sync.pins import (
    BARCODE_PIN,
    CODE_DATA_BASE,
    CODE_DATA_COUNT,
    CODE_STROBE_PIN,
    EDGE_PINS,
    FRAME_TIME_GPIO,
    GPIO_MAP,
)


def test_capture_window_is_contiguous_and_starts_at_zero():
    """PIO parallel capture reads a contiguous range; spec Sec.4 pins it at GPIO0-16."""
    assert CODE_DATA_BASE == 0
    assert CODE_DATA_COUNT == 16
    assert CODE_STROBE_PIN == CODE_DATA_BASE + CODE_DATA_COUNT


def test_barcode_is_gpio17():
    assert BARCODE_PIN == 17


def test_edge_pins_are_the_inputs_outside_the_capture_window():
    assert EDGE_PINS == (20, 21, 22, 23, 24, 25, 26, 27)


def test_frame_time_pins_match_the_board():
    assert FRAME_TIME_GPIO == {"cam_frame_eye": 26, "cam_frame_beh": 27}


def test_gpio_map_pins_every_name_to_its_exact_pin():
    """A whole-dict assertion, not a membership check: transposing two names within
    the same set of pins is the one mismapping a looser test would pass through, and
    it is the exact failure this module exists to prevent."""
    assert GPIO_MAP == {
        **{f"evt_d{i}": i for i in range(16)},
        "evt_strobe": 16,
        "barcode_out": 17,
        "pd1_comp": 20,
        "pd2_comp": 21,
        "rwd_cmd": 22,
        "rwd_dlvr": 23,
        "stim_trig": 24,
        "acc_trig": 25,
        "cam_frame_eye": 26,
        "cam_frame_beh": 27,
    }


def test_gpio_map_has_no_duplicate_pins():
    assert len(set(GPIO_MAP.values())) == len(GPIO_MAP)
