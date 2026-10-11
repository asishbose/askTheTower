"""Wire shapes: the `ToolResult` envelope (02 §3, e2e-wiring §6) and the per-tool `facts` (02 §2, 01 §3).

Facts are booleans, timestamps and closed enums only. The one free-ish string is `line`, which is the
caller's own word for the line (`"self"` or the alias they were granted under) — never a number. Everything
is `extra="forbid"`, so a field that isn't declared here cannot reach Alexa+.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from tower_policy import ReasonCode

NextStepKind = Literal["none", "call_carrier", "bind_line", "ask_consent"]
CallForwarding = Literal["none", "unconditional", "conditional", "unknown"]
Connectivity = Literal["DATA", "SMS", "NONE", "UNKNOWN"]
GrantKind = Literal["watch", "reachability"]
Source = Literal["carrier", "watch"]
WatchProfile = Literal["self", "transplant", "care"]


class _Shape(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NextStep(_Shape):
    """What Alexa+ can offer next. `url` only for `bind_line`; `carrier_support_number` only for
    `call_carrier` — the carrier's public support line from config, never a subscriber's number."""

    kind: NextStepKind = "none"
    url: str | None = None
    carrier_support_number: str | None = None


class LineFacts(_Shape):
    """`line_is_ok`. On a refusal only `line` (and `stale`/`as_of` for STALE_DATA) is set."""

    line: str
    sim_swapped_recently: bool | None = None
    swapped_at: AwareDatetime | None = None  # only when sim_swapped_recently is true
    call_forwarding: CallForwarding | None = None
    source: Source | None = None  # carrier = asked live; watch = Watches.last_state
    stale: bool = False  # true when these are last-known facts (STALE_DATA)
    as_of: AwareDatetime | None = None


class ReachFacts(_Shape):
    """`is_reachable`. No location, ever (01 §2)."""

    line: str
    reachable: bool | None = None
    connectivity: Connectivity | None = None
    last_status_time: AwareDatetime | None = None
    source: Source | None = None
    stale: bool = False
    as_of: AwareDatetime | None = None


class GrantFact(_Shape):
    alias: str
    grant: GrantKind


class RecentChecksFact(_Shape):
    by_actor: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, int] = Field(default_factory=dict)
    last_at: AwareDatetime | None = None


class WatchFacts(_Shape):
    """`watch_line` (02 §2): `{watching, since, notify_via, profile, grants, recent_checks}`.

    `profile` is the caller's stored Watch profile, also while watching is off; null with no Watch (06 §11.1).
    The contacts behind it never appear here. `grants` and `recent_checks` are the line-holder's view only
    (07 §4: a watcher never reads who else checked the line they watch); for a grantee they are empty / null."""

    line: str
    watching: bool | None = None
    since: AwareDatetime | None = None
    notify_via: Literal["sms"] | None = None
    profile: WatchProfile | None = None
    grants: list[GrantFact] = Field(default_factory=list)
    recent_checks: RecentChecksFact | None = None


Facts = LineFacts | ReachFacts | WatchFacts


class ToolResult(_Shape):
    """The one envelope every tool returns, refusals included (02 §3)."""

    summary: str = Field(min_length=1)
    facts: Facts
    reason_codes: list[ReasonCode] = Field(min_length=1)
    next_step: NextStep = Field(default_factory=NextStep)
    checked_at: AwareDatetime

    def to_wire(self) -> dict[str, object]:
        return self.model_dump(mode="json")
