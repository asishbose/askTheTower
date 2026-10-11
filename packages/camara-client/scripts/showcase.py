#!/usr/bin/env python3
"""`make showcase-gateway` (05 §8, testing-and-showcase §2.5).

ENV=local (default): everything in one process, nothing to start —
  1. list the MCP tools a Gateway generates from the vendored CAMARA specs (the in-process FakeGateway
     does what AgentCore Gateway does: one tool per operation, named `<api>__<operationId>`);
  2. `sim_swap_check` through GatewayClient → false; advance the mock's clock past the scripted swap;
     call again → true; DirectClient gives the identical result;
  3. swap `CARRIER_BASE_URL` to a second mock carrier running another scenario; the next call goes
     there — config only, no code change.

ENV=aws|eks: the same calls through `make_client(CarrierConfig.from_env())` — set CARRIER_CLIENT=gateway,
CARRIER_GATEWAY_URL and CARRIER_GATEWAY_TOOLS (live Gateway wiring lands with prompt 13).

Simulation aids, said on screen: `/_admin/*` on the mock moves its clock; the FakeGateway is a stand-in.
The phone numbers are 555-01xx fiction and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from camara_client import BreakerRegistry, CarrierConfig, DirectClient, GatewayClient, LineRef, make_client
from camara_client.testing import FakeGateway, mock_config

ASISH = "+16135550101"  # fiction (555-01xx); stays inside the carrier call
LINE = LineRef("line-asish", ASISH)


def say(text: str = "") -> None:
    print(text, flush=True)


async def local() -> None:
    from mock_carrier.app import create_app
    from mock_carrier.settings import Settings

    a_url, b_url = "http://carrier-a.mock", "http://carrier-b.mock"
    apps = {
        "carrier-a.mock": create_app(Settings(admin=True, scenario="demo", base_url=a_url)),
        "carrier-b.mock": create_app(Settings(admin=True, scenario="transplant", base_url=b_url)),
    }
    routes = {h: httpx.ASGITransport(app=app) for h, app in apps.items()}

    class Router(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return await routes[request.url.host].handle_async_request(request)

    router = Router()
    fake = FakeGateway(base_url=a_url, transport=router)
    breakers = BreakerRegistry()
    gateway = GatewayClient(
        mock_config(a_url, client="gateway"), client_factory=fake.client, breakers=breakers
    )
    direct = DirectClient(mock_config(a_url), secret="local-dev-tower", transport=router, breakers=breakers)

    for url in (a_url, b_url):  # first request pays FastAPI's lazy imports; keep it out of the 300 ms budget
        async with httpx.AsyncClient(transport=router, base_url=url) as warm:
            await warm.get("/healthz")
            await warm.post("/sim-swap/v2/check", json={})

    say("== 1. CAMARA operations as MCP tools (generated from specs/camara, Fall25) ==")
    async with fake.client() as c:
        for tool in sorted(await c.list_tools(), key=lambda t: t.name):
            say(f"  {tool.name:<70} {tool.description}")
    say(f"  check_tools(): missing = {await gateway.check_tools()}")

    say("\n== 2. sim_swap_check through the Gateway path, then flip the mock ==")
    say(
        f"  gateway  sim_swap_check(line-asish, 72h) → {(await gateway.sim_swap_check(LINE, 72)).model_dump()}"
    )
    async with httpx.AsyncClient(transport=router, base_url=a_url) as admin:
        r = await admin.post("/_admin/clock", json={"advance_s": 12 * 60})
        say(f"  mock: clock +12 min → fired {[f['event'] for f in r.json()['fired']]}  (simulation aid)")
    g = await gateway.sim_swap_check(LINE, 72)
    d = await direct.sim_swap_check(LINE, 72)
    say(f"  gateway  sim_swap_check(line-asish, 72h) → {g.model_dump()}")
    say(f"  direct   sim_swap_check(line-asish, 72h) → {d.model_dump()}   identical: {g == d}")

    say("\n== 3. backend swap: CARRIER_BASE_URL a → b, nothing else ==")
    for url in (a_url, b_url):
        env = {"CARRIER_BASE_URL": url, "CARRIER_CLIENT_SECRET": "local-dev-tower"}
        client = make_client(CarrierConfig.from_env(env), env=env, transport=router, breakers=breakers)
        async with httpx.AsyncClient(transport=router, base_url=url) as admin:
            if url == b_url:
                await admin.post("/_admin/clock", json={"advance_s": 120})
        reach = await client.reachability(LINE)
        say(f"  CARRIER_BASE_URL={url:<24} reachability → reachable={reach.reachable} {reach.connectivity}")
        await client.aclose()
    say("\n  (carrier-b runs scenarios/transplant.yaml: the line went dark at +1 min)")
    await direct.aclose()
    await fake.aclose()


async def remote() -> None:
    config = CarrierConfig.from_env()
    client = make_client(config)
    say(f"client={config.client} backend={config.backend} base_url={config.base_url}")
    if isinstance(client, GatewayClient):
        say(f"Gateway tools missing from the map: {await client.check_tools()}")
    try:
        result: Any = await client.sim_swap_check(LINE, 72)
        say(f"sim_swap_check(line-asish, 72h) → {json.dumps(result.model_dump())}")
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Carrier client showcase (05 §8)")
    ap.add_argument("--env", default="local", choices=["local", "eks", "aws"])
    args = ap.parse_args(argv)
    say(f"camara-client showcase — env={args.env} — {datetime.now(UTC):%Y-%m-%d %H:%M}Z")
    asyncio.run(local() if args.env == "local" else remote())
    return 0


if __name__ == "__main__":
    sys.exit(main())
