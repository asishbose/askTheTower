"""Calling Tower as Alexa+ would, from an e2e test: MCP over Streamable HTTP to `env.tower_url`.

ENV=aws: Tower takes a Cognito access token (AgentCore's JWT authorizer; the static bearer is local only), so a
demo user id `user-<name>` signs in as the Cognito user `<name>` (`ref_client.cognito`; passwords from the root
`.env`). Users that are not demo users have no Cognito account there: those tests skip on aws."""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from ref_client.cognito import CognitoUsers
from ref_client.mcp_client import TowerClient, TowerConfig

from tests.helpers.env import ROOT, Targets


@functools.cache
def cognito_users() -> CognitoUsers:
    return CognitoUsers.from_artifacts(ROOT / "artifacts")


def config(env: Targets, user_id: str) -> TowerConfig:
    if env.env == "aws":
        token = cognito_users().access_token(user_id.removeprefix("user-"))
        return TowerConfig(url=env.tower_url, authorization=f"Bearer {token}", timeout_s=15.0)
    return TowerConfig(url=env.tower_url, bearer=env.bearer, user_id=user_id, timeout_s=15.0)


async def acall(env: Targets, user_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    cfg = config(env, user_id)
    async with TowerClient(cfg) as tower:
        return await tower.call(tool, args)


def call(env: Targets, user_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(acall(env, user_id, tool, args))
