"""The UI against the real neighbours, in-process (doc 11 §3, §8, §11): the mock carrier with `MOCK_ADMIN_TOKEN`
set and the demo scenario's numbers loaded, and the binding page's local admin over the same store. Every
request the UI sends and every response it reads is recorded (`Wire`).

- G1: the right token drives the mock; a wrong one greys the control with the reason, and nothing changes.
- G2 / §8.3: the UI sends no number and reads none (state by `?view=refs`, events by `ref`); no page, fragment or
  SSE frame carries a number, a health word or a key.
- §3 pane 4: Mom's revoke from the grants pane goes through the binding page; `resolve` and the feed flip; the
  re-grant flips them back.
- G3: what Alerts' sent list holds has no number field, and the SMS pane shows role and template.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from alerts.sent_log import SentLog
from binding_page.app import create_app as create_page
from binding_page.config import Settings as PageSettings
from binding_page.deps import Deps as PageDeps
from camara_client import BreakerRegistry, CarrierConfig, DirectClient, OAuthConfig
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from demo_ui.feed import Frame
from mock_carrier.app import create_app as create_mock
from mock_carrier.settings import Settings as MockSettings
from mock_carrier.testing import ASISH, BASE, MOM
from tower_audit import HmacMarkerSigner
from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, Store, grant, resolve

from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

from .conftest import T0
from .refstack import Wire

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true"}
TOKEN = "mock-admin-token-under-test"
PAGE = "http://binding.test"
ALERTS = "http://alerts.test"
SENT_FIELDS = {"n", "at", "template", "role", "user_id", "body"}


@dataclass
class Rig:
    deps: Deps
    ui: httpx.AsyncClient
    mock: httpx.AsyncClient  # straight to the mock, for the test's own checks (not the UI's traffic)
    mock_wire: Wire
    page_wire: Wire
    alerts_wire: Wire
    sent: SentLog
    frames: list[Frame]

    def drain(self) -> list[Frame]:
        q = next(iter(self.deps.feed.subscribers))
        while not q.empty():
            self.frames.append(q.get_nowait())
        return self.frames

    def wires(self) -> list[Wire]:
        return [self.mock_wire, self.page_wire, self.alerts_wire]


def binding_page(store: Store) -> Any:
    cfg = CarrierConfig(
        client="direct",
        backend="mock",
        base_url=BASE,
        oauth=OAuthConfig(
            token_url=f"{BASE}/oauth2/token",
            authorize_url=f"{BASE}/oauth2/authorize",
            client_id="binding-page",
            secret_ref="env:CARRIER_CLIENT_SECRET",
            scopes=("number-verification",),
        ),
        profile="proactive",
    )
    unused = httpx.MockTransport(lambda r: httpx.Response(599))  # the admin routes never reach the carrier
    deps = PageDeps(
        settings=PageSettings(
            session_secret="test-session-secret", base_url=PAGE, tower_env="local", admin=True
        ),
        store=store,
        hasher=LocalLineIdHasher(b"k" * 32),
        cipher=LocalMsisdnCipher(b"m" * 32),
        carrier_config=cfg,
        carrier=DirectClient(cfg, secret="unused", transport=unused, breakers=BreakerRegistry()),
        audit_signer=HmacMarkerSigner(b"k" * 32),
        carrier_http=httpx.AsyncClient(transport=unused),
        now=lambda: T0 + timedelta(hours=1),
    )
    return create_page(deps)


def alerts_transport(sent: SentLog) -> httpx.MockTransport:
    """`GET /internal/sent?after=n` as `alerts/internal_api.py` serves it: `{"sent": [entry.dump(), …]}`."""

    def handle(r: httpx.Request) -> httpx.Response:
        if r.headers.get("authorization") != "Bearer ab":
            return httpx.Response(401)
        after = int(r.url.params.get("after", "0"))
        return httpx.Response(200, json={"sent": [e.dump() for e in sent.after(after)]})

    return httpx.MockTransport(handle)


async def rig_for(store: Store, mock_token: str | None) -> tuple[Rig, Any]:
    mock_app = create_mock(MockSettings(admin=True, admin_token=TOKEN, base_url=BASE, webhook_backoff_s=0.0))
    mock_wire = Wire(httpx.ASGITransport(app=mock_app))
    page_wire = Wire(httpx.ASGITransport(app=binding_page(store)))
    sent = SentLog()
    alerts_wire = Wire(alerts_transport(sent))
    settings = Settings(
        mock_url=BASE, mock_admin_token=mock_token, binding_url=PAGE, alerts_url=ALERTS, alerts_bearer="ab"
    )

    async def no_reset() -> None:
        return None

    deps = build_deps(
        settings,
        transports={"mock": mock_wire, "binding": page_wire, "alerts": alerts_wire},
        store=store,
        reset=no_reset,
        mode="scripted",
    )
    deps.feed.subscribe()
    ui = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)), base_url="http://ui.test")
    mock = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url=BASE)
    return Rig(deps, ui, mock, mock_wire, page_wire, alerts_wire, sent, []), mock_app


@pytest.fixture
async def rig(store: Store, lines: dict[str, str]) -> AsyncIterator[Rig]:
    grant(store, lines["user-mom"], "user-asish", "watch", "mom", granted_by="user-mom", now=T0)
    r, _ = await rig_for(store, TOKEN)
    try:
        yield r
    finally:
        await r.ui.aclose()
        await r.mock.aclose()
        await r.deps.aclose()


async def mom_state(mock: httpx.AsyncClient) -> dict[str, Any]:
    lines = (await mock.get("/_admin/state")).json()["lines"]  # the full view: this test may see the number
    return dict(lines[MOM])


def visible(html: str) -> str:
    """The words a person reads: markup and attribute values (an SVG's `stroke=`) are not text."""
    return re.sub(r"<[^>]*>", " ", html)


def assert_clean(text: str, where: str) -> None:
    assert phone_hits(text) == [], where
    assert ASISH not in text and MOM not in text and ASISH[2:] not in text, where
    assert not HEALTH_WORDS.search(visible(text)), where
    assert not AWS_KEY_ID.search(text) and not BEARER.search(text), where
    assert TOKEN not in text, where


async def test_the_admin_token_drives_the_real_mock_by_ref(rig: Rig) -> None:
    before = await mom_state(rig.mock)
    r = await rig.ui.post("/carrier/fire", data={"who": "mom", "event": "sim_swap"}, headers=HX)
    assert r.status_code == 200 and "sim_swap on mom" in r.text
    assert (await mom_state(rig.mock))["sim_change_at"] != before["sim_change_at"]
    r = await rig.ui.post("/carrier/advance", data={"minutes": 12}, headers=HX)
    assert "clock +12 min" in r.text and "fired: sim_swap" in r.text  # demo.yaml's +12 swap on Asish's line
    assert "fault 500" in (await rig.ui.post("/carrier/fault", data={"kind": "500", "n": 2}, headers=HX)).text
    assert (await rig.mock.get("/_admin/state")).json()["faults"]
    assert "cleared" in (await rig.ui.post("/carrier/faults/clear", headers=HX)).text
    assert (await rig.mock.get("/_admin/state")).json()["faults"] == []
    posts = [x for x in rig.mock_wire.log if x.method != "GET"]
    assert posts and all(x.headers["authorization"] == f"Bearer {TOKEN}" for x in posts)
    assert all(x.status == 200 for x in rig.mock_wire.log)


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_without_the_right_token_the_control_is_greyed_and_nothing_changes(
    store: Store, lines: dict[str, str], token: str | None
) -> None:
    r_, _ = await rig_for(store, token)
    try:
        before = await mom_state(r_.mock)
        r = await r_.ui.post("/carrier/fire", data={"who": "mom", "event": "sim_swap"}, headers=HX)
        assert 'class="status fail"' in r.text and "401" in r.text
        assert (await mom_state(r_.mock)) == before
        r = await r_.ui.post("/carrier/advance", data={"minutes": 5}, headers=HX)
        assert "401" in r.text
        await r_.deps.feed.tick()
        assert "Mom" in r_.deps.feed.last["carrier"]  # GET state stays open (Tower and Alerts read the clock)
        assert {x.status for x in r_.mock_wire.log if x.method == "POST"} == {401}
        assert TOKEN not in r.text
    finally:
        await r_.ui.aclose()
        await r_.mock.aclose()
        await r_.deps.aclose()


async def test_revoke_from_the_grants_pane_flips_resolve_and_the_feed(rig: Rig, store: Store) -> None:
    await rig.deps.feed.tick()
    assert "grant=<b>watch</b>" in rig.deps.feed.last["grants"]
    assert ' class="revoked"' not in rig.deps.feed.last["grants"]

    r = await rig.ui.post("/binding/grant", data={"action": "revoke"}, headers=HX)
    assert "Mom: revoke Asish&#39;s watch" in r.text  # autoescaped
    assert resolve(store, "user-asish", "mom").view.grant == "none"  # the consent store itself
    await rig.deps.feed.tick()
    grants = rig.deps.feed.last["grants"]
    assert "grant=<b>none</b>" in grants and ' class="revoked"' in grants
    assert any(f.event == "grants" and "grant=<b>none</b>" in f.html for f in rig.drain())  # pushed over SSE

    await rig.ui.post("/binding/grant", data={"action": "grant"}, headers=HX)
    assert resolve(store, "user-asish", "mom").view.grant == "watch"
    await rig.deps.feed.tick()
    assert "grant=<b>watch</b>" in rig.deps.feed.last["grants"]
    page_calls = [(x.method, httpx.URL(x.url).path) for x in rig.page_wire.log]
    assert page_calls.count(("POST", "/_admin/grants")) == 2  # consent is written by the page, not the UI
    reads = [x for x in rig.page_wire.log if httpx.URL(x.url).path == "/_admin/tables"]
    assert reads and all(httpx.URL(x.url).params.get("table") == "Grants" for x in reads)
    assert all(x.status == 200 and "Audit" not in x.response for x in reads)  # pane 4 never reads Audit rows


async def test_nothing_the_ui_shows_sends_or_reads_carries_a_number(rig: Rig) -> None:
    rig.sent.record(at=T0, template="SIM_SWAPPED_RECENT.sms", role="watcher", user_id="user-asish",
                    body="mom's SIM moved to another device at 14:13. Reply OK if you've checked.")  # fmt: skip
    rig.sent.record(at=T0, template="UNREACHABLE.sms", role="escalation[0]", user_id="user-partner",
                    body="asish's phone has been off the network for 20 minutes.")  # fmt: skip
    pages = [await rig.ui.get(p) for p in ("/", "/fragment/pill", "/fragment/conversation-controls",
                                           "/fragment/carrier-controls", "/healthz")]  # fmt: skip
    posts = [
        await rig.ui.post("/carrier/fire", data={"who": "asish", "event": "cf_set"}, headers=HX),
        await rig.ui.post("/carrier/fire", data={"who": "mom", "event": "unreachable"}, headers=HX),
        await rig.ui.post("/carrier/advance", data={"minutes": 20}, headers=HX),
        await rig.ui.post("/carrier/fault", data={"kind": "timeout"}, headers=HX),
        await rig.ui.post("/carrier/faults/clear", headers=HX),
        await rig.ui.post("/binding/bind-link", data={"who": "asish"}, headers=HX),
        await rig.ui.post("/binding/bind-link", data={"who": "mom"}, headers=HX),
        await rig.ui.post("/binding/grant", data={"action": "revoke"}, headers=HX),
        await rig.ui.post("/binding/grant", data={"action": "grant"}, headers=HX),
    ]
    assert all(r.status_code == 200 for r in pages + posts)
    await rig.deps.feed.tick()
    frames = rig.drain()
    assert {"carrier", "audit", "sms", "grants"} <= {f.event for f in frames}
    assert "escalation[0]" in rig.deps.feed.last["sms"] and "watcher" in rig.deps.feed.last["sms"]
    for r in pages + posts:
        assert_clean(r.text, f"{r.request.method} {r.request.url.path}")
    for f in frames:
        assert_clean(f.sse(), f"SSE {f.event}")

    for wire in rig.wires():
        assert wire.log
        for x in wire.log:  # both directions, every neighbour
            assert phone_hits(x.url + x.body) == [], x.text()
            assert phone_hits(x.response) == [], x.text()
    for x in (
        rig.mock_wire.log + rig.alerts_wire.log
    ):  # G2, G3: not even the field (the page's `msisdn_enc` is
        assert "msisdn" not in x.response and "to_e164" not in x.response, x.text()  # ciphertext, 04 §8)
    mock_paths = [httpx.URL(x.url) for x in rig.mock_wire.log]
    assert all(u.path.startswith("/_admin/") for u in mock_paths)
    assert all(u.params.get("view") == "refs" for u in mock_paths if u.path == "/_admin/state")
    sent = [e for x in rig.alerts_wire.log for e in httpx.Response(200, content=x.response).json()["sent"]]
    assert sent and all(set(e) == SENT_FIELDS for e in sent)  # G3: no number field to leak
