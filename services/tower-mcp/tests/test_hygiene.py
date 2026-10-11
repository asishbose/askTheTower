"""Guardrails: no model in the request path (rule 1), summaries only from tower_policy, no number formatting."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[1] / "src" / "tower_mcp"
FORBIDDEN = ("anthropic", "openai", "strands", "langchain", "bedrock")


def _sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(SRC.rglob("*.py"))]


def test_no_llm_sdk_import_in_source() -> None:
    for path, text in _sources():
        for name in FORBIDDEN:
            assert not re.search(rf"^\s*(import|from)\s+{name}", text, re.M | re.I), (path, name)
        assert "bedrock" not in text.lower(), path


def test_importing_the_server_loads_no_llm_sdk_and_no_bedrock_client() -> None:
    code = (
        "import sys, boto3\n"
        "made = []\n"
        "orig = boto3.client\n"
        "boto3.client = lambda name, *a, **k: (made.append(name), orig(name, *a, **k))[1]\n"
        "import tower_mcp.server, tower_mcp.deps\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('anthropic','openai','strands','langchain')]\n"
        "assert not bad, bad\n"
        "assert not any('bedrock' in n for n in made), made\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)  # noqa: S603
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr


def test_summaries_come_from_tower_policy() -> None:
    for path, text in _sources():
        assert not re.search(r"summary\s*=\s*f[\"']", text), path
        assert ".format(" not in text, path


def test_no_carrier_check_in_watch_line() -> None:
    text = (SRC / "tools" / "watch_line.py").read_text(encoding="utf-8")
    for call in ("sim_swap_check", "sim_swap_date", "call_forwarding(", "reachability(", "deps.carrier"):
        assert call not in text
