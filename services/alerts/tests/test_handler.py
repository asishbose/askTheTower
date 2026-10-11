"""Lambda routing (handler.py): API Gateway → the FastAPI app; Scheduler `{profile}` → poll; SNS → replies."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from alerts import handler
from alerts.runner import process_line
from alerts.testing import BEARER, MOM, T0, World

pytestmark = pytest.mark.integration


def _invoke(svc, event):
    return handler.lambda_handler(event, None, svc=svc)


async def _run(svc, event):
    return await handler._dispatch(svc, event)


async def test_routes(world: World, mom: str, carrier, clock, sender) -> None:
    svc = world.svc
    # API Gateway HTTP API v2 → internal API
    gw = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/internal/watch",
        "headers": {"Authorization": f"Bearer {BEARER}", "content-type": "application/json"},
        "body": json.dumps({"line_id": mom, "enable": True}),
        "isBase64Encoded": False,
    }
    r = await _run(svc, gw)
    assert r["statusCode"] == 200 and json.loads(r["body"])["subscriptions"] == 4
    hook = {**gw, "rawPath": "/hooks/sim-swap/" + "z" * 32, "body": "{}", "headers": {}}
    assert (await _run(svc, hook))["statusCode"] == 400

    # EventBridge Scheduler
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    r = await _run(svc, {"profile": "care"})
    assert r == {"profile": "care", "lines": 1, "alerts": 1, "escalated": 0}
    assert [s.label for s in sender.sent] == ["chain:user-asish"]

    # SNS inbound (two-way SMS): nothing pending → no result, nothing raised
    sns = {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {"Message": json.dumps({"originationNumber": MOM, "messageBody": "OK"})},
            }
        ]
    }
    assert await _run(svc, sns) == {"results": []}
    assert await _run(svc, {"action": "tick"}) == {"escalated": []}
    assert await _run(svc, {"something": "else"}) == {"status": "ignored"}


def test_sync_entry_point(world: World, mom: str) -> None:
    assert _invoke(world.svc, {"profile": "self"}) == {
        "profile": "self",
        "lines": 0,
        "alerts": 0,
        "escalated": 0,
    }


async def test_baseline_only_once(world: World, mom: str, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    await process_line(world.svc, mom, "poll", T0 + timedelta(minutes=5))
    assert sender.sent == []
