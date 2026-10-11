"""The demo's HTTP controls (mock admin + binding-page grant admin) and the CLI's failure paths, without a stack."""

from __future__ import annotations

import json

import httpx
import pytest
from ref_client import run
from ref_client.demo import (
    ALERTS_SETTLE_S_LOCAL,
    STORIES,
    Advance,
    BindingAdminGrants,
    Control,
    DemoError,
    alerts_settle_s,
)

pytestmark = pytest.mark.unit


async def test_grant_admin_contract() -> None:
    seen: list[dict[str, object]] = []

    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_admin/grants"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    g = BindingAdminGrants("http://bind.test/", transport=httpx.MockTransport(ok))
    await g.set_mom_grant("revoke")
    await g.aclose()
    assert seen == [
        {
            "owner_user_id": "user-mom",
            "grantee_user_id": "user-asish",
            "grant": "watch",
            "alias": "mom",
            "action": "revoke",
        }
    ]


async def test_missing_grant_admin_is_a_clear_error() -> None:
    g = BindingAdminGrants("http://bind.test", transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
    with pytest.raises(DemoError, match="BIND_ADMIN=1"):
        await g.set_mom_grant("grant")
    await g.aclose()


def test_demo_with_no_stack_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MOCK_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TOWER_URL", "http://127.0.0.1:9/mcp")
    assert run.main(["demo", "--agent", "scripted", "--no-write"]) == run.EXIT_TOWER
    assert "demo control call failed: ConnectError" in capsys.readouterr().err


# --- pacing: Alerts must see each transplant jump before the line changes again (06 §11.4) ---------------


class _Settings:
    async def save_transplant(self) -> None:
        return None

    async def reset(self) -> None:
        return None


def _clock_mock() -> httpx.AsyncClient:
    def clock(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_admin/clock"
        return httpx.Response(200, json={"fired": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(clock), base_url="http://mock.test")


async def test_an_alerts_jump_waits_for_alerts_and_a_plain_one_does_not() -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    mock = _clock_mock()
    control = Control(
        mock=mock,
        grants=BindingAdminGrants("http://bind.test"),
        settings=_Settings(),
        alerts_settle_s=2.5,
        sleep=sleep,
    )
    await control.advance(12)
    assert slept == []
    await control.advance(20, settle=True)
    assert slept == [2.5]
    await mock.aclose()


async def test_no_settle_time_means_no_wait() -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    mock = _clock_mock()
    control = Control(
        mock=mock, grants=BindingAdminGrants("http://bind.test"), settings=_Settings(), sleep=sleep
    )
    await control.advance(20, settle=True)
    assert slept == []
    await mock.aclose()


def test_only_the_transplant_jumps_wait_for_alerts() -> None:
    marked = {
        (story.name, step.minutes)
        for story in STORIES
        for step in story.steps
        if isinstance(step, Advance) and step.alerts
    }
    assert marked == {("transplant", 20), ("transplant", 15)}


@pytest.mark.parametrize(
    ("env", "environ", "expected"),
    [
        ("local", {}, ALERTS_SETTLE_S_LOCAL),
        ("eks", {}, 0.0),
        ("aws", {}, 0.0),
        ("local", {"DEMO_ALERTS_SETTLE_S": "0"}, 0.0),
        ("aws", {"DEMO_ALERTS_SETTLE_S": "4"}, 4.0),
    ],
)
def test_settle_time_from_the_environment(env: str, environ: dict[str, str], expected: float) -> None:
    assert alerts_settle_s(env, environ) == expected


def test_a_negative_settle_time_is_refused() -> None:
    with pytest.raises(ValueError, match="DEMO_ALERTS_SETTLE_S"):
        alerts_settle_s("local", {"DEMO_ALERTS_SETTLE_S": "-1"})
