"""CAMARA error → `ReasonCode` (05 §5), and `CarrierError`, the only exception this package raises.

The map is keyed on (HTTP status, CAMARA `code`) and lists every pair the vendored Fall25 specs
declare (`tests/test_error_map.py` enumerates them from the YAML). Anything else falls back by
status: 429 → STALE_DATA, everything else → CARRIER_ERROR. The carrier's `message` text never
crosses this boundary: a `CarrierError` carries the status and the code, nothing the carrier wrote.
"""

from __future__ import annotations

from typing import Any

from tower_policy import ReasonCode

_CE = ReasonCode.CARRIER_ERROR
_NB = ReasonCode.NOT_BOUND
_SD = ReasonCode.STALE_DATA

# (status, code) → (reason, retryable). `retryable` only matters on the proactive profile.
ERROR_MAP: dict[tuple[int, str], tuple[ReasonCode, bool]] = {
    # 400 — our request was wrong; retrying sends the same thing
    (400, "INVALID_ARGUMENT"): (_CE, False),
    (400, "OUT_OF_RANGE"): (_CE, False),
    (400, "INVALID_PROTOCOL"): (_CE, False),
    (400, "INVALID_CREDENTIAL"): (_CE, False),
    (400, "INVALID_TOKEN"): (_CE, False),
    (400, "INVALID_SINK"): (_CE, False),
    # 401 — token expired or revoked: the cached token is dropped, so one retry can succeed
    (401, "UNAUTHENTICATED"): (_CE, True),
    # 403 — never surfaced as text; the one exception is "not over the line's mobile data"
    (403, "PERMISSION_DENIED"): (_CE, False),
    (403, "SUBSCRIPTION_MISMATCH"): (_CE, False),
    (403, "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"): (_NB, False),
    # 404 — the line isn't on this carrier → NOT_BOUND; a missing subscription is a carrier error
    (404, "IDENTIFIER_NOT_FOUND"): (_NB, False),
    (404, "NOT_FOUND"): (_CE, False),
    (409, "ABORTED"): (_CE, True),
    (409, "ALREADY_EXISTS"): (_CE, False),
    (410, "GONE"): (_CE, False),
    (422, "SERVICE_NOT_APPLICABLE"): (_CE, False),
    (422, "MISSING_IDENTIFIER"): (_CE, False),
    (422, "UNSUPPORTED_IDENTIFIER"): (_CE, False),
    (422, "UNNECESSARY_IDENTIFIER"): (_CE, False),
    (422, "MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED"): (_CE, False),
    # 429 — answer from last-known state (Watch) instead
    (429, "QUOTA_EXCEEDED"): (_SD, False),
    (429, "TOO_MANY_REQUESTS"): (_SD, True),
    (501, "NOT_IMPLEMENTED"): (_CE, False),
    (503, "UNAVAILABLE"): (_CE, True),
}


class CarrierError(Exception):
    """A carrier call failed. `reason_code` is what Tower/Alerts act on; `status`/`code` are the
    CAMARA envelope's machine fields (or `None` for timeouts, transport errors, an open breaker);
    `kind` says which of those it was. No carrier message text, no number, no token."""

    def __init__(
        self,
        reason_code: ReasonCode,
        retryable: bool,
        *,
        status: int | None = None,
        code: str | None = None,
        kind: str = "http",
    ) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        self.status = status
        self.code = code
        self.kind = kind
        super().__init__(str(self))

    def __str__(self) -> str:
        detail = f"{self.status} {self.code}" if self.status is not None else self.kind
        return f"{self.reason_code.value} ({detail})"

    def __repr__(self) -> str:
        return (
            f"CarrierError(reason_code={self.reason_code.value!r}, retryable={self.retryable}, "
            f"status={self.status}, code={self.code!r}, kind={self.kind!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CarrierError):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(tuple(self.as_dict().items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code.value,
            "retryable": self.retryable,
            "status": self.status,
            "code": self.code,
            "kind": self.kind,
        }


def map_error(status: int, code: str | None) -> CarrierError:
    """The one place an HTTP error becomes a reason code."""
    if code is not None and (status, code) in ERROR_MAP:
        reason, retryable = ERROR_MAP[(status, code)]
    elif status == 429:
        reason, retryable = _SD, True
    elif status >= 500:
        reason, retryable = _CE, True
    else:
        reason, retryable = _CE, False
    return CarrierError(reason, retryable, status=status, code=code)


def error_code_of(body: Any) -> str | None:
    """The CAMARA `code` from an error envelope, if there is one; never the `message`."""
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str) and len(code) <= 128:
            return code
    return None


def timeout_error() -> CarrierError:
    """A per-call timeout: STALE_DATA (the caller answers from the Watch, or says CARRIER_ERROR)."""
    return CarrierError(_SD, True, kind="timeout")


def transport_error() -> CarrierError:
    return CarrierError(_CE, True, kind="transport")


def malformed_response() -> CarrierError:
    """A 2xx whose body does not have the spec's shape."""
    return CarrierError(_CE, False, kind="malformed")


def breaker_open() -> CarrierError:
    return CarrierError(_SD, False, kind="breaker_open")
