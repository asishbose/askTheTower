"""06 §3.2 (U3, 2026-10-11): `POST /internal/reset {user_ids}` — only the given users' lines lose their Watches and
line-keyed `AlertsState` rows; another user's line keeps both; `Users`, `Lines`, `Grants`, `Audit` untouched; no audit
row; bearer required; absent without local mode or `ALERTS_ADMIN=1`; 422 on 0 or 9 ids.

Human decision 1 (2026-10-11): with `DEMO_ADMIN_BEARER` configured, `/internal/sent` and `/internal/reset` accept only
it — the Alerts internal bearer is a 401 there; outside local mode without it they refuse (503); locally without it
they keep the internal bearer (compose)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from alerts import state as S
from alerts.config import Settings
from alerts.local import create_app
from alerts.state import ALERTS_STATE
from alerts.testing import ASISH, BEARER, MOM, PARTNER, T0, World
from tower_consent import tables as T

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {BEARER}"}
ADMIN_BEARER = "demo-admin-bearer-0123456789abcdef"
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_BEARER}"}


def _state_rows(world: World, line_id: str, watcher: str) -> None:
    """Every kind of line-keyed AlertsState row 06 §3.2 names, plus a dedupe row that must stay."""
    store = world.store
    S.ensure_sink_token(store, line_id)
    S.record_miss(store, line_id)
    assert S.claim_alert(store, line_id, watcher, "SIM_SWAPPED_RECENT", T0, S.EVENT_TTL, wall=T0)
    store.put(ALERTS_STATE, {"pk": f"esc#{line_id}#{watcher}", "line_id": line_id})
    S.remember_ack_route(store, line_id, line_id, watcher, T0)


def _snapshot(world: World) -> dict[str, list[dict[str, Any]]]:
    return {t.name: world.store.scan_all(t) for t in (T.USERS, T.LINES, T.GRANTS, T.AUDIT)}


async def test_reset_deletes_exactly_the_given_users_rows(world: World) -> None:
    world.standard()
    partner = world.bind("user-partner", PARTNER)
    mom, asish = world.lines[MOM], world.lines[ASISH]
    world.watch(MOM, "user-asish", "care")
    world.watch(MOM, "user-mom", "self")
    world.watch(ASISH, "user-asish", "self")
    world.watch(PARTNER, "user-partner", "self")
    for line_id, watcher in ((mom, "user-asish"), (asish, "user-asish"), (partner, "user-partner")):
        _state_rows(world, line_id, watcher)
    assert S.claim_event(world.store, "tok", "src", "evt-1", T0)
    before = _snapshot(world)
    partner_state = {i["pk"] for i in world.store.scan_all(ALERTS_STATE) if partner in i["pk"]}
    assert len(partner_state) == 4  # line#, rl#, esc#, ack# (+ its sink# below)

    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as c:
        assert (await c.post("/internal/reset", json={"user_ids": ["user-mom"]})).status_code == 401
        r = await c.post("/internal/reset", json={"user_ids": ["user-mom", "user-asish"]}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"lines": 2, "watches_deleted": 3, "alerts_state_deleted": 10}

    watches = world.store.scan_all(T.WATCHES)
    assert [(w["line_id"], w["watcher_user_id"]) for w in watches] == [(partner, "user-partner")]
    left = {i["pk"] for i in world.store.scan_all(ALERTS_STATE)}
    assert not any(mom in k or asish in k for k in left)
    partner_sink = S.get_line_row(world.store, partner)
    assert partner_sink is not None and partner_sink.sink_token
    assert partner_state | {f"sink#{partner_sink.sink_token}"} <= left
    assert any(k.startswith("evt#") for k in left)  # dedupe rows are left to their TTL
    assert _snapshot(world) == before  # Users, Lines, Grants, Audit untouched; no audit row written


async def test_reset_validates_the_ids_and_is_idempotent(world: World) -> None:
    world.standard()
    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as c:
        for bad in ([], [f"user-{i}" for i in range(9)], ["user mom"], ["x" * 129], [MOM]):
            r = await c.post("/internal/reset", json={"user_ids": bad}, headers=AUTH)
            assert r.status_code == 422, bad
        assert (
            await c.post("/internal/reset", json={"user_ids": ["u"], "x": 1}, headers=AUTH)
        ).status_code == 422
        r = await c.post("/internal/reset", json={"user_ids": ["user-nobody"]}, headers=AUTH)
        assert r.json() == {"lines": 0, "watches_deleted": 0, "alerts_state_deleted": 0}
        r = await c.post("/internal/reset", json={"user_ids": [f"user-{i}" for i in range(8)]}, headers=AUTH)
        assert r.status_code == 200


async def test_reset_is_served_on_lambda_only_with_alerts_admin(world: World) -> None:
    world.standard()
    for admin, status in ((False, 404), (True, 200)):
        world.svc.settings = Settings(
            mode="lambda", admin=admin, internal_bearer=BEARER, admin_bearer=ADMIN_BEARER
        )
        app = create_app(world.svc)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://alerts.test"
        ) as c:
            r = await c.post("/internal/reset", json={"user_ids": ["user-mom"]}, headers=ADMIN_AUTH)
            assert r.status_code == status, (admin, r.text)


async def _admin_statuses(world: World, settings: Settings, headers: dict[str, str]) -> tuple[int, int, int]:
    """(`GET /internal/sent`, `POST /internal/reset`, `POST /internal/watch`) with these headers."""
    world.svc.settings = settings
    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as c:
        sent = await c.get("/internal/sent", headers=headers)
        reset = await c.post("/internal/reset", json={"user_ids": ["user-mom"]}, headers=headers)
        watch = await c.post(
            "/internal/watch", json={"line_id": "ln_" + "a" * 64, "enable": True}, headers=headers
        )
    return sent.status_code, reset.status_code, watch.status_code


@pytest.mark.parametrize("mode", ["lambda", "k8s", "local"])
async def test_the_internal_bearer_never_opens_the_admin_routes(world: World, mode: str) -> None:
    """Decision 1: with `DEMO_ADMIN_BEARER` set, the Alerts internal bearer is a 401 on both admin routes."""
    world.standard()
    s = Settings(mode=mode, admin=True, internal_bearer=BEARER, admin_bearer=ADMIN_BEARER)  # type: ignore[arg-type]
    sent, reset, _ = await _admin_statuses(world, s, AUTH)
    assert (sent, reset) == (401, 401)
    sent, reset, watch = await _admin_statuses(world, s, ADMIN_AUTH)
    assert (sent, reset) == (200, 200)
    assert watch == 401  # and the admin bearer is not the internal one either: /internal/watch refuses it


async def test_off_local_without_the_admin_bearer_the_admin_routes_refuse(world: World) -> None:
    world.standard()
    s = Settings(mode="lambda", admin=True, internal_bearer=BEARER)
    sent, reset, watch = await _admin_statuses(world, s, AUTH)
    assert (sent, reset) == (503, 503) and watch != 401  # fail closed; Tower's watch API is unaffected


async def test_local_without_the_admin_bearer_keeps_the_internal_bearer(world: World) -> None:
    world.standard()
    s = Settings(mode="local", internal_bearer=BEARER)
    sent, reset, _ = await _admin_statuses(world, s, AUTH)
    assert (sent, reset) == (200, 200)
    sent, reset, _ = await _admin_statuses(world, s, ADMIN_AUTH)
    assert (sent, reset) == (401, 401)


def test_settings_read_demo_admin_bearer_and_hide_it() -> None:
    s = Settings.from_env({"ALERTS_MODE": "lambda", "ALERTS_ADMIN": "1", "DEMO_ADMIN_BEARER": ADMIN_BEARER})
    assert s.admin_bearer == ADMIN_BEARER and s.admin_routes_bearer == ADMIN_BEARER
    assert ADMIN_BEARER not in repr(s)
    assert Settings.from_env({"ALERTS_MODE": "lambda", "INTERNAL_BEARER": BEARER}).admin_routes_bearer is None
    assert Settings.from_env({"INTERNAL_BEARER": BEARER}).admin_routes_bearer == BEARER
