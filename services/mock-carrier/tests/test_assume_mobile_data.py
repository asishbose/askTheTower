"""08 §3 (D-G): `MOCK_ASSUME_MOBILE_DATA` — the authorize step attributes every request to the line of
`MOCK_ASSUME_CLIENT_ID`, as if the carrier saw that phone on mobile data. Default off; one simulation
at a time; an unknown client id fails at startup."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest
from mock_carrier.app import create_app
from mock_carrier.settings import Settings
from mock_carrier.testing import ASISH, MOM, REDIRECT, auth_code_token

pytestmark = pytest.mark.integration

VERIFY = "/number-verification/v2/verify"
SHARE = "/number-verification/v2/device-phone-number"
NOT_ON_NETWORK = "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"


def test_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOCK_ASSUME_MOBILE_DATA", raising=False)
    monkeypatch.delenv("MOCK_ASSUME_CLIENT_ID", raising=False)
    s = Settings.from_env()
    assert s.assume_mobile_data is False
    assert s.assume_client_id == "phone-asish"
    assert Settings().assume_mobile_data is False


def test_env_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_ASSUME_MOBILE_DATA", "1")
    monkeypatch.setenv("MOCK_ASSUME_CLIENT_ID", "phone-mom")
    s = Settings.from_env()
    assert (s.assume_mobile_data, s.assume_client_id) == (True, "phone-mom")


async def test_off_without_header_is_still_refused(client: httpx.AsyncClient) -> None:
    h = await auth_code_token(client, None)
    r = await client.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
    assert (r.status_code, r.json()["code"]) == (403, NOT_ON_NETWORK)


async def test_on_authorizes_without_header(make_client: Callable[..., httpx.AsyncClient]) -> None:
    async with make_client(assume_mobile_data=True) as c:
        h = await auth_code_token(c, None)
        r = await c.post(VERIFY, json={"phoneNumber": ASISH}, headers=h)
        assert r.status_code == 200 and r.json() == {"devicePhoneNumberVerified": True}
        assert (await c.get(SHARE, headers=h)).json() == {"devicePhoneNumber": ASISH}


async def test_on_assumes_the_configured_client(make_client: Callable[..., httpx.AsyncClient]) -> None:
    async with make_client(assume_mobile_data=True, assume_client_id="phone-mom") as c:
        h = await auth_code_token(c, None)
        assert (await c.get(SHARE, headers=h)).json() == {"devicePhoneNumber": MOM}


async def test_on_with_header_is_one_simulation_too_many(
    make_client: Callable[..., httpx.AsyncClient],
) -> None:
    async with make_client(assume_mobile_data=True) as c:
        r = await c.get(
            "/oauth2/authorize",
            params={"response_type": "code", "client_id": "binding-page", "redirect_uri": REDIRECT},
            headers={"X-Mock-Client-Id": "phone-asish"},
        )
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_request"
        assert "one simulation at a time" in r.json()["error_description"]


def test_unknown_client_id_fails_at_startup(settings: Settings) -> None:
    with pytest.raises(ValueError, match="MOCK_ASSUME_CLIENT_ID"):
        create_app(replace(settings, assume_mobile_data=True, assume_client_id="phone-stranger"))


def test_startup_logs_the_simulation(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="mock_carrier")
    create_app(replace(settings, assume_mobile_data=True))
    assert "SIMULATION: MOCK_ASSUME_MOBILE_DATA=1 (client id phone-asish)" in caplog.text
    caplog.clear()
    create_app(settings)
    assert "SIMULATION" not in caplog.text
