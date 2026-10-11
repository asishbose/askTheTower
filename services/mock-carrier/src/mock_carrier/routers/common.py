"""The pipeline every CAMARA operation runs through, in this order:

1. injected fault (`/_admin/faults`) — `timeout` sleeps then continues, `500`/`429` answer at once;
2. optional latency jitter (`MOCK_JITTER_MS`);
3. `x-correlator` check (echoed on every response);
4. bearer token → 401 `UNAUTHENTICATED`; scopes → 403 `PERMISSION_DENIED`;
5. JSON body parsed and validated against the operation's request schema → 400 `INVALID_ARGUMENT`.

Routers then identify the subject line with `subject_from_phone()` / `subject_from_device()`, which
apply the CAMARA two-/three-legged identifier rules (422 `MISSING_IDENTIFIER`, 422
`UNNECESSARY_IDENTIFIER`, 404 `IDENTIFIER_NOT_FOUND`).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from mock_carrier import errors
from mock_carrier.oauth import Token, verify_bearer
from mock_carrier.specs import Operation
from mock_carrier.state import Line, line_ref

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

CORRELATOR_RE = re.compile(r"^[a-zA-Z0-9-_:;.\/<>{}]{0,256}$")


@dataclass
class Call:
    rt: Runtime
    op: Operation
    token: Token
    body: Any
    correlator: str | None
    request: Request

    def json(self, content: Any, status: int = 200) -> JSONResponse:
        headers = {"x-correlator": self.correlator} if self.correlator else {}
        return JSONResponse(status_code=status, content=content, headers=headers)

    def empty(self, status: int = 204) -> Response:
        headers = {"x-correlator": self.correlator} if self.correlator else {}
        return Response(status_code=status, headers=headers)


def correlator_of(request: Request) -> str | None:
    value = request.headers.get("x-correlator")
    if value is None:
        return None
    return value if CORRELATOR_RE.match(value) else None


async def begin(rt: Runtime, op: Operation, request: Request) -> Call:
    rt.state.calls += 1
    fault = rt.take_fault()
    if fault == "timeout":
        await asyncio.sleep(rt.settings.fault_timeout_s)
    elif fault == "500":
        raise errors.Internal("Injected fault (mock /_admin/faults).")
    elif fault == "429":
        raise errors.TooManyRequests("Injected fault (mock /_admin/faults).")
    await rt.jitter()

    raw_corr = request.headers.get("x-correlator")
    if raw_corr is not None and not CORRELATOR_RE.match(raw_corr):
        raise errors.InvalidArgument("x-correlator: does not match the XCorrelator pattern")

    token = verify_bearer(rt, request.headers.get("authorization"))
    if token is None:
        raise errors.Unauthenticated(
            "Request not authenticated due to missing, invalid, or expired credentials."
        )
    if op.scopes and not any(all(token.has(s) for s in alt) for alt in _scope_alternatives(op)):
        raise errors.PermissionDenied("Client does not have sufficient permissions to perform this action.")

    body: Any = None
    if op.request_schema is not None or op.body_required:
        raw = await request.body()
        if not raw:
            if op.body_required:
                raise errors.InvalidArgument("body: a JSON request body is required")
        else:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise errors.InvalidArgument("body: not valid JSON") from exc
            rt.specs.validate_body(op, body)
    return Call(rt=rt, op=op, token=token, body=body, correlator=raw_corr, request=request)


def _scope_alternatives(op: Operation) -> tuple[tuple[str, ...], ...]:
    """Subscription create operations list one scope per event type in a single requirement; the
    type-specific check happens in the router, so here any one of them is enough."""
    if op.operation_id.startswith("create") and "subscriptions" in op.api:
        return tuple((s,) for alt in op.scopes for s in alt)
    return op.scopes


def subject(call: Call, phone: str | None) -> Line:
    """Resolve the subject line from the token (three-legged) or an explicit phone number (two-legged)."""
    rt, token = call.rt, call.token
    if token.three_legged:
        if phone is not None:
            raise errors.UnnecessaryIdentifier(
                "The device is already identified by the access token; do not send an identifier."
            )
        if token.line is None:
            raise errors.MissingIdentifier("The device cannot be identified from the access token.")
        line = rt.state.line_by_ref(token.line)
        if line is None:
            raise errors.IdentifierNotFound("Device identifier not found.")
        return line
    if phone is None:
        raise errors.MissingIdentifier(
            "The device cannot be identified: send phoneNumber with a two-legged token."
        )
    line = rt.line(phone)
    if line is None:
        if 404 not in call.op.responses:
            # subscription create declares no 404; an unknown line is "service not applicable"
            raise errors.ServiceNotApplicable("The service is not available for the provided identifier.")
        raise errors.IdentifierNotFound("Device identifier not found.")
    return line


def phone_from_device(device: dict[str, Any] | None) -> str | None:
    """CAMARA `Device` → phone number; other identifiers are not supported by the mock."""
    if device is None:
        return None
    phone = device.get("phoneNumber")
    if phone is None:
        raise errors.UnsupportedIdentifier("The mock identifies devices by phoneNumber only.")
    return str(phone)


def ref(line: Line) -> str:
    return line_ref(line.msisdn)


Handler = Callable[[Call], Awaitable[Response]]


def register(app: FastAPI, rt: Runtime, api: str, operation_id: str, handler: Handler) -> None:
    """Mount `handler` at the method + path the vendored spec gives for `operation_id`."""
    op = rt.specs.operation(api, operation_id)

    async def endpoint(request: Request) -> Response:
        call = await begin(rt, op, request)
        return await handler(call)

    endpoint.__name__ = operation_id
    app.add_api_route(op.full_path, endpoint, methods=[op.method], include_in_schema=False, name=operation_id)
