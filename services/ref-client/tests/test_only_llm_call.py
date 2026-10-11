"""Prompt 11 guardrail / review A5: the Bedrock call in `ref_client/agent.py` is the only language-model call in
the repository. A repo-wide grep for LLM SDK imports and Bedrock model-invocation clients."""

from __future__ import annotations

import re

import pytest

from .helpers import ROOT

pytestmark = pytest.mark.unit

ALLOWED = {"services/ref-client/src/ref_client/agent.py"}
LLM_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(anthropic|openai|strands|strands_agents|langchain\w*|llama_index|litellm|cohere|"
    r"mistralai|ollama|google\.generativeai|google\.genai|vertexai|transformers|whisper)\b",
    re.M,
)
BEDROCK_INVOKE = re.compile(
    r"""["']bedrock-(?:agent-)?runtime["']|\.(?:invoke_model|invoke_model_with_response_stream|converse|"""
    r"""converse_stream)\s*\(""",
)
SKIP_PARTS = {".venv", "node_modules", "tests", "__pycache__", ".git"}


def _sources() -> list[str]:
    out = []
    for f in ROOT.rglob("*.py"):
        rel = f.relative_to(ROOT)
        if SKIP_PARTS & set(rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def test_only_the_reference_client_calls_a_model() -> None:
    hits = {}
    for rel in _sources():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        found = LLM_IMPORT.findall(text) + BEDROCK_INVOKE.findall(text)
        if found and rel not in ALLOWED:
            hits[rel] = found
    assert hits == {}, f"LLM SDK use outside the reference client: {hits}"


def test_the_one_call_is_where_we_say() -> None:
    text = (ROOT / "services/ref-client/src/ref_client/agent.py").read_text(encoding="utf-8")
    assert BEDROCK_INVOKE.search(text) and not LLM_IMPORT.search(text)


def test_no_llm_sdk_in_dependencies() -> None:
    names = re.compile(r'"(anthropic|openai|strands-agents|langchain[\w-]*|litellm|llama-index[\w-]*)\b')
    for f in [
        ROOT / "pyproject.toml",
        *ROOT.glob("packages/*/pyproject.toml"),
        *ROOT.glob("services/*/pyproject.toml"),
    ]:
        assert not names.search(f.read_text(encoding="utf-8")), f
