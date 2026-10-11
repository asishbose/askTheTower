"""Bind tokens and the OAuth state (04 §2, §7): reuse, expiry, wrong user → 4xx, nothing stored."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from binding_page.session import FLOW_COOKIE
from tower_consent import get_bind_token, list_lines

pytestmark = pytest.mark.integration


def _no_lines(page: Any, *users: str) -> None:
    for u in users:
        assert list_lines(page.store, u) == []


async def _start(browser: httpx.AsyncClient, token: str, as_: str = "phone-asish") -> httpx.URL:
    """Tap Verify; return the callback URL the carrier sent the phone to (not yet followed)."""
    r = await browser.post(f"/bind/{token}/verify", data={"as": as_}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return httpx.URL(r.headers["location"])


async def test_unknown_token_is_404(page: Any) -> None:
    async with page.browser() as b:
        assert (await b.get("/bind/NoSuchTokenAtAllNoSuchTokenAtAllx")).status_code == 404
        assert (await b.post("/bind/NoSuchTokenAtAllNoSuchTokenAtAllx/verify")).status_code == 404
    _no_lines(page, "user-asish")


async def test_reuse_is_refused(page: Any, h: Any) -> None:
    browser = page.browser()
    token = page.bind_token("user-asish")
    r = await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"})
    assert r.status_code == 200
    # the same link again: the page, the verify step
    assert (await browser.get(f"/bind/{token}")).status_code == 404
    assert (await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"})).status_code == 404
    await browser.aclose()


async def test_replayed_callback_is_refused(page: Any) -> None:
    """The callback URL (code + state) replayed after success: the flow cookie is gone and the code is spent."""
    browser = page.browser()
    token = page.bind_token("user-asish")
    callback = await _start(browser, token)
    flow_cookie = browser.cookies.get(FLOW_COOKIE)
    r = await browser.get(str(callback))
    assert r.status_code == 200
    r = await browser.get(str(callback))
    assert r.status_code == 400
    # even with the old flow cookie put back, the token is spent (and the carrier code too)
    async with page.browser() as other:
        other.cookies.set(FLOW_COOKIE, flow_cookie, domain="binding.test", path="/bind")
        r = await other.get(str(callback))
        assert r.status_code in (400, 404)
    assert len(list_lines(page.store, "user-asish")) == 1
    await browser.aclose()


async def test_expired_token_is_refused(page: Any) -> None:
    browser = page.browser()
    token = page.bind_token("user-asish")
    page.clock.advance(minutes=10, seconds=1)
    assert (await browser.get(f"/bind/{token}?as=phone-asish")).status_code == 404
    assert (await browser.post(f"/bind/{token}/verify", data={"as": "phone-asish"})).status_code == 404
    _no_lines(page, "user-asish")
    await browser.aclose()


async def test_token_expiring_mid_flow_stores_nothing(page: Any) -> None:
    browser = page.browser()
    token = page.bind_token("user-asish")
    page.clock.advance(minutes=5)
    callback = await _start(browser, token)  # state + flow cookie live 10 min from here
    page.clock.advance(minutes=5, seconds=1)  # past the token's TTL, still inside the state's
    r = await browser.get(str(callback))
    assert r.status_code == 404
    _no_lines(page, "user-asish")
    await browser.aclose()


async def test_wrong_user_state_is_refused(page: Any) -> None:
    """A state signed for user B with user A's token in the flow cookie: the token is tied to A, so
    `consume_bind_token` refuses — nothing stored, and A's token survives (not burnt by B's attempt)."""
    browser = page.browser()
    token = page.bind_token("user-asish")
    callback = await _start(browser, token)
    state = page.deps.signer.unsign("state", callback.params["state"], now=page.deps.epoch())
    assert state is not None and state["u"] == "user-asish"
    forged = page.deps.signer.sign("state", {"u": "user-mallory", "n": state["n"]}, expires_at=state["x"])
    r = await browser.get(str(callback.copy_set_param("state", forged)))
    assert r.status_code == 404
    _no_lines(page, "user-asish", "user-mallory")
    assert get_bind_token(page.store, token, now=page.clock()) is not None
    await browser.aclose()


async def test_state_from_another_browser_is_refused(page: Any) -> None:
    """Login CSRF: browser 1's callback opened in browser 2 (which started its own flow) — nonce mismatch."""
    b1, b2 = page.browser(), page.browser()
    cb1 = await _start(b1, page.bind_token("user-asish"))
    await _start(b2, page.bind_token("user-mom"), as_="phone-mom")
    r = await b2.get(str(cb1))
    assert r.status_code == 400
    _no_lines(page, "user-asish", "user-mom")
    await b1.aclose()
    await b2.aclose()


async def test_tampered_state_is_refused(page: Any) -> None:
    browser = page.browser()
    callback = await _start(browser, page.bind_token("user-asish"))
    bad = callback.params["state"][:-1] + ("a" if callback.params["state"][-1] != "a" else "b")
    r = await browser.get(str(callback.copy_set_param("state", bad)))
    assert r.status_code == 400
    _no_lines(page, "user-asish")
    await browser.aclose()


async def test_invite_code_is_not_a_bind_token(page: Any, asish_browser: httpx.AsyncClient, h: Any) -> None:
    code = (await h.invite_code(asish_browser)).replace("-", "")
    assert (await asish_browser.get(f"/bind/{code}")).status_code == 404
