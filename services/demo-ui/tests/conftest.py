"""Fixtures: a moto DynamoDB store with Asish's and Mom's lines bound and an audit row on each, and the app built
over fake transports (the mock, the binding page and Alerts), so every request the UI makes is recorded."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws
from tower_audit import AuditRecord, append
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, bind_line, ensure_user, tables

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
ASISH = "+16135550101"  # the 555-01xx fiction of scenarios/demo.yaml, in memory only
MOM = "+16135550102"
POLICY = "a" * 64


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    with mock_aws():  # a session-wide moto may already be active: own tables per test, dropped after
        client = boto3.client("dynamodb", region_name="us-east-1")
        s = Store(client, f"ui{uuid.uuid4().hex[:8]}-")
        s.ensure_tables()
        yield s
        for t in tables.TABLES:
            client.delete_table(TableName=s.name(t))


@pytest.fixture
def lines(store: Store) -> dict[str, str]:
    hasher, cipher = LocalLineIdHasher(b"k" * 32), LocalMsisdnCipher(b"m" * 32)
    out: dict[str, str] = {}
    for user, e164 in (("user-asish", ASISH), ("user-mom", MOM)):
        ensure_user(store, user, now=T0)
        out[user] = bind_line(store, hasher, cipher, user, e164, "auth_code", now=T0).line_id
    rows: list[dict[str, Any]] = [
        {"line_id": out["user-asish"], "actor_user_id": "user-asish", "tool": "line_is_ok", "outcome": "ok",
         "reason_codes": ("OK",), "trigger": "voice"},
        {"line_id": out["user-mom"], "actor_user_id": "user-asish", "tool": "line_is_ok", "outcome": "ok",
         "reason_codes": ("OK",), "trigger": "voice"},
        {"line_id": out["user-mom"], "actor_user_id": "system:alerts", "tool": "alert", "outcome": "suppressed",
         "reason_codes": ("SUPPRESSED_REVOKED",), "trigger": "event"},
    ]  # fmt: skip
    for i, row in enumerate(rows):
        append(store, AuditRecord(ts=T0 + timedelta(minutes=i), policy_version=POLICY, **row))
    return out
