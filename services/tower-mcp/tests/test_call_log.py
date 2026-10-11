"""`TOWER_CALL_LOG=1` (prompt 15): one `tower_mcp.calls` line per tool call, local mode only, so the Alexa+
simulator run leaves Tower-side transcripts (`make showcase-alexa` → artifacts/transcripts/alexa-*.json)."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from tower_mcp.deps import Settings
from tower_mcp.schemas import ToolResult
from tower_mcp.server import CALL_LOG_PREFIX, log_call

from tests.privacy.patterns import E164_STRICT

pytestmark = pytest.mark.unit

RESULT = ToolResult.model_validate(
    {
        "summary": "Your line is as it was.",
        "facts": {
            "line": "mom",
            "sim_swapped_recently": False,
            "call_forwarding": "none",
            "source": "carrier",
        },
        "reason_codes": ["OK"],
        "checked_at": "2026-10-05T14:20:00Z",
    }
)


def deps(**settings: Any) -> Any:
    return SimpleNamespace(settings=Settings(**settings))


def lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "tower_mcp.calls"]


def test_settings_read_the_flag() -> None:
    assert Settings.from_env({"TOWER_CALL_LOG": "1"}).call_log is True
    assert Settings.from_env({}).call_log is False


def test_local_call_log_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="tower_mcp.calls")
    log_call(deps(env="local", call_log=True), "line_is_ok", "user-asish", {"line": "mom"}, RESULT)
    log_call(
        deps(env="local", call_log=True), "watch_line", "user-asish", {"line": "self", "enable": None}, None
    )
    first, second = lines(caplog)
    entry = json.loads(first.removeprefix(CALL_LOG_PREFIX))
    assert entry == {
        "tool": "line_is_ok",
        "args": {"line": "mom"},
        "user_id": "user-asish",
        "result": RESULT.model_dump(mode="json"),
    }
    assert json.loads(second.removeprefix(CALL_LOG_PREFIX))["result"] == {"error": "internal error"}
    assert not E164_STRICT.search(first + second)


@pytest.mark.parametrize("settings", [{"env": "local"}, {"env": "aws", "call_log": True}])
def test_off_by_default_and_never_outside_local(
    caplog: pytest.LogCaptureFixture, settings: dict[str, Any]
) -> None:
    caplog.set_level(logging.INFO, logger="tower_mcp.calls")
    log_call(deps(**settings), "line_is_ok", "user-asish", {"line": "self"}, RESULT)
    assert lines(caplog) == []
