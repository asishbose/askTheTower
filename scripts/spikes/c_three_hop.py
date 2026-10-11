#!/usr/bin/env python3
"""Spike C (prompt 02): what does a 3-hop tool call cost before any real code exists?

Throwaway evidence code. Nothing in `packages/` or `services/` imports it (tests/spikes asserts that).

A FastMCP server with one tool, `three_hops`, that does exactly the shape the budget (02 §4) assumes:
one DynamoDB `GetItem`, two parallel HTTP calls to the spike B stub (`POST /sim-swap/v2/check`, bearer from
its `/oauth2/token`, fetched once and cached), one `PutItem`, then returns JSON. 200 calls over Streamable
HTTP, p50/p95/p99 as a markdown table — using the timing, percentile, DynamoDB and in-process plumbing of
`scripts/latency.py` (the kept harness that prompt 08 grew out of this spike), so both report the same way.

Modes:
- default: everything in-process (stub over ASGI; DynamoDB Local via testcontainers when Docker is up,
  else moto; `--ddb` forces one). Labelled "harness only".
- `--stub-url http://host:8766`: the stub running elsewhere (`b_gateway_probe.py stub`), real sockets.
- `--gateway-url https://<gw>/mcp --sigv4`: the two HTTP calls go through AgentCore Gateway's
  `sim-swap___checkSimSwap` tool instead (TODO(human): needs spike B's Gateway).

    uv run python scripts/spikes/c_three_hop.py --out artifacts/spikes/C-three-hop.md
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LABEL = "harness only"
TABLE = "spike-c"
CHECK_TOOL = "sim-swap___checkSimSwap"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


latency = _load("spike_c_latency", ROOT / "scripts" / "latency.py")
spike_b = _load("spike_c_stub", ROOT / "scripts" / "spikes" / "b_gateway_probe.py")

CarrierCall = Callable[[], Awaitable[dict[str, Any]]]


def stub_caller(http: Any, base: str) -> CarrierCall:
    """One SIM Swap check against the spike B stub, client-credentials token fetched once."""
    token: dict[str, str] = {}

    async def call() -> dict[str, Any]:
        if "t" not in token:
            r = await http.post(
                f"{base}/oauth2/token",
                data={"grant_type": "client_credentials", "scope": "sim-swap:check"},
                auth=(spike_b.CLIENT_ID, spike_b.CLIENT_SECRET),
            )
            r.raise_for_status()
            token["t"] = r.json()["access_token"]
        r = await http.post(
            f"{base}/sim-swap/v2/check",
            json=spike_b.CHECK_BODY,
            headers={"Authorization": f"Bearer {token['t']}"},
        )
        r.raise_for_status()
        return dict(r.json())

    return call


def gateway_caller(client: Any) -> CarrierCall:
    async def call() -> dict[str, Any]:
        import json

        res = await client.call_tool(CHECK_TOOL, {"body": spike_b.CHECK_BODY}, raise_on_error=False)
        if res.is_error:
            raise RuntimeError("gateway check failed")
        return dict(json.loads("".join(getattr(c, "text", "") for c in res.content)))

    return call


def create_server(ddb: Any, table: str, carrier: CarrierCall) -> Any:
    from fastmcp import FastMCP

    mcp = FastMCP("spike-c-three-hop")

    @mcp.tool(name="three_hops", description="GetItem, two parallel carrier calls, PutItem.")
    async def three_hops(key: str = "line-1") -> dict[str, Any]:
        item = await asyncio.to_thread(ddb.get_item, TableName=table, Key={"pk": {"S": key}})
        a, b = await asyncio.gather(carrier(), carrier())
        n = int(item.get("Item", {}).get("n", {}).get("N", "0")) + 1
        await asyncio.to_thread(ddb.put_item, TableName=table, Item={"pk": {"S": key}, "n": {"N": str(n)}})
        return {"n": n, "swapped": a["swapped"] or b["swapped"]}

    return mcp.http_app(path="/mcp", stateless_http=True, json_response=True)


def ensure_table(ddb: Any, table: str) -> None:
    ddb.create_table(
        TableName=table,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


async def run(
    ddb: Any, n: int, *, stub_url: str | None = None, gateway: Any = None, warmup: int = 5
) -> list[Any]:
    """Time `n` calls of `three_hops` (after `warmup`), plus a bare GetItem row so the DB share is visible."""
    import time

    import httpx

    table = f"{TABLE}-{uuid.uuid4().hex[:6]}"
    ensure_table(ddb, table)
    # DynamoDB Local's JVM is slow on its first requests: the warm-up below absorbs it (documented in C-latency.md).
    t0 = time.perf_counter()
    ddb.get_item(TableName=table, Key={"pk": {"S": "warm"}})
    first_ddb_ms = (time.perf_counter() - t0) * 1000
    if gateway is not None:
        transport, base, carrier_label = None, "", "AgentCore Gateway"
    elif stub_url:
        transport, base, carrier_label = None, stub_url.rstrip("/"), f"stub at {stub_url}"
    else:
        transport, base, carrier_label = (
            httpx.ASGITransport(app=spike_b.create_stub()),
            "http://stub.local",
            "stub in-process (ASGI)",
        )
    async with httpx.AsyncClient(transport=transport, timeout=10) as http:
        carrier = gateway_caller(gateway) if gateway is not None else stub_caller(http, base)
        app = create_server(ddb, table, carrier)
        async with latency.serving(app), latency.asgi_client(app, {}) as client:
            samples, _ = await latency.timed(client, "three_hops", {"key": "line-1"}, n, warmup)
    getitem: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        ddb.get_item(TableName=table, Key={"pk": {"S": "line-1"}})
        getitem.append((time.perf_counter() - t0) * 1000)
    return [
        latency.Row(
            "three_hops — GetItem + 2×carrier ∥ + PutItem",
            "three_hops",
            samples,
            True,
            f"carrier: {carrier_label}; first-ever GetItem on this store took {first_ddb_ms:.0f} ms (warm-up, not counted)",
        ),
        latency.Row("GetItem alone (same table)", "-", getitem, False, "the DynamoDB share of one hop"),
    ]


def render(rows: list[Any], env: dict[str, str]) -> str:
    lines = [
        f"# Spike C latency — {LABEL}",
        "",
        f"> **{LABEL}.** A throwaway FastMCP tool with the hot path's shape (one GetItem, two parallel carrier "
        "calls, one PutItem) and none of Tower's code. It grounds the p95 < 400 ms gate; Tower's own numbers "
        "are in `artifacts/latency.md`.",
        "",
        *[f"- **{k}:** {v}" for k, v in env.items()],
        f"- **calls:** {len(rows[0].samples)} per row, sequential, after 5 uncounted warm-up calls",
        "",
        "| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate (p95 < 400 ms) |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        gate = ("pass" if r.passed else "**FAIL**") if r.gated else "reported (not gated)"
        lines.append(
            f"| {r.path} | {len(r.samples)} | {r.pct(50):.1f} | {r.p95:.1f} | {r.pct(99):.1f} | {max(r.samples):.1f} | {gate} |"
        )
    lines += ["", *[f"- *{r.path}*: {r.note}" for r in rows if r.note], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=int(os.environ.get("LATENCY_N", "200")))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--ddb", choices=["auto", "local", "moto"], default="auto")
    ap.add_argument("--stub-url", default=None)
    ap.add_argument("--gateway-url", default=None)
    ap.add_argument("--sigv4", action="store_true")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    kind = a.ddb if a.ddb != "auto" else ("local" if latency._docker() else "moto")

    async def go(ddb: Any) -> list[Any]:
        if not a.gateway_url:
            return await run(ddb, a.n, stub_url=a.stub_url)
        from fastmcp import Client

        auth: Any = None
        if a.sigv4:
            from camara_client.aws import SigV4Auth

            auth = SigV4Auth(a.region)
        async with Client(a.gateway_url, auth=auth) as gw:
            return await run(ddb, a.n, gateway=gw)

    with latency.dynamodb(kind) as (ddb, store_label):
        rows = asyncio.run(go(ddb))
    env = latency.environment(store_label, "throwaway FastMCP server, in-process ASGI (Streamable HTTP)")
    env.pop("mock", None)
    text = render(rows, env)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    return 0 if all(r.passed for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
