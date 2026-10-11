"""CloudEvents sender for subscription notifications.

`POST sink` with `Content-Type: application/cloudevents+json`; up to 3 retries with exponential
backoff on a network error, a 5xx or a 429; every attempt sequence is recorded in
`state.deliveries`. A sink whose host is `MOCK_LOOPBACK_SINK_HOST` (default `sink.mock.local`) is
delivered in-process to `state.sink_inbox` instead — a simulation aid for the showcase, so a judge can
watch a CloudEvent arrive without running a receiver. These are the only outbound calls the mock makes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from mock_carrier.clock import iso
from mock_carrier.state import Delivery, Subscription

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

RETRIES = 3


class WebhookSender:
    def __init__(self, rt: Runtime, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.rt = rt
        self.transport = transport

    def cloud_event(self, sub: Subscription, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        spec = self.rt.specs.specs[sub.api]
        return {
            "id": self.rt.state.next_id("evt"),
            "source": f"{spec.base_path}/subscriptions/{sub.id}",
            "type": event_type,
            "specversion": "1.0",
            "datacontenttype": "application/json",
            "time": iso(self.rt.now()),
            "data": data,
        }

    async def send(self, sub: Subscription, event_type: str, data: dict[str, Any]) -> Delivery:
        event = self.cloud_event(sub, event_type, data)
        headers = {"Content-Type": "application/cloudevents+json", "x-correlator": event["id"]}
        cred = sub.sink_credential or {}
        if cred.get("credentialType") == "ACCESSTOKEN" and cred.get("accessToken"):
            headers["Authorization"] = f"Bearer {cred['accessToken']}"
        for name, value in ((sub.protocol_settings or {}).get("headers") or {}).items():
            headers.setdefault(str(name), str(value))

        attempts, status, error, delivered = 0, None, None, False
        if urlsplit(sub.sink).hostname == self.rt.settings.loopback_sink_host:
            attempts, status, delivered = 1, 204, True
            self.rt.state.sink_inbox.append({"sink": sub.sink, "event": event})
        else:
            settings = self.rt.settings
            async with httpx.AsyncClient(
                transport=self.transport, timeout=settings.webhook_timeout_s
            ) as client:
                for attempt in range(RETRIES + 1):
                    attempts = attempt + 1
                    try:
                        resp = await client.post(sub.sink, json=event, headers=headers)
                        status, error = resp.status_code, None
                        if 200 <= resp.status_code < 300:
                            delivered = True
                            break
                        if resp.status_code < 500 and resp.status_code != 429:
                            break  # the sink refused it; retrying will not help
                    except httpx.HTTPError as exc:
                        status, error = None, type(exc).__name__
                    if attempt < RETRIES and settings.webhook_backoff_s > 0:
                        await asyncio.sleep(settings.webhook_backoff_s * (2**attempt))
        delivery = Delivery(
            id=self.rt.state.next_id("dlv"),
            subscription_id=sub.id,
            event_id=event["id"],
            type=event_type,
            sink=sub.sink,
            attempts=attempts,
            delivered=delivered,
            status_code=status,
            error=error,
            at=event["time"],
        )
        self.rt.state.deliveries.append(delivery)
        return delivery
