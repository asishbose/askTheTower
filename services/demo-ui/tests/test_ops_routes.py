"""Doc 11 §2 "Pane 5" (Routes), §11: pane 5 over ASGI with fake neighbours. `HX-Request` (the app's guard) and a
same-origin `Origin` on both POSTs; a name outside the allowlist is 404; ENV=aws and container mode turn the runner
off with the doc's reason while the checklist still renders; a run puts `ops` and `ops-status` frames on `/events`;
one job at a time, also against the macros; the stack-down state; every checklist state renders.

The runner here gets test targets (`sh -c …`): no test ever runs a real `make` target of the allowlist.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from demo_ui.app import create_app, sse_frames
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from demo_ui.feed import Frame
from demo_ui.ops.allowlist import OpsTarget
from demo_ui.ops.runner import OpsRunner
from tower_consent import Store

from tests.privacy.patterns import phone_hits

from .fakes import Recorder

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true"}
SAME = {**HX, "Origin": "http://ui.test"}
U3 = "ENV=aws: the local runner is off; the AWS pipeline is on the right"


def checkout(root: Path) -> Path:
    (root / "mk").mkdir(parents=True, exist_ok=True)
    (root / "Makefile").write_text("all:\n", encoding="utf-8")
    return root


class Stack:
    """The `ops` transport: the three `/healthz` probes and the checklist's GETs."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: list[httpx.Request] = []

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.requests.append(r)
        return httpx.Response(self.status)


def build(store: Store, root: Path, stack: Stack, **settings: Any) -> Deps:
    rec = Recorder()
    s = Settings(mock_admin_token="mt", alerts_bearer="ab", checkout=root, **settings)
    transports = {
        "mock": rec.mock(),
        "binding": rec.binding(),
        "alerts": rec.alerts(),
        "ops": httpx.MockTransport(stack),
    }

    async def no_reset() -> None:
        return None

    deps = build_deps(
        s,
        transports=transports,
        store=store,
        reset=no_reset,
        mode="scripted",
        which=lambda _: "/usr/bin/make",
    )
    if deps.ops.off is None:  # swap in test targets: the allowlist's own are never run by a test
        deps.ops = OpsRunner(
            publish=deps.feed.publish,
            cwd=root,
            off=None,
            parent_env=dict(os.environ),
            secrets=lambda: ["planted-secret-value"],
            other_busy=lambda: deps.runner.busy,
            targets=(
                OpsTarget(
                    "up",
                    ("sh", "-c", "echo up +16135550101 planted-secret-value"),
                    "local",
                    "stack",
                    "Start",
                    False,
                    ("base",),
                ),
                OpsTarget(
                    "seed",
                    ("sh", "-c", "echo 'Bearer abc.def'; sleep 30"),
                    "local",
                    "stack",
                    "Seed",
                    True,
                    ("base",),
                ),
            ),
            batch_s=0.05,
        )
    return deps


@pytest.fixture
async def app_client(
    store: Store, lines: dict[str, str], tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, Deps, Stack]]:
    stack = Stack()
    deps = build(store, checkout(tmp_path), stack)
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        yield c, deps, stack
    await deps.ops.aclose()


async def headers_of(app: Any, path: str) -> dict[str, str]:
    """The response headers of a streaming route, without waiting for a body that never ends."""
    got: dict[str, str] = {}
    started = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            got.update({k.decode(): v.decode() for k, v in message["headers"]})
            started.set()

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"ui.test")], "scheme": "http", "server": ("ui.test", 80), "client": ("t", 1),
             "http_version": "1.1", "root_path": ""}  # fmt: skip
    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    return got


async def test_guards_on_the_ops_posts(app_client: Any) -> None:
    c, deps, _ = app_client
    for path in ("/ops/run/up", "/ops/stop"):
        assert (await c.post(path)).status_code == 403  # the app's HX-Request guard
        r = await c.post(path, headers={**HX, "Origin": "http://evil.example"})
        assert r.status_code == 403 and "Origin" in r.text
    r = await c.post("/ops/run/rm%20-rf", headers=SAME)
    assert r.status_code == 404
    assert (await c.post("/ops/run/push", headers=SAME)).status_code == 404
    assert deps.ops.task is None


async def test_a_run_streams_frames_on_events_and_one_job_at_a_time(app_client: Any) -> None:
    c, deps, _ = app_client
    q = deps.feed.subscribe()
    r = await c.post("/ops/run/up", headers=SAME)
    assert r.status_code == 202 and "up: started" in r.text
    await deps.ops.task
    frames: list[Frame] = []
    while not q.empty():
        frames.append(q.get_nowait())
    events = [f.event for f in frames]
    assert events[0] == "ops-status" and "ops" in events and events[-1] == "ops-status"
    body = "\n".join(f.sse() for f in frames)
    assert phone_hits(body) == [] and "planted-secret-value" not in body and "event: ops\n" in body
    assert 'data-state="pass"' in frames[-1].html

    r = await c.post("/ops/run/seed", headers=HX)  # no Origin header at all is fine (not a browser)
    assert r.status_code == 202
    r = await c.post("/ops/run/up", headers=SAME)
    assert r.status_code == 409 and "running: seed" in r.text
    r = await c.post("/macro/moment-1", headers=HX)
    assert r.status_code == 409 and "running: seed" in r.text  # one lock with the macros
    column = (await c.get("/fragment/ops")).text
    assert "Stop" in column and column.count("disabled") >= 2
    r = await c.post("/ops/stop", headers=SAME)
    assert r.status_code == 200 and "stopped" in r.text
    assert (await deps.ops.task).state == "stopped"
    assert "Bearer abc.def" not in "\n".join(deps.ops.backlog)
    assert (await c.post("/ops/stop", headers=SAME)).text.count("nothing runs") == 1

    gen = sse_frames(deps.feed, keepalive_s=5)
    first = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)  # the generator subscribes on its first step
    deps.feed.publish("ops", "<span class='ln'>x</span>")
    assert (await first).startswith("event: ops\ndata: ")
    await gen.aclose()
    app = c._transport.app  # type: ignore[attr-defined]
    assert (await headers_of(app, "/events"))["content-type"].startswith("text/event-stream")


async def test_macro_holds_the_lock_against_ops(app_client: Any) -> None:
    c, deps, _ = app_client
    gate = asyncio.Event()

    async def held() -> None:
        await gate.wait()

    deps.runner.reset = held
    assert "started" in (await c.post("/macro/moment-1", headers=HX)).text
    r = await c.post("/ops/run/up", headers=SAME)
    assert r.status_code == 409 and "macro" in r.text
    deps.runner.task.cancel()


async def test_stack_down_disables_what_needs_it_and_up_is_the_call_to_action(app_client: Any) -> None:
    c, _, stack = app_client
    up = (await c.get("/fragment/ops")).text
    assert "waiting for the stack" not in up and 'data-target="seed"' in up
    assert {r.url.path for r in stack.requests} == {"/healthz"} and len(stack.requests) == 3
    assert all("authorization" not in r.headers for r in stack.requests)
    stack.status = 503
    down = (await c.get("/fragment/ops")).text
    assert "waiting for the stack: run <code>up</code>" in down
    seed = down[down.index('data-target="seed"') :].split(">", 1)[0]
    assert "disabled" in seed and "waiting for the stack: run up" in seed
    upb = down[down.rindex("<button", 0, down.index('data-target="up"')) :].split(">", 1)[0]
    assert "primary" in upb and "disabled" not in upb


async def test_the_checklist_renders_every_state(app_client: Any, tmp_path: Path) -> None:
    c, _, stack = app_client
    a = tmp_path / "artifacts"
    a.mkdir()
    (a / "tf-outputs-ecr.json").write_text(json.dumps({"ecr_repositories": {s: "r" for s in
        ("mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client")}}))  # fmt: skip
    (a / "image-tag").write_text("not a tag\n")  # fail
    (a / "terraform-plan.txt").write_text("NOT RUN\n")  # warn: summary not found
    (a / "cognito-users.json").write_text(json.dumps({"asish": "s-1", "mom": "s-2"}))  # done
    html = (await c.get("/fragment/ops-checklist")).text
    for st in ("todo", "done", "warn", "fail", "off"):
        assert f'data-state="{st}"' in html, st
    assert html.count('class="btn copy"') == 16 and "make ecr-up ENV=aws" in html  # 14 steps + open + close
    assert 'aria-current="step"' in html and "AWS runs are off" in html  # the gate is off here (no flag)
    assert phone_hits(html) == []
    assert stack.requests == []  # no .env.aws: nothing to probe
    assert (await c.get("/fragment/ops-checklist", params={"fresh": "1"})).status_code == 200


async def test_env_aws_runs_nothing_and_the_checklist_still_renders(
    store: Store, lines: dict[str, str], tmp_path: Path
) -> None:
    stack = Stack()
    deps = build(store, checkout(tmp_path), stack, env="aws", mock_url="", alerts_url="")
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        r = await c.post("/ops/run/up", headers=SAME)
        assert r.status_code == 403 and U3 in r.text
        assert U3 in (await c.get("/fragment/ops")).text
        assert "Nothing deployed yet" in (await c.get("/fragment/ops-checklist")).text
        health = (await c.get("/healthz")).json()
        assert health["ops"] == {"runner": False, "aws": "off", "running": False} and health["admin"] == "off"
    assert deps.ops.task is None and stack.requests == []

    container = build(store, checkout(tmp_path), Stack(), published_loopback=True)
    assert container.ops.off == "runs only in `make showcase-ui` on the host"
    no_make = build_deps(
        Settings(checkout=tmp_path), store=store, reset=None, mode="scripted", which=lambda _: None,
        transports={"ops": httpx.MockTransport(Stack())},
    )  # fmt: skip
    assert no_make.ops.off == "make not found"
    await no_make.aclose()


CONTROL_IDS = ("pill", "run-status", "stage", "announcer", "panes", "pane-conversation", "stepper", "log",
               "pane-carrier", "carrier", "pane-feed", "sms", "audit", "pane-binding", "qr", "grants",
               "binding-status")  # fmt: skip


async def test_the_page_has_two_tabs_and_the_control_room_keeps_its_ids(app_client: Any) -> None:
    c, _, stack = app_client
    page = (await c.get("/")).text
    assert '<nav class="tabs"' in page and ">Control room</a>" in page and 'href="#ops"' in page
    assert '<section id="tab-ops" class="ops-tab" hidden' in page and 'id="pane-ops"' not in page
    for i in CONTROL_IDS:
        assert page.count(f'id="{i}"') == 1, i
    for frag in (
        "/fragment/ops-header",
        "/fragment/ops-checklist",
        "/fragment/ops-teardown",
        "/fragment/ops",
    ):
        assert f'hx-get="{frag}"' in page and frag in page
    assert page.count("ops:open from:body") == 4  # nothing loads (no probe) until the Ops tab is shown
    assert stack.requests == []


async def test_header_and_teardown_with_the_gate_off_and_on(
    store: Store, lines: dict[str, str], tmp_path: Path
) -> None:
    root = checkout(tmp_path)
    off = build(store, root, Stack())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(off)), base_url="http://ui.test"
    ) as c:
        header = (await c.get("/fragment/ops-header")).text
        assert (
            'data-gate="off"' in header
            and "AWS runs: off: AWS runs are off: start the UI with make ops-ui" in header
        )
        teardown = (await c.get("/fragment/ops-teardown")).text
        assert "stack: unknown · ECR: unknown" in teardown and teardown.count("disabled") >= 4
        assert 'placeholder="type down"' in teardown and 'placeholder="type down-all"' in teardown
    await off.aclose()

    (root / ".env").write_text("A=1\n")
    on = build(store, root, Stack(), aws_ops="1")

    async def passes() -> Any:
        from demo_ui.ops.probes import ProbeResult

        return ProbeResult(True)

    async def quiet(**_: Any) -> Any:
        from demo_ui.ops.envload import Facts
        from demo_ui.ops.probes import ProbeResult, ProbeSnapshot

        return ProbeSnapshot(facts=ProbeResult(True, Facts("", "us-east-1", True, 2)),
                             sts=ProbeResult(True, "123456789012"))  # fmt: skip

    on.gate.config_check = passes
    on.quiet.snapshot = quiet  # type: ignore[method-assign]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(on)), base_url="http://ui.test"
    ) as c:
        header = (await c.get("/fragment/ops-header")).text
        assert 'data-gate="on"' in header and "AWS runs: on" in header
        assert "key pair from .env" in header and "us-east-1" in header and "account 1234-5678-9012" in header
        assert phone_hits(header) == []
        teardown = (await c.get("/fragment/ops-teardown")).text
        assert "data-why" not in teardown and "AWS runs are off" not in teardown
        html = (await c.get("/fragment/ops-checklist")).text
        first = html[html.index('data-run="aws-ecr-up"') - 60 : html.index('data-run="aws-ecr-up"') + 200]
        assert "data-why" not in first and "disabled" not in first  # step 1: enabled
        assert 'data-why="after 1 · ECR repositories (idempotent)"' in html
        close = html[html.index('data-run="aws-admin-close"') :].split(">", 1)[0]
        assert "data-why" not in close  # no order: only its word
    await on.aclose()
