"""Conformance: schemathesis generates requests from each vendored spec (as served at
`/openapi/<api>.json`) and runs every check (`--checks all` equivalent, see `schemathesis.toml`)
against the in-process mock — positive and negative data, error envelopes, status codes, headers,
content types, auth. Results are written to `artifacts/conformance-report.html`.

Auth: a client-credentials token from the mock's own `/oauth2/token` for every API, except Number
Verification, which needs an auth-code token the network attributed (`X-Mock-Client-Id`).
Subscription sinks are answered in-process (no network)."""

from __future__ import annotations

import html
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import schemathesis
from mock_carrier.app import create_app
from mock_carrier.settings import Settings
from mock_carrier.specs import SPEC_FILES
from mock_carrier.testing import REDIRECT
from schemathesis import GenerationMode
from schemathesis.config import SchemathesisConfig
from starlette.testclient import TestClient

pytestmark = pytest.mark.integration

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
REPORT = Path(os.environ.get("CONFORMANCE_REPORT", ROOT / "artifacts" / "conformance-report.html"))
CONFIG = SchemathesisConfig.from_path(str(SERVICE / "schemathesis.toml"))

SETTINGS = replace(Settings(), admin=False, base_url="http://localhost:8443", webhook_backoff_s=0.0)
APP = create_app(SETTINGS, sink_transport=httpx.MockTransport(lambda request: httpx.Response(204)))


def _tokens() -> dict[str, str]:
    with TestClient(APP) as c:
        r = c.post(
            "/oauth2/token", data={"grant_type": "client_credentials"}, auth=("tower", "local-dev-tower")
        )
        cc = r.json()["access_token"]
        r = c.get(
            "/oauth2/authorize",
            params={"client_id": "binding-page", "redirect_uri": REDIRECT},
            headers={"X-Mock-Client-Id": "phone-asish"},
            follow_redirects=False,
        )
        code = httpx.URL(r.headers["location"]).params["code"]
        r = c.post(
            "/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT},
            auth=("binding-page", "local-dev-binding"),
        )
        nv = r.json()["access_token"]
    return {"cc": cc, "nv": nv}


TOKENS = _tokens()
RESULTS: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"cases": 0, "failures": [], "statuses": defaultdict(int)}
)


def _schema(api: str) -> Any:
    return schemathesis.openapi.from_asgi(f"/openapi/{api}.json", APP, config=CONFIG)


def _auth(api: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS['nv' if api == 'number-verification' else 'cc']}"}


PHONE = re.compile(r"^\+[1-9][0-9]{4,14}$")
KNOWN = ("+16135550101", "+16135550102")


def _steer(case: Any) -> None:
    """For positive cases only, swap a schema-valid phone number for a line the mock knows (every
    other case, deterministically), use the one protocol the mock supports, and drop an expiry time
    that is already past — each swap keeps the body schema-valid, and without it random numbers
    would only ever exercise the 404/422 paths. Negative cases are never touched."""
    meta = getattr(case, "meta", None)
    if meta is None or meta.generation.mode is not GenerationMode.POSITIVE or not isinstance(case.body, dict):
        return
    body = case.body

    def swap(holder: Any) -> None:
        if (
            isinstance(holder, dict)
            and isinstance(holder.get("phoneNumber"), str)
            and PHONE.match(holder["phoneNumber"])
        ):
            if len(holder["phoneNumber"]) % 2 == 0:
                holder["phoneNumber"] = KNOWN[len(holder["phoneNumber"]) % 4 // 2]

    swap(body)
    swap(body.get("device"))
    config = body.get("config")
    if isinstance(config, dict):
        detail = config.get("subscriptionDetail")
        swap(detail)
        swap(detail.get("device") if isinstance(detail, dict) else None)
        if "subscriptionExpireTime" in config:
            config.pop("subscriptionExpireTime")
        if (
            body.get("protocol") in {"MQTT3", "MQTT5", "AMQP", "NATS", "KAFKA"}
            and "protocolSettings" not in body
        ):
            body["protocol"] = "HTTP"


def _run(api: str, case: Any) -> None:
    _steer(case)
    label = f"{case.operation.method.upper()} {case.operation.full_path}"
    entry = RESULTS[label]
    entry["api"] = api
    entry["cases"] += 1
    headers = {**(case.headers or {}), **_auth(api)}
    if "authorization" in {k.lower() for k in (case.headers or {})}:
        headers = dict(case.headers)  # schemathesis is probing auth itself; leave its header alone
    response = case.call(headers=headers)
    entry["statuses"][response.status_code] += 1
    try:
        case.validate_response(response, headers=headers)
    except BaseException as exc:
        entry["failures"].append(str(exc).splitlines()[0][:200])
        raise


sim_swap = _schema("sim-swap")
sim_swap_subs = _schema("sim-swap-subscriptions")
call_forwarding = _schema("call-forwarding-signal")
number_verification = _schema("number-verification")
reachability = _schema("device-reachability-status")
reachability_subs = _schema("device-reachability-status-subscriptions")


@sim_swap.parametrize()
def test_sim_swap(case: Any) -> None:
    _run("sim-swap", case)


@sim_swap_subs.parametrize()
def test_sim_swap_subscriptions(case: Any) -> None:
    _run("sim-swap-subscriptions", case)


@call_forwarding.parametrize()
def test_call_forwarding_signal(case: Any) -> None:
    _run("call-forwarding-signal", case)


@number_verification.parametrize()
def test_number_verification(case: Any) -> None:
    _run("number-verification", case)


@reachability.parametrize()
def test_device_reachability_status(case: Any) -> None:
    _run("device-reachability-status", case)


@reachability_subs.parametrize()
def test_device_reachability_status_subscriptions(case: Any) -> None:
    _run("device-reachability-status-subscriptions", case)


def _checks() -> list[str]:
    from schemathesis.checks import CHECKS

    return sorted(c.__name__ for c in CHECKS.get_all())


def write_report(path: Path = REPORT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checks = _checks()
    rows = []
    for label in sorted(RESULTS):
        e = RESULTS[label]
        statuses = ", ".join(f"{k}×{v}" for k, v in sorted(e["statuses"].items()))
        verdict = "pass" if not e["failures"] else f"FAIL ({len(e['failures'])})"
        fails = "<br>".join(html.escape(f) for f in e["failures"][:5])
        rows.append(
            f"<tr><td>{html.escape(e['api'])}</td><td><code>{html.escape(label)}</code></td><td>{e['cases']}</td>"
            f"<td>{html.escape(statuses)}</td><td class='{'ok' if not e['failures'] else 'bad'}'>{verdict}</td>"
            f"<td>{fails}</td></tr>"
        )
    total = sum(e["cases"] for e in RESULTS.values())
    failed = sum(1 for e in RESULTS.values() if e["failures"])
    versions = ", ".join(f"{a} {s['version']}" for a, s in APP.state.rt.specs.versions().items())
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Mock carrier conformance</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:72rem}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:.3rem .5rem;text-align:left;vertical-align:top;font-size:.9rem}}
.ok{{color:#176b2c;font-weight:600}}.bad{{color:#a11;font-weight:600}}</style></head><body>
<h1>Mock carrier — CAMARA conformance</h1>
<p>Generated by <code>services/mock-carrier/tests/test_conformance.py</code> (schemathesis {schemathesis.__version__},
in-process ASGI) at {datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")}.
Specs: CAMARA Fall25 — {html.escape(versions)}.</p>
<p><b>{len(RESULTS)} operations, {total} generated cases, {failed} operations with failures.</b></p>
<p>Checks run on every case: {", ".join(f"<code>{c}</code>" for c in checks)}.
Config: <code>services/mock-carrier/schemathesis.toml</code> (positive data may also be answered 422 —
CAMARA's identifier rules are prose, not schema).</p>
<table><tr><th>API</th><th>Operation</th><th>Cases</th><th>Status codes seen</th><th>Result</th><th>First failures</th></tr>
{"".join(rows)}</table></body></html>
"""
    path.write_text(body, encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _report() -> Iterator[None]:
    yield
    if RESULTS:
        write_report()


def test_every_vendored_operation_was_exercised() -> None:
    """Runs last in this module (pytest keeps definition order): every operation got cases."""
    ops = {op.label for op in APP.state.rt.specs.operations()}
    assert len(ops) == 15 and set(SPEC_FILES) == {e["api"] for e in RESULTS.values()} | set(SPEC_FILES)
    assert ops <= set(RESULTS), ops - set(RESULTS)
