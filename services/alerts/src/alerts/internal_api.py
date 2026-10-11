"""Internal API (bearer `INTERNAL_BEARER`; refuses everything when it is unset).

- `POST /internal/watch {line_id, enable, profile?, watcher_user_id?}` — called by Tower's `watch_line`
  after it has upserted the Watch (e2e §3 "Variants"). `enable=true`: subscribe the line's kinds and take a
  baseline observation (trigger `poll`). `enable=false`: when no enabled Watch remains on the line,
  unsubscribe and drop its sink token; otherwise re-subscribe to what the remaining Watches need.
  If the Watch doesn't exist and both `profile` and `watcher_user_id` are given, it is created.
- `GET /internal/sent?after=<n>` — local mode only (`ALERTS_MODE=local`; the route does not exist otherwise):
  the SMS this process sent, from the in-memory ledger (`sent_log.py`): `{n, at, template, role, user_id, body}`,
  never a number. The demo UI's live feed reads it (doc 11 §4, G3).
- `POST /inbound/sms {from, body}` — an SMS reply relayed by the local SMS gateway (on AWS the SNS inbound
  topic invokes `handler.py` instead). The number is hashed at once (`escalation.handle_reply`).
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tower_consent import Watch, get_watch, upsert_watch
from tower_consent.crypto import is_line_id
from tower_consent.models import Profile

from alerts import escalation
from alerts import state as S
from alerts.context import AlertsService
from alerts.runner import process_line
from alerts.subscriptions import kinds_for_line, subscribe, unsubscribe


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    enable: bool
    profile: Profile | None = None
    watcher_user_id: str | None = None

    @field_validator("line_id")
    @classmethod
    def _line_id(cls, v: str) -> str:
        if not is_line_id(v):
            raise ValueError("line_id must be an HMAC line id")
        return v


class InboundSms(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", repr=False)
    body: str = Field(max_length=480)


async def set_watch(svc: AlertsService, req: WatchRequest) -> dict[str, Any]:
    now = await svc.clock.now()
    if req.enable and req.watcher_user_id and req.profile:
        if get_watch(svc.store, req.line_id, req.watcher_user_id) is None:
            upsert_watch(
                svc.store,
                Watch(line_id=req.line_id, watcher_user_id=req.watcher_user_id, profile=req.profile),
            )
    if not req.enable and req.watcher_user_id:
        S.clear_escalation(svc.store, req.line_id, req.watcher_user_id)
    kinds = kinds_for_line(svc, req.line_id)
    if not kinds:
        removed = await unsubscribe(svc, req.line_id)
        return {"line_id": req.line_id, "subscribed": [], "unsubscribed": removed}
    ids = await subscribe(svc, req.line_id, kinds, now)
    baseline = 0
    if req.enable:
        baseline = len(await process_line(svc, req.line_id, "poll", now))
    return {
        "line_id": req.line_id,
        "subscribed": list(kinds),
        "subscriptions": len(ids),
        "evaluated": baseline,
    }


def router(svc: AlertsService) -> APIRouter:
    def bearer(authorization: str | None = Header(default=None)) -> None:
        expected = svc.settings.internal_bearer
        if not expected:
            raise HTTPException(status_code=503, detail="internal API disabled (INTERNAL_BEARER unset)")
        if authorization is None or not hmac.compare_digest(authorization, f"Bearer {expected}"):
            raise HTTPException(status_code=401, detail="unauthorized")

    r = APIRouter(dependencies=[Depends(bearer)])

    @r.post("/internal/watch")
    async def watch(req: WatchRequest) -> dict[str, Any]:
        return await set_watch(svc, req)

    if svc.settings.mode == "local" and svc.sent_log is not None:
        log_ = svc.sent_log

        @r.get("/internal/sent")
        async def sent(after: int = Query(default=0, ge=0)) -> dict[str, Any]:
            return {"sent": [e.dump() for e in log_.after(after)]}

    @r.post("/inbound/sms")
    async def inbound(msg: InboundSms) -> dict[str, Any]:
        now = await svc.clock.now()
        return {"results": await escalation.handle_reply(svc, msg.from_, msg.body, now)}

    return r
