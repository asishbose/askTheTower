"""Fixtures: the mock carrier in-process (httpx.ASGITransport), DynamoDB on **both** backends (moto in-process,
and DynamoDB Local via testcontainers when `docker info` succeeds — RUN-ALL Decisions), the demo seed, and
Tower itself — called directly (`tower.line_is_ok(...)`) or over Streamable HTTP in-process (`mcp`).

The DynamoDB part follows packages/tower-consent/tests/conftest.py (the root conftest's one session-wide `mock_aws`; a random
table prefix per test; a privacy sweep of every table on teardown). `TOWER_DDB_BACKENDS=moto` narrows the
matrix. Tower's clock is a `FixedClock` kept equal to the mock carrier's clock (`stack.advance`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from camara_client import BreakerRegistry, DirectClient
from camara_client.testing import mock_config
from mock_carrier.app import create_app as create_mock
from mock_carrier.settings import Settings as MockSettings
from mock_carrier.testing import ASISH, BASE, MOM
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, tables
from tower_mcp.deps import Deps, FixedClock, RecordingAlerts, Settings, build_deps
from tower_mcp.seed import DemoSeed, seed_demo
from tower_mcp.server import invoke

from tests.helpers import dynamo
from tests.privacy.patterns import phone_hits

MOCK_START = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # scenarios/demo.yaml `clock`
LOCAL_BEARER = "local-dev-tower-bearer"
SUPPORT = "611"  # carriers' public support short code: config, not anyone's line
BINDING = "http://bind.test"

BACKENDS = dynamo.backends()


def pytest_report_header(config: pytest.Config) -> str:
    return f"tower-mcp DynamoDB backends: {BACKENDS}; docker available: {dynamo.docker_available()}"


@pytest.fixture(scope="session", autouse=True)
def _fake_aws_env(fake_aws_env: None) -> None:
    """moto / DynamoDB Local credentials for every test here (root conftest `fake_aws_env`)."""


@pytest.fixture(scope="session", autouse=True)
def _warm_once() -> None:
    """One-time costs outside any 300 ms budget: fastmcp's imports and a first FastAPI request."""
    import fastmcp  # noqa: F401
    from fastmcp import Client  # noqa: F401

    async def warm() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_mock(MockSettings(admin=True, base_url=BASE))),
            base_url=BASE,
        ) as c:
            await c.get("/_admin/clock")

    asyncio.run(warm())


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
            if len(str(int(v))) >= 10 and path.split(".")[-1] not in (table.ttl_attribute, "ttl"):
                bad.setdefault(f"{table.name}.{path}", []).append(str(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(table, f"{path}.{k}" if path else str(k), x)
        elif isinstance(v, list | set | tuple):
            for x in v:
                walk(table, f"{path}[]", x)

    for t in tables.TABLES:
        for item in store.scan_all(t):
            walk(t, "", item)
    return bad


@pytest.fixture(params=BACKENDS)
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
    return LocalLineIdHasher(b"k" * 32)


@pytest.fixture
def cipher() -> LocalMsisdnCipher:
    return LocalMsisdnCipher(b"m" * 32)


@dataclass
class Stack:
    """The in-process stack: mock carrier + store + Tower deps, with helpers that drive the mock."""

    deps: Deps
    store: Store
    admin: httpx.AsyncClient
    clock: FixedClock
    seed: DemoSeed
    alerts: RecordingAlerts
    results: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[tuple[str, Any]] = field(default_factory=list)

    def inject(self, operation: str, handler: Any) -> None:
        """Register a botocore `before-call` hook on the store's client; removed at teardown (the moto client
        is shared by the session)."""
        event = f"before-call.dynamodb.{operation}"
        self.store.client.meta.events.register(event, handler)
        self.hooks.append((event, handler))

    async def calls(self) -> int:
        r = await self.admin.get("/_admin/state")
        return int(r.json()["calls"])

    async def event(self, msisdn: str, name: str) -> dict[str, Any]:
        r = await self.admin.post(f"/_admin/lines/{msisdn}/events", json={"event": name})
        assert r.status_code == 200, r.text
        return dict(r.json())

    async def fault(self, kind: str, n: int = 1) -> None:
        r = await self.admin.post("/_admin/faults", json={"kind": kind, "n": n})
        assert r.status_code == 200, r.text

    async def advance(self, seconds: float) -> None:
        r = await self.admin.post("/_admin/clock", json={"advance_s": seconds})
        assert r.status_code == 200, r.text
        self.clock.set(self.clock.at + timedelta(seconds=seconds))

    def _keep(self, result: Any) -> Any:
        self.results.append(result.to_wire())
        return result

    async def line_is_ok(self, user: str, line: str = "self") -> Any:
        return self._keep(await invoke(self.deps, "line_is_ok", user, line=line))

    async def is_reachable(self, user: str, line: str = "self") -> Any:
        return self._keep(await invoke(self.deps, "is_reachable", user, line=line))

    async def watch_line(self, user: str, line: str = "self", enable: bool | None = None) -> Any:
        return self._keep(await invoke(self.deps, "watch_line", user, line=line, enable=enable))


RESULTS: list[dict[str, Any]] = []
"""Every ToolResult produced by any test (via `Stack` or the MCP client) — test_privacy greps them."""


@pytest.fixture
async def stack(store: Store, hasher: LocalLineIdHasher, cipher: LocalMsisdnCipher) -> AsyncIterator[Stack]:
    mock_app = create_mock(
        MockSettings(admin=True, base_url=BASE, webhook_backoff_s=0.0, fault_timeout_s=0.5)
    )
    transport = httpx.ASGITransport(app=mock_app)
    carrier = DirectClient(
        mock_config(BASE), secret="local-dev-tower", transport=transport, breakers=BreakerRegistry()
    )
    clock = FixedClock(MOCK_START)
    alerts = RecordingAlerts()
    deps = build_deps(
        Settings(env="local", bearer=LOCAL_BEARER, binding_base_url=BINDING, carrier_support_number=SUPPORT),
        env={},
        store=store,
        carrier=carrier,
        cipher=cipher,
        clock=clock,
        alerts=alerts,
    )
    seed = seed_demo(
        store, hasher, cipher, asish_e164=ASISH, mom_e164=MOM, now=MOCK_START - timedelta(days=1)
    )
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as admin:
        r = await admin.get("/_admin/clock")
        assert r.json()["clock"].startswith("2026-10-05T14:00:00")
        await deps.warm()
        s = Stack(deps=deps, store=store, admin=admin, clock=clock, seed=seed, alerts=alerts)
        try:
            yield s
        finally:
            for event, handler in s.hooks:
                store.client.meta.events.unregister(event, handler)
            RESULTS.extend(s.results)
    await carrier.aclose()


@pytest.fixture
def log_capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def wire(result: Any) -> str:
    return json.dumps(result.to_wire(), sort_keys=True)


TOWER_URL = "http://tower.test"


def http_client_factory(app: Any) -> Any:
    """An httpx2 client factory (what fastmcp's transport uses) that talks to `app` in-process."""
    import httpx2

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **kw: Any) -> Any:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=TOWER_URL,
            headers=headers,
            timeout=timeout if timeout is not None else 30.0,
            auth=auth,
            follow_redirects=kw.get("follow_redirects", True),
        )

    return factory


def mcp_client(app: Any, user: str | None = "user-asish", bearer: str | None = LOCAL_BEARER) -> Any:
    """A fastmcp Client over Streamable HTTP to the in-process Tower app."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if user:
        headers["X-Tower-User"] = user
    transport = StreamableHttpTransport(
        f"{TOWER_URL}/mcp", headers=headers, httpx_client_factory=http_client_factory(app)
    )
    return Client(transport)


@asynccontextmanager
async def serving(app: Any) -> AsyncIterator[Any]:
    """Run the app's lifespan in one dedicated task (anyio cancel scopes must exit in the task that entered
    them; pytest-asyncio may run fixture setup and teardown in different tasks)."""
    ready, stop = asyncio.Event(), asyncio.Event()
    failure: list[BaseException] = []

    async def hold() -> None:
        try:
            async with app.router.lifespan_context(app):
                ready.set()
                await stop.wait()
        except BaseException as e:  # noqa: BLE001
            failure.append(e)
            ready.set()

    task = asyncio.create_task(hold())
    await ready.wait()
    if failure:
        raise failure[0]
    try:
        yield app
    finally:
        stop.set()
        await task


@pytest.fixture
async def tower_app(stack: Stack) -> AsyncIterator[Any]:
    from tower_mcp.server import create_app

    async with serving(create_app(stack.deps)) as app:
        yield app
