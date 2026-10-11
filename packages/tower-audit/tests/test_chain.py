"""Chain verify: tamper one row → verification names it (07 §6)."""

from __future__ import annotations

from typing import Any

import pytest
from tower_audit import AuditRecord, append, verify
from tower_audit.chain import HEAD_SK
from tower_consent import Store
from tower_consent import tables as T

from .conftest import RecordFactory

pytestmark = pytest.mark.integration


def five(store: Store, make_record: RecordFactory) -> list[AuditRecord]:
    return [
        append(
            store,
            make_record(
                i, outcome="ok" if i % 2 else "changed", reason_codes=["OK"] if i % 2 else ["UNREACHABLE"]
            ),
        )
        for i in range(5)
    ]


def set_attr(store: Store, row: AuditRecord, attr: str, value: Any) -> None:
    store.update(
        T.AUDIT,
        {"line_id": row.line_id, "ts_seq": row.ts_seq},
        "SET #a = :v",
        names={"#a": attr},
        values={":v": value},
    )


def test_intact_chain_verifies(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    assert verify(store, rows[0].line_id, signer).model_dump() == {
        "ok": True,
        "rows": 5,
        "trimmed": False,
        "first_bad_ts": None,
    }


def test_tamper_row_3_content_names_row_3(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    set_attr(store, rows[2], "outcome", "ok")  # flip "changed" → "ok"
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[2].ts


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        ("actor_user_id", "user-someone-else"),
        ("reason_codes", ["OK"]),
        ("tool", "is_reachable"),
        ("ttl", 1),
        ("prev_hash", "a" * 64),
    ],
)
def test_any_field_tamper_on_row_3_names_row_3(
    store: Store, make_record: RecordFactory, signer: Any, attr: str, value: Any
) -> None:
    rows = five(store, make_record)
    set_attr(store, rows[2], attr, value)
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[2].ts


def test_malformed_tamper_names_row(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    set_attr(store, rows[2], "outcome", "nothing-to-see")
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[2].ts


def test_tamper_newest_row_is_caught_by_head(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    set_attr(store, rows[4], "outcome", "refused")
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[4].ts


def test_deleted_middle_row_breaks_chain(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    store.delete(T.AUDIT, {"line_id": rows[2].line_id, "ts_seq": rows[2].ts_seq})
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[1].ts  # the chain breaks right after row 2


def test_deleted_newest_row_breaks_chain(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = five(store, make_record)
    store.delete(T.AUDIT, {"line_id": rows[4].line_id, "ts_seq": rows[4].ts_seq})
    assert not verify(store, rows[0].line_id, signer).ok


def test_deleted_oldest_row_without_trim_is_caught(
    store: Store, make_record: RecordFactory, signer: Any
) -> None:
    rows = five(store, make_record)
    store.delete(T.AUDIT, {"line_id": rows[0].line_id, "ts_seq": rows[0].ts_seq})
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[1].ts


def test_chains_are_per_line(store: Store, make_record: RecordFactory, signer: Any, hasher: Any) -> None:
    other = hasher.line_id("+15555550188")
    rows = five(store, make_record)
    theirs = [append(store, make_record(i, line_id=other)) for i in range(3)]
    set_attr(store, theirs[1], "outcome", "refused")
    assert verify(store, rows[0].line_id, signer).ok
    assert not verify(store, other, signer).ok


def test_head_points_at_newest_row(store: Store, make_record: RecordFactory) -> None:
    rows = five(store, make_record)
    head = store.get(T.AUDIT, {"line_id": rows[0].line_id, "ts_seq": HEAD_SK})
    assert head is not None and head["last_ts_seq"] == rows[4].ts_seq and head["ttl"] == rows[4].ttl


def test_empty_line_verifies(store: Store, line_id: str, signer: Any) -> None:
    assert verify(store, line_id, signer).ok
