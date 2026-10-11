"""`watch_line(line, enable | null)` — 02 §2. No carrier *check* is ever made here.

- `enable` true/false: upsert the Watch (04 store), then ask Alerts to (un)subscribe the carrier events
  (`AlertsClient`, an HTTP call to Alerts' internal endpoint — prompt 10). An Alerts failure is logged and
  does not undo the Watch: Alerts' scheduled polls read Watches by profile (06 §1), so the line is still
  watched, just without the seconds-fast subscription until Alerts reconciles. The caller's own Watch keeps
  its stored profile and contacts (the line-holder saves them on the binding page, 04 §9); with no Watch the
  defaults are `self` for one's own line and `care` for an alias, chain `[caller]` (06 §11.1).
- `enable` null: status — `watching`, `notify_via`, `profile` (stored, also while off), and for the line-holder `grants` and `recent_checks`
  (this is how "who can see my line?" / "who checked my line this week?" are answered, 04 §6, 07 §4).
  A grantee asking about a line they watch gets `watching` only: a watcher never reads who else checked.

Either way the call is audited (`tool=watch_line`, which `recent_checks` does not count as a check).

Summaries: `tower_policy` has no template for a watch state change or status (03 §4 lists the twelve reason
codes only), so the non-refusal sentences below are fixed strings with **no interpolation** — nothing
from the facts is formatted into them. Refusals are phrased by `tower_policy.phrase` like every other tool.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

import anyio
from tower_audit import RecentChecks, recent_checks
from tower_consent import EscalationStep, Watch, get_watch, list_grants, upsert_watch
from tower_consent.models import Profile
from tower_policy import Facts, Outcome, ReasonCode

from tower_mcp.deps import Deps
from tower_mcp.errors import ConsentUnavailable
from tower_mcp.next_step import build_next_step
from tower_mcp.schemas import GrantFact, NextStep, RecentChecksFact, ToolResult, WatchFacts
from tower_mcp.tools.common import (
    SELF,
    STORE_ERRORS,
    Timer,
    audit_record,
    audited,
    consent_gate,
    line_label,
    normalise_line,
    resolve_consent,
    scope_view,
    voice,
)

logger = logging.getLogger("tower_mcp.tools")

TOOL = "watch_line"
RECENT: Final = timedelta(days=7)  # "who checked my line this week?"
OK_OUTCOME: Final = Outcome(kind="ok", reason_codes=[ReasonCode.OK])

WATCH_SUMMARIES: Final[dict[str, str]] = {
    "enabled": "Alerts are on for that line. You'll get a text if it's SIM-swapped or forwarded.",
    # `transplant` / `care` also watch reachability (06 §11.1); still fixed, no interpolation, no digits.
    "enabled_reach": (
        "Alerts are on for that line. A text goes out if it's SIM-swapped, forwarded, or off the network too long."
    ),
    "disabled": "Alerts are off for that line.",
    "status_on": "Alerts are on for that line.",
    "status_off": "Alerts are off for that line.",
}


REACH_PROFILES: Final[frozenset[Profile]] = frozenset({"transplant", "care"})


def default_profile(line: str) -> Profile:
    return "self" if line == SELF else "care"


def enabled_summary(profile: Profile) -> str:
    return WATCH_SUMMARIES["enabled_reach" if profile in REACH_PROFILES else "enabled"]


def recent_fact(rc: RecentChecks, viewer: str) -> RecentChecksFact:
    by_actor = {("self" if actor == viewer else actor): n for actor, n in rc.by_actor.items()}
    return RecentChecksFact(by_actor=by_actor, outcomes=dict(rc.outcomes), last_at=rc.last_at)


async def watch_line(
    deps: Deps, user_id: str, line: str | None = "self", enable: bool | None = None
) -> ToolResult:
    timer = Timer(TOOL)
    now = await deps.clock.now()
    line = normalise_line(line)
    label = line_label(line)

    resolved = await resolve_consent(deps, user_id, line)
    view = scope_view(resolved.view, TOOL)
    timer.mark("resolve")

    refused = consent_gate(view, now)
    if refused is not None:
        next_step = await build_next_step(deps, user_id, refused.reason_codes, now)

        def refuse(_row: object = None) -> ToolResult:
            return ToolResult(
                summary=voice(deps, refused, Facts(fetched_at=now), label, now),
                facts=WatchFacts(line=label),
                reason_codes=list(refused.reason_codes),
                next_step=next_step,
                checked_at=now,
            )

        if resolved.line_id is None:
            timer.log(None)
            return refuse()
        rec = audit_record(
            deps, line_id=resolved.line_id, user_id=user_id, tool=TOOL, outcome=refused, now=now, source=None
        )
        return await audited(deps, rec, refuse)

    line_id = resolved.line_id
    assert line_id is not None  # noqa: S101
    owner = view.grant == "owner"

    def change() -> Watch:
        # Only the caller's own row is read: a grantee's Watch never reads the line-holder's settings, and
        # the line-holder's saved profile and contacts are kept exactly as stored (06 §11.1).
        existing = get_watch(deps.store, line_id, user_id)
        profile: Profile = existing.profile if existing else default_profile(line)
        escalation = existing.escalation if existing else [EscalationStep(user_id=user_id)]
        return upsert_watch(
            deps.store,
            Watch(
                line_id=line_id,
                watcher_user_id=user_id,
                profile=profile,
                enabled=bool(enable),
                escalation=escalation,
                subscription_ids=existing.subscription_ids if existing else [],
            ),
        )

    def status() -> tuple[Watch | None, list[GrantFact], RecentChecksFact | None]:
        watch = get_watch(deps.store, line_id, user_id)
        if not owner:
            return watch, [], None
        grants = [
            GrantFact(alias=g.alias, grant=g.grant)
            for g in list_grants(deps.store, line_id, include_revoked=False)
        ]
        rc = recent_checks(deps.store, line_id, now - RECENT, viewer_user_id=user_id)
        return watch, grants, recent_fact(rc, user_id)

    try:
        if enable is None:
            watch, grants, checks = await anyio.to_thread.run_sync(status)
            watching = bool(watch and watch.enabled)
            summary = WATCH_SUMMARIES["status_on" if watching else "status_off"]
        else:
            watch = await anyio.to_thread.run_sync(change)
            grants, checks, watching = [], None, watch.enabled
            summary = enabled_summary(watch.profile) if watching else WATCH_SUMMARIES["disabled"]
    except STORE_ERRORS:
        raise ConsentUnavailable("consent store unavailable") from None
    timer.mark("store")

    if enable is not None:
        try:
            await deps.alerts.watch(
                line_id=line_id, watcher_user_id=user_id, enable=bool(enable), profile=watch.profile
            )
        except Exception as e:  # noqa: BLE001 - polls are the fallback (06 §1); the Watch stands
            logger.warning("alerts (un)subscribe failed line_id=%s: %s", line_id, type(e).__name__)
        timer.mark("alerts")

    facts = WatchFacts(
        line=label,
        watching=watching,
        since=None,
        notify_via="sms" if watching else None,
        profile=watch.profile if watch else None,
        grants=grants,
        recent_checks=checks,
    )

    def respond(_row: object) -> ToolResult:
        return ToolResult(
            summary=summary,
            facts=facts,
            reason_codes=[ReasonCode.OK],
            next_step=NextStep(kind="none"),
            checked_at=now,
        )

    rec = audit_record(
        deps, line_id=line_id, user_id=user_id, tool=TOOL, outcome=OK_OUTCOME, now=now, source=None
    )
    out = await audited(deps, rec, respond)
    timer.mark("audit")
    timer.log(line_id)
    return out
