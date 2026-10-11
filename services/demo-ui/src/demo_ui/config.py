"""Settings from the environment (doc 11 §6). Nothing here prints a value; `Settings.__repr__` hides secrets.

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `local` | `local`, `eks` or `aws`; anything but `local` turns off pane 2, Reset and the macros (decision 2) |
| `TOWER_URL`, `TOWER_BEARER` | `http://localhost:8080/mcp`, — | Tower's MCP endpoint and bearer |
| `MOCK_URL`, `MOCK_ADMIN_TOKEN` | `http://localhost:8443`, — | the mock's `/_admin` and its token (G1, local); empty URL → pane 2 off |
| `BINDING_URL` | `http://localhost:8081` | the binding page (its `/_admin` answers 404 off-local) |
| `ALERTS_URL`, `ALERTS_INTERNAL_BEARER` | `http://localhost:8082`, — | `GET /internal/sent` (G3), local only: never sent on ENV=aws |
| `DYNAMO_ENDPOINT` (or `TOWER_DYNAMODB_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | `http://localhost:8000`, ``, `us-east-1` | the audit reads |
| `REF_AGENT`, `BEDROCK_MODEL_ID` | `auto` | `auto`, `bedrock` or `scripted` |
| `DEMO_UI_HOST`, `DEMO_UI_PORT` | `127.0.0.1`, `8090` | where the UI listens |
| `DEMO_UI_PUBLISHED_LOOPBACK` | `0` | `1`: the container listens on 0.0.0.0 but is published on 127.0.0.1 only (compose, kind) |
| `DEMO_UI_TOKEN` | — | required for any non-loopback listen without the flag above (decision 7) |
| `FEED_POLL_S` | `2` | feed poll interval |
| `DEMO_UI_SEED_PATH` | found from the checkout | the `make seed` module Reset runs (decision 5) |
| `DEMO_ALERTS_SETTLE_S` | `2.5` on `local`, else `0` | macro pause after a clock jump Alerts acts on (06 §11.4, `ref_client.demo`) |
| `DEMO_UI_CHECKOUT` | found from the package location | the repo root (holds `Makefile` and `mk/`): the Ops runner's `cwd`, `artifacts/` |
| `DEMO_UI_AWS_ENV_FILE` | `<checkout>/deploy/.env.aws` | ENV=aws: read at start; for `AWS_FILE_KEYS` the file wins over the environment |
| `BINDING_ADMIN_URL`, `BIND_ADMIN_TOKEN` | `BINDING_URL`, — | the binding page's `/_admin/*`; `BIND_ADMIN_TOKEN` only off ENV=aws |
| `DEMO_ADMIN_BEARER` | — | ENV=aws, from `deploy/.env.aws`: the one bearer of the demo admin path, sent on every call to the mock's, Alerts' and the binding page's admin routes (10 §2.1, decision 1 of 2026-10-11) |
| `DEMO_UI_COGNITO_USERS` | `<checkout>/artifacts/cognito-users.json` | ENV=aws: Asish's and Mom's Cognito subs (U3) |

The two file defaults apply only when `from_env()` reads the process environment; a mapping passed in (tests) names
its files explicitly, so a developer's own `deploy/.env.aws` never leaks into a test.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ref_client.demo import alerts_settle_s

Env = Literal["local", "eks", "aws"]
AgentChoice = Literal["auto", "bedrock", "scripted"]
SEED_REL = Path("deploy") / "compose" / "seed" / "seed.py"
AWS_ENV_REL = Path("deploy") / ".env.aws"
COGNITO_USERS_REL = Path("artifacts") / "cognito-users.json"
# doc 11 §6: the keys of deploy/.env.aws the UI reads; for these the file wins over the process environment.
AWS_FILE_KEYS = (
    "TOWER_URL", "BINDING_URL", "BINDING_ADMIN_URL", "MOCK_URL", "ALERTS_URL", "WEB_CHAT_URL", "DEMO_ADMIN_BEARER",
    "TOWER_TABLE_PREFIX", "AWS_REGION",
)  # fmt: skip
USER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
LOCAL_USERS = ("user-asish", "user-mom")


class ConfigError(ValueError):
    """A setting that makes the UI unsafe or unusable. The message names the variable, never its value."""


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def find_checkout() -> Path | None:
    """The repo root above the package: the directory holding `Makefile` and `mk/` (absent in the image)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "Makefile").is_file() and (parent / "mk").is_dir():
            return parent
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """`KEY=value` lines (what `scripts/render_env.py` writes); comments and blank lines skipped. Never logged."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def read_cognito_users(path: Path | None) -> tuple[str, str] | None:
    """(Asish's sub, Mom's sub) from `make cognito-users`' file, or None when absent, malformed or not subs."""
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ids = (str(data.get("asish", "")), str(data.get("mom", "")))
    if not all(USER_ID.fullmatch(i) and not i.startswith("user-") for i in ids) or ids[0] == ids[1]:
        return None
    return ids


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
    checkout: Path | None = None
    binding_admin_url: str = ""
    bind_admin_token: str | None = field(default=None, repr=False)
    web_chat_url: str = ""
    demo_admin_bearer: str | None = field(default=None, repr=False)
    aws_env_file: Path | None = None
    aws_env_loaded: bool = False
    aws_users: tuple[str, str] | None = field(default=None, repr=False)

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, discover_files: bool | None = None
    ) -> Settings:
        """`discover_files` (default: only for the process environment) fills in the two file defaults of §6."""
        discover = env is None if discover_files is None else discover_files
        e: Mapping[str, str] = os.environ if env is None else env
        raw_env = e.get("ENV", "local") or "local"
        if raw_env not in ("local", "eks", "aws"):
            raise ConfigError("ENV must be local, eks or aws")
        checkout_raw = e.get("DEMO_UI_CHECKOUT")
        checkout = Path(checkout_raw) if checkout_raw else find_checkout()
        aws_file, aws_loaded = None, False
        users_file: Path | None = None
        if raw_env == "aws":
            aws_file = _path(e.get("DEMO_UI_AWS_ENV_FILE"), checkout, AWS_ENV_REL, discover)
            if aws_file is not None and aws_file.is_file():
                try:
                    from_file = read_env_file(aws_file)
                except (OSError, UnicodeDecodeError) as err:
                    raise ConfigError("DEMO_UI_AWS_ENV_FILE cannot be read") from err
                e = {**e, **{k: v for k, v in from_file.items() if k in AWS_FILE_KEYS}}
                aws_loaded = True
            users_file = _path(e.get("DEMO_UI_COGNITO_USERS"), checkout, COGNITO_USERS_REL, discover)
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
            checkout=checkout,
            binding_admin_url=(e.get("BINDING_ADMIN_URL") or "").rstrip("/"),
            bind_admin_token=e.get("BIND_ADMIN_TOKEN") or None,
            web_chat_url=(e.get("WEB_CHAT_URL") or "").rstrip("/"),
            demo_admin_bearer=e.get("DEMO_ADMIN_BEARER") or None,
            aws_env_file=aws_file,
            aws_env_loaded=aws_loaded,
            aws_users=read_cognito_users(users_file) if raw_env == "aws" else None,
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
    def admin_on(self) -> bool:
        """ENV=aws with the demo admin path open (U2): the mock's admin URL and `DEMO_ADMIN_BEARER`, from `.env.aws`."""
        return self.env == "aws" and bool(self.mock_url) and bool(self.demo_admin_bearer)

    def _admin_bearer(self, local_token: str | None) -> str | None:
        """Decision 1 (2026-10-11): on ENV=aws every admin-path call carries `DEMO_ADMIN_BEARER` (the gate and the
        service behind it both check it); elsewhere each service's own local token."""
        return self.demo_admin_bearer if self.env == "aws" else local_token

    @property
    def mock_bearer(self) -> str | None:
        return self._admin_bearer(self.mock_admin_token)

    @property
    def alerts_admin_bearer(self) -> str | None:
        return self._admin_bearer(self.alerts_bearer)

    @property
    def binding_admin_bearer(self) -> str | None:
        return self._admin_bearer(self.bind_admin_token)

    @property
    def admin_label(self) -> str | None:
        """The pill's third part on ENV=aws (`admin on` / `admin off`); nothing elsewhere."""
        if self.env != "aws":
            return None
        return "admin on" if self.admin_on else "admin off"

    @property
    def carrier_enabled(self) -> bool:
        """Pane 2: ENV=local with a mock URL, or ENV=aws with the admin path open (decision 2 as amended)."""
        return (self.local and bool(self.mock_url)) or self.admin_on

    @property
    def carrier_off_reason(self) -> str:
        if self.env == "aws":
            return (
                "ENV=aws: admin path closed: see pane 5 step 6 (carrier controls, Reset and the macros are "
                "local-only unless demo_admin_cidrs is open; doc 11 decision 2)"
            )
        if not self.local:
            return (
                f"ENV={self.env}: carrier controls, Reset and the macros are local-only (doc 11 decision 2)"
            )
        return "MOCK_URL is empty: the mock is not reachable from this laptop"

    @property
    def macros_enabled(self) -> bool:
        """Reset and the macros: pane 2's condition, and on AWS the subs and the Alerts and binding admin URLs (U3)."""
        return self.carrier_enabled and self.macros_off_reason is None

    @property
    def macros_off_reason(self) -> str | None:
        if not self.carrier_enabled:
            return self.carrier_off_reason
        if self.local:
            return None
        if self.aws_users is None:
            return "ENV=aws: Reset and the macros need Asish's and Mom's Cognito subs: run `make cognito-users ENV=aws`"
        if not (self.alerts_url and self.binding_admin_enabled):
            return (
                "ENV=aws: deploy/.env.aws lacks the Alerts or binding admin URL: run `make outputs ENV=aws`"
            )
        return None

    @property
    def sms_enabled(self) -> bool:
        if self.env == "aws":
            return self.admin_on and bool(self.alerts_url)
        return self.local and bool(self.alerts_url)

    @property
    def binding_admin_base(self) -> str:
        return self.binding_admin_url or self.binding_url

    @property
    def binding_admin_enabled(self) -> bool:
        if self.env == "aws":
            return self.admin_on and bool(self.binding_admin_url)
        return self.local and bool(self.binding_url)

    @property
    def personas(self) -> tuple[str, ...]:
        """Who can speak in pane 1: Asish and Mom locally; Asish only on AWS (decision 3)."""
        return ("asish", "mom") if self.local else ("asish",)

    @property
    def user_ids(self) -> dict[str, str]:
        """`asish`/`mom` → the user id the services know: `user-…` locally, the Cognito subs on AWS (U3)."""
        asish, mom = self.aws_users if (self.env == "aws" and self.aws_users) else LOCAL_USERS
        return {"asish": asish, "mom": mom}

    def secrets(self) -> tuple[str, ...]:
        """Every secret value this process holds, for the Ops log's `mask_secrets` (doc 11 §2)."""
        values = (
            self.tower_bearer,
            self.mock_admin_token,
            self.alerts_bearer,
            self.bind_admin_token,
            self.demo_admin_bearer,
            self.ui_token,
        )
        return tuple(v for v in values if v)


def _path(explicit: str | None, checkout: Path | None, rel: Path, discover: bool) -> Path | None:
    if explicit:
        return Path(explicit)
    if discover and checkout is not None:
        return checkout / rel
    return None
