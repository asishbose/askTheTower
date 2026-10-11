"""Nightly, live, read-mostly: the demo admin path's status codes against a deployment (10 §2.1, 08 §6, 04 §8).

Skipped without AWS credentials and `artifacts/tf-outputs.json` (the `aws_outputs` fixture). Which half runs follows
the deployment: `demo_admin_cidrs = []` → the closed cases; non-empty → the open cases, which also need
`deploy/.env.aws` from `make outputs` (`DEMO_ADMIN_BEARER`) and must run from an address on the list.

| State | Request | Expected |
|---|---|---|
| closed | every admin route on the execute-api endpoint | 404 |
| open | through the CloudFront URL with the bearer (source = CloudFront, off the list) | 403 |
| open | no `Authorization`: mock `DELETE /_admin/faults`, Alerts `GET /internal/sent`, binding `GET /_admin/tables?table=Grants` | 401 (API Gateway: identity source missing) |
| open | a wrong bearer (decision 1: also what the Alerts internal bearer is here) | 403 (the gate) |
| open | `DEMO_ADMIN_BEARER` | 200 |

The only write is `DELETE /_admin/faults` on the mock (clears injected faults: harmless). Nothing is printed but
status codes; no assertion message carries a token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.aws.conftest import ROOT

pytestmark = pytest.mark.nightly

ENV_FILE = ROOT / "deploy" / ".env.aws"


def env_file(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.exists():
        pytest.skip("deploy/.env.aws missing: run `make outputs ENV=aws`")
    out: dict[str, str] = {}
    for line in path.read_text("utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def admin_requests(api: str) -> list[tuple[str, str, dict[str, str]]]:
    """(method, url, params) of each admin route, on the execute-api endpoint."""
    return [
        ("DELETE", f"{api}/carrier-admin/_admin/faults", {}),
        ("GET", f"{api}/internal/sent", {"after": "0"}),
        ("POST", f"{api}/internal/reset", {}),
        ("GET", f"{api}/_admin/tables", {"table": "Grants", "format": "json"}),
    ]


def _status(method: str, url: str, params: dict[str, str], headers: dict[str, str] | None = None) -> int:
    body = {"user_ids": ["nobody"]} if url.endswith("/internal/reset") else None
    r = httpx.request(method, url, params=params, headers=headers or {}, json=body, timeout=15)
    return r.status_code


def test_closed_path_is_404(aws_outputs: dict[str, Any]) -> None:
    if aws_outputs.get("mock_admin_url") or aws_outputs.get("alerts_internal_url"):
        pytest.skip("the demo admin path is open on this deployment")
    api = str(aws_outputs["api_endpoint"]).rstrip("/")
    got = {url.removeprefix(api): _status(m, url, p) for m, url, p in admin_requests(api)}
    assert got == {path: 404 for path in got}


def test_open_path_cidr_and_bearer(aws_outputs: dict[str, Any]) -> None:
    if not aws_outputs.get("alerts_internal_url"):
        pytest.skip("the demo admin path is closed (demo_admin_cidrs = [])")
    env = env_file()
    api = str(aws_outputs["api_endpoint"]).rstrip("/")
    mock, alerts, binding = (
        (m, u, p) for m, u, p in admin_requests(api) if not u.endswith("/internal/reset")
    )
    bearer = env.get("DEMO_ADMIN_BEARER", "")
    assert bearer, "deploy/.env.aws lacks DEMO_ADMIN_BEARER (run `make outputs ENV=aws`)"
    right = {"Authorization": f"Bearer {bearer}"}

    public = str(aws_outputs.get("binding_url", "")).rstrip("/")
    if public and public != api:  # through CloudFront the source address is CloudFront's: off the list
        assert _status("GET", f"{public}/internal/sent", {"after": "0"}, right) == 403

    routes = (mock, alerts, binding)
    without = {u.removeprefix(api): _status(m, u, p) for m, u, p in routes}
    assert without == {path: 401 for path in without}
    wrong = {"Authorization": f"Bearer {bearer[::-1]}"}
    with_wrong = {u.removeprefix(api): _status(m, u, p, wrong) for m, u, p in routes}
    assert with_wrong == {path: 403 for path in with_wrong}
    with_bearer = {u.removeprefix(api): _status(m, u, p, right) for m, u, p in routes}
    assert with_bearer == {path: 200 for path in with_bearer}
