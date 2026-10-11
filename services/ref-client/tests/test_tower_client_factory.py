"""09 §6.2 rule 7 / §6.3: `TowerClient` without a test factory (the CLI, `make demo`, the compose `web-chat`
service) must build its HTTP client the way fastmcp's `StreamableHttpTransport` calls a custom factory, with
`headers`, `auth`, `follow_redirects` and (optionally) `timeout` keyword arguments, and still carry the 401 hook.

Every in-process test injects its own factory, so this is the only test of the default path short of e2e.
Building a client opens no socket."""

from __future__ import annotations

import httpx2
import pytest
from ref_client.mcp_client import TowerClient, TowerConfig

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("timeout", [None, httpx2.Timeout(30.0, read=10.0)])
async def test_default_factory_accepts_the_transport_s_keywords_and_keeps_the_401_hook(
    timeout: httpx2.Timeout | None,
) -> None:
    tower = TowerClient(TowerConfig(url="http://tower.invalid/mcp", bearer="b"))
    factory = tower._watching_factory()
    kwargs = {"headers": {"Authorization": "Bearer b"}, "auth": None, "follow_redirects": True}
    client = factory(**kwargs, **({"timeout": timeout} if timeout else {}))  # as fastmcp 4 calls it
    try:
        assert isinstance(client, httpx2.AsyncClient)
        assert client.headers["authorization"] == "Bearer b"
        assert len(client.event_hooks["response"]) == 1  # the 401 watcher
    finally:
        await client.aclose()
