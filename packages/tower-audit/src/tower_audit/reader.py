"""Reading the log — only ever by the line's owner (07 §4).

Both public readers take `viewer_user_id` and refuse (`AuditAccessDenied`) unless that user owns the line in
`Lines`. A watcher never reads the audit of the line they watch, not even as counts: "who else checked" is the
owner's to know. The check is here, not in callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from tower_consent import Store, get_line
from tower_consent import tables as T

from tower_audit.chain import HEAD_SK
from tower_audit.errors import AuditAccessDenied
from tower_audit.record import AuditRecord, format_ts

CHECK_TOOLS: frozenset[str] = frozenset({"line_is_ok", "is_reachable", "alert"})
"""Rows that count as a "check" in `recent_checks`. `watch_line` (a state change or a status question, never a
question to the carrier — 02 §2) is not a check; otherwise asking "who checked my line?" would count itself."""


class RecentChecks(BaseModel):
    """`watch_line(enable=null)`'s `facts.recent_checks` (02 §2, 07 §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    by_actor: dict[str, int]
    outcomes: dict[str, int]
    last_at: datetime | None  # floored to the minute; phrasing decides how to say it


def query_items(
    store: Store, line_id: str, *, since: datetime | None = None, until: datetime | None = None
) -> list[dict[str, Any]]:
    """Raw items of one line in chain (SK) order, excluding the head item. No ownership check: internal."""
    lo = format_ts(since) if since else "0"
    hi = format_ts(until) + "#~" if until else HEAD_SK[:1]
    items: list[dict[str, Any]] = store.query(
        T.AUDIT,
        "line_id = :l AND ts_seq BETWEEN :lo AND :hi",
        {":l": line_id, ":lo": lo, ":hi": hi},
    )
    return [i for i in items if i.get("ts_seq") != HEAD_SK]


def query_rows(
    store: Store, line_id: str, *, since: datetime | None = None, until: datetime | None = None
) -> list[AuditRecord]:
    """`query_items` parsed into records (raises `pydantic.ValidationError` on a malformed row)."""
    return [AuditRecord.from_item(i) for i in query_items(store, line_id, since=since, until=until)]


def _require_owner(store: Store, line_id: str, viewer_user_id: str) -> None:
    line = get_line(store, line_id)
    if line is None or line.owner_user_id != viewer_user_id:
        raise AuditAccessDenied("only the line's owner can read its audit log")


def list_for_line(store: Store, line_id: str, viewer_user_id: str) -> list[AuditRecord]:
    """Every row of the line, newest first, for the owner's binding page. Anyone else: `AuditAccessDenied`."""
    _require_owner(store, line_id, viewer_user_id)
    return list(reversed(query_rows(store, line_id)))


def recent_checks(store: Store, line_id: str, since: datetime, *, viewer_user_id: str) -> RecentChecks:
    """Counts by actor and by outcome of the checks since `since`, and the last one's time to the minute."""
    _require_owner(store, line_id, viewer_user_id)
    by_actor: dict[str, int] = {}
    outcomes = {"ok": 0, "changed": 0, "refused": 0, "suppressed": 0}
    last: datetime | None = None
    for row in query_rows(store, line_id, since=since):
        if row.tool not in CHECK_TOOLS:
            continue
        by_actor[row.actor_user_id] = by_actor.get(row.actor_user_id, 0) + 1
        outcomes[row.outcome] += 1
        last = row.ts if last is None or row.ts > last else last
    last_at = last.replace(second=0, microsecond=0) if last else None
    return RecentChecks(by_actor=by_actor, outcomes=outcomes, last_at=last_at)
