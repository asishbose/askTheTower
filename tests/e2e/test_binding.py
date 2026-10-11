"""Binding (testing-and-showcase §2.4) against the running ENV.

- Every ENV: a user with no line asks Tower → `NOT_BOUND` with a single-use binding link; `GET` on that link
  answers 200 with no phone number on the page (on eks/aws this smoke is the whole check — no simulation there).
- ENV=local only: a real headless browser at phone size does the one tap with the mobile-data simulation param
  (`?as=phone-asish`, compose's `TOWER_ENV=local`): "Wi-Fi on" (no param) → refused; with it → "Line connected".
  Asish is already the owner after `make seed`, so the bind is the idempotent re-bind and changes no demo state.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.e2e.tower import call
from tests.helpers.env import Stack, Targets
from tests.helpers.patterns import phone_hits

pytestmark = pytest.mark.e2e


def _chromium_missing() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright is not installed"
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as e:  # noqa: BLE001 - any launch failure means "no browser here"
        return f"Chromium for Playwright is not available ({type(e).__name__}); run `uv run playwright install chromium`"
    return None


def test_not_bound_link_answers(stack: Stack, env: Targets) -> None:
    user = f"user-e2e-{uuid.uuid4().hex[:8]}"
    r = call(env, user, "line_is_ok", {"line": "self"})
    assert r["reason_codes"] == ["NOT_BOUND"]
    assert r["next_step"]["kind"] == "bind_line"
    url = r["next_step"]["url"]
    assert "/bind/" in url and not phone_hits(url)
    page = httpx.get(url, timeout=15.0, follow_redirects=True)
    assert page.status_code == 200, page.text[:500]
    assert not phone_hits(page.text)


def test_one_tap_in_a_real_browser(stack: Stack, env: Targets) -> None:
    if env.env != "local":
        pytest.skip(
            f"ENV={env.env}: the mobile-data simulation exists only locally; the GET smoke above is the check"
        )
    missing = _chromium_missing()
    if missing:
        pytest.skip(missing)
    from playwright.sync_api import sync_playwright

    def link() -> str:
        """A fresh bind link for Asish from the binding page's local admin (BIND_ADMIN=1, compose only)."""
        r = httpx.post(f"{env.binding_url}/_admin/bind-tokens", json={"user_id": "user-asish"}, timeout=10.0)
        assert r.status_code == 200, f"binding admin (BIND_ADMIN=1) answered {r.status_code}: {r.text[:300]}"
        return env.binding_url + httpx.URL(r.json()["url"]).path

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        tab = ctx.new_page()

        tab.goto(link())  # "Wi-Fi on": no simulated device → the carrier refuses
        tab.get_by_role("button", name="Verify").tap()
        tab.get_by_text("couldn't see this phone on mobile data").wait_for(timeout=15000)

        tab.goto(link() + "?as=phone-asish")
        assert tab.locator("input:not([type=hidden]), textarea, select").count() == 0  # nothing to type
        tab.get_by_role("button", name="Verify").tap()
        tab.get_by_text("Line connected").wait_for(timeout=15000)
        assert "•••• " in tab.content()  # masked last four only
        assert not phone_hits(tab.content())
        ctx.close()
        browser.close()
