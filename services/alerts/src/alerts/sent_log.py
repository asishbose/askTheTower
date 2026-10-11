"""The local sent-SMS ledger behind `GET /internal/sent` (06 §3.1; doc 11 §10 G3).

Built only with `ALERTS_MODE=local` (`runner.build_service`): every SMS that `send.send_one` delivers is recorded
with its template id (the `message_ref`), the rendered body, the recipient's role and user id. The number is not
a field: nothing here can return it. Bounded, in memory, numbered from 1 so a reader can ask for `after=<n>`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

MAX_ENTRIES = 200


@dataclass(frozen=True)
class SentEntry:
    n: int
    at: datetime
    template: str
    role: str
    user_id: str
    body: str

    def dump(self) -> dict[str, Any]:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d


class SentLog:
    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        self._entries: deque[SentEntry] = deque(maxlen=maxlen)
        self._n = 0

    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry:
        self._n += 1
        entry = SentEntry(n=self._n, at=at, template=template, role=role, user_id=user_id, body=body)
        self._entries.append(entry)
        return entry

    def after(self, n: int) -> list[SentEntry]:
        return [e for e in self._entries if e.n > n]
