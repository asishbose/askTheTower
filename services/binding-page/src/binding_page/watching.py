"""The line-holder's watch settings on the page (04 §9): save them, and build the Watching card.

`save` is the one path both `POST /me/lines/{line_id}/watch-settings` and the local `/_admin/watch-settings`
take: `tower_consent.set_watch_settings` (validates; every refusal writes nothing) → one audit row → if the
Watch is enabled, Alerts re-subscribes for the new profile. The write stands even if the audit or Alerts
fails: settings gate nothing on the hot path (04 §9.5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tower_audit import AuditRecord, AuditWriteFailed, append
from tower_consent import Watch, get_watch, list_grants, set_watch_settings
from tower_policy import ReasonCode, policy_version

from binding_page.deps import Deps

log = logging.getLogger("binding_page.watching")

# Radio values are behaviour keys, not profile names: the page source never carries the profile's internal
# name (04 §9.3). The form also accepts the profile names themselves (the admin route and scripts send those).
CHOICE_TO_PROFILE: Final[dict[str, str]] = {"fraud": "self", "reachable": "transplant", "daytime": "care"}
PROFILE_TO_CHOICE: Final[dict[str, str]] = {v: k for k, v in CHOICE_TO_PROFILE.items()}
CONTACT_SLOTS: Final = 3


@dataclass(frozen=True)
class Saved:
    watch: Watch
    audited: bool


def profile_from_form(value: str) -> str:
    return CHOICE_TO_PROFILE.get(value, value)


def save(deps: Deps, line_id: str, acting_user_id: str, profile: str, contacts: list[str]) -> Saved:
    """Validate and write, audit, tell Alerts. Raises the `tower_consent` refusals and store errors unchanged."""
    now = deps.now()
    watch = set_watch_settings(deps.store, line_id, acting_user_id, profile, contacts, now)
    log.info(
        "watch settings saved line_id=%s profile=%s steps=%d", line_id, watch.profile, len(watch.escalation)
    )
    audited = _audit(deps, line_id, acting_user_id, now)
    if watch.enabled:
        try:
            deps.alerts.watch(
                line_id=line_id, watcher_user_id=watch.watcher_user_id, enable=True, profile=watch.profile
            )
        except Exception as e:  # noqa: BLE001 - polls are the fallback (06 §1, 04 §9.5); the settings stand
            log.warning("alerts re-subscribe failed line_id=%s: %s", line_id, type(e).__name__)
    return Saved(watch=watch, audited=audited)


def _audit(deps: Deps, line_id: str, owner: str, now: datetime) -> bool:
    """One row, no contact ids (04 §9.2). Retried once; then counted as `audit_failed` (04 §9.5)."""
    record = AuditRecord(
        line_id=line_id,
        ts=now,
        actor_user_id=owner,
        tool="watch_line",
        trigger="binding",
        outcome="ok",
        reason_codes=(ReasonCode.OK,),
        policy_version=policy_version(),
    )
    for attempt in (1, 2):
        try:
            append(deps.store, record)
            return True
        except AuditWriteFailed:
            log.warning("audit append failed line_id=%s attempt=%d", line_id, attempt)
    deps.metrics["audit_failed"] += 1
    return False


@dataclass(frozen=True)
class Contact:
    user_id: str
    alias: str


@dataclass(frozen=True)
class WatchingCard:
    """What the Watching card shows for one owned line (04 §9.3). Never a number."""

    line_id: str
    enabled: bool
    choice: str
    grantees: list[Contact]
    selected: list[str]  # CONTACT_SLOTS entries; "" = nobody
    skipped: list[str]  # stored contacts without an active `watch` grant


def card(deps: Deps, line_id: str, owner: str) -> WatchingCard:
    watch = get_watch(deps.store, line_id, owner)
    grantees = [
        Contact(g.grantee_user_id, g.alias)
        for g in list_grants(deps.store, line_id, include_revoked=False)
        if g.grant == "watch"
    ]
    active = {c.user_id for c in grantees}
    stored = [s.user_id for s in watch.escalation if s.user_id != owner] if watch else []
    selected = [u for u in stored if u in active][:CONTACT_SLOTS]
    return WatchingCard(
        line_id=line_id,
        enabled=bool(watch and watch.enabled),
        choice=PROFILE_TO_CHOICE[watch.profile] if watch else PROFILE_TO_CHOICE["self"],
        grantees=grantees,
        selected=selected + [""] * (CONTACT_SLOTS - len(selected)),
        skipped=[u for u in stored if u not in active],
    )
