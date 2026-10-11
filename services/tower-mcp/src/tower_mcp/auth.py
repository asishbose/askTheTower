"""Inbound identity → `user_id` (02 §5, 01 §4; RUN-ALL Decisions "Alexa+ inbound identity").

Final shape for the Alexa+ surface (prompt 15), still to be confirmed against a live Alexa+ registration
(`docs/architecture/alexa/registration.md` step 6; `docs/architecture/components/01-alexa-surface.md` §4):

- **Bearer JWT.** Alexa+ account linking is OAuth 2.0: the user links their Amazon account to an identity
  provider we run, and Alexa+ presents that provider's access token on every tool call as
  `Authorization: Bearer <JWT>` (on AWS, AgentCore Runtime's custom JWT authorizer checks it first and passes
  the header through). Tower verifies it again against the JWKS at `TOWER_JWKS_URL`; `exp` and `sub` are
  required; `user_id` = the `sub` claim.
- **Audience / client.** Mirrors the Runtime authorizer's two lists. `TOWER_JWT_AUDIENCE` (comma-separated) is
  checked against `aud`; `TOWER_JWT_CLIENT_IDS` (comma-separated) against `client_id` — OAuth access tokens
  from some providers (Amazon Cognito among them) carry `client_id` and no `aud`. Each list is enforced when
  set; `TOWER_JWT_ISSUER` likewise.
- **Local mode only** (`TOWER_ENV=local`): the static bearer `TOWER_BEARER` plus an `X-Tower-User` header
  naming the user. Outside local mode that header is ignored and the static bearer is refused.

The audit log refuses actor ids that are not opaque (`tower_audit.AuditRecord.actor_user_id`: `[A-Za-z0-9._:@+-]`,
≤128 chars, no run of 10+ digits — fail closed). `safe_user_id` maps any other subject to a stable opaque id
(`u_` + 40 letters of its SHA-256), so a subject that happens to look like a phone number is never stored or
logged as one. The binding page learns `user_id` from the bind token Tower issues, so the mapping lives here only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import anyio
import jwt

logger = logging.getLogger("tower_mcp.auth")

_SAFE_ID = re.compile(r"[A-Za-z0-9._:@+-]{1,128}")
_DIGIT_RUN = re.compile(r"\d{10,}")
_HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")
USER_HEADER = "x-tower-user"
JWT_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class AuthError(Exception):
    """No usable identity on the request. The message is generic on purpose (no oracle)."""


@dataclass(frozen=True)
class Identity:
    user_id: str
    method: Literal["jwt", "local"]


def safe_user_id(raw: str) -> str:
    """An opaque, audit-safe user id. Safe subjects pass through unchanged (readable in the demo)."""
    raw = raw.strip()
    if not raw:
        raise AuthError("empty subject")
    if _SAFE_ID.fullmatch(raw) and not _DIGIT_RUN.search(raw):
        return raw
    return "u_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40].translate(_HEX_TO_LETTERS)


def split_list(value: str | Sequence[str] | None) -> list[str]:
    """`"a, b"` or `["a", "b"]` → `["a", "b"]` (env vars and Terraform's `join(",", …)` give the string form)."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    return [item.strip() for item in items if item and item.strip()]


def _bearer(headers: Mapping[str, str]) -> str:
    value = headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing bearer")
    return token.strip()


class Authenticator:
    """Turns request headers into an `Identity`, or raises `AuthError` (→ 401)."""

    def __init__(
        self,
        *,
        env: str = "local",
        local_bearer: str | None = None,
        jwks_url: str | None = None,
        issuer: str | None = None,
        audience: str | Sequence[str] | None = None,
        client_ids: str | Sequence[str] | None = None,
        jwk_client: Any = None,
    ) -> None:
        self.local = env == "local"
        self._local_bearer = local_bearer if self.local else None
        self._issuer = issuer
        self._audience = split_list(audience)
        self._client_ids = frozenset(split_list(client_ids))
        self._jwks = jwk_client
        if self._jwks is None and jwks_url:
            self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)
        if not self.local and self._jwks is None:
            logger.warning(
                "TOWER_ENV is not local and TOWER_JWKS_URL is unset: every request will be refused"
            )

    def __repr__(self) -> str:
        return f"Authenticator(local={self.local}, jwt={self._jwks is not None})"

    async def authenticate(self, headers: Mapping[str, str]) -> Identity:
        token = _bearer(headers)
        if self._local_bearer and hmac.compare_digest(token.encode(), self._local_bearer.encode()):
            user = headers.get(USER_HEADER, "")
            if not user.strip():
                raise AuthError("local bearer needs X-Tower-User")
            return Identity(user_id=safe_user_id(user), method="local")
        if self._jwks is None:
            raise AuthError("bearer not accepted")
        claims = await self._verify_jwt(token)
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise AuthError("token has no subject")
        return Identity(user_id=safe_user_id(sub), method="jwt")

    async def _verify_jwt(self, token: str) -> dict[str, Any]:
        try:
            # A JWKS cache miss is a blocking HTTP fetch inside PyJWT: keep it off the event loop.
            key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, token)
            options: dict[str, Any] = {"require": ["exp", "sub"], "verify_aud": bool(self._audience)}
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=JWT_ALGORITHMS,
                audience=self._audience or None,
                issuer=self._issuer,
                options=options,
            )
        except (jwt.PyJWTError, ValueError) as e:
            logger.info("bearer refused: %s", type(e).__name__)
            raise AuthError("bearer refused") from None
        if self._client_ids and claims.get("client_id") not in self._client_ids:
            logger.info("bearer refused: client_id not allowed")
            raise AuthError("bearer refused")
        return claims


ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]],
    Awaitable[None],
]

STATE_KEY = "tower_identity"


class AuthMiddleware:
    """Pure ASGI middleware: every request under `protected` must authenticate; the `Identity` is put on
    `scope["state"]` (Starlette's `request.state`) for the tool handlers. Anything else (`/healthz`) passes."""

    def __init__(self, app: ASGIApp, authenticator: Authenticator, protected: str = "/mcp") -> None:
        self.app = app
        self.authenticator = authenticator
        self.protected = protected

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(self.protected):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            identity = await self.authenticator.authenticate(headers)
        except AuthError:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer realm="tower"'),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})[STATE_KEY] = identity
        await self.app(scope, receive, send)
