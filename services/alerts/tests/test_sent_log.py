"""06 §3.1 (U1/U4, 2026-10-11): the `SentLog` Protocol and its three implementations.

One contract (`assert_contract`) runs against `MemorySentLog` and `DynamoSentLog` (moto / DynamoDB Local, the
`store` fixture's backends) — the Liskov rule: whatever a reader sees from one, it sees from the other.
`NullSentLog` records nothing. `build_sent_log` picks each for its mode; `send.py` never asks which it has."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from alerts.config import Settings
from alerts.local import create_app
from alerts.runner import build_sent_log, process_line
from alerts.sent_log import (
    MAX_ENTRIES,
    SENT_SMS,
    DynamoSentLog,
    MemorySentLog,
    NullSentLog,
    SentLog,
    terraform_definition,
)
from alerts.testing import BEARER, MOM, T0, TEST_WALL, World
from tower_consent import Store
from tower_consent.tables import create_table_kwargs

from tests.helpers.patterns import phone_hits

AUTH = {"Authorization": f"Bearer {BEARER}"}
ADMIN_BEARER = "demo-admin-bearer-0123456789abcdef"
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_BEARER}"}
BODIES = ("Mom's SIM changed in the last hour.", "Mom's phone is off the network.", "Your line is back.")


def assert_contract(log: SentLog) -> None:
    assert log.after(0) == []
    for i, body in enumerate(BODIES):
        e = log.record(
            at=T0 + timedelta(minutes=i),
            template="SIM_SWAPPED_RECENT.sms",
            role="watcher",
            user_id="u",
            body=body,
        )
        assert e.n == i + 1
    got = log.after(0)
    assert [e.n for e in got] == [1, 2, 3]  # counts from 1; the counter row (n = 0) is never an entry
    assert [e.body for e in got] == list(BODIES) and got[0].at == T0
    assert [e.n for e in log.after(1)] == [2, 3]  # ascending, only newer
    assert log.after(3) == []
    assert [e.n for e in log.after(0, limit=2)] == [1, 2]
    for e in got:
        dumped = e.dump()
        assert set(dumped) == {"n", "at", "template", "role", "user_id", "body"}  # the number is not a field
        assert phone_hits(json.dumps(dumped)) == []


@pytest.fixture
def sent_store(store: Store) -> Iterator[Store]:
    """`SentSms` beside the fixture's tables (Terraform creates it on AWS, only with the admin path open)."""
    store.client.create_table(**create_table_kwargs(SENT_SMS, store.prefix))
    yield store
    for item in store.scan_all(SENT_SMS):
        for k, v in item.items():
            if k not in ("expires_at", "n", "seq"):
                assert phone_hits(str(v)) == [], (k, v)
    store.client.delete_table(TableName=store.name(SENT_SMS))


@pytest.mark.unit
def test_memory_sent_log_contract() -> None:
    assert_contract(MemorySentLog())


@pytest.mark.unit
def test_null_sent_log_keeps_nothing() -> None:
    log = NullSentLog()
    e = log.record(at=T0, template="t", role="watcher", user_id="u", body="b")
    assert e.n == 1 and log.after(0) == []


@pytest.mark.integration
def test_dynamo_sent_log_contract_and_ttl(sent_store: Store) -> None:
    log = DynamoSentLog(sent_store, wall=lambda: TEST_WALL)
    assert_contract(log)
    items = {int(i["n"]): i for i in sent_store.scan_all(SENT_SMS)}
    assert items[0]["seq"] == 3 and items[0]["pk"] == "sent"  # the counter row
    # TTL is the real time of the write + 24 h, whatever (mock) time `at` says (06 §7 "Clocks")
    for n in (1, 2, 3):
        assert items[n]["expires_at"] == int((TEST_WALL + timedelta(hours=24)).timestamp())
    assert items[3]["at"] != items[1]["at"]
    assert [e.n for e in DynamoSentLog(sent_store, wall=lambda: TEST_WALL).after(0)] == [
        1,
        2,
        3,
    ]  # another Lambda reads the same list


@pytest.mark.unit
def test_build_sent_log_picks_one_per_mode() -> None:
    store = Store(client=None, prefix="p-")  # type: ignore[arg-type]  # never called: construction only
    assert isinstance(build_sent_log(Settings(mode="local"), store), MemorySentLog)
    assert isinstance(build_sent_log(Settings(mode="local", admin=True), store), MemorySentLog)
    for mode in ("lambda", "k8s"):
        assert isinstance(build_sent_log(Settings(mode=mode), store), NullSentLog)  # type: ignore[arg-type]
        assert isinstance(build_sent_log(Settings(mode=mode, admin=True), store), DynamoSentLog)  # type: ignore[arg-type]
    assert Settings.from_env({"ALERTS_MODE": "lambda", "ALERTS_ADMIN": "1"}).admin_routes is True
    assert Settings.from_env({"ALERTS_MODE": "lambda", "ALERTS_ADMIN": "0"}).admin_routes is False
    assert Settings.from_env({"ALERTS_MODE": "lambda"}).admin_routes is False
    assert Settings.from_env({"ALERTS_MODE": "local"}).admin_routes is True


@pytest.mark.unit
def test_send_py_has_no_branch_on_the_sent_log() -> None:
    src = (Path(__file__).resolve().parents[1] / "src" / "alerts" / "send.py").read_text("utf-8")
    assert "svc.sent_log.record(" in src
    assert not re.search(
        r"sent_log\s+is|isinstance\([^)]*SentLog|MemorySentLog|DynamoSentLog|NullSentLog", src
    )
    assert "settings.mode" not in src and "settings.admin" not in src


@pytest.mark.unit
def test_terraform_definition_is_demo_admin_only() -> None:
    d = terraform_definition()
    assert d["name"] == "SentSms" and d["hash_key"] == "pk" and d["range_key"] == "n"
    assert {"name": "n", "type": "N"} in d["attributes"]
    assert d["ttl"] == {"enabled": True, "attribute_name": "expires_at"}
    assert d["demo_admin_only"] is True and d["point_in_time_recovery"] is False
    assert MAX_ENTRIES == 200


@pytest.mark.integration
async def test_lambda_with_admin_serves_the_dynamo_list(
    world: World, mom: str, carrier, clock, sent_store: Store
) -> None:
    # decision 1 (2026-10-11): on AWS the admin routes take DEMO_ADMIN_BEARER, never the internal bearer
    world.svc.settings = Settings(
        mode="lambda", admin=True, internal_bearer=BEARER, admin_bearer=ADMIN_BEARER
    )
    world.svc.sent_log = DynamoSentLog(sent_store, wall=world.svc.wall)
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as c:
        assert (await c.get("/internal/sent")).status_code == 401
        assert (await c.get("/internal/sent", headers=AUTH)).status_code == 401
        r = await c.get("/internal/sent", params={"after": 0}, headers=ADMIN_AUTH)
        assert r.status_code == 200, r.text
        (entry,) = r.json()["sent"]
        assert (entry["n"], entry["role"], entry["user_id"]) == (1, "watcher", "user-asish")
        assert entry["template"] == "SIM_SWAPPED_RECENT.sms" and entry["body"]
        assert phone_hits(r.text) == []
        assert (await c.get("/internal/sent", params={"after": 1}, headers=ADMIN_AUTH)).json() == {"sent": []}


@pytest.mark.integration
async def test_lambda_without_admin_has_no_admin_routes(world: World) -> None:
    world.svc.settings = Settings(mode="lambda", internal_bearer=BEARER)
    app = create_app(world.svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://alerts.test") as c:
        assert (await c.get("/internal/sent", headers=AUTH)).status_code == 404
        r = await c.post("/internal/reset", json={"user_ids": ["user-mom"]}, headers=AUTH)
        assert r.status_code == 404
