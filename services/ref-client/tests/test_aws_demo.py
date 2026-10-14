"""`make demo ENV=aws` without a stack: Cognito sign-in per user, and every admin call through the demo admin path
with `DEMO_ADMIN_BEARER` and the users' subs (decision 1, 2026-10-11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ref_client.cognito import CognitoUnavailable, CognitoUsers
from ref_client.demo import AWS_STORIES, STORY_NAMES, AlertsReset, BindingAdminGrants, DemoError, aws_control

pytestmark = pytest.mark.unit

ASISH, MOM = "11111111-aaaa-4bbb-8ccc-000000000001", "22222222-aaaa-4bbb-8ccc-000000000002"
ADMIN = {
    "MOCK_URL": "https://api.test/carrier-admin",
    "BINDING_ADMIN_URL": "https://api.test/binding-admin",
    "ALERTS_URL": "https://api.test/alerts-admin",
    "DEMO_ADMIN_BEARER": "demo-bearer",
}


def _artifacts(tmp_path: Path) -> Path:
    (tmp_path / "tf-outputs.json").write_text(
        json.dumps({"cognito_client_id": {"value": "client-1"}, "region": {"value": "us-east-2"}})
    )
    (tmp_path / "cognito-users.json").write_text(json.dumps({"asish": ASISH, "mom": MOM}))
    return tmp_path


class _Cognito:
    class exceptions:  # noqa: N801 - boto3's shape
        pass

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def initiate_auth(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return {"AuthenticationResult": {"AccessToken": f"tok-{kw['AuthParameters']['USERNAME']}"}}


def test_users_sign_in_once_each_with_the_password_from_env(tmp_path: Path) -> None:
    fake = _Cognito()
    users = CognitoUsers.from_artifacts(_artifacts(tmp_path), {"COGNITO_PASSWORD_ASISH": "pw"})
    users.client_factory = lambda region: fake
    assert (users.sub("asish"), users.sub("mom")) == (ASISH, MOM)
    assert users.access_token("asish") == "tok-asish"
    assert users.access_token("asish") == "tok-asish"
    assert len(fake.calls) == 1
    assert fake.calls[0]["AuthFlow"] == "USER_PASSWORD_AUTH" and fake.calls[0]["ClientId"] == "client-1"
    with pytest.raises(CognitoUnavailable, match="COGNITO_PASSWORD_MOM") as e:
        users.access_token("mom")
    assert "pw" not in str(e.value)


def test_a_client_without_password_auth_says_to_deploy(tmp_path: Path) -> None:
    err = RuntimeError("x")
    err.response = {"Error": {"Code": "InvalidParameterException"}}  # type: ignore[attr-defined]
    users = CognitoUsers.from_artifacts(_artifacts(tmp_path), {"COGNITO_PASSWORD_ASISH": "pw"})
    users.client_factory = lambda region: _Cognito(err)
    with pytest.raises(CognitoUnavailable, match="ALLOW_USER_PASSWORD_AUTH"):
        users.access_token("asish")


def test_missing_artifacts_name_the_make_target(tmp_path: Path) -> None:
    with pytest.raises(CognitoUnavailable, match="make outputs ENV=aws"):
        CognitoUsers.from_artifacts(tmp_path, {})


def test_a_closed_admin_path_is_a_clear_error() -> None:
    with pytest.raises(DemoError, match="admin-open"):
        aws_control({**ADMIN, "DEMO_ADMIN_BEARER": ""}, asish=ASISH, mom=MOM)


async def test_admin_calls_carry_the_demo_bearer_and_the_subs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_ADMIN_TOKEN", "compose-token")  # the local token must not leak onto AWS
    control = aws_control(ADMIN, asish=ASISH, mom=MOM)
    assert control.mock.headers["Authorization"] == "Bearer demo-bearer"
    assert str(control.mock.base_url).startswith("https://api.test/carrier-admin")
    assert isinstance(control.grants, BindingAdminGrants)
    assert isinstance(control.settings, AlertsReset)
    for c in (control.mock, control.grants, control.settings):
        await c.aclose()

    seen: list[tuple[str, str, dict[str, Any]]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["Authorization"], json.loads(request.content)))
        return httpx.Response(200, json={})

    t = httpx.MockTransport(record)
    grants = BindingAdminGrants(ADMIN["BINDING_ADMIN_URL"], bearer="b", owner=MOM, grantee=ASISH, transport=t)
    await grants.set_mom_grant("revoke")
    reset = AlertsReset(ADMIN["ALERTS_URL"], "b", (ASISH, MOM), transport=t)
    await reset.reset()
    with pytest.raises(DemoError, match="stays local"):
        await reset.save_transplant()
    await grants.aclose()
    await reset.aclose()
    assert seen[0][:2] == ("/binding-admin/_admin/grants", "Bearer b")
    assert (seen[0][2]["owner_user_id"], seen[0][2]["grantee_user_id"]) == (MOM, ASISH)
    assert seen[1] == ("/alerts-admin/internal/reset", "Bearer b", {"user_ids": [ASISH, MOM]})


def test_the_transplant_story_stays_local() -> None:
    assert "transplant" in STORY_NAMES and "transplant" not in AWS_STORIES
    assert AWS_STORIES == ("moment-1", "moment-2", "moment-3")


def test_agentcore_calls_share_one_runtime_session() -> None:
    """Tower's log, 2026-10-14: without a session id every call started a new microVM (~7 s cold start)."""
    from ref_client.mcp_client import AGENTCORE_SESSION_ID, TowerConfig

    url = "https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Ax/invocations?qualifier=DEFAULT"
    a = TowerConfig(url=url, authorization="Bearer t").headers()
    b = TowerConfig(url=url, authorization="Bearer u").headers()
    assert a["Mcp-Session-Id"] == b["Mcp-Session-Id"] == AGENTCORE_SESSION_ID
    assert a["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] == AGENTCORE_SESSION_ID
    assert len(AGENTCORE_SESSION_ID) >= 33
    assert "Mcp-Session-Id" not in TowerConfig(url="http://localhost:8080/mcp").headers()
