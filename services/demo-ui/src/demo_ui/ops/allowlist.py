"""The only place an Ops command is defined (doc 11 §2 "The Ops tab", O1, T2). Data, not configuration.

No env var, flag, file or request can add, change or parameterise a target. Changing `TARGETS` means changing the
table between the `ops-allowlist` markers in `docs/architecture/components/11-demo-ui.md` in the same change:
`tests/test_ops_allowlist.py` compares the two. Every argv is a tuple (`shell=False`), starts with `make`, names one
target and fixes `ENV`: `ENV=local` on the local runner, `ENV=aws` on the aws runner. The root `.env` may set `ENV`
(`mk/vars.mk` reads it with `:=`) and only a command-line `ENV=` overrides it, so a local `make down` can never
become `terraform destroy`. Two parts are added at run time and nowhere else (`ops/runner.py`): `FORCE=1` after the
typed confirm word (`confirm_when="always"`), and `ADMIN_CIDR=<a.b.c.d/32>` after validation (`takes_cidr`).

The child's environment is an allow-list (`child_env`), never the parent's minus a few keys. A key matching
`DENY` can never pass, even if a set lists it: `check` runs at import.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

RunnerName = Literal["local", "aws"]
Group = Literal["stack", "demo", "tests", "pipeline", "admin", "teardown"]
EnvMode = Literal["scrubbed", "dotenv"]
ConfirmWhen = Literal["always", "plan_destroys"]

GROUPS: Mapping[RunnerName, tuple[Group, ...]] = {
    "local": ("stack", "demo", "tests"),
    "aws": ("pipeline", "admin", "teardown"),
}
ENV_MODE: Mapping[RunnerName, EnvMode] = {"local": "scrubbed", "aws": "dotenv"}  # T3: checked, not chosen

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

# doc 11 §2 "Never in the allowlist", in either runner: asserted absent by make target.
NEVER: frozenset[str] = frozenset(
    {
        "ecr-outputs",
        "down-eks",
        "deploy-eks",
        "register-gateway",
        "test-nightly",
        "showcase-infra",
        "secrets-check",
    }
)
# The make targets of the aws runner (T2), exactly; the local runner never names one of them (O1).
AWS_MAKE_TARGETS: frozenset[str] = frozenset(
    {
        "ecr-up", "push", "plan", "deploy", "outputs", "cognito-users", "seed-aws", "web-chat-sync", "web-chat-url",
        "demo", "latency-aws", "test-e2e", "test-report", "admin-open", "admin-close", "down", "down-all",
    }
)  # fmt: skip
LOCAL_NEVER: frozenset[str] = frozenset(
    {
        "ecr-up", "push", "plan", "deploy", "outputs", "cognito-users", "seed-aws", "web-chat-sync", "web-chat-url",
        "latency-aws", "down-all", "admin-open", "admin-close",
    }
)  # fmt: skip
# T5: the destructive four and their words. `always` targets also get FORCE=1 (their recipes ask).
CONFIRMS: Mapping[str, tuple[str, ConfirmWhen]] = {
    "aws-deploy": ("deploy", "plan_destroys"),
    "aws-admin-close": ("close", "always"),
    "aws-down": ("down", "always"),
    "aws-down-all": ("down-all", "always"),
}
# The only fixed extra assignment of the table (T2).
FIXED_EXTRA: Mapping[str, tuple[str, ...]] = {"aws-seed-bind-asish": ("SEED_ARGS=--bind-asish",)}
CIDR_TARGET = "aws-admin-open"
RUNTIME_ONLY = re.compile(r"^(?:FORCE|ADMIN_CIDR)=")


class AllowlistError(ValueError):
    """The allowlist breaks one of its own rules. Raised at import, so the UI does not start."""


@dataclass(frozen=True)
class OpsTarget:
    name: str
    argv: tuple[str, ...]
    runner: RunnerName
    group: Group
    label: str
    needs_stack: bool
    env: tuple[str, ...]
    env_mode: EnvMode = "scrubbed"
    confirm: str | None = None
    confirm_when: ConfirmWhen | None = None
    after: tuple[str, ...] = ()
    takes_cidr: bool = False

    @property
    def make_target(self) -> str:
        return self.argv[1] if len(self.argv) > 1 else ""

    @property
    def forces(self) -> bool:
        """FORCE=1 is appended after the typed word: the recipes of these ask on stdin, which a job has not."""
        return self.confirm_when == "always"


def _local(name: str, group: Group, label: str, env: tuple[str, ...], needs_stack: bool) -> OpsTarget:
    return OpsTarget(name, ("make", name, "ENV=local"), "local", group, label, needs_stack, env, "scrubbed")


AWS_ENV = ("base", "docker", "uv")


def _aws(name: str, target: str, group: Group, label: str, after: tuple[str, ...] = ()) -> OpsTarget:
    word, when = CONFIRMS.get(name, (None, None))
    argv = ("make", target, "ENV=aws", *FIXED_EXTRA.get(name, ()))
    return OpsTarget(
        name, argv, "aws", group, label, False, AWS_ENV, "dotenv", word, when, after, name == CIDR_TARGET
    )


TARGETS: tuple[OpsTarget, ...] = (
    _local("up", "stack", "Start the stack", ("base", "docker"), needs_stack=False),
    _local("down", "stack", "Stop the stack (compose down -v)", ("base", "docker"), needs_stack=True),
    _local("seed", "stack", "Reset the demo data", ("base", "docker"), needs_stack=True),
    _local("demo", "demo", "make demo: all four stories", ("base", "docker", "uv"), needs_stack=True),
    _local("showcase-tower", "demo", "Tower on its own (runs until Stop)", ("base", "docker", "uv"), needs_stack=False),
    _local("showcase-alerts", "demo", "Alerts on its own", ("base", "docker", "uv"), needs_stack=True),
    _local(
        "showcase-binding", "demo", "Binding page on its own (runs until Stop)", ("base", "docker", "uv"),
        needs_stack=False,
    ),
    _local("test-unit", "tests", "Unit tests", ("base", "uv"), needs_stack=False),
    _local("test-integration", "tests", "Integration tests", ("base", "docker", "uv"), needs_stack=False),
    _local("test-e2e", "tests", "End to end against the local stack", ("base", "docker", "uv"), needs_stack=True),
    _local("test-report", "tests", "Write artifacts/test-report.md", ("base", "uv"), needs_stack=False),
    _aws("aws-ecr-up", "ecr-up", "pipeline", "1 · ECR repositories (idempotent)"),
    _aws("aws-push", "push", "pipeline", "2 · Push the five images (linux/arm64)", ("aws-ecr-up",)),
    _aws("aws-plan", "plan", "pipeline", "3 · terraform plan (read it)", ("aws-push",)),
    _aws(
        "aws-deploy", "deploy", "pipeline", "4 · Deploy (apply, outputs, gateway tools, seed the mock)",
        ("aws-plan",),
    ),
    _aws("aws-outputs", "outputs", "pipeline", "5 · Write deploy/.env.aws", ("aws-deploy",)),
    _aws("aws-cognito-users", "cognito-users", "pipeline", "6 · Cognito users asish and mom", ("aws-deploy",)),
    _aws("aws-seed", "seed-aws", "pipeline", "7 · Seed the demo rows (Asish unbound)", ("aws-cognito-users",)),
    _aws("aws-web-chat-sync", "web-chat-sync", "pipeline", "8 · Upload the web chat page", ("aws-deploy",)),
    _aws("aws-web-chat-url", "web-chat-url", "pipeline", "9 · Web chat URL", ("aws-web-chat-sync",)),
    _aws("aws-seed-bind-asish", "seed-aws", "pipeline", "10 · Bind Asish for the stories", ("aws-seed",)),
    _aws("aws-demo", "demo", "pipeline", "11 · make demo on AWS", ("aws-seed-bind-asish",)),
    _aws("aws-latency", "latency-aws", "pipeline", "12 · Latency: 50 calls per path", ("aws-deploy",)),
    _aws("aws-test-e2e", "test-e2e", "pipeline", "13 · End to end on AWS", ("aws-seed-bind-asish",)),
    _aws("aws-test-report", "test-report", "pipeline", "14 · Write artifacts/test-report.md", ("aws-test-e2e",)),
    _aws("aws-admin-open", "admin-open", "admin", "Open the admin path to your /32", ("aws-deploy",)),
    _aws("aws-admin-close", "admin-close", "admin", "Close the admin path"),
    _aws("aws-down", "down", "teardown", "Destroy the stack, keep ECR (also stops the local stack)"),
    _aws("aws-down-all", "down-all", "teardown", "Destroy everything, ECR included (after judging)"),
)  # fmt: skip


def _check_local(t: OpsTarget) -> None:
    if t.argv != ("make", t.name, "ENV=local"):
        raise AllowlistError(f"{t.name}: a local argv is exactly make <name> ENV=local")
    if t.name in LOCAL_NEVER or t.make_target in LOCAL_NEVER:
        raise AllowlistError(f"{t.name}: the local runner never names an aws target (O1)")
    if t.confirm or t.confirm_when or t.after or t.takes_cidr:
        raise AllowlistError(f"{t.name}: a local target has no confirm, no after and no cidr")


def _check_aws(t: OpsTarget, earlier: set[str]) -> None:
    if not t.name.startswith("aws-"):
        raise AllowlistError(f"{t.name}: an aws id starts with aws- (R10)")
    if t.argv[:1] != ("make",) or t.argv[2:3] != ("ENV=aws",) or t.make_target not in AWS_MAKE_TARGETS:
        raise AllowlistError(f"{t.name}: an aws argv is make <one of T2's targets> ENV=aws")
    if t.argv[3:] != FIXED_EXTRA.get(t.name, ()):
        raise AllowlistError(f"{t.name}: the only extra assignment is SEED_ARGS=--bind-asish on its own row")
    if (t.confirm, t.confirm_when) != CONFIRMS.get(t.name, (None, None)):
        raise AllowlistError(f"{t.name}: confirm words belong to the destructive four only (T5)")
    if t.takes_cidr != (t.name == CIDR_TARGET):
        raise AllowlistError(f"{t.name}: only {CIDR_TARGET} takes a cidr")
    if not set(t.after) <= earlier:
        raise AllowlistError(f"{t.name}: after names an aws entry earlier in the table")


def check(targets: Sequence[OpsTarget], env_sets: Mapping[str, Sequence[str]]) -> None:
    """Every rule of doc 11 §2 that the data itself can break. Tests call it on doctored copies."""
    for key in (k for keys in env_sets.values() for k in keys):
        if DENY.match(key):
            raise AllowlistError(f"env set lists a denied key: {key}")
    names = [t.name for t in targets]
    if len(set(names)) != len(names):
        raise AllowlistError("duplicate target name")
    earlier: set[str] = set()
    for t in targets:
        if t.make_target in NEVER or any(a in ("ENV=eks",) or RUNTIME_ONLY.match(a) for a in t.argv):
            raise AllowlistError(f"{t.name}: never in the allowlist (doc 11 §2)")
        if t.runner not in GROUPS or t.group not in GROUPS[t.runner]:
            raise AllowlistError(f"{t.name}: unknown runner or group {t.runner}/{t.group}")
        if t.env_mode != ENV_MODE[t.runner]:
            raise AllowlistError(f"{t.name}: env mode {t.env_mode} does not go with the {t.runner} runner")
        unknown = set(t.env) - set(env_sets)
        if unknown:
            raise AllowlistError(f"{t.name}: unknown env set {sorted(unknown)}")
        if t.runner == "local":
            _check_local(t)
        else:
            _check_aws(t, earlier)
            earlier.add(t.name)


check(TARGETS, ENV_SETS)


def by_name(targets: Sequence[OpsTarget] = TARGETS) -> dict[str, OpsTarget]:
    return {t.name: t for t in targets}


def child_env(target: OpsTarget, parent: Mapping[str, str]) -> dict[str, str]:
    """The keys of the target's env sets present in `parent`, minus anything denied, plus `FIXED_ENV`."""
    return env_from_sets(target.env, parent)


def env_from_sets(sets: Sequence[str], parent: Mapping[str, str]) -> dict[str, str]:
    allowed = {k for s in sets for k in ENV_SETS.get(s, ()) if not DENY.match(k)}
    env = {k: v for k, v in parent.items() if k in allowed}
    return env | dict(FIXED_ENV)
