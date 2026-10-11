"""Configuration from environment variables (documented in the service README and `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `TOWER_ENV` | `local` | `local` enables the mobile-data simulation (`mobile_data.py`) and, with `BIND_ADMIN=1`, the admin view |
| `BASE_URL` | `http://localhost:8081` | public base URL of this page (links, QR code) |
| `BIND_REDIRECT_URI` | `<BASE_URL>/bind/callback` | the OAuth redirect URI registered with the carrier |
| `SESSION_SECRET` | — (required) | HMAC key for the OAuth `state`, the flow cookie, the session cookie and CSRF tokens |
| `SESSION_TTL_S` | `1800` | page session lifetime |
| `BIND_ADMIN` | off | `1` serves `/_admin/*` (only when `TOWER_ENV=local`) |
| `ALERTS_INTERNAL_URL` / `ALERTS_INTERNAL_BEARER` | — | Alerts' `POST /internal/watch`, called after a watch-settings save on an enabled Watch (04 §9.2); unset → log-only stub |
| `CARRIER_*` | see `camara_client.config` | `CARRIER_CLIENT_ID` defaults to `binding-page`, `CARRIER_SCOPES` to `number-verification`, `CARRIER_PROFILE` to `proactive` |
| `TOWER_DYNAMODB_ENDPOINT` (alias `DYNAMO_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | see `tower_consent.Store.from_env` | the store |
| `TOWER_LINE_ID_KEY`, `TOWER_MSISDN_KEY` / `TOWER_KMS_*` | see `tower_consent.crypto_from_env` | HMAC + encryption |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# A person is waiting on the page, not Alexa's 300 ms budget: the proactive profile (5 s, 1 retry; NV is never retried).
CARRIER_DEFAULTS = {
    "CARRIER_CLIENT_ID": "binding-page",
    "CARRIER_SCOPES": "number-verification",
    "CARRIER_SECRET_REF": "env:CARRIER_CLIENT_SECRET",
    "CARRIER_PROFILE": "proactive",
}


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    session_secret: str
    tower_env: str = "local"
    base_url: str = "http://localhost:8081"
    redirect_uri: str = ""
    session_ttl_s: int = 1800
    admin: bool = False
    port: int = 8081
    alerts_internal_url: str | None = None
    alerts_internal_bearer: str | None = None

    def __post_init__(self) -> None:
        if len(self.session_secret) < 16:
            raise ValueError("SESSION_SECRET must be set (at least 16 characters)")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if not self.redirect_uri:
            object.__setattr__(self, "redirect_uri", f"{self.base_url}/bind/callback")

    @property
    def local(self) -> bool:
        return self.tower_env == "local"

    @property
    def admin_enabled(self) -> bool:
        """The admin view exists only locally *and* when asked for."""
        return self.local and self.admin

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        return cls(
            session_secret=e.get("SESSION_SECRET", ""),
            tower_env=e.get("TOWER_ENV", "local"),
            base_url=e.get("BASE_URL", "http://localhost:8081"),
            redirect_uri=e.get("BIND_REDIRECT_URI", ""),
            session_ttl_s=int(e.get("SESSION_TTL_S", "1800")),
            admin=_flag(e.get("BIND_ADMIN")),
            port=int(e.get("PORT", "8081")),
            alerts_internal_url=e.get("ALERTS_INTERNAL_URL") or None,
            alerts_internal_bearer=e.get("ALERTS_INTERNAL_BEARER") or None,
        )


def carrier_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The process environment with the binding page's carrier defaults filled in."""
    e = dict(os.environ if env is None else env)
    for k, v in CARRIER_DEFAULTS.items():
        e.setdefault(k, v)
    return e
