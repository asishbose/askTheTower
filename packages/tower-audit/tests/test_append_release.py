"""Append-before-release (07 §6, 02 §6 last row): no row ⇒ no response; row ⇒ response.

The "crash" is a BaseException raised from the hook that runs after the append and before the response is
built — the moment a process kill would land. "Restart" is a fresh Store over the same tables.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError
from tower_audit import AuditRecord, AuditWriteFailed, append, release, verify
from tower_audit import writer as writer_mod
from tower_audit.reader import query_rows
from tower_consent import Store

from .conftest import NOW, RecordFactory

pytestmark = pytest.mark.integration


class SimulatedCrash(BaseException):
    """Not an Exception: nothing between the hook and the caller may catch it."""


def tool_call(
    store: Store, record: AuditRecord, responses: list[dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    """A minimal tool handler with Tower's shape: decide → audit → respond."""

    def respond(row: AuditRecord) -> dict[str, Any]:
        result = {"reason_codes": [c.value for c in row.reason_codes], "checked_at": row.ts.isoformat()}
        responses.append(result)
        return result

    return release(store, record, respond, **kw)


def restart(store: Store) -> Store:
    return Store(store.client, prefix=store.prefix)


def test_happy_path_row_then_response(store: Store, make_record: RecordFactory, signer: Any) -> None:
    responses: list[dict[str, Any]] = []
    result = tool_call(store, make_record(), responses)
    assert responses == [result]
    rows = query_rows(restart(store), make_record().line_id)
    assert len(rows) == 1 and rows[0].prev_hash == "genesis"
    assert verify(store, rows[0].line_id, signer).ok


def test_crash_between_decision_and_response(store: Store, make_record: RecordFactory) -> None:
    responses: list[dict[str, Any]] = []

    def crash(_: AuditRecord) -> None:
        raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        tool_call(store, make_record(), responses, after_append=crash)
    assert responses == []  # no response object was produced
    rows = query_rows(restart(store), make_record().line_id)  # on restart, the row exists
    assert len(rows) == 1 and rows[0].outcome == "ok"


def test_failed_write_means_no_response(store: Store, make_record: RecordFactory) -> None:
    responses: list[dict[str, Any]] = []

    def down(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb")

    store.client.meta.events.register("before-call.dynamodb.TransactWriteItems", down)
    try:
        with pytest.raises(AuditWriteFailed):
            tool_call(store, make_record(), responses)
    finally:
        store.client.meta.events.unregister("before-call.dynamodb.TransactWriteItems", down)
    assert responses == []
    assert query_rows(restart(store), make_record().line_id) == []


def test_unreachable_store_on_head_read_is_audit_write_failed(
    store: Store, make_record: RecordFactory
) -> None:
    def down(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb")

    store.client.meta.events.register("before-call.dynamodb.GetItem", down)
    try:
        with pytest.raises(AuditWriteFailed):
            append(store, make_record())
    finally:
        store.client.meta.events.unregister("before-call.dynamodb.GetItem", down)


def test_same_timestamp_takes_next_seq(store: Store, make_record: RecordFactory, signer: Any) -> None:
    a = append(store, make_record(0))
    b = append(store, make_record(0))
    assert (a.seq, b.seq) == (0, 1) and a.ts == b.ts
    assert verify(store, a.line_id, signer).ok


def test_clock_behind_head_never_sorts_before_it(
    store: Store, make_record: RecordFactory, signer: Any
) -> None:
    a = append(store, make_record(5))
    b = append(store, make_record(1))  # e.g. Alerts' clock a little behind Tower's
    assert b.ts_seq > a.ts_seq and b.ts == a.ts and b.seq == 1
    assert verify(store, a.line_id, signer).ok


def test_concurrent_append_collides_once_then_retries(
    store: Store, make_record: RecordFactory, signer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_read_head = writer_mod.read_head
    calls = {"n": 0}

    def racing_read_head(s: Store, line_id: str) -> Any:
        head = real_read_head(s, line_id)
        calls["n"] += 1
        if calls["n"] == 1:  # another writer appends between our read and our write
            monkeypatch.setattr(writer_mod, "read_head", real_read_head)
            append(
                s, make_record(1, actor_user_id="system:alerts", tool="alert", trigger="poll", source=None)
            )
            monkeypatch.setattr(writer_mod, "read_head", racing_read_head)
        return head

    append(store, make_record(0))
    monkeypatch.setattr(writer_mod, "read_head", racing_read_head)
    row = append(store, make_record(2))
    rows = query_rows(store, row.line_id)
    assert [r.actor_user_id for r in rows] == ["user-asish", "system:alerts", "user-asish"]
    assert calls["n"] == 2  # one retry
    assert verify(store, row.line_id, signer).ok  # no fork


def test_second_collision_raises(
    store: Store, make_record: RecordFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_read_head = writer_mod.read_head
    other = {"i": 10}

    def always_racing(s: Store, line_id: str) -> Any:
        head = real_read_head(s, line_id)
        monkeypatch.setattr(writer_mod, "read_head", real_read_head)
        other["i"] += 1
        append(s, make_record(other["i"]))
        monkeypatch.setattr(writer_mod, "read_head", always_racing)
        return head

    append(store, make_record(0))
    monkeypatch.setattr(writer_mod, "read_head", always_racing)
    with pytest.raises(AuditWriteFailed):
        append(store, make_record(1))


def test_append_does_not_swallow(store: Store, make_record: RecordFactory) -> None:
    """release() lets AuditWriteFailed through to the caller; nothing in the package catches it."""

    def boom(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb")

    store.client.meta.events.register("before-call.dynamodb.TransactWriteItems", boom)
    try:
        with pytest.raises(AuditWriteFailed):
            release(store, make_record(), lambda r: r)
    finally:
        store.client.meta.events.unregister("before-call.dynamodb.TransactWriteItems", boom)


def test_ttl_is_ninety_days(store: Store, make_record: RecordFactory) -> None:
    row = append(store, make_record())
    assert row.ttl == int((NOW + timedelta(days=90)).timestamp())
