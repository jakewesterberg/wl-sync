"""Session ids, session folders, and the saved open session."""

from __future__ import annotations

import datetime
import json
import logging

import pytest

from wl_sync.session import SessionId
from wl_sync.sessions import (
    SESSION_STATE_NAME,
    SessionsExhausted,
    WrongSession,
    close_session,
    next_id,
    open_session,
    read_open,
    session_dir,
    unassigned_dir,
)

DAY = datetime.date(2026, 10, 18)
NOW = datetime.datetime(2026, 10, 18, 9, 30, tzinfo=datetime.timezone.utc)


def sid(text: str) -> SessionId:
    return SessionId.parse(text)


def test_session_and_unassigned_folders_are_laid_out_as_wl_preproc_reads_them(tmp_path):
    assert session_dir(tmp_path, sid("2026-10-18_02")) == (
        tmp_path / "2026-10-18_02" / "syncbox"
    )
    assert unassigned_dir(tmp_path, DAY) == (
        tmp_path / "unassigned" / "2026-10-18" / "syncbox"
    )


def test_the_first_id_of_a_day_is_01(tmp_path):
    assert next_id(tmp_path, DAY) == sid("2026-10-18_01")


def test_ids_count_every_folder_already_used_that_day(tmp_path):
    for name in ("2026-10-18_01", "2026-10-18_03", "2026-10-17_07"):
        (tmp_path / name).mkdir()
    assert next_id(tmp_path, DAY) == sid("2026-10-18_04")


def test_the_unassigned_folder_never_counts_as_a_session(tmp_path):
    (tmp_path / "unassigned" / "2026-10-18").mkdir(parents=True)
    assert next_id(tmp_path, DAY) == sid("2026-10-18_01")


def test_the_100th_open_of_a_day_is_refused(tmp_path):
    (tmp_path / "2026-10-18_99").mkdir()
    with pytest.raises(SessionsExhausted):
        next_id(tmp_path, DAY)


def test_opening_creates_the_folder_before_saving_state(tmp_path, monkeypatch):
    opened = open_session(tmp_path, "r1", NOW, DAY)
    assert (tmp_path / str(opened.session.session_id)).is_dir()
    assert (tmp_path / SESSION_STATE_NAME).is_file()
    assert read_open(tmp_path).session_id == opened.session.session_id

    # The order: a failure while saving must leave the id spent (folder present) and
    # no state behind, so the next open cannot reissue it.
    other = tmp_path / "other"
    other.mkdir()

    def fail(root, session):
        raise OSError("disk full")

    monkeypatch.setattr("wl_sync.sessions.write_open", fail)
    with pytest.raises(OSError):
        open_session(other, "r1", NOW, DAY)
    assert (other / "2026-10-18_01").is_dir()
    assert read_open(other) is None


def test_reopening_with_the_same_request_id_returns_the_same_session(tmp_path):
    first = open_session(tmp_path, "r1", NOW, DAY)
    second = open_session(tmp_path, "r1", NOW, DAY)
    assert second.reused is True
    assert second.session.session_id == first.session.session_id
    assert second.closed is None
    assert not (tmp_path / "2026-10-18_02").exists()


def test_an_open_while_another_is_open_reports_the_one_it_closed(tmp_path):
    first = open_session(tmp_path, "r1", NOW, DAY)
    second = open_session(tmp_path, "r2", NOW, DAY)
    assert second.closed == first.session.session_id
    assert second.reused is False
    assert read_open(tmp_path) == second.session


def test_a_close_for_another_session_is_refused(tmp_path):
    first = open_session(tmp_path, "r1", NOW, DAY)
    with pytest.raises(WrongSession) as raised:
        close_session(tmp_path, sid("2026-10-18_02"))
    assert raised.value.open == first.session.session_id
    assert read_open(tmp_path) == first.session


def test_a_close_with_nothing_open_is_refused(tmp_path):
    with pytest.raises(WrongSession) as raised:
        close_session(tmp_path, sid("2026-10-18_01"))
    assert raised.value.open is None


def test_closing_clears_the_saved_session(tmp_path):
    opened = open_session(tmp_path, "r1", NOW, DAY)
    assert close_session(tmp_path, opened.session.session_id) == (
        opened.session.session_id
    )
    assert read_open(tmp_path) is None


GOOD = {
    "session_id": "2026-10-18_01",
    "opened_at": "2026-10-18T09:30:00+00:00",
    "request_id": "r1",
}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        json.dumps({k: v for k, v in GOOD.items() if k != "request_id"}),
        json.dumps({**GOOD, "session_id": "not-an-id"}),
        json.dumps({**GOOD, "opened_at": "2026-10-18T09:30:00"}),
    ],
    ids=["invalid-json", "not-an-object", "missing-key", "bad-id", "naive-time"],
)
def test_a_corrupt_state_file_reads_as_no_session_open(tmp_path, caplog, content):
    """Closes a coverage gap in existing code: each refusal branch of read_open.

    These cases passed when written, because read_open already handled them; they pin
    the behaviour so a later edit cannot turn a torn file into a startup crash.
    """
    (tmp_path / SESSION_STATE_NAME).write_text(content)
    with caplog.at_level(logging.ERROR, logger="wl_sync.sessions"):
        assert read_open(tmp_path) is None
    assert any(
        record.name == "wl_sync.sessions" and record.levelno == logging.ERROR
        for record in caplog.records
    )
