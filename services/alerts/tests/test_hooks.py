"""06 §6 webhook receiver, against the in-process mock carrier: real subscriptions, real CloudEvents."""

from __future__ import annotations

import time
from typing import Any

import pytest
from alerts import state as S
from alerts.hooks import validate_event
from alerts.testing import BEARER, MOM, audit_rows, row_summary

AUTH = {"Authorization": f"Bearer {BEARER}"}
SWAPPED = "org.camaraproject.sim-swap-subscriptions.v0.swapped"


def _event(
    event_id: str = "evt-0001", etype: str = SWAPPED, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "https://mock.test/sim-swap-subscriptions/v0.2/subscriptions/sub-0001",
        "type": etype,
        "specversion": "1.0",
        "datacontenttype": "application/json",
        "time": "2026-10-05T14:00:00Z",
        "data": data if data is not None else {"subscriptionId": "sub-0001"},
    }


async def _enable_mom(mw) -> str:
    w = mw.world
    w.standard()
    w.watch(MOM, "user-asish", "care")
    r = await mw.alerts.post("/internal/watch", json={"line_id": w.lines[MOM], "enable": True}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subscriptions"] == 4 and body["evaluated"] == 1
    return w.lines[MOM]


@pytest.mark.integration
async def test_enable_fire_alert_within_seconds(mock_world) -> None:
    line = await _enable_mom(mock_world)
    state = (await mock_world.admin.get("/_admin/state")).json()
    assert len([s for s in state["subscriptions"].values() if s["status"] == "ACTIVE"]) == 4
    t = time.perf_counter()
    r = await mock_world.admin.post(f"/_admin/lines/{MOM}/events", json={"type": "sim_swap"})
    assert r.status_code == 200, r.text
    elapsed = time.perf_counter() - t
    assert [s.label for s in mock_world.sender.sent] == ["chain:user-asish"]
    assert elapsed < 5.0
    assert row_summary(audit_rows(mock_world.svc.store, line)) == [
        ("event", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms")
    ]


@pytest.mark.integration
async def test_unknown_token_is_200_and_touches_nothing(mock_world) -> None:
    line = await _enable_mom(mock_world)
    before = mock_world.svc.store.scan_all(S.ALERTS_STATE)
    r = await mock_world.alerts.post("/hooks/sim-swap/" + "x" * 32, json=_event())
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert mock_world.svc.metrics["hooks_unknown_token"] == 1
    assert mock_world.svc.store.scan_all(S.ALERTS_STATE) == before
    assert audit_rows(mock_world.svc.store, line) == []
    # a number-shaped "token" is just unknown too
    r = await mock_world.alerts.post("/hooks/sim-swap/16135550102", json=_event())
    assert r.status_code == 200


@pytest.mark.integration
async def test_bad_payload_dropped_and_counted_without_oracle(mock_world) -> None:
    line = await _enable_mom(mock_world)
    token = S.get_line_row(mock_world.svc.store, line).sink_token
    bad = [
        b"not json",
        {"hello": "world"},
        _event(etype="org.camaraproject.sim-swap-subscriptions.v0.unknown"),
        {**_event(), "specversion": "0.3"},
        _event(data={}),
    ]
    for tok in (token, "y" * 32):
        for payload in bad:
            kw = {"content": payload} if isinstance(payload, bytes) else {"json": payload}
            r = await mock_world.alerts.post(f"/hooks/sim-swap/{tok}", **kw)
            assert r.status_code == 400 and r.json() == {"status": "dropped"}
    assert mock_world.svc.metrics["hooks_invalid"] == 2 * len(bad)
    r = await mock_world.alerts.post(f"/hooks/telepathy/{token}", json=_event())
    assert r.status_code == 400
    assert audit_rows(mock_world.svc.store, line) == []


@pytest.mark.integration
async def test_duplicate_event_id_dropped(mock_world) -> None:
    line = await _enable_mom(mock_world)
    await mock_world.admin.post(f"/_admin/lines/{MOM}/events", json={"type": "sim_swap"})
    assert len(mock_world.sender.sent) == 1
    deliveries = (await mock_world.admin.get("/_admin/state")).json()["deliveries"]
    swapped = [d for d in deliveries if d["type"] == SWAPPED][-1]
    token = S.get_line_row(mock_world.svc.store, line).sink_token
    # the carrier retries the same CloudEvent (same source + id)
    sub_id = swapped["subscription_id"]
    event = _event(event_id=swapped["event_id"], data={"subscriptionId": sub_id})
    event["source"] = f"https://mock.test/sim-swap-subscriptions/v0.2/subscriptions/{sub_id}"
    first = await mock_world.alerts.post(f"/hooks/sim-swap/{token}", json=event)
    second = await mock_world.alerts.post(f"/hooks/sim-swap/{token}", json=event)
    assert first.status_code == second.status_code == 200
    assert mock_world.svc.metrics["hooks_duplicate"] >= 1
    assert len(mock_world.sender.sent) == 1


@pytest.mark.integration
async def test_wrong_bearer_on_known_token_is_unknown(mock_world) -> None:
    line = await _enable_mom(mock_world)
    token = S.get_line_row(mock_world.svc.store, line).sink_token
    r = await mock_world.alerts.post(
        f"/hooks/sim-swap/{token}", json=_event(), headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 200
    assert mock_world.svc.metrics["hooks_unknown_token"] == 1


@pytest.mark.integration
async def test_internal_api_is_bearer_protected(mock_world) -> None:
    body = {"line_id": "ln_" + "a" * 64, "enable": True}
    assert (await mock_world.alerts.post("/internal/watch", json=body)).status_code == 401
    r = await mock_world.alerts.post("/internal/watch", json=body, headers={"Authorization": "Bearer x"})
    assert r.status_code == 401
    r = await mock_world.alerts.post("/internal/watch", json={**body, "line_id": "16135550102"}, headers=AUTH)
    assert r.status_code == 422
    mock_world.svc.settings = type(mock_world.svc.settings)(internal_bearer=None)
    assert (await mock_world.alerts.post("/internal/watch", json=body, headers=AUTH)).status_code == 503


@pytest.mark.integration
async def test_disable_unsubscribes(mock_world) -> None:
    from tower_consent import Watch, upsert_watch

    line = await _enable_mom(mock_world)
    upsert_watch(
        mock_world.svc.store, Watch(line_id=line, watcher_user_id="user-asish", profile="care", enabled=False)
    )
    r = await mock_world.alerts.post(
        "/internal/watch",
        json={"line_id": line, "enable": False, "watcher_user_id": "user-asish"},
        headers=AUTH,
    )
    assert r.json()["unsubscribed"] == 4
    state = (await mock_world.admin.get("/_admin/state")).json()
    assert not [s for s in state["subscriptions"].values() if s["status"] == "ACTIVE"]
    assert S.get_line_row(mock_world.svc.store, line) is None
    await mock_world.admin.post(f"/_admin/lines/{MOM}/events", json={"type": "sim_swap"})
    assert mock_world.sender.sent == []


@pytest.mark.integration
async def test_expired_subscription_event_resubscribes(mock_world) -> None:
    line = await _enable_mom(mock_world)
    old = set(S.get_line_row(mock_world.svc.store, line).subscription_ids)
    r = await mock_world.admin.post("/_admin/clock", json={"advance_s": 31 * 86400})
    assert r.status_code == 200
    row = S.get_line_row(mock_world.svc.store, line)
    assert len(row.subscription_ids) == 4 and not (set(row.subscription_ids) & old)
    assert ("event", "ok", ("OK",), None) in row_summary(audit_rows(mock_world.svc.store, line))


@pytest.mark.unit
def test_validate_event_against_vendored_schema() -> None:
    assert validate_event("sim-swap", _event()) == SWAPPED
    assert validate_event("reachability", _event()) is None  # wrong API's type
    reach = _event(
        etype="org.camaraproject.device-reachability-status-subscriptions.v0.reachability-disconnected"
    )
    assert validate_event("reachability", reach) is not None
    assert validate_event("sim-swap", {**_event(), "time": "yesterday"}) is None
    missing = _event()
    del missing["source"]
    assert validate_event("sim-swap", missing) is None
