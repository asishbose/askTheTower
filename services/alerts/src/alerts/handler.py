"""Lambda entry point: routes by event source (06; 10 §2–3).

| Event | Route |
|---|---|
| API Gateway (HTTP API v2 or REST v1) | the same FastAPI app as local: `/hooks/...`, `/internal/watch` |
| EventBridge Scheduler `{"profile": "transplant" \\| "care" \\| "self"}` | `runner.poll(profile)` |
| SNS (two-way SMS inbound topic) `Records[].Sns.Message` = `{originationNumber, messageBody}` | `escalation.handle_reply` |
| direct invoke `{"action": "watch", line_id, enable, ...}` | `internal_api.set_watch` (Tower may invoke instead of HTTP) |
| direct invoke `{"action": "tick"}` | `escalation.tick` |

One event loop per container, reused across invocations, so the carrier client's connection pool and token
cache survive warm starts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx

from alerts import escalation
from alerts.context import AlertsService
from alerts.internal_api import WatchRequest, set_watch
from alerts.runner import build_service, poll

log = logging.getLogger("alerts.handler")

_LOOP: asyncio.AbstractEventLoop | None = None
_SVC: AlertsService | None = None
_APP: tuple[AlertsService, Any] | None = None  # (the service it was built for, the app)


def _loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def _service() -> AlertsService:
    global _SVC
    if _SVC is None:
        _SVC = build_service()
    return _SVC


def _app(svc: AlertsService) -> Any:
    """The FastAPI app for `svc`, built once per service (a warm container reuses it)."""
    global _APP
    if _APP is None or _APP[0] is not svc:
        from alerts.local import create_app

        _APP = (svc, create_app(svc, scheduler=False))
    return _APP[1]


async def _http(svc: AlertsService, event: dict[str, Any]) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod", "GET")
    path = event.get("rawPath") or event.get("path") or "/"
    query = event.get("rawQueryString") or ""
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    transport = httpx.ASGITransport(app=_app(svc))
    async with httpx.AsyncClient(transport=transport, base_url="https://alerts.lambda") as client:
        r = await client.request(method, path + (f"?{query}" if query else ""), content=raw, headers=headers)
    return {
        "statusCode": r.status_code,
        "headers": {"content-type": r.headers.get("content-type", "application/json")},
        "body": r.text,
    }


async def _dispatch(svc: AlertsService, event: dict[str, Any]) -> Any:
    if "requestContext" in event:
        return await _http(svc, event)
    if event.get("Records") and event["Records"][0].get("EventSource", "").lower() == "aws:sns":
        results = []
        for rec in event["Records"]:
            msg = json.loads(rec["Sns"]["Message"])
            now = await svc.clock.now()
            results += await escalation.handle_reply(
                svc, str(msg.get("originationNumber", "")), str(msg.get("messageBody", "")), now
            )
        return {"results": results}
    if "profile" in event:
        return await poll(svc, str(event["profile"]))
    if event.get("action") == "watch":
        req = WatchRequest.model_validate({k: v for k, v in event.items() if k != "action"})
        return await set_watch(svc, req)
    if event.get("action") == "tick":
        return {"escalated": await escalation.tick(svc, await svc.clock.now())}
    log.warning("unrouted event (keys=%s)", sorted(event)[:8])
    return {"status": "ignored"}


def lambda_handler(event: dict[str, Any], context: Any = None, *, svc: AlertsService | None = None) -> Any:
    return _loop().run_until_complete(_dispatch(svc or _service(), event))


handler = lambda_handler
