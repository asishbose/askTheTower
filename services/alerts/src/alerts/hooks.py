"""`POST /hooks/{kind}/{sink_token}` — the CAMARA subscription webhook receiver (06 §6).

1. **Validate** the body against the vendored CloudEvents schema of that API (`schemas/*.cloudevents.json`,
   generated from `specs/camara/` by `scripts/vendor_schemas.py`): the base `CloudEvent`, then the event
   schema its `type` maps to. Invalid → 400 `{"status": "dropped"}` and counted. Validation runs *before* the
   token is looked at, so the status code says nothing about whether a token exists.
2. **Token → line_id.** Unknown (or a bearer that doesn't match it) → 200, logged, counted, no state touched —
   never a 404 that would confirm existence.
3. **Dedupe** on (token, CloudEvents `source`, `id`) with a conditional put in `AlertsState` (7-day TTL).
   Duplicate → 200, dropped, counted.
4. `subscription-ended` (expired / network-terminated / token expired / max events) → re-subscribe, audited;
   `SUBSCRIPTION_DELETED` is our own unsubscribe and is ignored. `subscription-started/updated` → ignored.
   Anything else → `process_line(line_id, trigger="event")`, inline (Lambda-style).

Nothing from the payload is logged: a carrier may include the phone number in `data` (the spec allows it).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from tower_policy import ReasonCode

from alerts import state as S
from alerts.context import AlertsService, audit
from alerts.runner import process_line
from alerts.subscriptions import kinds_for_line, subscribe

log = logging.getLogger("alerts.hooks")

SCHEMA_FILES = {
    "sim-swap": "sim-swap-subscriptions.cloudevents.json",
    "reachability": "device-reachability-status-subscriptions.cloudevents.json",
}
RESUBSCRIBE_REASONS = frozenset(
    {"SUBSCRIPTION_EXPIRED", "NETWORK_TERMINATED", "ACCESS_TOKEN_EXPIRED", "MAX_EVENTS_REACHED"}
)


@cache
def _schema(kind: str) -> dict[str, Any]:
    text = resources.files("alerts.schemas").joinpath(SCHEMA_FILES[kind]).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


@cache
def _validator(kind: str, name: str) -> Draft202012Validator:
    defs = _schema(kind)["$defs"]
    return Draft202012Validator({"$defs": defs, "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker())


def validate_event(kind: str, payload: Any) -> str | None:
    """The event `type` if `payload` is a valid CloudEvent of that API, else None."""
    if kind not in SCHEMA_FILES or not isinstance(payload, dict):
        return None
    try:
        _validator(kind, "CloudEvent").validate(payload)
        etype = str(payload["type"])
        name = _schema(kind)["mapping"].get(etype)
        if name is None:
            return None
        _validator(kind, name).validate(payload)
    except ValidationError:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("subscriptionId"), str):
        return None
    if not _is_rfc3339(str(payload["time"])):  # `format: date-time` (jsonschema checks it only with extras)
        return None
    return etype


def _is_rfc3339(value: str) -> bool:
    if "T" not in value.upper():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


async def handle_event(
    svc: AlertsService, kind: str, token: str, event: dict[str, Any], authorization: str | None = None
) -> str:
    """A *valid* CloudEvent for `token`. Returns a status word (for tests and logs)."""
    line_id = S.line_for_token(svc.store, token)
    if line_id is not None and authorization is not None and authorization != f"Bearer {token}":
        line_id = None
    if line_id is None:
        svc.count("hooks_unknown_token")
        log.info("hook dropped: unknown sink token (kind=%s)", kind)
        return "unknown_token"
    now = await svc.clock.now()
    if not S.claim_event(svc.store, token, str(event["source"]), str(event["id"]), now):
        svc.count("hooks_duplicate")
        log.info("hook dropped: duplicate event line=%s", line_id[:12])
        return "duplicate"
    S.touch_event(svc.store, line_id, now)
    svc.count("hooks_accepted")
    etype = str(event["type"])
    if etype.endswith(".subscription-ended"):
        reason = str(event["data"].get("terminationReason", ""))
        if reason in RESUBSCRIBE_REASONS and kinds_for_line(svc, line_id):
            await subscribe(svc, line_id, None, now)
            audit(svc, line_id, now, trigger="event", outcome="ok", codes=[ReasonCode.OK])
            svc.count("resubscribed")
            return "resubscribed"
        return "ignored"
    if etype.endswith((".subscription-started", ".subscription-updated")):
        return "ignored"
    log.info("hook event line=%s type=%s", line_id[:12], etype.rsplit(".", 1)[-1])
    await process_line(svc, line_id, "event", now)
    return "processed"


def router(svc: AlertsService) -> APIRouter:
    r = APIRouter()

    @r.post("/hooks/{kind}/{sink_token}")
    async def hook(kind: str, sink_token: str, request: Request) -> JSONResponse:
        try:
            payload = json.loads(await request.body())
        except (ValueError, UnicodeDecodeError):
            payload = None
        if validate_event(kind, payload) is None:
            svc.count("hooks_invalid")
            log.info("hook dropped: invalid payload (kind=%s)", kind if kind in SCHEMA_FILES else "?")
            return JSONResponse({"status": "dropped"}, status_code=400)
        await handle_event(svc, kind, sink_token, payload, request.headers.get("authorization"))
        return JSONResponse({"status": "ok"}, status_code=200)

    return r
