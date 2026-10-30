import datetime

import pytest

from wl_sync.session import SessionId


def test_round_trip():
    sid = SessionId.parse("2027-03-14_01")
    assert sid.date == datetime.date(2027, 3, 14)
    assert sid.index == 1
    assert str(sid) == "2027-03-14_01"


def test_index_is_zero_padded():
    assert str(SessionId(datetime.date(2027, 3, 14), 1)) == "2027-03-14_01"


@pytest.mark.parametrize(
    "bad",
    ["2027-03-14", "2027-03-14_1", "27-03-14_01", "2027-13-01_01", "2027-03-14_01_x", ""],
)
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        SessionId.parse(bad)


@pytest.mark.parametrize(
    "lenient",
    [
        "2027-03-14_01\n",  # `$` matches before a final newline
        "٢٠٢٧-03-14_01",  # Arabic-Indic digits: `\d` takes any Nd
        "2027-03-14_０１",  # fullwidth digits
    ],
)
def test_rejects_text_that_would_not_print_back_as_itself(lenient):
    """Only a canonical id parses, so `str(SessionId.parse(text)) == text` for every
    text that parses. wl-preproc takes SessionId from here and agreed to the tightening
    (its answers of 2026-10-22, item 2)."""
    with pytest.raises(ValueError):
        SessionId.parse(lenient)
