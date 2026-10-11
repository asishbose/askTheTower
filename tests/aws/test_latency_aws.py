"""Nightly, live: the latency run against Tower on AgentCore Runtime (spike C, prompt 13 step 6) →
artifacts/latency-aws.md. Skips without AWS credentials, a deployment, or a bearer for Runtime's JWT
authorizer (TOWER_JWT)."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest

from tests.aws.conftest import ROOT

pytestmark = pytest.mark.nightly


def test_latency_against_runtime(aws_outputs: dict[str, Any]) -> None:
    jwt = os.environ.get("TOWER_JWT")
    if not jwt:
        pytest.skip("no TOWER_JWT: a bearer from the issuer configured as tower_jwt_discovery_url")
    out = ROOT / "artifacts" / "latency-aws.md"
    r = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(ROOT / "scripts" / "latency.py"),
            "--url",
            str(aws_outputs["tower_mcp_url"]),
            "--jwt",
            jwt,
            "--out",
            str(out),
            "-n",
            os.environ.get("LATENCY_N", "200"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=3600,
    )
    assert r.returncode in (0, 1), r.stderr  # 1 = measured but over budget: the number is still recorded
    text = out.read_text("utf-8")
    assert "p95" in text and "in-process" not in text.split("\n", 3)[0]
