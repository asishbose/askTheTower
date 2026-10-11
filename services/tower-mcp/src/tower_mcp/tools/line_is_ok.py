"""`line_is_ok(line="self")` — exactly 02 §2 / e2e-wiring §3 (Path A).

1. identity → user_id (done by the server; 401 if absent)
2. consent.resolve → NOT_BOUND / NO_CONSENT short-circuit (no carrier call, nothing about the line revealed)
3. facts ← Watches.last_state if within FRESH, else SIM swap check and call forwarding in parallel (+ the swap
   date only when swapped),
   300 ms each (the carrier client's request profile); either call failing with no known alarm → CARRIER_ERROR,
   or, when it timed out and a Watch exists, last-known facts (STALE_DATA) — never OK on a half answer (D4)
4. outcome ← tower_policy.evaluate_line(facts, consent, now)
5. audit.append(...) — before returning; a failed append refuses the answer
6. ToolResult(summary=phrase(outcome), facts, reason_codes, next_step, checked_at)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import anyio
from camara_client import CarrierError, CFResult, LineRef, SimSwapResult
from tower_consent import LastState, Line, get_line, list_watches_for_line
from tower_policy import Facts, Outcome, ReasonCode, evaluate_line, within

from tower_mcp.deps import Deps
from tower_mcp.errors import ConsentUnavailable
from tower_mcp.next_step import build_next_step
from tower_mcp.schemas import LineFacts, ToolResult
from tower_mcp.tools.common import (
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

TOOL = "line_is_ok"


def freshest_line_state(states: list[LastState]) -> LastState | None:
    """The newest `last_state` that carries line facts (a reachability-only state has no `cf_status`)."""
    usable = [s for s in states if s.cf_status is not None]
    return max(usable, key=lambda s: s.at) if usable else None


def facts_from_state(state: LastState, swap_window: timedelta) -> Facts:
    """`Watches.last_state` → the same `Facts` a live check would give at `state.at`. `sim_swapped` means
    what the SIM Swap check means: a change within the window (the check's `maxAge`), as of `state.at`."""
    swapped = state.sim_change_at is not None and within(state.sim_change_at, state.at, swap_window)
    return Facts(
        fetched_at=state.at,
        sim_swapped=swapped,
        latest_sim_change=state.sim_change_at if swapped else None,
        call_forwarding=state.cf_status or "unknown",
    )


async def live_facts(deps: Deps, ref: LineRef, now: datetime) -> tuple[Facts, list[CarrierError]]:
    """SIM swap check + call forwarding in parallel (300 ms each, the client's request profile). Only when the
    check says "swapped" is the swap's date asked for (it is what `{time}` speaks; 03 §4) — one more call on
    the rare path, none on the common one."""
    max_age_h = max(1, int(deps.thresholds.SWAP_WINDOW.total_seconds() // 3600))
    results = await asyncio.gather(
        deps.carrier.sim_swap_check(ref, max_age_h=max_age_h),
        deps.carrier.call_forwarding(ref),
        return_exceptions=True,
    )
    errors: list[CarrierError] = []
    for r in results:
        if isinstance(r, CarrierError):
            errors.append(r)
        elif isinstance(r, BaseException):
            raise r
    check, cf = results
    swapped = check.swapped if isinstance(check, SimSwapResult) else None
    latest: datetime | None = None
    if swapped:
        try:
            latest = await deps.carrier.sim_swap_date(ref)
        except CarrierError as e:  # the swap stands; `{time}` falls back to when we observed it
            errors.append(e)
    forwarding = cf.status if isinstance(cf, CFResult) else "unknown"
    return Facts(
        fetched_at=now, sim_swapped=swapped, latest_sim_change=latest, call_forwarding=forwarding
    ), errors


def result_facts(label: str, outcome: Outcome, facts: Facts, source: str) -> LineFacts:
    codes = outcome.reason_codes
    if ReasonCode.CARRIER_ERROR in codes:
        return LineFacts(line=label, source=source)  # type: ignore[arg-type]
    recent = None if facts.sim_swapped is None else ReasonCode.SIM_SWAPPED_RECENT in codes
    if ReasonCode.STALE_DATA in codes and facts.sim_swapped is not None:
        recent = bool(facts.sim_swapped)
    return LineFacts(
        line=label,
        sim_swapped_recently=recent,
        swapped_at=facts.latest_sim_change if recent else None,
        call_forwarding=facts.call_forwarding,
        source=source,  # type: ignore[arg-type]
        stale=ReasonCode.STALE_DATA in codes,
        as_of=facts.fetched_at,
    )


async def line_is_ok(deps: Deps, user_id: str, line: str | None = "self") -> ToolResult:
    timer = Timer(TOOL)
    now = await deps.clock.now()
    line = normalise_line(line)
    label = line_label(line)

    resolved = await resolve_consent(deps, user_id, line)
    view = scope_view(resolved.view, TOOL)
    timer.mark("resolve")

    refused = consent_gate(view, now)
    if refused is not None:
        empty = Facts(fetched_at=now)
        next_step = await build_next_step(deps, user_id, refused.reason_codes, now)

        def refuse(_row: object = None) -> ToolResult:
            return ToolResult(
                summary=voice(deps, refused, empty, label, now),
                facts=LineFacts(line=label),
                reason_codes=list(refused.reason_codes),
                next_step=next_step,
                checked_at=now,
            )

        if resolved.line_id is None:  # NOT_BOUND: no line to audit against (07: "unattributable")
            timer.log(None)
            return refuse()
        rec = audit_record(
            deps, line_id=resolved.line_id, user_id=user_id, tool=TOOL, outcome=refused, now=now, source=None
        )
        out = await audited(deps, rec, refuse)
        timer.log(resolved.line_id)
        return out

    line_id = resolved.line_id
    assert line_id is not None  # noqa: S101 - consent holds ⇒ resolve returned the line

    def load() -> tuple[LastState | None, Line | None]:
        states = [w.last_state for w in list_watches_for_line(deps.store, line_id) if w.last_state]
        stored = freshest_line_state(states)
        if stored is not None and now - stored.at <= deps.thresholds.FRESH:
            return stored, None
        return stored, get_line(deps.store, line_id)

    try:
        stored, line_row = await anyio.to_thread.run_sync(load)
    except STORE_ERRORS:
        raise ConsentUnavailable("consent store unavailable") from None
    timer.mark("load")

    if line_row is None and stored is not None and now - stored.at <= deps.thresholds.FRESH:
        facts, source, checked_at = facts_from_state(stored, deps.thresholds.SWAP_WINDOW), "watch", stored.at
    else:
        if line_row is None:
            raise ConsentUnavailable("line record missing")
        ref = LineRef(line_id=line_id, e164=deps.cipher.decrypt(line_row.msisdn_enc))
        facts, errors = await live_facts(deps, ref, now)
        source, checked_at = "carrier", now
        # One unanswered call is enough to refuse (D4): any unknown fact with no known alarm is a carrier
        # error, and a timeout with a Watch answers from its last-known facts instead (02 §6). A live alarm
        # (the other call said "unconditional", or a swap) is never replaced by older stored facts.
        partial = facts.sim_swapped is None or facts.call_forwarding == "unknown"
        timed_out = any(e.reason_code == ReasonCode.STALE_DATA for e in errors)
        live_refused = evaluate_line(facts, view, now, deps.thresholds).kind == "refuse"
        if partial and live_refused and timed_out and stored is not None:
            facts, source, checked_at = (
                facts_from_state(stored, deps.thresholds.SWAP_WINDOW),
                "watch",
                stored.at,
            )
        timer.mark("carrier")

    outcome = evaluate_line(facts, view, now, deps.thresholds)
    summary = voice(deps, outcome, facts, label, now)
    next_step = await build_next_step(deps, user_id, outcome.reason_codes, now)
    timer.mark("policy")

    def respond(_row: object) -> ToolResult:
        return ToolResult(
            summary=summary,
            facts=result_facts(label, outcome, facts, source),
            reason_codes=list(outcome.reason_codes),
            next_step=next_step,
            checked_at=checked_at,
        )

    rec = audit_record(
        deps, line_id=line_id, user_id=user_id, tool=TOOL, outcome=outcome, now=now, source=source
    )  # type: ignore[arg-type]
    out = await audited(deps, rec, respond)
    timer.mark("audit")
    timer.log(line_id)
    return out
