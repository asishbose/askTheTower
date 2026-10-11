"""Orchestration: the two triggers → `evaluate` → `deliver` → `last_state`; the poller; service wiring.

`process_line(svc, line_id, trigger)` is what a webhook and a poll both call (06 §1). Per Decision:

| Decision | What happens |
|---|---|
| `refused` (grant gone) | on an **event**: one `SUPPRESSED_REVOKED` row (the carrier told us something we may no longer pass on); on a poll: nothing (no carrier call was made) |
| `miss` | `last_state` kept; the miss counted on the line; the 3rd consecutive miss → one `CARRIER_ERROR` row (06 §8) |
| `baseline` | `last_state` written; no alert |
| `evaluated`, no change | `last_state` written; no audit row (nothing was released) |
| `evaluated`, change | `deliver` (re-resolve → rate limit → audit → send), then `last_state` written with `last_alert_at` |
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from tower_audit import Trigger
from tower_consent import Store, crypto_from_env, list_watches_by_profile, update_last_state
from tower_policy import ReasonCode

from alerts import escalation
from alerts import state as S
from alerts.clock import Clock, MockCarrierClock, SystemClock
from alerts.config import Settings, carrier_env
from alerts.context import AlertsService, audit
from alerts.evaluate import Decision, evaluate
from alerts.send import DeliveryResult, deliver
from alerts.send_backends import LogSender, SmsSender, SnsSender, WebhookSmsSender
from alerts.sent_log import SentLog
from alerts.subscriptions import watchdog

log = logging.getLogger("alerts.runner")

MISSES_BEFORE_AUDIT = 3


async def apply(svc: AlertsService, d: Decision) -> DeliveryResult | None:
    """Act on one Decision (table above). Returns the delivery result when an alert was attempted."""
    if d.kind == "refused":
        log.info("watch refused line=%s watcher=%s code=%s", d.line_id[:12], d.watcher_user_id, d.codes[0])
        if d.trigger == "event":
            audit(
                svc,
                d.line_id,
                d.now,
                trigger="event",
                outcome="suppressed",
                codes=[ReasonCode.SUPPRESSED_REVOKED],
            )
            svc.count("suppressed_revoked")
        return None
    if d.kind == "miss":
        return None  # counted once per line in process_line
    result: DeliveryResult | None = None
    state = d.new_state
    assert state is not None
    if d.notify:
        result = await deliver(svc, d)
        if result.sent_codes:
            stamps = dict(state.last_alert_at)
            stamps.update({c.value: d.now for c in result.sent_codes})
            state = state.model_copy(update={"last_alert_at": stamps})
    if not update_last_state(svc.store, d.line_id, d.watcher_user_id, state):
        log.info("last_state not moved (a newer observation won) line=%s", d.line_id[:12])
    return result


async def process_line(
    svc: AlertsService, line_id: str, trigger: Trigger, now: datetime | None = None
) -> list[tuple[Decision, DeliveryResult | None]]:
    now = now if now is not None else await svc.clock.now()
    decisions = await evaluate(svc, line_id, trigger, now)
    if any(d.kind == "miss" for d in decisions):
        misses = S.record_miss(svc.store, line_id)
        if misses == MISSES_BEFORE_AUDIT:
            audit(svc, line_id, now, trigger=trigger, outcome="refused", codes=[ReasonCode.CARRIER_ERROR])
    elif any(d.kind in ("baseline", "evaluated") for d in decisions):
        S.reset_misses(svc.store, line_id)
    out = []
    for d in decisions:
        out.append((d, await apply(svc, d)))
    return out


async def poll(svc: AlertsService, profile: str, now: datetime | None = None) -> dict[str, Any]:
    """EventBridge `{profile}` (10 §3): every watched line of the profile, the subscription watchdog, and
    the escalation tick."""
    now = now if now is not None else await svc.clock.now()
    lines = sorted({w.line_id for w in list_watches_by_profile(svc.store, profile)})  # type: ignore[arg-type,unused-ignore]
    alerts = 0
    for line_id in lines:
        for _, res in await process_line(svc, line_id, "poll", now):
            alerts += bool(res and res.sent_codes)
        await watchdog(svc, line_id, now)
    # D7: a line whose Watches of this profile were all turned off (a revoke disables the grantee's Watch) is
    # not polled any more, but its carrier subscriptions must still go; the watchdog reconciles them.
    every = list_watches_by_profile(svc.store, profile, enabled_only=False)  # type: ignore[arg-type,unused-ignore]
    for line_id in sorted({w.line_id for w in every} - set(lines)):
        await watchdog(svc, line_id, now)
    escalated = await escalation.tick(svc, now)
    svc.count(f"poll_{profile}")
    return {"profile": profile, "lines": len(lines), "alerts": alerts, "escalated": len(escalated)}


# --- wiring --------------------------------------------------------------------------------------------------


def build_sender(settings: Settings) -> SmsSender:
    if settings.sender == "sns":
        import boto3

        return SnsSender(boto3.client("sns"))
    if settings.sender == "webhook" or settings.sms_gateway_url:
        if not settings.sms_gateway_url:
            raise ValueError("ALERTS_SENDER=webhook needs SMS_GATEWAY_URL")
        return WebhookSmsSender(settings.sms_gateway_url)
    return LogSender()


def build_clock(settings: Settings, env: Mapping[str, str]) -> Clock:
    if settings.clock == "mock":
        return MockCarrierClock(env.get("CARRIER_BASE_URL", "http://localhost:8443"))
    return SystemClock()


def build_service(env: Mapping[str, str] | None = None) -> AlertsService:
    """Everything from the environment (README "Config"). Creates `AlertsState` locally (Terraform on AWS)."""
    from camara_client import CarrierConfig, make_client

    e = carrier_env(os.environ if env is None else env)
    settings = Settings.from_env(e)
    store = Store.from_env(e)
    if settings.mode == "local" or (settings.mode == "k8s" and e.get("TOWER_DYNAMODB_ENDPOINT")):
        # DynamoDB Local (compose, kind); on AWS and EKS the tables come from Terraform
        store.ensure_tables()
        S.ensure_alerts_table(store)
    hasher, cipher = crypto_from_env(e)
    carrier = make_client(CarrierConfig.from_env(e), env=e)
    return AlertsService(
        store=store,
        hasher=hasher,
        cipher=cipher,
        carrier=carrier,
        sender=build_sender(settings),
        clock=build_clock(settings, e),
        settings=settings,
        sent_log=SentLog() if settings.mode == "local" else None,
    )
