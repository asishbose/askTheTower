"""A real browser at phone size drives the one tap and the page session: open link → "Wi-Fi on" refused → tap
Verify with the simulated device → "Line connected" → /me → audit. Skipped when Chromium for Playwright is not
installed (`uv run playwright install chromium`). Screenshots of the five pages go to `$BIND_SCREENSHOTS_DIR`
(e.g. `artifacts/screenshots/binding-page`) when set, else to the test's tmp dir.

The page runs in uvicorn on a free localhost port in a thread; the mock carrier is in-process behind it
(the browser never talks to the carrier locally — `mobile_data.py`)."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tower_consent import list_lines

pytestmark = pytest.mark.integration


def _chromium_ok() -> str | None:
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


_NO_BROWSER = _chromium_ok()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def served(page: Any) -> Iterator[str]:
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(page.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.skipif(_NO_BROWSER is not None, reason=_NO_BROWSER or "")
def test_phone_flow_in_a_real_browser(page: Any, served: str, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    shots = Path(os.environ.get("BIND_SCREENSHOTS_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    wifi_token = page.bind_token("user-asish")
    token = page.bind_token("user-asish")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True
        )
        tab = ctx.new_page()

        # "Wi-Fi on": the link without the simulated device → the carrier refuses
        tab.goto(f"{served}/bind/{wifi_token}")
        tab.get_by_role("button", name="Verify").tap()
        tab.get_by_text("couldn't see this phone on mobile data").wait_for()
        tab.screenshot(path=str(shots / "2-refused.png"), full_page=True)

        # the real tap: simulated mobile data
        tab.goto(f"{served}/bind/{token}?as=phone-asish")
        assert tab.locator("input:not([type=hidden]), textarea, select").count() == 0  # nothing to type
        tab.screenshot(path=str(shots / "1-bind.png"), full_page=True)
        tab.get_by_role("button", name="Verify").tap()
        tab.get_by_text("Line connected").wait_for()
        assert "•••• 0101" in tab.content()
        tab.screenshot(path=str(shots / "3-connected.png"), full_page=True)

        tab.get_by_role("link", name="Sharing and activity").tap()
        tab.get_by_role("heading", name="Your lines").wait_for()
        tab.get_by_role("button", name="Create invite code").tap()
        tab.locator("p.big").wait_for()
        assert tab.locator("p.big").inner_text().count("-") == 1
        tab.screenshot(path=str(shots / "4-me.png"), full_page=True)

        tab.get_by_role("link", name="Activity on this line").tap()
        tab.get_by_text("Log verified").wait_for()
        tab.screenshot(path=str(shots / "5-audit.png"), full_page=True)
        ctx.close()
        browser.close()

    assert len(list_lines(page.store, "user-asish")) == 1
    assert sorted(f.name for f in shots.glob("*.png")) >= ["1-bind.png", "2-refused.png", "3-connected.png"]
