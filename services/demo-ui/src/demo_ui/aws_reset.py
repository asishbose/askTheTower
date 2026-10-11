"""Reset on ENV=aws (doc 11 §3, §5; U3): the AWS-side counterpart of `make seed`, over HTTP only.

In this order, stopping at the first non-2xx with its step and status ("alerts reset: 401"), every call with
`DEMO_ADMIN_BEARER` (decision 1, 2026-10-11: the gate and each service behind it check that one bearer):
1. the mock reloads the demo scenario (`POST {MOCK_URL}/_admin/scenarios/load`), which also drops its subscriptions
   and, on Fargate (`MOCK_CLOCK=wall`, 08 §3), puts its clock back to wall time;
2. Alerts drops the Watches and its bookkeeping for Asish's and Mom's lines (`POST {ALERTS_URL}/internal/reset`;
   06 §3.2);
3. Mom grants Asish `watch` again through the binding page's bearer-mode admin (`POST /_admin/grants`; 04 §8),
   idempotent.

The ids are the Cognito subs from `artifacts/cognito-users.json`. It never imports the local seed module and never
writes to a store: every change is made by the service that owns it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from demo_ui.clients import Unavailable

log = logging.getLogger("demo_ui.aws_reset")


class AwsResetFailed(Unavailable):
    """A Reset step answered non-2xx or could not be reached; the message names the step and the status."""


@dataclass(frozen=True)
class AwsReset:
    mock: httpx.AsyncClient  # base MOCK_URL (`https://<api>/carrier-admin`), bearer DEMO_ADMIN_BEARER
    alerts: httpx.AsyncClient  # base ALERTS_URL, bearer DEMO_ADMIN_BEARER
    binding: httpx.AsyncClient  # base BINDING_ADMIN_URL, bearer DEMO_ADMIN_BEARER
    asish: str
    mom: str

    async def __call__(self) -> None:
        await _step(self.mock, "/_admin/scenarios/load", {"name": "demo"}, "mock load")
        await _step(self.alerts, "/internal/reset", {"user_ids": [self.asish, self.mom]}, "alerts reset")
        grant = {
            "owner_user_id": self.mom,
            "grantee_user_id": self.asish,
            "grant": "watch",
            "alias": "mom",
            "action": "grant",
        }
        await _step(self.binding, "/_admin/grants", grant, "binding re-grant")
        log.info("aws reset done steps=3")


async def _step(http: httpx.AsyncClient, path: str, body: dict[str, Any], what: str) -> None:
    try:
        r = await http.post(path, json=body)
    except httpx.HTTPError as e:
        raise AwsResetFailed(f"{what}: not reachable ({type(e).__name__})") from None
    if not 200 <= r.status_code < 300:
        log.warning("aws reset stopped step=%s status=%d", what, r.status_code)
        raise AwsResetFailed(f"{what}: {r.status_code}")
