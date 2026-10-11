"""Fixtures for tower-audit: the six tables (from `tower_consent.tables`, not redefined) on **both** backends.

Same pattern as `packages/tower-consent/tests/conftest.py`, on the root conftest's session-wide `mock_aws()` (starting moto costs
tens of seconds on /mnt/c) with a random table prefix per test, and DynamoDB Local via testcontainers when
`docker info` succeeds (skipped with the reason otherwise). `TOWER_DDB_BACKENDS=moto` narrows the matrix.

Every `store` is swept on teardown: no string in any table may look like a phone number, and only the TTL
attribute may hold a 10+-digit number (07 §3, 00 privacy invariants).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tower_audit import AuditRecord, HmacMarkerSigner
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, bind_line, tables
from tower_policy import ReasonCode, policy_version

from tests.helpers import dynamo
from tests.privacy.patterns import phone_hits

# Real current minute: DynamoDB Local sweeps TTL against wall-clock time, so a fixed past NOW
# lets it delete rows mid-test.
NOW = datetime.now(UTC).replace(second=0, microsecond=0)
LINE_KEY = b"k" * 32
MSISDN_KEY = b"m" * 32
MOM = "+15555550123"
OWNER = "user-mom"
WATCHER = "user-asish"

_BACKENDS = dynamo.backends()


def pytest_report_header(config: pytest.Config) -> str:
    return f"tower-audit DynamoDB backends: {_BACKENDS}; docker available: {dynamo.docker_available()}"


@pytest.fixture(scope="session", autouse=True)
def _fake_aws_env(fake_aws_env: None) -> None:
    """moto / DynamoDB Local credentials for every test here (root conftest `fake_aws_env`)."""


def sweep_for_numbers(store: Store) -> dict[str, list[str]]:
    """Dump every table; return {table.attr: hits}. Empty dict = clean."""
    bad: dict[str, list[str]] = {}

    def walk(table: tables.Table, path: str, v: Any) -> None:
        if isinstance(v, str):
            hits = phone_hits(v)
            if hits:
                bad.setdefault(f"{table.name}.{path}", []).extend(hits)
        elif isinstance(v, bool):
            return
        elif isinstance(v, int | float):
            if len(str(int(v))) >= 10 and path != table.ttl_attribute:
                bad.setdefault(f"{table.name}.{path}", []).append(str(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                if phone_hits(str(k)):
                    bad.setdefault(f"{table.name}.{path}<key>", []).append(str(k))
                walk(table, f"{path}.{k}" if path else str(k), x)
        elif isinstance(v, list | set | tuple):
            for x in v:
                walk(table, f"{path}[]", x)

    for t in tables.TABLES:
        for item in store.scan_all(t):
            walk(t, "", item)
    return bad


@pytest.fixture(params=_BACKENDS)
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    backend = request.param
    client = dynamo.client_for(request, backend)
    s = Store(client, prefix=f"a{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    yield s
    leaks = sweep_for_numbers(s)
    for t in tables.TABLES:
        client.delete_table(TableName=s.name(t))
    assert not leaks, f"phone-number-shaped values in tables: {leaks}"


@pytest.fixture
def hasher() -> LocalLineIdHasher:
    return LocalLineIdHasher(LINE_KEY)


@pytest.fixture
def signer() -> HmacMarkerSigner:
    return HmacMarkerSigner(LINE_KEY)


@pytest.fixture
def line_id(hasher: LocalLineIdHasher) -> str:
    return hasher.line_id(MOM)


@pytest.fixture
def bound_line(store: Store, hasher: LocalLineIdHasher) -> str:
    """Mom's line, bound by Mom (the owner)."""
    line = bind_line(store, hasher, LocalMsisdnCipher(MSISDN_KEY), OWNER, MOM, "auth_code", now=NOW)
    return line.line_id


RecordFactory = Callable[..., AuditRecord]


@pytest.fixture
def make_record(line_id: str) -> RecordFactory:
    """`make_record(i)` → a voice line_is_ok OK row at NOW + i minutes; any field can be overridden."""

    def _make(i: int = 0, **overrides: Any) -> AuditRecord:
        data: dict[str, Any] = {
            "line_id": line_id,
            "ts": NOW + timedelta(minutes=i),
            "actor_user_id": WATCHER,
            "tool": "line_is_ok",
            "trigger": "voice",
            "source": "carrier",
            "outcome": "ok",
            "reason_codes": [ReasonCode.OK],
            "policy_version": policy_version(),
        }
        data.update(overrides)
        return AuditRecord.model_validate(data)

    return _make
