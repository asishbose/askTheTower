"""The reference client as an HTTP endpoint: the agent behind the web chat page (09 §6).

    POST /invocations   {"input", "session_id"} + Authorization: Bearer → {"text", "next_step", "tool_calls"}
    GET  /ping          AgentCore Runtime's health contract
    GET  /healthz       compose and Helm
    GET  /config.js     the page config, only with TOWER_ENV=local (the sign-in stub, 09 §6.4)
    GET  /              services/web-chat when WEB_CHAT_DIR is set (local); AWS serves the page from S3

The rules of 09 §6.2 are applied in order in `invocations`. The agent is a client of Tower like Alexa+: it never
reads a table, evaluates policy or calls a carrier, and the bearer it was given is the one Tower sees. `next_step`
comes from a tool result, never from model text.

Run: `ref-client serve` (the image's default command) or `python -m ref_client.http`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ref_client import phrasebook
from ref_client.agent import (
    Agent,
    BedrockAgent,
    BedrockUnavailable,
    ScriptedAgent,
    bedrock_client,
    has_aws_credentials,
    load_system_prompt,
)
from ref_client.auth import bearer_header
from ref_client.mcp_client import DEFAULT_TOWER_URL, TowerClient, TowerConfig, TowerError, TowerUnauthorized
from ref_client.transcript import ToolCall, Turn, has_number, redact

log = logging.getLogger("ref_client.http")

AgentKind = Literal["bedrock", "scripted"]
AgentSessions = Callable[[TowerConfig], AbstractAsyncContextManager[Agent]]
NEXT_STEP_REJECTED = "NEXT_STEP_REJECTED"
WIRE_LOGGERS = ("botocore", "boto3", "urllib3", "httpx", "httpx2", "httpcore", "mcp", "fastmcp")


# --- wire shapes ---------------------------------------------------------------------------------------------
class Invocation(BaseModel):
    """The page's request body (09 §6.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: str = Field(min_length=1, max_length=500)
    session_id: str = Field(pattern=r"^[A-Za-z0-9-]{33,128}$")


class ToolCallOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    reason_codes: list[str]


class InvocationReply(BaseModel):
    """`next_step` is Tower's object as it arrived (09 §6.2 rule 4); `tool_calls` never carries `facts`."""

    model_config = ConfigDict(frozen=True)

    text: str
    next_step: dict[str, Any] | None
    tool_calls: list[ToolCallOut]


# --- configuration -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HttpSettings:
    """From the environment (service README, "As the web chat page")."""

    tower_url: str = DEFAULT_TOWER_URL
    tower_env: str = "local"
    binding_base_url: str = ""
    web_chat_dir: Path | None = None
    local_bearer: str | None = field(default=None, repr=False)
    agent: Literal["auto", "bedrock", "scripted"] = "auto"
    port: int = 8080
    tower_timeout_s: float = 10.0

    @property
    def local(self) -> bool:
        return self.tower_env == "local"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HttpSettings:
        e = os.environ if env is None else env
        agent = e.get("REF_AGENT", "auto") or "auto"
        if agent not in ("auto", "bedrock", "scripted"):
            raise ValueError(f"REF_AGENT must be auto, bedrock or scripted, not {agent!r}")
        web_dir = e.get("WEB_CHAT_DIR") or None
        return cls(
            tower_url=e.get("TOWER_URL") or DEFAULT_TOWER_URL,
            tower_env=e.get("TOWER_ENV", "local") or "local",
            binding_base_url=(e.get("WEB_CHAT_BINDING_BASE_URL") or "").rstrip("/"),
            web_chat_dir=Path(web_dir) if web_dir else None,
            local_bearer=e.get("TOWER_BEARER") or None,
            agent=agent,  # type: ignore[arg-type]  # checked against the Literal above
            port=int(e.get("REF_CLIENT_HTTP_PORT", "8080")),
            tower_timeout_s=float(e.get("TOWER_TIMEOUT_S", "10")),
        )


# --- the agent per request -----------------------------------------------------------------------------------
def tower_agents(
    kind: AgentKind,
    *,
    bedrock: Any = None,
    system_prompt: str | None = None,
    model_id: str | None = None,
    httpx_client_factory: Callable[..., Any] | None = None,
) -> AgentSessions:
    """One Tower session and one agent per request, with the caller's bearer. `bedrock` (a boto3
    `bedrock-runtime` client) is built once by the composition root; `httpx_client_factory` is the test seam."""

    @asynccontextmanager
    async def session(cfg: TowerConfig) -> AsyncIterator[Agent]:
        async with TowerClient(cfg, httpx_client_factory=httpx_client_factory) as tower:
            if kind == "bedrock":
                yield BedrockAgent(tower, client=bedrock, system_prompt=system_prompt, model_id=model_id)
            else:
                yield ScriptedAgent(tower)

    return session


# --- the rules -----------------------------------------------------------------------------------------------
def pick_next_step(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rule 4: the `next_step` of the last tool result of the turn whose `kind` is not `none`, as it arrived."""
    for result in reversed(results):
        step = result.get("next_step")
        if isinstance(step, dict) and step.get("kind") not in (None, "none"):
            return step
    return None


def vet_next_step(
    step: dict[str, Any] | None, binding_base_url: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Rule 5 (and the privacy invariant): `(step, None)` to pass it on, `(None, why)` to drop it.

    A `bind_line` URL outside `<binding base>/bind/` is dropped when the base is configured. Any step with a
    phone-number-shaped run is dropped too: a 10-digit `carrier_support_number` would otherwise put one in the
    chat path, and the page does not render `call_carrier` anyway (09 §6.4)."""
    if step is None:
        return None, None
    if has_number(json.dumps(step)):
        return None, "number"
    if step.get("kind") == "bind_line" and binding_base_url:
        url = step.get("url")
        if not isinstance(url, str) or not url.startswith(binding_base_url + "/bind/"):
            return None, "url_outside_bind"
    return step, None


def reply_for(turn: Turn, binding_base_url: str) -> tuple[InvocationReply, str | None]:
    step, rejected = vet_next_step(pick_next_step(turn.results), binding_base_url)
    calls = [
        ToolCallOut(name=c.name, reason_codes=[str(x) for x in r.get("reason_codes", [])])
        for c, r in zip(turn.tool_calls, turn.results, strict=False)
    ]
    return InvocationReply(text=redact(turn.spoken), next_step=step, tool_calls=calls), rejected


def session_hash(session_id: str | None) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:16] if session_id else "-"


def _request_log(
    status: int,
    started: float,
    *,
    session: str | None = None,
    turn: Turn | None = None,
    error: str | None = None,
) -> None:
    """Rule 8: one line per request. Never the input, the reply, the token, a URL or a number."""
    line: dict[str, Any] = {
        "event": "invocation",
        "status": status,
        "session": session_hash(session),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if turn is not None:
        line["tools"] = [c.name for c in turn.tool_calls]
        line["reason_codes"] = [list(r.get("reason_codes", [])) for r in turn.results]
    if error:
        line["error"] = error
    log.info(json.dumps(line))


def _error(status: int, code: str, started: float, session: str | None = None) -> JSONResponse:
    _request_log(status, started, session=session, error=code)
    return JSONResponse({"error": code}, status_code=status)


# --- the app -------------------------------------------------------------------------------------------------
def create_app(
    settings: HttpSettings, agents: AgentSessions, *, phrases: Mapping[str, ToolCall] | None = None
) -> FastAPI:
    """`agents` opens one agent per request with the caller's bearer; `phrases` is the scripted agent's lookup
    (`phrasebook.build()`), ignored by the Bedrock agent, which chooses for itself."""
    table = dict(phrases) if phrases is not None else phrasebook.build()
    app = FastAPI(title="Ask the Tower — reference client (web chat agent)", docs_url=None, redoc_url=None)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "Healthy"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config.js")
    async def config_js() -> Response:
        if not settings.local:
            return Response(status_code=404)
        return Response(
            local_config_js(settings), media_type="text/javascript", headers={"Cache-Control": "no-store"}
        )

    @app.post("/invocations")
    async def invocations(request: Request) -> Response:
        started = time.perf_counter()
        authorization = bearer_header(request.headers.get("authorization"))
        if authorization is None:  # rule 1: before the body, the model and Tower
            return _error(401, "unauthorized", started)
        try:
            body = Invocation.model_validate_json(await request.body())
        except ValidationError:  # rule 2
            return _error(422, "invalid_request", started)
        if has_number(body.input):  # rule 2: no number reaches a prompt, and it is never logged
            return _error(422, "no_numbers", started, body.session_id)
        user = request.headers.get("x-tower-user") if settings.local else None  # rule 3
        cfg = TowerConfig(
            url=settings.tower_url,
            authorization=authorization,
            user_id=user or None,
            timeout_s=settings.tower_timeout_s,
        )
        try:
            async with agents(cfg) as agent:
                turn = await agent.ask(body.input, expect=table.get(phrasebook.normalise(body.input)))
        except TowerUnauthorized:  # rule 7
            return _error(401, "unauthorized", started, body.session_id)
        except TowerError:
            return _error(502, "tower_unavailable", started, body.session_id)
        except BedrockUnavailable:
            return _error(503, "agent_unavailable", started, body.session_id)
        reply, rejected = reply_for(turn, settings.binding_base_url)
        if rejected:
            log.warning(
                json.dumps(
                    {
                        "event": NEXT_STEP_REJECTED,
                        "reason": rejected,
                        "session": session_hash(body.session_id),
                    }
                )
            )
        _request_log(200, started, session=body.session_id, turn=turn)
        return JSONResponse(reply.model_dump(mode="json"))

    if settings.web_chat_dir is not None:
        app.mount("/", StaticFiles(directory=settings.web_chat_dir, html=True), name="web-chat")
    return app


def local_config_js(settings: HttpSettings) -> str:
    """The page config for the local sign-in stub (09 §6.4): no Cognito, the agent on the same origin, and the
    local static bearer — which exists only with TOWER_ENV=local and is published on 127.0.0.1 only."""
    cfg = {
        "COGNITO_DOMAIN": "",
        "CLIENT_ID": "",
        "REDIRECT_URI": "",
        "AGENT_URL": "/invocations",
        "BINDING_BASE_URL": settings.binding_base_url,
        "LOCAL_BEARER": settings.local_bearer or "",
    }
    return f"window.WEB_CHAT_CONFIG = {json.dumps(cfg, indent=2)};\n"


# --- composition root ----------------------------------------------------------------------------------------
def pin_wire_loggers() -> None:
    """Wire-level debug logs carry headers (a bearer) and 10-digit checksums; keep them out at any level."""
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def agent_kind(settings: HttpSettings) -> AgentKind:
    if settings.agent == "auto":
        return "bedrock" if has_aws_credentials() else "scripted"
    return settings.agent


def app_from_env(settings: HttpSettings | None = None) -> FastAPI:
    settings = settings or HttpSettings.from_env()
    pin_wire_loggers()
    kind = agent_kind(settings)
    if kind == "bedrock":
        agents = tower_agents(kind, bedrock=bedrock_client(), system_prompt=load_system_prompt())
    else:
        agents = tower_agents(kind)
    log.info(
        json.dumps(
            {
                "event": "startup",
                "agent": kind,
                "tower_env": settings.tower_env,
                "page": bool(settings.web_chat_dir),
            }
        )
    )
    return create_app(settings, agents)


def serve() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = HttpSettings.from_env()
    uvicorn.run(
        app_from_env(settings),
        host="0.0.0.0",  # noqa: S104 — container entrypoint; compose publishes 127.0.0.1 only, Runtime is private
        port=settings.port,
        access_log=False,  # one structured line per request is ours (rule 8)
        log_level="info",
    )


if __name__ == "__main__":
    serve()
