"""D8/D9 (code-vs-docs.md) through the binding page: the Watching card and `POST /me/lines/{line_id}/watch-settings`
(04 §9.2–9.5), and the local-only `POST /_admin/watch-settings`.

Acceptance criteria 06 §11.5 1–8, page half. Specified, not yet built: these are expected to fail until the
routes exist.

The contacts here are grantees created directly in the store (`tower_consent.grant` as the line-holder): only
Asish and Mom bind through the one-tap flow, so these tests do not depend on the demo scenario's new lines. The
partner and the neighbour binding their own phones end to end is criterion 24 (tests/e2e/test_d9_transplant.py).

The page → Alerts call (`POST {ALERTS}/internal/watch`) has no seam in the binding page yet. `alerts_spy`
intercepts any httpx request to a path ending in `/internal/watch` and, if the page grows a `deps.alerts`
recorder like Tower's `RecordingAlerts`, reads that too.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from botocore.exceptions import EndpointConnectionError
from tower_audit.reader import query_rows
from tower_consent import (
    EscalationStep,
    LastState,
    Watch,
    get_watch,
    grant,
    list_lines,
    revoke,
    tables,
    upsert_watch,
)

from tests.helpers.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.integration

OWNER = "user-asish"
PARTNER = "user-partner"
NEIGHBOUR = "user-neighbour"
COUSIN = "user-cousin"  # reachability only
EX = "user-ex"  # watch, revoked
STRANGER = "user-stranger"  # no grant

LABEL_SELF = "Fraud only: SIM swap or call forwarding"
LABEL_TRANSPLANT = "Must stay reachable: text if off the network 20 minutes, any hour"
LABEL_CARE = "Daytime check-in: text if off the network 4 hours, 8 am–10 pm"
SAY_ALEXA = "Say 'Alexa, watch my line' to turn alerts on or off."
ESCALATION_NOTE = "If the first person doesn't reply OK within 15 minutes, the next one is texted."
MSG_NOT_OWNER = "Only the line-holder can change how this line is watched."
MSG_CONTACTS = "Contacts must be people you've shared this line with (watch)."
MSG_NO_CONTACT = "Pick at least one person to text."
SKIPPED = "no longer shared — skipped"


# --- the Alerts seam ---------------------------------------------------------------------------------------


@dataclass
class AlertsSpy:
    calls: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    def answer(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("alerts unavailable (test)", request=request)
        self.calls.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"ok": True}, request=request)


@pytest.fixture
def alerts_spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[AlertsSpy]:
    spy = AlertsSpy()
    monkeypatch.setenv("ALERTS_INTERNAL_URL", "http://alerts.test")
    monkeypatch.setenv("ALERTS_INTERNAL_BEARER", "test-internal-bearer")
    orig_async, orig_sync = httpx.AsyncClient.send, httpx.Client.send

    async def send_async(
        self: httpx.AsyncClient, request: httpx.Request, *a: Any, **kw: Any
    ) -> httpx.Response:
        if request.url.path.endswith("/internal/watch"):
            return spy.answer(request)
        return await orig_async(self, request, *a, **kw)

    def send_sync(self: httpx.Client, request: httpx.Request, *a: Any, **kw: Any) -> httpx.Response:
        if request.url.path.endswith("/internal/watch"):
            return spy.answer(request)
        return orig_sync(self, request, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send_async)
    monkeypatch.setattr(httpx.Client, "send", send_sync)
    yield spy


def alerts_calls(page: Any, spy: AlertsSpy) -> list[dict[str, Any]]:
    recorder = getattr(page.deps, "alerts", None)
    return [*spy.calls, *list(getattr(recorder, "calls", []))]


def alerts_down(page: Any, spy: AlertsSpy) -> None:
    spy.down = True
    recorder = getattr(page.deps, "alerts", None)
    if recorder is not None and hasattr(recorder, "fail"):
        recorder.fail = True


# --- setup -------------------------------------------------------------------------------------------------


@dataclass
class Setup:
    page: Any
    asish: httpx.AsyncClient
    mom: httpx.AsyncClient
    line: str  # Asish's line_id


@pytest.fixture
async def setup(page: Any, h: Any, alerts_spy: AlertsSpy) -> Any:
    asish, r = await h.bind(page, OWNER, "phone-asish")
    assert r.status_code == 200 and "Line connected" in r.text
    mom, r = await h.bind(page, "user-mom", "phone-mom")
    assert r.status_code == 200 and "Line connected" in r.text
    line = list_lines(page.store, OWNER)[0].line_id
    now = page.clock()
    for grantee in (PARTNER, NEIGHBOUR, EX):
        grant(page.store, line, grantee, "watch", "asish", granted_by=OWNER, now=now)
    grant(page.store, line, COUSIN, "reachability", "asish", granted_by=OWNER, now=now)
    revoke(page.store, line, EX, "watch", revoked_by=OWNER, now=now + timedelta(seconds=1))
    yield Setup(page=page, asish=asish, mom=mom, line=line)
    await asish.aclose()
    await mom.aclose()


async def post_settings(
    h: Any,
    browser: httpx.AsyncClient,
    line: str,
    profile: str,
    contacts: list[str],
    *,
    csrf: str | None = None,
) -> httpx.Response:
    data = {"profile": profile, "csrf": csrf if csrf is not None else await h.csrf_of(browser)}
    for i in range(3):
        data[f"contact_{i + 1}"] = contacts[i] if i < len(contacts) else ""
    return await browser.post(f"/me/lines/{line}/watch-settings", data=data, follow_redirects=False)


def snapshot(store: Any) -> str:
    dump = {
        t.name: sorted(store.scan_all(t), key=lambda i: json.dumps(i, sort_keys=True, default=str))
        for t in tables.TABLES
        if t.name
        != "BindTokens"  # csrf_of() does not touch it, but a bind link might; nothing here creates one
    }
    return json.dumps(dump, sort_keys=True, default=str)


def chain(w: Watch | None) -> list[tuple[str, bool]]:
    assert w is not None
    return [(s.user_id, s.requires_ack) for s in w.escalation]


def text(r: httpx.Response) -> str:
    return html.unescape(r.text)


# --- criterion 1 -------------------------------------------------------------------------------------------


async def test_c1_save_writes_the_owner_watch_and_one_audit_row(
    setup: Setup, h: Any, alerts_spy: AlertsSpy
) -> None:
    s = setup
    before = query_rows(s.page.store, s.line)
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR])
    assert r.status_code == 303 and r.headers["location"].endswith("/me"), r.text[:500]

    w = get_watch(s.page.store, s.line, OWNER)
    assert w is not None and w.profile == "transplant" and w.enabled is False
    assert chain(w) == [(PARTNER, True), (NEIGHBOUR, False)]

    rows = query_rows(s.page.store, s.line)
    new = rows[len(before) :]
    assert len(new) == 1, [(x.tool, x.trigger, x.outcome) for x in new]
    row = new[0]
    assert (row.tool, row.trigger, row.outcome, row.actor_user_id) == ("watch_line", "binding", "ok", OWNER)
    assert [c.value for c in row.reason_codes] == ["OK"]
    dumped = json.dumps(row.model_dump(mode="json"))
    assert PARTNER not in dumped and NEIGHBOUR not in dumped  # no contact ids in the row
    assert (
        alerts_calls(s.page, alerts_spy) == []
    )  # criterion 4, last bullet: a disabled Watch → no Alerts call


async def test_c1_blank_contact_slots_are_dropped(setup: Setup, h: Any) -> None:
    s = setup
    data = {
        "profile": "care",
        "contact_1": "",
        "contact_2": NEIGHBOUR,
        "contact_3": "",
        "csrf": await h.csrf_of(s.asish),
    }
    r = await s.asish.post(f"/me/lines/{s.line}/watch-settings", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert chain(get_watch(s.page.store, s.line, OWNER)) == [(NEIGHBOUR, False)]


# --- criterion 2 -------------------------------------------------------------------------------------------


async def test_c2_non_owner_is_403_and_nothing_is_written(setup: Setup, h: Any) -> None:
    s = setup
    before = snapshot(s.page.store)
    r = await post_settings(h, s.mom, s.line, "transplant", [PARTNER])
    assert r.status_code == 403
    assert MSG_NOT_OWNER in text(r)
    assert snapshot(s.page.store) == before


@pytest.mark.parametrize(
    ("profile", "contacts", "message"),
    [
        ("transplant", [COUSIN], MSG_CONTACTS),
        ("transplant", [EX], MSG_CONTACTS),
        ("transplant", [STRANGER], MSG_CONTACTS),
        ("transplant", [OWNER], MSG_CONTACTS),
        ("transplant", [PARTNER, PARTNER], MSG_CONTACTS),
        ("transplant", [], MSG_NO_CONTACT),
        ("care", [], MSG_NO_CONTACT),
        ("panic", [PARTNER], None),
    ],
)
async def test_c2_refusals_are_422_and_write_nothing(
    setup: Setup, h: Any, profile: str, contacts: list[str], message: str | None
) -> None:
    s = setup
    before = snapshot(s.page.store)
    r = await post_settings(h, s.asish, s.line, profile, contacts)
    assert r.status_code == 422, r.status_code
    if message is not None:
        assert message in text(r)
    assert snapshot(s.page.store) == before  # no Watch, no audit row


async def test_c2_four_contacts_is_422(setup: Setup, h: Any) -> None:
    s = setup
    now = s.page.clock()
    for u in ("user-aunt", "user-uncle"):
        grant(s.page.store, s.line, u, "watch", "asish", granted_by=OWNER, now=now)
    before = snapshot(s.page.store)
    data = {
        "profile": "care",
        "contact_1": PARTNER,
        "contact_2": NEIGHBOUR,
        "contact_3": "user-aunt",
        "contact_4": "user-uncle",
        "csrf": await h.csrf_of(s.asish),
    }
    r = await s.asish.post(f"/me/lines/{s.line}/watch-settings", data=data, follow_redirects=False)
    assert r.status_code == 422
    assert snapshot(s.page.store) == before


# --- criterion 3 -------------------------------------------------------------------------------------------


async def test_c3_self_with_no_contacts_is_accepted(setup: Setup, h: Any) -> None:
    s = setup
    r = await post_settings(h, s.asish, s.line, "self", [])
    assert r.status_code == 303
    w = get_watch(s.page.store, s.line, OWNER)
    assert w is not None and w.profile == "self" and w.escalation == []


# --- criterion 4 -------------------------------------------------------------------------------------------


def _enabled_dark_watch(s: Setup) -> dict[str, Any]:
    now = s.page.clock()
    upsert_watch(
        s.page.store,
        Watch(
            line_id=s.line,
            watcher_user_id=OWNER,
            profile="self",
            enabled=True,
            escalation=[EscalationStep(user_id=OWNER)],
            last_state=LastState(reachable=False, unreachable_since=now - timedelta(minutes=5), at=now),
        ),
    )
    item = s.page.store.get(tables.WATCHES, {"line_id": s.line, "watcher_user_id": OWNER})
    assert item is not None
    return dict(item["last_state"])


async def test_c4_save_on_enabled_watch_resubscribes_and_keeps_state(
    setup: Setup, h: Any, alerts_spy: AlertsSpy
) -> None:
    s = setup
    before = _enabled_dark_watch(s)
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR])
    assert r.status_code == 303
    item = s.page.store.get(tables.WATCHES, {"line_id": s.line, "watcher_user_id": OWNER})
    assert item is not None and item["enabled"] is True
    assert json.dumps(item["last_state"], sort_keys=True) == json.dumps(before, sort_keys=True)
    calls = alerts_calls(s.page, alerts_spy)
    assert len(calls) == 1, calls
    assert calls[0]["enable"] is True and calls[0]["profile"] == "transplant"
    assert calls[0]["line_id"] == s.line and calls[0]["watcher_user_id"] == OWNER


async def test_c4_alerts_down_still_303_and_settings_kept(
    setup: Setup, h: Any, alerts_spy: AlertsSpy
) -> None:
    s = setup
    _enabled_dark_watch(s)
    alerts_down(s.page, alerts_spy)
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR])
    assert r.status_code == 303
    w = get_watch(s.page.store, s.line, OWNER)
    assert w is not None and w.profile == "transplant" and w.enabled is True
    assert chain(w) == [(PARTNER, True), (NEIGHBOUR, False)]


async def test_c4_save_on_disabled_watch_makes_no_alerts_call(
    setup: Setup, h: Any, alerts_spy: AlertsSpy
) -> None:
    s = setup
    upsert_watch(s.page.store, Watch(line_id=s.line, watcher_user_id=OWNER, profile="self", enabled=False))
    r = await post_settings(h, s.asish, s.line, "care", [NEIGHBOUR])
    assert r.status_code == 303
    assert alerts_calls(s.page, alerts_spy) == []


# --- 04 §9.5 failure table ---------------------------------------------------------------------------------


async def test_store_down_on_save_is_503_and_nothing_written(setup: Setup, h: Any) -> None:
    s = setup
    csrf = await h.csrf_of(s.asish)
    before = snapshot(s.page.store)

    def down(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb.test")

    s.page.store.client.meta.events.register("before-call.dynamodb", down)
    try:
        r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER], csrf=csrf)
    finally:
        s.page.store.client.meta.events.unregister("before-call.dynamodb", down)
    assert r.status_code == 503
    assert snapshot(s.page.store) == before


async def test_audit_failure_after_write_shows_error_and_settings_stand(setup: Setup, h: Any) -> None:
    s = setup
    csrf = await h.csrf_of(s.asish)

    def down(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb.test")

    event = "before-call.dynamodb.TransactWriteItems"  # the audit append (tower_audit.writer)
    s.page.store.client.meta.events.register(event, down)
    try:
        r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR], csrf=csrf)
    finally:
        s.page.store.client.meta.events.unregister(event, down)
    assert r.status_code >= 500 and r.status_code != 303  # "the page shows an error"
    w = get_watch(s.page.store, s.line, OWNER)
    assert w is not None and w.profile == "transplant"  # the settings stand
    assert chain(w) == [(PARTNER, True), (NEIGHBOUR, False)]


# --- criterion 5 -------------------------------------------------------------------------------------------

SELECT_RE = re.compile(r'<select[^>]*name="(contact_[123])"[^>]*>(.*?)</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"', re.S)


async def test_c5_me_shows_the_watching_card(setup: Setup, h: Any) -> None:
    s = setup
    r = await s.asish.get("/me")
    assert r.status_code == 200
    page = text(r)
    for label in (LABEL_SELF, LABEL_TRANSPLANT, LABEL_CARE, SAY_ALEXA, ESCALATION_NOTE):
        assert label in page, label
    assert re.search(r"Alerts:\s*(<[^>]+>\s*)*off", page), "Alerts: on/off (read-only) missing"
    assert f'action="/me/lines/{s.line}/watch-settings"' in r.text

    selects = dict(SELECT_RE.findall(r.text))
    assert set(selects) == {"contact_1", "contact_2", "contact_3"}
    for name, body in selects.items():
        offered = {v for v in OPTION_RE.findall(body) if v}
        assert offered == {PARTNER, NEIGHBOUR}, (name, offered)  # active watch grantees only

    assert "transplant" not in page.lower()
    assert not HEALTH_WORDS.search(page)
    assert not phone_hits(page)


async def test_c5_stale_contact_is_listed_as_skipped(setup: Setup) -> None:
    s = setup
    upsert_watch(
        s.page.store,
        Watch(
            line_id=s.line,
            watcher_user_id=OWNER,
            profile="care",
            enabled=False,
            escalation=[EscalationStep(user_id=EX, requires_ack=True), EscalationStep(user_id=NEIGHBOUR)],
        ),
    )
    page = text(await s.asish.get("/me"))
    assert SKIPPED in page


async def test_c5_no_session_401_no_csrf_403(setup: Setup, h: Any) -> None:
    s = setup
    async with s.page.browser() as anon:
        r = await anon.post(
            f"/me/lines/{s.line}/watch-settings",
            data={"profile": "self", "csrf": "x"},
            follow_redirects=False,
        )
        assert r.status_code == 401
    before = snapshot(s.page.store)
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER], csrf="")
    assert r.status_code == 403
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER], csrf="abcdefghijklmnop")
    assert r.status_code == 403
    assert snapshot(s.page.store) == before


# --- criterion 6 (page; the partner's own Watch also depends on D7) -----------------------------------------


async def test_c6_revoke_on_page_removes_the_contact_from_the_chain(setup: Setup, h: Any) -> None:
    s = setup
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR])
    assert r.status_code == 303
    w = get_watch(s.page.store, s.line, OWNER)
    assert w is not None
    upsert_watch(s.page.store, w.model_copy(update={"enabled": True}))  # "Alexa, watch my line"
    upsert_watch(s.page.store, Watch(line_id=s.line, watcher_user_id=PARTNER, profile="care", enabled=True))

    me = (await s.asish.get("/me")).text
    ids = re.findall(r'action="/grants/([^"]+)/revoke"', me)
    (gid,) = [i for i in ids if unquote(i).endswith(f":watch:{PARTNER}")]
    r = await s.asish.post(f"/grants/{gid}/revoke", data={"csrf": await h.csrf_of(s.asish)})
    assert r.status_code == 200

    owner = get_watch(s.page.store, s.line, OWNER)
    assert chain(owner) == [(NEIGHBOUR, False)]
    assert owner is not None and owner.enabled is True
    partner = get_watch(s.page.store, s.line, PARTNER)
    assert partner is not None and partner.enabled is False  # D7


# --- criterion 7 -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("env", "admin"), [("local", False), ("aws", True), ("aws", False)])
async def test_c7_admin_watch_settings_is_off_unless_local_and_flagged(
    make_page: Any, env: str, admin: bool
) -> None:
    page = make_page(tower_env=env, admin=admin)
    body = {"owner_user_id": OWNER, "profile": "transplant", "contacts": [PARTNER]}
    async with page.browser() as b:
        assert (await b.post("/_admin/watch-settings", json=body)).status_code == 404


async def test_c7_admin_watch_settings_validates_saves_and_resets(make_page: Any, h: Any) -> None:
    page = make_page(admin=True)
    browser, r = await h.bind(page, OWNER, "phone-asish")
    assert "Line connected" in r.text
    await browser.aclose()
    line = list_lines(page.store, OWNER)[0].line_id
    now = page.clock()
    for grantee in (PARTNER, NEIGHBOUR):
        grant(page.store, line, grantee, "watch", "asish", granted_by=OWNER, now=now)
    grant(page.store, line, COUSIN, "reachability", "asish", granted_by=OWNER, now=now)

    async with page.browser() as b:
        before = snapshot(page.store)
        for bad in (
            {"profile": "transplant", "contacts": [COUSIN]},
            {"profile": "transplant", "contacts": [OWNER]},
            {"profile": "transplant", "contacts": [PARTNER, PARTNER]},
            {"profile": "transplant", "contacts": []},
            {"profile": "panic", "contacts": [PARTNER]},
        ):
            r = await b.post("/_admin/watch-settings", json={"owner_user_id": OWNER, **bad})
            assert 400 <= r.status_code < 500 and r.status_code != 404, (bad, r.status_code)
        assert snapshot(page.store) == before

        r = await b.post(
            "/_admin/watch-settings",
            json={"owner_user_id": OWNER, "profile": "transplant", "contacts": [PARTNER, NEIGHBOUR]},
        )
        assert r.status_code == 200, r.text
        w = get_watch(page.store, line, OWNER)
        assert w is not None and w.profile == "transplant" and w.enabled is False
        assert chain(w) == [(PARTNER, True), (NEIGHBOUR, False)]

        r = await b.post(
            "/_admin/watch-settings",
            json={"owner_user_id": OWNER, "profile": "transplant", "contacts": [PARTNER], "line_id": line},
        )
        assert r.status_code == 200, r.text
        assert chain(get_watch(page.store, line, OWNER)) == [(PARTNER, False)]

        r = await b.post(
            "/_admin/watch-settings",
            json={"owner_user_id": OWNER, "profile": "self", "contacts": [], "reset": True},
        )
        assert r.status_code == 200, r.text
        assert get_watch(page.store, line, OWNER) is None  # a demo re-run starts clean


# --- criterion 8 -------------------------------------------------------------------------------------------


async def test_c8_no_log_line_from_a_save_contains_a_number(
    setup: Setup, h: Any, alerts_spy: AlertsSpy, caplog: pytest.LogCaptureFixture
) -> None:
    s = setup
    _enabled_dark_watch(s)  # so the save also talks to Alerts
    csrf = await h.csrf_of(s.asish)
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    r = await post_settings(h, s.asish, s.line, "transplant", [PARTNER, NEIGHBOUR], csrf=csrf)
    assert r.status_code == 303
    alerts_down(s.page, alerts_spy)
    r = await post_settings(h, s.asish, s.line, "care", [NEIGHBOUR], csrf=csrf)
    assert r.status_code == 303
    for rec in caplog.records:
        line = rec.getMessage()
        assert not phone_hits(line), f"{rec.name}: number-shaped value in a log line"
