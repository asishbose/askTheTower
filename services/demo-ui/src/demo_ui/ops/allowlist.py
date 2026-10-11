"""The only place an Ops command is defined (doc 11 §2 "Pane 5", O1, O2). Data, not configuration.

No env var, flag, file or request can add, change or parameterise a target. Changing `TARGETS` means changing the
table between the `ops-allowlist` markers in `docs/architecture/components/11-demo-ui.md` in the same change:
`tests/test_ops_allowlist.py` compares the two. Every argv is a tuple (`shell=False`), starts with `make`, names one
target and carries `ENV=local` as its only variable: the root `.env` may set `ENV=aws` (`mk/vars.mk` reads it) and
only a command-line `ENV=local` overrides it, so `make down` can never become `terraform destroy`.

The child's environment is an allow-list (`child_env`), never the parent's minus a few keys. A key matching
`DENY` can never pass, even if a set lists it: `check` runs at import.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Group = Literal["stack", "demo", "tests"]
GROUPS: tuple[Group, ...] = ("stack", "demo", "tests")

ENV_SETS: Mapping[str, tuple[str, ...]] = {
    "base": (
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    ),
    "docker": (
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
        "DOCKER_BUILDKIT", "BUILDX_CONFIG",
    ),
    "uv": ("UV_CACHE_DIR", "UV_PYTHON", "UV_NATIVE_TLS", "UV_LINK_MODE"),
}  # fmt: skip

# Set on every child, whatever the parent has: plain, unbuffered output for the log viewer.
FIXED_ENV: Mapping[str, str] = {"NO_COLOR": "1", "PYTHONUNBUFFERED": "1", "TERM": "dumb"}

# Keys that never reach a child (doc 11 §2): credentials, Terraform inputs, and `ENV` itself (fixed on the argv).
DENY = re.compile(
    r"^(?:AWS_\w*|TF_VAR_\w*|COGNITO_\w*|\w*_TOKEN|\w*_BEARER|\w*_SECRET|\w*_PASSWORD|\w*_KEY|VIRTUAL_ENV|ENV)$",
    re.IGNORECASE,
)

# O2 and doc 11 §2 "Never in the allowlist": asserted absent by name.
NEVER: frozenset[str] = frozenset(
    {
        "ecr-up", "ecr-outputs", "push", "plan", "deploy", "down-eks", "down-all", "deploy-eks", "cognito-users",
        "seed-aws", "outputs", "web-chat-sync", "web-chat-url", "register-gateway", "latency-aws", "test-nightly",
        "showcase-infra", "secrets-check",
    }
)  # fmt: skip


class AllowlistError(ValueError):
    """The allowlist breaks one of its own rules. Raised at import, so the UI does not start."""


@dataclass(frozen=True)
class OpsTarget:
    name: str
    argv: tuple[str, ...]
    group: Group
    label: str
    env: tuple[str, ...]
    needs_stack: bool


def _make(name: str, group: Group, label: str, env: tuple[str, ...], needs_stack: bool) -> OpsTarget:
    return OpsTarget(name, ("make", name, "ENV=local"), group, label, env, needs_stack)


TARGETS: tuple[OpsTarget, ...] = (
    _make("up", "stack", "Start the stack", ("base", "docker"), needs_stack=False),
    _make("down", "stack", "Stop the stack (compose down -v)", ("base", "docker"), needs_stack=True),
    _make("seed", "stack", "Reset the demo data", ("base", "docker"), needs_stack=True),
    _make("demo", "demo", "make demo: all four stories", ("base", "docker", "uv"), needs_stack=True),
    _make("showcase-tower", "demo", "Tower on its own (runs until Stop)", ("base", "docker", "uv"), needs_stack=False),
    _make("showcase-alerts", "demo", "Alerts on its own", ("base", "docker", "uv"), needs_stack=True),
    _make(
        "showcase-binding", "demo", "Binding page on its own (runs until Stop)", ("base", "docker", "uv"),
        needs_stack=False,
    ),
    _make("test-unit", "tests", "Unit tests", ("base", "uv"), needs_stack=False),
    _make("test-integration", "tests", "Integration tests", ("base", "docker", "uv"), needs_stack=False),
    _make("test-e2e", "tests", "End to end against the local stack", ("base", "docker", "uv"), needs_stack=True),
    _make("test-report", "tests", "Write artifacts/test-report.md", ("base", "uv"), needs_stack=False),
)  # fmt: skip


def check(targets: Sequence[OpsTarget], env_sets: Mapping[str, Sequence[str]]) -> None:
    """Every rule of doc 11 §2 that the data itself can break. Tests call it on doctored copies."""
    for key in (k for keys in env_sets.values() for k in keys):
        if DENY.match(key):
            raise AllowlistError(f"env set lists a denied key: {key}")
    names = [t.name for t in targets]
    if len(set(names)) != len(names):
        raise AllowlistError("duplicate target name")
    for t in targets:
        if t.name in NEVER:
            raise AllowlistError(f"{t.name} is never run from the UI (O2)")
        if t.argv != ("make", t.name, "ENV=local"):
            raise AllowlistError(f"{t.name}: argv must be exactly make <name> ENV=local")
        if t.group not in GROUPS:
            raise AllowlistError(f"{t.name}: unknown group {t.group}")
        unknown = set(t.env) - set(env_sets)
        if unknown:
            raise AllowlistError(f"{t.name}: unknown env set {sorted(unknown)}")


check(TARGETS, ENV_SETS)


def by_name(targets: Sequence[OpsTarget] = TARGETS) -> dict[str, OpsTarget]:
    return {t.name: t for t in targets}


def child_env(target: OpsTarget, parent: Mapping[str, str]) -> dict[str, str]:
    """The keys of the target's env sets present in `parent`, minus anything denied, plus `FIXED_ENV`."""
    allowed = {k for s in target.env for k in ENV_SETS.get(s, ()) if not DENY.match(k)}
    env = {k: v for k, v in parent.items() if k in allowed}
    return env | dict(FIXED_ENV)
