"""`is_reachable(line)` — the `line_is_ok` skeleton with one carrier call (02 §2): Device Reachability Status →
`tower_policy.evaluate_reachability` → `OK` (reachable), `UNREACHABLE`, or a refusal. Never a location.

Grants: owner, `watch` or `reachability` (04 §3). A carrier timeout with a Watch that holds a reachability
state answers from it (STALE_DATA, clearly marked), as for `line_is_ok` (02 §6).
"""

from __future__ import annotations

from datetime import datetime

import anyio
from camara_client import CarrierError, LineRef, ReachResult
from tower_consent import LastState, Line, get_line, list_watches_for_line
from tower_policy import Facts, Outcome, ReasonCode, evaluate_reachability

from tower_mcp.deps import Deps
from tower_mcp.errors import ConsentUnavailable
from tower_mcp.next_step import build_next_step
from tower_mcp.schemas import Connectivity, ReachFacts, ToolResult
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

TOOL = "is_reachable"


def connectivity_of(r: ReachResult) -> Connectivity:
    if "DATA" in r.connectivity:
        return "DATA"
    if "SMS" in r.connectivity:
        return "SMS"
    return "UNKNOWN" if r.reachable else "NONE"


def freshest_reach_state(states: list[LastState]) -> LastState | None:
    usable = [s for s in states if s.reachable is not None]
    return max(usable, key=lambda s: s.at) if usable else None


def facts_from_state(state: LastState) -> Facts:
    return Facts(
        fetched_at=state.at,
        reachable=state.reachable,
        connectivity="UNKNOWN" if state.reachable else "NONE",
        last_status_time=state.unreachable_since if state.reachable is False else None,
    )


def result_facts(label: str, outcome: Outcome, facts: Facts, source: str) -> ReachFacts:
    if ReasonCode.CARRIER_ERROR in outcome.reason_codes:
        return ReachFacts(line=label, source=source)  # type: ignore[arg-type]
    return ReachFacts(
        line=label,
        reachable=facts.reachable,
        connectivity=facts.connectivity,
        last_status_time=facts.last_status_time,
        source=source,  # type: ignore[arg-type]
        stale=ReasonCode.STALE_DATA in outcome.reason_codes,
        as_of=facts.fetched_at,
    )


async def is_reachable(deps: Deps, user_id: str, line: str | None = "self") -> ToolResult:
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
                facts=ReachFacts(line=label),
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

    def load() -> tuple[LastState | None, Line | None]:
        states = [w.last_state for w in list_watches_for_line(deps.store, line_id) if w.last_state]
        return freshest_reach_state(states), get_line(deps.store, line_id)

    try:
        stored, line_row = await anyio.to_thread.run_sync(load)
    except STORE_ERRORS:
        raise ConsentUnavailable("consent store unavailable") from None
    if line_row is None:
        raise ConsentUnavailable("line record missing")
    timer.mark("load")

    ref = LineRef(line_id=line_id, e164=deps.cipher.decrypt(line_row.msisdn_enc))
    source = "carrier"
    checked_at: datetime = now
    try:
        r = await deps.carrier.reachability(ref)
        facts = Facts(
            fetched_at=now,
            reachable=r.reachable,
            connectivity=connectivity_of(r),
            last_status_time=r.last_status_time,
        )
    except CarrierError as err:
        facts = Facts(fetched_at=now)
        if err.reason_code == ReasonCode.STALE_DATA and stored is not None:
            facts, source, checked_at = facts_from_state(stored), "watch", stored.at
    timer.mark("carrier")

    outcome = evaluate_reachability(facts, view, now, deps.thresholds)
    summary = voice(deps, outcome, facts, label, now)
    next_step = await build_next_step(deps, user_id, outcome.reason_codes, now)

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
