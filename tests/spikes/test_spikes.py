"""Prompt 02 spike scripts: they run offline, redact what they capture, and nothing ships imports them."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from tests.privacy.patterns import BEARER, phone_hits

ROOT = Path(__file__).resolve().parents[2]
SPIKES = ROOT / "scripts" / "spikes"


def _load(name: str, path: Path | None = None) -> Any:
    spec = importlib.util.spec_from_file_location(f"spike_test_{name}", path or SPIKES / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_nothing_ships_imports_the_spikes() -> None:
    pat = re.compile(r"^\s*(from|import)\s+[\w.]*\b(spikes|a_alexa_echo|b_gateway_probe|c_three_hop)\b", re.M)
    hits = [
        str(p.relative_to(ROOT))
        for top in ("packages", "services")
        for p in (ROOT / top).rglob("*.py")
        if pat.search(p.read_text("utf-8", errors="ignore"))
    ]
    assert not hits, hits


@pytest.mark.unit
def test_token_shape_keeps_no_secret_and_no_number() -> None:
    a = _load("a_alexa_echo")
    token = jwt.encode(
        {"sub": "amzn1.account.AEXAMPLE", "iss": "https://issuer.example", "phone": "+15550001234", "exp": 2},
        "k" * 32,
        algorithm="HS256",
        headers={"kid": "key-1"},
    )
    shape = a.redact_headers([(b"authorization", f"Bearer {token}".encode()), (b"cookie", b"s=1")])
    text = json.dumps(shape)
    assert token not in text and not BEARER.search(text) and not phone_hits(text)
    auth = shape["authorization"]
    assert auth["jwt"] and auth["scheme"] == "Bearer" and auth["header"]["kid"] == "key-1"
    assert auth["claim_names"] == ["exp", "iss", "phone", "sub"] and auth["sub"]["prefix"] == "amzn1.accoun"
    assert shape["cookie"] == "<redacted>"


@pytest.mark.integration
@pytest.mark.parametrize("with_jwt", [True, False])
async def test_spike_a_captures_and_tests_the_identity_assumption(tmp_path: Path, with_jwt: bool) -> None:
    a = _load("a_alexa_echo")
    latency = _load("latency", ROOT / "scripts" / "latency.py")
    capture = tmp_path / "capture.jsonl"
    app = a.create_app(capture)
    headers = {"Accept": "application/json, text/event-stream"}
    if with_jwt:
        headers["Authorization"] = "Bearer " + jwt.encode({"sub": "opaque-user-1", "exp": 2}, "k" * 32)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "summary_probe", "arguments": {}},
    }
    async with (
        latency.serving(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a.local") as http,
    ):
        r = await http.post("/mcp", json=body, headers=headers)
    assert r.status_code == 200
    assert r.json()["result"]["structuredContent"]["summary"] == a.PROBE_SUMMARY
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    report = a.analyse(records)
    assert report["tools_calls"] == 1
    assert report["verdict"].startswith("HOLDS" if with_jwt else "REFUTED")


@pytest.mark.unit
def test_spike_b_classify_flags_flattened_arguments() -> None:
    b = _load("b_gateway_probe")
    flat = [
        {"name": "sim-swap___checkSimSwap", "inputSchema": {"properties": {"phoneNumber": {}, "maxAge": {}}}}
    ]
    assert b.classify(flat)["arguments"].startswith("REFUTED")
    assert b.classify([{"name": "x", "inputSchema": {}}])["naming"].startswith("REFUTED")


@pytest.mark.integration
async def test_spike_b_selftest_runs_oauth_through_the_stand_in() -> None:
    b = _load("b_gateway_probe")
    report = await b.selftest(5)
    assert report["check_tool"] == "sim-swap___checkSimSwap"
    assert report["naming"].startswith("HOLDS") and report["arguments"].startswith("HOLDS")
    assert report["results"] == ['{"swapped":false}']
    assert report["stub"] == {"token_grants": 1, "checks": 5}


@pytest.mark.integration
async def test_spike_c_three_hops_runs_in_process() -> None:
    c = _load("c_three_hop")
    with c.latency.dynamodb("moto") as (ddb, _label):
        rows = await c.run(ddb, 10, warmup=1)
    assert [len(r.samples) for r in rows] == [10, 10]
    text = c.render(rows, {"store": "moto"})
    assert "harness only" in text and "three_hops" in text
