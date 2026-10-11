"""Reason codes — exactly the twelve in `docs/architecture/components/03-policy-engine.md` §4.

Nine are returned by the engine; three are audit-only and written by the Alerts service (06).
Do not add codes here. If one seems missing, the case goes back to the design doc first.
"""

from enum import StrEnum, unique


@unique
class ReasonCode(StrEnum):
    # --- returned by the engine -------------------------------------------------
    OK = "OK"
    SIM_SWAPPED_RECENT = "SIM_SWAPPED_RECENT"
    CALL_FORWARDING_SET = "CALL_FORWARDING_SET"
    UNREACHABLE = "UNREACHABLE"
    NOT_BOUND = "NOT_BOUND"
    NO_CONSENT = "NO_CONSENT"
    STALE_DATA = "STALE_DATA"
    CARRIER_ERROR = "CARRIER_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    # --- audit only, written by Alerts (06); never returned by evaluate_* --------
    SUPPRESSED_REVOKED = "SUPPRESSED_REVOKED"
    ALERT_FAILED = "ALERT_FAILED"
    ACK_IGNORED_SWAPPED_LINE = "ACK_IGNORED_SWAPPED_LINE"


ENGINE_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.OK,
        ReasonCode.SIM_SWAPPED_RECENT,
        ReasonCode.CALL_FORWARDING_SET,
        ReasonCode.UNREACHABLE,
        ReasonCode.NOT_BOUND,
        ReasonCode.NO_CONSENT,
        ReasonCode.STALE_DATA,
        ReasonCode.CARRIER_ERROR,
        ReasonCode.SERVICE_UNAVAILABLE,
    }
)
"""Codes `evaluate_line` / `evaluate_reachability` may return (SERVICE_UNAVAILABLE is raised by callers)."""

AUDIT_ONLY_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.SUPPRESSED_REVOKED,
        ReasonCode.ALERT_FAILED,
        ReasonCode.ACK_IGNORED_SWAPPED_LINE,
    }
)
"""Codes that only ever appear in audit rows; `phrase()` renders them as the empty string."""

CHANGED_CODES: frozenset[ReasonCode] = frozenset(
    {ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET, ReasonCode.UNREACHABLE}
)
REFUSE_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.NOT_BOUND,
        ReasonCode.NO_CONSENT,
        ReasonCode.STALE_DATA,
        ReasonCode.CARRIER_ERROR,
        ReasonCode.SERVICE_UNAVAILABLE,
    }
)
