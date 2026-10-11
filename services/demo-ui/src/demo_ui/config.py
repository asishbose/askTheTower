"""Settings from the environment (doc 11 §6). Nothing here prints a value; `Settings.__repr__` hides secrets.

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `local` | `local`, `eks` or `aws`; anything but `local` turns off pane 2, Reset and the macros (decision 2) |
| `TOWER_URL`, `TOWER_BEARER` | `http://localhost:8080/mcp`, — | Tower's MCP endpoint and bearer |
| `MOCK_URL`, `MOCK_ADMIN_TOKEN` | `http://localhost:8443`, — | the mock's `/_admin` and its token (G1); empty URL → pane 2 off |
| `BINDING_URL` | `http://localhost:8081` | the binding page (its `/_admin` answers 404 off-local) |
| `ALERTS_URL`, `ALERTS_INTERNAL_BEARER` | `http://localhost:8082`, — | `GET /internal/sent` (G3) |
| `DYNAMO_ENDPOINT` (or `TOWER_DYNAMODB_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | `http://localhost:8000`, ``, `us-east-1` | the audit reads |
| `REF_AGENT`, `BEDROCK_MODEL_ID` | `auto` | `auto`, `bedrock` or `scripted` |
| `DEMO_UI_HOST`, `DEMO_UI_PORT` | `127.0.0.1`, `8090` | where the UI listens |
| `DEMO_UI_PUBLISHED_LOOPBACK` | `0` | `1`: the container listens on 0.0.0.0 but is published on 127.0.0.1 only (compose, kind) |
| `DEMO_UI_TOKEN` | — | required for any non-loopback listen without the flag above (decision 7) |
| `FEED_POLL_S` | `2` | feed poll interval |
| `DEMO_UI_SEED_PATH` | found from the checkout | the `make seed` module Reset runs (decision 5) |
| `DEMO_ALERTS_SETTLE_S` | `2.5` on `local`, else `0` | macro pause after a clock jump Alerts acts on (06 §11.4, `ref_client.demo`) |
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ref_client.demo import alerts_settle_s

Env = Literal["local", "eks", "aws"]
AgentChoice = Literal["auto", "bedrock", "scripted"]
SEED_REL = Path("deploy") / "compose" / "seed" / "seed.py"


class ConfigError(ValueError):
    """A setting that makes the UI unsafe or unusable. The message names the variable, never its value."""


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def default_seed_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / SEED_REL).exists():
            return parent / SEED_REL
    return None


@dataclass(frozen=True)
class Settings:
    env: Env = "local"
    tower_url: str = "http://localhost:8080/mcp"
    tower_bearer: str | None = field(default=None, repr=False)
    mock_url: str = "http://localhost:8443"
    mock_admin_token: str | None = field(default=None, repr=False)
    binding_url: str = "http://localhost:8081"
    alerts_url: str = "http://localhost:8082"
    alerts_bearer: str | None = field(default=None, repr=False)
    dynamo_endpoint: str | None = "http://localhost:8000"
    table_prefix: str = ""
    aws_region: str = "us-east-1"
    ref_agent: AgentChoice = "auto"
    host: str = "127.0.0.1"
    port: int = 8090
    published_loopback: bool = False
    ui_token: str | None = field(default=None, repr=False)
    feed_poll_s: float = 2.0
    seed_path: Path | None = None
    alerts_settle_s: float = 0.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        raw_env = e.get("ENV", "local") or "local"
        if raw_env not in ("local", "eks", "aws"):
            raise ConfigError("ENV must be local, eks or aws")
        agent = e.get("REF_AGENT", "auto") or "auto"
        if agent not in ("auto", "bedrock", "scripted"):
            raise ConfigError("REF_AGENT must be auto, bedrock or scripted")
        seed = e.get("DEMO_UI_SEED_PATH")
        try:
            settle = alerts_settle_s(raw_env, e)
        except ValueError as err:
            raise ConfigError("DEMO_ALERTS_SETTLE_S must be a number >= 0") from err
        settings = cls(
            env=raw_env,  # type: ignore[arg-type]
            tower_url=e.get("TOWER_URL") or cls.tower_url,
            tower_bearer=e.get("TOWER_BEARER") or None,
            mock_url=e.get("MOCK_URL", cls.mock_url).rstrip("/"),
            mock_admin_token=e.get("MOCK_ADMIN_TOKEN") or None,
            binding_url=e.get("BINDING_URL", cls.binding_url).rstrip("/"),
            alerts_url=e.get("ALERTS_URL", cls.alerts_url).rstrip("/"),
            alerts_bearer=e.get("ALERTS_INTERNAL_BEARER") or None,
            dynamo_endpoint=(
                e.get("TOWER_DYNAMODB_ENDPOINT")
                or e.get("DYNAMO_ENDPOINT", cls.dynamo_endpoint or "")
                or None
            ),
            table_prefix=e.get("TOWER_TABLE_PREFIX", ""),
            aws_region=e.get("AWS_REGION") or cls.aws_region,
            ref_agent=agent,  # type: ignore[arg-type]
            host=e.get("DEMO_UI_HOST") or cls.host,
            port=int(e.get("DEMO_UI_PORT") or cls.port),
            published_loopback=(e.get("DEMO_UI_PUBLISHED_LOOPBACK", "0").strip() == "1"),
            ui_token=e.get("DEMO_UI_TOKEN") or None,
            feed_poll_s=float(e.get("FEED_POLL_S") or (2 if raw_env == "local" else 5)),
            seed_path=Path(seed) if seed else default_seed_path(),
            alerts_settle_s=settle,
        )
        settings.check()
        return settings

    def check(self) -> None:
        """Decision 7: listening beyond loopback needs a token, unless the publish itself is loopback-only."""
        exposed = not is_loopback(self.host) and not self.published_loopback
        if exposed and not self.ui_token:
            raise ConfigError(
                "DEMO_UI_HOST is not a loopback address: set DEMO_UI_TOKEN (the UI can revoke grants), "
                "or listen on 127.0.0.1"
            )
        if self.feed_poll_s <= 0:
            raise ConfigError("FEED_POLL_S must be > 0")

    @property
    def local(self) -> bool:
        return self.env == "local"

    @property
    def carrier_enabled(self) -> bool:
        """Pane 2, Reset and the macros: ENV=local with a mock URL (decision 2)."""
        return self.local and bool(self.mock_url)

    @property
    def carrier_off_reason(self) -> str:
        if not self.local:
            return (
                f"ENV={self.env}: carrier controls, Reset and the macros are local-only (doc 11 decision 2)"
            )
        return "MOCK_URL is empty: the mock is not reachable from this laptop"

    @property
    def sms_enabled(self) -> bool:
        return self.local and bool(self.alerts_url)

    @property
    def binding_admin_enabled(self) -> bool:
        return self.local and bool(self.binding_url)

    @property
    def personas(self) -> tuple[str, ...]:
        """Who can speak in pane 1: Asish and Mom locally; Asish only on AWS (decision 3)."""
        return ("asish", "mom") if self.local else ("asish",)
