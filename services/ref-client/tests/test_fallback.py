"""Bedrock unreachable → a clear error, and nothing else affected: the system has no dependency on this service.

No AWS call is made: the Bedrock endpoint is pointed at a closed local port, credentials are fakes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from ref_client import run
from ref_client.agent import BedrockAgent, BedrockUnavailable, ScriptedAgent
from ref_client.transcript import ToolCall

from .conftest import RefStack
from .fakes import FakeTower
from .helpers import ROOT

OK = {
    "summary": "Your line is as it was.",
    "facts": {"line": "self"},
    "reason_codes": ["OK"],
    "next_step": {"kind": "none"},
    "checked_at": "2026-10-05T14:00:00Z",
}


@pytest.fixture
def no_aws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "REF_AGENT",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def dead_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("BEDROCK_ENDPOINT_URL", "http://127.0.0.1:9")  # discard port: connection refused


@pytest.mark.unit
async def test_unreachable_endpoint_is_a_clear_error(dead_bedrock: None) -> None:
    agent = BedrockAgent(FakeTower(OK), system_prompt="sys")
    with pytest.raises(BedrockUnavailable) as exc:
        await agent.ask("is my line ok")
    msg = str(exc.value)
    assert "Bedrock unavailable" in msg and agent.model_id in msg
    assert "Tower and the rest of the system are unaffected" in msg
    assert "Traceback" not in msg


@pytest.mark.unit
async def test_access_denied_is_a_clear_error() -> None:
    class Denied:
        def converse(self, **_kw: Any) -> Any:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")

    with pytest.raises(BedrockUnavailable, match="AccessDeniedException"):
        await BedrockAgent(FakeTower(OK), client=Denied(), system_prompt="sys").ask("hi")


@pytest.mark.unit
def test_cli_without_credentials(no_aws: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main(["say", "is my line ok"]) == run.EXIT_BEDROCK
    assert "no AWS credentials" in capsys.readouterr().err
    out = tmp_path / "corpus.md"
    assert run.main(["corpus", "--out", str(out)]) == run.EXIT_OK  # skipped, not failed
    assert "SKIPPED" in capsys.readouterr().out and not out.exists()
    assert run.main(["corpus", "--agent", "scripted"]) == run.EXIT_BEDROCK


@pytest.mark.integration
async def test_tower_unaffected_by_a_bedrock_failure(ref_stack: RefStack, dead_bedrock: None) -> None:
    async with ref_stack.tower() as tower:
        with pytest.raises(BedrockUnavailable):
            await BedrockAgent(tower, system_prompt="sys").ask("is my line ok")
        turn = await ScriptedAgent(tower).ask(
            "is my line ok", expect=ToolCall("line_is_ok", {"line": "self"})
        )
    assert turn.reason_codes == ["OK"]


@pytest.mark.unit
def test_nothing_depends_on_the_reference_client() -> None:
    """No package or product service imports `ref_client`; no compose/helm service waits on it.

    The rule (09 §1, design rule 1): the product — Tower, Alerts, the binding page, the mock, `packages/` — never
    has the model on its path. The demo UI (doc 11) is the one exception, by design: laptop-only demo tooling
    outside paths A/B/C (e2e §7) that drives Tower *through* the reference client, as `make demo` does. It must
    stay out of the product in turn: nothing imports `demo_ui` either.
    """
    pattern = re.compile(r"^\s*(from|import)\s+ref_client\b", re.M)
    tooling = re.compile(r"^\s*(from|import)\s+demo_ui\b", re.M)
    offenders = []
    for base in ("packages", "services"):
        for f in (ROOT / base).rglob("*.py"):
            if "ref-client" in f.parts or "demo-ui" in f.parts or ".venv" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text) or tooling.search(text):
                offenders.append(str(f.relative_to(ROOT)))
    assert offenders == []
    compose = ROOT / "deploy" / "compose" / "docker-compose.yml"
    if compose.exists():
        import yaml

        services = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {}).get("services", {})
        waits = {name: list(svc.get("depends_on") or []) for name, svc in services.items()}
        assert not [name for name, deps in waits.items() if "ref-client" in deps]
