"""09 §6.4 / prompt 20 acceptance: the page itself refuses to render a `next_step.url` outside
`BINDING_BASE_URL + "/bind/"`. `test_static.py` checks the source text; this runs `app.js` in Chromium.

The page's files are served from disk through Playwright's request interception (no server, no network); the
agent is a queue of canned replies. Skipped when Chromium for Playwright is not installed
(`uv run playwright install chromium`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

PAGE_DIR = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8083"  # a secure context, as the local page is (crypto.randomUUID needs one)
BIND = "https://bind.test"
LOCAL_BEARER = "local-dev-tower-bearer"
CONFIG = {
    "COGNITO_DOMAIN": "",  # local sign-in stub
    "CLIENT_ID": "",
    "REDIRECT_URI": "",
    "AGENT_URL": "/invocations",
    "BINDING_BASE_URL": BIND,
    "LOCAL_BEARER": LOCAL_BEARER,
}
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


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


def reply(next_step: Any, text: str = "Your line isn't connected yet.") -> dict[str, Any]:
    return {"text": text, "next_step": next_step, "tool_calls": [{"name": "line_is_ok", "reason_codes": []}]}


# (case id, agent reply, the link the page must show or None)
CASES: list[tuple[str, dict[str, Any], str | None]] = [
    ("bind link", reply({"kind": "bind_line", "url": f"{BIND}/bind/tok-1"}), f"{BIND}/bind/tok-1"),
    ("foreign host", reply({"kind": "bind_line", "url": "https://evil.example/bind/x"}), None),
    ("lookalike host", reply({"kind": "bind_line", "url": f"{BIND}.evil.example/bind/x"}), None),
    ("not /bind/", reply({"kind": "bind_line", "url": f"{BIND}/bindx"}), None),
    ("other path", reply({"kind": "bind_line", "url": f"{BIND}/me"}), None),
    ("script url", reply({"kind": "bind_line", "url": "javascript:alert(1)"}), None),
    ("no url", reply({"kind": "bind_line"}), None),
    ("ask_consent", reply({"kind": "ask_consent", "url": f"{BIND}/bind/tok-2"}), None),
    ("url in text only", reply(None, f"Tap {BIND}/bind/from-the-model to connect."), None),
]
REJECTED = {"foreign host", "lookalike host", "not /bind/", "other path", "script url", "no url"}


@pytest.mark.skipif(_NO_BROWSER is not None, reason=_NO_BROWSER or "")
def test_page_renders_only_a_bind_link_under_binding_base_url() -> None:
    from playwright.sync_api import Route, sync_playwright

    replies: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []

    def serve(route: Route) -> None:
        req = route.request
        path = req.url.removeprefix(ORIGIN).split("?", 1)[0]
        if not req.url.startswith(ORIGIN):
            route.abort()  # nothing leaves the test; every request is answered here, none reaches a socket
        elif path == "/invocations":
            sent.append({"headers": req.headers, "body": json.loads(req.post_data or "{}")})
            route.fulfill(status=200, content_type="application/json", body=json.dumps(replies.pop(0)))
        elif path == "/config.js":
            route.fulfill(
                content_type="text/javascript", body=f"window.WEB_CHAT_CONFIG = {json.dumps(CONFIG)};"
            )
        else:
            f = PAGE_DIR / (path.lstrip("/") or "index.html")
            if f.suffix in TYPES and f.is_file():
                route.fulfill(content_type=TYPES[f.suffix], body=f.read_bytes())
            else:
                route.fulfill(status=404, body="")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            console: list[str] = []
            page.on("console", lambda m: console.append(m.text))
            page.route("**/*", serve)
            page.goto(f"{ORIGIN}/")
            page.click('#sign-in-stub [data-user="user-asish"]')
            assert page.is_visible("#chat")
            for n, (case, answer, want) in enumerate(CASES, start=1):
                replies.append(answer)
                before = len(console)
                page.fill("#question", "Is my line OK?")
                page.press("#question", "Enter")
                page.wait_for_function(f"document.querySelectorAll('#messages li.tower').length === {n}")
                last = page.locator("#messages li.tower").last
                hrefs = last.locator("a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
                assert hrefs == ([want] if want else []), case
                assert (last.locator(".bind").count() == 1) is (want is not None), case
                warned = [m for m in console[before:] if "NEXT_STEP_REJECTED" in m]
                assert len(warned) == (1 if case in REJECTED else 0), case
                assert all(m == "NEXT_STEP_REJECTED" for m in warned), case  # never the URL itself
            # the model's URL is shown as text, never as a link
            assert "from-the-model" in page.locator("#messages li.tower").last.inner_text()
        finally:
            browser.close()

    assert len(sent) == len(CASES)
    for s in sent:  # the local stub: the static bearer and the picked user, nothing built from a reply
        assert s["headers"]["authorization"] == f"Bearer {LOCAL_BEARER}"
        assert s["headers"]["x-tower-user"] == "user-asish"
        assert s["body"]["input"] == "Is my line OK?" and len(s["body"]["session_id"]) >= 33
