"""Device Reachability Status: `getReachabilityStatus` → `{reachable, connectivity, lastStatusTime}`.
`connectivity` is present only while reachable; `lastStatusTime` is the mock-clock time of the last
transition (scenario load, or a `reachable`/`unreachable` event)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import Response

from mock_carrier.clock import iso
from mock_carrier.routers.common import Call, phone_from_device, register, subject

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "device-reachability-status"


async def retrieve(call: Call) -> Response:
    line = subject(call, phone_from_device(call.body.get("device")))
    body: dict[str, Any] = {"lastStatusTime": iso(line.last_status_at), "reachable": line.reachable}
    if line.reachable and line.connectivity:
        body["connectivity"] = list(line.connectivity)
    return call.json(body)


def mount(app: FastAPI, rt: Runtime) -> None:
    register(app, rt, API, "getReachabilityStatus", retrieve)
