"""ENV=local|eks|aws → the endpoints the e2e layer talks to, and `MockAdmin`, the mock carrier's admin API.

Resolution order for every endpoint: the process environment (what `make test-e2e ENV=…` exports from
mk/vars.mk) → `deploy/.env.<env>` (eks/aws: rendered from Terraform/Helm outputs by `scripts/render_env.py`) →
the local compose defaults (mk/vars.mk). The same tests run against all three; only these values differ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
VALID_ENVS = ("local", "eks", "aws")
LOCAL_DEFAULTS = {
    "TOWER_URL": "http://localhost:8080/mcp",
    "MOCK_URL": "http://localhost:8443",
    "BINDING_URL": "http://localhost:8081",
    "ALERTS_URL": "http://localhost:8082",
}
COMPOSE_ENV = ROOT / "deploy" / "compose" / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class Targets:
    env: str
    tower_url: str
    mock_url: str  # "" on aws: the mock sits behind an internal ALB (reached by Gateway / ECS Exec only)
    binding_url: str
    alerts_url: str
    bearer: str | None
    mock_admin_token: str | None = None  # G1: compose generates it; kind/AWS leave the mock's admin open

    @property
    def tower_health(self) -> str:
        return self.tower_url.rsplit("/mcp", 1)[0] + "/healthz"

    def health_urls(self) -> list[str]:
        urls = [self.tower_health, f"{self.binding_url}/healthz"]
        if self.mock_url:
            urls.append(f"{self.mock_url}/healthz")
        return urls


@dataclass(frozen=True)
class Stack:
    """What a test runs against. e2e: the running environment's URLs. integration: the DynamoDB endpoint
    (None = moto in-process) — the carrier, Tower and the binding page run in-process in their own fixtures."""

    layer: str
    env: str
    tower_url: str = ""
    mock_url: str = ""
    binding_url: str = ""
    alerts_url: str = ""
    dynamodb_endpoint: str | None = None


def resolve(env: str | None = None) -> Targets:
    env = env or os.environ.get("ENV") or "local"
    if env not in VALID_ENVS:
        raise ValueError(f"ENV must be one of {VALID_ENVS}, got {env!r}")
    file_values = _read_env_file(COMPOSE_ENV if env == "local" else ROOT / "deploy" / f".env.{env}")
    defaults = LOCAL_DEFAULTS if env == "local" else {}

    def get(key: str) -> str:
        return os.environ.get(key) or file_values.get(key) or defaults.get(key, "")

    return Targets(
        env=env,
        tower_url=get("TOWER_URL"),
        mock_url=get("MOCK_URL").rstrip("/"),
        binding_url=get("BINDING_URL").rstrip("/"),
        alerts_url=get("ALERTS_URL").rstrip("/"),
        bearer=get("TOWER_BEARER") or None,
        mock_admin_token=get("MOCK_ADMIN_TOKEN") or None,
    )


def answers(url: str, timeout: float = 3.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


class MockAdmin:
    """The mock carrier's `/_admin/*` (08 §3) as four verbs: load a scenario, fire an event, advance the clock,
    inject a fault — plus `clear_faults` and `state`. Synchronous; raises on any non-2xx answer."""

    def __init__(
        self, base_url: str, *, client: httpx.Client | None = None, token: str | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = client or httpx.Client(base_url=self.base_url, timeout=15.0, headers=headers)

    def _ok(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 300:
            raise AssertionError(
                f"mock admin {r.request.method} {r.request.url.path} → {r.status_code}: {r.text}"
            )
        return dict(r.json())

    def load_scenario(self, name: str = "demo", variant: str | None = None) -> dict[str, Any]:
        return self._ok(self._http.post("/_admin/scenarios/load", json={"name": name, "variant": variant}))

    def fire_event(self, msisdn: str, event: str) -> dict[str, Any]:
        return self._ok(self._http.post(f"/_admin/lines/{msisdn}/events", json={"event": event}))

    def advance_clock(self, *, minutes: float = 0, seconds: float = 0) -> dict[str, Any]:
        return self._ok(self._http.post("/_admin/clock", json={"advance_s": minutes * 60 + seconds}))

    def inject_fault(self, kind: str, n: int = 1) -> dict[str, Any]:
        return self._ok(self._http.post("/_admin/faults", json={"kind": kind, "n": n}))

    def clear_faults(self) -> dict[str, Any]:
        return self._ok(self._http.delete("/_admin/faults"))

    def state(self) -> dict[str, Any]:
        return self._ok(self._http.get("/_admin/state"))

    def close(self) -> None:
        self._http.close()
