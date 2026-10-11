"""`DirectClient` — CAMARA over HTTPS with httpx. Paths and versions come from the vendored specs
(`specs.py`); client-credentials tokens are cached per scope; the `device` / `phoneNumber` object is
built from the caller's decrypted E.164 inside the call and never logged.

Used locally against the mock, and on AWS wherever Gateway is not (05 §3: CIBA, or the cut line).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr
from tower_policy import ReasonCode

from camara_client import specs
from camara_client.breaker import DEFAULT_BREAKERS, BreakerRegistry
from camara_client.config import CarrierConfig
from camara_client.core import Answer, CarrierBase, error_from_answer
from camara_client.errors import CarrierError, malformed_response
from camara_client.timeouts import profile_for

TOKEN_MARGIN_S = 30.0


class OAuthSession:
    """Client-credentials tokens cached per scope, and the auth-code exchange. Shared by
    `DirectClient` and the fake Gateway's outbound "Identity"."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token_url: str,
        client_id: str,
        secret: str,
        clock: Callable[[], float] = time.monotonic,
        on_status: Callable[[int], None] | None = None,
    ) -> None:
        self._http = http
        self._token_url = token_url
        self._client_id = client_id
        self._secret = SecretStr(secret)
        self._clock = clock
        self._on_status = on_status
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def __repr__(self) -> str:
        return f"OAuthSession(token_url={self._token_url!r}, client_id={self._client_id!r})"

    def drop(self, scope: str) -> None:
        self._cache.pop(scope, None)

    async def token(self, scope: str) -> str:
        cached = self._cache.get(scope)
        if cached and cached[1] > self._clock():
            return cached[0]
        lock = self._locks.setdefault(scope, asyncio.Lock())
        async with lock:
            cached = self._cache.get(scope)
            if cached and cached[1] > self._clock():
                return cached[0]
            data = {"grant_type": "client_credentials"}
            if scope:
                data["scope"] = scope
            token, ttl = await self._request(data)
            self._cache[scope] = (token, self._clock() + max(ttl - TOKEN_MARGIN_S, 1.0))
            return token

    async def exchange_code(self, code: str, redirect_uri: str) -> str:
        token, _ = await self._request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        )
        return token

    async def _request(self, data: dict[str, str]) -> tuple[str, float]:
        r = await self._http.post(
            self._token_url,
            data=data,
            auth=(self._client_id, self._secret.get_secret_value()),
            headers={"Accept": "application/json"},
        )
        if self._on_status:
            self._on_status(r.status_code)
        body = _json_or_none(r)
        if r.status_code != 200:
            err = body.get("error") if isinstance(body, dict) else None
            raise CarrierError(
                ReasonCode.CARRIER_ERROR,
                r.status_code >= 500,
                status=r.status_code,
                code=err if isinstance(err, str) and len(err) <= 64 else None,
                kind="oauth",
            )
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise malformed_response()
        ttl = body.get("expires_in", 300)
        return body["access_token"], float(ttl) if isinstance(ttl, int | float) else 300.0


def _json_or_none(r: httpx.Response) -> Any:
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return None


class DirectClient(CarrierBase):
    """`CarrierClient` over CAMARA REST. Pass `transport` to run against an in-process mock."""

    def __init__(
        self,
        config: CarrierConfig,
        *,
        secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        breakers: BreakerRegistry | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.profile = profile_for(config.profile)
        self.base_url = config.base_url.rstrip("/")
        self.breaker = (breakers or DEFAULT_BREAKERS).for_url(self.base_url)
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self.profile.timeout_s + 0.5),
            follow_redirects=False,
        )
        self.oauth = OAuthSession(
            self._http,
            token_url=config.oauth.token_url,
            client_id=config.oauth.client_id,
            secret=secret,
            clock=clock,
            on_status=self._record,
        )

    def __repr__(self) -> str:
        return f"DirectClient(base_url={self.base_url!r}, backend={self.config.backend!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """Where the binding page sends the phone to start Number Verification's auth-code flow."""
        authorize = self.config.oauth.authorize_url or f"{self.base_url}/oauth2/authorize"
        scope = " ".join(
            dict.fromkeys(
                s
                for op_id in ("phoneNumberVerify", "phoneNumberShare")
                for s in specs.operation("number-verification", op_id).scope_for().split()
            )
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.oauth.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": scope,
            }
        )
        return f"{authorize}?{query}"

    async def _send(
        self,
        op: specs.Operation,
        *,
        json: Any = None,
        path_params: dict[str, str] | None = None,
        auth_code: tuple[str, str] | None = None,
    ) -> Answer:
        scope = op.scope_for(self.config.oauth.scopes)
        if auth_code is not None:
            token = await self.oauth.exchange_code(*auth_code)
        else:
            token = await self.oauth.token(scope)
        headers = {"Authorization": f"Bearer {token}", "x-correlator": str(uuid.uuid4())}
        url = self.base_url + op.render_path(path_params)
        r = await self._http.request(op.method, url, json=json, headers=headers)
        self._record(r.status_code)
        body = _json_or_none(r)
        if r.status_code >= 400:
            if r.status_code == 401 and auth_code is None:
                self.oauth.drop(scope)
            raise error_from_answer(r.status_code, body)
        if r.content and body is None:
            raise malformed_response()
        return Answer(r.status_code, body)
