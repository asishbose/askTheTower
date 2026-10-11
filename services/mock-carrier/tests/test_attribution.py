"""08 §6 Attribution: Number Verification needs a token the network attributed to the line.

The prompt's "→ 422" is the Commonalities 0.4 `UNIDENTIFIABLE_DEVICE`. The vendored Fall25 spec has no
422 on Number Verification; its code for "not authenticated over the mobile network" is
403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`, which is what is asserted here
(build log 04, Decisions)."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from mock_carrier.testing import ASISH, MOM, auth_code_token, cc_token

pytestmark = pytest.mark.integration

VERIFY = "/number-verification/v2/verify"
SHARE = "/number-verification/v2/device-phone-number"
NOT_ON_NETWORK = "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"


async def test_no_header_is_refused(client: httpx.AsyncClient) -> None:
    h = await auth_code_token(client, None)
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    assert (r.status_code, r.json()["code"]) == (403, NOT_ON_NETWORK)
    assert r.json()["status"] == 403 and r.json()["message"]


async def test_unknown_client_id_is_refused(client: httpx.AsyncClient) -> None:
    h = await auth_code_token(client, "phone-stranger")
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    assert (r.status_code, r.json()["code"]) == (403, NOT_ON_NETWORK)


async def test_right_client_id_verifies(client: httpx.AsyncClient) -> None:
    h = await auth_code_token(client, "phone-asish")
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    assert r.status_code == 200 and r.json() == {"devicePhoneNumberVerified": True}
    hashed = hashlib.sha256(ASISH.encode()).hexdigest()
    r = await client.post(VERIFY, json={"hashedPhoneNumber": hashed}, headers=h)
    assert r.json() == {"devicePhoneNumberVerified": True}
    r = await client.get(SHARE, headers=h)
    assert r.json() == {"devicePhoneNumber": ASISH}


async def test_other_lines_phone_does_not_verify(client: httpx.AsyncClient) -> None:
    """Mom's phone, on mobile data, claiming Asish's number: attributed, but not a match."""
    h = await auth_code_token(client, "phone-mom")
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    assert r.status_code == 200 and r.json() == {"devicePhoneNumberVerified": False}
    assert (await client.post(VERIFY, json={"phoneNumber": MOM}, headers=h)).json()[
        "devicePhoneNumberVerified"
    ]


async def test_two_legged_token_is_refused(client: httpx.AsyncClient) -> None:
    h = await cc_token(client)
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    # tower's token carries no number-verification scope at all
    assert (r.status_code, r.json()["code"]) == (403, "PERMISSION_DENIED")


async def test_both_identifiers_is_400(client: httpx.AsyncClient) -> None:
    h = await auth_code_token(client, "phone-asish")
    r = await client.post(VERIFY, json={"phoneNumber": ASISH, "hashedPhoneNumber": "0" * 64}, headers=h)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_ARGUMENT")


async def test_code_is_single_use(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/oauth2/authorize",
        params={"client_id": "binding-page", "redirect_uri": "http://localhost:8081/cb"},
        headers={"X-Mock-Client-Id": "phone-asish"},
    )
    code = httpx.URL(r.headers["location"]).params["code"]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": "http://localhost:8081/cb"}
    auth = ("binding-page", "local-dev-binding")
    assert (await client.post("/oauth2/token", data=form, auth=auth)).status_code == 200
    r = await client.post("/oauth2/token", data=form, auth=auth)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


async def test_unregistered_redirect_is_refused(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/oauth2/authorize", params={"client_id": "binding-page", "redirect_uri": "https://evil.example/cb"}
    )
    assert r.status_code == 400
