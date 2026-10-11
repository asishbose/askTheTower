"""Release an alert: re-resolve → rate limit → audit → send (06 §3–§5, e2e §4 steps 5–7).

Order, and why:

1. **Re-resolve the grant** immediately before sending (06 §4). Gone → one `SUPPRESSED_REVOKED` row, nothing
   sent, nothing rate-limited.
2. **Rate limit** per (line, watcher, reason) per `RATE_LIMIT` (6 h): `last_state.last_alert_at` plus an
   atomic claim in `AlertsState`, so an event and a poll racing on the same change send at most once. A
   repeat is audited (`suppressed`, the reason code), not sent.
3. **Audit** the alert (`changed`, codes, `message_ref` = template id) — the row exists before any SMS.
4. **Recipients** (06 §3): the line-holder is told too, at `Users.alert_phone_enc` (default: the bound line)
   — except after a SIM swap of this line (the alert is a swap, or the line's own swap is within
   `ACK_DISTRUST`): then the line-holder's `backup_phone_enc` if registered, else nobody. No recipient whose
   phone *is* the watched line is ever texted in that state, whoever they are. Then the escalation chain
   (`escalation.py`), from step 0; on the line-holder's own chain each contact's grant is re-read and their
   SMS uses their own alias (06 §11.2). The line-holder's copy above uses the Watch's alias (none for them).
5. **Send** with one retry; a second failure → `ALERT_FAILED` row and the next recipient in the chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from tower_audit import Trigger
from tower_consent import Line, get_line, get_user, list_lines
from tower_consent import tables as T
from tower_policy import Facts, ReasonCode

from alerts import state as S
from alerts.context import AlertsService, audit
from alerts.evaluate import Decision, watch_consent
from alerts.send_backends import (
    LogSender,
    SendError,
    SentSms,
    SmsSender,
    SnsSender,
    WebhookSmsSender,
)
from alerts.templates import message_ref, render
from alerts.windows import rate_limited, swap_distrusted

__all__ = [
    "LogSender",
    "Recipient",
    "SendError",
    "SentSms",
    "SmsSender",
    "SnsSender",
    "WebhookSmsSender",
    "deliver",
    "line_swapped_state",
    "phone_for_user",
    "send_one",
]

log = logging.getLogger("alerts.send")

Role = Literal["chain", "line_holder", "backup"]


@dataclass(frozen=True)
class Recipient:
    user_id: str
    role: Role
    phone_line_id: str
    e164: str = field(repr=False)

    @property
    def label(self) -> str:
        return f"{self.role}:{self.user_id}"

    @property
    def audience(self) -> str:
        """The role a sent-SMS reader sees (06 §3.1): never the number."""
        return {"chain": "watcher", "line_holder": "line-holder", "backup": "line-holder backup"}[self.role]


def _decrypt(svc: AlertsService, enc: object) -> str | None:
    if not isinstance(enc, str) or not enc:
        return None
    plain: str = svc.cipher.decrypt(enc)
    return plain


def phone_for_user(svc: AlertsService, user_id: str, *, default_line: Line | None = None) -> str | None:
    """`Users.alert_phone_enc`, else the given line (the line-holder's default), else their newest line."""
    user = get_user(svc.store, user_id)
    phone = _decrypt(svc, user.alert_phone_enc) if user else None
    if phone:
        return phone
    if default_line is not None:
        own: str = svc.cipher.decrypt(default_line.msisdn_enc)
        return own
    lines = sorted(list_lines(svc.store, user_id), key=lambda ln: ln.bound_at, reverse=True)
    return svc.cipher.decrypt(lines[0].msisdn_enc) if lines else None


def recipient(svc: AlertsService, user_id: str, role: Role, e164: str | None) -> Recipient | None:
    if not e164:
        return None
    return Recipient(user_id=user_id, role=role, phone_line_id=svc.hasher.line_id(e164), e164=e164)


def line_swapped_state(
    svc: AlertsService, codes: tuple[ReasonCode, ...], facts: Facts | None, now: datetime
) -> bool:
    """True when the watched line's own number must not be texted: a swap alert, or a swap within 24 h."""
    if ReasonCode.SIM_SWAPPED_RECENT in codes:
        return True
    return (
        facts is not None
        and bool(facts.sim_swapped)
        and swap_distrusted(facts.latest_sim_change, now, svc.thresholds)
    )


def line_holder_recipient(svc: AlertsService, line: Line, swapped: bool) -> Recipient | None:
    owner = line.owner_user_id
    if swapped:
        item = svc.store.get(T.USERS, {"user_id": owner}) or {}
        r = recipient(svc, owner, "backup", _decrypt(svc, item.get("backup_phone_enc")))
    else:
        r = recipient(svc, owner, "line_holder", phone_for_user(svc, owner, default_line=line))
    if r is not None and swapped and r.phone_line_id == line.line_id:
        log.info("recipient skipped: backup phone is the swapped line (line=%s)", line.line_id[:12])
        return None
    return r


async def send_one(
    svc: AlertsService,
    r: Recipient,
    body: str,
    *,
    line_id: str,
    now: datetime,
    trigger: Trigger,
    ref: str,
    audience: str | None = None,
) -> bool:
    """One SMS with one retry (06 §8). False → `ALERT_FAILED` has been audited. A delivered SMS goes into the
    local sent-SMS ledger when there is one, under `audience` (default: the recipient's role)."""
    for attempt in (1, 2):
        try:
            await svc.sender.send(r.e164, body, label=r.label)
            svc.count("sms_sent")
            if svc.sent_log is not None:
                svc.sent_log.record(
                    at=now, template=ref, role=audience or r.audience, user_id=r.user_id, body=body
                )
            return True
        except SendError:
            log.warning("send failed to=%s attempt=%d", r.label, attempt)
    svc.count("sms_failed")
    audit(
        svc,
        line_id,
        now,
        trigger=trigger,
        outcome="suppressed",
        codes=[ReasonCode.ALERT_FAILED],
        message_ref=ref,
    )
    return False


@dataclass
class DeliveryResult:
    sent_codes: tuple[ReasonCode, ...] = ()
    suppressed: tuple[ReasonCode, ...] = ()
    revoked: bool = False
    labels: list[str] = field(default_factory=list)


async def deliver(svc: AlertsService, d: Decision) -> DeliveryResult:
    """Release the alert in `d` (a Decision with `notify`). Returns what happened; audits every branch."""
    from alerts.escalation import ChainAlert, start_chain

    assert d.notify and d.facts is not None
    now = d.now
    consent = watch_consent(svc, d.line_id, d.watcher_user_id)
    if consent.refused:
        audit(
            svc,
            d.line_id,
            now,
            trigger=d.trigger,
            outcome="suppressed",
            codes=[ReasonCode.SUPPRESSED_REVOKED],
        )
        svc.count("suppressed_revoked")
        return DeliveryResult(revoked=True)

    to_send: list[ReasonCode] = []
    limited: list[ReasonCode] = []
    for code in d.codes:
        if rate_limited(d.prev, code, now, svc.thresholds) or not S.claim_alert(
            svc.store, d.line_id, d.watcher_user_id, code.value, now, svc.thresholds.RATE_LIMIT
        ):
            limited.append(code)
        else:
            to_send.append(code)
    for code in limited:
        audit(svc, d.line_id, now, trigger=d.trigger, outcome="suppressed", codes=[code])
        svc.count("rate_limited")
    if not to_send:
        return DeliveryResult(suppressed=tuple(limited))

    codes = tuple(to_send)
    ref = message_ref(codes)
    audit(svc, d.line_id, now, trigger=d.trigger, outcome="changed", codes=codes, message_ref=ref)
    body = render(codes, d.facts, alias=consent.alias, tz=d.tz, now=now)
    result = DeliveryResult(sent_codes=codes, suppressed=tuple(limited))

    line = get_line(svc.store, d.line_id)
    swapped = line_swapped_state(svc, codes, d.facts, now)
    steps = d.escalation_steps or ((d.watcher_user_id, False),)
    if line is not None and line.owner_user_id not in {u for u, _ in steps}:
        holder = line_holder_recipient(svc, line, swapped)
        if holder is not None and await send_one(
            svc, holder, body, line_id=d.line_id, now=now, trigger=d.trigger, ref=ref
        ):
            result.labels.append(holder.label)
    alert = ChainAlert(
        line_id=d.line_id,
        watcher_user_id=d.watcher_user_id,
        codes=codes,
        facts=d.facts,
        alias=consent.alias,
        tz=d.tz,
        trigger=d.trigger,
        swapped=swapped,
        owner_chain=consent.view.grant == "owner",
    )
    result.labels += await start_chain(svc, alert, steps, now)
    return result
