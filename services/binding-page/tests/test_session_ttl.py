"""04 §6: the page session lifetime is `SESSION_TTL_S` — default 1800 (30 min; local, kind, tests); AWS sets
86400 through Terraform `binding_session_ttl_s` (D-H). One knob, no second session setting (build log, Prompt 20, C3)."""

from __future__ import annotations

from typing import Any

import pytest
from binding_page.config import Settings

SECRET = "test-session-secret-not-real"


@pytest.mark.unit
def test_default_is_thirty_minutes() -> None:
    assert Settings(session_secret=SECRET).session_ttl_s == 1800
    assert Settings.from_env({"SESSION_SECRET": SECRET}).session_ttl_s == 1800


@pytest.mark.unit
def test_aws_value_is_read_from_the_env() -> None:
    env = {"SESSION_SECRET": SECRET, "SESSION_TTL_S": "86400"}
    assert Settings.from_env(env).session_ttl_s == 86400


@pytest.mark.integration
async def test_86400_reaches_the_session_cookie(make_page: Any, h: Any) -> None:
    page = make_page(session_ttl_s=86400)
    browser, r = await h.bind(page, "user-asish", "phone-asish")
    assert r.status_code == 200 and "Line connected" in r.text
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("atb_session="))
    assert "Max-Age=86400" in cookie
    assert (await browser.get("/me")).status_code == 200
    page.clock.advance(seconds=86400 - 60)
    assert (await browser.get("/me")).status_code == 200  # still signed in near the end of the demo day
    page.clock.advance(seconds=120)
    assert (await browser.get("/me")).status_code == 401  # the signed session expires with the cookie
    await browser.aclose()


@pytest.mark.integration
async def test_default_cookie_is_thirty_minutes(page: Any, h: Any) -> None:
    browser, r = await h.bind(page, "user-asish", "phone-asish")
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("atb_session="))
    assert "Max-Age=1800" in cookie
    await browser.aclose()
