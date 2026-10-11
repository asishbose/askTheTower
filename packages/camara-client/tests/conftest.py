"""Fixtures: the mock carrier in-process (httpx.ASGITransport), a DirectClient and a GatewayClient
(through the in-process FakeGateway) pointed at it, a fresh breaker registry per test and a settable
clock. Every client gets its own `BreakerRegistry` so no test sees another's open breaker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from camara_client import BreakerRegistry, DirectClient, GatewayClient
from camara_client.testing import FakeGateway, mock_config, monotonic_clock
from mock_carrier.app import create_app
from mock_carrier.settings import Settings
from mock_carrier.testing import BASE


@pytest.fixture(scope="session", autouse=True)
def _warm_mcp() -> None:
    """Pay fastmcp's one-time imports and first in-memory connection (seconds on /mnt/c) once, outside
    the 300 ms budgets the tests measure — what `GatewayClient.check_tools()` does at service start."""

    async def warm() -> None:
        fake = FakeGateway(base_url="http://warm.invalid")
        client = GatewayClient(
            mock_config("http://warm.invalid", client="gateway"),
            client_factory=fake.client,
            breakers=BreakerRegistry(),
        )
        assert await client.check_tools() == []
        await fake.aclose()

    asyncio.run(warm())


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(admin=True, base_url=BASE, webhook_backoff_s=0.0, fault_timeout_s=0.5)


@pytest.fixture
def mock_app(mock_settings: Settings) -> Any:
    return create_app(mock_settings)


@pytest.fixture
def mock_transport(mock_app: Any) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=mock_app)


@pytest.fixture
async def admin(mock_transport: httpx.ASGITransport) -> AsyncIterator[httpx.AsyncClient]:
    """A plain HTTP client on the mock, for `/_admin/*` and the OAuth authorize step."""
    async with httpx.AsyncClient(transport=mock_transport, base_url=BASE) as c:
        yield c


@pytest.fixture
def clock() -> tuple[Callable[[], float], Callable[[float], None]]:
    return monotonic_clock()


@pytest.fixture
def breakers(clock: tuple[Callable[[], float], Callable[[float], None]]) -> BreakerRegistry:
    return BreakerRegistry(clock=clock[0])


@pytest.fixture
def make_direct(
    mock_transport: httpx.ASGITransport, breakers: BreakerRegistry
) -> Callable[..., DirectClient]:
    def _make(profile: str = "request", client_id: str = "tower", secret: str | None = None) -> DirectClient:
        return DirectClient(
            mock_config(BASE, profile=profile, client_id=client_id),
            secret=secret if secret is not None else f"local-dev-{client_id}",
            transport=mock_transport,
            breakers=breakers,
        )

    return _make


@pytest.fixture
async def direct(make_direct: Callable[..., DirectClient]) -> AsyncIterator[DirectClient]:
    client = make_direct()
    yield client
    await client.aclose()


@pytest.fixture
async def fake_gateway(mock_transport: httpx.ASGITransport) -> AsyncIterator[FakeGateway]:
    gw = FakeGateway(base_url=BASE, transport=mock_transport)
    yield gw
    await gw.aclose()


@pytest.fixture
def make_gateway(fake_gateway: FakeGateway, breakers: BreakerRegistry) -> Callable[..., GatewayClient]:
    def _make(profile: str = "request") -> GatewayClient:
        return GatewayClient(
            mock_config(BASE, client="gateway", profile=profile),
            client_factory=fake_gateway.client,
            breakers=breakers,
        )

    return _make


@pytest.fixture
def gateway(make_gateway: Callable[..., GatewayClient]) -> GatewayClient:
    return make_gateway()


class Admin:
    """`/_admin/*` and the phone side of the OAuth authorize step, on the in-process mock."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def calls(self) -> int:
        r = await self.http.get("/_admin/state")
        assert r.status_code == 200
        return int(r.json()["calls"])

    async def fault(self, kind: str, n: int = 1) -> None:
        r = await self.http.post("/_admin/faults", json={"kind": kind, "n": n})
        assert r.status_code == 200, r.text

    async def event(self, msisdn: str, name: str, **kw: Any) -> None:
        r = await self.http.post(f"/_admin/lines/{msisdn}/events", json={"event": name, **kw})
        assert r.status_code == 200, r.text

    async def advance(self, seconds: float) -> None:
        r = await self.http.post("/_admin/clock", json={"advance_s": seconds})
        assert r.status_code == 200, r.text

    async def sink(self) -> list[dict[str, Any]]:
        r = await self.http.get("/_admin/sink")
        return [e["event"] for e in r.json()["events"]]

    async def auth_code(self, url: str, mock_client_id: str | None) -> str:
        """Follow `authorization_url()` as the phone would; return the code from the redirect."""
        headers = {"X-Mock-Client-Id": mock_client_id} if mock_client_id else {}
        r = await self.http.get(url, headers=headers)
        assert r.status_code == 302, r.text
        return str(httpx.URL(r.headers["location"]).params["code"])


@pytest.fixture
def mock(admin: httpx.AsyncClient) -> Admin:
    return Admin(admin)
