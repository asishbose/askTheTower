"""What both clients share: the seven operations of 05 §1 written once, one method per CAMARA
operation, over an abstract `_send` (HTTP for `DirectClient`, an MCP tool call for `GatewayClient`);
and the middleware around every call — breaker → timeout → error map → (proactive only) one retry.

Because the result parsing lives here, the two implementations cannot drift: given the same HTTP
answer they build the same result object or raise the same `CarrierError`.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import httpx

from camara_client import specs
from camara_client.breaker import CircuitBreaker
from camara_client.errors import (
    CarrierError,
    error_code_of,
    malformed_response,
    map_error,
    timeout_error,
    transport_error,
)
from camara_client.protocol import (
    CFResult,
    LineRef,
    NumberVerifyResult,
    ReachResult,
    SimSwapResult,
    SubscriptionKind,
)
from camara_client.timeouts import Profile

logger = logging.getLogger("camara_client")

T = TypeVar("T")

SIM_SWAP = "sim-swap"
CALL_FORWARDING = "call-forwarding-signal"
NUMBER_VERIFICATION = "number-verification"
REACHABILITY = "device-reachability-status"
SIM_SWAP_SUBS = "sim-swap-subscriptions"
REACHABILITY_SUBS = "device-reachability-status-subscriptions"

_EVENT_PREFIX = {
    SIM_SWAP_SUBS: "org.camaraproject.sim-swap-subscriptions.v0.",
    REACHABILITY_SUBS: "org.camaraproject.device-reachability-status-subscriptions.v0.",
}
# kind → (subscriptions API, create operation, event type suffix)
SUBSCRIPTIONS: dict[str, tuple[str, str, str]] = {
    "sim-swap": (SIM_SWAP_SUBS, "createSimSwapSubscription", "swapped"),
    "reachability-data": (
        REACHABILITY_SUBS,
        "createDeviceReachabilityStatusSubscription",
        "reachability-data",
    ),
    "reachability-sms": (REACHABILITY_SUBS, "createDeviceReachabilityStatusSubscription", "reachability-sms"),
    "reachability-disconnected": (
        REACHABILITY_SUBS,
        "createDeviceReachabilityStatusSubscription",
        "reachability-disconnected",
    ),
}
DELETE_OPS = {
    SIM_SWAP_SUBS: "deleteSubscription",
    REACHABILITY_SUBS: "deleteDeviceReachabilityStatusSubscription",
}


class Answer:
    """A carrier's 2xx answer: status and parsed JSON body (None when empty)."""

    __slots__ = ("status", "body")

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise malformed_response()
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise malformed_response() from None
    if dt.tzinfo is None:
        raise malformed_response()
    return dt.astimezone(UTC)


def _field(answer: Answer, name: str, kind: type) -> Any:
    body = answer.body
    if not isinstance(body, dict) or not isinstance(body.get(name), kind):
        raise malformed_response()
    return body[name]


def error_from_answer(status: int, body: Any) -> CarrierError:
    return map_error(status, error_code_of(body))


class CarrierBase(ABC):
    """The seven operations, the middleware, and the bookkeeping both clients need."""

    profile: Profile
    breaker: CircuitBreaker

    # --- transport (implemented by DirectClient / GatewayClient) ------------------------------------
    @abstractmethod
    async def _send(
        self,
        op: specs.Operation,
        *,
        json: Any = None,
        path_params: dict[str, str] | None = None,
        auth_code: tuple[str, str] | None = None,
    ) -> Answer:
        """Perform one operation. Return the 2xx `Answer`; raise `CarrierError` for anything else.
        Must call `self._record(status)` for every HTTP status the carrier answered with."""

    async def aclose(self) -> None:  # pragma: no cover - overridden where there is something to close
        return None

    async def __aenter__(self: T) -> T:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- middleware ---------------------------------------------------------------------------------
    def _record(self, status: int) -> None:
        # 501 NOT_IMPLEMENTED is a permanent, healthy answer ("this carrier has no such operation")
        if status >= 500 and status != 501:
            self.breaker.on_server_error()
        else:
            self.breaker.on_success()

    async def _run(
        self,
        what: str,
        line_id: str | None,
        attempt: Callable[[], Awaitable[T]],
        *,
        retries: int | None = None,
    ) -> T:
        tries = 1 + (self.profile.retries if retries is None else retries)
        last: CarrierError | None = None
        for n in range(tries):
            try:
                self.breaker.before_call()
                try:
                    async with asyncio.timeout(self.profile.timeout_s):
                        return await attempt()
                except TimeoutError:
                    raise timeout_error() from None
                except httpx.TimeoutException:
                    raise timeout_error() from None
                except httpx.TransportError:
                    raise transport_error() from None
                finally:
                    if self.breaker.trial_in_flight:
                        self.breaker.on_no_answer()
            except CarrierError as err:
                last = err
                logger.debug(
                    "carrier %s failed (attempt %d/%d) line_id=%s: %s",
                    what,
                    n + 1,
                    tries,
                    line_id or "-",
                    err,
                )
                if not err.retryable or err.kind == "breaker_open":
                    break
        assert last is not None
        raise last.with_traceback(None) from None

    # --- the seven operations -----------------------------------------------------------------------
    async def sim_swap_check(self, line: LineRef, max_age_h: int) -> SimSwapResult:
        op = specs.operation(SIM_SWAP, "checkSimSwap")

        async def attempt() -> SimSwapResult:
            answer = await self._send(op, json={"phoneNumber": line.e164, "maxAge": int(max_age_h)})
            return SimSwapResult(swapped=_field(answer, "swapped", bool))

        return await self._run(op.operation_id, line.line_id, attempt)

    async def sim_swap_date(self, line: LineRef) -> datetime | None:
        op = specs.operation(SIM_SWAP, "retrieveSimSwapDate")

        async def attempt() -> datetime | None:
            answer = await self._send(op, json={"phoneNumber": line.e164})
            if not isinstance(answer.body, dict) or "latestSimChange" not in answer.body:
                raise malformed_response()
            return _parse_time(answer.body["latestSimChange"])

        return await self._run(op.operation_id, line.line_id, attempt)

    async def call_forwarding(self, line: LineRef) -> CFResult:
        """`retrieveCallForwarding` gives all three states in one call. A carrier that has not
        implemented it (501) is asked `retrieveUnconditionalCallForwarding` instead, within the
        same time budget."""
        op = specs.operation(CALL_FORWARDING, "retrieveCallForwarding")
        fallback = specs.operation(CALL_FORWARDING, "retrieveUnconditionalCallForwarding")

        async def attempt() -> CFResult:
            try:
                answer = await self._send(op, json={"phoneNumber": line.e164})
            except CarrierError as err:
                if err.status != 501:
                    raise
                answer = await self._send(fallback, json={"phoneNumber": line.e164})
                return CFResult(status="unconditional" if _field(answer, "active", bool) else "none")
            body = answer.body
            if not isinstance(body, list) or not all(isinstance(x, str) for x in body):
                raise malformed_response()
            if "unconditional" in body:
                return CFResult(status="unconditional")
            if any(x.startswith("conditional") for x in body):
                return CFResult(status="conditional")
            return CFResult(status="none")

        return await self._run(op.operation_id, line.line_id, attempt)

    async def number_verify(
        self, auth_code: str, *, redirect_uri: str, e164: str | None = None
    ) -> NumberVerifyResult:
        """Exchange the auth code from the carrier redirect, then `phoneNumberVerify` (when the
        number to check is known) or `phoneNumberShare`. Never retried: a code is single-use."""
        op = (
            specs.operation(NUMBER_VERIFICATION, "phoneNumberVerify")
            if e164 is not None
            else specs.operation(NUMBER_VERIFICATION, "phoneNumberShare")
        )

        async def attempt() -> NumberVerifyResult:
            if e164 is not None:
                answer = await self._send(op, json={"phoneNumber": e164}, auth_code=(auth_code, redirect_uri))
                ok = _field(answer, "devicePhoneNumberVerified", bool)
                return NumberVerifyResult(verified=ok, e164=e164 if ok else None)
            answer = await self._send(op, auth_code=(auth_code, redirect_uri))
            return NumberVerifyResult(verified=True, e164=_field(answer, "devicePhoneNumber", str))

        return await self._run(op.operation_id, None, attempt, retries=0)

    async def reachability(self, line: LineRef) -> ReachResult:
        op = specs.operation(REACHABILITY, "getReachabilityStatus")

        async def attempt() -> ReachResult:
            answer = await self._send(op, json={"device": {"phoneNumber": line.e164}})
            reachable = _field(answer, "reachable", bool)
            raw = answer.body.get("connectivity") or []
            if not isinstance(raw, list) or not all(c in ("DATA", "SMS") for c in raw):
                raise malformed_response()
            return ReachResult(
                reachable=reachable,
                connectivity=tuple(sorted(set(raw))) if reachable else (),
                last_status_time=_parse_time(answer.body.get("lastStatusTime")),
            )

        return await self._run(op.operation_id, line.line_id, attempt)

    async def subscribe(
        self,
        kind: SubscriptionKind,
        line: LineRef,
        sink_url: str,
        ttl: timedelta,
        *,
        now: datetime,
        sink_token: str | None = None,
    ) -> str:
        """Create one CAMARA subscription; returns an opaque id `<api>/<carrier id>` for `unsubscribe`."""
        api, op_id, suffix = SUBSCRIPTIONS[kind]
        op = specs.operation(api, op_id)
        expires = iso(now + ttl)
        detail: dict[str, Any] = (
            {"phoneNumber": line.e164} if api == SIM_SWAP_SUBS else {"device": {"phoneNumber": line.e164}}
        )
        body: dict[str, Any] = {
            "protocol": "HTTP",
            "sink": sink_url,
            "types": [_EVENT_PREFIX[api] + suffix],
            "config": {"subscriptionDetail": detail, "subscriptionExpireTime": expires},
        }
        if sink_token is not None:
            body["sinkCredential"] = {
                "credentialType": "ACCESSTOKEN",
                "accessToken": sink_token,
                "accessTokenExpiresUtc": expires,
                "accessTokenType": "bearer",
            }

        async def attempt() -> str:
            answer = await self._send(op, json=body)
            sub_id = _field(answer, "id", str)
            if not sub_id or "/" in sub_id:
                raise malformed_response()
            return f"{api}/{sub_id}"

        return await self._run(op.operation_id, line.line_id, attempt)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Delete a subscription. Already gone (404 / 410) counts as done."""
        api, _, carrier_id = subscription_id.partition("/")
        if api not in DELETE_OPS or not carrier_id:
            raise ValueError("not a subscription id returned by subscribe()")
        op = specs.operation(api, DELETE_OPS[api])

        async def attempt() -> None:
            try:
                await self._send(op, path_params={"subscriptionId": carrier_id})
            except CarrierError as err:
                if err.status in (404, 410):
                    return None
                raise
            return None

        await self._run(op.operation_id, None, attempt)
