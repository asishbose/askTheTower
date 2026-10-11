"""`GatewayClient` — the same seven operations through AgentCore Gateway's MCP tools (05 §3).

Gateway turns each vendored OpenAPI operation into one MCP tool and makes the HTTPS call itself,
with outbound credentials from AgentCore Identity — Tower holds no carrier credential on this path.
Tool names are not hard-coded: they come from `gateway-tools.json`, written at deploy time from
Gateway's `tools/list`. Without that file the RUN-ALL convention `<api>__<operationId>` is assumed
(e.g. `sim-swap__checkSimSwap`) — verify against the real Gateway in prompt 13.

Assumed tool contract (the fake Gateway in `camara_client.testing` implements it; verify in 13):
- arguments: `{"body": <request JSON>}` for operations with a request body, plus each path parameter
  by name (`subscriptionId`);
- success: the carrier's response body as JSON text (empty for 204);
- failure: `isError` with JSON text `{"status": <http status>, "body": <carrier error body>}`; a bare
  CAMARA envelope (`{"status", "code", "message"}`) is accepted too;
- Number Verification's auth code travels in the request `_meta` under `io.askthetower/oauth`
  (`{"grant_type": "authorization_code", "code", "redirect_uri"}`), standing in for Identity's
  three-legged flow.

Live wiring (prompt 13):
- Real AgentCore names tools `<target>___<operationId>` (three underscores); the Terraform module names each
  target after its spec file, so `sim-swap___checkSimSwap`. `resolve_tool_names` maps whatever Gateway lists
  onto the operations; `CARRIER_GATEWAY_TOOLS=discover` does that at first use instead of reading a file.
- Inbound auth: `CARRIER_GATEWAY_AUTH=sigv4` signs every MCP request (`camara_client.aws.SigV4Auth`).
- `CARRIER_GATEWAY_SESSION=persistent` keeps one MCP session per process (opened by `check_tools()` at
  warm-up, closed by `aclose()`), so a request does not pay the MCP `initialize` round trip inside its
  300 ms budget. Per-call sessions remain the default (Lambda freezes between invocations).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from fastmcp import Client as McpClient
from mcp.types import TextContent

from camara_client import specs
from camara_client.breaker import DEFAULT_BREAKERS, BreakerRegistry
from camara_client.config import CarrierConfig
from camara_client.core import Answer, CarrierBase, error_from_answer
from camara_client.errors import malformed_response, transport_error
from camara_client.timeouts import profile_for

OAUTH_META_KEY = "io.askthetower/oauth"
CONVENTION = "<api>__<operationId>"
AGENTCORE_DELIMITER = "___"  # AgentCore Gateway: <target name>___<tool name>
DISCOVER = "discover"


def conventional_name(api: str, operation_id: str) -> str:
    return f"{api}__{operation_id}"


def conventional_tools() -> dict[str, str]:
    return {op.key: conventional_name(op.api, op.operation_id) for op in specs.operations()}


def tools_manifest(tool_names: Mapping[str, str] | None = None, *, gateway: str = "") -> dict[str, Any]:
    """The `gateway-tools.json` document: operation key (`<api>/<operationId>`) → Gateway tool name."""
    return {
        "gateway": gateway,
        "convention": CONVENTION,
        "tools": dict(sorted((tool_names or conventional_tools()).items())),
    }


def resolve_tool_names(listed: Iterable[str]) -> dict[str, str]:
    """Map Gateway's listed tool names onto the operations: `<api>___<operationId>` (AgentCore, target named
    after the spec), then `<api>__<operationId>` (the convention), then a unique `…___<operationId>` /
    `…__<operationId>` (a target named differently). Raises `ValueError` naming any operation left unmapped
    or matched ambiguously."""
    names = set(listed)
    out: dict[str, str] = {}
    missing: list[str] = []
    for op in specs.operations():
        exact = [
            n
            for n in (
                f"{op.api}{AGENTCORE_DELIMITER}{op.operation_id}",
                conventional_name(op.api, op.operation_id),
            )
            if n in names
        ]
        if exact:
            out[op.key] = exact[0]
            continue
        loose = sorted(
            n
            for n in names
            if n.endswith(f"{AGENTCORE_DELIMITER}{op.operation_id}") or n.endswith(f"__{op.operation_id}")
        )
        if len(loose) == 1:
            out[op.key] = loose[0]
        else:
            missing.append(op.key if not loose else f"{op.key} (ambiguous: {loose})")
    if missing:
        raise ValueError(f"Gateway lists no unique tool for {missing}")
    return out


def load_tool_names(path: str | Path | None) -> dict[str, str]:
    """Read `gateway-tools.json`; every operation the client calls must be named in it."""
    if path is None:
        return conventional_tools()
    doc = json.loads(Path(path).read_text("utf-8"))
    tools = doc.get("tools") if isinstance(doc, dict) else None
    if not isinstance(tools, dict) or not all(isinstance(v, str) for v in tools.values()):
        raise ValueError(f"{path}: expected {{'tools': {{'<api>/<operationId>': '<tool name>'}}}}")
    missing = sorted(set(conventional_tools()) - set(tools))
    if missing:
        raise ValueError(f"{path}: no Gateway tool for {missing}")
    return {str(k): str(v) for k, v in tools.items()}


def tool_arguments(op: specs.Operation, body: Any, path_params: Mapping[str, str] | None) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if body is not None:
        args["body"] = body
    for name in op.path_params:
        args[name] = (path_params or {})[name]
    return args


class GatewayClient(CarrierBase):
    """`CarrierClient` over AgentCore Gateway. `client_factory` builds a connected-on-enter
    `fastmcp.Client` (streamable HTTP to `gateway_url` by default; in-memory in tests)."""

    def __init__(
        self,
        config: CarrierConfig,
        *,
        client_factory: Callable[[], McpClient[Any]] | None = None,
        tool_names: Mapping[str, str] | None = None,
        gateway_auth: httpx2.Auth | str | None = None,
        breakers: BreakerRegistry | None = None,
    ) -> None:
        if client_factory is None and not config.gateway_url:
            raise ValueError("GatewayClient needs config.gateway_url or a client_factory")
        self.config = config
        self.profile = profile_for(config.profile)
        self.gateway_url = (config.gateway_url or "in-process").rstrip("/")
        self.breaker = (breakers or DEFAULT_BREAKERS).for_url(self.gateway_url)
        self._discover = tool_names is None and config.gateway_tools_file == DISCOVER
        self.tool_names: dict[str, str] = (
            dict(tool_names)
            if tool_names is not None
            else {}
            if self._discover
            else load_tool_names(config.gateway_tools_file)
        )
        if gateway_auth is None and config.gateway_auth == "sigv4":
            from camara_client.aws import SigV4Auth

            gateway_auth = SigV4Auth(config.gateway_region or "")
        url = config.gateway_url
        auth = gateway_auth
        self._factory: Callable[[], McpClient[Any]] = client_factory or (
            lambda: McpClient(url or "", auth=auth)
        )
        self._persistent = config.gateway_session == "persistent"
        self._shared: McpClient[Any] | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"GatewayClient(gateway_url={self.gateway_url!r}, backend={self.config.backend!r})"

    async def open(self) -> None:
        """Open the process-wide MCP session (`CARRIER_GATEWAY_SESSION=persistent`); idempotent."""
        async with self._lock:
            if self._shared is None:
                stack = AsyncExitStack()
                self._shared = await stack.enter_async_context(self._factory())
                self._stack = stack

    async def aclose(self) -> None:
        async with self._lock:
            stack, self._stack, self._shared = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[McpClient[Any]]:
        shared = self._shared
        if shared is not None and shared.is_connected():
            async with shared:  # re-entrant: reuses the open session
                yield shared
            return
        async with self._factory() as client:
            yield client

    async def _ensure_tool_names(self, client: McpClient[Any] | None = None) -> dict[str, str]:
        if not self.tool_names and self._discover:
            if client is None:
                async with self._session() as c:
                    listed = [t.name for t in await c.list_tools()]
            else:
                listed = [t.name for t in await client.list_tools()]
            self.tool_names = resolve_tool_names(listed)
        return self.tool_names

    async def check_tools(self) -> list[str]:
        """List Gateway's tools and return the operations whose mapped tool name is missing (empty
        when `gateway-tools.json` matches the deployed Gateway). Call once at startup: it also
        pays the MCP client's one-time import and connection cost outside any request budget, opens the
        persistent session when configured, and resolves the names in `discover` mode."""
        if self._persistent:
            await self.open()
        async with self._session() as client:
            listed = {t.name for t in await client.list_tools()}
            if self._discover and not self.tool_names:
                try:
                    self.tool_names = resolve_tool_names(listed)
                except ValueError:
                    return sorted(op.key for op in specs.operations())
        return sorted(key for key, name in self.tool_names.items() if name not in listed)

    async def _send(
        self,
        op: specs.Operation,
        *,
        json: Any = None,
        path_params: dict[str, str] | None = None,
        auth_code: tuple[str, str] | None = None,
    ) -> Answer:
        if not self.tool_names:
            try:
                await self._ensure_tool_names()
            except TimeoutError:
                raise
            except Exception:  # noqa: BLE001 — tools/list failed, or Gateway lacks a tool: a deploy fault
                raise transport_error() from None
        name = self.tool_names[op.key]
        meta = None
        if auth_code is not None:
            code, redirect_uri = auth_code
            meta = {
                OAUTH_META_KEY: {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                }
            }
        try:
            async with self._session() as client:
                result = await client.call_tool(
                    name, tool_arguments(op, json, path_params), raise_on_error=False, meta=meta
                )
        except (TimeoutError, KeyError, ValueError):
            raise
        except Exception:  # noqa: BLE001 — MCP/transport failure; its text may echo arguments
            raise transport_error() from None
        text = "".join(c.text for c in result.content if isinstance(c, TextContent))
        payload = _loads(text)
        if result.is_error:
            status, body = _error_parts(payload)
            if status is None:
                raise malformed_response()
            self._record(status)
            raise error_from_answer(status, body)
        self._record(200)
        if text.strip() and payload is _BAD:
            raise malformed_response()
        return Answer(200, None if payload is _BAD else payload)


_BAD: Any = object()


def _loads(text: str) -> Any:
    if not text.strip():
        return _BAD
    try:
        return json.loads(text)
    except ValueError:
        return _BAD


def _error_parts(payload: Any) -> tuple[int | None, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("status"), int):
        if "body" in payload:
            return payload["status"], payload["body"]
        return payload["status"], payload
    return None, None
