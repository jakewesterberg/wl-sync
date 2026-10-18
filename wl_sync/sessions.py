"""Session ids, session folders, and the saved open session.

The box mints one session id per animal's visit (`YYYY-MM-DD_NN`) when the task PC asks.
This module is the filesystem half of that: it decides what the next id is, makes the
folder an id names, and remembers which session is open across a restart. It does no
networking and touches no run loop, so all of it is testable on a laptop.

THE FOLDERS ARE THE COUNTER. There is no separate "last index used" file, because a
second record of the same fact is a second thing free to disagree with the first. The
next index is one more than the highest `YYYY-MM-DD_NN` folder already under the root,
so an id is spent the moment its folder exists. `open_session` makes the folder BEFORE
it saves state: a box that dies between the two has wasted an id, which is harmless,
where the opposite order could hand the same id to two animals.

THE UNASSIGNED FOLDER IS NOT A SESSION. Segments written while no session is open go
under `unassigned/<date>/`, which is a different shape from `<date>_NN` and is never
matched by the scan below. Were it counted, the first real session of a day would be
numbered by what happened before anyone asked for one.

THE SAVED SESSION SURVIVES A RESTART, and a corrupt one degrades. Under systemd
`Restart=always`, a raise at startup is a crash loop that records nothing at all;
reading a torn state file as "no session open" costs only that the next segments land
as unassigned, which wl-preproc can reconcile later.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wl_sync.session import SessionId

logger = logging.getLogger(__name__)

# Dot-prefixed so a directory listing of the data root shows sessions, not bookkeeping.
SESSION_STATE_NAME = ".wl-sync-session"

# The index is two digits in the id format, so the 100th open of a day has nowhere to go.
_MAX_INDEX = 99


def session_dir(root: Path, session_id: SessionId) -> Path:
    """Where a session's sync-box files go: `<root>/<id>/syncbox`.

    The `syncbox` level is the layout wl-preproc reads, one folder per recording device
    under the session.
    """
    return root / str(session_id) / "syncbox"


def unassigned_dir(root: Path, day: datetime.date) -> Path:
    """Where segments go while no session is open: `<root>/unassigned/<date>/syncbox`."""
    return root / "unassigned" / day.isoformat() / "syncbox"


def unassigned_id(day: datetime.date) -> SessionId:
    """The stand-in id for "no session open": index 0, which `next_id` never mints."""
    return SessionId(day, 0)


@dataclass(frozen=True, slots=True)
class OpenSession:
    """The session currently open, as saved to disk.

    `request_id` is what makes an open idempotent: the task PC retries a request whose
    reply it never saw, and the retry must get the same session back rather than
    silently closing it and minting another. `opened_at` must be timezone-aware so the
    saved text carries its offset and means the same instant wherever it is read.
    """

    session_id: SessionId
    opened_at: datetime.datetime
    request_id: str


def read_open(root: Path) -> OpenSession | None:
    """The saved open session, or None if there is none or it cannot be read.

    A missing file is the ordinary "nothing open". An UNREADABLE one -- invalid JSON,
    missing keys, a bad id -- is also None, but logged at ERROR because it is not
    ordinary: a torn write or a hand edit lost a session. It must not raise, for the
    crash-loop reason in the module docstring.
    """
    path = root / SESSION_STATE_NAME
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("cannot read saved session %s; treating as none open", path)
        return None
    try:
        data = json.loads(text)
        opened_at = datetime.datetime.fromisoformat(data["opened_at"])
        if opened_at.tzinfo is None:
            raise ValueError("opened_at has no timezone")
        return OpenSession(
            session_id=SessionId.parse(data["session_id"]),
            opened_at=opened_at,
            request_id=str(data["request_id"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.exception("saved session %s is corrupt; treating as none open", path)
        return None


def write_open(root: Path, session: OpenSession) -> None:
    """Save the open session atomically AND durably, as `clock.write_checkpoint` does.

    Atomic -- temp file plus rename -- so a crash mid-write cannot leave a truncated
    file that reads as a different session. Durable -- fsync the file, then the
    DIRECTORY -- so power loss cannot bring the box back with the rename lost and the
    previous animal's session still open.
    """
    path = root / SESSION_STATE_NAME
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "session_id": str(session.session_id),
        "opened_at": session.opened_at.isoformat(),
        "request_id": session.request_id,
    }
    with open(temporary, "w") as handle:
        handle.write(json.dumps(payload) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def clear_open(root: Path) -> None:
    """Forget the open session. Absent already is fine: a close is idempotent here."""
    try:
        (root / SESSION_STATE_NAME).unlink()
    except FileNotFoundError:
        return
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class SessionError(Exception):
    """Base of the refusals a session request can meet."""


class SessionsExhausted(SessionError):
    """The day already has `_99`; the two-digit index has no next value."""


class WrongSession(SessionError):
    """A close named a session that is not the open one.

    Carries `.open`, the session actually open (or None), so the reply can tell the
    task PC what the box believes instead of leaving it to guess.
    """

    def __init__(self, open: SessionId | None):
        super().__init__(f"open session is {open}" if open else "no session is open")
        self.open = open


def next_id(root: Path, day: datetime.date) -> SessionId:
    """One more than the highest `<day>_NN` folder under root, or `_01` if none.

    Highest-plus-one, not a count: if `_02` was deleted by hand, counting would reissue
    `_03`'s number. Names that do not parse are ignored, so stray files never block a
    visit. Raises SessionsExhausted rather than minting `_100`, which would not parse.
    """
    highest = 0
    for path in root.glob(f"{day.isoformat()}_??"):
        try:
            highest = max(highest, SessionId.parse(path.name).index)
        except ValueError:
            continue
    if highest >= _MAX_INDEX:
        raise SessionsExhausted(f"{day.isoformat()} already has {_MAX_INDEX} sessions")
    return SessionId(day, highest + 1)


@dataclass(frozen=True, slots=True)
class Opened:
    """What an open did: the session now open, the one it displaced, and whether it was a retry."""

    session: OpenSession
    closed: SessionId | None
    reused: bool


def open_session(
    root: Path, request_id: str, now: datetime.datetime, day: datetime.date
) -> Opened:
    """Open a session for this request, closing any other that is open.

    The same `request_id` as the open session is a retry and returns it unchanged
    (`reused=True`, `closed=None`): minting again would split one visit into two.
    Otherwise the folder is made first, then the state saved -- see the module
    docstring for why that order.
    """
    current = read_open(root)
    if current is not None and current.request_id == request_id:
        return Opened(current, None, True)
    session_id = next_id(root, day)
    (root / str(session_id)).mkdir(parents=True, exist_ok=True)
    session = OpenSession(session_id, now, request_id)
    write_open(root, session)
    return Opened(session, current.session_id if current else None, False)


def close_session(root: Path, session_id: SessionId) -> SessionId:
    """Close the open session if it is `session_id`, else raise WrongSession.

    Refusing a mismatch matters because a late close for the previous animal must not
    end the current one's recording.
    """
    current = read_open(root)
    if current is None or current.session_id != session_id:
        raise WrongSession(current.session_id if current else None)
    clear_open(root)
    return session_id


@dataclass
class SessionRequest:
    """A request handed from the endpoint to the run loop.

    For an open, `argument` is the request_id; for a close, the session id string. It
    lives here because both cli.py and endpoint.py use it and cli.py imports endpoint.py.
    """

    kind: Literal["open", "close"]
    argument: str
    reply: Future
