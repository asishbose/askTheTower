"""ENV=aws: the Control room's Tower calls carry a Cognito access token per user, not the local bearer."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from demo_ui.config import Settings
from demo_ui.deps import Deps, cognito_users
from ref_client.cognito import CognitoUsers
from ref_client.mcp_client import TowerError

SUBS = {"asish": "a" * 36, "mom": "b" * 36}


class _Cognito:
    def __init__(self, token: str) -> None:
        self.token = token
        self.calls: list[tuple[str, str]] = []

    def initiate_auth(self, **kw: object) -> dict:
        params = kw["AuthParameters"]
        self.calls.append((str(params["USERNAME"]), str(params["PASSWORD"])))  # type: ignore[index]
        return {"AuthenticationResult": {"AccessToken": self.token}}


def _deps(settings: Settings, cognito: CognitoUsers | None) -> Deps:
    return Deps(  # only tower_config is exercised; the services stay unbuilt
        settings=settings, mode="scripted", feed=None, runner=None, ops=None,  # type: ignore[arg-type]
        aws_status=None, stack=None, gate=None, quiet=None, cognito=cognito,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_aws_tower_config_is_the_users_cognito_token() -> None:
    fake = _Cognito("tok-123")
    users = CognitoUsers(
        client_id="cid", region="us-east-2", subs=SUBS,
        environ={"COGNITO_PASSWORD_ASISH": "pw-a", "COGNITO_PASSWORD_MOM": "pw-m"},
        client_factory=lambda _region: fake,
    )
    deps = _deps(Settings(env="aws", tower_url="https://rt.example/mcp", tower_bearer="local-only"), users)
    cfg = deps.tower_config("asish")
    assert cfg.authorization == "Bearer tok-123" and cfg.bearer is None and cfg.user_id is None
    assert cfg.url == "https://rt.example/mcp" and fake.calls == [("asish", "pw-a")]
    deps._tower_config("user-mom")
    assert fake.calls[-1] == ("mom", "pw-m")
    deps.tower_config("asish")  # cached: no second sign-in
    assert len(fake.calls) == 2
    users.clock = lambda: time.time() + 3600  # the hour is up: sign in again
    deps.tower_config("asish")
    assert len(fake.calls) == 3
    users.forget("mom")  # after a 401
    deps._tower_config("user-mom")
    assert len(fake.calls) == 4


@pytest.mark.unit
def test_aws_without_the_artifacts_says_what_to_run_and_local_keeps_the_bearer() -> None:
    deps = _deps(Settings(env="aws"), None)
    with pytest.raises(TowerError, match="make cognito-users ENV=aws"):
        deps.tower_config("asish")
    users = CognitoUsers(client_id="cid", region="r", subs=SUBS, environ={})
    with pytest.raises(TowerError, match="COGNITO_PASSWORD_ASISH"):
        _deps(Settings(env="aws"), users).tower_config("asish")
    cfg = _deps(Settings(env="local", tower_bearer="lb"), None).tower_config("mom")
    assert cfg.bearer == "lb" and cfg.user_id == "user-mom" and cfg.authorization is None


@pytest.mark.unit
def test_cognito_users_is_none_locally_and_when_the_artifacts_are_missing(tmp_path) -> None:
    assert cognito_users(Settings(env="local")) is None
    assert cognito_users(replace(Settings(env="aws"), checkout=tmp_path)) is None
