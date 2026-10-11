"""The showcase's table view: off unless TOWER_ENV=local and BIND_ADMIN=1; numbers redacted by construction."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("env", "admin"), [("local", False), ("aws", True), ("aws", False)])
async def test_admin_is_off_unless_local_and_flagged(make_page: Any, env: str, admin: bool) -> None:
    page = make_page(tower_env=env, admin=admin)
    async with page.browser() as b:
        assert (await b.get("/_admin/tables")).status_code == 404
        assert (
            await b.get("/_admin/tables", params={"table": "Grants", "format": "json"})
        ).status_code == 404
        assert (await b.get("/_admin/tables", params={"table": "Nope"})).status_code == 404
        assert (await b.get("/_admin/resolve", params={"user": "u", "line": "self"})).status_code == 404
        assert (await b.post("/_admin/bind-tokens", json={"user_id": "user-asish"})).status_code == 404


async def test_tables_and_resolve_and_fresh_link(make_page: Any, h: Any) -> None:
    page = make_page(admin=True)
    async with page.browser() as b:
        r = await b.post("/_admin/bind-tokens", json={"user_id": "user-mom"})
        assert r.status_code == 200
        url = r.json()["url"]
        assert url.startswith("http://binding.test/bind/")
        path = url.removeprefix("http://binding.test")
        assert (await b.post(f"{path}/verify", data={"as": "phone-mom"})).status_code == 200
        data = (await b.get("/_admin/tables?format=json")).json()
        assert set(data) == {"Users", "Lines", "Grants", "Watches", "Audit", "BindTokens"}
        (line,) = data["Lines"]
        assert line["line_id"].startswith("ln_") and line["msisdn_enc"].endswith("(ciphertext)")
        assert line["owner_user_id"] == "user-mom"
        html = (await b.get("/_admin/tables")).text
        assert "Lines (1)" in html and line["line_id"] in html
        r = await b.get("/_admin/resolve", params={"user": "user-mom", "line": "self"})
        assert r.json()["bound"] is True and r.json()["grant"] == "owner"


async def test_tables_filter_returns_only_that_table(make_page: Any, h: Any) -> None:
    """Demo UI pane 4 reads `?table=Grants`: the grants, and no other table (no line's Audit rows)."""
    page = make_page(admin=True)
    _, r = await h.bind(page, "user-mom", "phone-mom")
    assert "Line connected" in r.text
    body = {"owner_user_id": "user-mom", "grantee_user_id": "user-asish", "grant": "watch", "alias": "mom"}
    async with page.browser() as b:
        assert (await b.post("/_admin/grants", json={**body, "action": "grant"})).status_code == 200
        everything = (await b.get("/_admin/tables", params={"format": "json"})).json()
        assert set(everything) == {"Users", "Lines", "Grants", "Watches", "Audit", "BindTokens"}
        assert everything["Lines"] and everything["Users"]  # the unfiltered dump has other tables' rows
        r = await b.get("/_admin/tables", params={"format": "json", "table": "Grants"})
        assert r.status_code == 200
        data = r.json()
        assert set(data) == {"Grants"}
        assert data["Grants"] == everything["Grants"] and len(data["Grants"]) == 1
        assert data["Grants"][0]["grantee_user_id"] == "user-asish"
        html = (await b.get("/_admin/tables", params={"table": "Grants"})).text
        assert "Grants (1)" in html and "Audit (" not in html and "Lines (" not in html


async def test_tables_filter_refuses_an_unknown_table(make_page: Any) -> None:
    async with make_page(admin=True).browser() as b:
        for bad in ("Nope", "grants", ""):
            r = await b.get("/_admin/tables", params={"format": "json", "table": bad})
            assert r.status_code == 422, (bad, r.text)


async def test_admin_grants_is_off_unless_local_and_flagged(make_page: Any) -> None:
    body = {
        "owner_user_id": "user-mom",
        "grantee_user_id": "user-asish",
        "grant": "watch",
        "alias": "mom",
        "action": "grant",
    }
    for env, admin in (("local", False), ("aws", True)):
        async with make_page(tower_env=env, admin=admin).browser() as b:
            assert (await b.post("/_admin/grants", json=body)).status_code == 404


async def test_admin_grants_grant_revoke_idempotent(make_page: Any, h: Any) -> None:
    """The compose seed and `ref-client demo` moment 3: grant, revoke, re-grant — each twice, no error."""
    page = make_page(admin=True)
    _, r = await h.bind(page, "user-mom", "phone-mom")
    assert "Line connected" in r.text
    body = {"owner_user_id": "user-mom", "grantee_user_id": "user-asish", "grant": "watch", "alias": "mom"}
    async with page.browser() as b:

        async def post(action: str) -> dict[str, Any]:
            r = await b.post("/_admin/grants", json={**body, "action": action})
            assert r.status_code == 200, r.text
            return dict(r.json())

        async def view() -> dict[str, Any]:
            r = await b.get("/_admin/resolve", params={"user": "user-asish", "line": "mom"})
            return dict(r.json())

        first = await post("grant")
        assert first["changed"] is True and first["active"] is True and first["line_id"].startswith("ln_")
        assert (await post("grant"))["changed"] is False
        assert (await view())["grant"] == "watch"
        assert (await post("revoke"))["changed"] is True
        assert (await post("revoke"))["changed"] is False
        assert (await view())["grant"] == "none"
        assert (await post("grant"))["changed"] is True
        assert (await view())["grant"] == "watch"


async def test_admin_grants_refuses_without_a_line_or_bad_body(make_page: Any) -> None:
    page = make_page(admin=True)
    async with page.browser() as b:
        body = {
            "owner_user_id": "user-nobody",
            "grantee_user_id": "user-asish",
            "grant": "watch",
            "alias": "x",
            "action": "grant",
        }
        assert (await b.post("/_admin/grants", json=body)).status_code == 409
        assert (await b.post("/_admin/grants", json={**body, "grant": "owner"})).status_code == 422
        assert (await b.post("/_admin/grants", json={**body, "extra": 1})).status_code == 422
