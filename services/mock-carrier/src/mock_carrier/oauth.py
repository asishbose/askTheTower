"""OAuth 2 for the mock: the mock issues credentials, it never consumes any.

- `POST /oauth2/token` — `client_credentials` (two-legged), `authorization_code` (three-legged,
  issued by `/oauth2/authorize`), and the CIBA grant (behind `MOCK_CIBA=1`).
- `GET /oauth2/authorize` — auth-code flow with *simulated* network authentication: the carrier
  "sees" the device on its own network when the request carries `X-Mock-Client-Id` matching a line's
  `mobile_data_client_ids` (a simulation aid; a real carrier uses the cellular data session).
- `POST /oauth2/bc-authorize` — CIBA, auto-approved, only when `MOCK_CIBA=1`.
- `GET /oauth2/.well-known/openid-configuration` — discovery; the specs' `openIdConnectUrl` points here.

Access tokens are HS256 JWTs signed with `MOCK_JWT_SECRET`, timestamped by the mock clock. A
three-legged token carries `line` (an opaque line reference, never the number) and, when the network
attributed the device, `x_mock_client_id`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from mock_carrier.state import AuthCode, CibaRequest, line_ref

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime

CIBA_GRANT = "urn:openid:params:grant-type:ciba"
CODE_TTL = timedelta(minutes=5)
CIBA_TTL = timedelta(minutes=2)
MOCK_CLIENT_HEADER = "X-Mock-Client-Id"


class SimulationConflict(ValueError):
    """`MOCK_ASSUME_MOBILE_DATA=1` and an `X-Mock-Client-Id` header on the same request (08 §3)."""


# --- client registry -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Client:
    client_id: str
    secret: str
    grant_types: tuple[str, ...]
    scopes: tuple[str, ...]
    redirect_uri_prefixes: tuple[str, ...] = ()

    def allows(self, scope: str) -> bool:
        return any(scope_covers(g, scope) for g in self.scopes)


@dataclass
class ClientRegistry:
    clients: dict[str, Client] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> ClientRegistry:
        with Path(path).open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        clients = {}
        for cid, c in (raw.get("clients") or {}).items():
            clients[str(cid)] = Client(
                client_id=str(cid),
                secret=str(c["secret"]),
                grant_types=tuple(str(g) for g in c.get("grant_types", ["client_credentials"])),
                scopes=tuple(str(s) for s in c.get("scopes", [])),
                redirect_uri_prefixes=tuple(str(p) for p in c.get("redirect_uri_prefixes", [])),
            )
        return cls(clients)

    def authenticate(self, client_id: str | None, secret: str | None) -> Client | None:
        if not client_id or secret is None:
            return None
        client = self.clients.get(client_id)
        if client is None or not hmac.compare_digest(client.secret.encode(), secret.encode()):
            return None
        return client


def scope_covers(granted: str, required: str) -> bool:
    """`sim-swap` covers `sim-swap:check`; a scope always covers itself."""
    return granted == required or required.startswith(granted + ":")


# --- tokens ----------------------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_jwt(claims: dict[str, Any], secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    """Verify the signature; return the claims or None. Expiry is checked by the caller (mock clock)."""
    try:
        header, payload, sig = token.split(".")
        expected = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        if json.loads(_unb64(header)).get("alg") != "HS256":
            return None
        claims = json.loads(_unb64(payload))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


@dataclass(frozen=True)
class Token:
    client_id: str
    scopes: tuple[str, ...]
    three_legged: bool
    line: str | None  # opaque line reference; None on a two-legged token or an unattributed auth code
    attributed_client_id: str | None  # set when the network attributed the device

    def has(self, required: str) -> bool:
        return any(scope_covers(s, required) for s in self.scopes)


def issue_token(
    rt: Runtime,
    client: Client,
    scopes: list[str],
    *,
    three_legged: bool = False,
    line: str | None = None,
    attributed: str | None = None,
) -> dict[str, Any]:
    now = rt.now()
    ttl = rt.settings.token_ttl_s
    claims: dict[str, Any] = {
        "iss": rt.settings.base_url,
        "sub": line or client.client_id,
        "legs": 3 if three_legged else 2,
        "client_id": client.client_id,
        "scope": " ".join(scopes),
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + ttl,
        "jti": rt.state.next_id("tok"),
    }
    if line:
        claims["line"] = line
    if attributed:
        claims["x_mock_client_id"] = attributed
        claims["amr"] = ["mobile_network"]
    return {
        "access_token": encode_jwt(claims, rt.settings.jwt_secret),
        "token_type": "Bearer",
        "expires_in": ttl,
        "scope": claims["scope"],
    }


def verify_bearer(rt: Runtime, authorization: str | None) -> Token | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    claims = decode_jwt(authorization[7:].strip(), rt.settings.jwt_secret)
    if claims is None:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= int(rt.now().timestamp()):
        return None
    return Token(
        client_id=str(claims.get("client_id", "")),
        scopes=tuple(str(claims.get("scope", "")).split()),
        three_legged=claims.get("legs") == 3,
        line=claims.get("line"),
        attributed_client_id=claims.get("x_mock_client_id"),
    )


# --- HTTP ------------------------------------------------------------------------------------------
def _oauth_error(status: int, error: str, description: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers={"Cache-Control": "no-store"},
    )


async def _form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def _client_auth(request: Request, form: dict[str, str]) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            cid, _, secret = base64.b64decode(auth[6:].strip()).decode().partition(":")
            return cid, secret
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return None, None
    return form.get("client_id"), form.get("client_secret")


def _requested_scopes(client: Client, raw: str | None) -> list[str] | None:
    if not raw:
        return list(client.scopes)
    requested = [s for s in raw.split() if s != "openid"]
    if not all(client.allows(s) for s in requested):
        return None
    return requested or list(client.scopes)


def _msisdn_from_hint(hint: str) -> str:
    return hint[4:] if hint.startswith("tel:") else hint


def router(rt: Runtime) -> APIRouter:
    r = APIRouter(prefix="/oauth2", tags=["mock: oauth2"])

    @r.get("/.well-known/openid-configuration", summary="Discovery document")
    async def discovery() -> dict[str, Any]:
        base = rt.settings.base_url.rstrip("/")
        doc: dict[str, Any] = {
            "issuer": base,
            "token_endpoint": f"{base}/oauth2/token",
            "authorization_endpoint": f"{base}/oauth2/authorize",
            "grant_types_supported": ["client_credentials", "authorization_code"],
            "response_types_supported": ["code"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "scopes_supported": sorted({s for op in rt.specs.operations() for alt in op.scopes for s in alt}),
        }
        if rt.settings.ciba:
            doc["backchannel_authentication_endpoint"] = f"{base}/oauth2/bc-authorize"
            doc["grant_types_supported"].append(CIBA_GRANT)
            doc["backchannel_token_delivery_modes_supported"] = ["poll"]
        return doc

    @r.post("/token", summary="Token endpoint (client credentials, auth code, CIBA)")
    async def token(request: Request) -> Response:
        form = await _form(request)
        client = rt.clients.authenticate(*_client_auth(request, form))
        if client is None:
            return _oauth_error(401, "invalid_client", "unknown client or bad secret")
        grant = form.get("grant_type", "")
        if grant not in client.grant_types:
            return _oauth_error(400, "unauthorized_client", f"grant {grant!r} not allowed for this client")
        now = rt.now()
        if grant == "client_credentials":
            scopes = _requested_scopes(client, form.get("scope"))
            if scopes is None:
                return _oauth_error(400, "invalid_scope", "scope not allowed for this client")
            return JSONResponse(issue_token(rt, client, scopes), headers={"Cache-Control": "no-store"})
        if grant == "authorization_code":
            code = rt.state.auth_codes.get(form.get("code", ""))
            if (
                code is None
                or code.used
                or code.client_id != client.client_id
                or code.expires_at <= now
                or code.redirect_uri != form.get("redirect_uri", code.redirect_uri)
            ):
                return _oauth_error(400, "invalid_grant", "code unknown, used, expired or not yours")
            code.used = True
            line = line_ref(code.msisdn) if code.msisdn else None
            body = issue_token(
                rt,
                client,
                code.scope.split(),
                three_legged=True,
                line=line,
                attributed=code.attributed_client_id,
            )
            return JSONResponse(body, headers={"Cache-Control": "no-store"})
        if grant == CIBA_GRANT and rt.settings.ciba:
            req = rt.state.ciba_requests.pop(form.get("auth_req_id", ""), None)
            if req is None or req.client_id != client.client_id or req.expires_at <= now:
                return _oauth_error(400, "invalid_grant", "auth_req_id unknown or expired")
            line = line_ref(req.msisdn) if req.msisdn else None
            body = issue_token(rt, client, req.scope.split(), three_legged=True, line=line)
            return JSONResponse(body, headers={"Cache-Control": "no-store"})
        return _oauth_error(400, "unsupported_grant_type", f"grant {grant!r} is not supported")

    @r.get("/authorize", summary="Auth-code flow; network authentication simulated by X-Mock-Client-Id")
    async def authorize(request: Request) -> Response:
        q = request.query_params
        client = rt.clients.clients.get(q.get("client_id", ""))
        redirect_uri = q.get("redirect_uri", "")
        if client is None or "authorization_code" not in client.grant_types:
            return _oauth_error(400, "invalid_client", "unknown client or auth code not allowed")
        if not redirect_uri or not any(redirect_uri.startswith(p) for p in client.redirect_uri_prefixes):
            return _oauth_error(400, "invalid_request", "redirect_uri not registered for this client")
        if q.get("response_type", "code") != "code":
            return _oauth_error(400, "unsupported_response_type", "only response_type=code")
        scopes = _requested_scopes(client, q.get("scope"))
        if scopes is None:
            return _oauth_error(400, "invalid_scope", "scope not allowed for this client")
        try:
            attributed = rt.attributed_client_id(request.headers.get(MOCK_CLIENT_HEADER))
        except SimulationConflict as exc:
            return _oauth_error(400, "invalid_request", str(exc))
        line = rt.state.line_for_client_id(attributed) if attributed else None
        code = AuthCode(
            code=rt.state.next_id("code"),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            scope=" ".join(scopes),
            msisdn=line.msisdn if line else None,
            attributed_client_id=attributed if line else None,
            expires_at=rt.now() + CODE_TTL,
        )
        rt.state.auth_codes[code.code] = code
        params = {"code": code.code}
        if "state" in q:
            params["state"] = q["state"]
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    if rt.settings.ciba:

        @r.post("/bc-authorize", summary="CIBA backchannel authentication (MOCK_CIBA=1); auto-approved")
        async def bc_authorize(request: Request) -> Response:
            form = await _form(request)
            client = rt.clients.authenticate(*_client_auth(request, form))
            if client is None:
                return _oauth_error(401, "invalid_client", "unknown client or bad secret")
            if CIBA_GRANT not in client.grant_types:
                return _oauth_error(400, "unauthorized_client", "CIBA not allowed for this client")
            scopes = _requested_scopes(client, form.get("scope"))
            if scopes is None:
                return _oauth_error(400, "invalid_scope", "scope not allowed for this client")
            msisdn = _msisdn_from_hint(form.get("login_hint", ""))
            if msisdn not in rt.state.lines:
                return _oauth_error(400, "unknown_user_id", "login_hint does not identify a line")
            req = CibaRequest(
                auth_req_id=rt.state.next_id("ciba"),
                client_id=client.client_id,
                scope=" ".join(scopes),
                msisdn=msisdn,
                attributed_client_id=None,
                expires_at=rt.now() + CIBA_TTL,
            )
            rt.state.ciba_requests[req.auth_req_id] = req
            return JSONResponse(
                {"auth_req_id": req.auth_req_id, "expires_in": int(CIBA_TTL.total_seconds()), "interval": 0}
            )

    return r
