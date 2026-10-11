"""The in-process stack the reference client's tests drive: mock carrier (ASGI) + DynamoDB + Tower (ASGI, Streamable
HTTP) — the same wiring as services/tower-mcp/tests/conftest.py, trimmed to what the demo needs. The client
under test connects over MCP exactly as it would to a deployed Tower; only the HTTP transport is in-process.

DynamoDB: DynamoDB Local via testcontainers when `docker info` succeeds, moto otherwise (RUN-ALL Decisions);
`REF_DDB_BACKEND=moto|local` forces one. Tower's clock is a FixedClock moved with the mock's.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import pytest
from camara_client import BreakerRegistry, DirectClient
from camara_client.testing import mock_config
from mock_carrier.app import create_app as create_mock
from mock_carrier.settings import Settings as MockSettings
from mock_carrier.testing import ASISH, BASE, MOM
from ref_client.agent import ScriptedAgent
from ref_client.demo import Control
from ref_client.mcp_client import TowerClient, TowerConfig
from tower_consent import (
    LocalLineIdHasher,
    LocalMsisdnCipher,
    Store,
    delete_watch,
    errors,
    grant,
    revoke,
    set_watch_settings,
    tables,
)
from tower_mcp.deps import FixedClock, RecordingAlerts, Settings, build_deps
from tower_mcp.seed import DemoSeed, seed_demo
from tower_mcp.server import create_app

from tests.helpers import dynamo

MOCK_START = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # scenarios/demo.yaml `clock`
LOCAL_BEARER = "local-dev-tower-bearer"
TOWER_URL = "http://tower.test"

BACKEND = os.environ.get("REF_DDB_BACKEND") or ("local" if dynamo.docker_available() else "moto")


def pytest_report_header(config: pytest.Config) -> str:
    return f"ref-client DynamoDB backend: {BACKEND}"


@pytest.fixture(scope="session")
def ddb_client(request: pytest.FixtureRequest, fake_aws_env: None) -> Any:
    """One backend for the ref-client stack, on the root conftest's session-wide moto / DynamoDB Local.
    `fake_aws_env` (root) is requested here, *not* autouse: the Bedrock-gated tests must see the real
    environment to decide whether to skip."""
    return dynamo.client_for(request, BACKEND)


@pytest.fixture
def store(ddb_client: Any) -> Iterator[Store]:
    s = Store(ddb_client, prefix=f"r{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    yield s
    for t in tables.TABLES:
        ddb_client.delete_table(TableName=s.name(t))


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


@asynccontextmanager
async def serving(app: Any) -> AsyncIterator[Any]:
    """Run the app's lifespan in one task (anyio cancel scopes must exit in the task that entered them)."""
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


@dataclass
class InProcessGrants:
    """Mom's grant to Asish, set directly in the store — stands in for the binding page's local admin."""

    store: Store
    seed: DemoSeed
    clock: FixedClock

    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        try:
            if action == "revoke":
                revoke(
                    self.store,
                    self.seed.mom_line,
                    self.seed.asish_user,
                    "watch",
                    revoked_by=self.seed.mom_user,
                    now=self.clock.at,
                )
            else:
                grant(
                    self.store,
                    self.seed.mom_line,
                    self.seed.asish_user,
                    "watch",
                    "mom",
                    granted_by=self.seed.mom_user,
                    now=self.clock.at,
                )
        except (errors.GrantExists, errors.AliasCollision, errors.ConditionFailed, errors.GrantNotFound):
            pass  # idempotent, like the admin endpoint


@dataclass
class InProcessSettings:
    """Asish's watch settings (04 §9), set directly in the store — stands in for `/_admin/watch-settings`.
    The contacts get their `watch` grants here, as the compose seed gives them (06 §11.4)."""

    store: Store
    seed: DemoSeed
    clock: FixedClock
    contacts: tuple[str, ...] = ("user-partner", "user-neighbour")

    async def save_transplant(self) -> None:
        for contact in self.contacts:
            try:
                grant(
                    self.store,
                    self.seed.asish_line,
                    contact,
                    "watch",
                    "asish",
                    granted_by=self.seed.asish_user,
                    now=self.clock.at,
                )
            except errors.GrantExists:
                pass  # idempotent, like the seed
        set_watch_settings(
            self.store,
            self.seed.asish_line,
            self.seed.asish_user,
            "transplant",
            list(self.contacts),
            self.clock.at,
        )

    async def reset(self) -> None:
        delete_watch(self.store, self.seed.asish_line, self.seed.asish_user)


@dataclass
class RefStack:
    tower_app: Any
    control: Control
    clock: FixedClock
    seed: DemoSeed
    alerts: RecordingAlerts

    def config(self, user_id: str | None = "user-asish", bearer: str | None = LOCAL_BEARER) -> TowerConfig:
        return TowerConfig(url=f"{TOWER_URL}/mcp", bearer=bearer, user_id=user_id)

    def tower(self, user_id: str | None = "user-asish", bearer: str | None = LOCAL_BEARER) -> TowerClient:
        return TowerClient(
            self.config(user_id, bearer), httpx_client_factory=http_client_factory(self.tower_app)
        )


@pytest.fixture
async def ref_stack(store: Store) -> AsyncIterator[RefStack]:
    hasher, cipher = LocalLineIdHasher(b"k" * 32), LocalMsisdnCipher(b"m" * 32)
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
        Settings(
            env="local",
            bearer=LOCAL_BEARER,
            binding_base_url="http://bind.test",
            carrier_support_number="611",
        ),
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

    async def on_reset() -> None:
        clock.set(MOCK_START)

    async def on_advance(seconds: float) -> None:
        clock.set(clock.at + timedelta(seconds=seconds))

    async with (
        httpx.AsyncClient(transport=transport, base_url=BASE) as admin,
        serving(create_app(deps)) as app,
    ):
        control = Control(
            mock=admin,
            grants=InProcessGrants(store, seed, clock),
            settings=InProcessSettings(store, seed, clock),
            on_reset=on_reset,
            on_advance=on_advance,
        )
        yield RefStack(app, control, clock, seed, alerts)
    await carrier.aclose()


@pytest.fixture
def scripted_agent_for(ref_stack: RefStack) -> Any:
    """`agent_for(user_id)` for `run_demo`, with every Tower connection closed at teardown."""
    from contextlib import AsyncExitStack

    stack = AsyncExitStack()

    async def agent_for(user_id: str) -> ScriptedAgent:
        tower = await stack.enter_async_context(ref_stack.tower(user_id))
        return ScriptedAgent(tower)

    agent_for.close = stack.aclose  # type: ignore[attr-defined]
    return agent_for
