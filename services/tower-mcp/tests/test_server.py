"""The server over Streamable HTTP, in-process: auth → user_id, /healthz, three tools, structured results."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from .conftest import LOCAL_BEARER, RESULTS, TOWER_URL, Stack, mcp_client

pytestmark = pytest.mark.integration


async def _post(app: Any, headers: dict[str, str]) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    base = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=TOWER_URL) as c:
        return await c.post("/mcp", json=body, headers=base | headers)


async def test_healthz_needs_no_auth(tower_app: Any) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tower_app), base_url=TOWER_URL) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Tower-User": "user-asish"},
        {"Authorization": "Bearer wrong", "X-Tower-User": "user-asish"},
        {"Authorization": f"Bearer {LOCAL_BEARER}"},  # local bearer without X-Tower-User
        {"Authorization": f"Basic {LOCAL_BEARER}", "X-Tower-User": "user-asish"},
    ],
)
async def test_no_identity_is_401(tower_app: Any, headers: dict[str, str]) -> None:
    r = await _post(tower_app, headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_local_bearer_lists_three_tools(tower_app: Any) -> None:
    r = await _post(tower_app, {"Authorization": f"Bearer {LOCAL_BEARER}", "X-Tower-User": "user-asish"})
    assert r.status_code == 200, r.text
    names = sorted(t["name"] for t in r.json()["result"]["tools"])
    assert names == ["is_reachable", "line_is_ok", "watch_line"]


async def test_tools_over_mcp(tower_app: Any, stack: Stack) -> None:
    async with mcp_client(tower_app) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert set(tools) == {"line_is_ok", "is_reachable", "watch_line"}
        assert tools["line_is_ok"].inputSchema["properties"]["line"]["default"] == "self"
        res = await client.call_tool("line_is_ok", {"line": "self"})
        data = res.structured_content
        RESULTS.append(data)
        assert data["reason_codes"] == ["OK"]
        assert data["summary"] == "Your line is as it was."
        assert data["checked_at"].startswith("2026-10-05T14:00:00")
        res = await client.call_tool("watch_line", {"line": "mom", "enable": None})
        RESULTS.append(res.structured_content)
        assert res.structured_content["facts"]["watching"] is False
        res = await client.call_tool("is_reachable", {"line": "mom"})
        RESULTS.append(res.structured_content)
        assert res.structured_content["facts"]["reachable"] is True
        text = res.content[0].text
        assert json.loads(text)["reason_codes"] == ["OK"]


async def test_user_header_is_the_identity(tower_app: Any, stack: Stack) -> None:
    async with mcp_client(tower_app, user="user-mom") as client:
        res = await client.call_tool("line_is_ok", {"line": "mom"})
    RESULTS.append(res.structured_content)
    # "mom" is not a grant alias for this user → NO_CONSENT (04 §5; Asish holds that alias and would get OK)
    assert res.structured_content["reason_codes"] == ["NO_CONSENT"]


async def test_internal_error_is_bare(tower_app: Any, stack: Stack) -> None:
    def boom(_row: Any) -> None:
        raise RuntimeError("crash between audit and return: +16135550101 secret detail")

    stack.deps.after_audit = boom
    async with mcp_client(tower_app) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool("line_is_ok", {"line": "self"})
    assert str(exc.value) == "internal error"
