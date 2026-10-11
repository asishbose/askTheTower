"""The `CarrierClient` protocol (05 §1) and its result types.

Every method is `async` (Tower runs the SIM-swap and call-forwarding checks in parallel). The caller
decrypts the E.164 from `msisdn_enc` and hands it in as a `LineRef`; this package never sees
`msisdn_enc` or a key, and the number never leaves the call except inside the CAMARA request.
Results carry booleans and timestamps only (rule 3) — the one exception is `NumberVerifyResult.e164`,
which the binding page needs to bind the line it just verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SubscriptionKind = Literal["sim-swap", "reachability-data", "reachability-sms", "reachability-disconnected"]
"""One CAMARA subscription per kind: the Fall25 subscription APIs accept exactly one event type each."""

Connectivity = Literal["DATA", "SMS"]


@dataclass(frozen=True)
class LineRef:
    """A line as the client needs it: `line_id` (the HMAC, safe to log) and the decrypted E.164,
    which is excluded from `repr`/`str` so it cannot reach a log line or a traceback by accident."""

    line_id: str
    e164: str = field(repr=False)

    def __str__(self) -> str:
        return self.line_id


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SimSwapResult(_Result):
    swapped: bool


class CFResult(_Result):
    status: Literal["unconditional", "conditional", "none"]


class NumberVerifyResult(_Result):
    verified: bool
    e164: str | None = Field(default=None, repr=False)


class ReachResult(_Result):
    reachable: bool
    connectivity: tuple[Connectivity, ...] = ()
    last_status_time: AwareDatetime | None = None


@runtime_checkable
class CarrierClient(Protocol):
    """The seven operations of 05 §1. Failures raise `CarrierError(reason_code, retryable)`."""

    async def sim_swap_check(self, line: LineRef, max_age_h: int) -> SimSwapResult: ...

    async def sim_swap_date(self, line: LineRef) -> datetime | None: ...

    async def call_forwarding(self, line: LineRef) -> CFResult: ...

    async def number_verify(
        self, auth_code: str, *, redirect_uri: str, e164: str | None = None
    ) -> NumberVerifyResult: ...

    async def reachability(self, line: LineRef) -> ReachResult: ...

    async def subscribe(
        self,
        kind: SubscriptionKind,
        line: LineRef,
        sink_url: str,
        ttl: timedelta,
        *,
        now: datetime,
        sink_token: str | None = None,
    ) -> str: ...

    async def unsubscribe(self, subscription_id: str) -> None: ...

    async def aclose(self) -> None: ...
