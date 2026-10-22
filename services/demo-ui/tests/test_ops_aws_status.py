"""Doc 11 §2 "AWS pipeline" (the stepper), §11: each of the fourteen steps for absent, present, malformed and stale
evidence; the GETs 200 / 401 / 403 / 404 / timeout over `httpx.MockTransport` (no network); "Nothing deployed yet";
nothing raises; no request carries `Authorization`; evidence never holds an admin URL, a bearer or the `/32`; the
module imports no AWS SDK and runs no process (the probes are injected)."""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from demo_ui.ops import aws_status
from demo_ui.ops.aws_status import AwsStatus, Files

from .opsfix import (
    API,
    BINDING,
    ENV_AWS,
    WEB_CHAT,
    Wire,
    everything,
    pipeline_for,
    ran,
    state,
    transcripts,
    write,
)

pytestmark = pytest.mark.unit


def test_nothing_deployed_yet(tmp_path: Path) -> None:
    wire = Wire()
    p = pipeline_for(tmp_path, wire)
    assert [s.state for s in p.steps] == ["todo"] * 14 and p.admin.state == "off"
    assert p.headline == "Nothing deployed yet" and p.next_id == 1 and p.banner is None
    assert [s.n for s in p.steps] == list(range(1, 15)) and wire.requests == []
    assert [s.target for s in p.steps][:3] == ["aws-ecr-up", "aws-push", "aws-plan"]
    assert p.steps[0].title == "1 · ECR repositories (idempotent)"


def test_everything_done_and_no_request_carries_a_bearer(tmp_path: Path) -> None:
    everything(tmp_path)
    wire = Wire()
    p = pipeline_for(tmp_path, wire)
    assert [s.state for s in p.steps] == ["done"] * 14, [(s.n, s.state, s.detail) for s in p.steps]
    assert p.headline == "14 of 14 steps done" and p.next_id is None
    assert state(p, 2) == ("done", "") and p.steps[1].evidence == "tag abc1234"
    assert p.steps[2].evidence == "+120 ~0 −0" and p.steps[2].chips is not None
    assert p.steps[3].evidence == f"{BINDING} · {WEB_CHAT}"
    assert p.steps[4].evidence == "5 keys · admin on"
    assert p.steps[5].evidence == "2 users: asish, mom"
    assert p.steps[6].evidence == "bound: asish, mom · grants: mom>asish:watch"
    assert p.steps[7].evidence == "3 files uploaded" and p.steps[8].evidence == WEB_CHAT
    assert p.steps[9].evidence == "bound: asish, mom" and p.steps[10].evidence.startswith(
        "demo ok · 2026-10-11"
    )
    assert p.steps[12].evidence.startswith("pass · ") and p.steps[13].evidence == "17 passed of 20"
    assert p.admin.state == "done" and p.admin.tfvars == "open"
    assert sorted(str(r.url).rstrip("/") for r in wire.requests) == sorted(
        [f"{BINDING}/healthz", WEB_CHAT, f"{API}/carrier-admin/_admin/clock"]
    )
    assert all("authorization" not in r.headers for r in wire.requests)
    assert all(r.method == "GET" for r in wire.requests)
    shown = " ".join(f"{s.evidence} {s.detail}" for s in p.steps) + p.admin.detail
    assert (
        API not in shown and "dab-secret" not in shown and "203.0.113.7" not in shown and "+1613" not in shown
    )


@pytest.mark.parametrize(
    ("rel", "text", "n"),
    [
        ("artifacts/tf-outputs-ecr.json", "{not json", 1),
        ("artifacts/tf-outputs-ecr.json", json.dumps({"ecr_repositories": {"value": {"alerts": "r"}}}), 1),
        ("artifacts/tf-outputs-ecr.json", json.dumps([1, 2]), 1),
        ("artifacts/image-tag", "dev\n", 2),
        ("artifacts/image-tag", "abc1234\nabc1235\n", 2),
        ("artifacts/tf-outputs.json", "{broken", 4),
        ("deploy/.env.aws", "BINDING_URL=\n", 5),
        ("artifacts/cognito-users.json", json.dumps({"asish": "user-asish", "mom": "user-mom"}), 6),
        ("artifacts/cognito-users.json", "[]", 6),
        ("artifacts/seed-aws.json", "[1]", 7),
        ("artifacts/web-chat-sync.json", "{}", 8),
        ("artifacts/web-chat-sync.json", "nope", 8),
        ("artifacts/test-report.md", "| End to end (ENV=aws) | 1 | x | 0 | 0 | 1 | 1 s | y |\n", 14),
        ("artifacts/test-report.md", "| End to end (ENV=aws) | 17 | 2 | 1 | 0 | 20 | 1 s | y |\n", 14),
    ],
)
def test_malformed_or_failing_evidence_is_that_steps_fail(
    tmp_path: Path, rel: str, text: str, n: int
) -> None:
    everything(tmp_path)
    p0 = tmp_path / rel
    at = p0.stat().st_mtime
    write(tmp_path, rel, text, at)
    p = pipeline_for(tmp_path, Wire())
    got, detail = state(p, n)
    assert got == "fail" and detail, (rel, p.steps[n - 1])
    assert state(p, 1 if n != 1 else 2)[0] in {"done", "fail"}  # the others go on


def test_stale_evidence_warns(tmp_path: Path) -> None:
    everything(tmp_path)
    os.utime(tmp_path / "artifacts/terraform-plan.txt", (2_200, 2_200))  # older than aws.tfvars
    os.utime(tmp_path / "deploy/.env.aws", (3_900, 3_900))  # older than tf-outputs.json
    os.utime(tmp_path / "artifacts/seed-aws.json", (4_900, 4_900))  # older than cognito-users.json
    write(
        tmp_path,
        "artifacts/web-chat-sync.json",
        json.dumps({"url": "https://old.cloudfront.net", "files": 2}),
    )
    p = pipeline_for(tmp_path, Wire())
    assert state(p, 3) == ("warn", "inputs changed since the plan: plan again")
    assert state(p, 5) == ("warn", "outputs changed: write it again")
    assert state(p, 7) == ("warn", "users changed since the seed: seed again")
    assert state(p, 8) == ("warn", "re-deployed since: sync again") and state(p, 9)[0] == "todo"
    write(tmp_path, "artifacts/terraform-plan.txt", "NOT RUN — no summary here\n")
    assert state(pipeline_for(tmp_path, Wire()), 3) == ("warn", "summary not found")


def test_an_unreadable_env_file_fails_its_steps_and_nothing_raises(tmp_path: Path) -> None:
    everything(tmp_path)
    (tmp_path / "deploy" / ".env.aws").write_bytes(b"\xff\xfe\x00bad")
    p = pipeline_for(tmp_path, Wire())
    assert [state(p, n)[0] for n in (5, 8)] == ["fail", "fail"]
    assert state(p, 1)[0] == "done"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (200, "done"),
        (401, "done"),  # decision 1: the open gate answers a headerless GET 401 (no Authorization)
        (403, "warn"),
        (404, "fail"),
        (httpx.ReadTimeout("slow"), "fail"),
        (httpx.ConnectError("x"), "fail"),
    ],
)
def test_admin_box_get(tmp_path: Path, answer: int | Exception, expected: str) -> None:
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire({"/_admin/clock": answer}))
    assert p.admin.state == expected
    if answer == 403:
        assert p.admin.detail == "your IP is not in demo_admin_cidrs"
    if isinstance(answer, httpx.ReadTimeout):
        assert "timeout" in p.admin.detail


def test_admin_box_without_the_bearer_warns_and_tfvars_mismatch_warns(tmp_path: Path) -> None:
    everything(tmp_path)
    write(tmp_path, "deploy/.env.aws", ENV_AWS.replace("DEMO_ADMIN_BEARER=dab-secret-value-123\n", ""), 4_100)
    p = pipeline_for(tmp_path, Wire({"/_admin/clock": 401}))
    assert p.admin.state == "warn" and "DEMO_ADMIN_BEARER" in p.admin.detail and "dab" not in p.admin.detail
    write(tmp_path, "deploy/.env.aws", ENV_AWS.replace(f"MOCK_URL={API}/carrier-admin", "MOCK_URL="), 4_100)
    wire = Wire()
    p = pipeline_for(tmp_path, wire)
    assert (p.admin.state, p.admin.detail) == ("warn", "tfvars open, deployment closed: run deploy")
    assert not any("/_admin" in str(r.url) for r in wire.requests)
    write(
        tmp_path,
        "deploy/terraform/envs/aws.tfvars",
        "demo_admin_cidrs = [] # managed by make admin-open / admin-close\n",
    )
    assert pipeline_for(tmp_path, Wire()).admin.state == "off"
    write(tmp_path, "deploy/.env.aws", ENV_AWS, 4_100)
    p = pipeline_for(tmp_path, Wire())
    assert (p.admin.state, p.admin.detail) == ("warn", "tfvars closed, deployment open: run deploy")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('demo_admin_cidrs = ["203.0.113.7/32"] # managed\n', "open"),
        ("demo_admin_cidrs = []\n", "closed"),
        ('demo_admin_cidrs = [\n  "1.2.3.4/32",\n]\n', "unreadable"),
        ('demo_admin_cidrs = []\ndemo_admin_cidrs = ["1.2.3.4/32"]\n', "unreadable"),
        ('demo_admin_cidrs = ["1.2.3.4/32", "5.6.7.8/32"]\n', "unreadable"),
        ('region = "x"\n', "absent"),
    ],
)
def test_tfvars_state(tmp_path: Path, text: str, expected: str) -> None:
    path = write(tmp_path, "aws.tfvars", text)
    assert aws_status.tfvars_state(path) == expected
    assert aws_status.tfvars_state(tmp_path / "missing.tfvars") == "absent"


@pytest.mark.parametrize(
    ("answer", "expected"), [(200, "done"), (503, "fail"), (httpx.ReadTimeout("slow"), "fail")]
)
def test_deploy_needs_the_binding_page_to_answer(
    tmp_path: Path, answer: int | Exception, expected: str
) -> None:
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire({"/healthz": answer}))
    got, detail = state(p, 4)
    assert got == expected
    if expected == "fail":
        assert detail.startswith("deployed but not answering")
        assert p.next_id == 4


def test_web_chat_page_must_answer(tmp_path: Path) -> None:
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire({"dpage.cloudfront.net": 404}))
    assert state(p, 9) == ("fail", "the page does not answer (404)")


def test_transcript_steps_pass_fail_none(tmp_path: Path) -> None:
    everything(tmp_path)
    ran(tmp_path, "aws-demo", "fail")
    p = pipeline_for(tmp_path, Wire())
    assert state(p, 11)[0] == "fail" and state(p, 11)[1].startswith("last run failed")
    ran(tmp_path, "aws-demo", "pass")
    assert state(pipeline_for(tmp_path, Wire()), 11)[0] == "done"
    for f in (tmp_path / "artifacts" / "ops-log").glob("*-aws-test-e2e.log"):
        f.unlink()
    assert state(pipeline_for(tmp_path, Wire()), 13)[0] == "todo"
    assert transcripts(tmp_path).last("aws-test-e2e") is None
    last = transcripts(tmp_path).last("aws-demo")
    assert last is not None and last.state == "pass"


def test_latency_placeholder_and_report_not_run_here_are_todo(tmp_path: Path) -> None:
    write(tmp_path, "artifacts/latency-aws.md", "# Tower latency\n\n> **NOT MEASURED.** nothing deployed\n")
    write(
        tmp_path,
        "artifacts/test-report.md",
        "| End to end (ENV=aws) | – | – | – | – | – | – | not run here |\n",
    )
    p = pipeline_for(tmp_path, Wire())
    assert state(p, 12) == ("todo", "not measured") and state(p, 14) == ("todo", "not run here")


def test_gets_are_cached_for_five_seconds(tmp_path: Path) -> None:
    everything(tmp_path)
    wire = Wire()
    now = [0.0]

    async def go(advance: float) -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
            s = AwsStatus(Files.under(tmp_path), http, transcripts(tmp_path), monotonic=lambda: now[0])
            now[0] = 0.0
            await s.pipeline()
            now[0] = advance
            await s.pipeline()
            return len(wire.requests)

    assert asyncio.run(go(4.0)) == 3
    wire.requests.clear()
    assert asyncio.run(go(10.0)) == 6


def test_the_module_imports_no_aws_sdk_and_runs_no_process() -> None:
    tree = ast.parse(Path(aws_status.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots & {"boto3", "botocore", "subprocess", "os", "shutil"} == set()
    source = Path(aws_status.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source and "create_subprocess" not in source and "Authorization" not in source


def test_garbage_everywhere_never_raises(tmp_path: Path) -> None:
    everything(tmp_path)
    for path in (tmp_path / "artifacts").glob("*"):
        if path.is_file():
            path.write_bytes(b"\x00\xff garbage {[")
    (tmp_path / "deploy" / "terraform" / "envs" / "aws.tfvars").write_bytes(b"\xff\xfe")
    p = pipeline_for(tmp_path, Wire())
    assert len(p.steps) == 14 and {s.state for s in p.steps} <= {"todo", "done", "warn", "fail"}


def test_a_dirty_tree_tag_is_still_one_tag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from demo_ui.ops.aws_status import IMAGE_TAG

    assert IMAGE_TAG.fullmatch("cb5695d-10221305") and IMAGE_TAG.fullmatch("cb5695d")
    assert not IMAGE_TAG.fullmatch("cb5695d-dirty") and not IMAGE_TAG.fullmatch("latest")
