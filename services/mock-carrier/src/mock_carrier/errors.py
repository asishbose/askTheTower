"""CAMARA error envelope `{status, code, message}` — one exception class per code the vendored specs
(Fall25, Commonalities 0.6) define. Every non-2xx the mock produces goes through `envelope()`."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


class CamaraError(Exception):
    status: int = 500
    code: str = "INTERNAL"

    def __init__(self, message: str | None = None, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.__class__.__doc__ or self.code
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "message": self.message}


# --- 400 -------------------------------------------------------------------------------------------
class InvalidArgument(CamaraError):
    """Client specified an invalid argument, request body or query param."""

    status, code = 400, "INVALID_ARGUMENT"


class OutOfRange(CamaraError):
    """Client specified an invalid range."""

    status, code = 400, "OUT_OF_RANGE"


class InvalidProtocol(CamaraError):
    """Only HTTP is supported."""

    status, code = 400, "INVALID_PROTOCOL"


class InvalidCredential(CamaraError):
    """Only Access token is supported."""

    status, code = 400, "INVALID_CREDENTIAL"


class InvalidToken(CamaraError):
    """Only bearer token is supported."""

    status, code = 400, "INVALID_TOKEN"


class InvalidSink(CamaraError):
    """The sink is not a valid HTTPS URL."""

    status, code = 400, "INVALID_SINK"


# --- 401 / 403 -------------------------------------------------------------------------------------
class Unauthenticated(CamaraError):
    """Request not authenticated due to missing, invalid, or expired credentials."""

    status, code = 401, "UNAUTHENTICATED"


class PermissionDenied(CamaraError):
    """Client does not have sufficient permissions to perform this action."""

    status, code = 403, "PERMISSION_DENIED"


class UserNotAuthenticatedByMobileNetwork(CamaraError):
    """Client must authenticate via the mobile network to use this service."""

    status, code = 403, "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"


class SubscriptionMismatch(CamaraError):
    """Inconsistent access token for requested events subscription."""

    status, code = 403, "SUBSCRIPTION_MISMATCH"


# --- 404 -------------------------------------------------------------------------------------------
class NotFound(CamaraError):
    """The specified resource is not found."""

    status, code = 404, "NOT_FOUND"


class IdentifierNotFound(CamaraError):
    """Device identifier not found."""

    status, code = 404, "IDENTIFIER_NOT_FOUND"


# --- 409 -------------------------------------------------------------------------------------------
class AlreadyExists(CamaraError):
    """The resource that a client tried to create already exists."""

    status, code = 409, "ALREADY_EXISTS"


class Aborted(CamaraError):
    """Concurrency conflict."""

    status, code = 409, "ABORTED"


class Gone(CamaraError):
    """Access to the target resource is no longer available (sink-side code in the callback specs)."""

    status, code = 410, "GONE"


# --- 422 -------------------------------------------------------------------------------------------
class ServiceNotApplicable(CamaraError):
    """The service is not available for the provided identifier."""

    status, code = 422, "SERVICE_NOT_APPLICABLE"


class MissingIdentifier(CamaraError):
    """The device cannot be identified."""

    status, code = 422, "MISSING_IDENTIFIER"


class UnnecessaryIdentifier(CamaraError):
    """The device is already identified by the access token."""

    status, code = 422, "UNNECESSARY_IDENTIFIER"


class UnsupportedIdentifier(CamaraError):
    """The identifier provided is not supported."""

    status, code = 422, "UNSUPPORTED_IDENTIFIER"


class MultieventSubscriptionNotSupported(CamaraError):
    """Multi event subscription is not supported."""

    status, code = 422, "MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED"


# --- 429 / 5xx -------------------------------------------------------------------------------------
class TooManyRequests(CamaraError):
    """Rate limit reached."""

    status, code = 429, "TOO_MANY_REQUESTS"


class QuotaExceeded(CamaraError):
    """Out of resource quota."""

    status, code = 429, "QUOTA_EXCEEDED"


class Internal(CamaraError):
    """Unknown server error. Typically a server bug."""

    status, code = 500, "INTERNAL"


class NotImplemented_(CamaraError):
    """This functionality is not implemented yet."""

    status, code = 501, "NOT_IMPLEMENTED"


class Unavailable(CamaraError):
    """Service Unavailable."""

    status, code = 503, "UNAVAILABLE"


def envelope(err: CamaraError, *, correlator: str | None = None) -> JSONResponse:
    headers = dict(err.headers)
    if correlator:
        headers["x-correlator"] = correlator
    return JSONResponse(status_code=err.status, content=err.body(), headers=headers)


def envelope_for(status: int, code: str, message: str, *, correlator: str | None = None) -> JSONResponse:
    headers = {"x-correlator": correlator} if correlator else {}
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "message": message}, headers=headers
    )
