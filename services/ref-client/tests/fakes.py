"""Test doubles for the Bedrock Converse client and for Tower (no network, no AWS)."""

from __future__ import annotations

from typing import Any

from ref_client.mcp_client import ToolSpec


class FakeBedrock:
    """Replays Converse responses: each entry is either ("tool", name, args) or ("text", "...")."""

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kw: Any) -> dict[str, Any]:
        self.requests.append(kw)
        kind, *rest = self.script.pop(0)
        usage = {"inputTokens": 100, "outputTokens": 10}
        if kind == "tool":
            name, args = rest
            content = [{"toolUse": {"toolUseId": f"t{len(self.requests)}", "name": name, "input": args}}]
            return {
                "output": {"message": {"role": "assistant", "content": content}},
                "stopReason": "tool_use",
                "usage": usage,
            }
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": rest[0]}]}},
            "stopReason": "end_turn",
            "usage": usage,
        }


class FakeTower:
    """Answers every call with a fixed ToolResult; advertises Tower's three tools with their real descriptions."""

    def __init__(self, result: dict[str, Any]) -> None:
        from tower_mcp.descriptions import DESCRIPTIONS

        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []
        line = {"type": "string", "default": "self", "description": '"self" | alias'}
        self._tools = [
            ToolSpec(n, d, {"type": "object", "properties": {"line": line}}) for n, d in DESCRIPTIONS.items()
        ]

    async def tools(self) -> list[ToolSpec]:
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.result
