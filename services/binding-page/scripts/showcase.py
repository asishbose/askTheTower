#!/usr/bin/env python3
"""`make showcase-binding` (testing-and-showcase.md §2.4): the one tap, grants, revocation, the tables.

Starts a self-contained local stack unless one is already running:
  - DynamoDB: `DYNAMO_ENDPOINT` if it answers (compose's DynamoDB Local), else a throw-away DynamoDB Local
    container (`docker run`), else moto in server mode;
  - the mock carrier (demo scenario, `MOCK_ADMIN=1`) on `MOCK_PORT` (8443) unless `MOCK_URL` already answers;
  - the binding page on `PORT` (8081) with `TOWER_ENV=local BIND_ADMIN=1`, unless `BINDING_URL` already answers
    with the admin view on.
Then it prints a QR code (terminal) for `BASE_URL/bind/<fresh token>?as=phone-asish` and the §2.4 script.
Ctrl-C stops what it started.

`BASE_URL` defaults to `http://<this machine's LAN address>:8081` so a phone on the same Wi-Fi can open it
(WSL2: the LAN address is the VM's; use mirrored networking or a port proxy, or set `BASE_URL`).

`?as=phone-asish` is the local mobile-data simulation (`binding_page/mobile_data.py`): it does not exist on AWS.
Keys and the session secret are random per run and never printed.

Flags: `--print-only` prints the script without starting anything; `--smoke` starts everything, checks the
bind link answers 200, prints, and exits (used to prove the target runs).
"""

from __future__ import annotations

import base64
import contextlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

SERVICE = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PORT", "8081"))
MOCK_PORT = int(os.environ.get("MOCK_PORT", "8443"))
MOCK_URL = os.environ.get("MOCK_URL", f"http://localhost:{MOCK_PORT}")
BINDING_URL = os.environ.get("BINDING_URL", f"http://localhost:{PORT}")
DYNAMO_ENDPOINT = os.environ.get("DYNAMO_ENDPOINT", "http://localhost:8000")
USER = os.environ.get("SHOWCASE_USER", "user-asish")


def lan_address() -> str:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET; nothing is sent, it only picks the outbound interface
        return str(s.getsockname()[0])
    return "localhost"


def answers(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2.0).status_code < 500
    except httpx.HTTPError:
        return False


def free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def script(base_url: str, bind_url: str) -> str:
    admin = f"{base_url}/_admin"
    return f"""
Binding page showcase (testing-and-showcase.md §2.4) — {base_url}
  ?as=phone-asish is the LOCAL simulation of mobile data (the mock's X-Mock-Client-Id header); on AWS the
  carrier's own network attribution does this and the parameter is ignored.

1. "Wi-Fi on": open the link WITHOUT ?as= → tap Verify → refused ("couldn't see this phone on mobile data"), nothing stored:
     {bind_url.split("?")[0]}
   (a link works until it is used: the refusal leaves it valid, so step 2 can use the QR code's link)
2. The one tap: scan the QR code (or open {bind_url}) → tap Verify → "Line connected •••• 0101". Nothing typed.
3. Mom binds her line in a second browser (private window):
     curl -s -X POST {admin}/bind-tokens -H 'content-type: application/json' -d '{{"user_id":"user-mom"}}'
     → open the returned url with ?as=phone-mom → Verify → "•••• 0102"
4. Asish: "Sharing and activity" → "Create invite code" → read the code to Mom.
5. Mom: "Share your line" → the code, kind "watch", name "mom" → Share.
6. Show the tables (HMAC line_id everywhere, no number anywhere): {admin}/tables
     curl -s '{admin}/resolve?user=user-asish&line=mom'      → grant "watch"
7. Mom: Revoke → resolve flips:
     curl -s '{admin}/resolve?user=user-asish&line=mom'      → grant "none", revoked_at set
8. Mom: "Activity on this line" → the audit view with "Log verified".
"""


def print_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:  # pragma: no cover - qrcode is in the dev group
        print("(install qrcode for a terminal QR code)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def _serve(app: Any, port: int, host: str = "0.0.0.0") -> Any:  # noqa: S104 - a phone on the LAN opens it
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 60
    while not server.started:
        if time.monotonic() > deadline:
            raise SystemExit(f"server on port {port} did not start")
        time.sleep(0.1)
    return server


@contextlib.contextmanager
def dynamodb() -> Iterator[str]:
    if answers(DYNAMO_ENDPOINT):
        print(f"DynamoDB: using {DYNAMO_ENDPOINT}")
        yield DYNAMO_ENDPOINT
        return
    docker = shutil.which("docker")
    if docker and subprocess.run([docker, "info"], capture_output=True, timeout=20).returncode == 0:  # noqa: S603
        name = f"atb-showcase-ddb-{secrets.token_hex(3)}"
        subprocess.run(  # noqa: S603
            [docker, "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8000", "amazon/dynamodb-local:latest",
             "-jar", "DynamoDBLocal.jar", "-inMemory", "-sharedDb"],
            check=True, capture_output=True,
        )  # fmt: skip
        try:
            port = (
                subprocess.run(  # noqa: S603
                    [docker, "port", name, "8000/tcp"], check=True, capture_output=True, text=True
                )
                .stdout.split(":")[-1]
                .strip()
            )
            endpoint = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 60
            while not answers(endpoint):
                if time.monotonic() > deadline:
                    raise SystemExit("DynamoDB Local did not start")
                time.sleep(0.5)
            print(f"DynamoDB: DynamoDB Local container {name} at {endpoint}")
            yield endpoint
        finally:
            subprocess.run([docker, "rm", "-f", name], capture_output=True)  # noqa: S603
        return
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    print(f"DynamoDB: moto server at http://{host}:{port} (no Docker)")
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


def bind_link(binding_url: str, base_url: str) -> str:
    r = httpx.post(f"{binding_url}/_admin/bind-tokens", json={"user_id": USER}, timeout=10.0)
    r.raise_for_status()
    path = httpx.URL(r.json()["url"]).path
    return f"{base_url}{path}?as=phone-asish"


def main() -> None:
    base_url = os.environ.get("BASE_URL", f"http://{lan_address()}:{PORT}").rstrip("/")
    if "--print-only" in sys.argv:
        print(script(base_url, f"{base_url}/bind/<fresh token>?as=phone-asish"))
        return
    smoke = "--smoke" in sys.argv

    if answers(f"{BINDING_URL}/_admin/tables?format=json"):
        print(f"Binding page already running at {BINDING_URL} (admin view on).")
        url = bind_link(BINDING_URL, base_url)
        print_qr(url)
        print(script(base_url, url))
        return

    for k, v in {
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "AWS_REGION": "us-east-1",
    }.items():
        os.environ.setdefault(k, v)
    with dynamodb() as endpoint:
        if not answers(f"{MOCK_URL}/healthz"):
            if not free(MOCK_PORT):
                raise SystemExit(f"port {MOCK_PORT} is busy and is not the mock carrier")
            os.environ.setdefault("MOCK_ADMIN", "1")
            from mock_carrier.app import create_app as create_mock
            from mock_carrier.settings import Settings as MockSettings

            _serve(create_mock(MockSettings.from_env()), MOCK_PORT, host="127.0.0.1")
            print(f"Mock carrier: started at {MOCK_URL} (demo scenario)")
        else:
            print(f"Mock carrier: using {MOCK_URL}")
        if not free(PORT):
            raise SystemExit(f"port {PORT} is busy (a binding page without BIND_ADMIN=1?)")

        env = {
            "TOWER_ENV": "local",
            "BIND_ADMIN": "1",
            "BASE_URL": base_url,
            "BIND_REDIRECT_URI": f"http://localhost:{PORT}/bind/callback",  # registered with the mock
            "SESSION_SECRET": secrets.token_urlsafe(32),
            "TOWER_DYNAMODB_ENDPOINT": endpoint,
            "TOWER_TABLE_PREFIX": os.environ.get("TOWER_TABLE_PREFIX", ""),
            "TOWER_LINE_ID_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "TOWER_MSISDN_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "CARRIER_CLIENT": "direct",
            "CARRIER_BACKEND": "mock",
            "CARRIER_BASE_URL": MOCK_URL,
            "CARRIER_CLIENT_SECRET": "local-dev-binding",  # the mock's local placeholder (config/clients.yaml)
        }
        full_env = {**os.environ, **env}
        from binding_page.app import create_app_from_env, deps_from_env

        deps_from_env(full_env).store.ensure_tables()
        _serve(create_app_from_env(full_env), PORT)
        local = f"http://localhost:{PORT}"
        print(f"Binding page: started at {base_url} (and {local})")

        url = bind_link(local, base_url)
        print_qr(url)
        print(script(base_url, url))
        if smoke:
            r = httpx.get(f"{local}{httpx.URL(url).raw_path.decode()}", timeout=10.0)
            print(f"smoke: GET bind link → {r.status_code}")
            raise SystemExit(0 if r.status_code == 200 and "Verify" in r.text else 1)
        print("Ctrl-C to stop.")
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                time.sleep(3600)


if __name__ == "__main__":
    main()
