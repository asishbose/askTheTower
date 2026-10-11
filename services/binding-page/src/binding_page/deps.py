"""Everything a request handler needs, built once per app: settings, store, crypto, carrier, clock, signer, the
Alerts client and process-local counters."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from camara_client import CarrierClient, CarrierConfig
from fastapi import Request
from tower_audit import MarkerSigner
from tower_consent import LineIdHasher, MsisdnCipher, Store

from binding_page.alerts import AlertsWatch, RecordingAlerts
from binding_page.config import Settings
from binding_page.session import SESSION_COOKIE, Signer


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    settings: Settings
    store: Store
    hasher: LineIdHasher
    cipher: MsisdnCipher
    carrier_config: CarrierConfig
    carrier: CarrierClient
    audit_signer: MarkerSigner
    # Used only by mobile_data.start in TOWER_ENV=local (the page performs the authorize request itself).
    carrier_http: httpx.AsyncClient
    now: Callable[[], datetime] = utcnow
    alerts: AlertsWatch = field(default_factory=RecordingAlerts)
    metrics: Counter[str] = field(default_factory=Counter)  # no per-line labels (10 §2)
    signer: Signer = field(init=False)

    def __post_init__(self) -> None:
        self.signer = Signer(self.settings.session_secret)

    def epoch(self) -> int:
        return int(self.now().timestamp())


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


@dataclass(frozen=True)
class PageSession:
    user_id: str
    raw: str  # the cookie value (CSRF tokens are derived from it)


def current_session(request: Request, deps: Deps) -> PageSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    body = deps.signer.unsign("session", raw, now=deps.epoch())
    if body is None or not isinstance(body.get("u"), str) or raw is None:
        return None
    return PageSession(user_id=body["u"], raw=raw)
