"""Composition root: every client the UI uses is built here, once, from `Settings` (clean-code rule: no
module-level network clients). Tests pass transports, a store, a Tower client factory or a reset of their own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Literal

import httpx
from ref_client.agent import Agent, BedrockAgent, ScriptedAgent, has_aws_credentials
from ref_client.demo import BindingAdminGrants, BindingAdminSettings, Control
from ref_client.mcp_client import TowerClient, TowerConfig, user_id_for
from ref_client.transcript import Turn
from tower_consent import Store

from demo_ui.clients import AlertsSent, BindingAdmin, MockAdmin, Unavailable, bearer
from demo_ui.config import Settings
from demo_ui.feed import AuditSource, CarrierSource, Feed, GrantsSource, SmsSource, Source
from demo_ui.runner import Runner

log = logging.getLogger("demo_ui.deps")

Mode = Literal["scripted", "bedrock"]
# DynamoDB Local accepts any credentials. Given to the boto3 client only, never put in `os.environ`, where they
# would look like Bedrock credentials to `has_aws_credentials` (doc 11 §6).
DDB_LOCAL_CREDENTIALS = {"aws_access_key_id": "dynamodblocal", "aws_secret_access_key": "dynamodblocal"}


class ResetUnavailable(Unavailable):
    """Reset cannot run here (no seed module, ENV is not local, or the seed failed)."""


def agent_mode(settings: Settings) -> Mode:
    if settings.ref_agent == "auto":
        return "bedrock" if has_aws_credentials() else "scripted"
    return settings.ref_agent


def build_store(settings: Settings) -> Store:
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.dynamo_endpoint:
        kwargs |= {"endpoint_url": settings.dynamo_endpoint, **DDB_LOCAL_CREDENTIALS}
    return Store(boto3.client("dynamodb", **kwargs), settings.table_prefix)


def load_seed(settings: Settings) -> ModuleType:
    """The `make seed` module (decision 5), imported from its file: compose copies it into the image."""
    path = settings.seed_path
    if path is None or not path.exists():
        raise ResetUnavailable("Reset needs deploy/compose/seed/seed.py (DEMO_UI_SEED_PATH)")
    spec = importlib.util.spec_from_file_location("att_compose_seed", path)
    if spec is None or spec.loader is None:
        raise ResetUnavailable("Reset: the seed module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class Deps:
    settings: Settings
    mode: Mode
    feed: Feed
    runner: Runner
    mock: MockAdmin | None = None
    binding: BindingAdmin | None = None
    tower_factory: Callable[..., Any] | None = None  # httpx client factory for TowerClient (test seam)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def tower_config(self, who: str) -> TowerConfig:
        s = self.settings
        return TowerConfig(url=s.tower_url, bearer=s.tower_bearer, user_id=user_id_for(who))

    def make_agent(self, tower: TowerClient) -> Agent:
        return BedrockAgent(tower) if self.mode == "bedrock" else ScriptedAgent(tower)

    @asynccontextmanager
    async def agents(self) -> AsyncIterator[Callable[[str], Awaitable[Agent]]]:
        async with AsyncExitStack() as stack:

            async def agent_for(user_id: str) -> Agent:
                cfg = TowerConfig(
                    url=self.settings.tower_url, bearer=self.settings.tower_bearer, user_id=user_id
                )
                tower = await stack.enter_async_context(
                    TowerClient(cfg, httpx_client_factory=self.tower_factory)
                )
                return self.make_agent(tower)

            yield agent_for

    async def say(self, who: str, text: str) -> Turn:
        """One free-text utterance through Bedrock (the scripted agent has no tool choice to make)."""
        async with TowerClient(self.tower_config(who), httpx_client_factory=self.tower_factory) as tower:
            return await self.make_agent(tower).ask(text)

    async def aclose(self) -> None:
        for close in self.closers:
            await close()


def build_deps(
    settings: Settings,
    *,
    transports: dict[str, httpx.AsyncBaseTransport] | None = None,
    store: Store | None = None,
    reset: Callable[[], Awaitable[None]] | None = None,
    tower_factory: Callable[..., Any] | None = None,
    mode: Mode | None = None,
) -> Deps:
    t = transports or {}
    closers: list[Callable[[], Awaitable[None]]] = []

    def client(name: str, base: str, headers: dict[str, str]) -> httpx.AsyncClient:
        c = httpx.AsyncClient(base_url=base, headers=headers, timeout=10.0, transport=t.get(name))
        closers.append(c.aclose)
        return c

    mock = (
        MockAdmin(client("mock", settings.mock_url, bearer(settings.mock_admin_token)))
        if settings.carrier_enabled
        else None
    )
    binding = (
        BindingAdmin(client("binding", settings.binding_url, {})) if settings.binding_admin_enabled else None
    )
    alerts = (
        AlertsSent(client("alerts", settings.alerts_url, bearer(settings.alerts_bearer)))
        if settings.sms_enabled
        else None
    )
    store = store if store is not None else build_store(settings)

    sources: list[Source] = [
        CarrierSource(mock, settings.carrier_off_reason),
        AuditSource(store),
        SmsSource(
            alerts, f"ENV={settings.env}: the SMS goes to the real phone; the audit row is the evidence"
        ),
        GrantsSource(binding, "binding page admin is local-only: use the QR code from next_step"),
    ]
    feed = Feed(sources, settings.feed_poll_s)
    the_mode = mode or agent_mode(settings)

    control: Control | None = None
    if mock is not None:
        grants = BindingAdminGrants(settings.binding_url, transport=t.get("binding"))
        watch_settings = BindingAdminSettings(settings.binding_url, transport=t.get("binding"))
        closers += [grants.aclose, watch_settings.aclose]
        control = Control(
            mock=mock.http,
            grants=grants,
            settings=watch_settings,
            alerts_settle_s=settings.alerts_settle_s,
        )

    def get_control() -> Control:
        if control is None:
            raise ResetUnavailable(settings.carrier_off_reason)
        return control

    deps = Deps(
        settings=settings,
        mode=the_mode,
        feed=feed,
        runner=Runner(
            reset=reset or (lambda: seed_reset(settings, store)),
            control=get_control,
            agents=lambda: deps.agents(),
            publish=feed.publish,
            agent_label=the_mode,
            local=settings.local,
        ),
        mock=mock,
        binding=binding,
        tower_factory=tower_factory,
        closers=closers,
    )
    return deps


async def seed_reset(settings: Settings, store: Store) -> None:
    """Reset = `make seed` (decision 5): the same `seed.reset` with this UI's clients and store."""
    seed = load_seed(settings)

    def run() -> None:
        with (
            httpx.Client(
                base_url=settings.mock_url, timeout=15.0, headers=bearer(settings.mock_admin_token)
            ) as m,
            httpx.Client(base_url=settings.binding_url, timeout=30.0, follow_redirects=False) as page,
        ):
            seed.reset(m, page, store, echo=lambda msg: log.info("reset step=%s", msg))

    try:
        await asyncio.to_thread(run)
    except (httpx.HTTPError, RuntimeError) as e:  # seed.SeedError is a RuntimeError; its text names no value
        raise ResetUnavailable(f"Reset failed: {type(e).__name__}: {e}") from None
