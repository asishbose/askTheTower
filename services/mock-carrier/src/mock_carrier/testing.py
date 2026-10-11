"""Test helpers for anything that runs the mock in-process (this service's tests and the carrier
client's): a CloudEvents sink that lives in the test process, and OAuth helpers that obtain tokens
the way real callers do. Not used by the running service."""

from __future__ import annotations

import json
from typing import Any

import httpx

ASISH = "+16135550101"
MOM = "+16135550102"
BASE = "http://mock.test"
SINK = "https://sink.test/hooks/line-a"
REDIRECT = "http://localhost:8081/callback"


class Sink:
    """An in-process webhook receiver: records every request; can be told to fail the next n."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail_next = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next > 0:
            self.fail_next -= 1
            return httpx.Response(503)
        self.events.append(json.loads(request.content))
        return httpx.Response(204)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def cc_token(
    client: httpx.AsyncClient, client_id: str = "tower", scope: str | None = None
) -> dict[str, str]:
    data = {"grant_type": "client_credentials"}
    if scope:
        data["scope"] = scope
    r = await client.post("/oauth2/token", data=data, auth=(client_id, f"local-dev-{client_id}"))
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def auth_code_token(client: httpx.AsyncClient, mock_client_id: str | None) -> dict[str, str]:
    """Run the auth-code flow as the binding page would, from a 'phone' that sends X-Mock-Client-Id."""
    headers = {"X-Mock-Client-Id": mock_client_id} if mock_client_id else {}
    r = await client.get(
        "/oauth2/authorize",
        params={
            "response_type": "code",
            "client_id": "binding-page",
            "redirect_uri": REDIRECT,
            "state": "s1",
        },
        headers=headers,
    )
    assert r.status_code == 302, r.text
    location = httpx.URL(r.headers["location"])
    assert location.params["state"] == "s1"
    r = await client.post(
        "/oauth2/token",
        data={"grant_type": "authorization_code", "code": location.params["code"], "redirect_uri": REDIRECT},
        auth=("binding-page", "local-dev-binding"),
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def advance(client: httpx.AsyncClient, seconds: float) -> dict[str, Any]:
    r = await client.post("/_admin/clock", json={"advance_s": seconds})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body
