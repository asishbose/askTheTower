"""04 §8 "Bearer mode" (U3, 2026-10-11): with `BIND_ADMIN_TOKEN` set, every `/_admin/*` request needs the bearer,
`tables` answers only `?table=Grants`, and `watch-settings` is 404. Without a token off-local, everything stays 404.
Local behaviour without a token is `test_admin.py`, unchanged."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from binding_page.config import Settings
from mock_carrier.testing import MOM
from tower_consent import bind_line

pytestmark = pytest.mark.integration

TOKEN = "bind-admin-token-for-tests-only"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GRANT = {
    "owner_user_id": "user-mom",
    "grantee_user_id": "user-asish",
    "grant": "watch",
    "alias": "mom",
    "action": "grant",
}
WATCH = {"owner_user_id": "user-mom", "profile": "self", "contacts": []}


async def _call(b: httpx.AsyncClient, route: str, headers: dict[str, str]) -> httpx.Response:
    if route == "tables":
        return await b.get("/_admin/tables", params={"table": "Grants", "format": "json"}, headers=headers)
    if route == "resolve":
        return await b.get("/_admin/resolve", params={"user": "user-asish", "line": "mom"}, headers=headers)
    if route == "bind-tokens":
        return await b.post("/_admin/bind-tokens", json={"user_id": "user-asish"}, headers=headers)
    if route == "grants":
        return await b.post("/_admin/grants", json=GRANT, headers=headers)
    return await b.post("/_admin/watch-settings", json=WATCH, headers=headers)


ROUTES = ("tables", "resolve", "bind-tokens", "grants", "watch-settings")


async def test_bearer_mode_needs_the_bearer_and_narrows_the_surface(make_page: Any) -> None:
    page = make_page(tower_env="aws", admin=True, admin_token=TOKEN)
    bind_line(page.store, page.deps.hasher, page.deps.cipher, "user-mom", MOM, "auth_code", now=page.clock())
    async with page.browser() as b:
        for route in ROUTES:
            assert (await _call(b, route, {})).status_code == 401, route
            assert (await _call(b, route, {"Authorization": "Bearer wrong"})).status_code == 401, route
            assert (await _call(b, route, {"Authorization": TOKEN})).status_code == 401, route

        r = await _call(b, "grants", AUTH)
        assert r.status_code == 200 and r.json()["changed"] is True, r.text
        r = await _call(b, "tables", AUTH)
        assert r.status_code == 200 and set(r.json()) == {"Grants"}
        assert r.json()["Grants"][0]["grantee_user_id"] == "user-asish"
        r = await _call(b, "resolve", AUTH)
        assert r.status_code == 200 and r.json()["grant"] == "watch"
        r = await _call(b, "bind-tokens", AUTH)
        assert r.status_code == 200 and r.json()["url"].startswith("http://binding.test/bind/")

        for params in ({"table": "Audit"}, {"table": "Lines", "format": "json"}, {"format": "json"}, {}):
            r = await b.get("/_admin/tables", params=params, headers=AUTH)
            assert r.status_code == 422, params
        assert (await _call(b, "watch-settings", AUTH)).status_code == 404
    assert TOKEN not in repr(page.deps.settings)


async def test_off_local_without_a_token_is_404_everywhere(make_page: Any) -> None:
    for kw in ({"admin": True}, {"admin": False, "admin_token": TOKEN}):
        page = make_page(tower_env="aws", **kw)
        async with page.browser() as b:
            for route in ROUTES:
                assert (await _call(b, route, AUTH)).status_code == 404, (kw, route)
                assert (await _call(b, route, {})).status_code == 404, (kw, route)


def test_settings_read_the_token_from_the_environment() -> None:
    base = {"SESSION_SECRET": "x" * 16, "TOWER_ENV": "aws", "BIND_ADMIN": "1"}
    assert Settings.from_env(base).admin_enabled is False
    on = Settings.from_env({**base, "BIND_ADMIN_TOKEN": TOKEN})
    assert on.admin_enabled and on.admin_bearer_mode and TOKEN not in repr(on)
    assert Settings.from_env({**base, "BIND_ADMIN_TOKEN": ""}).admin_enabled is False
    local = Settings.from_env({**base, "TOWER_ENV": "local"})
    assert local.admin_enabled and not local.admin_bearer_mode
