"""The Tower MCP server: FastMCP, Streamable HTTP at `/mcp`, `/healthz`, auth middleware → `user_id`.

Three tools, registered with the verbatim descriptions of 01 §2 (`descriptions.py`). Nothing else is exposed.
Each tool is a thin wrapper: identity from the request (set by `auth.AuthMiddleware`), then the handler in
`tools/`, then failures mapped by `errors.py` to a refusal envelope or a bare "internal error".

Transport: stateless Streamable HTTP with JSON responses — what AgentCore Runtime hosts (port 8000, `/mcp`)
and what MCP Inspector / the reference client speak. No session state lives in this process.

Run: `python -m tower_mcp` (or the `tower-mcp` script); configuration is environment only (`deps.py`).
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tower_mcp import descriptions
from tower_mcp.auth import STATE_KEY, Authenticator, AuthError, AuthMiddleware, Identity
from tower_mcp.deps import Deps, Settings, build_deps
from tower_mcp.errors import refusal_code
from tower_mcp.schemas import ToolResult
from tower_mcp.tools import is_reachable, line_is_ok, watch_line
from tower_mcp.tools.common import ToolName, unavailable

logger = logging.getLogger("tower_mcp")
call_logger = logging.getLogger("tower_mcp.calls")
CALL_LOG_PREFIX = "call "

MCP_PATH = "/mcp"
SERVER_NAME = "ask-the-tower"
INSTRUCTIONS = (
    "Ask the carrier about the user's own phone line, or a line someone has shared with them: SIM swap, "
    "call forwarding, network reachability, and alerts. Results are facts only; read `summary` aloud."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WATCH = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def current_identity() -> Identity:
    """The identity `AuthMiddleware` attached to this HTTP request. No request / no identity → refused."""
    try:
        request = get_http_request()
    except RuntimeError:
        raise AuthError("no HTTP request") from None
    identity = request.scope.get("state", {}).get(STATE_KEY)
    if not isinstance(identity, Identity):
        raise AuthError("no identity")
    return identity


HANDLERS: dict[str, Any] = {"line_is_ok": line_is_ok, "is_reachable": is_reachable, "watch_line": watch_line}


async def invoke(deps: Deps, tool: ToolName, user_id: str, **arguments: Any) -> ToolResult:
    """Run one tool for an authenticated `user_id`, mapping every failure (errors.py): a refusal envelope
    (`SERVICE_UNAVAILABLE`) or `ToolError("internal error")` — never a trace, never a partial result."""
    line = arguments.get("line", "self")
    try:
        result: ToolResult = await HANDLERS[tool](deps, user_id, **arguments)
    except Exception as e:  # noqa: BLE001 - every failure is mapped; nothing escapes as a trace
        code = refusal_code(e)
        if code is None:
            logger.error("%s failed: internal error (%s)", tool, type(e).__name__)
            log_call(deps, tool, user_id, arguments, None)
            raise ToolError("internal error") from None
        logger.warning("%s refused SERVICE_UNAVAILABLE: %s", tool, type(e).__name__)
        result = unavailable(deps, tool, line, await _now(deps))
    log_call(deps, tool, user_id, arguments, result)
    return result


def log_call(
    deps: Deps, tool: ToolName, user_id: str, arguments: dict[str, Any], result: ToolResult | None
) -> None:
    """Local demo aid (`TOWER_CALL_LOG=1`, honoured only with `TOWER_ENV=local`): one log line per tool call —
    tool, arguments, caller and the envelope exactly as returned — so a client Tower can't see into (the
    Alexa+ simulator) still leaves a Tower-side transcript (`make showcase-alexa` collects them). Never on AWS:
    there, the audit log is the only history (rule 3). The envelope holds no number by construction."""
    if not (deps.settings.local and deps.settings.call_log):
        return
    entry = {
        "tool": tool,
        "args": arguments,
        "user_id": user_id,
        "result": result.model_dump(mode="json") if result is not None else {"error": "internal error"},
    }
    call_logger.info("%s%s", CALL_LOG_PREFIX, json.dumps(entry, separators=(",", ":"), sort_keys=True))


async def _run(deps: Deps, tool: ToolName, **arguments: Any) -> ToolResult:
    try:
        identity = current_identity()
    except AuthError:
        raise ToolError("unauthorized") from None
    return await invoke(deps, tool, identity.user_id, **arguments)


async def _now(deps: Deps) -> datetime:
    try:
        return await deps.clock.now()
    except Exception:  # noqa: BLE001
        return datetime.now(UTC)


def create_server(deps: Deps, *, close_deps: bool = False) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        await deps.warm()
        try:
            yield
        finally:
            if close_deps:
                await deps.aclose()

    mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, lifespan=lifespan, mask_error_details=True)
    args = descriptions.ARGS

    @mcp.tool(name="line_is_ok", description=descriptions.LINE_IS_OK, annotations=READ_ONLY)
    async def line_is_ok_tool(
        line: Annotated[str, Field(description=args["line_is_ok"]["line"])] = "self",
    ) -> ToolResult:
        return await _run(deps, "line_is_ok", line=line)

    @mcp.tool(name="is_reachable", description=descriptions.IS_REACHABLE, annotations=READ_ONLY)
    async def is_reachable_tool(
        line: Annotated[str, Field(description=args["is_reachable"]["line"])] = "self",
    ) -> ToolResult:
        return await _run(deps, "is_reachable", line=line)

    @mcp.tool(name="watch_line", description=descriptions.WATCH_LINE, annotations=WATCH)
    async def watch_line_tool(
        line: Annotated[str, Field(description=args["watch_line"]["line"])] = "self",
        enable: Annotated[bool | None, Field(description=args["watch_line"]["enable"])] = None,
    ) -> ToolResult:
        return await _run(deps, "watch_line", line=line, enable=enable)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "tower-mcp"})

    return mcp


def create_app(deps: Deps, *, close_deps: bool = False, authenticator: Authenticator | None = None) -> Any:
    """The ASGI app: FastMCP's Streamable HTTP app wrapped in the auth middleware."""
    s = deps.settings
    auth = authenticator or Authenticator(
        env=s.env,
        local_bearer=s.bearer,
        jwks_url=s.jwks_url,
        issuer=s.jwt_issuer,
        audience=s.jwt_audience,
        client_ids=s.jwt_client_ids,
    )
    mcp = create_server(deps, close_deps=close_deps)
    app = mcp.http_app(
        path=MCP_PATH,
        stateless_http=True,
        json_response=True,
    )
    app.add_middleware(AuthMiddleware, authenticator=auth, protected=MCP_PATH)
    app.state.mcp = mcp
    app.state.deps = deps
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    deps = build_deps(settings)
    app = create_app(deps, close_deps=True)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_level="info")


if __name__ == "__main__":
    main()
