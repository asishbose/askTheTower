"""The app over ASGI with fake neighbours (doc 11 §3, §8, §9, §11): health, the guards, the carrier controls by
`ref` with the admin token, ENV=aws degradation, the audit read as owners, and no number anywhere on the wire or
on the page."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from demo_ui import feed as feed_mod
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import build_deps
from demo_ui.feed import AuditSource
from tower_audit import AuditAccessDenied, list_for_line
from tower_consent import Store

from tests.privacy.patterns import phone_hits

from .fakes import ASISH_REF, MOM_REF, Recorder, dump

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true"}


async def no_reset() -> None:
    return None


def build(store: Store, rec: Recorder, **settings: Any) -> Any:
    s = Settings(mock_admin_token="mt", alerts_bearer="ab", **settings)
    transports = {"mock": rec.mock(), "binding": rec.binding(), "alerts": rec.alerts()}
    return build_deps(s, transports=transports, store=store, reset=no_reset, mode="scripted")


@pytest.fixture
async def client(
    store: Store, lines: dict[str, str]
) -> AsyncIterator[tuple[httpx.AsyncClient, Recorder, Any]]:
    rec = Recorder()
    deps = build(store, rec)
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        yield c, rec, deps


async def test_health_reports_panes_not_settings(client: Any) -> None:
    c, _, _ = client
    body = (await c.get("/healthz")).json()
    assert body["mode"] == "scripted" and body["env"] == "local" and body["panes"]["carrier"] is True
    assert "mt" not in str(body) and "ab" not in str(body)


async def test_page_and_static_assets(client: Any) -> None:
    c, _, _ = client
    page = (await c.get("/")).text
    assert 'sse-connect="/events"' in page and "/static/htmx.min.js" in page
    assert (await c.get("/static/htmx.min.js")).status_code == 200
    assert "scripted" in (await c.get("/fragment/pill")).text
    controls = (await c.get("/fragment/conversation-controls")).text
    assert "Moment 1" in controls and "Transplant" in controls and "free text needs Bedrock" in controls


async def test_post_needs_hx_request(client: Any) -> None:
    c, _, _ = client
    assert (await c.post("/carrier/advance", data={"minutes": 12})).status_code == 403


async def test_carrier_controls_use_ref_and_token(client: Any) -> None:
    c, rec, _ = client
    r = await c.post("/carrier/fire", data={"who": "mom", "event": "sim_swap"}, headers=HX)
    assert r.status_code == 200 and "sim_swap on mom" in r.text
    r = await c.post("/carrier/advance", data={"minutes": 12}, headers=HX)
    assert "fired: sim_swap" in r.text
    await c.post("/carrier/fault", data={"kind": "500"}, headers=HX)
    await c.post("/carrier/faults/clear", headers=HX)
    mock_calls = [q for q in rec.requests if q.url.host == "localhost" and q.url.port == 8443]
    assert any(q.url.path == f"/_admin/lines/{MOM_REF}/events" for q in mock_calls)
    assert all(q.headers["authorization"] == "Bearer mt" for q in mock_calls)
    assert all(q.url.path.startswith("/_admin/") for q in mock_calls)  # never a CAMARA path (§8.2)
    assert phone_hits(dump(rec.requests)) == []
    assert ASISH_REF not in dump([q for q in mock_calls if q.url.path.endswith("/events")])


async def test_mock_401_greys_the_control_with_the_reason(client: Any) -> None:
    c, rec, _ = client
    rec.status = 401
    r = await c.post("/carrier/advance", data={"minutes": 1}, headers=HX)
    assert "401" in r.text and "fail" in r.text


async def test_feed_tick_renders_every_pane_without_a_number(client: Any) -> None:
    c, rec, deps = client
    rec.sent = [{"n": 1, "at": "2026-10-05T14:13:00+00:00", "template": "SIM_SWAPPED_RECENT.sms",
                 "role": "watcher", "user_id": "user-asish", "body": "mom's SIM moved to another device at 10:13"}]  # fmt: skip
    rec.grants = [
        {"line_id": "ln_abcdefgh", "grantee_user_id": "user-asish", "grant": "watch", "alias": "mom"}
    ]
    deps.feed.subscribe()
    await deps.feed.tick()
    panes = deps.feed.last
    assert set(panes) == {"carrier", "audit", "sms", "grants"}
    assert "Asish" in panes["carrier"] and "SUPPRESSED_REVOKED" in panes["audit"]
    assert "Asish" in panes["audit"] and "Mom" in panes["audit"]
    assert "watcher" in panes["sms"] and "resolve" in panes["grants"]
    assert all(phone_hits(html) == [] for html in panes.values())
    alerts_calls = [q for q in rec.requests if q.url.path == "/internal/sent"]
    assert alerts_calls and alerts_calls[0].headers["authorization"] == "Bearer ab"
    tables_calls = [q for q in rec.requests if q.url.path == "/_admin/tables"]
    assert tables_calls and all(
        q.url.params.get("table") == "Grants" for q in tables_calls
    )  # pane 4: Grants only


async def test_binding_qr_and_grant(client: Any) -> None:
    c, rec, _ = client
    r = await c.post("/binding/bind-link", data={"who": "asish"}, headers=HX)
    assert "<svg" in r.text and "as=phone-asish" in r.text
    r = await c.post("/binding/grant", data={"action": "revoke"}, headers=HX)
    assert "revoke" in r.text
    body = next(q for q in rec.requests if q.url.path == "/_admin/grants").content.decode()
    assert '"action":"revoke"' in body.replace(" ", "") and '"owner_user_id":"user-mom"' in body.replace(
        " ", ""
    )


async def test_macro_route_starts_and_refuses_a_second(client: Any) -> None:
    c, _, deps = client
    import asyncio

    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    deps.runner.reset = held
    r = await c.post("/macro/moment-1", headers=HX)
    assert "started" in r.text
    r = await c.post("/macro/moment-2", headers=HX)
    assert r.status_code == 409
    assert (await c.post("/macro/nope", headers=HX)).status_code == 404
    deps.runner.task.cancel()


async def test_say_is_off_in_scripted_mode(client: Any) -> None:
    c, _, _ = client
    r = await c.post("/say", data={"who": "asish", "text": "is my line ok"}, headers=HX)
    assert "free text is off" in r.text


async def test_token_guard(store: Store, lines: dict[str, str]) -> None:
    rec = Recorder()
    deps = build(store, rec, host="0.0.0.0", ui_token="secret-ui")  # noqa: S104 - the LAN case under test
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/")).status_code == 401
        assert (await c.get("/events")).status_code == 401
        r = await c.get("/", params={"token": "secret-ui"})
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
        assert (await c.get("/fragment/pill")).status_code == 200  # the cookie now
        c.cookies.clear()
        assert (
            await c.get("/fragment/pill", headers={"Authorization": "Bearer secret-ui"})
        ).status_code == 200
        assert (await c.get("/fragment/pill", params={"token": "wrong"})).status_code == 401


async def test_env_aws_turns_off_carrier_and_calls_no_empty_url(store: Store, lines: dict[str, str]) -> None:
    rec = Recorder()
    deps = build(store, rec, env="aws", mock_url="", alerts_url="")
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        assert "local-only" in (await c.post("/macro/moment-1", headers=HX)).text
        assert "local-only" in (await c.post("/carrier/advance", data={"minutes": 1}, headers=HX)).text
        assert "local-only" in (await c.get("/fragment/carrier-controls")).text
        controls = (await c.get("/fragment/conversation-controls")).text
        assert 'value="mom"' not in controls  # Asish only (decision 3)
        r = await c.post("/binding/bind-link", data={"who": "asish"}, headers=HX)
        assert "next_step" in r.text
        deps.feed.subscribe()
        await deps.feed.tick()
    assert rec.requests == []  # nothing to an empty URL, nothing to the off-local admin surfaces
    assert "audit" in deps.feed.last and "local-only" in deps.feed.last["carrier"]


def test_audit_is_read_only_as_each_owner(
    store: Store, lines: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    def spy(s: Store, line_id: str, viewer: str) -> Any:
        seen.append((line_id, viewer))
        return list_for_line(s, line_id, viewer)

    monkeypatch.setattr(feed_mod, "list_for_line", spy)
    rows = AuditSource(store).rows()
    assert sorted(seen) == sorted((line, owner) for owner, line in lines.items())
    assert [r["log"] for r in rows].count("Mom") == 2
    with pytest.raises(AuditAccessDenied):  # what the feed never does: read Mom's line as her watcher
        list_for_line(store, lines["user-mom"], "user-asish")


def test_audit_lines_are_cached(store: Store, lines: dict[str, str]) -> None:
    now = [0.0]
    src = AuditSource(store, monotonic=lambda: now[0])
    src.rows()
    first = src._lines_at
    now[0] = 10.0
    src.rows()
    assert src._lines_at == first
    now[0] = 31.0
    src.rows()
    assert src._lines_at == 31.0


# --- step 3: every POST guarded, the token on every route but /healthz, ENV=aws and binding 404 ------------------

POSTS: list[tuple[str, dict[str, Any]]] = [
    ("/say", {"who": "asish", "text": "Is my line OK?"}),
    ("/macro/moment-1", {}),
    ("/carrier/reset", {}),
    ("/carrier/advance", {"minutes": 12}),
    ("/carrier/fire", {"who": "mom", "event": "sim_swap"}),
    ("/carrier/fault", {"kind": "500", "n": 1}),
    ("/carrier/faults/clear", {}),
    ("/binding/bind-link", {"who": "asish"}),
    ("/binding/grant", {"action": "revoke"}),
]


async def test_every_post_without_hx_request_is_403_and_reaches_nothing(client: Any) -> None:
    """One app for all nine POST routes (a moto store per parametrised case costs seconds each on /mnt/c)."""
    c, rec, deps = client
    for path, form in POSTS:
        for headers in ({}, {"HX-Request": "false"}):
            r = await c.post(path, data=form, headers=headers)
            assert r.status_code == 403 and "HX-Request" in r.text, (path, headers)
    assert rec.requests == [] and deps.runner.task is None


async def test_token_guards_posts_and_the_sse_stream(store: Store, lines: dict[str, str]) -> None:
    rec = Recorder()
    deps = build(store, rec, host="0.0.0.0", ui_token="secret-ui")  # noqa: S104 - the LAN case under test
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        r = await c.post("/carrier/advance", data={"minutes": 1}, headers=HX)
        assert r.status_code == 401  # the token is checked before anything else
        r = await c.post(
            "/carrier/advance", data={"minutes": 1}, headers={"Authorization": "Bearer secret-ui"}
        )
        assert r.status_code == 403  # a token is not a CSRF guard: HX-Request still needed
        r = await c.post(
            "/carrier/advance", data={"minutes": 1}, headers={**HX, "Authorization": "Bearer secret-ui"}
        )
        assert r.status_code == 200 and "clock +1 min" in r.text
        assert (await c.get("/events", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/events", cookies={"demo_ui_token": "wrong"})).status_code == 401
        assert "secret-ui" not in (await c.get("/healthz")).text
    assert all(q.url.path != "/_admin/clock" or q.method == "POST" for q in rec.requests)
    assert sum(q.url.path == "/_admin/clock" for q in rec.requests) == 1  # only the authorised call went out


async def test_env_aws_switches_off_reset_and_macros_everywhere(store: Store, lines: dict[str, str]) -> None:
    from demo_ui.deps import ResetUnavailable

    rec = Recorder()
    deps = build(store, rec, env="aws", mock_url="", alerts_url="")
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        health = (await c.get("/healthz")).json()
        assert health["env"] == "aws" and health["panes"] == {
            "conversation": True, "carrier": False, "feed_sms": False, "binding_admin": False}  # fmt: skip
        controls = (await c.get("/fragment/conversation-controls")).text
        assert controls.count("disabled title=") == 4 and "decision 2" in controls  # four macro buttons, why
        assert "local-only" in (await c.post("/carrier/reset", headers=HX)).text
        r = await c.post("/binding/grant", data={"action": "revoke"}, headers=HX)
        assert "on the resident&#39;s own phone at /me" in r.text
        for event in ("sim_swap", "cf_set", "cf_clear", "unreachable", "reachable"):
            r = await c.post("/carrier/fire", data={"who": "asish", "event": event}, headers=HX)
            assert "local-only" in r.text
        deps.feed.subscribe()
        await deps.feed.tick()
    with pytest.raises(ResetUnavailable, match="local-only"):
        deps.runner.control()
    assert deps.mock is None and deps.binding is None and deps.runner.task is None
    assert "the SMS goes to the real phone" in deps.feed.last["sms"]
    assert "next_step" in deps.feed.last["grants"]
    assert rec.requests == []


async def test_binding_admin_404_degrades_pane_4_to_the_next_step_qr(client: Any) -> None:
    c, rec, deps = client
    rec.status = 404  # BIND_ADMIN off: every /_admin/* on the page is 404 (04 §8)
    deps.feed.subscribe()
    await deps.feed.tick()
    assert (
        "404" in deps.feed.last["grants"] and "QR code from next_step still works" in deps.feed.last["grants"]
    )
    r = await c.post("/binding/grant", data={"action": "revoke"}, headers=HX)
    assert 'class="status fail"' in r.text and "404" in r.text
    r = await c.post("/binding/bind-link", data={"who": "mom"}, headers=HX)
    assert "404" in r.text and "<svg" not in r.text
    assert "SMS list unavailable" in deps.feed.last["sms"]  # Alerts 404 too: the audit goes on
    assert "SUPPRESSED_REVOKED" in deps.feed.last["audit"]


async def test_carrier_controls_wait_for_a_running_macro(client: Any) -> None:
    import asyncio

    c, rec, deps = client
    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    deps.runner.reset = held
    await c.post("/macro/moment-1", headers=HX)
    n = len(rec.requests)
    for path, form in POSTS[2:7]:
        r = await c.post(path, data=form, headers=HX)
        assert "a macro is running" in r.text, path
    assert len(rec.requests) == n  # nothing reached the mock while the macro holds the timeline
    deps.runner.task.cancel()
