"""Every failure → a refusal envelope with the right code, or (for a bug) a generic MCP error.

Never in a result: a stack trace, a carrier error string, a DynamoDB message (02 §3). The mapping:

| Exception | Result |
|---|---|
| consent store unreachable / throttled / misconfigured (`ConsentUnavailable`, botocore errors, crypto errors) | `SERVICE_UNAVAILABLE` refusal — never guess consent (02 §6) |
| `tower_audit.AuditWriteFailed` | `SERVICE_UNAVAILABLE` refusal — no audit, no answer |
| anything else (the policy raised, a bug, the crash hook) | `InternalError` → MCP error result "internal error", logged; never a partial result |

Carrier failures never reach here: `camara_client.CarrierError` is turned into missing facts, and the policy
engine says `CARRIER_ERROR` / `STALE_DATA` (03 §3).
"""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError
from tower_audit import AuditWriteFailed
from tower_consent.errors import ConsentError
from tower_policy import ReasonCode


class TowerError(Exception):
    """Base for this service's own errors."""


class ConsentUnavailable(TowerError):
    """The consent store could not answer. Never treated as "no consent" or "consent"."""


class InternalError(TowerError):
    """Surfaced to the MCP client as a bare error ("internal error"); details stay in our logs."""


SERVICE_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    ConsentUnavailable,
    AuditWriteFailed,
    ClientError,
    BotoCoreError,
    ConsentError,
)


def refusal_code(exc: BaseException) -> ReasonCode | None:
    """The reason code a failure is answered with, or None if it is a bug (→ InternalError)."""
    if isinstance(exc, SERVICE_UNAVAILABLE_ERRORS):
        return ReasonCode.SERVICE_UNAVAILABLE
    return None
