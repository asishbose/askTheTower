"""Calling Tower as Alexa+ would, from an e2e test: MCP over Streamable HTTP to `env.tower_url`."""

from __future__ import annotations

import asyncio
from typing import Any

from ref_client.mcp_client import TowerClient, TowerConfig

from tests.helpers.env import Targets


async def acall(env: Targets, user_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    cfg = TowerConfig(url=env.tower_url, bearer=env.bearer, user_id=user_id, timeout_s=15.0)
    async with TowerClient(cfg) as tower:
        return await tower.call(tool, args)


def call(env: Targets, user_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(acall(env, user_id, tool, args))
