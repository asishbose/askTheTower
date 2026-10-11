"""Backend swap (05 §4, §7): two mock carriers with different scenarios; flipping the config's base URL
moves the next call to the other one — no code change, same client code path. Once in-process (two
ASGI apps behind one host-routing transport) and once over real sockets (two uvicorn servers)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from camara_client import BreakerRegistry, CarrierConfig, LineRef, make_client
from mock_carrier.app import create_app
from mock_carrier.settings import Settings
from mock_carrier.testing import ASISH

pytestmark = pytest.mark.integration

LINE = LineRef("line-asish", ASISH)


class HostRouter(httpx.AsyncBaseTransport):
    """Route by host to in-process apps — two "carriers" in one test process."""

    def __init__(self, apps: dict[str, Any]) -> None:
        self.routes = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.routes[request.url.host].handle_async_request(request)


def _env(base_url: str, profile: str = "request") -> dict[str, str]:
    return {
        "CARRIER_PROFILE": profile,
        "CARRIER_CLIENT": "direct",
        "CARRIER_BACKEND": "mock",
        "CARRIER_BASE_URL": base_url,
        "CARRIER_CLIENT_ID": "tower",
        "CARRIER_SECRET_REF": "env:CARRIER_CLIENT_SECRET",
        "CARRIER_CLIENT_SECRET": "local-dev-tower",
    }


async def _flip_and_observe(env_a: dict[str, str], env_b: dict[str, str], transport: Any = None) -> None:
    breakers = BreakerRegistry()
    a = make_client(CarrierConfig.from_env(env_a), env=env_a, transport=transport, breakers=breakers)
    assert (await a.reachability(LINE)).reachable is True
    assert (await a.sim_swap_check(LINE, 72)).swapped is False
    await a.aclose()

    b = make_client(CarrierConfig.from_env(env_b), env=env_b, transport=transport, breakers=breakers)
    assert (await b.reachability(LINE)).reachable is False  # transplant scenario: dark since +1 min
    assert (await b.sim_swap_check(LINE, 72)).swapped is True
    await b.aclose()

    back = make_client(CarrierConfig.from_env(env_a), env=env_a, transport=transport, breakers=breakers)
    assert (await back.reachability(LINE)).reachable is True
    await back.aclose()


async def _warm(base_url: str, transport: Any = None) -> None:
    """A mock's first request pays FastAPI's lazy imports (~0.75 s): keep it out of the 300 ms budget."""
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=30) as c:
        await c.get("/healthz")
        await c.post("/sim-swap/v2/check", json={})
        await c.post(
            "/oauth2/token", data={"grant_type": "client_credentials"}, auth=("tower", "local-dev-tower")
        )


async def _prepare_b(base_url: str, transport: Any = None) -> None:
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as c:
        assert (await c.post("/_admin/clock", json={"advance_s": 120})).status_code == 200
        r = await c.post(f"/_admin/lines/{ASISH}/events", json={"event": "sim_swap"})
        assert r.status_code == 200


async def test_swap_in_process() -> None:
    app_a = create_app(Settings(admin=True, scenario="demo", base_url="http://carrier-a.test"))
    app_b = create_app(Settings(admin=True, scenario="transplant", base_url="http://carrier-b.test"))
    router = HostRouter({"carrier-a.test": app_a, "carrier-b.test": app_b})
    await _warm("http://carrier-a.test", router)
    await _warm("http://carrier-b.test", router)
    await _prepare_b("http://carrier-b.test", router)
    await _flip_and_observe(_env("http://carrier-a.test"), _env("http://carrier-b.test"), router)


class _Server:
    def __init__(self, app: Any) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def two_carriers() -> Iterator[tuple[str, str]]:
    with (
        _Server(create_app(Settings(admin=True, scenario="demo"))) as a,
        _Server(create_app(Settings(admin=True, scenario="transplant"))) as b,
    ):
        yield a, b


async def test_swap_over_real_sockets(two_carriers: tuple[str, str]) -> None:
    a, b = two_carriers
    await _warm(a)
    await _warm(b)
    await _prepare_b(b)
    # Over real sockets to threaded servers on a shared dev box, a cold connection can exceed 300 ms;
    # this test is about the swap, not the budget (that is test_timeouts), so it uses the 5 s profile.
    await _flip_and_observe(_env(a, "proactive"), _env(b, "proactive"))
