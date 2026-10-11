"""Fixtures for the binding page: the store on **both** DynamoDB backends (moto, and DynamoDB Local via
testcontainers when Docker is up — the root conftest's session-wide fixtures, prompt 18), the mock carrier in-process, and the page in-process
(`httpx.ASGITransport`), with a settable clock.

`TOWER_DDB_BACKENDS=moto` narrows the matrix (CI without Docker).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from binding_page.app import create_app
from binding_page.config import Settings
from binding_page.deps import Deps
from camara_client import BreakerRegistry, CarrierConfig, DirectClient, OAuthConfig
from mock_carrier.app import create_app as create_mock
from mock_carrier.settings import Settings as MockSettings
from mock_carrier.testing import ASISH, BASE, MOM
from tower_audit import HmacMarkerSigner
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, create_bind_token, tables

from tests.helpers import dynamo
from tests.privacy.patterns import phone_hits

# The test clock starts at the real minute, not a fixed date: DynamoDB Local runs a TTL sweeper on wall-clock
# time, so a fixed past date makes it delete "fresh" BindTokens (expires_at = NOW + 10 min) mid-test.
NOW = datetime.now(UTC).replace(second=0, microsecond=0)
LINE_KEY = b"k" * 32
MSISDN_KEY = b"m" * 32
PAGE = "http://binding.test"
REDIRECT = "http://localhost:8081/bind/callback"  # registered with the mock for client `binding-page`
SECRET = "test-session-secret-not-real"

__all__ = ["ASISH", "MOM", "NOW", "PAGE"]  # re-exported for readers; tests import from mock_carrier.testing

_BACKENDS = dynamo.backends()


def pytest_report_header(config: pytest.Config) -> str:
    return f"binding-page DynamoDB backends: {_BACKENDS}; docker available: {dynamo.docker_available()}"


@pytest.fixture(scope="session", autouse=True)
def _fake_aws_env(fake_aws_env: None) -> None:
    """moto / DynamoDB Local credentials for every test here (root conftest `fake_aws_env`)."""


def sweep_for_numbers(store: Store) -> dict[str, list[str]]:
    bad: dict[str, list[str]] = {}

    def walk(table: tables.Table, path: str, v: Any) -> None:
        if isinstance(v, str):
            if phone_hits(v):
                bad.setdefault(f"{table.name}.{path}", []).extend(phone_hits(v))
        elif isinstance(v, bool):
            return
        elif isinstance(v, int | float):
            if len(str(int(v))) >= 10 and path != table.ttl_attribute:
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


@pytest.fixture(params=_BACKENDS)
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    backend = request.param
    client = dynamo.client_for(request, backend)
    s = Store(client, prefix=f"b{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    yield s
    leaks = sweep_for_numbers(s)
    for t in tables.TABLES:
        client.delete_table(TableName=s.name(t))
    assert not leaks, f"phone-number-shaped values in tables: {leaks}"


def table_counts(store: Store) -> dict[str, int]:
    return {t.name: len(store.scan_all(t)) for t in tables.TABLES}


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t = self.t + timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def mock_app() -> Any:
    return create_mock(MockSettings(admin=True, base_url=BASE, webhook_backoff_s=0.0))


class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps the mock's ASGI transport and records every request the page makes to the carrier."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def carrier_transport(mock_app: Any) -> RecordingTransport:
    return RecordingTransport(httpx.ASGITransport(app=mock_app))


def carrier_config() -> CarrierConfig:
    return CarrierConfig(
        client="direct",
        backend="mock",
        base_url=BASE,
        oauth=OAuthConfig(
            token_url=f"{BASE}/oauth2/token",
            authorize_url=f"{BASE}/oauth2/authorize",
            client_id="binding-page",
            secret_ref="env:CARRIER_CLIENT_SECRET",
            scopes=("number-verification",),
        ),
        profile="proactive",
    )


@dataclass
class Page:
    """One running binding page + what a test needs to poke at it."""

    deps: Deps
    app: Any
    store: Store
    clock: Clock
    carrier_transport: RecordingTransport

    def browser(self) -> httpx.AsyncClient:
        """A fresh 'phone browser': its own cookie jar, follows redirects."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=PAGE, follow_redirects=True
        )

    def bind_token(self, user_id: str, **kw: Any) -> str:
        return create_bind_token(self.store, user_id, now=self.clock(), **kw).token


@pytest.fixture
def make_page(
    store: Store, clock: Clock, carrier_transport: RecordingTransport
) -> Iterator[Callable[..., Page]]:
    created: list[Deps] = []

    def _make(**settings_kw: Any) -> Page:
        settings = Settings(
            session_secret=SECRET,
            base_url=PAGE,
            redirect_uri=REDIRECT,
            **{"tower_env": "local", **settings_kw},
        )
        cfg = carrier_config()
        deps = Deps(
            settings=settings,
            store=store,
            hasher=LocalLineIdHasher(LINE_KEY),
            cipher=LocalMsisdnCipher(MSISDN_KEY),
            carrier_config=cfg,
            carrier=DirectClient(
                cfg, secret="local-dev-binding", transport=carrier_transport, breakers=BreakerRegistry()
            ),
            audit_signer=HmacMarkerSigner(LINE_KEY),
            carrier_http=httpx.AsyncClient(transport=carrier_transport, follow_redirects=False),
            now=clock,
        )
        created.append(deps)
        return Page(
            deps=deps, app=create_app(deps), store=store, clock=clock, carrier_transport=carrier_transport
        )

    yield _make


@pytest.fixture
def page(make_page: Callable[..., Page]) -> Page:
    return make_page()


async def bind(page: Page, user_id: str, as_: str | None) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Run the whole one-tap flow in a fresh browser: open the link, tap Verify. Returns (browser, last response)."""
    browser = page.browser()
    token = page.bind_token(user_id)
    url = f"/bind/{token}" + (f"?as={as_}" if as_ else "")
    r = await browser.get(url)
    assert r.status_code == 200, r.text
    data = {"as": as_} if as_ else {}
    r = await browser.post(f"/bind/{token}/verify", data=data)
    return browser, r


CSRF_RE = re.compile(r'name="csrf" value="([a-p]+)"')


async def csrf_of(browser: httpx.AsyncClient) -> str:
    r = await browser.get("/me")
    assert r.status_code == 200, r.text
    m = CSRF_RE.search(r.text)
    assert m, "no CSRF token on /me"
    return m.group(1)


INVITE_RE = re.compile(r'<p class="big">([A-Z]{4}-[A-Z]{4})</p>')


async def invite_code(browser: httpx.AsyncClient) -> str:
    r = await browser.post("/me/invite", data={"csrf": await csrf_of(browser)})
    assert r.status_code == 200, r.text
    m = INVITE_RE.search(r.text)
    assert m, "no invite code shown"
    return m.group(1)


@dataclass(frozen=True)
class Helpers:
    bind: Callable[..., Any] = bind
    csrf_of: Callable[..., Any] = csrf_of
    invite_code: Callable[..., Any] = invite_code
    table_counts: Callable[..., Any] = table_counts


@pytest.fixture
def h() -> Helpers:
    """Flow helpers (test modules can't import conftest under --import-mode=importlib)."""
    return Helpers()


@pytest.fixture
async def asish_browser(page: Page) -> AsyncIterator[httpx.AsyncClient]:
    browser, r = await bind(page, "user-asish", "phone-asish")
    assert r.status_code == 200 and "Line connected" in r.text, r.text
    yield browser
    await browser.aclose()
