"""Trim re-anchors the chain (07 §5): append 10; trim before row 6; verify ok with trimmed=True."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

import pytest
from tower_audit import AuditRecord, HmacMarkerSigner, append, trim, trim_horizon, verify
from tower_audit.chain import parse_marker
from tower_audit.reader import query_rows
from tower_consent import Store
from tower_consent import tables as T

from .conftest import NOW, RecordFactory

pytestmark = pytest.mark.integration

LATER = NOW + timedelta(days=1)


def ten(store: Store, make_record: RecordFactory) -> list[AuditRecord]:
    return [append(store, make_record(i)) for i in range(10)]


def test_trim_before_row_6(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = ten(store, make_record)
    res = trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER)
    assert res is not None and res.anchor_ts == rows[5].ts and res.rows_before == 5
    anchored = query_rows(store, rows[0].line_id)[5]
    marker = parse_marker(anchored.prev_hash or "")
    assert marker is not None and marker.original_prev_hash == rows[5].prev_hash
    assert verify(store, rows[0].line_id, signer).model_dump() == {
        "ok": True,
        "rows": 5,
        "trimmed": True,
        "first_bad_ts": None,
    }


def test_ttl_deletes_trimmed_rows_in_any_order(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = ten(store, make_record)
    trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER)
    old = rows[:5]
    random.Random(7).shuffle(old)  # noqa: S311 - test ordering, not crypto
    for r in old:  # what DynamoDB TTL will do, lazily and unordered
        store.delete(T.AUDIT, {"line_id": r.line_id, "ts_seq": r.ts_seq})
        assert verify(store, r.line_id, signer).ok
    res = verify(store, rows[0].line_id, signer)
    assert res.ok and res.trimmed and res.rows == 5


def test_append_after_trim_still_verifies(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = ten(store, make_record)
    trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER)
    append(store, make_record(20))
    res = verify(store, rows[0].line_id, signer)
    assert res.ok and res.rows == 6


def test_tamper_after_trim_still_named(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = ten(store, make_record)
    trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER)
    store.update(
        T.AUDIT,
        {"line_id": rows[7].line_id, "ts_seq": rows[7].ts_seq},
        "SET outcome = :o",
        values={":o": "refused"},
    )
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[7].ts


def test_marker_signed_with_another_key_is_rejected(
    store: Store, make_record: RecordFactory, signer: Any
) -> None:
    rows = ten(store, make_record)
    forger = HmacMarkerSigner(b"x" * 32)
    trim(store, rows[0].line_id, rows[5].ts, signer=forger, now=LATER)
    for r in rows[:5]:
        store.delete(T.AUDIT, {"line_id": r.line_id, "ts_seq": r.ts_seq})
    res = verify(store, rows[0].line_id, signer)
    assert not res.ok and res.first_bad_ts == rows[5].ts


def test_nothing_to_trim(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = ten(store, make_record)
    assert trim(store, rows[0].line_id, rows[0].ts, signer=signer, now=LATER) is None
    assert trim(store, rows[0].line_id, NOW + timedelta(days=5), signer=signer, now=LATER) is None
    assert verify(store, rows[0].line_id, signer).trimmed is False


def test_trim_twice_is_idempotent_and_later_trim_moves_anchor(
    store: Store, make_record: RecordFactory, signer: Any
) -> None:
    rows = ten(store, make_record)
    trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER)
    trim(store, rows[0].line_id, rows[5].ts, signer=signer, now=LATER + timedelta(hours=1))
    assert verify(store, rows[0].line_id, signer).rows == 5
    trim(store, rows[0].line_id, rows[8].ts, signer=signer, now=LATER + timedelta(days=1))
    res = verify(store, rows[0].line_id, signer)
    assert res.ok and res.trimmed and res.rows == 2


def test_chain_restarts_after_everything_expired(
    store: Store, make_record: RecordFactory, signer: Any
) -> None:
    rows = [append(store, make_record(i)) for i in range(3)]
    head_key = {"line_id": rows[0].line_id, "ts_seq": "~head"}
    store.delete(T.AUDIT, head_key)  # TTL took the head (same ttl as the newest row) before the rows
    fresh = append(store, make_record(0, ts=NOW + timedelta(days=91)))
    assert fresh.prev_hash == "genesis"
    res = verify(store, rows[0].line_id, signer)
    assert res.ok and res.trimmed and res.rows == 1


def test_trim_horizon_is_one_day_ahead_of_ttl() -> None:
    assert trim_horizon(NOW) == NOW - timedelta(days=89)
