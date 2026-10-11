"""Shared create/list/get/delete for the two CAMARA subscription APIs (Commonalities 0.6 shape).

- Only `protocol: HTTP` (400 `INVALID_PROTOCOL` otherwise); `sinkCredential` only `ACCESSTOKEN`
  (400 `INVALID_CREDENTIAL` otherwise); the sink must be `https://` (spec pattern).
- The scope needed for create is `<api>:<event type>:create`.
- `subscriptionExpireTime` must be after the mock clock (400 `OUT_OF_RANGE`); absent → +30 days.
- `subscriptionMaxEvents` honoured; `initialEvent` (reachability) sends the current status at once.
- The same client, line, type and sink with an active subscription → 409 `ALREADY_EXISTS`.
- Subscriptions are visible only to the client that created them (others get 404).
- Delete → 204 and a `subscription-ended` CloudEvent (`SUBSCRIPTION_DELETED`).
- Events carry `subscriptionId` only: the optional `phoneNumber`/`device` is never echoed to the sink.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import Response

from mock_carrier import errors
from mock_carrier.clock import parse_iso
from mock_carrier.routers.common import Call, register, subject
from mock_carrier.state import Line, Subscription

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

IdentifierOf = Callable[[dict[str, Any]], str | None]


def _check_protocol(body: dict[str, Any]) -> None:
    if body.get("protocol") != "HTTP":
        raise errors.InvalidProtocol("Only HTTP is supported.")
    cred = body.get("sinkCredential")
    if cred is not None:
        if cred.get("credentialType") != "ACCESSTOKEN":
            raise errors.InvalidCredential("Only Access token is supported.")
        if not cred.get("accessToken") or str(cred.get("accessTokenType", "bearer")).lower() != "bearer":
            raise errors.InvalidToken("Only bearer token is supported.")


async def create(call: Call, identifier_of: IdentifierOf) -> Response:
    rt, body = call.rt, call.body
    _check_protocol(body)
    event_type = body["types"][0]
    required = f"{call.op.api}:{event_type}:create"
    if not call.token.has(required):
        raise errors.PermissionDenied(f"Missing scope {required}.")
    config = body["config"]
    detail = config.get("subscriptionDetail") or {}
    line: Line = subject(call, identifier_of(detail))

    now = rt.now()
    expires_at = rt.default_expiry()
    if "subscriptionExpireTime" in config:
        try:
            expires_at = parse_iso(str(config["subscriptionExpireTime"]))
        except ValueError as exc:
            raise errors.InvalidArgument("config/subscriptionExpireTime: not an RFC 3339 date-time") from exc
        if expires_at <= now:
            raise errors.OutOfRange("config/subscriptionExpireTime: must be in the future.")

    for s in rt.state.subscriptions.values():
        if (
            s.is_active()
            and s.api == call.op.api
            and s.client_id == call.token.client_id
            and s.msisdn == line.msisdn
            and s.types == [event_type]
            and s.sink == body["sink"]
        ):
            raise errors.AlreadyExists("An active subscription with the same sink and type already exists.")

    sub = Subscription(
        id=rt.state.next_id("sub"),
        api=call.op.api,
        client_id=call.token.client_id,
        msisdn=line.msisdn,
        sink=body["sink"],
        protocol="HTTP",
        types=[event_type],
        config=config,
        protocol_settings=body.get("protocolSettings"),
        sink_credential=body.get("sinkCredential"),
        starts_at=now,
        expires_at=expires_at,
        max_events=config.get("subscriptionMaxEvents"),
    )
    rt.state.subscriptions[sub.id] = sub
    response = call.json(sub.public(), status=201)
    if config.get("initialEvent") and rt.reachability_type(line) == event_type:
        await rt.notify(sub, event_type)
    return response


def _visible(call: Call, sub_id: str) -> Subscription:
    sub = call.rt.state.subscriptions.get(sub_id)
    if (
        sub is None
        or sub.api != call.op.api
        or sub.client_id != call.token.client_id
        or sub.status == "DELETED"
    ):
        raise errors.NotFound("The specified resource is not found.")
    return sub


async def list_(call: Call) -> Response:
    subs = [
        s.public()
        for s in sorted(call.rt.state.subscriptions.values(), key=lambda s: s.id)
        if s.api == call.op.api and s.client_id == call.token.client_id and s.status != "DELETED"
    ]
    return call.json(subs)


async def get(call: Call) -> Response:
    return call.json(_visible(call, call.request.path_params["subscriptionId"]).public())


async def delete(call: Call) -> Response:
    sub = _visible(call, call.request.path_params["subscriptionId"])
    if sub.is_active():
        await call.rt.end_subscription(sub, "SUBSCRIPTION_DELETED", status="DELETED")
    else:
        sub.status = "DELETED"
    return call.empty(204)


def mount_api(app: FastAPI, rt: Runtime, api: str, ops: dict[str, str], identifier_of: IdentifierOf) -> None:
    async def _create(call: Call) -> Response:
        return await create(call, identifier_of)

    register(app, rt, api, ops["create"], _create)
    register(app, rt, api, ops["list"], list_)
    register(app, rt, api, ops["get"], get)
    register(app, rt, api, ops["delete"], delete)
