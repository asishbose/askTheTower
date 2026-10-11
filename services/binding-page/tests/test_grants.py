"""Grants through the page (04 §3, §7): bind as Mom; invite as Asish; grant watch "mom"; resolve flips; revoke;
flips back; alias collision → 409. Revocation only with the page session."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from binding_page.app import create_app
from tower_consent import list_granted_to, list_lines, resolve

pytestmark = pytest.mark.integration


async def _bind(page: Any, h: Any, user: str, device: str) -> httpx.AsyncClient:
    browser, r = await h.bind(page, user, device)
    assert r.status_code == 200 and "Line connected" in r.text
    return browser


async def _grant(h: Any, browser: httpx.AsyncClient, code: str, kind: str, alias: str) -> httpx.Response:
    return await browser.post(
        "/grants",
        data={"csrf": await h.csrf_of(browser), "invite_code": code, "kind": kind, "alias": alias},
    )


def _paths(routes: Any) -> list[str]:
    """Every route path, walking included routers (FastAPI >= 0.14x wraps them)."""
    out: list[str] = []
    for r in routes:
        if hasattr(r, "original_router"):
            out += _paths(r.original_router.routes)
        elif getattr(r, "path", None):
            out.append(r.path)
    return out


def _revoke_ids(html: str) -> list[str]:
    import re

    return re.findall(r'action="/grants/([^"]+)/revoke"', html)


async def test_grant_resolve_revoke_cycle(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    assert resolve(page.store, "user-asish", "mom").view.grant == "none"

    code = await h.invite_code(asish)
    r = await _grant(h, mom, code.lower(), "watch", "mom")  # codes are case- and dash-insensitive
    assert r.status_code == 200 and "user-asish" in r.text and "“mom”" in r.text
    rc = resolve(page.store, "user-asish", "mom")
    assert rc.view.bound is True and rc.view.grant == "watch"
    assert rc.line_id == list_lines(page.store, "user-mom")[0].line_id

    # the grantee sees it under "shared with you"
    r = await asish.get("/me")
    assert "“mom” — watch" in r.text

    # the invite code is spent
    r = await _grant(h, mom, code, "reachability", "mum")
    assert r.status_code == 404

    # revoke: one tap on Mom's page
    r = await mom.get("/me")
    (gid,) = _revoke_ids(r.text)
    r = await mom.post(f"/grants/{gid}/revoke", data={"csrf": await h.csrf_of(mom)})
    assert r.status_code == 200 and "Revoked: user-asish" in r.text
    rc = resolve(page.store, "user-asish", "mom")
    assert rc.view.grant == "none" and rc.view.revoked_at is not None
    assert list_granted_to(page.store, "user-asish") == []
    assert _revoke_ids(r.text) == []

    # re-grant after revoke works with a fresh code
    r = await _grant(h, mom, await h.invite_code(asish), "watch", "mom")
    assert r.status_code == 200
    assert resolve(page.store, "user-asish", "mom").view.grant == "watch"
    await mom.aclose()
    await asish.aclose()


async def test_alias_collision_is_409(page: Any, h: Any) -> None:
    """The grantee already has an unrevoked grant called "mom" → a second grant under that alias is refused."""
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    assert (await _grant(h, mom, await h.invite_code(asish), "watch", "mom")).status_code == 200
    code = await h.invite_code(asish)
    r = await _grant(h, mom, code, "reachability", "mom")
    assert r.status_code == 409
    assert "Pick another" in r.text
    assert resolve(page.store, "user-asish", "mom").view.grant == "watch"
    # the refused attempt did not spend the invite code: a different alias works with it
    assert (await _grant(h, mom, code, "reachability", "mum")).status_code == 200
    assert resolve(page.store, "user-asish", "mum").view.grant == "reachability"
    await mom.aclose()
    await asish.aclose()


async def test_grant_errors(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    assert (await _grant(h, mom, "ZZZZ-ZZZZ", "watch", "mom")).status_code == 404  # unknown code
    assert (await _grant(h, mom, "+16135550101", "watch", "mom")).status_code == 404  # never a number
    assert (await _grant(h, mom, await h.invite_code(asish), "owner", "mom")).status_code == 422
    assert (await _grant(h, mom, await h.invite_code(asish), "watch", "Mom 2")).status_code == 422
    assert (await _grant(h, mom, await h.invite_code(mom), "watch", "me too")).status_code == 422  # self
    assert (await _grant(h, mom, await h.invite_code(asish), "watch", "mom")).status_code == 200
    assert resolve(page.store, "user-asish", "mom").view.grant == "watch"
    assert (
        await _grant(h, mom, await h.invite_code(asish), "watch", "mother")
    ).status_code == 409  # duplicate
    # an expired invite code
    stale = await h.invite_code(asish)
    page.clock.advance(hours=24, seconds=1)
    mom2 = await _bind(page, h, "user-mom", "phone-mom")  # Mom's session outlives nothing: re-open a link
    assert (await _grant(h, mom2, stale, "reachability", "mum")).status_code == 404
    for b in (mom, asish, mom2):
        await b.aclose()


async def test_revoke_needs_the_page_session(page: Any, h: Any) -> None:
    """Acceptance: no revoke route callable without the page session; no CSRF-less revoke; only the owner."""
    mom = await _bind(page, h, "user-mom", "phone-mom")
    asish = await _bind(page, h, "user-asish", "phone-asish")
    assert (await _grant(h, mom, await h.invite_code(asish), "watch", "mom")).status_code == 200
    (gid,) = _revoke_ids((await mom.get("/me")).text)

    async with page.browser() as anonymous:
        assert (await anonymous.post(f"/grants/{gid}/revoke")).status_code == 401
        assert (await anonymous.post(f"/grants/{gid}/revoke", data={"csrf": "x"})).status_code == 401
    assert (await mom.post(f"/grants/{gid}/revoke")).status_code == 403  # session but no CSRF token
    assert (await mom.post(f"/grants/{gid}/revoke", data={"csrf": "abc"})).status_code == 403
    # the grantee can't revoke (nor un-revoke) the line-holder's grant
    assert (
        await asish.post(f"/grants/{gid}/revoke", data={"csrf": await h.csrf_of(asish)})
    ).status_code == 403
    assert resolve(page.store, "user-asish", "mom").view.grant == "watch"

    # the only revoke route in the app is the session-guarded one
    app = create_app(page.deps)
    revoke_routes = [p for p in _paths(app.routes) if "revoke" in p]
    assert "/me" in _paths(app.routes)  # the walk sees the included routers
    assert revoke_routes == ["/grants/{gid}/revoke"]
    await mom.aclose()
    await asish.aclose()


async def test_me_needs_a_session(page: Any) -> None:
    async with page.browser() as b:
        assert (await b.get("/me")).status_code == 401
        assert (await b.post("/me/invite")).status_code == 401
        assert (await b.post("/grants", data={"invite_code": "ABCD-EFGH"})).status_code == 401


async def test_session_expires(page: Any, h: Any) -> None:
    mom = await _bind(page, h, "user-mom", "phone-mom")
    page.clock.advance(seconds=page.deps.settings.session_ttl_s + 1)
    assert (await mom.get("/me")).status_code == 401
    await mom.aclose()
