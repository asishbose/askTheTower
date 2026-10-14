#!/usr/bin/env python3
"""Hot-path latency harness (spike C, prompt 08): N calls per tool over Streamable HTTP; p50/p95/p99 table.

Two modes:

- `--in-process` (default when no `--url`): mock carrier + DynamoDB (DynamoDB Local via testcontainers when
  Docker is up, else moto; `--ddb` forces one) + Tower, all in this process; calls go through the real MCP
  Streamable HTTP stack over an ASGI transport. The result is labelled **"in-process, not representative"**:
  no network, one Python process, the mock answers in microseconds. It measures *our* code (02 §4).
- `--url https://…/mcp` with `--bearer`/`--user` (local bearer) or `--jwt`: a running Tower (compose, EKS,
  AgentCore). `ENV=aws make latency-aws` uses this with `--out artifacts/latency-aws.md`.

Paths reported separately: `line_is_ok` live (one fact set: SIM swap check + call forwarding status, in
parallel), `line_is_ok` stored (fresh `Watches.last_state`, zero carrier calls), `is_reachable` live (one fact
set: one reachability status call), `watch_line` status. Gate: p95 < 400 ms on the live request paths
(testing-and-showcase §3). Keep the first number even if it's bad. In-process, each row also counts the carrier
requests its measured calls made (`Row.carrier_calls`, by CAMARA path): that, not a p50 comparison, is what
proves the stored path skips the carrier.

    uv run python scripts/latency.py --out artifacts/latency.md
    uv run python scripts/latency.py --url http://localhost:8000/mcp --bearer "$TOWER_BEARER" --user user-asish
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

GATE_MS = 400.0
LABEL_IN_PROCESS = "in-process, not representative"
MOCK_START = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # scenarios/demo.yaml clock
LOCAL_BEARER = "latency-local-bearer"
ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Row:
    path: str
    tool: str
    samples: list[float]
    gated: bool
    note: str = ""
    carrier_calls: dict[str, int] | None = (
        None  # CAMARA path -> requests during the measured calls (in-process)
    )

    def carrier_per_call(self) -> float | None:
        if self.carrier_calls is None or not self.samples:
            return None
        return sum(self.carrier_calls.values()) / len(self.samples)

    def pct(self, p: float) -> float:
        s = sorted(self.samples)
        if not s:
            return float("nan")
        k = max(0, min(len(s) - 1, round(p / 100 * len(s) + 0.5) - 1))
        return s[k]

    @property
    def p95(self) -> float:
        return self.pct(95)

    @property
    def passed(self) -> bool:
        return (not self.gated) or self.p95 < GATE_MS


class CountingTransport(httpx.AsyncBaseTransport):
    """Passes requests to `inner` and records each CAMARA path (OAuth and `/_admin` excluded): the carrier
    calls a path makes, counted rather than inferred from timings."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith(("/oauth2", "/_admin", "/.well-known")):
            self.paths.append(path)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.paths:
            out[p] = out.get(p, 0) + 1
        return out


# --- clients -------------------------------------------------------------------------------------------
class RawMcp:
    """Bare JSON-RPC `tools/call` POSTs (stateless Streamable HTTP needs no session): the timing covers HTTP,
    the MCP layer and the tool, not an MCP client library's own result parsing."""

    def __init__(self, http: Any, path: str, headers: dict[str, str]) -> None:
        self.http = http
        self.path = path
        self.headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        } | headers
        self.n = 0

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.n += 1
        body = {
            "jsonrpc": "2.0",
            "id": self.n,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        r = await self.http.post(self.path, json=body, headers=self.headers)
        r.raise_for_status()
        text = r.text
        if text.startswith("event:") or text.startswith("data:"):  # an SSE-framed answer
            text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
        import json

        msg = json.loads(text)
        result: dict[str, Any] = msg["result"]
        if result.get("isError"):
            raise RuntimeError(f"{tool} returned an MCP error")
        return dict(result.get("structuredContent") or {})


@asynccontextmanager
async def asgi_client(app: Any, headers: dict[str, str]) -> AsyncIterator[RawMcp]:
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://tower.local", timeout=30
    ) as http:
        yield RawMcp(http, "/mcp", headers)


class SdkMcp:
    """`ref_client.TowerClient` (one AgentCore session id for the process, `TowerConfig.headers`) with `RawMcp`'s
    one method, for AgentCore Runtime: bare POSTs without a session id each land on a new microVM (a ~7 s cold
    start per call, Tower's log 2026-10-14). A failed call closes the client; the next one reconnects. The timing
    includes the SDK's result parsing — sub-millisecond next to the network hops."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.tower: Any = None

    async def open(self) -> None:
        from ref_client.mcp_client import TowerClient

        self.tower = await TowerClient(self.config).__aenter__()

    async def close(self) -> None:
        if self.tower is not None:
            tower, self.tower = self.tower, None
            try:
                await tower.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - a broken session closes as best it can
                pass

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.tower is None:
            await self.open()
        try:
            result: dict[str, Any] = await self.tower.call(tool, args)
        except Exception:
            # The next call reconnects, on a fresh AgentCore session: the old id kept answering "cannot connect"
            # for ~10 s after its microVM went away (2026-10-14 run). One failed call must not end the run.
            await self.close()
            self.config = dataclasses.replace(self.config, session_id=f"latency-{uuid.uuid4()}")
            raise
        return result


@asynccontextmanager
async def url_client(url: str, headers: dict[str, str]) -> AsyncIterator[RawMcp | SdkMcp]:
    import httpx

    if "bedrock-agentcore" in url:
        from ref_client.mcp_client import TowerConfig

        sdk = SdkMcp(TowerConfig(url=url, authorization=headers.get("Authorization"), timeout_s=30.0))
        try:
            yield sdk
        finally:
            await sdk.close()
        return
    async with httpx.AsyncClient(timeout=30) as http:
        yield RawMcp(http, url, headers)


async def timed(
    client: RawMcp | SdkMcp,
    tool: str,
    args: dict[str, Any],
    n: int,
    warmup: int = 5,
    measuring: Callable[[], None] | None = None,
    failures: list[str] | None = None,
) -> tuple[list[float], list[str]]:
    """`n` sequential calls (after `warmup` uncounted ones); returns ms per call and each result's source.
    `measuring` runs between the warm-up and the counted calls. With `failures` (a deployment over the network),
    a failed call is recorded there (its error, never a value) instead of ending the run, and is not a sample;
    more than a tenth of `n` failing ends it."""

    async def call() -> dict[str, Any] | None:
        try:
            return await client.call_tool(tool, args)
        except Exception as e:
            if failures is None:
                raise
            failures.append(f"{type(e).__name__}: {e}")
            print(f"  {tool}({args.get('line')}): call failed ({failures[-1]})", file=sys.stderr, flush=True)
            if len(failures) > max(1, n // 10):
                raise
            return None

    for _ in range(warmup):
        await call()
    if measuring:
        measuring()
    out: list[float] = []
    sources: list[str] = []
    for i in range(n):
        t0 = time.perf_counter()
        res = await call()
        if res is None:
            continue
        out.append((time.perf_counter() - t0) * 1000)
        sources.append(str(res.get("facts", {}).get("source")))
        if (i + 1) % 50 == 0:
            line = args.get("line")
            print(f"  {tool}({line}): {i + 1}/{n}, last {out[-1]:.0f} ms", file=sys.stderr, flush=True)
    return out, sources


# --- the in-process stack ------------------------------------------------------------------------------
def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0  # noqa: S603,S607
    except (OSError, subprocess.TimeoutExpired):
        return False


@contextmanager
def dynamodb(kind: str) -> Iterator[tuple[Any, str]]:
    """A boto3 DynamoDB client: `local` (DynamoDB Local in a container) or `moto` (in-process)."""
    import boto3

    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        os.environ.setdefault(k, v)
    if kind == "local":
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs

        c = (
            DockerContainer("amazon/dynamodb-local:latest")
            .with_exposed_ports(8000)
            .with_command("-jar DynamoDBLocal.jar -inMemory -sharedDb")
        )
        with c:
            wait_for_logs(c, "Initializing DynamoDB Local", timeout=60)
            endpoint = f"http://{c.get_container_host_ip()}:{c.get_exposed_port(8000)}"
            yield (
                boto3.client("dynamodb", region_name="us-east-1", endpoint_url=endpoint),
                "DynamoDB Local (testcontainers)",
            )
    else:
        from moto import mock_aws

        with mock_aws():
            yield boto3.client("dynamodb", region_name="us-east-1"), "moto (in-process)"


@asynccontextmanager
async def serving(app: Any) -> AsyncIterator[None]:
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await ready.wait()
    try:
        yield
    finally:
        stop.set()
        await task


async def run_in_process(
    store: Any, n: int = 200, progress: Callable[[str], None] | None = None
) -> list[Row]:
    """Seed the demo, start Tower in-process against the in-process mock, and measure each path."""
    import httpx
    from camara_client import BreakerRegistry, DirectClient
    from camara_client.testing import mock_config
    from mock_carrier.app import create_app as create_mock
    from mock_carrier.settings import Settings as MockSettings
    from mock_carrier.testing import ASISH, BASE, MOM
    from tower_consent import LastState, LocalLineIdHasher, LocalMsisdnCipher
    from tower_mcp.deps import FixedClock, RecordingAlerts, Settings, build_deps
    from tower_mcp.seed import seed_demo, seed_watch
    from tower_mcp.server import create_app

    say = progress or (lambda _m: None)
    store.ensure_tables()
    hasher, cipher = LocalLineIdHasher(os.urandom(32)), LocalMsisdnCipher(os.urandom(32))
    seed = seed_demo(
        store, hasher, cipher, asish_e164=ASISH, mom_e164=MOM, now=MOCK_START - timedelta(days=1)
    )
    # Mom's line has a fresh Watch state (what Alerts keeps current): the stored-state path. Asish's has none.
    seed_watch(
        store,
        seed.mom_line,
        seed.asish_user,
        LastState(
            sim_change_at=MOCK_START - timedelta(days=54),
            cf_status="none",
            reachable=True,
            at=MOCK_START - timedelta(minutes=2),
        ),
        profile="care",
    )
    mock = create_mock(MockSettings(admin=True, base_url=BASE))
    transport = httpx.ASGITransport(app=mock)
    counting = CountingTransport(transport)
    carrier = DirectClient(
        mock_config(BASE), secret="local-dev-tower", transport=counting, breakers=BreakerRegistry()
    )
    deps = build_deps(
        Settings(env="local", bearer=LOCAL_BEARER, carrier_support_number="611"),
        env={},
        store=store,
        carrier=carrier,
        cipher=cipher,
        clock=FixedClock(MOCK_START),
        alerts=RecordingAlerts(),
    )
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as admin:
        await admin.get("/_admin/clock")  # the mock's first request is slow (FastAPI warm-up)
    app = create_app(deps)
    rows: list[Row] = []
    headers = {"Authorization": f"Bearer {LOCAL_BEARER}", "X-Tower-User": seed.asish_user}
    try:
        async with serving(app), asgi_client(app, headers) as client:
            plan: list[tuple[str, str, dict[str, Any], bool, str]] = [
                (
                    "line_is_ok — live (carrier)",
                    "line_is_ok",
                    {"line": "self"},
                    True,
                    "no Watch: SIM swap check + call forwarding in parallel (no swap, so no date call)",
                ),
                (
                    "line_is_ok — stored state (Watch)",
                    "line_is_ok",
                    {"line": "mom"},
                    False,
                    "fresh Watches.last_state: zero carrier calls",
                ),
                (
                    "is_reachable — live (carrier)",
                    "is_reachable",
                    {"line": "mom"},
                    True,
                    "one Device Reachability Status call",
                ),
                (
                    "watch_line — status",
                    "watch_line",
                    {"line": "self"},
                    False,
                    "grants + recent_checks reads; no carrier call",
                ),
            ]
            for path, tool, args, gated, note in plan:
                say(f"{path}: {n} calls")
                samples, sources = await timed(client, tool, args, n, measuring=counting.paths.clear)
                if tool == "line_is_ok":
                    expected = "watch" if "stored" in path else "carrier"
                    assert set(sources) == {expected}, f"{path}: sources {set(sources)}"
                rows.append(Row(path, tool, samples, gated, note, carrier_calls=counting.counts()))
    finally:
        await carrier.aclose()
    return rows


async def run_against_url(url: str, headers: dict[str, str], n: int) -> list[Row]:
    rows: list[Row] = []
    async with url_client(url, headers) as client:
        for tool, args in (
            ("line_is_ok", {"line": "self"}),
            ("line_is_ok", {"line": "mom"}),
            ("is_reachable", {"line": "mom"}),
            ("watch_line", {"line": "self"}),
        ):
            failures: list[str] = []
            samples, sources = await timed(client, tool, args, n, failures=failures)
            src = "/".join(sorted(set(sources)))
            note = ""
            if failures:
                kinds = sorted(set(failures))
                note = f"{len(failures)} call(s) failed and are not in the samples: {'; '.join(kinds)}"
            rows.append(
                Row(
                    f"{tool}({args['line']}) — source {src}",
                    tool,
                    samples,
                    tool != "watch_line" and "watch" not in src,
                    note,
                )
            )
    return rows


# --- report --------------------------------------------------------------------------------------------
def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "?"


def environment(store_label: str, target: str) -> dict[str, str]:
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}",
        "target": target,
        "store": store_label,
        "mock": f"mock-carrier {_version('mock-carrier')} (scenario demo.yaml, clock {MOCK_START:%Y-%m-%dT%H:%MZ})",
        "fastmcp": _version("fastmcp"),
    }


def _counted(r: Row) -> str:
    per = r.carrier_per_call()
    return "" if per is None else f" — carrier requests counted: {per:g} per call"


def render(rows: list[Row], env: dict[str, str], label: str) -> str:
    lines = [
        f"# Tower latency — {label}",
        "",
        f"> **{label}.** "
        + (
            "Mock carrier, DynamoDB and Tower in one Python process; calls go through the real MCP Streamable HTTP "
            "stack over an ASGI transport, so there is no network hop and the mock answers in microseconds. This "
            "measures our code path, not a deployment. The real numbers come from `make latency-aws` (deferred)."
            if label == LABEL_IN_PROCESS
            else "Measured against a running Tower over the network."
        ),
        "",
        *[f"- **{k}:** {v}" for k, v in env.items()],
        f"- **calls:** {len(rows[0].samples) if rows else 0} per path, sequential, after 5 uncounted warm-up calls; client-side wall time per JSON-RPC `tools/call` POST",
        "",
        "| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate (p95 < 400 ms) |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        gate = ("pass" if r.passed else "**FAIL**") if r.gated else "reported (not gated)"
        lines.append(
            f"| {r.path} | {len(r.samples)} | {r.pct(50):.1f} | {r.p95:.1f} | {r.pct(99):.1f} | {max(r.samples):.1f} | {gate} |"
        )
    lines += ["", *[f"- *{r.path}*: {r.note}{_counted(r)}" for r in rows if r.note]]
    lines += [
        "",
        "Where the time goes: run Tower with `LOG_LEVEL=debug`; each call logs per-step timings "
        "(`resolve`, `load`, `carrier`, `policy`, `audit`) with `line_id` only.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=int(os.environ.get("LATENCY_N", "200")))
    ap.add_argument("--out", default=None, help="write the markdown table here (also printed)")
    ap.add_argument("--url", default=os.environ.get("TOWER_URL"))
    ap.add_argument("--bearer", default=os.environ.get("TOWER_BEARER"))
    ap.add_argument("--user", default=os.environ.get("TOWER_USER", "user-asish"))
    ap.add_argument("--jwt", default=os.environ.get("TOWER_JWT"))
    ap.add_argument("--ddb", choices=["auto", "local", "moto"], default="auto")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    if a.url and not a.jwt and os.environ.get("ENV") == "aws":
        # AgentCore's JWT authorizer refuses the local bearer: sign the user in to Cognito (ref_client.cognito;
        # client id and subs from artifacts/, the password from COGNITO_PASSWORD_<NAME> in the root .env).
        from ref_client.cognito import CognitoUsers

        a.jwt = CognitoUsers.from_artifacts(ROOT / "artifacts").access_token(a.user.removeprefix("user-"))
    if a.url:
        headers = {"Authorization": f"Bearer {a.jwt or a.bearer}"}
        if not a.jwt:
            headers["X-Tower-User"] = a.user
        rows = asyncio.run(run_against_url(a.url, headers, a.n))
        text = render(rows, environment("as deployed", a.url), "measured against " + a.url)
    else:
        from tower_consent import Store

        kind = a.ddb if a.ddb != "auto" else ("local" if _docker() else "moto")
        with dynamodb(kind) as (client, label):
            store = Store(client, prefix=f"lat{uuid.uuid4().hex[:6]}-")
            rows = asyncio.run(run_in_process(store, a.n, progress=lambda m: print(m, file=sys.stderr)))
        text = render(rows, environment(label, "in-process ASGI (Streamable HTTP)"), LABEL_IN_PROCESS)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    return 0 if all(r.passed for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GATE_MS",
    "LABEL_IN_PROCESS",
    "CountingTransport",
    "Row",
    "environment",
    "main",
    "render",
    "run_in_process",
]
