"""Shared fixtures for the Ops tab's stepper tests: evidence files for a fully deployed pipeline, a recording
`httpx.MockTransport`, and a pipeline builder with injected probe answers (no process, no network)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from demo_ui.ops.aws_status import AwsStatus, Files, Pipeline
from demo_ui.ops.probes import ProbeSnapshot
from demo_ui.ops.transcript import Transcripts

SERVICES = ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"]
API = "https://abc123.execute-api.us-east-1.amazonaws.com"
BINDING = "https://dbind.cloudfront.net"
WEB_CHAT = "https://dpage.cloudfront.net"
ENV_AWS = (
    f"# generated\nTOWER_URL=https://runtime.example/mcp\nBINDING_URL={BINDING}\nWEB_CHAT_URL={WEB_CHAT}\n"
    f"MOCK_URL={API}/carrier-admin\nDEMO_ADMIN_BEARER=dab-secret-value-123\n"
)
T0 = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
LATENCY = (
    "# Tower latency — AWS\n\n| Path | n | p50 ms | p95 ms | p99 ms | max ms | Gate |\n|---|---:|---:|---:|---:|---:|---|\n"
    "| line_is_ok — live (carrier) | 200 | 185.3 | 249.9 | 266.1 | 297.6 | pass |\n"
)
REPORT = "| Layer |\n| End to end (ENV=aws) | 17 | 0 | 0 | 3 | 20 | 300 s | x |\n"


class Wire:
    def __init__(self, answers: dict[str, int | Exception] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.requests.append(r)
        for suffix, answer in self.answers.items():
            if str(r.url).rstrip("/").endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return httpx.Response(answer)
        return httpx.Response(200)


def write(root: Path, rel: str, text: str, at: float | None = None) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if at is not None:
        os.utime(p, (at, at))
    return p


def transcripts(root: Path) -> Transcripts:
    return Transcripts(root / "artifacts" / "ops-log", wall=lambda: T0)


def ran(root: Path, name: str, state: str) -> None:
    t = transcripts(root).open(name)
    t.start(name, ("make", name))
    t.end(state, 0 if state == "pass" else 2, 12.0)


def everything(root: Path) -> None:
    """Every step's evidence, in the order `make` writes it (mtimes increasing)."""
    write(
        root,
        "artifacts/tf-outputs-ecr.json",
        json.dumps({"ecr_repositories": {"value": {s: f"acct.dkr/att-dev/{s}" for s in SERVICES}}}),
        1_000,
    )
    write(root, "artifacts/image-tag", "abc1234\n", 2_000)
    write(
        root,
        "deploy/terraform/envs/aws.tfvars",
        'region = "us-east-1"\ndemo_admin_cidrs = ["203.0.113.7/32"]\n',
        2_500,
    )
    write(root, "artifacts/terraform-plan.txt", "...\nPlan: 120 to add, 0 to change, 0 to destroy.\n", 3_000)
    outputs = {
        "binding_url": {"value": BINDING, "sensitive": False},
        "web_chat_url": {"value": WEB_CHAT, "sensitive": False},
        "sms_sandbox_commands": {"value": "+16135550101", "sensitive": True},
    }
    write(root, "artifacts/tf-outputs.json", json.dumps(outputs), 4_000)
    write(root, "deploy/.env.aws", ENV_AWS, 4_100)
    write(root, "artifacts/cognito-users.json", json.dumps({"asish": "sub-a-1", "mom": "sub-m-2"}), 5_000)
    write(
        root,
        "artifacts/seed-aws.json",
        json.dumps(
            {
                "mock_loaded": True,
                "people": ["asish", "mom"],
                "bound": ["asish", "mom"],
                "grants": ["mom>asish:watch"],
            }
        ),
        6_000,
    )
    write(
        root, "artifacts/web-chat-sync.json", json.dumps({"url": WEB_CHAT, "files": ["a", "b", "c"]}), 7_000
    )
    write(root, "artifacts/latency-aws.md", LATENCY, 8_000)
    write(root, "artifacts/test-report.md", REPORT, 9_000)
    for name in ("aws-web-chat-url", "aws-demo", "aws-test-e2e"):
        ran(root, name, "pass")  # fmt: skip


def pipeline_for(root: Path, wire: Wire, snap: ProbeSnapshot | None = None) -> Pipeline:
    async def go() -> Pipeline:
        async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
            return await AwsStatus(Files.under(root), http, transcripts(root)).pipeline(snap)

    return asyncio.run(go())


def state(p: Pipeline, n: int) -> tuple[str, str]:
    step = p.steps[n - 1]
    assert step.n == n
    return step.state, step.detail
