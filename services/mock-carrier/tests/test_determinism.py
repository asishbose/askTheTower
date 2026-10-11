"""08 §5: the same scenario and the same admin calls, twice → identical /_admin/state. Also: no
randomness or wall-clock reads in the service code."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mock_carrier.testing import ASISH, MOM, auth_code_token, cc_token

pytestmark = pytest.mark.integration

SRC = Path(__file__).resolve().parents[1] / "src" / "mock_carrier"


async def _script(client: httpx.AsyncClient) -> dict[str, Any]:
    await client.post("/_admin/scenarios/load", json={"name": "demo"})
    h = await cc_token(client)
    await client.post(
        "/sim-swap-subscriptions/v0.3/subscriptions",
        json={
            "protocol": "HTTP",
            "sink": "https://sink.mock.local/a",
            "types": ["org.camaraproject.sim-swap-subscriptions.v0.swapped"],
            "config": {"subscriptionDetail": {"phoneNumber": ASISH}},
        },
        headers=h,
    )
    await client.post("/_admin/faults", json={"kind": "429", "n": 1})
    await client.post("/sim-swap/v2/check", json={"phoneNumber": ASISH}, headers=h)
    await client.post("/_admin/clock", json={"advance_s": 25 * 60})
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "unreachable"})
    await client.post(
        "/device-reachability-status/v1/retrieve", json={"device": {"phoneNumber": MOM}}, headers=h
    )
    await auth_code_token(client, "phone-mom")
    state: dict[str, Any] = (await client.get("/_admin/state")).json()
    return state


async def test_same_calls_same_state(make_client: Callable[..., httpx.AsyncClient]) -> None:
    async with make_client() as a:
        first = await _script(a)
    async with make_client() as b:
        second = await _script(b)
    assert first == second
    assert first["sink_inbox"] and first["deliveries"] and first["calls"] == 3


async def test_same_app_reloaded_same_state(client: httpx.AsyncClient) -> None:
    first = await _script(client)
    # counters keep running within one process, so compare everything the scenario reset owns
    second = await _script(client)
    assert first["lines"] == second["lines"] and first["timeline"] == second["timeline"]
    assert first["clock"] == second["clock"]


def test_no_randomness_or_wall_clock_outside_clock_py() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "clock.py" and re.search(
            r"datetime\.now|datetime\.utcnow|time\.time\(|date\.today", text
        ):
            offenders.append(f"{path.name}: wall clock")
        if path.name != "runtime.py" and re.search(r"\brandom\.|import random|uuid4|secrets\.", text):
            offenders.append(f"{path.name}: randomness")
    assert offenders == []


def test_jitter_is_off_by_default() -> None:
    text = (SRC / "runtime.py").read_text(encoding="utf-8")
    assert "random.Random() if settings.jitter_ms > 0 else None" in text
