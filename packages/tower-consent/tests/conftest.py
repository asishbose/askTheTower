"""Fixtures: a fresh set of the six tables per test, on **both** backends.

- `moto`  — in-process `mock_aws`; always runs.
- `local` — DynamoDB Local (`amazon/dynamodb-local`) started once per run with testcontainers (root conftest). Runs when
  `docker info` succeeds; otherwise those cases are skipped with the reason. RUN-ALL Decisions: "moto when
  Docker is unavailable; testcontainers + DynamoDB Local when it is" — we keep both, and run both when we can.

`TOWER_DDB_BACKENDS=moto` (or `local`) narrows the matrix, e.g. in a CI job without Docker.

Every `store` is swept on teardown: no attribute name or string value in any table may match the phone-number
regex (the privacy invariant, 00), and only TTL attributes may hold a 10+-digit number.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, tables

from tests.helpers import dynamo
from tests.privacy.patterns import phone_hits

# Real current minute: DynamoDB Local sweeps TTL against wall-clock time, so a fixed past NOW
# lets it delete rows mid-test.
NOW = datetime.now(UTC).replace(second=0, microsecond=0)
LINE_KEY = b"k" * 32
MSISDN_KEY = b"m" * 32

_BACKENDS = dynamo.backends()


def pytest_report_header(config: pytest.Config) -> str:
    return f"tower-consent DynamoDB backends: {_BACKENDS}; docker available: {dynamo.docker_available()}"


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
    s = Store(client, prefix=f"t{uuid.uuid4().hex[:8]}-")
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
def cipher() -> LocalMsisdnCipher:
    return LocalMsisdnCipher(MSISDN_KEY)


@pytest.fixture
def now() -> datetime:
    return NOW


class RequestCounter:
    """Counts every DynamoDB API request made through one boto3 client (botocore `before-call` event)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, model: Any, **_: Any) -> None:
        self.calls.append(model.name)


@pytest.fixture
def counting(store: Store) -> Callable[[], AbstractContextManager[RequestCounter]]:
    """`with counting() as c: ...; assert c.calls == ["Query"]`."""

    @contextmanager
    def _cm() -> Iterator[RequestCounter]:
        counter = RequestCounter()
        store.client.meta.events.register("before-call.dynamodb", counter)
        try:
            yield counter
        finally:
            store.client.meta.events.unregister("before-call.dynamodb", counter)

    return _cm
