"""SIM Swap: `checkSimSwap` (maxAge honoured, 1..2400 h per the spec) and `retrieveSimSwapDate`
(`latestSimChange` nullable — null when the line has no recorded change)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import Response

from mock_carrier.clock import iso
from mock_carrier.routers.common import Call, register, subject

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "sim-swap"
DEFAULT_MAX_AGE_H = 240


async def check(call: Call) -> Response:
    line = subject(call, call.body.get("phoneNumber"))
    max_age = int(call.body.get("maxAge", DEFAULT_MAX_AGE_H))
    now = call.rt.now()
    changed = line.sim_change_at
    swapped = changed is not None and now - timedelta(hours=max_age) <= changed <= now
    return call.json({"swapped": swapped})


async def retrieve_date(call: Call) -> Response:
    line = subject(call, call.body.get("phoneNumber"))
    changed = line.sim_change_at
    if changed is not None and changed > call.rt.now():
        changed = None  # a change scheduled after the mock clock has not happened yet
    return call.json({"latestSimChange": iso(changed) if changed else None})


def mount(app: FastAPI, rt: Runtime) -> None:
    register(app, rt, API, "checkSimSwap", check)
    register(app, rt, API, "retrieveSimSwapDate", retrieve_date)
