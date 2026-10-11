"""Reader scope (07 §4 "never"): a watcher cannot read the watched line's audit; the owner can; the counts
match the rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tower_audit import AuditAccessDenied, append, list_for_line, recent_checks
from tower_consent import Store, grant

from .conftest import NOW, OWNER, WATCHER, RecordFactory

pytestmark = pytest.mark.integration

ALERTS = {"actor_user_id": "system:alerts", "tool": "alert", "source": None}


def seed(store: Store, bound_line: str, make_record: RecordFactory) -> None:
    """A week on Mom's line. Rows are appended in time order (the writer never lets a row sort earlier)."""
    grant(store, bound_line, WATCHER, "watch", "mom", granted_by=OWNER, now=NOW)
    records = [make_record(day * 1440, **ALERTS, trigger="poll") for day in range(7)]  # daily check x7
    records += [
        make_record(600, actor_user_id=WATCHER),  # Asish checked twice; both times fine
        make_record(2000, actor_user_id=WATCHER, source="watch"),
        make_record(
            3000,
            **ALERTS,
            trigger="event",
            outcome="changed",
            reason_codes=["SIM_SWAPPED_RECENT"],
            message_ref="SIM_SWAPPED_RECENT.sms",
        ),
        make_record(
            4000, **ALERTS, trigger="event", outcome="suppressed", reason_codes=["SUPPRESSED_REVOKED"]
        ),
        make_record(4100, tool="watch_line", actor_user_id=OWNER, source=None),  # not a check
        # the last check has seconds, so last_at's rounding shows
        make_record(0, ts=NOW + timedelta(minutes=9000, seconds=42), **ALERTS, trigger="poll"),
    ]
    for r in sorted(records, key=lambda r: r.ts):
        append(store, r)


def test_watcher_cannot_list_the_watched_line(
    store: Store, bound_line: str, make_record: RecordFactory
) -> None:
    seed(store, bound_line, make_record)
    with pytest.raises(AuditAccessDenied):
        list_for_line(store, bound_line, WATCHER)
    with pytest.raises(AuditAccessDenied):
        recent_checks(store, bound_line, NOW, viewer_user_id=WATCHER)


def test_unknown_line_is_the_same_refusal(store: Store, line_id: str) -> None:
    with pytest.raises(AuditAccessDenied):
        list_for_line(store, line_id, OWNER)  # not bound at all: no oracle


def test_owner_lists_everything_newest_first(
    store: Store, bound_line: str, make_record: RecordFactory
) -> None:
    seed(store, bound_line, make_record)
    rows = list_for_line(store, bound_line, OWNER)
    assert len(rows) == 13
    assert [r.ts_seq for r in rows] == sorted((r.ts_seq for r in rows), reverse=True)


def test_recent_checks_aggregates(store: Store, bound_line: str, make_record: RecordFactory) -> None:
    seed(store, bound_line, make_record)
    rc = recent_checks(store, bound_line, NOW - timedelta(minutes=1), viewer_user_id=OWNER)
    assert rc.by_actor == {"system:alerts": 10, WATCHER: 2}
    assert rc.outcomes == {"ok": 10, "changed": 1, "refused": 0, "suppressed": 1}
    assert sum(rc.by_actor.values()) == sum(rc.outcomes.values())
    assert rc.last_at == (NOW + timedelta(minutes=9000)).replace(second=0)
    assert rc.last_at is not None and rc.last_at.second == 0 and rc.last_at.microsecond == 0


def test_recent_checks_since(store: Store, bound_line: str, make_record: RecordFactory) -> None:
    seed(store, bound_line, make_record)
    rc = recent_checks(store, bound_line, NOW + timedelta(minutes=2500), viewer_user_id=OWNER)
    assert rc.by_actor == {
        "system:alerts": 8
    }  # polls on days 2-6, the alert, the suppressed row, the last poll
    assert rc.outcomes["suppressed"] == 1 and rc.outcomes["changed"] == 1


def test_recent_checks_empty(store: Store, bound_line: str) -> None:
    rc = recent_checks(store, bound_line, datetime(2026, 1, 1, tzinfo=UTC), viewer_user_id=OWNER)
    assert rc.by_actor == {} and rc.last_at is None and set(rc.outcomes.values()) == {0}
