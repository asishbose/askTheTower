"""Device Reachability Status Subscriptions: events `reachability-data`, `reachability-sms`,
`reachability-disconnected` on the `reachable` / `unreachable` line events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from mock_carrier.routers.common import phone_from_device
from mock_carrier.routers.subscriptions import mount_api

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

API = "device-reachability-status-subscriptions"
OPS = {
    "create": "createDeviceReachabilityStatusSubscription",
    "list": "retrieveDeviceReachabilityStatusSubscriptionList",
    "get": "retrieveDeviceReachabilityStatusSubscription",
    "delete": "deleteDeviceReachabilityStatusSubscription",
}


def _device(detail: dict[str, Any]) -> str | None:
    return phone_from_device(detail.get("device"))


def mount(app: FastAPI, rt: Runtime) -> None:
    mount_api(app, rt, API, OPS, _device)
