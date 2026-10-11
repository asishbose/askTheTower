#!/usr/bin/env python3
"""Spike B (prompt 02): AgentCore Gateway against a CAMARA-shaped OAuth 2 client-credentials target.

Throwaway evidence code. Nothing in `packages/` or `services/` imports it (tests/spikes asserts that).

Three subcommands:

- `stub` — the ~40-line target: `POST /oauth2/token` (client credentials, Basic or form credentials) and
  `POST /sim-swap/v2/check` → `{"swapped": false}` behind the issued bearer. Logs every token grant and
  check (no request bodies: they carry a phone number). Put it on a throwaway public URL, give Gateway
  `specs/camara/sim-swap.yaml` with `servers[0].url` set to it, and Identity the client id/secret.
- `probe --url <gateway>/mcp` — the minimal MCP client: `tools/list` → the generated names and input
  schemas (written to `--out`), classify them against what `camara_client.gateway` assumes, then N calls
  to the SIM Swap check tool and a p50/p95/p99 table. `--sigv4 --region` when the Gateway's inbound auth
  is `AWS_IAM` (as `deploy/terraform` configures it); `--bearer` for a JWT authorizer.
- `selftest` — the same probe against an in-process stand-in (`camara_client.testing.FakeGateway` naming
  tools the AgentCore way, forwarding to the stub over ASGI). Proves the script, not Gateway.

Assumptions under test (the build already depends on them; see docs/architecture/spikes/B-agentcore-gateway.md):
names `<target>___<operationId>` (or `<api>__<operationId>`), resolved at start-up by
`camara_client.gateway.resolve_tool_names` / `CARRIER_GATEWAY_TOOLS=discover`; arguments `{"body": …}`.

    uv run python scripts/spikes/b_gateway_probe.py stub --port 8766
    uv run python scripts/spikes/b_gateway_probe.py probe --url https://<gw>/mcp --sigv4 --region us-east-1
    uv run python scripts/spikes/b_gateway_probe.py selftest
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request  # module level: the route annotations must resolve

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts" / "spikes" / "B-gateway-tools.json"
CLIENT_ID = os.environ.get("SPIKE_B_CLIENT_ID", "spike-b")
CLIENT_SECRET = os.environ.get("SPIKE_B_CLIENT_SECRET", "spike-b-local-only")
OPERATION = "checkSimSwap"
CHECK_BODY = {"phoneNumber": "+15550000000", "maxAge": 240}  # 555-01xx fiction range, never a real line
log = logging.getLogger("spike_b")


# --- the stub target ----------------------------------------------------------------------------------
def create_stub(client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET) -> Any:
    app = FastAPI(title="spike-b-camara-stub")
    app.state.tokens, app.state.grants, app.state.checks = set(), [], 0

    @app.post("/oauth2/token")
    async def token(request: Request) -> dict[str, Any]:
        form = await request.form()
        cid, csec = form.get("client_id"), form.get("client_secret")
        method = "client_secret_post"
        basic = request.headers.get("authorization", "")
        if basic.lower().startswith("basic "):
            cid, _, csec = base64.b64decode(basic[6:]).decode().partition(":")
            method = "client_secret_basic"
        if form.get("grant_type") != "client_credentials" or (cid, csec) != (client_id, client_secret):
            raise HTTPException(401, {"error": "invalid_client"})
        tok = secrets.token_urlsafe(24)
        app.state.tokens.add(tok)
        app.state.grants.append({"method": method, "scope": form.get("scope")})
        log.info("token grant #%d via %s scope=%s", len(app.state.grants), method, form.get("scope"))
        return {"access_token": tok, "token_type": "Bearer", "expires_in": 3600}

    @app.post("/sim-swap/v2/check")
    async def check(request: Request) -> dict[str, bool]:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in app.state.tokens:
            raise HTTPException(401, {"status": 401, "code": "UNAUTHENTICATED", "message": "bad token"})
        body = await request.json()
        app.state.checks += 1
        log.info("check #%d body keys=%s", app.state.checks, sorted(body))
        return {"swapped": False}

    return app


# --- what the build assumed, as a function of what Gateway listed --------------------------------------
def classify(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Name convention and argument shape of the generated check tool vs. `camara_client.gateway`."""
    from camara_client.gateway import AGENTCORE_DELIMITER

    names = [t["name"] for t in tools]
    hits = [n for n in names if n.endswith(f"__{OPERATION}")]
    out: dict[str, Any] = {"tools": names, "check_tool": hits[0] if len(hits) == 1 else None}
    if len(hits) != 1:
        out["naming"] = f"REFUTED: {len(hits)} tools end in __{OPERATION}; set gateway-tools.json by hand"
        return out
    name = hits[0]
    if name == f"sim-swap{AGENTCORE_DELIMITER}{OPERATION}":
        out["naming"] = "HOLDS: <target>___<operationId> with the target named after the spec file"
    elif name == f"sim-swap__{OPERATION}":
        out["naming"] = "HOLDS: <api>__<operationId> (the RUN-ALL convention)"
    else:
        out["naming"] = "HOLDS (loose): unique …___/__<operationId> suffix; resolve_tool_names maps it"
    schema = next(t for t in tools if t["name"] == name).get("inputSchema") or {}
    props = schema.get("properties") or {}
    out["input_schema"] = schema
    if "body" in props:
        out["arguments"] = "HOLDS: request body under `body` (tool_arguments() is right)"
    elif {"phoneNumber", "maxAge"} & set(props):
        out["arguments"] = "REFUTED: body fields flattened — change camara_client.gateway.tool_arguments()"
    else:
        out["arguments"] = f"UNKNOWN: properties {sorted(props)} — inspect input_schema"
    return out


def pct(samples: list[float], p: float) -> float:
    s = sorted(samples)
    return s[max(0, min(len(s) - 1, round(p / 100 * len(s) + 0.5) - 1))]


async def probe(make_client: Callable[[], AbstractAsyncContextManager[Any]], n: int) -> dict[str, Any]:
    async with make_client() as client:
        listed = await client.list_tools()
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": getattr(t, "input_schema", None) or {},
            }
            for t in listed
        ]
        report = classify(tools)
        name = report.get("check_tool")
        if not name:
            return report
        args = {"body": CHECK_BODY} if report["arguments"].startswith("HOLDS") else dict(CHECK_BODY)
        samples: list[float] = []
        results: list[str] = []
        for _ in range(n):
            t0 = time.perf_counter()
            res = await client.call_tool(name, args, raise_on_error=False)
            samples.append((time.perf_counter() - t0) * 1000)
            results.append("error" if res.is_error else "".join(getattr(c, "text", "") for c in res.content))
    report["calls"] = n
    report["results"] = sorted(set(results))
    report["latency_ms"] = {k: round(pct(samples, p), 1) for k, p in (("p50", 50), ("p95", 95), ("p99", 99))}
    report["latency_ms"]["max"] = round(max(samples), 1)
    return report


def render(report: dict[str, Any], target: str) -> str:
    lat = report.get("latency_ms", {})
    return "\n".join(
        [
            f"## Spike B probe — {target}",
            "",
            f"- tools listed: {len(report['tools'])}; check tool: `{report.get('check_tool')}`",
            f"- naming: {report.get('naming')}",
            f"- arguments: {report.get('arguments', '—')}",
            f"- results: {report.get('results', '—')}",
            "",
            "| calls | p50 ms | p95 ms | p99 ms | max ms |",
            "|---:|---:|---:|---:|---:|",
            f"| {report.get('calls', 0)} | {lat.get('p50', '—')} | {lat.get('p95', '—')} | {lat.get('p99', '—')} | {lat.get('max', '—')} |",
            "",
        ]
    )


async def selftest(n: int) -> dict[str, Any]:
    import httpx
    from camara_client.gateway import AGENTCORE_DELIMITER
    from camara_client.testing import FakeGateway

    stub = create_stub()
    gw = FakeGateway(
        base_url="http://stub.local",
        transport=httpx.ASGITransport(app=stub),
        client_id=CLIENT_ID,
        secret=CLIENT_SECRET,
        tool_name=lambda api, op: f"{api}{AGENTCORE_DELIMITER}{op}",
    )
    try:
        report = await probe(gw.client, n)
    finally:
        await gw.aclose()
    report["stub"] = {"token_grants": len(stub.state.grants), "checks": stub.state.checks}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stub")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8766)
    p = sub.add_parser("probe")
    p.add_argument("--url", required=True, help="the Gateway's MCP URL")
    p.add_argument("--sigv4", action="store_true", help="sign with SigV4 (Gateway inbound auth AWS_IAM)")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--bearer", default=os.environ.get("SPIKE_B_BEARER"))
    for q in (p, sub.add_parser("selftest")):
        q.add_argument("-n", type=int, default=20)
        q.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sys.path.insert(0, str(ROOT))

    if args.cmd == "stub":
        import uvicorn

        uvicorn.run(create_stub(), host=args.host, port=args.port)
        return 0
    if args.cmd == "selftest":
        report, target = asyncio.run(selftest(args.n)), "in-process stand-in (NOT Gateway)"
    else:
        from fastmcp import Client

        auth: Any = None
        if args.sigv4:
            from camara_client.aws import SigV4Auth

            auth = SigV4Auth(args.region)
        elif args.bearer:
            auth = args.bearer
        report, target = asyncio.run(probe(lambda: Client(args.url, auth=auth), args.n)), args.url
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        from tests.privacy.patterns import E164_STRICT  # spec examples carry phone-shaped strings

        args.out.write_text(E164_STRICT.sub("<digits>", json.dumps(report, indent=2)), encoding="utf-8")
    print(render(report, target))
    ok = (
        str(report.get("naming", "")).startswith("HOLDS")
        and str(report.get("arguments", "")).startswith("HOLDS")
        and report.get("results") == ['{"swapped":false}']
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
