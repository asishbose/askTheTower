"""Doc 11 §2 "Pane 5" (AWS pipeline), §11: each of the seven steps for absent, present and malformed evidence, the
probes 200 / 403 / 404 / timeout over `httpx.MockTransport` (no network), "Nothing deployed yet", nothing raises,
no request carries `Authorization`, and the module imports no AWS SDK and runs no process."""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from demo_ui.ops import aws_status
from demo_ui.ops.aws_status import AwsStatus, Checklist, Files

pytestmark = pytest.mark.unit

SERVICES = ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"]
API = "https://abc123.execute-api.us-east-1.amazonaws.com"
ENV_AWS = (
    f"# generated\nTOWER_URL=https://runtime.example/mcp\nBINDING_URL={API}\nWEB_CHAT_URL=https://d1.cloudfront.net\n"
    f"MOCK_URL={API}/carrier-admin\nDEMO_ADMIN_BEARER=dab-secret-value\n"
)


class Wire:
    def __init__(self, answers: dict[str, int | Exception] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.requests.append(r)
        for suffix, answer in self.answers.items():
            if str(r.url).endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return httpx.Response(answer)
        return httpx.Response(200)


def checklist_for(root: Path, wire: Wire) -> Checklist:
    import asyncio

    async def go() -> Checklist:
        async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
            return await AwsStatus(Files.under(root), http).checklist()

    return asyncio.run(go())


def state(c: Checklist, n: int) -> tuple[str, str]:
    step = c.steps[n - 1]
    assert step.id == n
    return step.state, step.detail


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def everything(root: Path) -> None:
    write(
        root,
        "artifacts/tf-outputs-ecr.json",
        json.dumps({"ecr_repositories": {"value": {s: f"r/{s}" for s in SERVICES}}}),
    )
    write(root, "artifacts/image-tag", "abc1234\n")
    write(root, "deploy/.env.aws", ENV_AWS)
    users = write(root, "artifacts/cognito-users.json", json.dumps({"asish": "sub-a-1", "mom": "sub-m-2"}))
    seed = write(
        root, "artifacts/seed-aws.json", json.dumps({"mock_loaded": True, "people": ["asish", "mom"]})
    )
    os.utime(users, (1_000, 1_000))
    os.utime(seed, (2_000, 2_000))
    write(root, "artifacts/web-chat-sync.json", json.dumps({"url": "https://d1.cloudfront.net", "files": 3}))
    write(
        root,
        "artifacts/test-report.md",
        "| Layer |\n| End to end (ENV=aws) | 17 | 0 | 0 | 3 | 20 | 300 s | x |\n",
    )


def test_nothing_deployed_yet(tmp_path: Path) -> None:
    wire = Wire()
    c = checklist_for(tmp_path, wire)
    assert [s.state for s in c.steps] == ["todo"] * 5 + ["off", "todo"]
    assert (
        c.headline == "Nothing deployed yet"
    )  # step 6 is `off` (closed admin path), which counts as nothing
    assert [s.id for s in c.steps] == list(range(1, 8)) and wire.requests == []
    assert all(s.command for s in c.steps) and c.steps[0].command == "make ecr-up"


def test_all_todo_says_nothing_deployed_yet() -> None:
    steps = tuple(aws_status._step(n, "todo") for n in range(1, 8))
    assert Checklist(steps).headline == "Nothing deployed yet"
    one_done = (aws_status._step(1, "done"), *steps[1:])
    assert Checklist(one_done).headline == "1 of 7 steps done"


def test_everything_done_and_no_request_carries_a_bearer(tmp_path: Path) -> None:
    everything(tmp_path)
    wire = Wire()
    c = checklist_for(tmp_path, wire)
    assert [s.state for s in c.steps] == ["done"] * 7, c.steps
    assert state(c, 2) == ("done", "tag abc1234") and state(c, 6) == ("done", "admin on")
    assert sorted(str(r.url) for r in wire.requests) == sorted(
        [f"{API}/healthz", "https://d1.cloudfront.net", f"{API}/carrier-admin/_admin/clock"]
    )
    assert all("authorization" not in r.headers for r in wire.requests)
    assert all(r.method == "GET" for r in wire.requests)
    assert not any(API in s.detail or "cloudfront" in s.detail for s in c.steps)  # no URL in any detail


@pytest.mark.parametrize(
    ("rel", "text", "n"),
    [
        ("artifacts/tf-outputs-ecr.json", "{not json", 1),
        ("artifacts/tf-outputs-ecr.json", json.dumps({"ecr_repositories": {"value": {"alerts": "r"}}}), 1),
        ("artifacts/tf-outputs-ecr.json", json.dumps([1, 2]), 1),
        ("artifacts/image-tag", "dev\n", 2),
        ("artifacts/image-tag", "abc1234\nabc1235\n", 2),
        ("deploy/.env.aws", "BINDING_URL=\n", 3),
        ("artifacts/cognito-users.json", json.dumps({"asish": "user-asish", "mom": "user-mom"}), 4),
        ("artifacts/cognito-users.json", "[]", 4),
        ("artifacts/web-chat-sync.json", "{}", 5),
        ("artifacts/web-chat-sync.json", "nope", 5),
        ("artifacts/test-report.md", "| End to end (ENV=aws) | 1 | x | 0 | 0 | 1 | 1 s | y |\n", 7),
        ("artifacts/test-report.md", "| End to end (ENV=aws) | 17 | 2 | 1 | 0 | 20 | 1 s | y |\n", 7),
    ],
)
def test_malformed_or_failing_evidence_is_that_steps_fail(
    tmp_path: Path, rel: str, text: str, n: int
) -> None:
    everything(tmp_path)
    write(tmp_path, rel, text)
    c = checklist_for(tmp_path, Wire())
    got, detail = state(c, n)
    assert got == "fail" and detail, (rel, c.steps[n - 1])
    others = [s.state for s in c.steps if s.id not in (n, n + 1)]
    assert set(others) <= {"done", "warn", "off", "fail"}  # the other steps go on


def test_an_unreadable_env_file_fails_its_three_steps_and_nothing_raises(tmp_path: Path) -> None:
    everything(tmp_path)
    (tmp_path / "deploy" / ".env.aws").write_bytes(b"\xff\xfe\x00bad")
    c = checklist_for(tmp_path, Wire())
    assert [state(c, n)[0] for n in (3, 5, 6)] == ["fail", "fail", "fail"]
    assert state(c, 1)[0] == "done"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (200, "done"),
        (401, "done"),  # decision 1: the open gate answers a headerless probe 401 (no Authorization)
        (403, "warn"),
        (404, "fail"),
        (httpx.ReadTimeout("slow"), "fail"),
        (httpx.ConnectError("x"), "fail"),
    ],
)
def test_admin_probe(tmp_path: Path, answer: int | Exception, expected: str) -> None:
    everything(tmp_path)
    c = checklist_for(tmp_path, Wire({"/_admin/clock": answer}))
    got, detail = state(c, 6)
    assert got == expected
    if answer == 403:
        assert detail == "your IP is not in demo_admin_cidrs"
    if isinstance(answer, httpx.ReadTimeout):
        assert "timeout" in detail


def test_admin_off_when_mock_url_is_empty(tmp_path: Path) -> None:
    everything(tmp_path)
    write(tmp_path, "deploy/.env.aws", ENV_AWS.replace(f"MOCK_URL={API}/carrier-admin", "MOCK_URL="))
    wire = Wire()
    c = checklist_for(tmp_path, wire)
    assert state(c, 6)[0] == "off" and state(c, 7)[0] == "done"  # `off` does not hold step 7 back
    assert not any("/_admin" in str(r.url) for r in wire.requests)


@pytest.mark.parametrize(
    ("answer", "expected"), [(200, "done"), (503, "fail"), (httpx.ReadTimeout("slow"), "fail")]
)
def test_deployed_probe(tmp_path: Path, answer: int | Exception, expected: str) -> None:
    everything(tmp_path)
    c = checklist_for(tmp_path, Wire({"/healthz": answer}))
    got, detail = state(c, 3)
    assert got == expected
    if expected == "fail":
        assert detail.startswith("deployed but not answering")
        assert state(c, 4)[1].startswith("after step 3")


def test_users_without_seed_warn_and_a_stale_seed_warns(tmp_path: Path) -> None:
    everything(tmp_path)
    seed = tmp_path / "artifacts" / "seed-aws.json"
    os.utime(seed, (500, 500))
    assert state(checklist_for(tmp_path, Wire()), 4) == ("warn", "users changed since the seed: seed again")
    seed.unlink()
    assert state(checklist_for(tmp_path, Wire()), 4) == ("warn", "users created, seed not run")
    (tmp_path / "artifacts" / "cognito-users.json").unlink()
    assert state(checklist_for(tmp_path, Wire()), 4)[0] == "todo"


def test_web_chat_redeployed_since_the_sync_warns(tmp_path: Path) -> None:
    everything(tmp_path)
    write(tmp_path, "artifacts/web-chat-sync.json", json.dumps({"url": "https://old.cloudfront.net"}))
    assert state(checklist_for(tmp_path, Wire()), 5) == ("warn", "re-deployed since: sync again")
    c = checklist_for(tmp_path, Wire({"cloudfront.net": 404}))
    assert state(c, 5)[0] == "warn"
    everything(tmp_path)
    assert state(checklist_for(tmp_path, Wire({"cloudfront.net": 404})), 5)[0] == "fail"


def test_report_not_run_here_is_todo(tmp_path: Path) -> None:
    write(
        tmp_path,
        "artifacts/test-report.md",
        "| End to end (ENV=aws) | – | – | – | – | – | – | not run here |\n",
    )
    assert state(checklist_for(tmp_path, Wire()), 7)[0] == "todo"


def test_checklist_is_cached_for_five_seconds(tmp_path: Path) -> None:
    import asyncio

    everything(tmp_path)
    wire = Wire()
    now = [0.0]

    async def go(advance: Callable[[], None]) -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
            s = AwsStatus(Files.under(tmp_path), http, monotonic=lambda: now[0])
            await s.checklist()
            advance()
            await s.checklist()
            return len(wire.requests)

    assert asyncio.run(go(lambda: now.__setitem__(0, 4.0))) == 3
    wire.requests.clear()
    assert asyncio.run(go(lambda: now.__setitem__(0, 10.0))) == 6


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


def test_admin_open_without_the_demo_admin_bearer_warns(tmp_path: Path) -> None:
    everything(tmp_path)
    write(tmp_path, "deploy/.env.aws", ENV_AWS.replace("DEMO_ADMIN_BEARER=dab-secret-value\n", ""))
    for answer in (200, 401):
        c = checklist_for(tmp_path, Wire({"/_admin/clock": answer}))
        got, detail = state(c, 6)
        assert got == "warn" and "DEMO_ADMIN_BEARER" in detail and "dab-secret-value" not in detail
