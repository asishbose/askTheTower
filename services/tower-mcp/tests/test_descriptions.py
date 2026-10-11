"""Doc ↔ code drift guard: the descriptions FastMCP registers == descriptions.py == the block in 01 §2."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from tower_mcp import descriptions
from tower_mcp.deps import FixedClock, RecordingAlerts, Settings, build_deps
from tower_mcp.server import create_server

from .conftest import MOCK_START

pytestmark = pytest.mark.unit

DOC = Path(__file__).resolve().parents[3] / "docs/architecture/components/01-alexa-surface.md"


def parse_doc_block(md: str) -> dict[str, tuple[str, dict[str, str]]]:
    """The ```yaml block of 01 §2 → {tool: (description, {arg: spec})}. Descriptions are folded scalars
    (`>`), so lines join with single spaces; arg lines are kept verbatim minus trailing comments."""
    section = md.split("## 2.", 1)[1].split("## 3.", 1)[0]
    match = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert match, "01 §2 has no yaml block"
    tools: dict[str, dict[str, Any]] = {}
    current, mode = "", ""
    for raw in match.group(1).splitlines():
        if not raw.strip():
            continue
        if re.fullmatch(r"\S.*:", raw):
            current = raw[:-1]
            tools[current] = {"description": [], "args": {}}
            continue
        text = raw.strip()
        if text.startswith("description:"):
            mode = "d"
        elif text == "args:":
            mode = "a"
        elif mode == "d":
            tools[current]["description"].append(text)
        elif mode == "a":
            key, _, spec = text.partition(":")
            tools[current]["args"][key] = re.sub(r"\s+#.*$", "", spec.strip())
    return {k: (" ".join(v["description"]), v["args"]) for k, v in tools.items()}


def test_doc_matches_code() -> None:
    doc = parse_doc_block(DOC.read_text(encoding="utf-8"))
    assert set(doc) == set(descriptions.DESCRIPTIONS)
    for name, (text, args) in doc.items():
        assert descriptions.DESCRIPTIONS[name] == text, name
        assert descriptions.ARGS[name] == args, name


async def test_registered_matches_code() -> None:
    deps = build_deps(  # type: ignore[arg-type]
        Settings(),
        env={},
        store=object(),
        carrier=object(),
        cipher=object(),
        clock=FixedClock(MOCK_START),
        alerts=RecordingAlerts(),
    )
    mcp = create_server(deps)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {"line_is_ok", "is_reachable", "watch_line"}  # nothing else is exposed
    for name, tool in tools.items():
        assert tool.description == descriptions.DESCRIPTIONS[name]
        props = tool.parameters["properties"]
        assert set(props) == set(descriptions.ARGS[name])
        for arg, spec in descriptions.ARGS[name].items():
            assert props[arg]["description"] == spec
