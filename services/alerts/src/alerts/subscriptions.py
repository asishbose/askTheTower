"""CAMARA subscriptions per watched line (06 §1, §6, §8).

- One unguessable sink token per line (`AlertsState`), one sink URL per subscription *kind*:
  `{HOOKS_BASE_URL}/hooks/sim-swap/{token}` and `{HOOKS_BASE_URL}/hooks/reachability/{token}`. The token is
  also sent as the CAMARA `sinkCredential` access token, so the carrier echoes it as a bearer.
- Kinds: `sim-swap` for every profile; `care`/`transplant` add the three reachability event types (the Fall25
  API takes one type per subscription): `reachability-disconnected` starts the clock, `-data`/`-sms` stop it.
- Subscriptions are per line; Watches are per (line, watcher). The set is the union over the line's enabled
  Watches; the ids are mirrored onto each Watch's `subscription_ids` (e2e §6 shape).
- Only a Watch that is enabled *and* still consented (owner, or an active `watch` grant — the same re-read as
  06 §4) needs a subscription. A revoked grantee's Watch is disabled by `tower_consent.revoke`; if that write
  was missed, this check still drops it (D7).
- Watchdog (06 §8): no event for more than TTL/2 since (re)subscribing → re-subscribe and audit. It also
  reconciles: no Watch needs the line any more → unsubscribe (never re-subscribe after a revoke); the kinds
  needed changed → re-subscribe with the new set.
"""

from __future__ import annotations

import logging
from datetime import datetime

from camara_client import CarrierError, LineRef, SubscriptionKind
from tower_consent import get_line, list_watches_for_line
from tower_consent import tables as T
from tower_policy import ReasonCode

from alerts import state as S
from alerts.context import AlertsService, audit
from alerts.evaluate import watch_consent

log = logging.getLogger("alerts.subscriptions")

REACH_KINDS: tuple[SubscriptionKind, ...] = (
    "reachability-disconnected",
    "reachability-data",
    "reachability-sms",
)
KINDS: dict[str, tuple[SubscriptionKind, ...]] = {
    "self": ("sim-swap",),
    "care": ("sim-swap", *REACH_KINDS),
    "transplant": ("sim-swap", *REACH_KINDS),
}


def hook_kind(kind: SubscriptionKind) -> str:
    return "sim-swap" if kind == "sim-swap" else "reachability"


def sink_url(svc: AlertsService, kind: SubscriptionKind, token: str) -> str:
    return f"{svc.settings.hooks_base_url}/hooks/{hook_kind(kind)}/{token}"


def kinds_for_line(svc: AlertsService, line_id: str) -> tuple[SubscriptionKind, ...]:
    """The union of what the line's enabled, consented Watches need (a revoked grantee's Watch needs nothing)."""
    wanted: list[SubscriptionKind] = []
    for w in list_watches_for_line(svc.store, line_id):
        if w.enabled and not watch_consent(svc, line_id, w.watcher_user_id).refused:
            wanted += [k for k in KINDS[w.profile] if k not in wanted]
    return tuple(wanted)


def _mirror_ids(svc: AlertsService, line_id: str, ids: list[str]) -> None:
    for w in list_watches_for_line(svc.store, line_id):
        svc.store.update(
            T.WATCHES,
            {"line_id": line_id, "watcher_user_id": w.watcher_user_id},
            "SET subscription_ids = :s",
            values={":s": ids},
        )


async def unsubscribe(svc: AlertsService, line_id: str, *, drop_token: bool = True) -> int:
    """Delete the line's subscriptions (404/410 count as done in camara_client). Returns how many."""
    row = S.get_line_row(svc.store, line_id)
    if row is None:
        return 0
    n = 0
    for sid in row.subscription_ids:
        try:
            await svc.carrier.unsubscribe(sid)
            n += 1
        except CarrierError as e:
            log.warning("unsubscribe failed line=%s reason=%s", line_id[:12], e.reason_code)
    S.set_subscriptions(svc.store, line_id, [], None)
    _mirror_ids(svc, line_id, [])
    if drop_token:
        S.drop_sink(svc.store, line_id)
    return n


async def subscribe(
    svc: AlertsService, line_id: str, kinds: tuple[SubscriptionKind, ...] | None, now: datetime
) -> list[str]:
    """(Re)create the line's subscriptions for `kinds` (default: what its enabled Watches need)."""
    kinds = kinds if kinds is not None else kinds_for_line(svc, line_id)
    line = get_line(svc.store, line_id)
    if line is None or not kinds:
        return []
    row = S.ensure_sink_token(svc.store, line_id)
    assert row.sink_token
    if row.subscription_ids:
        await unsubscribe(svc, line_id, drop_token=False)
    ref = LineRef(line_id, svc.cipher.decrypt(line.msisdn_enc))
    ids: list[str] = []
    for kind in kinds:
        try:
            ids.append(
                await svc.carrier.subscribe(
                    kind,
                    ref,
                    sink_url(svc, kind, row.sink_token),
                    svc.settings.subscription_ttl,
                    now=now,
                    sink_token=row.sink_token,
                )
            )
        except CarrierError as e:  # polls remain the fallback (06 §1)
            log.warning("subscribe failed line=%s kind=%s reason=%s", line_id[:12], kind, e.reason_code)
            svc.count("subscribe_failed")
    S.set_subscriptions(svc.store, line_id, ids, now, kinds=list(kinds))
    _mirror_ids(svc, line_id, ids)
    log.info("subscribed line=%s kinds=%s ok=%d", line_id[:12], ",".join(kinds), len(ids))
    return ids


async def watchdog(svc: AlertsService, line_id: str, now: datetime) -> bool:
    """Re-subscribe when nothing was heard for more than TTL/2 since (re)subscribing. Audited.

    Reconciles first (D7): if no enabled, consented Watch needs the line, its subscriptions are deleted and
    nothing is re-subscribed; if the needed kinds changed (one Watch of several was revoked), re-subscribe
    now with only what the remaining Watches need. Returns True iff it (re)subscribed.
    """
    wanted = kinds_for_line(svc, line_id)
    row = S.get_line_row(svc.store, line_id)
    if not wanted:
        if row is not None and row.subscription_ids:
            n = await unsubscribe(svc, line_id)
            log.info("unsubscribed line=%s (no consented watch needs it) n=%d", line_id[:12], n)
            svc.count("unsubscribed_unneeded")
        return False
    last = None
    if row is not None:
        heard = [t for t in (row.subs_at, row.last_event_at) if t is not None]
        last = max(heard) if heard else None
    changed = row is not None and row.kinds is not None and set(row.kinds) != set(wanted)
    if not changed and last is not None and now - last <= svc.settings.subscription_ttl / 2:
        return False
    await subscribe(svc, line_id, wanted, now)
    audit(svc, line_id, now, trigger="poll", outcome="ok", codes=[ReasonCode.OK])
    svc.count("resubscribed")
    return True
