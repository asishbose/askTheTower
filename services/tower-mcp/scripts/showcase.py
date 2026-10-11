#!/usr/bin/env python3
"""`make showcase-tower` — Tower on its own (testing-and-showcase §2.2).

Starts the mock carrier (`MOCK_ADMIN=1`), a DynamoDB (DynamoDB Local in Docker when available, else moto's
server), and Tower with a fresh local bearer; seeds the demo (Asish's line, Mom's line, Mom's `watch` grant to
Asish as "mom"); prints the script: MCP Inspector command + URL + headers, the tool calls, and the admin curl
lines that change the mock. Ctrl-C stops everything.

    uv run python services/tower-mcp/scripts/showcase.py [--print-only] [--ddb local|moto]
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOCK_PORT, TOWER_PORT, DDB_PORT = 8443, 8000, 8001


def demo_numbers() -> tuple[str, str]:
    import yaml

    lines = list(yaml.safe_load((ROOT / "scenarios" / "demo.yaml").read_text(encoding="utf-8"))["lines"])
    return str(lines[0]), str(lines[1])


def script(bearer: str, asish: str) -> str:
    url = f"http://localhost:{TOWER_PORT}/mcp"
    mock = f"http://localhost:{MOCK_PORT}"
    return f"""
=== Tower showcase (testing-and-showcase §2.2) ===================================================
MCP endpoint : {url}   (Streamable HTTP; stateless; JSON responses)
Headers      : Authorization: Bearer {bearer}
               X-Tower-User: user-asish          (local mode only — TOWER_ENV=local)

1. MCP Inspector:  npx @modelcontextprotocol/inspector
   Transport "Streamable HTTP", URL {url}, add the two headers above → List Tools → three tools,
   descriptions verbatim from docs/architecture/components/01-alexa-surface.md §2.
2. Call line_is_ok {{"line": "self"}}            → reason_codes ["OK"], "Your line is as it was."
3. SIM swap on the mock, then call again       → ["SIM_SWAPPED_RECENT"] with the swap's time:
     curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {mock}/_admin/clock -H 'content-type: application/json' -d '{{"advance_s": 720}}'
     curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {mock}/_admin/lines/{asish}/events -H 'content-type: application/json' -d '{{"event": "sim_swap"}}'
4. Call forwarding on the mock, then call again → ["CALL_FORWARDING_SET"]:
     curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {mock}/_admin/lines/{asish}/events -H 'content-type: application/json' -d '{{"event": "cf_set"}}'
5. Call line_is_ok {{"line": "bob"}}             → ["NOT_BOUND"], next_step.kind "bind_line" with a binding URL
6. Call line_is_ok {{"line": "mom"}}; is_reachable {{"line": "mom"}}; watch_line {{"line": "self"}} (status)
   (as X-Tower-User: user-mom, watch_line self shows the grant to "mom" and who checked)
7. Reset: curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {mock}/_admin/scenarios/load -H 'content-type: application/json' -d '{{"name": "demo"}}'
8. Latency (in-process, 200 calls per tool): uv run python scripts/latency.py --out artifacts/latency.md
==================================================================================================
"""


def wait_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - localhost
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


def docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0  # noqa: S603,S607


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print-only", action="store_true")
    ap.add_argument("--ddb", choices=["auto", "local", "moto"], default="auto")
    a = ap.parse_args()
    asish, mom = demo_numbers()
    bearer = secrets.token_urlsafe(24)
    if a.print_only:
        print(script("<TOWER_BEARER>", asish))
        return 0

    procs: list[subprocess.Popen[bytes]] = []
    container: str | None = None
    moto_server = None
    env = os.environ | {
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "TOWER_DYNAMODB_ENDPOINT": f"http://127.0.0.1:{DDB_PORT}",
        "TOWER_ENV": "local",
        "TOWER_BEARER": bearer,
        "TOWER_LINE_ID_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "TOWER_MSISDN_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "TOWER_PORT": str(TOWER_PORT),
        "TOWER_CLOCK_URL": f"http://127.0.0.1:{MOCK_PORT}/_admin/clock",
        "CARRIER_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}",
        "CARRIER_CLIENT_SECRET": "local-dev-tower",
        "CARRIER_SUPPORT_NUMBER": "611",
        "BINDING_BASE_URL": "http://localhost:8081",
        "MOCK_ADMIN": "1",
        "MOCK_PORT": str(MOCK_PORT),
    }
    os.environ.update(env)
    try:
        kind = a.ddb if a.ddb != "auto" else ("local" if docker_ok() else "moto")
        if kind == "local":
            cmd = ["docker", "run", "-d", "--rm", "-p", f"{DDB_PORT}:8000", "amazon/dynamodb-local:latest"]
            container = subprocess.check_output(cmd, text=True).strip()  # noqa: S603
            time.sleep(2)
        else:
            from moto.server import ThreadedMotoServer

            moto_server = ThreadedMotoServer(port=DDB_PORT)
            moto_server.start()
        print(f"DynamoDB: {kind} on :{DDB_PORT}", file=sys.stderr)

        procs.append(subprocess.Popen([sys.executable, "-m", "mock_carrier"], env=env))  # noqa: S603
        wait_http(f"http://127.0.0.1:{MOCK_PORT}/healthz", timeout=180.0)

        from tower_consent import Store, crypto_from_env
        from tower_mcp.seed import seed_demo

        store = Store.from_env(env)
        for _ in range(30):
            try:
                store.ensure_tables()
                break
            except Exception:  # noqa: BLE001 - DynamoDB Local's JVM is still starting
                time.sleep(1)
        hasher, cipher = crypto_from_env(env)
        seed_demo(
            store, hasher, cipher, asish_e164=asish, mom_e164=mom, now=datetime.now(UTC) - timedelta(days=1)
        )

        procs.append(subprocess.Popen([sys.executable, "-m", "tower_mcp"], env=env))  # noqa: S603
        # fastmcp's cold import alone is tens of seconds on a WSL2 /mnt/c checkout.
        wait_http(f"http://127.0.0.1:{TOWER_PORT}/healthz", timeout=300.0)
        print(script(bearer, asish), flush=True)
        print("Running. Ctrl-C to stop.", file=sys.stderr)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            p.terminate()
        if container:
            subprocess.run(["docker", "stop", container], capture_output=True)  # noqa: S603,S607
        if moto_server is not None:
            moto_server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
