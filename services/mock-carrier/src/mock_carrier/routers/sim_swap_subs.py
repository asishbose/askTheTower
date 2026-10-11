"""SIM Swap Subscriptions: `POST/GET/DELETE /sim-swap-subscriptions/v*/subscriptions`; CloudEvents
of type `org.camaraproject.sim-swap-subscriptions.v0.swapped` on every `sim_swap` event."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from mock_carrier.routers.subscriptions import mount_api

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "sim-swap-subscriptions"
OPS = {
    "create": "createSimSwapSubscription",
    "list": "retrieveSubscriptionList",
    "get": "retrieveSubscription",
    "delete": "deleteSubscription",
}


def _phone(detail: dict[str, Any]) -> str | None:
    value = detail.get("phoneNumber")
    return str(value) if value is not None else None


def mount(app: FastAPI, rt: Runtime) -> None:
    mount_api(app, rt, API, OPS, _phone)
