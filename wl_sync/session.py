"""Session identity, minted by the sync box at session start.

The sync box owns this format because it is the thing that creates it.
Everything downstream — the rig directory layout, the ELN, wl-preproc — consumes it.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

_SESSION_ID_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})$")


@dataclass(frozen=True, slots=True)
class SessionId:
    date: datetime.date
    index: int

    @classmethod
    def parse(cls, text: str) -> SessionId:
        match = _SESSION_ID_RE.match(text)
        if match is None:
            raise ValueError(f"malformed session id: {text!r}, expected YYYY-MM-DD_NN")
        year, month, day, index = (int(group) for group in match.groups())
        return cls(datetime.date(year, month, day), index)

    def __str__(self) -> str:
        return f"{self.date.isoformat()}_{self.index:02d}"
