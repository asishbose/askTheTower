"""`evaluate(line_id, trigger, now)` — the one function both triggers converge on (06 §1).

For each enabled Watch on the line:

1. **Consent first** (03 §3 order): the watcher's grant is re-read (`watch_consent`). A revoked or missing
   grant stops here — no carrier call is made for a watcher who may no longer see the line.
2. **Facts:** one fetch per line (proactive profile: 5 s, one retry), the profile's full fact set —
   `line` (SIM swap check + date, call forwarding) and/or `reach` (Device Reachability Status). Any carrier
   failure makes the whole observation a **miss**: `last_state` is kept, the miss is counted (06 §8).
3. **Policy:** `tower_policy.evaluate_line` / `evaluate_reachability` — the same engine Tower uses.
4. **Diff vs last_state** (06 §2) and **windows** (`windows.py`) → `Decision{notify, codes, new_state}`.

The first observation of a watch is a baseline: it records state and never alerts on the line facts
(a swap from last month is not news). Nothing here sends, audits or writes; `runner.py` does that.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from camara_client import CarrierError, CFResult, LineRef, ReachResult, SimSwapResult
from tower_audit import Trigger
from tower_consent import LastState, Watch, get_line, list_grants, list_watches_for_line
from tower_policy import (
    ConsentView,
    Facts,
    Outcome,
    ReasonCode,
    Thresholds,
    evaluate_line,
    evaluate_reachability,
)

from alerts.context import AlertsService
from alerts.windows import next_unreachable_since, unreachable_due

log = logging.getLogger("alerts.evaluate")

Group = Literal["line", "reach"]
GROUPS: dict[str, tuple[Group, ...]] = {
    "self": ("line",),
    "care": ("line", "reach"),
    "transplant": ("line", "reach"),
}
"""Fact groups per profile. Every evaluation fetches the profile's full set so `last_state.at` is honest
for Tower's stored-state path (02 §4). `transplant` includes `line` because watch_line registers a SIM Swap
subscription for every profile (06 §1)."""

DecisionKind = Literal["evaluated", "baseline", "refused", "miss"]


@dataclass(frozen=True)
class WatchConsent:
    view: ConsentView
    alias: str | None = None
    owner_user_id: str | None = None

    @property
    def refused(self) -> bool:
        return not self.view.bound or self.view.grant == "none" or self.view.revoked_at is not None

    @property
    def code(self) -> ReasonCode:
        return ReasonCode.NOT_BOUND if not self.view.bound else ReasonCode.NO_CONSENT


def watch_consent(svc: AlertsService, line_id: str, watcher_user_id: str) -> WatchConsent:
    """Re-read the grant behind a Watch (06 §4). Owner → `owner`; an active `watch` grant → `watch`.

    A `reachability`-only grant does not cover alerts (04 §3 table: only `watch` carries "SMS when the line
    changes"), so it resolves like no grant: `NO_CONSENT`. Two reads (Lines GetItem, Grants Query); no cache.
    """
    line = get_line(svc.store, line_id)
    if line is None:
        return WatchConsent(ConsentView(bound=False, grant="none", line_id=line_id))
    if line.owner_user_id == watcher_user_id:
        return WatchConsent(ConsentView(bound=True, grant="owner", line_id=line_id), None, line.owner_user_id)
    mine = [g for g in list_grants(svc.store, line_id) if g.grantee_user_id == watcher_user_id]
    active = [g for g in mine if g.grant == "watch" and g.active]
    if active:
        return WatchConsent(
            ConsentView(bound=True, grant="watch", line_id=line_id), active[0].alias, line.owner_user_id
        )
    revoked = [g.revoked_at for g in mine if g.revoked_at is not None]
    alias = mine[0].alias if mine else None
    return WatchConsent(
        ConsentView(bound=True, grant="none", revoked_at=max(revoked) if revoked else None, line_id=line_id),
        alias,
        line.owner_user_id,
    )


@dataclass(frozen=True)
class Decision:
    line_id: str
    watcher_user_id: str
    profile: str
    trigger: Trigger
    now: datetime
    kind: DecisionKind
    notify: bool = False
    codes: tuple[ReasonCode, ...] = ()
    outcomes: tuple[Outcome, ...] = ()
    facts: Facts | None = None
    prev: LastState | None = None
    new_state: LastState | None = None  # None → keep last_state as it is
    consent: WatchConsent | None = None
    tz: str = "UTC"
    escalation_steps: tuple[tuple[str, bool], ...] = field(default=())

    def comparable(self) -> tuple[object, ...]:
        """Everything but the trigger: an event and a poll on the same facts must agree on this."""
        return (
            self.line_id,
            self.watcher_user_id,
            self.kind,
            self.notify,
            self.codes,
            self.outcomes,
            self.facts,
            self.new_state,
        )


def _connectivity(r: ReachResult) -> Literal["DATA", "SMS", "NONE", "UNKNOWN"]:
    if "DATA" in r.connectivity:
        return "DATA"
    if "SMS" in r.connectivity:
        return "SMS"
    return "NONE" if not r.reachable else "UNKNOWN"


async def fetch_facts(svc: AlertsService, line_id: str, groups: set[Group], now: datetime) -> Facts | None:
    """One observation of the line. `None` = the carrier did not fully answer (a miss, never a change)."""
    line = get_line(svc.store, line_id)
    if line is None:
        return None
    ref = LineRef(line_id, svc.cipher.decrypt(line.msisdn_enc))
    swap_h = int(svc.thresholds.SWAP_WINDOW / timedelta(hours=1))
    names: list[str] = []
    calls: list[Coroutine[Any, Any, object]] = []
    if "line" in groups:
        names += ["swap", "date", "cf"]
        calls += [
            svc.carrier.sim_swap_check(ref, swap_h),
            svc.carrier.sim_swap_date(ref),
            svc.carrier.call_forwarding(ref),
        ]
    if "reach" in groups:
        names.append("reach")
        calls.append(svc.carrier.reachability(ref))
    results = dict(zip(names, await asyncio.gather(*calls, return_exceptions=True), strict=True))
    failed = {k: v for k, v in results.items() if isinstance(v, BaseException)}
    if failed:
        for k, err in failed.items():
            reason = err.reason_code if isinstance(err, CarrierError) else type(err).__name__
            log.warning("carrier miss line=%s call=%s reason=%s", line_id[:12], k, reason)
        svc.count("carrier_miss")
        return None
    data: dict[str, object] = {"fetched_at": now}
    if "line" in groups:
        swap = results["swap"]
        cf = results["cf"]
        assert isinstance(swap, SimSwapResult) and isinstance(cf, CFResult)
        data.update(sim_swapped=swap.swapped, latest_sim_change=results["date"], call_forwarding=cf.status)
    if "reach" in groups:
        reach = results["reach"]
        assert isinstance(reach, ReachResult)
        data.update(
            reachable=reach.reachable,
            connectivity=_connectivity(reach),
            last_status_time=reach.last_status_time,
        )
    return Facts.model_validate(data)


def decide(
    *,
    watch: Watch,
    trigger: Trigger,
    now: datetime,
    facts: Facts,
    consent: WatchConsent,
    thresholds: Thresholds,
    tz: str,
) -> Decision:
    """Pure: policy outcome(s) + diff against `watch.last_state` + windows → Decision."""
    prev = watch.last_state
    groups = GROUPS[watch.profile]
    codes: list[ReasonCode] = []
    outcomes: list[Outcome] = []
    state = {
        "sim_change_at": prev.sim_change_at if prev else None,
        "cf_status": prev.cf_status if prev else None,
        "reachable": prev.reachable if prev else None,
        "unreachable_since": prev.unreachable_since if prev else None,
        "last_alert_at": dict(prev.last_alert_at) if prev else {},
        # `update_last_state` moves only forward (`at` strictly newer); an observation at the same instant as
        # the stored one (an event right after a poll on a frozen mock clock) is recorded 1 µs later.
        "at": now if prev is None or now > prev.at else prev.at + timedelta(microseconds=1),
    }
    if "line" in groups:
        out = evaluate_line(facts, consent.view, now, thresholds)
        outcomes.append(out)
        if prev is not None and ReasonCode.SIM_SWAPPED_RECENT in out.reason_codes:
            newer = (
                prev.sim_change_at is None
                if facts.latest_sim_change is None
                else prev.sim_change_at is None or facts.latest_sim_change > prev.sim_change_at
            )
            if newer:
                codes.append(ReasonCode.SIM_SWAPPED_RECENT)
        if (
            prev is not None
            and ReasonCode.CALL_FORWARDING_SET in out.reason_codes
            and prev.cf_status != "unconditional"
        ):
            codes.append(ReasonCode.CALL_FORWARDING_SET)
        if facts.latest_sim_change is not None:
            state["sim_change_at"] = facts.latest_sim_change
        state["cf_status"] = facts.call_forwarding
    if "reach" in groups and facts.reachable is not None:
        out = evaluate_reachability(facts, consent.view, now, thresholds)
        outcomes.append(out)
        since = next_unreachable_since(prev, facts.reachable, facts.last_status_time, now)
        state["reachable"] = facts.reachable
        state["unreachable_since"] = since
        last = prev.last_alert_at.get(ReasonCode.UNREACHABLE.value) if prev else None
        if ReasonCode.UNREACHABLE in out.reason_codes and unreachable_due(
            watch.profile, since, now, thresholds, tz, last
        ):
            codes.append(ReasonCode.UNREACHABLE)
    return Decision(
        line_id=watch.line_id,
        watcher_user_id=watch.watcher_user_id,
        profile=watch.profile,
        trigger=trigger,
        now=now,
        kind="baseline" if prev is None else "evaluated",
        notify=bool(codes),
        codes=tuple(codes),
        outcomes=tuple(outcomes),
        facts=facts,
        prev=prev,
        new_state=LastState.model_validate(state),
        consent=consent,
        tz=tz,
        escalation_steps=tuple((s.user_id, s.requires_ack) for s in watch.escalation),
    )


def line_tz(svc: AlertsService, owner_user_id: str | None) -> str:
    """The line-holder's IANA zone: a `tz` attribute on their Users row, else ALERTS_DEFAULT_TZ."""
    if owner_user_id:
        from tower_consent import tables as T

        item = svc.store.get(T.USERS, {"user_id": owner_user_id})
        if item and isinstance(item.get("tz"), str):
            return str(item["tz"])
    return svc.settings.default_tz


async def evaluate(
    svc: AlertsService,
    line_id: str,
    trigger: Trigger,
    now: datetime,
    *,
    watchers: set[str] | None = None,
) -> list[Decision]:
    """Every enabled Watch on the line → one Decision each. Reads only; writes nothing.

    On an **event**, a Watch that `revoke` turned off (disabled, grant revoked) is still considered so the
    carrier's message about a line we may no longer report on is audited `SUPPRESSED_REVOKED` (06 §4, D7).
    A Watch the watcher turned off themselves (grant still active) is skipped, as before.
    """
    watches = [
        w
        for w in list_watches_for_line(svc.store, line_id)
        if (w.enabled or trigger == "event") and (watchers is None or w.watcher_user_id in watchers)
    ]
    decisions: list[Decision] = []
    allowed: list[tuple[Watch, WatchConsent]] = []
    for w in sorted(watches, key=lambda w: w.watcher_user_id):
        c = watch_consent(svc, line_id, w.watcher_user_id)
        if not w.enabled and c.view.revoked_at is None:
            continue  # turned off, not revoked: nothing to evaluate or suppress
        if c.refused:
            decisions.append(
                Decision(
                    line_id=line_id,
                    watcher_user_id=w.watcher_user_id,
                    profile=w.profile,
                    trigger=trigger,
                    now=now,
                    kind="refused",
                    codes=(c.code,),
                    prev=w.last_state,
                    consent=c,
                )
            )
        else:
            allowed.append((w, c))
    if not allowed:
        return decisions
    groups: set[Group] = {g for w, _ in allowed for g in GROUPS[w.profile]}
    facts = await fetch_facts(svc, line_id, groups, now)
    for w, c in allowed:
        if facts is None:
            decisions.append(
                Decision(
                    line_id=line_id,
                    watcher_user_id=w.watcher_user_id,
                    profile=w.profile,
                    trigger=trigger,
                    now=now,
                    kind="miss",
                    codes=(ReasonCode.CARRIER_ERROR,),
                    prev=w.last_state,
                    consent=c,
                )
            )
            continue
        tz = line_tz(svc, c.owner_user_id)
        decisions.append(
            decide(
                watch=w, trigger=trigger, now=now, facts=facts, consent=c, thresholds=svc.thresholds, tz=tz
            )
        )
    return decisions
