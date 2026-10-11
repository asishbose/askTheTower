"""The sent-SMS ledger behind `GET /internal/sent` (06 §3.1; doc 11 G3, G7; U1/U4 2026-10-11).

Every SMS that `send.send_one` delivers is recorded through the `SentLog` Protocol with its template id (the
`message_ref`), the rendered body, the recipient's role and user id. The number is not a field: nothing here
can return it. Entries are numbered from 1 so a reader asks only for what is new (`after=<n>`).

| Implementation | Chosen by `runner.build_service` when | Store |
|---|---|---|
| `MemorySentLog` | `ALERTS_MODE=local` | in memory, last 200 |
| `DynamoSentLog` | `lambda`/`k8s` with `ALERTS_ADMIN=1` (the demo admin path is open, 10 §2.1) | table `SentSms` |
| `NullSentLog` | `lambda`/`k8s` without `ALERTS_ADMIN` | nothing |
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from tower_consent import Store
from tower_consent.models import format_ts
from tower_consent.store import from_av
from tower_consent.tables import Key, Table

from alerts.clock import Wall

MAX_ENTRIES = 200
# SentSms is the cloud twin of the in-memory ledger for the demo window only (06 §3.1 "Why gated"): 24 h.
SENT_TTL = timedelta(hours=24)
SENT_PK = "sent"

SENT_SMS = Table(name="SentSms", hash_key=Key("pk"), range_key=Key("n", "N"), ttl_attribute="expires_at")


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


class SentLog(Protocol):
    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry: ...

    def after(self, n: int, limit: int = MAX_ENTRIES) -> list[SentEntry]:
        """Entries with `entry.n > n`, ascending, at most `limit`."""
        ...


class MemorySentLog:
    """Local mode: bounded, in process memory, gone on restart."""

    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        self._entries: deque[SentEntry] = deque(maxlen=maxlen)
        self._n = 0

    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry:
        self._n += 1
        entry = SentEntry(n=self._n, at=at, template=template, role=role, user_id=user_id, body=body)
        self._entries.append(entry)
        return entry

    def after(self, n: int, limit: int = MAX_ENTRIES) -> list[SentEntry]:
        return [e for e in self._entries if e.n > n][:limit]


class NullSentLog:
    """`lambda`/`k8s` without `ALERTS_ADMIN`: records nothing, so no list of bodies exists anywhere."""

    def __init__(self) -> None:
        self._n = 0

    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry:
        self._n += 1
        return SentEntry(n=self._n, at=at, template=template, role=role, user_id=user_id, body=body)

    def after(self, n: int, limit: int = MAX_ENTRIES) -> list[SentEntry]:
        return []


class DynamoSentLog:
    """`SentSms` (06 §7): counter row `{pk:"sent", n:0}` gives the next `n` (`ADD seq :1`), then one
    conditional `PutItem` per entry; `after` is one `Query pk = "sent" AND n > :n`, ascending.

    `at` is the (mock) time of the send; `expires_at` is `wall()` + 24 h, real time (06 §7 "Clocks")."""

    def __init__(self, store: Store, *, wall: Wall) -> None:
        self._client = store.client
        self._name = store.name(SENT_SMS)
        self._wall = wall

    def _next_n(self) -> int:
        resp = self._client.update_item(
            TableName=self._name,
            Key={"pk": {"S": SENT_PK}, "n": {"N": "0"}},
            UpdateExpression="ADD seq :one",
            ExpressionAttributeValues={":one": {"N": "1"}},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["seq"]["N"])

    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry:
        entry = SentEntry(n=self._next_n(), at=at, template=template, role=role, user_id=user_id, body=body)
        self._client.put_item(
            TableName=self._name,
            Item={
                "pk": {"S": SENT_PK},
                "n": {"N": str(entry.n)},
                "at": {"S": format_ts(at)},
                "template": {"S": template},
                "role": {"S": role},
                "user_id": {"S": user_id},
                "body": {"S": body},
                "expires_at": {"N": str(int((self._wall() + SENT_TTL).timestamp()))},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
        return entry

    def after(self, n: int, limit: int = MAX_ENTRIES) -> list[SentEntry]:
        # `n` is never negative (the route has ge=0), so the counter row (n = 0) can never match.
        resp = self._client.query(
            TableName=self._name,
            KeyConditionExpression="pk = :pk AND n > :after",
            ExpressionAttributeValues={":pk": {"S": SENT_PK}, ":after": {"N": str(max(0, n))}},
            ScanIndexForward=True,
            Limit=limit,
        )
        return [_entry(from_av(i)) for i in resp.get("Items", [])]


def _entry(item: dict[str, Any]) -> SentEntry:
    return SentEntry(
        n=int(item["n"]),
        at=datetime.fromisoformat(str(item["at"])),
        template=str(item["template"]),
        role=str(item["role"]),
        user_id=str(item["user_id"]),
        body=str(item["body"]),
    )


def terraform_definition(prefix: str = "") -> dict[str, Any]:
    """`SentSms` for `modules/dynamodb/generate.py`: created only while the demo admin path is open."""
    return {
        "name": SENT_SMS.physical_name(prefix),
        "billing_mode": "PAY_PER_REQUEST",
        "hash_key": "pk",
        "range_key": "n",
        "attributes": [{"name": "pk", "type": "S"}, {"name": "n", "type": "N"}],
        "global_secondary_indexes": [],
        "ttl": {"enabled": True, "attribute_name": "expires_at"},
        "point_in_time_recovery": False,
        "demo_admin_only": True,
    }
