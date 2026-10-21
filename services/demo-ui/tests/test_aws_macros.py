"""Doc 11 §5, §7, §11 (prompt 22, U2/U3): on ENV=aws with the admin path open, Moment 1–3 run over the admin
routes with this UI's bearer-mode `BindingAdmin` as the `GrantControl` and the Cognito subs; Reset is the HTTP
reset; Transplant is off with its caption; the pill reads `admin on`; the SMS lane re-reads with an overlap and
drops what it has; `/healthz` carries no token."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from ref_client.demo import STORIES
from tower_consent import Store

from tests.privacy.patterns import phone_hits

from .fakes import Recorder, StoryAgent

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true"}
API = "https://api.test"
SUBS = ("sub-asish-0a1b", "sub-mom-2c3d")
# decision 1 (2026-10-11): on AWS every admin call carries DEMO_ADMIN_BEARER; the local tokens are set here only to
# prove they never leave the laptop on ENV=aws
ADMIN = "demo-admin-bearer-123456"
TOKENS = {
    "admin": ADMIN,
    "mock": "mock-token-123456",
    "alerts": "alerts-bearer-123456",
    "binding": "bind-token-123456",
}


def prefixed(inner: httpx.MockTransport, prefix: str, seen: list[httpx.Request]) -> httpx.MockTransport:
    """The gateway's `/carrier-admin/_admin/{proxy+}` route, seen from the mock: the prefix is gone (10 §2.1)."""

    def handle(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        assert r.url.path.startswith(prefix + "/_admin/")
        stripped = httpx.Request(
            r.method,
            r.url.copy_with(path=r.url.path.removeprefix(prefix)),
            headers=r.headers,
            content=r.content,
        )
        return inner.handle_request(stripped)

    return httpx.MockTransport(handle)


def aws_deps(store: Store, rec: Recorder, seen: list[httpx.Request]) -> Deps:
    s = Settings(
        env="aws",
        tower_url="https://runtime.example/mcp",
        mock_url=f"{API}/carrier-admin",
        mock_admin_token=TOKENS["mock"],
        binding_url="https://d1.cloudfront.net",
        binding_admin_url=API,
        bind_admin_token=TOKENS["binding"],
        alerts_url=API,
        alerts_bearer=TOKENS["alerts"],
        demo_admin_bearer=ADMIN,
        dynamo_endpoint=None,
        aws_users=SUBS,
    )
    transports = {
        "mock": prefixed(rec.mock(), "/carrier-admin", seen),
        "binding": rec.binding(),
        "alerts": rec.alerts(),
    }
    deps = build_deps(s, transports=transports, store=store, mode="scripted")

    @asynccontextmanager
    async def agents() -> AsyncIterator[Callable[[str], Any]]:
        agent = StoryAgent()

        async def agent_for(user_id: str) -> StoryAgent:
            return agent

        yield agent_for

    deps.runner.agents = agents
    return deps


async def test_moment_1_to_3_run_against_the_admin_path(store: Store, lines: dict[str, str]) -> None:
    rec, seen = Recorder(), []
    deps = aws_deps(store, rec, seen)
    result = await deps.runner.run("moment-3")
    assert result.error is None and result.failed == 0
    assert result.steps == sum(len(s.steps) for s in STORIES[:3])
    assert all(r.headers["authorization"] == f"Bearer {ADMIN}" for r in seen)
    assert seen[0].method == "POST" and seen[0].url.path == "/carrier-admin/_admin/scenarios/load"
    resets = [r for r in rec.requests if r.url.path == "/internal/reset"]
    assert len(resets) == 1 and resets[0].headers["authorization"] == f"Bearer {ADMIN}"
    assert SUBS[0] in resets[0].content.decode() and SUBS[1] in resets[0].content.decode()
    grants = [r for r in rec.requests if r.url.path == "/_admin/grants"]
    actions = [r.content.decode() for r in grants]
    # Reset's re-grant, run_demo's "start from the seeded grant", then Moment 3's revoke and grant
    assert [a.rsplit('"action":', 1)[1] for a in (x.replace(" ", "") for x in actions)] == [
        '"grant"}',
        '"grant"}',
        '"revoke"}',
        '"grant"}',
    ]
    assert all(r.headers["authorization"] == f"Bearer {ADMIN}" for r in grants)
    assert all(r.headers.get("authorization") == f"Bearer {ADMIN}" for r in rec.requests + seen)
    assert all(SUBS[1] in a and "user-mom" not in a for a in actions)
    assert all(str(r.url).startswith(API) for r in grants + resets)  # the execute-api URL, never CloudFront
    assert not any(r.url.path.endswith("/watch-settings") for r in rec.requests)
    await deps.aclose()


async def test_the_page_on_aws_with_the_admin_path_open(store: Store, lines: dict[str, str]) -> None:
    rec, seen = Recorder(), []
    deps = aws_deps(store, rec, seen)
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        pill = (await c.get("/fragment/pill")).text
        assert "scripted · ENV=aws · admin on" in pill
        controls = (await c.get("/fragment/conversation-controls")).text
        assert "disabled title=" not in controls and "transplant" not in controls  # hidden on ENV=aws
        assert controls.count('data-macro="') == 3
        assert 'value="mom"' not in controls  # Asish only (decision 3)
        r = await c.post("/macro/transplant", headers=HX)
        assert "Transplant runs locally" in r.text and deps.runner.task is None
        assert "Reset (make seed)" in (await c.get("/fragment/carrier-controls")).text
        r = await c.post("/carrier/fire", data={"who": "mom", "event": "sim_swap"}, headers=HX)
        assert "sim_swap on mom" in r.text
        health = (await c.get("/healthz")).text
        assert '"admin":"on"' in health.replace(" ", "")
        assert not any(t in health for t in TOKENS.values())
        rec.grants = [
            {"line_id": "ln_abcdefgh", "grantee_user_id": SUBS[0], "grant": "watch", "alias": "mom"}
        ]
        rec.sent = [{"n": 2, "at": "2026-10-11T10:00:00Z", "template": "SIM_SWAPPED_RECENT.sms", "role": "watcher",
                     "user_id": SUBS[0], "body": "mom's SIM moved to another device"}]  # fmt: skip
        deps.feed.subscribe()
        await deps.feed.tick()
        rec.sent.insert(0, {**rec.sent[0], "n": 1, "role": "line-holder"})  # n=1 became visible after n=2
        await deps.feed.tick()
    afters = [r.url.params["after"] for r in rec.requests if r.url.path == "/internal/sent"]
    assert afters == ["0", "0"]  # max(0, last - 5)
    sms = deps.feed.last["sms"]
    assert sms.count('class="ev ev-sms"') == 2 and sms.index('data-n="2"') < sms.index('data-n="1"')
    assert 'class="sms-phone"' in sms  # U4: the lane is a mocked phone
    resolve = [r for r in rec.requests if r.url.path == "/_admin/resolve"]
    assert resolve and resolve[0].url.params["user"] == SUBS[0]
    assert all(phone_hits(html) == [] for html in deps.feed.last.values())
    await deps.aclose()
