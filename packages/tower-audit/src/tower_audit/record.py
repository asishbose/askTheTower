"""`AuditRecord` — exactly the row in docs/architecture/components/07-audit-log.md §2 — and its canonical JSON.

Stored item (table `tower_consent.tables.AUDIT`):

    line_id         PK — the HMAC line id (never a number)
    ts_seq          SK — "<ts>#<seq>": ts is fixed-width RFC 3339 UTC with microseconds and a Z
                    ("2026-10-06T14:30:00.000000Z"), seq a 4-digit tie-breaker; string order = chain order
    actor_user_id   who asked, or "system:alerts" (the trigger says which kind)
    tool            line_is_ok | is_reachable | watch_line | alert
    trigger         voice | poll | event | binding
    source          carrier | watch                       (omitted when not a voice check)
    outcome         ok | changed | refused | suppressed
    reason_codes    [ReasonCode, ...]                     (tower_policy.ReasonCode values only)
    message_ref     "<REASON_CODE>.<form>"                (a tower_policy template id; never a body; omitted if none)
    policy_version  tower_policy.policy_version(), re-lettered (see `encode_digest`)
    prev_hash       SHA-256 of the previous row's canonical JSON, re-lettered; "genesis" for a line's first row;
                    a signed "trimmed|..." marker on the oldest row after a trim (chain.py)
    ttl             epoch seconds, ts + 90 days (DynamoDB TTL attribute)

No free-text field exists: every string is an enum value, an id that is validated not to be digit-shaped,
or a hash written with the letters a-p (a hex digest alone would match the privacy regex `\\+?\\d{10,15}` in
about a fifth of all hashes).

Canonical JSON (`AuditRecord.canonical()`): every field above (ts and seq separately, not ts_seq), sorted keys,
no whitespace, ASCII, `null` for absent optionals, integers only (a float anywhere is a bug and raises).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from tower_consent.crypto import is_line_id
from tower_policy import AUDIT_ONLY_CODES, TEMPLATES, OutcomeKind, ReasonCode

Tool = Literal["line_is_ok", "is_reachable", "watch_line", "alert"]
Trigger = Literal["voice", "poll", "event", "binding"]
Source = Literal["carrier", "watch"]
AuditOutcome = Literal["ok", "changed", "refused", "suppressed"]

RETENTION: Final = timedelta(days=90)
GENESIS: Final = "genesis"
SYSTEM_ALERTS: Final = "system:alerts"

TS_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
SEQ_WIDTH: Final = 4
MAX_SEQ: Final = 10**SEQ_WIDTH - 1

TEMPLATE_IDS: Final[frozenset[str]] = frozenset(
    f"{code.value}.sms"
    for code, forms in TEMPLATES.items()
    if code not in AUDIT_ONLY_CODES and forms.get("sms")
)
"""Valid `message_ref` values: the SMS template of each reason code that has one (alerts are SMS; 06)."""

_HEX_TO_LETTERS: Final = str.maketrans("0123456789abcdef", "abcdefghijklmnop")
_LETTER_DIGEST = re.compile(r"[a-p]{64}")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9._:@+-]{1,128}")
_DIGIT_RUN = re.compile(r"\d{10,}")
MARKER_RE: Final = re.compile(
    r"trimmed\|(?P<at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\|(?P<orig>genesis|[a-p]{64})\|(?P<sig>[a-p]{64})"
)


def encode_digest(digest_hex: str) -> str:
    """A hex digest with its sixteen symbols re-lettered to a-p: same information, no digits."""
    return digest_hex.translate(_HEX_TO_LETTERS)


def format_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime(TS_FORMAT)


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, TS_FORMAT).replace(tzinfo=UTC)


def format_ts_seq(ts: datetime, seq: int) -> str:
    return f"{format_ts(ts)}#{seq:0{SEQ_WIDTH}d}"


def parse_ts_seq(ts_seq: str) -> tuple[datetime, int]:
    ts, _, seq = ts_seq.partition("#")
    return parse_ts(ts), int(seq)


def audit_outcome(kind: OutcomeKind) -> AuditOutcome:
    """Map the policy engine's `ok | changed | refuse` to the audit vocabulary."""
    return "refused" if kind == "refuse" else kind


def _no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("canonical JSON carries no floats")
    if isinstance(value, dict):
        for v in value.values():
            _no_floats(v)
    elif isinstance(value, list | tuple):
        for v in value:
            _no_floats(v)


class AuditRecord(BaseModel):
    """One audit row. Build it with `prev_hash=None`; `append` positions it (ts, seq, prev_hash, ttl)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: str
    ts: datetime
    seq: int = 0
    actor_user_id: str
    tool: Tool
    trigger: Trigger
    source: Source | None = None
    outcome: AuditOutcome
    reason_codes: tuple[ReasonCode, ...]
    message_ref: str | None = None
    policy_version: str
    prev_hash: str | None = None
    ttl: int = 0

    @model_validator(mode="before")
    @classmethod
    def _fill_ttl(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("ttl") and isinstance(data.get("ts"), datetime):
            ts: datetime = data["ts"]
            if ts.tzinfo is not None:
                data = {**data, "ttl": int((ts + RETENTION).timestamp())}
        return data

    @field_validator("line_id")
    @classmethod
    def _line_id(cls, v: str) -> str:
        if not is_line_id(v):
            raise ValueError("line_id must be an HMAC line id (ln_ + 64 letters)")
        return v

    @field_validator("ts")
    @classmethod
    def _ts(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("ts must be timezone-aware")
        return v.astimezone(UTC)

    @field_validator("seq")
    @classmethod
    def _seq(cls, v: int) -> int:
        if not 0 <= v <= MAX_SEQ:
            raise ValueError(f"seq must be within 0..{MAX_SEQ}")
        return v

    @field_validator("actor_user_id")
    @classmethod
    def _actor(cls, v: str) -> str:
        if not _ID.fullmatch(v) or _DIGIT_RUN.search(v):
            raise ValueError("actor_user_id must be an opaque id (no spaces, no 10+ digit run)")
        return v

    @field_validator("reason_codes")
    @classmethod
    def _codes(cls, v: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        if not v:
            raise ValueError("reason_codes must not be empty")
        return v

    @field_validator("message_ref")
    @classmethod
    def _message_ref(cls, v: str | None) -> str | None:
        if v is not None and v not in TEMPLATE_IDS:
            raise ValueError("message_ref must be a known template id")
        return v

    @field_validator("policy_version")
    @classmethod
    def _policy_version(cls, v: str) -> str:
        if _HEX_DIGEST.fullmatch(v):
            return encode_digest(v)
        if not _LETTER_DIGEST.fullmatch(v):
            raise ValueError("policy_version must be tower_policy.policy_version() (hex or re-lettered)")
        return v

    @field_validator("prev_hash")
    @classmethod
    def _prev_hash(cls, v: str | None) -> str | None:
        if v is not None and v != GENESIS and not _LETTER_DIGEST.fullmatch(v) and not MARKER_RE.fullmatch(v):
            raise ValueError("prev_hash must be 'genesis', a re-lettered SHA-256, or a trim marker")
        return v

    @field_validator("ttl")
    @classmethod
    def _ttl(cls, v: int) -> int:
        if v < 0:
            raise ValueError("ttl must be epoch seconds")
        return v

    # --- derived -------------------------------------------------------------------------------------------

    @property
    def ts_seq(self) -> str:
        return format_ts_seq(self.ts, self.seq)

    def positioned(self, ts: datetime, seq: int, prev_hash: str) -> AuditRecord:
        """A copy at chain position (ts, seq) with `prev_hash`; ttl is recomputed from the new ts."""
        data = self.model_dump()
        data.update(ts=ts, seq=seq, prev_hash=prev_hash, ttl=0)
        return AuditRecord.model_validate(data)

    def canonical_dict(self, *, prev_hash: str | None = None) -> dict[str, Any]:
        """The hashed form. `prev_hash` overrides the stored value (verify passes a trim marker's original)."""
        out: dict[str, Any] = {
            "line_id": self.line_id,
            "ts": format_ts(self.ts),
            "seq": self.seq,
            "actor_user_id": self.actor_user_id,
            "tool": self.tool,
            "trigger": self.trigger,
            "source": self.source,
            "outcome": self.outcome,
            "reason_codes": [c.value for c in self.reason_codes],
            "message_ref": self.message_ref,
            "policy_version": self.policy_version,
            "prev_hash": self.prev_hash if prev_hash is None else prev_hash,
            "ttl": self.ttl,
        }
        _no_floats(out)
        return out

    def canonical(self, *, prev_hash: str | None = None) -> str:
        """Sorted keys, no whitespace, ASCII, RFC 3339 Z timestamps, integers only."""
        return json.dumps(
            self.canonical_dict(prev_hash=prev_hash),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    # --- DynamoDB ------------------------------------------------------------------------------------------

    def to_item(self) -> dict[str, Any]:
        if self.prev_hash is None:
            raise ValueError("an unpositioned record cannot be stored; use append()")
        item: dict[str, Any] = {
            "line_id": self.line_id,
            "ts_seq": self.ts_seq,
            "actor_user_id": self.actor_user_id,
            "tool": self.tool,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "reason_codes": [c.value for c in self.reason_codes],
            "policy_version": self.policy_version,
            "prev_hash": self.prev_hash,
            "ttl": self.ttl,
        }
        if self.source is not None:
            item["source"] = self.source
        if self.message_ref is not None:
            item["message_ref"] = self.message_ref
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> AuditRecord:
        data = dict(item)
        ts, seq = parse_ts_seq(data.pop("ts_seq"))
        data.update(ts=ts, seq=seq)
        return cls.model_validate(data)
