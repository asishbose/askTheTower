"""Escalation down `watch.escalation` and acknowledgements (06 §3).

- `start_chain` walks the steps in order, moving on while a step fails delivery (`ALERT_FAILED` → next), is
  skipped (its phone is the swapped line and no backup exists) or — on the line-holder's own chain — has no
  active `watch` grant any more (`SUPPRESSED_REVOKED`, next step at once; 06 §11.2). Each contact on the
  line-holder's chain is texted under their own grant alias. A step with `requires_ack` that was delivered
  parks the chain in `AlertsState` (`esc#…`) with `sent_at` and the `remaining` steps; a step without it
  ends the chain.
- `tick` (every poll, and the local scheduler's minute tick) moves a parked chain on once `ESCALATE_NEXT`
  (15 min) has passed without an acknowledgement — re-resolving the grant first, walking the parked snapshot
  (06 §11.3), auditing the release before the first send.
- `handle_reply` takes an SMS reply ("OK" or "CANCEL", from the SNS inbound topic or the local gateway). The
  replying number is hashed to a line id at once; the number itself goes no further than a SIM-swap check.
  A reply from a line within `ACK_DISTRUST` (24 h) of its own SIM swap is **ignored**, audited
  `ACK_IGNORED_SWAPPED_LINE`, the chain continues, and the watcher's next message says so
  (`templates.ACK_IGNORED_NOTE`) — or, when the watcher is the line-holder (whose number is that line), the
  next chain step's message does. A carrier error on that check counts as "swapped" (fail closed), except
  `NOT_BOUND` (the number is not this carrier's), which cannot be caused by a swap here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from camara_client import CarrierError, LineRef
from tower_audit import Trigger
from tower_consent import EscalationStep, Watch, get_line, get_watch, list_grants, list_watches_for_line
from tower_consent import tables as T
from tower_policy import Facts, ReasonCode

from alerts import state as S
from alerts.context import AlertsService, audit
from alerts.evaluate import watch_consent
from alerts.send import Recipient, line_swapped_state, phone_for_user, recipient, send_one
from alerts.templates import ACK_IGNORED_NOTE, message_ref, render
from alerts.windows import swap_distrusted

log = logging.getLogger("alerts.escalation")

ACK_WORDS = frozenset({"OK", "CANCEL"})


def chain_recipient(svc: AlertsService, user_id: str, line_id: str, swapped: bool) -> Recipient | None:
    """A chain member's phone; if it *is* the swapped line, their backup phone instead, else skip them."""
    line = get_line(svc.store, line_id)
    default = line if line is not None and line.owner_user_id == user_id else None
    r = recipient(svc, user_id, "chain", phone_for_user(svc, user_id, default_line=default))
    if r is None or not (swapped and r.phone_line_id == line_id):
        return r
    item = svc.store.get(T.USERS, {"user_id": user_id}) or {}
    enc = item.get("backup_phone_enc")
    backup = recipient(svc, user_id, "backup", svc.cipher.decrypt(enc) if isinstance(enc, str) else None)
    if backup is not None and backup.phone_line_id != line_id:
        return backup
    log.info("chain step skipped: %s's phone is the swapped line", user_id)
    svc.count("swapped_line_skipped")
    return None


@dataclass(frozen=True)
class ChainAlert:
    """One alert walking a Watch's chain: what it says, about which line, for which Watch.

    `owner_chain` marks a Watch whose watcher is the line-holder (06 §11.2): every other step is a contact who
    must still hold an active `watch` grant, and each contact's SMS names the line by that contact's own alias.
    """

    line_id: str
    watcher_user_id: str
    codes: tuple[ReasonCode, ...]
    facts: Facts
    alias: str | None
    tz: str
    trigger: Trigger
    swapped: bool
    owner_chain: bool = False

    def body(self, alias: str | None, now: datetime, note: str | None) -> str:
        text = render(self.codes, self.facts, alias=alias, tz=self.tz, now=now)
        return f"{text} {note}" if note else text


def step_consent(svc: AlertsService, alert: ChainAlert, user_id: str) -> tuple[bool, str | None]:
    """(may this step be texted, the alias its SMS uses). 06 §11.2 rules 1–2, line-holder's chain only.

    The watcher's own step was re-resolved already (06 §4). Any other step on the line-holder's chain is
    re-read now — one Grants query, Alerts only — and texted under that contact's own grant alias.
    """
    if not alert.owner_chain or user_id == alert.watcher_user_id:
        return True, alert.alias
    for g in list_grants(svc.store, alert.line_id, include_revoked=False):
        if g.grantee_user_id == user_id and g.grant == "watch":
            return True, g.alias
    return False, None


def step_audience(alert: ChainAlert, user_id: str, step: int, r: Recipient) -> str:
    """`watcher` for a grantee's own Watch, `escalation[n]` on the line-holder's chain; `+ backup` when the
    step's backup phone was used (06 §3.1)."""
    base = "watcher" if not alert.owner_chain and user_id == alert.watcher_user_id else f"escalation[{step}]"
    return f"{base} backup" if r.role == "backup" else base


async def start_chain(
    svc: AlertsService,
    alert: ChainAlert,
    steps: Sequence[tuple[str, bool]],
    now: datetime,
    *,
    first_step: int = 0,
    note: str | None = None,
    release_audit: bool = False,
) -> list[str]:
    """Walk `steps` in order; returns the labels texted. Parks (with the `remaining` snapshot) or clears.

    A step without consent is audited `SUPPRESSED_REVOKED` and the next one is tried at once (06 §11.2.1).
    `release_audit` writes the `changed` row just before the first send (tick's release; `deliver` has
    already written it). `note` is appended to the first message delivered. `first_step` is the absolute
    index of `steps[0]` in the Watch's chain, kept on the parked row for the record.
    """
    labels: list[str] = []
    for i, (user_id, requires_ack) in enumerate(steps):
        allowed, alias = step_consent(svc, alert, user_id)
        if not allowed:
            audit(
                svc,
                alert.line_id,
                now,
                trigger=alert.trigger,
                outcome="suppressed",
                codes=[ReasonCode.SUPPRESSED_REVOKED],
            )
            svc.count("suppressed_revoked")
            continue
        r = chain_recipient(svc, user_id, alert.line_id, alert.swapped)
        if r is None:
            continue
        ref = message_ref(alert.codes)
        if release_audit:
            audit(
                svc,
                alert.line_id,
                now,
                trigger=alert.trigger,
                outcome="changed",
                codes=alert.codes,
                message_ref=ref,
            )
            release_audit = False
        body = alert.body(alias, now, note)
        audience = step_audience(alert, user_id, first_step + i, r)
        if not await send_one(
            svc, r, body, line_id=alert.line_id, now=now, trigger=alert.trigger, ref=ref, audience=audience
        ):
            continue
        labels.append(r.label)
        note = None
        if requires_ack:
            park(svc, alert, now, step=first_step + i, rest=steps[i + 1 :], phone_line_id=r.phone_line_id)
            return labels
        break
    S.clear_escalation(svc.store, alert.line_id, alert.watcher_user_id)
    return labels


def park(
    svc: AlertsService,
    alert: ChainAlert,
    now: datetime,
    *,
    step: int,
    rest: Sequence[tuple[str, bool]],
    phone_line_id: str,
) -> None:
    S.save_escalation(
        svc.store,
        S.Escalation(
            line_id=alert.line_id,
            watcher_user_id=alert.watcher_user_id,
            codes=list(alert.codes),
            facts=alert.facts,
            alias=alert.alias,
            tz=alert.tz,
            step=step,
            sent_at=now,
            remaining=[EscalationStep(user_id=u, requires_ack=a) for u, a in rest],
        ),
    )
    S.remember_ack_route(svc.store, phone_line_id, alert.line_id, alert.watcher_user_id, now)


def steps_after(esc: S.Escalation, watch: Watch) -> tuple[list[tuple[str, bool]], int]:
    """The steps a tick walks: the parked snapshot, or (rows parked before it existed) the live chain."""
    if esc.remaining is not None:
        return [(s.user_id, s.requires_ack) for s in esc.remaining], esc.step + 1
    rest = watch.escalation[esc.step + 1 :]
    return [(s.user_id, s.requires_ack) for s in rest], esc.step + 1


async def tick(svc: AlertsService, now: datetime) -> list[str]:
    """Move every parked chain whose ack window has passed. Returns the labels texted."""
    texted: list[str] = []
    for esc in S.list_escalations(svc.store):
        if esc.acked:
            S.clear_escalation(svc.store, esc.line_id, esc.watcher_user_id)
            continue
        if now - esc.sent_at < svc.thresholds.ESCALATE_NEXT:
            continue
        watch = get_watch(svc.store, esc.line_id, esc.watcher_user_id)
        if watch is None or not watch.enabled:
            S.clear_escalation(svc.store, esc.line_id, esc.watcher_user_id)
            continue
        consent = watch_consent(svc, esc.line_id, esc.watcher_user_id)
        if consent.refused:
            audit(
                svc,
                esc.line_id,
                now,
                trigger="poll",
                outcome="suppressed",
                codes=[ReasonCode.SUPPRESSED_REVOKED],
            )
            S.clear_escalation(svc.store, esc.line_id, esc.watcher_user_id)
            continue
        codes = tuple(esc.codes)
        alert = ChainAlert(
            line_id=esc.line_id,
            watcher_user_id=esc.watcher_user_id,
            codes=codes,
            facts=esc.facts,
            alias=esc.alias,
            tz=esc.tz,
            trigger="poll",
            swapped=line_swapped_state(svc, codes, esc.facts, now),
            owner_chain=consent.view.grant == "owner",
        )
        steps, first = steps_after(esc, watch)
        note: str | None = None
        release_audit = True
        if esc.ack_ignored and not esc.note_sent:
            if alert.owner_chain:
                # The watcher's number is the swapped line: the note rides on the next step (06 §11.3).
                note = ACK_IGNORED_NOTE
            else:
                texted += await note_to_watcher(svc, alert, now)
                release_audit = False
        log.info("escalate line=%s from step %d", esc.line_id[:12], esc.step)
        svc.count("escalations")
        texted += await start_chain(
            svc, alert, steps, now, first_step=first, note=note, release_audit=release_audit
        )
    return texted


async def note_to_watcher(svc: AlertsService, alert: ChainAlert, now: datetime) -> list[str]:
    """A grantee's Watch: the watcher is told the reply was ignored (06 §3). Audits the release first."""
    ref = message_ref(alert.codes)
    audit(
        svc, alert.line_id, now, trigger=alert.trigger, outcome="changed", codes=alert.codes, message_ref=ref
    )
    w = chain_recipient(svc, alert.watcher_user_id, alert.line_id, alert.swapped)
    body = alert.body(alert.alias, now, ACK_IGNORED_NOTE)
    if w is not None and await send_one(
        svc, w, body, line_id=alert.line_id, now=now, trigger=alert.trigger, ref=ref
    ):
        return [w.label]
    return []


async def phone_recently_swapped(svc: AlertsService, phone_line_id: str, e164: str, now: datetime) -> bool:
    for w in list_watches_for_line(svc.store, phone_line_id):
        if w.last_state is not None and swap_distrusted(w.last_state.sim_change_at, now, svc.thresholds):
            return True
    hours = max(1, int(svc.thresholds.ACK_DISTRUST / timedelta(hours=1)))
    try:
        result = await svc.carrier.sim_swap_check(LineRef(phone_line_id, e164), hours)
    except CarrierError as e:
        fail_closed: bool = e.reason_code != ReasonCode.NOT_BOUND
        return fail_closed
    swapped: bool = result.swapped
    return swapped


async def handle_reply(svc: AlertsService, from_e164: str, text: str, now: datetime) -> list[str]:
    """An inbound SMS. Returns one of "accepted" / "ignored" per escalation it touched."""
    word = text.strip().upper().rstrip(".!")
    if word not in ACK_WORDS:
        svc.count("reply_not_ack")
        return []
    phone_line_id = svc.hasher.line_id(from_e164)
    targets: list[tuple[str, str]] = []
    route = S.ack_route(svc.store, phone_line_id)
    if route is not None:
        targets.append(route)
    for w in list_watches_for_line(svc.store, phone_line_id):  # a reply from the watched line itself
        if (phone_line_id, w.watcher_user_id) not in targets:
            targets.append((phone_line_id, w.watcher_user_id))
    pending = [
        e
        for e in (S.get_escalation(svc.store, line, watcher) for line, watcher in targets)
        if e is not None and not e.acked
    ]
    if not pending:
        svc.count("reply_unmatched")
        return []
    distrusted = await phone_recently_swapped(svc, phone_line_id, from_e164, now)
    results: list[str] = []
    for esc in pending:
        if distrusted:
            audit(
                svc,
                esc.line_id,
                now,
                trigger="event",
                outcome="suppressed",
                codes=[ReasonCode.ACK_IGNORED_SWAPPED_LINE],
            )
            S.save_escalation(svc.store, esc.model_copy(update={"ack_ignored": True}))
            svc.count("ack_ignored_swapped_line")
            results.append("ignored")
        else:
            audit(svc, esc.line_id, now, trigger="event", outcome="ok", codes=[ReasonCode.OK])
            S.clear_escalation(svc.store, esc.line_id, esc.watcher_user_id)
            svc.count("ack_accepted")
            results.append("accepted")
    return results
