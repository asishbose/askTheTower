"""08 §5 Faults: injected explicitly, counted down, visible in state.
{timeout,1} → the next call takes > 400 ms; {500,2} → two 5xx then clean; {429,1} → one 429."""

from __future__ import annotations

import time

import httpx
import pytest
from mock_carrier.testing import ASISH, cc_token

pytestmark = pytest.mark.integration

CHECK = "/sim-swap/v2/check"
BODY = {"phoneNumber": ASISH, "maxAge": 240}


async def _check(client: httpx.AsyncClient, h: dict[str, str]) -> httpx.Response:
    return await client.post(CHECK, json=BODY, headers=h)


async def test_timeout_fault(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    r = await client.post("/_admin/faults", json={"kind": "timeout", "n": 1})
    assert r.json() == {"faults": ["timeout"]}
    t0 = time.perf_counter()
    r = await _check(client, h)
    slow = time.perf_counter() - t0
    assert slow > 0.4 and r.status_code == 200
    t0 = time.perf_counter()
    assert (await _check(client, h)).status_code == 200
    assert time.perf_counter() - t0 < 0.4
    assert (await client.get("/_admin/state")).json()["faults"] == []


async def test_500_fault_twice_then_clean(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    await client.post("/_admin/faults", json={"kind": "500", "n": 2})
    assert (await client.get("/_admin/state")).json()["faults"] == ["500", "500"]
    for _ in range(2):
        r = await _check(client, h)
        assert r.status_code == 500 and r.json() == {
            "status": 500,
            "code": "INTERNAL",
            "message": "Injected fault (mock /_admin/faults).",
        }
    assert (await _check(client, h)).status_code == 200


async def test_429_fault(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    await client.post("/_admin/faults", json={"kind": "429", "n": 1})
    r = await _check(client, h)
    assert (r.status_code, r.json()["code"]) == (429, "TOO_MANY_REQUESTS")
    assert (await _check(client, h)).status_code == 200


async def test_faults_apply_to_camara_calls_only(client: httpx.AsyncClient) -> None:
    await client.post("/_admin/faults", json={"kind": "500", "n": 1})
    h = await cc_token(client)  # the token endpoint does not consume the fault
    assert (await client.get("/_admin/state")).status_code == 200
    assert (await _check(client, h)).status_code == 500


async def test_bad_fault_kind(client: httpx.AsyncClient) -> None:
    r = await client.post("/_admin/faults", json={"kind": "teapot", "n": 1})
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"
    r = await client.delete("/_admin/faults")
    assert r.json() == {"faults": []}
