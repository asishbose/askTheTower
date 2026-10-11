"""Test aids shared by this package's tests and later prompts' (Tower, Alerts, binding page):

- `FakeGateway` — an in-process FastMCP server that does what AgentCore Gateway does: one MCP tool per
  operation in the vendored specs, named by the Gateway convention, each making the HTTPS call to a
  configured carrier base URL with its own outbound credentials (standing in for AgentCore Identity).
- `FixtureCarrier` — an `httpx.MockTransport` that replays one fixture case and records mismatches.
- `run_case()` — drive any `CarrierClient` through a fixture case and return a comparable outcome.

Not used by any running service.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import fastmcp
import httpx
from fastmcp.server.dependencies import get_context
from fastmcp.tools import Tool
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from camara_client import specs
from camara_client.config import CarrierConfig, OAuthConfig
from camara_client.direct import OAuthSession
from camara_client.errors import CarrierError
from camara_client.gateway import OAUTH_META_KEY, conventional_name
from camara_client.protocol import CarrierClient, LineRef

FIXTURE_TOKEN = "fixture-access-token"


# --- the fake Gateway --------------------------------------------------------------------------------
class _ProxyTool(Tool):
    """One Gateway-generated tool: arguments → HTTPS request → response body (or an error result)."""

    def __init__(self, *, gateway: FakeGateway, op: specs.Operation, **kw: Any) -> None:
        super().__init__(**kw)
        object.__setattr__(self, "_gateway", gateway)
        object.__setattr__(self, "_op", op)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        gw: FakeGateway = self._gateway  # type: ignore[attr-defined]
        op: specs.Operation = self._op  # type: ignore[attr-defined]
        rc = get_context().request_context
        meta = getattr(rc, "meta", None) or {}
        oauth = meta.get(OAUTH_META_KEY) if isinstance(meta, dict) else None
        status, text = await gw.forward(op, arguments, oauth if isinstance(oauth, dict) else None)
        if status >= 400:
            body = _maybe_json(text)
            payload = json.dumps({"status": status, "body": body})
            return ToolResult(content=[TextContent(type="text", text=payload)], is_error=True)
        return ToolResult(content=[TextContent(type="text", text=text)])


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


@dataclass
class FakeGateway:
    """In-process stand-in for AgentCore Gateway + Identity.

    `transport` reaches the carrier (an `httpx.ASGITransport` over the mock, or a `FixtureCarrier`);
    `client_id`/`secret` are the client-credentials identity; `auth_code_client` the (id, secret) used to
    exchange Number Verification auth codes."""

    base_url: str
    transport: httpx.AsyncBaseTransport | None = None
    client_id: str = "tower"
    secret: str = "local-dev-tower"
    auth_code_client: tuple[str, str] = ("binding-page", "local-dev-binding")
    token_url: str | None = None
    tool_name: Callable[[str, str], str] = conventional_name
    calls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        token_url = self.token_url or f"{self.base_url}/oauth2/token"
        self._http = httpx.AsyncClient(transport=self.transport, timeout=30.0)
        self._identity = OAuthSession(
            self._http, token_url=token_url, client_id=self.client_id, secret=self.secret
        )
        self._codes = OAuthSession(
            self._http,
            token_url=token_url,
            client_id=self.auth_code_client[0],
            secret=self.auth_code_client[1],
        )
        self.server = fastmcp.FastMCP("fake-agentcore-gateway")
        for op in specs.operations():
            props: dict[str, Any] = {name: {"type": "string"} for name in op.path_params}
            required = list(op.path_params)
            if op.request_schema is not None:
                props["body"] = op.request_schema
                if op.method in ("POST", "PUT", "PATCH"):
                    required.append("body")
            self.server.add_tool(
                _ProxyTool(
                    gateway=self,
                    op=op,
                    name=self.tool_name(op.api, op.operation_id),
                    description=f"{op.method} {op.path} ({op.api})",
                    parameters={"type": "object", "properties": props, "required": required},
                )
            )

    def client(self) -> fastmcp.Client[Any]:
        """A fresh in-memory MCP client connected to this Gateway (the `GatewayClient` factory)."""
        return fastmcp.Client(self.server)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def forward(
        self, op: specs.Operation, arguments: dict[str, Any], oauth: dict[str, Any] | None
    ) -> tuple[int, str]:
        self.calls.append(op.key)
        try:
            if oauth and oauth.get("grant_type") == "authorization_code":
                token = await self._codes.exchange_code(str(oauth["code"]), str(oauth["redirect_uri"]))
            else:
                token = await self._identity.token(op.scope_for())
        except CarrierError as err:
            return err.status or 502, json.dumps({"status": err.status or 502, "code": "UNAUTHENTICATED"})
        path = op.render_path({k: str(arguments[k]) for k in op.path_params})
        r = await self._http.request(
            op.method,
            self.base_url + path,
            json=arguments.get("body"),
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code, r.text


def mock_config(
    base_url: str,
    *,
    client: str = "direct",
    profile: str = "request",
    client_id: str = "tower",
    gateway_url: str | None = None,
) -> CarrierConfig:
    """A `CarrierConfig` for the mock carrier at `base_url` (OAuth under `/oauth2/`)."""
    base_url = base_url.rstrip("/")
    return CarrierConfig(
        client=client,  # type: ignore[arg-type]
        backend="mock",
        base_url=base_url,
        oauth=OAuthConfig(
            token_url=f"{base_url}/oauth2/token",
            authorize_url=f"{base_url}/oauth2/authorize",
            client_id=client_id,
            secret_ref=f"env:CARRIER_SECRET_{client_id.upper().replace('-', '_')}",
        ),
        profile=profile,  # type: ignore[arg-type]
        gateway_url=gateway_url or ("http://fake-gateway.test" if client == "gateway" else None),
    )


# --- fixtures ----------------------------------------------------------------------------------------
def fixture_files() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in sorted(specs.fixtures_dir().iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json") and "__" in entry.name:
            out[entry.name] = json.loads(entry.read_text("utf-8"))
    return out


def fixture_cases() -> Iterator[tuple[str, dict[str, Any]]]:
    for doc in fixture_files().values():
        for case in doc["cases"]:
            yield f"{doc['operation']} :: {case['case']}", case


@dataclass
class FixtureCarrier:
    """Replays one fixture case: answers the token endpoint, then each expected exchange in order,
    recording any request that differs from what the spec-derived fixture says must be sent."""

    case: dict[str, Any]
    mismatches: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._pending = list(self.case["exchanges"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": FIXTURE_TOKEN, "token_type": "Bearer", "expires_in": 3600}
            )
        if not self._pending:
            self.mismatches.append(f"unexpected {request.method} {request.url.path}")
            return httpx.Response(599)
        exp = self._pending.pop(0)
        want = exp["request"]
        got_json = json.loads(request.content) if request.content else None
        if (request.method, request.url.path, got_json) != (want["method"], want["path"], want["json"]):
            self.mismatches.append(
                f"expected {want['method']} {want['path']} {want['json']}, "
                f"got {request.method} {request.url.path} {got_json}"
            )
        if request.headers.get("authorization") != f"Bearer {FIXTURE_TOKEN}":
            self.mismatches.append("missing bearer")
        resp = exp["response"]
        if "text" in resp:
            return httpx.Response(resp["status"], text=resp["text"])
        if resp.get("json") is None:
            return httpx.Response(resp["status"])
        return httpx.Response(resp["status"], json=resp["json"])

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    @property
    def unconsumed(self) -> int:
        return len(self._pending)


def _args(call: dict[str, Any]) -> dict[str, Any]:
    args = dict(call["args"])
    if "line" in args:
        args["line"] = LineRef(**args["line"])
    if "now" in args:
        args["now"] = datetime.fromisoformat(args["now"])
    if "ttl_h" in args:
        args["ttl"] = timedelta(hours=args.pop("ttl_h"))
    return args


async def run_case(client: CarrierClient, case: dict[str, Any]) -> dict[str, Any]:
    """Call the client method a fixture names; return `{"result": ...}` or `{"error": ...}` as plain
    JSON-able data so two clients' outcomes compare byte for byte."""
    method = getattr(client, case["call"]["method"])
    try:
        result = await method(**_args(case["call"]))
    except CarrierError as err:
        return {"error": err.as_dict()}
    if hasattr(result, "model_dump"):
        return {"result": result.model_dump(mode="json"), "type": type(result).__name__}
    if isinstance(result, datetime):
        return {"result": result.isoformat(), "type": "datetime"}
    return {"result": result, "type": type(result).__name__}


def outcome_json(outcome: dict[str, Any]) -> str:
    return json.dumps(outcome, sort_keys=True)


def monotonic_clock(start: float = 1000.0) -> tuple[Callable[[], float], Callable[[float], None]]:
    """A settable clock for breakers and token caches: returns (clock, advance)."""
    now = [start]

    def clock() -> float:
        return now[0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    return clock, advance


__all__ = [
    "FIXTURE_TOKEN",
    "FakeGateway",
    "FixtureCarrier",
    "fixture_cases",
    "fixture_files",
    "mock_config",
    "monotonic_clock",
    "outcome_json",
    "run_case",
]
