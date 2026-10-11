"""Wiring, built once at startup: settings, thresholds, carrier client, consent store, cipher, clock, Alerts.

Nothing here decides anything. Tool handlers receive one `Deps` and call the shared packages through it.

Environment (also in `.env.example` and the README):

| Variable | Default | Meaning |
|---|---|---|
| `TOWER_ENV` | `local` | `local` enables the static bearer + `X-Tower-User`; anything else requires a JWT |
| `TOWER_BEARER` | — | the local static bearer (local only) |
| `TOWER_JWKS_URL` / `TOWER_JWT_ISSUER` / `TOWER_JWT_AUDIENCE` / `TOWER_JWT_CLIENT_IDS` | — | inbound JWT verification (lists comma-separated) |
| `TOWER_HOST` / `TOWER_PORT` | `0.0.0.0` / `8000` | listen address (AgentCore Runtime expects 8000, path `/mcp`) |
| `TOWER_TZ` | `America/Toronto` | zone for spoken times ("2:14 today") |
| `TOWER_THRESHOLDS` | packaged `thresholds.yaml` | policy thresholds file |
| `TOWER_CLOCK_URL` | — | local demo only: follow the mock carrier's clock (`<mock>/_admin/clock`) |
| `TOWER_CALL_LOG` | `0` | local demo only: `1` logs each tool call + result as one `tower_mcp.calls` line (Alexa+ transcripts) |
| `BINDING_BASE_URL` | `http://localhost:8081` | binding page; `next_step.url` = `<base>/bind/<token>` |
| `CARRIER_SUPPORT_NUMBER` | — | the carrier's public support line for `next_step.call_carrier` |
| `ALERTS_INTERNAL_URL` / `ALERTS_INTERNAL_BEARER` | — | Alerts' `POST /internal/watch`; unset → log-only stub |
| `TOWER_DYNAMODB_ENDPOINT` (alias `DYNAMO_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | — | consent/audit store |
| `TOWER_LINE_ID_KEY` / `TOWER_MSISDN_KEY` (local) or `TOWER_KMS_*` | — | `crypto_from_env` |
| `CARRIER_*` | see `camara_client.config` | carrier client |
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from camara_client import CarrierClient, CarrierConfig, DirectClient, GatewayClient, make_client
from camara_client import specs as carrier_specs
from tower_audit import AuditRecord
from tower_consent import MsisdnCipher, Store, crypto_from_env
from tower_consent.models import Profile
from tower_policy import Thresholds, default_thresholds, load_thresholds, policy_version

logger = logging.getLogger("tower_mcp")

DEFAULT_TZ = "America/Toronto"


@dataclass(frozen=True)
class Settings:
    env: str = "local"
    bearer: str | None = None
    jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_client_ids: str | None = None
    host: str = "0.0.0.0"  # noqa: S104 - container entrypoint
    port: int = 8000
    tz: str = DEFAULT_TZ
    thresholds_path: str | None = None
    clock_url: str | None = None
    call_log: bool = False
    binding_base_url: str = "http://localhost:8081"
    carrier_support_number: str | None = None
    alerts_internal_url: str | None = None
    alerts_internal_bearer: str | None = None

    @property
    def local(self) -> bool:
        return self.env == "local"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env

        def opt(name: str) -> str | None:
            return e.get(name) or None

        return cls(
            env=e.get("TOWER_ENV", "local"),
            bearer=opt("TOWER_BEARER"),
            jwks_url=opt("TOWER_JWKS_URL"),
            jwt_issuer=opt("TOWER_JWT_ISSUER"),
            jwt_audience=opt("TOWER_JWT_AUDIENCE"),
            jwt_client_ids=opt("TOWER_JWT_CLIENT_IDS"),
            host=e.get("TOWER_HOST", "0.0.0.0"),  # noqa: S104
            port=int(e.get("TOWER_PORT", "8000")),
            tz=e.get("TOWER_TZ", DEFAULT_TZ),
            thresholds_path=opt("TOWER_THRESHOLDS"),
            clock_url=opt("TOWER_CLOCK_URL"),
            call_log=e.get("TOWER_CALL_LOG", "0").strip().lower() in {"1", "true", "yes"},
            binding_base_url=e.get("BINDING_BASE_URL", "http://localhost:8081").rstrip("/"),
            carrier_support_number=opt("CARRIER_SUPPORT_NUMBER"),
            alerts_internal_url=opt("ALERTS_INTERNAL_URL"),
            alerts_internal_bearer=opt("ALERTS_INTERNAL_BEARER"),
        )


# --- clock --------------------------------------------------------------------------------------------
class Clock(Protocol):
    async def now(self) -> datetime: ...


class SystemClock:
    async def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Settable clock for tests and the latency harness."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    async def now(self) -> datetime:
        return self.at

    def set(self, at: datetime) -> None:
        self.at = at


class MockCarrierClock:
    """Local demo aid: Tower's `now` is the mock carrier's controllable clock (08 §1), so "twelve minutes
    ago" on the mock is twelve minutes ago for the policy too. Only honoured when `TOWER_ENV=local`.
    Falls back to the system clock (logged) if the mock does not answer within 100 ms."""

    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url
        self._http = httpx.AsyncClient(transport=transport, timeout=0.1)

    async def now(self) -> datetime:
        try:
            r = await self._http.get(self.url)
            r.raise_for_status()
            text = str(r.json()["clock"])
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning("mock clock unavailable; using the system clock")
            return datetime.now(UTC)

    async def aclose(self) -> None:
        await self._http.aclose()


# --- Alerts (prompt 10 provides the endpoint) -----------------------------------------------------------
class AlertsClient(Protocol):
    """Tower → Alerts: (un)subscribe the carrier events for a Watch (02 §2, 06 §1)."""

    async def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None: ...


@dataclass
class RecordingAlerts:
    """Stand-in when `ALERTS_INTERNAL_URL` is unset (and in tests): records, logs, never fails."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    async def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None:
        if self.fail:
            raise httpx.ConnectError("alerts unavailable (test)")
        call = {"line_id": line_id, "watcher_user_id": watcher_user_id, "enable": enable, "profile": profile}
        self.calls.append(call)
        logger.info("alerts stub: watch line_id=%s enable=%s profile=%s", line_id, enable, profile)


class HttpAlerts:
    """`POST {ALERTS_INTERNAL_URL}/internal/watch {line_id, watcher_user_id, enable, profile}`, bearer-protected
    (prompt 10's `internal_api.py`). 2 s timeout: this is a state change, not the voice hot path."""

    def __init__(
        self, base_url: str, bearer: str | None, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.url = base_url.rstrip("/") + "/internal/watch"
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self._http = httpx.AsyncClient(transport=transport, timeout=2.0, headers=headers)

    def __repr__(self) -> str:
        return f"HttpAlerts(url={self.url!r})"

    async def watch(self, *, line_id: str, watcher_user_id: str, enable: bool, profile: Profile) -> None:
        r = await self._http.post(
            self.url,
            json={
                "line_id": line_id,
                "watcher_user_id": watcher_user_id,
                "enable": enable,
                "profile": profile,
            },
        )
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._http.aclose()


# --- the bundle ----------------------------------------------------------------------------------------
AuditHook = Callable[[AuditRecord], None]


@dataclass
class Deps:
    settings: Settings
    store: Store
    carrier: CarrierClient
    cipher: MsisdnCipher
    thresholds: Thresholds
    clock: Clock
    alerts: AlertsClient
    policy_version: str = field(default_factory=policy_version)
    after_audit: AuditHook | None = None  # test seam: runs after the audit row is durable, before return
    wall_clock: Clock | None = None  # bind-token expiry is checked by the binding page on real time

    async def wall_now(self) -> datetime:
        return await (self.wall_clock or self.clock).now()

    async def warm(self) -> None:
        """Pay one-time costs before the first request (06's build note: a cold token fetch or a cold
        fastmcp client exceeds the 300 ms carrier budget). Failures are logged, not fatal."""
        try:
            if isinstance(self.carrier, DirectClient):
                for api, op in WARM_OPERATIONS:
                    scope = carrier_specs.operation(api, op).scope_for(self.carrier.config.oauth.scopes)
                    await self.carrier.oauth.token(scope)
            elif isinstance(self.carrier, GatewayClient):
                await self.carrier.check_tools()
        except Exception as e:  # noqa: BLE001 - warm-up is best effort
            logger.warning("carrier warm-up failed: %s", type(e).__name__)

    async def aclose(self) -> None:
        for thing in (self.carrier, self.clock, self.alerts):
            close = getattr(thing, "aclose", None)
            if close is not None:
                await close()


WARM_OPERATIONS = (
    ("sim-swap", "checkSimSwap"),
    ("sim-swap", "retrieveSimSwapDate"),
    ("call-forwarding-signal", "retrieveCallForwarding"),
    ("device-reachability-status", "getReachabilityStatus"),
)


def build_deps(
    settings: Settings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    store: Store | None = None,
    carrier: CarrierClient | None = None,
    cipher: MsisdnCipher | None = None,
    clock: Clock | None = None,
    alerts: AlertsClient | None = None,
    thresholds: Thresholds | None = None,
) -> Deps:
    """Everything from the environment unless injected (tests inject the in-process mock and moto)."""
    e: Mapping[str, str] = os.environ if env is None else env
    settings = settings or Settings.from_env(e)
    if store is None:
        store_env = dict(e)
        if not store_env.get("TOWER_DYNAMODB_ENDPOINT") and store_env.get("DYNAMO_ENDPOINT"):
            store_env["TOWER_DYNAMODB_ENDPOINT"] = store_env["DYNAMO_ENDPOINT"]
        store = Store.from_env(store_env)
    if cipher is None:
        _, cipher = crypto_from_env(e)
    if carrier is None:
        carrier = make_client(CarrierConfig.from_env(e), env=e)
    if clock is None:
        clock = (
            MockCarrierClock(settings.clock_url) if settings.local and settings.clock_url else SystemClock()
        )
    if alerts is None:
        alerts = (
            HttpAlerts(settings.alerts_internal_url, settings.alerts_internal_bearer)
            if settings.alerts_internal_url
            else RecordingAlerts()
        )
    if thresholds is None:
        thresholds = (
            load_thresholds(settings.thresholds_path) if settings.thresholds_path else default_thresholds()
        )
    return Deps(
        settings=settings,
        store=store,
        carrier=carrier,
        cipher=cipher,
        thresholds=thresholds,
        clock=clock,
        alerts=alerts,
        wall_clock=SystemClock() if isinstance(clock, MockCarrierClock) else None,
    )
