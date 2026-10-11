"""The skeleton every tool shares (02 §2): resolve consent, gate, phrase, audit-before-return.

Decisions stay in `tower_policy`: the consent gate is the engine's own first two rules, reached by calling
`evaluate_reachability` with no carrier facts (it returns `NOT_BOUND` / `NO_CONSENT` before it looks at any
fact). The only consent logic here is the per-tool grant scope of 04 §3, which the engine deliberately does
not know about (`reachability` grants `is_reachable` only).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal

import anyio
from botocore.exceptions import BotoCoreError, ClientError
from opentelemetry import trace
from tower_audit import AuditRecord, release
from tower_audit import audit_outcome as to_audit_outcome
from tower_consent import ResolvedConsent, resolve, validate_alias
from tower_consent.errors import ConsentError, InvalidAlias
from tower_policy import ConsentView, Facts, Outcome, ReasonCode, evaluate_reachability, phrase, phrase_code

from tower_mcp.deps import Deps
from tower_mcp.errors import ConsentUnavailable
from tower_mcp.schemas import ToolResult

logger = logging.getLogger("tower_mcp.tools")

ToolName = Literal["line_is_ok", "is_reachable", "watch_line"]
SELF = "self"

# 04 §3: which grant lets a grantee call which tool. The owner may call everything.
TOOL_GRANTS: dict[str, frozenset[str]] = {
    "line_is_ok": frozenset({"owner", "watch"}),
    "is_reachable": frozenset({"owner", "watch", "reachability"}),
    "watch_line": frozenset({"owner", "watch"}),
}
STORE_ERRORS = (ClientError, BotoCoreError, ConsentError)
CONSENT_CODES = frozenset({ReasonCode.NOT_BOUND, ReasonCode.NO_CONSENT})


def normalise_line(line: str | None) -> str:
    return (line or SELF).strip().lower() or SELF


def line_label(line: str) -> str:
    """What `facts.line` echoes: "self", a valid alias, or "unknown" — never what the caller typed if that
    is not an alias (a number-shaped `line` must not come back in the result)."""
    if line == SELF:
        return SELF
    try:
        return validate_alias(line)
    except InvalidAlias:
        return "unknown"


def spoken_name(label: str) -> str | None:
    """`{Name}` in templates: the alias, capitalised as a name; None for self/unknown ("That person")."""
    if label in (SELF, "unknown"):
        return None
    return label[:1].upper() + label[1:]


class Timer:
    """Per-step timings, logged at debug with `line_id` only (prompt 08 acceptance: where the time goes)."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        self.t0 = time.perf_counter()
        self.last = self.t0
        self.steps: list[tuple[str, float]] = []

    def mark(self, step: str) -> None:
        now = time.perf_counter()
        self.steps.append((step, (now - self.last) * 1000))
        self.last = now

    def log(self, line_id: str | None) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            parts = " ".join(f"{s}={ms:.1f}ms" for s, ms in self.steps)
            total = (time.perf_counter() - self.t0) * 1000
            logger.debug("%s line_id=%s %s total=%.1fms", self.tool, line_id or "-", parts, total)


async def resolve_consent(deps: Deps, user_id: str, line: str) -> ResolvedConsent:
    """One DynamoDB read (04 §5). Any store failure → `ConsentUnavailable` (→ SERVICE_UNAVAILABLE)."""
    try:
        resolved = await anyio.to_thread.run_sync(resolve, deps.store, user_id, line)
    except STORE_ERRORS as e:
        logger.warning("consent store unavailable: %s", type(e).__name__)
        raise ConsentUnavailable("consent store unavailable") from None
    if resolved.line_id is not None:
        # The nightly reconciliation (07 §1, prompt 13) matches this tool span to its audit row by line_id.
        # The HMAC id only — never a number; traces are not metrics, so the per-line label rule (10 §2) holds.
        trace.get_current_span().set_attribute("tower.line_id", resolved.line_id)
    return resolved


def scope_view(view: ConsentView, tool: ToolName) -> ConsentView:
    """A grant that does not cover this tool is no consent for it (04 §3)."""
    if view.bound and view.grant != "none" and view.grant not in TOOL_GRANTS[tool]:
        return ConsentView(bound=True, grant="none", revoked_at=None, line_id=view.line_id)
    return view


def consent_gate(view: ConsentView, now: datetime) -> Outcome | None:
    """The engine's consent rules alone: `NOT_BOUND` / `NO_CONSENT`, or None if consent holds."""
    outcome = evaluate_reachability(Facts(fetched_at=now), view, now)
    if outcome.refused and outcome.reason_codes[0] in CONSENT_CODES:
        return outcome
    return None


def voice(deps: Deps, outcome: Outcome, facts: Facts, label: str, now: datetime) -> str:
    return phrase(outcome, facts, alias=spoken_name(label), tz=deps.settings.tz, form="voice", now=now)


def voice_code(deps: Deps, code: ReasonCode, label: str, now: datetime) -> str:
    return phrase_code(
        code, Facts(fetched_at=now), alias=spoken_name(label), tz=deps.settings.tz, form="voice", now=now
    )


def audit_record(
    deps: Deps,
    *,
    line_id: str,
    user_id: str,
    tool: ToolName,
    outcome: Outcome,
    now: datetime,
    source: Literal["carrier", "watch"] | None,
) -> AuditRecord:
    return AuditRecord(
        line_id=line_id,
        ts=now,
        actor_user_id=user_id,
        tool=tool,
        trigger="voice",
        source=source,
        outcome=to_audit_outcome(outcome.kind),
        reason_codes=tuple(outcome.reason_codes),
        policy_version=deps.policy_version,
    )


async def audited[R](deps: Deps, record: AuditRecord, respond: Callable[[AuditRecord], R]) -> R:
    """Append the row, then build the answer (07 `release`). `AuditWriteFailed` propagates → refusal."""
    return await anyio.to_thread.run_sync(
        lambda: release(deps.store, record, respond, after_append=deps.after_audit)
    )


def unavailable(deps: Deps, tool: ToolName, line: str | None, now: datetime) -> ToolResult:
    """The `SERVICE_UNAVAILABLE` refusal (consent store down, audit write failed): same envelope, no facts."""
    from tower_mcp.schemas import LineFacts, ReachFacts, WatchFacts

    label = line_label(normalise_line(line))
    facts_cls = {"line_is_ok": LineFacts, "is_reachable": ReachFacts, "watch_line": WatchFacts}[tool]
    return ToolResult(
        summary=voice_code(deps, ReasonCode.SERVICE_UNAVAILABLE, label, now),
        facts=facts_cls(line=label),
        reason_codes=[ReasonCode.SERVICE_UNAVAILABLE],
        checked_at=now,
    )
