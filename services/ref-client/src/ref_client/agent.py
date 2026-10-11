"""The agent: one utterance in, Tower tool calls, one spoken answer out.

`BedrockAgent` is the only place in this repository that calls a language model (prompt 11 guardrail; the
repo-wide grep is `tests/test_only_llm_call.py`). It uses the Bedrock Converse API with tool use: the tools are
whatever Tower advertises at connect (names, descriptions, input schemas — nothing hard-coded), the system
prompt is `prompts/ref-client/system.md`, temperature 0. It decides *which Tower tool to call*, like Alexa+;
it never sees carrier credentials, never touches a store, never decides policy.

Why Converse and not the Strands Agents SDK: `strands-agents` 1.58 (the latest at build time) requires
`mcp<2.2`, while Tower's FastMCP 4.0.11 runs on `mcp` 2.3 in the same uv workspace lock. The tool loop Strands
would run for us is ~40 lines here, against the same Bedrock API Strands' `BedrockModel` calls. Swap back when
Strands accepts `mcp>=2.3` (build log 11, decision).

`ScriptedAgent` is the no-model stand-in for runs without AWS credentials: the demo script names the tool call
for each utterance and the agent reads `summary` verbatim. It proves the Tower side of the transcript (tool
calls → reason codes), not tool selection; transcripts say which agent produced them.
"""

from __future__ import annotations

import asyncio
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from ref_client.mcp_client import ToolSpec
from ref_client.transcript import ToolCall, Turn, guard_spoken

DEFAULT_MODEL_ID = (
    "amazon.nova-micro-v1:0"  # RUN-ALL Decisions: a Nova Micro id; override with BEDROCK_MODEL_ID
)
DEFAULT_REGION = "us-east-1"
MAX_TOOL_ROUNDS = 3
_THINKING = re.compile(r"<thinking>.*?</thinking>", re.S | re.I)


class BedrockUnavailable(RuntimeError):
    """Bedrock could not be used (no credentials, no network, no model access). The message says so plainly;
    nothing else in the system depends on this service."""


class ToolCaller(Protocol):
    async def tools(self) -> list[ToolSpec]: ...
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class Agent(Protocol):
    name: str
    model_id: str | None

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn: ...


def load_system_prompt() -> str:
    """`prompts/ref-client/system.md`: packaged into the wheel as `ref_client/_data/system.md`, read from the
    service folder in a source checkout; `REF_SYSTEM_PROMPT` (a path) overrides."""
    override = os.environ.get("REF_SYSTEM_PROMPT")
    if override:
        return Path(override).read_text(encoding="utf-8")
    try:
        return (resources.files("ref_client") / "_data" / "system.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        pass
    return (Path(__file__).resolve().parents[2] / "prompts" / "ref-client" / "system.md").read_text("utf-8")


def has_aws_credentials() -> bool:
    """True when the standard credential sources are configured locally. Looks at env vars and the shared
    files only — no network call (in particular no instance-metadata probe)."""
    env = os.environ
    if env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"):
        return True
    if env.get("AWS_PROFILE") or env.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return True
    if env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or env.get("AWS_CONTAINER_CREDENTIALS_FULL_URI"):
        return True
    home = Path(env.get("HOME", "~")).expanduser()
    for f in (env.get("AWS_SHARED_CREDENTIALS_FILE"), home / ".aws" / "credentials"):
        if f and Path(f).is_file() and "aws_access_key_id" in Path(f).read_text(errors="ignore"):
            return True
    return False


def converse_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Tower's MCP tool list → Converse `toolSpec`s. Descriptions pass through verbatim (they are the UX);
    the input schema keeps names, types, descriptions and defaults (`bool | null` becomes an optional bool)."""
    out = []
    for t in tools:
        props: dict[str, Any] = {}
        for name, p in (t.input_schema.get("properties") or {}).items():
            types = [a.get("type") for a in p.get("anyOf", [])] or [p.get("type", "string")]
            kept = [x for x in types if x and x != "null"] or ["string"]
            prop: dict[str, Any] = {"type": kept[0]}
            if p.get("description"):
                prop["description"] = p["description"]
            if p.get("default") is not None:
                prop["default"] = p["default"]
            props[name] = prop
        schema = {"type": "object", "properties": props, "required": list(t.input_schema.get("required", []))}
        out.append(
            {"toolSpec": {"name": t.name, "description": t.description, "inputSchema": {"json": schema}}}
        )
    return out


def bedrock_client(region: str | None = None) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region or os.environ.get("AWS_REGION") or DEFAULT_REGION,
        endpoint_url=os.environ.get("BEDROCK_ENDPOINT_URL") or None,
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}),
    )


class BedrockAgent:
    name = "bedrock"

    def __init__(
        self,
        tower: ToolCaller,
        *,
        model_id: str | None = None,
        region: str | None = None,
        system_prompt: str | None = None,
        client: Any = None,
    ) -> None:
        self.tower = tower
        self.model_id: str | None = model_id or os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
        self.region = region or os.environ.get("AWS_REGION") or DEFAULT_REGION
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()
        self._client = client
        self.usage = {"inputTokens": 0, "outputTokens": 0}

    def _bedrock(self) -> Any:
        if self._client is None:
            self._client = bedrock_client(self.region)
        return self._client

    async def _converse(self, messages: list[dict[str, Any]], tool_config: dict[str, Any]) -> dict[str, Any]:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            resp: dict[str, Any] = await asyncio.to_thread(
                self._bedrock().converse,
                modelId=self.model_id,
                system=[{"text": self.system_prompt}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={"temperature": 0.0, "maxTokens": 400},
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            raise BedrockUnavailable(self._explain(code)) from None
        except BotoCoreError as e:
            raise BedrockUnavailable(self._explain(type(e).__name__)) from None
        for k in self.usage:
            self.usage[k] += int(resp.get("usage", {}).get(k, 0))
        return resp

    def _explain(self, cause: str) -> str:
        return (
            f"Bedrock unavailable ({cause}) for model {self.model_id} in {self.region}. "
            "Check AWS credentials, region and model access (BEDROCK_MODEL_ID, AWS_REGION). "
            "Only the reference client needs Bedrock; Tower and the rest of the system are unaffected. "
            "Without Bedrock, `ref-client demo --agent scripted` runs the demo with the tool calls from the script."
        )

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        """`expect` is ignored: the model chooses. Each utterance is a fresh conversation."""
        tool_config = {"tools": converse_tools(await self.tower.tools())}
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": utterance}]}]
        turn = Turn(utterance=utterance)
        for _ in range(MAX_TOOL_ROUNDS + 1):
            resp = await self._converse(messages, tool_config)
            message = resp["output"]["message"]
            messages.append(message)
            uses = [b["toolUse"] for b in message.get("content", []) if "toolUse" in b]
            if resp.get("stopReason") != "tool_use" or not uses:
                text = " ".join(b["text"] for b in message.get("content", []) if "text" in b)
                turn.spoken = _THINKING.sub("", text).strip()
                break
            if len(turn.tool_calls) >= MAX_TOOL_ROUNDS:
                turn.spoken = " ".join(r["summary"] for r in turn.results)
                break
            replies = []
            for use in uses:
                call = ToolCall(use["name"], dict(use.get("input") or {}))
                turn.tool_calls.append(call)
                result = await self.tower.call(call.name, call.args)
                turn.results.append(result)
                turn.result = result
                replies.append(
                    {
                        "toolResult": {
                            "toolUseId": use["toolUseId"],
                            "content": [{"json": result}],
                            "status": "success",
                        }
                    }
                )
            messages.append({"role": "user", "content": replies})
        turn.spoken, turn.guard = guard_spoken(turn.spoken, turn.results)
        return turn


class ScriptedAgent:
    """No model: calls the tool the script names and reads `summary` verbatim."""

    name = "scripted"
    model_id: str | None = None

    def __init__(self, tower: ToolCaller) -> None:
        self.tower = tower

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        turn = Turn(utterance=utterance)
        if expect is None:
            turn.spoken = "(scripted agent: no tool call scripted for this utterance)"
            return turn
        turn.tool_calls.append(ToolCall(expect.name, dict(expect.args)))
        result = await self.tower.call(expect.name, dict(expect.args))
        turn.results.append(result)
        turn.result = result
        turn.spoken = str(result["summary"])
        return turn
