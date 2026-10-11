"""SMS delivery behind one Protocol (06 §3; e2e §2 "Alerts → SNS → phone").

The E.164 is a parameter of `send` and goes nowhere else: not into a log line, not into an exception. Log
lines name the recipient by role and user id (`chain:user-asish`).

- `SnsSender` — `sns.publish(PhoneNumber=…)`, transactional SMS. AWS.
- `LogSender` — logs the body; local and tests. Keeps the sends in memory (`sent`) for tests.
- `WebhookSmsSender` — `LogSender` plus `POST SMS_GATEWAY_URL {"to", "body"}` (an SMS-gateway app on a phone).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("alerts.sms")


class SendError(Exception):
    """Delivery failed. Carries no number and no provider text."""


class SmsSender(Protocol):
    async def send(self, to_e164: str, body: str, *, label: str) -> None: ...


@dataclass(frozen=True)
class SentSms:
    label: str
    body: str
    to_e164: str = field(repr=False)


class LogSender:
    def __init__(self) -> None:
        self.sent: list[SentSms] = []
        self.fail_next = 0  # tests: fail the next n attempts

    async def send(self, to_e164: str, body: str, *, label: str) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            log.warning("SMS to=%s FAILED (injected)", label)
            raise SendError("injected failure")
        self.sent.append(SentSms(label=label, body=body, to_e164=to_e164))
        log.info("SMS to=%s body=%r", label, body)


class WebhookSmsSender(LogSender):
    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self.url = url
        self._http = httpx.AsyncClient(transport=transport, timeout=5.0)

    async def send(self, to_e164: str, body: str, *, label: str) -> None:
        await super().send(to_e164, body, label=label)
        try:
            r = await self._http.post(self.url, json={"to": to_e164, "body": body})
        except httpx.HTTPError:
            raise SendError("gateway unreachable") from None
        if r.status_code >= 300:
            raise SendError(f"gateway status {r.status_code}")


class SnsSender:
    def __init__(self, sns_client: Any) -> None:
        self.sns = sns_client

    async def send(self, to_e164: str, body: str, *, label: str) -> None:
        try:
            self.sns.publish(
                PhoneNumber=to_e164,
                Message=body,
                MessageAttributes={
                    "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}
                },
            )
        except Exception:  # noqa: BLE001 - provider errors may echo the number; never propagate their text
            log.warning("SMS to=%s FAILED (sns)", label)
            raise SendError("sns publish failed") from None
        log.info("SMS to=%s via sns", label)
