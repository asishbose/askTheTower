"""Sign the demo users in to the Terraform Cognito pool from the laptop (`make demo ENV=aws`, the e2e layer).

On AWS Tower sits behind AgentCore Runtime's JWT authorizer and verifies the token again itself
(`user_id` = `sub`); the local static bearer and `X-Tower-User` are refused there. This module turns a demo
user name (`asish`, `mom`) into a Cognito access token with `USER_PASSWORD_AUTH` on the web chat client:

- the pool's client id and region come from `artifacts/tf-outputs.json` (`cognito_client_id`, `region`);
- each user's sub from `artifacts/cognito-users.json` (`make cognito-users`);
- the password from `COGNITO_PASSWORD_<NAME>` (root `.env`, exported by make). It is never printed or stored.

Tokens are kept in memory for the run only (one hour of validity, the client's setting).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CognitoUnavailable(RuntimeError):
    """A file, a value or the sign-in itself is missing. The message names what, never a secret."""


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise CognitoUnavailable(f"{path} not readable — run {what} first") from None
    if not isinstance(data, dict):
        raise CognitoUnavailable(f"{path} is not a JSON object — run {what} again")
    return data


def _output(outputs: Mapping[str, Any], key: str) -> str:
    v = outputs.get(key)
    v = v.get("value") if isinstance(v, Mapping) else v
    if not isinstance(v, str) or not v:
        raise CognitoUnavailable(f"tf-outputs.json has no {key} — run make outputs ENV=aws")
    return v


@dataclass
class CognitoUsers:
    """The demo users on ENV=aws: their subs and, on demand, a fresh access token each."""

    client_id: str
    region: str
    subs: dict[str, str]
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False)
    client_factory: Callable[[str], Any] | None = field(default=None, repr=False)
    # name → (access token, expiry as time.time()); a token is reused until a minute before it expires
    # (Cognito's default lifetime is an hour) and re-fetched after that, so a long Control room session keeps going.
    _tokens: dict[str, tuple[str, float]] = field(default_factory=dict, repr=False)
    clock: Callable[[], float] = field(default=time.time, repr=False)
    REFRESH_MARGIN_S = 60.0

    @classmethod
    def from_artifacts(cls, artifacts: Path, environ: Mapping[str, str] | None = None) -> CognitoUsers:
        outputs = _read_json(artifacts / "tf-outputs.json", "make outputs ENV=aws")
        users = _read_json(artifacts / "cognito-users.json", "make cognito-users ENV=aws")
        subs = {str(k): str(v) for k, v in users.items()}
        missing = [n for n in ("asish", "mom") if not subs.get(n)]
        if missing:
            raise CognitoUnavailable(
                f"cognito-users.json has no {', '.join(missing)} — run make cognito-users ENV=aws"
            )
        return cls(
            client_id=_output(outputs, "cognito_client_id"),
            region=_output(outputs, "region"),
            subs=subs,
            environ=os.environ if environ is None else environ,
        )

    def sub(self, name: str) -> str:
        try:
            return self.subs[name]
        except KeyError:
            raise CognitoUnavailable(f"no Cognito user {name} in cognito-users.json") from None

    def forget(self, name: str) -> None:
        """Drop a cached token (after Tower answered 401 with it): the next access_token signs in again."""
        self._tokens.pop(name, None)

    def access_token(self, name: str) -> str:
        cached = self._tokens.get(name)
        if cached is not None and self.clock() < cached[1] - self.REFRESH_MARGIN_S:
            return cached[0]
        password = self.environ.get(f"COGNITO_PASSWORD_{name.upper().replace('-', '_')}", "")
        if not password:
            raise CognitoUnavailable(f"COGNITO_PASSWORD_{name.upper()} is not set in the root .env")
        client = (self.client_factory or _client)(self.region)
        try:
            r = client.initiate_auth(
                ClientId=self.client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": name, "PASSWORD": password},
            )
        except Exception as e:  # botocore ClientError and friends; the message carries no secret
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            hint = (
                " (the web chat client needs ALLOW_USER_PASSWORD_AUTH: run plan + deploy once)"
                if code == "InvalidParameterException"
                else ""
            )
            raise CognitoUnavailable(f"Cognito sign-in for {name} failed: {code}{hint}") from None
        token = (r.get("AuthenticationResult") or {}).get("AccessToken")
        if not token:
            challenge = r.get("ChallengeName")
            raise CognitoUnavailable(f"Cognito sign-in for {name}: no access token (challenge {challenge})")
        expires_in = float((r.get("AuthenticationResult") or {}).get("ExpiresIn") or 3600)
        self._tokens[name] = (str(token), self.clock() + expires_in)
        return str(token)


def _client(region: str) -> Any:
    import boto3

    return boto3.client("cognito-idp", region_name=region)
