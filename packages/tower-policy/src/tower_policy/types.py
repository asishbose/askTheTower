"""Input and output shapes of the engine (03 §2, e2e-wiring §6). Frozen pydantic models.

`datetime` fields must be timezone-aware (conventions: always UTC on the wire). A naive datetime is
rejected at construction so the engine never compares aware with naive.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from tower_policy.codes import CHANGED_CODES, REFUSE_CODES, ReasonCode

CallForwarding = Literal["none", "unconditional", "conditional", "unknown"]
Connectivity = Literal["DATA", "SMS", "NONE", "UNKNOWN"]
Grant = Literal["owner", "watch", "reachability", "none"]
OutcomeKind = Literal["ok", "changed", "refuse"]


def _require_aware(name: str, value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None):
        raise ValueError(f"{name} must be timezone-aware")
    return value


class Facts(BaseModel):
    """What the carrier said, as booleans and timestamps only. `None` = the carrier didn't answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fetched_at: datetime
    sim_swapped: bool | None = None
    latest_sim_change: datetime | None = None
    call_forwarding: CallForwarding = "unknown"
    reachable: bool | None = None
    connectivity: Connectivity = "UNKNOWN"
    last_status_time: datetime | None = None

    @field_validator("fetched_at", "latest_sim_change", "last_status_time")
    @classmethod
    def _aware(cls, v: datetime | None, info: ValidationInfo) -> datetime | None:
        return _require_aware(info.field_name or "datetime", v)


class ConsentView(BaseModel):
    """Consent state for one (line, asking user) pair, as resolved by tower-consent (04)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bound: bool
    grant: Grant
    revoked_at: datetime | None = None
    line_id: str | None = None  # HMAC line id; carried for audit, never consulted by the rules

    @field_validator("revoked_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _require_aware("revoked_at", v)


class Outcome(BaseModel):
    """`ok | changed | refuse` plus the reason codes that explain it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OutcomeKind
    reason_codes: list[ReasonCode]

    @property
    def refused(self) -> bool:
        return self.kind == "refuse"

    @property
    def changed(self) -> bool:
        return self.kind == "changed"

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


def ok() -> Outcome:
    return Outcome(kind="ok", reason_codes=[ReasonCode.OK])


def changed(codes: list[ReasonCode]) -> Outcome:
    if not codes or any(c not in CHANGED_CODES for c in codes):
        raise ValueError(f"changed() takes one or more of {sorted(CHANGED_CODES)}, got {codes}")
    return Outcome(kind="changed", reason_codes=list(codes))


def refuse(code: ReasonCode) -> Outcome:
    if code not in REFUSE_CODES:
        raise ValueError(f"refuse() takes one of {sorted(REFUSE_CODES)}, got {code}")
    return Outcome(kind="refuse", reason_codes=[code])
