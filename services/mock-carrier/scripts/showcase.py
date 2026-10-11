#!/usr/bin/env python3
"""`make showcase-mock`: start the mock with MOCK_ADMIN=1 and print the 08 §7 / testing-and-showcase
§2.8 script as curl lines ready to paste into a second terminal. Ctrl-C stops the mock.

Simulation aids (say so on screen): `/_admin/*` and the `X-Mock-Client-Id` header do not exist on a
real carrier."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE / "src"))

PORT = os.environ.get("MOCK_PORT", "8443")
URL = os.environ.get("MOCK_URL", f"http://localhost:{PORT}")


def _client() -> tuple[str, str]:
    """The `tower` client's local placeholder credentials, read from config/clients.yaml."""
    import yaml

    path = Path(os.environ.get("MOCK_CLIENTS_FILE", SERVICE / "config" / "clients.yaml"))
    clients = yaml.safe_load(path.read_text(encoding="utf-8"))["clients"]
    return "tower", str(clients["tower"]["secret"])


CLIENT_ID, CLIENT_SECRET = _client()

SCRIPT = f"""
Mock carrier showcase — {URL}
  Swagger UI (generated from the vendored CAMARA specs):  {URL}/docs
  /_admin and X-Mock-Client-Id are simulation aids, not CAMARA.

# 0. a token (client credentials) — the mock issues credentials, it never consumes any
MOCK_CLIENT="{CLIENT_ID}:{CLIENT_SECRET}"   # local placeholder from config/clients.yaml
TOKEN=$(curl -s -u "$MOCK_CLIENT" -d grant_type=client_credentials {URL}/oauth2/token | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# 1. load the demo scenario (clock 2026-10-05T14:00Z)
curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {URL}/_admin/scenarios/load -H 'content-type: application/json' -d '{{"name":"demo"}}'

# 2. check → false
curl -s -X POST {URL}/sim-swap/v2/check -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{{"phoneNumber":"+16135550101","maxAge":1}}'

# 3. advance the clock 12 minutes → the timeline fires sim_swap → check → true
curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {URL}/_admin/clock -H 'content-type: application/json' -d '{{"advance_s":720}}'
curl -s -X POST {URL}/sim-swap/v2/check -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{{"phoneNumber":"+16135550101","maxAge":1}}'

# 4. retrieve-date shows the moved timestamp (14:12Z)
curl -s -X POST {URL}/sim-swap/v2/retrieve-date -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{{"phoneNumber":"+16135550101"}}'

# 5. subscribe to Mom's line with the loopback sink, fire an admin event, watch the CloudEvent arrive
curl -s -X POST {URL}/sim-swap-subscriptions/v0.3/subscriptions -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \\
  -d '{{"protocol":"HTTP","sink":"https://sink.mock.local/demo","types":["org.camaraproject.sim-swap-subscriptions.v0.swapped"],"config":{{"subscriptionDetail":{{"phoneNumber":"+16135550102"}}}}}}'
curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {URL}/_admin/lines/%2B16135550102/events -H 'content-type: application/json' -d '{{"event":"sim_swap"}}'
curl -s {URL}/_admin/sink

# 6. inject a timeout fault and watch it count down (the next call takes > 400 ms, then it's gone)
curl -s -X POST -H "Authorization: Bearer $MOCK_ADMIN_TOKEN" {URL}/_admin/faults -H 'content-type: application/json' -d '{{"kind":"timeout","n":1}}'
time curl -s -X POST {URL}/sim-swap/v2/check -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{{"phoneNumber":"+16135550101"}}'
curl -s {URL}/_admin/state | python3 -c 'import json,sys;print("faults:", json.load(sys.stdin)["faults"])'

# 7. Number Verification from "Asish's phone on mobile data" (simulated by X-Mock-Client-Id)
curl -s -o /dev/null -w '%{{redirect_url}}\\n' -H 'X-Mock-Client-Id: phone-asish' \\
  '{URL}/oauth2/authorize?response_type=code&client_id=binding-page&redirect_uri=http://localhost:8081/cb'
"""


def main() -> None:
    os.environ.setdefault("MOCK_ADMIN", "1")
    if "--print-only" in sys.argv:
        print(SCRIPT)
        return
    print(SCRIPT, flush=True)
    from mock_carrier.__main__ import main as serve

    serve()


if __name__ == "__main__":
    main()
