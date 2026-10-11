"""Shared by the ref-client tests: paths, the golden files, and a demo run against the in-process stack."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from ref_client.agent import has_aws_credentials
from ref_client.demo import DemoRun, run_demo

from tests.helpers.transcripts import GOLDEN, STORIES  # the one golden set: tests/e2e/golden (prompt 18)

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
__all__ = ["GOLDEN", "ROOT", "STORIES", "golden", "needs_bedrock", "scripted_demo", "transcripts_dir"]

needs_bedrock = pytest.mark.skipif(
    not has_aws_credentials(),
    reason="no AWS credentials: Bedrock (BEDROCK_MODEL_ID) not reachable here — RUN-ALL Decisions: skipped, not failed",
)


def golden(name: str) -> dict[str, Any]:
    return dict(json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8")))


def transcripts_dir(tmp_path: Path) -> Path:
    """`REF_TRANSCRIPTS_WRITE=1` writes the run to artifacts/transcripts/ (the committed evidence)."""
    return ROOT / "artifacts" / "transcripts" if os.environ.get("REF_TRANSCRIPTS_WRITE") == "1" else tmp_path


async def scripted_demo(agent_for: Any, control: Any, out_dir: Path | None) -> DemoRun:
    try:
        return await run_demo(agent_for, control, env="local", out_dir=out_dir, echo=lambda _l: None)
    finally:
        await agent_for.close()
