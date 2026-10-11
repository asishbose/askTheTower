"""08 §4/§6 Subscriptions: create → fire an admin event → a CloudEvent at a sink inside the test
process with the right type and subscriptionId; expiry; delete; retries."""

from __future__ import annotations

import httpx
import pytest
from mock_carrier.testing import ASISH, MOM, SINK, Sink, advance, cc_token

pytestmark = pytest.mark.integration

SIM_SUBS = "/sim-swap-subscriptions/v0.3/subscriptions"
REACH_SUBS = "/device-reachability-status-subscriptions/v0.8/subscriptions"
SWAPPED = "org.camaraproject.sim-swap-subscriptions.v0.swapped"
SIM_ENDED = "org.camaraproject.sim-swap-subscriptions.v0.subscription-ended"
R = "org.camaraproject.device-reachability-status-subscriptions.v0."


def sim_body(phone: str = MOM, **config: object) -> dict[str, object]:
    return {
        "protocol": "HTTP",
        "sink": SINK,
        "types": [SWAPPED],
        "config": {"subscriptionDetail": {"phoneNumber": phone}, **config},
    }


def reach_body(event: str, phone: str = MOM, **config: object) -> dict[str, object]:
    return {
        "protocol": "HTTP",
        "sink": SINK,
        "types": [R + event],
        "config": {"subscriptionDetail": {"device": {"phoneNumber": phone}}, **config},
    }


async def test_sim_swap_event_reaches_sink(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    r = await client.post(SIM_SUBS, json=sim_body(), headers={**h, "x-correlator": "t-1"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert r.headers["x-correlator"] == "t-1"
    assert sub["id"] and sub["status"] == "ACTIVE" and sub["expiresAt"] and sub["startsAt"]

    r = await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    assert r.status_code == 200
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["type"] == SWAPPED and ev["data"]["subscriptionId"] == sub["id"]
    assert (
        ev["specversion"] == "1.0" and ev["id"] and ev["source"] and ev["time"] == "2026-10-05T14:00:00.000Z"
    )
    assert "phoneNumber" not in ev["data"]  # never echoed to the sink
    assert sink.requests[0].headers["content-type"] == "application/cloudevents+json"

    state = (await client.get("/_admin/state")).json()
    assert state["deliveries"][0]["delivered"] is True and state["deliveries"][0]["attempts"] == 1

    # an event on another line does not reach this subscription
    await client.post(f"/_admin/lines/{ASISH}/events", json={"event": "sim_swap"})
    assert len(sink.events) == 1


async def test_timeline_events_fan_out(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    r = await client.post(SIM_SUBS, json=sim_body(ASISH), headers=h)
    sub_id = r.json()["id"]
    await advance(client, 12 * 60)
    assert [e["data"]["subscriptionId"] for e in sink.events] == [sub_id]


async def test_list_get_delete(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    sub_id = (await client.post(SIM_SUBS, json=sim_body(), headers=h)).json()["id"]
    assert [s["id"] for s in (await client.get(SIM_SUBS, headers=h)).json()] == [sub_id]
    assert (await client.get(f"{SIM_SUBS}/{sub_id}", headers=h)).json()["id"] == sub_id

    # another client cannot see it
    other = await cc_token(client, "alerts")
    assert (await client.get(SIM_SUBS, headers=other)).json() == []
    assert (await client.get(f"{SIM_SUBS}/{sub_id}", headers=other)).status_code == 404

    r = await client.delete(f"{SIM_SUBS}/{sub_id}", headers=h)
    assert r.status_code == 204
    assert sink.events[-1]["type"] == SIM_ENDED
    assert sink.events[-1]["data"] == {"subscriptionId": sub_id, "terminationReason": "SUBSCRIPTION_DELETED"}
    r = await client.get(f"{SIM_SUBS}/{sub_id}", headers=h)
    assert (r.status_code, r.json()["code"]) == (404, "NOT_FOUND")
    assert (await client.delete(f"{SIM_SUBS}/{sub_id}", headers=h)).status_code == 404

    n = len(sink.events)
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    assert len(sink.events) == n  # deleted subscriptions get nothing


async def test_expiry(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    body = sim_body(subscriptionExpireTime="2026-10-05T15:00:00Z")
    sub = (await client.post(SIM_SUBS, json=body, headers=h)).json()
    assert sub["expiresAt"] == "2026-10-05T15:00:00.000Z"
    await advance(client, 3600)
    assert sink.events[-1]["data"] == {
        "subscriptionId": sub["id"],
        "terminationReason": "SUBSCRIPTION_EXPIRED",
    }
    assert (await client.get(f"{SIM_SUBS}/{sub['id']}", headers=h)).json()["status"] == "EXPIRED"
    n = len(sink.events)
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    assert len(sink.events) == n

    r = await client.post(SIM_SUBS, json=sim_body(subscriptionExpireTime="2026-10-05T14:00:00Z"), headers=h)
    assert (r.status_code, r.json()["code"]) == (400, "OUT_OF_RANGE")


async def test_max_events(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    sub = (await client.post(SIM_SUBS, json=sim_body(subscriptionMaxEvents=1), headers=h)).json()
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    assert [e["type"] for e in sink.events] == [SWAPPED, SIM_ENDED]
    assert sink.events[1]["data"]["terminationReason"] == "MAX_EVENTS_REACHED"
    assert (await client.get(f"{SIM_SUBS}/{sub['id']}", headers=h)).json()["status"] == "EXPIRED"


async def test_reachability_events(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    r = await client.post(REACH_SUBS, json=reach_body("reachability-disconnected"), headers=h)
    assert r.status_code == 201, r.text
    disc = r.json()["id"]
    data = (
        await client.post(REACH_SUBS, json=reach_body("reachability-data", initialEvent=True), headers=h)
    ).json()
    assert [e["type"] for e in sink.events] == [R + "reachability-data"]  # initialEvent: currently on data
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "unreachable"})
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "reachable", "connectivity": ["DATA"]})
    assert [(e["type"], e["data"]["subscriptionId"]) for e in sink.events[1:]] == [
        (R + "reachability-disconnected", disc),
        (R + "reachability-data", data["id"]),
    ]


async def test_create_errors(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    bad_proto = {**sim_body(), "protocol": "MQTT3"}
    r = await client.post(SIM_SUBS, json=bad_proto, headers=h)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_PROTOCOL")
    r = await client.post(SIM_SUBS, json={**sim_body(), "sink": "http://plain.example/x"}, headers=h)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_SINK")
    plain = {**sim_body(), "sinkCredential": {"credentialType": "PLAIN", "identifier": "a", "secret": "b"}}
    r = await client.post(SIM_SUBS, json=plain, headers=h)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_CREDENTIAL")
    r = await client.post(SIM_SUBS, json=sim_body(phone="+19995550000"), headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "SERVICE_NOT_APPLICABLE")
    no_id = {**sim_body(), "config": {"subscriptionDetail": {}}}
    r = await client.post(SIM_SUBS, json=no_id, headers=h)
    assert (r.status_code, r.json()["code"]) == (422, "MISSING_IDENTIFIER")
    assert (await client.post(SIM_SUBS, json=sim_body(), headers=h)).status_code == 201
    r = await client.post(SIM_SUBS, json=sim_body(), headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "ALREADY_EXISTS")
    limited = await cc_token(client, "limited")
    r = await client.post(SIM_SUBS, json=sim_body(ASISH), headers=limited)
    assert (r.status_code, r.json()["code"]) == (403, "PERMISSION_DENIED")


async def test_access_token_sink_credential_is_forwarded(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    cred = {
        "credentialType": "ACCESSTOKEN",
        "accessToken": "sink-token-1",
        "accessTokenType": "bearer",
        "accessTokenExpiresUtc": "2026-12-01T00:00:00Z",
    }
    r = await client.post(SIM_SUBS, json={**sim_body(), "sinkCredential": cred}, headers=h)
    assert r.status_code == 201
    assert "sinkCredential" not in r.json()  # never echoed back
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    assert sink.requests[-1].headers["authorization"] == "Bearer sink-token-1"


async def test_delivery_retries_then_records(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    await client.post(SIM_SUBS, json=sim_body(), headers=h)
    sink.fail_next = 2
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    d = (await client.get("/_admin/state")).json()["deliveries"][-1]
    assert (d["attempts"], d["delivered"], d["status_code"]) == (3, True, 204)
    sink.fail_next = 10
    await client.post(f"/_admin/lines/{MOM}/events", json={"event": "sim_swap"})
    d = (await client.get("/_admin/state")).json()["deliveries"][-1]
    assert (d["attempts"], d["delivered"], d["status_code"]) == (4, False, 503)  # 1 try + 3 retries


async def test_loopback_sink(client: httpx.AsyncClient, sink: Sink) -> None:
    h = await cc_token(client)
    body = {**sim_body(), "sink": "https://sink.mock.local/demo"}
    await client.post(SIM_SUBS, json=body, headers=h)
    await client.post(f"/_admin/lines/{MOM}/events", json={"type": "sim_swap"})  # `type` alias (06 §8)
    inbox = (await client.get("/_admin/sink")).json()["events"]
    assert inbox[0]["event"]["type"] == SWAPPED
    assert sink.requests == []  # nothing left the process
