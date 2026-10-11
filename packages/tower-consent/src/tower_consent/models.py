"""Row shapes for the consent tables (04 §4, 06 §7, e2e-wiring §6). Pydantic v2, timezone-aware UTC only.

Datetimes are stored as fixed-width RFC 3339 strings (`2026-10-06T09:21:00.000000Z`) so that string order is
time order — `update_last_state`'s conditional write and the `Lines.by_owner` sort key rely on it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, field_validator
from tower_policy.types import CallForwarding

from tower_consent.errors import InvalidAlias

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _aware_utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("datetime must be timezone-aware")
    return v.astimezone(UTC)


def format_ts(v: datetime) -> str:
    return _aware_utc(v).strftime(TS_FORMAT)


UtcDatetime = Annotated[datetime, AfterValidator(_aware_utc), PlainSerializer(format_ts, when_used="json")]

GrantKind = Literal["watch", "reachability"]
Profile = Literal["care", "transplant", "self"]
BindingMethod = Literal["auth_code", "ciba", "device_token"]
TokenKind = Literal["bind", "invite"]

ALIAS_RE = re.compile(r"^[a-z][a-z' -]{0,23}$")
RESERVED_ALIASES = frozenset({"self", "owner", "me", "my line", "none"})


def validate_alias(alias: str) -> str:
    """Lowercase letters (plus space, hyphen, apostrophe), starts with a letter, <= 24 chars, no digits."""
    if not isinstance(alias, str) or not ALIAS_RE.match(alias) or alias != alias.strip():
        raise InvalidAlias("alias must be lowercase letters, at most 24 characters, no digits")
    if alias in RESERVED_ALIASES:
        raise InvalidAlias("alias is reserved")
    return alias


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    def to_item(self) -> dict[str, Any]:
        """DynamoDB item (plain python values; `None` attributes omitted so `attribute_not_exists` works)."""
        return self.model_dump(mode="json", exclude_none=True)


class User(_Row):
    user_id: str
    alexa_link_id: str | None = None
    alert_phone_enc: str | None = None  # 04 §4 `alert_phone_e164 (encrypted)` — MsisdnCipher output only
    created_at: UtcDatetime


class Line(_Row):
    line_id: str
    msisdn_enc: str
    owner_user_id: str
    bound_at: UtcDatetime
    binding_method: BindingMethod
    carrier_hint: str | None = None

    def __repr__(self) -> str:  # msisdn_enc is ciphertext, but keep reprs short and boring
        return f"Line(line_id={self.line_id!r}, owner_user_id={self.owner_user_id!r})"


class Grant(_Row):
    line_id: str
    grantee_user_id: str
    grant: GrantKind
    alias: str
    granted_at: UtcDatetime
    revoked_at: UtcDatetime | None = None

    @field_validator("alias")
    @classmethod
    def _alias(cls, v: str) -> str:
        return validate_alias(v)

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def to_item(self) -> dict[str, Any]:
        item = super().to_item()
        item["grantee_grant"] = f"{self.grantee_user_id}#{self.grant}"
        item["line_grant"] = f"{self.line_id}#{self.grant}"
        return item


class EscalationStep(_Row):
    user_id: str
    requires_ack: bool = False


class LastState(_Row):
    """`Watches.last_state` (06 §7): small, per line, overwritten each evaluation."""

    sim_change_at: UtcDatetime | None = None
    cf_status: CallForwarding | None = None
    reachable: bool | None = None
    unreachable_since: UtcDatetime | None = None
    last_alert_at: dict[str, UtcDatetime] = Field(default_factory=dict)  # reason code -> ts
    at: UtcDatetime


class Watch(_Row):
    line_id: str
    watcher_user_id: str
    profile: Profile
    last_state: LastState | None = None
    subscription_ids: list[str] = Field(default_factory=list)
    escalation: list[EscalationStep] = Field(default_factory=list)
    enabled: bool = True


class BindToken(_Row):
    """Single-use token, 10-minute TTL (`expires_at`, epoch seconds — the DynamoDB TTL attribute)."""

    token: str
    user_id: str
    kind: TokenKind = "bind"
    created_at: UtcDatetime
    expires_at: int

    def __repr__(self) -> str:
        return f"BindToken(kind={self.kind!r}, user_id={self.user_id!r}, token=<redacted>)"
