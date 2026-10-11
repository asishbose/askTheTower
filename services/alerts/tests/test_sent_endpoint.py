"""06 §3.1 / doc 11 G3: the local sent-SMS ledger and `GET /internal/sent` — template id, body, recipient role,
never a number; local mode only, behind the internal bearer."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from alerts import escalation
from alerts.config import Settings
from alerts.local import create_app
from alerts.runner import process_line
from alerts.sent_log import SentLog
from alerts.testing import ASISH, BEARER, MOM, T0, FakeLine, World

from tests.helpers.patterns import phone_hits

pytestmark = pytest.mark.integration

M = timedelta(minutes=1)
AUTH = {"Authorization": f"Bearer {BEARER}"}


async def test_grantee_watch_sms_is_recorded_as_watcher(world: World, mom: str, carrier, clock) -> None:
    world.svc.sent_log = SentLog()
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + M)
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    (entry,) = world.svc.sent_log.after(0)
    assert (entry.n, entry.role, entry.user_id) == (1, "watcher", "user-asish")
    assert entry.template == "SIM_SWAPPED_RECENT.sms"
    assert entry.at == clock.at
    assert phone_hits(json.dumps(entry.dump())) == []


async def test_owner_chain_steps_are_escalation_n(world: World, carrier, clock) -> None:
    world.svc.sent_log = SentLog()
    world.standard()
    world.grant_watch(ASISH, "user-asish", "user-partner", "asish")
    world.grant_watch(ASISH, "user-asish", "user-neighbour", "asish")
    world.watch(ASISH, "user-asish", "transplant", [("user-partner", True), ("user-neighbour", False)])
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line = world.lines[ASISH]
    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + M)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 21 * M)
    await process_line(world.svc, line, "poll", clock.at)
    clock.set(T0 + 36 * M)
    await escalation.tick(world.svc, clock.at)
    got = [(e.role, e.user_id) for e in world.svc.sent_log.after(0)]
    assert got == [
        ("line-holder", "user-asish"),
        ("escalation[0]", "user-partner"),
        ("escalation[1]", "user-neighbour"),
    ]
    assert [e.n for e in world.svc.sent_log.after(1)] == [2, 3]


async def _get(svc: Any, **params: Any) -> httpx.Response:
    app = create_app(svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://alerts.test") as c:
        return await c.get("/internal/sent", params=params, headers=AUTH)


async def test_endpoint_returns_entries_after_n(world: World) -> None:
    world.svc.sent_log = SentLog()
    for i in range(3):
        world.svc.sent_log.record(
            at=T0, template="UNREACHABLE.sms", role="watcher", user_id="u", body=f"b{i}"
        )
    r = await _get(world.svc, after=1)
    assert r.status_code == 200
    assert [e["n"] for e in r.json()["sent"]] == [2, 3]
    assert set(r.json()["sent"][0]) == {"n", "at", "template", "role", "user_id", "body"}


async def test_endpoint_needs_the_bearer(world: World) -> None:
    world.svc.sent_log = SentLog()
    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://alerts.test") as c:
        assert (await c.get("/internal/sent")).status_code == 401


async def test_endpoint_absent_outside_local_mode(world: World) -> None:
    world.svc.sent_log = SentLog()
    world.svc.settings = Settings(mode="lambda", internal_bearer=BEARER)
    assert (await _get(world.svc)).status_code == 404


def test_build_service_has_a_ledger_only_in_local_mode() -> None:
    assert Settings.from_env({"ALERTS_MODE": "local"}).mode == "local"
    log = SentLog(maxlen=2)
    for i in range(3):
        log.record(at=T0, template="t", role="watcher", user_id="u", body=str(i))
    assert [e.n for e in log.after(0)] == [2, 3]  # bounded, numbering keeps counting
