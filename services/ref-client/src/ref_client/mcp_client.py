"""Tower over MCP Streamable HTTP, exactly as Alexa+ would reach it: discover the tools, call them, get the
`ToolResult` envelope back as a dict.

Auth: `Authorization: Bearer $TOWER_BEARER`; in local mode also `X-Tower-User: <user_id>` (Tower's local
identity, 01 §4). On AWS the bearer is the JWT and the header is ignored by Tower.

This module knows nothing about policy, consent or carriers. It holds no carrier credentials and no store.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

DEFAULT_TOWER_URL = "http://localhost:8080/mcp"  # mk/vars.mk TOWER_URL for ENV=local
# AgentCore Runtime routes each request to a microVM by session. Tower is a stateless MCP server, so it
# issues no `Mcp-Session-Id` and, without one from the client, every request lands in a new session: a cold
# start (~7 s) per tool call (Tower's log, 2026-10-14). One random id per process keeps the calls on one warm
# Tower. It names no user; Tower authenticates every request by its bearer, sharing a microVM shares no data.
AGENTCORE_SESSION_ID = f"tower-{uuid.uuid4()}"
AGENTCORE_SESSION_HEADERS = ("Mcp-Session-Id", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")


log = logging.getLogger("ref_client.tower")


class TowerError(RuntimeError):
    """Tower refused the request outright (401, unknown tool, internal error) or could not be reached.
    A policy refusal is *not* an error: it is a normal `ToolResult` with a refusal reason code."""


class TowerUnauthorized(TowerError):
    """Tower answered 401: the bearer was refused (expired, wrong issuer or client). The web chat page signs in
    again (09 §6.2 rule 7)."""


@dataclass(frozen=True)
class ToolSpec:
    """One tool as Tower advertises it: the description is the voice UX (01 §2)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TowerConfig:
    url: str = DEFAULT_TOWER_URL
    bearer: str | None = None
    user_id: str | None = None
    timeout_s: float = 10.0
    # The caller's whole `Authorization` header, forwarded byte for byte (the web chat agent, 09 §6.2 rule 3).
    # Wins over `bearer`.
    authorization: str | None = field(default=None, repr=False)
    # AgentCore Runtime session (33+ characters); None: this process's `AGENTCORE_SESSION_ID`.
    session_id: str | None = None

    @classmethod
    def from_env(cls, *, user_id: str | None = None) -> TowerConfig:
        return cls(
            url=os.environ.get("TOWER_URL") or DEFAULT_TOWER_URL,
            bearer=os.environ.get("TOWER_BEARER") or None,
            user_id=user_id,
            timeout_s=float(os.environ.get("TOWER_TIMEOUT_S", "10")),
        )

    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if "bedrock-agentcore" in self.url:
            h.update(dict.fromkeys(AGENTCORE_SESSION_HEADERS, self.session_id or AGENTCORE_SESSION_ID))
        if self.authorization:
            h["Authorization"] = self.authorization
        elif self.bearer:
            h["Authorization"] = f"Bearer {self.bearer}"
        if self.user_id:
            h["X-Tower-User"] = self.user_id
        return h


def user_id_for(name: str) -> str:
    """`--user asish` → `user-asish` (the demo seed's ids); a full id passes through."""
    return name if name.startswith("user-") or ":" in name else f"user-{name}"


class TowerClient:
    """`async with TowerClient(cfg) as tower: await tower.tools(); await tower.call("line_is_ok", {...})`.

    `httpx_client_factory` is the test seam: tests hand in a factory that talks to Tower's ASGI app in-process.
    """

    def __init__(self, config: TowerConfig, *, httpx_client_factory: Callable[..., Any] | None = None):
        self.config = config
        self._factory = httpx_client_factory
        self._client: Any = None
        self._tools: list[ToolSpec] | None = None
        self._refused = False  # Tower answered 401 at least once on this session

    def _watching_factory(self) -> Callable[..., Any]:
        """The MCP client turns a 401 into a generic "Server returned an error response" and drops the status,
        so a response hook on the HTTP client remembers it (09 §6.2 rule 7 needs 401 apart from "unreachable")."""
        from mcp.shared._httpx_utils import create_mcp_http_client

        def default_factory(
            headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None, **_: Any
        ) -> Any:
            # fastmcp passes extra keywords (follow_redirects=True) that create_mcp_http_client rejects;
            # it already follows redirects, so only these three are forwarded.
            return create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)

        inner = self._factory or default_factory

        async def on_response(response: Any) -> None:
            if response.status_code == 401:
                self._refused = True

        def factory(*args: Any, **kwargs: Any) -> Any:
            client = inner(*args, **kwargs)
            client.event_hooks["response"].append(on_response)
            return client

        return factory

    def _error(self, what: str, exc: BaseException) -> TowerError:
        if self._refused:
            return TowerUnauthorized(f"{what}: Tower refused the bearer (401)")
        return TowerError(f"{what}: {type(exc).__name__}")

    async def __aenter__(self) -> TowerClient:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(
            self.config.url, headers=self.config.headers(), httpx_client_factory=self._watching_factory()
        )
        self._client = Client(transport, timeout=self.config.timeout_s)
        try:
            await self._client.__aenter__()
        except Exception as e:  # noqa: BLE001 - one clear error for "cannot reach Tower"
            self._client = None
            raise self._error(f"cannot connect to Tower at {self.config.url}", e) from None
        return self

    async def __aexit__(
        self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(et, e, tb)
            self._client = None

    def _on_agentcore(self) -> bool:
        return "bedrock-agentcore" in self.config.url

    async def _reconnect(self) -> None:
        """Close the session and open a new one under a fresh AgentCore session id (tools stay cached)."""
        try:
            await self.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — the old session is gone anyway
            self._client = None
        self.config = dataclasses.replace(self.config, session_id=f"tower-{uuid.uuid4()}")
        await self.__aenter__()

    async def tools(self) -> list[ToolSpec]:
        """Discovered at connect, cached for the session. Nothing is hard-coded here."""
        if self._tools is None:
            try:
                listed = await self._client.list_tools()
            except Exception as e:  # noqa: BLE001
                raise self._error("tools/list failed", e) from None
            self._tools = [ToolSpec(t.name, t.description or "", dict(t.inputSchema or {})) for t in listed]
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool; returns the `ToolResult` wire dict (summary, facts, reason_codes, next_step, ...)."""
        from fastmcp.exceptions import ToolError

        for attempt in (1, 2):
            try:
                res = await self._client.call_tool(name, arguments)
                break
            except ToolError as e:
                raise (TowerUnauthorized if self._refused else TowerError)(f"{name}: {e}") from None
            except Exception as e:  # noqa: BLE001
                # On AgentCore the runtime session can be retired under an open client (idle, or the microVM
                # recycled): the call fails with an MCP/transport error, not a tool error. Every Tower tool is
                # safe to repeat, so reconnect on a fresh session and try once more.
                if attempt == 2 or self._refused or not self._on_agentcore():
                    raise self._error(name, e) from None
                log.warning("tower: %s failed (%s); reconnecting on a new session", name, type(e).__name__)
                await self._reconnect()
        data = res.structured_content
        if not isinstance(data, dict):
            text = res.content[0].text if res.content else "{}"
            data = json.loads(text)
        if "summary" not in data or "reason_codes" not in data:
            raise TowerError(f"{name}: result is not a ToolResult envelope")
        return dict(data)
