"""Fixtures for the Alerts tests.

DynamoDB comes from the root conftest (prompt 18): one session-wide moto `mock_aws` (moto start-up is slow
on /mnt/c) and, when `docker info` succeeds, one DynamoDB Local container via testcontainers for the whole run. `TOWER_DDB_BACKENDS=moto` narrows the matrix. Every `store` is
swept on teardown — the six consent tables *and* `AlertsState` — for phone-number-shaped values.

`world` = a seeded store + `FakeCarrier` + `LogSender` + `FixedClock` + `AlertsService`.
`mock_world` = the same store wired to the in-process mock carrier (DirectClient, client id `alerts`,
mock clock), with the mock's webhooks delivered to the Alerts FastAPI app.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from alerts.clock import FixedClock, MockCarrierClock
from alerts.context import AlertsService
from alerts.local import create_app
from alerts.send_backends import LogSender
from alerts.state import ALERTS_STATE, ensure_alerts_table
from alerts.testing import (
    ASISH,
    LINE_KEY,
    MOM,
    MSISDN_KEY,
    T0,
    FakeCarrier,
    FakeLine,
    LazyASGI,
    World,
    make_service,
)
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, tables

from tests.helpers import dynamo
from tests.privacy.patterns import HEALTH_WORDS, phone_hits

_BACKENDS = dynamo.backends()
ALL_TABLES = (*tables.TABLES, ALERTS_STATE)


def pytest_report_header(config: pytest.Config) -> str:
    return f"alerts DynamoDB backends: {_BACKENDS}; docker available: {dynamo.docker_available()}"


@pytest.fixture(scope="session", autouse=True)
def _fake_aws_env(fake_aws_env: None) -> None:
    """moto / DynamoDB Local credentials for every test here (root conftest `fake_aws_env`)."""


def sweep_for_numbers(store: Store) -> dict[str, list[str]]:
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

    for t in ALL_TABLES:
        for item in store.scan_all(t):
            walk(t, "", item)
    return bad


@pytest.fixture(params=_BACKENDS)
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    backend = request.param
    client = dynamo.client_for(request, backend)
    s = Store(client, prefix=f"a{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    ensure_alerts_table(s)
    if backend == "local":
        # These tests run on a simulated clock (T0 = 2026-10-05T14:00Z), but DynamoDB Local's TTL sweeper runs on the
        # wall clock: every `expires_at` here is already in the past, so a rate-limit claim could be swept between
        # two calls of one test. TTL is housekeeping, never logic (claims compare `sent_at`), so switch it off.
        for t in ALL_TABLES:
            if t.ttl_attribute:
                client.update_time_to_live(
                    TableName=s.name(t),
                    TimeToLiveSpecification={"Enabled": False, "AttributeName": t.ttl_attribute},
                )
    yield s
    leaks = sweep_for_numbers(s)
    for t in ALL_TABLES:
        client.delete_table(TableName=s.name(t))
    assert not leaks, f"phone-number-shaped values in tables: {leaks}"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def carrier(clock: FixedClock) -> FakeCarrier:
    return FakeCarrier(clock)


def assert_clean_bodies(sender: LogSender) -> None:
    """test_privacy's invariant, applied to every SMS any test produces: no number, no health word."""
    for sms in sender.sent:
        assert not phone_hits(sms.body), sms.body
        assert not HEALTH_WORDS.search(sms.body), sms.body
        assert not phone_hits(sms.label), sms.label


@pytest.fixture
def sender() -> Iterator[LogSender]:
    s = LogSender()
    yield s
    assert_clean_bodies(s)


@pytest.fixture
def mom(world: World, carrier: FakeCarrier) -> str:
    """Mom's line, watched by Asish (`care`, default chain), facts set on the fake carrier. Returns line_id."""
    world.standard()
    world.watch(MOM, "user-asish", "care")
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60), last_status_time=T0)
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    return world.lines[MOM]


@pytest.fixture
def svc(store: Store, carrier: FakeCarrier, clock: FixedClock, sender: LogSender) -> AlertsService:
    return make_service(store, carrier, clock, sender=sender)


@pytest.fixture
def world(svc: AlertsService, store: Store) -> World:
    return World(
        svc=svc, store=store, hasher=LocalLineIdHasher(LINE_KEY), cipher=LocalMsisdnCipher(MSISDN_KEY), now=T0
    )


@dataclass
class MockWorld:
    world: World
    svc: AlertsService
    sender: LogSender
    admin: httpx.AsyncClient  # the mock carrier (admin + CAMARA)
    alerts: httpx.AsyncClient  # the Alerts app


@pytest.fixture
async def mock_world(store: Store) -> AsyncIterator[MockWorld]:
    from camara_client import BreakerRegistry, make_client
    from camara_client.testing import mock_config
    from mock_carrier.app import create_app as create_mock
    from mock_carrier.settings import Settings as MockSettings
    from mock_carrier.testing import BASE

    lazy = LazyASGI()
    mock_app = create_mock(
        MockSettings(admin=True, base_url=BASE, webhook_backoff_s=0.0, fault_timeout_s=0.5),
        sink_transport=httpx.ASGITransport(app=lazy),
    )
    mock_transport = httpx.ASGITransport(app=mock_app)
    carrier = make_client(
        mock_config(BASE, client_id="alerts", profile="proactive"),
        secret="local-dev-alerts",
        transport=mock_transport,
        breakers=BreakerRegistry(),
    )
    sender = LogSender()
    svc = make_service(store, carrier, MockCarrierClock(BASE, transport=mock_transport), sender=sender)
    app = create_app(svc)
    lazy.app = app
    world = World(
        svc=svc, store=store, hasher=LocalLineIdHasher(LINE_KEY), cipher=LocalMsisdnCipher(MSISDN_KEY), now=T0
    )
    async with (
        httpx.AsyncClient(transport=mock_transport, base_url=BASE) as admin,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as alerts,
    ):
        yield MockWorld(world=world, svc=svc, sender=sender, admin=admin, alerts=alerts)
    await carrier.aclose()
    assert_clean_bodies(sender)
