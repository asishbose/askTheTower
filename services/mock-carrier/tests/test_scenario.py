"""08 §6 Scenario + the §7 clock trick: load demo.yaml; advance 12 min → swapped; retrieve-date is the
event time; advance to 20 min → call forwarding active."""

from __future__ import annotations

import httpx
import pytest
from mock_carrier.testing import ASISH, MOM, advance, cc_token

pytestmark = pytest.mark.integration

CHECK = "/sim-swap/v2/check"
DATE = "/sim-swap/v2/retrieve-date"
UCF = "/call-forwarding-signal/v0.4/unconditional-call-forwardings"
CF = "/call-forwarding-signal/v0.4/call-forwardings"
REACH = "/device-reachability-status/v1/retrieve"


async def test_demo_timeline(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    r = await client.post("/_admin/scenarios/load", json={"name": "demo"})
    assert r.json()["clock"] == "2026-10-05T14:00:00.000Z"

    r = await client.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 1}, headers=h)
    assert r.status_code == 200 and r.json() == {"swapped": False}
    assert (await client.post(DATE, json={"phoneNumber": ASISH}, headers=h)).json() == {
        "latestSimChange": "2026-09-01T10:00:00.000Z"
    }

    fired = await advance(client, 12 * 60)
    assert [e["event"] for e in fired["fired"]] == ["sim_swap"]
    r = await client.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 1}, headers=h)
    assert r.json() == {"swapped": True}
    r = await client.post(DATE, json={"phoneNumber": ASISH}, headers=h)
    assert r.json() == {"latestSimChange": "2026-10-05T14:12:00.000Z"}
    assert (await client.post(UCF, json={"phoneNumber": ASISH}, headers=h)).json() == {"active": False}

    fired = await advance(client, 8 * 60)
    assert [e["event"] for e in fired["fired"]] == ["cf_set"]
    assert (await client.post(UCF, json={"phoneNumber": ASISH}, headers=h)).json() == {"active": True}
    assert (await client.post(CF, json={"phoneNumber": ASISH}, headers=h)).json() == ["unconditional"]

    # Mom's line is untouched by the timeline
    assert (await client.post(CHECK, json={"phoneNumber": MOM, "maxAge": 1}, headers=h)).json() == {
        "swapped": False
    }
    assert (await client.post(CF, json={"phoneNumber": MOM}, headers=h)).json() == ["inactive"]


async def test_retrieve_date_moves_with_the_clock(client: httpx.AsyncClient) -> None:
    """§7: fire sim_swap on demand, then the timestamp is the mock clock's, wherever the clock is."""
    await client.post("/_admin/clock", json={"now": "2027-01-01T09:00:00Z"})
    h = await cc_token(client)  # tokens are stamped by the mock clock; one from before the jump has expired
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    r = await client.post(DATE, json={"phoneNumber": MOM}, headers=h)
    assert r.json() == {"latestSimChange": "2027-01-01T09:00:00.000Z"}
    await advance(client, 3600)
    r = await client.post(CHECK, json={"phoneNumber": MOM, "maxAge": 1}, headers=h)
    assert r.json() == {"swapped": True}
    await advance(client, 1)
    assert (await client.post(CHECK, json={"phoneNumber": MOM, "maxAge": 1}, headers=h)).json() == {
        "swapped": False
    }


async def test_max_age_bounds(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    r = await client.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 2401}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"
    r = await client.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 0}, headers=h)
    assert r.status_code == 400
    r = await client.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 2400}, headers=h)
    assert r.json() == {"swapped": True}  # 2026-09-01 is within 100 days of 2026-10-05
    r = await client.post(CHECK, json={"phoneNumber": ASISH}, headers=h)  # default 240 h
    assert r.json() == {"swapped": False}


async def test_identifier_rules(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    r = await client.post(CHECK, json={}, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "MISSING_IDENTIFIER")
    r = await client.post(CHECK, json={"phoneNumber": "+19995550000"}, headers=h)
    assert (r.status_code, r.json()["code"]) == (404, "IDENTIFIER_NOT_FOUND")
    r = await client.post(
        REACH, json={"device": {"ipv4Address": {"publicAddress": "84.125.93.10", "publicPort": 1}}}, headers=h
    )
    assert (r.status_code, r.json()["code"]) == (422, "UNSUPPORTED_IDENTIFIER")


async def test_care_scenario_reachability(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    await client.post("/_admin/scenarios/load", json={"name": "care"})
    await advance(client, 30 * 60)
    r = await client.post(REACH, json={"device": {"phoneNumber": MOM}}, headers=h)
    assert r.json() == {"lastStatusTime": "2026-10-05T13:30:00.000Z", "reachable": False}
    fired = await advance(client, 4 * 3600)
    assert [e["event"] for e in fired["fired"]] == ["reachable"]
    r = await client.post(REACH, json={"device": {"phoneNumber": MOM}}, headers=h)
    assert r.json() == {
        "lastStatusTime": "2026-10-05T17:30:00.000Z",
        "reachable": True,
        "connectivity": ["DATA"],
    }


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        (None, ["unreachable", "reachable"]),
        ("blip", ["unreachable", "reachable", "unreachable", "reachable"]),
    ],
)
async def test_transplant_variants(
    client: httpx.AsyncClient, variant: str | None, expected: list[str]
) -> None:
    r = await client.post("/_admin/scenarios/load", json={"name": "transplant", "variant": variant})
    assert r.status_code == 200
    fired = await advance(client, 3600)
    assert [e["event"] for e in fired["fired"]] == expected


async def test_token_expires_on_the_mock_clock(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    assert (await client.post(CHECK, json={"phoneNumber": ASISH}, headers=h)).status_code == 200
    await advance(client, 24 * 3600)
    r = await client.post(CHECK, json={"phoneNumber": ASISH}, headers=h)
    assert (r.status_code, r.json()["code"]) == (401, "UNAUTHENTICATED")


async def test_unknown_scenario_is_400(client: httpx.AsyncClient) -> None:
    r = await client.post("/_admin/scenarios/load", json={"name": "../etc"})
    assert r.status_code == 400 and r.json()["code"] == "INVALID_ARGUMENT"
