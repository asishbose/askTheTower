"""Configuration from environment variables. Every knob is documented in the service README."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PACKAGE_DIR.parent.parent  # services/mock-carrier


def _find_up(start: Path, rel: str) -> Path | None:
    for p in [start, *start.parents]:
        cand = p / rel
        if cand.exists():
            return cand
    return None


def default_specs_dir() -> Path:
    found = _find_up(SERVICE_DIR, "specs/camara")
    return found if found else SERVICE_DIR / "specs" / "camara"


def default_scenarios_dir() -> Path:
    local = SERVICE_DIR / "scenarios"  # symlink to ../../scenarios in the checkout; a copy in the image
    if local.exists():
        return local
    found = _find_up(SERVICE_DIR, "scenarios")
    return found if found else local


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All runtime configuration. Construct directly in tests; `Settings.from_env()` in the container."""

    admin: bool = False
    admin_token: str | None = field(default=None, repr=False)  # G1: bearer for the mutating /_admin routes
    ciba: bool = False
    jitter_ms: int = 0
    port: int = 8443
    tls_cert: str | None = None
    tls_key: str | None = None
    scenario: str = "demo"
    scenarios_dir: Path = field(default_factory=default_scenarios_dir)
    specs_dir: Path = field(default_factory=default_specs_dir)
    clients_file: Path = field(default_factory=lambda: SERVICE_DIR / "config" / "clients.yaml")
    jwt_secret: str = "mock-carrier-local-dev-only"
    token_ttl_s: int = 24 * 3600
    base_url: str = "http://localhost:8443"
    fault_timeout_s: float = 0.5
    webhook_backoff_s: float = 0.2
    webhook_timeout_s: float = 2.0
    loopback_sink_host: str = "sink.mock.local"
    # 08 §3 (D-G): on Fargate the mock cannot see the phone's network, so it can assume one client id.
    assume_mobile_data: bool = False
    assume_client_id: str = "phone-asish"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            admin=_flag("MOCK_ADMIN"),
            admin_token=env.get("MOCK_ADMIN_TOKEN") or None,
            ciba=_flag("MOCK_CIBA"),
            jitter_ms=int(env.get("MOCK_JITTER_MS", "0") or 0),
            port=int(env.get("MOCK_PORT", "8443")),
            tls_cert=env.get("MOCK_TLS_CERT") or None,
            tls_key=env.get("MOCK_TLS_KEY") or None,
            scenario=env.get("MOCK_SCENARIO", "demo"),
            scenarios_dir=Path(env["MOCK_SCENARIOS_DIR"])
            if env.get("MOCK_SCENARIOS_DIR")
            else default_scenarios_dir(),
            specs_dir=Path(env["MOCK_SPECS_DIR"]) if env.get("MOCK_SPECS_DIR") else default_specs_dir(),
            clients_file=Path(env.get("MOCK_CLIENTS_FILE") or (SERVICE_DIR / "config" / "clients.yaml")),
            jwt_secret=env.get("MOCK_JWT_SECRET", "mock-carrier-local-dev-only"),
            token_ttl_s=int(env.get("MOCK_TOKEN_TTL_S", str(24 * 3600))),
            base_url=env.get("MOCK_BASE_URL", f"http://localhost:{env.get('MOCK_PORT', '8443')}"),
            fault_timeout_s=float(env.get("MOCK_FAULT_TIMEOUT_S", "0.5")),
            webhook_backoff_s=float(env.get("MOCK_WEBHOOK_BACKOFF_S", "0.2")),
            webhook_timeout_s=float(env.get("MOCK_WEBHOOK_TIMEOUT_S", "2.0")),
            loopback_sink_host=env.get("MOCK_LOOPBACK_SINK_HOST", "sink.mock.local"),
            assume_mobile_data=_flag("MOCK_ASSUME_MOBILE_DATA"),
            assume_client_id=env.get("MOCK_ASSUME_CLIENT_ID") or "phone-asish",
        )
