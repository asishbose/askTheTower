"""The UI served for real (uvicorn on a free 127.0.0.1 port, in a thread, configured as `__main__` does) with fake
neighbours and a moto store (doc 11 §2, §6, §8.3, §11):

- Playwright (Chromium), skipped when it is not installed: the four panes render and fill from the SSE feed;
  Moment 1, pressed in the page, shows each step with a green badge and the run's pass status, over SSE; at phone
  size the panes stack and nothing overflows sideways; two tabs share one poller (the audit reads per tick do not
  double).
- Without a browser: `DEMO_UI_TOKEN` guards the SSE stream over real HTTP, and the server's log lines (uvicorn's
  included, access log off) carry no number, no token and no request line.

Same layer and skip rule as `services/binding-page/tests/test_playwright.py`.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from demo_ui import feed as feed_mod
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from ref_client.demo import STORIES, Control
from tower_audit import list_for_line
from tower_consent import Store

from tests.privacy.patterns import AWS_KEY_ID, BEARER, phone_hits

from .fakes import Recorder, StoryAgent

pytestmark = pytest.mark.integration

TOKEN = "served-ui-token-under-test"


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
needs_browser = pytest.mark.skipif(_NO_BROWSER is not None, reason=_NO_BROWSER or "")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Served:
    url: str
    deps: Deps
    rec: Recorder
    audit_reads_per_tick: list[tuple[int, int]] = field(
        default_factory=list
    )  # (open pages, list_for_line calls)


class Noop:
    async def set_mom_grant(self, action: Any) -> None:
        return None

    async def save_transplant(self) -> None:
        return None

    async def reset(self) -> None:
        return None


@pytest.fixture
def serve(
    store: Store, lines: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Served]]:
    import uvicorn

    servers: list[tuple[Any, threading.Thread]] = []

    def start(**settings: Any) -> Served:
        rec = Recorder()
        rec.sent = [{"n": 1, "at": "2026-10-05T14:13:00+00:00", "template": "SIM_SWAPPED_RECENT.sms",
                     "role": "watcher", "user_id": "user-asish", "body": "mom's SIM moved at 14:13"}]  # fmt: skip
        rec.grants = [{"line_id": lines["user-mom"], "grantee_user_id": "user-asish", "grant": "watch",
                       "alias": "mom"}]  # fmt: skip
        s = Settings(mock_admin_token="mt", alerts_bearer="ab", feed_poll_s=0.2, **settings)
        transports = {"mock": rec.mock(), "binding": rec.binding(), "alerts": rec.alerts()}

        async def no_reset() -> None:
            return None

        deps = build_deps(s, transports=transports, store=store, reset=no_reset, mode="scripted")
        control = Control(
            mock=httpx.AsyncClient(transport=rec.mock(), base_url="http://mock.test"),
            grants=Noop(),
            settings=Noop(),
        )

        @asynccontextmanager
        async def agents() -> AsyncIterator[Callable[[str], Any]]:
            async def agent_for(user_id: str) -> StoryAgent:
                return StoryAgent()

            yield agent_for

        deps.runner.control = lambda: control
        deps.runner.agents = agents
        served = Served("", deps, rec)

        reads = [0]

        def spy(st: Store, line_id: str, viewer: str) -> Any:
            reads[0] += 1
            return list_for_line(st, line_id, viewer)

        monkeypatch.setattr(feed_mod, "list_for_line", spy)
        tick = deps.feed.tick

        async def counted_tick() -> int:
            before = reads[0]
            changed = await tick()
            served.audit_reads_per_tick.append((len(deps.feed.subscribers), reads[0] - before))
            return changed

        deps.feed.tick = counted_tick  # type: ignore[method-assign]
        port = _free_port()
        config = uvicorn.Config(
            create_app(deps), host="127.0.0.1", port=port, access_log=False, log_config=None,
            timeout_graceful_shutdown=2,
        )  # fmt: skip
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if time.monotonic() > deadline:  # pragma: no cover
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)  # waiting for the server thread, not for the code under test
        servers.append((server, thread))
        served.url = f"http://127.0.0.1:{port}"
        return served

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=15)


@needs_browser
def test_four_panes_a_macro_over_sse_and_the_phone_layout(serve: Callable[..., Served]) -> None:
    from playwright.sync_api import expect, sync_playwright

    served = serve()
    says = sum(1 for s in STORIES[0].steps if hasattr(s, "codes"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        desk = browser.new_page(viewport={"width": 1280, "height": 900})
        desk.goto(served.url)
        expect(desk.locator("#pill")).to_contain_text("scripted")
        for pane in ("conversation", "carrier", "feed", "binding"):
            expect(desk.locator(f"#pane-{pane}")).to_be_visible()
        expect(desk.locator("#carrier")).to_contain_text("Asish")  # pushed over SSE by the poller
        expect(desk.locator("#carrier")).to_contain_text("line:1111aaaa")
        expect(desk.locator("#audit")).to_contain_text("SUPPRESSED_REVOKED")
        expect(desk.locator("#sms")).to_contain_text("watcher")
        expect(desk.locator("#grants")).to_contain_text("resolve")
        expect(desk.locator(".macros button")).to_have_count(4)

        desk.get_by_role("button", name="Moment 1").click()
        expect(desk.locator("#run-status .status.pass")).to_contain_text("0 mismatched", timeout=20_000)
        expect(desk.locator("#log .badge.pass")).to_have_count(says)
        expect(desk.locator("#log .badge.fail")).to_have_count(0)
        expect(desk.locator("#log article.step")).to_have_count(len(STORIES[0].steps))
        expect(desk.locator("#log .ms").first).to_contain_text(" ms")
        assert phone_hits(desk.content()) == []

        second = browser.new_page()  # a second tab: still one poller
        second.goto(served.url)
        expect(second.locator("#audit")).to_contain_text("SUPPRESSED_REVOKED")
        deadline = time.monotonic() + 10
        while not any(pages == 2 for pages, _ in served.audit_reads_per_tick):
            assert time.monotonic() < deadline, served.audit_reads_per_tick
            desk.wait_for_timeout(100)
        assert {reads for _, reads in served.audit_reads_per_tick} == {2}  # Asish's line + Mom's, per tick
        second.close()

        phone = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        tab = phone.new_page()
        tab.goto(served.url)
        expect(tab.locator("#carrier")).to_contain_text("Asish")
        expect(tab.locator(".macros button")).to_have_count(4)
        widths = tab.evaluate(
            "() => ({doc: document.documentElement.scrollWidth, view: window.innerWidth,"
            " panes: [...document.querySelectorAll('.pane')].map(p => p.getBoundingClientRect().toJSON())})"
        )
        assert widths["doc"] <= widths["view"], widths  # no sideways scroll at phone size
        assert len(widths["panes"]) == 4
        assert all(p["right"] <= widths["view"] + 0.5 for p in widths["panes"]), widths
        assert len({round(p["left"]) for p in widths["panes"]}) == 1  # stacked: one column
        phone.close()
        browser.close()


def test_the_token_guards_the_sse_stream_over_http_and_the_log_is_clean(
    serve: Callable[..., Served], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    served = serve(ui_token=TOKEN)
    with httpx.Client(base_url=served.url, timeout=10) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/events").status_code == 401
        assert c.get("/events", headers={"Authorization": "Bearer nope"}).status_code == 401
        with c.stream("GET", "/events", params={"token": TOKEN}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            assert "httponly" in r.headers["set-cookie"].lower()
            first = next(r.iter_lines())
            assert first.startswith("event: ")  # the current fragments at once (§4)
        with c.stream("GET", "/events", headers={"Authorization": f"Bearer {TOKEN}"}) as r:
            assert r.status_code == 200
        r = c.post("/carrier/fire", data={"who": "mom", "event": "sim_swap"},
                   headers={"Authorization": f"Bearer {TOKEN}", "HX-Request": "true"})  # fmt: skip
        assert r.status_code == 200 and "sim_swap on mom" in r.text
    text = caplog.text
    assert phone_hits(text) == [] and not AWS_KEY_ID.search(text) and not BEARER.search(text)
    assert TOKEN not in text and "Bearer mt" not in text
    assert not [rec for rec in caplog.records if rec.name == "uvicorn.access"]  # access log off (§8.3)
    assert "GET /events" not in text and "/_admin/" not in text  # no request lines, no bodies
