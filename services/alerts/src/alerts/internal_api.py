"""Internal API (bearer `INTERNAL_BEARER`; refuses everything when it is unset), and the two demo admin routes
(bearer `Settings.admin_routes_bearer`: `DEMO_ADMIN_BEARER` when set, never `INTERNAL_BEARER` then; 06 §3.1).

- `POST /internal/watch {line_id, enable, profile?, watcher_user_id?}` — called by Tower's `watch_line`
  after it has upserted the Watch (e2e §3 "Variants"). `enable=true`: subscribe the line's kinds and take a
  baseline observation (trigger `poll`). `enable=false`: when no enabled Watch remains on the line,
  unsubscribe and drop its sink token; otherwise re-subscribe to what the remaining Watches need.
  If the Watch doesn't exist and both `profile` and `watcher_user_id` are given, it is created.
- `GET /internal/sent?after=<n>` — registered only when `Settings.admin_routes` (`ALERTS_MODE=local` or
  `ALERTS_ADMIN=1`; otherwise the route does not exist, 404): the SMS sent, from the service's `SentLog`
  (`sent_log.py`): `{n, at, template, role, user_id, body}`, never a number. The demo UI's live feed reads it
  (06 §3.1; doc 11 §4, G3). Its bearer is the admin one: `DEMO_ADMIN_BEARER` when configured (AWS, demo admin path
  open), else `INTERNAL_BEARER` in local mode only; outside local mode without `DEMO_ADMIN_BEARER` → 503.
- `POST /internal/reset {user_ids}` — same registration rule: the demo reset (06 §3.2, `demo_reset.py`), counts only.
- `POST /inbound/sms {from, body}` — an SMS reply relayed by the local SMS gateway (on AWS the SNS inbound
  topic invokes `handler.py` instead). The number is hashed at once (`escalation.handle_reply`).
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from tower_consent import Watch, get_watch, upsert_watch
from tower_consent.crypto import is_line_id
from tower_consent.models import Profile

from alerts import escalation
from alerts import state as S
from alerts.context import AlertsService
from alerts.demo_reset import reset_users
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


UserId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class ResetRequest(BaseModel):
    """06 §3.2: 1-8 user ids (Cognito subs on AWS, `user-…` locally)."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[UserId] = Field(min_length=1, max_length=8)


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
        held = S.get_escalation(svc.store, req.line_id, req.watcher_user_id)
        # conditional (06 §5): if a tick moved the chain meanwhile, its next tick sees the Watch off and ends it
        if held is not None:
            S.clear_escalation(svc.store, held)
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


def check_bearer(authorization: str | None, expected: str | None, what: str) -> None:
    """503 when no bearer is configured (fail closed), 401 unless `authorization` is exactly `Bearer <expected>`."""
    if not expected:
        raise HTTPException(status_code=503, detail=f"disabled ({what} unset)")
    if authorization is None or not hmac.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="unauthorized")


def router(svc: AlertsService) -> APIRouter:
    def bearer(authorization: str | None = Header(default=None)) -> None:
        check_bearer(authorization, svc.settings.internal_bearer, "INTERNAL_BEARER")

    r = APIRouter()
    internal = APIRouter(dependencies=[Depends(bearer)])

    @internal.post("/internal/watch")
    async def watch(req: WatchRequest) -> dict[str, Any]:
        return await set_watch(svc, req)

    @internal.post("/inbound/sms")
    async def inbound(msg: InboundSms) -> dict[str, Any]:
        now = await svc.clock.now()
        return {"results": await escalation.handle_reply(svc, msg.from_, msg.body, now)}

    r.include_router(internal)
    if svc.settings.admin_routes:
        r.include_router(admin_router(svc))
    return r


def admin_router(svc: AlertsService) -> APIRouter:
    """`/internal/sent` and `/internal/reset` (06 §3.1, §3.2), behind the admin bearer only."""

    def admin_bearer(authorization: str | None = Header(default=None)) -> None:
        check_bearer(authorization, svc.settings.admin_routes_bearer, "DEMO_ADMIN_BEARER")

    r = APIRouter(dependencies=[Depends(admin_bearer)])

    @r.get("/internal/sent")
    async def sent(after: int = Query(default=0, ge=0)) -> dict[str, Any]:
        return {"sent": [e.dump() for e in svc.sent_log.after(after)]}

    @r.post("/internal/reset")
    async def reset(req: ResetRequest) -> dict[str, int]:
        result = await asyncio.to_thread(reset_users, svc.store, list(req.user_ids))
        return {
            "lines": result.lines,
            "watches_deleted": result.watches_deleted,
            "alerts_state_deleted": result.alerts_state_deleted,
        }

    return r
