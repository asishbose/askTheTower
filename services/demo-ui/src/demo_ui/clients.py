"""Thin httpx wrappers for the three admin surfaces the UI reads and drives (doc 11 §3). No CAMARA path, no
Gateway: the mock is reached only under `/_admin` (§8.2), and lines only by the mock's opaque `ref` (G2).

Every failure becomes `Unavailable(reason)`: the pane greys out with the reason (§9) and nothing falls back.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from ref_client.demo import SentListUnavailable

HOLDERS = {
    "phone-asish": "asish",
    "phone-mom": "mom",
    "phone-partner": "partner",
    "phone-neighbour": "neighbour",
}
LINE_EVENTS = ("sim_swap", "cf_set", "cf_clear", "unreachable", "reachable")
FAULT_KINDS = ("timeout", "500", "429")


class Unavailable(RuntimeError):
    """A pane's source cannot be used now. The message is shown on screen; it never carries a value."""


def bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _call(http: httpx.AsyncClient, method: str, path: str, what: str, **kw: Any) -> Any:
    try:
        r = await http.request(method, path, **kw)
    except httpx.HTTPError as e:
        raise Unavailable(f"{what}: not reachable ({type(e).__name__})") from None
    if r.status_code == 401:
        raise Unavailable(f"{what}: 401, the token is missing or wrong")
    if r.status_code == 404:
        raise Unavailable(f"{what}: 404, the admin surface is off here")
    if r.status_code >= 300:
        raise Unavailable(f"{what}: answered {r.status_code}")
    return r.json()


def holder_of(line: dict[str, Any]) -> str | None:
    for cid in line.get("mobile_data_client_ids", []):
        if cid in HOLDERS:
            return HOLDERS[cid]
    return None


class MockAdmin:
    """`/_admin/*` on the mock with `MOCK_ADMIN_TOKEN` (G1) locally, `DEMO_ADMIN_BEARER` on ENV=aws (decision 1).
    Reads use `?view=refs`: no number crosses."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def state(self) -> dict[str, Any]:
        data: dict[str, Any] = await _call(self.http, "GET", "/_admin/state", "mock", params={"view": "refs"})
        return data

    async def ref_of(self, who: str) -> str:
        for ref, line in (await self.state())["lines"].items():
            if holder_of(line) == who:
                return str(ref)
        raise Unavailable(f"mock: no line held by {who} in the loaded scenario")

    async def advance(self, minutes: int) -> list[str]:
        body = await _call(self.http, "POST", "/_admin/clock", "mock", json={"advance_s": minutes * 60})
        return [str(f.get("event", "")) for f in body.get("fired", [])]

    async def fire(
        self, who: str, event: Literal["sim_swap", "cf_set", "cf_clear", "unreachable", "reachable"]
    ) -> None:
        ref = await self.ref_of(who)
        await _call(self.http, "POST", f"/_admin/lines/{ref}/events", "mock", json={"event": event})

    async def fault(self, kind: str, n: int) -> None:
        await _call(self.http, "POST", "/_admin/faults", "mock", json={"kind": kind, "n": n})

    async def clear_faults(self) -> None:
        await _call(self.http, "DELETE", "/_admin/faults", "mock")


class BindingAdmin:
    """The binding page's admin (04 §8): bind links, grants, resolve. Locally open; on AWS in bearer mode behind the
    demo admin path (U2, U3; bearer `DEMO_ADMIN_BEARER`, decision 1), where the users are Cognito subs. 404 everywhere
    when it is off.

    It is also the AWS `GrantControl` for the macros (`set_mom_grant`), so `ref_client` stays unchanged (doc 11 §5).
    """

    def __init__(self, http: httpx.AsyncClient, user_ids: dict[str, str] | None = None) -> None:
        self.http = http
        self.ids = user_ids or {"asish": "user-asish", "mom": "user-mom"}

    async def bind_url(self, user_id: str) -> str:
        body = await _call(
            self.http, "POST", "/_admin/bind-tokens", "binding page", json={"user_id": user_id}
        )
        return str(body["url"])

    async def grants(self) -> list[dict[str, Any]]:
        params = {
            "format": "json",
            "table": "Grants",
        }  # only Grants: pane 4 never reads another line's Audit rows
        tables = await _call(self.http, "GET", "/_admin/tables", "binding page", params=params)
        return list(tables.get("Grants", []))

    async def resolve(self, user: str, line: str) -> dict[str, Any]:
        params = {"user": user, "line": line}
        data: dict[str, Any] = await _call(self.http, "GET", "/_admin/resolve", "binding page", params=params)
        return data

    async def mom_grant(self, action: Literal["grant", "revoke"]) -> None:
        """Mom grants or revokes Asish's `watch` (the demo's moment 3), through `tower_consent` on the page."""
        body = {
            "owner_user_id": self.ids["mom"],
            "grantee_user_id": self.ids["asish"],
            "grant": "watch",
            "alias": "mom",
            "action": action,
        }
        await _call(self.http, "POST", "/_admin/grants", "binding page", json=body)

    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        """`ref_client.demo.GrantControl`: the macros' Grant step on ENV=aws."""
        await self.mom_grant(action)


class AlertsSent:
    """`GET /internal/sent?after=<n>` (G3; on AWS from `SentSms`, U1): template id, role, user id, body; never a
    number."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def after(self, n: int) -> list[dict[str, Any]]:
        body = await _call(self.http, "GET", "/internal/sent", "alerts", params={"after": n})
        return list(body.get("sent", []))


class AlertsSentControl:
    """`ref_client.demo.SentList` over the feed's `AlertsSent`: the macros' sent-list wait (09 §4) reads the same
    client and bearer as pane 3. `Unavailable` becomes `SentListUnavailable`, so the wait is skipped, not failed."""

    def __init__(self, alerts: AlertsSent) -> None:
        self.alerts = alerts

    async def after(self, n: int) -> list[dict[str, Any]]:
        try:
            return await self.alerts.after(n)
        except Unavailable as e:
            raise SentListUnavailable(str(e)) from None
