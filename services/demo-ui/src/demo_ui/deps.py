"""Composition root: every client the UI uses is built here, once, from `Settings` (clean-code rule: no
module-level network clients). Tests pass transports, a store, a Tower client factory or a reset of their own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import httpx
from ref_client.agent import Agent, BedrockAgent, ScriptedAgent, has_aws_credentials
from ref_client.demo import BindingAdminGrants, BindingAdminSettings, Control, DemoError
from ref_client.cognito import CognitoUnavailable, CognitoUsers
from ref_client.mcp_client import TowerClient, TowerConfig, TowerError, TowerUnauthorized, user_id_for
from ref_client.transcript import Turn
from tower_consent import Store

from demo_ui.aws_reset import AwsReset
from demo_ui.clients import AlertsSent, AlertsSentControl, BindingAdmin, MockAdmin, Unavailable, bearer
from demo_ui import views
from demo_ui.config import AWS_FILE_KEYS, Settings, read_env_file
from demo_ui.feed import AuditSource, CarrierSource, Feed, GrantsSource, SmsSource, Source
from demo_ui.ops import plan
from demo_ui.ops.aws_status import COGNITO_NEEDED, AwsStatus, Files
from demo_ui.ops.gate import Gate
from demo_ui.ops.probes import Probes, QuietProbes
from demo_ui.ops.runner import AwsContext, FileSecrets, OpsRunner, off_reason
from demo_ui.ops.stack import StackProbe
from demo_ui.ops.transcript import REL as OPS_LOG
from demo_ui.ops.transcript import Transcripts
from demo_ui.runner import Runner

log = logging.getLogger("demo_ui.deps")

Mode = Literal["scripted", "bedrock"]
# DynamoDB Local accepts any credentials. Given to the boto3 client only, never put in `os.environ`, where they
# would look like Bedrock credentials to `has_aws_credentials` (doc 11 §6).
DDB_LOCAL_CREDENTIALS = {"aws_access_key_id": "dynamodblocal", "aws_secret_access_key": "dynamodblocal"}


class ResetUnavailable(Unavailable):
    """Reset cannot run here (no seed module, ENV is not local, or the seed failed)."""


SMS_OVERLAP_AWS = 5  # 11 §4: on AWS a counter value can become visible after a higher one (06 §3.1)
COMPOSE_ENV = Path("deploy") / "compose" / ".env"


class AwsSettingsControl:
    """`ref_client.demo.SettingsControl` on ENV=aws: Transplant stays local (doc 11 §5), so there is nothing to
    save and nothing to reset; the bearer-mode binding admin has no `watch-settings` (04 §8)."""

    async def save_transplant(self) -> None:
        raise DemoError("Transplant runs locally: the AWS binding admin has no watch-settings (doc 11 §5)")

    async def reset(self) -> None:
        return None


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
    ops: OpsRunner
    aws_status: AwsStatus
    stack: StackProbe
    gate: Gate
    quiet: QuietProbes
    mock: MockAdmin | None = None
    binding: BindingAdmin | None = None
    tower_factory: Callable[..., Any] | None = None  # httpx client factory for TowerClient (test seam)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # ENV=aws: Tower sits behind AgentCore's JWT authorizer, so each user signs in to Cognito and the access token
    # is the bearer (the same as `ref-client demo --env aws`); the static TOWER_BEARER is local only.
    cognito: CognitoUsers | None = field(default=None, repr=False)
    # The AgentCore Runtime session the Tower calls ride on. AgentCore retires an idle session after a few
    # minutes and refuses the next request on it (MCPError at connect): `say` then rotates to a fresh id and
    # retries once (what scripts/latency.py does).
    session_id: str = field(default_factory=lambda: f"control-{uuid.uuid4()}")

    def tower_config(self, who: str) -> TowerConfig:
        return self._tower_config(user_id_for(who))

    def new_session(self) -> str:
        self.session_id = f"control-{uuid.uuid4()}"
        return self.session_id

    def _tower_config(self, user_id: str) -> TowerConfig:
        s = self.settings
        if s.env != "aws":
            return TowerConfig(url=s.tower_url, bearer=s.tower_bearer, user_id=user_id, session_id=self.session_id)
        if self.cognito is None:
            raise TowerError("ENV=aws: Tower needs a Cognito sign-in: run make outputs and make cognito-users ENV=aws")
        try:
            token = self.cognito.access_token(user_id.removeprefix("user-"))
        except CognitoUnavailable as e:
            raise TowerError(str(e)) from None
        return TowerConfig(url=s.tower_url, authorization=f"Bearer {token}", session_id=self.session_id)

    def make_agent(self, tower: TowerClient) -> Agent:
        return BedrockAgent(tower) if self.mode == "bedrock" else ScriptedAgent(tower)

    @asynccontextmanager
    async def agents(self) -> AsyncIterator[Callable[[str], Awaitable[Agent]]]:
        async with AsyncExitStack() as stack:

            async def agent_for(user_id: str) -> Agent:
                return self.make_agent(await self._open_tower(stack, user_id))

            yield agent_for

    async def _open_tower(self, stack: AsyncExitStack, user_id: str) -> TowerClient:
        """Connect to Tower for `user_id`, retrying once when Tower refuses the token (401: it expired — sign in
        again) or the connect fails (the idle AgentCore session was retired — a fresh session id)."""
        for attempt in (1, 2):
            try:
                return await stack.enter_async_context(
                    TowerClient(self._tower_config(user_id), httpx_client_factory=self.tower_factory)
                )
            except TowerUnauthorized:
                if attempt == 2 or self.cognito is None:
                    raise
                log.warning("tower: 401 for %s; signing in again", user_id)
                self.cognito.forget(user_id.removeprefix("user-"))
            except TowerError as e:
                if attempt == 2 or "cannot connect" not in str(e):
                    raise
                log.warning("tower: connect failed on session %s; retrying on a new one", self.session_id[:16])
                self.new_session()
        raise AssertionError("unreachable")

    async def say(self, who: str, text: str) -> Turn:
        """One free-text utterance through Bedrock (the scripted agent has no tool choice to make)."""
        async with AsyncExitStack() as stack:
            tower = await self._open_tower(stack, user_id_for(who))
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
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Deps:
    t = transports or {}
    closers: list[Callable[[], Awaitable[None]]] = []
    ids = settings.user_ids

    def client(name: str, base: str, headers: dict[str, str]) -> httpx.AsyncClient:
        c = httpx.AsyncClient(base_url=base, headers=headers, timeout=10.0, transport=t.get(name))
        closers.append(c.aclose)
        return c

    mock = (
        MockAdmin(client("mock", settings.mock_url, bearer(settings.mock_bearer)))
        if settings.carrier_enabled
        else None
    )
    binding = (
        BindingAdmin(
            client("binding", settings.binding_admin_base, bearer(settings.binding_admin_bearer)), ids
        )
        if settings.binding_admin_enabled
        else None
    )
    alerts = (
        AlertsSent(client("alerts", settings.alerts_url, bearer(settings.alerts_admin_bearer)))
        if settings.sms_enabled
        else None
    )
    store = store if store is not None else build_store(settings)

    sources: list[Source] = [
        CarrierSource(mock, settings.carrier_off_reason),
        AuditSource(store, owners=((ids["asish"], "Asish"), (ids["mom"], "Mom"))),
        SmsSource(
            alerts,
            f"ENV={settings.env}: the SMS goes to the real phone; the audit row is the evidence",
            overlap=SMS_OVERLAP_AWS if settings.env == "aws" else 0,
        ),
        GrantsSource(binding, "binding page admin is local-only: use the QR code from next_step"),
    ]
    feed = Feed(sources, settings.feed_poll_s)
    the_mode = mode or agent_mode(settings)
    control = build_control(settings, mock, binding, t, closers, alerts)

    def get_control() -> Control:
        if control is None or not settings.macros_enabled:
            raise ResetUnavailable(settings.macros_off_reason or settings.carrier_off_reason)
        return control

    # The Ops tab (doc 11 §2): GETs and the stepper use a client that carries no header at all (no bearer).
    probe_http = httpx.AsyncClient(timeout=2.0, transport=t.get("ops"), follow_redirects=False)
    closers.append(probe_http.aclose)
    checkout = settings.checkout
    root = checkout or Path.cwd()
    parent = os.environ if environ is None else environ
    compose_secrets = FileSecrets(checkout / COMPOSE_ENV if checkout else None)
    aws_env_path = settings.aws_env_file or (checkout / "deploy" / ".env.aws" if checkout else None)
    aws_secrets = FileSecrets(aws_env_path)
    probes = Probes(checkout, parent)
    quiet = QuietProbes(probes, monotonic=time.monotonic)
    gate = Gate(
        published_loopback=settings.published_loopback,
        host=settings.host,
        flag=settings.aws_ops,
        checkout=checkout,
        which=which,
        config_check=probes.config_check,
    )
    transcripts = Transcripts(root / OPS_LOG, wall=lambda: datetime.now(UTC))
    files = Files.under(root, settings.aws_env_file)
    aws_status = AwsStatus(files, probe_http, transcripts)

    async def aws_context() -> AwsContext:
        """The stepper's view for an aws POST (called only after the gate passed)."""
        snap = await quiet.snapshot(gate_on=True)
        pipeline = await aws_status.pipeline(snap)
        facts = snap.facts.value if snap.facts is not None and snap.facts.ok else None
        blocked = {} if facts is not None and facts.cognito == 2 else {"aws-cognito-users": COGNITO_NEEDED}
        return AwsContext(pipeline.done_targets, blocked, plan.deploy_needs_word(files.plan))

    ops = OpsRunner(
        publish=feed.publish,
        cwd=checkout,
        off=off_reason(
            env=settings.env,
            container=settings.published_loopback,
            make_path=which("make"),
            checkout=checkout,
        ),
        parent_env=parent,
        secrets=lambda: (*settings.secrets(), *compose_secrets(), *aws_secrets()),
        other_busy=lambda: deps.runner.busy,
        gate=gate.reason,
        aws_context=aws_context,
        transcripts=transcripts,
        on_end=settings_reloader(settings, aws_env_path, publish=feed.publish),
    )
    closers.append(ops.aclose)
    stack_urls = (
        {"mock": settings.mock_url, "binding": settings.binding_url, "alerts": settings.alerts_url}
        if settings.local
        else {}
    )

    deps = Deps(
        settings=settings,
        mode=the_mode,
        feed=feed,
        runner=Runner(
            reset=reset or build_reset(settings, store, mock, alerts, binding),
            control=get_control,
            agents=lambda: deps.agents(),
            publish=feed.publish,
            agent_label=the_mode,
            local=settings.local,
        ),
        ops=ops,
        aws_status=aws_status,
        stack=StackProbe(probe_http, stack_urls),
        gate=gate,
        quiet=quiet,
        mock=mock,
        binding=binding,
        tower_factory=tower_factory,
        closers=closers,
        cognito=cognito_users(settings),
    )
    return deps


def aws_file_values(path: Path | None) -> dict[str, str]:
    """The AWS_FILE_KEYS of deploy/.env.aws as the UI would load them ({} when absent or unreadable)."""
    if path is None:
        return {}
    try:
        return {k: v for k, v in read_env_file(path).items() if k in AWS_FILE_KEYS}
    except (OSError, UnicodeDecodeError):
        return {}


def settings_reloader(
    settings: Settings,
    path: Path | None,
    *,
    publish: Callable[[str, str], None],
    reexec: Callable[[], None] | None = None,
    delay_s: float = 2.0,
) -> Callable[[Any], None]:
    """ENV=aws: after an aws job, if deploy/.env.aws no longer says what this process loaded at start (a deploy,
    outputs or admin-open rewrote the addresses or the bearer), say so in the Ops log and re-exec the UI with the
    same command line, so the page comes back on the new stack without a restart by hand (the page reconnects)."""
    loaded = aws_file_values(path) if settings.env == "aws" else {}

    def _reexec() -> None:
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def on_end(job: Any) -> None:
        if settings.env != "aws" or getattr(job.target, "runner", None) != "aws":
            return
        now = aws_file_values(path)
        if now == loaded:
            return
        changed = sorted(k for k in set(loaded) | set(now) if loaded.get(k) != now.get(k))
        log.info("deploy/.env.aws changed (%s): reloading the UI", ", ".join(changed))
        publish(
            "ops",
            views.render(
                "ops_log.html",
                lines=[f"deploy/.env.aws changed ({', '.join(changed)}): the UI reloads itself in {delay_s:g} s; the page reconnects"],
            ),
        )
        asyncio.get_running_loop().call_later(delay_s, reexec or _reexec)

    return on_end


def cognito_users(settings: Settings) -> CognitoUsers | None:
    """ENV=aws: the demo users' subs and sign-in (artifacts/tf-outputs.json + cognito-users.json); None locally
    or when the artifacts are missing (then every Tower call says what to run)."""
    if settings.env != "aws":
        return None
    try:
        return CognitoUsers.from_artifacts((settings.checkout or Path.cwd()) / "artifacts")
    except CognitoUnavailable as e:
        log.warning("cognito: %s", e)
        return None


def build_control(
    settings: Settings,
    mock: MockAdmin | None,
    binding: BindingAdmin | None,
    t: dict[str, httpx.AsyncBaseTransport],
    closers: list[Callable[[], Awaitable[None]]],
    alerts: AlertsSent | None = None,
) -> Control | None:
    """The macros' controls: locally ref_client's binding-admin controls; on AWS this UI's bearer-mode
    `BindingAdmin` as the `GrantControl` and no Transplant settings (doc 11 §5), so `ref_client` is unchanged.
    The sent-list wait (09 §4) reads the feed's `AlertsSent`; without it the wait is skipped."""
    if mock is None:
        return None
    sent = AlertsSentControl(alerts) if alerts is not None else None
    if settings.local:
        grants = BindingAdminGrants(settings.binding_url, transport=t.get("binding"))
        watch_settings = BindingAdminSettings(settings.binding_url, transport=t.get("binding"))
        closers += [grants.aclose, watch_settings.aclose]
        return Control(
            mock=mock.http,
            grants=grants,
            settings=watch_settings,
            alerts_settle_s=settings.alerts_settle_s,
            sent=sent,
        )
    if binding is None:
        return None
    return Control(
        mock=mock.http,
        grants=binding,
        settings=AwsSettingsControl(),
        alerts_settle_s=settings.alerts_settle_s,
        sent=sent,
    )


def build_reset(
    settings: Settings,
    store: Store,
    mock: MockAdmin | None,
    alerts: AlertsSent | None,
    binding: BindingAdmin | None,
) -> Callable[[], Awaitable[None]]:
    """Reset: the `make seed` code locally (decision 5); HTTP only on AWS (U3), never the seed module."""
    if settings.local:
        return lambda: seed_reset(settings, store)
    if settings.macros_enabled and mock is not None and alerts is not None and binding is not None:
        ids = settings.user_ids
        return AwsReset(mock.http, alerts.http, binding.http, asish=ids["asish"], mom=ids["mom"])

    async def off() -> None:
        raise ResetUnavailable(settings.macros_off_reason or settings.carrier_off_reason)

    return off


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
