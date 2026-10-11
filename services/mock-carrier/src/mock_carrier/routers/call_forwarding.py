"""Call Forwarding Signal: `retrieveUnconditionalCallForwarding` → `{active}`;
`retrieveCallForwarding` → the spec's `CallForwardingSignal`, a bare array of forwarding types
(`["inactive"]` when none is set)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import Response

from mock_carrier.routers.common import Call, register, subject

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "call-forwarding-signal"


async def unconditional(call: Call) -> Response:
    line = subject(call, call.body.get("phoneNumber"))
    return call.json({"active": line.unconditional_active()})


async def forwardings(call: Call) -> Response:
    line = subject(call, call.body.get("phoneNumber"))
    return call.json(line.forwarding_types())


def mount(app: FastAPI, rt: Runtime) -> None:
    register(app, rt, API, "retrieveUnconditionalCallForwarding", unconditional)
    register(app, rt, API, "retrieveCallForwarding", forwardings)
