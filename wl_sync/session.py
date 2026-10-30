"""Session identity, minted by the sync box at session start.

The sync box owns this format because it is the thing that creates it.
Everything downstream — the rig directory layout, the ELN, wl-preproc — consumes it.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

# fullmatch and [0-9], not match and \d: `$` matches before a final newline, and `\d`
# takes any Unicode decimal digit, which int() converts. Either let text through that
# prints back as a different string. Every id the box mints matches this already.
_SESSION_ID_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})_([0-9]{2})")


@dataclass(frozen=True, slots=True)
class SessionId:
    date: datetime.date
    index: int

    @classmethod
    def parse(cls, text: str) -> SessionId:
        match = _SESSION_ID_RE.fullmatch(text)
        if match is None:
            raise ValueError(f"malformed session id: {text!r}, expected YYYY-MM-DD_NN")
        year, month, day, index = (int(group) for group in match.groups())
        return cls(datetime.date(year, month, day), index)

    def __str__(self) -> str:
        return f"{self.date.isoformat()}_{self.index:02d}"
