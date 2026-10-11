"""`CarrierConfig` — the one config block of 05 §4, plus which client to build.

`client` (direct | gateway) is orthogonal to `backend` (mock | sandbox): the backend is only a base
URL and credentials, and nothing in the code branches on it. `oauth.secret_ref` names the client
secret; it is resolved by the caller (AgentCore Identity on AWS, an environment variable locally) —
`resolve_secret_ref` handles the local `env:NAME` form only.

Environment variables (`CarrierConfig.from_env`):

| Variable | Default |
|---|---|
| `CARRIER_CLIENT` | `direct` (`gateway` for the AgentCore Gateway path) |
| `CARRIER_BACKEND` | `mock` (`sandbox`) — recorded, never branched on |
| `CARRIER_BASE_URL` | `http://localhost:8443` |
| `CARRIER_TOKEN_URL` | `<base>/oauth2/token` |
| `CARRIER_AUTHORIZE_URL` | `<base>/oauth2/authorize` |
| `CARRIER_CLIENT_ID` | `tower` |
| `CARRIER_SECRET_REF` | `env:CARRIER_CLIENT_SECRET` |
| `CARRIER_SCOPES` | the four API scopes of 05 §4 (space- or comma-separated) |
| `CARRIER_PROFILE` | `request` (`proactive` for Alerts) |
| `CARRIER_GATEWAY_URL` | — (required when `CARRIER_CLIENT=gateway`) |
| `CARRIER_GATEWAY_TOOLS` | — path to `gateway-tools.json` written at deploy time, or `discover` (resolve the names from Gateway's `tools/list` on first use) |
| `CARRIER_GATEWAY_AUTH` | `none` (`sigv4` on AWS: IAM-authorised Gateway, `camara_client.aws.SigV4Auth`) |
| `CARRIER_GATEWAY_REGION` | `AWS_REGION` / `AWS_DEFAULT_REGION` |
| `CARRIER_GATEWAY_SESSION` | `per-call` (`persistent`: one MCP session per process, opened at warm-up — Tower) |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SCOPES = (
    "sim-swap",
    "call-forwarding-signal",
    "device-reachability-status",
    "number-verification",
    "sim-swap-subscriptions",
    "device-reachability-status-subscriptions",
)


class OAuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token_url: str
    authorize_url: str | None = None
    client_id: str
    secret_ref: str = "env:CARRIER_CLIENT_SECRET"
    scopes: tuple[str, ...] = DEFAULT_SCOPES


class CarrierConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client: Literal["direct", "gateway"] = "direct"
    backend: Literal["mock", "sandbox"] = "mock"
    base_url: str
    oauth: OAuthConfig
    profile: Literal["request", "proactive"] = "request"
    gateway_url: str | None = None
    gateway_tools_file: str | None = Field(
        default=None, description="gateway-tools.json from deploy time, or 'discover'"
    )
    gateway_auth: Literal["none", "sigv4"] = "none"
    gateway_region: str | None = None
    gateway_session: Literal["per-call", "persistent"] = "per-call"

    @model_validator(mode="after")
    def _gateway_needs_url(self) -> CarrierConfig:
        if self.client == "gateway" and not self.gateway_url:
            raise ValueError("client=gateway needs gateway_url (CARRIER_GATEWAY_URL)")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CarrierConfig:
        e = os.environ if env is None else env
        base = e.get("CARRIER_BASE_URL", "http://localhost:8443").rstrip("/")
        raw_scopes = e.get("CARRIER_SCOPES", "")
        scopes = tuple(s for s in raw_scopes.replace(",", " ").split() if s) or DEFAULT_SCOPES
        return cls(
            client=e.get("CARRIER_CLIENT", "direct"),  # type: ignore[arg-type]
            backend=e.get("CARRIER_BACKEND", "mock"),  # type: ignore[arg-type]
            base_url=base,
            oauth=OAuthConfig(
                token_url=e.get("CARRIER_TOKEN_URL") or f"{base}/oauth2/token",
                authorize_url=e.get("CARRIER_AUTHORIZE_URL") or f"{base}/oauth2/authorize",
                client_id=e.get("CARRIER_CLIENT_ID", "tower"),
                secret_ref=e.get("CARRIER_SECRET_REF", "env:CARRIER_CLIENT_SECRET"),
                scopes=scopes,
            ),
            profile=e.get("CARRIER_PROFILE", "request"),  # type: ignore[arg-type]
            gateway_url=e.get("CARRIER_GATEWAY_URL") or None,
            gateway_tools_file=e.get("CARRIER_GATEWAY_TOOLS") or None,
            gateway_auth=(e.get("CARRIER_GATEWAY_AUTH") or "none").strip().lower(),  # type: ignore[arg-type]
            gateway_region=e.get("CARRIER_GATEWAY_REGION")
            or e.get("AWS_REGION")
            or e.get("AWS_DEFAULT_REGION")
            or None,
            gateway_session=(e.get("CARRIER_GATEWAY_SESSION") or "per-call").strip().lower(),  # type: ignore[arg-type]
        )


def resolve_secret_ref(ref: str, env: Mapping[str, str] | None = None) -> str:
    """Resolve a local `env:NAME` reference. Any other scheme belongs to the caller (Identity / Secrets
    Manager on AWS) and is refused here rather than guessed."""
    e = os.environ if env is None else env
    scheme, _, name = ref.partition(":")
    if scheme != "env" or not name:
        raise ValueError(f"secret_ref scheme {scheme!r} must be resolved by the caller")
    value = e.get(name)
    if not value:
        raise ValueError(f"secret_ref names {name}, which is not set")
    return value
